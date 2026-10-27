#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Freeze the STAGE-0 regression oracle from the live real graph + assert determinism. [NEO4J]

Assumes the graph is already built in the TARGET Neo4j — by tests/stage0_real_graph.py (the `self`
oracle, builder "stage0-ast") or by the product's own default sync (the `self-graphify-build` oracle,
builder "graphify-default-sync") — from the oracle's PINNED corpus (`--corpus-sha`; see
tests/stage0_refreeze.py, which does build + freeze on a throwaway instance and cleans it up). The target
is NEVER defaulted: --project <dir> (that project's own DeepMap instance) or --uri bolt://host:port.
Constructs DeepMapRetriever (real nomic embeddings), runs a FROZEN query set TWICE, asserts
byte-identical top-K ORDERING (the determinism gate — test-strategy:20 / plan 0.3), and
writes the canonical ordering-only oracle. This is the executable no-regression baseline the
gate (tests/stage0_regression_check.py) consumes.

The frozen `order` is a list of STABLE re-sync-survivable keys `relpath::name:line` (the
fixture's own node-key format, stage0_real_graph.py:90) — NOT graph-instance elementIds
(`4:<uuid>:N`), which change on every re-sync and weld the oracle to one ephemeral graph
(see tests/oracles/README.md).
"""
from __future__ import annotations
import importlib.util, json, logging, os, sys
from pathlib import Path
from neo4j import GraphDatabase
logging.getLogger("neo4j").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage0_portable as _portable  # noqa: E402  (load_queries + oracle registry + explicit target)
URI = None   # resolved in main() — never an implicit bolt 7687
AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "deepmap"))
FROZEN = [
    "validate the incoming request before processing",
    "how are embeddings generated for the graph",
    "fuse the ranked results from multiple search arms",
    "run the tests and report pass or fail",
    "compute reciprocal rank fusion across arms",
]
TOP_K = int(os.environ.get("DEEPMAP_TOP_K", "10"))
KEY_FORMAT = "relpath::name:line"   # stable, re-sync-survivable (NOT elementId)


def stable_key(r: dict) -> str:
    """Re-sync-stable node identity: 'relpath::name:line' — the fixture's own node-key
    format (stage0_real_graph.py:90). Derived from source-stable fields (file/name/line),
    so it survives a DETACH-DELETE + re-MERGE; elementId (`4:<uuid>:N`) does not. The path
    separator is normalized to '/' so the oracle is also OS-portable (a Windows backslash
    source_file and a POSIX slash one fold to the same key). Used IDENTICALLY at freeze
    (here) and at gate time (stage0_regression_check.py)."""
    f = str(r.get("file") or "").replace("\\", "/")
    return f"{f}::{r.get('name')}:{r.get('line')}"


def enc_real():
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer("nomic-ai/modernbert-embed-base")
    return lambda t: [float(x) for x in m.encode(t)]


def load_retriever():
    spec = importlib.util.spec_from_file_location("dm_retrieval", REPO / "build" / "retrieval.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def main():
    for s in (sys.stdout, sys.stderr):
        try: s.reconfigure(encoding="utf-8")
        except Exception: pass
    import argparse
    p = argparse.ArgumentParser(
        description="Freeze the STAGE-0 no-regression oracle — repo-parametric so it freezes THIS "
                    "repo or an external clone (and can register it for the gate to find by id).")
    p.add_argument("--queries-file", default=None, help="a .json array / .txt of probe queries (default: built-in set).")
    p.add_argument("--oracle-path", default=None, help="write the oracle here (default: tests/stage0_real_oracle.json).")
    p.add_argument("--repo-id", default=None, help="register the frozen oracle under this id (with --register).")
    p.add_argument("--repo-dir", default=None, help="the external clone root (recorded in the registry).")
    p.add_argument("--pinned-sha", default=None, help="the clone's pinned SHA (recorded in the registry).")
    p.add_argument("--top-k", type=int, default=TOP_K, help=f"top-K ordering depth (default {TOP_K}).")
    p.add_argument("--register", action="store_true", help="add/replace the --repo-id entry in the oracle registry.")
    p.add_argument("--corpus-sha", default=None,
                   help="the COMMIT the graph was built from (recorded as the oracle's pinned corpus).")
    p.add_argument("--corpus-tree", default=None,
                   help="the repo subdirectory whose CONTENTS were the corpus root (keys relative to it).")
    p.add_argument("--corpus-paths", default=None,
                   help="comma-separated repo paths that were the corpus (keys relative to the repo root).")
    p.add_argument("--builder", default=None, choices=_portable.BUILDERS,
                   help="what turned the corpus into the graph (recorded with the corpus).")
    _portable.add_target_args(p)
    args = p.parse_args()
    # The target FIRST — before the (slow) model load; refuse rather than guess.
    global URI
    URI, target_problem, target_how = _portable.resolve_target(args.uri, args.project)
    if target_problem:
        print(f"[freeze] FATAL: {target_problem}", file=sys.stderr)
        return 2
    corpus = None
    if args.corpus_sha or args.builder or args.corpus_tree or args.corpus_paths:
        try:
            corpus = _portable.make_corpus(
                args.corpus_sha or "", builder=args.builder or "", tree=args.corpus_tree,
                paths=[x.strip() for x in args.corpus_paths.split(",") if x.strip()] if args.corpus_paths else None)
        except ValueError as exc:
            print(f"[freeze] FATAL: bad corpus: {exc}", file=sys.stderr)
            return 2
        if args.pinned_sha and args.pinned_sha.strip().lower() != corpus["sha"]:
            print("[freeze] FATAL: --pinned-sha differs from --corpus-sha", file=sys.stderr)
            return 2

    top_k = int(args.top_k)
    queries = _portable.load_queries(args.queries_file) if args.queries_file else FROZEN
    if args.oracle_path:
        out = Path(args.oracle_path)
    elif args.repo_id:
        out = _portable.default_oracle_path(args.repo_id, REPO)
    else:
        out = REPO / "tests" / "stage0_real_oracle.json"

    enc = enc_real()
    R = load_retriever()
    driver = GraphDatabase.driver(URI, auth=AUTH); driver.verify_connectivity()
    ret = R.DeepMapRetriever(driver, embed_fn=lambda q: enc("search_query: " + q))

    oracle, nondet = {}, []
    for q in queries:
        a = [stable_key(r) for r in ret.search(q, top_k=top_k)]
        b = [stable_key(r) for r in ret.search(q, top_k=top_k)]
        if a != b:
            nondet.append(q)
        top5 = ret.search(q, top_k=5)
        oracle[q] = {"order": a,                                    # STABLE keys; the contract
                     "named": [(r["type"], r["name"]) for r in top5]}
    driver.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"uri": URI, "target": target_how, "rrf_k": R.RRF_K,
            "weights": R.DEFAULT_ARM_WEIGHTS,
            "top_k": top_k, "key_format": KEY_FORMAT,
            "retriever": "build/retrieval.py",
            "repo_id": args.repo_id,
            "contract": ("TOP-K ORDERING of stable "
                         "relpath::name:line keys; scores "
                         "never compared (plan 0.3)")}
    if corpus:
        meta["corpus"] = corpus
    out.write_text(json.dumps({"_meta": meta, "queries": oracle}, indent=2, sort_keys=True), encoding="utf-8")
    print("=" * 60)
    print("STAGE-0 real-graph oracle freeze")
    print("=" * 60)
    for q, v in oracle.items():
        print(f"  {q!r}\n     -> {[n for _, n in v['named']]}")
    if nondet:
        print(f"\nNON-DETERMINISTIC for: {nondet} -> tie-break is a T4 fix (plan:442-449)")
        return 1

    if args.register and args.repo_id:
        reg = REPO / _portable.REGISTRY_RELPATH
        try:
            rel_oracle = str(out.resolve().relative_to(REPO.resolve())).replace("\\", "/")
        except ValueError:
            rel_oracle = str(out)
        _portable.register_oracle(reg, args.repo_id, repo_dir=(args.repo_dir or "."),
                                  oracle_path=rel_oracle, queries_file=args.queries_file,
                                  pinned_sha=(corpus["sha"] if corpus else args.pinned_sha), corpus=corpus)
        print(f"registered oracle '{args.repo_id}' -> {reg.relative_to(REPO)}")
    elif args.register and not args.repo_id:
        print("WARNING: --register ignored (no --repo-id given).", file=sys.stderr)

    print(f"\nDETERMINISTIC: re-run stable-key ordering identical for all {len(queries)} queries.")
    try:
        shown = out.relative_to(REPO)
    except ValueError:
        shown = out
    print(f"oracle frozen ({KEY_FORMAT}, top_k={top_k}) -> {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
