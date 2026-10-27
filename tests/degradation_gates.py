#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for graceful FTS-only degradation (spec --no-embeddings path).

If the embedding model can't load/encode, search must degrade to BM25-only (still useful) rather
than failing. Uses a fake Neo4j driver that serves canned fulltext + hydrate rows and returns []
for the vector arms — so we can prove the degraded path end-to-end without a live DB or a model.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"


def _load_retrieval():
    spec = importlib.util.spec_from_file_location("dm_retrieval", BUILD / "retrieval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _raises_import_error(_driver):
    raise ImportError("sentence-transformers not installed")


def _raises_runtime_error(_driver):
    raise RuntimeError("Neo4j connection reset")   # a NON-model error (DB outage / real bug)


_FTS_ROWS = [
    {"id": "n1", "name": "parse_config", "type": "Function", "file": "cfg.py", "line": 4,
     "summary": "parse the config", "score": 3.0},
    {"id": "n2", "name": "load_config", "type": "Function", "file": "cfg.py", "line": 9,
     "summary": "load the config", "score": 1.5},
]
_HYDRATE_ROWS = {
    "n1": {"id": "n1", "name": "parse_config", "type": "Function", "file": "cfg.py", "line": 4,
           "summary": "parse the config", "confidence": "EXTRACTED"},
    "n2": {"id": "n2", "name": "load_config", "type": "Function", "file": "cfg.py", "line": 9,
           "summary": "load the config", "confidence": "EXTRACTED"},
}


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        c = cypher.lower()
        if "db.index.fulltext.querynodes" in c:
            return iter([dict(r) for r in _FTS_ROWS])
        if "unwind $ids" in c:
            ids = params.get("ids", [])
            return iter([_HYDRATE_ROWS[i] for i in ids if i in _HYDRATE_ROWS])
        return iter([])     # vector/graph/SEARCH-probe -> empty


class _FakeDriver:
    def session(self):
        return _FakeSession()

    def verify_connectivity(self):
        pass


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


def _bad_embed(_q):
    raise RuntimeError("model unavailable")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — FTS-only graceful-degradation gates (no Neo4j, no model)")
    print("=" * 64)

    R = _load_retrieval()

    print("\n-- _embed_or_none --")
    ret_bad = R.DeepMapRetriever(_FakeDriver(), embed_fn=_bad_embed)
    check("returns None when the model raises", ret_bad._embed_or_none("q") is None)
    ret_ok = R.DeepMapRetriever(_FakeDriver(), embed_fn=lambda q: [0.0] * 768)
    check("returns the vector when the model works", ret_ok._embed_or_none("q") == [0.0] * 768)

    print("\n-- degraded search (embedding down -> BM25-only) --")
    try:
        res = ret_bad.search("parse the config", top_k=5)
        check("search does NOT raise on embedding failure", True)
    except Exception as e:  # noqa: BLE001
        check("search does NOT raise on embedding failure", False, f"{type(e).__name__}: {e}")
        res = []
    check("degraded search still returns FTS results", len(res) >= 1, f"got {len(res)}")
    check("top result is the best FTS hit (parse_config)",
          res and res[0]["name"] == "parse_config", str(res[:1]))
    check("results still carry confidence", all("confidence" in r for r in res))

    print("\n-- sync-level degradation (BEHAVIORAL: graph built even if the model fails) --")
    # Replace the source-substring checks with a REAL exercise of sync's degradation path: stub the
    # heavy collaborators (extract/push/index) and drive only the embeddings outcome. This catches a
    # broken degradation contract a `"...skipped" in source` grep never could (mutation-resistant).
    import tempfile
    from types import SimpleNamespace

    class _SyncFakeDriver:
        def verify_connectivity(self):
            pass

        def session(self):
            return _FakeSession()

        def close(self):
            pass

    def _fresh_sync(embed_fn):
        S = _load_sync()
        S._acquire_sync_lock = lambda root: object()      # no real OS lock in the test
        S._acquire_instance_lock = lambda *a, **k: object()   # nor the cross-instance lock (release is a no-op below)
        S._release_sync_lock = lambda lock: None
        S._rebuild_code = lambda root: (
            [{"key": "a.py::f:1", "label": "Function", "name": "f",
              "source_file": "a.py", "start_line": 1, "body_summary": "x"}], [], [], "ast")
        S.push_to_neo4j = lambda driver, nodes, calls, imports, typed_edges=None: (len(nodes), len(calls))
        S.indexes = SimpleNamespace(create_all_indexes=lambda d: None,
                                    wait_for_indexes=lambda d, timeout=120: True)
        S.embeddings = SimpleNamespace(generate_embeddings=embed_fn)
        return S

    # Hermetic routing: a throwaway DEEPMAP_HOME (never this machine's registry) + an explicit NEO4J_URI
    # (the operator escape hatch), so sync's Neo4j route (ROUND-2 D1) cannot depend on which projects
    # this machine has registered; the fake driver stands in for that instance.
    _saved_env = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI")}
    os.environ["DEEPMAP_HOME"] = tempfile.mkdtemp(prefix="dm_degrade_home_")
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"

    # (A) model-unavailable (ImportError) -> DEGRADE: sync STILL returns True (graph + indexes built).
    S = _fresh_sync(_raises_import_error)
    with tempfile.TemporaryDirectory() as td:
        try:
            ok = S.sync(td, driver=_SyncFakeDriver())
            check("model ImportError -> sync returns True (graph built, search runs FTS-only)",
                  ok is True, str(ok))
        except Exception as e:  # noqa: BLE001
            check("model ImportError -> sync returns True (no raise)", False, f"{type(e).__name__}: {e}")

    # (B) a NON-model error (DB outage / real bug) must FAIL LOUD — never swallowed as "degradation".
    S = _fresh_sync(_raises_runtime_error)
    with tempfile.TemporaryDirectory() as td:
        raised = False
        try:
            S.sync(td, driver=_SyncFakeDriver())
        except RuntimeError:
            raised = True
        check("non-model RuntimeError FAILS LOUD (the except is narrow, not catch-all)", raised)

    # (C) stale-cleanup must REFUSE to wipe on a 0-node extraction (never DETACH DELETE a good graph).
    S = _fresh_sync(lambda d: 0)
    S._rebuild_code = lambda root: ([], [], [], "ast")    # 0 nodes
    S._cleanup_is_safe = lambda root: True                # pretend repo-root so only the 0-node guard remains
    wiped = {"called": False}
    S._cleanup_stale = lambda driver, keys: wiped.__setitem__("called", True)
    with tempfile.TemporaryDirectory() as td:
        S.sync(td, cleanup_stale=True, driver=_SyncFakeDriver())
        check("0-node extraction -> _cleanup_stale NOT called (refuses to wipe the graph)",
              wiped["called"] is False)
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
