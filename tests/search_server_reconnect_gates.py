#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: the MCP search server survives a restart of its project's Neo4j (2026-10-23).

THE BUG: the search server is long-lived and keeps ONE neo4j driver whose pool holds idle Bolt connections. After the
project's Neo4j was restarted under the running servers (provision.py stop + heal, a Neo4j patch upgrade, a crash and
heal), every pooled connection was dead. The driver handed one out unchecked (it checks liveness only when
liveness_check_timeout is set — off by default), so the FIRST deepmap_search / deepmap_impact / deepmap_cypher call
failed with "Failed to read from defunct connection" — worded by bounded_tool as "Neo4j is not reachable ... probably
stopped or hibernated — run /deep-map:setup", while Neo4j was up. The failure made the driver drop its idle
connections, so the SECOND call succeeded: a misleading error on every first call after a restart.

THE FIX (build/deepmap_search_server.py — the server path; build/retrieval.py's ranking functions are hash-locked by
tests/fts_r7_gates.py and untouched):
  1. the driver checks a pooled connection is alive (a RESET round trip) before handing it out —
     liveness_check_timeout=NEO4J_LIVENESS_CHECK_S; a dead one is closed and a fresh connection opened;
  2. a call that still meets a dead pooled connection (the driver's own "defunct connection" / "Failed to write data to
     connection" / "closed connection" wording on ServiceUnavailable or SessionExpired) is retried ONCE: never on a
     failed connect (Neo4j really down — that answer is right, and a refused connect costs ~2 s on Windows), never on
     an auth rejection (the login lock), never on a query error, never twice.

Locks (hermetic: a fake neo4j module / scripted retrievers, no Neo4j, no network, a temp DEEPMAP_HOME):
  A  _get_retriever passes liveness_check_timeout (a finite number >= 0, at most 5 s) beside the bounded connect /
     acquisition timeouts; the REAL installed driver accepts exactly those keys (an unknown key is a
     ConfigurationError) and its pool holds the value;
  B  the dead-connection wording the retry keys on is the REAL driver's own (read from its source when installed);
  C  per tool (deepmap_search / deepmap_impact / deepmap_cypher), through the REGISTERED MCP tool, with the driver's
     real exception classes and with name-only doubles: dead once -> answered after exactly 2 attempts (and a stderr
     line says so); dead twice -> the outage error after exactly 2 attempts; Neo4j down (a refused connect) -> 1
     attempt and the outage message; an auth rejection -> 1 attempt and the back-off diagnosis; a query error -> 1
     attempt; dead then a query error -> the query error; a rejected cypher query -> 0 attempts.
The live proof (a real Neo4j restarted under a running server) is not a gate: it needs Neo4j — see the change log.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0

DEFUNCT_READ = ("Failed to read from defunct connection IPv4Address(('127.0.0.1', 7689)) "
                "(ResolvedIPv4Address(('127.0.0.1', 7689)))")
DEFUNCT_WRITE = ("Failed to write data to connection IPv4Address(('127.0.0.1', 7689)) "
                 "(ResolvedIPv4Address(('127.0.0.1', 7689)))")
REFUSED = ("Couldn't connect to 127.0.0.1:7689 (resolved to ('127.0.0.1:7689',)):\nFailed to establish connection to "
           "ResolvedIPv4Address(('127.0.0.1', 7689)) (reason [WinError 10061] No connection could be made because the "
           "target machine actively refused it)")
_ENV_KEYS = ("DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY", "DEEPMAP_PROJECT_GUARD", "NEO4J_URI",
             "DEEPMAP_MCP_WARMUP_TIMEOUT_S", "DEEPMAP_MCP_TOOL_TIMEOUT_S", "DEEPMAP_CYPHER_TIMEOUT_S",
             "DEEPMAP_MCP_AUTH_BACKOFF_S", "DEEPMAP_MCP_AUTH_BACKOFF_MAX_S", "DEEPMAP_RETRIEVAL_LOG",
             "DEEPMAP_APPLY_LEARNED", "DEEPMAP_LEARNING")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail else ''}")


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / f"{name}.py")
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
def _modules(**mods):
    saved = {k: sys.modules.get(k) for k in mods}
    try:
        for k, v in mods.items():
            sys.modules[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _fake_embeddings():
    emb = types.ModuleType("embeddings")
    emb.ensure_model = lambda **kw: None
    emb.embed_query = lambda text: [0.0] * 768
    return emb


# ---- A: the driver is configured to check a pooled connection before handing it out ---------------------------------
def gate_driver_config(home: str) -> "dict | None":
    print("\n-- A: the server's driver checks a pooled connection is alive before handing it out --")
    recorded: dict = {}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **kw):
            raise RuntimeError("probe -> the SEARCH-clause detection falls back (expected)")

    class _Driver:
        def verify_connectivity(self):
            pass

        def session(self, **kw):
            return _Session()

        def close(self):
            pass

    class _GDB:
        @staticmethod
        def driver(uri, **kw):
            recorded.update(uri=uri, **kw)
            return _Driver()

    fake = types.ModuleType("neo4j")
    fake.GraphDatabase = _GDB
    with _env(**{k: None for k in _ENV_KEYS}, DEEPMAP_HOME=home), \
            _modules(neo4j=fake, embeddings=_fake_embeddings()):
        S = _load("deepmap_search_server", "dm_reconnect_driver_cfg")
        S._retriever = None
        try:
            S._get_retriever()
        except Exception as exc:  # noqa: BLE001
            check("_get_retriever builds the retriever over the (fake) driver", False, f"{type(exc).__name__}: {exc}")
            return None
    live = recorded.get("liveness_check_timeout", "missing")
    check("GraphDatabase.driver(...) gets liveness_check_timeout (dead pooled connections are replaced, not used)",
          "liveness_check_timeout" in recorded, str(sorted(recorded)))
    check(f"...a finite number of seconds in [0, 5] (a restart takes longer, so every connection that may have died "
          f"is checked): {live!r}", isinstance(live, (int, float)) and not isinstance(live, bool)
          and math.isfinite(live) and 0 <= live <= 5)
    check("...beside the bounded connect / acquisition timeouts (a dead or frozen Neo4j still fails in bounded time)",
          recorded.get("connection_timeout") and recorded.get("connection_acquisition_timeout"), str(recorded))
    return {k: v for k, v in recorded.items() if k not in ("uri", "auth")}


def gate_real_driver_accepts(kwargs: "dict | None") -> None:
    print("\n-- A2: the REAL installed driver takes exactly these settings --")
    try:
        from neo4j import GraphDatabase
    except Exception:  # noqa: BLE001
        check("(no neo4j driver installed: the real-driver config check is skipped)", True)
        return
    if not kwargs:
        check("the server's driver settings were recorded", False)
        return
    try:
        drv = GraphDatabase.driver("bolt://127.0.0.1:9", auth=("neo4j", "x"), **kwargs)   # no connection is opened
    except Exception as exc:  # noqa: BLE001 — ConfigurationError: an unknown key
        check(f"the real driver accepts {sorted(kwargs)}", False, f"{type(exc).__name__}: {exc}")
        return
    try:
        held = getattr(getattr(getattr(drv, "_pool", None), "pool_config", None), "liveness_check_timeout", "n/a")
        check(f"the real driver accepts {sorted(kwargs)} (an unknown key would be a ConfigurationError)", True)
        check("...and its pool will check liveness with that value",
              held == "n/a" or held == kwargs.get("liveness_check_timeout"), f"pool has {held!r}")
    finally:
        drv.close()


# ---- B: the wording the retry keys on is the real driver's own -------------------------------------------------------
def gate_driver_wording(S) -> None:
    print("\n-- B: the dead-connection wording the retry keys on is the real driver's own --")
    try:
        import neo4j
    except Exception:  # noqa: BLE001
        check("(no neo4j driver installed: the wording check is skipped)", True)
        return
    src = Path(neo4j.__file__).resolve().parent / "_sync" / "io" / "_bolt.py"
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        check(f"(the driver's {src.name} is not where 6.x keeps it: the wording check is skipped)", True)
        return
    for driver_text, sample in (("Failed to read from defunct connection", DEFUNCT_READ),
                                ("Failed to write data to connection", DEFUNCT_WRITE)):
        check(f"the installed driver raises {driver_text!r}", driver_text in text)
    signs = getattr(S, "_DEAD_CONNECTION_SIGNS", ())
    check("the server's dead-connection signs each appear in the installed driver's messages",
          bool(signs) and all(s.lower() in text.lower() for s in signs), str(signs))


# ---- C: one retry on a dead pooled connection, through the registered tools ------------------------------------------
class _Doubles:
    """Name-only stand-ins for the driver's exception classes (what mcp_bounds matches when the driver isn't loaded)."""
    ServiceUnavailable = type("ServiceUnavailable", (Exception,), {})
    SessionExpired = type("SessionExpired", (Exception,), {})

    class AuthError(Exception):
        code = "Neo.ClientError.Security.Unauthorized"

    class SyntaxErr(Exception):
        code = "Neo.ClientError.Statement.SyntaxError"
        message = "Invalid input 'RETRUN'"


_Doubles.AuthError.__name__ = "AuthError"
_Doubles.SyntaxErr.__name__ = "CypherSyntaxError"


def _real_kinds():
    try:
        from neo4j import exceptions as NE
    except Exception:  # noqa: BLE001
        return None
    k = types.SimpleNamespace(ServiceUnavailable=NE.ServiceUnavailable, SessionExpired=NE.SessionExpired)
    k.auth = lambda: NE.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.Unauthorized",
                                                   message="The client is unauthorized due to authentication failure.")
    k.syntax = lambda: NE.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError",
                                                     message="Invalid input 'RETRUN'")
    return k


class _Script:
    """Raises the scripted errors in order, then answers; counts the attempts."""
    def __init__(self, errors, answer):
        self.errors, self.answer, self.calls = list(errors), answer, 0

    def __call__(self, *a, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.answer


class _Tx:
    def __init__(self, script):
        self.script = script

    def run(self, q, params=None):
        return self.script()

    def rollback(self):
        pass


class _CypherDriver:
    def __init__(self, script):
        self.script = script

    def session(self, **kw):
        drv = self

        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def begin_transaction(self, **kw):
                return _Tx(drv.script)
        return _S()


def _call(S, name, args):
    import anyio

    async def go():
        return await S.mcp._tool_manager.call_tool(name, args, convert_result=False)
    err = io.StringIO()
    saved = sys.stderr
    sys.stderr = err
    try:
        try:
            return False, anyio.run(go), err.getvalue()
        except Exception as exc:  # noqa: BLE001 — FastMCP raises ToolError for an isError result
            return True, str(exc), err.getvalue()
    finally:
        sys.stderr = saved


def _wire(S, tool: str, script: _Script) -> dict:
    """Point the server's retriever at `script` for `tool`; return the call's arguments."""
    if tool == "deepmap_search":
        S._get_retriever = lambda: types.SimpleNamespace(search=script)
        return {"query": "where are orders saved"}
    if tool == "deepmap_impact":
        S._get_retriever = lambda: types.SimpleNamespace(impact=script)
        return {"symbol": "save"}
    S._get_retriever = lambda: types.SimpleNamespace(driver=_CypherDriver(script))
    return {"query": "MATCH (f:Function) RETURN f.name AS name"}


def _chained(exc, cause):
    """`exc` as if raised `from cause` (what a wrapper that re-raises a driver error looks like)."""
    exc.__cause__ = cause
    return exc


def _answer(tool: str):
    if tool == "deepmap_cypher":
        return [{"name": "save"}]
    return [{"name": "save", "file": "app/order.py", "line": 10}]


def gate_retry(S, kinds, label: str) -> None:
    print(f"\n-- C ({label}): a dead pooled connection is retried ONCE, nothing else is --")
    for tool in ("deepmap_search", "deepmap_impact", "deepmap_cypher"):
        cases = [
            ("dead once (defunct read) -> answered after exactly 2 attempts",
             [kinds.ServiceUnavailable(DEFUNCT_READ)], False, 2, "save"),
            ("dead once (failed write, SessionExpired) -> answered after exactly 2 attempts",
             [kinds.SessionExpired(DEFUNCT_WRITE)], False, 2, "save"),
            ("dead twice -> the outage error after exactly 2 attempts (never a third)",
             [kinds.ServiceUnavailable(DEFUNCT_READ), kinds.ServiceUnavailable(DEFUNCT_READ),
              kinds.ServiceUnavailable(DEFUNCT_READ)], True, 2, "is not reachable"),
            ("Neo4j down (a refused connect) -> 1 attempt and the outage message",
             [kinds.ServiceUnavailable(REFUSED), kinds.ServiceUnavailable(REFUSED)], True, 1, "is not reachable"),
            ("an auth rejection -> 1 attempt and the back-off diagnosis (never feed the login lock)",
             [kinds.auth(), kinds.auth()], True, 1, "credentials"),
            ("a query error -> 1 attempt", [kinds.syntax(), kinds.syntax()], True, 1, ""),
            ("dead once, then a query error -> that query error after 2 attempts",
             [kinds.ServiceUnavailable(DEFUNCT_READ), kinds.syntax()], True, 2, ""),
            ("a non-driver error that merely mentions a defunct connection -> 1 attempt",
             [RuntimeError(DEFUNCT_READ), RuntimeError(DEFUNCT_READ)], True, 1, ""),
            ("an auth rejection raised FROM a dead connection -> still 1 attempt (never feed the login lock)",
             [_chained(kinds.auth(), kinds.ServiceUnavailable(DEFUNCT_READ)),
              _chained(kinds.auth(), kinds.ServiceUnavailable(DEFUNCT_READ))], True, 1, "credentials"),
            ("a query error raised FROM a dead connection -> still 1 attempt",
             [_chained(kinds.syntax(), kinds.ServiceUnavailable(DEFUNCT_READ)),
              _chained(kinds.syntax(), kinds.ServiceUnavailable(DEFUNCT_READ))], True, 1, ""),
        ]
        for what, errors, want_err, want_calls, want_text in cases:
            script = _Script(errors, _answer(tool))
            args = _wire(S, tool, script)
            with _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
                err, out, stderr = _call(S, tool, args)
            S._AUTH.success()
            ok = err == want_err and script.calls == want_calls and want_text in str(out)
            check(f"{tool}: {what}", ok, f"isError={err} attempts={script.calls} out={str(out)[:160]!r}")
            if not want_err:
                check(f"{tool}: ...and the server's stderr says it retried on a fresh connection",
                      "retrying once" in stderr.lower(), stderr[-200:])
    script = _Script([kinds.ServiceUnavailable(DEFUNCT_READ)], _answer("deepmap_cypher"))
    args = _wire(S, "deepmap_cypher", script)
    err, out, _ = _call(S, "deepmap_cypher", {"query": "MATCH (n) DETACH DELETE n"})
    check("deepmap_cypher: a rejected query never reaches the driver (0 attempts, no retry)",
          err and "DETACH" in str(out) and script.calls == 0, (err, script.calls))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — the search server survives a restart of its Neo4j (liveness check + one retry) — no Neo4j")
    print("=" * 72)
    sys.path.insert(0, str(BUILD))
    with tempfile.TemporaryDirectory(prefix="dm_reconnect_gates_") as home:
        kwargs = gate_driver_config(home)
        gate_real_driver_accepts(kwargs)
        with _env(**{k: None for k in _ENV_KEYS}, DEEPMAP_HOME=home), _modules(embeddings=_fake_embeddings()):
            S = _load("deepmap_search_server", "dm_reconnect_server")
            S._touch_last_used = lambda: None
            gate_driver_wording(S)
            gate_retry(S, types.SimpleNamespace(ServiceUnavailable=_Doubles.ServiceUnavailable,
                                                SessionExpired=_Doubles.SessionExpired,
                                                auth=lambda: _Doubles.AuthError("unauthorized"),
                                                syntax=lambda: _Doubles.SyntaxErr("Invalid input")),
                       "name-only doubles")
            real = _real_kinds()
            if real is None:
                check("(no neo4j driver installed: the real-exception pass is skipped)", True)
            else:
                gate_retry(S, real, "the driver's real exception classes")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
