#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the CLI's round-8 fixes (2026-09 live re-validation, owner W3).

Locks:
  A  J4  every bolt URI the CLI GENERATES dials 127.0.0.1 (the address a DeepMap instance binds): on Windows
         "localhost" resolves to ::1 first, so each new connection paid a refused IPv6 connect (~2 s), and a
         listener on [::1]:<port> (Docker publishes [::1]:7687) was reached instead of the instance.
           * no string in build/deepmap_cli.py spells a DeepMap-generated "bolt://localhost" URI;
           * the port_registry-less fallback route is bolt://127.0.0.1:7687 (port_registry.bolt_uri(BASE_BOLT));
           * a registry route dials port_registry.bolt_uri(<its port>); an operator NEO4J_URI is used VERBATIM;
           * the base-claim check (ROUND-5 G1) still recognises a base route written by an older build
             (bolt://localhost:7687, bolt://[::1]:7687), so the upgrade keeps the F2 protection.
  B  J6  read commands (search / impact / status) retry ONCE on a CONNECTION-LEVEL drop — a Neo4j JVM whose
         Bolt event loop died (after a long sleep/resume) force-closes about 1 in 20-40 new connections:
           * a reset / closed-mid-handshake / defunct connection is classified 'dropped' (not 'hung', whose
             remedy text said "did not answer in time"), and is retried once with a fresh driver; the answer is
             printed once, a one-line stderr note says it was retried, both drivers are closed;
           * a second drop fails with the 'dropped' message (closed the connection twice, restart with stop +
             heal; never "stopped or hibernated") after exactly TWO attempts;
           * refused / hung (timeout) / auth / server-side query errors / an unresolvable project are NEVER
             retried (a timeout retry would double a 30 s wait);
           * status prints its offline "learning:" line once; search embeds the query once across a retry;
           * a handshake failure whose cause type was lost (an operator localhost URI: the chain carries the ::1
             refusal) is retried only when it failed fast (a hung JVM fails at the deadline).
  C  J6  doctor reports a partly dead Bolt acceptor:
           * its census is retried once on a drop, and a drop it survived is a WARN + a verdict reason naming the
             restart (stop, then heal);
           * this project's own instance log (<home>/instances/<key>/logs/debug.log) recording force-closed
             channels ("event executor terminated") SINCE THE INSTANCE LAST STARTED is a WARN "Bolt acceptor" line
             with the count, the last time and the restart remedy + a verdict reason; events before the last
             start banner, an operator NEO4J_URI (not DeepMap's log) and a stopped instance never count; the scan
             reads a bounded tail and never raises.
  D  J6  core.longpaths: doctor/status freshness git probes pass `-c core.longpaths=true` on Windows (a committed
         file past MAX_PATH read as an uncommitted change), and a long path's mtime is read through its \\\\?\\ form;
         proven on a REAL ~215-char repo root (Windows only).

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers (neo4j.GraphDatabase.driver patched), temp git repos; never touches
~/.deepmap or the committed .mcp.json. No sockets. Helpers shared with tests/cli_route_impact_gates.py.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
CLI_PATH = BUILD / "deepmap_cli.py"
_WIN = os.name == "nt"
_LONG = ["-c", "core.longpaths=true"]
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x", "GIT_CONFIG_NOSYSTEM": "1"}

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load_module(REPO / "tests" / "cli_route_impact_gates.py", f"dm_cli_r2_helpers8_{time.time_ns()}")


# ---- real-driver-shaped exception chains ------------------------------------------------------------------
def _E():
    import neo4j.exceptions as E
    return E


def _outer(inner, port=17689):
    E = _E()
    out = E.ServiceUnavailable(f"Couldn't connect to 127.0.0.1:{port} (resolved to ('127.0.0.1:{port}',)):\n"
                               + str(inner))
    out.__cause__ = inner
    return out


def _reset_exc(port=17689):
    """The chain the real driver raises when the server RESETS the socket during the handshake (a force-closed
    channel with the client's handshake bytes unread -> RST): the handshake read's OSError is the cause."""
    E = _E()
    inner = E.ServiceUnavailable(f"Failed to read four byte Bolt handshake response from server "
                                 f"ResolvedIPv4Address(('127.0.0.1', {port})) (deadline Deadline(timeout=30.0))")
    inner.__cause__ = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    return _outer(inner, port)


def _fin_exc(port=17689):
    """The server closed the socket (FIN) before answering the handshake."""
    E = _E()
    inner = E.ServiceUnavailable(f"Connection to ResolvedIPv4Address(('127.0.0.1', {port})) closed with incomplete "
                                 "handshake response")
    return _outer(inner, port)


def _defunct_exc(port=17689):
    """A pooled connection that died under a query (mid-session)."""
    E = _E()
    e = E.SessionExpired(f"Failed to read from defunct connection IPv4Address(('127.0.0.1', {port})) "
                         f"(ResolvedIPv4Address(('127.0.0.1', {port})))")
    e.__cause__ = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    return e


def _hung_exc(port=17689):
    E = _E()
    inner = E.ServiceUnavailable(f"Failed to read four byte Bolt handshake response from server "
                                 f"ResolvedIPv4Address(('127.0.0.1', {port})) (deadline Deadline(timeout=15))")
    inner.__cause__ = TimeoutError("timed out")
    return _outer(inner, port)


def _refused_exc(port=17689):
    E = _E()
    out = E.ServiceUnavailable(f"Couldn't connect to 127.0.0.1:{port} (resolved to ('127.0.0.1:{port}',)):\n"
                               f"Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', {port})) "
                               "(reason [WinError 10061] No connection could be made because the target machine "
                               "actively refused it)")
    out.__cause__ = ConnectionRefusedError(10061, "No connection could be made because the target machine actively "
                                                  "refused it")
    return out


def _dual_stack_exc(port=7999):
    """An operator URI bolt://localhost:<port>: the driver tries ::1 (refused) then 127.0.0.1 (handshake read
    failed) and raises `from errors[0]` — the ::1 refusal; the 127.0.0.1 failure survives only as TEXT."""
    E = _E()
    out = E.ServiceUnavailable(f"Couldn't connect to localhost:{port} (resolved to ('[::1]:{port}', '127.0.0.1:{port}')):\n"
                               f"Failed to establish connection to ResolvedIPv6Address(('::1', {port}, 0, 0)) "
                               "(reason [WinError 10061] refused)\n"
                               f"Failed to read four byte Bolt handshake response from server "
                               f"ResolvedIPv4Address(('127.0.0.1', {port})) (deadline Deadline(timeout=30.0))")
    out.__cause__ = ConnectionRefusedError(10061, "refused")
    return out


def _auth_exc():
    E = _E()
    return E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.Unauthorized",
                                       message="The client is unauthorized due to authentication failure.")


def _query_exc():
    E = _E()
    return E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="Invalid input")


# ---- fake drivers -------------------------------------------------------------------------------------------
class _DropOnceDriver(H._GraphDriver):
    """verify_connectivity OK, but every session.run raises `exc` (a connection that dies under a query)."""

    def __init__(self, exc, **kw):
        super().__init__(**kw)
        self.run_exc = exc

    def session(self, **kw):
        drv = self

        class _S(H._Session):
            def run(self, q, **k):
                drv.queries.append(str(getattr(q, "text", q)))
                raise drv.run_exc
        return _S(self)


class _Seq:
    """A driver factory returning the drivers of `makers` in order (the last one repeats)."""

    def __init__(self, *makers):
        self.makers = list(makers)
        self.calls: list = []
        self.made: list = []

    def __call__(self, uri, *a, **kw):
        self.calls.append((uri, kw))
        mk = self.makers[min(len(self.calls) - 1, len(self.makers) - 1)]
        d = mk()
        self.made.append(d)
        return d


def _register(PR, root: Path, **fields) -> None:
    ent = {"bolt_port": 17689, "http_port": 17476, "search_port": 18911, "embed_port": 18912}
    ent.update(fields)
    H._write_registry(PR, {root: ent})


def _run(C, argv, factory, *, cwd, overrides=None, env=None):
    with H._env(**(env or {})), H._patched_graphdb(factory):
        return H._main(C, argv, overrides=overrides, cwd=cwd)


def _reasons(out: str) -> list:
    lines, on = [], False
    for ln in out.splitlines():
        if "[deepmap] verdict:" in ln:
            on = True
            continue
        if on and ln.startswith("  - "):
            lines.append(ln[4:])
        elif on and ln.strip() and not ln.startswith("  - "):
            break
    return lines


# =========================================================================== A: J4 URIs
def gate_a_uris(C, PR, work: Path) -> None:
    print("\n-- A (J4): generated bolt URIs dial 127.0.0.1; operator NEO4J_URI verbatim --")
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    bad = [(n.lineno, n.value[:80]) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and "bolt://localhost" in n.value]
    check("no string in build/deepmap_cli.py spells a DeepMap-generated 'bolt://localhost' URI", not bad, bad)

    saved = sys.modules.get("port_registry", "<absent>")
    sys.modules["port_registry"] = None   # `import port_registry` now raises ImportError
    try:
        with H._env(NEO4J_URI=None):
            C._LAST_ROUTE = None
            r = C._neo4j_route()
    finally:
        if saved == "<absent>":
            sys.modules.pop("port_registry", None)
        else:
            sys.modules["port_registry"] = saved
    check("port_registry unavailable: the fallback route is bolt://127.0.0.1:7687 (legacy, verified before use)",
          getattr(r, "uri", None) == "bolt://127.0.0.1:7687" == PR.bolt_uri(PR.BASE_BOLT)
          and getattr(r, "source", None) == "legacy-unverified", (getattr(r, "uri", None), getattr(r, "source", None)))

    p = work / "a_reg"
    p.mkdir()
    _register(PR, p)
    fac = _Seq(lambda: H._GraphDriver(owner=PR.project_identity(p), nodes=3))
    rc, raised, out, err = _run(C, ["status"], fac, cwd=p)
    check("registry route: the driver dials port_registry.bolt_uri(<its bolt port>) = bolt://127.0.0.1:17689",
          raised is None and rc == 0 and [u for u, _ in fac.calls] == [PR.bolt_uri(17689)] == ["bolt://127.0.0.1:17689"],
          f"{rc} {raised!r} {fac.calls}")
    check("status names the URI it used (bolt://127.0.0.1:17689)", "DeepMap status @ bolt://127.0.0.1:17689" in out,
          out[:300])
    fac = _Seq(lambda: H._GraphDriver(owner=PR.project_identity(p), nodes=3))
    rc, raised, out, err = _run(C, ["status"], fac, cwd=p, env={"NEO4J_URI": "bolt://localhost:7999"})
    check("operator NEO4J_URI=bolt://localhost:7999: used VERBATIM (never rewritten)",
          [u for u, _ in fac.calls] == ["bolt://localhost:7999"], fac.calls)
    H._clear_registry(PR)

    R = C._Route
    with H._env(NEO4J_URI=None):
        cases = {
            "bolt://127.0.0.1:7687 (this build)": (R("bolt://127.0.0.1:7687", "registry", bolt=7687), True),
            "bolt://localhost:7687 (an older build's route)": (R("bolt://localhost:7687", "registry", bolt=7687), True),
            "bolt://[::1]:7687": (R("bolt://[::1]:7687", "registry", bolt=7687), True),
            "a per-project instance (7689)": (R("bolt://127.0.0.1:7689", "registry", bolt=7689), False),
            "a remote host on 7687": (R("bolt://db.example.invalid:7687", "registry", bolt=7687), False),
            "the legacy route (verified separately)": (R("bolt://127.0.0.1:7687", "legacy-unverified", bolt=7687), False),
        }
        for label, (route, want) in cases.items():
            got = C._is_base_claim_route(route)
            check(f"base-claim check: {label} -> {want}", got is want, got)
    with H._env(NEO4J_URI="bolt://localhost:7687"):
        got = C._is_base_claim_route(R("bolt://localhost:7687", "registry", bolt=7687))
    check("base-claim check: never with an operator NEO4J_URI", got is False, got)


# =========================================================================== B: J6 read retry
def gate_b_classify(C) -> None:
    print("\n-- B1 (J6): a connection DROP is classified 'dropped' (not 'hung'); controls unchanged --")
    for label, exc in (("a RESET during the Bolt handshake (ConnectionResetError cause)", _reset_exc()),
                       ("the server CLOSED the socket mid-handshake (incomplete handshake response)", _fin_exc()),
                       ("a pooled connection that died under a query (defunct, reset)", _defunct_exc())):
        got = C._classify_db_error(exc)
        check(f"{label} -> 'dropped'", got == "dropped", got)
    E = _E()
    acq = getattr(E, "ConnectionAcquisitionTimeoutError", None)
    defunct_to = E.SessionExpired("Failed to read from defunct connection IPv4Address(('127.0.0.1', 1)) (timed out)")
    controls = [("a handshake read TIMEOUT stays 'hung'", _hung_exc(), "hung"),
                ("a refused connection stays 'unreachable'", _refused_exc(), "unreachable"),
                ("an auth failure stays 'auth'", _auth_exc(), "auth"),
                ("a server-side query error stays 'query'", _query_exc(), "query"),
                ("a defunct connection that TIMED OUT stays 'hung'", defunct_to, "hung"),
                ("an unresolvable project stays 'resolve'", C.ProjectResolutionError("no route"), "resolve")]
    if isinstance(acq, type):
        controls.append(("a pool acquisition timeout stays 'hung'", acq("failed to obtain a connection from the "
                                                                          "pool within 30.0s (timeout)"), "hung"))
    for label, exc, want in controls:
        got = C._classify_db_error(exc)
        check(f"control: {label}", got == want, got)


def gate_b_retry(C, PR, work: Path) -> None:
    print("\n-- B2 (J6): search / impact / status retry ONCE on a dropped connection --")
    p = work / "b_retry"
    p.mkdir()
    _register(PR, p)
    ident = PR.project_identity(p)
    good = lambda: H._GraphDriver(owner=ident, nodes=4)   # noqa: E731

    # --- search: first driver's connect is reset, the retry answers
    captured: dict = {}
    fac = _Seq(lambda: H._GraphDriver(owner=ident, session_exc=_reset_exc()), good)
    rc, raised, out, err = _run(C, ["search", "where is x"], fac, cwd=p, overrides=H._search_stubs(captured))
    check("search: a reset on the first connection is retried -> exit 0, no traceback",
          raised is None and rc == 0, f"{rc} {raised!r} {err[-400:]}")
    check("search: exactly two drivers (one retry), both closed",
          len(fac.calls) == 2 and all(d.closed for d in fac.made), f"{len(fac.calls)} {[d.closed for d in fac.made]}")
    check("search: the answer is printed once", out.count("results for") == 1, out[-300:])
    check("search: one stderr note says the connection was dropped and retried",
          err.count("retrying once") == 1 and "closed" in err, err[-400:])

    # --- two drops in a row: fail with the 'dropped' message after exactly two attempts
    fac = _Seq(lambda: H._GraphDriver(owner=ident, session_exc=_reset_exc()))
    rc, raised, out, err = _run(C, ["search", "where is x"], fac, cwd=p, overrides=H._search_stubs({}))
    check("search: a second drop -> exit 3 after exactly TWO attempts (retry once, never more)",
          raised is None and rc == 3 and len(fac.calls) == 2, f"{rc} {raised!r} {len(fac.calls)}")
    check("... worded as DROPPED connections (closed twice) with the restart remedy (stop, then heal)",
          "closed" in err and "twice" in err and " stop " in err and " heal " in err
          and "did not answer in time" not in err and "stopped or hibernated" not in err, err[-700:])

    # --- impact: the connection dies UNDER the walk query (mid-session), the retry answers
    rows = [H._row(1), H._row(2)]
    fac = _Seq(lambda: _DropOnceDriver(_defunct_exc(), owner=ident), lambda: H._impact_driver(rows=rows))
    rc, raised, out, err = _run(C, ["impact", "target"], fac, cwd=p)
    check("impact: a connection that dies under the query is retried -> exit 0 with the dependents",
          raised is None and rc == 0 and "2 dependent(s)" in out and out.count("dependent(s) of") == 1,
          f"{rc} {raised!r} {out[-300:]} {err[-300:]}")
    check("impact: two drivers, both closed", len(fac.calls) == 2 and all(d.closed for d in fac.made), fac.calls)

    # --- status: the offline learning line is printed ONCE across a retry
    fac = _Seq(lambda: H._GraphDriver(owner=ident, session_exc=_fin_exc()), good)
    rc, raised, out, err = _run(C, ["status"], fac, cwd=p)
    check("status: a mid-handshake close is retried -> exit 0",
          raised is None and rc == 0 and len(fac.calls) == 2, f"{rc} {raised!r} {len(fac.calls)} {err[-300:]}")
    check("status: 'learning:' and the census header are printed once",
          out.count("learning:") == 1 and out.count("DeepMap status @") == 1, out[:400])

    # --- non-drops are NEVER retried
    for label, exc in (("refused (instance down)", _refused_exc()), ("hung (handshake timeout)", _hung_exc()),
                       ("auth failure", _auth_exc()), ("server-side query error", _query_exc())):
        fac = _Seq(lambda e=exc: H._GraphDriver(owner=ident, session_exc=e))
        rc, raised, out, err = _run(C, ["status"], fac, cwd=p)
        check(f"status: {label} is NOT retried (one driver, exit 3)",
              raised is None and rc == 3 and len(fac.calls) == 1 and "retrying" not in err,
              f"{rc} {raised!r} {len(fac.calls)} {err[-200:]}")
    H._clear_registry(PR)
    q = work / "b_unreg"
    q.mkdir()
    H._write_registry(PR, {work / "b_other": {"bolt_port": 7687, "http_port": 7474, "search_port": 8787,
                                              "embed_port": 8788}})
    fac = _Seq(good)
    rc, raised, out, err = _run(C, ["status"], fac, cwd=q)
    check("an unresolvable project is refused once (no retry, no driver)",
          raised is None and rc == 3 and not fac.calls and "retrying" not in err, f"{rc} {raised!r} {fac.calls}")
    H._clear_registry(PR)


def gate_b_embed_once(C, PR, work: Path) -> None:
    print("\n-- B3 (J6): search embeds the query ONCE across a retry --")
    p = work / "b_embed"
    p.mkdir()
    _register(PR, p)
    ident = PR.project_identity(p)
    embeds: list = []

    class _Ret:
        def __init__(self, drv, embed_fn=None, arm_weights=None):
            self.drv, self.embed_fn = drv, embed_fn

        def search(self, q, top_k=15):
            self.embed_fn(q)
            with self.drv.session() as s:
                list(s.run("CALL db.index.vector.queryNodes(...)"))
            return []
    stubs = H._search_stubs({})
    stubs["retrieval"] = types.SimpleNamespace(DeepMapRetriever=_Ret)
    stubs["embeddings"] = types.SimpleNamespace(embed_query=lambda q: embeds.append(q) or [0.0] * 768, EMBED_DIM=768)
    fac = _Seq(lambda: _DropOnceDriver(_defunct_exc(), owner=ident), lambda: H._GraphDriver(owner=ident, nodes=4))
    rc, raised, out, err = _run(C, ["search", "find the parser"], fac, cwd=p, overrides=stubs,
                                env={"DEEPMAP_CLI_EMBED_SERVER": "0"})
    check("search: a drop AFTER the query was embedded is retried -> exit 0", raised is None and rc == 0,
          f"{rc} {raised!r} {err[-300:]}")
    check("search: the query was embedded once (the retry reuses the vector)", embeds == ["find the parser"], embeds)
    H._clear_registry(PR)


def gate_b_dual_stack(C, work: Path) -> None:
    print("\n-- B4 (J6): a cause-less handshake failure (operator localhost URI) is retried only when FAST --")
    p = work / "b_dual"
    p.mkdir()
    check("the dual-stack chain keeps its 'hung' classification (the cause type was lost)",
          C._classify_db_error(_dual_stack_exc()) == "hung", C._classify_db_error(_dual_stack_exc()))
    good = lambda: H._GraphDriver(owner=None, nodes=4)   # noqa: E731
    fac = _Seq(lambda: H._GraphDriver(session_exc=_dual_stack_exc()), good)
    rc, raised, out, err = _run(C, ["status"], fac, cwd=p, env={"NEO4J_URI": "bolt://localhost:7999"})
    check("a FAST cause-less handshake failure is retried (a drop, not a deadline) -> exit 0",
          raised is None and rc == 0 and len(fac.calls) == 2, f"{rc} {raised!r} {len(fac.calls)} {err[-300:]}")
    saved = getattr(C, "_DROP_FAST_S", None)
    try:
        C._DROP_FAST_S = 0.0   # every attempt now counts as slow (a failure at the driver's deadline)
        fac = _Seq(lambda: H._GraphDriver(session_exc=_dual_stack_exc()), good)
        rc, raised, out, err = _run(C, ["status"], fac, cwd=p, env={"NEO4J_URI": "bolt://localhost:7999"})
    finally:
        if saved is not None:
            C._DROP_FAST_S = saved
    check("a SLOW one (at the deadline: a hung JVM) is not retried -> exit 3, one driver",
          saved is not None and raised is None and rc == 3 and len(fac.calls) == 1,
          f"{saved!r} {rc} {raised!r} {len(fac.calls)}")


# =========================================================================== C: J6 doctor
_BANNER = "2026-09-24 16:08:49.918+0000 INFO  [o.n.s.CommunityNeoWebServer] ======== Neo4j 5.26.0 ========"
_BOLT_ON = "2026-09-24 16:08:56.743+0000 INFO  [o.n.b.p.c.c.n.SocketNettyConnector] Bolt enabled on 127.0.0.1:17689."


def _force_close(ts: str, port=17689) -> str:
    return (f"{ts} WARN  [i.n.c.AbstractChannel] Force-closing a channel whose registration task was not accepted by "
            f"an event loop: [id: 0xc8b84a5f, L:/127.0.0.1:{port} - R:/127.0.0.1:65354]\n"
            "java.util.concurrent.RejectedExecutionException: event executor terminated\n"
            "\tat io.netty.util.concurrent.SingleThreadEventExecutor.reject(SingleThreadEventExecutor.java:931)")


def _write_log(home: Path, key: str, lines: list) -> Path:
    d = home / "instances" / key / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "debug.log"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _doctor(C, cwd: Path, factory, *, up=(18911, 18912), env=None):
    probed: list = []
    prov = H._fake_provision(set(up), probed)
    with H._env(**(env or {})), H._patched_graphdb(factory):
        rc, out = H._run_doctor(C, cwd=cwd, provision=prov)
    return rc, out


def gate_c_doctor(C, PR, work: Path, home: Path) -> None:
    print("\n-- C (J6): doctor reports a partly dead Bolt acceptor --")
    p = work / "c_doc"
    p.mkdir()
    _register(PR, p)
    ident, key = PR.project_identity(p), PR.project_key(p)
    good = lambda: H._GraphDriver(owner=ident, nodes=4)   # noqa: E731

    # C1: the census survives one dropped connection (retried) and doctor says so
    fac = _Seq(lambda: H._GraphDriver(owner=ident, session_exc=_reset_exc()), good)
    rc, out = _doctor(C, p, fac)
    neo = H._line_with(out, "Neo4j reachable")
    check("doctor: a dropped census connection is retried -> the census is shown, exit 0",
          rc == 0 and "[OK" in neo and "4 nodes" in H._line_with(out, "graph") and len(fac.calls) == 2,
          f"{rc} {len(fac.calls)} {out[:600]}")
    drop_ln = next((ln for ln in out.splitlines() if "[WARN]" in ln and "dropped" in ln.lower()), "")
    check("doctor: a WARN line names the dropped connection and the restart (stop, then heal)",
          bool(drop_ln) and " stop " in drop_ln and " heal " in drop_ln, drop_ln or out[:900])
    check("doctor: the verdict carries it as a reason (DEGRADED)",
          H._verdict_of(out).startswith("DEGRADED") and any("Bolt" in r or "dropped" in r.lower() for r in _reasons(out)),
          f"{H._verdict_of(out)} {_reasons(out)}")

    # C2: this instance's debug.log records force-closed channels since it last started
    logf = _write_log(home, key, [_force_close("2026-09-20 10:00:00.000+0000"), _BANNER, _BOLT_ON,
                                  "2026-09-25 01:00:00.000+0000 INFO  [o.n.x] something unrelated",
                                  _force_close("2026-09-26 13:40:53.848+0000"),
                                  _force_close("2026-09-27 07:19:23.593+0000")])
    fac = _Seq(good)
    rc, out = _doctor(C, p, fac)
    acc = H._line_with(out, "] Bolt acceptor")
    check("doctor: force-closed channels logged since the instance started -> WARN 'Bolt acceptor' line",
          "[WARN]" in acc, acc or out[:900])
    check("... with the count since the last start (2, not the pre-restart 3) and the last time",
          " 2 " in acc and "2026-09-27 07:19:23" in acc and "2026-09-20" not in acc, acc)
    check("... naming 'event executor terminated' and the restart remedy (stop, then heal)",
          "event executor terminated" in acc and " stop " in acc and " heal " in acc, acc)
    check("... and the verdict names it (DEGRADED; exit stays 0 — usable)",
          rc == 0 and H._verdict_of(out).startswith("DEGRADED")
          and any("Bolt" in r for r in _reasons(out)), f"{rc} {H._verdict_of(out)} {_reasons(out)}")

    # C3: events only BEFORE the last start banner (the instance was restarted since) -> nothing
    _write_log(home, key, [_force_close("2026-09-20 10:00:00.000+0000"), _BANNER, _BOLT_ON,
                           "2026-09-25 01:00:00.000+0000 INFO  [o.n.x] fine"])
    rc, out = _doctor(C, p, _Seq(good))
    check("doctor: force-closes only before the last start (a restarted instance) -> no Bolt acceptor line/reason",
          not H._line_with(out, "] Bolt acceptor") and not any("Bolt" in r for r in _reasons(out)), out[:900])

    # C4: an operator NEO4J_URI -> DeepMap's instance log is not that endpoint's: never read
    _write_log(home, key, [_BANNER, _BOLT_ON, _force_close("2026-09-26 13:40:53.848+0000")])
    rc, out = _doctor(C, p, _Seq(good), env={"NEO4J_URI": "bolt://db.example.invalid:7687"})
    check("doctor: operator NEO4J_URI -> the per-project instance log is never consulted",
          not H._line_with(out, "] Bolt acceptor"), H._line_with(out, "] Bolt acceptor"))

    # C4b: an operator NEO4J_URI + a dropped first connection -> WARN, the remedy is the endpoint's, not provision's
    fac = _Seq(lambda: H._GraphDriver(owner=ident, session_exc=_reset_exc()), good)
    rc, out = _doctor(C, p, fac, env={"NEO4J_URI": "bolt://db.example.invalid:7687"})
    ln = H._line_with(out, "] Neo4j connections")
    check("doctor, operator NEO4J_URI + a dropped connection: WARN + reason name the operator endpoint (no "
          "provision.py restart of a store DeepMap does not manage)",
          rc == 0 and "[WARN]" in ln and "operator-managed" in ln and "provision.py" not in ln
          and any("operator-managed" in r and "provision.py" not in r for r in _reasons(out)),
          f"{rc} {ln} {_reasons(out)}")

    # C5: the instance is DOWN (refused) -> an old log says nothing about now
    rc, out = _doctor(C, p, _Seq(lambda: H._GraphDriver(session_exc=_refused_exc())))
    check("doctor: a refused (stopped) instance -> no Bolt acceptor claim from its old log",
          rc == 1 and not H._line_with(out, "] Bolt acceptor"), H._line_with(out, "] Bolt acceptor"))

    # C6: the doctor FAILS on two drops, with the dropped wording + the log evidence
    rc, out = _doctor(C, p, _Seq(lambda: H._GraphDriver(session_exc=_reset_exc())))
    neo = H._line_with(out, "Neo4j")
    check("doctor: two drops -> FAIL Neo4j line worded as dropped connections (not 'did not answer in time')",
          rc == 1 and "[FAIL]" in neo and "closed" in neo and "did not answer" not in neo, neo)
    check("... and the log evidence is still reported (something accepted the connection)",
          "[WARN]" in H._line_with(out, "] Bolt acceptor"), out[:1200])

    # C7: the bounded, never-raising log scan
    scan = getattr(C, "_bolt_acceptor_signal", None)
    check("deepmap_cli._bolt_acceptor_signal exists", callable(scan))
    if callable(scan):
        d = home / "scan"
        d.mkdir(exist_ok=True)
        f = d / "debug.log"
        noise = ("2026-09-24 00:00:00.000+0000 INFO  [o.n.x] " + "n" * 200 + "\n") * 600   # ~150 KB
        f.write_text(_BANNER + "\n" + noise + _force_close("2026-09-27 09:21:42.225+0000") + "\n", encoding="utf-8")
        saved = getattr(C, "_LOG_TAIL_BYTES", None)
        try:
            C._LOG_TAIL_BYTES = 8192   # the banner is OUTSIDE the tail window: the window postdates it
            sig = scan(d)
        finally:
            if saved is not None:
                C._LOG_TAIL_BYTES = saved
        check("scan: a signature inside a bounded tail is found even when the start banner is before the window",
              isinstance(sig, dict) and sig.get("events") == 1 and "2026-09-27 09:21:42" in str(sig.get("last")),
              sig)
        check("scan: a missing log -> None", scan(home / "nope") is None, scan(home / "nope"))
        (d / "debug.log").write_bytes(b"\xff\xfe\x00garbage\n")
        check("scan: an undecodable log -> None (never raises)", scan(d) is None, scan(d))
    try:
        logf.unlink()
    except OSError:
        pass
    H._clear_registry(PR)


# =========================================================================== D: J6 core.longpaths
class _RecordRun:
    """Patches subprocess.run: records argv, answers `rev-parse HEAD` with HEAD and `status` with nothing."""

    def __init__(self, head: str):
        self.head, self.calls, self.orig = head, [], None

    def __enter__(self):
        self.orig = subprocess.run

        def fake(argv, *a, **kw):
            self.calls.append(list(argv))
            out = (self.head + "\n") if "rev-parse" in argv else ""
            if kw.get("encoding") or kw.get("text"):
                return subprocess.CompletedProcess(argv, 0, out, "")
            return subprocess.CompletedProcess(argv, 0, out.encode(), b"")
        subprocess.run = fake
        return self

    def __exit__(self, *a):
        subprocess.run = self.orig
        return False


def _mk_long_root(base: Path, target_len: int = 215) -> Path:
    cur = base.resolve()
    i = 0
    while len(str(cur)) < target_len - 10:
        room = target_len - len(str(cur)) - 1
        cur = cur / (f"L{i}" + "x" * max(1, min(60, room - 2)))
        i += 1
    cur.mkdir(parents=True, exist_ok=True)
    return cur


def _xp(p: Path) -> str:
    """`p` in its \\\\?\\ extended-length form on Windows (write a file past MAX_PATH)."""
    s = os.path.abspath(str(p))
    return ("\\\\?\\" + s) if _WIN and not s.startswith("\\\\?\\") else s


def _rm_tree(base: Path) -> None:
    """Delete ONLY `base` (a temp dir this gate created), long paths and git's read-only objects included."""
    def onexc(fn, path, exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            fn(path)
        except Exception:
            pass
    try:
        shutil.rmtree(_xp(base), onexc=onexc)
    except TypeError:   # Python < 3.12
        shutil.rmtree(_xp(base), onerror=onexc)
    except Exception:
        pass


def _g(root: Path, *args, check_rc=False) -> str:
    p = subprocess.run(["git", *_LONG, "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env=dict(os.environ, **_GIT_ENV))
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return (p.stdout or "").strip()


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def gate_d_longpaths(C, work: Path) -> None:
    print("\n-- D (J6): the freshness git probes pass core.longpaths (Windows) --")
    want = _LONG if _WIN else []

    def flag_ok(argv) -> bool:
        if "-C" not in argv:
            return False
        pre = argv[1:argv.index("-C")]
        return pre == want if _WIN else "core.longpaths=true" not in argv

    root = work / "d_argv"
    root.mkdir()
    head = "a" * 40
    H._write_manifest(root, built_at_sha=head, dirty=False, built_at_dirty=False, built_at_utc=_utc(time.time()))
    if shutil.which("git"):
        with _RecordRun(head) as rr:
            line = C._staleness_line(root)
        revp = [c for c in rr.calls if "rev-parse" in c]
        stat_ = [c for c in rr.calls if "status" in c]
        check(("`git rev-parse HEAD` passes -c core.longpaths=true before -C" if _WIN
               else "`git rev-parse HEAD`: no longpaths flag off Windows"),
              len(revp) == 1 and flag_ok(revp[0]), rr.calls)
        check(("`git status` (uncommitted-since-build) passes -c core.longpaths=true before -C" if _WIN
               else "`git status`: no longpaths flag off Windows"),
              len(stat_) == 1 and flag_ok(stat_[0]), rr.calls)
        check("(the recorded run still answers 'fresh')", "fresh" in str(line), line)
    else:
        check("(git on PATH for the argv checks)", True, "skipped — git absent")
    if not _WIN or not shutil.which("git"):
        check("(real long-root repo check is Windows-only)", True, "skipped off Windows / no git")
        return

    # Two real shapes of the trap: a ~175-char root (the field report: git's own objects fit under MAX_PATH, but a
    # committed 285-char file can't be lstat'ed -> plain `git status` errors on it ("Filename too long") or, on
    # other git builds, lists it as MODIFIED) and a ~215-char root (the loose objects are past MAX_PATH too -> plain
    # `git status` fails outright).
    for target_len, shape in ((175, "cannot read that file"), (215, "fails outright")):
        base = Path(tempfile.mkdtemp(prefix="dm-w3r8-"))
        try:
            root = _mk_long_root(base, target_len)
            _g(root, "init", "-q", check_rc=True)
            name = "long_module_" + "n" * (285 - len(str(root)) - len("long_module_") - 4) + ".py"
            f = root / name
            with open(_xp(f), "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 1\n")
            _g(root, "add", "-A", check_rc=True)
            _g(root, "commit", "-q", "-m", "c1", check_rc=True)
            head = _g(root, "rev-parse", "HEAD").lower()
            plain = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30, env=dict(os.environ, **_GIT_ENV))
            trapped = (plain.returncode != 0 if target_len >= 200 else
                       (name in (plain.stdout or "") or "too long" in (plain.stderr or "").lower()))
            tag = f"{len(str(root))}-char root"
            check(f"({tag} precondition: the committed file's path is {len(str(f))} chars and PLAIN `git status` "
                  f"{shape})", len(str(f)) > 260 and trapped,
                  f"len={len(str(f))} rc={plain.returncode} out={plain.stdout.strip()[:160]} "
                  f"err={plain.stderr.strip()[:160]}")
            mtime = os.stat(_xp(f)).st_mtime
            H._write_manifest(root, built_at_sha=head, dirty=False, built_at_dirty=False,
                              built_at_utc=_utc(mtime - 120))
            line = C._staleness_line(root) or ""
            check(f"{tag}: clean build at HEAD with a committed >260-char file -> fresh (never 'uncommitted' / STALE)",
                  "fresh" in line and "STALE" not in line, line)
            time.sleep(1.1)
            with open(_xp(f), "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 2\n")
            mt2 = os.stat(_xp(f)).st_mtime
            H._write_manifest(root, built_at_sha=head, dirty=False, built_at_dirty=False, built_at_utc=_utc(mt2 - 60))
            line = C._staleness_line(root) or ""
            check(f"{tag}: a GENUINE edit of that long file after the build -> STALE naming it (git status with "
                  f"core.longpaths; its mtime read via \\\\?\\)", "STALE" in line and "long_module_" in line, line)
            H._write_manifest(root, built_at_sha=head, dirty=True, built_at_dirty=True, dirty_paths_count=1,
                              dirty_paths_sample=[name], built_at_utc=_utc(mt2 - 60))
            line = C._staleness_line(root) or ""
            check(f"{tag}: a dirty build whose sampled >260-char file changed after it -> STALE (the sample's mtime "
                  f"is read)", "STALE" in line, line)
        finally:
            _rm_tree(base)
        check(f"the {target_len}-char long-root temp tree was removed (exact path)", not base.exists(), str(base))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-8 gates (W3: 127.0.0.1 bolt URIs, read retry + dead-acceptor doctor, longpaths)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in H._SCRUB + ("DEEPMAP_HOME", "DEEPMAP_CLI_EMBED_SERVER")}
    for k in saved_env:
        if k != "DEEPMAP_HOME":
            os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td).resolve()
        home = base / "home"
        home.mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            C = H._load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_r8_{time.time_ns()}")
            for gate in (lambda: gate_a_uris(C, PR, work),
                         lambda: gate_b_classify(C),
                         lambda: gate_b_retry(C, PR, work),
                         lambda: gate_b_embed_once(C, PR, work),
                         lambda: gate_b_dual_stack(C, work),
                         lambda: gate_c_doctor(C, PR, work, home),
                         lambda: gate_d_longpaths(C, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
                finally:
                    H._clear_registry(PR)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
