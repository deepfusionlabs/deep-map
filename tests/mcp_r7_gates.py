#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the MCP servers + embeddings, round 7 (W4).

THE DEFECTS (round-6 review + the bug-by-bug sweep, reproduced hermetically):
  I4 race      deepmap_search stored each call's learned weights on the ONE shared retriever
               (`ret.arm_weights = ...`); tool bodies now run concurrently in worker threads, so with
               DEEPMAP_APPLY_LEARNED=1 one query could be fused with ANOTHER query's per-intent weights.
  boundary     an out-of-range top_k was clamped silently, and a truncated query was reported only on the
               server's stderr — the caller never learned either.
  outage       a stopped / hibernated Neo4j reached the agent as the raw driver text (ServiceUnavailable ...)
               with no next step (auth errors and deadlines were already made readable).
  T4d-I/T10d-W 'SEARCH clause not supported' was printed on EVERY retriever construction (every CLI search)
               against Neo4j 5.x, where the procedure fallback is the normal path; and generate_embeddings ran
               for 15-68 minutes on a large repo without a single progress line.
Everything here is off the ranking path: the frozen retriever is not edited (the race fix searches on a
per-call copy), and the embedding vectors / insert order are unchanged by the progress output.
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

_PASS = 0
_FAIL = 0


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
def _module(name: str, mod):
    saved = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        yield mod
    finally:
        if saved is not None:
            sys.modules[name] = saved
        else:
            sys.modules.pop(name, None)


@contextlib.contextmanager
def _stderr():
    buf, saved = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = saved


_ENV_KEYS = ("DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY", "DEEPMAP_PROJECT_GUARD", "DEEPMAP_APPLY_LEARNED",
             "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_LEARNING", "DEEPMAP_DEBUG", "DEEPMAP_EMBED_PROGRESS_S", "NEO4J_URI",
             "DEEPMAP_MCP_WARMUP_TIMEOUT_S", "DEEPMAP_MCP_TOOL_TIMEOUT_S")
_HIT = {"id": "1", "name": "hit", "type": "Function", "file": "a.py", "line": 1, "summary": "",
        "confidence": "EXTRACTED", "score": 1.0}


def _call(srv, name, args):
    """(isError?, payload-or-error-text) through FastMCP's real tool manager (the bounded_tool wrapper)."""
    import anyio

    async def go():
        return await srv.mcp._tool_manager.call_tool(name, args, convert_result=False)
    try:
        return False, anyio.run(go)
    except Exception as exc:  # noqa: BLE001 — FastMCP raises ToolError for an isError result
        return True, str(exc)


# ---------------------------------------------------------------------------------------------------------
def gate_arm_weights_race(S) -> None:
    print("\n-- I4: concurrent searches never share mutable arm_weights (learned fusion applied) --")

    class _SharedRetriever:
        def __init__(self):
            self.arm_weights = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}
            self.fused_with = {}
            self.search_self = []

        def search(self, query, top_k=15):
            self.search_self.append(self)
            time.sleep(0.25)                      # embed + three arms: the window another call lands in
            used = dict(self.arm_weights)         # _weighted_rrf reads self.arm_weights only at fusion time
            shared.fused_with[query] = used
            return [dict(_HIT, name=query)]

    shared = _SharedRetriever()
    per_query = {"who calls save": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0},
                 "where is auth handled": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0}}
    lf = types.ModuleType("learned_fusion")
    lf.apply_enabled = lambda *a, **k: True
    lf.resolve_arm_weights_for_live = lambda query=None, **k: dict(per_query[query])
    lf.EQUAL_WEIGHTS = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}
    S._get_retriever = lambda: shared
    with _module("learned_fusion", lf):
        ta = threading.Thread(target=S.deepmap_search, args=("who calls save",))
        tb = threading.Thread(target=S.deepmap_search, args=("where is auth handled",))
        ta.start()
        time.sleep(0.05)
        tb.start()
        ta.join(5)
        tb.join(5)
    check("call A was fused with ITS OWN learned weights (not call B's)",
          shared.fused_with.get("who calls save") == per_query["who calls save"], str(shared.fused_with))
    check("call B was fused with its own learned weights", shared.fused_with.get("where is auth handled")
          == per_query["where is auth handled"], str(shared.fused_with))
    check("the shared retriever's arm_weights were never mutated",
          shared.arm_weights == {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}, str(shared.arm_weights))
    lf_off = types.ModuleType("learned_fusion")
    lf_off.apply_enabled = lambda *a, **k: False
    lf_off.resolve_arm_weights_for_live = lambda **k: {"vector": 9.0}
    lf_off.EQUAL_WEIGHTS = {}
    shared.search_self.clear()
    with _module("learned_fusion", lf_off):
        S.deepmap_search("default path")
    check("learned fusion OFF (the default): the shared retriever itself searches — byte-identical path",
          shared.search_self == [shared], str(shared.search_self))


class _FakeRetriever:
    def __init__(self, error=None):
        self.error, self.queries, self.arm_weights = error, [], {}

    def search(self, query, top_k=15):
        self.queries.append((query, top_k))
        if self.error is not None:
            raise self.error
        return [dict(_HIT)]

    def impact(self, symbol, limit=30, edge_types=None):
        if self.error is not None:
            raise self.error
        return []


def gate_boundary_notes(S) -> None:
    print("\n-- the caller hears about a clamped top_k and a truncated query (both were silent) --")
    fake = _FakeRetriever()
    S._get_retriever = lambda: fake
    out = S.deepmap_search("where is auth handled", top_k=80)
    notes = [r["note"] for r in out if set(r) == {"note"}]
    check("top_k=80 -> answered with 50 and a trailing note saying it was clamped",
          fake.queries[-1][1] == 50 and any("80" in n and "50" in n for n in notes), f"{fake.queries} {notes}")
    out = S.deepmap_search("retry upload " * 400)
    notes = [r["note"] for r in out if set(r) == {"note"}]
    check("a query over 2,000 chars -> a trailing note says it was truncated (was: server stderr only)",
          any("truncated" in n and "2000" in n for n in notes), str(notes))
    out = S.deepmap_search("where is auth handled", top_k=5)
    check("an in-range top_k and a normal query: the answer is unchanged (no note)", out == [dict(_HIT)], str(out))


def _fake_embeddings():
    """A stand-in `embeddings` (the servers import it lazily): the soft warm-up must not load the real model."""
    fake = types.ModuleType("embeddings")
    fake.ensure_model = lambda max_wait_s=None, retry_after_s=None: object()
    fake.embed_query = lambda text: [0.0] * 768
    fake.ModelUnavailable = type("ModelUnavailable", (RuntimeError,), {})
    fake.short_error = lambda e: f"{type(e).__name__}: {e}"
    fake.model_status = lambda: {"state": "ready"}
    return fake


def gate_outage_readable(S) -> None:
    print("\n-- a stopped Neo4j reads as 'not reachable -> /deep-map:setup', not a raw driver error --")
    from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired
    with _env(DEEPMAP_MCP_WARMUP_TIMEOUT_S="5", DEEPMAP_MCP_TOOL_TIMEOUT_S="5"), \
            _module("embeddings", _fake_embeddings()):
        for label, exc in (("ServiceUnavailable", ServiceUnavailable("Couldn't connect to localhost:7699")),
                           ("SessionExpired", SessionExpired("Failed to read from defunct connection"))):
            S._get_retriever = lambda e=exc: _FakeRetriever(e)
            err, text = _call(S, "deepmap_search", {"query": "auth flow"})
            check(f"{label} during search -> still an isError (T10: never an empty answer)", err, text[:200])
            check(f"{label}: the error says the database is not reachable and names /deep-map:setup",
                  "not reachable" in text and "/deep-map:setup" in text, text[:300])
            check(f"{label}: it keeps the driver's own text (the connection detail)",
                  "localhost:7699" in text or "defunct" in text, text[:300])
        S._get_retriever = lambda: _FakeRetriever(ServiceUnavailable("Couldn't connect to localhost:7699"))
        err, text = _call(S, "deepmap_impact", {"symbol": "save"})
        check("…impact too", err and "not reachable" in text and "/deep-map:setup" in text, text[:300])
        S._get_retriever = lambda: _FakeRetriever(ValueError("some query problem"))
        err, text = _call(S, "deepmap_search", {"query": "auth flow"})
        check("a non-outage error is passed through unchanged", err and "some query problem" in text
              and "not reachable" not in text, text[:200])
        S._AUTH.streak = 0
        S._AUTH._until = 0.0
        S._get_retriever = lambda: _FakeRetriever(AuthError("The client is unauthorized"))
        err, text = _call(S, "deepmap_search", {"query": "auth flow"})
        check("an auth REJECTION keeps its own backoff diagnosis (not relabelled as an outage)",
              err and "credentials" in text and "not reachable" not in text, text[:200])
        S._AUTH.streak = 0
        S._AUTH._until = 0.0


def gate_search_clause_note(R_path_alias) -> None:
    print("\n-- 'SEARCH clause not supported' is no longer printed on every retriever construction --")
    from neo4j.exceptions import CypherSyntaxError

    def syntax_error():
        e = CypherSyntaxError("Invalid input 'SEARCH': expected a graph pattern")
        e._neo4j_code = "Neo.ClientError.Statement.SyntaxError"
        return e

    class _Drv:
        def __init__(self, exc):
            self.exc = exc

        def session(self, **kw):
            drv = self

            class _S:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def run(s, *a, **k):
                    raise drv.exc
            return _S()

    with _env(DEEPMAP_DEBUG=None):
        R = _load("retrieval", R_path_alias + "_a")
        with _stderr() as buf:
            r1 = R.DeepMapRetriever(_Drv(syntax_error()), embed_fn=lambda q: [0.0])
            r2 = R.DeepMapRetriever(_Drv(syntax_error()), embed_fn=lambda q: [0.0])
        check("Neo4j 5.x (SEARCH is a syntax error): the procedure fallback is chosen", r1._use_search_clause is False
              and r2._use_search_clause is False)
        check("…silently — the expected case prints nothing", "SEARCH" not in buf.getvalue(), buf.getvalue()[:200])
        R = _load("retrieval", R_path_alias + "_b")
        with _stderr() as buf:
            R.DeepMapRetriever(_Drv(RuntimeError("connection refused")), embed_fn=lambda q: [0.0])
            R.DeepMapRetriever(_Drv(RuntimeError("connection refused")), embed_fn=lambda q: [0.0])
        check("an UNEXPECTED probe failure is reported — once per process, not per construction",
              buf.getvalue().count("SEARCH clause") == 1, buf.getvalue()[:300])
        check("…with the reason", "connection refused" in buf.getvalue(), buf.getvalue()[:300])
    with _env(DEEPMAP_DEBUG="1"):
        R = _load("retrieval", R_path_alias + "_c")
        with _stderr() as buf:
            R.DeepMapRetriever(_Drv(syntax_error()), embed_fn=lambda q: [0.0])
            R.DeepMapRetriever(_Drv(syntax_error()), embed_fn=lambda q: [0.0])
        check("DEEPMAP_DEBUG=1: the expected fallback is reported too (once)",
              buf.getvalue().count("SEARCH clause") == 1, buf.getvalue()[:300])


def gate_embed_progress() -> None:
    print("\n-- generate_embeddings reports progress on a long run (it was silent for 15-68 min) --")

    class _Clock:
        def __init__(self, step):
            self.t, self.step = 1000.0, step

        def __call__(self):
            self.t += self.step
            return self.t

    class _Model:
        def encode(self, texts, show_progress_bar=False):
            if isinstance(texts, str):
                texts = [texts]
            return [[float(len(t) % 7), float(sum(map(ord, t)) % 11)] + [0.0] * 766 for t in texts]

    class _Drv:
        def __init__(self, n):
            self.rows = [{"id": f"e{i}", "name": f"fn_{i}", "body_summary": f"does thing {i}", "docstring": None,
                          "summary": None, "rationale": None, "embed_hash": None, "key": f"k{i:05d}"}
                         for i in range(n)]
            self.sets = []

        def session(self, **kw):
            drv = self

            class _S:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def run(s, cypher, **p):
                    if "RETURN elementId(n) AS id" in cypher:
                        return list(drv.rows)
                    drv.sets.append((p.get("id"), tuple(p.get("emb") or ())[:2], p.get("h")))
                    return []
            return _S()

    def run(n, step, env_s=None):
        with _env(DEEPMAP_EMBED_PROGRESS_S=env_s):
            E = _load("embeddings", f"dm_embeddings_r7_{n}_{step}_{env_s}")
            E._model = _Model()
            if hasattr(E, "_progress_clock"):
                E._progress_clock = _Clock(step)
            drv = _Drv(n)
            with _stderr() as buf:
                count = E.generate_embeddings(drv, batch_size=100)
        return count, drv.sets, buf.getvalue()

    count, sets_slow, out = run(2500, 9.0)
    lines = [ln for ln in out.splitlines() if "[deepmap embed]" in ln]
    progress = [ln for ln in lines if "/2,500" in ln and "%" in ln]
    check("a slow 2,500-node run prints progress lines (N/M, percent)", len(progress) >= 3, out[:400])
    check("…each with a rate and a time estimate", progress and all("nodes/s" in ln and "left" in ln
                                                                    for ln in progress), str(progress[:2]))
    check("…at most one line per 30 s (not one per batch)", len(progress) < 25, str(len(progress)))
    check("a large job announces itself up front", any("embedding 2,500 nodes" in ln for ln in lines), str(lines[:2]))
    check("…and says when it is done", any("done" in ln for ln in lines), str(lines[-1:]))
    count_fast, sets_fast, out_fast = run(2500, 0.0)
    check("a fast run (under 30 s) prints no progress lines",
          not [ln for ln in out_fast.splitlines() if "%" in ln], out_fast[:200])
    check("the embeddings and their insert order are byte-identical with and without progress output",
          sets_slow == sets_fast and count == count_fast == 2500, f"{len(sets_slow)} vs {len(sets_fast)}")
    _c, _s, out_off = run(2500, 9.0, env_s="0")
    check("DEEPMAP_EMBED_PROGRESS_S=0 turns it off", "[deepmap embed]" not in out_off, out_off[:200])
    _c, _s, out_small = run(300, 0.0)
    check("a small job stays quiet (no start line, no progress)", "[deepmap embed]" not in out_small, out_small[:200])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — MCP servers + embeddings round 7: arm_weights race, boundary notes, outage text, notes (no Neo4j)")
    print("=" * 72)
    sys.path.insert(0, str(BUILD))
    with _env(**{k: None for k in _ENV_KEYS}):
        S = _load("deepmap_search_server", "dm_search_server_mcp_r7")
        gate_arm_weights_race(S)
        gate_boundary_notes(S)
        gate_outage_readable(S)
        gate_search_clause_note("dm_retrieval_mcp_r7")
        gate_embed_progress()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
