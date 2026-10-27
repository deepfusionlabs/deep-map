#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: graphify_extract.extract() must not silently reuse a STALE graph.json (F3e). No Neo4j, no spend.

The 2026-07 field audit: extract()'s docstring promises "None on non-zero exit", but the code only
checked `graph.json` EXISTS — never the subprocess return code or freshness. So when graphify exits
non-zero (a crash on a newly-added file, or an atomic temp+rename that fails before the rename) while a
PRIOR run's graph.json is still on disk, extract() read the stale file and returned it as if fresh:
sync then reports a broken re-sync as READY and stamps it built-at-HEAD — contradicting the "setup
never reports success on a broken graph" honesty wedge.

Fix: a FRESHNESS guard — anchor the subprocess start time and trust graph.json only if it was
(re)written this run (mtime >= start, with a small skew slack). Preferred over a strict returncode==0
gate, which would false-negative on graphify's benign per-file warnings that still write a valid graph.
Hermetic: a fake `graphify` module + a monkeypatched subprocess.run, throwaway temp dirs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


# A minimal valid graphify graph (one node, no links) that map_graph accepts.
_FRESH_GRAPH = {
    "nodes": [{"id": "n1", "label": "foo()", "file_type": "code", "source_file": "a.py",
               "source_location": "L1"}],
    "links": [],
}


class _Proc:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = ""
        self.stderr = "graphify: fatal error on new_file.py" if rc else ""


def _with_fake_graphify_and_run(fake_run):
    """Install a fake `graphify` module (so find_spec passes) + patch subprocess.run; return a restorer."""
    saved_mod = sys.modules.get("graphify")
    fake = types.ModuleType("graphify")
    fake.__spec__ = importlib.util.spec_from_loader("graphify", loader=None)
    sys.modules["graphify"] = fake
    saved_run = GX.subprocess.run
    GX.subprocess.run = fake_run

    def restore():
        GX.subprocess.run = saved_run
        if saved_mod is None:
            sys.modules.pop("graphify", None)
        else:
            sys.modules["graphify"] = saved_mod
    return restore


def gate_stale_graph_rejected() -> None:
    print("\n-- F3e: a non-zero graphify exit + a STALE prior graph.json -> extract() returns None --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        go = GX.graphify_out_dir(root)      # where graphify writes (G1: a short dir under DEEPMAP_HOME)
        go.mkdir(parents=True, exist_ok=True)
        # A stale graph.json from a PRIOR successful run (mtime one hour ago).
        gpath = go / "graph.json"
        gpath.write_text(json.dumps(_FRESH_GRAPH), encoding="utf-8")
        old = time.time() - 3600
        os.utime(gpath, (old, old))

        def fake_run(*a, **k):     # graphify "fails" and does NOT rewrite graph.json
            return _Proc(rc=1)

        restore = _with_fake_graphify_and_run(fake_run)
        try:
            res = GX.extract(root)
            check("stale graph.json after a failed graphify run -> extract() returns None (ast fallback)",
                  res is None, repr(type(res)))
        finally:
            restore()


def gate_fresh_graph_used() -> None:
    print("\n-- F3e: graphify (re)writes graph.json this run -> extract() maps it (no false negative) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        go = GX.graphify_out_dir(root)
        go.mkdir(parents=True, exist_ok=True)
        gpath = go / "graph.json"

        def fake_run(*a, **k):     # graphify succeeds and writes a FRESH graph.json
            gpath.write_text(json.dumps(_FRESH_GRAPH), encoding="utf-8")
            return _Proc(rc=0)

        restore = _with_fake_graphify_and_run(fake_run)
        try:
            res = GX.extract(root)
            check("a freshly-written graph.json -> extract() returns a mapped (nodes, calls, imports)",
                  isinstance(res, tuple) and len(res) == 3, repr(type(res)))
            if isinstance(res, tuple):
                nodes = res[0]
                check("the mapped graph has the expected node", any(n.get("name") == "foo" for n in nodes),
                      str([n.get("name") for n in nodes]))
        finally:
            restore()


def gate_fresh_despite_warning_rc() -> None:
    print("\n-- F3e: a benign non-zero rc but a FRESH graph.json is still used (mtime, not strict rc) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        go = GX.graphify_out_dir(root)
        go.mkdir(parents=True, exist_ok=True)
        gpath = go / "graph.json"

        def fake_run(*a, **k):     # graphify emits a per-file warning (rc=1) but DID write a fresh graph
            gpath.write_text(json.dumps(_FRESH_GRAPH), encoding="utf-8")
            return _Proc(rc=1)

        restore = _with_fake_graphify_and_run(fake_run)
        try:
            res = GX.extract(root)
            check("a fresh graph.json written this run is trusted even with a warning rc (no over-strict "
                  "returncode gate)", isinstance(res, tuple) and len(res) == 3, repr(type(res)))
        finally:
            restore()


def gate_no_graph_still_none() -> None:
    print("\n-- F3e: no graph.json at all -> None (pre-existing behavior preserved) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        GX.graphify_out_dir(root).mkdir(parents=True, exist_ok=True)

        def fake_run(*a, **k):
            return _Proc(rc=1)

        restore = _with_fake_graphify_and_run(fake_run)
        try:
            check("no graph.json -> None", GX.extract(root) is None)
        finally:
            restore()


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — graphify extract freshness gates (F3e, no Neo4j)")
    print("=" * 64)
    # graphify's output dir lives under DEEPMAP_HOME now (G1) — keep this run off the real ~/.deepmap.
    import shutil
    home, saved = tempfile.mkdtemp(prefix="dm_fresh_home_"), os.environ.get("DEEPMAP_HOME")
    os.environ["DEEPMAP_HOME"] = home
    try:
        gate_stale_graph_rejected()
        gate_fresh_graph_used()
        gate_fresh_despite_warning_rc()
        gate_no_graph_still_none()
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
