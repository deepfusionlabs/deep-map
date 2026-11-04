#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto-sync — the coordinator's wiring (spec Appendix C1, C3-C7): the CLI, provision and run_all ends of DeepMap's
background refresh.

Locks:
  A. C4  `deepmap sync <root> --unattended=session-start [--session <id>]` parses (only that one trigger); the two
         options are hidden from `sync -h` (DeepMap's own hook starts them, not a user); C5's exit-code paragraph is
         in the epilog; the hooks' text proof of the contract (deepmap_hook_state.cli_takes_unattended) holds.
  B. C3  cmd_sync hands an unattended run to auto_sync.run(target, trigger=, session=) BEFORE sync is loaded and
         returns its code unchanged; --stale-cleanup / --switch-project / --force-prune with --unattended -> exit 2,
         nothing loaded; a plain sync never loads auto_sync. End to end (a real subprocess, auto-sync off, a temp
         DEEPMAP_HOME, a closed NEO4J_URI port): the heal hook's argv exits 4 ("disabled"), never 2.
  C. C6  `status` prints the auto-sync line BEFORE the Neo4j census (shows with the instance down), "[WARN] " for a
         WARN level, nothing when the module fails; `doctor` prints it as one informational line that never changes
         the verdict or the exit code.
  C3. design 5.5  a graph built before a graph revision that covers it (sync_status.graph_rev_note): `status` prints
         "graph revision: <note>; ... /deep-map:refresh rebuilds it now" after the auto-sync line and before the
         Neo4j census, `doctor` one WARN line BEFORE its coverage table, whatever auto-sync's state; nothing for a graph
         this code built; never the verdict or the exit code; a sync_status that cannot load -> no line.
  D. C6/H3  _staleness_line judges "edited after the build" against the build's source capture
         (edits_seen_through_utc) when recorded — an edit made while the sync ran is not in the graph — and against
         built_at_utc for a manifest that predates it (unchanged).
  E. C7  provision._sync_running_for reads .deepmap/sync_running.json (sync_status.read_running: live only for a
         matching pid + start stamp) without creating any file; hibernate_idle keeps an idle running instance whose
         project has a live sync holder, and stops it once there is none.
  F. C1  tests/run_all.py hands every gate DEEPMAP_AUTO_SYNC=0, an operator's 1 included (the auto-sync gates set
         it per case themselves), so no gate that runs a SessionStart hook for real starts a background refresh.

Hermetic: temp DEEPMAP_HOME / projects / git repos, the Neo4j driver and provision probes stubbed (no socket, no
Neo4j, no network, no spend); never ~/.deepmap. Usage: python tests/auto_sync_cli_wiring_gates.py [--tree <repo>]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

_PASS = 0
_FAIL = 0
_TEMPS: list = []

# env that changes what the CLI / hooks / auto_sync resolve — never inherited from the developer's shell
_SCRUB_PREFIXES = ("DEEPMAP_", "CLAUDE_")
_SCRUB = ("NEO4J_URI", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail != '' else ''}")


def _tmp(prefix: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    _TEMPS.append(d)
    return d


def _rm_tree(d: Path) -> None:
    """Remove a temp tree this gate created (exact path), clearing the read-only bit git sets on its objects."""
    import stat

    def _retry(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(d), onexc=_retry)
    else:
        shutil.rmtree(str(d), onerror=_retry)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cli():
    return _load_module(REPO / "build" / "deepmap_cli.py", f"dm_cli_autosync_{time.time_ns()}")


@contextlib.contextmanager
def _chdir(d: Path):
    prev = os.getcwd()
    os.chdir(str(d))
    try:
        yield
    finally:
        os.chdir(prev)


class _Recorder:
    """C._load replacement: records every module name asked for; serves the stubs given, else the real module."""

    def __init__(self, C, stubs: dict):
        self.C, self.stubs, self.names, self.orig = C, stubs, [], C._load

    def __enter__(self):
        def _load(name):
            self.names.append(name)
            if name in self.stubs:
                got = self.stubs[name]
                if isinstance(got, BaseException):
                    raise got
                return got
            return self.orig(name)
        self.C._load = _load
        return self

    def __exit__(self, *a):
        self.C._load = self.orig
        return False


def _auto_sync_stub(rc=4, line=("OK", "on — never run (test stub)"), line_raises=None):
    calls = []
    ns = types.SimpleNamespace(calls=calls)

    def run(target, trigger=None, session=None):
        calls.append({"target": target, "trigger": trigger, "session": session})
        return rc

    def status_line(root, env=None):
        if line_raises is not None:
            raise line_raises
        return line

    ns.run, ns.status_line = run, status_line
    return ns


# =================================================================================================== A — parser (C4/C5)
def gate_a_parser() -> None:
    print("\n-- A. `sync --unattended=session-start [--session <id>]`: parsed, hidden, its exit codes documented --")
    C = _cli()
    p = C.build_parser()
    a = p.parse_args(["sync", "proj", "--unattended=session-start", "--session", "SID-1"])
    check("`sync <root> --unattended=session-start --session SID-1` parses to cmd_sync with both values",
          a.fn is C.cmd_sync and getattr(a, "unattended", None) == "session-start" and getattr(a, "session", None)
          == "SID-1", vars(a))
    a = p.parse_args(["sync", "proj"])
    check("a plain `sync <root>`: unattended None, session None (a user's sync is unchanged)",
          getattr(a, "unattended", "missing") is None and getattr(a, "session", "missing") is None, vars(a))
    err = io.StringIO()
    rc = None
    try:
        with contextlib.redirect_stderr(err):
            p.parse_args(["sync", "proj", "--unattended=on-save"])
    except SystemExit as e:
        rc = e.code
    check("only the session-start trigger exists: `--unattended=on-save` is an argparse refusal (exit 2)",
          rc == 2 and "invalid choice" in err.getvalue(), (rc, err.getvalue()[-200:]))
    sp = p._subparsers._group_actions[0].choices["sync"]
    helptext = sp.format_help()
    head = helptext.split("exit codes:", 1)[0]
    check("`sync -h` does not list --unattended / --session as options (DeepMap's hook starts them, not a user)",
          "--unattended" not in head and "--session" not in head, head[-400:])
    epi = sp.epilog or ""
    check("the `sync -h` epilog documents the unattended exit codes (C5: 0 current / 1 failed / 3 database / 4 not "
          "refreshed; .deepmap/autosync.json + autosync.log)",
          "--unattended=session-start (DeepMap's own background refresh)" in epi and "4 = not refreshed" in epi
          and ".deepmap/autosync.json and .deepmap/autosync.log" in epi and epi.index("--unattended")
          > epi.index("4  NOT PUSHED") and "env: DEEPMAP_GRAPHIFY_TIMEOUT" in epi, epi[-700:])
    src = (REPO / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
    line = next((ln for ln in src.splitlines() if ln.startswith('    p = sub.add_parser("sync");')), "")
    check("the scanned one-line sync subparser is untouched (installer_gates / switch_guard_gates scan it)",
          "--switch-project" in line and "--unattended" not in line and "--session" not in line, line[:160])
    HS = _load_module(REPO / "hooks" / "deepmap_hook_state.py", f"dm_hook_state_autosync_{time.time_ns()}")
    check("the hooks' text proof agrees: deepmap_hook_state.cli_takes_unattended(this CLI) is True",
          HS.cli_takes_unattended(str(REPO / "build" / "deepmap_cli.py")) is True)


# =================================================================================================== B — dispatch (C3)
def gate_b_dispatch(work: Path) -> None:
    print("\n-- B. cmd_sync: an unattended run goes to auto_sync.run before sync loads; the code passes through --")
    C = _cli()
    p = C.build_parser()
    for rc in (0, 1, 3, 4):
        stub = _auto_sync_stub(rc=rc)
        args = p.parse_args(["sync", str(work), "--unattended=session-start", "--session", "S-7"])
        with _Recorder(C, {"auto_sync": stub, "sync": RuntimeError("sync must not load")}) as rec:
            got = C.cmd_sync(args)
        check(f"rc {rc}: auto_sync.run(target, trigger='session-start', session='S-7') once, its code returned, "
              "sync never loaded", got == rc and rec.names == ["auto_sync"]
              and stub.calls == [{"target": str(work), "trigger": "session-start", "session": "S-7"}],
              (got, rec.names, stub.calls))
    stub = _auto_sync_stub(rc=0)
    args = p.parse_args(["sync", str(work), "--unattended=session-start"])
    with _Recorder(C, {"auto_sync": stub, "sync": RuntimeError("sync must not load")}) as rec:
        got = C.cmd_sync(args)
    check("no --session: auto_sync.run gets session=None", got == 0 and stub.calls and stub.calls[0]["session"] is None,
          stub.calls)
    for flag in ("--stale-cleanup", "--switch-project", "--force-prune"):
        stub = _auto_sync_stub(rc=0)
        args = p.parse_args(["sync", str(work), "--unattended=session-start", flag])
        err = io.StringIO()
        with _Recorder(C, {"auto_sync": stub, "sync": RuntimeError("sync must not load")}) as rec, \
                contextlib.redirect_stderr(err):
            got = C.cmd_sync(args)
        check(f"--unattended with {flag}: exit 2, nothing loaded, the refusal names the flags",
              got == 2 and rec.names == [] and not stub.calls and "--unattended never takes" in err.getvalue(),
              (got, rec.names, err.getvalue()))
    fake_sync = types.SimpleNamespace(sync=lambda *a, **k: True, last_verdict="READY", last_not_pushed=None)
    stub = _auto_sync_stub()
    args = p.parse_args(["sync", str(work)])
    with _Recorder(C, {"auto_sync": stub, "sync": fake_sync}) as rec, contextlib.redirect_stderr(io.StringIO()):
        got = C.cmd_sync(args)
    check("a plain sync loads sync only (never auto_sync) and exits 0 on READY", got == 0 and rec.names == ["sync"],
          (got, rec.names))


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def gate_b_end_to_end(work: Path, home: Path) -> None:
    print("\n-- B2. end to end: the heal hook's argv against the real CLI (auto-sync off) --")
    root = work / "e2e"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUB_PREFIXES) and k not in _SCRUB}
    env.update(DEEPMAP_HOME=str(home), DEEPMAP_AUTO_SYNC="0", DEEPMAP_MANAGED_VENV="0", DEEPMAP_EXTRACTOR="ast",
               NEO4J_URI=f"bolt://127.0.0.1:{_closed_port()}", NEO4J_PASSWORD="dmw6-unused", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)
    cli = str(REPO / "build" / "deepmap_cli.py")

    def run(*extra):
        p = subprocess.run([PY, cli, "sync", str(root), *extra], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, cwd=str(work), env=env)
        return p.returncode, (p.stdout + p.stderr)

    rc, out = run("--unattended=session-start", "--session", "W6-E2E")
    check("`sync <root> --unattended=session-start --session <id>` with auto-sync off: exit 4 (disabled), never an "
          "argparse refusal", rc == 4 and "unrecognized arguments" not in out and "invalid choice" not in out,
          (rc, out[-500:]))
    rc, out = run("--unattended=session-start", "--stale-cleanup")
    check("... with --stale-cleanup: exit 2 and the refusal", rc == 2 and "--unattended never takes" in out,
          (rc, out[-300:]))
    rc, out = run("--unattended=bogus")
    check("... --unattended=bogus: exit 2 (argparse: invalid choice)", rc == 2 and "invalid choice" in out,
          (rc, out[-300:]))
    check("the e2e runs wrote nothing under the project's .deepmap but DeepMap's own records (no graph_build.json)",
          not (root / ".deepmap" / "graph_build.json").exists())


# ============================================================================================ C — status / doctor (C6)
def gate_c_status(work: Path) -> None:
    print("\n-- C. `status`: the auto-sync line shows before the Neo4j census, even with the instance down --")
    proj = work / "status"
    proj.mkdir()

    class _Down(Exception):
        pass

    def down(*a, **k):
        raise _Down("instance down (test)")

    for line, want in ((("OK", "on — never run (test stub)"), "auto-sync: on — never run (test stub)"),
                       (("WARN", "paused — 3 failed (test stub)"), "auto-sync: [WARN] paused — 3 failed (test stub)")):
        C = _cli()
        C._read_retry = down
        out = io.StringIO()
        raised = None
        with _chdir(proj), _Recorder(C, {"auto_sync": _auto_sync_stub(line=line)}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                C.cmd_status(argparse.Namespace())
            except _Down as e:
                raised = e
        check(f"status_line {line[0]}: `{want}` printed, then the census (down) raises as before",
              want in out.getvalue().splitlines() and raised is not None, out.getvalue()[-300:])
    C = _cli()
    C._read_retry = down
    out = io.StringIO()
    with _chdir(proj), _Recorder(C, {"auto_sync": RuntimeError("no auto_sync")}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            C.cmd_status(argparse.Namespace())
        except _Down:
            pass
    check("an auto_sync module that fails to load prints no auto-sync line and status goes on to the census",
          "auto-sync" not in out.getvalue(), out.getvalue()[-300:])
    C = _cli()
    C._read_retry = down
    out = io.StringIO()
    saved = os.environ.get("DEEPMAP_AUTO_SYNC")
    os.environ["DEEPMAP_AUTO_SYNC"] = "0"
    try:
        with _chdir(proj), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                C.cmd_status(argparse.Namespace())
            except _Down:
                pass
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_AUTO_SYNC", None)
        else:
            os.environ["DEEPMAP_AUTO_SYNC"] = saved
    check("the REAL build/auto_sync.py status_line through the CLI: `auto-sync: off (DEEPMAP_AUTO_SYNC=0)`",
          "auto-sync: off (DEEPMAP_AUTO_SYNC=0)" in out.getvalue().splitlines(), out.getvalue()[-300:])


def gate_c_doctor(work: Path) -> None:
    print("\n-- C2. `doctor`: one informational auto-sync line; never the verdict or the exit code --")
    H = _load_module(REPO / "tests" / "cli_project_honesty_gates.py", f"dm_w6_honesty_{time.time_ns()}")
    proj = work / "doctor"
    proj.mkdir()
    H._write_manifest(proj, verdict="READY", extractor="ast")
    runs = {}
    cases = (("ok", {"auto_sync": _auto_sync_stub(line=("OK", "on — never run (test stub)"))}),
             ("warn", {"auto_sync": _auto_sync_stub(line=("WARN", "last run failed: x (test stub)"))}),
             ("raises", {"auto_sync": _auto_sync_stub(line_raises=RuntimeError("boom"))}),
             ("unloadable", {"auto_sync": RuntimeError("no module")}))
    for name, stubs in cases:
        C = _cli()
        with _Recorder(C, stubs):
            rc, out = H._run_doctor(C, driver=H._OkDriver(), provision=H._fake_provision({8787, 8788}, []), cwd=proj)
        runs[name] = (rc, out)
    want = {"ok": "[OK  ] auto-sync — on — never run (test stub)",
            "warn": "[WARN] auto-sync — last run failed: x (test stub)",
            "raises": "[WARN] auto-sync — could not probe (RuntimeError)",
            "unloadable": "[WARN] auto-sync — could not probe (RuntimeError)"}
    for name, w in want.items():
        rc, out = runs[name]
        check(f"doctor ({name}): `{w}`", any(ln.strip() == w for ln in out.splitlines()),
              [ln for ln in out.splitlines() if "auto-sync" in ln] or out[-300:])
    verdicts = {n: (r[0], H._verdict_of(r[1])) for n, r in runs.items()}
    check("the same verdict and exit code whatever the auto-sync line says (informational only)",
          len(set(verdicts.values())) == 1 and verdicts["ok"][1], verdicts)
    rc, out = runs["warn"]
    check("a WARN auto-sync line is not a degraded reason (not listed under the verdict)",
          "last run failed: x (test stub)" not in out.split("[deepmap] verdict:", 1)[-1], out[-400:])


# ========================================================================== C3: the graph-revision line (design 5.5)
_DUE_COVERAGE = {"languages": {"C#": {"files": 20, "nodes": 900}}, "dirs": {},
                 "parser_missing": {".sql": {"files": 12, "package": "tree-sitter-sql"}}}   # what 0.6.3 wrote


def _rev_manifest(root: Path, rev=None) -> None:
    """A 0.6.3-shaped manifest (SQL named in parser_missing, READY); `rev` recorded as build_recipe.graph_rev."""
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    data = {"nodes": 7, "calls_edges": 3, "embedded": 7, "verdict": "READY", "reasons": [], "extractor": "graphify",
            "coverage": _DUE_COVERAGE, "build_recipe": {"schema": 1, "env": {}}}
    if rev is not None:
        data["build_recipe"]["graph_rev"] = rev
    (d / "graph_build.json").write_text(json.dumps(data), encoding="utf-8")


def gate_c3_graph_rev(work: Path) -> None:
    print("\n-- C3. status / doctor name a graph built before a graph revision, before the coverage table --")
    sys.path.insert(0, str(REPO / "build"))
    ST = _load_module(REPO / "build" / "sync_status.py", f"dm_w6_st_{time.time_ns()}")
    rev = getattr(ST, "GRAPH_REV", None)
    check("(sync_status.GRAPH_REV is 1: revision 1 ships, so a 0.6.3 SQL graph is due)", rev == 1, rev)
    note = ST.graph_rev_note({"coverage": _DUE_COVERAGE, "build_recipe": {"schema": 1}}) or ""
    want = f"graph revision: {note}; the coverage below is that build's, and /deep-map:refresh rebuilds it now"

    class _Down(Exception):
        pass

    def down(*a, **k):
        raise _Down("instance down (test)")

    def status_out(proj: Path, **env) -> str:
        C = _cli()
        C._read_retry = down
        out = io.StringIO()
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            stub = _Recorder(C, {"auto_sync": _auto_sync_stub()}) if not env else contextlib.nullcontext()
            with _chdir(proj), stub, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                try:
                    C.cmd_status(argparse.Namespace())
                except _Down:
                    pass
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return out.getvalue()

    due, fresh = work / "rev_due", work / "rev_fresh"
    _rev_manifest(due)
    _rev_manifest(fresh, rev=rev)
    stub_line = "auto-sync: " + _auto_sync_stub().status_line(None)[1]
    lines = status_out(due).splitlines()
    check("status, due: the graph-revision line names both revisions and /deep-map:refresh",
          bool(note) and want in lines and "graph revision 0, this code 1" in want, lines[-6:])
    check("...printed after the auto-sync line and before the Neo4j census (shows with the instance down)",
          want in lines and stub_line in lines and lines.index(stub_line) < lines.index(want), lines[-6:])
    lines = status_out(fresh).splitlines()
    check("status, a graph this code built (graph_rev 1): no graph-revision line",
          not any(ln.startswith("graph revision") for ln in lines), lines[-6:])
    lines = status_out(due, DEEPMAP_AUTO_SYNC="0").splitlines()
    check("status, due with auto-sync off: the line still prints (whatever auto-sync's state)", want in lines,
          lines[-6:])
    check("...beside the REAL auto_sync status_line, which names it too",
          any(ln.startswith("auto-sync: off (DEEPMAP_AUTO_SYNC=0)") and "graph revision 0" in ln for ln in lines),
          lines[-6:])

    H = _load_module(REPO / "tests" / "cli_project_honesty_gates.py", f"dm_w6_honesty_rev_{time.time_ns()}")
    runs = {}
    for name, proj in (("due", due), ("fresh", fresh)):
        C = _cli()
        with _Recorder(C, {"auto_sync": _auto_sync_stub()}):
            runs[name] = H._run_doctor(C, driver=H._OkDriver(), provision=H._fake_provision({8787, 8788}, []),
                                       cwd=proj)
    rc, out = runs["due"]
    lines = [ln.strip() for ln in out.splitlines()]
    wd = f"[WARN] graph revision {chr(0x2014)} {want[len('graph revision: '):]}"
    cov = next((i for i, ln in enumerate(lines) if "coverage (last sync)" in ln), -1)
    check("doctor, due: '[WARN] graph revision <dash> ...' (the same words)", wd in lines,
          [ln for ln in lines if "graph revision" in ln] or out[-400:])
    check("...printed BEFORE the coverage table (a stale 'LOW COVERAGE ... /deep-map:doctor' never loops back)",
          wd in lines and 0 <= lines.index(wd) < cov, (lines.index(wd) if wd in lines else None, cov))
    rc2, out2 = runs["fresh"]
    check("doctor, a graph this code built: no graph-revision line", "[WARN] graph revision" not in out2,
          [ln for ln in out2.splitlines() if "graph revision" in ln])
    check("doctor: the same verdict and exit code due or not (informational only)",
          (rc, H._verdict_of(out)) == (rc2, H._verdict_of(out2)) and H._verdict_of(out), ((rc, H._verdict_of(out)),
                                                                                          (rc2, H._verdict_of(out2))))
    C = _cli()
    with _Recorder(C, {"sync_status": RuntimeError("no sync_status")}):
        line = C._graph_rev_line(due) if hasattr(C, "_graph_rev_line") else "missing"
    check("a sync_status that cannot load -> no line, no exception", line is None, line)


# ============================================================================================= D — _staleness_line (H3)
def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _git(root: Path, *args) -> str:
    """git in a temp repo this gate made: no system config, no hooks, no signing, no inherited GIT_DIR."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e",
               GIT_CONFIG_NOSYSTEM="1")
    p = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull,
                        *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       env=env)
    return (p.stdout or "").strip()


def _manifest(root: Path, **fields) -> None:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph_build.json").write_text(json.dumps(fields), encoding="utf-8")


def gate_d_staleness(work: Path) -> None:
    print("\n-- D. freshness (H3): an edit after the build's SOURCE CAPTURE is not in the graph --")
    if not shutil.which("git"):
        check("(git on PATH for the freshness checks)", True, "skipped — git absent")
        return
    root = work / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c1")
    head = _git(root, "rev-parse", "HEAD").lower()
    (root / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")   # an uncommitted edit, mtime m
    m = os.stat(root / "a.py").st_mtime
    C = _cli()
    clean = dict(built_at_sha=head, dirty=False, built_at_dirty=False)
    _manifest(root, **clean, built_at_utc=_utc(m + 120), edits_seen_through_utc=_utc(m - 120))
    ln = C._staleness_line(root) or ""
    check("clean build: the edit came after the source capture but before the manifest was written -> STALE, "
          "naming the file", "STALE" in ln and "a.py" in ln, ln)
    _manifest(root, **clean, built_at_utc=_utc(m + 120), edits_seen_through_utc=_utc(m + 60))
    ln = C._staleness_line(root) or ""
    check("clean build: the edit came before the source capture -> fresh", "fresh" in ln and "STALE" not in ln, ln)
    _manifest(root, **clean, built_at_utc=_utc(m + 120))
    ln = C._staleness_line(root) or ""
    check("a manifest from before auto-sync (no edits_seen_through_utc): judged against built_at_utc as before",
          "fresh" in ln and "STALE" not in ln, ln)
    _manifest(root, **clean, built_at_utc=_utc(m - 120))
    ln = C._staleness_line(root) or ""
    check("... and an edit after its built_at_utc is still STALE", "STALE" in ln and "a.py" in ln, ln)
    dirty = dict(built_at_sha=head, dirty=True, built_at_dirty=True, dirty_paths_count=1, dirty_paths_sample=["a.py"])
    _manifest(root, **dirty, built_at_utc=_utc(m + 120), edits_seen_through_utc=_utc(m - 120))
    ln = C._staleness_line(root) or ""
    check("dirty build: its sampled file changed after the source capture -> STALE", "STALE" in ln and "a.py" in ln,
          ln)
    _manifest(root, **dirty, built_at_utc=_utc(m + 120), edits_seen_through_utc=_utc(m + 60))
    ln = C._staleness_line(root) or ""
    check("dirty build: the sample predates the capture -> 'built from uncommitted changes', not STALE",
          "uncommitted" in ln and "STALE" not in ln, ln)
    # the graph holds the UNCOMMITTED a.py; the user then reverted it to HEAD after the source capture: git status is
    # clean, so only the sample's own mtime (against the capture time) can tell the graph no longer matches
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    r = time.time()
    os.utime(root / "a.py", (r, r))
    clean_now = _git(root, "status", "--porcelain", "--", "a.py") == ""
    _manifest(root, **dirty, built_at_utc=_utc(r + 120), edits_seen_through_utc=_utc(r - 120))
    ln = C._staleness_line(root) or ""
    check("dirty build: its sampled file was reverted to HEAD after the source capture (git status clean) -> STALE, "
          "naming the file", clean_now and "STALE" in ln and "a.py" in ln, (clean_now, ln))
    # One rule with the hooks: a docs-only commit after the build, PROVED current by the background refresh
    # (deepmap_hook_state._fresh_proof), reads "current at <HEAD>" — never "may be STALE" — while the hooks say the same.
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "docs only")
    head2 = _git(root, "rev-parse", "HEAD").lower()
    _manifest(root, built_at_sha=head, dirty=False, built_at_dirty=False, source_fingerprint="fp-1",
              built_at_utc=_utc(time.time() - 60), edits_seen_through_utc=_utc(time.time() - 60))
    marker = root / ".deepmap" / "graph_build.json"
    old = time.time() - 30
    os.utime(marker, (old, old))
    proof = {"outcome": "fresh", "fresh_at_head": head2, "fingerprint": "fp-1", "finished_epoch": time.time()}
    (root / ".deepmap" / "autosync.json").write_text(json.dumps({"last_run": proof}), encoding="utf-8")
    saved = os.environ.get("DEEPMAP_AUTO_SYNC")
    try:
        os.environ["DEEPMAP_AUTO_SYNC"] = "1"
        ln = C._staleness_line(root) or ""
        check("a docs-only commit PROVED current by the background refresh -> 'current at <HEAD>', not STALE (the "
              "hooks' _fresh_proof rule)", ln.startswith(f"current at {head2[:7]}") and "STALE" not in ln, ln)
        bad = dict(proof, fingerprint="fp-other")
        (root / ".deepmap" / "autosync.json").write_text(json.dumps({"last_run": bad}), encoding="utf-8")
        ln = C._staleness_line(root) or ""
        check("... a proof for another source fingerprint is no proof -> 'may be STALE' as before", "STALE" in ln, ln)
        (root / ".deepmap" / "autosync.json").write_text(json.dumps({"last_run": proof}), encoding="utf-8")
        os.environ["DEEPMAP_AUTO_SYNC"] = "0"
        ln = C._staleness_line(root) or ""
        check("... with auto-sync off the proof is not honoured (the hooks' rule too) -> 'may be STALE'",
              "STALE" in ln, ln)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_AUTO_SYNC", None)
        else:
            os.environ["DEEPMAP_AUTO_SYNC"] = saved


# ========================================================================================= E — provision hibernate (C7)
def gate_e_hibernate() -> None:
    print("\n-- E. hibernate_idle never stops an instance a live sync is writing (C7: read the holder record) --")
    sys.path.insert(0, str(REPO / "build"))
    import port_registry as PR  # noqa: E402
    import provision as P       # noqa: E402
    import sync_status as ST    # noqa: E402
    fn = getattr(P, "_sync_running_for", None)
    check("provision._sync_running_for exists", callable(fn))
    if not callable(fn):
        fn = lambda root: False   # noqa: E731 — the integration checks below still run (RED, not a crash)
    G = _load_module(REPO / "tests" / "per_project_neo4j_gates.py", f"dm_w6_pp_{time.time_ns()}")
    check("no root / empty root -> False", fn(None) is False and fn("") is False)
    with G._EnvHome() as (home, work):
        a = G._mk_project(work, "a")
        b = G._mk_project(work, "b")
        check("no .deepmap/sync_running.json -> False", fn(str(b)) is False)
        ident = ST.process_identity()
        live_ok = ident.get("pid_created") is not None
        ST.write_running(b, {"origin": "unattended", "phase": "write"})
        before = sorted(x.name for x in (b / ".deepmap").iterdir())
        got = fn(str(b))
        after = sorted(x.name for x in (b / ".deepmap").iterdir())
        if live_ok:
            check("a holder record naming a live process (pid + start stamp) -> True", got is True, got)
        else:
            check("(this platform reads no start stamp: a holder is never proven live -> False)", got is False, got)
        check("read-only: no lock or other file created by the probe", before == after, (before, after))
        dead = subprocess.Popen([PY, "-c", "pass"])
        dead.wait(timeout=60)
        rec = json.loads(ST.running_path(b).read_text(encoding="utf-8"))
        rec.update(pid=dead.pid, pid_created=12345.0)
        ST.running_path(b).write_text(json.dumps(rec), encoding="utf-8")
        check("a record naming an exited process -> False", fn(str(b)) is False)
        rec.update(pid=os.getpid(), pid_created=None)
        ST.running_path(b).write_text(json.dumps(rec), encoding="utf-8")
        unknown = (ST.read_running(b) or {}).get("live", "missing")
        check("a record with no start stamp (liveness unknown, live None) -> False: only a PROVEN live holder keeps an "
              "instance up", unknown is None and fn(str(b)) is False, (unknown, fn(str(b))))
        ST.running_path(b).write_text("{not json", encoding="utf-8")
        check("an unreadable record -> False (never raises)", fn(str(b)) is False)
        ST.running_path(b).unlink()
        # the sweep itself (per_project_neo4j_gates' stubs: a live pidfile, stop recorded)
        saved_pid, saved_stop = P._pid_alive, P.stop_neo4j
        calls = []
        try:
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
            PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
            key_b = PR.project_key(b)
            P._pid_alive = lambda pid: True
            P.stop_neo4j = lambda version=None, project_key=None: (calls.append(project_key) or True)
            G._write_pidfile(key_b)
            G._set_last_used(key_b, 120)
            n = P.hibernate_idle(target=str(a), scope="others")
            check("(baseline) B idle + running, no sync -> stopped", n == 1 and calls == [key_b], (n, calls))
            if live_ok:
                ST.write_running(b, {"origin": "unattended", "phase": "write"})
                del calls[:]
                n = P.hibernate_idle(target=str(a), scope="others")
                check("B idle + running WITH a live sync holder -> kept (0 stopped)", n == 0 and calls == [],
                      (n, calls))
                ST.running_path(b).unlink()
                del calls[:]
                n = P.hibernate_idle(target=str(a), scope="others")
                check("... the holder gone -> stopped again", n == 1 and calls == [key_b], (n, calls))
        finally:
            P._pid_alive, P.stop_neo4j = saved_pid, saved_stop


# ============================================================================================= F — run_all exports (C1)
def gate_f_run_all_env() -> None:
    print("\n-- F. run_all runs every gate with DeepMap's background refresh off (C1 / M12) --")
    RA = _load_module(REPO / "tests" / "run_all.py", f"dm_run_all_w6_{time.time_ns()}")
    seen = []

    def fake_run(argv, **kw):
        seen.append(kw.get("env") or {})
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    RA.subprocess = types.SimpleNamespace(run=fake_run, CompletedProcess=subprocess.CompletedProcess)
    saved = os.environ.get("DEEPMAP_AUTO_SYNC")
    try:
        for operator in ("1", None):
            if operator is None:
                os.environ.pop("DEEPMAP_AUTO_SYNC", None)
            else:
                os.environ["DEEPMAP_AUTO_SYNC"] = operator
            del seen[:]
            with contextlib.redirect_stdout(io.StringIO()):
                ok = RA.run("probe", ["tests/dm_w6_probe_gates.py"])
            env = seen[0] if seen else {}
            check(f"run() hands the gate DEEPMAP_AUTO_SYNC=0 (operator's value: {operator!r}) and keeps "
                  "PYTHONIOENCODING=utf-8", ok is True and env.get("DEEPMAP_AUTO_SYNC") == "0"
                  and env.get("PYTHONIOENCODING") == "utf-8", {k: env.get(k) for k in ("DEEPMAP_AUTO_SYNC",
                                                                                     "PYTHONIOENCODING")})
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_AUTO_SYNC", None)
        else:
            os.environ["DEEPMAP_AUTO_SYNC"] = saved


def main() -> int:
    global REPO
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=None, help="the repository to test (default: this file's)")
    ns = ap.parse_args()
    if ns.tree:
        REPO = Path(ns.tree).resolve()
    print("=" * 72)
    print(f"DeepMap — auto-sync wiring: CLI sync --unattended, status/doctor, freshness H3, hibernate, run_all env "
          f"(root: {REPO})")
    print("=" * 72)
    for k in [k for k in os.environ if k.startswith(_SCRUB_PREFIXES) or k in _SCRUB]:
        os.environ.pop(k, None)
    home = _tmp("dmw6h-")
    os.environ["DEEPMAP_HOME"] = str(home)
    work = _tmp("dmw6w-")
    try:
        for g in (gate_a_parser, lambda: gate_b_dispatch(work), lambda: gate_b_end_to_end(work, home),
                  lambda: gate_c_status(work), lambda: gate_c_doctor(work), lambda: gate_c3_graph_rev(work),
                  lambda: gate_d_staleness(work),
                  gate_e_hibernate, gate_f_run_all_env):
            try:
                g()
            except (Exception, SystemExit) as e:  # noqa: BLE001 — one RED check, never an aborted suite
                check(f"(gate raised {type(e).__name__}: {e})", False)
    finally:
        for d in _TEMPS:
            if d.exists():
                _rm_tree(d)
        check("every temp dir this gate created was removed (exact paths)", not any(d.exists() for d in _TEMPS),
              [str(d) for d in _TEMPS if d.exists()])
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
