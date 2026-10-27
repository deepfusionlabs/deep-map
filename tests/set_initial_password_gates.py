#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the Windows metacharacter-password hardening of
`build/provision.py:set_initial_password`.

A Neo4j password containing cmd.exe metacharacters (`& | ^ < > %`) cannot be delivered intact to
`neo4j-admin.bat` on Windows: the OS launches a `.bat` in an implicit `cmd.exe` WITHOUT Python
escaping, and `neo4j-admin dbms set-initial-password` takes the password as a positional arg only
(no stdin/env/file). The old code SILENTLY mangled such a password (-> a confusing downstream
connect/timeout). The fix converts that into a LOUD, actionable refusal that runs nothing.

These gates lock:
  - `_win_password_cmd_safe`: rejects the metacharacter set + leading/trailing whitespace, accepts
    ASCII-safe passwords (incl. spaces / embedded quotes) and the dev default `deepmap`,
  - `set_initial_password` on Windows: REFUSES to run neo4j-admin for an unsafe password (spy proves
    subprocess.run is not called + an error is printed), and DOES run for a safe password.

Pure + hermetic: monkeypatches `is_windows`/`_is_initialized`/`paths`/`subprocess.run` (no Neo4j, no
process spawned). Cross-platform — forces the Windows branch regardless of the host OS.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

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


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Spy:
    """Records subprocess.run calls and returns a benign fake process."""
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        class _P:
            returncode = 0
            stdout = "Changed password for user 'neo4j'."
            stderr = ""
        return _P()


def gate_helper(P) -> None:
    print("\n-- _win_password_cmd_safe: rejects cmd metacharacters + edge whitespace --")
    fn = getattr(P, "_win_password_cmd_safe", None)
    check("_win_password_cmd_safe exists", callable(fn))
    if not callable(fn):
        return
    check("dev default 'deepmap' is safe", fn("deepmap") is True)
    for ch in "&|^<>%":
        check(f"rejects a password containing {ch!r}", fn(f"p{ch}w") is False, f"{ch}")
    check("rejects the classic injection 'p&whoami'", fn("p&whoami") is False)
    check("rejects '%NEO4J%' expansion", fn("%NEO4J%") is False)
    check("rejects leading whitespace", fn(" pw") is False)
    check("rejects trailing whitespace", fn("pw ") is False)
    # Not over-rejecting: internal space + embedded quote are handled by the existing cmd wrapper.
    check("accepts an internal space", fn("pass word") is True)
    check("accepts an embedded double-quote", fn('pa"ss') is True)
    check("accepts a long ASCII-safe password", fn("Str0ng-Pass_123!") is True)


def gate_windows_refuses_unsafe(P) -> None:
    print("\n-- set_initial_password (Windows): refuses unsafe pw, runs safe pw --")
    saved = {"is_windows": P.is_windows, "_is_initialized": P._is_initialized,
             "paths": P.paths, "run": P.subprocess.run, "_is_macos": getattr(P, "_is_macos", None)}
    try:
        P.is_windows = lambda: True
        # the Windows branch, whatever the host: on a Mac neo4j-admin's environment would first get the JDK the Java
        # preflight picks (provision._neo4j_java_env, /usr/libexec/java_home calls through the spy)
        P._is_macos = lambda: False
        P._is_initialized = lambda *a, **k: False   # (version, project_key=None)
        # admin_bin only needs to be *something*; the unsafe path returns before touching it.
        P.paths = lambda *a, **k: {"admin_bin": Path("neo4j-admin.bat"), "conf_dir": Path("conf")}

        # (a) Unsafe password -> neo4j-admin is NOT run, an error is printed.
        spy = _Spy()
        P.subprocess.run = spy
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            P.set_initial_password("5.26.0", "p&whoami")
        check("unsafe pw -> subprocess.run NOT called (no silent corruption)",
              len(spy.calls) == 0, f"{len(spy.calls)} call(s)")
        check("unsafe pw -> a clear error is printed to stderr",
              "p&whoami".__len__() > 0 and err.getvalue().strip() != ""
              and ("password" in err.getvalue().lower()), err.getvalue().strip()[:80])

        # (b) Safe password (dev default) -> neo4j-admin IS run.
        spy2 = _Spy()
        P.subprocess.run = spy2
        with contextlib.redirect_stderr(io.StringIO()):
            P.set_initial_password("5.26.0", "deepmap")
        check("safe pw 'deepmap' -> subprocess.run IS called (unchanged happy path)",
              len(spy2.calls) == 1, f"{len(spy2.calls)} call(s)")
    finally:
        P.is_windows = saved["is_windows"]
        P._is_initialized = saved["_is_initialized"]
        P.paths = saved["paths"]
        P.subprocess.run = saved["run"]
        if saved["_is_macos"] is None:
            del P._is_macos
        else:
            P._is_macos = saved["_is_macos"]


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — set_initial_password Windows metacharacter gates (no Neo4j)")
    print("=" * 64)
    P = _load("provision", BUILD)
    gate_helper(P)
    gate_windows_refuses_unsafe(P)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
