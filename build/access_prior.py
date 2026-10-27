#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 / D1 — the OFFLINE per-node access-prior re-ranker EXPERIMENT (zero-egress).

Learns a small LOCAL prior from repeated access patterns — "the files/symbols this user actually
works in" — and re-ranks the FUSED result list in REPLAY, as a post-fusion experiment. It is
NEVER wired into live search: `build/retrieval.py` is untouched, no CLI/MCP call site imports
this module, and results persist only under instance.json `experiments.access_prior` with
`applied` FORCED false (build/instance_state.py), a key the live resolver provably ignores.

The prior: for every logged search copy whose returned results were later accessed, each
accessed id accrues `grade x 0.5^(age_days / half_life_days)` (an Edit/impact outranks a weak
Read; older activity decays; age is measured against the newest timestamp WITHIN the supplied
records — deterministic, no wall clock; an unparseable timestamp degrades to full weight, never
raises). Counts are normalized `log1p(count)/log1p(max)` into [0,1]. Ids are the stable logged
result ids — replay-consistent within a log epoch (element ids churn on a graph rebuild; a live
use, out of scope, would need file+name keying).

The re-rank is MULTIPLICATIVE — `adjusted = rrf_score x (1 + beta * prior)` — preserving
Weighted-RRF's scale-freedom with an exact beta=0 identity (the parity keystone gate), beta on a
small grid that INCLUDES 0.0 so "no change" is always selectable.

CIRCULARITY, NAMED: the prior is learned FROM accesses and the evaluation qrels are DERIVED from
accesses. Even with strict query-level splits (a fold's prior is built from TRAIN-fold access
signal only — leak-gated), "the user keeps working in the same files" means focus persistence —
not ranking quality — explains part of any win, and every access carries the baseline ranking's
position bias. Mitigations: query-level k-fold splits, `prior_hit_rate` reported as the exposure
statistic, beta capped at 1.0, and the persisted verdict is LOG-REPLAY EVIDENCE ONLY — any
activation additionally requires the benchmark twin-capture A/B (the learned_fusion G4
discipline).

Operator driver (offline, no Neo4j):
    import access_prior, retrieval_log
    print(access_prior.evaluate_and_record(r"c:/path/to/project"))
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
from retrieval import _weighted_rrf, RRF_K  # noqa: E402  (the REAL fusion — imported, frozen)
import retrieval_log as _rl                 # noqa: E402
import tune_weights as _tw                  # noqa: E402
import instance_state as _is                # noqa: E402

ARM_NAMES = tuple(_rl.ARM_NAMES)
EQUAL_WEIGHTS = {a: 1.0 for a in ARM_NAMES}
DEFAULT_BETA_GRID = (0.0, 0.1, 0.25, 0.5, 1.0)
DEFAULT_HALF_LIFE_DAYS = 14.0
DEFAULT_MIN_LABELED = 40

CIRCULARITY_NOTE = ("circularity: the prior is learned FROM accesses and the qrels are DERIVED "
                    "from accesses — focus persistence and position bias inflate any measured "
                    "win even under strict query-level splits; prior_hit_rate is the exposure "
                    "statistic. Log-replay evidence only.")
ACTIVATION_NOTE = ("OFFLINE experiment — never wired into live search; any future activation "
                   "additionally requires the benchmark twin-capture A/B (the same rule as learned "
                   "fusion: learned weights must beat equal weights on the benchmark). applied stays false.")


def _parse_utc(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _accumulate(pairs, half_life_days: float) -> dict:
    """Core prior math over [(graded_map, ts_str), ...]: grade x recency-decay, then
    log1p-normalized into [0,1]. The decay reference is the newest PARSEABLE ts within `pairs`
    (deterministic); an unparseable ts contributes at full weight."""
    parsed = [(gm, _parse_utc(ts)) for gm, ts in pairs]
    ref = max((t for _, t in parsed if t is not None), default=None)
    counts: dict = {}
    for gm, t in parsed:
        if not isinstance(gm, dict):
            continue
        decay = 1.0
        if ref is not None and t is not None:
            age_days = max(0.0, (ref - t).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / max(1e-9, half_life_days))
        for rid, grade in gm.items():
            if isinstance(grade, (int, float)) and grade > 0:
                counts[rid] = counts.get(rid, 0.0) + float(grade) * decay
    if not counts:
        return {}
    max_c = max(counts.values())
    if max_c <= 0:
        return {}
    return {rid: math.log1p(c) / math.log1p(max_c) for rid, c in counts.items()}


def build_prior(records, *, half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
                grade_weights=None) -> dict:
    """The per-node access prior over a whole log. {} on empty/malformed. Never raises."""
    try:
        folded = _rl.fold_graded(records or [], grade_weights=grade_weights)
        pairs = [(r.get("graded"), r.get("ts")) for r in folded
                 if isinstance(r, dict) and isinstance(r.get("graded"), dict) and r["graded"]]
        return _accumulate(pairs, half_life_days)
    except Exception:
        return {}


def ts_by_qid(records) -> dict:
    """{qid: ts} for the search records (the evaluate/leak-gate helper). Never raises."""
    out = {}
    for r in records or []:
        if isinstance(r, dict) and r.get("type") == "search" and r.get("qid") is not None:
            out[r["qid"]] = r.get("ts")
    return out


def fold_priors(labeled, ts_map, folds, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> list:
    """One prior per fold, each built from the TRAIN complement's graded maps ONLY — the leakage
    rule: a node accessed only by a fold's held-out queries has prior 0.0 in that fold."""
    priors = []
    n = len(labeled)
    for f in range(len(folds)):
        test_idx = set(folds[f])
        pairs = []
        for i in range(n):
            if i in test_idx:
                continue
            q = labeled[i]
            gm = q.get("graded") if isinstance(q.get("graded"), dict) else {}
            if gm:
                pairs.append((gm, (ts_map or {}).get(q.get("qid"))))
        priors.append(_accumulate(pairs, half_life_days))
    return priors


def fuse_scores(per_arm: dict, weights: dict, rrf_k: int | None = None) -> dict:
    """The fused SCORE MAP — built exactly like tune_weights._fuse (ordered ids -> descending
    synthetic scores through the REAL _weighted_rrf, arms in ARM_NAMES order) but returning scores,
    which the re-rank needs. Its insertion order is the fusion's, so rerank's stable sort reproduces
    search()'s tie order. Never raises."""
    try:
        runs = []
        for arm in ARM_NAMES:
            ids = per_arm.get(arm) if isinstance(per_arm, dict) else None
            ids = [i for i in ids if isinstance(i, (str, int, float))] if isinstance(ids, list) else []
            if ids:
                runs.append((arm, {rid: float(-i) for i, rid in enumerate(ids)}))
        return dict(_weighted_rrf(runs, weights, k=(RRF_K if rrf_k is None else rrf_k)))
    except Exception:
        return {}


def rerank(scores: dict, prior: dict, beta: float) -> list:
    """POST-FUSION re-rank: adjusted = score x (1 + beta * prior). beta=0 is an exact identity
    with the fused ordering live search() produces — the same STABLE score sort as search() and
    tune_weights._fuse, so equal scores keep the fusion's insertion order (never an id re-sort)."""
    try:
        pr = prior if isinstance(prior, dict) else {}
        adjusted = {rid: s * (1.0 + beta * pr.get(rid, 0.0)) for rid, s in (scores or {}).items()}
        return [rid for rid, _ in sorted(adjusted.items(), key=lambda kv: kv[1], reverse=True)]
    except Exception:
        return list(scores or {})


def _ndcg(ranked, q, k: int) -> float:
    graded = q.get("graded") or {p: 1.0 for p in q.get("positives", ())}
    return _tw._ndcg_one(ranked, graded, k)


def evaluate(records, *, beta_grid=DEFAULT_BETA_GRID, half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
             n_folds: int = 5, seed: int | None = None, objective: str = "ndcg", k: int = 10,
             min_labeled: int = DEFAULT_MIN_LABELED,
             bootstrap_iters: int = _tw.DEFAULT_BOOTSTRAP_ITERS,
             alpha: float = _tw.DEFAULT_ALPHA, eps: float = _tw.DEFAULT_EPS) -> dict:
    """The OFFLINE evaluation: query-level k-fold; each fold's prior is TRAIN-only (leak-gated);
    beta chosen on train (ties -> the smaller beta, so 'no change' wins when nothing helps);
    out-of-fold per-query nDCG deltas (re-ranked vs plain equal-weight fusion) pooled into the
    same seeded bootstrap the tuner uses. Deterministic; never raises. Verdicts:
    insufficient_data | no_improvement | not_significant | beats_baseline_significant."""
    out = {"experiment": "access_prior", "verdict": "insufficient_data", "n_labeled": 0,
           "min_labeled": min_labeled, "n_folds": 0, "beta_grid": list(beta_grid),
           "chosen_betas": [], "modal_beta": None, "half_life_days": half_life_days,
           "objective": objective, "ndcg_k": k, "cv_mean_delta": None, "per_fold_delta": [],
           "bootstrap": None, "prior_hit_rate": None,
           "circularity_note": CIRCULARITY_NOTE, "note": ACTIVATION_NOTE,
           "generated_utc": _tw._now_iso(), "applied": False}
    try:
        labeled = _tw.build_qrels(records or [])
        n = len(labeled)
        out["n_labeled"] = n
        if n < min_labeled or n < 2:
            return out
        if seed is None:
            seed = _tw._seed_from_qids([q.get("qid") for q in labeled])
        kk = min(max(2, n_folds), n)
        folds = _tw.kfold_indices(n, kk, seed)
        tmap = ts_by_qid(records)
        priors = fold_priors(labeled, tmap, folds, half_life_days)
        deltas, per_fold, chosen = [], [], []
        hits = total_pos = 0
        for f in range(kk):
            test_idx = set(folds[f])
            train = [labeled[i] for i in range(n) if i not in test_idx]
            test = [labeled[i] for i in range(n) if i in test_idx]
            if not train or not test:
                continue
            prior = priors[f]
            best_beta, best_score = 0.0, None
            for beta in beta_grid:
                sc = sum(_ndcg(rerank(fuse_scores(q["per_arm"], EQUAL_WEIGHTS), prior, beta), q, k)
                         for q in train) / len(train)
                if best_score is None or sc > best_score + 1e-12:
                    best_beta, best_score = beta, sc
            chosen.append(best_beta)
            fold_ds = []
            for q in test:
                scores = fuse_scores(q["per_arm"], EQUAL_WEIGHTS)
                base = _ndcg(rerank(scores, {}, 0.0), q, k)
                rr = _ndcg(rerank(scores, prior, best_beta), q, k)
                fold_ds.append(rr - base)
                gm = q.get("graded") if isinstance(q.get("graded"), dict) else {}
                for rid in gm:
                    total_pos += 1
                    if prior.get(rid, 0.0) > 0.0:
                        hits += 1
            deltas.extend(fold_ds)
            per_fold.append(sum(fold_ds) / len(fold_ds) if fold_ds else 0.0)
        ci = _tw.bootstrap_ci(deltas, iters=bootstrap_iters, seed=seed, alpha=alpha)
        out["n_folds"] = len(per_fold)
        out["chosen_betas"] = chosen
        out["modal_beta"] = max(set(chosen), key=lambda b: (chosen.count(b), -b)) if chosen else None
        out["cv_mean_delta"] = ci["mean"]
        out["per_fold_delta"] = per_fold
        out["bootstrap"] = {"ci_lo": ci["lo"], "ci_hi": ci["hi"], "mean": ci["mean"],
                            "iters": ci["iters"], "alpha": ci["alpha"]}
        out["prior_hit_rate"] = (hits / total_pos) if total_pos else 0.0
        if ci["mean"] <= eps:
            out["verdict"] = "no_improvement"
        elif ci["lo"] <= 0.0:
            out["verdict"] = "not_significant"
        else:
            out["verdict"] = "beats_baseline_significant"
        return out
    except Exception:
        return out


def evaluate_and_record(root, *, records=None, **kw) -> dict:
    """evaluate + persist under instance.json experiments.access_prior (applied FORCED false by
    instance_state.write_experiment). Returns the result + {written}. Never raises."""
    try:
        if records is None:
            records = _rl.read_records(root) if root is not None else []
        res = evaluate(records, **kw)
        path = _is.write_experiment(root, "access_prior", res)
        res = dict(res)
        res["written"] = path is not None
        return res
    except Exception:
        return {"experiment": "access_prior", "verdict": "insufficient_data", "written": False,
                "applied": False}


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    result = evaluate_and_record(root)
    print(f"access_prior experiment: verdict={result.get('verdict')} "
          f"n_labeled={result.get('n_labeled')} written={result.get('written')} (applied:false)")
