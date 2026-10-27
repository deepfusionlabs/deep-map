#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the benchmark RUN-SCALE controls added for the first measured (Scenario-A) pass:

  * --runs-per-arm (build_record honors the actual per-arm count; default stays the methodology's 4) —
    a smaller first/calibration pass must be RECORDED honestly, not silently passed off as a 4-run median.
  * render_markdown_table reflects the record's ACTUAL runs_per_arm — the header states the real count
    and the `n⚠` partial-survival flag triggers on < that count (so a COMPLETE n=2 row is NOT mislabelled
    `2⚠` and the header never claims "runs/arm: 4" over an n=2 table).
  * merge_scorecards / do_merge — stitch the per-repo captured runs (Neo4j holds ONE graph at a time, so
    the matrix is captured per-repo) into ONE combined head-to-head table. Pure re-aggregation: no spend,
    and `measured` stays True only when EVERY merged scorecard carries a blind score.

Pure + hermetic: imports the harness module, exercises the functions on synthetic records + temp result
dirs; never spends, never invokes claude or Neo4j.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"

# Labels/asserts mention the `n⚠` partial-survival flag (U+26A0); a legacy Windows console codepage
# (cp1252/cp1255) cannot encode it. Force UTF-8 so the gate output never crashes on the display layer.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

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
    spec = importlib.util.spec_from_file_location("dm_run_scale", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _scorecard(B, *, repo, qid, arm, scored, completeness=80.0):
    metrics = {k: None for k in B.METRIC_KEYS}
    metrics["tool_calls"] = 5
    metrics["wall_clock_s"] = 1.0
    if scored:
        metrics["completeness_pct"] = completeness
        metrics["correctness_pct"] = 90.0
        metrics["wrong_high"] = 0
    return {"repo": repo, "question_id": qid, "family": "transitive", "grep_can_do": False,
            "arm": arm, "run": 1, "exit_code": 0, "metrics": metrics, "scored": scored}


def gate_build_record_runs_per_arm() -> None:
    print("\n-- build_record records the ACTUAL runs_per_arm (default 4; override honored) --")
    B = _load()
    spec = {"os": "t", "python": "3.x"}
    repos = [{"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    qs = [{"id": "q", "repo": "r", "family": "transitive", "grep_can_do": False}]
    rec_default = B.build_record(repos, qs, [], spec, run_id="x")
    check("default runs_per_arm == RUNS_PER_ARM (4)", rec_default["runs_per_arm"] == B.RUNS_PER_ARM)
    rec_two = B.build_record(repos, qs, [], spec, run_id="x", runs_per_arm=2)
    check("override runs_per_arm=2 recorded", rec_two["runs_per_arm"] == 2)


def gate_render_reflects_runs_per_arm() -> None:
    print("\n-- render_markdown_table reflects the record's runs_per_arm (header + n⚠ threshold) --")
    B = _load()
    spec = {"os": "t", "python": "3.x", "claude_version": "v"}
    repos = [{"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    qs = [{"id": "q", "repo": "r", "family": "transitive", "grep_can_do": False}]
    # Two scored runs per arm at n=2 → a COMPLETE n=2 row.
    cards = []
    for arm in B.ARMS:
        for _ in range(2):
            cards.append(_scorecard(B, repo="r", qid="q", arm=arm, scored=True))
    rec = B.build_record(repos, qs, cards, spec, run_id="x", runs_per_arm=2)
    md = B.render_markdown_table(rec)
    check("header states the actual count (runs/arm: 2)", "runs/arm: 2" in md, md.split(chr(10))[8] if len(md.split(chr(10)))>8 else md[:200])
    check("header does NOT claim runs/arm: 4 over an n=2 table", "runs/arm: 4" not in md)
    check("a COMPLETE n=2 row is NOT flagged 2⚠ (partial)", "2⚠" not in md)
    check("the n⚠ legend cites the actual count (2)", "FEWER than 2 runs" in md)


def gate_render_partial_flag_against_actual() -> None:
    print("\n-- render: a row with FEWER runs than runs_per_arm IS flagged n⚠ --")
    B = _load()
    spec = {"os": "t", "python": "3.x", "claude_version": "v"}
    repos = [{"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    qs = [{"id": "q", "repo": "r", "family": "transitive", "grep_can_do": False}]
    # n=2 target but only 1 surviving run for ON → flagged 1⚠.
    cards = [_scorecard(B, repo="r", qid="q", arm=B.ARM_ON, scored=True)]
    for _ in range(2):
        cards.append(_scorecard(B, repo="r", qid="q", arm=B.ARM_OFF, scored=True))
    rec = B.build_record(repos, qs, cards, spec, run_id="x", runs_per_arm=2)
    md = B.render_markdown_table(rec)
    check("a 1-of-2 row IS flagged 1⚠", "1⚠" in md)


def gate_merge_scorecards_pure() -> None:
    print("\n-- merge_scorecards: concatenate per-repo scorecards; rpa = max nominal target --")
    B = _load()
    spec = {"os": "t"}
    r1 = {"scorecards": [_scorecard(B, repo="a", qid="qa", arm=B.ARM_ON, scored=True)],
          "machine_spec": spec, "runs_per_arm": 2}
    r2 = {"scorecards": [_scorecard(B, repo="b", qid="qb", arm=B.ARM_ON, scored=True),
                          _scorecard(B, repo="b", qid="qb", arm=B.ARM_OFF, scored=True)],
          "machine_spec": {"os": "second"}, "runs_per_arm": 4}
    combined, merged_spec, rpa = B.merge_scorecards([r1, r2])
    check("all scorecards concatenated (1+2=3)", len(combined) == 3)
    check("first machine_spec wins", merged_spec.get("os") == "t")
    check("runs_per_arm = max nominal (4)", rpa == 4)
    check("empty input → defaults (no crash)", B.merge_scorecards([])[2] == B.RUNS_PER_ARM)


def gate_merge_measured_only_when_all_scored() -> None:
    print("\n-- merged record: measured True only when EVERY merged scorecard is scored --")
    B = _load()
    spec = {"os": "t", "python": "3.x"}
    repos = [{"name": "a", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"},
             {"name": "b", "url": "u", "pinned_sha": "b" * 40, "license": "MIT"}]
    qs = [{"id": "qa", "repo": "a", "family": "transitive", "grep_can_do": False},
          {"id": "qb", "repo": "b", "family": "transitive", "grep_can_do": False}]
    scored = [_scorecard(B, repo="a", qid="qa", arm=B.ARM_ON, scored=True),
              _scorecard(B, repo="b", qid="qb", arm=B.ARM_ON, scored=True)]
    rec_all = B.build_record(repos, qs, scored, spec, run_id="m")
    check("all scored → measured True", rec_all["measured"] is True)
    mixed = scored + [_scorecard(B, repo="b", qid="qb", arm=B.ARM_OFF, scored=False)]
    rec_mixed = B.build_record(repos, qs, mixed, spec, run_id="m")
    check("one unscored merged card → measured False (honesty)", rec_mixed["measured"] is False)
    md = B.render_markdown_table(rec_mixed)
    check("both repos appear in the merged table", "| a |" in md and "| b |" in md)


def gate_do_merge_end_to_end() -> None:
    print("\n-- do_merge: reads per-repo results.json, writes ONE combined results.md/json --")
    B = _load()
    spec = {"os": "t", "python": "3.x"}
    repos, questions = B.load_config()
    # Pick two real question ids from two different repos so build_record's metadata lines up.
    by_repo = {}
    for q in questions:
        by_repo.setdefault(q["repo"], q["id"])
        if len(by_repo) >= 2:
            break
    (repoA, qA), (repoB, qB) = list(by_repo.items())[:2]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        orig_root = B.RESULTS_ROOT
        B.RESULTS_ROOT = root
        try:
            for rid, repo, qid in (("runA", repoA, qA), ("runB", repoB, qB)):
                cards = [_scorecard(B, repo=repo, qid=qid, arm=arm, scored=True) for arm in B.ARMS]
                rec = B.build_record(repos, questions, cards, spec, run_id=rid, runs_per_arm=2)
                (root / rid).mkdir(parents=True, exist_ok=True)
                (root / rid / "results.json").write_text(json.dumps(rec, default=B._json_default), encoding="utf-8")

            class A:  # minimal argparse.Namespace stand-in
                merge = "runA,runB"
            rc = B.do_merge(A(), "combined")
            check("do_merge returns 0", rc == 0)
            out = root / "combined" / "results.json"
            check("combined results.json written", out.is_file())
            rec = json.loads(out.read_text(encoding="utf-8"))
            cards = rec.get("scorecards", [])
            repos_in = {c["repo"] for c in cards}
            check("combined holds BOTH repos' scorecards", repoA in repos_in and repoB in repos_in)
            check("combined is measured (all scored)", rec["measured"] is True)
            md = (root / "combined" / "results.md").read_text(encoding="utf-8")
            check("combined results.md has both repo rows", f"| {repoA} |" in md and f"| {repoB} |" in md)
            # A missing run-id is skipped, never fabricated.
            class A2:
                merge = "runA,does-not-exist"
            check("do_merge tolerates a missing run-id (still 0 with the present one)",
                  B.do_merge(A2(), "combined2") == 0)
        finally:
            B.RESULTS_ROOT = orig_root


def gate_run_output_dir_unique_per_question() -> None:
    """run_one MUST persist each question's raw output (stdout.json — the captured ANSWER the blind
    scorer reads) to a path that includes the QUESTION ID. Keyed by repo+arm+run alone, every question
    in a repo writes to the SAME <repo>/<arm>/run-N/ dir, overwriting each other; then EVERY scorecard's
    answer_text_path points to the LAST-written file and scoring grades every question against one
    answer — the Scenario-A capture-collision bug."""
    print("\n-- run_output_dir: per-question path (no cross-question answer collision) --")
    B = _load()
    fn = getattr(B, "run_output_dir", None)
    check("run_output_dir helper exists", callable(fn))
    if not callable(fn):
        return
    out = Path("X")
    q1 = {"repo": "psf/requests", "id": "requests-transport-1"}
    q2 = {"repo": "psf/requests", "id": "requests-concept-1"}
    d1 = fn(out, q1, B.ARM_ON, 1)
    d2 = fn(out, q2, B.ARM_ON, 1)
    check("different questions (same repo/arm/run) → DIFFERENT dirs", d1 != d2, f"{d1} vs {d2}")
    check("path includes question id (q1)", "requests-transport-1" in str(d1))
    check("path includes question id (q2)", "requests-concept-1" in str(d2))
    check("same question/arm/run → stable path", fn(out, q1, B.ARM_ON, 1) == d1)
    check("arm distinguishes", fn(out, q1, B.ARM_OFF, 1) != d1)
    check("run index distinguishes", fn(out, q1, B.ARM_ON, 2) != d1)
    check("repo still in the path", "psf__requests" in str(d1).replace("\\", "/"))


def gate_console_encoding_safe() -> None:
    """The harness prints status glyphs (— and →) to the console. On a legacy Windows codepage
    (cp1252/cp1255) a raw print of those crashes with UnicodeEncodeError — e.g. `--merge` wrote its
    files then died on the summary print, exiting non-zero and making an orchestrator read the merge
    as FAILED. The CLI must make its OWN console UTF-8-safe (the written result files are already
    utf-8); it must not depend on the caller setting PYTHONIOENCODING."""
    print("\n-- console encoding: unicode status glyphs never crash a legacy codepage --")
    B = _load()
    helper = getattr(B, "_ensure_console_utf8", None)
    check("_ensure_console_utf8 helper exists", callable(helper))
    if not callable(helper):
        return
    import io
    saved_out, saved_err = sys.stdout, sys.stderr
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1255", errors="strict")
    crashed = False
    try:
        sys.stdout = buf
        helper()                       # the safeguard the CLI runs at startup
        print("captured 24 runs → out — done")   # the exact glyphs that crashed --merge/--run
        sys.stdout.flush()
    except UnicodeEncodeError:
        crashed = True
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    check("after the safeguard, printing '→' and '—' to a cp1255 stream does NOT crash", not crashed)
    # And main() must actually invoke the safeguard (so the real CLI is protected, not just the helper).
    import inspect
    src = inspect.getsource(B.main)
    check("main() calls _ensure_console_utf8 at startup", "_ensure_console_utf8" in src)


def gate_cli_parses_new_flags() -> None:
    print("\n-- argparse: --runs-per-arm and --merge are wired --")
    B = _load()
    import argparse
    # Build the same parser main() builds by calling it with --help would exit; instead re-run main's
    # parser indirectly via a tiny parse using the public flags through do_* dispatch is heavy — assert
    # the option strings exist by introspecting a fresh parse of a no-op mode.
    # Simplest robust check: parse a --selftest line augmented with the new flags must not error.
    rc_help_free = True
    try:
        # main() dispatches by mode; we only need argparse to ACCEPT the flags. Use --merge with no ids
        # → do_merge returns 2 (no ids) WITHOUT spending; that proves the flag parsed + dispatched.
        rc = B.main(["--merge", ""])
        check("--merge dispatches (empty ids → rc 2, no spend)", rc == 2)
    except SystemExit:
        rc_help_free = False
    check("argparse accepted --merge without SystemExit", rc_help_free)


def main() -> int:
    print("benchmark run-scale gates (no-Neo4j): --runs-per-arm + --merge + honest rendering")
    gate_build_record_runs_per_arm()
    gate_render_reflects_runs_per_arm()
    gate_render_partial_flag_against_actual()
    gate_merge_scorecards_pure()
    gate_merge_measured_only_when_all_scored()
    gate_do_merge_end_to_end()
    gate_run_output_dir_unique_per_question()
    gate_console_encoding_safe()
    gate_cli_parses_new_flags()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
