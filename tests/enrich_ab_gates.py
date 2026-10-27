#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for benchmark/enrich_ab.py — the enrichment A/B bridge (deterministic; no judge, no spend).

Proves: enrich_ab REDUCES to weight_ab.deterministic_ab (frozen score_completeness reused, not
re-implemented); the ships-verdict is honest (lift + no-regression + significant => True; no-lift or
lift-with-regression => False); the family slicing keys on `family`; and the standalone `--selftest`
exits 0 without any claude/Neo4j.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))
sys.path.insert(0, str(ROOT / "build"))
import enrich_ab as EAB          # noqa: E402
import weight_ab as WAB          # noqa: E402

FAILS = []


def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILS.append(msg)


QS = [
    {"id": "c1", "family": "concept_to_impl", "ground_truth_set": ["a.py:1 — Foo (x)"]},
    {"id": "c2", "family": "concept_to_impl", "ground_truth_set": ["b.py:2 — Bar (y)"]},
    {"id": "m1", "family": "multi_hop", "ground_truth_set": ["c.py:3 — Baz (z)"]},
    {"id": "t1", "family": "transitive", "ground_truth_set": ["d.py:4 — Qux (w)"]},
]
DET = {"c1": "n/a", "c2": "n/a", "m1": "c.py:3 — Baz (z)", "t1": "d.py:4 — Qux (w)"}
ENR = {"c1": "a.py:1 — Foo (x)", "c2": "b.py:2 — Bar (y)", "m1": "c.py:3 — Baz (z)", "t1": "d.py:4 — Qux (w)"}


def test_reduces_to_weight_ab():
    a = EAB.enrich_ab(DET, ENR, QS)
    b = WAB.deterministic_ab(DET, ENR, QS, slice_key="family")
    check(a == b, "enrich_ab reduces EXACTLY to weight_ab.deterministic_ab (frozen scorer reused)")
    check("concept_to_impl" in a["per_slice"] and "multi_hop" in a["per_slice"],
          "family slicing keyed on `family`")
    check(a["per_slice"]["concept_to_impl"]["delta"] > 0, "concept_to_impl lift measured (>0)")


def test_verdict_ships():
    v = EAB.enrich_verdict(EAB.enrich_ab(DET, ENR, QS))
    check(v["concept_delta"] > 0 and not v["regressions"], "verdict: concept lifts, no regressions")
    check(v["ships"] == v["significant"], "verdict: ships gated on significance too")


def test_verdict_no_lift_does_not_ship():
    v = EAB.enrich_verdict(EAB.enrich_ab(DET, DET, QS))
    check(v["ships"] is False and v["concept_delta"] == 0.0, "verdict: no lift => ships=False")


def test_verdict_regression_blocks_ship():
    regr = dict(ENR); regr["m1"] = "n/a"           # concept lifts BUT multi_hop regresses
    v = EAB.enrich_verdict(EAB.enrich_ab(DET, regr, QS))
    check(v["concept_delta"] > 0, "verdict: concept still lifts")
    check("multi_hop" in v["regressions"] and v["ships"] is False,
          "verdict: a regressing family blocks the ship")


def test_selftest_subprocess():
    r = subprocess.run([sys.executable, str(ROOT / "benchmark" / "enrich_ab.py"), "--selftest"],
                       capture_output=True, text=True, timeout=120)
    check(r.returncode == 0 and "selftest OK" in r.stdout, "enrich_ab.py --selftest exits 0 (no spend)")


def main():
    print("=" * 72)
    print("enrich_ab_gates — enrichment A/B bridge (deterministic; no judge, no spend)")
    print("=" * 72)
    for fn in (test_reduces_to_weight_ab, test_verdict_ships, test_verdict_no_lift_does_not_ship,
               test_verdict_regression_blocks_ship, test_selftest_subprocess):
        print(f"\n--- {fn.__name__} ---")
        try:
            fn()
        except Exception as exc:
            FAILS.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
            print(f"  [FAIL] {fn.__name__} raised {type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    print("RESULT:", "GREEN" if not FAILS else f"RED — {len(FAILS)}: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
