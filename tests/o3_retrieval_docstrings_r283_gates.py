#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0.28.3 (O3) — build/retrieval.py's docstrings state what the code does; the `impact` re-lock is docstring-only.

ISS-build:019 (C5): the module docstring gave arm 3 as "vector x log(1 + inDegree)", but both Cypher variants of
`_arm_graph_weighted_one` compute `score * (1.0 + log(1.0 + toFloat(inDegree)))`. Read literally, the docstring made an
uncalled node (inDegree 0) score 0; the code keeps its vector score (1 + log(1) = 1). The docstring now states the
code's formula, vector score × (1 + log(1 + inDegree)).

ISS-build:018 (C6), the retrieval part: `DeepMapRetriever.impact`'s docstring said impact "degrades to one-hop :CALLS on
a pre-Track-C graph". It does not. `query_impact` narrows the walk to the edge types the graph holds (CALLS is always
kept) and still walks `*1..max_hops` (default 3); its own docstring says a pre-Track-C graph "collapses to CALLS-only
multi-hop". Only error paths drop to one hop: an unsupported-feature error or a server-side timeout falls back to
one-hop query_callers, and a transaction-memory error to a 1-hop walk. Section B drives that behavior through a fake
driver first, so the text checks are anchored to what the code does, not to a phrase.

`impact` is in tests/fts_r7_gates.py's hash lock (`_FROZEN`), so the fix is a deliberate RE-LOCK of that one entry.
The proof that the change is docstring-only never reads git (a depth-1 CI checkout has no history, and `git show HEAD:`
becomes the fixed text once committed). The pre-fix segment is pinned below, and its sha256[:16] must equal the old
lock value. The current segment, with its docstring stripped, must be AST-identical to it and byte-identical outside
the docstring. fts_r7's table may differ from the v0.27.4 base in that one entry only.

Hermetic: a recording fake driver; no Neo4j, no network, no git.
Usage: python tests/o3_retrieval_docstrings_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

# `DeepMapRetriever.impact`'s source segment (ast.get_source_segment, CRLF-normalised) as fts_r7 locked it at the
# v0.27.4 base 9c8d50bce, unchanged through 37230dd85 (v0.28.1). Pinned so the docstring-only proof needs no git.
_IMPACT_BEFORE = "\n".join([
    'def impact(self, symbol: str, limit: int = 30, edge_types=None) -> list[dict]:',
    '        """What REACHES `symbol` — multi-hop reverse reachability across :CALLS + framework/runtime',
    '        edges (routes/DI/events/jobs/inheritance), each hop confidence-graded. See query_impact;',
    '        degrades to one-hop :CALLS on a pre-Track-C graph or an unsupported-feature error.',
    '',
    '        `edge_types` (optional) overrides the traversed edge set for this call — e.g. add the ORM',
    '        associations HAS_MANY/REFERENCES for a Rails/ORM blast radius. Default None resolves from',
    '        DEEPMAP_IMPACT_EDGES / DEEPMAP_IMPACT_PROFILE / the frozen default set."""',
    '        return query_impact(self.driver, symbol, limit, edge_types=edge_types)',
])
_IMPACT_BEFORE_HASH = "3b8b1a9f0e476f71"

# tests/fts_r7_gates.py `_FROZEN` at the v0.27.4 base, before this re-lock (sha256[:16] of each AST source segment).
_BASE_FROZEN = {
    "DeepMapRetriever.__init__": "83778256c6d5dbb6", "DeepMapRetriever.impact": "3b8b1a9f0e476f71",
    "DeepMapRetriever._embed_or_none": "c9099cee93560d20", "DeepMapRetriever.search": "6b56bebdf189c474",
    "DeepMapRetriever._arm_vector": "ba935c518e31c19e", "DeepMapRetriever._arm_vector_search": "e97472fb11d9e197",
    "DeepMapRetriever._arm_vector_procedure": "1a36097f347db2b5",
    "DeepMapRetriever._arm_fulltext": "8a371f7f01c1031d", "DeepMapRetriever._arm_graph_weighted": "bde632e1e442e77a",
    "DeepMapRetriever._arm_graph_weighted_one": "7d2b037814eced42", "DeepMapRetriever._hydrate": "2e609792337998dd",
    "RRF_K": "da4cb420ff7ad1bd", "_candidate_floor": "171a91847a4687fd", "_is_fatal_db_error": "e4e5e5366619cfce",
    "_LUCENE_SPECIAL": "973253f4b6d36996", "_is_missing_index_error": "af1ea7989271f91a",
    "_is_fts_parse_error": "525b06f56ca9ffe0", "_weighted_rrf": "e8a914f677a25b69",
    "VECTOR_INDEXES": "35b6fc04a3df18aa", "DEFAULT_ARM_WEIGHTS": "05f8eca77cb90625",
}
_RELOCKED = "DeepMapRetriever.impact"
_RELOCK_NOTE = "docstring corrected 2026-09-29; behavior identical (AST equal with docstring stripped)"
_IMPACT_ENV = ("DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_MAX_HOPS", "DEEPMAP_IMPACT_TIMEOUT_S")
_WALK = "MATCH path = (caller)"                      # query_impact's multi-hop walk
_ONE_HOP = "MATCH (caller)-[r:CALLS]->(callee)"      # query_callers, the one-hop fallback


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _h16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _flat(text) -> str:
    return " ".join((text or "").split())


def _clauses(doc, word: str) -> list:
    """The clauses (split after '.' / ';') of the flattened docstring that mention `word`."""
    return [c for c in re.split(r"(?<=[.;])\s+", _flat(doc)) if word in c]


def _method(tree: ast.Module, cls: str, name: str):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return next((m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == name), None)
    return None


def _function(tree: ast.Module, name: str):
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _docstring_node(fn):
    first = fn.body[0] if fn.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first
    return None


def _ast_without_docstring(segment: str) -> str:
    """ast.dump of the segment's function with its docstring statement removed (same interpreter on both sides, so
    the comparison holds on every Python version)."""
    fn = copy.deepcopy(ast.parse(segment).body[0])
    if _docstring_node(fn) is not None:
        fn.body = fn.body[1:]
    return ast.dump(fn)


def _text_outside_docstring(segment: str) -> str:
    """The segment's text with the docstring literal cut out: the def line, the indentation and the body stay."""
    fn = ast.parse(segment).body[0]
    doc = _docstring_node(fn)
    return segment if doc is None else segment.replace(ast.get_source_segment(segment, doc), "", 1)


def _load(root: Path):
    path = root / "build" / "retrieval.py"
    spec = importlib.util.spec_from_file_location("_dm_retrieval_o3_r283", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    return mod


@contextlib.contextmanager
def _default_walk_env():
    """The default walk (no edge override / profile / depth / timeout from the environment) for the duration."""
    saved = {k: os.environ.pop(k, None) for k in _IMPACT_ENV}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# ---- a recording fake driver ----------------------------------------------------------------------------
class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, parameters=None, **params):
        text = getattr(cypher, "text", cypher)
        self.drv.log.append(text)
        if "db.relationshipTypes" in text:
            return [{"types": list(self.drv.present)}]
        if "RETURN count(callee) AS matched" in text:
            return [{"matched": 1}]
        if _WALK in text:
            if self.drv.walk_errors:
                raise self.drv.walk_errors.pop(0)
            return [dict(r) for r in self.drv.walk_rows]
        if _ONE_HOP in text:
            return [dict(r) for r in self.drv.caller_rows]
        return []    # the constructor's SEARCH-clause probe


class _Driver:
    def __init__(self, present=("CALLS",), walk_errors=()):
        self.present = present
        self.walk_errors = list(walk_errors)
        self.walk_rows = [
            {"caller": "a", "caller_type": "Function", "file": "a.py", "line": 1, "confidence": "INFERRED",
             "callee": "target", "hop": 1, "via": "CALLS", "via_path": ["CALLS"]},
            {"caller": "b", "caller_type": "Function", "file": "b.py", "line": 2, "confidence": "INFERRED",
             "callee": "target", "hop": 2, "via": "CALLS", "via_path": ["CALLS", "CALLS"]}]
        self.caller_rows = [{"caller": "a", "caller_type": "Function", "file": "a.py", "line": 1,
                             "confidence": "INFERRED", "callee": "target"}]
        self.log: list = []

    def session(self, **kw):
        return _Session(self)

    def walks(self) -> list:
        return [t for t in self.log if _WALK in t]

    def one_hops(self) -> list:
        return [t for t in self.log if _ONE_HOP in t]


class _MemoryRefusal(Exception):
    """Neo4j refusing a query for transaction memory (what retrieval._is_memory_error reads: the `code`)."""
    code = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"


def _impact(R, drv):
    with _default_walk_env():
        return R.DeepMapRetriever(drv, embed_fn=lambda q: [0.0] * 768).impact("target")


# ---------------------------------------------------------------------------------------------------------
def gate_graph_weighted_formula(src: str, tree: ast.Module) -> None:
    print("\n-- A (ISS-build:019): the module docstring gives arm 3's formula as the code computes it --")
    one = _method(tree, "DeepMapRetriever", "_arm_graph_weighted_one")
    seg = ast.get_source_segment(src, one) if one else ""
    code = re.findall(r"\b(\w+) \* \(1\.0 \+ log\(1\.0 \+ toFloat\(inDegree\)\)\) AS score", seg)
    check("the code: both Cypher variants of _arm_graph_weighted_one score = <vector score> * (1 + log(1 + inDegree))",
          sorted(code) == ["rawScore", "score"], str(code))
    doc = _flat(ast.get_docstring(tree))
    check("the module docstring states that formula: vector score × (1 + log(1 + inDegree))",
          re.search(r"(?:×|\*|\bx)\s*\(\s*1\s*\+\s*log\(\s*1\s*\+\s*inDegree\s*\)\s*\)", doc) is not None,
          next(iter(_clauses(doc, "inDegree")), doc[:200]))
    check("…and no longer the multiplier-free 'x log(1 + inDegree)' (which scores an uncalled node 0)",
          re.search(r"(?:×|\*|\bx)\s*log\(\s*1\s*\+\s*inDegree\s*\)", doc) is None,
          next(iter(_clauses(doc, "inDegree")), ""))


def gate_impact_depth(R, tree: ast.Module) -> None:
    print("\n-- B (ISS-build:018): impact() on a pre-Track-C graph is multi-hop; only error paths drop to one hop --")
    drv = _Driver(present=("CALLS",))
    rows = _impact(R, drv)
    walks = drv.walks()
    check("behavior: a graph holding only :CALLS is walked over :CALLS alone, multi-hop (*1..3), in one walk",
          len(walks) == 1 and "[rels:CALLS*1..3]" in walks[0], str([w[w.find("MATCH path"):][:70] for w in walks]))
    check("…never through the one-hop query_callers fallback; the answer reports depth 3 and a 2-hop dependent",
          not drv.one_hops() and getattr(rows, "max_hops", None) == 3 and any(r.get("hop") == 2 for r in rows),
          f"one_hop={len(drv.one_hops())} max_hops={getattr(rows, 'max_hops', None)}")
    from neo4j.exceptions import ClientError
    drv = _Driver(present=("CALLS",), walk_errors=[ClientError("SyntaxError: variable-length not supported")])
    rows = _impact(R, drv)
    check("behavior: an unsupported-feature error (an error path) degrades to one-hop :CALLS callers, said in a note",
          len(drv.one_hops()) == 1 and getattr(rows, "max_hops", None) == 1
          and any("(1 hop)" in n for n in getattr(rows, "notes", [])), f"notes={getattr(rows, 'notes', None)}")
    drv = _Driver(present=("CALLS",), walk_errors=[_MemoryRefusal("out of memory")])
    rows = _impact(R, drv)
    walks = drv.walks()
    check("behavior: a transaction-memory refusal (an error path) is answered by a 1-hop walk (*1..1)",
          len(walks) == 2 and "*1..1]" in walks[1] and getattr(rows, "max_hops", None) == 1,
          str([w[w.find("MATCH path"):][:70] for w in walks]))

    fn = _method(tree, "DeepMapRetriever", "impact")
    doc = ast.get_docstring(fn) if fn else ""
    about = _clauses(doc, "pre-Track-C")
    check("impact()'s docstring still describes the pre-Track-C case", bool(about), _flat(doc)[:240])
    check("…and no longer says a pre-Track-C graph gets one hop (the stale claim)",
          bool(about) and not any(re.search(r"one[- ]hop", c) for c in about), str(about))
    check("…it says the pre-Track-C walk is multi-hop over :CALLS, as the behavior above shows",
          bool(about) and all("multi-hop" in c and "CALLS" in c for c in about), str(about))
    check("…and that an error path is what drops a walk to one hop",
          any(re.search(r"one[- ]hop", c) and "error" in c for c in re.split(r"(?<=[.;])\s+", _flat(doc))),
          _flat(doc)[:320])
    qi = _function(tree, "query_impact")
    qi_about = _clauses(ast.get_docstring(qi) if qi else "", "pre-Track-C")
    check("query_impact's own docstring agrees (a pre-Track-C graph collapses to CALLS-only multi-hop)",
          bool(qi_about) and all("multi-hop" in c and "CALLS" in c for c in qi_about), str(qi_about))


def gate_relock_is_docstring_only(src: str, tree: ast.Module, root: Path) -> None:
    print("\n-- C: the fts_r7 re-lock of DeepMapRetriever.impact is docstring-only, and the only re-lock --")
    check("the pinned pre-fix segment is the one fts_r7 locked (sha256[:16] == 3b8b1a9f0e476f71)",
          _h16(_IMPACT_BEFORE) == _IMPACT_BEFORE_HASH, _h16(_IMPACT_BEFORE))
    fn = _method(tree, "DeepMapRetriever", "impact")
    now = ast.get_source_segment(src, fn) if fn else ""
    check("impact() with its docstring stripped is AST-identical to the pre-fix segment (behavior identical)",
          bool(now) and _ast_without_docstring(now) == _ast_without_docstring(_IMPACT_BEFORE))
    check("…and byte-identical outside the docstring (signature, indentation, body)",
          bool(now) and _text_outside_docstring(now) == _text_outside_docstring(_IMPACT_BEFORE),
          repr(_text_outside_docstring(now))[:200] if now else "impact() not found")
    fts_path = root / "tests" / "fts_r7_gates.py"
    fts_src = _read(fts_path)
    node = next((n for n in ast.parse(fts_src).body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "_FROZEN" for t in n.targets)), None)
    frozen = ast.literal_eval(node.value) if node is not None else {}
    check("fts_r7's lock still covers the same 20 symbols", set(frozen) == set(_BASE_FROZEN),
          str(sorted(set(frozen) ^ set(_BASE_FROZEN))))
    moved = sorted(k for k in _BASE_FROZEN if frozen.get(k) != _BASE_FROZEN[k])
    check("fts_r7 re-locked DeepMapRetriever.impact and nothing else (19 entries keep their v0.27.4 base values)",
          moved == [_RELOCKED], str(moved))
    check("…to the sha256[:16] of the corrected segment", bool(now) and frozen.get(_RELOCKED) == _h16(now),
          f"lock={frozen.get(_RELOCKED)} now={_h16(now) if now else None}")
    lines = fts_src.split("\n")
    at = next((i for i, ln in enumerate(lines) if f'"{_RELOCKED}":' in ln), None)
    near = " ".join(lines[max(0, at - 3):at + 1]) if at is not None else ""
    check("…with the re-lock note beside the entry", _RELOCK_NOTE in _flat(near.replace("#", " ")),
          near[:240] or "entry not found")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — retrieval docstrings match the code; the impact re-lock is docstring-only (root: {root})")
    print("=" * 72)
    src = _read(root / "build" / "retrieval.py")
    tree = ast.parse(src)
    gate_graph_weighted_formula(src, tree)
    gate_impact_depth(_load(root), tree)
    gate_relock_is_docstring_only(src, tree, root)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
