#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap enrichment A/B — does OPT-IN LLM enrichment lift concept_to_impl WITHOUT regressing others?

Structurally analogous to weight_ab.py (the RM-1 G4 bridge), with ONE difference the task under-states:
weight_ab flips a SERVER ENV VAR over one identical graph; the enrichment A/B's two arms differ by the
SUMMARY SOURCE baked into the graph (deterministic vs enriched body_summary → embedding), so the capture
is TWO graph builds on the SAME pinned SHA / SAME questions / SAME OFF arm:

  * `deepmap_on@det`      — the ON arm on a graph built with the DETERMINISTIC summaries (default),
  * `deepmap_on@enriched` — the ON arm on a graph re-built after `deepmap enrich` wrote the sidecar.

Scoring REUSES the FROZEN deterministic scorer via weight_ab.deterministic_ab (score_completeness; NO
judge, NO spend). The CLAIM ships iff concept_to_impl LIFTS, the lift is significant, AND NO other family
regresses — judged over the FULL question set (non-regression cannot be proven on the 10 concept
questions alone). The two graph builds + ON-arm captures spend (operator-gated, like G3); everything in
THIS module is free/deterministic and CI-runnable.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_BUILD = _REPO / "build"
for _p in (str(_HERE), str(_BUILD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import weight_ab as _wab          # noqa: E402  (deterministic_ab + score_answer_set — REUSED, not re-impl)
import instance_state as _is      # noqa: E402  (the ONE locked, atomic instance.json writer)


def arm_key(variant: str) -> str:
    """Namespaced ON-arm key so the blind scorer grades each capture independently (like any other arm)."""
    return f"deepmap_on@{'enriched' if str(variant).lower() == 'enriched' else 'det'}"


def enrich_ab(det_answers: dict, enriched_answers: dict, questions: list, *,
              slice_key: str = "family", iters: int = 1000) -> dict:
    """Deterministic A/B of enriched-vs-deterministic ON-arm answers on the FULL question set, sliced by
    family. Pure reuse of weight_ab.deterministic_ab (frozen score_completeness; no judge, no spend) —
    `questions` as shipped in benchmark/questions.yaml (answer_key.ground_truth_set; a flattened
    top-level ground_truth_set works too). `det`=baseline, `enriched`=treatment. Returns overall +
    per-slice deltas + bootstrap significance."""
    return _wab.deterministic_ab(det_answers, enriched_answers, questions, slice_key=slice_key, iters=iters)


def enrich_verdict(ab: dict, *, target: str = "concept_to_impl", regress_eps: float = 0.0) -> dict:
    """The ships-gate: enrichment's CLAIM ships iff the TARGET family (concept_to_impl) lifts, the lift is
    significant, AND no OTHER family regresses beyond -regress_eps. A flat/negative A/B → ships=False
    (reported plainly; a null result is a valid, honest outcome)."""
    ab = ab or {}
    per = ab.get("per_slice", {}) if isinstance(ab.get("per_slice"), dict) else {}
    concept = per.get(target, {}) if isinstance(per.get(target), dict) else {}
    concept_delta = float(concept.get("delta", 0.0) or 0.0)
    regressions = sorted(
        fam for fam, d in per.items()
        if fam != target and isinstance(d, dict) and float(d.get("delta", 0.0) or 0.0) < -regress_eps
    )
    significant = bool(ab.get("significant", False))
    ships = bool(concept_delta > 0 and significant and not regressions)
    return {
        "target": target,
        "concept_delta": round(concept_delta, 4),
        "significant": significant,
        "regressions": regressions,
        "overall_delta": ab.get("overall_completeness_delta", 0.0),
        "n_questions": ab.get("n_questions", 0),
        "ships": ships,
        "scorer": ab.get("scorer"),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_enrich_evidence(ab: dict, verdict: dict, *, model: str | None = None,
                          run_id_det: str | None = None, run_id_enriched: str | None = None,
                          generated_utc: str | None = None) -> dict:
    """Shape the A/B + verdict into an `enrichment_evidence` block (mirrors weight_ab.build_g4_evidence)."""
    return {
        "present": True,
        "ships": bool((verdict or {}).get("ships", False)),
        "concept_delta": (verdict or {}).get("concept_delta"),
        "significant": bool((verdict or {}).get("significant", False)),
        "regressions": (verdict or {}).get("regressions", []),
        "overall_completeness_delta": (ab or {}).get("overall_completeness_delta", 0.0),
        "n_questions": (ab or {}).get("n_questions", 0),
        "per_slice": (ab or {}).get("per_slice"),
        "model": model,
        "run_id_det": run_id_det,
        "run_id_enriched": run_id_enriched,
        "scorer": (ab or {}).get("scorer"),
        "generated_utc": generated_utc or _now_iso(),
    }


def write_enrich_evidence(root, evidence: dict):
    """Merge `enrichment_evidence` into <root>/.deepmap/instance.json — one locked, atomic
    read-modify-write through instance_state.update_instance (an existing non-object file is left
    untouched, never wiped; every other key is kept). Zero-egress, confined, never raises. Returns the
    path or None. Mirrors weight_ab.write_benchmark_evidence."""
    try:
        def _put(data):
            data["enrichment_evidence"] = evidence
            return data

        return _is.update_instance(root, _put)[0]
    except Exception:
        return None


# ---- selftest (spends nothing) ----------------------------------------------
def _selftest() -> int:
    """Prove the bridge computes deltas + the ships-gate on synthetic answers — no capture, no spend."""
    questions = [
        {"id": "c1", "family": "concept_to_impl", "ground_truth_set": ["a.py:1 — Foo (x)"]},
        {"id": "c2", "family": "concept_to_impl", "ground_truth_set": ["b.py:2 — Bar (y)"]},
        {"id": "m1", "family": "multi_hop", "ground_truth_set": ["c.py:3 — Baz (z)"]},
    ]
    det = {"c1": "nothing", "c2": "nothing", "m1": "c.py:3 — Baz (z)"}
    enriched = {"c1": "a.py:1 — Foo (x)", "c2": "b.py:2 — Bar (y)", "m1": "c.py:3 — Baz (z)"}
    ab = enrich_ab(det, enriched, questions)
    v = enrich_verdict(ab)
    assert ab["per_slice"]["concept_to_impl"]["delta"] > 0, ab
    assert v["concept_delta"] > 0 and not v["regressions"], v
    # a no-lift A/B must NOT ship
    ab0 = enrich_ab(det, det, questions)
    v0 = enrich_verdict(ab0)
    assert v0["ships"] is False and v0["concept_delta"] == 0.0, v0
    # a lift-with-regression must NOT ship
    regr = {"c1": "a.py:1 — Foo (x)", "c2": "b.py:2 — Bar (y)", "m1": "nothing"}
    vR = enrich_verdict(enrich_ab(det, regr, questions))
    assert vR["ships"] is False and "multi_hop" in vR["regressions"], vR
    print("[enrich_ab] selftest OK — deterministic A/B + ships-gate wired; no judge, no spend.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="enrich_ab", description="Enrichment A/B (deterministic; no spend).")
    p.add_argument("--selftest", action="store_true", help="prove the bridge; spends nothing.")
    args = p.parse_args(argv)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
