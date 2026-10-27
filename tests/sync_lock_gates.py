#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the sync concurrency lock (T10).

The lock is an OS-level exclusive lock (flock on POSIX, an msvcrt byte-lock on Windows) held via
an open file handle, so the kernel releases it automatically on crash — no stale/pid/steal logic.
This gate proves: a holder blocks a second acquirer, release frees it, and the handle is closed.
Pure: filesystem only, no Neo4j.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync", BUILD / "sync.py")
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


def gate_stale_cleanup_guard(S) -> None:
    """BEHAVIORAL: sync(cleanup_stale=True) must REFUSE to prune when extraction yields 0 nodes (a
    crash / empty extraction must NEVER wipe a good graph), but MUST prune when nodes are present.
    A source grep can't catch an inverted guard, so this drives sync() with patched internals + a
    fake driver (no Neo4j) and checks whether _cleanup_stale actually fires."""
    import types
    print("\n-- stale-cleanup 0-node wipe guard (behavioral) --")
    counter = {"cleanup": 0}
    saved = {k: getattr(S, k) for k in ("_rebuild_code", "push_to_neo4j", "_cleanup_stale", "indexes", "embeddings")}
    # Isolate the cross-instance lock (sync() now takes it too) to a throwaway DEEPMAP_HOME so this
    # gate never touches the real ~/.deepmap or races a live sync on this machine.
    _saved_home = os.environ.get("DEEPMAP_HOME")
    os.environ["DEEPMAP_HOME"] = tempfile.mkdtemp(prefix="dm_wipeguard_home_")

    class _FakeSess:
        # The switch guard reads the :_DeepMapMeta owner + a node count and writes the owner; an empty
        # result => no owner recorded => this gate's tempdir is treated as a fresh graph.
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **k):
            return []

    class _FakeDrv:
        def verify_connectivity(self):
            pass

        def close(self):
            pass

        def session(self):
            return _FakeSess()

    def run_with(nodes, git=False):
        counter["cleanup"] = 0
        S._rebuild_code = lambda root: (list(nodes), [], [], "ast")
        S.push_to_neo4j = lambda d, n, c, i, typed_edges=None: (len(n), 0)
        S._cleanup_stale = lambda d, keys: counter.__setitem__("cleanup", counter["cleanup"] + 1)
        S.indexes = types.SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True)
        S.embeddings = types.SimpleNamespace(generate_embeddings=lambda d: 0)
        td = Path(tempfile.mkdtemp(prefix="dm_wipeguard_"))
        if git:
            (td / ".git").mkdir()   # mark it a repo root so the whole-graph prune is in-scope
        return S.sync(td, cleanup_stale=True, driver=_FakeDrv())

    node = {"key": "x", "label": "Function", "name": "x", "source_file": "a.py", "start_line": 1, "body_summary": "s"}
    try:
        run_with([])
        check("0 extracted nodes -> _cleanup_stale NOT called (never wipes a good graph)", counter["cleanup"] == 0,
              f"cleanup called {counter['cleanup']}x")
        run_with([node], git=True)
        check("non-empty extraction AT A REPO ROOT (.git) -> _cleanup_stale IS called", counter["cleanup"] == 1,
              f"cleanup called {counter['cleanup']}x")
        # bug-hunt #3 HIGH: keys are root-relative, so a subdir/non-repo sync's live set never covers
        # the full graph — a graph-wide prune would wipe out-of-scope nodes. It MUST be refused.
        run_with([node], git=False)
        check("non-empty extraction NOT at a repo root -> _cleanup_stale NOT called (subdir-wipe guard)",
              counter["cleanup"] == 0, f"cleanup called {counter['cleanup']}x")
    finally:
        for k, v in saved.items():
            setattr(S, k, v)
        if _saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = _saved_home

    print("\n-- _cleanup_is_safe (pure: gates the whole-graph prune on a repo-root marker) --")
    safe_root = Path(tempfile.mkdtemp(prefix="dm_safe_")); (safe_root / ".git").mkdir()
    unsafe_root = Path(tempfile.mkdtemp(prefix="dm_unsafe_"))
    pyproj_root = Path(tempfile.mkdtemp(prefix="dm_pyproj_")); (pyproj_root / "pyproject.toml").write_text("", encoding="utf-8")
    optin_root = Path(tempfile.mkdtemp(prefix="dm_optin_")); (optin_root / ".deepmap-root").write_text("", encoding="utf-8")
    check("_cleanup_is_safe True at a repo root (.git present)", S._cleanup_is_safe(safe_root) is True)
    check("_cleanup_is_safe False without any marker (bare folder)", S._cleanup_is_safe(unsafe_root) is False)
    check("_cleanup_is_safe True for a non-git project root (pyproject.toml) — P7",
          S._cleanup_is_safe(pyproj_root) is True)
    check("_cleanup_is_safe True with an explicit .deepmap-root opt-in — P7",
          S._cleanup_is_safe(optin_root) is True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — sync concurrency lock gates (T10; OS lock; no Neo4j)")
    print("=" * 64)

    S = _load_sync()
    root = Path(tempfile.mkdtemp(prefix="dm_synclock_"))

    print("\n-- acquire / hold / release --")
    h1 = S._acquire_sync_lock(root)
    check("first acquire returns a handle", h1 is not None)
    check("lock file exists under .deepmap/", (root / ".deepmap" / "sync.lock").exists())

    print("\n-- a second acquirer is blocked while held --")
    h2 = S._acquire_sync_lock(root)
    check("concurrent acquire returns None (OS lock held)", h2 is None)

    print("\n-- release frees it, and re-acquire works --")
    S._release_sync_lock(h1)
    check("handle closed on release", getattr(h1, "closed", True))
    h3 = S._acquire_sync_lock(root)
    check("re-acquire after release succeeds", h3 is not None)
    S._release_sync_lock(h3)

    print("\n-- release(None) is safe (skip path leaks nothing) --")
    try:
        S._release_sync_lock(None)
        check("release(None) does not raise", True)
    except Exception as e:  # noqa: BLE001
        check("release(None) does not raise", False, str(e))

    print("\n-- no pid/stale machinery remains (OS lock self-heals) --")
    check("legacy _pid_alive removed", not hasattr(S, "_pid_alive"))

    gate_stale_cleanup_guard(S)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
