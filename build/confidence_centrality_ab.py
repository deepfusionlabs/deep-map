#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 / D3 — the OFFLINE confidence-weighted-centrality EXPERIMENT (zero-egress).

The live graph-weighted ranking arm weights every `:CALLS` in-edge EQUALLY (`count(caller)`), so a
node's centrality boost is driven as much by AMBIGUOUS name-fanout edges as by firm ones (the Rails
field-review P0-2: 82.9% of CALLS are AMBIGUOUS, inflating centrality). This module produces the
MEASURED CASE for the post-benchmark decision to weight in-degree by the `confidence` the graph
ALREADY stores — entirely offline. A confidence-weighted-centrality variant of the graph arm is
recomputed against a live graph through an INJECTED query function, substituted for the logged graph
arm, re-fused with the REAL `_weighted_rrf`, and scored against the log's graded qrels.

`build/retrieval.py` is UNTOUCHED — its `VECTOR_INDEXES` / fusion are IMPORTED, never re-implemented,
the frozen `_arm_graph_weighted` Cypher is not edited, and nothing here is wired into live search:
results persist only under instance.json `experiments.confidence_centrality` with `applied` FORCED
false. THE PARITY KEYSTONE: gamma=(1,1,1) reduces the weighted sum EXACTLY to `count(caller)` — the
frozen arm — so a null experiment can never accidentally move ranking.

Weighting (bound params, never interpolated): EXTRACTED=$gExt, INFERRED=$gInf, AMBIGUOUS=$gAmb; a node
with no callers contributes 0 (the `r IS NULL` guard), matching `count`. The reviewer's candidate is
(1.0, 0.5, 0.15). Honesty statistics mirror graph_signal_ab: replay_fidelity (a rebuilt graph shows up
as drift) and qrel_coverage (the log-bias AGAINST the variant). Neo4j is needed at RUN time only.

Operator driver (offline, Neo4j up):
    import confidence_centrality_ab as cc, retrieval
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=(user, pw))
    print(cc.replay_and_record(root, query_fn=cc.neo4j_query_fn(drv), embed_fn=my_embed_fn,
                               gamma=cc.DEFAULT_GAMMA))
"""
from __future__ import annotations

import sys
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
from retrieval import VECTOR_INDEXES   # noqa: E402  (frozen, import-only)
import retrieval_log as _rl   # noqa: E402
import tune_weights as _tw    # noqa: E402
import instance_state as _is  # noqa: E402

ARM_NAMES = tuple(_rl.ARM_NAMES)
EQUAL_WEIGHTS = {a: 1.0 for a in ARM_NAMES}
PARITY_GAMMA = (1.0, 1.0, 1.0)                 # weighted sum == count(caller): the null / parity point
DEFAULT_GAMMA = (1.0, 0.5, 0.15)               # the field-review candidate (down-weight AMBIGUOUS)
DEFAULT_MIN_LABELED = _tw.DEFAULT_MIN_LABELED_BUCKET
FIDELITY_DRIFT_THRESHOLD = 0.8

NOTE = ("log-replay A/B — variant candidates the log never saw score 0 by construction (a bias "
        "AGAINST the variant: a win is conservative, a loss is NOT conclusive). The measured case "
        "for the benchmark-gated decision to weight :CALLS in-degree by confidence; the LIVE "
        "graph arm stays count(caller) (retrieval.py _arm_graph_weighted untouched). applied stays false.")


def _norm_gamma(gamma):
    """(gExt, gInf, gAmb) floats. None -> PARITY (1,1,1). A dict or a 3-seq is accepted; bad -> parity."""
    try:
        if gamma is None:
            return PARITY_GAMMA
        if isinstance(gamma, dict):
            return (float(gamma.get("EXTRACTED", 1.0)), float(gamma.get("INFERRED", 1.0)),
                    float(gamma.get("AMBIGUOUS", 1.0)))
        g = tuple(float(x) for x in gamma)
        return g if len(g) == 3 else PARITY_GAMMA
    except Exception:
        return PARITY_GAMMA


def weighted_arm_cypher(gamma=None) -> str:
    """The confidence-weighted centrality Cypher (procedure path — offline replay needs no SEARCH
    clause). gamma=None emits the FROZEN count(caller) baseline byte-for-byte; otherwise the weighted
    variant, with the per-tier weights ALWAYS bound as $gExt/$gInf/$gAmb (never interpolated). A node
    with no callers contributes 0 (`r IS NULL` guard) so gamma=(1,1,1) == count exactly."""
    if gamma is None:
        return """
            CALL db.index.vector.queryNodes($index_name, $k, $vec)
            YIELD node, score AS rawScore
            OPTIONAL MATCH (node)<-[:CALLS]-(caller)
            WITH node, rawScore, count(caller) AS inDegree
            RETURN elementId(node) AS id,
                   rawScore * (1.0 + log(1.0 + toFloat(inDegree))) AS score
            ORDER BY score DESC LIMIT $k
        """
    return """
        CALL db.index.vector.queryNodes($index_name, $k, $vec)
        YIELD node, score AS rawScore
        OPTIONAL MATCH (node)<-[r:CALLS]-(caller)
        WITH node, rawScore, sum(CASE WHEN r IS NULL THEN 0.0
                WHEN coalesce(r.confidence, 'INFERRED') = 'EXTRACTED' THEN $gExt
                WHEN coalesce(r.confidence, 'INFERRED') = 'INFERRED' THEN $gInf
                ELSE $gAmb END) AS inDegree
        RETURN elementId(node) AS id,
               rawScore * (1.0 + log(1.0 + toFloat(inDegree))) AS score
        ORDER BY score DESC LIMIT $k
    """


def _clean_rows(rows) -> list:
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("id") is None:
            continue
        s = r.get("score")
        if not isinstance(s, (int, float)) or s != s:   # non-numeric / NaN
            continue
        out.append({"id": r["id"], "score": float(s)})
    return out


def weighted_graph_arm(query_fn, qvec, k, *, gamma=None) -> list:
    """The confidence-weighted graph arm, pooled across the 5 vector indexes EXACTLY as the frozen
    _arm_graph_weighted pools (a failing index is skipped — FD-1 isolation — the rest still answer).
    gamma=None gives the count(caller) baseline. Returns cleaned {id, score} rows, score-desc, top-k.
    Never raises."""
    cypher = weighted_arm_cypher(gamma)
    pooled = []
    for _label, index_name in VECTOR_INDEXES:
        try:
            params = {"index_name": index_name, "k": k, "vec": qvec}
            if gamma is not None:
                g = _norm_gamma(gamma)
                params.update(gExt=g[0], gInf=g[1], gAmb=g[2])
            pooled += _clean_rows(query_fn(cypher, params))
        except Exception:
            continue
    pooled.sort(key=lambda r: r["score"], reverse=True)
    return pooled[:k]


def _usable_query(rec_query):
    if isinstance(rec_query, str) and rec_query and not rec_query.startswith("redacted:"):
        return rec_query
    return None


def _topk_overlap(logged_ids, recomputed_ids, k: int) -> float:
    top_logged = set(logged_ids[:k])
    if not top_logged:
        return 1.0
    return len(top_logged & set(recomputed_ids[:k])) / len(top_logged)


def replay(records, *, query_fn, embed_fn, gamma=DEFAULT_GAMMA, top_k: int = 15,
           k: int = 10, min_labeled: int = DEFAULT_MIN_LABELED,
           bootstrap_iters: int = _tw.DEFAULT_BOOTSTRAP_ITERS, alpha: float = _tw.DEFAULT_ALPHA,
           eps: float = _tw.DEFAULT_EPS) -> dict:
    """Replay the log's labeled queries with the CONFIDENCE-WEIGHTED graph arm SUBSTITUTED for the
    logged one (vector/bm25 untouched), re-fuse under EQUAL weights with the real fusion, and score
    paired nDCG deltas -> seeded bootstrap. The baseline is the count(caller) arm (gamma=None), so this
    isolates the confidence weighting. Deterministic; never raises. Verdicts: insufficient_data |
    backend_unavailable | no_improvement | not_significant | beats_baseline_significant."""
    g = _norm_gamma(gamma)
    out = {"experiment": "confidence_centrality", "verdict": "insufficient_data", "n_labeled": 0,
           "n_replayed": 0, "n_skipped_redacted": 0, "n_failed": 0, "min_labeled": min_labeled,
           "gamma": {"EXTRACTED": g[0], "INFERRED": g[1], "AMBIGUOUS": g[2]}, "top_k": top_k,
           "ndcg_k": k, "mean_delta": None, "bootstrap": None, "replay_fidelity": None,
           "graph_drift": None, "qrel_coverage": None, "note": NOTE, "applied": False,
           "generated_utc": _tw._now_iso()}
    try:
        labeled = _tw.build_qrels(records or [])
        out["n_labeled"] = len(labeled)
        qtext = {}
        for r in records or []:
            if isinstance(r, dict) and r.get("type") == "search" and r.get("qid") is not None:
                qtext[r["qid"]] = r.get("query")
        usable = []
        for q in labeled:
            text = _usable_query(qtext.get(q.get("qid")))
            if text is None:
                out["n_skipped_redacted"] += 1
                continue
            usable.append((q, text))
        if not usable or len(usable) < min_labeled:
            return out
        if query_fn is None:
            out["verdict"] = "backend_unavailable"
            return out
        # The per-arm depth live search() fetches (max(top_k*3, DEEPMAP_CANDIDATE_FLOOR)) — the SAME rule
        # the log's per-arm pools were captured with (retrieval_log.fetch_k_for).
        fetch_k = _rl.fetch_k_for(top_k)
        deltas, fidelities, cov_hits, cov_total = [], [], 0, 0
        replayed_qids = []
        for q, text in usable:
            try:
                qvec = embed_fn(text)
            except Exception:
                out["n_failed"] += 1
                continue
            variant_rows = weighted_graph_arm(query_fn, qvec, fetch_k, gamma=g)
            base_rows = weighted_graph_arm(query_fn, qvec, fetch_k, gamma=None)   # count(caller)
            if not variant_rows and not base_rows:
                out["n_failed"] += 1
                continue
            variant_ids = _rl._rank_ids(variant_rows)
            logged_graph = q["per_arm"].get("graph_weighted") or []
            if logged_graph:
                fidelities.append(_topk_overlap(logged_graph, _rl._rank_ids(base_rows), k))
            logged_ids = set()
            for a in ARM_NAMES:
                logged_ids.update(q["per_arm"].get(a) or [])
            logged_ids.update((q.get("graded") or {}).keys())
            cov_total += len(variant_ids)
            cov_hits += sum(1 for rid in variant_ids if rid in logged_ids)
            graded = q.get("graded") or {p: 1.0 for p in q.get("positives", ())}
            base = _tw._ndcg_one(_tw._fuse(q["per_arm"], EQUAL_WEIGHTS), graded, k)
            per_arm_variant = dict(q["per_arm"])
            per_arm_variant["graph_weighted"] = variant_ids
            var = _tw._ndcg_one(_tw._fuse(per_arm_variant, EQUAL_WEIGHTS), graded, k)
            deltas.append(var - base)
            replayed_qids.append(q.get("qid"))
        out["n_replayed"] = len(deltas)
        if not deltas:
            out["verdict"] = "backend_unavailable" if out["n_failed"] else "insufficient_data"
            return out
        ci = _tw.bootstrap_ci(deltas, iters=bootstrap_iters,
                              seed=_tw._seed_from_qids(replayed_qids), alpha=alpha)
        out["mean_delta"] = ci["mean"]
        out["bootstrap"] = {"ci_lo": ci["lo"], "ci_hi": ci["hi"], "mean": ci["mean"],
                            "iters": ci["iters"], "alpha": ci["alpha"]}
        if fidelities:
            out["replay_fidelity"] = sum(fidelities) / len(fidelities)
            out["graph_drift"] = out["replay_fidelity"] < FIDELITY_DRIFT_THRESHOLD
        out["qrel_coverage"] = (cov_hits / cov_total) if cov_total else 0.0
        if ci["mean"] <= eps:
            out["verdict"] = "no_improvement"
        elif ci["lo"] <= 0.0:
            out["verdict"] = "not_significant"
        else:
            out["verdict"] = "beats_baseline_significant"
        return out
    except Exception:
        return out


def replay_and_record(root, *, records=None, query_fn=None, embed_fn=None, **kw) -> dict:
    """replay + persist under instance.json experiments.confidence_centrality (applied FORCED false by
    instance_state.write_experiment). Returns the result + {written}. Never raises."""
    try:
        if records is None:
            records = _rl.read_records(root) if root is not None else []
        res = replay(records, query_fn=query_fn, embed_fn=embed_fn, **kw)
        path = _is.write_experiment(root, "confidence_centrality", res)
        res = dict(res)
        res["written"] = path is not None
        return res
    except Exception:
        return {"experiment": "confidence_centrality", "verdict": "insufficient_data",
                "written": False, "applied": False}


def neo4j_query_fn(driver):
    """The RUN-time adapter: wrap a live neo4j driver as the injected query function. The only place a
    driver is touched; the module itself imports clean without one."""
    def fn(cypher, params):
        with driver.session() as s:
            return [dict(r) for r in s.run(cypher, **(params or {}))]
    return fn
