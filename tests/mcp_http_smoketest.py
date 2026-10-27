#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Out-of-band smoke test for the DeepMap MCP servers over streamable-HTTP.

WHY THIS EXISTS: the mcp-SDK **stdio** reader hangs under Claude Code on Windows (a trivial
ping tool hung identically — not a DeepMap bug; see CLAUDE.md). The
fix is to run the servers over **streamable-HTTP**. This test drives a running HTTP server with
the *same MCP client protocol Claude Code uses* (mcp.client.streamable_http) — proving the tool
answers without hanging — and it is wrapped in a hard asyncio timeout so it can never itself
hang a caller.

Usage (server must already be running on the URL):
    DEEPMAP_MCP_TRANSPORT=streamable-http NEO4J_PASSWORD=deepmap \
        python build/deepmap_search_server.py &
    python tests/mcp_http_smoketest.py --url http://127.0.0.1:8787/mcp \
        --tool deepmap_search --query "fuse ranked search results"

Exit 0 = the tool responded with content; non-zero = failure/timeout. Designed to be run under
an OS-level `timeout` as a second belt (the asyncio deadline is the suspenders).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

DEFAULT_URL = "http://127.0.0.1:8787/mcp"
DEFAULT_DEADLINE = 90.0   # seconds; covers a cold model load (~3s) with wide margin


async def _probe(url: str, tool: str, query: str, top_k: int) -> dict:
    """Connect, initialize, list tools, call `tool`, return a small result summary dict."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]
            if tool not in names:
                raise RuntimeError(f"tool {tool!r} not advertised; server offers {names}")
            args = {"query": query}
            if tool == "deepmap_search":
                args["top_k"] = top_k
            elif tool == "deepmap_embed":
                args = {"text": query}
            elif tool == "deepmap_impact":
                args = {"symbol": query, "limit": top_k}
            elif tool == "deepmap_cypher":
                args = {"query": query, "limit": top_k}
            result = await session.call_tool(tool, args)
            if result.isError:
                raise RuntimeError(f"tool call returned isError: {result.content}")
            # Prefer structured content; fall back to the first text block.
            payload = result.structuredContent
            if payload is None and result.content:
                block = result.content[0]
                text = getattr(block, "text", None)
                payload = json.loads(text) if text else None
            return {"tools": names, "payload": payload}


def _leaf_errors(e: BaseException) -> str:
    """'Type: message' of every leaf of a (possibly nested) exception group, '; '-joined."""
    subs = getattr(e, "exceptions", None)
    if isinstance(subs, (list, tuple)) and subs:
        return "; ".join(_leaf_errors(s) for s in subs)
    return f"{type(e).__name__}: {e}"


async def _run(args) -> int:
    try:
        summary = await asyncio.wait_for(
            _probe(args.url, args.tool, args.query, args.top_k), timeout=args.deadline
        )
    except asyncio.TimeoutError:
        print(f"FAIL: probe exceeded {args.deadline}s deadline (server hung or unreachable)",
              file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — smoke test: report any failure plainly
        # The MCP client runs inside anyio task groups, so a tool's isError (raised in _probe) arrives
        # wrapped in an ExceptionGroup whose own message is only "unhandled errors in a TaskGroup" —
        # report the LEAF errors: provision.verify_mcp words its verdict from this text (a cold start's
        # "still starting up", an unavailable embedding model).
        print(f"FAIL: {_leaf_errors(e)}", file=sys.stderr)
        return 1

    payload = summary["payload"]
    print(f"PASS: {args.tool} responded over HTTP; tools advertised = {summary['tools']}")
    if args.tool == "deepmap_search":
        rows = payload.get("result") if isinstance(payload, dict) else payload
        rows = rows or []
        # A {"note": ...} entry is not a result: the server's own caveat (e.g. keyword-only when the
        # embedding model is unavailable). Print each as `NOTE: ...` — provision.verify_mcp reads it.
        notes = [r["note"] for r in rows if isinstance(r, dict) and r.get("note")]
        rows = [r for r in rows if not (isinstance(r, dict) and r.get("note"))]
        print(f"  {len(rows)} result(s):")
        for r in rows[:5]:
            print(f"    - {r.get('name')} ({r.get('type')})  {r.get('file')}:{r.get('line')}")
        for n in notes:
            print(f"  NOTE: {n}")
        if not rows:
            print("  WARNING: zero results (empty graph?) — transport OK but check the data",
                  file=sys.stderr)
    elif args.tool == "deepmap_embed":
        vec = payload.get("result") if isinstance(payload, dict) else payload
        n = len(vec) if isinstance(vec, list) else 0
        print(f"  embedding dim = {n} (expected 768)")
        if n != 768:
            return 3
    elif args.tool == "deepmap_impact":
        rows = payload.get("result") if isinstance(payload, dict) else payload
        rows = rows or []
        print(f"  {len(rows)} caller(s):")
        for r in rows[:5]:
            print(f"    - [{r.get('confidence')}] {r.get('caller')} -> {r.get('callee')}  "
                  f"{r.get('file')}:{r.get('line')}")
    elif args.tool == "deepmap_cypher":
        rows = payload.get("result") if isinstance(payload, dict) else payload
        rows = rows or []
        notes = [r["note"] for r in rows if isinstance(r, dict) and r.get("note")]
        rows = [r for r in rows if not (isinstance(r, dict) and r.get("note"))]
        print(f"  {len(rows)} row(s):")
        for r in rows[:5]:
            print(f"    - {json.dumps(r, ensure_ascii=False, default=str)[:200]}")
        for n in notes:
            print(f"  NOTE: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--tool", default="deepmap_search")
    ap.add_argument("--query", default="fuse the ranked results from multiple search arms")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    args = ap.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
