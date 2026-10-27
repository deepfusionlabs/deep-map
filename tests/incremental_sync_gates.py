#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for incremental sync (DEEPMAP_INCREMENTAL_SYNC) + node-key indexes.

The correctness bar for incremental sync is ONE invariant: the (nodes, calls, imports) it
reconstructs — reusing unchanged files from a per-file content-hash cache — is IDENTICAL to a full
re-extraction of the same tree. If that holds, everything downstream (push_to_neo4j MERGE/prune,
confidence, stale-cleanup, embeddings) produces a byte-identical graph, because it is fed identical
input. These gates prove:

  * incremental == full on a cold cache AND a warm cache (and a warm cache actually SKIPS parsing);
  * ADD / CHANGE / DELETE / MOVE of a file is each reflected exactly (== full of the mutated tree);
  * the reconstructed graph is identical at the push layer (same node keys, edges, confidence),
    captured via a fake Neo4j session;
  * a cold / corrupt / version-mismatched cache degrades to a full extraction (never a wrong graph);
  * an unreadable file cannot poison the cache (no hash collision with a zero-byte file);
  * the DEEPMAP_INCREMENTAL_SYNC dispatch routes the ast path to the incremental variant;
  * node-key RANGE indexes are created (one per code label, ON (n.key)) and waited on, and are
    disjoint from the search/embed vector+FTS indexes (so search coherence is untouched);
  * THE GATE HAS TEETH — a deliberately-broken incremental (that skips the content-hash check)
    DIVERGES from full, so a regression in cache invalidation would flip these gates RED.

Pure: temp file trees + fake Neo4j session. No Neo4j, no model, no network, no spend.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

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


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- fixtures --------------------------------------------------------------
TREE_1 = {
    "pkg/__init__.py": "",
    "pkg/a.py": (
        "import os\n"
        "from pkg.b import helper\n"
        "\n"
        "def alpha():\n"
        "    helper()\n"
        "    return os.getpid()\n"
        "\n"
        "class Svc:\n"
        "    def run(self):\n"
        "        self.step()\n"
        "    def step(self):\n"
        "        return 1\n"
    ),
    "pkg/b.py": (
        "def helper():\n"
        "    return 42\n"
        "\n"
        "def beta():\n"
        "    alpha()\n"
    ),
    "top.py": "def main():\n    return 0\n",
}


def _write_tree(root: Path, files: dict) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _equal_tuples(a, b) -> bool:
    """(nodes, calls, imports) equality. Nodes are dict lists (order + content), calls/imports are
    tuple lists — both in the SAME deterministic file order, so plain == is exact."""
    return a[0] == b[0] and a[1] == b[1] and a[2] == b[2]


def _diff(a, b) -> str:
    nd = [n for n in a[0] if n not in b[0]] + [n for n in b[0] if n not in a[0]]
    return f"nodes±={[n.get('key') for n in nd][:6]} calls_eq={a[1] == b[1]} imports_eq={a[2] == b[2]}"


# ---- gates -----------------------------------------------------------------
def gate_cold_warm_equals_full(S, EC) -> None:
    print("\n-- incremental == full (cold + warm cache); warm cache skips parsing --")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_tree(root, TREE_1)
        full = S._rebuild_code_ast(root)

        # cold cache: no .deepmap/extract_cache.json yet -> every file a miss
        check("no cache file before first incremental run", not EC.cache_path(root).exists())
        inc1 = S._rebuild_code_ast_incremental(root)
        check("incremental (cold) == full", _equal_tuples(inc1, full), _diff(inc1, full))
        check("cache file written after first run", EC.cache_path(root).exists())

        # warm cache: identical tree -> every file a hit, == full, and extract_one NOT called
        inc2 = S._rebuild_code_ast_incremental(root)
        check("incremental (warm) == full", _equal_tuples(inc2, full), _diff(inc2, full))

        # prove the warm run reuses the cache WITHOUT re-parsing (the actual speed win)
        prev = EC.load_cache(root, extractor="ast")
        files = [(p, rel, p.read_bytes()) for p, rel in S._iter_ast_files(root)]
        calls_seen = {"n": 0}

        def _counting_extract(py, rel):
            calls_seen["n"] += 1
            return S._extract_ast_file(py, rel)

        n, c, i, nf, stats = EC.incremental_extract(files, prev.get("files", {}), _counting_extract)
        check("warm cache -> 0 files re-parsed (extract_one never called)", calls_seen["n"] == 0,
              f"reparsed={calls_seen['n']}")
        check("warm stats: all hits, no misses", stats["misses"] == 0 and stats["hits"] == stats["total"],
              str(stats))
        check("direct incremental_extract == full too", _equal_tuples((n, c, i), full), _diff((n, c, i), full))


def gate_add_change_delete_move(S) -> None:
    print("\n-- ADD / CHANGE / DELETE / MOVE each reflected (== full of the mutated tree) --")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_tree(root, TREE_1)
        S._rebuild_code_ast_incremental(root)   # warm the cache on state 1

        # ADD a new file
        _write_tree(root, {"pkg/c.py": "def gamma():\n    helper()\n"})
        inc = S._rebuild_code_ast_incremental(root)
        full = S._rebuild_code_ast(root)
        check("ADD file reflected (incremental == full)", _equal_tuples(inc, full), _diff(inc, full))
        check("added file's node present", any(n["key"].startswith("pkg/c.py::") for n in inc[0]))

        # CHANGE an existing file (new function + new call)
        (root / "pkg" / "b.py").write_text(
            "def helper():\n    return 43\n\ndef beta():\n    alpha()\n\ndef delta():\n    beta()\n",
            encoding="utf-8")
        inc = S._rebuild_code_ast_incremental(root)
        full = S._rebuild_code_ast(root)
        check("CHANGE file reflected (incremental == full)", _equal_tuples(inc, full), _diff(inc, full))
        check("changed file's new symbol present", any(n["name"] == "delta" for n in inc[0]))

        # DELETE a file
        (root / "top.py").unlink()
        inc = S._rebuild_code_ast_incremental(root)
        full = S._rebuild_code_ast(root)
        check("DELETE file reflected (incremental == full)", _equal_tuples(inc, full), _diff(inc, full))
        check("deleted file's nodes gone", not any(n["key"].startswith("top.py::") for n in inc[0]))

        # MOVE a file (delete a.py, create moved_a.py with the SAME content -> new keys, keyed by rel)
        moved_src = (root / "pkg" / "a.py").read_text(encoding="utf-8")
        (root / "pkg" / "a.py").unlink()
        (root / "pkg" / "moved_a.py").write_text(moved_src, encoding="utf-8")
        inc = S._rebuild_code_ast_incremental(root)
        full = S._rebuild_code_ast(root)
        check("MOVE file reflected (incremental == full)", _equal_tuples(inc, full), _diff(inc, full))
        check("moved file re-keyed to the new path (cache is rel-keyed, not content-only)",
              any(n["key"].startswith("pkg/moved_a.py::") for n in inc[0])
              and not any(n["key"].startswith("pkg/a.py::") for n in inc[0]))


def gate_graph_identity_push(S) -> None:
    """The graph is identical at the push layer: feed full and incremental (nodes,calls,imports) to
    push_to_neo4j with a capturing fake session, and assert the emitted Cypher+params (node MERGEs
    with confidence, CALLS edges with confidence, prunes, typed/imports) match exactly — i.e. same
    node keys, same edges, same confidence."""
    print("\n-- graph identity at the push layer (same keys/edges/confidence) --")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_tree(root, TREE_1)
        S._rebuild_code_ast_incremental(root)   # cold -> writes cache
        inc = S._rebuild_code_ast_incremental(root)  # warm (all hits)
        full = S._rebuild_code_ast(root)

        def _capture(nodes, calls, imports):
            ops = []

            class _Sess:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def run(self, cypher, **p):
                    ops.append((cypher, json.dumps(p, sort_keys=True, default=str)))
                    return []

                def begin_transaction(self):   # the edge rebuild runs inside a write transaction
                    return self

                def commit(self):
                    pass

            class _Drv:
                def session(self):
                    return _Sess()

            S.push_to_neo4j(_Drv(), nodes, calls, imports)
            return ops

        ops_full = _capture(*full)
        ops_inc = _capture(*inc)
        # order-independent multiset compare (push order is deterministic anyway, but be robust)
        from collections import Counter
        cf, ci = Counter(ops_full), Counter(ops_inc)
        check("push emits the SAME Cypher+params for incremental as for full",
              cf == ci,
              f"full={len(ops_full)} inc={len(ops_inc)} only_in_full={sum((cf - ci).values())} "
              f"only_in_inc={sum((ci - cf).values())}")
        node_merges = [o for o in ops_inc if "MERGE (x:" in o[0]]
        edge_merges = [o for o in ops_inc if "MERGE (a)-[r:CALLS]->(b)" in o[0]]
        check("node MERGEs carry confidence (EXTRACTED)", node_merges
              and all('"conf": "EXTRACTED"' in o[1] for o in node_merges), str(len(node_merges)))
        check("CALLS edges emitted with a confidence grade", bool(edge_merges), str(len(edge_merges)))


def gate_cache_tolerant(S, EC) -> None:
    print("\n-- a cold/corrupt/version-mismatched cache degrades to full (never wrong) --")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_tree(root, TREE_1)
        full = S._rebuild_code_ast(root)

        # corrupt cache -> load_cache returns empty -> full extraction, correct result
        cp = EC.cache_path(root)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("{ this is not json", encoding="utf-8")
        check("load_cache tolerates a corrupt file (empty files map)",
              EC.load_cache(root, extractor="ast") == {"files": {}})
        inc = S._rebuild_code_ast_incremental(root)
        check("incremental with a corrupt prior cache == full", _equal_tuples(inc, full), _diff(inc, full))

        # version mismatch -> discarded
        cp.write_text(json.dumps({"version": 999, "extractor": "ast",
                                  "files": {"pkg/a.py": {"hash": "deadbeef", "nodes": [{"key": "poison"}],
                                                         "calls": [], "imports": []}}}),
                      encoding="utf-8")
        check("version mismatch -> empty (no poison entry reused)",
              EC.load_cache(root, extractor="ast") == {"files": {}})
        inc = S._rebuild_code_ast_incremental(root)
        check("incremental with a version-mismatched cache == full", _equal_tuples(inc, full), _diff(inc, full))
        check("no poison node leaked into the graph", not any(n.get("key") == "poison" for n in inc[0]))

        # extractor mismatch -> discarded (an ast cache must not be reused for another engine)
        cp.write_text(json.dumps({"version": EC.CACHE_VERSION, "extractor": "graphify", "files": {}}),
                      encoding="utf-8")
        check("extractor mismatch -> empty", EC.load_cache(root, extractor="ast") == {"files": {}})


def gate_no_cache_poison(S, EC) -> None:
    """An unreadable file must NOT collide with a zero-byte file (both would otherwise 'hash' the
    same and a later real file could reuse the wrong empty extraction). The _UNREADABLE sentinel
    guarantees an unreadable file always misses and can never share a stored hash with real bytes."""
    print("\n-- unreadable vs empty file: no hash collision / cache poisoning --")
    empty_hash = EC.hash_bytes(b"")
    check("zero-byte file has a real hex hash", len(empty_hash) == 16 and empty_hash != EC._UNREADABLE)
    # simulate: file A came in as unreadable (data=None) then later as real content
    files_run1 = [(Path("a.py"), "a.py", None)]
    _n, _c, _i, nf1, st1 = EC.incremental_extract(files_run1, {}, lambda p, r: ([{"key": "a.py::module"}], [], []))
    check("unreadable file counts as a miss", st1["misses"] == 1)
    check("unreadable file stored with the sentinel hash (never a real digest)",
          nf1["a.py"]["hash"] == EC._UNREADABLE)
    # next run the file is a real zero-byte file -> must NOT reuse the sentinel entry
    files_run2 = [(Path("a.py"), "a.py", b"")]
    calls = {"n": 0}

    def _ex(p, r):
        calls["n"] += 1
        return ([{"key": "a.py::module", "label": "Module"}], [], [])

    _n, _c, _i, nf2, st2 = EC.incremental_extract(files_run2, nf1, _ex)
    check("a real file after an unreadable one is a MISS (re-extracted, not sentinel-reused)",
          st2["misses"] == 1 and calls["n"] == 1, str(st2))
    check("the now-readable file is stored with its real hash", nf2["a.py"]["hash"] == empty_hash)


def gate_dispatch_wiring(S) -> None:
    """The DEEPMAP_INCREMENTAL_SYNC flag routes the ast path through the incremental variant while
    producing output identical to the full path (incl. _maybe_exclude_tests + the (…, 'ast') tuple).
    Forces DEEPMAP_EXTRACTOR=ast so the graphify branch is skipped deterministically."""
    print("\n-- DEEPMAP_INCREMENTAL_SYNC dispatch (ast path only) --")
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_EXTRACTOR", "DEEPMAP_INCREMENTAL_SYNC")}
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_tree(root, TREE_1)
            os.environ["DEEPMAP_EXTRACTOR"] = "ast"

            os.environ["DEEPMAP_INCREMENTAL_SYNC"] = "0"
            check("_incremental_enabled False when off", not S._incremental_enabled())
            nodes_off, calls_off, imports_off, engine_off = S._rebuild_code(root)

            os.environ["DEEPMAP_INCREMENTAL_SYNC"] = "1"
            check("_incremental_enabled True when on", S._incremental_enabled())
            nodes_on, calls_on, imports_on, engine_on = S._rebuild_code(root)

            check("both resolve to the ast engine", engine_off == "ast" and engine_on == "ast",
                  f"{engine_off}/{engine_on}")
            check("_rebuild_code output identical with the flag on vs off",
                  nodes_on == nodes_off and calls_on == calls_off and imports_on == imports_off)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_key_indexes(IDX) -> None:
    print("\n-- node-key RANGE indexes (write-path speedup; disjoint from search/embed) --")
    labels = {lab for lab, _ in IDX.KEY_INDEXES}
    names = {nm for _, nm in IDX.KEY_INDEXES}
    check("5 key indexes, one per code label",
          len(IDX.KEY_INDEXES) == 5 and labels == {"Function", "Method", "Class", "Module", "Route"},
          str(labels))
    check("names are deepmap_key_<label>",
          names == {"deepmap_key_function", "deepmap_key_method", "deepmap_key_class",
                    "deepmap_key_module", "deepmap_key_route"}, str(names))
    vnames = {nm for _, nm in IDX.VECTOR_INDEXES}
    fnames = {nm for nm, _, _ in IDX.FTS_INDEXES}
    check("key indexes are DISJOINT from vector+FTS (search coherence untouched)",
          names.isdisjoint(vnames | fnames), str(names & (vnames | fnames)))

    # create_all_indexes issues one `CREATE INDEX ... ON (n.key)` per code label (fake session)
    captured = []

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **p):
            captured.append(cypher)
            return []

    class _Drv:
        def session(self):
            return _Sess()

    status = IDX.create_all_indexes(_Drv())
    key_creates = [c for c in captured if "ON (n.key)" in c]
    check("create_all_indexes issues 5 key-index CREATEs on (n.key)", len(key_creates) == 5,
          str(len(key_creates)))
    for label in ("Function", "Method", "Class", "Module", "Route"):
        check(f"key index CREATE for :{label}",
              any(f"FOR (n:{label})" in c and "ON (n.key)" in c for c in key_creates))
    check("status map reports every key index", all(n in status for n in names), str(names - set(status)))

    # wait_for_indexes must INCLUDE the key indexes in its expected set: a graph with the vector+FTS
    # indexes ONLINE but the key indexes MISSING must NOT be reported ready. A fake clock avoids a
    # real sleep.
    import types

    class _Clock:
        def __init__(self):
            self.t = 0.0

        def time(self):
            self.t += 0.4
            return self.t

        def sleep(self, *_):
            pass

    all_online = {nm: "ONLINE" for nm in (vnames | fnames | names)}
    without_keys = {nm: "ONLINE" for nm in (vnames | fnames)}

    def _wait_with(states):
        clock = _Clock()
        saved = IDX.time
        IDX.time = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
        try:
            class _S:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def run(self, cypher, **p):
                    return [dict(name=n, state=st) for n, st in states.items()]

            class _D:
                def session(self):
                    return _S()

            return IDX.wait_for_indexes(_D(), timeout=1)
        finally:
            IDX.time = saved

    check("wait_for_indexes True when key indexes are ONLINE too", _wait_with(all_online) is True)
    check("wait_for_indexes NOT satisfied when key indexes are MISSING (they're in the expected set)",
          _wait_with(without_keys) is False)


def gate_red_under_mutation(S, EC) -> None:
    """PROVE THE GATE HAS TEETH. A deliberately-broken incremental — one that reuses a cache entry by
    rel WITHOUT checking the content hash — must DIVERGE from a full extraction after a file changes.
    The real incremental (which DOES check the hash) must match. So a regression that drops the
    content-hash check would flip the equality gates above to RED."""
    print("\n-- RED-under-mutation: a broken (no hash-check) incremental diverges from full --")

    def _broken_incremental(root):
        prev = EC.load_cache(root, extractor="ast").get("files", {})
        nodes, calls, imports = [], [], []
        for py, rel in S._iter_ast_files(root):
            cached = prev.get(rel)
            if cached is not None:                       # BUG: reuse stale content, no hash check
                nodes.extend(cached.get("nodes") or [])
                calls.extend(tuple(x) for x in (cached.get("calls") or []))
                imports.extend(tuple(x) for x in (cached.get("imports") or []))
            else:
                fn, fc, fi = S._extract_ast_file(py, rel)
                nodes.extend(fn)
                calls.extend(fc)
                imports.extend(fi)
        return nodes, calls, imports

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_tree(root, TREE_1)
        S._rebuild_code_ast_incremental(root)            # warm cache on state 1
        # mutate a file's content (a body edit that changes its extracted nodes)
        (root / "pkg" / "b.py").write_text(
            "def helper():\n    return 999\n\ndef beta():\n    alpha()\n\ndef zeta():\n    pass\n",
            encoding="utf-8")
        full = S._rebuild_code_ast(root)

        broken = _broken_incremental(root)
        check("BROKEN incremental (no hash-check) DIVERGES from full (gate would go RED)",
              not _equal_tuples(broken, full),
              "broken matched full — the gate could not detect a stale-cache regression")

        real = S._rebuild_code_ast_incremental(root)
        check("REAL incremental (hash-checked) matches full after the change (gate GREEN)",
              _equal_tuples(real, full), _diff(real, full))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — incremental sync + node-key index gates (no Neo4j)")
    print("=" * 68)

    S = _load("dm_sync_inc", "sync.py")
    EC = _load("dm_extract_cache", "extract_cache.py")
    IDX = _load("dm_indexes_inc", "indexes.py")

    gate_cold_warm_equals_full(S, EC)
    gate_add_change_delete_move(S)
    gate_graph_identity_push(S)
    gate_cache_tolerant(S, EC)
    gate_no_cache_poison(S, EC)
    gate_dispatch_wiring(S)
    gate_key_indexes(IDX)
    gate_red_under_mutation(S, EC)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
