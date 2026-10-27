#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j coherence gates across indexes.py / retrieval.py / embeddings.py.

Search breaks SILENTLY if the index names/labels/dims drift between the module that CREATES the
indexes (indexes.py), the module that SEARCHES them (retrieval.py), and the module that EMBEDS
the nodes (embeddings.py). This gate fails loud on any drift — the cross-module analog of the
.mcp.json<->code URL guard.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
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
    print("DeepMap — index/retrieval/embeddings coherence gates (no Neo4j)")
    print("=" * 64)

    IDX = _load("dm_indexes", "indexes.py")
    RET = _load("dm_retrieval", "retrieval.py")
    EMB = _load("dm_embeddings", "embeddings.py")

    created = dict(IDX.VECTOR_INDEXES)          # label -> index name (6, incl. Community)
    searched = dict(RET.VECTOR_INDEXES)         # label -> index name (5, Community excluded)

    print("\n-- dimensions --")
    check("indexes.VECTOR_DIM == embeddings.EMBED_DIM == 768",
          IDX.VECTOR_DIM == EMB.EMBED_DIM == 768, f"{IDX.VECTOR_DIM}/{EMB.EMBED_DIM}")

    print("\n-- searched indexes all exist in the created set --")
    for label, name in RET.VECTOR_INDEXES:
        check(f"searched {label} index {name} is created by indexes.py",
              created.get(label) == name, f"created={created.get(label)!r}")

    print("\n-- created-but-not-searched is exactly Community --")
    extra = set(created) - set(searched)
    check("only Community is created-but-not-searched", extra == {"Community"}, str(extra))

    print("\n-- embeddings labels match the vector-index labels --")
    check("EMBEDDABLE_LABELS == vector-index labels (set)",
          set(EMB.EMBEDDABLE_LABELS) == set(created), f"{set(EMB.EMBEDDABLE_LABELS) ^ set(created)}")

    print("\n-- FTS coherence --")
    fts_names = {name for name, _, _ in IDX.FTS_INDEXES}
    ret_src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("retrieval uses 'deepmap_code_fts'", "deepmap_code_fts" in ret_src)
    # functional usage, not just the name in a comment (a commented-out FTS query would still grep)
    check("retrieval actually CALLs the FTS procedure",
          "CALL db.index.fulltext.queryNodes" in ret_src)
    check("'deepmap_code_fts' is defined in indexes.FTS_INDEXES", "deepmap_code_fts" in fts_names)
    code_fts = next((labels for n, labels, _ in IDX.FTS_INDEXES if n == "deepmap_code_fts"), "")
    code_labels = set(code_fts.split("|"))
    check("code FTS covers every searched vector label",
          set(searched) <= code_labels, f"missing {set(searched) - code_labels}")

    print("\n-- arm-weight keys are coherent --")
    check("DEFAULT_ARM_WEIGHTS has the 3 arms",
          set(RET.DEFAULT_ARM_WEIGHTS) == {"vector", "bm25", "graph_weighted"},
          str(set(RET.DEFAULT_ARM_WEIGHTS)))

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
