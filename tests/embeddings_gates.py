#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for build/embeddings pure functions (compose_text + content_hash).

These are load-bearing: compose_text decides WHAT gets embedded (intent summary, not raw code,
DD-3), and content_hash (model-salted) decides WHEN a node is re-embedded. Neither touches the
model, so they're unit-testable offline.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load():
    spec = importlib.util.spec_from_file_location("dm_embeddings", BUILD / "embeddings.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — embeddings pure-function gates (no model, no Neo4j)")
    print("=" * 64)

    E = _load()

    print("\n-- compose_text (what gets embedded) --")
    t = E.compose_text({"name": "charge", "body_summary": "charge the user"})
    check("uses the document prefix", t.startswith(E.DOC_PREFIX), t)
    check("includes name + summary", "charge" in t and "charge the user" in t, t)

    check("prefers body_summary over docstring/summary",
          "B" in E.compose_text({"name": "f", "body_summary": "B", "docstring": "D", "summary": "S"})
          and "D" not in E.compose_text({"name": "f", "body_summary": "B", "docstring": "D"}),
          "fallback order wrong")
    check("falls back to docstring when no body_summary",
          "D" in E.compose_text({"name": "f", "docstring": "D"}))
    check("falls back to summary when neither",
          "S" in E.compose_text({"name": "f", "summary": "S"}))
    check("appends rationale clause",
          "because" in E.compose_text({"name": "f", "body_summary": "x", "rationale": "because reasons"}))
    # empty summary -> no dangling ": "
    empty = E.compose_text({"name": "f"})
    check("no dangling ': ' when summary empty", not empty.rstrip().endswith(":"), repr(empty))

    print("\n-- content_hash (when to re-embed) --")
    h = E.content_hash("hello")
    check("16 hex chars", len(h) == 16 and all(c in "0123456789abcdef" for c in h), h)
    check("deterministic", E.content_hash("hello") == E.content_hash("hello"))
    check("different text -> different hash", E.content_hash("a") != E.content_hash("b"))
    check("model-salted (model swap invalidates)",
          E.content_hash("x", "model-A") != E.content_hash("x", "model-B"))
    check("default model salt is MODEL_NAME",
          E.content_hash("x") == E.content_hash("x", E.MODEL_NAME))

    print("\n-- contract constants --")
    check("EMBED_DIM == 768", E.EMBED_DIM == 768)
    check("asymmetric prefixes differ", E.DOC_PREFIX != E.QUERY_PREFIX)
    check("query prefix is search_query:", E.QUERY_PREFIX.strip() == "search_query:")

    print("\n-- generate_embeddings: ONE vectorized encode per chunk, not per node (#10) --")
    calls = {"encode": 0, "texts": 0}

    class _FakeModel:
        def encode(self, texts, show_progress_bar=False, **k):
            calls["encode"] += 1
            if isinstance(texts, str):           # the per-item path (must NOT be used here)
                calls["texts"] += 1
                return [0.0] * E.EMBED_DIM
            calls["texts"] += len(texts)         # the batched path
            return [[float(i)] * E.EMBED_DIM for i in range(len(texts))]

    E._get_model = lambda: _FakeModel()
    rows = [{"id": f"e{i}", "name": f"f{i}", "body_summary": f"does thing {i}",
             "docstring": None, "summary": None, "rationale": None, "embed_hash": None}
            for i in range(5)]
    set_emb = []

    class _Sess:
        def __init__(self, rows):
            self._rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **p):
            if "RETURN elementId" in cypher:
                return list(self._rows)
            if "SET n.embedding" in cypher:
                set_emb.append((p.get("id"), p.get("emb")))
            return []

    class _Drv:
        def __init__(self):
            self._first = True

        def session(self):
            first, self._first = self._first, False
            return _Sess(rows if first else [])

    n = E.generate_embeddings(_Drv(), batch_size=100, force=True)
    check("all 5 nodes embedded", n == 5, str(n))
    check("encode called ONCE for the 5-node chunk (batched, not 5x) (#10)", calls["encode"] == 1, str(calls["encode"]))
    check("every node received an embedding SET", len(set_emb) == 5, str(len(set_emb)))
    check("embeddings are 768-d", all(len(e) == E.EMBED_DIM for _id, e in set_emb))

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
