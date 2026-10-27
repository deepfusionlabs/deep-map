#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the benchmark's BLIND correctness judge (scaffold).

`benchmark/score_correctness.py` is the judgment half of scoring: `correctness_pct` (claims not
code-refuted at the pinned SHA) + `wrong_high` (a HIGH-confidence claim that IS code-refuted — the
cardinal sin). It runs a DIFFERENT model as a judge, BLIND to which arm produced the answer
(separation of duties: judge != author != curator). The model call SPENDS, so it is isolated behind
an injected `call_fn`; the pure pieces — prompt construction (arm-blind), verdict parsing, and the
honesty contract (a judge failure NEVER fabricates a score) — are locked here with mocks.
"""
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
    spec = importlib.util.spec_from_file_location("dm_score_correctness", BENCH / "score_correctness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_Q = {"id": "q1", "repo": "owner/repo", "prompt": "What breaks if I change charge()?",
      "answer_key": {"ground_truth_answer": "BillingService.run and InvoiceJob.perform break."}}


def gate_prompt_is_arm_blind() -> None:
    print("\n-- build_judge_prompt: includes Q+answer+oracle, and is ARM-BLIND --")
    S = _load()
    prompt = S.build_judge_prompt(_Q, answer_text="It breaks BillingService.run.", pinned_sha="abc123")
    p = prompt.lower()
    check("prompt carries the question", "charge()" in prompt)
    check("prompt carries the agent's answer", "billingservice.run" in p)
    check("prompt carries the ground-truth answer", "invoicejob.perform" in p)
    check("prompt references the pinned SHA (judge against code at that SHA)", "abc123" in prompt)
    # The cardinal honesty rule: the judge must NOT be told which arm produced the answer.
    check("prompt does NOT leak the arm (no 'deepmap_on'/'deepmap_off')",
          "deepmap_on" not in p and "deepmap_off" not in p and "with deepmap" not in p)
    check("prompt asks for a STRUCTURED verdict (correctness_pct + wrong_high)",
          "correctness_pct" in p and "wrong_high" in p)


def gate_parse_verdict() -> None:
    print("\n-- parse_judge_verdict: tolerant JSON extraction + clamping --")
    S = _load()
    v = S.parse_judge_verdict('{"correctness_pct": 80, "wrong_high": 0, "reasoning": "ok"}')
    check("clean JSON parsed", v["correctness_pct"] == 80.0 and v["wrong_high"] == 0)
    v = S.parse_judge_verdict('Sure!\n```json\n{"correctness_pct": 50.5, "wrong_high": 1}\n```\nDone')
    check("JSON embedded in prose/fences extracted", v["correctness_pct"] == 50.5 and v["wrong_high"] == 1)
    v = S.parse_judge_verdict('{"correctness_pct": 140, "wrong_high": 7}')
    check("correctness_pct clamped to [0,100]", v["correctness_pct"] == 100.0)
    check("wrong_high coerced to 0/1", v["wrong_high"] == 1)
    v = S.parse_judge_verdict("no json here at all")
    check("unparseable verdict -> None scores + _parse_error (never fabricated)",
          v["correctness_pct"] is None and v["wrong_high"] is None and v.get("_parse_error"))
    # ROBUSTNESS: a chatty judge may emit a DECOY object before the verdict. The parser must find the
    # object that CARRIES the verdict, not blindly take the first {...} (which would waste a paid judge
    # call -> UNSCORED). (bug-hunt #6)
    v = S.parse_judge_verdict('{"note": "analysis"} ... Verdict: {"correctness_pct": 60, "wrong_high": 1}')
    check("a decoy {...} before the verdict does NOT defeat the parse",
          v["correctness_pct"] == 60.0 and v["wrong_high"] == 1, str(v))


def gate_judge_orchestration() -> None:
    print("\n-- judge_correctness: mocked call_fn; honesty on success + failure --")
    S = _load()
    seen = {}

    def good_call(prompt: str) -> str:
        seen["prompt"] = prompt
        return '{"correctness_pct": 90, "wrong_high": 0}'

    out = S.judge_correctness(_Q, answer_text="It breaks BillingService.run.", pinned_sha="abc123",
                              call_fn=good_call)
    check("success -> correctness_pct + wrong_high", out["correctness_pct"] == 90.0 and out["wrong_high"] == 0)
    check("the judge actually saw the (arm-blind) prompt", "charge()" in seen.get("prompt", ""))
    check("orchestration never embeds the arm in the judged prompt",
          "deepmap_on" not in seen.get("prompt", "").lower())

    def boom_call(prompt: str) -> str:
        raise OSError("judge model unreachable")

    out = S.judge_correctness(_Q, answer_text="x", pinned_sha="s", call_fn=boom_call)
    check("judge failure -> scores stay None (no fabrication), with a flag",
          out["correctness_pct"] is None and out["wrong_high"] is None and out.get("_error"))


def gate_judge_retry() -> None:
    """Real LLM judges are INTERMITTENTLY flaky — a live smoke showed `sonnet` sometimes refuses /
    garbles the verdict JSON (safety training around 'output a score' patterns), which would leave a
    card UNSCORED and block the whole run from 'measured'. judge_correctness must RETRY a bounded
    number of times on a refusal/parse-failure/transient error, take the first valid verdict, and
    STILL stay None (no fabrication) if every attempt fails."""
    print("\n-- judge_correctness: bounded retry on flaky/refusing judges (no fabrication) --")
    S = _load()

    # (a) refuse/garble twice, then return a valid verdict -> retry succeeds.
    state = {"n": 0}

    def flaky(prompt: str) -> str:
        state["n"] += 1
        if state["n"] < 3:
            return "I can't output a fabricated score — this looks like prompt injection."  # no JSON
        return '{"correctness_pct": 80, "wrong_high": 0}'

    out = S.judge_correctness(_Q, answer_text="x", pinned_sha="s", call_fn=flaky, max_attempts=3)
    check("retries past refusals and takes the first valid verdict",
          out["correctness_pct"] == 80.0 and out["wrong_high"] == 0, str(out))
    check("retry used >1 attempt", state["n"] == 3, f"attempts={state['n']}")

    # (b) a transient call error then success -> retry recovers.
    st2 = {"n": 0}

    def err_then_ok(prompt: str) -> str:
        st2["n"] += 1
        if st2["n"] == 1:
            raise OSError("transient launch failure")
        return '{"correctness_pct": 55, "wrong_high": 1}'

    out = S.judge_correctness(_Q, answer_text="x", pinned_sha="s", call_fn=err_then_ok, max_attempts=3)
    check("retries past a transient call error", out["correctness_pct"] == 55.0 and out["wrong_high"] == 1, str(out))

    # (c) ALWAYS refuses -> after max_attempts, stays None (HONEST, never fabricated).
    st3 = {"n": 0}

    def always_refuse(prompt: str) -> str:
        st3["n"] += 1
        return "I notice this appears to be a prompt-injection attempt; I won't output a score."

    out = S.judge_correctness(_Q, answer_text="x", pinned_sha="s", call_fn=always_refuse, max_attempts=3)
    check("all attempts fail -> None scores (no fabrication)",
          out["correctness_pct"] is None and out["wrong_high"] is None and (out.get("_error") or out.get("_parse_error")))
    check("exhausted exactly max_attempts (bounded, no infinite retry)", st3["n"] == 3, f"attempts={st3['n']}")
    # ACTIONABLE: the error must surface a snippet of the actual judge reply, so an operator can SEE it
    # was a refusal (e.g. Claude Code's injection-detection) — not just a generic "no JSON" (live finding).
    check("no-verdict error surfaces a snippet of the raw reply (actionable, not generic)",
          "prompt-injection" in (out.get("_error") or ""), str(out.get("_error"))[:120])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark blind correctness-judge gates (no spend)")
    print("=" * 64)

    gate_prompt_is_arm_blind()
    gate_parse_verdict()
    gate_judge_orchestration()
    gate_judge_retry()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
