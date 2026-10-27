#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared MCP transport selection for the DeepMap servers (C8/C9).

One source of truth for ``DEEPMAP_MCP_TRANSPORT`` so the search and embed servers behave
identically. Default ``stdio`` preserves the documented contract and non-Windows installs;
``streamable-http`` is the Windows-safe path (the mcp-SDK stdio reader hangs under Claude Code
on Windows — see CLAUDE.md).
"""
from __future__ import annotations

import os

# Accepted aliases -> the canonical FastMCP transport name.
_ALIASES = {
    "stdio": "stdio",
    "http": "streamable-http",
    "streamable-http": "streamable-http",
    "streamable_http": "streamable-http",
    "streamablehttp": "streamable-http",
    "sse": "sse",
}


def resolve_transport(env: dict | None = None) -> str:
    """Resolve DEEPMAP_MCP_TRANSPORT to a canonical FastMCP transport name.

    Unknown values fall back to ``stdio`` (the safe documented default) rather than crashing.
    Pure: pass ``env`` to unit-test without touching the process environment.
    """
    env = os.environ if env is None else env
    raw = str(env.get("DEEPMAP_MCP_TRANSPORT", "stdio")).strip().lower()
    return _ALIASES.get(raw, "stdio")


def run(mcp, *, env: dict | None = None) -> None:
    """Run a FastMCP server on the env-selected transport."""
    mcp.run(transport=resolve_transport(env))
