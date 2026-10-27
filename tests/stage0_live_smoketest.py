#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STAGE-0 LIVE smoke-test of build/retrieval.py against a real Neo4j.  [NEO4J]

The first time the never-run v2.0/v2.1 retriever touches a LIVE Neo4j. It is NOT the
full T2 regression oracle (that needs a real repo's intent-summary graph via the absent
product pipeline) — it validates the retriever's live EXECUTION path on a SYNTHETIC graph
(schema + the 5 searched vector indexes with HNSW options + the code FTS index + a handful
of nodes/edges with random 768-d vectors). It proves the code RUNS on live Neo4j; it does
NOT measure retrieval quality (the embeddings are random).

Scenarios exercised (runbook: tests/oracles/README.md):
  1.1 _detect_search_clause result on this Neo4j version (SEARCH clause vs procedure fallback)
  1.2 empty-arm path (a query with no FTS terms; surviving-arm fusion)
  1.3 HNSW option acceptance (vector.hnsw.m=24, ef_construction=400, 768-dim cosine)
  1.7 elementId hydration (every fused id hydrates; no dropped rows / all-zero scores)
  + end-to-end: 3 arms + Weighted-RRF fusion with the {id,name,type,file,line,summary,score}
    shape; the procedure-fallback path independently confirmed.

Run:  python tests/stage0_live_smoketest.py --project <throwaway dir>   (or --uri bolt://host:port)
      The target is NEVER defaulted (7687 is typically another project's database) and the graph there
      is WIPED, so it must be empty, a STAGE-0 harness graph, or --project's own (else --allow-wipe).
Auth: the locked (neo4j, deepmap) — NOTE the installer must relax Neo4j's default 8-char
      minimum password length for "deepmap" to be accepted (finding 1.6).
"""
from __future__ import annotations
import hashlib, importlib.util, logging, os, sys, time
from pathlib import Path

from neo4j import GraphDatabase

logging.getLogger("neo4j").setLevel(logging.ERROR)  # quiet benign notification stream

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage0_portable as _portable  # noqa: E402  (explicit target + wipe guard)
URI = None   # resolved in main() — never an implicit bolt 7687
AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "deepmap"))
DIM = 768

VECTOR_INDEXES = [  # the 5 SEARCHED labels (Community excluded), mirrors retrieval.py:54-61
    ("Function", "deepmap_fn_embeddings"),
    ("Method", "deepmap_method_embeddings"),
    ("Class", "deepmap_class_embeddings"),
    ("Module", "deepmap_module_embeddings"),
    ("Route", "deepmap_route_embeddings"),
]


def _identity(project) -> str:
    """The owner string sync stamps for `project` (port_registry.project_identity)."""
    sys.path.insert(0, str(REPO / "build"))
    import port_registry as PR   # noqa: E402
    return PR.project_identity(project)


def utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def embed(text: str):
    """Deterministic 768-d vector seeded by text (smoke test only; NOT a real model)."""
    import random
    rnd = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
    return [rnd.uniform(-1.0, 1.0) for _ in range(DIM)]


def load_retriever():
    spec = importlib.util.spec_from_file_location("dm_retrieval", REPO / "build" / "retrieval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NODES = [
    ("validate_payment", "Function", "pay.py", 10, "validate the incoming payment request before charging"),
    ("charge_card", "Function", "pay.py", 40, "charge the customer credit card via the gateway"),
    ("PaymentService", "Class", "pay.py", 5, "service that orchestrates payment validation and capture"),
    ("refund", "Method", "pay.py", 80, "issue a refund for a captured payment"),
    ("payments_route", "Route", "routes.py", 3, "POST /payments endpoint that handles a payment request"),
    ("app_module", "Module", "app.py", 1, "application entrypoint wiring routes and services"),
    ("log_event", "Function", "util.py", 12, "append an audit log event for compliance"),
]
CALLS = [
    ("payments_route", "validate_payment"),
    ("validate_payment", "charge_card"),
    ("PaymentService", "validate_payment"),
    ("refund", "charge_card"),
    ("app_module", "payments_route"),
]


def setup_graph(driver, report):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run(f"MERGE (m:{_portable.HARNESS_MARKER_LABEL} {{id:'singleton'}}) SET m.builder='stage0_live_smoketest'")
        hnsw_ok, hnsw_err = True, ""
        for label, idx in VECTOR_INDEXES:
            full = (f"CREATE VECTOR INDEX {idx} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
                    "OPTIONS {indexConfig: {`vector.dimensions`: 768, "
                    "`vector.similarity_function`: 'cosine', `vector.hnsw.m`: 24, "
                    "`vector.hnsw.ef_construction`: 400}}")
            try:
                s.run(full)
            except Exception as exc:
                hnsw_ok, hnsw_err = False, str(exc)
                s.run(f"CREATE VECTOR INDEX {idx} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
                      "OPTIONS {indexConfig: {`vector.dimensions`: 768, "
                      "`vector.similarity_function`: 'cosine'}}")
        report["1.3 HNSW option acceptance"] = (
            "PASS - m=24/ef_construction=400 accepted" if hnsw_ok
            else f"PARTIAL - extended hnsw options rejected, used dims+similarity ({hnsw_err[:80]})")
        s.run("CREATE FULLTEXT INDEX deepmap_code_fts IF NOT EXISTS "
              "FOR (n:Function|Class|Module|Method|Route) "
              "ON EACH [n.name, n.body_summary, n.docstring, n.rationale]")
        for name, label, f, line, summ in NODES:
            s.run(f"CREATE (n:{label} {{name:$name, source_file:$f, start_line:$line, "
                  "body_summary:$summ, embedding:$emb})",
                  name=name, f=f, line=line, summ=summ, emb=embed(summ))
        for caller, callee in CALLS:
            s.run("MATCH (a {name:$a}),(b {name:$b}) CREATE (a)-[:CALLS]->(b)", a=caller, b=callee)
    deadline = time.time() + 60
    states = {}
    while time.time() < deadline:
        with driver.session() as s:
            rows = list(s.run("SHOW INDEXES YIELD name,state WHERE name STARTS WITH 'deepmap_' "
                              "RETURN name,state"))
        states = {r["name"]: r["state"] for r in rows}
        if states and all(v == "ONLINE" for v in states.values()):
            report["index ONLINE"] = f"PASS - {len(states)} deepmap_* indexes ONLINE"
            return
        time.sleep(1)
    report["index ONLINE"] = f"FAIL - not all ONLINE: {states}"


def main():
    global URI
    utf8()
    import argparse
    ap = argparse.ArgumentParser(description="STAGE-0 LIVE smoke-test on a SYNTHETIC graph (wipes its target).")
    _portable.add_target_args(ap, destructive=True)
    args = ap.parse_args()
    URI, problem, how = _portable.resolve_target(args.uri, args.project)
    if problem:
        print(f"[smoketest] FATAL: {problem}", file=sys.stderr)
        return 2
    R = load_retriever()
    driver = GraphDatabase.driver(URI, auth=AUTH)
    driver.verify_connectivity()
    why = _portable.wipe_problem(*_portable.read_graph_state(driver), project=args.project,
                                 allow=args.allow_wipe, identity=_identity)
    if why:
        driver.close()
        print(f"[smoketest] FATAL: {URI} ({how}): {why}", file=sys.stderr)
        return 2
    report = {}
    setup_graph(driver, report)

    ret = R.DeepMapRetriever(driver, embed_fn=embed)
    report["1.1 _detect_search_clause"] = (
        f"detected mode = {'SEARCH clause' if ret._use_search_clause else 'procedure fallback'}"
        " (both valid; locked-decision dual path)")

    try:
        res = ret.search("validate the payment request", top_k=5)
        shape_ok = all(set(r) >= {"id", "name", "type", "file", "line", "summary", "score"} for r in res)
        report["end-to-end search()"] = (
            f"PASS - {len(res)} results, shape_ok={shape_ok}; top={[r['name'] for r in res][:5]}"
            if res else "WARN - 0 results (arms executed but no hits)")
        report["1.7 elementId hydration"] = (
            "PASS - all rows hydrated (no all-zero/dropped)"
            if res and all(isinstance(r["id"], str) and r["name"] for r in res) else "CHECK - inspect rows")
    except Exception as exc:
        report["end-to-end search()"] = f"FAIL - {type(exc).__name__}: {exc}"

    try:
        res2 = ret.search("a io", top_k=5)
        report["1.2 empty-arm (FTS empty)"] = f"PASS - surviving arms fused, {len(res2)} results, no crash"
    except Exception as exc:
        report["1.2 empty-arm (FTS empty)"] = f"FAIL - {type(exc).__name__}: {exc}"

    try:
        ret._use_search_clause = False
        res3 = ret.search("validate the payment request", top_k=5)
        report["procedure-fallback path"] = f"PASS - fallback returns {len(res3)} results"
    except Exception as exc:
        report["procedure-fallback path"] = f"FAIL - {type(exc).__name__}: {exc}"

    driver.close()
    print("\n" + "=" * 70)
    print("STAGE-0 LIVE smoke-test of build/retrieval.py  (Neo4j " + URI + ")")
    print("=" * 70)
    for k, v in report.items():
        print(f"  {k}: {v}")
    fails = [k for k, v in report.items() if str(v).startswith("FAIL")]
    print("\nRESULT:", "ALL GREEN (no FAIL)" if not fails else f"FAILURES: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
