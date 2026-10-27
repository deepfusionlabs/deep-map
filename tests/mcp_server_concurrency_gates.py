#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the MCP servers' concurrency, time bounds and boundary honesty (v0.28.0, W4).

THE BUGS (2026-09-24 live validation, all 2-skeptic confirmed):
  S4  deepmap_search / deepmap_impact / deepmap_embed were plain `def` tools; mcp 1.28's FastMCP calls a
      sync tool DIRECTLY on the event loop, so one slow call (a 5,000-char query, a hung Neo4j) blocked
      every other caller — a ~1.4 s impact took 53 s behind a long search. Query length was unbounded
      (a 5,000-char query embedded for 13–35 s) and the model loaded lazily inside the first call
      (100–170 s on a loaded machine, so setup's MCP verify timed out).
  S5  no time bound: with the Neo4j JVM frozen a call hung for 90–180 s; the server's driver had no
      connection-acquisition bound of its own.
  S6  an empty query over MCP returned [] (indistinguishable from "no match") instead of an error;
      impact's honesty notes had no way to reach an MCP caller.

THE FIX (locked here): each tool is registered as an async wrapper that runs the unchanged sync body in a
worker thread under a deadline (the loop stays free); cold start (model + driver) gets its own, longer
bound and is pre-warmed in the background at server start; queries are capped at the boundary
(mcp_bounds.QUERY_MAX_CHARS); the driver gets bounded connect/acquire timeouts; query encodes are
serialised (a HF fast tokenizer is not safe across threads). The directly-callable sync functions and the
advertised tool schemas are unchanged (frozen contract). Pure + hermetic: fakes only — no Neo4j, no
model, no sockets, never touches ~/.deepmap or the committed .mcp.json.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import threading
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

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


def _load(fname: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _fake_embeddings(delay: float = 0.0, record: list | None = None):
    """A stand-in `embeddings` module (the servers `import embeddings` lazily)."""
    fake = types.ModuleType("embeddings")
    fake.loads = {"n": 0}

    def _get_model():
        fake.loads["n"] += 1
        return object()

    def embed_query(text):
        _get_model()                       # like the real embed_query: loads the model on first use
        if record is not None:
            record.append(text)
        time.sleep(delay)
        return [0.0] * 768

    class ModelUnavailable(RuntimeError):
        pass

    def ensure_model(max_wait_s=None, retry_after_s=None):   # the servers' non-blocking loader (v0.28 r2)
        return _get_model()

    fake._get_model = _get_model
    fake.embed_query = embed_query
    fake.ensure_model = ensure_model
    fake.ModelUnavailable = ModelUnavailable
    fake.model_status = lambda: {"state": "ready"}
    saved = sys.modules.get("embeddings")
    sys.modules["embeddings"] = fake
    try:
        yield fake
    finally:
        if saved is not None:
            sys.modules["embeddings"] = saved
        else:
            sys.modules.pop("embeddings", None)


class _FakeRetriever:
    def __init__(self, search_delay=0.0, impact_rows=None):
        self.search_delay = search_delay
        self.impact_rows = impact_rows if impact_rows is not None else []
        self.queries = []
        self.arm_weights = {}

    def search(self, query, top_k=15):
        self.queries.append(query)
        time.sleep(self.search_delay)
        return [{"id": "1", "name": "hit", "type": "Function", "file": "a.py", "line": 1,
                 "summary": "", "confidence": "EXTRACTED", "score": 1.0}]

    def impact(self, symbol, limit=30, edge_types=None):
        self.symbol = symbol
        return self.impact_rows


def _call(srv, name, args, convert=False):
    import anyio

    async def go():
        return await srv.mcp._tool_manager.call_tool(name, args, convert_result=convert)
    return anyio.run(go)


def gate_bounds(B) -> None:
    print("\n-- mcp_bounds: query-length cap + time bounds (one source of truth) --")
    check("QUERY_MAX_CHARS is a positive bound (~2000)", getattr(B, "QUERY_MAX_CHARS", 0) >= 500,
          repr(getattr(B, "QUERY_MAX_CHARS", None)))
    cap = getattr(B, "cap_query", None)
    check("cap_query helper exists", callable(cap))
    if not callable(cap):
        return
    q = "find the code that retries failed uploads"
    out, note = cap(q)
    check("a normal query passes through byte-identical, no note", out == q and note is None, repr((out, note)))
    long_q = ("alpha beta gamma delta " * 400).strip()
    out, note = cap(long_q)
    check("an over-long query is truncated to <= QUERY_MAX_CHARS", len(out) <= B.QUERY_MAX_CHARS, str(len(out)))
    check("truncation lands on a word boundary (no half token)", long_q.startswith(out) and
          long_q[len(out):len(out) + 1] in (" ", ""), repr(out[-12:]))
    check("the truncation note says so and names the cap", note is not None and str(B.QUERY_MAX_CHARS) in note,
          repr(note))
    out, note = cap("x" * 5000)
    check("an unbroken 5,000-char token is hard-cut at the cap", len(out) == B.QUERY_MAX_CHARS and note)
    for fn_name in ("tool_timeout_s", "warmup_timeout_s"):
        fn = getattr(B, fn_name, None)
        check(f"{fn_name}() exists and returns a positive bound", callable(fn) and fn() > 0,
              repr(fn() if callable(fn) else None))
    if callable(getattr(B, "tool_timeout_s", None)):
        with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="7.5"):
            check("tool_timeout_s honours DEEPMAP_MCP_TOOL_TIMEOUT_S", B.tool_timeout_s() == 7.5)
        with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="banana"):
            check("a malformed timeout env falls back to the default (never raises)", B.tool_timeout_s() > 0)
        with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="-3"):
            check("a non-positive timeout env falls back to the default", B.tool_timeout_s() > 0)
    kw = getattr(B, "neo4j_driver_timeouts", lambda: {})()
    check("neo4j_driver_timeouts() gives bounded connect + acquisition timeouts",
          kw.get("connection_timeout", 0) > 0 and kw.get("connection_acquisition_timeout", 0) > 0
          and kw["connection_acquisition_timeout"] > kw["connection_timeout"], str(kw))


def gate_tools_async_and_schema(S, E) -> None:
    print("\n-- the tools are ASYNC (never run on the event loop) with UNCHANGED schemas --")
    tm = S.mcp._tool_manager
    for name, props in (("deepmap_search", {"query", "top_k"}),
                        ("deepmap_impact", {"symbol", "limit", "edge_types"})):
        tool = tm.get_tool(name)
        check(f"{name} is registered", tool is not None)
        if tool is None:
            continue
        check(f"{name} is an async tool (FastMCP will not call it on the loop)", tool.is_async)
        check(f"{name} advertises the same parameters {sorted(props)}",
              set(tool.parameters.get("properties", {})) == props, str(tool.parameters.get("properties")))
        check(f"{name} keeps its docstring as the tool description",
              tool.description.strip() == (getattr(S, name).__doc__ or "").strip())
    tool = E.mcp._tool_manager.get_tool("deepmap_embed")
    check("deepmap_embed is an async tool", tool is not None and tool.is_async)
    check("deepmap_embed advertises only `text`",
          tool is not None and set(tool.parameters.get("properties", {})) == {"text"})
    check("the directly-callable sync contract is unchanged (deepmap_search is a plain function)",
          callable(S.deepmap_search) and not _is_coro(S.deepmap_search))


def _is_coro(fn):
    import inspect
    return inspect.iscoroutinefunction(fn)


def gate_no_head_of_line_blocking(S) -> None:
    print("\n-- a slow search does NOT block a concurrent impact (the loop stays free) --")
    import anyio
    fake = _FakeRetriever(search_delay=1.5)
    S._get_retriever = lambda: fake
    done = {}

    async def run():
        t0 = time.monotonic()

        async def slow():
            await S.mcp._tool_manager.call_tool("deepmap_search", {"query": "slow query"})
            done["search"] = time.monotonic() - t0

        async def fast():
            await anyio.sleep(0.2)
            await S.mcp._tool_manager.call_tool("deepmap_impact", {"symbol": "foo"})
            done["impact"] = time.monotonic() - t0

        async with anyio.create_task_group() as tg:
            tg.start_soon(slow)
            tg.start_soon(fast)

    with _fake_embeddings(), _env(DEEPMAP_MCP_TOOL_TIMEOUT_S=None):
        anyio.run(run)
    check("the impact answered while the 1.5 s search was still running",
          done.get("impact", 99) < 1.0 and done.get("search", 0) >= 1.4, str(done))


def gate_deadline(S) -> None:
    print("\n-- a call that exceeds the deadline fails FAST with a clear error (hung Neo4j) --")
    from mcp.server.fastmcp.exceptions import ToolError
    fake = _FakeRetriever(search_delay=3.0)
    S._get_retriever = lambda: fake
    err, t0 = None, time.monotonic()
    with _fake_embeddings(), _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="0.5"):
        try:
            _call(S, "deepmap_search", {"query": "hangs"})
        except ToolError as exc:
            err = exc
    took = time.monotonic() - t0
    check("the stuck call is abandoned near the deadline (not after the 3 s hang)", took < 2.0, f"{took:.2f}s")
    check("it surfaces as a tool ERROR (isError), not an empty result", err is not None)
    check("the error names the deadline and a next step",
          err is not None and "0.5" in str(err) and "status" in str(err).lower(), str(err))


def gate_cold_start_bound_separately(S) -> None:
    print("\n-- cold start (driver + model) is NOT charged to the per-call deadline --")
    slow_start = {"n": 0}
    fake = _FakeRetriever()

    def slow_get():
        slow_start["n"] += 1
        if slow_start["n"] == 1:
            time.sleep(1.0)            # a slow first load
        return fake

    S._get_retriever = slow_get
    err = None
    with _fake_embeddings(), _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="0.5", DEEPMAP_MCP_WARMUP_TIMEOUT_S="10"):
        try:
            out = _call(S, "deepmap_search", {"query": "first call"})
        except Exception as exc:  # noqa: BLE001
            err, out = exc, None
    check("a first call whose init takes longer than the query deadline still succeeds",
          err is None and out and out[0]["name"] == "hit", repr(err))


def gate_empty_is_error(S) -> None:
    print("\n-- an empty query / symbol is an ERROR over MCP (not a silent []) --")
    from mcp.server.fastmcp.exceptions import ToolError
    S._get_retriever = lambda: _FakeRetriever()
    for name, args in (("deepmap_search", {"query": ""}), ("deepmap_search", {"query": "   \n"}),
                       ("deepmap_impact", {"symbol": ""}), ("deepmap_impact", {"symbol": "  "})):
        err = None
        with _fake_embeddings():
            try:
                _call(S, name, args)
            except ToolError as exc:
                err = exc
        check(f"{name}({args}) -> isError", err is not None)
        check(f"{name}({args}) error says it was empty", err is not None and "empty" in str(err).lower(), str(err))


def gate_query_cap_at_boundary(S, E, B) -> None:
    print("\n-- the query length is capped at the MCP boundary (search + embed) --")
    fake = _FakeRetriever()
    S._get_retriever = lambda: fake
    with _fake_embeddings():
        _call(S, "deepmap_search", {"query": "retry upload " * 1000})
    got = fake.queries[-1] if fake.queries else ""
    cap = getattr(B, "QUERY_MAX_CHARS", 2000)
    check("deepmap_search hands the retriever at most QUERY_MAX_CHARS", 0 < len(got) <= cap, str(len(got)))
    fake.queries.clear()
    with _fake_embeddings():
        S.deepmap_search("where is auth handled", top_k=5)
    check("a normal query reaches the retriever byte-identical",
          fake.queries == ["where is auth handled"], str(fake.queries))
    seen: list = []
    with _fake_embeddings(record=seen):
        _call(E, "deepmap_embed", {"text": "z" * 9000})
    check("deepmap_embed embeds at most QUERY_MAX_CHARS", seen and len(seen[-1]) <= cap,
          str(len(seen[-1]) if seen else None))


def gate_impact_notes(S) -> None:
    print("\n-- impact honesty notes reach the MCP caller as trailing {note} entries --")

    class _Rows(list):
        notes = ["'foo' is in the graph (1 matching symbol) but no dependents were found."]

    S._get_retriever = lambda: _FakeRetriever(impact_rows=_Rows())
    with _fake_embeddings():
        out = _call(S, "deepmap_impact", {"symbol": "foo"})
    check("an empty-with-note impact returns the note (not a bare [])",
          out == [{"note": _Rows.notes[0]}], str(out))
    row = {"caller": "a", "caller_type": "Function", "file": "a.py", "line": 1, "confidence": "INFERRED",
           "callee": "foo", "hop": 1, "via": "CALLS", "via_path": ["CALLS"]}
    S._get_retriever = lambda: _FakeRetriever(impact_rows=[row])
    with _fake_embeddings():
        out = _call(S, "deepmap_impact", {"symbol": "foo"})
    check("a plain answer is returned unchanged (no note appended)", out == [row], str(out))
    fake = _FakeRetriever(impact_rows=[])
    S._get_retriever = lambda: fake
    with _fake_embeddings():
        _call(S, "deepmap_impact", {"symbol": "  build_record \n"})
    check("the symbol is stripped at the boundary", getattr(fake, "symbol", None) == "build_record",
          repr(getattr(fake, "symbol", None)))


def gate_driver_timeouts() -> None:
    print("\n-- the server's Neo4j driver is built with bounded connect/acquisition timeouts --")
    seen = {}

    class _Drv:
        def verify_connectivity(self):
            return None

        def session(self, **kw):
            raise RuntimeError("probe -> SEARCH clause unsupported (expected in a fake)")

        def close(self):
            pass

    class _GDB:
        @staticmethod
        def driver(*a, **kw):
            seen.update(kw)
            return _Drv()

    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = _GDB
    saved = sys.modules.get("neo4j")
    sys.modules["neo4j"] = fake_neo4j
    try:
        with _fake_embeddings(), contextlib.redirect_stderr(io.StringIO()):
            S = _load("deepmap_search_server.py", "dm_srv_driver_w4")
            S._get_retriever()
    finally:
        if saved is not None:
            sys.modules["neo4j"] = saved
        else:
            sys.modules.pop("neo4j", None)
    check("connection_timeout is set", seen.get("connection_timeout", 0) > 0, str(seen))
    check("connection_acquisition_timeout is set", seen.get("connection_acquisition_timeout", 0) > 0, str(seen))
    check("notifications stay silenced (unchanged)", seen.get("notifications_min_severity") == "OFF")


def gate_warmup(S, E) -> None:
    print("\n-- the model (+ search retriever) warm up in the background at server start --")
    src = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    esrc = (BUILD / "deepmap_embed_server.py").read_text(encoding="utf-8")
    main_s = src.split('if __name__ == "__main__":', 1)[-1]
    main_e = esrc.split('if __name__ == "__main__":', 1)[-1]
    check("search server starts a warm-up thread before serving", "_warm_up" in main_s and "Thread(" in main_s)
    check("embed server starts a warm-up thread before serving", "_warm_up" in main_e and "Thread(" in main_e)
    check("warm-up is opt-out via DEEPMAP_MCP_WARMUP=0", "DEEPMAP_MCP_WARMUP" in src and "DEEPMAP_MCP_WARMUP" in esrc)
    have = callable(getattr(S, "_warm_up", None)) and callable(getattr(E, "_warm_up", None))
    check("both servers expose a _warm_up() entry point", have)
    if not have:
        return
    fake = _FakeRetriever()
    calls = {"ret": 0}

    def get():
        calls["ret"] += 1
        return fake

    S._get_retriever = get
    with _fake_embeddings() as emb:
        S._warm_up()
        check("search warm-up loads the model AND opens the retriever",
              emb.loads["n"] >= 1 and calls["ret"] == 1, f"{emb.loads} {calls}")
    with _fake_embeddings() as emb:
        E._warm_up()
        check("embed warm-up loads the model", emb.loads["n"] >= 1, str(emb.loads))

    def boom():
        raise RuntimeError("neo4j not up yet")

    S._get_retriever = boom
    err = io.StringIO()
    with _fake_embeddings(), contextlib.redirect_stderr(err):
        try:
            S._warm_up()
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
    check("a failed warm-up never raises (the first call retries)", ok)
    check("a failed warm-up says so on stderr", "warm" in err.getvalue().lower(), err.getvalue()[:200])


def gate_encode_serialised() -> None:
    print("\n-- query encodes are serialised (a fast tokenizer is not thread-safe) --")
    Em = _load("embeddings.py", "dm_embeddings_lock_w4")
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class _Model:
        def encode(self, text, show_progress_bar=False, **kw):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return [0.0] * 4

    Em._get_model = lambda: _Model()
    ts = [threading.Thread(target=Em.embed_query, args=(f"q{i}",)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("6 concurrent embed_query calls never overlap inside encode", state["peak"] == 1, str(state))
    check("embed_query output is unchanged (768-d path returns floats)",
          Em.embed_query("x") == [0.0, 0.0, 0.0, 0.0])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — MCP server concurrency / time bounds / boundary honesty (S4/S5/S6; no Neo4j)")
    print("=" * 72)
    B = _load("mcp_bounds.py", "dm_mcp_bounds_w4")
    # No project env: the advertised descriptions must then be the docstrings byte-for-byte (a server started
    # WITH DEEPMAP_PROJECT_ROOT prefixes a project line — locked in tests/mcp_project_label_gates.py).
    with _env(DEEPMAP_MCP_TRANSPORT=None, DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None):
        S = _load("deepmap_search_server.py", "dm_srv_w4")
        E = _load("deepmap_embed_server.py", "dm_embed_w4")
    gate_bounds(B)
    gate_tools_async_and_schema(S, E)
    gate_no_head_of_line_blocking(S)
    gate_deadline(S)
    gate_cold_start_bound_separately(S)
    gate_empty_is_error(S)
    gate_query_cap_at_boundary(S, E, B)
    gate_impact_notes(S)
    gate_driver_timeouts()
    gate_warmup(S, E)
    gate_encode_serialised()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
