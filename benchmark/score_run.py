#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — Stage-2 scoring DRIVER (blind, separation-of-duties).

Takes a CAPTURED run's scorecards and fills their score fields by applying:
  - the DETERMINISTIC completeness scorer (`score_completeness`) — no model, reproducible, and
  - the BLIND correctness judge (`judge_correctness`) — a different model, arm-blind (spends).

HONESTY contract (the load-bearing part):
  - a scorecard is FULLY SCORED only when BOTH halves succeed → metrics get
    {completeness_pct, correctness_pct, wrong_high} and `scored=True`;
  - if the judge fails (or no oracle / no answer), the scorecard stays UNSCORED (metrics stay None),
    so `build_record` / `_all_scored` can NEVER read "measured" — the deterministic completeness is
    kept separately in `_provisional_completeness` for inspection, never fabricated into the result;
  - that holds for a RE-score too (an in-place re-score with an independent judge is a documented workflow):
    every pass starts by clearing what an earlier pass derived — the score metrics, `scored`, the judge's model
    and reasoning, a stale error, the provisional completeness — so a card whose re-score fails ends UNSCORED
    instead of keeping the PREVIOUS judge's verdict under a run that still reads "measured" (ISS-benchmark:011).

The model call is injected as `judge_call_fn` (and the answer reader as `read_answer_fn`) so the whole
driver is unit-testable with NO spend. The real run wires `judge_call_fn` to a `claude -p` call on the
judge model and `read_answer_fn` to read each scorecard's captured stdout.json.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent


def _load(fname: str):
    spec = importlib.util.spec_from_file_location(f"dm_bench_{Path(fname).stem}", _HERE / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_completeness = _load("score_completeness.py")
_correctness = _load("score_correctness.py")


def _ground_truth_set(question: Dict[str, Any]) -> List[str]:
    ak = (question or {}).get("answer_key") or {}
    gts = ak.get("ground_truth_set")
    return list(gts) if isinstance(gts, list) else []


# What a scoring pass derives on a card — cleared at the start of EVERY pass (ISS-benchmark:011).
SCORE_METRICS = ("completeness_pct", "correctness_pct", "wrong_high")
_PASS_FIELDS = ("_judge_model", "_judge_reasoning", "_score_error", "_provisional_completeness")


def _reset_score_fields(rec: Dict[str, Any]) -> None:
    """Clear everything an earlier scoring pass left on `rec` (the score metrics -> None, `scored` -> False, the
    judge's model / reasoning, a stale error, the provisional completeness), so this pass's outcome is the ONLY
    verdict the card carries: a re-score that fails leaves it UNSCORED, never with the previous judge's numbers."""
    rec["scored"] = False
    metrics = rec.get("metrics")
    if not isinstance(metrics, dict):
        metrics = rec["metrics"] = {}
    for k in SCORE_METRICS:
        metrics[k] = None
    for k in _PASS_FIELDS:
        rec.pop(k, None)


def _score_one_record(rec: Dict[str, Any],
                      questions_by_id: Dict[str, Dict[str, Any]],
                      judge_call_fn: Callable[[str], str],
                      read_answer_fn: Callable[[Dict[str, Any]], str],
                      pinned: Dict[str, str],
                      judge_model: Optional[str]) -> None:
    """Score ONE captured scorecard IN PLACE (the whole per-card logic). Mutates ONLY `rec` — it reads
    `questions_by_id`/`pinned` (immutable inputs) and calls the injected `read_answer_fn`/`judge_call_fn`
    (each independent per card). That single-writer property is what makes Enabler A's parallel path
    verdict-identical to the sequential one: no shared mutable state, no cross-card contamination. It starts
    by clearing whatever an earlier pass derived (_reset_score_fields), so an in-place RE-score never mixes
    two judges' verdicts."""
    _reset_score_fields(rec)
    q = questions_by_id.get(rec.get("question_id"))
    if q is None:
        rec["scored"] = False
        rec["_score_error"] = "no question/oracle for this scorecard"
        return
    try:
        answer = read_answer_fn(rec) or ""
    except Exception as exc:   # answer file missing / unreadable -> unscored, never fabricated
        rec["scored"] = False
        rec["_score_error"] = f"answer unreadable: {type(exc).__name__}: {exc}"
        return

    # An EMPTY captured answer is a FAILED CAPTURE, not a real 0%: the reader returns '' for a
    # missing/blank stdout.json (it does not raise), so catch it here — stay UNSCORED and DO NOT
    # spend a judge call on nothing (honesty + no wasted spend).
    if not answer.strip():
        rec["scored"] = False
        rec["_score_error"] = "no captured answer (empty) — not scored (failed capture is not a real 0)"
        return

    comp = _completeness.score_completeness(answer, _ground_truth_set(q))
    rec["_provisional_completeness"] = comp   # deterministic, always available

    # An EMPTY ground_truth_set is a CURATOR wiring smell, not a real 0% — score_completeness flags
    # it (empty_oracle). Stay UNSCORED + surface it (never silently score 0%, which would drag the
    # HERO aggregate down) and DO NOT spend a judge call on a question with no completeness target.
    if comp.get("empty_oracle"):
        rec["scored"] = False
        rec["_score_error"] = ("question has no ground_truth_set (empty completeness oracle) — "
                               "curator must add one before this card can be scored")
        return

    sha = pinned.get(rec.get("repo"), "")
    if judge_model:
        corr = _correctness.judge_correctness(q, answer, sha, judge_call_fn, model=judge_model)
    else:
        corr = _correctness.judge_correctness(q, answer, sha, judge_call_fn)

    if corr.get("correctness_pct") is not None and corr.get("wrong_high") is not None:
        rec["metrics"]["completeness_pct"] = comp["completeness_pct"]
        rec["metrics"]["correctness_pct"] = corr["correctness_pct"]
        rec["metrics"]["wrong_high"] = corr["wrong_high"]
        rec["scored"] = True
        rec["_judge_model"] = corr.get("judge_model")
        if corr.get("reasoning"):
            rec["_judge_reasoning"] = corr["reasoning"]
    else:
        rec["scored"] = False
        rec["_score_error"] = corr.get("_error") or corr.get("_parse_error") or "judge produced no verdict"


def score_records(run_records: List[Dict[str, Any]],
                  questions_by_id: Dict[str, Dict[str, Any]],
                  judge_call_fn: Callable[[str], str],
                  read_answer_fn: Callable[[Dict[str, Any]], str],
                  pinned_sha_by_repo: Optional[Dict[str, str]] = None,
                  judge_model: Optional[str] = None,
                  max_workers: int = 1) -> List[Dict[str, Any]]:
    """Score each captured scorecard IN PLACE and return the list. Completeness is always computed
    (kept in `_provisional_completeness`); the metric fields + `scored=True` are set ONLY when the
    correctness judge also succeeds — so an unjudgeable card never reads as measured. `judge_model`,
    when given, is forwarded to the judge so the RECORDED `_judge_model` equals the model actually
    invoked (never a hardcoded default that could misrepresent which model judged).

    ENABLER A (RM-2): `max_workers` > 1 runs the INDEPENDENT per-card judge calls concurrently on a
    bounded ThreadPoolExecutor — the judge is the ~1.5 min/call bottleneck across the n=4 × 5 × 33 ×
    up-to-3-arms matrix. The scoring LOGIC is unchanged: each card is scored by `_score_one_record`,
    which mutates ONLY its own `rec`, so the parallel path yields the SAME per-card verdicts as
    sequential (order-independent). Default `max_workers=1` is byte-identical to the prior sequential
    behaviour (backward compatible)."""
    pinned = pinned_sha_by_repo or {}
    workers = max(1, int(max_workers or 1))
    if workers == 1 or len(run_records) <= 1:
        for rec in run_records:
            _score_one_record(rec, questions_by_id, judge_call_fn, read_answer_fn, pinned, judge_model)
        return run_records

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(run_records))) as ex:
        futures = [ex.submit(_score_one_record, rec, questions_by_id, judge_call_fn,
                             read_answer_fn, pinned, judge_model) for rec in run_records]
        for f in futures:
            f.result()   # surface any unexpected exception (the body itself never raises — honesty)
    return run_records


def scoring_summary(run_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Quick {n, scored, unscored} tally for an operator's progress read-out (no fabrication)."""
    n = len(run_records)
    scored = sum(1 for r in run_records if r.get("scored"))
    return {"n": n, "scored": scored, "unscored": n - scored,
            "all_scored": n > 0 and scored == n}
