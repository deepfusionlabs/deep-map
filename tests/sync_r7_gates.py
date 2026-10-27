#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the round-7 sync internals (W6: build/sync.py outside the extractor region,
build/indexes.py).

  A  @overload (ast side, T4#4 / T4d-F7): the ast engine emitted one node per `@overload` stub
     (click's Context.invoke x3 — @846, @851, @853). A stub-bodied @overload def followed by a same-scope
     implementation is now skipped, so the one node is the implementation at its own line.
  B  Receiver-aware ast resolution (T3#5): `x.m()` recorded only `m`, so `re.search(...)` bound to the
     project's DeepMapRetriever.search and `subprocess.run(...)` to the project's `run`, both INFERRED.
     The ast extractor now records how each callee was reached (bare / self / module / name / expr) as
     private node metadata; resolution DROPS an edge Python scoping rules out (an external-module
     receiver; a bare call to another file's method) and grades an unverified receiver AMBIGUOUS. The
     (caller, callee) tuples and every caller without that metadata (the graphify path) are unchanged.
  C  Nested-def shadowing (T3#6): a bare call to a nested def (not a node on the graphify path) bound to
     another file's same-named def as INFERRED. When the caller's own scope chain defines the name, a
     cross-file target is graded AMBIGUOUS (a grade change only: CALLS in-degree is unchanged).
  D  Line-key embedding reuse (T7d-7): an ast node re-keyed by a line shift was re-embedded. Its
     embedding is now copied from the old node with the same label, file, name and embedding content
     hash (so generate_embeddings finds it current), before the stale prune deletes the old node.
  E  Wipe guard before the push + no engine-downgrade duplication (T7#11, T7d-1 lead): the blast
     radius was measured AFTER the push (a full re-key read as exactly 50% and pruned), and a forced or
     fallen-back ast run pushed its nodes next to graphify's before refusing the prune — every Python
     symbol twice. The census now runs before the push, stale nodes of files that were re-extracted or
     deleted do not count, and an engine downgrade refuses the PUSH (graph + manifest kept) unless
     --stale-cleanup --force-prune switches engines deliberately.
  F  Excluded keys + graph totals (T5#7): nodes DEEPMAP_EXCLUDE_TESTS / the vendor filter drop on
     purpose no longer count as blast radius, and a skipped prune records the graph's real totals in the
     manifest and says so in the verdict.
  G  Long paths (T10#14 / T10d-8): the ast walker used rglob, which silently drops a file whose path
     exceeds 259 characters on Windows (LongPathsEnabled=0). It now walks with the extended-length
     prefix and reports directories it cannot list.
  H  DEEPMAP_FTS_SHORT_NAME=1 (I2, default OFF): a `short_name` property (the bare `method` of
     `Class.method`) is written and indexed in deepmap_code_fts; OFF is byte-identical (statements and
     index definition), and a flip re-creates the index.
  I  Incremental cache semantics marker: a per-file cache entry written by an older ast extractor is
     not reused (the round-7 extraction differs), so incremental stays identical to a full rebuild.

Hermetic: temp dirs, fake drivers, stubbed index/embedding stages, no sockets, no Neo4j.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _load(name="sync"):
    spec = importlib.util.spec_from_file_location(f"dm_r7_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _quiet():
    return contextlib.ExitStack()


# =====================================================================================================
# A — @overload stubs (ast side)
# =====================================================================================================
_OVERLOAD_SRC = '''import typing
import typing as t
from typing import overload


class Context:
    @t.overload
    def invoke(self, cb: int) -> int: ...

    @t.overload
    def invoke(self, cb: str) -> str: ...

    def invoke(self, cb):
        return cb

    @overload
    async def fetch(self, x: int) -> int: ...

    async def fetch(self, x):
        return x


@overload
def parse(x: int) -> int: ...


@typing.overload
def parse(x: str) -> str:
    """A documented stub."""
    ...


def parse(x):
    return x


@overload
def only_stub(x: int) -> int: ...


@overload
def dispatched(x: int):
    return x + 1


def dispatched(x):
    return x


def caller():
    Context().invoke(1)
    parse(2)
'''


def _line_of(src: str, needle: str) -> int:
    for i, line in enumerate(src.splitlines(), 1):
        if needle in line:
            return i
    raise AssertionError(needle)


def gate_a_overload() -> None:
    print("\n-- A: @overload stubs are not nodes when an implementation follows (ast engine) --")
    S = _load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        py = _write(root, "core.py", _OVERLOAD_SRC)
        with contextlib.redirect_stderr(io.StringIO()):
            nodes, calls, _imports = S._extract_ast_file(py, "core.py")
    by_name: dict = {}
    for n in nodes:
        by_name.setdefault(n["name"], []).append(n)
    impl_invoke = _line_of(_OVERLOAD_SRC, "    def invoke(self, cb):")
    impl_parse = _line_of(_OVERLOAD_SRC, "def parse(x):")
    impl_fetch = _line_of(_OVERLOAD_SRC, "    async def fetch(self, x):")
    inv = by_name.get("Context.invoke", [])
    check("Context.invoke: ONE node (the two @t.overload stubs are skipped)", len(inv) == 1,
          [n["start_line"] for n in inv])
    check("Context.invoke's node is the implementation (its line, its key)",
          len(inv) == 1 and inv[0]["start_line"] == impl_invoke
          and inv[0]["key"] == f"core.py::Context.invoke:{impl_invoke}", inv)
    par = by_name.get("parse", [])
    check("parse: ONE node — @overload and @typing.overload (even with a docstring stub) skipped",
          len(par) == 1 and par[0]["start_line"] == impl_parse, [n["start_line"] for n in par])
    fet = by_name.get("Context.fetch", [])
    check("an async @overload stub is skipped too", len(fet) == 1 and fet[0]["start_line"] == impl_fetch,
          [n["start_line"] for n in fet])
    check("a lone @overload stub with no implementation after it is KEPT", len(by_name.get("only_stub", [])) == 1,
          by_name.get("only_stub"))
    check("an @overload def with a REAL body (a dispatch-library overload) is kept, not a stub",
          len(by_name.get("dispatched", [])) == 2, [n["start_line"] for n in by_name.get("dispatched", [])])
    edges = S._resolve_calls(nodes, calls)
    caller = next(n["key"] for n in nodes if n["name"] == "caller")
    to_parse = {k: v for k, v in edges.items() if k[0] == caller and k[1] == par[0]["key"]} if par else {}
    check("a call to parse binds its ONE implementation as INFERRED (was AMBIGUOUS across 3 stub nodes)",
          list(to_parse.values()) == ["INFERRED"], edges)
    # the direct _Extractor use (tests/confidence_gates.py drives it this way) skips stubs too
    ex = S._Extractor("core.py")
    ex.visit(ast.parse(_OVERLOAD_SRC))
    check("_Extractor.visit(Module) skips the stubs as well",
          sum(1 for n in ex.nodes if n["name"] == "Context.invoke") == 1,
          [n["name"] for n in ex.nodes])


# =====================================================================================================
# B — receiver-aware resolution on the ast path
# =====================================================================================================
_RECV_TREE = {
    "retr.py": "class DeepMapRetriever:\n    def search(self, q):\n        return q\n",
    "runner.py": "def run(cmd):\n    return cmd\n",
    "helpers.py": ("def helper():\n    return 1\n\n\nclass Tool:\n    def build(self):\n        return 2\n\n"
                   "    @staticmethod\n    def make():\n        return 3\n"),
    "svc.py": '''import re
import subprocess
import os.path
import numpy as np
from pathlib import Path
import helpers
from helpers import Tool

_PAT = re.compile("x")


class Svc:
    def a(self):
        re.search("x", "y")
        subprocess.run(["ls"])
        os.path.join("a", "b")
        np.array([1])
        Path.cwd()

    def b(self):
        _PAT.search("x")
        obj = make_obj()
        obj.search("q")
        self.helper2()
        helper()
        helpers.helper()
        Tool.make()
        build()

    def helper2(self):
        def inner():
            return 1
        inner()
        return 2

    def c(self):
        re = Thing()
        re.search("x")
''',
}


def _old_resolve_calls(S, nodes, calls):
    """The pre-round-7 _resolve_calls (default path, precise OFF, F8b OFF) — the byte-identity oracle for
    callers that carry no receiver metadata."""
    import edge_resolve
    name_to_keys = edge_resolve.build_name_index(nodes)
    key_to_file = {n["key"]: str(n.get("source_file", "")) for n in nodes}
    edges = {}
    for caller_key, callee in calls:
        if caller_key not in key_to_file:
            continue
        targets = [ck for ck in name_to_keys.get(callee, []) if ck != caller_key]
        econf = "AMBIGUOUS" if len(targets) > 1 else "INFERRED"
        for ck in targets:
            edges[(caller_key, ck)] = econf
    return edges


def gate_b_receivers() -> None:
    print("\n-- B: ast resolution is receiver-aware (external modules, bare->other file's method, unknowns) --")
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None,
                                                   DEEPMAP_DROP_APP_TO_TEST_CALLS=None):
        root = Path(td)
        for rel, text in _RECV_TREE.items():
            _write(root, rel, text)
        with contextlib.redirect_stderr(io.StringIO()):
            nodes, calls, _imports = S._rebuild_code_ast(root)
        key = {n["name"]: n["key"] for n in nodes if n["label"] != "Module"}
        edges = S._resolve_calls(nodes, calls)

    def e(a, b):
        return edges.get((key.get(a), key.get(b)))

    check("the (caller, callee) tuples still carry the plain callee name (enrich / graphify unchanged)",
          (key.get("Svc.a"), "search") in calls and (key.get("Svc.a"), "run") in calls, calls[:12])
    check("re.search(...) (import re: an external module) no longer binds DeepMapRetriever.search",
          e("Svc.a", "DeepMapRetriever.search") is None, e("Svc.a", "DeepMapRetriever.search"))
    check("subprocess.run(...) no longer binds the project's run()", e("Svc.a", "run") is None, e("Svc.a", "run"))
    check("_PAT.search / obj.search (receiver of unknown type) -> AMBIGUOUS, the edge is KEPT",
          e("Svc.b", "DeepMapRetriever.search") == "AMBIGUOUS", e("Svc.b", "DeepMapRetriever.search"))
    check("self.helper2() -> INFERRED (unchanged)", e("Svc.b", "Svc.helper2") == "INFERRED", e("Svc.b", "Svc.helper2"))
    check("helper() / helpers.helper() (a PROJECT module receiver) -> INFERRED",
          e("Svc.b", "helper") == "INFERRED", e("Svc.b", "helper"))
    check("Tool.make() (a class imported from a project module) -> INFERRED",
          e("Svc.b", "Tool.make") == "INFERRED", e("Svc.b", "Tool.make"))
    check("build() — a BARE call whose only candidate is another file's METHOD -> no edge",
          e("Svc.b", "Tool.build") is None, e("Svc.b", "Tool.build"))
    check("inner() — a bare call to a same-file function nested in a method (a :Method node) -> kept INFERRED",
          e("Svc.helper2", "Svc.inner") == "INFERRED", e("Svc.helper2", "Svc.inner"))
    check("re.search after `re = Thing()` (the alias is shadowed locally) -> AMBIGUOUS, kept",
          e("Svc.c", "DeepMapRetriever.search") == "AMBIGUOUS", e("Svc.c", "DeepMapRetriever.search"))

    # callers WITHOUT receiver metadata (graphify-style nodes, synthetic tuples) resolve exactly as before
    S = _load()
    fx_nodes, fx_calls = [], []
    for t in range(4):
        fx_nodes.append({"key": f"lib.py::t{t}:{t + 1}", "label": "Function", "name": f"t{t}",
                         "source_file": "lib.py", "start_line": t + 1, "body_summary": "x"})
    fx_nodes.append({"key": "lib.py::Svc.run:50", "label": "Method", "name": "Svc.run", "source_file": "lib.py",
                     "start_line": 50, "body_summary": "x"})
    fx_nodes.append({"key": "b.py::t1:9", "label": "Function", "name": "t1", "source_file": "b.py",
                     "start_line": 9, "body_summary": "x"})
    for i in range(12):
        k = f"app{i}.py::caller{i}:1"
        fx_nodes.append({"key": k, "label": "Function", "name": f"caller{i}", "source_file": f"app{i}.py",
                         "start_line": 1, "body_summary": "x"})
        fx_calls += [(k, f"t{i % 4}"), (k, "run"), (k, "Svc.run"), (k, "missing")]
    with _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_DROP_APP_TO_TEST_CALLS=None):
        new, old = S._resolve_calls(fx_nodes, fx_calls), _old_resolve_calls(S, fx_nodes, fx_calls)
    check("no receiver metadata -> _resolve_calls is byte-identical to the pre-round-7 resolver",
          list(new.items()) == list(old.items()), {k: (new.get(k), old.get(k)) for k in set(new) ^ set(old)})


# =====================================================================================================
# C — nested-def shadowing
# =====================================================================================================
def _g_node(key, name, sf, line=1, label="Function"):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line, "body_summary": name}


def gate_c_shadowing() -> None:
    print("\n-- C: a bare call to a name the caller's own scope defines is not INFERRED to another file --")
    S = _load()
    attach = getattr(S, "_attach_local_defs", None)
    check("sync._attach_local_defs exists", callable(attach))
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None):
        root = Path(td)
        _write(root, "tests/a_gates.py", "def _rec(x):\n    return x\n")
        _write(root, "tests/b_gates.py", "def gate_setup():\n    def _rec(x):\n        return x\n    _rec(1)\n")
        _write(root, "tests/c_use.py", "from tests.a_gates import _rec\n\n\ndef use():\n    _rec(2)\n")
        _write(root, "tests/d_meth.py", ("class K:\n    def _rec(self):\n        return 1\n\n\n"
                                          "def other():\n    _rec(3)\n"))
        _write(root, "tests/e_sib.py", ("def outer():\n    def _rec():\n        return 1\n    return _rec\n\n\n"
                                         "def sibling():\n    _rec(4)\n"))
        nodes = [_g_node("tests_a_gates__rec", "_rec", "tests/a_gates.py"),
                 _g_node("tests_b_gates_gate_setup", "gate_setup", "tests/b_gates.py", 1),
                 _g_node("tests_c_use_use", "use", "tests/c_use.py", 4),
                 # (no node for K._rec: its last segment would be a 2nd candidate and hide what is tested)
                 _g_node("tests_d_meth_other", "other", "tests/d_meth.py", 6),
                 _g_node("tests_e_sib_outer", "outer", "tests/e_sib.py", 1),
                 _g_node("tests_e_sib_sibling", "sibling", "tests/e_sib.py", 7)]
        calls = [("tests_b_gates_gate_setup", "_rec"), ("tests_c_use_use", "_rec"),
                 ("tests_d_meth_other", "_rec"), ("tests_e_sib_sibling", "_rec")]
        before = S._resolve_calls([dict(n) for n in nodes], calls)
        check("precondition: without scope data the nested-def call binds tests/a_gates.py::_rec INFERRED",
              before.get(("tests_b_gates_gate_setup", "tests_a_gates__rec")) == "INFERRED", before)
        work = [dict(n) for n in nodes]
        if callable(attach):
            with contextlib.redirect_stderr(io.StringIO()):
                attach(root, work, calls)
        edges = S._resolve_calls(work, calls)
        check("gate_setup's own nested _rec shadows: the cross-file edge is AMBIGUOUS (kept, same in-degree)",
              edges.get(("tests_b_gates_gate_setup", "tests_a_gates__rec")) == "AMBIGUOUS", edges)
        check("a file that IMPORTS _rec (no local def) keeps INFERRED",
              edges.get(("tests_c_use_use", "tests_a_gates__rec")) == "INFERRED", edges)
        check("a METHOD named _rec in the caller's file does not shadow a bare call (class scope) -> INFERRED",
              edges.get(("tests_d_meth_other", "tests_a_gates__rec")) == "INFERRED", edges)
        check("a def nested in a SIBLING function is out of scope -> INFERRED",
              edges.get(("tests_e_sib_sibling", "tests_a_gates__rec")) == "INFERRED", edges)
        check("the edge SET is unchanged — only grades moved (CALLS in-degree / graph arm unaffected)",
              set(edges) == set(before), set(edges) ^ set(before))
        # precise mode: a graphify-resolved cross-file KEY whose name the caller's scope defines
        with _env(DEEPMAP_PRECISE_CALLS="1"):
            pw = [dict(n) for n in nodes]
            # R11/R1: a graphify-RESOLVED callee is a MARKED key (as map_graph hands it over)
            pcalls = [("tests_b_gates_gate_setup", S.graphify_extract.call_key("tests_a_gates__rec"))]
            if callable(attach):
                with contextlib.redirect_stderr(io.StringIO()):
                    attach(root, pw, pcalls)
            pe = S._resolve_calls(pw, pcalls)
        check("precise mode: a resolved cross-file key shadowed by the caller's nested def -> AMBIGUOUS",
              pe.get(("tests_b_gates_gate_setup", "tests_a_gates__rec")) == "AMBIGUOUS", pe)


# =====================================================================================================
# D — line-key embedding reuse
# =====================================================================================================
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _EmbSess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.stmts.append((cypher, p))
        if self.drv.fail:
            raise RuntimeError("simulated outage")
        if "_embed_hash" in cypher and "RETURN" in cypher:
            return _Res(self.drv.rows)
        return _Res()


class _EmbDrv:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.stmts = rows, fail, []

    def session(self, *a, **k):
        return _EmbSess(self)


def gate_d_embedding_reuse() -> None:
    print("\n-- D: a re-keyed node (same file, name, summary) reuses its embedding instead of re-embedding --")
    S = _load()
    import embeddings as E
    reuse = getattr(S, "_reuse_rekeyed_embeddings", None)
    check("sync._reuse_rekeyed_embeddings exists", callable(reuse))
    if not callable(reuse):
        return

    def h(name, summ):
        return E.content_hash(E.compose_text({"name": name, "body_summary": summ}))

    existing = [
        {"k": "a.py::f:1", "ls": ["Function"], "sf": "a.py", "nm": "f", "h": h("f", "does f")},
        {"k": "a.py::g:5", "ls": ["Function"], "sf": "a.py", "nm": "g", "h": h("g", "old g")},
        {"k": "a.py::C.m:9", "ls": ["Method"], "sf": "a.py", "nm": "C.m", "h": h("C.m", "does m")},
        {"k": "a.py::keep:20", "ls": ["Function"], "sf": "a.py", "nm": "keep", "h": h("keep", "same")},
    ]
    new_nodes = [
        _g_node("a.py::f:3", "f", "a.py", 3) | {"body_summary": "does f"},          # shifted, same text
        _g_node("a.py::g:7", "g", "a.py", 7) | {"body_summary": "new g"},           # shifted, text changed
        _g_node("b.py::f:1", "f", "b.py", 1) | {"body_summary": "does f"},          # other file
        _g_node("a.py::C.m:12", "C.m", "a.py", 12, "Method") | {"body_summary": "does m"},
        _g_node("a.py::keep:20", "keep", "a.py", 20) | {"body_summary": "same"},    # key unchanged
    ]
    drv = _EmbDrv(existing)
    with contextlib.redirect_stderr(io.StringIO()):
        n = reuse(drv, new_nodes)
    writes = [(c, p) for c, p in drv.stmts if "SET" in c and "embedding" in c]
    pairs = sorted((r["new"], r["old"]) for _c, p in writes for r in (p.get("rows") or []))
    check("returns the number of embeddings reused (2)", n == 2, n)
    check("pairs: the shifted f and C.m take the old node's embedding; changed/other-file/unchanged don't",
          pairs == [("a.py::C.m:12", "a.py::C.m:9"), ("a.py::f:3", "a.py::f:1")], pairs)
    check("the copy is label-qualified (key-index seeks) and sets embedding + both hashes",
          all(("MATCH (x:" in c and "MATCH (o:" in c and "x.embedding=o.embedding" in c.replace(" ", "")
               and "_embed_hash" in c and "_content_hash" in c) for c, _p in writes), [c for c, _ in writes])
    with contextlib.redirect_stderr(io.StringIO()):
        n_fail = reuse(_EmbDrv(existing, fail=True), new_nodes)
    check("a database error degrades to 0 (never raises; generate_embeddings re-embeds as before)", n_fail == 0)


# =====================================================================================================
# E / F — wipe guard before the push, engine-downgrade push refusal, excluded keys, graph totals
# =====================================================================================================
class _WSess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.stmts.append(cypher)
        if "n.source_file AS sf" in cypher and "count(" in cypher:
            if self.drv.census_fail:
                raise RuntimeError("census query failed")
            return _Res(self.drv.census_rows)
        return _Res()


class _WDrv:
    def __init__(self, census_rows=(), census_fail=False):
        self.census_rows, self.census_fail, self.stmts = list(census_rows), census_fail, []

    def session(self, *a, **k):
        return _WSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _wire(S, nodes, engine, *, info=None, order=None, blast=(0, 0), graph_nodes=True, graph_total=None):
    order = order if order is not None else []
    Ext = getattr(S, "_Extraction", None)
    inf = dict({"requested": "auto", "resolved": engine, "fallback_reason": None}, **(info or {}))

    def _rebuild(root):
        order.append("extract")
        return Ext([dict(n) for n in nodes], [], [], engine, inf)

    def _push(driver, n, c, i, typed_edges=None):
        order.append("push")
        return (len(n), 0)

    def _blast(driver, live):
        order.append("census")
        return blast

    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = _push
    S._prune_blast_radius = _blast
    S._cleanup_stale = lambda d, live: order.append("prune")
    S._graph_has_nodes = lambda d: graph_nodes
    S._graph_node_count = lambda d: graph_total
    S._reuse_rekeyed_embeddings = lambda d, ns: order.append("reuse") or 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: order.append("create_indexes"),
                                wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(nodes)
    S._maybe_auto_tune = lambda root: None
    return order


def _proj(td, manifest=None, files=("a.py",)):
    root = Path(td) / "p"
    root.mkdir()
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    for f in files:
        _write(root, f, "def f():\n    pass\n")
    if manifest is not None:
        (root / ".deepmap").mkdir(parents=True, exist_ok=True)
        (root / ".deepmap" / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _manifest(root):
    p = Path(root) / ".deepmap" / "graph_build.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _run(S, root, drv, **kw):
    out = io.StringIO()
    with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
        ok = S.sync(root, driver=drv, **kw)
    return ok, out.getvalue()


def gate_e_guard_and_downgrade() -> None:
    print("\n-- E: the prune census runs BEFORE the push; an engine downgrade refuses the PUSH --")
    node = _g_node("a.py::f:1", "f", "a.py")

    # E1 — census before push
    S = _load()
    order = _wire(S, [node], "graphify", blast=(1, 10))
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"), DEEPMAP_EXTRACTOR=None):
        root = _proj(td, {"extractor": "graphify", "graph_engines": ["graphify"]})
        _run(S, root, _WDrv(), cleanup_stale=True)
    check("the blast-radius census runs BEFORE push_to_neo4j (it measured the post-push graph)",
          "census" in order and "push" in order and order.index("census") < order.index("push"), order)
    check("the prune itself still runs after the push, and embedding reuse runs before the prune",
          all(x in order for x in ("push", "reuse", "prune"))
          and order.index("push") < order.index("reuse") < order.index("prune"), order)

    # E2 — refined census: re-extracted files / deleted files do not count; a real partial extraction does
    def _case(census_rows, blast, *, files=("a.py",), census_fail=False, envs=None):
        S = _load()
        order = _wire(S, [node], "graphify", blast=blast)
        with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                       DEEPMAP_HOME=str(Path(td) / "home"),
                                                       DEEPMAP_EXTRACTOR=None, **(envs or {})):
            root = _proj(td, {"extractor": "graphify", "graph_engines": ["graphify"]}, files=files)
            ok, text = _run(S, root, _WDrv(census_rows, census_fail=census_fail), cleanup_stale=True)
            return ("prune" in order), text, _manifest(root), S

    pruned, text, _m, _s = _case([{"sf": "a.py", "c": 100}], (100, 100))
    check("a full RE-KEY (every stale node's file was re-extracted) prunes — no longer blocked or 50%-lucky",
          pruned, text[-300:])
    pruned, text, man, S5 = _case([{"sf": "b.py", "c": 60}], (60, 100), files=("a.py", "b.py"))
    check("60/100 stale nodes in a file that still exists but was NOT extracted -> prune SKIPPED",
          not pruned and "prune would" in text.lower(), text[-400:])
    pruned, text, _m, _s = _case([{"sf": "gone.py", "c": 80}], (80, 100))
    check("80/100 stale nodes whose files were DELETED -> prune runs (a real orphan set)", pruned, text[-300:])
    pruned, text, _m, _s = _case([{"sf": "b.py", "c": 60}], (60, 100), files=("a.py", "b.py"), census_fail=True)
    check("a failing census query keeps the original >50% refusal (fail safe)", not pruned, text[-300:])

    # F — deliberately excluded keys are exempt; a skipped prune records the graph totals
    pruned, text, _m, _s = _case([{"sf": "tests/test_x.py", "c": 70}], (70, 100),
                                 files=("a.py", "tests/test_x.py"), envs={"DEEPMAP_EXCLUDE_TESTS": "1"})
    check("F: DEEPMAP_EXCLUDE_TESTS — 70/100 stale TEST nodes are the intended delete -> prune runs", pruned,
          text[-300:])
    pruned, text, _m, _s = _case([{"sf": "vendor/lib.js", "c": 70}], (70, 100), files=("a.py", "vendor/lib.js"),
                                 envs={"DEEPMAP_INCLUDE_VENDOR": None})
    check("F: vendored nodes the default vendor filter drops -> exempt, prune runs", pruned, text[-300:])
    pruned, text, _m, _s = _case([{"sf": "vendor/lib.js", "c": 70}], (70, 100), files=("a.py", "vendor/lib.js"),
                                 envs={"DEEPMAP_INCLUDE_VENDOR": "1"})
    check("F: with DEEPMAP_INCLUDE_VENDOR=1 those nodes are NOT excluded -> the guard still refuses", not pruned,
          text[-300:])
    S = _load()
    order = _wire(S, [node], "graphify", blast=(60, 100), graph_total=161)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"), DEEPMAP_EXTRACTOR=None):
        root = _proj(td, {"extractor": "graphify", "graph_engines": ["graphify"]}, files=("a.py", "b.py"))
        ok, text = _run(S, root, _WDrv([{"sf": "b.py", "c": 60}]), cleanup_stale=True)
        man = _manifest(root)
    check("F: a skipped prune records the graph's real node total in the manifest (graph_nodes)",
          man.get("graph_nodes") == 161 and man.get("nodes") == 1, man)
    check("F: ... and why the prune was skipped", man.get("stale_cleanup_skipped") == "blast-radius", man)
    check("F: the verdict names the kept stale nodes and is not READY",
          man.get("verdict") == "DEGRADED" and any("STALE" in str(r) for r in (man.get("reasons") or [])), man)
    check("F: the printed counts show the graph total next to this run's",
          "161" in text and "graph holds" in text, text[-500:])

    # E3 — engine downgrade: the PUSH is refused (no duplicate ast copy), graph + manifest kept
    prior = {"extractor": "graphify", "graph_engines": ["graphify"], "verdict": "READY", "nodes": 50}

    def _down(*, requested="ast", info=None, **kw):
        S = _load()
        inf = dict({"requested": requested, "resolved": "ast",
                    "fallback_reason": None if requested == "ast" else "graphify crashed (exit 1)"},
                   **(info or {}))
        order = _wire(S, [node], "ast", info=inf, blast=(40, 100))
        with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                       DEEPMAP_HOME=str(Path(td) / "home"),
                                                       DEEPMAP_EXTRACTOR=requested if requested == "ast" else None):
            root = _proj(td, dict(prior))
            ok, text = _run(S, root, _WDrv(), **kw)
            return ok, text, order, _manifest(root), getattr(S, "last_verdict", "<missing>")

    ok, text, order, man, lv = _down(cleanup_stale=True)
    check("forced ast over a graphify graph: NOTHING is pushed (no second copy of every Python symbol)",
          "push" not in order and "prune" not in order, order)
    check("... sync returns True (non-fatal, graph preserved) and last_verdict is DEGRADED", ok is True and lv == "DEGRADED",
          (ok, lv))
    check("... the manifest still describes the kept graphify graph", man == prior, man)
    check("... the message says nothing was pushed, names DEEPMAP_EXTRACTOR=ast and --stale-cleanup --force-prune",
          "DOWNGRADE" in text and "DEEPMAP_EXTRACTOR=ast" in text and "--stale-cleanup --force-prune" in text
          and "nothing was pushed" in text.lower(), text[-600:])
    ok, text, order, man, lv = _down(requested="auto")
    check("auto FALLBACK over a graphify graph (no --stale-cleanup): push refused too, cause named",
          "push" not in order and "fell back" in text and "graphify crashed" in text, text[-500:])
    ok, text, order, man, lv = _down(force_prune=True)
    check("--force-prune WITHOUT --stale-cleanup does not switch engines (would leave both copies): refused",
          "push" not in order and "--stale-cleanup" in text, (order, text[-300:]))
    ok, text, order, man, lv = _down(cleanup_stale=True, force_prune=True)
    check("--stale-cleanup --force-prune: the deliberate engine switch pushes AND prunes",
          "push" in order and "prune" in order, order)
    check("... after it the manifest's graph_engines is ['ast']", man.get("graph_engines") == ["ast"], man)

    # the manifest says graphify but the graph is EMPTY (store wiped, .deepmap kept): no refusal, memory reset
    S = _load()
    order = _wire(S, [node], "ast", info={"requested": "ast", "resolved": "ast"}, graph_nodes=False)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"), DEEPMAP_EXTRACTOR="ast"):
        root = _proj(td, dict(prior))
        ok, text = _run(S, root, _WDrv())          # no --stale-cleanup: the reset must not ride on a prune
        man = _manifest(root)
    check("an EMPTY graph with a stale graphify manifest: the ast graph is pushed (never refused into emptiness)",
          "push" in order, order)
    check("... and the engine memory is reset to what the graph now holds (['ast'])",
          man.get("graph_engines") == ["ast"], man)


# =====================================================================================================
# G — long paths in the ast walker
# =====================================================================================================
def _old_iter(root: Path):
    out = []
    for py in sorted(p for p in root.rglob("*") if p.suffix.lower() == ".py"):
        if any(part in {"__pycache__", ".venv"} for part in py.parts):
            continue
        out.append(py.relative_to(root).as_posix())
    return out


def gate_g_long_paths() -> None:
    print("\n-- G: the ast walker reads >259-char paths and reports directories it cannot list --")
    S = _load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "tree"
        for rel in ("b.py", "A.py", "pkg/z.py", "pkg/Sub/y.PY", "pkg/__pycache__/c.py", ".venv/x.py",
                    "docs/readme.md", "pkg2/m.py"):
            _write(root, rel, "def f():\n    pass\n")
        got = [rel for _py, rel in S._iter_ast_files(root)]
        check("same files, same order as the previous rglob walk on a normal tree", got == _old_iter(root),
              (got, _old_iter(root)))
    if os.name != "nt":
        check("long-path fixture is Windows-only (MAX_PATH); POSIX has no 260-char limit", True)
    else:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "lp"
            _write(root, "pkg/ok.py", "def ok():\n    pass\n")
            pfx = "\\\\?\\"
            deep = pfx + str((root / "pkg").resolve())
            for i in range(10):
                deep += "\\" + ("d%02d_" % i) + "x" * 22
            os.makedirs(deep)
            with open(deep + "\\deep_mod.py", "w", encoding="utf-8") as fh:
                fh.write("def deep_fn():\n    return 1\n")
            check("precondition: the fixture file path is over 259 characters", len(deep) - 4 + 12 > 259)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rels = [rel for _py, rel in S._iter_ast_files(root)]
                nodes, _c, _i = S._rebuild_code_ast(root)
            deep_rel = [r for r in rels if r.endswith("deep_mod.py")]
            check("the >259-char file is enumerated (rglob dropped it silently)", len(deep_rel) == 1, rels)
            check("its rel path is the normal POSIX relpath (no \\\\?\\ prefix in keys)",
                  bool(deep_rel) and deep_rel[0].startswith("pkg/d00_") and "?" not in deep_rel[0], deep_rel)
            check("its function is extracted into the graph", any(n["name"] == "deep_fn" for n in nodes),
                  sorted(n["name"] for n in nodes))
            shutil.rmtree(pfx + str(Path(td).resolve() / "lp"), ignore_errors=True)
    # an unlistable directory is reported once (simulated through os.walk's onerror)
    S = _load()
    real_walk = os.walk

    def _walk(top, *a, **k):
        onerror = k.get("onerror")
        if onerror is not None:
            e = PermissionError(13, "Access is denied")
            e.filename = str(top) + os.sep + "locked"
            onerror(e)
        yield from real_walk(top, *a, **k)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "t"
        _write(root, "a.py", "def a():\n    pass\n")
        err = io.StringIO()
        os.walk = _walk
        try:
            with contextlib.redirect_stderr(err):
                rels = [rel for _py, rel in S._iter_ast_files(root)]
        finally:
            os.walk = real_walk
        text = err.getvalue()
        check("readable files are still yielded", rels == ["a.py"], rels)
        check("an unlistable directory is reported ONCE, naming it and that its files are missing",
              text.count("could not list") == 1 and "locked" in text and "not in the graph" in text, text)


# =====================================================================================================
# H — DEEPMAP_FTS_SHORT_NAME (index content, default OFF)
# =====================================================================================================
_OLD_UPSERT = ("UNWIND $rows AS row MERGE (x:{label} {{key:row.key}}) "
               "SET x.name=row.name, x.source_file=row.sf, x.start_line=row.line, "
               "x.body_summary=row.summ, x.confidence=row.conf")


class _CapSess:
    def __init__(self, cap, show=None, props=None, props_fail=False):
        self.cap, self.show, self.props, self.props_fail = cap, show or [], props, props_fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.cap.append((cypher, p))
        if "SHOW INDEXES" in cypher and "properties" in cypher:
            if self.props_fail:
                raise RuntimeError("no properties column")
            return _Res([{"properties": self.props}] if self.props is not None else [])
        if "SHOW INDEXES" in cypher:
            return _Res([{"name": n} for n in self.show])
        return _Res()


class _CapDrv:
    def __init__(self, **kw):
        self.cap, self.kw = [], kw

    def session(self, *a, **k):
        return _CapSess(self.cap, **self.kw)


def gate_h_short_name() -> None:
    print("\n-- H: DEEPMAP_FTS_SHORT_NAME — OFF byte-identical; ON writes + indexes short_name --")
    nodes = [_g_node("svc.py::Svc.run:3", "Svc.run", "svc.py", 3, "Method"),
             _g_node("svc.py::top:9", "top", "svc.py", 9),
             _g_node("svc.py::Odd.:12", "Odd.", "svc.py", 12, "Method")]
    for flag, label in ((None, "unset"), ("0", "0")):
        with _env(DEEPMAP_FTS_SHORT_NAME=flag):
            S = _load()
            drv = _CapDrv()
            with drv.session() as s:
                S._upsert_nodes(s, nodes)
        stmts = [c for c, _p in drv.cap]
        rows = [r for _c, p in drv.cap for r in (p.get("rows") or [])]
        check(f"OFF ({label}): the node upsert statements are byte-identical to before",
              sorted(stmts) == sorted({_OLD_UPSERT.format(label="Method"), _OLD_UPSERT.format(label="Function")}),
              stmts)
        check(f"OFF ({label}): the rows carry exactly the old fields",
              all(set(r) == {"key", "name", "sf", "line", "summ", "conf"} for r in rows), rows[:1])
    with _env(DEEPMAP_FTS_SHORT_NAME="1"):
        S = _load()
        drv = _CapDrv()
        with drv.session() as s:
            S._upsert_nodes(s, nodes)
    rows = {r["key"]: r for _c, p in drv.cap for r in (p.get("rows") or [])}
    check("ON: the upsert SETs x.short_name", all("x.short_name=row.short" in c for c, _p in drv.cap),
          [c for c, _ in drv.cap])
    check("ON: Svc.run -> short 'run'; an undotted name -> None (property removed); 'Odd.' -> None",
          rows.get("svc.py::Svc.run:3", {}).get("short") == "run"
          and rows.get("svc.py::top:9", {}).get("short", "x") is None
          and rows.get("svc.py::Odd.:12", {}).get("short", "x") is None, rows)

    I = _load("indexes")
    base = ["name", "body_summary", "docstring", "rationale"]
    check("indexes.FTS_INDEXES (the search-coherence constant) is unchanged",
          next(p for n, _l, p in I.FTS_INDEXES if n == "deepmap_code_fts") == base)
    fp = getattr(I, "fts_code_properties", None)
    check("indexes.fts_code_properties exists", callable(fp))
    if not callable(fp):
        return
    with _env(DEEPMAP_FTS_SHORT_NAME=None):
        check("fts_code_properties() OFF == the 4 properties", fp() == base, fp())
    with _env(DEEPMAP_FTS_SHORT_NAME="1"):
        check("fts_code_properties() ON == the 4 + short_name", fp() == base + ["short_name"], fp())

    def _ddl(**drvkw):
        I = _load("indexes")
        drv = _CapDrv(**drvkw)
        status = I.create_all_indexes(drv)
        code = [c for c, _p in drv.cap if "FULLTEXT" in c and "deepmap_code_fts" in c]
        drops = [c for c, _p in drv.cap if c.startswith("DROP INDEX")]
        return code, drops, status

    old_ddl = ("CREATE FULLTEXT INDEX deepmap_code_fts IF NOT EXISTS FOR (n:Function|Class|Module|Method|Route) "
               "ON EACH [n.name, n.body_summary, n.docstring, n.rationale]")
    with _env(DEEPMAP_FTS_SHORT_NAME=None):
        code, drops, status = _ddl()
        check("OFF, fresh graph: the code FTS DDL is byte-identical, nothing dropped",
              code == [old_ddl] and not drops, (code, drops))
        code, drops, status = _ddl(show=["deepmap_code_fts"], props=list(base))
        check("OFF, existing 4-property index: no DROP", not drops and code == [old_ddl], (code, drops))
        code, drops, status = _ddl(show=["deepmap_code_fts"], props=base + ["short_name"])
        check("OFF, an index left with short_name by an ON run: DROP + re-CREATE with the 4 properties",
              drops == ["DROP INDEX deepmap_code_fts IF EXISTS"] and code == [old_ddl]
              and status.get("deepmap_code_fts") == "recreated", (code, drops, status))
        code, drops, status = _ddl(show=["deepmap_code_fts"], props_fail=True)
        check("OFF, the properties can't be read: no DROP, no crash", not drops and code == [old_ddl], (code, drops))
    with _env(DEEPMAP_FTS_SHORT_NAME="1"):
        code, drops, status = _ddl(show=["deepmap_code_fts"], props=list(base))
        check("ON over a 4-property index: DROP + CREATE including n.short_name",
              drops == ["DROP INDEX deepmap_code_fts IF EXISTS"] and len(code) == 1 and "n.short_name" in code[0],
              (code, drops))
        code, drops, status = _ddl(show=["deepmap_code_fts"], props=base + ["short_name"])
        check("ON over an index that already has short_name: no DROP", not drops, drops)


# =====================================================================================================
# I — incremental cache written by an older ast extractor is not reused
# =====================================================================================================
def gate_i_cache_semantics() -> None:
    print("\n-- I: an ast extract cache from before round 7 is not reused (incremental == full) --")
    S = _load()
    import extract_cache as EC
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "core.py", _OVERLOAD_SRC)
        _write(root, "m.py", "import re\n\n\ndef g():\n    return re.search('a', 'b')\n")
        # an OLD-shape cache: same content hashes, but the entries hold the pre-round-7 extraction
        files = {}
        for py, rel in S._iter_ast_files(root):
            data = py.read_bytes()
            files[rel] = {"hash": EC.hash_bytes(data), "nodes": [{"key": f"{rel}::stale:1", "label": "Function",
                                                                  "name": "stale", "source_file": rel,
                                                                  "start_line": 1, "body_summary": "old"}],
                          "calls": [], "imports": []}
        EC.save_cache(root, extractor="ast", files=files)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            inc = S._rebuild_code_ast_incremental(root)
            full = S._rebuild_code_ast(root)
        check("incremental over a pre-round-7 cache == a full rebuild (old entries not reused)",
              list(inc[0]) == list(full[0]) and list(inc[1]) == list(full[1]), err.getvalue()[-200:])
        check("... every file was re-parsed once", "0/2 files reused" in err.getvalue(), err.getvalue()[-200:])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            inc2 = S._rebuild_code_ast_incremental(root)
        check("the rewritten cache is reused on the next run (2/2 hits) and still == full",
              "2/2 files reused" in err.getvalue() and list(inc2[0]) == list(full[0]), err.getvalue()[-200:])
        prev = EC.load_cache(root, extractor="ast")
        check("the cache keeps the plain extractor='ast' tag (existing readers unaffected)",
              all(r in prev.get("files", {}) for r in ("core.py", "m.py")), list(prev.get("files", {}))[:4])


# =====================================================================================================
# J — manifest fields: a skipped prune's graph totals + the round-7 engine-output counters
# =====================================================================================================
def gate_j_manifest_fields() -> None:
    print("\n-- J: write_graph_build_manifest records the skipped-prune totals and the round-7 counters --")
    S = _load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py", "def f():\n    pass\n")
        state = {"sha": None, "dirty": None, "dirty_paths_sample": [], "dirty_paths_count": 0,
                 "content_hash": "0123456789abcdef"}
        S.write_graph_build_manifest(
            root, n_nodes=5, n_edges=1, n_embedded=5, extractor="graphify", source_state=state,
            extraction={"requested": "auto", "calls_filtered": 3, "overloads_moved": 0, "calls_plus": True,
                        "supplemental_nodes": "7"},
            stale_cleanup_skipped="blast-radius", graph_nodes=12)
        m = _manifest(root)
        check("stale_cleanup_skipped + graph_nodes are recorded; nodes stays this run's count",
              m.get("stale_cleanup_skipped") == "blast-radius" and m.get("graph_nodes") == 12 and m.get("nodes") == 5, m)
        check("a non-zero round-7 counter is recorded as graphify_<counter>",
              m.get("graphify_calls_filtered") == 3 and m.get("graphify_supplemental_nodes") == 7, m)
        check("a zero / non-count counter is left out (no graphify_overloads_moved / graphify_calls_plus)",
              "graphify_overloads_moved" not in m and "graphify_calls_plus" not in m, m)
        S.write_graph_build_manifest(root, n_nodes=5, n_edges=1, n_embedded=5, extractor="graphify",
                                     source_state=state)
        m = _manifest(root)
        check("without a skipped prune neither field is written", "graph_nodes" not in m
              and "stale_cleanup_skipped" not in m, m)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-7 sync internals gates (W6; no Neo4j)")
    print("=" * 72)
    home = tempfile.mkdtemp(prefix="dm_syncr7_home_")
    with _env(DEEPMAP_HOME=home, DEEPMAP_EXTRACTOR=None, DEEPMAP_INCREMENTAL_SYNC=None,
              DEEPMAP_PRECISE_CALLS=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None,
              DEEPMAP_EXCLUDE_TESTS=None, DEEPMAP_INCLUDE_VENDOR=None, DEEPMAP_FTS_SHORT_NAME=None,
              DEEPMAP_DROP_APP_TO_TEST_CALLS=None, DEEPMAP_AUTO_GITIGNORE="0"):
        for gate in (gate_a_overload, gate_b_receivers, gate_c_shadowing, gate_d_embedding_reuse,
                     gate_e_guard_and_downgrade, gate_g_long_paths, gate_h_short_name, gate_i_cache_semantics,
                     gate_j_manifest_fields):
            try:
                gate()
            except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
