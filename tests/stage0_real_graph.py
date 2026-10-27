#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STAGE-0 REAL-graph pipeline: build a typed code graph from REAL source and run the
retriever on it against a live Neo4j.  [NEO4J]

A step up from tests/stage0_live_smoketest.py (synthetic graph): this extracts a REAL,
typed DeepMap-schema graph (:Module/:Function/:Method/:Class with name, source_file,
start_line, body_summary, embedding + :CALLS edges) from real Python source via the stdlib
`ast`, pushes it into the live Neo4j, indexes + embeds it, and runs DeepMapRetriever.

Embeddings auto-upgrade: if the ML stack (sentence-transformers + nomic-ai/modernbert-embed-
base) is importable, real semantic embeddings are used (asymmetric search_document/search_query
prefixing per design.md:34-37); otherwise a deterministic stub is used so the pipeline still
runs (vector relevance is then meaningless, but structure + FTS + graph-weighting are real).

NOTE on the AST source: production DeepMap uses the upstream `graphify` engine (design mandate).
graphify's raw extraction is loosely typed; this script builds the typed schema DIRECTLY via
`ast` to produce a clean, real STAGE-0 test corpus for validating the retriever end-to-end.

Run:  python tests/stage0_real_graph.py --project <throwaway dir> [--repo-dir <corpus>] [target_dir ...]
      (or --uri bolt://host:port). The Neo4j target is NEVER defaulted — the graph there is WIPED
      (`MATCH (n) DETACH DELETE n`), so it must be empty, a STAGE-0 harness graph (the builder stamps
      a :_Stage0Harness marker) or --project's own graph; anything else is refused (--allow-wipe).
      tests/stage0_refreeze.py drives this on a throwaway instance for the pinned `self` corpus.
Default extraction target: this repo's build/ + scripts/ + tests/.
"""
from __future__ import annotations
import ast
import hashlib
import logging
import os
import sys
import time
import importlib.util
from pathlib import Path

from neo4j import GraphDatabase

logging.getLogger("neo4j").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage0_portable import relpath_for, load_queries  # noqa: E402  (portable STAGE-0 core)
import stage0_portable as _portable  # noqa: E402  (explicit target + wipe guard)
URI = None   # resolved in main() — never an implicit bolt 7687
AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "deepmap"))
DIM = 768
_NODE_BATCH = 500      # nodes per UNWIND (consecutive same-label runs)
_EDGE_BATCH = 10000    # CALLS rows per write transaction
VECTOR_INDEXES = [
    ("Function", "deepmap_fn_embeddings"), ("Method", "deepmap_method_embeddings"),
    ("Class", "deepmap_class_embeddings"), ("Module", "deepmap_module_embeddings"),
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


# ---- embeddings: real model if available, else deterministic stub -----------
def make_encoder():
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("nomic-ai/modernbert-embed-base")

        def enc(text):
            return [float(x) for x in model.encode(text)]
        return enc, "REAL (nomic-ai/modernbert-embed-base)"
    except Exception as exc:
        def enc(text):
            import random
            rnd = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
            return [rnd.uniform(-1.0, 1.0) for _ in range(DIM)]
        return enc, f"STUB (deterministic; ML stack not ready: {type(exc).__name__})"


# ---- extract a typed DeepMap-schema graph from real Python via ast ----------
def summarize(node, name, kind):
    doc = ast.get_docstring(node)
    if doc:
        return doc.strip().split("\n")[0][:300]
    return f"{kind} {name}"


class Extractor(ast.NodeVisitor):
    def __init__(self, src_file):
        self.src_file = src_file
        self.nodes = []      # dicts: key,label(type),name,source_file,start_line,body_summary
        self.calls = []      # (caller_key, callee_name)
        self.class_stack = []
        self.cur_func_key = None

    def _key(self, name, line):
        return f"{self.src_file}::{name}:{line}"

    def visit_ClassDef(self, node):
        key = self._key(node.name, node.lineno)
        self.nodes.append(dict(key=key, label="Class", name=node.name,
                               source_file=self.src_file, start_line=node.lineno,
                               body_summary=summarize(node, node.name, "class")))
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_func(self, node):
        kind = "Method" if self.class_stack else "Function"
        name = node.name if not self.class_stack else f"{self.class_stack[-1]}.{node.name}"
        # Route heuristic: a decorator mentioning route/get/post/app
        deco = " ".join(ast.dump(d) for d in node.decorator_list).lower()
        if any(w in deco for w in ("route", "get", "post", "put", "delete", "endpoint")):
            kind = "Route"
        key = self._key(name, node.lineno)
        self.nodes.append(dict(key=key, label=kind, name=name, source_file=self.src_file,
                               start_line=node.lineno, body_summary=summarize(node, name, kind.lower())))
        prev = self.cur_func_key
        self.cur_func_key = key
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = None
                f = child.func
                if isinstance(f, ast.Name):
                    callee = f.id
                elif isinstance(f, ast.Attribute):
                    callee = f.attr
                if callee:
                    self.calls.append((key, callee))
        self.generic_visit(node)
        self.cur_func_key = prev

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func


def extract_dir(target: Path, relpath_base=None, prefix: str = ""):
    """Extract a typed graph from every .py under `target`. Node keys are PORTABLE
    `relpath::name:line` relative to `relpath_base` (default: this repo) — pass the clone root as
    `relpath_base` to validate an arbitrary EXTERNAL repo with the same harness. `prefix` lets
    several repos share one graph without key collisions."""
    base = Path(relpath_base) if relpath_base is not None else REPO
    nodes, calls = [], []
    for py in sorted(Path(target).rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        rel = relpath_for(py, base, prefix)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        mod_name = py.stem
        nodes.append(dict(key=f"{rel}::module", label="Module", name=mod_name,
                          source_file=rel, start_line=1,
                          body_summary=(ast.get_docstring(tree) or f"module {mod_name}").split("\n")[0][:300]))
        ex = Extractor(rel)
        ex.visit(tree)
        nodes.extend(ex.nodes)
        calls.extend(ex.calls)
    return nodes, calls


# ---- push to Neo4j + index + embed ------------------------------------------
def build_graph(driver, nodes, calls, enc):
    name_to_keys = {}
    for n in nodes:
        # bare name for CALLS resolution (method -> last segment too)
        for nm in {n["name"], n["name"].split(".")[-1]}:
            name_to_keys.setdefault(nm, []).append(n["key"])
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        # mark the graph as the harness's own, so a later harness run may wipe it again (wipe_problem)
        s.run(f"MERGE (m:{_portable.HARNESS_MARKER_LABEL} {{id:'singleton'}}) SET m.builder='stage0_real_graph'")
        for label, idx in VECTOR_INDEXES:
            s.run(f"CREATE VECTOR INDEX {idx} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
                  "OPTIONS {indexConfig: {`vector.dimensions`: 768, "
                  "`vector.similarity_function`: 'cosine', `vector.hnsw.m`: 24, "
                  "`vector.hnsw.ef_construction`: 400}}")
        s.run("CREATE FULLTEXT INDEX deepmap_code_fts IF NOT EXISTS "
              "FOR (n:Function|Class|Module|Method|Route) "
              "ON EACH [n.name, n.body_summary, n.docstring, n.rationale]")
    # The SAME graph the original one-statement-per-node / per-edge loop built (every node MERGEd by
    # (label, key) in the original order, last SET wins; every CALLS edge MERGEd between every node
    # carrying the caller key and every node carrying the callee key), written in BATCHES: the per-edge
    # label-less `MATCH (a {key}),(b {key})` was two AllNodesScans per statement, and the pinned `self`
    # corpus (build/ + scripts/ + tests/) resolves ~380k edge MERGEs — hours at ~15 statements/s.
    #   * nodes: consecutive same-label runs, UNWIND in the original order (per-label vector-index and
    #     full-text insertion order unchanged), one embedding per node exactly as before;
    #   * edges: the distinct (caller key, callee key) pairs, bound by elementId (an index seek).
    edge_count = 0
    pairs, seen = [], set()
    for caller_key, callee_name in calls:
        for callee_key in name_to_keys.get(callee_name, []):
            if callee_key == caller_key:
                continue
            edge_count += 1
            if (caller_key, callee_key) not in seen:
                seen.add((caller_key, callee_key))
                pairs.append((caller_key, callee_key))
    runs = []
    for n in nodes:
        row = {"key": n["key"], "name": n["name"], "sf": n["source_file"], "line": n["start_line"],
               "summ": n["body_summary"], "emb": enc("search_document: " + (n["body_summary"] or n["name"]))}
        if runs and runs[-1][0] == n["label"] and len(runs[-1][1]) < _NODE_BATCH:
            runs[-1][1].append(row)
        else:
            runs.append((n["label"], [row]))
    with driver.session() as s:
        for label, rows in runs:
            s.execute_write(lambda tx, lb=label, rs=rows: tx.run(
                f"UNWIND $rows AS r MERGE (x:{lb} {{key:r.key}}) "
                "SET x.name=r.name, x.source_file=r.sf, x.start_line=r.line, "
                "x.body_summary=r.summ, x.embedding=r.emb", rows=rs).consume())
        ids = {}
        for rec in s.run("MATCH (n) WHERE n.key IS NOT NULL RETURN n.key AS k, elementId(n) AS id"):
            ids.setdefault(rec["k"], []).append(rec["id"])
        edge_rows = [{"a": ia, "b": ib} for ck, bk in pairs for ia in ids.get(ck, ()) for ib in ids.get(bk, ())]
        for i in range(0, len(edge_rows), _EDGE_BATCH):
            chunk = edge_rows[i:i + _EDGE_BATCH]
            s.execute_write(lambda tx, rs=chunk: tx.run(
                "UNWIND $rows AS r MATCH (a) WHERE elementId(a) = r.a MATCH (b) WHERE elementId(b) = r.b "
                "MERGE (a)-[:CALLS]->(b)", rows=rs).consume())
    # wait for indexes online
    for _ in range(60):
        with driver.session() as s:
            rows = list(s.run("SHOW INDEXES YIELD name,state WHERE name STARTS WITH 'deepmap_' "
                              "RETURN state"))
        if rows and all(r["state"] == "ONLINE" for r in rows):
            break
        time.sleep(1)
    return edge_count


def load_retriever():
    spec = importlib.util.spec_from_file_location("dm_retrieval", REPO / "build" / "retrieval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DEFAULT_QUERIES = [
    "validate the incoming request before processing",
    "how are embeddings generated for the graph",
    "fuse the ranked results from multiple search arms",
    "run the tests and report pass or fail",
]


def main():
    utf8()
    import argparse
    p = argparse.ArgumentParser(
        description="STAGE-0 REAL-graph builder — repo-parametric so it validates THIS repo or an "
                    "arbitrary external clone (keys stay portable relpath::name:line).")
    p.add_argument("targets", nargs="*", help="dirs to extract (default: --repo-dir, or build/scripts/tests).")
    p.add_argument("--repo-dir", default=None,
                   help="the repo root to validate (external clone). Sets the relpath base + default target.")
    p.add_argument("--relpath-base", default=None,
                   help="base for portable relpaths (default: --repo-dir, else this repo).")
    p.add_argument("--prefix", default="", help="optional key prefix (lets several repos share one graph).")
    p.add_argument("--queries-file", default=None, help="a .json array / .txt of probe queries.")
    p.add_argument("--require-real-embeddings", action="store_true",
                   help="exit 2 instead of falling back to the deterministic STUB encoder (an oracle frozen "
                        "on stub vectors would not match a real-encoder gate run).")
    _portable.add_target_args(p, destructive=True)
    args = p.parse_args()
    # The target FIRST — before the (slow) model load or any extraction; refuse rather than guess.
    global URI
    URI, target_problem, target_how = _portable.resolve_target(args.uri, args.project)
    if target_problem:
        print(f"[real-graph] FATAL: {target_problem}", file=sys.stderr)
        return 2

    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else None
    relpath_base = Path(args.relpath_base).resolve() if args.relpath_base else (repo_dir or REPO)
    if args.targets:
        targets = [Path(a) for a in args.targets]
    elif repo_dir is not None:
        targets = [repo_dir]
    else:
        targets = [REPO / "build", REPO / "scripts", REPO / "tests"]
    queries = load_queries(args.queries_file) if args.queries_file else DEFAULT_QUERIES

    enc, enc_kind = make_encoder()
    if args.require_real_embeddings and not enc_kind.startswith("REAL"):
        print(f"[real-graph] FATAL: --require-real-embeddings but the encoder is {enc_kind}", file=sys.stderr)
        return 2
    nodes, calls = [], []
    for t in targets:
        n, c = extract_dir(t, relpath_base, args.prefix)
        nodes += n
        calls += c

    R = load_retriever()
    driver = GraphDatabase.driver(URI, auth=AUTH)
    driver.verify_connectivity()
    why = _portable.wipe_problem(*_portable.read_graph_state(driver), project=args.project,
                                 allow=args.allow_wipe, identity=_identity)
    if why:
        driver.close()
        print(f"[real-graph] FATAL: {URI} ({target_how}): {why}", file=sys.stderr)
        return 2
    edges = build_graph(driver, nodes, calls, enc)

    by_label = {}
    for n in nodes:
        by_label[n["label"]] = by_label.get(n["label"], 0) + 1

    ret = R.DeepMapRetriever(driver, embed_fn=lambda q: enc("search_query: " + q))

    print("\n" + "=" * 72)
    print("STAGE-0 REAL-graph retrieval  (Neo4j " + URI + ")")
    print("=" * 72)
    print(f"  embeddings: {enc_kind}")
    print(f"  relpath base: {relpath_base}")
    print(f"  extracted: {len(nodes)} nodes {by_label}, {edges} CALLS edges from {len(targets)} target(s)")
    print(f"  detect_search_clause: {'SEARCH' if ret._use_search_clause else 'procedure fallback'}")
    ok = True
    for q in queries:
        try:
            res = ret.search(q, top_k=5)
            print(f"\n  Q: {q!r}")
            for r in res[:5]:
                print(f"     - [{r['type']}] {r['name']}  ({r['file']}:{r['line']})  score={r['score']:.4f}")
            if not res:
                print("     (no results)")
        except Exception as exc:
            ok = False
            print(f"  Q: {q!r} -> FAIL {type(exc).__name__}: {exc}")
    driver.close()
    print("\nRESULT:", "REAL graph built + retrieval ran" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
