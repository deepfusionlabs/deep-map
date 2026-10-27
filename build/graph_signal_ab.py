#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 / D2 — the OFFLINE framework-edge-aware graph-signal EXPERIMENT (zero-egress).

The live graph-weighted ranking arm keys on :CALLS in-degree ONLY — folding the v0.17.0
framework/runtime edges (ROUTES_TO/DEPENDS_ON/TRIGGERS/...) into ranking is benchmark-gated
(retrieval.py:174-178). This module produces the MEASURED CASE for that post-G3 decision,
entirely offline: an extended-in-degree variant of the graph arm is recomputed against a live
graph through an INJECTED query function, substituted for the logged graph arm, re-fused with
the REAL `_weighted_rrf`, and scored against the log's graded qrels. `build/retrieval.py` is
untouched — its `_sanitize_rel_types` / `VECTOR_INDEXES` / fusion are IMPORTED, never
re-implemented — and nothing here is wired into live search: results persist only under
instance.json `experiments.graph_signal` with `applied` FORCED false.

Two Cypher variants, both mirroring the frozen arm's procedure path
(db.index.vector.queryNodes -> in-degree -> score x (1 + log(1 + inDegree))):
  * all-equal: one count over `<-[:CALLS|ROUTES_TO|...]-` (the sanitized pattern),
  * typed-discount: `callsIn + $lam x typedIn` inside the log term — lambda is ALWAYS a bound
    parameter, and lambda=0 reduces EXACTLY to CALLS-only (the parity keystone).

Honesty statistics, always recorded:
  * replay_fidelity — the recomputed CALLS-only ordering vs the LOGGED ordering (top-k overlap):
    a graph rebuilt since logging SHOWS UP as drift instead of silently contaminating the number,
  * qrel_coverage — variant candidates the log never saw score 0 by construction, a bias
    AGAINST the variant: a win here is conservative; a loss is NOT conclusive.
Neo4j is needed at RUN time only (pass `neo4j_query_fn(driver)`); the gates inject fakes.
Insufficient data / an unreachable backend are first-class verdicts, never fabricated numbers.

Operator driver (offline, Neo4j up):
    import graph_signal_ab, retrieval
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=(user, pw))
    print(graph_signal_ab.replay_and_record(root, query_fn=graph_signal_ab.neo4j_query_fn(drv),
                                            embed_fn=my_embed_fn))
"""
from __future__ import annotations

import sys
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
from retrieval import (_sanitize_rel_types, VECTOR_INDEXES,  # noqa: E402  (frozen, import-only)
                       DEFAULT_IMPACT_EDGE_TYPES)
import retrieval_log as _rl   # noqa: E402
import tune_weights as _tw    # noqa: E402
import instance_state as _is  # noqa: E402

ARM_NAMES = tuple(_rl.ARM_NAMES)
EQUAL_WEIGHTS = {a: 1.0 for a in ARM_NAMES}
DEFAULT_LAMBDA_GRID = (0.0, 0.25, 0.5, 1.0)
DEFAULT_MIN_LABELED = _tw.DEFAULT_MIN_LABELED_BUCKET   # 40 — per-slice-grade evidence floor
FIDELITY_DRIFT_THRESHOLD = 0.8

NOTE = ("log-replay A/B — variant candidates the log never saw score 0 by construction (a bias "
        "AGAINST the variant: a win is conservative, a loss is NOT conclusive). The measured "
        "case for a later, benchmark-gated ranking-upgrade decision; the LIVE graph arm stays :CALLS-only "
        "(benchmark-gated — retrieval.py:174-178). applied stays false.")


def extended_arm_cypher(edge_types, lam=None) -> str:
    """The extended-in-degree Cypher (procedure path — offline replay needs no SEARCH clause).
    Types pass through the FROZEN retrieval._sanitize_rel_types injection guard (label-position
    interpolation); `lam` is ALWAYS the bound $lam parameter, never interpolated."""
    rels = _sanitize_rel_types(edge_types if edge_types is not None else DEFAULT_IMPACT_EDGE_TYPES)
    typed = [t for t in rels if t != "CALLS"]
    if lam is None or not typed:
        pattern = "|".join(rels)
        return f"""
            CALL db.index.vector.queryNodes($index_name, $k, $vec)
            YIELD node, score AS rawScore
            OPTIONAL MATCH (node)<-[:{pattern}]-(caller)
            WITH node, rawScore, count(caller) AS inDegree
            RETURN elementId(node) AS id,
                   rawScore * (1.0 + log(1.0 + toFloat(inDegree))) AS score
            ORDER BY score DESC LIMIT $k
        """
    typed_pattern = "|".join(typed)
    return f"""
        CALL db.index.vector.queryNodes($index_name, $k, $vec)
        YIELD node, score AS rawScore
        OPTIONAL MATCH (node)<-[:CALLS]-(c1)
        WITH node, rawScore, count(c1) AS callsIn
        OPTIONAL MATCH (node)<-[:{typed_pattern}]-(c2)
        WITH node, rawScore, callsIn, count(c2) AS typedIn
        RETURN elementId(node) AS id,
               rawScore * (1.0 + log(1.0 + toFloat(callsIn) + $lam * toFloat(typedIn))) AS score
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


def extended_graph_arm(query_fn, qvec, k, *, edge_types=None, lam=None) -> list:
    """The extended-in-degree arm, pooled across the 5 vector indexes EXACTLY as the frozen
    _arm_graph_weighted pools: a failing index is skipped (FD-1 isolation), the rest still
    answer. Returns cleaned {id, score} rows, score-desc, top-k. Never raises."""
    cypher = extended_arm_cypher(edge_types, lam)
    pooled = []
    for _label, index_name in VECTOR_INDEXES:
        try:
            params = {"index_name": index_name, "k": k, "vec": qvec}
            if lam is not None:
                params["lam"] = float(lam)
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


def replay(records, *, query_fn, embed_fn, edge_types=None, lam=None, top_k: int = 15,
           objective: str = "ndcg", k: int = 10, min_labeled: int = DEFAULT_MIN_LABELED,
           bootstrap_iters: int = _tw.DEFAULT_BOOTSTRAP_ITERS, alpha: float = _tw.DEFAULT_ALPHA,
           eps: float = _tw.DEFAULT_EPS) -> dict:
    """Replay the log's labeled queries with the extended graph arm SUBSTITUTED for the logged
    one (vector/bm25 untouched), re-fuse under EQUAL weights with the real fusion, and score
    paired nDCG deltas -> seeded bootstrap. Deterministic; never raises. Verdicts:
    insufficient_data | backend_unavailable | no_improvement | not_significant |
    beats_baseline_significant."""
    rels = _sanitize_rel_types(edge_types if edge_types is not None else DEFAULT_IMPACT_EDGE_TYPES)
    out = {"experiment": "graph_signal", "verdict": "insufficient_data", "n_labeled": 0,
           "n_replayed": 0, "n_skipped_redacted": 0, "n_failed": 0, "min_labeled": min_labeled,
           "edge_types": rels, "lam": lam, "top_k": top_k, "objective": objective, "ndcg_k": k,
           "mean_delta": None, "bootstrap": None, "replay_fidelity": None, "graph_drift": None,
           "qrel_coverage": None, "note": NOTE, "applied": False,
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
            variant_rows = extended_graph_arm(query_fn, qvec, fetch_k, edge_types=rels, lam=lam)
            calls_rows = extended_graph_arm(query_fn, qvec, fetch_k, edge_types=["CALLS"])
            if not variant_rows and not calls_rows:
                out["n_failed"] += 1
                continue
            variant_ids = _rl._rank_ids(variant_rows)
            logged_graph = q["per_arm"].get("graph_weighted") or []
            if logged_graph:
                fidelities.append(_topk_overlap(logged_graph, _rl._rank_ids(calls_rows), k))
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
    """replay + persist under instance.json experiments.graph_signal (applied FORCED false by
    instance_state.write_experiment). Returns the result + {written}. Never raises."""
    try:
        if records is None:
            records = _rl.read_records(root) if root is not None else []
        res = replay(records, query_fn=query_fn, embed_fn=embed_fn, **kw)
        path = _is.write_experiment(root, "graph_signal", res)
        res = dict(res)
        res["written"] = path is not None
        return res
    except Exception:
        return {"experiment": "graph_signal", "verdict": "insufficient_data", "written": False,
                "applied": False}


def neo4j_query_fn(driver):
    """The RUN-time adapter: wrap a live neo4j driver as the injected query function. The only
    place a driver is touched; the module itself imports clean without one."""
    def fn(cypher, params):
        with driver.session() as s:
            return [dict(r) for r in s.run(cypher, **(params or {}))]
    return fn
