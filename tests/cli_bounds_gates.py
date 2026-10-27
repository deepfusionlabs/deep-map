#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the shared search/impact result-count bounds (build/mcp_bounds.py).

Locks:
  - the bound constants (top_k <= 50, impact limit <= 100; defaults 15 / 30) live in ONE module,
  - clamp_int clamps above/below/at-boundary, passes in-range/default through with NO note, and
    coerces a non-integer to the floor,
  - the MCP server AND the CLI both SOURCE the bounds from mcp_bounds (no drift-prone magic literals),
  - `deepmap search|impact` emits a CLEAR message (to stderr) on an out-of-range flag and the clamped
    value reaches the retriever; an in-range/default value stays silent (behaviour unchanged).

Pure + hermetic: imports the module, source-scans the two call sites, and drives cmd_search/cmd_impact
in-process with fake driver/retrieval modules (the tests/cli_gates.py idiom). Never connects to Neo4j.
"""
from __future__ import annotations

import argparse
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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def gate_constants(B) -> None:
    print("\n-- mcp_bounds: the single source of truth for the counts --")
    check("TOP_K_MAX == 50 (frozen MCP contract)", B.TOP_K_MAX == 50, str(B.TOP_K_MAX))
    check("TOP_K_DEFAULT == 15", B.TOP_K_DEFAULT == 15, str(B.TOP_K_DEFAULT))
    check("IMPACT_LIMIT_MAX == 100", B.IMPACT_LIMIT_MAX == 100, str(B.IMPACT_LIMIT_MAX))
    check("IMPACT_LIMIT_DEFAULT == 30", B.IMPACT_LIMIT_DEFAULT == 30, str(B.IMPACT_LIMIT_DEFAULT))


def gate_clamp_int(B) -> None:
    print("\n-- clamp_int: above/below/at-boundary/in-range/non-int --")
    v, note = B.clamp_int(999, 1, 50, "top-k")
    check("above hi -> hi + note", v == 50 and note is not None, f"{v} {note!r}")
    check("note names the flag + bound", "top-k" in (note or "") and "50" in (note or ""), note or "")
    v, note = B.clamp_int(-5, 1, 50, "top-k")
    check("below lo -> lo + note", v == 1 and note is not None, f"{v} {note!r}")
    v, note = B.clamp_int(0, 1, 100, "limit")
    check("zero -> lo(1) + note", v == 1 and note is not None, f"{v} {note!r}")
    v, note = B.clamp_int(50, 1, 50, "top-k")
    check("at hi boundary -> same, NO note", v == 50 and note is None, f"{v} {note!r}")
    v, note = B.clamp_int(1, 1, 50, "top-k")
    check("at lo boundary -> same, NO note", v == 1 and note is None, f"{v} {note!r}")
    v, note = B.clamp_int(15, 1, 50, "top-k")
    check("in range -> same, NO note (silent default path)", v == 15 and note is None, f"{v} {note!r}")
    v, note = B.clamp_int("x", 1, 50, "top-k")
    check("non-integer -> floor(1) + note", v == 1 and note is not None, f"{v} {note!r}")


def gate_source_parity(B) -> None:
    print("\n-- both call sites SOURCE the bounds from mcp_bounds (no magic-literal drift) --")
    srv = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    check("server imports the names from mcp_bounds",
          "from mcp_bounds import" in srv and "TOP_K_MAX" in srv and "IMPACT_LIMIT_MAX" in srv, "")
    check("server keeps TOP_K_MAX/IMPACT_LIMIT_MAX as module attributes (used by mcp_transport_gates)",
          "min(max(1, int(top_k)), TOP_K_MAX)" in srv and "min(max(1, int(limit)), IMPACT_LIMIT_MAX)" in srv, "")
    cli = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("CLI imports mcp_bounds + uses clamp_int", "import mcp_bounds" in cli and "clamp_int" in cli, "")
    check("CLI dropped the magic-literal top_k clamp",
          "min(max(1, int(args.top_k)), 50)" not in cli, "residual magic 50")
    check("CLI dropped the magic-literal limit clamp",
          "min(max(1, int(args.limit)), 100)" not in cli, "residual magic 100")


# ---- e2e: drive cmd_search/cmd_impact in-process with fakes (tests/cli_gates.py idiom) ----

class _FakeRetriever:
    def __init__(self, drv, embed_fn=None, arm_weights=None):
        pass

    def search(self, q, top_k=15):
        _CAP["top_k"] = top_k
        return []


class _FakeRetrievalMod:
    DeepMapRetriever = _FakeRetriever

    @staticmethod
    def query_impact(drv, name, limit, edge_types=None, max_hops=None):
        _CAP["limit"] = limit
        return []


class _FakeEmb:
    @staticmethod
    def embed_query(t):
        return [0.0] * 768


class _FakeNoop:
    @staticmethod
    def maybe_log_search(*a, **k):
        return None

    @staticmethod
    def resolve_arm_weights_for_live(*a, **k):
        return None


class _FakeDriver:
    def verify_connectivity(self):
        pass

    def close(self):
        pass


def _fake_load(name):
    if name == "embeddings":
        return _FakeEmb
    if name in ("retrieval_log", "learned_fusion"):
        return _FakeNoop
    return _FakeRetrievalMod


_CAP: dict = {}


def _run_cmd(C, fn, ns):
    """Call a cmd_* with fakes wired; return (clamped_capture, stderr_text)."""
    _CAP.clear()
    orig_load, orig_driver = C._load, C._driver
    C._load = _fake_load
    C._driver = lambda: _FakeDriver()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            fn(ns)
    finally:
        C._load, C._driver = orig_load, orig_driver
    return dict(_CAP), err.getvalue()


def gate_cli_clamp_message(C, B) -> None:
    print("\n-- CLI clamps to the mcp_bounds max AND prints a clear message on out-of-range --")
    cap, err = _run_cmd(C, C.cmd_search, argparse.Namespace(query="q", top_k=999))
    check("search clamps 999 -> TOP_K_MAX(50) into the retriever", cap.get("top_k") == B.TOP_K_MAX, str(cap.get("top_k")))
    check("search prints a clear clamp message (stderr)", "top-k" in err and "50" in err, err.strip())

    cap, err = _run_cmd(C, C.cmd_impact, argparse.Namespace(name="x", limit=-5, edges=None, max_hops=None))
    check("impact clamps -5 -> 1 into the retriever", cap.get("limit") == 1, str(cap.get("limit")))
    check("impact prints a clear clamp message (stderr)", "limit" in err and "1" in err, err.strip())

    cap, err = _run_cmd(C, C.cmd_search, argparse.Namespace(query="q", top_k=B.TOP_K_DEFAULT))
    check("in-range top_k passes through unchanged", cap.get("top_k") == B.TOP_K_DEFAULT, str(cap.get("top_k")))
    check("in-range top_k is SILENT (no message — default behaviour unchanged)", err.strip() == "", err.strip())


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — CLI/MCP result-count bounds gates (single source of truth; no Neo4j)")
    print("=" * 68)
    B = _load("mcp_bounds")
    C = _load("deepmap_cli")

    gate_constants(B)
    gate_clamp_int(B)
    gate_source_parity(B)
    gate_cli_clamp_message(C, B)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
