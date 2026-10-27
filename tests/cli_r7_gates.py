#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 gates for the CLI (owner W3: build/deepmap_cli.py). RED-first, hermetic.

Locks:
  M7  I4: doctor never treats a .mcp.json MCP pair registered to ANOTHER project as this project's. A copied
      checkout / worktree inherits the original's committed .mcp.json; provision's lookup refuses that pair
      (_mcp_pair_claimed_by_other), and _project_mcp_ports now returns the same verdict (a problem naming the
      owner + /deep-map:setup), so doctor WARNs and never probes those servers as this project's. An
      unclaimed .mcp.json pair and this project's own registry pair are unchanged.
  E7  T8d-X3: `deepmap search` embeds its query through THIS project's running deepmap-embed server (the
      registered port; a warm model) and falls back to the in-process model on any failure (refused,
      isError, wrong size, garbage, timeout), with the SAME vector either way. No registered pair -> no HTTP
      call. DEEPMAP_CLI_EMBED_SERVER=0 opts out. The JSON-RPC call is proven against the REAL deepmap-embed
      FastMCP server (stateless JSON, in-process uvicorn on an ephemeral 127.0.0.1 port, stub model). A slow
      in-process model load prints ONE note; a fast one prints nothing.
  H7  T10b-3 / T10d-W: a Neo4j that accepts the connection but never answers (a frozen / suspended JVM) is
      classified 'hung', not 'unreachable': the message says it did not answer, and names stop + heal
      instead of "may be stopped or hibernated. Revive it with /deep-map:setup" (setup can't revive it while it
      holds its port). A refused connection stays 'unreachable' with the old wording. The exception shape is
      the one the REAL driver raises against a never-answering local listener (probed here too).
  X7  doctor + status report the edge-confidence mix of the graph (CALLS EXTRACTED/INFERRED/AMBIGUOUS shares,
      typed-edge counts, the widest AMBIGUOUS fan-out); a mostly-AMBIGUOUS CALLS layer WARNs without changing
      the verdict; a session that can't answer the query prints no line and nothing breaks.
  T7  tune wording: the gate reasons / refusals from the frozen learned_fusion + tune_weights + learned_prior
      name `deepmap tune` (no such executable) — the CLI prints them as the runnable deepmap_cli.py command;
      `tune --dry-run` shows the cold-start threshold (0/150).
  G7  `impact --edges` naming ONLY unknown types is a usage error (exit 2, no driver, names the known types)
      instead of a silent CALLS-only walk; a partly-unknown list warns and walks the known ones.
  D7  hint / output drift: the learning --enable access-hook matcher covers MultiEdit/NotebookEdit (and keeps
      the canonical substring) and says the hook path is version-specific; hibernate no longer claims
      "revive is automatic on the next use"; "1 result"; help text on the flags; line-buffered stdout;
      `deepmap impact` stamps last_used like search.
  B7  I4: doctor honours setup's PERSISTED MCP blocker (port_registry.read_setup_blocker): a server on a port
      setup could not restart / attribute is WARNed with the blocker's reason (and is a verdict reason), never
      "[OK] UP"; once setup clears the blocker it is [OK] again.
  F7  freshness sees UNCOMMITTED edits made after a clean build (git status at doctor/status time), ignoring
      DeepMap's own artifacts (.deepmap/, graphify-out/, .mcp.json).

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers, temp project dirs; never touches ~/.deepmap or the committed
.mcp.json. The only sockets are 127.0.0.1 listeners this gate opens itself (the real embed server under
uvicorn, and a never-answering listener for the real driver's hung shape) — never a real Neo4j or MCP server.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time
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
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load_module(REPO / "tests" / "cli_route_impact_gates.py", f"dm_cli_r7_helpers_{time.time_ns()}")

_SCRUB = tuple(H._SCRUB) + ("DEEPMAP_CLI_EMBED_SERVER", "DEEPMAP_CLI_EMBED_TIMEOUT_S", "DEEPMAP_CLI_SLOW_NOTE_S",
                            "DEEPMAP_EMBED_PORT", "DEEPMAP_SEARCH_PORT")


def _fake_prov(up_ports: set, probed: list, *, mcp_json_pair=None, claim=None, with_claim_helper=True):
    """H._fake_provision + a .mcp.json pair and (optionally) provision's _mcp_pair_claimed_by_other."""
    ns = H._fake_provision(up_ports, probed)
    ns._ports_from_mcp_json = lambda root: mcp_json_pair
    if with_claim_helper:
        ns._mcp_pair_claimed_by_other = lambda target, pair: claim
    return ns


def _vec(seed: int, dim: int = 768) -> list:
    """A 768-d vector of float32-exact values (what embeddings.embed_query returns: float(np.float32))."""
    rnd = random.Random(seed)
    return [struct.unpack("f", struct.pack("f", rnd.uniform(-1, 1)))[0] for _ in range(dim)]


# ---- M7: doctor + a .mcp.json pair registered to another project ------------------------------------------
def gate_mcp_json_claim(C, PR, P, work: Path) -> None:
    print("\n-- M7 (I4): a .mcp.json MCP pair registered to ANOTHER project is never probed as this project's --")
    a, w = work / "mainA", work / "worktreeW"
    a.mkdir()
    w.mkdir()
    for d in (a, w):   # the worktree inherits A's committed .mcp.json (A's registered ports)
        (d / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "deepmap-search": {"type": "http", "url": "http://127.0.0.1:18787/mcp"},
            "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:18788/mcp"}}}), encoding="utf-8")
    H._write_registry(PR, {a: {"search_port": 18787, "embed_port": 18788, "bolt_port": 17689,
                               "http_port": 17476}})
    s, e, src, problem = C._project_mcp_ports(P, w)
    check("real provision: W's .mcp.json pair (A's registered ports) comes back WITH a problem",
          (s, e, src) == (18787, 18788, ".mcp.json") and bool(problem), f"{(s, e, src, problem)}")
    check("... the problem names the owning project and /deep-map:setup",
          bool(problem) and (str(a).replace("\\", "/").lower() in str(problem).replace("\\", "/").lower()
                             or a.name in str(problem)) and "/deep-map:setup" in str(problem), str(problem))
    check("... it is the same verdict as provision's own lookup (_mcp_pair_claimed_by_other)",
          bool(P._mcp_pair_claimed_by_other(w, (18787, 18788))), "provision says unclaimed")
    s, e, src, problem = C._project_mcp_ports(P, a)
    check("control: A itself -> its registry pair, no problem", (s, e, src, problem) == (18787, 18788, "registry", None),
          f"{(s, e, src, problem)}")
    # an UNCLAIMED .mcp.json pair (registry present, nobody holds 18801/18802) stays this project's
    (w / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": "http://127.0.0.1:18801/mcp"},
        "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:18802/mcp"}}}), encoding="utf-8")
    s, e, src, problem = C._project_mcp_ports(P, w)
    check("control: an unclaimed .mcp.json pair -> returned as before, no problem",
          (s, e, src, problem) == (18801, 18802, ".mcp.json", None), f"{(s, e, src, problem)}")

    # doctor end to end (fake provision; the pair's servers are UP): WARN + never probed as ours
    probed: list = []
    prov = _fake_prov({18787, 18788}, probed, mcp_json_pair=(18787, 18788), claim=str(a))
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(w), nodes=5))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=w, provision=prov)
    ln = H._line_with(out, "deepmap-search")
    check("doctor in the worktree: the MCP line WARNs, not [OK] UP", "[WARN]" in ln and "UP" not in ln
          and "[OK" not in ln, ln)
    check("... it says the ports belong to another project and names /deep-map:setup",
          "another project" in ln and "/deep-map:setup" in ln, ln)
    check("... those ports were never probed as this project's", 18787 not in probed and 18788 not in probed,
          str(probed))
    check("... and the verdict is not READY", not H._verdict_of(out).startswith("READY"), H._verdict_of(out))
    # an older provision without the helper: the CLI's own registry check still refuses the pair
    probed = []
    prov = _fake_prov({18787, 18788}, probed, mcp_json_pair=(18787, 18788), with_claim_helper=False)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(w), nodes=5))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=w, provision=prov)
    ln = H._line_with(out, "deepmap-search")
    check("provision without _mcp_pair_claimed_by_other: the registry fallback still refuses A's pair",
          "UP" not in ln and "/deep-map:setup" in ln and 18787 not in probed, f"{ln} {probed}")
    # control: an unclaimed .mcp.json pair is probed and reported as before
    probed = []
    prov = _fake_prov({18801, 18802}, probed, mcp_json_pair=(18801, 18802), claim=None)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(w), nodes=5))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=w, provision=prov)
    ln = H._line_with(out, "deepmap-search")
    check("control: an unclaimed .mcp.json pair is probed and reported UP with its [ports: .mcp.json] tag",
          "UP" in ln and ".mcp.json" in ln and 18801 in probed, f"{ln} {probed}")
    H._clear_registry(PR)


# ---- B7: doctor honours setup's persisted MCP blocker (I4 / H1) ---------------------------------------------
def gate_setup_blocker(C, PR, work: Path) -> None:
    print("\n-- B7 (I4): doctor honours setup's persisted MCP blocker — never '[OK] UP' for a held port --")
    p = work / "blockedP"
    p.mkdir()
    H._write_registry(PR, {p: {"search_port": 18687, "embed_port": 18688, "bolt_port": 17891, "http_port": 17892}})
    ok = PR.write_setup_blocker(p, reason="deepmap-search on port 18687 is held by pid 4242 that setup could not "
                                          "restart — stop that process by hand, then re-run /deep-map:setup",
                                servers=["deepmap-search"], ports=[18687], pids=[4242])
    check("(fixture) port_registry wrote the setup blocker record", ok is True, str(ok))
    probed: list = []
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(p), nodes=5))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=p, provision=_fake_prov({18687, 18688}, probed))
    ln = H._line_with(out, "deepmap-search")
    check("the blocked server is NOT reported [OK] UP: WARN naming the setup blocker and its fix",
          "[WARN]" in ln and "[OK" not in ln and "blocker" in ln.lower() and "4242" in ln, ln)
    check("... and it is a verdict reason (not READY)", any("deepmap-search" in r for r in _reasons(out))
          and not H._verdict_of(out).startswith("READY"), H._verdict_of(out) + " | " + " / ".join(_reasons(out)))
    ln = H._line_with(out, "deepmap-embed")
    check("the server the blocker does not name stays [OK] UP", "[OK" in ln and "UP" in ln, ln)
    PR.clear_setup_blocker(p)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(p), nodes=5))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=p, provision=_fake_prov({18687, 18688}, []))
    ln = H._line_with(out, "deepmap-search")
    check("control: once setup cleared the blocker, the server is [OK] UP again", "[OK" in ln and "UP" in ln, ln)
    H._clear_registry(PR)


def _reasons(out: str) -> list:
    return [ln.strip()[2:] for ln in out.splitlines() if ln.startswith("  - ")]


# ---- E7: CLI query embedding through the running embed server ---------------------------------------------
class _EmbedRet:
    """A retriever stand-in that calls embed_fn exactly like the real vector arm does (once per search)."""
    seen: dict = {}

    def __init__(self, drv, embed_fn=None, arm_weights=None):
        self.embed_fn = embed_fn

    def search(self, q, top_k=15):
        _EmbedRet.seen["vec"] = self.embed_fn(q)
        _EmbedRet.seen["q"] = q
        return [{"type": "Function", "name": "f", "file": "a.py", "line": 1, "score": 0.5, "summary": "s"}]


def _search_overrides(embed_query):
    return {
        "embeddings": types.SimpleNamespace(embed_query=embed_query, EMBED_DIM=768),
        "retrieval": types.SimpleNamespace(DeepMapRetriever=_EmbedRet),
        "learned_fusion": types.SimpleNamespace(resolve_arm_weights_for_live=lambda **k: None),
        "retrieval_log": types.SimpleNamespace(maybe_log_search=lambda *a, **k: None),
    }


def _run_search(C, proj: Path, embed_query, post=None, query="auth flow"):
    """(rc, raised, out, err, posts, local_calls) of `deepmap search` with a fake Neo4j and a scripted transport."""
    posts, local = [], []

    def _local(t):
        local.append(t)
        return embed_query(t)
    saved_post = getattr(C, "_mcp_http_post", None)
    if post is not None:
        def _post(url, body, timeout):
            posts.append((url, body, timeout))
            return post(url, body, timeout)
        C._mcp_http_post = _post
    _EmbedRet.seen.clear()
    try:
        rc, raised, out, err = H._main(C, ["search", query], overrides=_search_overrides(_local),
                                       driver=H._GraphDriver(owner=None, nodes=3), cwd=proj)
    finally:
        if saved_post is not None:
            C._mcp_http_post = saved_post
    return rc, raised, out, err, posts, local


def _ok_reply(vec):
    return lambda url, body, timeout: {"jsonrpc": "2.0", "id": body.get("id"), "result": {
        "content": [{"type": "text", "text": str(x)} for x in vec], "structuredContent": {"result": vec},
        "isError": False}}


def gate_embed_server(C, PR, work: Path) -> None:
    print("\n-- E7 (T8d-X3): the CLI embeds its query through this project's warm embed server --")
    proj = work / "embedP"
    proj.mkdir()
    H._write_registry(PR, {proj: {"search_port": 18987, "embed_port": 18988, "bolt_port": 17991,
                                  "http_port": 17992}})
    server_vec, local_vec = _vec(1), _vec(2)
    check("the embed server URL is THIS project's registered embed port",
          getattr(C, "_embed_server_url", lambda r: None)(proj) == "http://127.0.0.1:18988/mcp",
          str(getattr(C, "_embed_server_url", lambda r: "<missing>")(proj)))
    rc, raised, out, err, posts, local = _run_search(C, proj, lambda t: local_vec, post=_ok_reply(server_vec))
    check("search with a warm embed server: rc 0, no traceback", raised is None and rc == 0, f"{rc} {raised!r} {err}")
    check("... the query is embedded by the server (one JSON-RPC tools/call to deepmap_embed)",
          len(posts) == 1 and posts[0][0] == "http://127.0.0.1:18988/mcp"
          and posts[0][1].get("method") == "tools/call"
          and posts[0][1].get("params", {}).get("name") == "deepmap_embed"
          and posts[0][1].get("params", {}).get("arguments", {}).get("text") == "auth flow", str(posts)[:300])
    check("... the retriever gets the server's vector exactly, and the in-process model is NOT loaded",
          _EmbedRet.seen.get("vec") == server_vec and not local, f"local calls {local}")
    check("... and the call carries a bounded timeout", bool(posts) and 0 < float(posts[0][2]) <= 120,
          str(posts[0][2] if posts else None))

    bad_replies = {
        "connection refused": lambda u, b, t: (_ for _ in ()).throw(ConnectionRefusedError(10061, "refused")),
        "isError": lambda u, b, t: {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "DeepMap's embedding model is unavailable"}], "isError": True}},
        "JSON-RPC error": lambda u, b, t: {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad"}},
        "wrong size": _ok_reply(_vec(3, dim=384)),
        "non-numeric": _ok_reply(["x"] * 768),
        "garbage": lambda u, b, t: {"hello": "world"},
        "timeout": lambda u, b, t: (_ for _ in ()).throw(TimeoutError("timed out")),
    }
    for label, reply in bad_replies.items():
        rc, raised, out, err, posts, local = _run_search(C, proj, lambda t: local_vec, post=reply)
        check(f"server {label}: falls back to the in-process model (same vector the CLI always used), rc 0",
              raised is None and rc == 0 and local == ["auth flow"] and _EmbedRet.seen.get("vec") == local_vec,
              f"{rc} {raised!r} local={local} err={err[-200:]}")
    rc, raised, out, err, posts, local = _run_search(C, proj, lambda t: local_vec, post=bad_replies["isError"])
    check("a server that answered but failed says so ONCE on stderr (why the search is slower)",
          err.count("embed server") == 1 and "in-process" in err, err[-300:])
    rc, raised, out, err, posts, local = _run_search(C, proj, lambda t: local_vec,
                                                     post=bad_replies["connection refused"])
    check("a server that is simply not running: no noise on stderr (the fast in-process path stays silent)",
          "embed server" not in err, err[-300:])

    with H._env(DEEPMAP_CLI_EMBED_SERVER="0"):
        rc, raised, out, err, posts, local = _run_search(C, proj, lambda t: local_vec, post=_ok_reply(server_vec))
    check("DEEPMAP_CLI_EMBED_SERVER=0: no HTTP call, the in-process model embeds",
          not posts and local == ["auth flow"], f"posts={len(posts)} local={local}")

    other = work / "embedNoPair"
    other.mkdir()
    rc, raised, out, err, posts, local = _run_search(C, other, lambda t: local_vec, post=_ok_reply(server_vec))
    check("a project with no registered MCP pair: no HTTP call at all (the base default pair is never used)",
          not posts and local == ["auth flow"] and C._embed_server_url(other) is None,
          f"posts={len(posts)} url={C._embed_server_url(other)}")

    with H._env(DEEPMAP_EMBED_PORT="18999"):
        check("an explicit DEEPMAP_EMBED_PORT override wins (as for provision / doctor)",
              C._embed_server_url(other) == "http://127.0.0.1:18999/mcp", str(C._embed_server_url(other)))

    # the slow-load note: printed only when the in-process embed is slow
    with H._env(DEEPMAP_CLI_SLOW_NOTE_S="0.05"):
        rc, raised, out, err, posts, local = _run_search(
            C, other, lambda t: (time.sleep(0.4), local_vec)[1], post=_ok_reply(server_vec))
    check("a slow in-process model load prints ONE 'loading the embedding model' note",
          err.count("loading the embedding model") == 1, err[-300:])
    with H._env(DEEPMAP_CLI_SLOW_NOTE_S="5"):
        rc, raised, out, err, posts, local = _run_search(C, other, lambda t: local_vec, post=_ok_reply(server_vec))
    check("a fast in-process embed prints nothing (the default output is unchanged)", err.strip() == "", err)
    H._clear_registry(PR)


def gate_embed_server_real_protocol(C) -> None:
    print("\n-- E7: the JSON-RPC call against the REAL deepmap-embed FastMCP server (stub model, uvicorn) --")
    import logging
    for name in ("mcp", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        import uvicorn  # noqa: F401 — installed with the mcp SDK
    except Exception as exc:  # noqa: BLE001
        check(f"(skip) uvicorn not importable ({type(exc).__name__}) — protocol section not run", True)
        return
    want = _vec(7)
    stub = types.ModuleType("embeddings")
    stub.EMBED_DIM = 768
    stub.embed_query = lambda text: list(want) if text == "fuse ranked search results" else _vec(8)
    stub.ensure_model = lambda **k: object()
    stub.short_error = lambda e: f"{type(e).__name__}: {e}"
    stub.ModelUnavailable = RuntimeError
    saved = sys.modules.get("embeddings")
    sys.modules["embeddings"] = stub
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    env_saved = {k: os.environ.get(k) for k in ("DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY", "DEEPMAP_EMBED_PORT")}
    for k in env_saved:
        os.environ.pop(k, None)
    server = thread = None
    try:
        srv_mod = _load_module(BUILD / "deepmap_embed_server.py", f"dm_embed_srv_r7_{time.time_ns()}")
        import uvicorn
        app = srv_mod.mcp.streamable_http_app()
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.time() + 15
        while not getattr(server, "started", False) and time.time() < deadline and thread.is_alive():
            time.sleep(0.05)
        check("(fixture) the real embed server app is serving on an ephemeral 127.0.0.1 port",
              getattr(server, "started", False), "uvicorn did not start")
        url = f"http://127.0.0.1:{port}/mcp"
        got, err = None, None
        try:
            got = C._embed_via_server(url, "fuse ranked search results", dim=768, timeout=10)
        except Exception as exc:  # noqa: BLE001
            err = exc
        check("one stateless JSON-RPC tools/call returns the server's 768-d vector", err is None and got is not None,
              repr(err))
        check("... bit-identical to what the model returned (float32 values survive the JSON round trip)",
              got == want, f"first diff at {next((i for i, (x, y) in enumerate(zip(got or [], want)) if x != y), None)}")
        bad = None
        try:
            C._embed_via_server(url, "fuse ranked search results", dim=384, timeout=10)
        except Exception as exc:  # noqa: BLE001
            bad = exc
        check("a vector of the wrong size for this build is rejected (so the caller falls back)", bad is not None,
              "accepted")
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        dead_port = closed.getsockname()[1]
        closed.close()   # nothing listens there now
        t0, dead = time.time(), None
        try:
            C._embed_via_server(f"http://127.0.0.1:{dead_port}/mcp", "x", dim=768, timeout=5)
        except Exception as exc:  # noqa: BLE001
            dead = exc
        check("a port nobody listens on fails fast (refused, not a timeout)", dead is not None and time.time() - t0 < 4,
              f"{dead!r} {time.time() - t0:.1f}s")
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        try:
            sock.close()
        except Exception:
            pass
        if saved is None:
            sys.modules.pop("embeddings", None)
        else:
            sys.modules["embeddings"] = saved
        for k, v in env_saved.items():
            if v is not None:
                os.environ[k] = v


# ---- H7: a hung / frozen Neo4j is worded as hung, not stopped ---------------------------------------------
def _hung_exc():
    """The exact chain the real driver raises against a listener that never answers the Bolt handshake."""
    import neo4j.exceptions as E
    inner = E.ServiceUnavailable("Failed to read four byte Bolt handshake response from server "
                                 "ResolvedIPv4Address(('127.0.0.1', 7689)) (deadline Deadline(timeout=15))")
    inner.__cause__ = TimeoutError("timed out")
    outer = E.ServiceUnavailable("Couldn't connect to localhost:7689 (resolved to ('127.0.0.1:7689',)):\n"
                                 + str(inner))
    outer.__cause__ = inner
    return outer


def _refused_exc():
    import neo4j.exceptions as E
    inner = ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it")
    outer = E.ServiceUnavailable("Couldn't connect to localhost:7689 (resolved to ('127.0.0.1:7689',)):\n"
                                 "Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7689)) "
                                 "(reason [WinError 10061] refused)")
    outer.__cause__ = inner
    return outer


def gate_hung(C, PR, work: Path) -> None:
    print("\n-- H7 (T10b-3): a Neo4j that accepts but never answers is 'hung', not 'stopped or hibernated' --")
    import neo4j.exceptions as E
    check("a handshake read timeout (TimeoutError cause) -> 'hung'", C._classify_db_error(_hung_exc()) == "hung",
          C._classify_db_error(_hung_exc()))
    acq = getattr(E, "ConnectionAcquisitionTimeoutError", None)
    if isinstance(acq, type):
        e = acq("failed to obtain a connection from the pool within 30.0s (timeout)")
        check("ConnectionAcquisitionTimeoutError (a pool whose connections never come back) -> 'hung'",
              C._classify_db_error(e) == "hung", C._classify_db_error(e))
        pool = getattr(E, "ConnectionPoolError")
        check("control: a plain ConnectionPoolError stays 'unreachable'", C._classify_db_error(pool("pool")) == "unreachable",
              C._classify_db_error(pool("pool")))
    check("control: a refused connection stays 'unreachable'", C._classify_db_error(_refused_exc()) == "unreachable",
          C._classify_db_error(_refused_exc()))
    tto = E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
                                      message="The transaction has been terminated... timed out")
    check("control: a server-side transaction timeout (the server ANSWERED) stays 'query'",
          C._classify_db_error(tto) == "query", C._classify_db_error(tto))

    p = work / "hungP"
    p.mkdir()
    H._write_registry(PR, {p: {"bolt_port": 17689, "http_port": 17476, "search_port": 18887, "embed_port": 18888}})
    rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_hung_exc()), cwd=p)
    check("status on a hung instance: exit 3, no traceback", raised is None and rc == 3, f"{rc} {raised!r}")
    check("... says it did not answer (hung / overloaded), not 'may be stopped or hibernated'",
          "did not answer" in err and "hung" in err and "stopped or hibernated" not in err, err[-500:])
    check("... names the runnable restart: provision.py stop <root> then heal <root> (graph kept)",
          "provision.py" in err and " stop " in err and " heal " in err and str(p.name) in err, err[-500:])
    rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_refused_exc()), cwd=p)
    check("control: a stopped instance keeps the 'stopped or hibernated ... /deep-map:setup' wording",
          rc == 3 and "not reachable" in err and "/deep-map:setup" in err and "did not answer" not in err, err[-400:])
    fac = H._Factory(lambda: H._GraphDriver(session_exc=_hung_exc()))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=p)
    neo = H._line_with(out, "Neo4j")
    check("doctor on a hung instance: FAIL 'not answering' + stop/heal, not 'revives a stopped/hibernated'",
          "[FAIL]" in neo and "not answering" in neo and " heal " in neo and "revives a stopped" not in neo, neo)
    with H._env(NEO4J_URI="bolt://db.example.invalid:7687"):
        rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_hung_exc()), cwd=p)
    check("operator NEO4J_URI hung: points at the operator-managed endpoint, no provision stop/heal",
          "did not answer" in err and "NEO4J_URI" in err and "provision.py" not in err, err[-400:])
    # the plain (stopped) outage remedy is route-aware too
    rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_refused_exc()), cwd=p)
    check("stopped per-project instance: names heal <this project> (revive without a re-sync) and /deep-map:setup",
          " heal " in err and p.name in err and "/deep-map:setup" in err and "re-sync" in err, err[-400:])
    with H._env(NEO4J_URI="bolt://db.example.invalid:7687"):
        rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_refused_exc()), cwd=p)
    check("operator NEO4J_URI down: says the endpoint is operator-managed — no 'revive with setup/heal'",
          "not reachable" in err and "NEO4J_URI" in err and "provision.py" not in err and "revive" not in err,
          err[-400:])
    fac = H._Factory(lambda: H._GraphDriver(session_exc=_refused_exc()))
    with H._env(NEO4J_URI="bolt://db.example.invalid:7687"), H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=p)
    neo = H._line_with(out, "Neo4j")
    check("doctor, operator NEO4J_URI down: FAIL names the operator endpoint, not '/deep-map:setup revives it'",
          "[FAIL]" in neo and "NEO4J_URI" in neo and "revives a stopped" not in neo, neo)
    H._clear_registry(PR)


def gate_hung_real_driver(C) -> None:
    print("\n-- H7: the REAL neo4j driver against a never-answering local listener (no Neo4j) --")
    try:
        from neo4j import GraphDatabase
    except Exception as exc:  # noqa: BLE001
        check(f"(skip) neo4j driver not importable ({type(exc).__name__})", True)
        return
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    held = []
    stop = threading.Event()

    def _accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                held.append(c)
            except OSError:
                continue
    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    exc = None
    try:
        drv = GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("neo4j", "x"), connection_timeout=2,
                                   connection_acquisition_timeout=3)
        try:
            drv.verify_connectivity()
        except Exception as e:  # noqa: BLE001
            exc = e
        finally:
            drv.close()
    finally:
        stop.set()
        t.join(timeout=3)
        for c in held:
            try:
                c.close()
            except Exception:
                pass
        srv.close()
    check("(fixture) the real driver raised against the silent listener", exc is not None, "no exception")
    check("... and the CLI classifies that real exception as 'hung'", exc is not None and C._classify_db_error(exc) == "hung",
          f"{type(exc).__name__}: {str(exc)[:200]} -> {C._classify_db_error(exc) if exc else None}")


# ---- X7: edge-confidence mix in doctor + status -----------------------------------------------------------
def _mix_driver(rows, fan=None, owner=None, nodes=40):
    def answer(q, kw):
        if "type(r) AS t" in q:
            return list(rows)
        if "AS fan" in q:
            return [fan] if fan else []
        if "owning_root" in q:
            return [{"owning_root": owner, "r": owner}] if owner else []
        if "SHOW INDEXES" in q:
            return [{"name": "deepmap_fn_embeddings", "type": "VECTOR", "state": "ONLINE"}]
        if "count(" in q:
            return [{"c": nodes}]
        return []
    return H._GraphDriver(answer=answer, owner=owner, nodes=nodes)


def gate_edge_mix(C, PR, work: Path) -> None:
    print("\n-- X7: doctor + status report the edge-confidence mix the graph actually holds --")
    p = work / "mixP"
    p.mkdir()
    ident = PR.project_identity(p)
    rows = [{"t": "CALLS", "c": "AMBIGUOUS", "n": 70}, {"t": "CALLS", "c": "INFERRED", "n": 25},
            {"t": "CALLS", "c": "EXTRACTED", "n": 5}, {"t": "IMPORTS", "c": "INFERRED", "n": 12},
            {"t": "INHERITS", "c": "EXTRACTED", "n": 3}]
    with H._env(NEO4J_URI="bolt://localhost:7999"):   # the verdict WITHOUT a mix answer (environment-dependent)
        fac = H._Factory(lambda: H._GraphDriver(owner=ident, nodes=40))
        with H._patched_graphdb(fac):
            _rc, base_out = H._run_doctor(C, cwd=p, provision=_fake_prov({8787, 8788}, []))
    base_verdict = H._verdict_of(base_out)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: _mix_driver(rows, fan={"nm": "run", "fan": 9}, owner=ident))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=p, provision=_fake_prov({8787, 8788}, []))
    ln = H._line_with(out, "] edges")
    check("doctor prints an 'edges' line with the CALLS confidence shares",
          "CALLS 100" in ln and "AMBIGUOUS 70%" in ln and "INFERRED 25%" in ln and "EXTRACTED 5%" in ln, ln)
    check("... and the typed-edge counts", "IMPORTS 12" in ln and "INHERITS 3" in ln, ln)
    check("... mostly-AMBIGUOUS CALLS -> WARN naming 'verify by reading' and the widest fan-out ('run' -> 9)",
          ln.strip().startswith("[WARN]") and "verify" in ln and "'run'" in ln and "9" in ln, ln)
    check("... informational: it does not change the verdict (same verdict as without the mix line)",
          H._verdict_of(out) == base_verdict and "AMBIGUOUS" not in H._verdict_of(out),
          f"{H._verdict_of(out)!r} vs {base_verdict!r}")
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        fac = H._Factory(lambda: _mix_driver([{"t": "CALLS", "c": "EXTRACTED", "n": 8},
                                              {"t": "CALLS", "c": "INFERRED", "n": 2}], owner=ident))
        with H._patched_graphdb(fac):
            rc, out = H._run_doctor(C, cwd=p)
    ln = H._line_with(out, "] edges")
    check("a mostly-resolved CALLS layer -> [OK]", ln.strip().startswith("[OK") and "EXTRACTED 80%" in ln, ln)
    check("a session that can't answer the mix query (answers [{'c': n}] to anything): no edges line, same verdict",
          H._line_with(base_out, "] edges") == "" and "Traceback" not in base_out,
          H._line_with(base_out, "] edges") + " | " + base_verdict)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        rc, raised, out, err = H._main(C, ["status"], driver=_mix_driver(rows, fan={"nm": "run", "fan": 9},
                                                                         owner=ident), cwd=p)
    check("status prints the edge types and the CALLS confidence mix",
          rc == 0 and "CALLS=100" in out and "IMPORTS=12" in out and "AMBIGUOUS 70%" in out,
          f"{rc} {raised!r} {out[-400:]}")


# ---- T7: tune wording + threshold ---------------------------------------------------------------------------
def gate_tune_wording(C, work: Path) -> None:
    print("\n-- T7: tune prints runnable commands for the frozen modules' `deepmap tune` hints; shows the threshold --")
    root = work / "tuneP"
    (root / ".deepmap").mkdir(parents=True)
    (root / ".deepmap" / "instance.json").write_text(json.dumps({"learned_fusion": {
        "arm_weights": {"vector": 1.2, "bm25": 0.9, "graph_weighted": 0.9}, "applied": False,
        "beats_equal": True, "significant": True, "objective": "ndcg", "reason": "learned_beats_equal",
        "n_labeled_queries": 200}}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        C.cmd_tune(argparse.Namespace(root=str(root), status=False, report=True, apply=False, export_prior=False,
                                      min_queries=None, min_queries_bucket=None, dry_run=False))
    txt = out.getvalue()
    check("tune --report: the gate reasons never name a bare `deepmap tune` executable", "`deepmap tune" not in txt,
          txt[-500:])
    check("... the 'applied=false' reason names the runnable deepmap_cli.py ... tune ... --apply",
          "applied=false" in txt and "deepmap_cli.py" in H._line_with(txt, "applied=false")
          and "--apply" in H._line_with(txt, "applied=false"), H._line_with(txt, "applied=false"))
    (root / ".deepmap" / "instance.json").write_text("{}", encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = C.cmd_tune(argparse.Namespace(root=str(root), status=False, report=False, apply=True,
                                           export_prior=False, min_queries=None, min_queries_bucket=None,
                                           dry_run=False))
    txt = out.getvalue()
    check("tune --apply refusal names the runnable tune command (not `deepmap tune`)",
          rc == 1 and "REFUSED" in txt and "`deepmap tune" not in txt and "deepmap_cli.py" in txt, txt[-400:])
    (root / ".deepmap" / "instance.json").write_text(json.dumps({"learned_fusion": {
        "arm_weights": {"vector": 1.2, "bm25": 0.9, "graph_weighted": 0.9}, "applied": False,
        "beats_equal": True, "significant": True}}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        C.cmd_tune(argparse.Namespace(root=str(root), status=False, report=False, apply=True, export_prior=False,
                                      min_queries=None, min_queries_bucket=None, dry_run=False))
    txt = out.getvalue()
    check("tune --apply: the benchmark A/B hint is the absolute, quoted benchmark/weight_ab.py G4 procedure "
          "(not the plan-only run_benchmark.py --ab-weights)",
          "--ab-weights" not in txt and f'"{(REPO / "benchmark" / "weight_ab.py").as_posix()}"' in txt, txt[-400:])
    home = work / "priorHome"
    home.mkdir()
    (root / ".deepmap" / "instance.json").write_text("{}", encoding="utf-8")
    with H._env(DEEPMAP_HOME=str(home)):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            C.cmd_tune(argparse.Namespace(root=str(root), status=False, report=False, apply=False,
                                          export_prior=True, min_queries=None, min_queries_bucket=None,
                                          dry_run=False))
    txt = out.getvalue()
    check("tune --export-prior refusal names the runnable tune command", "`deepmap tune" not in txt
          and "deepmap_cli.py" in txt, txt[-300:])
    empty = work / "tuneEmpty"
    empty.mkdir()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        C.cmd_tune(argparse.Namespace(root=str(empty), status=False, report=False, apply=False, export_prior=False,
                                      min_queries=None, min_queries_bucket=None, dry_run=True, objective="ndcg",
                                      optimizer="grid", tune_rrf_k=False, no_profiles=False, seed_from_prior=False))
    ln = H._line_with(out.getvalue(), "labeled queries")
    check("tune --dry-run on an empty log shows the cold-start threshold (0/150)", "0/150" in ln, ln)


# ---- G7: --edges naming only unknown types ----------------------------------------------------------------
def gate_edges_all_unknown(C, work: Path) -> None:
    print("\n-- G7: `impact --edges` with only unknown types is a usage error, not a silent CALLS-only walk --")
    p = work / "edgesP"
    p.mkdir()
    seen: dict = {}

    def _qi(drv, name, limit, edge_types=None, max_hops=None):
        seen["edge_types"] = edge_types
        return []
    over = {"retrieval": types.SimpleNamespace(query_impact=_qi, _IMPACT_MAX_HOPS_CAP=5),
            "port_registry": types.SimpleNamespace(touch_last_used=lambda *a, **k: None)}
    opened = []

    class _Drv(H._GraphDriver):
        def __init__(self):
            super().__init__(owner=None, nodes=3)
            opened.append(1)
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        saved = C._driver
        C._driver = lambda: _Drv()
        try:
            rc, raised, out, err = H._main(C, ["impact", "save", "--edges", "CASCADE,BOGUS-1"], overrides=over, cwd=p)
        finally:
            C._driver = saved
    check("only unknown --edges types: exit 2, no traceback", raised is None and rc == 2, f"{rc} {raised!r}")
    check("... no driver was opened and query_impact never ran", not opened and "edge_types" not in seen,
          f"opened={len(opened)} seen={seen}")
    check("... the message names the unknown types, the known ones, and how to get the default walk",
          "CASCADE" in err and "CASCADES" in err and "omit --edges" in err, err[-400:])
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        rc, raised, out, err = H._main(C, ["impact", "save", "--edges", "CASCADE,renders"], overrides=over,
                                       driver=H._GraphDriver(owner=None, nodes=3), cwd=p)
    check("partly unknown: walks the known ones (normalised), warns about the rest",
          rc == 0 and seen.get("edge_types") == ["RENDERS"] and "CASCADE" in err, f"{rc} {seen} {err[-300:]}")
    seen.clear()
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        rc, raised, out, err = H._main(C, ["impact", "save"], overrides=over,
                                       driver=H._GraphDriver(owner=None, nodes=3), cwd=p)
    check("no --edges: edge_types stays None (the default walk; the CLI never sends [])",
          rc == 0 and seen.get("edge_types") is None, f"{rc} {seen}")


# ---- D7: hint / output drift -------------------------------------------------------------------------------
def gate_drift(C, work: Path) -> None:
    print("\n-- D7: hint and output drift --")
    proj = work / "learnP"
    proj.mkdir()
    with H._env(DEEPMAP_LEARNING=None):
        _rc, _raised, snippet, _err = H._main(C, ["learning", "--enable"], cwd=proj)
    try:
        body = json.loads(snippet[snippet.index("{"):snippet.rindex("}") + 1])
        matcher = body["hooks"]["PostToolUse"][0]["matcher"]
    except Exception:  # noqa: BLE001
        matcher = ""
    alts = set(matcher.split("|"))
    check("learning --enable: the matcher covers MultiEdit and NotebookEdit (the hook grades them as edits)",
          {"MultiEdit", "NotebookEdit"} <= alts, matcher)
    check("... and keeps the canonical Read|Edit|Write|.*deepmap_impact substring", "Read|Edit|Write|.*deepmap_impact"
          in matcher, matcher)
    check("... and says the hook path is this plugin version's (re-run after a plugin update)",
          "plugin update" in snippet and "learning --enable" in snippet, snippet[-500:])
    doc = C.cmd_hibernate.__doc__ or ""
    check("hibernate's docstring no longer claims 'Revive is automatic on the next use'",
          "automatic on the next use" not in doc, doc)
    fake_p = types.SimpleNamespace(hibernate_idle=lambda target, idle_minutes, scope: 2)
    saved = C._load
    C._load = lambda name: fake_p if name == "provision" else saved(name)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            C.cmd_hibernate(argparse.Namespace(target=".", idle_minutes=30, all=False, others=True))
    finally:
        C._load = saved
    check("hibernate's output says how a hibernated instance comes back (heal / session start; graph kept)",
          "heal" in out.getvalue() and "kept" in out.getvalue(), out.getvalue())
    # "1 result" (singular)
    one = work / "oneR"
    one.mkdir()

    class _One:
        def __init__(self, *a, **k):
            pass

        def search(self, q, top_k=15):
            return [{"type": "Function", "name": "f", "file": "a.py", "line": 1, "score": 0.5,
                     "summary": "x" * 300}]
    over = {"embeddings": types.SimpleNamespace(embed_query=lambda q: [0.0]),
            "retrieval": types.SimpleNamespace(DeepMapRetriever=_One),
            "learned_fusion": types.SimpleNamespace(resolve_arm_weights_for_live=lambda **k: None),
            "retrieval_log": types.SimpleNamespace(maybe_log_search=lambda *a, **k: None),
            "port_registry": types.SimpleNamespace(touch_last_used=lambda *a, **k: None)}
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        rc, raised, out, err = H._main(C, ["search", "q"], overrides=over, driver=H._GraphDriver(nodes=3), cwd=one)
    check("search with one hit says '1 result for', not '1 results'", "1 result for" in out and "1 results" not in out,
          out[:200])
    check("... a long summary is cut with an ellipsis (never silently mid-word at 70 chars)",
          "…" in out, out[-200:])
    # help text on the flags users reach for
    ap = C.build_parser()
    sub = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))
    missing = []
    for cmd, flags in (("search", ("--top-k",)), ("impact", ("--limit", "--max-hops", "--edges")),
                       ("tune", ("--status", "--report", "--apply", "--dry-run", "--min-queries")),
                       ("enrich", ("--deterministic", "--dry-run", "--status", "--confirm-spend", "--max-nodes")),
                       ("sync", ("--stale-cleanup", "--switch-project", "--force-prune"))):
        sp = sub.choices[cmd]
        for act in sp._actions:
            if any(f in act.option_strings for f in flags) and not (act.help or "").strip():
                missing.append(f"{cmd} {act.option_strings[0]}")
    check("every user-facing flag of search/impact/tune/enrich/sync carries help text", not missing, str(missing))
    # line-buffered stdout
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    saved_out, saved_err = sys.stdout, sys.stderr
    try:
        sys.stdout = buf
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        C._utf8()
        lb, enc = sys.stdout.line_buffering, sys.stdout.encoding
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    check("_utf8() makes stdout UTF-8 AND line-buffered (no out-of-order lines under a pipe)",
          lb is True and enc.lower().replace("-", "") == "utf8", f"line_buffering={lb} encoding={enc}")
    # impact stamps last_used like search
    touched = []
    imp = work / "impTouch"
    imp.mkdir()
    over = {"retrieval": types.SimpleNamespace(query_impact=lambda *a, **k: [], _IMPACT_MAX_HOPS_CAP=5),
            "port_registry": types.SimpleNamespace(touch_last_used=lambda root: touched.append(str(root)))}
    with H._env(NEO4J_URI="bolt://localhost:7999"):
        rc, raised, out, err = H._main(C, ["impact", "save"], overrides=over, driver=H._GraphDriver(nodes=3), cwd=imp)
    check("`deepmap impact` stamps this project's last_used (the idle-hibernation signal), like search",
          rc == 0 and touched, f"{rc} {raised!r} touched={touched}")


# ---- F7: freshness sees uncommitted edits made after a clean build ---------------------------------------
def gate_fresh_dirty(C, work: Path) -> None:
    print("\n-- F7: freshness sees UNCOMMITTED edits made after a clean build --")
    repo = work / "freshR"
    head = H._git_repo(repo)
    if not head:
        check("(skip) git not available", True)
        return
    built = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
    H._write_manifest(repo, built_at_sha=head, dirty=False, built_at_utc=built, verdict="READY")
    ln = C._staleness_line(repo) or ""
    check("clean tree after a clean build: fresh", "fresh" in ln and "STALE" not in ln, ln)
    (repo / ".mcp.json").write_text("{}", encoding="utf-8")
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    ln = C._staleness_line(repo) or ""
    check("DeepMap's own artifacts (.deepmap/, graphify-out/, .mcp.json) never read as edits", "STALE" not in ln, ln)
    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    ln = C._staleness_line(repo) or ""
    check("a tracked source file edited after the build: 'may be STALE', naming it", "STALE" in ln and "a.py" in ln, ln)
    H._git("checkout", "--", "a.py", cwd=repo)
    (repo / "new_mod.py").write_text("def b():\n    return 3\n", encoding="utf-8")
    ln = C._staleness_line(repo) or ""
    check("a NEW untracked source file after the build: 'may be STALE'", "STALE" in ln and "new_mod.py" in ln, ln)
    os.unlink(repo / "new_mod.py")
    old = time.time() - 3600
    (repo / "b.py").write_text("x = 1\n", encoding="utf-8")
    os.utime(repo / "b.py", (old, old))
    ln = C._staleness_line(repo) or ""
    check("an uncommitted file NOT touched since the build (older mtime) is not flagged", "STALE" not in ln, ln)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-7 gates (W3: I4 .mcp.json claim, embed server, hung wording, edge mix, drift)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in saved_env:
        if k != "DEEPMAP_HOME":
            os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
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
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_r7_{time.time_ns()}")
            P = _load_module(BUILD / "provision.py", f"dm_prov_r7_{time.time_ns()}")
            for gate in (lambda: gate_mcp_json_claim(C, PR, P, work),
                         lambda: gate_setup_blocker(C, PR, work),
                         lambda: gate_embed_server(C, PR, work),
                         lambda: gate_embed_server_real_protocol(C),
                         lambda: gate_hung(C, PR, work),
                         lambda: gate_hung_real_driver(C),
                         lambda: gate_edge_mix(C, PR, work),
                         lambda: gate_tune_wording(C, work),
                         lambda: gate_edges_all_unknown(C, work),
                         lambda: gate_drift(C, work),
                         lambda: gate_fresh_dirty(C, work)):
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
