#!/usr/bin/env python3
"""DeepMap MCP search server (C9) — the fused-hybrid-search tool Claude Code calls.

Implements the frozen MCP contract (this module is where it lives):
    deepmap_search(query: str, top_k: int <= 50 = 15) -> list[dict]

Wraps build/retrieval.py's DeepMapRetriever (the 3-arm Weighted-RRF retriever) with the real
query embedder (build/embeddings.embed_query). Enforces the top_k <= 50 clamp AT THE MCP
BOUNDARY — the retriever itself does not (the contract lives here). Lazy-loads
the Neo4j driver + model so import is cheap.

Additive tools on the same server: deepmap_impact (reverse reachability) and deepmap_cypher (read-only Cypher for
ad-hoc structure questions — build/cypher_readonly.py validates the query before it reaches Neo4j, runs it in a READ
transaction that is always rolled back, and caps rows, bytes and time; the CLI's `deepmap cypher` is its twin).

Config via env: NEO4J_URI (default bolt://127.0.0.1:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD
(deepmap — note Neo4j's default 8-char minimum must be relaxed for this; the neo4j.conf
block build/provision.py writes relaxes it). An explicit NEO4J_URI (the operator's, or the per-project one provision
injects) is dialled verbatim; the default is port_registry.bolt_uri(BASE_BOLT) — 127.0.0.1, the address every
DeepMap instance binds, never `localhost` (round 8, J4: on Windows `localhost` tries ::1 first, which cost ~2 s
per new Bolt connection and could reach another program listening on [::1]).

Transport (env DEEPMAP_MCP_TRANSPORT, default ``stdio``):
  - ``stdio``           — the documented default; Claude Code spawns the process per session.
  - ``streamable-http`` — long-running HTTP server (the **Windows-safe path**: the mcp-SDK
                          stdio reader hangs under Claude Code on Windows — see
                          build/_mcp_transport.py / CLAUDE.md). Listens on
                          DEEPMAP_MCP_HOST:DEEPMAP_SEARCH_PORT (127.0.0.1:8787) at /mcp.
                          stateless + JSON responses ⇒ no session bookkeeping, robust to
                          client reconnects; the heavy retriever still loads once per process.

Run as an MCP server:
    python build/deepmap_search_server.py                       # stdio
    DEEPMAP_MCP_TRANSPORT=streamable-http python build/deepmap_search_server.py   # HTTP

Concurrency + time bounds (v0.28.0): each tool body runs in a worker thread under a per-call deadline
(mcp_bounds.bounded_tool), so one slow query or a hung Neo4j no longer blocks every other caller; the
model and driver are pre-warmed in the background at server start (DEEPMAP_MCP_WARMUP=0 opts out).

Degradation (T10): the Neo4j driver is REQUIRED (down -> the call fails loud); the embedding model is
NOT — if it is missing, failed to load, or is still loading past the warm-up budget, deepmap_search
answers from BM25 fulltext alone and says so in a trailing {"note": ...} entry. A failed load is retried
at most every DEEPMAP_MCP_MODEL_RETRY_S (300 s), so a broken ML stack never costs each call a reload.

Project guard (v0.28.0, D3): provision starts this server with DEEPMAP_PROJECT_ROOT / DEEPMAP_PROJECT_KEY.
On the first call of an MCP session whose client offers the `roots` capability, the server asks for the
session's roots and refuses (isError) when none of them is this project — so a session in another
project can't silently get this project's answers. It FAILS OPEN when the client offers no roots, the
request fails or times out (DEEPMAP_MCP_ROOTS_TIMEOUT_S, 3 s), or the env is absent;
DEEPMAP_PROJECT_GUARD=0 turns it off. Over the stateless streamable-HTTP transport a tool call cannot
see the client's capabilities (each request is its own session), so the guard fails open there.

Project label (v0.28.0, E4): because the deployed transport hides the client's roots, the server also NAMES
its project where every agent reads it at zero per-call cost — each tool description starts with "Searches
the DeepMap graph of project <name> (<path>)" (impact: "Traces dependents in ...") and the MCP
`instructions` say the same, with what to do when that is not the agent's project. Without the project env
the descriptions stay the docstrings and there are no instructions (mcp_bounds.project_tool_describer).

Auth backoff (v0.28.0, E4): after Neo4j REJECTS the server's login (wrong password, another program's Neo4j,
the login lock), calls are answered with that diagnosis WITHOUT a new login for DEEPMAP_MCP_AUTH_BACKOFF_S
(10 s), doubling per consecutive rejection up to DEEPMAP_MCP_AUTH_BACKOFF_MAX_S (120 s) — so a
misconfigured server can't keep Neo4j's login lock engaged against sync and the CLI. An outage is never
backed off (fail loud, re-tried on every call).

A restarted Neo4j (2026-10-23): the driver checks each pooled connection is alive before handing it out
(NEO4J_LIVENESS_CHECK_S), and a call that still meets a dead pooled connection is retried ONCE on a fresh one
(_once_more_on_dead_connection) — so the first call after `provision.py stop` + heal (or an upgrade) under the
running servers answers instead of failing with "Neo4j is not reachable". A failed connect, an auth rejection or
a query error is never retried.
"""
from __future__ import annotations

import copy
import importlib.util
import math
import os
import re
import sys
import threading
import weakref
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The search/impact bounds live in ONE module (mcp_bounds) shared with the CLI, so the frozen MCP clamp
# (top_k <= 50 / impact limit <= 100), the query-length cap and the time limits can never drift apart.
import mcp_bounds  # noqa: E402
from mcp_bounds import TOP_K_MAX, TOP_K_DEFAULT, IMPACT_LIMIT_MAX, IMPACT_LIMIT_DEFAULT  # noqa: E402
# deepmap_cypher's validator, caps and result shape: ONE module shared with the CLI's `deepmap cypher` (stdlib only).
import cypher_readonly  # noqa: E402

# Default port 8787 (search); the embed server uses 8788. Override via env for parallel installs.
mcp = FastMCP(
    "deepmap-search",
    # E4: name the project this server indexes in the initialize result (None without the project env).
    instructions=mcp_bounds.project_instructions("search"),
    host=os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("DEEPMAP_SEARCH_PORT", "8787")),
    stateless_http=True,    # each request independent — no Mcp-Session-Id handshake to keep alive
    json_response=True,     # plain application/json responses (no long-lived SSE stream)
)

def _default_neo4j_uri() -> str:
    """The URI dialled when NEO4J_URI is unset: port_registry.bolt_uri(BASE_BOLT), i.e. bolt://127.0.0.1:7687
    (round 8, J4 — the one spelling of a DeepMap-generated bolt URI). Never raises: if port_registry cannot be
    used, the same 127.0.0.1 URI is spelled out here."""
    try:
        import port_registry as _pr
        return _pr.bolt_uri(_pr.BASE_BOLT)
    except Exception:  # noqa: BLE001
        return "bolt://127.0.0.1:7687"


def _neo4j_uri() -> str:
    """NEO4J_URI VERBATIM when set (an operator's endpoint, or the per-project URI provision injects — never
    rewritten); the 127.0.0.1 default when it is unset or empty."""
    return os.environ.get("NEO4J_URI") or _default_neo4j_uri()


# Seconds a pooled Bolt connection may sit idle before the driver checks it is alive (a RESET round trip) before
# handing it out. After the project's Neo4j is restarted under this long-lived server (provision.py stop + heal, a
# patch upgrade, a crash and heal) every pooled connection is dead; without the check (the driver's default) one was
# handed out, so the first call after a restart failed with "Neo4j is not reachable ... probably stopped" while Neo4j
# was up. Checked, a dead connection is closed and a fresh one opened. Why 2 s, not 0: a restart takes far longer
# (stop + heal: 27 s measured; a JVM start alone ~10 s), so every connection a restart killed has been idle longer
# than this and is checked; and a call's back-to-back sessions (a search opens dozens) reuse a connection idle for
# milliseconds without a check — at 0 every session paid the round trip (+0.74 ms each, measured), at 2 s a call
# pays it at most once.
NEO4J_LIVENESS_CHECK_S = 2.0

_retriever = None
_retriever_lock = threading.Lock()  # guard the lazy init in the long-lived, concurrent server
# E4: after a REJECTED login, hold off new logins (Neo4j locks the user after repeated failures, which would
# lock out sync and the CLI too). Shared by the lazy init below and the tool wrappers (bounded_tool backoff=).
_AUTH = mcp_bounds.AuthBackoff(f"Neo4j at {_neo4j_uri()}")


def _get_retriever():
    """Lazy-init: Neo4j driver + DeepMapRetriever wired to the real query embedder.

    Double-checked locking: without it, two concurrent first-requests both pass the
    ``is None`` check and each open a Neo4j driver — the second is never closed, a real
    connection/fd leak in a server that lives for the session (audit #2).
    """
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                # Inside the lock: concurrent first calls queue here, so after one rejected login the rest
                # get the backoff diagnosis instead of each logging in again (E4).
                _AUTH.check()
                from neo4j import GraphDatabase
                import embeddings  # build/embeddings.py
                spec = importlib.util.spec_from_file_location("dm_retrieval", HERE / "retrieval.py")
                R = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(R)
                driver = GraphDatabase.driver(
                    _neo4j_uri(),
                    auth=(os.environ.get("NEO4J_USER", "neo4j"),
                          os.environ.get("NEO4J_PASSWORD", "deepmap")),
                    notifications_min_severity="OFF",  # silence the benign graph-arm count() warning
                    liveness_check_timeout=NEO4J_LIVENESS_CHECK_S,   # a restarted Neo4j: dead pooled connections
                    **mcp_bounds.neo4j_driver_timeouts())  # a dead/frozen Neo4j fails a connect in bounded time
                try:
                    driver.verify_connectivity()
                    # embed_fn = the degrade-aware wrapper: a model that isn't ready makes THIS call
                    # keyword-only (the retriever's _embed_or_none), never an error or a long wait.
                    _retriever = R.DeepMapRetriever(driver, embed_fn=_search_embed)
                except BaseException as exc:
                    # If connectivity/construction fails, close the driver so a failed first call
                    # doesn't leak a connection pool (the long-lived server retries on the next call).
                    driver.close()
                    if isinstance(exc, Exception):
                        _AUTH.record(exc)   # a rejected login opens the backoff window (no-op otherwise)
                    raise
    return _retriever


def _warm_retriever() -> None:
    """REQUIRED cold-start work (deepmap_search + deepmap_impact): the Neo4j driver + retriever. A failure
    here is the tool error — an unreachable instance must never look like an empty answer (T10)."""
    _get_retriever()


# ---- a dead pooled connection after a Neo4j restart: one retry on a fresh connection -------------------------------
# The liveness check above replaces a dead IDLE connection before it is handed out; this is the backstop for one that
# still reaches a query (it died after the check, or a driver without the check). The driver words such a failure
# with these phrases — on ServiceUnavailable (a bolt:// driver) or SessionExpired (neo4j://) — and, on it, closes every
# idle connection to that server, so ONE retry runs on a fresh connection. A failed CONNECT ("Couldn't connect" /
# "Failed to establish connection": Neo4j is down — the outage answer is right, and a refused connect costs ~2 s on
# Windows), an auth rejection (Neo4j's login lock), a query error and anything else are never retried, and nothing is
# retried twice. Every tool is a read, so a retry can't double a write.
_DEAD_CONNECTION_SIGNS = ("defunct connection", "failed to write data to connection", "closed connection")
_DEAD_CONNECTION_TYPES = ("ServiceUnavailable", "SessionExpired")


def _dead_pooled_connection(exc) -> bool:
    """True iff `exc` (or what it was raised from) is the driver reporting that a connection it had OPEN died
    (ServiceUnavailable / SessionExpired worded as a defunct or closed connection, or a failed write); False for a
    failed connect, an auth rejection, a query error or anything else. Never raises."""
    try:
        if mcp_bounds.is_neo4j_auth_error(exc) or cypher_readonly.query_error_message(exc) is not None:
            return False
        exc_mod = sys.modules.get("neo4j.exceptions")   # only when the driver is loaded: no import on this path
        kinds = tuple(k for k in (getattr(exc_mod, n, None) for n in _DEAD_CONNECTION_TYPES) if isinstance(k, type))
        seen, e = set(), exc
        while e is not None and id(e) not in seen and len(seen) < 8:
            seen.add(id(e))
            if (kinds and isinstance(e, kinds)) or type(e).__name__ in _DEAD_CONNECTION_TYPES:
                text = " ".join(str(getattr(e, "message", None) or e).split()).lower()
                return any(sign in text for sign in _DEAD_CONNECTION_SIGNS)
            e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    except Exception:  # noqa: BLE001
        pass
    return False


def _once_more_on_dead_connection(op, what: str):
    """op() — and, when it fails on a dead pooled connection (_dead_pooled_connection), op() ONCE more: the driver has
    dropped its idle connections by then, so the retry runs on a fresh one. The retry's own failure propagates."""
    try:
        return op()
    except Exception as exc:
        if not _dead_pooled_connection(exc):
            raise
        print(f"[deepmap-search] {what}: a pooled Neo4j connection was dead ({type(exc).__name__}; Neo4j restarted "
              "under this server?) — retrying once on a fresh connection.", file=sys.stderr)
    return op()


def _warm_model() -> None:
    """OPTIONAL cold-start work (deepmap_search only): load the embedding model, waiting on a load already in
    progress only until it is warmup_timeout_s() old, and re-trying a failed load at most every
    model_retry_s(). A failure is logged by bounded_tool and the search degrades to keyword-only."""
    import embeddings
    embeddings.ensure_model(max_wait_s=mcp_bounds.warmup_timeout_s(), retry_after_s=mcp_bounds.model_retry_s())


# Why THIS call's query could not be embedded (None = it was): set by _search_embed, read by deepmap_search.
# Thread-local because each call's body runs in its own worker thread and the retriever is shared.
_embed_state = threading.local()


def _search_embed(text: str) -> list[float]:
    """The retriever's embed_fn. Never WAITS on a load in progress or RE-TRIES a failed one inside a query —
    the per-call warm-up already did both within its budget — so a model that still isn't ready makes the
    retriever skip the vector arms (FTS-only, T10) and the reason is recorded for the caller's note. (A
    direct, non-MCP call on a never-loaded model still loads it here, as before.)"""
    import embeddings
    unavailable = getattr(embeddings, "ModelUnavailable", ())
    short = getattr(embeddings, "short_error", lambda e: f"{type(e).__name__}: {e}")
    try:
        embeddings.ensure_model(max_wait_s=0, retry_after_s=math.inf)
    except Exception as exc:
        _embed_state.problem = (str(exc) if isinstance(exc, unavailable) else
                                f"the embedding model could not be loaded ({short(exc)})")
        raise
    try:
        return embeddings.embed_query(text)
    except Exception as exc:
        _embed_state.problem = f"the query could not be embedded ({short(exc)})"
        raise


def _degraded_note(problem: str) -> str:
    return (f"Keyword-only results: vector search is unavailable ({problem}), so these are BM25 matches on "
            "literal words and code that is described differently may be missing. Run /deep-map:doctor.")


def _touch_last_used() -> None:
    """Stamp this project's registry `last_used` — the idle-hibernation signal (v0.27): provision.hibernate_idle
    stops another project's running instance once that stamp is 30 min old. BOTH tools call it after an
    answered call, as CLI search and impact do (ISS-build:021: deepmap_impact used to skip it, so a session that
    only traced blast radius looked idle and could have its Neo4j stopped mid-session). This server's cwd is the
    plugin repo, not the user project, so the project is found by the injected NEO4J_URI's BOLT port
    (port_registry.touch_last_used_by_bolt: throttled, never creates an entry). Best-effort; never raises."""
    try:
        import port_registry as _pr
        _pr.touch_last_used_by_bolt(int(os.environ.get("NEO4J_URI", "").rsplit(":", 1)[-1]))
    except Exception:
        pass


# ---- D3: the project guard (fail-open) --------------------------------------------------------------
_guard_verdicts: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # ServerSession -> '' | refusal


def _root_path(uri) -> "str | None":
    """A local filesystem path for a `file:` root URI (None for any other scheme / a remote host)."""
    from urllib.parse import unquote, urlparse
    u = urlparse(str(uri))
    if u.scheme.lower() != "file":
        return None
    path = unquote(u.path or "")
    host = (u.netloc or "").lower()
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        if host and host != "localhost":
            path = f"//{u.netloc}{path}"
        path = path.replace("/", os.sep)
    elif host and host != "localhost":
        return None
    return path or None


def _same_or_nested(a: str, b: str) -> bool:
    """a and b name one project: the same resolved path, or one contains the other. Never raises."""
    try:
        pa = Path(os.path.normcase(str(Path(a).resolve())))
        pb = Path(os.path.normcase(str(Path(b).resolve())))
        return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)
    except Exception:  # noqa: BLE001
        return False


def _roots_problem(paths: list, root: str, key: str) -> str:
    """'' when any session root is this server's project; otherwise the refusal message. Blocking (git)."""
    import port_registry as pr
    idents = []
    for p in paths:
        ident = pr.project_identity(p, timeout=5)
        if root and _same_or_nested(ident, root):
            return ""
        if key and pr.project_key(p, timeout=5) == key:
            return ""
        idents.append(ident)
    here = idents[0]
    mine = root or f"the project with key {key}"
    try:
        own = pr.lookup_ports(here)
    except Exception:  # noqa: BLE001 — an unreadable registry: give the generic advice
        own = None
    if own:
        return (f"This DeepMap server indexes {mine}, but your session is in {here}, which has its own "
                f"DeepMap server (port {own[0]}) — reload Claude Code (or reconnect with /mcp) so {here}'s "
                "own .mcp.json is used.")
    return (f"This DeepMap server indexes {mine}; your session is in {here}, which has no DeepMap index of "
            f"its own — run /deep-map:setup in {here} (or reload Claude Code so its own .mcp.json is used).")


async def _session_verdict(session, root: str, key: str) -> str:
    """Ask the client for its roots (bounded) and judge them. FAILS OPEN ('') on anything but a clear
    mismatch: no roots capability (always the case over stateless HTTP), no file roots, an error, a timeout."""
    import anyio
    from mcp import types as mt
    try:
        if not session.check_client_capability(mt.ClientCapabilities(roots=mt.RootsCapability())):
            return ""
        with anyio.fail_after(mcp_bounds.roots_timeout_s()):
            listed = await session.list_roots()
        paths = [p for p in (_root_path(getattr(r, "uri", "")) for r in (listed.roots or [])) if p]
        if not paths:
            return ""
        with anyio.fail_after(30):
            return await anyio.to_thread.run_sync(_roots_problem, paths, root, key, abandon_on_cancel=True)
    except Exception as exc:  # noqa: BLE001 — never block a call on the guard's own failure
        print(f"[deepmap-search] project guard skipped ({type(exc).__name__}: {exc}); serving.",
              file=sys.stderr)
        return ""


async def _project_guard() -> "str | None":
    """bounded_tool's guard for deepmap_search / deepmap_impact: the refusal message, or None to serve."""
    root = os.environ.get("DEEPMAP_PROJECT_ROOT", "").strip()
    key = os.environ.get("DEEPMAP_PROJECT_KEY", "").strip()
    if not (root or key) or os.environ.get("DEEPMAP_PROJECT_GUARD", "1").strip() == "0":
        return None
    try:
        session = mcp.get_context().request_context.session
    except Exception:  # noqa: BLE001 — not inside an MCP request (a direct call): nothing to check
        return None
    try:
        verdict = _guard_verdicts.get(session)
    except TypeError:
        verdict = None
    if verdict is None:
        verdict = await _session_verdict(session, root, key)
        try:
            _guard_verdicts[session] = verdict        # judged once per MCP session
        except TypeError:
            pass
    return verdict or None


def _warm_up() -> None:
    """Pre-load the embedding model and open the Neo4j driver in the background at server start, so the
    first call does not pay the cold start (100-170 s seen on a loaded machine, past setup's MCP verify).
    Best-effort: a failure (e.g. Neo4j not up yet) is logged and the first call retries the lazy init."""
    try:
        import embeddings
        embeddings.embed_query("warm-up")          # the load plus the one-off cost of a first encode
    except Exception as exc:  # noqa: BLE001
        print(f"[deepmap-search] warm-up: embedding model not loaded yet ({type(exc).__name__}: {exc}); "
              "the first call will retry.", file=sys.stderr)
    try:
        _get_retriever()
    except Exception as exc:  # noqa: BLE001
        print(f"[deepmap-search] warm-up: Neo4j not reachable yet ({type(exc).__name__}: {exc}); "
              "the first call will retry.", file=sys.stderr)


@mcp_bounds.bounded_tool(mcp, warm=_warm_retriever, soft_warm=_warm_model, guard=_project_guard, backoff=_AUTH,
                         describe=mcp_bounds.project_tool_describer("Searches the DeepMap graph"))
def deepmap_search(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict]:
    """Search THIS project's code by what it DOES — intent and behaviour, not literal tokens.

    USE THIS INSTEAD OF grep/Glob when the exact identifier or string is unknown: "where is auth
    handled", "what computes the fused score", "code that retries failed uploads" — it matches
    meaning (fused vector + BM25 + graph-weighted search over the project's code graph), so it
    finds the implementation even when no token in the query appears in the code. grep stays right
    when you already know the exact token; they compose — deepmap_search to find the entry point,
    then read/grep to trace details. For who-calls / blast-radius questions use deepmap_impact.
    Returns ranked entities {id, name, type, file, line, summary, confidence, score} across
    Function/Method/Class/Module/Route; `confidence` is the node's extraction confidence
    (EXTRACTED for parsed nodes). A query longer than 2,000 characters is truncated (put the key
    terms first) and a top_k over 50 is clamped; an empty query is an error. If the embedding model is
    unavailable the answer is keyword-only (BM25). Each of these ends the answer with a {"note": ...}
    entry saying so."""
    # Empty/whitespace-only query guard (P2): with no content the vector arms would embed an empty
    # string and return nearest neighbours of a ~zero vector — confident junk. Reject at the boundary
    # before touching the (byte-frozen) retriever so no ranking path ever sees a contentless query —
    # as an ERROR, so the caller can't mistake it for "nothing matched".
    if not query or not query.strip():
        raise ValueError("empty query — nothing to search (describe what the code does, "
                         "e.g. 'where is auth handled').")
    query, capped = mcp_bounds.cap_query(query)   # a 5,000-char query took 13-35 s to embed
    if capped:
        print(f"[deepmap-search] {capped}", file=sys.stderr)
    asked = int(top_k)
    top_k = min(max(1, int(top_k)), TOP_K_MAX)   # clamp at the boundary (contract)
    ret = _get_retriever()
    searcher = ret
    # RM-1 (gated, OFF by default): apply learned weights ONLY when the operator opted in
    # (DEEPMAP_APPLY_LEARNED) AND the gate passes (applied + beats_equal + significant). Default: the
    # env is unset -> the shared retriever searches as it is -> equal-weight ranking, byte-identical to today.
    # Applied, the weights go on a PER-CALL shallow copy: tool bodies run concurrently in worker threads, and
    # writing them onto the one shared retriever let a call be fused with another call's per-intent weights.
    try:
        import learned_fusion as _lf
        if _lf.apply_enabled():
            weights = _lf.resolve_arm_weights_for_live(query=query) or dict(_lf.EQUAL_WEIGHTS)
            searcher = copy.copy(ret)
            searcher.arm_weights = dict(weights)
    except Exception:
        searcher = ret
    _embed_state.problem = None
    results = _once_more_on_dead_connection(lambda: searcher.search(query, top_k=top_k), "deepmap_search")
    # RM-1: opt-in local retrieval logging (off by default; no-op unless DEEPMAP_RETRIEVAL_LOG is
    # set). Observes only — the returned ranking is unchanged. Never raises into the tool call.
    try:
        import retrieval_log as _rl
        _rl.maybe_log_search(searcher, query, results, top_k=top_k)
    except Exception:
        pass
    # v0.27: stamp this project's last_used (the idle-hibernation signal). Best-effort; never raises.
    _touch_last_used()
    # The model was unavailable, so the answer is BM25-only: say so (appended after the log above, which
    # records only real results). A truncated query and a clamped top_k are said too — both used to be
    # silent to the caller (the truncation only reached the server's stderr).
    notes = []
    problem = getattr(_embed_state, "problem", None)
    if problem:
        notes.append(_degraded_note(problem))
    if capped:
        notes.append(capped[:1].upper() + capped[1:])
    if top_k != asked:
        notes.append(f"top_k {asked} is out of range [1, {TOP_K_MAX}]; clamped to {top_k}.")
    if notes:
        results = list(results) + [{"note": n} for n in notes]
    return results


@mcp_bounds.bounded_tool(mcp, warm=_warm_retriever, guard=_project_guard, backoff=_AUTH,
                         describe=mcp_bounds.project_tool_describer("Traces dependents in the DeepMap graph"))
def deepmap_impact(symbol: str, limit: int = IMPACT_LIMIT_DEFAULT,
                   edge_types: list[str] | None = None) -> list[dict]:
    """What REACHES `symbol` — multi-hop reverse-reachability (refactor impact / blast radius) across
    :CALLS PLUS framework/runtime edges (routes, dependency injection, events, scheduled jobs,
    inheritance): the runtime ENTRY POINTS a grep or a plain call graph can't see. Use before
    refactoring, or for "what depends on / breaks if I change X". Returns dependents {caller,
    caller_type, file, line, confidence, callee, hop, via, via_path}: `hop` = shortest reverse distance,
    `via` = the edge type attaching the dependent (e.g. ROUTES_TO, CALLS), `via_path` = the edge types
    along the path, `confidence` = the WEAKEST edge on it (EXTRACTED / INFERRED / AMBIGUOUS — a name
    that maps to several symbols or a reflective hop: verify by reading). Matches `foo`, `.foo`,
    `Class.method`, `Class#method` or `Owner::method` (owner generics like `Foo<T>` are fine), and a
    module-qualified `module.foo` (the defining file's stem). A graph without runtime edges gets a
    multi-hop :CALLS walk.
    `edge_types` (optional) REPLACES the default walk — it is not added to it (CALLS is always kept).
    The default follows CALLS, ROUTES_TO, DEPENDS_ON, TRIGGERS, SCHEDULED_AS, HOOKS, INHERITS, CASCADES
    and RENDERS, so ["CALLS","HAS_MANY","REFERENCES"] DROPS all of those but CALLS. To ADD the ORM
    associations (belongs_to/has_many; not in the default) on a Rails/ORM app, pass the whole default
    list plus "HAS_MANY" and "REFERENCES".
    Trailing {"note": ...} entries keep a short answer honest: the symbol is not in the graph, or it has
    no resolved dependents (calls through a receiver, trait or namespace are often unresolved — verify
    by reading), the walk was truncated / reduced in depth, an edge type is unknown or absent from the
    graph, the limit was clamped, or — for a method — the dependents are a lower bound."""
    # Empty/whitespace-only symbol guard (P2): a contentless symbol matches nothing meaningful —
    # reject at the boundary (as an error) rather than issuing a `name = ''` traversal.
    if not symbol or not symbol.strip():
        raise ValueError("empty symbol — nothing to trace (provide a function/method/class name).")
    asked = int(limit)
    limit = min(max(1, int(limit)), IMPACT_LIMIT_MAX)
    rows = _once_more_on_dead_connection(
        lambda: _get_retriever().impact(symbol.strip(), limit=limit, edge_types=edge_types), "deepmap_impact")
    # Impact is a use, like search: stamp last_used so an impact-only session never looks idle (ISS-build:021).
    _touch_last_used()
    # The retriever's honesty notes (not in the graph / no resolved dependents / truncated / reduced
    # depth / an unknown edge type / a partial method answer) ride along as trailing {"note": ...} entries,
    # and so does a clamped limit (it was silent); a plain answer is returned unchanged.
    notes = list(getattr(rows, "notes", None) or [])
    if limit != asked:
        notes.append(f"limit {asked} is out of range [1, {IMPACT_LIMIT_MAX}]; clamped to {limit}.")
    return list(rows) + [{"note": n} for n in notes]


def _cypher_timeout_s() -> float:
    """The read timeout deepmap_cypher asks Neo4j for: cypher_readonly.timeout_s() (DEEPMAP_CYPHER_TIMEOUT_S), kept
    5 s inside the tool's own deadline, so Neo4j ends a runaway query itself before bounded_tool abandons the call's
    worker thread (an abandoned thread's query would otherwise keep running on the server)."""
    return max(1.0, min(cypher_readonly.timeout_s(), mcp_bounds.tool_timeout_s() - 5.0))


# No warm=: the query is validated BEFORE the driver is asked for, so a rejected query is answered as rejected even
# when Neo4j is down (_get_retriever opens the driver on the first call, bounded by the driver's connect timeouts).
@mcp_bounds.bounded_tool(mcp, guard=_project_guard, backoff=_AUTH,
                         describe=mcp_bounds.project_tool_describer("Queries the DeepMap graph"))
def deepmap_cypher(query: str, params: dict | None = None,
                   limit: int = cypher_readonly.LIMIT_DEFAULT) -> list[dict]:
    """Read-only Cypher over THIS project's code graph, for ad-hoc STRUCTURE questions: list, count, group, find
    paths. Prefer deepmap_search (what code does) and deepmap_impact (what depends on X) when they fit.
    Nodes :Function :Method :Class :Module (rarely :Route) with key (stable id), name (methods: Class.method),
    source_file, start_line, body_summary, confidence. Links (a)-[:TYPE]->(b) carry confidence EXTRACTED |
    INFERRED | AMBIGUOUS (AMBIGUOUS = the name matched several symbols: unverified, read the code). Types: CALLS,
    IMPORTS, INHERITS, IMPLEMENTS, ROUTES_TO, DEPENDS_ON, HOOKS, SCHEDULED_AS, TRIGGERS, DISPATCHES, CASCADES
    (dependent -> owner), HAS_MANY, REFERENCES, RENDERS.
    Examples:
      MATCH (f:Function) WHERE f.source_file STARTS WITH 'src/api/' RETURN f.name, f.start_line
      MATCH (c:Class)<-[:INHERITS]-(s) RETURN c.name, count(s) AS n ORDER BY n DESC LIMIT 10
      MATCH p = shortestPath((a:Function {name: $a})-[:CALLS*..6]->(b {name: $b})) RETURN p
    Pass values in `params` ($a). Writes, LOAD CSV, USE, admin, apoc/gds/dbms procedures and multiple statements
    are rejected before reaching Neo4j; the query runs in a READ transaction that is rolled back. Returns at most
    `limit` rows (default 100, max 500) and about 64 KB of text, without embedding properties or lists of 256+
    floats; trailing {"note": ...} entries are DeepMap's remarks (a cut or empty answer)."""
    # The validator first: a rejected query is an ERROR (never an empty answer) and never reaches Neo4j.
    statement = cypher_readonly.check_read_only(query)
    values = cypher_readonly.check_params(params)
    budget = _cypher_timeout_s()
    try:
        rows, notes = _once_more_on_dead_connection(
            lambda: cypher_readonly.run_read_only(_get_retriever().driver, statement, values, limit=limit,
                                                  timeout=budget), "deepmap_cypher")
    except Exception as exc:
        # A Neo4j error about THIS query (syntax, an unknown procedure, the READ-mode refusal of a write, the read
        # timeout / memory limit) reads as one sentence; an outage or an auth rejection propagates unchanged so
        # bounded_tool words it (Neo4jUnreachable / the auth back-off).
        friendly = cypher_readonly.query_error_message(exc, budget)
        if friendly:
            raise ValueError(friendly) from exc
        raise
    # A structural query is a use like search and impact: stamp last_used (the idle-hibernation signal).
    _touch_last_used()
    return rows + [{"note": n} for n in notes]


if __name__ == "__main__":
    import _mcp_transport
    # Fast startup: serve immediately and warm the model + driver in the background, so the first call
    # (and setup's MCP verify) doesn't pay the cold start. DEEPMAP_MCP_WARMUP=0 keeps it fully lazy.
    if os.environ.get("DEEPMAP_MCP_WARMUP", "1").strip() != "0":
        threading.Thread(target=_warm_up, name="deepmap-search-warm-up", daemon=True).start()
    _mcp_transport.run(mcp)
