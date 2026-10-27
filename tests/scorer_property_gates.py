#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend METAMORPHIC/property gates for the deterministic completeness matcher.

The fuzz gate (`scorer_fuzz_gates.py`) proves the matcher never crashes; these prove it's *correct*
in ways example-based tests can't — invariants that must hold for EVERY input, checked over a large
SEEDED random set:

  1. ORDER-INDEPENDENCE — `ground_truth_set` is a set; permuting it must not change the score or the
     matched set (a curator reordering questions.yaml must never move the HERO number).
  2. MONOTONICITY — completeness is presence-based, so APPENDING text to an answer can only keep or
     RAISE completeness, never lower it (no "answering more" penalty).
  3. SELF-CREDIT — an answer literally built FROM the ground-truth atoms scores 100% (every atom is
     found in text that contains it — the matcher must credit a perfect answer).
  4. ALIAS SUBSUMPTION — a `a | b | c` atom is credited IFF at least one accepted form is credited
     individually (the `|` is an OR over surface forms, nothing more, nothing less).

A violation of any of these is a real correctness defect in the HERO metric. Seeded ⇒ reproducible.
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"

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


def _load():
    spec = importlib.util.spec_from_file_location("dm_score_completeness_prop", BENCH / "score_completeness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# A mix of every atom shape the matcher dispatches on.
_ATOMS = ["charge", "Svc.run", "billing/pay.py:12", "a->b", "x | RetryWorker | x-y",
          "InvoiceJob.perform", "auth", "pay.py:7", "jobs/invoice_runner.py:44", "load_config"]
_SEED = 20260629
_N = 2500


def gate_order_independence(S, rnd) -> None:
    print("\n-- property: ground_truth_set order does not change the score or matched set --")
    viol = 0
    for _ in range(_N):
        gts = rnd.sample(_ATOMS, rnd.randint(1, len(_ATOMS)))
        ans = " ".join(rnd.sample(_ATOMS, rnd.randint(0, len(_ATOMS))))
        a = S.score_completeness(ans, gts)
        shuffled = gts[:]
        rnd.shuffle(shuffled)
        b = S.score_completeness(ans, shuffled)
        if a["completeness_pct"] != b["completeness_pct"] or set(a["matched"]) != set(b["matched"]):
            viol += 1
    check(f"order-independent across {_N} cases", viol == 0, f"{viol} violations")


def gate_monotonicity(S, rnd) -> None:
    print("\n-- property: appending text to an answer never LOWERS completeness --")
    viol = 0
    for _ in range(_N):
        gts = rnd.sample(_ATOMS, rnd.randint(1, len(_ATOMS)))
        ans = " ".join(rnd.sample(_ATOMS, rnd.randint(0, len(_ATOMS))))
        extra = ans + " " + " ".join(rnd.sample(_ATOMS, rnd.randint(0, 4)))
        if S.score_completeness(extra, gts)["completeness_pct"] < S.score_completeness(ans, gts)["completeness_pct"] - 1e-9:
            viol += 1
    check(f"monotonic non-decreasing in answer content across {_N} cases", viol == 0, f"{viol} violations")


def gate_self_credit(S, rnd) -> None:
    print("\n-- property: an answer built FROM the ground-truth atoms scores 100% --")
    viol = []
    for _ in range(_N):
        gts = rnd.sample(_ATOMS, rnd.randint(1, len(_ATOMS)))
        # An answer that literally contains every atom must credit all of them.
        ans = "  ".join(gts)
        p = S.score_completeness(ans, gts)["completeness_pct"]
        if p != 100.0:
            viol.append((gts, p))
    check(f"self-credit == 100% across {_N} cases", not viol, str(viol[:2]))


def gate_alias_subsumption(S, rnd) -> None:
    print("\n-- property: 'a | b' credited IFF a or b credited individually --")
    forms = ["charge", "Svc.run", "auth", "InvoiceJob.perform", "RetryWorker", "pay.py:7"]
    viol = 0
    for _ in range(_N):
        a, b = rnd.choice(forms), rnd.choice(forms)
        ans = " ".join(rnd.sample(forms, rnd.randint(0, len(forms))))
        alias = S.score_completeness(ans, [f"{a} | {b}"])["completeness_pct"] == 100.0
        either = (S.score_completeness(ans, [a])["completeness_pct"] == 100.0
                  or S.score_completeness(ans, [b])["completeness_pct"] == 100.0)
        if alias != either:
            viol += 1
    check(f"alias == OR-of-forms across {_N} cases", viol == 0, f"{viol} violations")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — completeness-matcher metamorphic/property gates (seeded, no spend)")
    print("=" * 64)

    S = _load()
    rnd = random.Random(_SEED)
    gate_order_independence(S, rnd)
    gate_monotonicity(S, rnd)
    gate_self_credit(S, rnd)
    gate_alias_subsumption(S, rnd)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
