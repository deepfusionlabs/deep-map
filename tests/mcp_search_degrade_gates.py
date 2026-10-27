#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: deepmap_search DEGRADES to keyword-only (BM25) when the embedding model is unavailable.

THE CONTRACT (T10, pre-existing): an embedding-model outage is NOT a graph outage — search skips the two
vector arms and still answers from BM25 fulltext (DeepMapRetriever._embed_or_none). Doctor reports that
state as "vector search off (FTS-only)".

THE REGRESSION (round-1 review, HIGH): v0.28's bounded_tool ran a warm step before every deepmap_search
call that loaded the model with no guard, so a missing/broken ML stack made EVERY search an isError
("No module named sentence_transformers") before the retriever's FTS fallback could run. A model load
that HANGS (HF network timeouts) cost every call the whole 300 s warm-up budget, then an error.

LOCKED HERE (end to end, through the REAL MCP client, the REAL retriever and the REAL embeddings module —
only neo4j and sentence_transformers are faked):
  * model missing / failing / hung -> deepmap_search returns the BM25 hits (NOT isError), issues no vector
    query, and says so in ONE trailing {"note": ...} entry (keyword-only results; may miss semantic hits);
  * a failed or stuck load is not re-paid on every call (bounded, fast follow-up calls) and is retried
    after DEEPMAP_MCP_MODEL_RETRY_S, so a recovered model is picked up without a restart;
  * a healthy model is unchanged: vector arms run, no note;
  * a DOWN Neo4j still fails LOUD (isError) — only the model degrades;
  * deepmap_impact never needs the model; deepmap_embed (no degraded mode) errors clearly and fast.
Hermetic: temp DEEPMAP_HOME, NEO4J_URI unset, no sockets, no real model/torch, never touches ~/.deepmap.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
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


# ---------------------------------------------------------------- fake neo4j (cypher-routed) ----------
class _Graph:
    """What the fake instance answers + what it was asked."""

    def __init__(self, down: bool = False):
        self.down = down
        self.cyphers: list[str] = []

    def vector_queries(self) -> int:
        return sum("db.index.vector.queryNodes" in c for c in self.cyphers)


class _Session:
    def __init__(self, g: _Graph):
        self.g = g

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        cypher = str(cypher)
        self.g.cyphers.append(cypher)
        if "SEARCH n IN" in cypher:                       # _detect_search_clause probe -> procedure path
            raise RuntimeError("SEARCH clause not supported (fake)")
        if "db.index.fulltext.queryNodes" in cypher:
            return [{"id": "fts1", "name": "auth_flow", "type": "Function", "file": "auth.py", "line": 3,
                     "summary": "handles the auth flow", "score": 3.0}]
        if "db.index.vector.queryNodes" in cypher:
            return [{"id": "vec1", "name": "login_user", "type": "Function", "file": "login.py", "line": 9,
                     "summary": "logs a user in", "score": 0.9}]
        if "UNWIND $ids" in cypher:
            names = {"fts1": ("auth_flow", "auth.py", 3), "vec1": ("login_user", "login.py", 9)}
            return [{"id": i, "name": names[i][0], "type": "Function", "file": names[i][1],
                     "line": names[i][2], "summary": "", "confidence": "EXTRACTED"}
                    for i in params.get("ids", []) if i in names]
        if "CALLS" in cypher and "caller" in cypher:      # impact (existence probe / walk / one-hop)
            if "count(callee)" in cypher:
                return [{"matched": 1}]
            if "db.relationshipTypes" in cypher:
                return [{"types": ["CALLS"]}]
            return [{"caller": "main", "caller_type": "Function", "file": "m.py", "line": 1,
                     "confidence": "INFERRED", "callee": "auth_flow", "hop": 1, "via": "CALLS",
                     "via_path": ["CALLS"]}]
        if "db.relationshipTypes" in cypher:
            return [{"types": ["CALLS"]}]
        if "count(callee)" in cypher:
            return [{"matched": 1}]
        return []


class _ServiceUnavailable(Exception):
    """Named like the driver's instance-down error (the retriever's fatal classifier keys on it)."""


_ServiceUnavailable.__name__ = "ServiceUnavailable"


class _Driver:
    def __init__(self, g: _Graph):
        self.g = g

    def verify_connectivity(self):
        if self.g.down:
            raise _ServiceUnavailable("Couldn't connect to localhost:7699 (fake instance down)")

    def session(self, **kw):
        return _Session(self.g)

    def close(self):
        pass


def _fake_neo4j(g: _Graph) -> types.ModuleType:
    m = types.ModuleType("neo4j")

    class GraphDatabase:
        @staticmethod
        def driver(*a, **kw):
            return _Driver(g)

    m.GraphDatabase = GraphDatabase
    return m


# ---------------------------------------------------------------- fake sentence_transformers ----------
class _Loads:
    def __init__(self):
        self.n = 0


def _st_module(mode: str, loads: _Loads, *, delay: float = 0.0, release: threading.Event | None = None):
    """mode: 'ok' | 'fail' (raise after `delay`) | 'hang' (block until `release`)."""
    m = types.ModuleType("sentence_transformers")

    class _Model:
        def encode(self, text, show_progress_bar=False, **kw):
            return [0.01] * 768

    class SentenceTransformer:
        def __new__(cls, name, *a, **kw):
            loads.n += 1
            if mode == "ok":
                time.sleep(delay)
                return _Model()
            if mode == "hang":
                release.wait(30)
                raise OSError("gave up waiting for huggingface.co (fake)")
            time.sleep(delay)
            raise OSError("We couldn't connect to 'https://huggingface.co' to load this model (fake), and "
                          "couldn't find them in the cached files.\nCheck your internet connection or see how "
                          "to run the library in offline mode at 'https://huggingface.co/docs/...'.")

    m.SentenceTransformer = SentenceTransformer
    return m


@contextlib.contextmanager
def _world(g: _Graph, st_module):
    """Fresh server + REAL embeddings module, with neo4j / sentence_transformers / torch faked."""
    saved = {k: sys.modules.get(k) for k in ("neo4j", "sentence_transformers", "torch", "embeddings")}
    sys.modules["neo4j"] = _fake_neo4j(g)
    sys.modules["sentence_transformers"] = st_module        # None -> "No module named ..." on import
    sys.modules["torch"] = None                             # never import the real torch in a gate
    sys.modules.pop("embeddings", None)                     # the server imports the real one fresh
    try:
        spec = importlib.util.spec_from_file_location(f"dm_srv_degrade_{id(g)}", BUILD / "deepmap_search_server.py")
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        yield srv
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _embed_world(st_module):
    saved = {k: sys.modules.get(k) for k in ("sentence_transformers", "torch", "embeddings")}
    sys.modules["sentence_transformers"] = st_module
    sys.modules["torch"] = None
    sys.modules.pop("embeddings", None)
    spec = importlib.util.spec_from_file_location("dm_embed_degrade", BUILD / "deepmap_embed_server.py")
    e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e)
    return e, saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ---------------------------------------------------------------- the REAL MCP client ------------------
def _client_calls(srv_mcp, calls):
    """Run [(tool, args), ...] through a real mcp ClientSession (in-memory transport). Returns
    [(is_error, payload_list_or_text, seconds)]."""
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    out = []

    async def go():
        async with create_connected_server_and_client_session(srv_mcp) as client:
            for name, args in calls:
                t0 = time.monotonic()
                res = await client.call_tool(name, args)
                took = time.monotonic() - t0
                if res.isError:
                    out.append((True, " ".join(getattr(c, "text", "") for c in res.content), took))
                    continue
                sc = res.structuredContent
                if isinstance(sc, dict) and "result" in sc:
                    payload = sc["result"]
                else:
                    payload = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
                out.append((False, payload, took))

    anyio.run(go)
    return out


def _notes(payload) -> list[str]:
    return [p["note"] for p in payload if isinstance(p, dict) and set(p) == {"note"}]


def _hits(payload) -> list[str]:
    return [p.get("name") for p in payload if isinstance(p, dict) and "note" not in p]


# ---------------------------------------------------------------- gates --------------------------------
def gate_model_missing() -> None:
    print("\n-- the ML stack is MISSING: deepmap_search answers keyword-only, not isError --")
    g = _Graph()
    with _world(g, None) as srv, _env(DEEPMAP_MCP_MODEL_RETRY_S=None):
        (err, payload, _t), (err2, payload2, _t2) = _client_calls(
            srv.mcp, [("deepmap_search", {"query": "auth flow"}), ("deepmap_search", {"query": "login"})])
    check("deepmap_search is NOT an error when the model can't load", not err, str(payload)[:300])
    if err:
        return
    check("it returns the BM25 (fulltext) hit", "auth_flow" in _hits(payload), str(payload))
    check("no vector query was issued (both vector arms skipped)", g.vector_queries() == 0, str(g.vector_queries()))
    notes = _notes(payload)
    check("exactly ONE trailing note says the answer is keyword-only",
          len(notes) == 1 and payload[-1] == {"note": notes[0]}, str(payload))
    check("the note names the degraded mode and why (BM25 / keyword-only + the model)",
          notes and ("keyword" in notes[0].lower() or "bm25" in notes[0].lower())
          and "model" in notes[0].lower(), str(notes))
    check("the note points to a runnable next step (/deep-map:doctor)",
          notes and "/deep-map:doctor" in notes[0], str(notes))
    check("a second search also degrades (no isError on later calls)", not err2 and "auth_flow" in _hits(payload2),
          str(payload2)[:200])


def gate_model_load_fails_slowly() -> None:
    print("\n-- a load that FAILS slowly (HF network) is paid once, then degrades fast; retried later --")
    g = _Graph()
    loads = _Loads()
    st = _st_module("fail", loads, delay=0.6)
    with _world(g, st) as srv, _env(DEEPMAP_MCP_MODEL_RETRY_S="600"):
        res = _client_calls(srv.mcp, [("deepmap_search", {"query": "auth flow"}),
                                      ("deepmap_search", {"query": "auth flow"}),
                                      ("deepmap_search", {"query": "auth flow"})])
    (e1, p1, t1), (e2, p2, t2), (e3, p3, t3) = res
    check("first call degrades (BM25 hit + note), not isError", not e1 and "auth_flow" in _hits(p1) and _notes(p1),
          str(p1)[:300])
    n1 = (_notes(p1) or [""])[0]
    check("the note is ONE short line (HF's multi-line load advice is trimmed to its first line)",
          n1 and "\n" not in n1 and len(n1) < 600 and "huggingface" in n1, repr(n1[:700]))
    check("follow-up calls are FAST (< 0.4 s) — the failed load is not re-paid per call",
          not e2 and not e3 and t2 < 0.4 and t3 < 0.4, f"t2={t2:.2f}s t3={t3:.2f}s")
    check("the model load was attempted once inside the retry window", loads.n == 1, f"loads={loads.n}")
    # retry window elapsed + the model recovered -> the next call loads it and uses the vector arms
    g2 = _Graph()
    loads2 = _Loads()
    st2 = _st_module("fail", loads2, delay=0.0)
    with _world(g2, st2) as srv, _env(DEEPMAP_MCP_MODEL_RETRY_S="0.2"):
        import anyio
        from mcp.shared.memory import create_connected_server_and_client_session
        seen = {}

        async def go():
            async with create_connected_server_and_client_session(srv.mcp) as client:
                r1 = await client.call_tool("deepmap_search", {"query": "auth flow"})
                st2.SentenceTransformer = _st_module("ok", loads2).SentenceTransformer   # the stack is fixed
                await anyio.sleep(0.3)                                                  # retry window passes
                r2 = await client.call_tool("deepmap_search", {"query": "auth flow"})
                seen["r1"], seen["r2"] = r1, r2

        anyio.run(go)
    r1, r2 = seen.get("r1"), seen.get("r2")
    p2 = (r2.structuredContent or {}).get("result", []) if r2 is not None else []
    check("after DEEPMAP_MCP_MODEL_RETRY_S the load is retried and a recovered model is used (no restart)",
          r1 is not None and not r1.isError and r2 is not None and not r2.isError
          and g2.vector_queries() > 0 and not _notes(p2), f"vector_queries={g2.vector_queries()} p2={p2}")


def gate_model_load_hangs() -> None:
    print("\n-- a load that HANGS costs at most the warm-up budget once, then degrades immediately --")
    g = _Graph()
    loads = _Loads()
    release = threading.Event()
    st = _st_module("hang", loads, release=release)
    try:
        with _world(g, st) as srv, _env(DEEPMAP_MCP_WARMUP_TIMEOUT_S="0.8", DEEPMAP_MCP_TOOL_TIMEOUT_S="5"):
            res = _client_calls(srv.mcp, [("deepmap_search", {"query": "auth flow"}),
                                          ("deepmap_search", {"query": "auth flow"})])
    finally:
        release.set()
    (e1, p1, t1), (e2, p2, t2) = res
    check("the first call answers keyword-only after ~the warm-up budget (not a 'still starting up' error)",
          not e1 and "auth_flow" in _hits(p1) and bool(_notes(p1)) and t1 < 3.0, f"err={e1} t1={t1:.2f}s {str(p1)[:200]}")
    check("the second call does NOT wait on the stuck load again (< 0.5 s)", not e2 and t2 < 0.5 and
          "auth_flow" in _hits(p2), f"t2={t2:.2f}s")
    check("the note says the model is still loading", _notes(p1) and "load" in _notes(p1)[0].lower(), str(_notes(p1)))


def gate_healthy_model_unchanged() -> None:
    print("\n-- a HEALTHY model: vector arms run, no note (the normal path is unchanged) --")
    g = _Graph()
    loads = _Loads()
    with _world(g, _st_module("ok", loads)) as srv:
        ((err, payload, _t),) = _client_calls(srv.mcp, [("deepmap_search", {"query": "auth flow"})])
    check("healthy search is not an error", not err, str(payload)[:200])
    check("the vector arms ran", g.vector_queries() > 0, str(g.vector_queries()))
    check("fused hits from BOTH the vector and BM25 arms", {"auth_flow", "login_user"} <= set(_hits(payload or [])),
          str(payload))
    check("no degraded note on a healthy answer", not _notes(payload or []), str(payload))
    check("the model loaded exactly once", loads.n == 1, f"loads={loads.n}")


def gate_neo4j_down_still_loud() -> None:
    print("\n-- a DOWN Neo4j still fails LOUD (T10) — only the MODEL degrades --")
    for st_desc, st in (("model missing", None), ("model healthy", "ok")):
        g = _Graph(down=True)
        mod = None if st is None else _st_module(st, _Loads())
        with _world(g, mod) as srv:
            ((err, payload, _t),) = _client_calls(srv.mcp, [("deepmap_search", {"query": "auth flow"})])
        check(f"Neo4j down + {st_desc}: deepmap_search is isError (never a fabricated empty answer)", err,
              str(payload)[:200])
        check(f"Neo4j down + {st_desc}: the error names the connection problem",
              err and ("connect" in str(payload).lower() or "unavailable" in str(payload).lower()), str(payload)[:200])


def gate_impact_needs_no_model() -> None:
    print("\n-- deepmap_impact never waits on / fails for the embedding model --")
    g = _Graph()
    with _world(g, None) as srv:
        ((err, payload, _t),) = _client_calls(srv.mcp, [("deepmap_impact", {"symbol": "auth_flow"})])
    check("impact answers with the model missing", not err and any(isinstance(p, dict) and p.get("caller") == "main"
                                                                   for p in (payload or [])), str(payload)[:200])


def gate_embed_server_errors_clearly_and_fast() -> None:
    print("\n-- deepmap_embed (no degraded mode) errors CLEARLY, and a failed load is not re-paid per call --")
    loads = _Loads()
    E, saved = _embed_world(_st_module("fail", loads, delay=0.6))
    try:
        with _env(DEEPMAP_MCP_MODEL_RETRY_S="600"):
            res = _client_calls(E.mcp, [("deepmap_embed", {"text": "auth"}), ("deepmap_embed", {"text": "auth"})])
    finally:
        _restore(saved)
    (e1, m1, t1), (e2, m2, t2) = res
    check("deepmap_embed is isError when the model can't load", e1 and e2, f"{m1[:120]} | {m2[:120]}")
    check("the error says the embedding model is unavailable and names a next step",
          e1 and "model" in m1.lower() and "/deep-map:doctor" in m1, m1[:300])
    check("the second call fails FAST (< 0.4 s) without a second load attempt", t2 < 0.4 and loads.n == 1,
          f"t2={t2:.2f}s loads={loads.n}")


def gate_embeddings_state_api() -> None:
    print("\n-- embeddings: a non-blocking model_status() + ensure_model() for the long-lived servers --")
    saved = {k: sys.modules.get(k) for k in ("sentence_transformers", "torch")}
    loads = _Loads()
    sys.modules["sentence_transformers"] = _st_module("fail", loads)
    sys.modules["torch"] = None
    try:
        spec = importlib.util.spec_from_file_location("dm_emb_state", BUILD / "embeddings.py")
        Em = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(Em)
        have = all(callable(getattr(Em, n, None)) for n in ("model_status", "ensure_model"))
        check("embeddings exposes model_status() and ensure_model()", have)
        if not have:
            return
        check("a fresh process reports the model as cold", Em.model_status().get("state") == "cold",
              str(Em.model_status()))
        try:
            Em._get_model()
            raised = False
        except OSError:
            raised = True
        check("_get_model still raises the original error (unchanged for sync / the CLI)", raised)
        st = Em.model_status()
        check("model_status() records the failure and its reason", st.get("state") == "failed"
              and "huggingface" in str(st.get("error", "")).lower(), str(st))
        try:
            Em.ensure_model(max_wait_s=1, retry_after_s=600)
            fast = False
        except Exception as exc:  # noqa: BLE001
            fast = type(exc).__name__ == "ModelUnavailable"
        check("ensure_model() inside the retry window raises ModelUnavailable at once (no reload)",
              fast and loads.n == 1, f"loads={loads.n}")
        Em._get_model  # noqa: B018 — the direct path stays available
        sys.modules["sentence_transformers"] = _st_module("ok", loads)
        m = Em.ensure_model(max_wait_s=1, retry_after_s=0)
        check("ensure_model() retries once the window has passed and returns the model",
              m is not None and Em.model_status().get("state") == "ready", str(Em.model_status()))
        check("embed_query output is unchanged (768 floats)", len(Em.embed_query("x")) == 768)
    finally:
        _restore(saved)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — deepmap_search degrades to keyword-only when the model is unavailable (T10; no Neo4j)")
    print("=" * 72)
    with tempfile.TemporaryDirectory() as home, _env(DEEPMAP_HOME=home, NEO4J_URI=None, DEEPMAP_MCP_TRANSPORT=None,
                                                     DEEPMAP_RETRIEVAL_LOG=None, DEEPMAP_APPLY_LEARNED=None,
                                                     DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None,
                                                     DEEPMAP_MCP_WARMUP_TIMEOUT_S=None,
                                                     DEEPMAP_MCP_TOOL_TIMEOUT_S=None), \
            contextlib.redirect_stderr(io.StringIO()):
        gates = (gate_model_missing, gate_model_load_fails_slowly, gate_model_load_hangs,
                 gate_healthy_model_unchanged, gate_neo4j_down_still_loud, gate_impact_needs_no_model,
                 gate_embed_server_errors_clearly_and_fast, gate_embeddings_state_api)
        for gate in gates:
            try:
                gate()
            except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crashed suite
                check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
