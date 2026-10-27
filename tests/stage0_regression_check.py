#!/usr/bin/env python3
"""stage0_regression_check.py — STAGE-0 ORDERING-only no-regression gate. [NEO4J]

Re-runs the FROZEN query set against the CURRENT retriever and asserts the top-K
ORDERING matches the frozen oracle. Ordering is the contract; score VALUE/FORMAT
differences are allowed and ignored (test-strategy:20; plan §0.3 = lines 51-52).
The fusion engine is the pure-Python _weighted_rrf (ranx dropped), so fused scores
are a NEW series — NEVER compared across runs.

The compared identity is the STABLE key `relpath::name:line` (the fixture's own
node-key format, stage0_real_graph.py:90), NOT graph-instance elementIds
(`4:<uuid>:N`). elementIds change on every re-sync and welded the gate to one
ephemeral graph, the defect this harness fixes (tests/oracles/README.md).
Freeze (stage0_freeze_oracle.py) and gate (here) derive the key
IDENTICALLY, so a re-sync with fresh elementIds still gates cleanly.

This gate is what /oss-kit:oss-validate and CI run on a live instance. It IMPORTS
and EXERCISES the retriever only; it never edits product code.

A/B note (settled): the separately-deployed retriever never existed — this repo's
build/retrieval.py IS the canonical retriever (CLAUDE.md "Oracle identity"). So the
config below DEFAULTS to Scenario A and is runnable out of the box; every knob is
still env-overridable for a hypothetical Scenario B.

TARGET (never defaulted): --project <dir> (that project's OWN DeepMap instance, from the port registry),
--uri bolt://host:port, or an explicitly exported NEO4J_URI. The graph there must be built from the
oracle's PINNED corpus (registry `corpus`: a commit + builder) — tests/stage0_refreeze.py --check does the
whole round trip on a throwaway instance and cleans it up.

Exit 0  -> ordering matches the oracle for every query (no regression).
Exit 1  -> at least one query's top-K ordering diverged (REGRESSION).
Exit 2  -> harness/config error (no target, oracle missing, bad module, embed_fn unwired, or the target
           graph was not built from the oracle's corpus — most frozen keys absent).
Exit 3  -> detected NON-DETERMINISM (a RETRIEVER defect -> T4 tie-break fix),
           only when DEEPMAP_ASSERT_DETERMINISM=1.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

# Silence benign Neo4j driver notifications (e.g. "null value eliminated in set
# function" from the graph-weighted arm's count()) so the gate verdict isn't buried.
logging.getLogger("neo4j").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import stage0_portable as _portable  # noqa: E402  (registry + portable key helpers)

# --- CONFIG (A/B settled -> Scenario A defaults; all env-overridable) --------
# RETRIEVER_MODULE is the canonical retriever (T0/§0.2). Scenario B (a separately
# deployed baseline) never materialized, so we default to this repo's build/.
RETRIEVER_MODULE = Path(
    os.environ.get("RETRIEVER_MODULE", REPO / "build" / "retrieval.py")
).resolve()

ORACLE_PATH = Path(
    os.environ.get("DEEPMAP_ORACLE", REPO / "tests" / "stage0_real_oracle.json")
).resolve()
TOP_K = int(os.environ.get("DEEPMAP_TOP_K", "10"))  # oracle _meta.top_k wins if present
ASSERT_DETERMINISM = os.environ.get("DEEPMAP_ASSERT_DETERMINISM") == "1"

# Neo4j auth is the LOCKED constant (neo4j, deepmap) — verify, do not re-decide
# (plan §2.2-G; spec §4.5). The TARGET is never defaulted: main() resolves --uri / --project (that
# project's own DeepMap instance) / an explicit NEO4J_URI through stage0_portable.resolve_target — an
# implicit bolt 7687 was another project's database on the 2026-09 validation box.
NEO4J_AUTH = (
    os.environ.get("NEO4J_USER", "neo4j"),
    os.environ.get("NEO4J_PASSWORD", "deepmap"),
)

# The embedding source. By default we build the SAME inline encoder the freeze
# script uses (nomic-ai/modernbert-embed-base, asymmetric "search_query: " prefix)
# so freeze and gate share a byte-identical embedding path with no build/-on-path
# requirement. Set DEEPMAP_EMBED_MODULE/_SYMBOL to bind a different (e.g. deployed)
# embed helper instead.
EMBED_MODULE = os.environ.get("DEEPMAP_EMBED_MODULE")     # optional override
EMBED_SYMBOL = os.environ.get("DEEPMAP_EMBED_SYMBOL", "embed_query")


def stable_key(r: dict) -> str:
    """Re-sync-stable node identity: 'relpath::name:line' (stage0_real_graph.py:90).
    Identical to the freeze-time helper — derived from source-stable file/name/line,
    so it survives a DETACH-DELETE + re-MERGE. elementId does NOT. The path separator
    is normalized to '/' so the oracle is OS-portable."""
    f = str(r.get("file") or "").replace("\\", "/")
    return f"{f}::{r.get('name')}:{r.get('line')}"


def resolve_oracle_path_arg(oracle_path=None, repo_id=None, registry_path=None,
                            default=None, repo_root=REPO):
    """Resolve WHICH frozen oracle to gate against — the decoupling that lets the SAME gate validate
    THIS repo or an arbitrary registered external clone. Precedence:
      1. --oracle-path  -> that exact file,
      2. --repo-id      -> the registry entry's oracle_path, resolved against repo_root,
      3. else           -> `default` (the in-repo oracle / DEEPMAP_ORACLE env).
    Raises KeyError if --repo-id is given but unknown (fail loud, never gate a phantom oracle)."""
    if oracle_path:
        return Path(oracle_path).resolve()
    if repo_id:
        reg = Path(registry_path) if registry_path else (Path(repo_root) / _portable.REGISTRY_RELPATH)
        entry = _portable.lookup_oracle(reg, repo_id)
        if entry is None:
            raise KeyError(f"repo_id {repo_id!r} not found in oracle registry {reg}")
        return _portable.resolve_oracle_path(entry, repo_root).resolve()
    return Path(default).resolve() if default else ORACLE_PATH


def _load_retriever_module(path: Path):
    if not path.is_file():
        print(f"[gate] FATAL: RETRIEVER_MODULE not found: {path}", file=sys.stderr)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("deepmap_retriever_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_embed_fn():
    """Default: build the inline nomic encoder used at freeze time (search_query:
    prefix), guaranteeing freeze==gate embeddings. Override via DEEPMAP_EMBED_MODULE."""
    if EMBED_MODULE:
        import importlib
        try:
            m = importlib.import_module(EMBED_MODULE)
            fn = getattr(m, EMBED_SYMBOL)
        except Exception as e:
            print(f"[gate] FATAL: cannot import embed_fn {EMBED_MODULE}.{EMBED_SYMBOL}: {e}\n"
                  f"       Unset it to use the default inline nomic encoder.", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as e:
            print(f"[gate] FATAL: ML stack unavailable for the default encoder ({e}).\n"
                  f"       Install sentence-transformers, or set DEEPMAP_EMBED_MODULE/_SYMBOL.",
                  file=sys.stderr)
            sys.exit(2)
        model = SentenceTransformer("nomic-ai/modernbert-embed-base")
        # Same path as stage0_freeze_oracle.py: prefix applied once, externally.
        def fn(text: str):
            return [float(x) for x in model.encode("search_query: " + text)]

    v = fn("probe")
    if not (isinstance(v, list) and len(v) == 768):
        print("[gate] FATAL: embed_fn must return list[float] len 768 "
              "(design.md:20; retrieval.py:100).", file=sys.stderr)
        sys.exit(2)
    return fn


def _canonical_sequence(results: list[dict], top_k: int) -> list[str]:
    """Score-STRIPPED top-K STABLE-KEY sequence.

    Take the stable key IN RETURNED ORDER and DROP the score entirely — score MEANS
    DIFFERENT THINGS per path (raw arm score on the single-arm bypass vs an RRF score
    on the multi-arm path, retrieval.py) and is NOT comparable. Ordering (post-_hydrate,
    retrieval.py:264) is the contract."""
    return [stable_key(r) for r in results[:top_k]]


def _entry_query_and_order(key: str, entry: dict) -> tuple[str, list[str]]:
    """Normalize an oracle entry across both producer shapes:
      * stage0_freeze_oracle.py  -> keyed by query TEXT, fields {order, named}
      * stage0_capture_oracle.py -> keyed by qid,        fields {query, order, ...}
    Returns (query_text, expected_order)."""
    query = entry.get("query", key)
    return query, [str(x) for x in entry["order"]]


def _diff(query: str, expected: list[str], actual: list[str]) -> str:
    lines = [f"  QUERY: {query!r}",
             f"    rank | {'ORACLE (frozen)':<40} | CURRENT",
             f"    -----+-{'-'*40}-+--------"]
    for i in range(max(len(expected), len(actual))):
        e = expected[i] if i < len(expected) else "<missing>"
        a = actual[i] if i < len(actual) else "<missing>"
        mark = "" if e == a else "   <-- DIVERGES"
        lines.append(f"    {i+1:>4} | {e:<40} | {a}{mark}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="STAGE-0 no-regression gate — repo-decoupled: gate THIS repo's oracle, or any "
                    "registered external clone's via --oracle-path / --repo-id.")
    p.add_argument("--oracle-path", default=None, help="gate against this exact frozen oracle file.")
    p.add_argument("--repo-id", default=None, help="resolve the oracle via the registry by repo id.")
    p.add_argument("--registry", default=None,
                   help=f"oracle registry path (default: {_portable.REGISTRY_RELPATH}).")
    _portable.add_target_args(p)
    args = p.parse_args()
    # The target FIRST — before the retriever, the model or a driver is touched (fail fast, touch nothing).
    neo4j_uri, target_problem, target_how = _portable.resolve_target(args.uri, args.project)
    if target_problem:
        print(f"[gate] FATAL: {target_problem}", file=sys.stderr)
        return 2
    try:
        oracle_path = resolve_oracle_path_arg(oracle_path=args.oracle_path, repo_id=args.repo_id,
                                              registry_path=args.registry, default=ORACLE_PATH)
    except KeyError as exc:
        print(f"[gate] FATAL: {exc}", file=sys.stderr)
        return 2

    if not oracle_path.is_file():
        print(f"[gate] FATAL: frozen T2 oracle not found: {oracle_path}\n"
              f"       Capture it in STAGE 0 / T2 before running this gate "
              f"(tests/stage0_real_graph.py then tests/stage0_freeze_oracle.py).", file=sys.stderr)
        return 2

    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    frozen_queries = oracle["queries"]                       # frozen query set (committed order)
    top_k = oracle.get("_meta", {}).get("top_k", TOP_K)
    key_fmt = oracle.get("_meta", {}).get("key_format", "")
    if key_fmt and key_fmt != "relpath::name:line":
        print(f"[gate] FATAL: oracle key_format {key_fmt!r} != 'relpath::name:line'.\n"
              f"       This gate compares stable keys; an elementId-keyed oracle is stale "
              f"— RE-FREEZE with stage0_freeze_oracle.py.", file=sys.stderr)
        return 2

    mod = _load_retriever_module(RETRIEVER_MODULE)

    try:
        from neo4j import GraphDatabase                      # neo4j Bolt driver (Apache-2.0)
    except ImportError:
        print("[gate] FATAL: neo4j driver not importable", file=sys.stderr)
        return 2

    embed_fn = _load_embed_fn()                              # real, 768-dim-validated

    print(f"[gate] target: {neo4j_uri} ({target_how}); oracle: {oracle_path}")
    driver = GraphDatabase.driver(neo4j_uri, auth=NEO4J_AUTH)
    try:
        # WRONG GRAPH vs REGRESSION: an oracle is frozen on a pinned corpus. If the target graph holds
        # almost none of its keys, it was built from something else (a project graph, a stale build) —
        # that is a harness/config error (exit 2), never reported as an ordering regression.
        with driver.session() as s:
            present = {stable_key(r) for r in s.run(
                "MATCH (n) WHERE n.source_file IS NOT NULL "
                "RETURN n.source_file AS file, n.name AS name, n.start_line AS line")}
        have, total, missing = _portable.graph_key_coverage(oracle, present)
        if total and have * 2 < total:
            corpus = (oracle.get("_meta") or {}).get("corpus") or {}
            print(f"[gate] FATAL: the target graph holds only {have}/{total} of the oracle's frozen keys "
                  f"(e.g. missing {missing[:3]}) — it was not built from this oracle's pinned corpus "
                  f"({corpus.get('builder', '?')} @ {str(corpus.get('sha', '?'))[:12]}). Build it with "
                  f"tests/stage0_refreeze.py --check (a throwaway instance), then re-run.", file=sys.stderr)
            return 2
        if missing:
            print(f"[gate] note: {total - have} of the oracle's {total} keys are absent from the target graph "
                  f"(e.g. {missing[:3]}) — the extraction changed; expect a divergence below.", file=sys.stderr)
        retriever = mod.DeepMapRetriever(driver, embed_fn)   # 2-arg call MUST work (retrieval.py:73)

        failures: list[str] = []
        for key, entry in frozen_queries.items():
            query, expected_seq = _entry_query_and_order(key, entry)

            seq = _canonical_sequence(retriever.search(query, top_k=top_k), top_k)

            if ASSERT_DETERMINISM:
                seq2 = _canonical_sequence(retriever.search(query, top_k=top_k), top_k)
                if seq != seq2:
                    print(f"[gate] NON-DETERMINISTIC for {query!r}:\n" + _diff(query, seq, seq2),
                          file=sys.stderr)
                    print("[gate] A deterministic tie-break (e.g. elementId) across retrieval.py "
                          "sorts is a T4 fix before the oracle can gate.", file=sys.stderr)
                    return 3

            if seq != expected_seq:
                failures.append(_diff(query, expected_seq, seq))
    finally:
        driver.close()

    if failures:
        print(f"[gate] REGRESSION: top-K ORDERING diverged from the frozen T2 oracle "
              f"in {len(failures)} quer(y/ies).", file=sys.stderr)
        print("[gate] (score-format/value differences are allowed; ORDERING is the contract "
              "— test-strategy:20, plan §0.3)", file=sys.stderr)
        for f in failures:
            print(f, file=sys.stderr)
        print("\n[gate] If a T4 CORRECTNESS fix legitimately changed the output, RE-CAPTURE + "
              "RE-FREEZE the oracle in STAGE 0 (plan T4), then re-run.", file=sys.stderr)
        return 1

    print(f"[gate] PASS: top-K stable-key ordering matches the frozen oracle for "
          f"{len(frozen_queries)} quer(y/ies). (scores ignored by design)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
