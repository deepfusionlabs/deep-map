#!/usr/bin/env python3
"""STAGE-0 T2: run deepmap_search end-to-end and FREEZE the regression oracle.

Touches NO product code. Imports the retriever module named by RETRIEVER_MODULE
(A/B parametric: Scenario A = this repo's build/retrieval.py; Scenario B = the
deployed file located by T0) and EXERCISES it only.

Contract encoded here (do not weaken):
  * The no-regression gate is TOP-K ORDERING ONLY of STABLE keys (relpath::name:line,
    stage0_real_graph.py:90), never byte-equal scores (plan 0.3; test-strategy:20) and
    never graph-instance elementIds (which die on re-sync — T0/T4). 'score' AND the raw
    elementId are stored in a PROVENANCE section only and never compared downstream.
    The CI diff targets queries[*].order, NOT the file.
  * 'score' means different things per path: a raw arm score on the single-arm
    bypass (retrieval.py:128-133) vs an RRF score on the multi-arm path
    (retrieval.py:136-139). Not comparable. Ordering is the contract.
  * Arms 1 (vector) and 3 (graph_weighted) read the SAME per-label vector indexes,
    so a node earns two rank contributions (RRF double-contribution; FD-6.3). We
    CAPTURE the ARM1/ARM3 overlap count; we do NOT fix it.
  * VECTOR_INDEXES = 5 SEARCHED labels (Community excluded, retrieval.py:60); the
    schema CREATES 6. We record + ASSERT the 5 searched per-arm counts.
  * Determinism: none of the score sorts in retrieval.py carry a secondary tie-break
    key (lines 48,131,137,156,212,264) -> equal-score order is undefined. We run each
    query TWICE and assert byte-identical ORDERING before freezing. A failure here is
    a T4 fix (deterministic tie-break, e.g. elementId).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from collections import OrderedDict

# ----------------------------------------------------------------------------
# CONFIG — the only knobs. Set RETRIEVER_MODULE per T0 (A/B parametric).
# ----------------------------------------------------------------------------
# Scenario A (staged draft, this repo): import path to build/retrieval.py.
# Scenario B (deployed baseline located by T0): the deployed module's import path.
# NO DEFAULT — a default would silently pick Scenario A, defeating T0 (plan 0.2).
RETRIEVER_MODULE = os.environ.get("RETRIEVER_MODULE")     # <-- set from T0; do NOT default
RETRIEVER_SRC_PATH = os.environ.get("RETRIEVER_SRC_PATH") # optional: dir to prepend to sys.path

# P6: allocated per-project Bolt port — read from the T1 registry; do NOT assume 7687.
NEO4J_URI = os.environ.get("NEO4J_URI")
NEO4J_AUTH = ("neo4j", "deepmap")       # LOCKED constant (spec 4.5); FD-1.6 verifies, never re-decides
TOP_K = 15                              # matches deepmap_search default (design.md:51)

ORACLE_OUT = "stage0_oracle.json"

# The FROZEN QUERY SET — commit these. Adapt the symbol tokens (Q3) to the pinned
# T1 fixture, then never change them without re-freezing the oracle.
FROZEN_QUERIES = OrderedDict([
    ("Q1_concept_to_impl",   "validate the incoming request payload before processing"),
    ("Q2_non_function",      "how are database connections opened and pooled"),
    ("Q3_bm25_keyword",      "parse_config retry_backoff"),
    ("Q4_graph_hub",         "the main entry point that ties the request lifecycle together"),
    ("Q5_degraded_arm",      "xy"),  # <=2-char tokens -> Arm 2 (FTS) returns [] (retrieval.py:189); 2 arms survive
])

# ----------------------------------------------------------------------------
# Embedding function — embed_fn: str -> list[float] (768).  (design.md:20)
# Use the SAME embedder the product uses (nomic-ai/modernbert-embed-base, the
# asymmetric "search_query: " prefix, embed_query — design.md:37). It is as
# A/B-parametric as the retriever; wire it to the deployed embed path. The stub
# below FAILS LOUDLY so no oracle is ever frozen on random vectors.
# ----------------------------------------------------------------------------
def embed_fn(text: str) -> list[float]:
    raise NotImplementedError(
        "Wire embed_fn to the deployed embedder (embed_query / the 'search_query: ' "
        "asymmetric path, design.md:37). Do NOT freeze an oracle on a placeholder vector."
    )


# ----------------------------------------------------------------------------
def _require_config():
    if not RETRIEVER_MODULE:
        sys.exit("FATAL: set RETRIEVER_MODULE from T0 (A/B) — do not default (plan 0.2).")
    if not NEO4J_URI:
        sys.exit("FATAL: set NEO4J_URI from the T1 instance registry — per-project allocated port (P6).")


def _preflight_embed_fn():
    v1 = embed_fn("probe"); v2 = embed_fn("probe")
    assert isinstance(v1, list) and len(v1) == 768, "embed_fn must return list[float] len 768 (design.md:20)"
    assert v1 == v2, "embed_fn is non-pure -> fix the embedder; do NOT blame retrieval.py sort ties."


def _load_retriever_class():
    if RETRIEVER_SRC_PATH and RETRIEVER_SRC_PATH not in sys.path:
        sys.path.insert(0, RETRIEVER_SRC_PATH)
    mod = importlib.import_module(RETRIEVER_MODULE)
    return mod, getattr(mod, "DeepMapRetriever")


def stable_key(r: dict) -> str:
    """Re-sync-stable node identity: 'relpath::name:line' (stage0_real_graph.py:90).
    Survives a re-sync; elementId (`4:<uuid>:N`) does not. Path separator normalized to
    '/' for OS-portability. Identical to the freeze / gate helpers
    (stage0_freeze_oracle.py, stage0_regression_check.py)."""
    f = str(r.get("file") or "").replace("\\", "/")
    return f"{f}::{r.get('name')}:{r.get('line')}"


def _order(results: list[dict]) -> list[str]:
    """The CONTRACT key: ordered list of STABLE keys (relpath::name:line). NOT elementIds
    (re-sync-fragile) and NOT score. The raw elementId is kept in provenance only."""
    return [stable_key(r) for r in results]


def _normalize_row(r: dict) -> "OrderedDict":
    """Canonical per-row record for the PROVENANCE section. 'score' kept here ONLY
    (never compared)."""
    return OrderedDict([
        ("id",      r.get("id")),
        ("name",    r.get("name")),
        ("type",    r.get("type")),
        ("file",    r.get("file")),
        ("line",    r.get("line")),
        ("summary", r.get("summary")),
        ("score",   r.get("score")),   # provenance only; NEVER part of the diff
    ])


def _arm_meta_from(vec: list, fts: list, graph: list) -> "OrderedDict":
    """Per-arm counts + ARM1/ARM3 overlap, derived from the SAME arm execution
    that produced the frozen order (see search_once). Counts are POST-fetch_k pool
    sizes (<= top_k*3 = 45), not the full candidate universe."""
    vec_ids   = {r["id"] for r in vec}
    graph_ids = {r["id"] for r in graph}
    return OrderedDict([
        ("vector_pool_size_le45",         len(vec)),
        ("bm25_pool_size_le45",           len(fts)),
        ("graph_weighted_pool_size_le45", len(graph)),
        ("arm1_arm3_overlap",             len(vec_ids & graph_ids)),  # FD-6.3 double-contribution
        ("surviving_arms",                sorted(
            name for name, res in
            (("vector", vec), ("bm25", fts), ("graph_weighted", graph)) if res)),
    ])


def search_once(retriever, query: str, top_k: int):
    """Mirror search() (retrieval.py:100-105) inline so 'order' and 'arm_meta' come
    from ONE execution (no third re-issue, no determinism hazard). fetch_k == top_k*3
    (retrieval.py:101)."""
    fetch_k = top_k * 3
    qvec  = retriever.embed_fn(query)
    vec   = retriever._arm_vector(qvec, fetch_k)          # ARM 1 (retrieval.py:143)
    fts   = retriever._arm_fulltext(query, fetch_k)       # ARM 2 (retrieval.py:186)
    graph = retriever._arm_graph_weighted(qvec, fetch_k)  # ARM 3 (retrieval.py:203)
    results = retriever.search(query, top_k=top_k)        # the FROZEN public path
    return results, _arm_meta_from(vec, fts, graph)


def main() -> int:
    _require_config()
    _preflight_embed_fn()

    from neo4j import GraphDatabase

    mod, DeepMapRetriever = _load_retriever_class()
    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    # Two-arg construction MUST keep working (backward compat; design.md:20,
    # retrieval.py:73 arm_weights optional). Default weights == equal == plain RRF.
    retriever = DeepMapRetriever(driver, embed_fn)

    rrf_k = getattr(mod, "RRF_K", None)                       # expect 60 (retrieval.py:29)
    rrf_weights = dict(getattr(retriever, "arm_weights", {})) # expect all 1.0 (retrieval.py:65-69)
    searched_indexes = list(getattr(mod, "VECTOR_INDEXES", []))  # 5 searched (retrieval.py:54-61)
    use_search_clause = getattr(retriever, "_use_search_clause", None)  # SEARCH vs procedure (retrieval.py:79)

    # ASSERT the 5-searched invariant — under Scenario B a missing/6-label list must not
    # silently freeze a hollow oracle (R4).
    if len(searched_indexes) != 5:
        sys.stderr.write(
            f"WARNING: VECTOR_INDEXES has {len(searched_indexes)} entries; expected 5 "
            f"(Function/Method/Class/Module/Route, Community excluded, retrieval.py:60). "
            f"Confirm the canonical RETRIEVER_MODULE before trusting this oracle.\n")

    oracle_queries = OrderedDict()
    determinism_failures = []

    for qid, query in FROZEN_QUERIES.items():
        # --- DETERMINISM GATE: run TWICE, assert byte-identical ORDERING ---------
        run_a = retriever.search(query, top_k=TOP_K)
        run_b = retriever.search(query, top_k=TOP_K)
        if _order(run_a) != _order(run_b):
            determinism_failures.append(OrderedDict([
                ("query_id", qid),
                ("query", query),
                ("order_run_a", _order(run_a)),
                ("order_run_b", _order(run_b)),
            ]))
            continue  # do not freeze a non-deterministic row

        # order + arm_meta from ONE execution (search_once), so they cannot disagree
        results, arm_meta = search_once(retriever, query, TOP_K)
        # belt-and-braces: the frozen order must equal the determinism-checked order
        if _order(results) != _order(run_a):
            determinism_failures.append(OrderedDict([
                ("query_id", qid), ("query", query),
                ("order_capture", _order(results)), ("order_gate", _order(run_a)),
            ]))
            continue

        oracle_queries[qid] = OrderedDict([
            ("query", query),
            ("order", _order(results)),      # THE CONTRACT: ordering only. CI diffs THIS.
            ("arm_meta", arm_meta),
            ("provenance_results",           # full rows incl. score; NEVER compared
                [_normalize_row(r) for r in results]),
        ])

    driver.close()

    if determinism_failures:
        sys.stderr.write(
            "\n*** DETERMINISM GATE FAILED — ORACLE NOT FROZEN ***\n"
            "Re-running the same query produced a different top-K ORDERING.\n"
            "Root cause: none of the score sorts in retrieval.py carry a secondary\n"
            "tie-break key (lines 48, 131, 137, 156, 212, 264) -> ties are undefined.\n"
            "FIX is a T4 item: add ONE consistent secondary key (e.g. elementId) across\n"
            "all six sorts, re-run T2, THEN freeze. (implementation-plan.md:442-449)\n\n"
            + json.dumps(determinism_failures, indent=2) + "\n"
        )
        return 2

    # --- CANONICAL, SORTED-KEY, TIMESTAMP-FREE oracle ----------------------------
    oracle = OrderedDict([
        ("_meta", OrderedDict([
            ("retriever_module", RETRIEVER_MODULE),               # A/B identity (plan 0.2)
            ("retriever_file", getattr(mod, "__file__", None)),
            ("use_search_clause", use_search_clause),             # SEARCH vs procedure (retrieval.py:79)
            ("rrf_k", rrf_k),                                     # expect 60 (retrieval.py:29)
            ("rrf_weights", OrderedDict(sorted(rrf_weights.items()))),   # expect equal
            ("searched_vector_indexes", [list(t) for t in searched_indexes]),  # 5; Community excluded
            ("top_k", TOP_K),
            ("key_format", "relpath::name:line"),                # stable; NOT elementId
            ("neo4j_uri", NEO4J_URI),
            ("auth_user", NEO4J_AUTH[0]),                         # locked; password not stored
            ("contract", "TOP-K ORDERING of stable relpath::name:line keys; the diff targets queries[*].order; 'score'+elementId are provenance, never compared"),
        ])),
        ("queries", oracle_queries),
    ])

    with open(ORACLE_OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(oracle, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    sys.stderr.write(
        f"OK: oracle frozen -> {ORACLE_OUT} "
        f"({len(oracle_queries)}/{len(FROZEN_QUERIES)} queries; "
        f"rrf_k={rrf_k}; weights={rrf_weights}; "
        f"searched_indexes={len(searched_indexes)}; use_search_clause={use_search_clause})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
