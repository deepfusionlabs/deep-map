#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — BLIND correctness judge (the judgment half of scoring).

`correctness_pct` = the fraction of the answer's claims NOT refuted by the code at the pinned SHA;
`wrong_high` = 1 if any HIGH-confidence claim IS code-refuted (the cardinal sin — plan §7.5). The judge
can check a claim IN the code only when it can read it: the cli judge (`claude -p` with Read/Grep) run
in the card's own pinned clone — run_benchmark.py --score --clone-root, which refuses a clone that is
not pristine at the pin. The API transports (openrouter / api) are sent this prompt alone (question,
reference answer, answer under review), so they judge the claims against the reference, not the code;
do_score says so when one is used (ISS-benchmark:006). Unlike
completeness (deterministic; `score_completeness.py`), correctness needs judgment, so a DIFFERENT
model judges it — BLIND to which arm produced the answer (separation of duties: judge != author !=
curator; plan §7.6). The model call SPENDS, so it is isolated behind an injected `call_fn`; the pure
pieces (prompt build, verdict parse) are unit-tested with no spend.

HONESTY: a judge that errors or returns garbage yields None scores (never a fabricated number) — the
record stays UNSCORED and the run can't read "measured".
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional

# The judge MUST be a different model from the benchmark arm (Opus 4.8) to avoid self-preference bias.
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"


def build_judge_prompt(question: Dict[str, Any], answer_text: str, pinned_sha: str) -> str:
    """Construct the BLIND judge prompt. It carries the question, the agent's answer, the pinned
    ground-truth answer, and the SHA to judge against — and deliberately CARRIES NO ARM IDENTITY, so
    the judge cannot prefer the DeepMap arm. Asks for a strict JSON verdict."""
    ak = question.get("answer_key") or {}
    ground_truth = ak.get("ground_truth_answer") or ak.get("ground_truth_set") or "(none provided)"
    return (
        "You are a STRICT, IMPARTIAL judge scoring one answer to a code question. You do NOT know which "
        "tool or configuration produced the answer — judge the ANSWER ON ITS MERITS only, against the "
        f"repository's source at commit SHA `{pinned_sha}`.\n\n"
        f"QUESTION:\n{question.get('prompt', '')}\n\n"
        f"REFERENCE (pinned ground-truth answer, curator-verified at {pinned_sha}):\n{ground_truth}\n\n"
        f"ANSWER UNDER REVIEW:\n{answer_text}\n\n"
        "Score two things, checking each factual claim against the code at that SHA:\n"
        "  - correctness_pct: 0-100, the percentage of the answer's claims that are NOT refuted by the "
        "code (a claim with no support AND no refutation is neutral — exclude it from the denominator).\n"
        "  - wrong_high: 1 if the answer makes ANY high-confidence/assertive claim that the code REFUTES "
        "(the cardinal sin), else 0.\n\n"
        "Reply with ONLY a JSON object and nothing else:\n"
        '{"correctness_pct": <0-100>, "wrong_high": <0 or 1>, "reasoning": "<one sentence>"}'
    )


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Tolerantly pull the VERDICT JSON object out of a possibly-prose/fenced judge reply. Collects
    every balanced top-level object that parses and PREFERS the one carrying `correctness_pct` — a
    chatty judge may emit a decoy object (an example, an analysis blob) BEFORE the real verdict, and
    blindly taking the first `{...}` would waste a paid judge call (-> UNSCORED). Falls back to the
    first parseable object when none carries the key (so the caller still reports a precise error)."""
    if not text:
        return None
    candidates: list = []
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            candidates.append(json.loads(fence.group(1)))
        except Exception:
            pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidates.append(json.loads(text[start:i + 1]))
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    if not candidates:
        return None
    for c in candidates:                          # prefer the object that actually carries the verdict
        if isinstance(c, dict) and "correctness_pct" in c:
            return c
    return candidates[0]                           # none has it -> first, so the caller reports the error


def parse_judge_verdict(text: str) -> Dict[str, Any]:
    """Parse the judge reply into {correctness_pct, wrong_high, reasoning}. correctness_pct is clamped
    to [0,100]; wrong_high is coerced to 0/1. A reply with no usable JSON -> None scores + _parse_error
    (never a fabricated number)."""
    obj = _extract_json(text)
    if not isinstance(obj, dict) or "correctness_pct" not in obj:
        return {"correctness_pct": None, "wrong_high": None,
                "_parse_error": "judge reply had no parseable {correctness_pct, wrong_high} JSON"}
    try:
        cp: Optional[float] = max(0.0, min(100.0, float(obj["correctness_pct"])))
    except (TypeError, ValueError):
        cp = None
    wh_raw = obj.get("wrong_high")
    try:
        wh: Optional[int] = 1 if int(wh_raw) != 0 else 0
    except (TypeError, ValueError):
        wh = 1 if bool(wh_raw) else (None if wh_raw is None else 0)
    out: Dict[str, Any] = {"correctness_pct": cp, "wrong_high": wh}
    if obj.get("reasoning"):
        out["reasoning"] = str(obj["reasoning"])
    if cp is None:
        out["_parse_error"] = "correctness_pct missing/non-numeric"
    return out


def judge_correctness(question: Dict[str, Any], answer_text: str, pinned_sha: str,
                      call_fn: Callable[[str], str],
                      model: str = DEFAULT_JUDGE_MODEL, max_attempts: int = 3) -> Dict[str, Any]:
    """Run the blind judge for one answer. `call_fn(prompt) -> raw_text` is the (spending) model call,
    injected so the orchestration is testable with no spend. Returns {correctness_pct, wrong_high}.

    RETRIES up to `max_attempts` on a transient call error OR a refusal/garble (a reply with no
    parseable verdict) — a LIVE smoke showed real judges (e.g. claude-sonnet) INTERMITTENTLY refuse or
    wrap-away the verdict JSON (safety training around 'output a score' patterns), and a single such
    miss would leave a card UNSCORED and block the whole run from 'measured'. It takes the FIRST valid
    verdict; if EVERY attempt fails it returns None scores + a flag (the record stays unscored —
    honesty; never a fabricated number)."""
    prompt = build_judge_prompt(question, answer_text, pinned_sha)
    attempts = max(1, int(max_attempts))
    last_err: str = "judge produced no verdict"
    for attempt in range(1, attempts + 1):
        try:
            raw = call_fn(prompt)
        except Exception as exc:   # judge unreachable / budget / launch failure — transient; retry
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        v = parse_judge_verdict(raw)
        if v.get("correctness_pct") is not None and v.get("wrong_high") is not None:
            out: Dict[str, Any] = {"correctness_pct": v.get("correctness_pct"),
                                   "wrong_high": v.get("wrong_high"), "judge_model": model,
                                   "_attempts": attempt}
            if v.get("reasoning"):
                out["reasoning"] = v["reasoning"]
            return out
        # No verdict (refusal/garble) — include a SNIPPET of the raw reply so the failure is ACTIONABLE
        # (a live finding: `claude -p` can return a plain-text injection-refusal instead of the JSON
        # envelope; an operator must SEE that to choose the API transport or reword). Then retry.
        snippet = " ".join((raw or "").split())[:200]
        last_err = (v.get("_parse_error") or "judge reply had no usable verdict") + (f" — reply: {snippet!r}" if snippet else "")
    # Every attempt failed — stay UNSCORED (honesty), never fabricate a score.
    return {"correctness_pct": None, "wrong_high": None, "_error": last_err,
            "judge_model": model, "_attempts": attempts}
