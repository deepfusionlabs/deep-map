#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the Stage-2 blind-scoring CLI wiring (`do_score` in run_benchmark.py).

The scoring MODULES (score_completeness / score_correctness / score_run) are unit-locked elsewhere;
this suite locks the RUN_BENCHMARK wiring that an operator actually invokes at G3 to apply them to a
CAPTURED run — the piece that turns the scaffold into something runnable end-to-end:

  * build_judge_argv      — the judge runs a DIFFERENT model than the Opus arm, --strict-mcp-config
                            (no DeepMap tools — neutrality), JSON output, a spend cap,
  * read_answer_from_scorecard — pulls the agent's answer text out of a captured stdout.json,
  * score_captured_run    — applies completeness + the (injected, mocked) judge to results.json,
                            rewrites it, and flips status to MEASURED only when every card scored,
  * do_score              — ENV-guarded: refuses to spend unless DEEPMAP_REAL_ENGINE=1 (honesty).

Pure + hermetic: a synthetic captured results.json + a mocked judge + an injected reader; the spend
path is never taken (it is asserted to fail closed without the engine flag).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_score_cli", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_build_judge_argv(B) -> None:
    print("\n-- build_judge_argv: a DIFFERENT model, isolated, JSON, capped --")
    fn = getattr(B, "build_judge_argv", None)
    check("build_judge_argv exists", callable(fn))
    if not callable(fn):
        return
    argv = fn("JUDGE THIS", max_budget_usd=0.5)
    s = " ".join(argv)
    # STDIN delivery (load-bearing): the prompt must NOT be a positional arg — a long prompt passed as
    # the claude -p ARG is received mangled (model deflects); piping via stdin makes the cli judge reliable.
    check("invokes claude -p (print mode)", "-p" in argv, s)
    check("prompt is NOT a positional arg (delivered via STDIN)", "JUDGE THIS" not in argv, s)
    check("judge model is NOT the Opus benchmark arm (no self-preference)",
          "--model" in argv and "opus" not in [a.lower() for a in argv], s)
    check("uses JSON output (envelope parse)", "--output-format" in argv and "json" in argv, s)
    check("isolated with --strict-mcp-config (no DeepMap tools leak into the judge)",
          "--strict-mcp-config" in argv, s)
    check("carries a spend cap", "--max-budget-usd" in argv and "0.5" in argv, s)


def gate_read_answer(B) -> None:
    print("\n-- read_answer_from_scorecard: parses the captured stdout.json answer --")
    fn = getattr(B, "read_answer_from_scorecard", None)
    check("read_answer_from_scorecard exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        sc_dir = base / "results" / "run1" / "r" / "deepmap_on" / "run-1"
        sc_dir.mkdir(parents=True)
        (sc_dir / "stdout.json").write_text(json.dumps(
            {"result": "BillingService.run and InvoiceJob.perform break.", "total_cost_usd": 0.1}),
            encoding="utf-8")
        rec = {"answer_text_path": "results/run1/r/deepmap_on/run-1/stdout.json"}
        got = fn(rec, results_root_parent=base)
        check("returns the agent's answer text from the envelope",
              "BillingService.run" in got and "InvoiceJob.perform" in got, got)
        # a missing file -> '' (never crashes the whole scoring pass)
        got2 = fn({"answer_text_path": "results/nope/stdout.json"}, results_root_parent=base)
        check("missing answer file -> '' (no crash)", got2 == "")


def _make_results_json(B, results_dir: Path) -> None:
    """Write a synthetic CAPTURED results.json (two arms, unscored) using the real build_record."""
    def scorecard(arm):
        return {"repo": "owner/repo", "question_id": "q1", "family": "transitive_impact",
                "grep_can_do": False, "arm": arm, "run": 1, "exit_code": 0, "started_at": "t",
                "metrics": {k: None for k in B.METRIC_KEYS},
                "answer_text_path": f"results/run1/owner__repo/{arm}/run-1/stdout.json", "scored": False}
    repos = [{"name": "owner/repo", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    questions = [{"id": "q1", "repo": "owner/repo", "prompt": "What breaks if I change charge()?",
                  "family": "transitive_impact", "grep_can_do": False,
                  "answer_key": {"ground_truth_set": ["BillingService.run", "InvoiceJob.perform", "RetryWorker"],
                                 "ground_truth_answer": "BillingService.run, InvoiceJob.perform, RetryWorker"}}]
    record = B.build_record(repos, questions, [scorecard(B.ARM_ON), scorecard(B.ARM_OFF)],
                            B.machine_spec(), run_id="run1")
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "results.json").write_text(json.dumps(record, indent=2, sort_keys=True,
                                                         default=B._json_default), encoding="utf-8")
    return repos, questions


def gate_score_captured_run(B) -> None:
    print("\n-- score_captured_run: completeness + mocked judge -> rewritten results.json, MEASURED --")
    fn = getattr(B, "score_captured_run", None)
    check("score_captured_run exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        results_dir = Path(td) / "run1"
        repos, questions = _make_results_json(B, results_dir)
        # ON enumerates all 3 of the ground-truth set; OFF only 1 -> ON completeness > OFF.
        answers = {B.ARM_ON: "Breaks BillingService.run, InvoiceJob.perform, and RetryWorker.",
                   B.ARM_OFF: "Breaks BillingService.run."}
        reader = lambda rec: answers[rec["arm"]]
        judge = lambda prompt: '{"correctness_pct": 100, "wrong_high": 0}'
        rec = fn(results_dir, repos, questions, judge, reader)
        check("status flips to MEASURED (captured AND scored)", rec.get("measured") is True,
              str(rec.get("status_tag")))
        cards = {c["arm"]: c for c in rec["scorecards"]}
        check("ON completeness == 100 (all 3 enumerated)",
              cards[B.ARM_ON]["metrics"]["completeness_pct"] == 100.0,
              str(cards[B.ARM_ON]["metrics"]["completeness_pct"]))
        check("OFF completeness ~33.3 (1 of 3)",
              round(cards[B.ARM_OFF]["metrics"]["completeness_pct"], 1) == 33.3,
              str(cards[B.ARM_OFF]["metrics"]["completeness_pct"]))
        check("correctness filled from the mocked judge", cards[B.ARM_ON]["metrics"]["correctness_pct"] == 100.0)
        # results.json on disk was rewritten with the scored record
        on_disk = json.loads((results_dir / "results.json").read_text(encoding="utf-8"))
        check("results.json on disk rewritten as measured", on_disk.get("measured") is True)

        # HONESTY: a judge failure keeps the run UNSCORED (results can't read measured).
        results_dir2 = Path(td) / "run2"
        repos2, questions2 = _make_results_json(B, results_dir2)
        def boom(prompt):
            raise OSError("judge unreachable")
        rec2 = fn(results_dir2, repos2, questions2, boom, reader)
        check("judge failure -> measured stays False (no fabrication)", rec2.get("measured") is False)

        # PROVENANCE: the recorded _judge_model must equal the model actually passed (not a hardcoded
        # constant) — else the record can claim a different judge than the one that ran.
        results_dir3 = Path(td) / "run3"
        repos3, questions3 = _make_results_json(B, results_dir3)
        rec3 = fn(results_dir3, repos3, questions3, judge, reader, judge_model="haiku-test-alias")
        scored3 = [c for c in rec3["scorecards"] if c.get("scored")]
        check("recorded _judge_model == the model actually passed (provenance not hardcoded)",
              scored3 and all(c.get("_judge_model") == "haiku-test-alias" for c in scored3),
              str([c.get("_judge_model") for c in scored3]))


def gate_do_score_judge_model_guard(B) -> None:
    print("\n-- do_score: REFUSES a judge model that is the same as the Opus benchmark arm --")
    fn = getattr(B, "do_score", None)
    if not callable(fn):
        check("do_score exists (for guard test)", False)
        return
    saved = os.environ.get(B.ENGINE_ENV)
    os.environ[B.ENGINE_ENV] = "1"   # past the ENV-guard, so the model guard is what must catch it
    try:
        for bad in ("opus", "OPUS", B.MODEL):
            args = SimpleNamespace(score="whatever", clone_root=None, judge_model=bad,
                                   max_budget_usd=1.0, timeout_s=60)
            rc = fn(args, "t")
            check(f"--judge-model {bad!r} (== the arm) -> refuses (rc 2), spends nothing", rc == 2, str(rc))
    finally:
        if saved is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = saved


def gate_do_score_env_guard(B) -> None:
    print("\n-- do_score: ENV-guarded — refuses to spend without DEEPMAP_REAL_ENGINE=1 --")
    fn = getattr(B, "do_score", None)
    check("do_score exists", callable(fn))
    if not callable(fn):
        return
    saved = os.environ.pop(B.ENGINE_ENV, None)
    try:
        args = SimpleNamespace(score="whatever", clone_root=None, judge_model="sonnet",
                               max_budget_usd=1.0, timeout_s=60)
        rc = fn(args, "t")
        check("no DEEPMAP_REAL_ENGINE -> returns 2 (refuses, spends nothing)", rc == 2, str(rc))
    finally:
        if saved is not None:
            os.environ[B.ENGINE_ENV] = saved


def gate_judge_transport_selection(B) -> None:
    print("\n-- make_judge_call_fn: pluggable transport (cli default / api opt-in, honest-on-missing) --")
    fn = getattr(B, "make_judge_call_fn", None)
    check("make_judge_call_fn exists", callable(fn))
    if not callable(fn):
        return
    env = "DEEPMAP_JUDGE_TRANSPORT"
    saved = os.environ.get(env)
    try:
        # default (unset) -> the claude-p CLI transport: its call_fn invokes subprocess (mock it).
        os.environ.pop(env, None)
        calls = []
        orig_run = B.subprocess.run
        B.subprocess.run = lambda argv, **k: (calls.append(argv) or type("P", (), {"stdout": '{"result":"x"}', "stderr": "", "returncode": 0})())
        try:
            cli_call = fn(model_alias="sonnet", cwd=None, max_budget_usd=0.5, timeout_s=30)
            cli_call("p")
            check("default transport is the claude -p CLI (invokes claude subprocess)",
                  calls and "-p" in calls[-1] and any("claude" in str(a) for a in calls[-1]), str(calls[-1] if calls else None))
        finally:
            B.subprocess.run = orig_run
        # api transport -> fails HONEST (RuntimeError) when the SDK/key is absent (never fabricates).
        os.environ[env] = "api"
        api_call = fn(model_alias="sonnet", cwd=None, max_budget_usd=0.5, timeout_s=30)
        raised = None
        try:
            api_call("p")
        except Exception as exc:  # noqa: BLE001
            raised = exc
        # Either the SDK/key is missing (RuntimeError, the expected path here) or — if a key IS present —
        # it would make a real call; we only assert it DIDN'T silently fabricate (it raised or returned a str).
        check("api transport without SDK/key -> RAISES honestly (retry/unscored handle it, no fabrication)",
              raised is not None and "api" in str(raised).lower() or "anthropic" in str(raised).lower()
              or isinstance(raised, Exception), str(raised)[:100])
        # openrouter transport -> fails HONEST (RuntimeError naming OPENROUTER_API_KEY) when no key.
        os.environ[env] = "openrouter"
        saved_or = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            or_call = fn(model_alias="sonnet", cwd=None, max_budget_usd=0.5, timeout_s=30)
            r2 = None
            try:
                or_call("p")
            except Exception as exc:  # noqa: BLE001
                r2 = exc
            check("openrouter transport without OPENROUTER_API_KEY -> RAISES honestly (no fabrication)",
                  r2 is not None and "openrouter" in str(r2).lower(), str(r2)[:100])
            # WITH a key + a mocked HTTP layer: verify the request shape (URL/auth/model/messages) +
            # response parsing — no network, no spend. Locks the OpenRouter call contract.
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
            os.environ["DEEPMAP_JUDGE_MODEL"] = "openai/gpt-4o"
            import urllib.request as _ur
            captured = {}

            class _Resp:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *a):
                    return False

                def read(self_):
                    return b'{"choices":[{"message":{"content":"{\\"correctness_pct\\":88,\\"wrong_high\\":0}"}}]}'

            orig_open = _ur.urlopen
            _ur.urlopen = lambda req, **k: (captured.update(url=req.full_url, hdr=dict(req.header_items()),
                                                            body=req.data) or _Resp())
            try:
                or_call2 = fn(model_alias="sonnet", cwd=None, max_budget_usd=0.5, timeout_s=30)
                reply = or_call2("the judge prompt")
                import json as _json
                sent = _json.loads(captured.get("body", b"{}").decode("utf-8"))
                check("openrouter POSTs to the OpenRouter chat endpoint",
                      "openrouter.ai/api/v1/chat/completions" in (captured.get("url") or ""), captured.get("url"))
                check("openrouter sends the Bearer key + the configured model + user prompt",
                      any("bearer sk-or-test" in str(v).lower() for v in (captured.get("hdr") or {}).values())
                      and sent.get("model") == "openai/gpt-4o"
                      and any(m.get("content") == "the judge prompt" for m in sent.get("messages", [])),
                      str(sent.get("model")))
                check("openrouter parses the verdict out of the choices[0].message.content",
                      "correctness_pct" in reply and "88" in reply, reply[:80])
            finally:
                _ur.urlopen = orig_open
                os.environ.pop("DEEPMAP_JUDGE_MODEL", None)
        finally:
            if saved_or is not None:
                os.environ["OPENROUTER_API_KEY"] = saved_or
            else:
                os.environ.pop("OPENROUTER_API_KEY", None)
    finally:
        if saved is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = saved


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark Stage-2 scoring-CLI wiring gates (no spend)")
    print("=" * 64)

    B = _load()
    gate_build_judge_argv(B)
    gate_judge_transport_selection(B)
    gate_read_answer(B)
    gate_score_captured_run(B)
    gate_do_score_env_guard(B)
    gate_do_score_judge_model_guard(B)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
