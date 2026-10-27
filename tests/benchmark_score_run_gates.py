#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the Stage-2 scoring DRIVER (`benchmark/score_run.py`).

The driver applies the deterministic completeness scorer + the blind correctness judge to a CAPTURED
run's scorecards, fills the score fields, and flips the honesty state designed -> captured -> measured.
It must:
  - score completeness deterministically (no judge) and correctness via the injected (mocked) judge,
  - mark a scorecard `scored` ONLY when BOTH halves succeeded,
  - keep a scorecard UNSCORED when the judge fails (so the run can't read "measured" — honesty),
  - never see the arm in the judged prompt (separation of duties is the judge module's job; here we
    just confirm the driver routes per-scorecard answers correctly).

Pure + hermetic: synthetic scorecards + a mocked judge + an injected answer-reader; never spends.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BENCH / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


def _scorecard(B, arm, qid="q1", repo="owner/repo"):
    return {"repo": repo, "question_id": qid, "family": "transitive_impact", "grep_can_do": False,
            "arm": arm, "run": 1, "exit_code": 0, "started_at": "t",
            "metrics": {k: None for k in B.METRIC_KEYS}, "answer_text_path": "p", "scored": False}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark Stage-2 scoring-driver gates (no spend)")
    print("=" * 64)

    B = _load("dm_run_benchmark_sr", "run_benchmark.py")
    SR = _load("dm_score_run", "score_run.py")

    questions_by_id = {"q1": {
        "id": "q1", "repo": "owner/repo", "prompt": "What breaks if I change charge()?",
        "answer_key": {"ground_truth_set": ["BillingService.run", "InvoiceJob.perform", "RetryWorker"],
                       "ground_truth_answer": "BillingService.run, InvoiceJob.perform, RetryWorker"}}}
    pinned = {"owner/repo": "abc123"}

    # Per-scorecard captured answers: ON enumerates all 3; OFF only 1.
    answers = {
        ("q1", B.ARM_ON): "It breaks BillingService.run, InvoiceJob.perform, and RetryWorker.",
        ("q1", B.ARM_OFF): "It breaks BillingService.run.",
    }
    read_answer = lambda rec: answers[(rec["question_id"], rec["arm"])]
    judge = lambda prompt: '{"correctness_pct": 100, "wrong_high": 0}'

    print("\n-- scores both arms; ON completeness > OFF; status flips to MEASURED --")
    recs = [_scorecard(B, B.ARM_ON), _scorecard(B, B.ARM_OFF)]
    scored = SR.score_records(recs, questions_by_id, judge_call_fn=judge,
                              read_answer_fn=read_answer, pinned_sha_by_repo=pinned)
    on = next(r for r in scored if r["arm"] == B.ARM_ON)
    off = next(r for r in scored if r["arm"] == B.ARM_OFF)
    check("ON completeness == 100.0 (enumerated all 3)", on["metrics"]["completeness_pct"] == 100.0,
          str(on["metrics"]["completeness_pct"]))
    check("OFF completeness ~33.33 (1 of 3)", round(off["metrics"]["completeness_pct"], 1) == 33.3,
          str(off["metrics"]["completeness_pct"]))
    check("correctness filled from the (mocked) judge", on["metrics"]["correctness_pct"] == 100.0)
    check("wrong_high filled", on["metrics"]["wrong_high"] == 0)
    check("both scorecards flagged scored", on["scored"] is True and off["scored"] is True)
    # Build the full record and confirm the honesty state machine reads MEASURED.
    rec = B.build_record([{"name": "owner/repo", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}],
                         list(questions_by_id.values()), scored, {"os": "t", "python": "3"}, run_id="t")
    check("all-scored record -> measured=True", rec["measured"] is True)
    check("status_tag == MEASURED", rec["status_tag"] == B.TAG_MEASURED)

    print("\n-- a judge FAILURE keeps that scorecard UNSCORED -> run can't read measured --")
    def boom_judge(prompt):
        raise OSError("judge unreachable")
    recs2 = [_scorecard(B, B.ARM_ON), _scorecard(B, B.ARM_OFF)]
    scored2 = SR.score_records(recs2, questions_by_id, judge_call_fn=boom_judge,
                               read_answer_fn=read_answer, pinned_sha_by_repo=pinned)
    # A scorecard is FULLY scored only when BOTH halves succeed. The deterministic completeness is
    # still computed (and kept provisionally) but metrics stay UNSCORED so the run can't read measured.
    check("judge failure -> deterministic completeness kept provisionally",
          scored2[0]["_provisional_completeness"]["completeness_pct"] == 100.0)
    check("judge failure -> metrics.completeness_pct stays None (card not fully scored)",
          scored2[0]["metrics"]["completeness_pct"] is None)
    check("judge failure -> correctness stays None (no fabrication)",
          scored2[0]["metrics"]["correctness_pct"] is None)
    check("judge failure -> scorecard NOT scored", scored2[0]["scored"] is False)
    rec2 = B.build_record([{"name": "owner/repo", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}],
                          list(questions_by_id.values()), scored2, {"os": "t", "python": "3"}, run_id="t")
    check("unscorable run -> measured=False (honesty preserved)", rec2["measured"] is False)

    print("\n-- an EMPTY/missing captured answer stays UNSCORED and never spends a judge call --")
    # The real reader (read_answer_from_scorecard) returns '' for a missing file (it does NOT raise),
    # so an empty answer must be caught BEFORE the judge: scored as 0% + a wasted judge spend would be
    # both dishonest (a failed capture is not a real 0) and wasteful.
    judge_calls = {"n": 0}
    def counting_judge(prompt):
        judge_calls["n"] += 1
        return '{"correctness_pct": 100, "wrong_high": 0}'
    empty_reader = lambda rec: "" if rec["arm"] == B.ARM_OFF else "BillingService.run, InvoiceJob.perform, RetryWorker"
    recs3 = [_scorecard(B, B.ARM_ON), _scorecard(B, B.ARM_OFF)]
    scored3 = SR.score_records(recs3, questions_by_id, judge_call_fn=counting_judge,
                               read_answer_fn=empty_reader, pinned_sha_by_repo=pinned)
    off3 = next(r for r in scored3 if r["arm"] == B.ARM_OFF)
    on3 = next(r for r in scored3 if r["arm"] == B.ARM_ON)
    check("empty-answer card -> NOT scored (failed capture is not a real 0)", off3["scored"] is False)
    check("empty-answer card -> a _score_error explains it", bool(off3.get("_score_error")), str(off3.get("_score_error")))
    check("empty-answer card -> metrics stay None (no fabricated 0)",
          off3["metrics"]["completeness_pct"] is None)
    check("the judge was called ONCE (only for the non-empty answer, not the empty one)",
          judge_calls["n"] == 1, f"judge called {judge_calls['n']}x")
    check("the non-empty card still scored normally", on3["scored"] is True)

    print("\n-- a question with an EMPTY ground_truth_set stays UNSCORED (curator wiring smell) --")
    # A missing completeness oracle is a curator error, not a real 0% — score_completeness flags it
    # (empty_oracle); the driver must act on the flag: stay UNSCORED + surface it, NOT silently score
    # 0% (which would drag the HERO aggregate down) and NOT spend a judge call on it.
    q_noset = {"id": "qN", "repo": "owner/repo", "prompt": "p",
               "answer_key": {"ground_truth_answer": "x"}}      # NO ground_truth_set
    jc = {"n": 0}
    def cj(prompt):
        jc["n"] += 1
        return '{"correctness_pct": 100, "wrong_high": 0}'
    recs4 = [_scorecard(B, B.ARM_ON, qid="qN")]
    scored4 = SR.score_records(recs4, {"qN": q_noset}, judge_call_fn=cj,
                               read_answer_fn=lambda r: "a real answer", pinned_sha_by_repo=pinned)
    check("empty ground_truth_set -> card NOT scored (not a fabricated 0%)", scored4[0]["scored"] is False)
    check("empty ground_truth_set -> a _score_error names the missing oracle",
          "ground_truth_set" in (scored4[0].get("_score_error") or ""), str(scored4[0].get("_score_error")))
    check("empty ground_truth_set -> metrics.completeness_pct stays None (no 0% in the aggregate)",
          scored4[0]["metrics"]["completeness_pct"] is None)
    check("empty ground_truth_set -> the judge is NOT called (no wasted spend)", jc["n"] == 0, str(jc["n"]))

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
