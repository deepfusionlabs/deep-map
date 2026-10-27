#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the benchmark's HONESTY state-machine + fail-closed pin gate.

This locks the load-bearing honesty contract of `benchmark/run_benchmark.py` — the hard rule
"never present a benchmark NUMBER as measured until G3" (CLAUDE.md / plan §7.7/§7.9):

  * status_tag / _all_scored — the THREE states (designed → captured-UNSCORED → measured); capture
    alone is NOT "measured", and a single unscored run keeps the whole set out of "measured".
  * build_record — `measured` is True ONLY when captured AND scored; `headline` stays None even when
    measured (a measured one-liner is never auto-fabricated); `where_grep_wins` is HYPOTHESIS until
    measured; provenance.publishable flips indicative→measured in lockstep.
  * aggregate — MEDIAN (a DeepMap choice, NOT mean) with min/max/n, partial-survival visible.
  * assert_pinned_clean — FAIL CLOSED on a placeholder/malformed/mismatched/dirty clone (never spend
    a token against a stale tree). Hermetic: builds a throwaway git repo in a tempdir.
  * render_markdown_table — never prints "MEASURED" over an unscored table; cells are em-dash, never
    fabricated numbers.

Pure + hermetic: imports the harness module, calls the functions on synthetic records; never spends,
never invokes claude or Neo4j. (Git is used only for the assert_pinned_clean gate, in a tempdir.)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
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


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_honesty", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rec(B, scored: bool, *, repo="r", qid="q", arm=None, tool_calls=5, completeness=80.0):
    """A synthetic per-(arm,question,run) record in run_one's shape."""
    arm = arm or B.ARM_ON
    metrics = {k: None for k in B.METRIC_KEYS}
    metrics["tool_calls"] = tool_calls
    metrics["cost_usd"] = 0.1
    metrics["wall_clock_s"] = 1.0
    if scored:
        metrics["completeness_pct"] = completeness
        metrics["correctness_pct"] = 90.0
        metrics["wrong_high"] = 0
    return {"repo": repo, "question_id": qid, "family": "f", "grep_can_do": False,
            "arm": arm, "run": 1, "exit_code": 0, "started_at": "t",
            "metrics": metrics, "answer_text_path": "p", "scored": scored}


def _capture_via_run_one(B, *, tool_calls=10, cost=0.1, is_error=False, subtype="success",
                         exit_code=0, repo="r", qid="q", arm=None, run_idx=1):
    """Produce a per-run record through the REAL capture path — `run_one` with `subprocess` stubbed
    (NO claude, NO spend, NO Neo4j). This is the ISS-benchmark:001 (IF-1) regression anchor:
    `run_one` persists `metrics` as `{k: metrics.get(k) for k in METRIC_KEYS}` PLUS the error-state
    keys the median-exclusion guard (`_is_errored_run`) reads. Building the record via `run_one` —
    NOT by hand-injecting `_is_error`/`_exit_code` into a metrics dict — means a regression that drops
    those keys from the persist step makes the exclusion gates go RED (a hand-injected dict never
    could — that shortcut is exactly what masked the original bug)."""
    import json as _json
    from types import SimpleNamespace
    arm = arm or B.ARM_ON
    # A real `claude --output-format json` envelope. is_error:true with exit 0 is the budget /
    # max-turns abort case (well-formed, still carries total_cost_usd + num_turns) — the pollution
    # that M12 must exclude from the medians.
    env = _json.dumps({
        "is_error": is_error, "subtype": subtype,
        "total_cost_usd": cost, "num_turns": tool_calls,
        "usage": {"input_tokens": 11, "output_tokens": 22},
        "result": "answer text (never scored here)",
    })

    def fake_run(argv, **kwargs):        # run_one reads only .stdout / .stderr / .returncode
        return SimpleNamespace(stdout=env, stderr="", returncode=exit_code)

    td = tempfile.TemporaryDirectory()
    saved_sub, saved_root = B.subprocess, B.RESULTS_ROOT
    try:
        # Replace only run_benchmark's `subprocess` reference (a shim with .run) — the real
        # subprocess module is left untouched, so concurrent/other gates are unaffected.
        B.subprocess = SimpleNamespace(run=fake_run)
        B.RESULTS_ROOT = Path(td.name) / "results"
        B.RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        out_dir = B.RESULTS_ROOT / "trun"
        repo_dir = Path(td.name) / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        question = {"id": qid, "repo": repo, "prompt": "p", "family": "f", "grep_can_do": False}
        rec = B.run_one(arm, question, repo_dir, Path("cfg.json"), run_idx, out_dir, 5.0, 60)
    finally:
        B.subprocess, B.RESULTS_ROOT = saved_sub, saved_root
        td.cleanup()
    return rec


# --------------------------------------------------------------------------- #
# status_tag / _all_scored — the three honesty states.
# --------------------------------------------------------------------------- #

def gate_status_tag_three_state() -> None:
    print("\n-- status_tag / _all_scored: designed -> captured-UNSCORED -> measured --")
    B = _load_benchmark()
    check("no runs -> DESIGNED", B.status_tag([]) == B.TAG_DESIGNED)
    check("empty set is not 'all scored'", B._all_scored([]) is False)

    unscored = [_rec(B, scored=False)]
    check("captured but unscored -> CAPTURED (not measured)", B.status_tag(unscored) == B.TAG_CAPTURED)
    check("_all_scored False on an unscored run", B._all_scored(unscored) is False)

    scored = [_rec(B, scored=True), _rec(B, scored=True, arm=B.ARM_OFF)]
    check("all runs scored -> MEASURED", B.status_tag(scored) == B.TAG_MEASURED)
    check("_all_scored True when every run carries completeness_pct", B._all_scored(scored) is True)

    # MUTATION GUARD: one unscored run among scored ones must NOT read as measured (catches any vs all).
    mixed = [_rec(B, scored=True), _rec(B, scored=False, arm=B.ARM_OFF)]
    check("one unscored run keeps the WHOLE set out of measured", B.status_tag(mixed) == B.TAG_CAPTURED)
    check("_all_scored is ALL, not ANY", B._all_scored(mixed) is False)


def gate_build_record_honesty_flags() -> None:
    print("\n-- build_record: measured/headline/where_grep_wins honesty flags --")
    B = _load_benchmark()
    spec = {"os": "test-os", "python": "3.x"}
    repos = [{"name": "r", "url": "u", "pinned_sha": "PIN AT RUN TIME", "license": "MIT"}]
    questions = [{"id": "q", "repo": "r", "family": "f", "grep_can_do": False}]

    # (a) designed — no runs.
    rec0 = B.build_record(repos, questions, [], spec, run_id="t0")
    check("designed: measured=False", rec0["measured"] is False)
    check("designed: captured=False", rec0["captured"] is False)
    check("designed: status_tag=DESIGNED", rec0["status_tag"] == B.TAG_DESIGNED)
    check("designed: headline is None (never fabricated)", rec0["headline"] is None)
    check("designed: where_grep_wins is HYPOTHESIS",
          isinstance(rec0["where_grep_wins"], str) and "HYPOTHESIS" in rec0["where_grep_wins"])
    check("designed: provenance.publishable=indicative", rec0["provenance"]["publishable"] == "indicative")

    # (b) captured but UNSCORED — runs exist, no scores.
    rec1 = B.build_record(repos, questions, [_rec(B, scored=False)], spec, run_id="t1")
    check("captured-unscored: measured=False", rec1["measured"] is False)
    check("captured-unscored: captured=True", rec1["captured"] is True)
    check("captured-unscored: status_tag=CAPTURED", rec1["status_tag"] == B.TAG_CAPTURED)
    check("captured-unscored: headline still None", rec1["headline"] is None)
    check("captured-unscored: where_grep_wins still HYPOTHESIS",
          "HYPOTHESIS" in (rec1["where_grep_wins"] or ""))

    # (c) measured — runs exist AND all scored.
    rec2 = B.build_record(repos, questions, [_rec(B, scored=True)], spec, run_id="t2")
    check("measured: measured=True", rec2["measured"] is True)
    check("measured: status_tag=MEASURED", rec2["status_tag"] == B.TAG_MEASURED)
    # KEY honesty invariant: even when measured, the headline is NOT auto-written — it stays None
    # (the one-liner is hand-authored after the blind pass; the harness never fabricates it).
    check("measured: headline STILL None (never auto-fabricated)", rec2["headline"] is None)
    check("measured: where_grep_wins cleared to None (filled by blind pass)",
          rec2["where_grep_wins"] is None)
    check("measured: provenance.publishable=measured", rec2["provenance"]["publishable"] == "measured")


# --------------------------------------------------------------------------- #
# aggregate — MEDIAN, min/max/n, partial survival.
# --------------------------------------------------------------------------- #

def gate_aggregate_median() -> None:
    print("\n-- aggregate: MEDIAN (not mean) + min/max/n, partial survival visible --")
    B = _load_benchmark()
    runs = [_rec(B, scored=True, tool_calls=tc) for tc in (10, 20, 30, 100)]
    agg = B.aggregate(runs)
    rows = agg["rows"]
    check("one aggregate row per (repo,qid,arm)", len(rows) == 1, str(len(rows)))
    tc = rows[0]["tool_calls"]
    # median of (10,20,30,100) = 25; mean would be 40 — locks median, not mean.
    check("tool_calls median == 25 (median, NOT the mean 40)", tc["median"] == 25, str(tc))
    check("tool_calls min == 10", tc["min"] == 10)
    check("tool_calls max == 100", tc["max"] == 100)
    check("tool_calls n == 4", tc["n"] == 4)

    # partial survival: a None metric is excluded from n (no fabricated value).
    partial = [_rec(B, scored=True, tool_calls=5), _rec(B, scored=True, tool_calls=None)]
    agg2 = B.aggregate(partial)
    tc2 = agg2["rows"][0]["tool_calls"]
    check("None metric excluded from aggregate n", tc2["n"] == 1, str(tc2))

    # M12: an ERRORED run (is_error / non-zero exit) is EXCLUDED from the median (its tool_calls are
    # meaningless) and counted as errored_excluded — it must not pollute the valid-run median. Build
    # the errored records through the REAL capture path (run_one), NOT by hand-injecting the error
    # keys — that shortcut masked ISS-benchmark:001 (run_one dropped them, so the guard never fired).
    good = _capture_via_run_one(B, tool_calls=10, is_error=False, exit_code=0)
    bad = _capture_via_run_one(B, tool_calls=9999, is_error=True, subtype="error_max_turns", exit_code=0)
    rowE = B.aggregate([good, bad])["rows"][0]
    check("errored run EXCLUDED from the median (n==1, not 2)", rowE["tool_calls"]["n"] == 1, str(rowE["tool_calls"]))
    check("the errored value (9999) is NOT in the median (==10)", rowE["tool_calls"]["median"] == 10, str(rowE["tool_calls"]))
    check("errored_excluded counts the dropped run", rowE.get("errored_excluded") == 1, str(rowE.get("errored_excluded")))
    only_bad = _capture_via_run_one(B, tool_calls=5, is_error=False, exit_code=1)
    rowA = B.aggregate([only_bad])["rows"][0]
    check("an all-errored group still appears (runs=0, errored_excluded=1)",
          rowA["runs"] == 0 and rowA["errored_excluded"] == 1, str(rowA))


def gate_errored_run_excluded_real_capture_path() -> None:
    """ISS-benchmark:001 (IF-1): the errored-run median-exclusion guard must fire for a record built
    by the REAL capture path (run_one -> aggregate), not only for one with _is_error/_exit_code
    hand-injected into metrics. The bug: run_one persisted only METRIC_KEYS, dropping the error-state
    keys `_is_errored_run` reads (the exit code lived at the record TOP level as `exit_code`, and
    `_exit_code` was set only in `_probe_call`), so aggregate() medianed an errored run in as valid
    and errored_excluded stayed 0. This drives run_one (subprocess stubbed, no spend) so a persist
    regression that drops those keys re-reddens the gate."""
    print("\n-- ISS-benchmark:001 (IF-1): errored run EXCLUDED via the REAL capture path (run_one -> aggregate) --")
    B = _load_benchmark()

    clean = _capture_via_run_one(B, tool_calls=10, is_error=False, exit_code=0, run_idx=1)
    # (a) a budget / max-turns abort: well-formed envelope, is_error:true, but EXIT 0 — the case that
    # the top-level `exit_code` alone would MISS; only the persisted metrics._is_error catches it.
    aborted = _capture_via_run_one(B, tool_calls=9999, is_error=True, subtype="error_max_turns",
                                   exit_code=0, run_idx=2)
    # (b) a non-zero-exit run (a crash): caught via the persisted metrics._exit_code.
    crashed = _capture_via_run_one(B, tool_calls=8888, is_error=False, exit_code=1, run_idx=3)

    # The capture path must carry the error-state keys where _is_errored_run reads them.
    check("run_one persists _is_error into record['metrics']", "_is_error" in clean["metrics"],
          str(sorted(clean["metrics"])))
    check("run_one persists _exit_code into record['metrics']", "_exit_code" in clean["metrics"])
    check("a clean run is NOT flagged errored (real path)", B._is_errored_run(clean) is False)
    check("an is_error:true / exit-0 abort IS flagged errored (real path)",
          B._is_errored_run(aborted) is True, f"metrics={aborted.get('metrics')}")
    check("a non-zero-exit run IS flagged errored (real path)",
          B._is_errored_run(crashed) is True, f"exit_code={crashed['metrics'].get('_exit_code')}")

    # End-to-end: aggregate must EXCLUDE both errored runs and count them, medianing only the clean one.
    row = B.aggregate([clean, aborted, crashed])["rows"][0]
    check("only the clean run enters the median (n==1, not 3)", row["tool_calls"]["n"] == 1, str(row["tool_calls"]))
    check("neither errored value (9999/8888) pollutes the median (==10)",
          row["tool_calls"]["median"] == 10, str(row["tool_calls"]))
    check("errored_excluded counts BOTH captured errored runs (==2)",
          row["errored_excluded"] == 2, str(row.get("errored_excluded")))


# --------------------------------------------------------------------------- #
# assert_pinned_clean — FAIL CLOSED (hermetic git repo).
# --------------------------------------------------------------------------- #

def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)


def gate_assert_pinned_clean_failclosed() -> None:
    print("\n-- assert_pinned_clean: FAIL CLOSED on placeholder/malformed/mismatch/dirty --")
    B = _load_benchmark()

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # placeholder + malformed reject BEFORE any git call (sha-shape checks).
        check("placeholder SHA -> FAIL CLOSED (reason)", B.assert_pinned_clean(d, "PIN AT RUN TIME") is not None)
        check("empty SHA -> FAIL CLOSED (reason)", B.assert_pinned_clean(d, "") is not None)
        check("malformed (non-40-hex) SHA -> FAIL CLOSED (reason)",
              B.assert_pinned_clean(d, "deadbeef") is not None)
        check("40-char non-hex SHA -> FAIL CLOSED (reason)",
              B.assert_pinned_clean(d, "z" * 40) is not None)

    if not shutil.which("git"):
        check("(git on PATH for the live pin checks)", True, "skipped — git not installed")
        return

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        init = _git("init", "-q", str(d))
        if init.returncode != 0:
            check("(git init succeeded)", False, init.stderr.strip())
            return
        (d / "f.txt").write_text("hello", encoding="utf-8")
        _git("-C", str(d), "add", "f.txt")
        commit = _git("-c", "user.email=t@t", "-c", "user.name=t", "-C", str(d),
                      "commit", "-q", "-m", "init")
        if commit.returncode != 0:
            check("(git commit succeeded)", False, commit.stderr.strip())
            return
        head = _git("-C", str(d), "rev-parse", "HEAD").stdout.strip()

        check("clone AT pinned SHA + clean -> None (PASS, may spend)",
              B.assert_pinned_clean(d, head) is None, f"head={head}")
        wrong = ("0" * 40) if head.lower() != "0" * 40 else ("1" * 40)
        r_wrong = B.assert_pinned_clean(d, wrong)
        check("HEAD != pinned SHA -> FAIL CLOSED (reason)", r_wrong is not None)
        check("mismatch reason mentions fail-closed", "FAIL CLOSED" in (r_wrong or ""))

        # dirty worktree at the right SHA -> still FAIL CLOSED.
        (d / "f.txt").write_text("changed", encoding="utf-8")
        r_dirty = B.assert_pinned_clean(d, head)
        check("dirty worktree -> FAIL CLOSED (reason)", r_dirty is not None)
        check("dirty reason says DIRTY", "DIRTY" in (r_dirty or "").upper())


def gate_assert_pinned_clean_ignores_untracked_sidecars() -> None:
    """The OFF arm greps the clone's TRACKED source at the pinned SHA; the harness itself drops
    UNTRACKED sidecars into the clone that are NOT source contamination — `.deepmap/graph_build.json`
    (sync writes it, and assert_graph_at_pinned_sha REQUIRES it) and `.oss-kit/` (a Stop-hook artifact
    of a headless `claude -p` run in the clone). A third-party clone does not gitignore these, so a
    naive `git status --porcelain` flags them and FAILS CLOSED — a contradiction with the graph-SHA
    check that needs `.deepmap/`. assert_pinned_clean must therefore IGNORE purely-untracked entries
    and fail only on a TRACKED modification (reset_pristine wipes untracked leakage before every run)."""
    print("\n-- assert_pinned_clean: ignore untracked harness sidecars (.deepmap/.oss-kit), still fail on tracked edits --")
    B = _load_benchmark()
    if not shutil.which("git"):
        check("(git on PATH for the untracked-sidecar check)", True, "skipped — git not installed")
        return
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        if _git("init", "-q", str(d)).returncode != 0:
            check("(git init succeeded)", False); return
        (d / "f.txt").write_text("hello", encoding="utf-8")
        _git("-C", str(d), "add", "f.txt")
        if _git("-c", "user.email=t@t", "-c", "user.name=t", "-C", str(d),
                "commit", "-q", "-m", "init").returncode != 0:
            check("(git commit succeeded)", False); return
        head = _git("-C", str(d), "rev-parse", "HEAD").stdout.strip()

        # The exact reproduction of the Scenario-A integration bug: the sync-written sidecar.
        (d / ".deepmap").mkdir()
        (d / ".deepmap" / "graph_build.json").write_text('{"built_at_sha":"x"}', encoding="utf-8")
        check("untracked .deepmap/ sidecar -> NOT dirty (PASS — graph-SHA check needs it)",
              B.assert_pinned_clean(d, head) is None,
              f"got: {B.assert_pinned_clean(d, head)!r}")

        # An additional untracked artifact (the Stop-hook .oss-kit/) must also not trip it.
        (d / ".oss-kit").mkdir()
        (d / ".oss-kit" / ".autopilot-iter").write_text("1", encoding="utf-8")
        check("untracked .oss-kit/ + .deepmap/ -> still NOT dirty (PASS)",
              B.assert_pinned_clean(d, head) is None)

        # But a MODIFIED TRACKED file (the real contamination the OFF arm would grep) STILL fails closed.
        (d / "f.txt").write_text("changed", encoding="utf-8")
        r = B.assert_pinned_clean(d, head)
        check("modified TRACKED file -> STILL FAIL CLOSED (reason)", r is not None)
        check("tracked-edit reason still says DIRTY", "DIRTY" in (r or "").upper())


# --------------------------------------------------------------------------- #
# render_markdown_table — no fabricated numbers; banner honesty.
# --------------------------------------------------------------------------- #

def gate_render_no_fabrication() -> None:
    print("\n-- render_markdown_table: never 'MEASURED' over an unscored table; em-dash cells --")
    B = _load_benchmark()
    spec = {"os": "test-os", "python": "3.x"}
    repos = [{"name": "r", "url": "u", "pinned_sha": "PIN AT RUN TIME", "license": "MIT"}]
    questions = [{"id": "q", "repo": "r", "family": "f", "grep_can_do": False}]

    designed = B.build_record(repos, questions, [], spec, run_id="t")
    md = B.render_markdown_table(designed)
    first_line = md.splitlines()[0]
    check("designed banner is the DESIGNED tag", "DESIGNED" in first_line.upper(), first_line)
    check("designed banner does NOT say MEASURED", "MEASURED" not in first_line.upper(), first_line)
    check("empty table uses em-dash placeholder cells (no fabricated numbers)", "—" in md)
    check("headline line is the EMPTY placeholder, not a number",
          "EMPTY until runs are captured" in md)

    # captured-but-unscored: the banner must still not read MEASURED.
    captured = B.build_record(repos, questions, [_rec(B, scored=False)], spec, run_id="t")
    md2 = B.render_markdown_table(captured)
    check("captured-unscored banner is not MEASURED", "MEASURED" not in md2.splitlines()[0].upper())

    # M05: wrong_high is a SAFETY metric — the table shows the WORST-case (max), not the median, so a
    # minority confidently-wrong answer (e.g. wrong_high [0,0,2]) is never smoothed to 0.
    wruns = []
    for wh in (0, 0, 2):
        r = _rec(B, scored=True)
        r["metrics"]["wrong_high"] = wh
        wruns.append(r)
    md3 = B.render_markdown_table(B.build_record(repos, questions, wruns, spec, run_id="t"))
    check("wrong_high column header marks it WORST (not median)", "wrong_high (WORST)" in md3,
          md3.splitlines()[0])
    datarow = [ln for ln in md3.splitlines() if ln.startswith("| r | q |")]
    check("wrong_high cell shows the worst-case 2 (max), not the median 0",
          any("| 2 |" in ln for ln in datarow), str(datarow))


def gate_machine_spec() -> None:
    print("\n-- machine_spec: the published reproducibility snapshot pins the G3 model + never fabricates --")
    B = _load_benchmark()
    spec = B.machine_spec()
    # Required keys for the §7.8 reproducibility record (T18: 'commits ... machine spec').
    required = ("os", "os_system", "cpu", "cpu_count", "python", "claude_version",
                "neo4j_version", "git_version", "model", "captured_at")
    for k in required:
        check(f"machine_spec carries '{k}'", k in spec, str(sorted(spec)))
    # The model is the HONESTY anchor — the benchmark is gated on Opus 4.8 specifically (G3); the
    # recorded model must be exactly that id, never a stand-in.
    check("model is pinned to the G3-required id (claude-opus-4-8)",
          spec.get("model") == B.MODEL == "claude-opus-4-8", str(spec.get("model")))
    # An undetectable tool must be None (honest 'unknown'), never a fabricated version string.
    check("graphify_version is None here (plugin tree property — not fabricated)",
          spec.get("graphify_version") is None)
    check("captured_at is an ISO-Z timestamp string", isinstance(spec.get("captured_at"), str)
          and spec["captured_at"].endswith("Z"), str(spec.get("captured_at")))
    check("python is a version string", isinstance(spec.get("python"), str) and spec["python"][0].isdigit())


def gate_minimal_yaml_fallback() -> None:
    print("\n-- _minimal_yaml_list / _coerce_scalar: stdlib fallback never silently mis-parses --")
    B = _load_benchmark()

    # _coerce_scalar: booleans/null/numbers/quotes — the honesty-critical one is bool coercion,
    # because validate_config requires grep_can_do to be an actual bool, not the string "false".
    check("'true' -> bool True", B._coerce_scalar("true") is True)
    check("'false' -> bool False", B._coerce_scalar("false") is False)
    check("'null' -> None", B._coerce_scalar("null") is None)
    check("'42' -> int 42", B._coerce_scalar("42") == 42 and isinstance(B._coerce_scalar("42"), int))
    check("'3.14' -> float", abs(B._coerce_scalar("3.14") - 3.14) < 1e-9)
    check("quoted string unquoted", B._coerce_scalar('"hello"') == "hello")
    check("plain string stays string", B._coerce_scalar("caddy") == "caddy")

    # A bare top-level list of single-level mappings, with a nested mapping / block scalar
    # (`answer_key: |`) marked opaque and its DEEPER lines (incl. nested `- `) skipped.
    text = (
        "- name: caddy\n"
        "  url: https://github.com/caddyserver/caddy\n"
        "  pinned_sha: PIN AT RUN TIME\n"
        "  grep_can_do: false\n"
        "- name: click\n"
        "  grep_can_do: true\n"
        "  answer_key: |\n"
        "    ground_truth_set:\n"
        "      - x\n"            # a nested '- ' entry inside an opaque value must NOT spawn an item
        "      - y\n"
    )
    items = B._minimal_yaml_list(text, Path("x"))
    check("bare top-level list -> exactly two items (nested '- ' does not spawn spurious items)",
          isinstance(items, list) and len(items) == 2, str(items if isinstance(items, list) else type(items)))
    check("item0 name == caddy", items[0].get("name") == "caddy", str(items[0]))
    check("URL with ':' preserved intact",
          items[0].get("url") == "https://github.com/caddyserver/caddy", str(items[0].get("url")))
    check("placeholder pinned_sha preserved as string", items[0].get("pinned_sha") == "PIN AT RUN TIME")
    check("grep_can_do false -> bool False (not the string)", items[0].get("grep_can_do") is False)
    check("grep_can_do true -> bool True (not the string)", items[1].get("grep_can_do") is True)
    check("nested answer_key block marked opaque (present, not mis-parsed)",
          B._present(items[1].get("answer_key")) is True and isinstance(items[1].get("answer_key"), B._Opaque),
          str(items[1].get("answer_key")))

    # #15 (bug-hunt) — SIBLING keys that FOLLOW a block scalar must be parsed, not dropped. The real
    # questions.yaml writes `prompt: >-` then `family:`/`grep_can_do:` AFTER it; the old parser
    # dropped them, making validate_config report dozens of false problems under the stdlib fallback.
    sib = (
        "- id: q1\n"
        "  prompt: >-\n"
        "    a multi-line\n"
        "    prompt body\n"
        "  family: blast_radius\n"        # SIBLING after the block scalar
        "  grep_can_do: false\n"          # SIBLING after the block scalar
    )
    s_items = B._minimal_yaml_list(sib, Path("x"))
    check("sibling key after a block scalar is parsed (#15: family present)",
          s_items[0].get("family") == "blast_radius", str(s_items[0]))
    check("sibling bool after a block scalar is parsed as a real bool (#15)",
          s_items[0].get("grep_can_do") is False, str(s_items[0]))
    check("the block scalar itself stays opaque", isinstance(s_items[0].get("prompt"), B._Opaque))

    # #16/#17 — a top-level MAPPING whose values are lists: items are grouped under their key
    # (templates vs questions stay SEPARATE), and a top-level key after a list is NOT absorbed
    # into the last item of the previous list.
    grouped = (
        "schema: deepmap/v1\n"
        "templates:\n"
        "  - id: T1\n"
        "    name: tmpl\n"
        "questions:\n"
        "  - id: q1\n"
        "    repo: caddy\n"
        "  - id: q2\n"
        "    repo: click\n"
    )
    g = B._minimal_yaml_list(grouped, Path("x"))
    check("top-level mapping -> dict grouped by key (#16)", isinstance(g, dict), str(type(g)))
    check("templates list has exactly its 1 item (not merged with questions) (#16)",
          isinstance(g.get("templates"), list) and len(g["templates"]) == 1, str(g.get("templates")))
    check("questions list has exactly its 2 items (#16)",
          isinstance(g.get("questions"), list) and len(g["questions"]) == 2, str(g.get("questions")))
    check("the last template item did NOT absorb the 'questions' top-level key (#17)",
          "questions" not in g["templates"][0], str(g["templates"][0]))
    check("_as_list selects the questions list from the grouped dict",
          [q.get("id") for q in B._as_list(g, Path("x"))] == ["q1", "q2"], str(B._as_list(g, Path("x"))))


def gate_yaml_comment_handling() -> None:
    """bug-hunt #3: the stdlib fallback's comment strip must treat '#' as a comment ONLY at line
    start or after whitespace — a '#' embedded in a value (a URL fragment, a prompt) or inside a
    quoted scalar is LITERAL. The old `split('#',1)[0]` truncated all of those."""
    print("\n-- _strip_yaml_comment: embedded/quoted '#' is literal, real comment stripped --")
    B = _load_benchmark()
    check("embedded # (url fragment) is kept",
          B._strip_yaml_comment("  url: https://x/y#readme") == "  url: https://x/y#readme")
    check("a real trailing comment IS stripped",
          B._strip_yaml_comment("  name: caddy   # the server") == "  name: caddy")
    check("a # inside a quoted scalar is kept (comment after still stripped)",
          B._strip_yaml_comment('  q: "count #items"  # note') == '  q: "count #items"')
    check("a bare '#' line start is a full comment",
          B._strip_yaml_comment("   # whole line") == "")
    frag = "repos:\n  - name: x\n    url: https://h/p#readme\n"
    items = B._as_list(B._minimal_yaml_list(frag, Path("x")), Path("x"))
    check("a url#fragment value survives the stdlib parser (not truncated)",
          items and items[0].get("url") == "https://h/p#readme", str(items))


def gate_validate_config_honesty_hooks() -> None:
    print("\n-- validate_config: honesty hooks (grep_can_do bool, pins, dup ids) --")
    B = _load_benchmark()

    def repo(**kw):
        base = {"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}
        base.update(kw)
        return base

    def q(**kw):
        base = {"id": "q1", "repo": "r", "prompt": "p", "grep_can_do": False,
                "family": "f", "answer_key": {"ground_truth_set": ["x"]}}
        base.update(kw)
        return base

    check("a fully-valid config has no problems", B.validate_config([repo()], [q()]) == [])

    # The placeholder SHA is EXPECTED pre-G3 — must NOT be flagged as malformed.
    check("placeholder pinned_sha is not a problem (expected pre-G3)",
          B.validate_config([repo(pinned_sha="PIN AT RUN TIME")], [q()]) == [])

    # grep_can_do MUST be an explicit bool — it is the honesty hook (publishes its own losses).
    probs = B.validate_config([repo()], [q(grep_can_do="true")])
    check("grep_can_do as a string is flagged (the honesty hook)",
          any("grep_can_do" in p for p in probs), str(probs))
    probs = B.validate_config([repo()], [q(grep_can_do=None)])
    check("grep_can_do missing/None is flagged", any("grep_can_do" in p for p in probs), str(probs))

    # A real-but-malformed SHA is a wiring problem (the OFF arm greps it; the ON graph builds at it).
    check("malformed (non-40-hex) pinned_sha is flagged",
          any("pinned_sha" in p for p in B.validate_config([repo(pinned_sha="deadbeef")], [q()])),
          "")

    # Missing required keys.
    bad_repo = repo(); del bad_repo["license"]
    check("repo missing a required key is flagged",
          any("license" in p for p in B.validate_config([bad_repo], [q()])))
    bad_q = q(); del bad_q["prompt"]
    check("question missing a required key is flagged",
          any("prompt" in p for p in B.validate_config([repo()], [bad_q])))

    # Duplicates + dangling references.
    check("duplicate repo name is flagged",
          any("duplicate" in p.lower() for p in B.validate_config([repo(), repo()], [q()])))
    check("duplicate question id is flagged",
          any("duplicate" in p.lower() for p in B.validate_config([repo()], [q(), q()])))
    check("question referencing an unknown repo is flagged",
          any("not in repos" in p for p in B.validate_config([repo()], [q(repo="ghost")])))

    # The blind scorer needs a non-empty completeness oracle (soft honesty check).
    check("empty answer_key.ground_truth_set is flagged (no completeness oracle)",
          any("ground_truth_set" in p for p in
              B.validate_config([repo()], [q(answer_key={"ground_truth_set": []})])))


def gate_build_claude_argv_fairness() -> None:
    print("\n-- build_claude_argv: the FAIR-baseline contract (OFF = empty MCP set, not a strawman) --")
    B = _load_benchmark()
    repo_dir = Path(".")
    cfg = Path("/tmp/mcp_on.json")
    on = B.build_claude_argv(B.ARM_ON, "what does X do", repo_dir, cfg, 5.0)
    off = B.build_claude_argv(B.ARM_OFF, "what does X do", repo_dir, cfg, 5.0)

    # ON arm: loads the DeepMap servers via --mcp-config, and ONLY those — --strict-mcp-config keeps every ambient
    # user- / plugin-scoped MCP server (e.g. the plugin's 8787/8788 pair serving another project) out of the ON arm
    # (ISS-benchmark:007; strict means "only the servers --mcp-config names", so the clone's pair still loads).
    check("ON arm passes --mcp-config", "--mcp-config" in on, str(on))
    check("ON arm points --mcp-config at the config path", str(cfg) in on, str(on))
    check("ON arm IS --strict-mcp-config (only its --mcp-config servers load)", "--strict-mcp-config" in on, str(on))

    # OFF arm: --strict-mcp-config with NO --mcp-config => the empty MCP set (fair, isolated).
    check("OFF arm passes --strict-mcp-config", "--strict-mcp-config" in off, str(off))
    check("OFF arm passes NO --mcp-config (empty MCP baseline, not a strawman)",
          "--mcp-config" not in off, str(off))

    # Both arms share the identical model/output/permission/budget invocation (the contrast is
    # MCP-config only, plan §7.2) — so a win can't be an artifact of different flags.
    for label, argv in (("ON", on), ("OFF", off)):
        # STDIN delivery (load-bearing): the prompt must NOT be a positional arg — a long question passed
        # as the claude -p ARG is received mangled; run_one/_probe_call pipe it via stdin instead.
        check(f"{label}: prompt is NOT a positional arg (delivered via STDIN)", "what does X do" not in argv, str(argv))
        check(f"{label}: --model opus", "--model" in argv and "opus" in argv, str(argv))
        check(f"{label}: --output-format json",
              "--output-format" in argv and "json" in argv, str(argv))
        check(f"{label}: --permission-mode acceptEdits",
              "--permission-mode" in argv and "acceptEdits" in argv, str(argv))
        check(f"{label}: --max-budget-usd carried", "--max-budget-usd" in argv and "5.0" in argv, str(argv))
        check(f"{label}: -p/--print headless flag present", "-p" in argv or "--print" in argv, str(argv))

    # The ONLY difference between the two argvs is the --mcp-config pair — assert the shared prefix
    # (model/output/permission/budget/strict) is byte-identical so the contrast is purely which servers load.
    on_core = [a for a in on if a not in ("--mcp-config", str(cfg))]
    check("the two arms differ ONLY in the --mcp-config pair (shared core identical)",
          on_core == off and on == off + ["--mcp-config", str(cfg)], f"on={on} off={off}")


def gate_do_run_persists_on_failure() -> None:
    print("\n-- do_run: a per-run OSError does not abort/discard; spent records persist (#14) --")
    import json as _json
    import tempfile
    from types import SimpleNamespace
    B = _load_benchmark()

    repos = [{"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    questions = [{"id": "q1", "repo": "r", "prompt": "p", "grep_can_do": False,
                  "family": "f", "answer_key": {"ground_truth_set": ["x"]}}]
    # Hermetic: no claude, no network, no Neo4j — stub every external touchpoint.
    B.load_config = lambda: (repos, questions)
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    # This gate exercises the run loop's OSError-persistence (#14), NOT the fairness equalizers —
    # the ON-arm graph-SHA verify + the per-run pristine reset have their own dedicated coverage
    # (tests/benchmark_fairness_gates.py), so stub them to None here to isolate the #14 behaviour.
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.reset_pristine = lambda d, s: None

    calls = {"n": 0}

    def fake_run_one(arm, q, repo_dir, cfg, run_idx, out_dir, **kw):
        calls["n"] += 1
        if calls["n"] == 1:   # first run captures; the rest fail to launch (claude vanished)
            return {"repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx,
                    "exit_code": 0, "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}
        raise OSError("claude removed from PATH mid-matrix")

    B.run_one = fake_run_one

    saved_engine = os.environ.get(B.ENGINE_ENV)
    td = tempfile.TemporaryDirectory()
    B.RESULTS_ROOT = Path(td.name)
    try:
        os.environ[B.ENGINE_ENV] = "1"
        clone_root = Path(td.name) / "clones"
        (clone_root / "r").mkdir(parents=True)   # repo_dir must exist (owner/name layout)
        args = SimpleNamespace(repo=None, clone_root=str(clone_root), max_budget_usd=1.0, timeout_s=60)
        rc = B.do_run(args, "t14")
        results = Path(td.name) / "t14" / "results.json"
        check("do_run did not crash on a per-run OSError (returns an int)", isinstance(rc, int), str(rc))
        check("results.json persisted despite the OSError (spent records not discarded)", results.exists())
        rec = _json.loads(results.read_text(encoding="utf-8")) if results.exists() else {}
        check("the captured run survived in the persisted record",
              len(rec.get("scorecards", [])) == 1, str(len(rec.get("scorecards", []))))
        check("persisted status is captured-unscored (never fabricated 'measured')",
              rec.get("measured") is False and rec.get("captured") is True, str(rec.get("status_tag")))
    finally:
        if saved_engine is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = saved_engine
        td.cleanup()


def gate_do_run_persists_on_nonos_error() -> None:
    """#14 hardening: a per-run failure that is NOT OSError/Timeout (e.g. a stray UnicodeDecodeError —
    a ValueError subclass) must ALSO be logged-and-continued, never abort the whole matrix and discard
    already-spent records. (The encoding fix prevents the common decode case; this proves the loop is
    resilient to ANY single-run exception.)"""
    print("\n-- do_run: a per-run NON-OSError (e.g. ValueError) does not abort the matrix (#14) --")
    import json as _json
    import tempfile
    from types import SimpleNamespace
    B = _load_benchmark()
    repos = [{"name": "r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    questions = [{"id": "q1", "repo": "r", "prompt": "p", "grep_can_do": False, "family": "f",
                  "answer_key": {"ground_truth_set": ["x"]}}]
    B.load_config = lambda: (repos, questions)
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.reset_pristine = lambda d, s: None

    calls = {"n": 0}

    def fake_run_one(arm, q, repo_dir, cfg, run_idx, out_dir, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx,
                    "exit_code": 0, "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}
        raise ValueError("simulated non-OSError mid-matrix (e.g. a stray decode error)")

    B.run_one = fake_run_one
    saved_engine = os.environ.get(B.ENGINE_ENV)
    td = tempfile.TemporaryDirectory()
    B.RESULTS_ROOT = Path(td.name)
    try:
        os.environ[B.ENGINE_ENV] = "1"
        clone_root = Path(td.name) / "clones"
        (clone_root / "r").mkdir(parents=True)
        args = SimpleNamespace(repo=None, clone_root=str(clone_root), max_budget_usd=1.0, timeout_s=60)
        raised = None
        try:
            rc = B.do_run(args, "t14b")
        except Exception as exc:  # noqa: BLE001 — the whole point is it must NOT propagate
            raised = exc
            rc = None
        check("do_run did NOT propagate the per-run ValueError (matrix not aborted)", raised is None,
              f"propagated {type(raised).__name__ if raised else None}")
        results = Path(td.name) / "t14b" / "results.json"
        check("results.json still persisted (spent records not discarded)", results.exists())
        if results.exists():
            rec = _json.loads(results.read_text(encoding="utf-8"))
            check("the captured run survived despite the non-OSError",
                  len(rec.get("scorecards", [])) == 1, str(len(rec.get("scorecards", []))))
    finally:
        if saved_engine is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = saved_engine
        td.cleanup()


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark HONESTY state-machine + pin-gate gates (no Neo4j, no spend)")
    print("=" * 64)

    gate_status_tag_three_state()
    gate_build_record_honesty_flags()
    gate_aggregate_median()
    gate_errored_run_excluded_real_capture_path()
    gate_machine_spec()
    gate_minimal_yaml_fallback()
    gate_yaml_comment_handling()
    gate_do_run_persists_on_failure()
    gate_do_run_persists_on_nonos_error()
    gate_validate_config_honesty_hooks()
    gate_build_claude_argv_fairness()
    gate_assert_pinned_clean_failclosed()
    gate_assert_pinned_clean_ignores_untracked_sidecars()
    gate_render_no_fabrication()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
