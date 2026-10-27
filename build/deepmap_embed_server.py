#!/usr/bin/env python3
"""DeepMap MCP embed server (C8) — the query-embedding tool.

Implements the frozen MCP contract (this module is where it lives):
    deepmap_embed(text: str) -> list[float]   # 768-d, "search_query: " prefix, lazy model load

Thin wrapper over build/embeddings.embed_query. The model lazy-loads (~3s) on the first call — and,
since v0.28.0, is pre-warmed in the background at server start (DEEPMAP_MCP_WARMUP=0 opts out). The
tool body runs in a worker thread under a per-call deadline (mcp_bounds.bounded_tool), and text is
capped at mcp_bounds.QUERY_MAX_CHARS — the same cap deepmap_search applies to a query. Started with
DEEPMAP_PROJECT_ROOT / DEEPMAP_PROJECT_KEY (provision injects them), the tool description and the MCP
instructions name the project this server pair serves (E4); without them they are unchanged.

Transport (env DEEPMAP_MCP_TRANSPORT, default ``stdio``): ``streamable-http`` is the
Windows-safe path — listens on DEEPMAP_MCP_HOST:DEEPMAP_EMBED_PORT (127.0.0.1:8788) at /mcp.
See build/_mcp_transport.py.

Run as an MCP server:
    python build/deepmap_embed_server.py                       # stdio
    DEEPMAP_MCP_TRANSPORT=streamable-http python build/deepmap_embed_server.py   # HTTP
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mcp_bounds  # noqa: E402 — the query-length cap + time bounds, shared with the search server

mcp = FastMCP(
    "deepmap-embed",
    # E4: name the project this server pair serves (None without the project env).
    instructions=mcp_bounds.project_instructions("embed"),
    host=os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("DEEPMAP_EMBED_PORT", "8788")),
    stateless_http=True,
    json_response=True,
)


def _warm_model() -> None:
    """Cold-start work deepmap_embed cannot do without: the model. The embed tool has no degraded mode, so
    an unavailable model is the tool error — but a clear one, and a FAST one: a load already in progress is
    waited on only until it is warmup_timeout_s() old, and a failed load is re-tried at most every
    model_retry_s() instead of on every call (an HF network timeout used to cost each call the whole load)."""
    import embeddings
    try:
        embeddings.ensure_model(max_wait_s=mcp_bounds.warmup_timeout_s(), retry_after_s=mcp_bounds.model_retry_s())
    except Exception as exc:
        short = getattr(embeddings, "short_error", lambda e: f"{type(e).__name__}: {e}")
        why = str(exc) if isinstance(exc, getattr(embeddings, "ModelUnavailable", ())) else (
            f"the embedding model could not be loaded ({short(exc)})")
        raise RuntimeError(f"DeepMap's embedding model is unavailable: {why}. Run /deep-map:doctor "
                           "(deepmap_search still answers keyword-only).") from exc


def _warm_up() -> None:
    """Pre-load the model in the background at server start (best-effort; the first call retries)."""
    try:
        import embeddings
        embeddings.embed_query("warm-up")          # the load plus the one-off cost of a first encode
    except Exception as exc:  # noqa: BLE001
        print(f"[deepmap-embed] warm-up: embedding model not loaded yet ({type(exc).__name__}: {exc}); "
              "the first call will retry.", file=sys.stderr)


@mcp_bounds.bounded_tool(mcp, warm=_warm_model,
                         describe=mcp_bounds.project_tool_describer("DeepMap query embedder serving project",
                                                                    answers_about_project=False))
def deepmap_embed(text: str) -> list[float]:
    """Embed text as a 768-d query vector (nomic-ai/modernbert-embed-base, general-text),
    using the asymmetric 'search_query: ' prefix. For pairing with deepmap_search. Text longer than
    2,000 characters is truncated (the same cap deepmap_search applies to a query)."""
    import embeddings  # build/embeddings.py
    text, capped = mcp_bounds.cap_query(text)
    if capped:
        print(f"[deepmap-embed] {capped}", file=sys.stderr)
    return embeddings.embed_query(text)


if __name__ == "__main__":
    import _mcp_transport
    # Fast startup: serve immediately and load the model in the background (DEEPMAP_MCP_WARMUP=0 = lazy).
    if os.environ.get("DEEPMAP_MCP_WARMUP", "1").strip() != "0":
        threading.Thread(target=_warm_up, name="deepmap-embed-warm-up", daemon=True).start()
    _mcp_transport.run(mcp)
