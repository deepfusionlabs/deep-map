#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for ROUND-8 J4 in the MCP servers (W4): the search server dials 127.0.0.1, not localhost.

THE DEFECT (round-7 live validation, L1/L2): every per-project Neo4j instance binds 127.0.0.1 only, but
DeepMap-generated URIs said bolt://localhost:<port>. On Windows `localhost` resolves to ::1 FIRST, so each NEW
Bolt connection paid a refused IPv6 connect (~2 s: driver connect p50 2054 ms vs 29 ms) before falling back to
IPv4 — and anything listening on [::1]:<port> (Docker publishes [::1]:7687) was reached instead of DeepMap's
instance. The search server's own default (NEO4J_URI unset) was 'bolt://localhost:7687', and its AuthBackoff /
outage messages named that URI.

THE FIX (binding J4): the default is port_registry.bolt_uri(BASE_BOLT) == 'bolt://127.0.0.1:7687' (one helper for
every DeepMap-generated URI; a literal 127.0.0.1 fallback if port_registry cannot be imported). An operator's
NEO4J_URI (or the one provision injects) is used VERBATIM — never rewritten. No DeepMap-generated
'bolt://localhost' string is left in the W4 files (AST scan, docstrings included).

Hermetic: the neo4j driver's GraphDatabase and the embeddings module are faked; no Neo4j, no sockets, no model.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import os
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
if str(BUILD) not in sys.path:
    sys.path.insert(0, str(BUILD))

_PASS = 0
_FAIL = 0

W4_FILES = ("deepmap_search_server.py", "deepmap_embed_server.py", "mcp_bounds.py", "retrieval.py", "embeddings.py")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail else ''}")


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


class _World:
    def __init__(self):
        self.uris: list = []


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        # _detect_search_clause's probe: the Neo4j 5.x answer (a syntax error) -> the procedure path, silently
        exc = RuntimeError("Invalid input 'SEARCH' (fake 5.x syntax error)")
        exc.code = "Neo.ClientError.Statement.SyntaxError"
        raise exc


class _Driver:
    def verify_connectivity(self):
        return None

    def session(self, **kw):
        return _Session()

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
def _server(w: _World, port_registry_mod=None):
    """A fresh search server whose `from neo4j import GraphDatabase` records the URI it is asked to dial."""
    import neo4j
    saved_gdb, saved_emb = neo4j.GraphDatabase, sys.modules.get("embeddings")
    saved_pr = sys.modules.get("port_registry")

    class _GDB:
        @staticmethod
        def driver(uri, *a, **kw):
            w.uris.append(uri)
            return _Driver()

    neo4j.GraphDatabase = _GDB
    sys.modules["embeddings"] = _fake_embeddings()
    if port_registry_mod is not None:
        sys.modules["port_registry"] = port_registry_mod
    try:
        spec = importlib.util.spec_from_file_location(f"dm_srv_uri_r8_{time.monotonic_ns()}",
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
        if port_registry_mod is not None:
            if saved_pr is None:
                sys.modules.pop("port_registry", None)
            else:
                sys.modules["port_registry"] = saved_pr


def _dial(env_uri, port_registry_mod=None) -> "tuple[str | None, str]":
    """(the URI the search server dialled on its first retriever init, its AuthBackoff label)."""
    w = _World()
    with _env(NEO4J_URI=env_uri, DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None):
        with _server(w, port_registry_mod) as srv:
            srv._get_retriever()
            where = getattr(getattr(srv, "_AUTH", None), "where", "")
    return (w.uris[0] if w.uris else None), where


def gate_no_localhost_literal() -> None:
    print("\n-- no DeepMap-generated 'bolt://localhost' string is left in the W4 files (docstrings included) --")
    for name in W4_FILES:
        src = (BUILD / name).read_text(encoding="utf-8")
        hits = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if "bolt://localhost" in low or "neo4j://localhost" in low:
                    hits.append(getattr(node, "lineno", "?"))
        check(f"{name}: no bolt://localhost / neo4j://localhost string constant", not hits, f"lines {hits}")


def gate_default_uri() -> None:
    print("\n-- NEO4J_URI unset: the search server dials port_registry.bolt_uri(BASE_BOLT) (127.0.0.1) --")
    import port_registry as PR
    expected = PR.bolt_uri(PR.BASE_BOLT)
    check("port_registry.bolt_uri(BASE_BOLT) is bolt://127.0.0.1:7687 (W1's helper)", expected == "bolt://127.0.0.1:7687",
          expected)
    uri, where = _dial(None)
    check("the driver is opened on bolt://127.0.0.1:7687 (was bolt://localhost:7687)", uri == expected, uri)
    check("the AuthBackoff / outage label names that same URI", where == f"Neo4j at {expected}", where)
    uri_empty, _w = _dial("")
    check("an EMPTY NEO4J_URI falls back to the same default (it is not a URI)", uri_empty == expected, uri_empty)
    # port_registry unusable -> still the 127.0.0.1 default (never localhost)
    broken = types.ModuleType("port_registry")   # no bolt_uri / BASE_BOLT
    uri_b, where_b = _dial(None, port_registry_mod=broken)
    check("port_registry unusable: the fallback default is still bolt://127.0.0.1:7687",
          uri_b == "bolt://127.0.0.1:7687" and "127.0.0.1:7687" in where_b, f"{uri_b} / {where_b}")


def gate_operator_uri_verbatim() -> None:
    print("\n-- an explicit NEO4J_URI (operator's, or provision's injected one) is dialled VERBATIM --")
    for raw in ("bolt://localhost:7689", "bolt://127.0.0.1:7811", "neo4j+s://db.example.com:7687",
                "bolt://LOCALHOST:7693", "bolt://[::1]:7695"):
        uri, where = _dial(raw)
        check(f"NEO4J_URI={raw!r} is used unchanged", uri == raw, uri)
        check(f"…and the label names it unchanged ({raw})", where == f"Neo4j at {raw}", where)


def gate_last_used_port() -> None:
    print("\n-- the last_used stamp still keys on the injected URI's port (127.0.0.1 form) --")
    import port_registry as PR
    seen: list = []
    saved = PR.touch_last_used_by_bolt
    PR.touch_last_used_by_bolt = lambda port, env=None: seen.append(port)
    w = _World()

    class _Ret:
        def search(self, q, top_k=15):
            return [{"name": "x", "score": 1.0}]

    try:
        with _env(NEO4J_URI="bolt://127.0.0.1:7811", DEEPMAP_APPLY_LEARNED=None, DEEPMAP_RETRIEVAL_LOG=None):
            with _server(w) as srv:
                srv._retriever = _Ret()
                srv.deepmap_search("auth flow")
    finally:
        PR.touch_last_used_by_bolt = saved
    check("touch_last_used_by_bolt got the bolt port 7811 from bolt://127.0.0.1:7811", seen == [7811], seen)


def main() -> int:
    print("mcp_uri_r8_gates — ROUND-8 J4 in the MCP servers (127.0.0.1, operator URI verbatim)")
    gate_no_localhost_literal()
    gate_default_uri()
    gate_operator_uri_verbatim()
    gate_last_used_port()
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
