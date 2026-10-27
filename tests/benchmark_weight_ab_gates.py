#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for benchmark/weight_ab.py — the RM-1 G4 evidence bridge.

The learned-vs-equal A/B on the gold benchmark MUST reuse the FROZEN deterministic scorer
(score_completeness) and must never call a spend-y judge. Locks:
  - ab_env / ab_arm_key: the ON-arm injection seam ('learned' sets DEEPMAP_APPLY_LEARNED, 'equal'
    removes it) + namespaced arm keys,
  - deterministic_ab REUSES score_completeness (identity), computes overall + per-slice completeness
    deltas, and a bootstrap significance verdict — no judge, no network,
  - equal answers == learned answers -> zero delta, NOT significant (no false G4 win),
  - build_g4_evidence shapes present/significant/pre-registered-slice,
  - write_benchmark_evidence merges into instance.json learned_fusion (zero-egress, never raises),
  - the evidence gates apply_learned: apply refuses without it, applies with a significant one.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
BENCH = REPO / "benchmark"

_PASS = 0
_FAIL = 0

QUESTIONS = [
    {"id": "q1", "family": "multi_hop", "ground_truth_set": ["charge", "refund", "void"]},
    {"id": "q2", "family": "multi_hop", "ground_truth_set": ["alpha", "beta"]},
    {"id": "q3", "family": "transitive", "ground_truth_set": ["gamma"]},
]
EQUAL_ANS = {"q1": "charge", "q2": "alpha", "q3": "(nothing relevant)"}
LEARNED_ANS = {"q1": "charge refund void", "q2": "alpha beta", "q3": "gamma"}


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def gate_env_seam(W) -> None:
    print("\n-- ab_env / ab_arm_key: the ON-arm injection seam --")
    check("ab_arm_key equal", W.ab_arm_key("equal") == "deepmap_on@equal")
    check("ab_arm_key learned", W.ab_arm_key("learned") == "deepmap_on@learned")
    learned = W.ab_env("learned", {"NEO4J_PASSWORD": "x"})
    check("learned variant sets DEEPMAP_APPLY_LEARNED=1", learned.get("DEEPMAP_APPLY_LEARNED") == "1")
    check("learned variant preserves the base env", learned.get("NEO4J_PASSWORD") == "x")
    equal = W.ab_env("equal", {"NEO4J_PASSWORD": "x", "DEEPMAP_APPLY_LEARNED": "1"})
    check("equal variant REMOVES DEEPMAP_APPLY_LEARNED", "DEEPMAP_APPLY_LEARNED" not in equal)


def gate_reuses_scorer(W) -> None:
    print("\n-- deterministic_ab reuses the FROZEN score_completeness (no judge, no spend) --")
    import score_completeness as sc  # the real module
    check("weight_ab._sc IS benchmark.score_completeness", W._sc.score_completeness is sc.score_completeness)
    scored = W.score_answer_set(LEARNED_ANS, QUESTIONS)
    check("learned answers fully enumerate q1 (100%)", scored["q1"] == 100.0, str(scored))
    check("equal answers partially enumerate q1 (33.33%)", W.score_answer_set(EQUAL_ANS, QUESTIONS)["q1"] == 33.33,
          str(W.score_answer_set(EQUAL_ANS, QUESTIONS)))


def gate_ab_and_significance(W) -> None:
    print("\n-- deterministic_ab: learned > equal -> positive, significant, per-slice --")
    ab = W.deterministic_ab(EQUAL_ANS, LEARNED_ANS, QUESTIONS)
    check("overall completeness delta > 0", ab["overall_completeness_delta"] > 0, str(ab["overall_completeness_delta"]))
    check("statistically significant (CI lo > 0)", ab["significant"] is True and ab["bootstrap"]["ci_lo"] > 0, str(ab["bootstrap"]))
    check("per-slice keyed by family", set(ab["per_slice"]) == {"multi_hop", "transitive"}, str(list(ab["per_slice"])))
    check("multi_hop slice records n=2", ab["per_slice"]["multi_hop"]["n"] == 2)
    # No-op A/B: learned == equal -> zero delta, NOT significant.
    ab0 = W.deterministic_ab(EQUAL_ANS, EQUAL_ANS, QUESTIONS)
    check("equal==learned -> zero overall delta", ab0["overall_completeness_delta"] == 0.0)
    check("equal==learned -> NOT significant (no false win)", ab0["significant"] is False)
    # Determinism.
    check("deterministic (same inputs -> identical result)", W.deterministic_ab(EQUAL_ANS, LEARNED_ANS, QUESTIONS) == ab)


def gate_evidence_and_apply(W, T) -> None:
    print("\n-- build_g4_evidence + write_benchmark_evidence gate apply_learned --")
    ab = W.deterministic_ab(EQUAL_ANS, LEARNED_ANS, QUESTIONS)
    ev = W.build_g4_evidence(ab, pre_registered_slice="multi_hop", generated_utc="2026-07-03T00:00:00Z",
                             weights_fingerprint=T.learned_weights_fingerprint(
                                 {"arm_weights": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}}))
    check("evidence present + significant", ev["present"] is True and ev["significant"] is True)
    check("evidence carries the pre-registered slice delta", ev["slice_delta"] == ab["per_slice"]["multi_hop"]["delta"])
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Seed a learned_fusion that beats+significant but has NO benchmark evidence yet.
        d = root / ".deepmap"; d.mkdir(parents=True)
        (d / "instance.json").write_text(json.dumps({"learned_fusion": {
            "applied": False, "beats_equal": True, "significant": True,
            "arm_weights": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}}}), encoding="utf-8")
        ok, _ = T.apply_learned(root, require_benchmark=True)
        check("apply_learned REFUSES before benchmark evidence", ok is False)
        out = W.write_benchmark_evidence(root, ev)
        check("write_benchmark_evidence wrote instance.json", out is not None)
        lf = json.loads((d / "instance.json").read_text(encoding="utf-8"))["learned_fusion"]
        check("benchmark_evidence merged (arm_weights preserved)", "benchmark_evidence" in lf and "arm_weights" in lf)
        ok, reason = T.apply_learned(root, require_benchmark=True)
        check("apply_learned APPLIES once significant evidence is present", ok is True, reason)
        check("never raises on a bad root", W.write_benchmark_evidence("\x00::bad::", ev) is None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 G4 benchmark-bridge gates (deterministic, reuses scorer; no spend)")
    print("=" * 68)
    if str(BENCH) not in sys.path:
        sys.path.insert(0, str(BENCH))
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    W = _load("weight_ab", BENCH)
    T = _load("tune_weights", BUILD)

    gate_env_seam(W)
    gate_reuses_scorer(W)
    gate_ab_and_significance(W)
    gate_evidence_and_apply(W, T)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
