#!/usr/bin/env python3
"""ISS-build:023 (C13) — ensure_neo4j keeps its "Never raises" promise; a started JVM is never left unrecorded.

Finding: ensure_neo4j documents "Never raises", but past its fast path only the download/extract step was guarded, and
only for (OSError, RuntimeError). A download that broke off mid-stream (http.client.IncompleteRead), a corrupt archive
(tarfile / zipfile errors), an unwritable console log, a POSIX launcher that is missing or not executable (Popen) and —
on Windows — the pid-file write that runs AFTER the JVM was launched all escaped to setup / `provision.py ensure` as raw
tracebacks: no "setup verdict: FAILED", no persisted verdict, and on Windows a running JVM with no run/neo4j.pid, which
`stop` can then never find.

The fix: every failure below the fast path is a False with a one-line reason in `diag` (and on stderr); setup prints
"setup verdict: FAILED" naming it; start_neo4j creates its run dir BEFORE the launch and, when the Windows pid file
can't be written, stops the launcher tree it has just started (its own live Popen handle) instead of leaving it
unrecorded.

Hermetic: temp DEEPMAP_HOME; the network, the JVM and every process kill are fakes (no real kill is ever issued).
Usage: python tests/o1_ensure_never_raises_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import http.client
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import types
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
VER = "5.26.0"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _load(root: Path):
    sys.path.insert(0, str(root / "build"))
    import provision as P  # noqa: E402
    return P


class _Patch:
    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.saved = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.mod, k, v)


def _call(fn, *a, **k):
    """(result, raised exception or None, captured stdout+stderr)."""
    out, err = io.StringIO(), io.StringIO()
    res, exc = None, None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            res = fn(*a, **k)
        except BaseException as e:  # noqa: BLE001 — a raise IS the defect under test
            exc = e
    return res, exc, out.getvalue() + err.getvalue()


def _down(P, **more):
    """The provisioning path: Neo4j not up, preflight passes; `more` overrides."""
    base = {"_bolt_state": lambda *a, **k: "down", "preflight": lambda *a, **k: True}
    base.update(more)
    return _Patch(P, **base)


def _ensure(P, diag):
    return _call(P.ensure_neo4j, uri="bolt://127.0.0.1:7687", user="neo4j", password="deepmap", version=VER,
                 project_key=None, diag=diag)


class _FakeProc:
    def __init__(self, pid=4242, rc=None):
        self.pid, self._rc = pid, rc

    def poll(self):
        return self._rc


def _fake_subprocess(popen):
    return types.SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                                 run=subprocess.run, TimeoutExpired=subprocess.TimeoutExpired,
                                 SubprocessError=subprocess.SubprocessError, CompletedProcess=subprocess.CompletedProcess,
                                 list2cmdline=subprocess.list2cmdline)


def gate_a_install_errors(P) -> None:
    print("\n-- A: a download / extract failure of ANY kind is a False with a reason, never a traceback --")

    def raiser(exc):
        def f(version):
            raise exc
        return f

    for label, exc in (("a mid-stream break (http.client.IncompleteRead)", http.client.IncompleteRead(b"x" * 10, 99)),
                       ("a corrupt tar (tarfile.ReadError)", tarfile.ReadError("unexpected end of data")),
                       ("a corrupt zip (zipfile.BadZipFile)", zipfile.BadZipFile("File is not a zip file")),
                       ("a truncated stream (EOFError)", EOFError("Compressed file ended before the end")),
                       ("an OSError (unchanged)", OSError(28, "No space left on device")),
                       ("a RuntimeError (unchanged)", RuntimeError("SHA-256 mismatch"))):
        diag: dict = {}
        with _down(P, _is_initialized=lambda *a, **k: False, _shared_binary_present=lambda *a, **k: False,
                   _download_extract_shared=raiser(exc)):
            res, raised, out = _ensure(P, diag)
        check(f"{label}: ensure_neo4j returns False (no raise)", res is False and raised is None,
              f"res={res!r} raised={raised!r}")
        check(f"... {label}: diag['reason'] names it", type(exc).__name__ in str(diag.get("reason")), repr(diag))
        check(f"... {label}: nothing was started", "started" not in diag, repr(diag))


def gate_b_start_errors(P, home: Path) -> None:
    print("\n-- B: a start that fails BEFORE the launch (log / launcher) is a False with a reason --")
    spawned: list = []

    def counting_popen(argv, **kw):
        spawned.append(argv)
        return _FakeProc()

    # (1) the console log can't be opened (a directory sits where it goes)
    log = P.paths(VER)["log"]
    log.mkdir(parents=True, exist_ok=True)
    diag: dict = {}
    try:
        with _down(P, _is_initialized=lambda *a, **k: True, _shared_binary_present=lambda *a, **k: True,
                   _download_extract_shared=lambda v: None, configure_neo4j=lambda *a, **k: False,
                   set_initial_password=lambda *a, **k: True, subprocess=_fake_subprocess(counting_popen),
                   _pid_create_time=lambda pid: 1.0):
            res, raised, out = _ensure(P, diag)
    finally:
        log.rmdir()
    check("an unopenable console log: False, no raise", res is False and raised is None, f"res={res!r} raised={raised!r}")
    check("... nothing was launched, and diag says why", spawned == [] and bool(diag.get("reason")), f"{spawned} {diag}")

    # (2) the launcher can't be executed (POSIX: a missing / non-executable bin/neo4j -> Popen raises)
    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", str(argv[0]))

    diag = {}
    with _down(P, _is_initialized=lambda *a, **k: True, _shared_binary_present=lambda *a, **k: True,
               _download_extract_shared=lambda v: None, configure_neo4j=lambda *a, **k: False,
               set_initial_password=lambda *a, **k: True, subprocess=_fake_subprocess(missing),
               _pid_create_time=lambda pid: 1.0, is_windows=lambda: False):
        res, raised, out = _ensure(P, diag)
    check("a launcher Popen can't start (FileNotFoundError): False, no raise", res is False and raised is None,
          f"res={res!r} raised={raised!r}")
    check("... diag names the failed start", "FileNotFoundError" in str(diag.get("reason")), repr(diag))

    # (3) anything else unexpected below the fast path (e.g. the store dir can't be listed)
    def unreadable(*a, **k):
        raise PermissionError(13, "Access is denied", "data/databases")

    diag = {}
    with _down(P, _is_initialized=unreadable):
        res, raised, out = _ensure(P, diag)
    check("an unexpected error (PermissionError listing the store): False, no raise", res is False and raised is None,
          f"res={res!r} raised={raised!r}")
    check("... with a one-line reason in diag", bool(diag.get("reason")) and "\n" not in str(diag.get("reason")),
          repr(diag))


def gate_c_windows_pidfile(P) -> None:
    print("\n-- C: Windows — the pid file can't be written AFTER the launch: the launch is stopped, never orphaned --")
    for stop_ok in (True, False):
        killed: list = []
        spawned: list = []

        def popen(argv, **kw):
            spawned.append((argv, kw))
            return _FakeProc(pid=4242, rc=None)

        def fake_kill(pid, *, tree):
            killed.append((pid, tree))
            return stop_ok

        pf = P.paths(VER)["pidfile"]
        pf.mkdir(parents=True, exist_ok=True)       # a DIRECTORY where the pid file goes: its write fails
        diag: dict = {}
        try:
            with _down(P, is_windows=lambda: True, _is_initialized=lambda *a, **k: True,
                       _shared_binary_present=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                       configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True,
                       subprocess=_fake_subprocess(popen), _terminate_pid=fake_kill, _pid_create_time=lambda pid: 1.0,
                       wait_for_bolt=lambda *a, **k: True):
                res, raised, out = _ensure(P, diag)
        finally:
            pf.rmdir()
        tag = "the stop works" if stop_ok else "the stop FAILS"
        check(f"{tag}: ensure_neo4j returns False (no raise)", res is False and raised is None,
              f"res={res!r} raised={raised!r} {out[-300:]}")
        check(f"... {tag}: the launcher tree it just started was stopped (pid 4242, tree)",
              len(spawned) == 1 and killed == [(4242, True)], f"spawned={len(spawned)} killed={killed}")
        check(f"... {tag}: diag names the unrecorded pid", "4242" in str(diag.get("reason")), repr(diag))
        if not stop_ok:
            check("... the stop failed: the reason tells the user to stop pid 4242 by hand",
                  "by hand" in str(diag.get("reason")), repr(diag))

    # a launcher that already exited is never signalled (its pid may be reused)
    killed2: list = []
    pf = P.paths(VER)["pidfile"]
    pf.mkdir(parents=True, exist_ok=True)
    diag = {}
    try:
        with _down(P, is_windows=lambda: True, _is_initialized=lambda *a, **k: True,
                   _shared_binary_present=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                   configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True,
                   subprocess=_fake_subprocess(lambda argv, **kw: _FakeProc(pid=4343, rc=1)),
                   _terminate_pid=lambda pid, *, tree: killed2.append(pid) or True, _pid_create_time=lambda pid: 1.0):
            res, raised, out = _ensure(P, diag)
    finally:
        pf.rmdir()
    check("a launcher that already EXITED is not signalled (PID reuse), and ensure still says False",
          res is False and raised is None and killed2 == [], f"res={res!r} raised={raised!r} killed={killed2}")


def gate_d_setup_verdict(P, work: Path) -> None:
    print("\n-- D: setup / `provision.py ensure` print a verdict and exit 1 — never a traceback --")
    proj = work / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

    def boom(version):
        raise http.client.IncompleteRead(b"", 180_000_000)

    stubs = dict(_source_census_problem=lambda t: None, apply_ports_for=lambda *a, **k: (8801, 8802),
                 resolved_instance=lambda t: None, _is_initialized=lambda *a, **k: False,
                 _shared_binary_present=lambda *a, **k: False, _download_extract_shared=boom)
    with _down(P, **stubs):
        rc, raised, out = _call(P.main, ["setup", str(proj)])
    check("`setup` with a broken download: exit 1, no traceback", rc == 1 and raised is None,
          f"rc={rc!r} raised={raised!r}")
    check("... prints 'setup verdict: FAILED' naming the Neo4j failure",
          "setup verdict: FAILED" in out and "IncompleteRead" in out, out[-600:])
    with _down(P, **stubs):
        rc, raised, out = _call(P.main, ["ensure", str(proj)])
    check("`ensure` with a broken download: exit 1, no traceback", rc == 1 and raised is None,
          f"rc={rc!r} raised={raised!r}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — ensure_neo4j never raises; no unrecorded JVM (root: {root})")
    print("=" * 72)
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_VERSION", "NEO4J_PASSWORD"):
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="o1ens") as td:
        home = Path(td) / "home"
        home.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        P = _load(root)
        gate_a_install_errors(P)
        gate_b_start_errors(P, home)
        gate_c_windows_pidfile(P)
        work = Path(td) / "work"
        work.mkdir()
        gate_d_setup_verdict(P, work)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
