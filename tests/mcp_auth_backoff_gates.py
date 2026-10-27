#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: the MCP search server BACKS OFF after Neo4j rejects its credentials (v0.28.0 round 3, E4).

THE PROBLEM: a DeepMap search server started with credentials its Neo4j rejects (a wrong NEO4J_PASSWORD, a
store initialised with other credentials, another program's Neo4j on the port) re-tried the login on EVERY
tool call — and the warm-up thread tried once more at start. Neo4j counts failed logins per user and, after
dbms.security.auth_max_failed_attempts (3), LOCKS that user for auth_lock_time; each further failure keeps
the lock engaged. So a misconfigured MCP server kept `neo4j` locked out for sync, the CLI and setup's own
checks (the live-found "Neo4j read as down while it was locking logins" case in provision._bolt_state).
Impact made it worse: a rate-limit error is a plain ClientError, which impact treated as an unsupported
query feature — it fell back to a one-hop walk and so logged in a SECOND time per call.

THE FIX (locked here): mcp_bounds.AuthBackoff. After a rejected login (AuthError / Unauthorized, the
AuthenticationRateLimit lock, CredentialsExpired) the server answers every call with a clear isError —
naming the endpoint, why it is not retrying, and a runnable next step (/deep-map:doctor) — WITHOUT
contacting Neo4j, for a window that starts at DEEPMAP_MCP_AUTH_BACKOFF_S (10 s) and doubles per
consecutive rejection up to DEEPMAP_MCP_AUTH_BACKOFF_MAX_S (120 s). It then retries once; a success resets
it. Concurrent first calls log in once (the check sits inside the retriever's init lock), the start-up
warm-up's failure counts, a rejection in the middle of a query counts, and an OUTAGE is NOT an auth
failure (a down Neo4j is still re-tried on every call, fail-loud, T10). Impact now fails loud on a
Neo.ClientError.Security.* error instead of degrading into a second login.

Driven through the REAL mcp ClientSession (in-memory) against the REAL server + retriever, with only the
neo4j driver's GraphDatabase faked (the real neo4j exception classes). Hermetic: temp DEEPMAP_HOME, fake
embeddings, no sockets, never touches ~/.deepmap or the committed .mcp.json.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
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


def _neo4j_err(code: str, message: str):
    from neo4j import exceptions as E
    return E.Neo4jError._hydrate_neo4j(code=code, message=message)


def _auth_error():
    return _neo4j_err("Neo.ClientError.Security.Unauthorized",
                      "The client is unauthorized due to authentication failure.")


def _rate_limited():
    return _neo4j_err("Neo.ClientError.Security.AuthenticationRateLimit",
                      "The client has provided incorrect authentication details too many times in a row.")


def _down():
    from neo4j.exceptions import ServiceUnavailable
    return ServiceUnavailable("Couldn't connect to localhost:17699 (fake instance down)")


# ---------------------------------------------------------------- fake neo4j instance ------------------
class _World:
    """What the fake Neo4j does: `login` = None (accepts) or a factory for the login error; `body_error` =
    a factory raised by every query (credentials revoked after the driver connected)."""

    def __init__(self, login=_auth_error):
        self.login = login
        self.body_error = None
        self.attempts = 0      # logins (verify_connectivity) the server made
        self.queries = 0       # session.run calls the server made


class _Session:
    def __init__(self, w: _World):
        self.w = w

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.w.queries += 1
        if self.w.body_error is not None:
            raise self.w.body_error()
        cypher = str(cypher)
        if "SEARCH n IN" in cypher:
            raise RuntimeError("SEARCH clause not supported (fake)")
        if "db.index.fulltext.queryNodes" in cypher:
            return [{"id": "fts1", "name": "auth_flow", "type": "Function", "file": "auth.py", "line": 3,
                     "summary": "handles the auth flow", "score": 3.0}]
        if "db.index.vector.queryNodes" in cypher:
            return []
        if "UNWIND $ids" in cypher:
            return [{"id": "fts1", "name": "auth_flow", "type": "Function", "file": "auth.py", "line": 3,
                     "summary": "", "confidence": "EXTRACTED"} for i in params.get("ids", []) if i == "fts1"]
        if "count(callee)" in cypher:
            return [{"matched": 1}]
        if "db.relationshipTypes" in cypher:
            return [{"types": ["CALLS"]}]
        if "caller" in cypher:
            return [{"caller": "main", "caller_type": "Function", "file": "m.py", "line": 1,
                     "confidence": "INFERRED", "callee": "auth_flow", "hop": 1, "via": "CALLS",
                     "via_path": ["CALLS"]}]
        return []


class _Driver:
    def __init__(self, w: _World):
        self.w = w

    def verify_connectivity(self):
        self.w.attempts += 1
        time.sleep(0.05)                    # a login round-trip, so concurrent first calls really overlap
        if self.w.login is not None:
            raise self.w.login()

    def session(self, **kw):
        return _Session(self.w)

    def close(self):
        pass


def _fake_embeddings() -> types.ModuleType:
    m = types.ModuleType("embeddings")

    class ModelUnavailable(RuntimeError):
        pass

    m.ModelUnavailable = ModelUnavailable
    m.ensure_model = lambda max_wait_s=None, retry_after_s=None: object()
    m.embed_query = lambda text: [0.01] * 768
    m._get_model = lambda: object()
    m.model_status = lambda: {"state": "ready"}
    return m


@contextlib.contextmanager
def _server(w: _World):
    """A fresh search server whose `from neo4j import GraphDatabase` yields the fake instance `w`."""
    import neo4j
    saved_gdb, saved_emb = neo4j.GraphDatabase, sys.modules.get("embeddings")

    class _GDB:
        @staticmethod
        def driver(*a, **kw):
            return _Driver(w)

    neo4j.GraphDatabase = _GDB
    sys.modules["embeddings"] = _fake_embeddings()
    try:
        spec = importlib.util.spec_from_file_location(f"dm_srv_auth_{time.monotonic_ns()}",
                                                      BUILD / "deepmap_search_server.py")
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        yield srv
    finally:
        neo4j.GraphDatabase = saved_gdb
        if saved_emb is None:
            sys.modules.pop("embeddings", None)
        else:
            sys.modules["embeddings"] = saved_emb


def _calls(srv, calls, concurrent: bool = False):
    """[(tool, args)] through ONE real in-memory ClientSession -> [(is_error, payload_or_text, seconds)]."""
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    out: list = [None] * len(calls)

    async def one(client, i, name, args):
        t0 = time.monotonic()
        res = await client.call_tool(name, args)
        took = time.monotonic() - t0
        if res.isError:
            out[i] = (True, " ".join(getattr(c, "text", "") for c in res.content), took)
            return
        sc = res.structuredContent
        payload = sc["result"] if isinstance(sc, dict) and "result" in sc else [
            json.loads(c.text) for c in res.content if getattr(c, "text", None)]
        out[i] = (False, payload, took)

    async def go():
        async with create_connected_server_and_client_session(srv.mcp) as client:
            if concurrent:
                async with anyio.create_task_group() as tg:
                    for i, (name, args) in enumerate(calls):
                        tg.start_soon(one, client, i, name, args)
            else:
                for i, (name, args) in enumerate(calls):
                    await one(client, i, name, args)

    anyio.run(go)
    return out


SEARCH = ("deepmap_search", {"query": "auth flow"})
IMPACT = ("deepmap_impact", {"symbol": "auth_flow"})


def _hit(payload) -> bool:
    return isinstance(payload, list) and any(isinstance(p, dict) and p.get("name") == "auth_flow" for p in payload)


# ------------------------------------------------------------------------------------------------ gates
def gate_wrong_password_backs_off() -> None:
    print("\n-- a REJECTED login is not re-tried on every call; then retried with a doubling window --")
    w = _World(login=_auth_error)
    # Windows of 1.5 s / 3 s: roomy enough that a loaded CI box can't blur "inside" and "after" the window.
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="1.5", DEEPMAP_MCP_AUTH_BACKOFF_MAX_S="10"):
        (e1, t1, _s1), = _calls(srv, [SEARCH])
        check("the first call is an isError (never an empty answer)", e1, str(t1)[:200])
        check("the error says Neo4j REJECTED the server's credentials and names the endpoint",
              "rejected" in t1.lower() and "credentials" in t1.lower() and "bolt://" in t1, t1[:400])
        check("it says why it will not retry (Neo4j's login lock) and for how long",
              "login lock" in t1.lower() and "not try again" in t1.lower(), t1[:400])
        check("it gives a RUNNABLE next step (/deep-map:doctor), no relative python path",
              "/deep-map:doctor" in t1 and "python build/" not in t1, t1[:400])
        check("exactly one login so far", w.attempts == 1, str(w.attempts))
        res = _calls(srv, [SEARCH, IMPACT, SEARCH])
        check("calls inside the window are refused too (search + impact)", all(r[0] for r in res),
              str([r[1][:80] for r in res]))
        check("...WITHOUT another login attempt (Neo4j's failed-login counter is not fed)", w.attempts == 1,
              str(w.attempts))
        check("...and they answer at once (no connect / no wait)", all(r[2] < 0.5 for r in res),
              str([round(r[2], 2) for r in res]))
        check("the in-window refusal carries the same diagnosis + next step",
              all("credentials" in r[1].lower() and "/deep-map:doctor" in r[1] for r in res), res[0][1][:300])
        time.sleep(1.6)
        (e2, t2, _), = _calls(srv, [SEARCH])
        check("after the window the server tries ONE login again", e2 and w.attempts == 2, f"{w.attempts} {t2[:120]}")
        check("a second consecutive rejection doubles the window (streak 2)", getattr(srv._AUTH, "streak", None) == 2,
              repr(getattr(srv._AUTH, "streak", None)))
        time.sleep(1.0)
        (e3, _t3, _), = _calls(srv, [SEARCH])
        check("1 s later (inside the doubled 3 s window) there is still no new login",
              e3 and w.attempts == 2, str(w.attempts))
        w.login = None                                     # the credentials were fixed
        time.sleep(2.2)
        (e4, p4, _), (e5, p5, _) = _calls(srv, [SEARCH, IMPACT])
        check("once the credentials work, the next call after the window SUCCEEDS", not e4 and _hit(p4), str(p4)[:200])
        check("impact answers too", not e5, str(p5)[:200])
        check("a success resets the backoff (streak 0)", getattr(srv._AUTH, "streak", None) == 0,
              repr(getattr(srv._AUTH, "streak", None)))


def gate_concurrent_first_calls_log_in_once() -> None:
    print("\n-- concurrent first calls against a rejecting Neo4j log in ONCE --")
    w = _World(login=_auth_error)
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
        res = _calls(srv, [SEARCH, SEARCH, IMPACT, SEARCH], concurrent=True)
    check("all four concurrent calls are isError", all(r and r[0] for r in res), str(res)[:300])
    check("only ONE login attempt was made", w.attempts == 1, str(w.attempts))
    check("every refusal names the credential problem", all(r and "credentials" in r[1].lower() for r in res),
          str([r[1][:80] for r in res if r]))


def gate_rate_limit_is_auth() -> None:
    print("\n-- Neo4j's login LOCK (AuthenticationRateLimit, a plain ClientError) is an auth failure too --")
    w = _World(login=_rate_limited)
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
        res = _calls(srv, [SEARCH, IMPACT])
    check("both calls are isError", all(r[0] for r in res), str(res)[:300])
    check("the error says Neo4j is temporarily refusing logins after repeated failures",
          "repeated" in res[0][1].lower() and "login" in res[0][1].lower(), res[0][1][:300])
    check("the second call did not log in again", w.attempts == 1, str(w.attempts))


def gate_outage_is_not_backed_off() -> None:
    print("\n-- a DOWN Neo4j is NOT an auth failure: re-tried on every call, fail-loud (T10) --")
    w = _World(login=_down)
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
        res = _calls(srv, [SEARCH, SEARCH, IMPACT])
        streak = getattr(getattr(srv, "_AUTH", None), "streak", 0)
    check("every call is isError naming the connection problem",
          all(r[0] and ("connect" in r[1].lower() or "unavailable" in r[1].lower()) for r in res),
          str([r[1][:80] for r in res]))
    check("every call re-tried the connection (a restarted Neo4j is picked up at once)", w.attempts == 3,
          str(w.attempts))
    check("no auth backoff was armed", not streak, repr(streak))


def gate_rejection_mid_query() -> None:
    print("\n-- credentials rejected AFTER the driver connected (a query fails with AuthError) --")
    w = _World(login=None)
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="1.5"):
        (e0, p0, _), = _calls(srv, [SEARCH])
        check("fixture: a healthy search first", not e0 and _hit(p0), str(p0)[:200])
        w.body_error = _auth_error
        (e1, t1, _), = _calls(srv, [SEARCH])
        check("the failing query is an isError naming the credential problem",
              e1 and "credentials" in t1.lower(), str(t1)[:300])
        q = w.queries
        res = _calls(srv, [SEARCH, IMPACT])
        check("the next calls inside the window are refused without touching Neo4j (no query ran)",
              all(r[0] for r in res) and w.queries == q, f"queries {q} -> {w.queries}")
        w.body_error = None
        time.sleep(1.6)
        (e2, p2, _), = _calls(srv, [SEARCH])
        check("after the window it recovers", not e2 and _hit(p2), str(p2)[:200])


def gate_warmup_failure_counts() -> None:
    print("\n-- the start-up warm-up's rejected login counts: the first call does not log in again --")
    w = _World(login=_auth_error)
    with _server(w) as srv, _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
        with contextlib.redirect_stderr(io.StringIO()):
            srv._warm_up()
        check("fixture: the warm-up tried to log in once", w.attempts == 1, str(w.attempts))
        (e1, t1, _), = _calls(srv, [SEARCH])
    check("the first tool call is refused with the credential diagnosis", e1 and "credentials" in t1.lower(),
          str(t1)[:300])
    check("...without a second login", w.attempts == 1, str(w.attempts))


def gate_impact_security_error_fails_loud() -> None:
    print("\n-- impact: a Neo.ClientError.Security.* error fails loud (no one-hop fallback = no 2nd login) --")
    spec = importlib.util.spec_from_file_location("dm_retrieval_auth", BUILD / "retrieval.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    for desc, exc, want in (
            ("AuthenticationRateLimit", _rate_limited(), True),
            ("CredentialsExpired", _neo4j_err("Neo.ClientError.Security.CredentialsExpired", "must be changed"),
             True),
            ("Unauthorized (AuthError)", _auth_error(), True),
            ("a Cypher syntax error (an unsupported feature on an old Neo4j)",
             _neo4j_err("Neo.ClientError.Statement.SyntaxError", "Invalid input '*'"), False)):
        check(f"_impact_should_raise({desc}) is {want}", R._impact_should_raise(exc) is want)
    sessions = {"n": 0}

    class _Drv:
        def session(self, **kw):
            sessions["n"] += 1
            outer = self

            class _S:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def run(self, *a, **k):
                    raise _rate_limited()
            return _S()

    raised = None
    try:
        R.query_impact(_Drv(), "auth_flow", limit=5)
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("query_impact re-raises the login lock (never a fabricated one-hop answer)",
          raised is not None and "AuthenticationRateLimit" in str(getattr(raised, "code", "")), repr(raised)[:200])
    check("...after ONE session (the one-hop fallback did not open a second connection / login)",
          sessions["n"] == 1, str(sessions["n"]))


def gate_bounds_unit() -> None:
    print("\n-- mcp_bounds: the auth classifier, the backoff arithmetic, the env knobs --")
    spec = importlib.util.spec_from_file_location("dm_bounds_auth", BUILD / "mcp_bounds.py")
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    fn = getattr(B, "is_neo4j_auth_error", None)
    check("mcp_bounds.is_neo4j_auth_error exists", callable(fn))
    check("mcp_bounds.AuthBackoff exists", hasattr(B, "AuthBackoff"))
    if not callable(fn) or not hasattr(B, "AuthBackoff"):
        return
    wrapped = RuntimeError("the retriever gave up")
    wrapped.__cause__ = _auth_error()

    class _Weird(Exception):
        def __str__(self):
            raise ValueError("unprintable")

    for desc, exc, want in (("AuthError", _auth_error(), True), ("AuthenticationRateLimit", _rate_limited(), True),
                            ("an error raised FROM an AuthError", wrapped, True),
                            ("ServiceUnavailable", _down(), False), ("a ValueError", ValueError("x"), False),
                            ("an exception whose str() raises", _Weird(), False), ("None", None, False)):
        try:
            got = fn(exc)
        except Exception as e:  # noqa: BLE001
            got = f"raised {type(e).__name__}"
        check(f"is_neo4j_auth_error({desc}) is {want}", got is want, repr(got))
    now = [100.0]
    with _env(DEEPMAP_MCP_AUTH_BACKOFF_S="10", DEEPMAP_MCP_AUTH_BACKOFF_MAX_S="35"):
        b = B.AuthBackoff("Neo4j at bolt://localhost:17999", clock=lambda: now[0])
        windows = []
        for _ in range(4):
            e = _auth_error()
            b.record(e)
            windows.append(round(b.remaining(), 3))
            now[0] += 100
        check("the window doubles per consecutive rejection and is capped (10, 20, 35, 35)",
              windows == [10.0, 20.0, 35.0, 35.0], str(windows))
        e = _auth_error()
        first = b.record(e)
        left = b.remaining()
        again = b.record(e)
        check("the SAME exception seen twice (retriever init + tool wrapper) counts once",
              b.remaining() == left and b.streak == 5, f"{b.streak} {left} {b.remaining()}")
        check("record() returns the friendly error for an auth failure, None otherwise",
              isinstance(first, B.Neo4jAuthBackoff) and isinstance(again, B.Neo4jAuthBackoff)
              and b.record(ValueError("x")) is None)
        started = now[0] - 1                                     # a call that began BEFORE the last rejection
        b.success(since=started)
        check("a success that STARTED before the latest rejection does not reset it", b.streak == 5, str(b.streak))
        b.success(since=now[0] + 1)
        check("a success after it resets the backoff", b.streak == 0 and b.remaining() == 0)
        blocked = None
        try:
            b.check()
        except Exception as exc:  # noqa: BLE001
            blocked = exc
        check("check() passes when no rejection is pending", blocked is None, repr(blocked))
        for _ in range(200):
            b.record(_auth_error())
        check("a long streak never overflows (the window stays at the cap)", b.remaining() == 35.0, str(b.remaining()))
    for raw, want in (("7.5", 7.5), ("banana", B.AUTH_BACKOFF_S), ("-1", B.AUTH_BACKOFF_S)):
        with _env(DEEPMAP_MCP_AUTH_BACKOFF_S=raw):
            check(f"auth_backoff_s() with env {raw!r} -> {want}", B.auth_backoff_s() == want, str(B.auth_backoff_s()))
    with _env(DEEPMAP_MCP_AUTH_BACKOFF_S="50", DEEPMAP_MCP_AUTH_BACKOFF_MAX_S="5"):
        check("the cap is never below the base window", B.auth_backoff_max_s() >= 50, str(B.auth_backoff_max_s()))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — MCP search server backs off after rejected Neo4j logins (E4; real MCP client; no Neo4j)")
    print("=" * 72)
    with tempfile.TemporaryDirectory() as home, _env(
            DEEPMAP_HOME=home, NEO4J_URI="bolt://localhost:17699", DEEPMAP_MCP_TRANSPORT=None,
            DEEPMAP_RETRIEVAL_LOG=None, DEEPMAP_APPLY_LEARNED=None, DEEPMAP_PROJECT_ROOT=None,
            DEEPMAP_PROJECT_KEY=None, DEEPMAP_MCP_WARMUP_TIMEOUT_S=None, DEEPMAP_MCP_TOOL_TIMEOUT_S=None,
            DEEPMAP_MCP_WARMUP="0", DEEPMAP_MCP_AUTH_BACKOFF_S=None, DEEPMAP_MCP_AUTH_BACKOFF_MAX_S=None), \
            contextlib.redirect_stderr(io.StringIO()):
        for gate in (gate_wrong_password_backs_off, gate_concurrent_first_calls_log_in_once, gate_rate_limit_is_auth,
                     gate_outage_is_not_backed_off, gate_rejection_mid_query, gate_warmup_failure_counts,
                     gate_impact_security_error_fails_loud, gate_bounds_unit):
            try:
                gate()
            except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crashed suite
                check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
