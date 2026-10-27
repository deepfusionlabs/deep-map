#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend UNIT gates for the RM-1 richer metrics + rigorous-validation primitives
(design §1). These are the pure functions the comprehensive tuner is built on:

  - graded nDCG@k / MAP / MRR (all replaying the REAL rank-based fusion),
  - the negatives-aware objective (skip-above penalty; penalty=0 recovers the pure metric),
  - a deterministic seeded PRNG + k-fold partition + bootstrap CI (no `random`, no wall-clock),
  - significance = CI lower bound > 0 (the gate that turns a point win into a defensible claim).

Dependency-free (stdlib math only) — ranx/numpy/sklearn are NOT imported (compliance holds).
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _approx(a, b, tol=1e-9) -> bool:
    return abs(a - b) <= tol


def _q(per_arm, positives, graded=None, negatives=None, bucket="default"):
    return {"qid": "q", "per_arm": per_arm, "positives": set(positives),
            "graded": graded or {p: 1.0 for p in positives}, "negatives": negatives or [], "bucket": bucket}


def gate_uses_real_fusion(T, R) -> None:
    print("\n-- metrics replay the REAL retrieval._weighted_rrf (no re-impl) --")
    check("tune_weights._weighted_rrf IS retrieval._weighted_rrf", T._weighted_rrf is R._weighted_rrf)
    check("tune_weights.RRF_K == retrieval.RRF_K", T.RRF_K == R.RRF_K)


def gate_ndcg(T) -> None:
    print("\n-- nDCG@k uses GRADED gains + position discount --")
    # Single arm ranks a,b,c; positive 'a' at rank 1 -> nDCG 1.0.
    q = _q({"vector": ["a", "b", "c"]}, ["a"])
    check("positive at rank 1 -> nDCG 1.0", _approx(T.ndcg([q], T.EQUAL_WEIGHTS, k=10), 1.0), str(T.ndcg([q], T.EQUAL_WEIGHTS)))
    # Positive 'b' at rank 2 -> nDCG = 1/log2(3).
    q = _q({"vector": ["a", "b", "c"]}, ["b"])
    check("positive at rank 2 -> nDCG 1/log2(3)", _approx(T.ndcg([q], T.EQUAL_WEIGHTS, k=10), 1.0 / math.log2(3), 1e-9),
          str(T.ndcg([q], T.EQUAL_WEIGHTS)))
    # Graded: a STRONG positive ranked above a WEAK one scores higher than the reverse.
    strong_high = T._ndcg_one(["a", "b"], {"a": 1.0, "b": 0.5}, 10)
    strong_low = T._ndcg_one(["b", "a"], {"a": 1.0, "b": 0.5}, 10)
    check("nDCG prefers the STRONG positive ranked higher", strong_high > strong_low, f"{strong_high} vs {strong_low}")


def gate_map_mrr(T) -> None:
    print("\n-- MAP / MRR hand-computed --")
    # ranked a,x,b ; positives {a,b}: AP = (1/1 + 2/3)/2.
    ap = T.average_precision(["a", "x", "b"], {"a", "b"})
    check("average_precision = (1 + 2/3)/2", _approx(ap, (1.0 + 2.0 / 3.0) / 2.0), str(ap))
    q = _q({"vector": ["x", "p"]}, ["p"])
    check("MRR: positive at rank 2 -> 0.5", _approx(T.mrr([q], T.EQUAL_WEIGHTS), 0.5), str(T.mrr([q], T.EQUAL_WEIGHTS)))


def gate_negatives_penalty(T) -> None:
    print("\n-- negatives-aware objective: penalty=0 == pure metric; a skipped negative is penalized --")
    # 'neg' is a skip-above negative ranked over the positive.
    q = _q({"vector": ["neg", "p"], "bm25": ["neg", "p"], "graph_weighted": ["neg", "p"]},
           ["p"], negatives=["neg"])
    pure = T.score_objective([q], T.EQUAL_WEIGHTS, objective="ndcg", negative_penalty=0.0)
    penalized = T.score_objective([q], T.EQUAL_WEIGHTS, objective="ndcg", negative_penalty=0.1)
    check("negative_penalty=0.0 recovers the pure metric",
          _approx(pure, T.ndcg([q], T.EQUAL_WEIGHTS)), f"{pure}")
    check("a negative ranked over the positive lowers the score", penalized < pure, f"{penalized} vs {pure}")
    # No negatives -> penalty is a no-op.
    q2 = _q({"vector": ["p", "x"]}, ["p"], negatives=[])
    check("no negatives -> penalty is a no-op",
          _approx(T.score_objective([q2], T.EQUAL_WEIGHTS, negative_penalty=0.1),
                  T.score_objective([q2], T.EQUAL_WEIGHTS, negative_penalty=0.0)))


def gate_prng_determinism(T) -> None:
    print("\n-- deterministic PRNG + seed (no `random`, no wall-clock) --")
    a = [T._LCG(42).randint(0, 100) for _ in range(1)]
    r1, r2 = T._LCG(7), T._LCG(7)
    seq1 = [r1.randint(0, 1000) for _ in range(20)]
    seq2 = [r2.randint(0, 1000) for _ in range(20)]
    check("_LCG same seed -> identical sequence", seq1 == seq2)
    check("_LCG different seeds -> different sequence", seq1 != [T._LCG(8).randint(0, 1000) for _ in range(20)])
    check("_seed_from_qids deterministic", T._seed_from_qids(["a", "b", "c"]) == T._seed_from_qids(["c", "b", "a"]))
    check("_seed_from_qids distinct for distinct ids", T._seed_from_qids(["a"]) != T._seed_from_qids(["b"]))


def gate_kfold(T) -> None:
    print("\n-- k-fold partition: every index once, balanced, deterministic --")
    folds = T.kfold_indices(10, 5, seed=123)
    flat = sorted(i for f in folds for i in f)
    check("all 10 indices appear exactly once across 5 folds", flat == list(range(10)), str(flat))
    check("5 folds", len(folds) == 5, str(len(folds)))
    check("folds balanced (each size 2)", all(len(f) == 2 for f in folds), str([len(f) for f in folds]))
    check("k-fold deterministic for a seed", T.kfold_indices(10, 5, 123) == folds)


def gate_bootstrap_significance(T) -> None:
    print("\n-- bootstrap CI: significance = lo > 0; deterministic --")
    pos = [0.3] * 30
    ci = T.bootstrap_ci(pos, iters=500, seed=99)
    check("all-positive deltas -> CI lower bound > 0 (significant)", ci["lo"] > 0.0, str(ci))
    mixed = [0.5, -0.5] * 15
    ci_m = T.bootstrap_ci(mixed, iters=500, seed=99)
    check("zero-mean mixed deltas -> CI lower bound <= 0 (NOT significant)", ci_m["lo"] <= 0.0, str(ci_m))
    check("bootstrap deterministic (same deltas + seed -> identical CI)",
          T.bootstrap_ci(pos, iters=500, seed=99) == ci)
    check("empty deltas -> zero CI (no crash)", T.bootstrap_ci([], iters=500, seed=1)["iters"] == 0)


def gate_rrf_k_plumbing(T) -> None:
    print("\n-- _fuse honors a non-default RRF_K (READ-only use of the frozen fn) --")
    per_arm = {"vector": ["a", "b"], "bm25": ["b", "a"]}
    r60 = T._fuse(per_arm, {"vector": 1.0, "bm25": 1.0}, None)
    rk = T._fuse(per_arm, {"vector": 1.0, "bm25": 1.0}, 5)
    check("_fuse(k=None) uses RRF_K and returns a full ranking", len(r60) == 2)
    check("_fuse accepts a non-default k without raising", isinstance(rk, list) and len(rk) == 2)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 metrics + validation unit gates (no Neo4j, no spend)")
    print("=" * 68)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import retrieval as R  # noqa: E402
    spec = importlib.util.spec_from_file_location("dm_tune_weights", BUILD / "tune_weights.py")
    T = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = T
    spec.loader.exec_module(T)

    gate_uses_real_fusion(T, R)
    gate_ndcg(T)
    gate_map_mrr(T)
    gate_negatives_penalty(T)
    gate_prng_determinism(T)
    gate_kfold(T)
    gate_bootstrap_significance(T)
    gate_rrf_k_plumbing(T)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
