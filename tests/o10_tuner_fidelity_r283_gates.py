#!/usr/bin/env python3
"""0.28.3 (O10) — the RM-1 offline tuner, drift guard and replays judge the rankings LIVE search produces.

Findings (DeepInit refresh 2026-09-29, validated):
  * D3 — tune_weights._fuse re-sorted equal-score RRF ties by id, while live search() keeps _weighted_rrf's
    stable insertion order. Reproduced: per_arm {vector:[x,p], bm25:[p,x], graph:[x,p]} at the LEARNED
    weights (0.5, 1.0, 0.5) is an exact fused tie; search() ranks x first, the tuner ranked p first — so the
    tuner reported "learned beats equal, significant" for weights that change nothing live.
    access_prior.rerank copied the same id tie-break. And with --tune-rrf-k the significance calc, the
    per-slice / profile judgements and the drift guard evaluated at a k live never fuses at (always 60).
  * D4 — --seed-from-prior / DEEPMAP_SEED_PRIOR never changed the weights on the default GRID optimizer
    (the prior only moved coordinate descent), though the CLI/runbook promise a warm start.
  * C4 — retrieval_log.per_arm_candidates (the "fusion-lossless" log) and the offline graph_signal /
    confidence_centrality replays fetched top_k*3 per arm while search() fetches
    max(top_k*3, DEEPMAP_CANDIDATE_FLOOR).

Locks (hermetic; the REAL DeepMapRetriever.search() on canned arms is the oracle):
  - tune_weights._fuse == search() order on 300 tie-heavy random inputs (incl. the validator's example);
    access_prior.rerank(beta=0) == search() order; the learned weights on the tie fixture rank the accessed
    result first IN search(),
  - every learned-vs-equal judgement (cross_val_delta, per-slice, profile gain, drift guard) fuses at 60 even
    when the tuned/stored k is 30 — which stays RECORDED (rrf_k / candidate_rrf_k) as a recommendation,
  - a seed prior moves the GRID result to the best-scoring candidate closest to it (and the CV folds use the
    same seeded procedure), while the UNSEEDED tuner output is byte-identical on a tie-free fixture,
  - capture_search / maybe_log_search fetch the SAME per-arm depth search() used (floor honoured) so the
    logged pools re-fuse to search()'s exact top-k; the replays fetch that depth too; unset floor -> top_k*3.

Usage: python tests/o10_tuner_fidelity_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
ARMS = ("vector", "bm25", "graph_weighted")
EQUAL = {a: 1.0 for a in ARMS}
TIE = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}

# A fixture with NO exact fused-score tie for ANY grid candidate (checked below), so D3 cannot move it;
# the expected values are the pre-fix (0.28.2) unseeded tuner output. Weights/verdict/folds compare
# exactly; the CV/CI floats to 1e-12 relative — nDCG divides by math.log2, whose last bit differs between
# libms (MSVC vs glibc measured: ...225 vs ...222), while any real change moves them by orders more.
TIE_FREE = [({"vector": ["c", "e", "a", "f", "d"], "bm25": ["a"], "graph_weighted": ["c", "a", "b"]}, "a"),
            ({"vector": ["g", "d"], "bm25": ["d", "b", "e"], "graph_weighted": ["e", "d", "c", "a", "b"]}, "g")]
TIE_FREE_EXPECT = {"arm_weights": {"vector": 1.0, "bm25": 0.5, "graph_weighted": 0.5},
                   "reason": "learned_beats_equal", "significant": True, "n_folds": 5,
                   "cv_mean_delta": 0.10012659774903225,
                   "ci_lo": 0.06675106516602149, "ci_hi": 0.13350213033204297}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(root: Path):
    p = str(root / "build")
    if p not in sys.path:
        sys.path.insert(0, p)
    import retrieval as R                  # noqa: E402
    import retrieval_log as L              # noqa: E402
    import tune_weights as T               # noqa: E402
    import access_prior as AP              # noqa: E402
    import graph_signal_ab as GS           # noqa: E402
    import confidence_centrality_ab as CC  # noqa: E402
    return R, L, T, AP, GS, CC


def _fake(R, per_arm: dict, weights: dict | None = None):
    """A driverless DeepMapRetriever: the REAL search()/_weighted_rrf over canned per-arm rankings."""
    class Fake(R.DeepMapRetriever):
        def __init__(self):
            self.arm_weights = dict(R.DEFAULT_ARM_WEIGHTS)
            self.arm_weights.update(weights or {})
            self.embed_fn = lambda q: [0.0]
            self._use_search_clause = True
            self.calls: list[tuple[str, int]] = []

        def _embed_or_none(self, query):
            return [0.0]

        def _rows(self, arm, k):
            self.calls.append((arm, k))
            ids = per_arm.get(arm) or []
            return [{"id": i, "name": i, "score": float(len(ids) - n)} for n, i in enumerate(ids)][:k]

        def _arm_vector(self, qvec, k):
            return self._rows("vector", k)

        def _arm_fulltext(self, query, k):
            return self._rows("bm25", k)

        def _arm_graph_weighted(self, qvec, k):
            return self._rows("graph_weighted", k)

        def _hydrate(self, top_ids, scores):
            return [{"id": i, "name": i, "file": f"f/{i}.py", "line": 1, "score": scores.get(i)} for i in top_ids]

    return Fake()


def _live_order(R, per_arm, weights):
    n = len(set(sum((per_arm.get(a) or [] for a in ARMS), [])))
    return [r["id"] for r in _fake(R, per_arm, weights).search("q", top_k=max(1, n))]


def _log(L, shapes, reps):
    recs, i = [], 0
    for _ in range(reps):
        for per_arm, pos in shapes:
            ids = list(dict.fromkeys(sum(per_arm.values(), [])))
            returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ids]
            recs.append(L.build_record(f"q{i}", per_arm, returned, ts=f"t{i:04d}"))
            recs.append({"type": "access", "v": 2, "paths": [f"f/{pos}.py"], "symbols": [], "kind": "edit",
                         "ts": f"t{i:04d}b"})
            i += 1
    return recs


# ---- D3 --------------------------------------------------------------------------------------------
def gate_production_order(R, L, T, AP) -> None:
    print("\n-- D3: the tuner and the re-ranker rank EXACTLY like live search() (stable tie order) --")
    example = {"vector": ["Z", "M"], "bm25": ["A", "Q"]}
    check("validator example: _fuse == search() == [Z, A, M, Q]",
          T._fuse(example, EQUAL) == _live_order(R, example, EQUAL) == ["Z", "A", "M", "Q"],
          f"{T._fuse(example, EQUAL)} vs {_live_order(R, example, EQUAL)}")
    rng = random.Random(20260929)
    pool = [f"n{i}" for i in range(7)]
    grid = T.candidate_weights(0.5, 0.25)
    bad_fuse = bad_rerank = 0
    first = ""
    for trial in range(300):
        per_arm = {a: rng.sample(pool, rng.randint(0, 4)) for a in ARMS}
        w = EQUAL if trial % 3 == 0 else rng.choice(grid)
        live = _live_order(R, per_arm, w)
        tuned = T._fuse(per_arm, w)
        rr = AP.rerank(AP.fuse_scores(per_arm, w), {pool[0]: 0.7, pool[3]: 0.2}, 0.0)
        if tuned != live:
            bad_fuse += 1
            first = first or f"{per_arm} w={w}: tuner {tuned} vs live {live}"
        if rr != live:
            bad_rerank += 1
    check("300 tie-heavy random inputs: tune_weights._fuse == search() order", bad_fuse == 0,
          f"{bad_fuse} mismatches, e.g. {first}")
    check("300 tie-heavy random inputs: access_prior.rerank(beta=0) == search() order", bad_rerank == 0,
          f"{bad_rerank} mismatches")

    learned = {"vector": 0.5, "bm25": 1.0, "graph_weighted": 0.5}
    q = {"per_arm": TIE, "positives": {"p"}}
    check("the exact-tie weights (0.5,1.0,0.5): search() ranks the decoy first -> tuner MRR 0.5, not 1.0",
          _live_order(R, TIE, learned)[0] == "x" and abs(T.mrr([q], learned) - 0.5) < 1e-12,
          f"live {_live_order(R, TIE, learned)} tuner-mrr {T.mrr([q], learned)}")
    res = T.tune_profiles(records=_log(L, [(TIE, "p")], 12), min_labeled=4)
    w = res["arm_weights"]
    check("the learned weights the tuner reports as a significant win rank the accessed result FIRST in search()",
          res["beats_equal"] is True and _live_order(R, TIE, w)[0] == "p", f"{w} -> {_live_order(R, TIE, w)}")
    # From equal, every single / pairwise coord step lands exactly ON the x/p tie (live keeps x first):
    # the opt-in coord optimizer must cross it with an all-arm step, not "win" through an id tie-break.
    res = T.tune_profiles(records=_log(L, [(TIE, "p")], 12), min_labeled=4, optimizer="coord")
    w = res["arm_weights"]
    check("coord (opt-in) escapes the two-decoy-arm saddle with a win that is REAL in search()",
          res["beats_equal"] is True and _live_order(R, TIE, w)[0] == "p", f"{w} -> {_live_order(R, TIE, w)}")


def gate_judged_at_live_k(L, T) -> None:
    print("\n-- D3: every learned-vs-equal judgement fuses at RRF_K=60; a tuned k is only RECORDED --")
    fixed = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 0.5}
    seen: list = []
    real_opt, real_qs = T._optimize, T._query_score

    def fake_opt(labeled, **kw):
        return dict(fixed), 1.0, 30

    def spy_qs(q, weights, *, objective, k, rrf_k=None):
        seen.append(rrf_k)
        return real_qs(q, weights, objective=objective, k=k, rrf_k=rrf_k)

    T._optimize, T._query_score = fake_opt, spy_qs
    try:
        res = T.tune_profiles(records=_log(L, [(TIE, "p")], 16), min_labeled=4, min_labeled_bucket=2,
                              tune_rrf_k=True)
    finally:
        T._optimize, T._query_score = real_opt, real_qs
    at = sorted({str(x) for x in seen})
    check("tune_profiles(--tune-rrf-k): CV, per-slice and profile judgements all fuse at 60",
          bool(seen) and all(x in (None, 60) for x in seen), f"k values used: {at}")
    check("... the tuned k stays recorded as a recommendation (candidate_rrf_k / rrf_k == 30)",
          res.get("candidate_rrf_k") == 30 and res.get("rrf_k") == 30, str({x: res.get(x) for x in ("rrf_k", "candidate_rrf_k")}))

    import tempfile
    import json
    seen.clear()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".deepmap").mkdir()
        (root / ".deepmap" / "instance.json").write_text(json.dumps({"learned_fusion": {
            "arm_weights": dict(fixed), "beats_equal": True, "significant": True, "objective": "ndcg",
            "ndcg_k": 10, "rrf_k": 30, "profiles": {"default": {"arm_weights": dict(fixed), "rrf_k": 30}}}}),
            encoding="utf-8")
        T._query_score = spy_qs
        try:
            d = T.check_drift(root, records=_log(L, [(TIE, "p")], 25))
        finally:
            T._query_score = real_qs
    check("check_drift with a stored rrf_k=30 (+ a profile row at 30) still judges at 60",
          d.get("verdict") in ("ok", "degraded") and bool(seen) and all(x in (None, 60) for x in seen),
          f"verdict {d.get('verdict')}, k values used: {sorted({str(x) for x in seen})}")


# ---- D4 --------------------------------------------------------------------------------------------
def _l1(w, ref):
    return sum(abs(w[a] - ref[a]) for a in ARMS)


def gate_seed_moves_grid(L, T, R) -> None:
    print("\n-- D4: a seed prior moves the GRID result (tie-break toward the prior); unseeded unchanged --")
    recs = _log(L, [(TIE, "p")], 12)
    labeled = T.build_qrels(recs)
    seed = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}
    scored = [(T.score_objective(labeled, w), w) for w in T.candidate_weights(0.5, 0.25)]
    top = max(s for s, _ in scored)
    plateau = [w for s, w in scored if s == top]
    want = max(plateau, key=lambda w: (-_l1(w, seed), -w["vector"], -w["bm25"], -w["graph_weighted"]))
    unseeded = T.tune_profiles(records=recs, min_labeled=4)
    seeded = T.tune_profiles(records=recs, min_labeled=4, seed_prior=dict(seed))
    check("the fixture leaves a plateau of equally-scoring grid candidates", len(plateau) > 3, str(len(plateau)))
    check("seeded GRID tune picks the plateau member closest to the prior", seeded["arm_weights"] == want,
          f"{seeded['arm_weights']} vs {want}")
    check("... which differs from the unseeded pick (the seed is not a no-op)",
          seeded["arm_weights"] != unseeded["arm_weights"], f"{seeded['arm_weights']} == {unseeded['arm_weights']}")
    check("... and is still a significant, production-verified win",
          seeded["significant"] is True and _live_order(R, TIE, seeded["arm_weights"])[0] == "p")
    far = {"vector": 5.0, "bm25": 0.1, "graph_weighted": 9.0}
    out = T.tune_profiles(records=recs, min_labeled=4, seed_prior=dict(far))["arm_weights"]
    check("a far-away seed never leaves the equal-anchored envelope", all(0.5 <= v <= 1.5 for v in out.values()),
          str(out))

    calls: list = []
    real = T._optimize

    def spy(labeled_, **kw):
        calls.append(dict(kw))
        return real(labeled_, **kw)

    T._optimize = spy
    try:
        T.tune_profiles(records=recs, min_labeled=4, seed_prior=dict(seed))
    finally:
        T._optimize = real
    anchored = [c for c in calls if c.get("anchor") == seed]
    check("the CV folds re-tune with the SAME seeded procedure as the global fit",
          len(anchored) >= 1 + 2 and all(c.get("prior") == seed for c in anchored),
          f"{len(anchored)} of {len(calls)} _optimize calls anchored to the seed")

    tf = _log(L, TIE_FREE, 12)
    res = T.tune_profiles(records=tf, min_labeled=4)
    tie_free = True
    for w in list(T.candidate_weights(0.5, 0.25)) + [EQUAL]:
        for per_arm, _pos in TIE_FREE:
            runs = [(a, {rid: float(-n) for n, rid in enumerate(per_arm[a])}) for a in ARMS if per_arm.get(a)]
            vals = list(R._weighted_rrf(runs, w).values())
            tie_free = tie_free and len(set(vals)) == len(vals)
    check("the unseeded reference fixture has no exact fused tie for any grid candidate", tie_free)
    got = {"arm_weights": res["arm_weights"], "reason": res["reason"], "significant": res["significant"],
           "n_folds": res["n_folds"], "cv_mean_delta": res["cv_mean_delta"],
           "ci_lo": res["bootstrap"]["ci_lo"], "ci_hi": res["bootstrap"]["ci_hi"]}
    floats = ("cv_mean_delta", "ci_lo", "ci_hi")
    same = (all(got[x] == TIE_FREE_EXPECT[x] for x in got if x not in floats)
            and all(math.isclose(got[x], TIE_FREE_EXPECT[x], rel_tol=1e-12, abs_tol=0.0) for x in floats))
    check("UNSEEDED tune_profiles output is unchanged from 0.28.2 (weights, verdict, folds; CV/CI to 1e-12)",
          same, f"{got} vs {TIE_FREE_EXPECT}")
    check("seed_prior=None is the unseeded path", T.tune_profiles(records=tf, min_labeled=4, seed_prior=None)
          ["arm_weights"] == res["arm_weights"])


# ---- C4 --------------------------------------------------------------------------------------------
class _Env:
    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_candidate_floor(R, L, GS, CC) -> None:
    print("\n-- C4: the log and the replays fetch the SAME per-arm depth search() fetched --")
    fk = getattr(L, "fetch_k_for", None)
    with _Env(DEEPMAP_CANDIDATE_FLOOR="100"):
        check("fetch_k_for(15) honours the floor (100)", callable(fk) and fk(15) == 100, str(fk(15) if callable(fk) else None))
    with _Env(DEEPMAP_CANDIDATE_FLOOR=None):
        check("fetch_k_for(15) with no floor == 45 (the default, unchanged)", callable(fk) and fk(15) == 45)
    with _Env(DEEPMAP_CANDIDATE_FLOOR="junk"):
        check("an invalid floor is ignored (45), exactly like search()", callable(fk) and fk(15) == 45)

    # Deep canned arms: the graph arm's top ids sit at vector ranks 61-100, so a 45-deep log loses their
    # vector contribution and cannot re-fuse to search()'s top-15.
    vec = [f"A{i}" for i in range(120)]
    bm = [f"B{i}" for i in range(120)]
    graph = [f"A{i}" for i in range(60, 120)] + [f"B{i}" for i in range(60)]
    per_arm = {"vector": vec, "bm25": bm, "graph_weighted": graph}
    for floor, depth in (("100", 100), (None, 45)):
        with _Env(DEEPMAP_CANDIDATE_FLOOR=floor):
            r = _fake(R, per_arm)
            results, logged = L.capture_search(r, "q", top_k=15)
            search_k = {k for a, k in r.calls[:3]}
            log_k = {k for a, k in r.calls[3:]}
            runs = [(a, {rid: float(-n) for n, rid in enumerate(ids)}) for a, ids in logged.items() if ids]
            fused = R._weighted_rrf(runs, R.DEFAULT_ARM_WEIGHTS)
            refused = [i for i, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)][:15]
            live = [x["id"] for x in results]
            tag = f"floor={floor}"
            check(f"{tag}: search() fetched {depth} per arm", search_k == {depth}, str(search_k))
            check(f"{tag}: the logged pools were fetched at the SAME depth", log_k == search_k, f"{log_k} vs {search_k}")
            check(f"{tag}: the logged pools re-fuse to search()'s exact top-15 (fusion-lossless)", refused == live,
                  f"{refused[:5]} vs {live[:5]}")

    recs = []
    for i in range(3):
        pa = {"vector": ["p", "x"], "bm25": ["x", "p"], "graph_weighted": ["p"]}
        recs.append(L.build_record(f"where is the parser {i}", pa,
                                   [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("p", "x")],
                                   ts=f"t{i}"))
        recs.append({"type": "access", "v": 2, "paths": ["f/p.py"], "symbols": [], "kind": "edit", "ts": f"t{i}b"})
    for mod, name in ((GS, "graph_signal_ab"), (CC, "confidence_centrality_ab")):
        for floor, depth in (("100", 100), (None, 45)):
            ks: list = []

            def qfn(cypher, params, _ks=ks):
                _ks.append(params.get("k"))
                return [{"id": "p", "score": 0.9}, {"id": "x", "score": 0.4}]

            with _Env(DEEPMAP_CANDIDATE_FLOOR=floor):
                out = mod.replay(recs, query_fn=qfn, embed_fn=lambda t: [0.1, 0.2], top_k=15, min_labeled=1,
                                 bootstrap_iters=50)
            check(f"{name}.replay floor={floor}: the recomputed graph arm fetched {depth} per index",
                  bool(ks) and set(ks) == {depth} and out.get("n_replayed") == 3, f"{sorted(set(ks))} {out.get('verdict')}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — O10: the offline tuner judges live search()'s rankings (root: {root})")
    print("=" * 72)
    R, L, T, AP, GS, CC = _load(root)
    with _Env(DEEPMAP_CANDIDATE_FLOOR=None):
        gate_production_order(R, L, T, AP)
        gate_judged_at_live_k(L, T)
        gate_seed_moves_grid(L, T, R)
    gate_candidate_floor(R, L, GS, CC)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
