#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — OFFLINE per-arm fusion-weight tuner (local-only, zero-egress).

Reads the opt-in retrieval log, treats accessed_ids as soft-positive qrels, and searches a
bounded grid of per-arm Weighted-RRF weights to find weights that rank the accessed results
higher — validated on a held-out slice. This is "self-improving" only in the honest BATCH
sense (design §5): it adapts weights to *your* codebase from real usage, offline. It is NOT a
runtime learner and it does NOT touch live ranking.

Cold-start safety (design §6) — the parts that make it not naive:
  * DEFAULT = equal weights. Learning only *adjusts* a default that already cannot regress.
  * THRESHOLD — don't tune below N labeled queries; below that, equal stands.
  * REGULARIZE toward equal — cap how far a weight may deviate from the equal prior.
  * EXPLORATION floor — never starve an arm to zero (so a good arm can still be discovered).
  * HELD-OUT A/B — tune on train, compare to equal on holdout, and AUTO-REVERT to equal if
    learned does not beat equal. (This is the G4 discipline in miniature.)

The result is written to .deepmap/instance.json under `learned_fusion` with **applied: false**.
Live ranking (build/retrieval.py) is FROZEN and does NOT read this file — wiring the learned
weights into search is gated on G3 closing and the learned-vs-equal A/B passing (G4). Until
then this tuner is a data producer only.

Fusion is replayed with the REAL, rank-based retrieval._weighted_rrf (imported, not
re-implemented) and ordered exactly like search() orders it — a stable score sort that keeps the
fusion's insertion order on ties, never an id re-sort — at the RRF_K live search always fuses at, so
the tuner's ranking is identical to production's. (A --tune-rrf-k search records its best k as a
recommendation only: every learned-vs-equal judgement — CV significance, per-slice, profile rows, the
drift guard — is made at RRF_K.)
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import the REAL fusion + arm names + logging primitives from build/ (this module's dir).
_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
from retrieval import _weighted_rrf, RRF_K, DEFAULT_ARM_WEIGHTS  # noqa: E402
import retrieval_log as _rl  # noqa: E402

ARM_NAMES = tuple(_rl.ARM_NAMES)
EQUAL_WEIGHTS = {a: 1.0 for a in ARM_NAMES}

# Defaults (design §6). All overridable per call.
DEFAULT_MIN_LABELED = 150          # don't tune below this many labeled queries
DEFAULT_MAX_DEVIATION = 0.5        # a weight may not move more than this from the equal prior
DEFAULT_EXPLORATION_FLOOR = 0.25   # never starve an arm below this
DEFAULT_HOLDOUT_FRAC = 0.25        # fraction of labeled queries held out for the A/B
DEFAULT_EPS = 1e-9                 # learned must beat equal by more than this on holdout
_GRID_VALUES = (0.5, 0.75, 1.0, 1.25, 1.5)

# RM-1 extension defaults (richer learning + rigorous validation). All overridable per call.
DEFAULT_OBJECTIVE = "ndcg"         # graded, position-discounted — the only metric that uses grades
DEFAULT_NDCG_K = 10                # nDCG@k cutoff
DEFAULT_MIN_LABELED_BUCKET = 40    # a per-intent profile needs >= this many labeled queries in-bucket
DEFAULT_N_FOLDS = 5                # k-fold cross-validation of learned vs equal
DEFAULT_BOOTSTRAP_ITERS = 1000     # bootstrap resamples for the CI on the mean per-query delta
DEFAULT_NEG_PENALTY = 0.1          # small penalty for ranking skip-above negatives over positives
DEFAULT_ALPHA = 0.05               # 95% CI
_RRF_K_GRID = (30, 60, 90)         # searched only when tune_rrf_k=True (default keeps k=60)
_INTENT_BUCKETS = ("impact", "symbol_short", "nl_long", "default")


# ---- qrels from the log ----------------------------------------------------
def build_qrels(records: list[dict]) -> list[dict]:
    """Fold access events (GRADED), then keep only LABELED queries (>=1 accessed id). Each ->
    {"qid","per_arm","positives"(set),"graded"{id:weight},"negatives"[...],"features"{...},"bucket"}.
    `positives` (a set) is retained for back-compat; `graded`/`negatives`/`bucket` power the richer
    metrics + per-intent profiles. Each logged search is one labeled sample. Pure; never raises."""
    folded = _rl.fold_graded(records or [])
    labeled = []
    for r in folded:
        graded_raw = r.get("graded") if isinstance(r.get("graded"), dict) else {}
        graded = {kk: float(vv) for kk, vv in graded_raw.items()
                  if isinstance(kk, (str, int, float)) and isinstance(vv, (int, float))}
        if not graded:
            # Fallback to the binary accessed_ids at the weak grade (defensive; v1 / hand-edited logs).
            graded = {x: _rl.GRADE_DEFAULT for x in _rl._as_list(r.get("accessed_ids"))
                      if isinstance(x, (str, int, float))}
        pos = set(graded.keys())
        if not pos:
            continue
        raw = r.get("per_arm_candidate_ids")
        raw = raw if isinstance(raw, dict) else {}
        per_arm = {}
        for a in ARM_NAMES:
            v = raw.get(a, [])
            per_arm[a] = [i for i in v if isinstance(i, (str, int, float))] if isinstance(v, list) else []
        negatives = [i for i in _rl._as_list(r.get("negatives")) if isinstance(i, (str, int, float))]
        features = r.get("features") if isinstance(r.get("features"), dict) else {}
        labeled.append({"qid": r.get("qid"), "per_arm": per_arm, "positives": pos,
                        "graded": graded, "negatives": negatives, "features": features,
                        "bucket": _rl.intent_bucket(features)})
    return labeled


# ---- metrics: replay the REAL fusion, score the accessed results ----------
def _fuse(per_arm: dict, weights: dict, k: int | None = None) -> list[str]:
    """Re-fuse per-arm ORDERED ids under `weights` with the real rank-based _weighted_rrf.
    Ordered ids -> strictly-descending synthetic scores (fusion is rank-only, so this is exact).
    `k` (the RRF smoothing constant) defaults to the frozen RRF_K; a non-default k is READ-only use
    of the frozen function (the tuner may search k, but never writes it where retrieval.py reads)."""
    kk = RRF_K if k is None else k
    runs = []
    for arm in ARM_NAMES:
        ids = per_arm.get(arm) or []
        if ids:
            runs.append((arm, {rid: float(-i) for i, rid in enumerate(ids)}))
    fused = _weighted_rrf(runs, weights, k=kk)
    # EXACTLY search()'s order: a STABLE score-descending sort over _weighted_rrf's insertion order (arm by
    # arm in ARM_NAMES order, rank by rank — the same run order search() fuses in). Equal-score ties keep that
    # order as live does; re-sorting them by id made the tuner credit weights whose "win" live never shows.
    return [rid for rid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)]


def _reciprocal_rank(ranked: list[str], positives: set) -> float:
    for i, rid in enumerate(ranked, start=1):
        if rid in positives:
            return 1.0 / i
    return 0.0


def mrr(labeled: list[dict], weights: dict, *, rrf_k: int | None = None) -> float:
    if not labeled:
        return 0.0
    total = sum(_reciprocal_rank(_fuse(q["per_arm"], weights, rrf_k), q["positives"]) for q in labeled)
    return total / len(labeled)


def _ndcg_one(ranked: list, graded: dict, k: int) -> float:
    """nDCG@k for one query, using GRADED gains (Read 0.5 < Edit/impact 1.0)."""
    dcg = 0.0
    for i, rid in enumerate(ranked[:k], start=1):
        g = graded.get(rid, 0.0)
        if g:
            dcg += g / math.log2(i + 1)
    ideal = sorted((v for v in graded.values() if v), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, start=1))
    return (dcg / idcg) if idcg > 0 else 0.0


def ndcg(labeled: list[dict], weights: dict, k: int = DEFAULT_NDCG_K, *, rrf_k: int | None = None) -> float:
    if not labeled:
        return 0.0
    total = 0.0
    for q in labeled:
        graded = q.get("graded") or {p: 1.0 for p in q.get("positives", ())}
        total += _ndcg_one(_fuse(q["per_arm"], weights, rrf_k), graded, k)
    return total / len(labeled)


def average_precision(ranked: list, positives: set) -> float:
    if not positives:
        return 0.0
    hits = 0
    ssum = 0.0
    for i, rid in enumerate(ranked, start=1):
        if rid in positives:
            hits += 1
            ssum += hits / i
    return ssum / len(positives)


def mapk(labeled: list[dict], weights: dict, *, rrf_k: int | None = None) -> float:
    if not labeled:
        return 0.0
    return sum(average_precision(_fuse(q["per_arm"], weights, rrf_k), q["positives"])
               for q in labeled) / len(labeled)


def _negative_cost(ranked: list, positives: set, negatives: list, k: int) -> float:
    """Fraction of a query's skip-above negatives ranked in the top-k ABOVE the first positive."""
    if not negatives:
        return 0.0
    first_pos = len(ranked)
    for i, rid in enumerate(ranked):
        if rid in positives:
            first_pos = i
            break
    negset = set(negatives)
    bad = sum(1 for rid in ranked[:first_pos][:k] if rid in negset)
    return bad / max(1, len(negatives))


def _query_score(q: dict, weights: dict, *, objective: str, k: int, rrf_k: int | None = None) -> float:
    """The per-query metric (used by CV to build unbiased per-query deltas)."""
    ranked = _fuse(q["per_arm"], weights, rrf_k)
    obj = (objective or DEFAULT_OBJECTIVE).lower()
    if obj == "mrr":
        return _reciprocal_rank(ranked, q["positives"])
    if obj == "map":
        return average_precision(ranked, q["positives"])
    graded = q.get("graded") or {p: 1.0 for p in q.get("positives", ())}
    return _ndcg_one(ranked, graded, k)


def score_objective(labeled: list[dict], weights: dict, *, objective: str = DEFAULT_OBJECTIVE,
                    k: int = DEFAULT_NDCG_K, negative_penalty: float = DEFAULT_NEG_PENALTY,
                    rrf_k: int | None = None) -> float:
    """The tuner's optimization target: a graded rank metric MINUS a small skip-above-negative penalty.
    negative_penalty=0.0 recovers the pure metric. The penalty is bounded so it refines tie-breaks
    (push skipped-high results down) but can never by itself beat the equal prior."""
    if not labeled:
        return 0.0
    obj = (objective or DEFAULT_OBJECTIVE).lower()
    if obj == "mrr":
        base = mrr(labeled, weights, rrf_k=rrf_k)
    elif obj == "map":
        base = mapk(labeled, weights, rrf_k=rrf_k)
    else:
        base = ndcg(labeled, weights, k=k, rrf_k=rrf_k)
    if negative_penalty:
        pen = sum(_negative_cost(_fuse(q["per_arm"], weights, rrf_k),
                                 q.get("positives", set()), q.get("negatives", []), k) for q in labeled)
        base -= negative_penalty * (pen / len(labeled))
    return base


# ---- the weight grid (regularized + exploration-floored) -------------------
def candidate_weights(max_deviation: float, exploration_floor: float) -> list[dict]:
    """All grid combinations within the deviation cap AND above the exploration floor.
    Equal weights are always included (grid contains 1.0). Deterministic order."""
    vals = [v for v in _GRID_VALUES if v >= exploration_floor - 1e-9 and abs(v - 1.0) <= max_deviation + 1e-9]
    out = []
    for a in vals:
        for b in vals:
            for c in vals:
                out.append({"vector": a, "bm25": b, "graph_weighted": c})
    return out


def _deviation(w: dict) -> float:
    return sum(abs(w[a] - 1.0) for a in ARM_NAMES)


def _deviation_from(w: dict, ref: dict) -> float:
    """L1 distance of `w` from a reference vector (a cold-start seed) — _deviation is the ref=equal case."""
    return sum(abs(w[a] - float(ref[a])) for a in ARM_NAMES)


# ---- train / holdout split (deterministic) ---------------------------------
def split_train_holdout(labeled: list[dict], holdout_frac: float):
    """Deterministic interleaved split by sorted qid. Guarantees a non-empty holdout AND a
    non-empty train when >=2 labeled queries exist; returns ([],[]) style only when n<2."""
    order = sorted(labeled, key=lambda q: str(q.get("qid")))
    n = len(order)
    if n < 2:
        return order, []      # can't validate on <2 — caller reverts to equal
    stride = max(2, int(round(1.0 / holdout_frac))) if holdout_frac > 0 else 2
    holdout = [q for i, q in enumerate(order) if i % stride == 0]
    train = [q for i, q in enumerate(order) if i % stride != 0]
    if not train:            # pathological (stride==1 guard already prevents, but be safe)
        train, holdout = order[1:], order[:1]
    return train, holdout


# ---- the tuner -------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _result(weights, beats, reason, labeled, *, n_train=0, n_holdout=0,
            hl=None, he=None, tl=None, candidate=None) -> dict:
    return {
        "arm_weights": dict(weights),
        "beats_equal": bool(beats),
        "applied": False,   # G4 gate — the tuner NEVER applies weights to live ranking
        "reason": reason,
        "metric": "mrr",
        "n_labeled_queries": len(labeled),
        "n_train": n_train,
        "n_holdout": n_holdout,
        "train_mrr_learned": tl,
        "holdout_mrr_learned": hl,
        "holdout_mrr_equal": he,
        "candidate_learned": dict(candidate) if candidate else None,
        "k": RRF_K,
        "retriever": "deepmap-3arm-weighted-rrf",
        "generated_utc": _now_iso(),
    }


def tune(*, records: list[dict] | None = None, root=None,
         min_labeled: int = DEFAULT_MIN_LABELED,
         max_deviation: float = DEFAULT_MAX_DEVIATION,
         exploration_floor: float = DEFAULT_EXPLORATION_FLOOR,
         holdout_frac: float = DEFAULT_HOLDOUT_FRAC,
         eps: float = DEFAULT_EPS) -> dict:
    """Tune per-arm weights offline with cold-start safety. Supply `records` (raw log records,
    search + access) OR `root` (reads <root>/.deepmap/retrieval_log.jsonl). Returns a result
    dict; `applied` is always False. Deterministic; never raises on empty/insufficient data."""
    if records is None:
        records = _rl.read_records(root) if root is not None else []
    labeled = build_qrels(records)

    # Cold-start: below the threshold, equal weights stand.
    if len(labeled) < min_labeled:
        return _result(EQUAL_WEIGHTS, False, "below_threshold", labeled)

    train, holdout = split_train_holdout(labeled, holdout_frac)
    if not train or not holdout:
        return _result(EQUAL_WEIGHTS, False, "insufficient_split", labeled,
                       n_train=len(train), n_holdout=len(holdout))

    # Search the regularized/floored grid on TRAIN. Tie-break toward equal (smallest deviation,
    # then lexicographic) so the outcome is deterministic and biased to the safe prior.
    grid = candidate_weights(max_deviation, exploration_floor)
    scored = [(mrr(train, w), _deviation(w),
               (w["vector"], w["bm25"], w["graph_weighted"]), w) for w in grid]
    best_train_mrr, _, _, best = max(scored, key=lambda t: (t[0], -t[1], tuple(-x for x in t[2])))

    # A/B on HOLDOUT: learned must beat equal there, or we auto-revert.
    holdout_equal = mrr(holdout, EQUAL_WEIGHTS)
    holdout_learned = mrr(holdout, best)
    if holdout_learned > holdout_equal + eps and best != EQUAL_WEIGHTS:
        return _result(best, True, "learned_beats_equal", labeled,
                       n_train=len(train), n_holdout=len(holdout),
                       hl=holdout_learned, he=holdout_equal, tl=best_train_mrr, candidate=best)
    return _result(EQUAL_WEIGHTS, False, "reverted_not_better", labeled,
                   n_train=len(train), n_holdout=len(holdout),
                   hl=holdout_learned, he=holdout_equal, tl=best_train_mrr, candidate=best)


# ---- optimizer: grid (auditable default) or coordinate descent -------------
def _optimize(labeled, *, objective, k, max_deviation, exploration_floor, negative_penalty,
              optimizer="grid", tune_rrf_k=False, prior=None, anchor=None):
    """Search per-arm weights (+ optional RRF_K) maximizing the objective on `labeled`. Returns
    (weights, score, rrf_k). Deterministic. 'grid' = exhaustive over the regularized/floored grid
    (the auditable default + reference oracle); 'coord' = coordinate descent (finer / RRF_K search).
    Both replay the REAL _weighted_rrf. `prior` is coordinate descent's starting point. Tie-break:
    highest score, then closest (L1) to the ANCHOR — the equal prior by default, or a valid cold-start
    seed (tune_profiles' seed_prior), which is how a seed moves the GRID result wherever the data leaves
    equally-scoring candidates — then lexicographic, then RRF_K closest to the frozen default.
    anchor=None keeps the equal-anchored key byte-identical to the unseeded tuner."""
    prior = prior or EQUAL_WEIGHTS
    ref = dict(anchor) if _valid_weights(anchor) else None
    rrf_ks = _RRF_K_GRID if tune_rrf_k else (RRF_K,)
    best = None   # (key_tuple, weights, rk)

    def consider(w, rk):
        nonlocal best
        sc = score_objective(labeled, w, objective=objective, k=k,
                             negative_penalty=negative_penalty, rrf_k=rk)
        dev = _deviation(w) if ref is None else _deviation_from(w, ref)
        key = (sc, -dev, (-w["vector"], -w["bm25"], -w["graph_weighted"]), -abs(rk - RRF_K))
        if best is None or key > best[0]:
            best = (key, dict(w), rk)

    if optimizer == "coord":
        for rk in rrf_ks:
            w, _sc = _coord_descent(labeled, objective=objective, k=k, max_deviation=max_deviation,
                                    exploration_floor=exploration_floor, negative_penalty=negative_penalty,
                                    prior=prior, rrf_k=rk)
            consider(w, rk)
    else:
        for rk in rrf_ks:
            for w in candidate_weights(max_deviation, exploration_floor):
                consider(w, rk)
    return dict(best[1]), best[0][0], best[2]


def _coord_descent(labeled, *, objective, k, max_deviation, exploration_floor,
                   negative_penalty, prior, rrf_k, steps=(0.5, 0.25, 0.125)):
    """Descent over the weight box [max(floor,1-dev), 1+dev]^3, coarse->fine. Considers single-arm,
    pairwise-joint AND all-arm joint moves each round — joint moves escape saddles where moving one
    arm alone can't improve the ranking (e.g. two arms both rank a decoy first and one ranks the
    target first: from equal every single or pairwise step lands exactly ON the fused tie, which live
    search's stable order resolves for the decoy, so only the all-arm step crosses it strictly).
    Deterministic (fixed arm/step/sign order + deviation tie-break). Replays _weighted_rrf."""
    lo = max(exploration_floor, 1.0 - max_deviation)
    hi = 1.0 + max_deviation

    def clamp(v):
        return min(hi, max(lo, v))

    def neighbors(w, step):
        for a in ARM_NAMES:                                  # single-arm moves
            for d in (step, -step):
                nb = dict(w)
                nb[a] = clamp(round(w[a] + d, 6))
                if nb != w:
                    yield nb
        for i in range(len(ARM_NAMES)):                      # pairwise joint moves
            for j in range(i + 1, len(ARM_NAMES)):
                for di in (step, -step):
                    for dj in (step, -step):
                        nb = dict(w)
                        nb[ARM_NAMES[i]] = clamp(round(w[ARM_NAMES[i]] + di, 6))
                        nb[ARM_NAMES[j]] = clamp(round(w[ARM_NAMES[j]] + dj, 6))
                        if nb != w:
                            yield nb
        for signs in itertools.product((step, -step), repeat=len(ARM_NAMES)):   # all-arm joint moves
            nb = {a: clamp(round(w[a] + d, 6)) for a, d in zip(ARM_NAMES, signs)}
            if nb != w:
                yield nb

    cur = {a: clamp(prior.get(a, 1.0)) for a in ARM_NAMES}
    best_score = score_objective(labeled, cur, objective=objective, k=k,
                                 negative_penalty=negative_penalty, rrf_k=rrf_k)
    for step in steps:
        improved = True
        while improved:
            improved = False
            for cand in neighbors(cur, step):
                sc = score_objective(labeled, cand, objective=objective, k=k,
                                     negative_penalty=negative_penalty, rrf_k=rrf_k)
                if (sc > best_score + 1e-12
                        or (abs(sc - best_score) <= 1e-12 and _deviation(cand) < _deviation(cur) - 1e-12)):
                    cur, best_score = cand, sc
                    improved = True
    return cur, best_score


# ---- rigorous validation: k-fold CV + seeded bootstrap CI (design §1) ------
def _seed_from_qids(qids) -> int:
    """A deterministic seed from the query ids — so CV/bootstrap are reproducible with NO wall-clock
    and NO `random` (both would break determinism / the resume model)."""
    joined = "\x00".join(sorted(str(q) for q in (qids or [])))
    return int(hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:8], 16) or 1


class _LCG:
    """A tiny deterministic PRNG (linear congruential). No `random`, no time — reproducible."""
    def __init__(self, seed: int):
        self.s = (int(seed) & 0x7FFFFFFF) or 1

    def _next(self) -> int:
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s

    def randint(self, a: int, b: int) -> int:
        if b <= a:
            return a
        return a + self._next() % (b - a + 1)


def kfold_indices(n: int, k: int, seed: int) -> list[list[int]]:
    """Deterministic k-fold partition of range(n): a seeded Fisher-Yates shuffle, round-robin into
    k folds. Every index appears exactly once."""
    idx = list(range(n))
    rng = _LCG(seed)
    for i in range(n - 1, 0, -1):
        j = rng.randint(0, i)
        idx[i], idx[j] = idx[j], idx[i]
    kk = max(1, k)
    folds = [[] for _ in range(kk)]
    for pos, v in enumerate(idx):
        folds[pos % kk].append(v)
    return folds


def cross_val_delta(labeled, *, objective, k, n_folds, seed, max_deviation, exploration_floor,
                    negative_penalty, optimizer, tune_rrf_k, prior=None) -> dict:
    """Out-of-fold (UNBIASED) per-query deltas (learned - equal): for each fold, tune on the
    complement with the SAME procedure as the global fit (an optional cold-start `prior` as coord start
    AND tie-break anchor) and score the held-out fold vs equal — both fused at RRF_K, the only k live
    search uses (a --tune-rrf-k k is a recommendation, never the k significance is judged at). Returns
    {per_query_delta, mean_delta, per_fold_delta, n_folds}. Deterministic; never raises."""
    n = len(labeled)
    kk = min(n_folds, n)
    if kk < 2:
        return {"per_query_delta": [], "mean_delta": 0.0, "per_fold_delta": [], "n_folds": 0}
    folds = kfold_indices(n, kk, seed)
    deltas, per_fold = [], []
    for f in range(kk):
        test_idx = set(folds[f])
        train = [labeled[i] for i in range(n) if i not in test_idx]
        test = [labeled[i] for i in range(n) if i in test_idx]
        if not train or not test:
            continue
        w, _, _rk = _optimize(train, objective=objective, k=k, max_deviation=max_deviation,
                              exploration_floor=exploration_floor, negative_penalty=negative_penalty,
                              optimizer=optimizer, tune_rrf_k=tune_rrf_k, prior=prior, anchor=prior)
        fold_ds = [_query_score(q, w, objective=objective, k=k)
                   - _query_score(q, EQUAL_WEIGHTS, objective=objective, k=k) for q in test]
        deltas.extend(fold_ds)
        per_fold.append(sum(fold_ds) / len(fold_ds) if fold_ds else 0.0)
    mean = sum(deltas) / len(deltas) if deltas else 0.0
    return {"per_query_delta": deltas, "mean_delta": mean, "per_fold_delta": per_fold, "n_folds": len(per_fold)}


def bootstrap_ci(deltas, *, iters, seed, alpha=DEFAULT_ALPHA) -> dict:
    """Seeded bootstrap CI on the mean of paired per-query deltas. significant <=> lo > 0. Deterministic."""
    if not deltas:
        return {"lo": 0.0, "hi": 0.0, "mean": 0.0, "iters": 0, "alpha": alpha}
    n = len(deltas)
    rng = _LCG((seed ^ 0x9E3779B9) & 0x7FFFFFFF)
    means = []
    for _ in range(max(1, iters)):
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randint(0, n - 1)]
        means.append(s / n)
    means.sort()
    m = len(means)
    lo = means[min(m - 1, int((alpha / 2) * m))]
    hi = means[min(m - 1, int((1 - alpha / 2) * m))]
    return {"lo": lo, "hi": hi, "mean": sum(deltas) / n, "iters": m, "alpha": alpha}


def per_slice_deltas(labeled, weights, *, objective, k, rrf_k=None) -> dict:
    """Mean per-query (learned - equal) delta within each intent bucket — the pre-registered-slice
    material for G4. {bucket: {n, delta}}."""
    by_bucket: dict = {}
    for q in labeled:
        by_bucket.setdefault(q.get("bucket", "default"), []).append(q)
    out = {}
    for bucket, qs in by_bucket.items():
        ds = [_query_score(q, weights, objective=objective, k=k, rrf_k=rrf_k)
              - _query_score(q, EQUAL_WEIGHTS, objective=objective, k=k) for q in qs]
        out[bucket] = {"n": len(qs), "delta": (sum(ds) / len(ds)) if ds else 0.0}
    return out


# ---- the comprehensive tuner: per-intent profiles + CV significance --------
def _profile_result(weights, beats, reason, labeled, *, objective, k, rrf_k, significant,
                    candidate=None, candidate_rrf_k=None, cv=None, ci=None, per_slice=None) -> dict:
    cv = cv or {}
    ci = ci or {}
    learned = score_objective(labeled, weights, objective=objective, k=k, negative_penalty=0.0) if labeled else 0.0
    equal = score_objective(labeled, EQUAL_WEIGHTS, objective=objective, k=k, negative_penalty=0.0) if labeled else 0.0
    return {
        "arm_weights": dict(weights),
        "beats_equal": bool(beats),
        "applied": False,           # G4 gate — the tuner NEVER applies weights to live ranking
        "reason": reason,
        "metric": objective,
        "objective": objective,
        "k": rrf_k,                 # back-compat mirror of rrf_k (tune() stored RRF_K here)
        "ndcg_k": k,
        "rrf_k": rrf_k,             # RRF_K, or a --tune-rrf-k RECOMMENDATION (judgements + live use RRF_K)
        "significant": bool(significant),
        "n_labeled_queries": len(labeled),
        "n_folds": cv.get("n_folds", 0),
        "cv_mean_delta": cv.get("mean_delta", 0.0),
        "cv_per_fold_delta": cv.get("per_fold_delta", []),
        "bootstrap": {"ci_lo": ci.get("lo", 0.0), "ci_hi": ci.get("hi", 0.0), "mean": ci.get("mean", 0.0),
                      "iters": ci.get("iters", 0), "alpha": ci.get("alpha", DEFAULT_ALPHA)},
        "per_slice_delta": per_slice or {},
        "holdout_mrr_learned": learned,   # legacy field name; holds the configured-objective score
        "holdout_mrr_equal": equal,
        "candidate_learned": dict(candidate) if candidate else None,
        "candidate_rrf_k": candidate_rrf_k,
        "grade_weights": dict(_rl.GRADE_WEIGHTS),
        "retriever": "deepmap-3arm-weighted-rrf",
        "generated_utc": _now_iso(),
    }


def tune_profiles(*, records: list[dict] | None = None, root=None, objective: str = DEFAULT_OBJECTIVE,
                  k: int = DEFAULT_NDCG_K, min_labeled: int = DEFAULT_MIN_LABELED,
                  min_labeled_bucket: int = DEFAULT_MIN_LABELED_BUCKET,
                  max_deviation: float = DEFAULT_MAX_DEVIATION,
                  exploration_floor: float = DEFAULT_EXPLORATION_FLOOR,
                  negative_penalty: float = DEFAULT_NEG_PENALTY, optimizer: str = "grid",
                  tune_rrf_k: bool = False, n_folds: int = DEFAULT_N_FOLDS,
                  bootstrap_iters: int = DEFAULT_BOOTSTRAP_ITERS, seed: int | None = None,
                  eps: float = DEFAULT_EPS, with_profiles: bool = True,
                  seed_prior: dict | None = None) -> dict:
    """The comprehensive OFFLINE tuner (RM-1 extension). Graded objective (default nDCG@10), per-intent
    weight PROFILES, and rigorous validation (k-fold CV + seeded bootstrap CI). `beats_equal` requires
    STATISTICAL SIGNIFICANCE (CI lower bound > 0), not just a point win. Result is a SUPERSET of
    tune()'s shape (global vector at top level) plus profiles/objective/significance/per-slice. Always
    `applied: false`. Deterministic; never raises on empty/insufficient data.

    `seed_prior` (optional, the cold-start user prior — build/learned_prior.py): a WARM-START for
    the global fit (and, so significance is judged on the same procedure, for every CV fold): it is
    coordinate descent's starting point AND the tie-break anchor — among equally-scoring candidates
    the one closest to the seed wins, which is how the default GRID optimizer honours a seed. It never
    moves the equal-anchored clamp bounds / the grid itself, never out-scores the data, and never the
    below-threshold behavior (equal stands regardless of any seed). Invalid input is ignored; no seed
    leaves the unseeded tuner byte-identical.

    Every learned-vs-equal judgement (the CV significance, the per-slice deltas, a profile row's gain
    over global) fuses at RRF_K — live search never uses another k — so a --tune-rrf-k result records
    its best k (rrf_k / candidate_rrf_k / a profile's rrf_k) as a recommendation only."""
    if records is None:
        records = _rl.read_records(root) if root is not None else []
    labeled = build_qrels(records)
    n = len(labeled)
    if seed is None:
        seed = _seed_from_qids([q.get("qid") for q in labeled]) if labeled else 1

    # Cold-start: below the global threshold, equal stands (no learning attempted).
    if n < min_labeled:
        res = _profile_result(EQUAL_WEIGHTS, False, "below_threshold", labeled, objective=objective,
                              k=k, rrf_k=RRF_K, significant=False)
        res["profiles"] = {}
        return res

    # GLOBAL learned vector over all labeled, validated out-of-fold. An (optional, validated)
    # cold-start seed warm-starts the search; the safety envelope is unchanged.
    _sp = dict(seed_prior) if _valid_weights(seed_prior) else None
    gw, _gsc, grk = _optimize(labeled, objective=objective, k=k, max_deviation=max_deviation,
                              exploration_floor=exploration_floor, negative_penalty=negative_penalty,
                              optimizer=optimizer, tune_rrf_k=tune_rrf_k, prior=_sp, anchor=_sp)
    cv = cross_val_delta(labeled, objective=objective, k=k, n_folds=n_folds, seed=seed,
                         max_deviation=max_deviation, exploration_floor=exploration_floor,
                         negative_penalty=negative_penalty, optimizer=optimizer, tune_rrf_k=tune_rrf_k,
                         prior=_sp)
    ci = bootstrap_ci(cv["per_query_delta"], iters=bootstrap_iters, seed=seed)
    significant = ci["lo"] > 0.0 and cv["mean_delta"] > eps
    beats = bool(significant and gw != EQUAL_WEIGHTS)
    final = gw if beats else EQUAL_WEIGHTS
    reason = ("learned_beats_equal" if beats
              else ("reverted_not_significant" if gw != EQUAL_WEIGHTS else "no_improvement"))
    slices = per_slice_deltas(labeled, final, objective=objective, k=k)   # judged at RRF_K, like live
    res = _profile_result(final, beats, reason, labeled, objective=objective, k=k,
                          rrf_k=(grk if beats else RRF_K), significant=significant,
                          candidate=gw, candidate_rrf_k=grk, cv=cv, ci=ci, per_slice=slices)

    # PER-INTENT profiles: a bucket earns a row only when it has >= min_labeled_bucket samples AND
    # its own learned vector beats the GLOBAL learned vector on that bucket. Else it falls back to
    # global at lookup time (the layered cold-start ladder: bucket -> global -> equal).
    profiles = {}
    if with_profiles and beats:
        for bucket in _INTENT_BUCKETS:
            qs = [q for q in labeled if q.get("bucket") == bucket]
            if len(qs) < min_labeled_bucket:
                continue
            bw, _, brk = _optimize(qs, objective=objective, k=k, max_deviation=max_deviation,
                                   exploration_floor=exploration_floor, negative_penalty=negative_penalty,
                                   optimizer=optimizer, tune_rrf_k=tune_rrf_k, prior=gw)
            # beats-global is judged at RRF_K for both vectors — the k live search fuses a profile row at.
            gain = sum(_query_score(q, bw, objective=objective, k=k)
                       - _query_score(q, gw, objective=objective, k=k) for q in qs) / len(qs)
            if bw != gw and gain > eps:
                profiles[bucket] = {"arm_weights": dict(bw), "rrf_k": brk, "beats_global": True,
                                    "n_labeled": len(qs), "gain_over_global": gain, "reason": "beats_global"}
    res["profiles"] = profiles
    return res


# ---- persistence (zero-egress, merge, never raise) -------------------------
def learned_weights_fingerprint(lf):
    """A stable fingerprint of EXACTLY the weights live search can serve from a `learned_fusion` block:
    the global arm_weights plus every VALID per-intent profile row (the rows learned_fusion._select_weights
    can pick). Everything else — rrf_k (live always fuses at RRF_K), stats, flags, evidence — is ignored,
    and 1 == 1.0. `benchmark_evidence.weights_fingerprint` carries it so `tune --apply` can prove the
    evidence measured THESE weights (G4). None when the global vector is invalid. Never raises."""
    try:
        if not isinstance(lf, dict) or not _valid_weights(lf.get("arm_weights")):
            return None
        gw = lf["arm_weights"]
        canon = {"arm_weights": {a: float(gw[a]) for a in ARM_NAMES}, "profiles": {}}
        prof = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
        for bucket, row in prof.items():
            if isinstance(row, dict) and _valid_weights(row.get("arm_weights")):
                canon["profiles"][str(bucket)] = {a: float(row["arm_weights"][a]) for a in ARM_NAMES}
        blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except Exception:
        return None


# The REAL G4 evidence procedure (benchmark/weight_ab.py — documented in its ab_env). Named with an
# absolute path: this hint is printed in the user's project, where a relative benchmark/ path does not exist.
_WEIGHT_AB = (Path(__file__).resolve().parent.parent / "benchmark" / "weight_ab.py").as_posix()
_G4_PROCEDURE = (f'the learned-vs-equal benchmark A/B in "{_WEIGHT_AB}" (see its ab_env): stage_learned_weights '
                 "into the benchmark clone -> capture the ON arm with equal and with learned weights -> "
                 "restore -> deterministic_ab -> build_g4_evidence(weights_fingerprint=...) -> "
                 "write_benchmark_evidence into this project")


def write_instance_weights(root, result: dict):
    """Merge the tune result into <root>/.deepmap/instance.json under `learned_fusion` (applied:
    false), preserving other top-level keys and every unrelated learned_fusion sub-key — ONE locked,
    atomic read-modify-write (instance_state.merge_learned_fusion); an existing file that is not a JSON
    object is left untouched (None), never wiped. Returns the path, or None on failure. Never raises.
    Zero-egress: writes only under <root>/.deepmap/."""
    try:
        import instance_state as _is
        # MERGE into any existing learned_fusion block so unrelated sub-keys survive a re-tune —
        # notably `logging_enabled` (the opt-in flag logging_enabled() reads). Replacing the whole
        # block would silently disable logging on the next tune. (A kept benchmark_evidence is bound
        # to the weights it measured — apply_learned re-checks its weights_fingerprint.)
        return _is.merge_learned_fusion(root, {
            "arm_weights": dict(result.get("arm_weights", EQUAL_WEIGHTS)),
            "applied": False,   # NEVER live until G4 — flipped only by `deepmap tune --apply` (E)
            "beats_equal": bool(result.get("beats_equal", False)),
            "significant": bool(result.get("significant", False)),
            "reason": result.get("reason"),
            "metric": result.get("metric", "mrr"),
            "objective": result.get("objective", result.get("metric", "mrr")),
            "ndcg_k": result.get("ndcg_k"),
            "rrf_k": result.get("rrf_k", result.get("k")),
            "n_labeled_queries": result.get("n_labeled_queries", 0),
            "n_folds": result.get("n_folds", 0),
            "cv_mean_delta": result.get("cv_mean_delta"),
            "cv_per_fold_delta": result.get("cv_per_fold_delta"),
            "bootstrap": result.get("bootstrap"),
            "per_slice_delta": result.get("per_slice_delta"),
            "holdout_mrr_learned": result.get("holdout_mrr_learned"),
            "holdout_mrr_equal": result.get("holdout_mrr_equal"),
            "candidate_learned": result.get("candidate_learned"),
            "candidate_rrf_k": result.get("candidate_rrf_k"),
            "profiles": result.get("profiles", {}),
            "grade_weights": result.get("grade_weights"),
            "generated_utc": result.get("generated_utc") or _now_iso(),
            "note": ("Offline learned fusion weights (opt-in, local). NOT applied to live ranking: "
                     "build/retrieval.py uses equal weights until learned weights beat equal weights on "
                     "the benchmark (statistically significant). Safe to delete; regenerated by "
                     "`deepmap tune`."),
        })
    except Exception:
        return None


def apply_learned(root, *, require_benchmark: bool = True) -> tuple:
    """OPERATOR-GATED: flip learned_fusion.applied to True so live search MAY use learned weights
    (still requires the DEEPMAP_APPLY_LEARNED env at query time — see build/learned_fusion.py). Flips
    ONLY when learned beats equal, is statistically significant, AND (require_benchmark, the default) a
    benchmark-evidence artifact shows learned>equal significant on the gold set FOR THESE WEIGHTS: its
    weights_fingerprint must equal learned_weights_fingerprint() of the CURRENT block, so evidence
    measured before a re-tune can never put new, never-benchmarked weights live. NEVER applies on the
    log's self-holdout alone — that is the G4 discipline. The checks and the flip are ONE locked, atomic
    read-modify-write (instance_state.update_instance). Returns (ok, reason); never raises."""
    try:
        import instance_state as _is
        path = _is._instance_path(root)
        if path is None:
            return False, "bad root"
        if not path.exists():   # refuse without creating .deepmap/ state in a directory that has none
            return False, "no learned_fusion (run `deepmap tune` first)"
    except Exception:
        return False, "bad root"
    refusal: list = []

    def _flip(data):
        lf = data.get("learned_fusion")
        if not isinstance(lf, dict):
            refusal.append("no learned_fusion (run `deepmap tune` first)")
            return None
        if lf.get("beats_equal") is not True:
            refusal.append("beats_equal is not true (learned did not beat equal)")
            return None
        if lf.get("significant") is not True:
            refusal.append("not statistically significant (CI lower bound <= 0)")
            return None
        if require_benchmark:
            be = lf.get("benchmark_evidence")
            if not (isinstance(be, dict) and be.get("present") is True and be.get("significant") is True):
                refusal.append(f"no significant benchmark_evidence — run {_G4_PROCEDURE}, then re-run "
                               "--apply")
                return None
            bound = be.get("weights_fingerprint")
            if not bound:
                refusal.append("benchmark_evidence is not bound to any learned weights (no "
                               f"weights_fingerprint) — re-run {_G4_PROCEDURE} on the current weights")
                return None
            if bound != learned_weights_fingerprint(lf):
                refusal.append("benchmark_evidence was measured on DIFFERENT learned weights (weights_fingerprint "
                               f"mismatch — they were re-tuned since the A/B) — re-run {_G4_PROCEDURE} on the "
                               "current weights")
                return None
        lf = dict(lf)
        lf["applied"] = True
        lf["applied_utc"] = _now_iso()
        data["learned_fusion"] = lf
        return data

    written, status = _is.update_instance(root, _flip)
    if written is not None:
        return True, "applied (learned_fusion.applied=true)"
    if refusal:
        return False, refusal[0]
    if status == "unparseable":
        return False, "instance.json is not a JSON object — left untouched (fix or delete it, then re-run `deepmap tune`)"
    if status == "locked":
        return False, "instance.json is being written by another DeepMap process — retry"
    return False, "instance.json unreadable/unwritable"


# ---- drift guard: newest-slice re-validation of the STORED weights ---------
# The learning loop's honesty check (design §6 discipline, applied continuously): on every
# re-tune, the weights that are CURRENTLY standing (possibly applied live) are re-validated
# against equal on the newest slice of labeled queries. A statistically significant degradation
# auto-reverts to equal and says so — a stale profile is never silently kept. The only `applied`
# transition the guard ever makes is true -> false.
DEFAULT_DRIFT_MIN_N = 20     # need at least this many labeled queries to judge drift at all
DEFAULT_DRIFT_FRAC = 0.25    # the newest slice is max(min_n, ceil(frac * n_labeled)) queries


def _valid_weights(w) -> bool:
    return (isinstance(w, dict) and set(w) == set(ARM_NAMES)
            and all(isinstance(w.get(a), (int, float)) and w[a] > 0 for a in ARM_NAMES))


def _stored_weights_for(q: dict, lf: dict):
    """(weights, rrf_k): the weights the LIVE ladder would use for this query's bucket — a mirror of
    learned_fusion._select_weights (profile row -> global) — plus that row's RECORDED rrf_k (a
    --tune-rrf-k recommendation; live search, and so the drift guard, always fuse at RRF_K)."""
    profiles = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
    row = profiles.get(q.get("bucket"))
    if isinstance(row, dict) and _valid_weights(row.get("arm_weights")):
        rk = row.get("rrf_k")
        return dict(row["arm_weights"]), (rk if isinstance(rk, int) else None)
    w = lf.get("arm_weights")
    return (dict(w) if _valid_weights(w) else None), None


def check_drift(root, *, records=None, min_n: int = DEFAULT_DRIFT_MIN_N,
                frac: float = DEFAULT_DRIFT_FRAC, bootstrap_iters: int = DEFAULT_BOOTSTRAP_ITERS,
                alpha: float = DEFAULT_ALPHA, eps: float = DEFAULT_EPS) -> dict:
    """READ-ONLY: re-validate the stored learned_fusion weights vs equal on the NEWEST slice of
    labeled queries (log STREAM order — append order is age order; timestamps are never parsed).
    Evaluation mirrors the live profile->global ladder at the stored objective/ndcg_k, fused at RRF_K
    like live search (a stored rrf_k from --tune-rrf-k is a recorded recommendation; live never uses it).
    Paired per-query deltas -> the same seeded bootstrap the tuner uses; degraded <=> the CI sits
    ENTIRELY below zero (ci_hi < 0 and mean < -eps — the mirror of the tuner's win rule).
    Verdicts: no_learned_weights | not_learned | insufficient_data | ok | degraded. `weights_fingerprint`
    names the weights that were judged (apply_drift_guard reverts only if they are still the stored ones).
    Deterministic; never raises. NOT cross_val_delta — that re-tunes per fold, the wrong tool for
    judging FIXED stored weights."""
    out = {"verdict": "no_learned_weights", "n_labeled": 0, "n_slice": 0, "frac": frac,
           "min_n": min_n, "mean_delta": None, "ci_lo": None, "ci_hi": None, "alpha": alpha,
           "was_applied": False, "objective": None, "weights_fingerprint": None, "checked_utc": _now_iso()}
    try:
        import instance_state as _is
        lf = _is.read_instance(root).get("learned_fusion") if root is not None else None
        if not isinstance(lf, dict):
            return out
        gw = lf.get("arm_weights")
        if not _valid_weights(gw):
            return out
        out["was_applied"] = lf.get("applied") is True
        out["weights_fingerprint"] = learned_weights_fingerprint(lf)
        profiles = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
        valid_profiles = {b: r for b, r in profiles.items()
                          if isinstance(r, dict) and _valid_weights(r.get("arm_weights"))}
        if dict(gw) == EQUAL_WEIGHTS and not valid_profiles:
            out["verdict"] = "not_learned"
            return out
        if records is None:
            records = _rl.read_records(root) if root is not None else []
        labeled = build_qrels(records)
        n = len(labeled)
        out["n_labeled"] = n
        if n < min_n:
            out["verdict"] = "insufficient_data"
            return out
        n_slice = min(n, max(min_n, int(math.ceil(frac * n))))
        newest = labeled[-n_slice:]
        out["n_slice"] = n_slice
        objective = lf.get("objective") or DEFAULT_OBJECTIVE
        k = lf.get("ndcg_k") if isinstance(lf.get("ndcg_k"), int) else DEFAULT_NDCG_K
        out["objective"] = objective
        deltas = []
        for q in newest:
            w, _rk = _stored_weights_for(q, lf)   # a row's rrf_k is a recommendation — live fuses at RRF_K
            if w is None:                       # defensively: an unusable row -> a zero delta
                deltas.append(0.0)
                continue
            deltas.append(_query_score(q, w, objective=objective, k=k)
                          - _query_score(q, EQUAL_WEIGHTS, objective=objective, k=k))
        ci = bootstrap_ci(deltas, iters=bootstrap_iters,
                          seed=_seed_from_qids([q.get("qid") for q in newest]), alpha=alpha)
        out["mean_delta"] = ci["mean"]
        out["ci_lo"] = ci["lo"]
        out["ci_hi"] = ci["hi"]
        out["verdict"] = "degraded" if (ci["hi"] < 0.0 and ci["mean"] < -eps) else "ok"
        return out
    except Exception:
        return out


def apply_drift_guard(root, *, records=None, **kw) -> dict:
    """check_drift + record the verdict under learned_fusion.drift (ALWAYS — the self-check is
    visible either way). Iff degraded: revert to equal (weights, profiles cleared, beats_equal/
    significant/applied all false, reason=drift_reverted) with the full pre-revert state kept
    under drift.previous — an honest, never-silent retirement of a stale profile. The revert is ONE
    locked, atomic read-modify-write that re-reads the block and reverts ONLY if the stored weights are
    still the ones judged (a concurrent re-tune's new weights are never reverted on a stale verdict —
    the next guard run judges them). Returns the check dict + {reverted, written}. Never raises."""
    d: dict = {}
    try:
        d = check_drift(root, records=records, **kw)
        d["reverted"] = False
        d["written"] = False
        block = {kk: d.get(kk) for kk in ("checked_utc", "verdict", "n_labeled", "n_slice",
                                          "frac", "min_n", "mean_delta", "ci_lo", "ci_hi", "alpha")}
        block["reverted"] = False
        import instance_state as _is
        if d.get("verdict") == "degraded":
            judged = d.get("weights_fingerprint")

            def _revert(data):
                lf = data.get("learned_fusion")
                lf = dict(lf) if isinstance(lf, dict) else {}
                if judged is not None and learned_weights_fingerprint(lf) != judged:
                    return None      # re-tuned since the check: not the weights that were judged
                blk = dict(block)
                blk["reverted"] = True
                blk["previous"] = {kk: lf.get(kk) for kk in
                                   ("arm_weights", "profiles", "applied", "applied_utc",
                                    "reason", "beats_equal", "significant")}
                lf.update({"arm_weights": dict(EQUAL_WEIGHTS), "profiles": {},
                           "beats_equal": False, "significant": False, "applied": False,
                           "reason": "drift_reverted", "drift": blk})
                data["learned_fusion"] = lf
                return data

            path, _status = _is.update_instance(root, _revert)
            d["reverted"] = path is not None
        else:
            path = _is.merge_learned_fusion(root, {"drift": block})
        d["written"] = path is not None
        return d
    except Exception:
        d = d if isinstance(d, dict) else {}
        d.setdefault("verdict", "no_learned_weights")
        d.setdefault("reverted", False)
        d.setdefault("written", False)
        return d
