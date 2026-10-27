#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the deepmap CLI argument parser (build/deepmap_cli.py).

Locks the command surface + dispatch (sync / search / impact / status) so a refactor can't
silently drop a command or mis-wire a flag. Pure: only builds + parses argv; never connects.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load():
    spec = importlib.util.spec_from_file_location("dm_cli", BUILD / "deepmap_cli.py")
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


def gate_resource_cleanup(C) -> None:
    """The CLI commands must close the Neo4j driver even when an operation raises (no connection
    leak). A fake driver raises on verify_connectivity and records close(); each command must
    propagate the error AND still have closed the driver via its finally block. No Neo4j."""
    import argparse
    print("\n-- driver closed on error (no leak) --")

    class _FakeDriver:
        def __init__(self):
            self.closed = False

        def verify_connectivity(self):
            raise RuntimeError("simulated outage")

        def session(self):
            raise RuntimeError("should not be reached (verify_connectivity raised first)")

        def close(self):
            self.closed = True

    orig_driver = C._driver
    try:
        for name, fn, args in (
            ("search", C.cmd_search, argparse.Namespace(query="q", top_k=5)),
            ("impact", C.cmd_impact, argparse.Namespace(name="x", limit=5)),
            ("status", C.cmd_status, argparse.Namespace()),
        ):
            fake = _FakeDriver()
            C._driver = lambda f=fake: f      # monkeypatch the module-global factory
            raised = False
            try:
                fn(args)
            except RuntimeError:
                raised = True
            check(f"cmd_{name}: propagates the underlying error", raised)
            check(f"cmd_{name}: closed the driver via finally (no leak)", fake.closed, "driver leaked")

        # cmd_doctor is a DIAGNOSTIC — it CATCHES the error (never propagates) but must STILL close
        # the driver via its inner finally on the failure path (its most common case) — bug-hunt #13.
        fake = _FakeDriver()
        C._driver = lambda f=fake: f
        d_raised = False
        try:
            C.cmd_doctor(argparse.Namespace())
        except Exception:
            d_raised = True
        check("cmd_doctor: does NOT propagate (a diagnostic reports, never crashes)", not d_raised)
        check("cmd_doctor: closed the driver via finally on the failure path (#13, no leak)",
              fake.closed, "driver leaked on the doctor failure path")
    finally:
        C._driver = orig_driver


def gate_input_clamp_and_null_format(C) -> None:
    """cmd_search/cmd_impact must CLAMP a negative/zero/huge top_k|limit (so an invalid LIMIT never
    reaches Neo4j) and format a NULL-field result row without a TypeError (bug-hunt #14/#15). No Neo4j
    — fake retrieval/embeddings modules + a fake driver capture the clamped value."""
    import argparse
    print("\n-- input clamp + NULL-field formatting --")
    captured: dict = {}

    class _FakeRetriever:
        def __init__(self, drv, embed_fn=None, arm_weights=None):
            captured["arm_weights"] = arm_weights   # RM-1: cmd_search passes the gated weights (None by default)

        def search(self, q, top_k=15):
            captured["top_k"] = top_k
            return [{"type": None, "name": None, "file": None, "line": None,
                     "score": None, "summary": None}]   # a NULL row must not crash the formatter

    class _FakeRetrievalMod:
        DeepMapRetriever = _FakeRetriever

        @staticmethod
        def query_callers(drv, name, limit):
            captured["limit"] = limit
            return [{"confidence": None, "caller": None, "callee": None, "file": None, "line": None}]

        @staticmethod
        def query_impact(drv, name, limit, edge_types=None, max_hops=None):   # Track C: multi-hop impact
            captured["limit"] = limit
            return [{"confidence": None, "caller": None, "callee": None, "file": None, "line": None,
                     "hop": None, "via": None, "via_path": None}]   # NULL row must not crash the formatter

    class _FakeEmb:
        @staticmethod
        def embed_query(t):
            return [0.0] * 768

    class _FakeRetrievalLog:  # RM-1: cmd_search calls _load("retrieval_log").maybe_log_search
        @staticmethod
        def maybe_log_search(*a, **k):
            captured["logged"] = True
            return None

    class _FakeLearnedFusion:  # RM-1: cmd_search resolves gated weights (None by default -> equal)
        @staticmethod
        def resolve_arm_weights_for_live(*a, **k):
            captured["resolved"] = True
            return None

    class _FakeDriver:
        def verify_connectivity(self):
            pass

        def close(self):
            pass

    def _fake_load(name):
        if name == "embeddings":
            return _FakeEmb
        if name == "retrieval_log":
            return _FakeRetrievalLog
        if name == "learned_fusion":
            return _FakeLearnedFusion
        return _FakeRetrievalMod

    orig_load, orig_driver = C._load, C._driver
    C._load = _fake_load
    C._driver = lambda: _FakeDriver()
    try:
        crashed = False
        try:
            C.cmd_search(argparse.Namespace(query="q", top_k=-5))
        except Exception as exc:  # noqa: BLE001 — a TypeError here is the bug we fixed
            crashed = True
            print(f"    cmd_search raised: {exc!r}")
        check("cmd_search clamps a negative top_k to >=1", captured.get("top_k") == 1, str(captured.get("top_k")))
        check("cmd_search formats a NULL-field row without crashing", not crashed)
        check("cmd_search invokes the RM-1 opt-in logger (wired)", captured.get("logged") is True)
        check("cmd_search resolves gated learned weights (wired)", captured.get("resolved") is True)
        check("cmd_search default arm_weights is None (equal-weight; gate off)", captured.get("arm_weights") is None, str(captured.get("arm_weights")))

        crashed = False
        try:
            C.cmd_impact(argparse.Namespace(name="x", limit=-3))
        except Exception as exc:  # noqa: BLE001
            crashed = True
            print(f"    cmd_impact raised: {exc!r}")
        check("cmd_impact clamps a negative limit to >=1", captured.get("limit") == 1, str(captured.get("limit")))
        check("cmd_impact formats a NULL-field row without crashing", not crashed)
    finally:
        C._load, C._driver = orig_load, orig_driver


def gate_version(C) -> None:
    """`deepmap version` prints the manifest version, needs no Neo4j, and never crashes."""
    import contextlib
    import io
    import json
    print("\n-- version command (manifest-sourced, no Neo4j) --")
    manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    expected = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.cmd_version(None)
    out = buf.getvalue()
    check("version exits 0", rc == 0, f"rc={rc}")
    check("version prints 'deepmap'", "deepmap" in out, out.strip())
    check("version matches the manifest", expected and expected in out,
          f"expected {expected!r} in {out.strip()!r}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — CLI parser gates (no Neo4j)")
    print("=" * 64)

    C = _load()
    p = C.build_parser()

    print("\n-- command dispatch --")
    a = p.parse_args(["sync", ".", "--stale-cleanup"])
    check("sync -> cmd_sync", a.fn is C.cmd_sync)
    check("sync target + flag parsed", a.target == "." and a.stale_cleanup is True)
    check("sync flag defaults off", p.parse_args(["sync", "x"]).stale_cleanup is False)

    a = p.parse_args(["search", "find the parser", "--top-k", "5"])
    check("search -> cmd_search", a.fn is C.cmd_search)
    check("search query + top_k parsed", a.query == "find the parser" and a.top_k == 5)
    check("search top_k default 15", p.parse_args(["search", "q"]).top_k == 15)

    a = p.parse_args(["impact", "validate", "--limit", "7"])
    check("impact -> cmd_impact", a.fn is C.cmd_impact)
    check("impact name + limit parsed", a.name == "validate" and a.limit == 7)
    check("impact limit default 30", p.parse_args(["impact", "x"]).limit == 30)

    check("status -> cmd_status", p.parse_args(["status"]).fn is C.cmd_status)
    check("doctor -> cmd_doctor", p.parse_args(["doctor"]).fn is C.cmd_doctor)
    check("version -> cmd_version", p.parse_args(["version"]).fn is C.cmd_version)

    print("\n-- a subcommand is required --")
    try:
        p.parse_args([])
        check("no subcommand is rejected", False, "did not raise")
    except SystemExit:
        check("no subcommand is rejected", True)

    gate_resource_cleanup(C)
    gate_input_clamp_and_null_format(C)
    gate_version(C)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
