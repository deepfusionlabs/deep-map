#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for benchmark/cost_estimate.py (the planning-aid cost model).

cost_estimate.py is a pure planning calculator: it estimates the $ to run the with-vs-without matrix
+ blind scoring, parametric in the repo/question set, runs-per-arm, arm model(s), and judge(s). It is
NOT product code (the retriever is untouched) — but it informs a spend decision, so its load-bearing
logic is locked here:

  - `_plan_runnable`: Claude arms run via the claude PLAN (`claude -p`) => ~$0 OpenRouter out-of-pocket;
    non-Claude arms + ALL judges hit OpenRouter per-token. This split is the whole point of the model.
  - `_cost`: monotonic in tokens; uses the per-model PRICES.
  - `estimate(...)`: returns the OUT-OF-POCKET (lo, hi) tuple; Opus-only-arm + non-Claude-judge ~ $0;
    a non-Claude arm pushes out-of-pocket strictly positive.

Pure + hermetic (no Neo4j, no network, no spend)."""
from __future__ import annotations

import importlib.util
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
    spec = importlib.util.spec_from_file_location("dm_cost_estimate", BENCH / "cost_estimate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_plan_runnable(C) -> None:
    print("\n[plan-runnable: Claude arms ride the plan; others hit OpenRouter]")
    check("Opus is plan-runnable (claude -p)", C._plan_runnable("anthropic/claude-opus-4.8") is True)
    check("Sonnet is plan-runnable", C._plan_runnable("anthropic/claude-sonnet-4.6") is True)
    check("GPT-5 is NOT plan-runnable (needs OpenRouter)", C._plan_runnable("openai/gpt-5") is False)
    check("Gemini is NOT plan-runnable", C._plan_runnable("google/gemini-2.5-pro") is False)
    check("DeepSeek is NOT plan-runnable", C._plan_runnable("deepseek/deepseek-r1") is False)


def gate_cost_monotonic(C) -> None:
    print("\n[_cost: monotonic in tokens, uses PRICES]")
    cheap = C._cost((1000, 100), "deepseek/deepseek-r1")
    dear = C._cost((1000, 100), "anthropic/claude-opus-4.8")
    check("Opus costs more than DeepSeek for the same tokens", dear > cheap, f"{dear} vs {cheap}")
    more = C._cost((2000, 200), "openai/gpt-5")
    less = C._cost((1000, 100), "openai/gpt-5")
    check("more tokens cost strictly more", more > less, f"{more} vs {less}")
    check("zero tokens cost zero", C._cost((0, 0), "openai/gpt-5") == 0.0)


def gate_out_of_pocket(C) -> None:
    print("\n[estimate: out-of-pocket reflects the plan/OpenRouter split]")
    # Scenario A: Opus-only arm (on the plan) + a non-Claude judge -> out-of-pocket ~ $0 (just the judge,
    # which is pennies). The KEY honesty point: the expensive Opus arm is absorbed by the plan.
    a_lo, a_hi = C.estimate(n_repos=5, n_questions=9, runs_per_arm=4,
                            arm_models=["anthropic/claude-opus-4.8"], judges=["deepseek/deepseek-r1"])
    check("Opus-only arm + DeepSeek judge -> out-of-pocket under $5 (plan absorbs Opus)",
          0 <= a_hi < 5, f"lo={a_lo} hi={a_hi}")
    # Scenario B: add a non-Claude arm -> out-of-pocket strictly positive and bigger than A.
    b_lo, b_hi = C.estimate(n_repos=5, n_questions=9, runs_per_arm=4,
                            arm_models=["anthropic/claude-opus-4.8", "openai/gpt-5"],
                            judges=["deepseek/deepseek-r1"])
    check("adding a non-Claude arm pushes out-of-pocket strictly up", b_hi > a_hi, f"{b_hi} vs {a_hi}")
    check("a non-Claude arm's out-of-pocket is positive", b_lo > 0, f"lo={b_lo}")
    # More runs -> more cost (monotonic in the run config).
    c_lo, c_hi = C.estimate(n_repos=5, n_questions=9, runs_per_arm=8,
                            arm_models=["anthropic/claude-opus-4.8", "openai/gpt-5"],
                            judges=["deepseek/deepseek-r1"])
    check("doubling runs/arm raises out-of-pocket", c_hi > b_hi, f"{c_hi} vs {b_hi}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("benchmark/cost_estimate.py — planning-model gates (no spend)")
    print("=" * 64)
    C = _load()
    gate_plan_runnable(C)
    gate_cost_monotonic(C)
    gate_out_of_pocket(C)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
