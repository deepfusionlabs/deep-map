#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the benchmark RUN-LOOP fairness/reproducibility equalizers.

Three previously-unwired (or unrecorded) fairness controls the README flagged as required-before-G3:

  1. PRISTINE RESET between runs (`reset_pristine_argv` / `reset_pristine`). The agent runs with
     `--permission-mode acceptEdits`, so it CAN write to the clone — without a reset, one run's
     writes leak into the next and poison the comparison. The fix restores the clone to a pristine
     checkout at the pinned SHA before every run (`git reset --hard <sha>` + `git clean -fdx`).

  2. ON-ARM GRAPH SHA verification (`read_graph_build_sha` / `assert_graph_at_pinned_sha`). The OFF
     arm greps the clone (verified by `assert_pinned_clean`), but the ON arm queries the Neo4j graph,
     which is built out-of-band — a STALE graph (older SHA) silently makes the contrast unfair.
     The check FAILS CLOSED unless the graph's build SHA is KNOWN and equals the pinned SHA.

  3. SAMPLING PROVENANCE (`machine_spec()["sampling"]`). For reproducibility the record must state
     the sampling configuration (model alias→resolved id, temperature, seed) — honestly, including
     that `claude -p` pins neither temperature nor seed (so runs vary; medians mitigate).

Pure + hermetic: imports the harness module and exercises the command-construction + verification
functions against synthetic paths/manifests. Never spends, never invokes claude/git/Neo4j.
"""
from __future__ import annotations

import importlib.util
import json
import os
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


def _load():
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_fair", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_SHA = "a" * 40
_OTHER_SHA = "b" * 40


# --------------------------------------------------------------------------- #
# 1) Pristine reset command construction.
# --------------------------------------------------------------------------- #

def gate_reset_pristine_argv() -> None:
    print("\n-- reset_pristine_argv: git reset --hard <sha> + git clean -fdx --")
    B = _load()
    fn = getattr(B, "reset_pristine_argv", None)
    check("reset_pristine_argv exists", callable(fn))
    if not callable(fn):
        return
    cmds = fn(Path("/clones/owner__repo"), _SHA)
    check("returns exactly two git commands (reset + clean)", isinstance(cmds, list) and len(cmds) == 2,
          str(cmds))
    reset, clean = cmds[0], cmds[1]
    # command 1: hard reset to the pinned SHA (discard tracked-file edits + move HEAD to the SHA)
    check("cmd1 is `git ... reset --hard <sha>`",
          "reset" in reset and "--hard" in reset and _SHA in reset, str(reset))
    check("cmd1 targets the EXACT pinned SHA (not HEAD)", reset[-1] == _SHA, str(reset))
    check("cmd1 scopes git to the clone dir (-C <repo_dir>)",
          "-C" in reset and any("owner__repo" in str(a) for a in reset), str(reset))
    # command 2: remove untracked + ignored files/dirs (acceptEdits can create new files too)
    check("cmd2 is `git ... clean -fdx` (untracked + ignored)",
          "clean" in clean and "-fdx" in clean, str(clean))
    check("cmd2 scopes git to the clone dir", "-C" in clean, str(clean))
    # cmd2 MUST exclude .deepmap/ — that gitignored dir holds DeepMap's own per-repo state, incl. the
    # graph_build.json SHA manifest assert_graph_at_pinned_sha reads. A bare `clean -fdx` would delete
    # the benchmark's own provenance (and break a second --run). It is NOT agent output, so preserve it.
    check("cmd2 EXCLUDES .deepmap (keeps the graph-build manifest / DeepMap state)",
          any(".deepmap" in str(a) for a in clean) and
          any(a in ("-e", "--exclude", "--exclude=.deepmap") or str(a).startswith("--exclude") for a in clean),
          str(clean))
    # a placeholder/opaque SHA must NOT silently reset to a bogus ref — it falls back to HEAD
    # (the matrix's assert_pinned_clean fails closed earlier, so a real run never reaches here
    # with a placeholder; this just proves the constructor never emits a malformed ref).
    cmds2 = fn(Path("/c/x"), "PIN AT RUN TIME")
    check("non-SHA pin -> safe HEAD target, never a bogus ref",
          cmds2[0][-1] == "HEAD", str(cmds2[0]))


def gate_reset_pristine_execution_honesty() -> None:
    print("\n-- reset_pristine: returns a reason (fail-closed) when git is missing/fails --")
    B = _load()
    fn = getattr(B, "reset_pristine", None)
    check("reset_pristine exists", callable(fn))
    if not callable(fn):
        return
    # Point at a dir that is NOT a git repo so `git reset` fails -> a non-None reason (caller FAILS
    # CLOSED, never spends on a dirty tree). This actually invokes git but spends no tokens and
    # mutates nothing (a non-repo temp dir).
    with tempfile.TemporaryDirectory() as td:
        reason = fn(Path(td), _SHA)
        # Either git is on PATH and fails on a non-repo (reason set), or git is absent (reason set).
        # The ONE thing that must never happen: a silent None "success" on a tree we did not reset.
        check("non-repo dir -> a fail-closed reason (never a silent success)",
              reason is not None and isinstance(reason, str) and reason, repr(reason))


# --------------------------------------------------------------------------- #
# 2) ON-arm graph SHA verification.
# --------------------------------------------------------------------------- #

def _write_manifest(repo_dir: Path, sha) -> None:
    d = repo_dir / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"built_at_sha": sha} if sha is not None else {}
    (d / "graph_build.json").write_text(json.dumps(payload), encoding="utf-8")


def gate_read_graph_build_sha() -> None:
    print("\n-- read_graph_build_sha: manifest sidecar, then DEEPMAP_GRAPH_SHA, then None --")
    B = _load()
    fn = getattr(B, "read_graph_build_sha", None)
    check("read_graph_build_sha exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        # no manifest, no env -> (None, unavailable)
        saved = os.environ.pop("DEEPMAP_GRAPH_SHA", None)
        try:
            sha, src = fn(rd)
            check("no manifest + no env -> (None, source)", sha is None and isinstance(src, str), f"{sha},{src}")
            # env fallback
            os.environ["DEEPMAP_GRAPH_SHA"] = _SHA.upper()   # case-insensitive
            sha, src = fn(rd)
            check("env DEEPMAP_GRAPH_SHA used (lowercased)", sha == _SHA and "env" in src.lower(), f"{sha},{src}")
            # manifest takes precedence over env
            _write_manifest(rd, _OTHER_SHA)
            sha, src = fn(rd)
            check("manifest sidecar takes precedence over env", sha == _OTHER_SHA and "manifest" in src.lower(),
                  f"{sha},{src}")
        finally:
            os.environ.pop("DEEPMAP_GRAPH_SHA", None)
            if saved is not None:
                os.environ["DEEPMAP_GRAPH_SHA"] = saved


def gate_assert_graph_at_pinned_sha() -> None:
    print("\n-- assert_graph_at_pinned_sha: FAIL CLOSED on stale/unknown graph SHA --")
    B = _load()
    fn = getattr(B, "assert_graph_at_pinned_sha", None)
    check("assert_graph_at_pinned_sha exists", callable(fn))
    if not callable(fn):
        return
    saved = os.environ.pop("DEEPMAP_GRAPH_SHA", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            # unknown graph SHA (no manifest, no env) -> FAIL CLOSED with a reason (never a free pass)
            r = fn(rd, _SHA)
            check("unknown graph SHA -> fail-closed reason (not a free pass)",
                  r is not None and "fail closed" in r.lower(), repr(r))
            # matching graph SHA -> None (OK to proceed)
            _write_manifest(rd, _SHA)
            r = fn(rd, _SHA)
            check("graph SHA == pinned -> None (proceed)", r is None, repr(r))
            # stale graph SHA -> FAIL CLOSED, and the reason names both SHAs
            _write_manifest(rd, _OTHER_SHA)
            r = fn(rd, _SHA)
            check("stale graph SHA -> fail-closed reason naming the mismatch",
                  r is not None and _OTHER_SHA in r and _SHA in r, repr(r))
            # a non-40-hex pin is itself disqualifying
            _write_manifest(rd, _SHA)
            r = fn(rd, "PIN AT RUN TIME")
            check("non-SHA pin -> fail-closed (curator must pin a real SHA)",
                  r is not None and "fail closed" in r.lower(), repr(r))
    finally:
        if saved is not None:
            os.environ["DEEPMAP_GRAPH_SHA"] = saved


# --------------------------------------------------------------------------- #
# 3) Sampling provenance.
# --------------------------------------------------------------------------- #

def gate_sampling_provenance() -> None:
    print("\n-- machine_spec()['sampling']: honest reproducibility provenance --")
    B = _load()
    spec = B.machine_spec()
    samp = spec.get("sampling")
    check("machine_spec carries a 'sampling' block", isinstance(samp, dict), str(type(samp)))
    if not isinstance(samp, dict):
        return
    check("records the model alias passed to claude -p ('opus')", samp.get("model_alias") == "opus",
          str(samp.get("model_alias")))
    check("resolved_model == the G3-required id (claude-opus-4-8)", samp.get("resolved_model") == B.MODEL,
          str(samp.get("resolved_model")))
    # HONESTY: claude -p exposes neither --temperature nor --seed in the verified flag set, so the
    # provenance must NOT fabricate a pinned seed/temperature — they are recorded as not-pinned.
    check("seed is recorded as NOT pinned (None) — never a fabricated seed", samp.get("seed") is None,
          str(samp.get("seed")))
    check("temperature is recorded as NOT pinned (None)", samp.get("temperature") is None,
          str(samp.get("temperature")))
    check("a determinism note states runs are not deterministic (honesty)",
          isinstance(samp.get("determinism"), str) and "not" in samp["determinism"].lower(),
          str(samp.get("determinism")))
    # Reproducibility provenance: the DeepMap repo's own HEAD SHA, so a published result traces to the
    # exact harness/product commit that produced it. Best-effort: a 40-hex SHA, or None outside git
    # (never a crash, never a fabricated value).
    dc = spec.get("deepmap_commit", "MISSING")
    check("machine_spec records the DeepMap repo HEAD ('deepmap_commit')", dc != "MISSING",
          "key absent")
    check("deepmap_commit is a 40-hex SHA or None (best-effort, never fabricated)",
          dc is None or (isinstance(dc, str) and len(dc) == 40 and all(c in "0123456789abcdef" for c in dc)),
          str(dc))


def gate_background_refresh_off() -> None:
    print("\n-- M12: a measured graph never changes mid-run (DeepMap's background refresh off in arms + clone "
          "builds) --")
    B = _load()
    env = B.claude_child_env({"DEEPMAP_AUTO_SYNC": "1", "DEEPMAP_FRESHNESS_NOTE": "1"})
    check("claude_child_env() (both arms, the probe, the judge) sets DEEPMAP_AUTO_SYNC=0 over an operator's 1",
          env.get("DEEPMAP_AUTO_SYNC") == "0", str(env.get("DEEPMAP_AUTO_SYNC")))
    check("claude_child_env() sets DEEPMAP_FRESHNESS_NOTE=0 (no background-refresh notes inside a measured session)",
          env.get("DEEPMAP_FRESHNESS_NOTE") == "0", str(env.get("DEEPMAP_FRESHNESS_NOTE")))
    spec = importlib.util.spec_from_file_location("dm_clone_graph_fair", BENCH / "clone_graph.py")
    CG = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(CG)
    check("clone_graph.child_env() (every clone build) forces DEEPMAP_AUTO_SYNC=0, an operator's 1 included",
          CG.child_env({"DEEPMAP_AUTO_SYNC": "1"}).get("DEEPMAP_AUTO_SYNC") == "0"
          and CG.child_env({}).get("DEEPMAP_AUTO_SYNC") == "0")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark run-loop fairness/reproducibility gates (no spend)")
    print("=" * 64)

    gate_reset_pristine_argv()
    gate_reset_pristine_execution_honesty()
    gate_read_graph_build_sha()
    gate_assert_graph_at_pinned_sha()
    gate_sampling_provenance()
    gate_background_refresh_off()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
