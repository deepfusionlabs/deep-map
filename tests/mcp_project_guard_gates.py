#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the MCP search server's PROJECT GUARD (v0.28.0, round-2 decision D3).

THE PROBLEM (2026-09 live validation): a Claude Code session in project Y could be wired to project X's
DeepMap server (the plugin shipped the dev repo's .mcp.json; a stale or copied .mcp.json; a port pair
re-allocated after instance-rm) and silently get X's answers — confident, wrong-project results.

THE GUARD (build/deepmap_search_server.py `_project_guard`, via mcp_bounds.bounded_tool `guard=`):
provision starts the server with DEEPMAP_PROJECT_ROOT / DEEPMAP_PROJECT_KEY. On the first tool call of an
MCP session whose client declares the `roots` capability, the server asks for the roots (bounded by
DEEPMAP_MCP_ROOTS_TIMEOUT_S) and, when NONE of them is this project (port_registry.project_identity;
the same path, or one contains the other), refuses deepmap_search / deepmap_impact with an isError that
names both projects and a runnable next step. Judged once per session. It FAILS OPEN — serves as before —
when the client offers no roots, the roots request errors or times out, the env is absent, or
DEEPMAP_PROJECT_GUARD=0; and over the deployed STATELESS streamable-HTTP transport (each request is its
own session, so the client's capabilities are not visible to a tool call) it is a no-op by construction,
with no added latency.

Driven by the REAL mcp ClientSession (list_roots_callback=...): an in-memory stateful session (as over
stdio), and the server's own stateless streamable-HTTP app through an in-process ASGI transport (no
sockets). Hermetic: temp DEEPMAP_HOME + temp project dirs, fake retriever, never touches ~/.deepmap.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
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


class _FakeRetriever:
    arm_weights: dict = {}

    def search(self, query, top_k=15):
        return [{"id": "1", "name": "auth_flow", "type": "Function", "file": "auth.py", "line": 3,
                 "summary": "", "confidence": "EXTRACTED", "score": 1.0}]

    def impact(self, symbol, limit=30, edge_types=None):
        return [{"caller": "main", "caller_type": "Function", "file": "m.py", "line": 1,
                 "confidence": "INFERRED", "callee": symbol, "hop": 1, "via": "CALLS", "via_path": ["CALLS"]}]


def _fake_embeddings() -> types.ModuleType:
    m = types.ModuleType("embeddings")

    class ModelUnavailable(RuntimeError):
        pass

    m.ModelUnavailable = ModelUnavailable
    m.ensure_model = lambda max_wait_s=None, retry_after_s=None: object()
    m.embed_query = lambda text: [0.0] * 768
    m._get_model = lambda: object()
    m.model_status = lambda: {"state": "ready"}
    return m


def _load_server(path: "Path | None" = None):
    # DM_GUARD_SERVER_UNDER_TEST points the gate at another copy of the server (RED-first evidence).
    path = path or Path(os.environ.get("DM_GUARD_SERVER_UNDER_TEST") or BUILD / "deepmap_search_server.py")
    sys.modules["embeddings"] = _fake_embeddings()
    spec = importlib.util.spec_from_file_location(f"dm_srv_guard_{time.monotonic_ns()}", path)
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    srv._get_retriever = lambda: _FakeRetriever()
    return srv


def _roots_cb(paths, calls: list, *, mode: str = "ok"):
    """A client roots callback: 'ok' returns `paths` as file roots; 'error' answers with an MCP error;
    'raise' raises inside the client; 'late' answers only after the server timeout."""
    from mcp import types as mt

    async def cb(context):
        calls.append(1)
        if mode == "error":
            return mt.ErrorData(code=-32603, message="roots unavailable (fake)")
        if mode == "raise":
            raise RuntimeError("client blew up listing roots (fake)")
        if mode == "late":
            # Answers only after the server's roots timeout. (The Python client handles a server->client
            # request INLINE in its receive loop, so this also delays the client seeing the result — the
            # gate therefore judges the server by WHAT it answered and by its own verdict time.)
            import anyio
            await anyio.sleep(1.5)
        return mt.ListRootsResult(roots=[mt.Root(uri=Path(p).resolve().as_uri(), name=Path(p).name)
                                         for p in paths])
    return cb


def _session_calls(srv, calls, roots_cb=None):
    """[(tool, args), ...] through ONE real in-memory (stateful) ClientSession -> [(is_error, text, secs)]."""
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    out = []

    async def go():
        async with create_connected_server_and_client_session(srv.mcp, list_roots_callback=roots_cb) as client:
            for name, args in calls:
                t0 = time.monotonic()
                res = await client.call_tool(name, args)
                text = " ".join(getattr(c, "text", "") for c in res.content)
                out.append((res.isError, text, time.monotonic() - t0))

    anyio.run(go)
    return out


def _http_calls(srv, calls, roots_cb):
    """The same, but over the server's REAL stateless streamable-HTTP app (in-process ASGI, no sockets)."""
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    out = []
    app = srv.mcp.streamable_http_app()

    async def go():
        async with srv.mcp.session_manager.run():
            http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8787",
                                     timeout=30)
            async with http:
                async with streamable_http_client("http://127.0.0.1:8787/mcp", http_client=http) as (r, w, _):
                    async with ClientSession(r, w, list_roots_callback=roots_cb) as client:
                        await client.initialize()
                        for name, args in calls:
                            t0 = time.monotonic()
                            res = await client.call_tool(name, args)
                            text = " ".join(getattr(c, "text", "") for c in res.content)
                            out.append((res.isError, text, time.monotonic() - t0))

    anyio.run(go)
    return out


SEARCH = ("deepmap_search", {"query": "auth flow"})
IMPACT = ("deepmap_impact", {"symbol": "auth_flow"})


def gate_mismatch_refused(X: Path, Y: Path) -> None:
    print("\n-- a session whose roots are ANOTHER project is refused (isError), naming both + a next step --")
    srv = _load_server()
    calls: list = []
    res = _session_calls(srv, [SEARCH, IMPACT, SEARCH], _roots_cb([Y], calls))
    (e1, t1, _), (e2, t2, _), (e3, _t3, _) = res
    check("deepmap_search is refused for a session in another project", e1, t1[:200])
    check("deepmap_impact is refused too", e2, t2[:200])
    check("the refusal names the server's project and the session's project",
          str(X.resolve()) in t1 and str(Y.resolve()) in t1, t1[:400])
    check("the refusal says the session's project has no index of its own and to run /deep-map:setup there",
          "no DeepMap index of its own" in t1 and "/deep-map:setup" in t1, t1[:400])
    check("the refusal also offers the reload path (its own .mcp.json)", ".mcp.json" in t1, t1[:400])
    check("it is judged ONCE per MCP session (roots asked once for 3 calls)", len(calls) == 1 and e3,
          f"roots asked {len(calls)}x")


def gate_own_project_served(X: Path, Y: Path) -> None:
    print("\n-- a session in THIS project (root, a subdir, or among several roots) is served --")
    for desc, roots in (("the project root", [X]), ("a subdirectory of it", [X / "sub"]),
                        ("one of several roots", [Y, X])):
        srv = _load_server()
        calls: list = []
        ((err, text, _),) = _session_calls(srv, [SEARCH], _roots_cb(roots, calls))
        check(f"roots = {desc}: deepmap_search answers", not err and "auth_flow" in text, text[:200])
        check(f"roots = {desc}: the roots were actually consulted", len(calls) == 1, f"{len(calls)}x")


def gate_fail_open(X: Path, Y: Path) -> None:
    print("\n-- FAIL OPEN: no roots capability / roots error / client raise / timeout -> serve as today --")
    srv = _load_server()
    ((err, text, _),) = _session_calls(srv, [SEARCH], None)
    check("a client that declares NO roots capability is served", not err and "auth_flow" in text, text[:200])
    for mode in ("error", "raise"):
        srv = _load_server()
        calls: list = []
        ((err, text, _),) = _session_calls(srv, [SEARCH], _roots_cb([Y], calls, mode=mode))
        check(f"a roots request that fails ({mode}) is served (fail open)", not err and "auth_flow" in text,
              text[:200])
    srv = _load_server()
    calls = []
    verdict_secs: list = []
    real_verdict = srv._session_verdict

    async def timed_verdict(*a, **kw):
        t0 = time.monotonic()
        try:
            return await real_verdict(*a, **kw)
        finally:
            verdict_secs.append(time.monotonic() - t0)

    srv._session_verdict = timed_verdict
    with _env(DEEPMAP_MCP_ROOTS_TIMEOUT_S="0.5"):
        res = _session_calls(srv, [SEARCH, SEARCH], _roots_cb([Y], calls, mode="late"))
    (e1, t1, _s1), (e2, _t2, s2) = res
    check("roots that arrive AFTER the timeout are not waited for: served (a foreign root that came in time "
          "would have been refused)", not e1 and "auth_flow" in t1, f"err={e1} {t1[:160]}")
    check("the server gave up on roots at its timeout (verdict in < 1.2 s, not the client's 1.5 s)",
          len(verdict_secs) == 1 and verdict_secs[0] < 1.2, str([round(x, 2) for x in verdict_secs]))
    check("the timeout is paid once per session (the next call is immediate, not re-judged)",
          not e2 and s2 < 0.4 and len(verdict_secs) == 1, f"{s2:.2f}s verdicts={len(verdict_secs)}")


def gate_switches(X: Path, Y: Path, key_x: str) -> None:
    print("\n-- no project env / DEEPMAP_PROJECT_GUARD=0 -> no guard; KEY-only env still guards --")
    with _env(DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None):
        srv = _load_server()
        calls: list = []
        ((err, text, _),) = _session_calls(srv, [SEARCH], _roots_cb([Y], calls))
    check("without DEEPMAP_PROJECT_ROOT/KEY the server serves (and never asks for roots)",
          not err and not calls, f"err={err} asked={len(calls)}")
    with _env(DEEPMAP_PROJECT_GUARD="0"):
        srv = _load_server()
        calls = []
        ((err, text, _),) = _session_calls(srv, [SEARCH], _roots_cb([Y], calls))
    check("DEEPMAP_PROJECT_GUARD=0 turns the guard off", not err and not calls, f"err={err} asked={len(calls)}")
    with _env(DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=key_x):
        srv = _load_server()
        ok = _session_calls(srv, [SEARCH], _roots_cb([X], []))[0]
        srv = _load_server()
        bad = _session_calls(srv, [SEARCH], _roots_cb([Y], []))[0]
    check("KEY-only env: the project's own root is served", not ok[0], ok[1][:200])
    check("KEY-only env: another project's root is refused", bad[0] and "/deep-map:setup" in bad[1], bad[1][:200])


def gate_registered_elsewhere(X: Path, Y: Path) -> None:
    print("\n-- the session's project HAS its own DeepMap servers -> say reload / reconnect, not setup --")
    import port_registry
    saved = port_registry.lookup_ports
    port_registry.lookup_ports = lambda root, env=None: (8795, 8796) if Path(root).resolve() == Y.resolve() else None
    try:
        srv = _load_server()
        ((err, text, _),) = _session_calls(srv, [SEARCH], _roots_cb([Y], []))
    finally:
        port_registry.lookup_ports = saved
    check("refused", err, text[:200])
    check("the message names Y's own server port and says to reload / reconnect with /mcp",
          "8795" in text and "/mcp" in text and "reload" in text.lower(), text[:400])
    check("it does NOT tell the user to re-run setup in a project that already has DeepMap",
          "/deep-map:setup" not in text, text[:400])


def gate_stateless_http_fail_open(X: Path, Y: Path) -> None:
    print("\n-- the DEPLOYED stateless streamable-HTTP config: the guard is a no-op (no roots asked, no delay) --")
    srv = _load_server()
    check("the server is configured stateless + JSON (the deployed transport)",
          srv.mcp.settings.stateless_http and srv.mcp.settings.json_response)
    calls: list = []
    try:
        with _env(DEEPMAP_MCP_ROOTS_TIMEOUT_S="5"):
            res = _http_calls(srv, [SEARCH, IMPACT], _roots_cb([Y], calls, mode="late"))
    except Exception as exc:  # noqa: BLE001
        check("the stateless HTTP round-trip ran", False, f"{type(exc).__name__}: {exc}")
        return
    (e1, t1, s1), (e2, t2, s2) = res
    check("over stateless HTTP a session with a foreign root is still served (fail open by construction)",
          not e1 and not e2 and "auth_flow" in t1, f"{t1[:120]} | {t2[:120]}")
    check("the server never asks for roots it cannot receive (callback not invoked)", not calls, f"{len(calls)}x")
    check("no added latency (each call well under the 5 s roots timeout)", s1 < 2.0 and s2 < 2.0,
          f"{s1:.2f}s {s2:.2f}s")


def gate_direct_call_unguarded() -> None:
    print("\n-- the directly-callable sync contract is unguarded (no MCP request -> nothing to check) --")
    srv = _load_server()
    check("deepmap_search(...) called directly still answers", srv.deepmap_search("auth flow", top_k=3)[0]["name"]
          == "auth_flow")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — MCP search server project guard (D3; real MCP client; no Neo4j)")
    print("=" * 72)
    saved_emb = sys.modules.get("embeddings")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        X, Y, home = base / "projX", base / "projY", base / "home"
        for d in (X / "sub", Y, home):
            d.mkdir(parents=True)
        import port_registry
        ident_x = port_registry.project_identity(X, timeout=10)
        key_x = port_registry.project_key(X, timeout=10)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None, DEEPMAP_MCP_TRANSPORT=None, DEEPMAP_RETRIEVAL_LOG=None,
                  DEEPMAP_APPLY_LEARNED=None, DEEPMAP_PROJECT_GUARD=None, DEEPMAP_MCP_ROOTS_TIMEOUT_S=None,
                  DEEPMAP_MCP_WARMUP_TIMEOUT_S=None, DEEPMAP_MCP_TOOL_TIMEOUT_S=None,
                  DEEPMAP_PROJECT_ROOT=ident_x, DEEPMAP_PROJECT_KEY=key_x), \
                contextlib.redirect_stderr(io.StringIO()):
            for gate, args in ((gate_mismatch_refused, (X, Y)), (gate_own_project_served, (X, Y)),
                               (gate_fail_open, (X, Y)), (gate_switches, (X, Y, key_x)),
                               (gate_registered_elsewhere, (X, Y)), (gate_stateless_http_fail_open, (X, Y)),
                               (gate_direct_call_unguarded, ())):
                try:
                    gate(*args)
                except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crashed suite
                    check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    if saved_emb is not None:
        sys.modules["embeddings"] = saved_emb
    else:
        sys.modules.pop("embeddings", None)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
