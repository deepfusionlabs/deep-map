#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the empty/whitespace-query guard (P2, Rails field-review).

THE ISSUE (0.23.1): neither the MCP `deepmap_search`/`deepmap_impact` handlers nor the CLI
`search`/`impact` commands rejected an empty or whitespace-only query — the vector arms would then
embed an empty string and return nearest neighbours of a ~zero vector (confident junk). The fix
rejects a contentless query AT THE BOUNDARY (MCP handlers + CLI), before the byte-frozen retriever
is ever reached — so the ranking path never sees a contentless query and `retrieval.py` is untouched.

These gates lock: (1) CLI `cmd_search`/`cmd_impact` short-circuit an empty/whitespace input WITHOUT
opening a Neo4j driver (behavioural — `_driver` is stubbed to raise if called); (2) both MCP handlers
carry the boundary guard (source). Pure + hermetic (no Neo4j, no spend).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
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


def _load_cli(base: Path):
    spec = importlib.util.spec_from_file_location("dm_deepmap_cli_eq", base / "deepmap_cli.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _boom(*a, **k):
    raise AssertionError("a Neo4j driver was opened for a contentless query (guard failed to fire)")


def _call(CLI, fn, args):
    """Invoke a cmd_* handler with `_driver`/`_load` stubbed to raise if reached; return (rc, out)."""
    saved = {"_driver": CLI._driver, "_load": CLI._load}
    out = io.StringIO()
    rc = raised = None
    try:
        CLI._driver = _boom
        CLI._load = lambda name: types.SimpleNamespace(
            embed_query=lambda q: [0.0],
            DeepMapRetriever=lambda *a, **k: types.SimpleNamespace(search=_boom),
            query_impact=_boom,
        )
        with contextlib.redirect_stdout(out):
            rc = fn(args)
    except BaseException as exc:  # noqa: BLE001 — we WANT to see a guard-miss surface
        raised = exc
    finally:
        CLI._driver = saved["_driver"]
        CLI._load = saved["_load"]
    return rc, out.getvalue(), raised


def gate_cli(CLI) -> None:
    print("\n-- CLI: empty/whitespace query|symbol short-circuits BEFORE any Neo4j driver --")
    for q in ("", "   ", "\t\n"):
        rc, out, raised = _call(CLI, CLI.cmd_search, types.SimpleNamespace(query=q, top_k=10))
        check(f"cmd_search({q!r}) returns 0, no driver opened", rc == 0 and raised is None,
              f"rc={rc!r} raised={type(raised).__name__ if raised else None}")
        check(f"cmd_search({q!r}) says the query was empty", "empty" in out.lower())
    for s in ("", "   "):
        rc, out, raised = _call(CLI, CLI.cmd_impact,
                                types.SimpleNamespace(name=s, limit=30, edges=None, max_hops=None))
        check(f"cmd_impact({s!r}) returns 0, no driver opened", rc == 0 and raised is None,
              f"rc={rc!r} raised={type(raised).__name__ if raised else None}")
        check(f"cmd_impact({s!r}) says the symbol was empty", "empty" in out.lower())
    # A REAL query must NOT be short-circuited — it must reach the (stubbed) driver, i.e. _boom fires.
    rc, _out, raised = _call(CLI, CLI.cmd_search, types.SimpleNamespace(query="auth flow", top_k=5))
    check("a non-empty query is NOT short-circuited (reaches the driver)",
          isinstance(raised, AssertionError))


def gate_mcp_source() -> None:
    print("\n-- MCP handlers carry the boundary guard (source) --")
    src = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    # v0.28: an empty query/symbol is an MCP ERROR (isError), not `[]` — an empty list was
    # indistinguishable from "no match" for the agent (2026-09 live validation).
    check("deepmap_search rejects an empty/whitespace query as an error",
          "not query or not query.strip()" in src and 'raise ValueError("empty query' in src)
    check("deepmap_impact rejects an empty/whitespace symbol as an error",
          "not symbol or not symbol.strip()" in src and 'raise ValueError("empty symbol' in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — empty-query boundary-guard gates (no Neo4j)")
    print("=" * 64)
    CLI = _load_cli(BUILD)
    gate_cli(CLI)
    gate_mcp_source()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
