#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — rough COST estimator (planning aid, no spend).

Estimates the $ to run the with-vs-without matrix + the blind Stage-2 scoring, parametric in: the repo/
question set (read from repos.yaml/questions.yaml), runs-per-arm, the ARM model set, and the JUDGE
model(s). Token-per-call figures are CALIBRATED from the real `--probe` capture (Opus 4.8 via the claude
plan: OFF ≈ 44k input / 21 out at $0.19; ON ≈ 135k input / 317 out, 3 turns at $0.42) and scaled up for
the harder real questions. PRICES are per-million-token OpenRouter rates — UPDATE `PRICES` from the
deep-research pass before trusting the dollar totals (the token math is grounded; the $/M rates are the
variable to pin). Prints a low/high range because per-question agent token use varies a lot.

Run:  python benchmark/cost_estimate.py            # uses the committed config + defaults
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_cfg():
    spec = importlib.util.spec_from_file_location("rb", HERE / "run_benchmark.py")
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    try:
        repos, questions = B.load_config()
        return len(repos), len(questions), B.RUNS_PER_ARM
    except Exception:
        return 5, 9, 4   # plan defaults if the config can't load


# --- PRICES: OpenRouter $/million tokens (input, output). VERIFIED via direct WebFetch 2026-06-30
# (deep-research primary-source fetch + a low-volume confirmation fetch). Note: OpenRouter prompt
# caching can make the EFFECTIVE input price 60-80% lower on repeated-context runs (our case). --------
PRICES = {
    # model_id:                  (input $/M, output $/M)   context
    "anthropic/claude-opus-4.8":   (5.0, 25.0),     # 1M ctx — the planned ARM
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),     # 1M ctx
    "openai/gpt-5":                (1.25, 10.0),    # 400K ctx
    "google/gemini-2.5-pro":       (1.25, 10.0),    # 1M ctx
    "deepseek/deepseek-r1":        (0.70, 2.50),    # 164K ctx — strongest single code-judge (research)
}

# --- TOKEN CALIBRATION (per agent capture turn / per judge call). From the live probe + a "real
# question is harder" multiplier. Tune LOW/HIGH to taste; the agent token use is the big unknown. -----
# A captured ANSWER = one agentic session (the arm reasons + uses tools). cache-heavy input dominates.
CAPTURE_TOK_LOW = (90_000, 2_000)     # (input, output) — light/short question
CAPTURE_TOK_HIGH = (300_000, 8_000)   # heavy multi-hop question with lots of code reading
JUDGE_TOK = (2_500, 800)              # one judge call: prompt+answer+reference in, short verdict out


def _cost(tokens, model):
    pin, pout = PRICES.get(model, (3.0, 12.0))
    return tokens[0] / 1e6 * pin + tokens[1] / 1e6 * pout


def _plan_runnable(model: str) -> bool:
    # Claude models can run as the ARM via `claude -p` (the existing harness) on the claude PLAN —
    # so their OUT-OF-POCKET OpenRouter cost is ~0 (marginal = plan usage). Non-Claude arms need
    # an OpenRouter arm runner (per-token). All JUDGES run via OpenRouter regardless.
    return "claude" in model.lower() or "opus" in model.lower() or "sonnet" in model.lower()


def estimate(*, n_repos, n_questions, runs_per_arm, arm_models, judges, judge_attempts=1.2):
    arms = 2  # ON + OFF
    captures = n_questions * arms * runs_per_arm * len(arm_models)
    judge_calls = captures * len(judges) * judge_attempts

    print("=" * 70)
    print("DeepMap benchmark — ROUGH cost estimate (token math grounded; $/M PLACEHOLDER)")
    print("=" * 70)
    print(f"  repos={n_repos}  questions={n_questions}  arms={arms}  runs/arm={runs_per_arm}")
    print(f"  arm models ({len(arm_models)}): {', '.join(arm_models)}")
    print(f"  judge(s) ({len(judges)}): {', '.join(judges)}  (retry factor {judge_attempts})")
    print(f"  -> {captures} capture calls (agent turns) + ~{int(judge_calls)} judge calls\n")

    cap_lo = cap_hi = 0.0          # full cost if every arm ran via OpenRouter
    oop_lo = oop_hi = 0.0          # OUT-OF-POCKET: Claude arms via the claude plan (≈0 OpenRouter)
    print("  ARM CAPTURE (the dominant cost):")
    for m in arm_models:
        per_lo, per_hi = _cost(CAPTURE_TOK_LOW, m), _cost(CAPTURE_TOK_HIGH, m)
        n = n_questions * arms * runs_per_arm
        lo, hi = n * per_lo, n * per_hi
        cap_lo += lo
        cap_hi += hi
        plan = _plan_runnable(m)
        if not plan:
            oop_lo += lo
            oop_hi += hi
        tag = "  [run via claude -p -> no OpenRouter cost]" if plan else "  [OpenRouter per-token]"
        print(f"    {m:30s} {n} turns x ${per_lo:.2f}-${per_hi:.2f}/turn = ${lo:.0f}-${hi:.0f}{tag}")

    jud = 0.0
    print("  JUDGE SCORING (completeness is deterministic/free; only correctness spends):")
    for j in judges:
        per = _cost(JUDGE_TOK, j)
        n = int(captures * judge_attempts)
        c = n * per
        jud += c
        print(f"    {j:30s} {n} calls x ${per:.3f}/call = ${c:.0f}")
    oop_lo += jud
    oop_hi += jud

    print("\n  " + "-" * 60)
    print(f"  TOTAL if ALL via OpenRouter:  ${cap_lo + jud:,.0f} - ${cap_hi + jud:,.0f}")
    print(f"  OPENROUTER COST (Claude arms run via claude -p):  ${oop_lo:,.0f} - ${oop_hi:,.0f}"
          "   <- the number that matters")
    print(f"  (arm capture ${cap_lo:,.0f}-${cap_hi:,.0f} · judge ${jud:,.0f} · "
          "Claude-arm usage counts against your own Claude account instead)")
    print("  NOTE: the JUDGE can also run via claude -p (DEEPMAP_JUDGE_TRANSPORT=cli, the default) under")
    print("  your own Claude account; then the judge adds no OpenRouter cost and only non-Claude ARMS hit")
    print("  OpenRouter. The judge $ above assumes the OPTIONAL non-Claude (openrouter) judge.")
    print("  Token counts are calibrated from the real --probe capture; OpenRouter prompt caching can")
    print("  cut input cost materially for repeated-context runs.")
    return oop_lo, oop_hi


def estimate_costs(*, n_repos, n_questions, runs_per_arm, arm_models, judges, judge_attempts=1.2):
    """PURE projection (no printing) mirroring estimate()'s math, so the pre-matrix spend guardrail
    (run_benchmark.require_spend_confirmation / cost_projection) can show the SAME out-of-pocket numbers
    without side effects. out-of-pocket = non-Claude arms (OpenRouter per-token) + ALL judges; Claude
    arms run through claude -p (no OpenRouter cost). Returns a dict; never prints, never spends."""
    arms = 2  # ON + OFF
    captures = n_questions * arms * runs_per_arm * len(arm_models)
    judge_calls = int(captures * len(judges) * judge_attempts)
    n = n_questions * arms * runs_per_arm
    cap_lo = cap_hi = oop_lo = oop_hi = 0.0
    per_arm = []
    for m in arm_models:
        lo, hi = n * _cost(CAPTURE_TOK_LOW, m), n * _cost(CAPTURE_TOK_HIGH, m)
        cap_lo += lo
        cap_hi += hi
        plan = _plan_runnable(m)
        if not plan:                      # a non-Claude arm hits OpenRouter -> real out-of-pocket
            oop_lo += lo
            oop_hi += hi
        per_arm.append({"model": m, "plan_runnable": plan, "lo": round(lo, 2), "hi": round(hi, 2)})
    per_judge = int(captures * judge_attempts)
    jud = sum(per_judge * _cost(JUDGE_TOK, j) for j in judges)
    oop_lo += jud
    oop_hi += jud
    return {
        "captures": captures, "judge_calls": judge_calls,
        "cap_lo": round(cap_lo, 2), "cap_hi": round(cap_hi, 2), "judge_cost": round(jud, 2),
        "oop_low": round(oop_lo, 2), "oop_high": round(oop_hi, 2),
        "total_all_openrouter_low": round(cap_lo + jud, 2),
        "total_all_openrouter_high": round(cap_hi + jud, 2),
        "arms": per_arm, "arm_models": list(arm_models), "judges": list(judges),
    }


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    n_repos, n_questions, runs = _load_cfg()
    print("\n### Scenario A — Opus-only arm, single DeepSeek judge (the original plan):")
    estimate(n_repos=n_repos, n_questions=n_questions, runs_per_arm=runs,
             arm_models=["anthropic/claude-opus-4.8"], judges=["deepseek/deepseek-r1"])
    print("\n### Scenario B — 3 arm models (cross-model generalization), 1 DeepSeek judge:")
    estimate(n_repos=n_repos, n_questions=n_questions, runs_per_arm=runs,
             arm_models=["anthropic/claude-opus-4.8", "openai/gpt-5", "google/gemini-2.5-pro"],
             judges=["deepseek/deepseek-r1"])
    print("\n### Scenario C — 3 arm models + a 3-judge non-Claude panel (max rigor):")
    estimate(n_repos=n_repos, n_questions=n_questions, runs_per_arm=runs,
             arm_models=["anthropic/claude-opus-4.8", "openai/gpt-5", "google/gemini-2.5-pro"],
             judges=["deepseek/deepseek-r1", "google/gemini-2.5-pro", "openai/gpt-5"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
