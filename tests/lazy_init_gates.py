#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j concurrency gates for the long-lived servers' lazy initialization.

The MCP servers init expensive singletons on the FIRST request — the embedding model
(`embeddings._get_model`) and the Neo4j driver + retriever
(`deepmap_search_server._get_retriever`). With an unguarded `if x is None:` check, two
concurrent first-requests can BOTH pass the check and BOTH build the singleton — double-
loading the model (wasteful) or creating a SECOND Neo4j driver that is never closed (a real
fd/connection leak in a process that runs for the session). A code-audit finding, since fixed.

These gates widen the init window with a slow stub and hammer it with N threads, asserting
the singleton is built EXACTLY ONCE. RED before the `threading.Lock` + double-check fix
(stub built N times); GREEN after (built once). No Neo4j, no real model.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

N_THREADS = 8
SLOW = 0.15  # init window — wide enough that all N threads overlap deterministically

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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _hammer(fn):
    """Run fn() in N threads released as simultaneously as possible; collect exceptions."""
    barrier = threading.Barrier(N_THREADS)
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait()
            fn()
        except BaseException as exc:  # noqa: BLE001 — surface any thread error
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def gate_model_single_load() -> None:
    print("\n-- embeddings._get_model: single model load under concurrency --")
    emb = _load("embeddings")

    builds = {"n": 0}
    lock = threading.Lock()

    class FakeST:
        def __init__(self, name):
            with lock:
                builds["n"] += 1
            time.sleep(SLOW)

        def encode(self, text, **kw):
            return [0.0] * 768

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeST
    saved = sys.modules.get("sentence_transformers")
    sys.modules["sentence_transformers"] = fake
    try:
        emb._model = None
        errors = _hammer(emb._get_model)
        check("no thread errored during concurrent _get_model", not errors,
              "; ".join(repr(e) for e in errors[:2]))
        check(f"model built EXACTLY ONCE under {N_THREADS} concurrent first-calls",
              builds["n"] == 1, f"built {builds['n']}x (race -> wasteful double-load)")
        check("subsequent call returns the cached model (no rebuild)",
              (emb._get_model() is not None) and builds["n"] == 1)
    finally:
        if saved is not None:
            sys.modules["sentence_transformers"] = saved
        else:
            sys.modules.pop("sentence_transformers", None)


def gate_retriever_single_driver() -> None:
    print("\n-- deepmap_search_server._get_retriever: single driver under concurrency --")

    drivers = {"n": 0, "closed": 0}
    lock = threading.Lock()

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **kw):
            raise RuntimeError("probe -> _detect_search_clause falls back (expected)")

    class FakeDriver:
        def verify_connectivity(self):
            time.sleep(SLOW)

        def session(self, **kw):
            return FakeSession()

        def close(self):
            with lock:
                drivers["closed"] += 1

    class FakeGDB:
        @staticmethod
        def driver(*a, **kw):
            with lock:
                drivers["n"] += 1
            return FakeDriver()

    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = FakeGDB
    saved_neo4j = sys.modules.get("neo4j")
    sys.modules["neo4j"] = fake_neo4j

    # Avoid a real model load: stub the embeddings module the server imports.
    fake_emb = types.ModuleType("embeddings")
    fake_emb.embed_query = lambda text: [0.0] * 768
    saved_emb = sys.modules.get("embeddings")
    sys.modules["embeddings"] = fake_emb

    saved_search = sys.modules.get("mcp")  # presence required to import the server
    try:
        try:
            srv = _load("deepmap_search_server")
        except Exception as exc:  # noqa: BLE001 — mcp SDK absent -> can't run this gate
            check("deepmap_search_server importable (mcp SDK present)", False, repr(exc))
            return
        srv._retriever = None
        errors = _hammer(srv._get_retriever)
        check("no thread errored during concurrent _get_retriever", not errors,
              "; ".join(repr(e) for e in errors[:2]))
        check(f"Neo4j driver created EXACTLY ONCE under {N_THREADS} concurrent first-calls",
              drivers["n"] == 1, f"created {drivers['n']}x (race -> leaked driver/connection)")
        check("the retriever singleton is set", srv._retriever is not None)
    finally:
        for name, saved in (("neo4j", saved_neo4j), ("embeddings", saved_emb), ("mcp", saved_search)):
            if saved is not None:
                sys.modules[name] = saved
            else:
                sys.modules.pop(name, None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — lazy-init concurrency gates (no Neo4j)")
    print("=" * 64)

    gate_model_single_load()
    gate_retriever_single_driver()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
