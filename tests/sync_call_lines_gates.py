#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: the :CALLS push carries the CALL-SITE line (`r.line`), display-only.

A caller's private `call_lines` {"key": {target key: line}, "name": {callee name: line}} (map_graph on the
graphify path, _Extractor on the ast path, the opt-in typed pass) becomes `r.line` on its :CALLS edges, so
deepmap_impact can say where the call is (`call_line`) instead of only where the caller starts. The edges, their
grades and the CALLS in-degree must not move: the line is set AFTER the MERGE, never part of its pattern, and a
push with no line at all sends exactly the rows it sent before.

  A  sync._call_lines (pure): key before full name before the last "." segment before the last "::" segment; a
     name fan-out gives every target the line; the line survives _unkey_receiver_calls (a precise key turned
     back into a name); the output holds plain str keys, never a CallKey; bool / 0 / negative / str / float /
     None values are ignored (the next lookup is tried); {} when no node has the metadata; _resolve_calls'
     output is untouched by it.
  B  push_to_neo4j through a recording fake session: "l" only on rows whose line is known; the MERGE pattern has
     no line and the SET writes `r.line=row.l`; a line-less push sends rows of exactly {a, b, c}; the
     oversized-anchor path (_EDGE_TX_MAX_ROWS=1) still writes the lines; _write_calls still takes 2-tuples.
  C  the ast path: _Extractor records the FIRST (minimum) line per callee name, multi-line calls at their start,
     nothing for a def without calls; a real ast extraction pushed through the fake writes the call site.
  D  the incremental cache: CACHE_VERSION is 2, a cache an older build wrote (version 1, the old ast semantics
     stamp, nodes without call_lines) is discarded, and incremental (cold, warm) == full with the lines.
  E  mutation teeth: each mutant of build/sync.py (or build/extract_cache.py) must turn a check RED.

Hermetic: temp trees, a recording fake driver; no Neo4j, no network, no model.
usage: python tests/sync_call_lines_gates.py
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

CK = GX.call_key
_PASS = _FAIL = 0
_TMP: list = []

# every graph flag a check touches is pinned ("0" where off), so a later default flip cannot move these checks
OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_INCREMENTAL_SYNC": "0"}


class Rec:
    """Check recorder: loud (the real run) or silent (a mutant run only counts its failures)."""

    def __init__(self, loud=True):
        self.loud, self.failed, self.labels = loud, 0, []

    def check(self, label, ok, detail=""):
        global _PASS, _FAIL
        if self.loud:
            if ok:
                _PASS += 1
                print(f"  [PASS] {label}")
            else:
                _FAIL += 1
                print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")
        if not ok:
            self.failed += 1
            self.labels.append(label)
        return bool(ok)


LOUD = Rec()


def tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dm_calllines_"))
    _TMP.append(d)
    return d


@contextlib.contextmanager
def flags(**kv):
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


def load_module(path: Path, name: str, source: "str | None" = None):
    """`path` (or `source` standing for it) as a fresh module `name` (sys.path already holds its siblings)."""
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    code = compile(source if source is not None else path.read_text(encoding="utf-8"), str(path), "exec")
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)  # noqa: S102 — this repository's own module (or a mutant of it)
    finally:
        sys.modules.pop(name, None)
    return mod


def node(key, label, name, sf, line=1, **extra):
    d = {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line, "body_summary": name}
    d.update(extra)
    return d


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


# ---- a recording fake driver with explicit transactions ------------------------------------------------
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Log:
    def __init__(self):
        self.stmts = []                # (cypher, params)


class _Tx:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **p):
        self.log.stmts.append((cypher, p))
        return _Res()

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sess(_Tx):
    def begin_transaction(self, *a, **k):
        return _Tx(self.log)


class _Drv:
    def __init__(self):
        self.log = _Log()

    def session(self, *a, **k):
        return _Sess(self.log)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def calls_rows(drv) -> list:
    """[(cypher, row)] for every :CALLS row the push wrote."""
    out = []
    for c, p in drv.log.stmts:
        if "MERGE" in c and "[r:CALLS" in c:
            for r in p.get("rows") or []:
                out.append((c, r))
    return out


def push(Sm, nodes, calls, budget=None):
    drv = _Drv()
    saved = Sm._EDGE_TX_MAX_ROWS
    if budget is not None:
        Sm._EDGE_TX_MAX_ROWS = budget
    try:
        with flags(**OFF):
            Sm.push_to_neo4j(drv, copy.deepcopy(nodes), list(calls), [], None)
    finally:
        Sm._EDGE_TX_MAX_ROWS = saved
    return drv


# =====================================================================================================
# A — sync._call_lines (pure)
# =====================================================================================================
def a_cases(Sm, rec):
    fn = getattr(Sm, "_call_lines", None)
    if not rec.check("A: sync._call_lines exists", callable(fn)):
        return
    caller = "a.py::caller:1"
    tgt = [node("b.py::foo:3", "Function", "foo", "b.py", 3),
           node("c.py::Svc.run:4", "Method", "Svc.run", "c.py", 4),
           node("d.rb::Admin::Order:2", "Class", "Admin::Order", "d.rb", 2),
           node("e.rb::A::B.c:6", "Method", "A::B.c", "e.rb", 6)]

    def one(cl, b):
        ns = [node(caller, "Function", "caller", "a.py", 1, call_lines=cl)] + copy.deepcopy(tgt)
        return fn(ns, {(caller, b): "INFERRED"}).get((caller, b))

    rec.check("A1: the key entry wins over every name entry",
              one({"key": {"b.py::foo:3": 7}, "name": {"foo": 9}}, "b.py::foo:3") == 7,
              one({"key": {"b.py::foo:3": 7}, "name": {"foo": 9}}, "b.py::foo:3"))
    rec.check("A2: no key entry -> the target's full name",
              one({"name": {"Svc.run": 5, "run": 6}}, "c.py::Svc.run:4") == 5,
              one({"name": {"Svc.run": 5, "run": 6}}, "c.py::Svc.run:4"))
    rec.check("A3: ... then its last '.' segment",
              one({"name": {"run": 6}}, "c.py::Svc.run:4") == 6, one({"name": {"run": 6}}, "c.py::Svc.run:4"))
    rec.check("A4: ... then its last '::' segment (Ruby `Admin::Order` called as `Order`)",
              one({"name": {"Order": 8}}, "d.rb::Admin::Order:2") == 8,
              one({"name": {"Order": 8}}, "d.rb::Admin::Order:2"))
    rec.check("A4b: '.' segment before '::' segment (`A::B.c`: `c` before `B.c`)",
              one({"name": {"c": 11, "B.c": 12}}, "e.rb::A::B.c:6") == 11,
              one({"name": {"c": 11, "B.c": 12}}, "e.rb::A::B.c:6"))
    rec.check("A4c: a key entry for ANOTHER target is not used", one({"key": {"b.py::foo:3": 7}}, "c.py::Svc.run:4")
              is None, one({"key": {"b.py::foo:3": 7}}, "c.py::Svc.run:4"))
    # A8: bad values are ignored and the next lookup is tried
    bad = [True, False, 0, -3, "7", 7.0, None, [7], {"x": 7}]
    got = [one({"key": {"b.py::foo:3": v}, "name": {"foo": 9}}, "b.py::foo:3") for v in bad]
    rec.check("A8: a bool / 0 / negative / str / float / None / list / dict key value is ignored (the name "
              "entry is used)", got == [9] * len(bad), list(zip(bad, got)))
    got = [one({"name": {"foo": v}}, "b.py::foo:3") for v in bad]
    rec.check("A8b: ... and with no valid value anywhere there is no line", got == [None] * len(bad),
              list(zip(bad, got)))
    odd = [None, "x", 7, [("foo", 3)], {"key": "x", "name": ["foo"]}, {"key": None, "name": None}]
    got = [one(v, "b.py::foo:3") for v in odd]
    rec.check("A8c: malformed call_lines metadata never raises and gives no line", got == [None] * len(odd), got)
    # A9: no metadata anywhere -> {}
    ns = [node(caller, "Function", "caller", "a.py", 1)] + copy.deepcopy(tgt)
    out = fn(ns, {(caller, "b.py::foo:3"): "INFERRED"})
    rec.check("A9: no node carries call_lines -> {} (an empty dict)", out == {} and isinstance(out, dict), out)
    # A5: a name fan-out gives every target the line (through the REAL _resolve_calls)
    ns = [node(caller, "Function", "caller", "a.py", 1, call_lines={"name": {"foo": 4}}),
          node("b.py::foo:3", "Function", "foo", "b.py", 3), node("c.py::foo:8", "Function", "foo", "c.py", 8),
          node("d.py::other:2", "Function", "other", "d.py", 2, call_lines={"name": {"foo": 2}})]
    calls = [(caller, "foo"), ("d.py::other:2", "foo")]
    with flags(**OFF):
        edges = Sm._resolve_calls(copy.deepcopy(ns), calls)
        before = dict(edges)
        lines = fn(ns, edges)
    rec.check("A5: a name fan-out (2 targets) gives every target its caller's line",
              lines.get((caller, "b.py::foo:3")) == 4 and lines.get((caller, "c.py::foo:8")) == 4
              and lines.get(("d.py::other:2", "b.py::foo:3")) == 2, lines)
    rec.check("A5b: _call_lines leaves _resolve_calls' output untouched (same pairs, same grades)",
              edges == before and list(edges) == list(before), (edges, before))
    rec.check("A5c: only edges are keyed (no line for a pair that is not an edge)",
              set(lines) <= set(edges), set(lines) - set(edges))
    # A7: a CallKey callee comes back as a plain str key
    ns = [node(caller, "Function", "caller", "a.py", 1, call_lines={"key": {"b.py::foo:3": 6}},
               call_typed={"b.py::foo:3": "INFERRED"}),
          node("b.py::foo:3", "Function", "foo", "b.py", 3), node("c.py::foo:8", "Function", "foo", "c.py", 8)]
    with flags(**OFF):
        edges = Sm._resolve_calls(copy.deepcopy(ns), [(caller, CK("b.py::foo:3"))])
        lines = fn(ns, edges)
    keyed = [b for (_a, b) in edges if GX.is_call_key(b)]
    rec.check("A7: a typed CallKey edge gets its key's line", lines.get((caller, "b.py::foo:3")) == 6, (edges, lines))
    rec.check("A7b: ... and the output holds plain str keys, never a CallKey (not vacuous: the edge was keyed)",
              keyed and all(type(a) is str and type(b) is str for (a, b) in lines)
              and all(type(v) is int for v in lines.values()), (keyed, [(type(a), type(b)) for a, b in lines]))


SRC_UNKEY = ("def helper():\n"
             "    return 1\n"
             "\n"
             "\n"
             "class Box:\n"
             "    def helper(self):\n"
             "        return 2\n"
             "\n"
             "\n"
             "def caller(obj):\n"
             "    return obj.helper()\n")


def a_unkey(Sm, rec):
    """A6: a precise key the engine bound to a same-named module-level function, reached through a receiver, goes
    back to the name (_unkey_receiver_calls): the line is still found, now by name, for every target."""
    root = write_tree(tmp() / "unkey", {"a.py": SRC_UNKEY})
    ns = [node("a.py::module", "Module", "a", "a.py", 1),
          node("a.py::helper:1", "Function", "helper", "a.py", 1),
          node("a.py::Box:5", "Class", "Box", "a.py", 5),
          node("a.py::Box.helper:6", "Method", "Box.helper", "a.py", 6),
          node("a.py::caller:10", "Function", "caller", "a.py", 10,
               call_lines={"key": {"a.py::helper:1": 11}, "name": {"helper": 11}})]
    calls = [("a.py::caller:10", CK("a.py::helper:1"))]
    with flags(**dict(OFF, DEEPMAP_PRECISE_CALLS="1")):
        out, n = Sm._unkey_receiver_calls(root, ns, list(calls))
        edges = Sm._resolve_calls(ns, out)
        lines = Sm._call_lines(ns, edges) if callable(getattr(Sm, "_call_lines", None)) else {}
    named = [c for c in out if not GX.is_call_key(c[1])]
    rec.check("A6: (not vacuous) _unkey_receiver_calls turned the receiver key back into a name", n >= 1 and named,
              (n, out))
    tg = {b for (a, b) in edges if a == "a.py::caller:10"}
    rec.check("A6b: every target of the unkeyed call carries the call-site line 11",
              tg and all(lines.get(("a.py::caller:10", b)) == 11 for b in tg), (tg, lines))


# =====================================================================================================
# B — push_to_neo4j / _write_calls through a recording fake
# =====================================================================================================
def b_nodes(with_lines=True):
    ns = [node("a.py::caller:1", "Function", "caller", "a.py", 1,
               **({"call_lines": {"name": {"foo": 3, "bar": 5}}} if with_lines else {})),
          node("a.py::plain:9", "Function", "plain", "a.py", 9),
          node("b.py::foo:3", "Function", "foo", "b.py", 3),
          node("c.py::foo:8", "Function", "foo", "c.py", 8),
          node("b.py::bar:12", "Method", "Svc.bar", "b.py", 12)]
    calls = [("a.py::caller:1", "foo"), ("a.py::caller:1", "bar"), ("a.py::plain:9", "foo")]
    return ns, calls


def b_cases(Sm, rec):
    ns, calls = b_nodes()
    drv = push(Sm, ns, calls)
    rows = calls_rows(drv)
    by = {(r["a"], r["b"]): r for _c, r in rows}
    rec.check("B0: (not vacuous) the push wrote 5 CALLS rows", len(rows) == 5, rows)
    rec.check("B1: rows whose caller recorded the call carry \"l\" = the call-site line",
              by.get(("a.py::caller:1", "b.py::foo:3"), {}).get("l") == 3
              and by.get(("a.py::caller:1", "c.py::foo:8"), {}).get("l") == 3
              and by.get(("a.py::caller:1", "b.py::bar:12"), {}).get("l") == 5, by)
    rec.check("B1b: a row whose caller has no call_lines carries no \"l\" key at all (never l=None)",
              all("l" not in r for (a, _b), r in by.items() if a == "a.py::plain:9")
              and any(a == "a.py::plain:9" for (a, _b) in by), by)
    stmts = {c for c, _r in rows}
    merge_ok = []
    for c in stmts:
        head, _, tail = c.partition(" SET ")
        merge = head[head.find("MERGE"):] if "MERGE" in head else head
        merge_ok.append("line" not in merge and "row.l" not in merge and "r.line=row.l" in tail
                        and "MERGE (a)-[r:CALLS]->(b)" in merge)
    rec.check("B2: the MERGE pattern is `(a)-[r:CALLS]->(b)` with no line in it; the SET writes r.line=row.l",
              stmts and all(merge_ok), sorted(stmts))
    rec.check("B2b: every CALLS statement still sets r.confidence=row.c (confidence_gates' contract)",
              stmts and all("SET r.confidence=row.c" in c for c in stmts), sorted(stmts))
    # B3: a line-less push sends exactly the rows {a, b, c} the pre-change push sent
    ns2, calls2 = b_nodes(with_lines=False)
    drv2 = push(Sm, ns2, calls2)
    rows2 = [r for _c, r in calls_rows(drv2)]
    with flags(**OFF):
        edges = Sm._resolve_calls(copy.deepcopy(ns2), list(calls2))
    want = sorted(({"a": a, "b": b, "c": c} for (a, b), c in edges.items()), key=lambda r: (r["a"], r["b"]))
    got = sorted(rows2, key=lambda r: (r["a"], r["b"]))
    rec.check("B3: a push with no call_lines anywhere sends rows of exactly {a, b, c} (today's rows)",
              got == want and all(set(r) == {"a", "b", "c"} for r in rows2), (got, want))
    # B4: the oversized-anchor path writes the lines too
    drv3 = push(Sm, ns, calls, budget=1)
    by3 = {(r["a"], r["b"]): r for _c, r in calls_rows(drv3)}
    rec.check("B4: with _EDGE_TX_MAX_ROWS=1 (every anchor oversized) the rows are the same, lines included",
              by3 == by, (by3, by))
    # B5: _write_calls still takes the (b, conf) items a direct caller may hand it
    log = _Log()
    with flags(**OFF):
        Sm._write_calls(_Tx(log), ["x"], {"x": [("y", "INFERRED")]}, {"x": ["Function"], "y": ["Function"]})
        Sm._write_calls(_Tx(log), ["x"], {"x": [("y", "INFERRED", None), ("z", "AMBIGUOUS", 4)]},
                        {"x": ["Function"], "y": ["Function"], "z": ["Method"]}, prune=False)
    rows5 = [r for c, p in log.stmts if "MERGE" in c for r in p.get("rows") or []]
    rec.check("B5: _write_calls takes (b, conf) and (b, conf, line) items; a None line sends no \"l\"",
              {"a": "x", "b": "y", "c": "INFERRED"} in rows5 and rows5.count({"a": "x", "b": "y", "c": "INFERRED"}) == 2
              and {"a": "x", "b": "z", "c": "AMBIGUOUS", "l": 4} in rows5, rows5)


# =====================================================================================================
# C — the ast path
# =====================================================================================================
SRC_AST = ("import os\n"                              # 1
           "\n"                                       # 2
           "def top(obj):\n"                          # 3
           "    if obj:\n"                            # 4
           "        return helper(\n"                 # 5
           "            obj.go(1),\n"                 # 6
           "        )\n"                              # 7
           "    obj.go(2)\n"                          # 8
           "    helper()\n"                           # 9
           "    def inner():\n"                       # 10
           "        return later()\n"                 # 11
           "    return inner()\n"                     # 12
           "\n"                                       # 13
           "class Svc:\n"                             # 14
           "    def run(self):\n"                     # 15
           "        self.step()\n"                    # 16
           "        self.step()\n"                    # 17
           "    def step(self):\n"                    # 18
           "        return 1\n")                      # 19


def c_cases(Sm, rec):
    ex = Sm._Extractor("m.py")
    ex.visit(ast.parse(SRC_AST))
    by = {n["name"]: n for n in ex.nodes}
    top = by.get("top", {}).get("call_lines")
    rec.check("C1: _Extractor records {\"name\": {callee: the MINIMUM call line}} (helper 5 not 9, go 6 not 8; a "
              "multi-line call at its first line; a nested def's call counted for the outer def as its calls are)",
              top == {"name": {"helper": 5, "go": 6, "later": 11, "inner": 12}}, top)
    rec.check("C1b: ... a method's repeated self call: its first line",
              by.get("Svc.run", {}).get("call_lines") == {"name": {"step": 16}}, by.get("Svc.run"))
    rec.check("C1c: a def without calls carries no call_lines key",
              "call_lines" not in by.get("Svc.step", {"call_lines": 1}), by.get("Svc.step"))
    rec.check("C1d: the inner def records its own call", by.get("inner", {}).get("call_lines") == {"name": {"later": 11}},
              by.get("inner"))
    rec.check("C1e: the lines are plain ints and the names plain str (JSON-safe for the incremental cache)",
              all(type(k) is str and type(v) is int for n in ex.nodes for k, v in
                  ((n.get("call_lines") or {}).get("name") or {}).items()), [n.get("call_lines") for n in ex.nodes])
    # C2: a real ast extraction pushed through the fake writes the call site (not the caller's start line)
    root = write_tree(tmp() / "ast", {"pkg/a.py": "from pkg.b import helper\n\n\ndef alpha():\n    x = 1\n"
                                                 "    return helper(x)\n",
                                      "pkg/b.py": "def helper(v):\n    return v\n"})
    with flags(**OFF):
        fn, fc, _fi = Sm._rebuild_code_ast(root)
    drv = push(Sm, fn, fc)
    by2 = {(r["a"], r["b"]): r for _c, r in calls_rows(drv)}
    row = by2.get(("pkg/a.py::alpha:4", "pkg/b.py::helper:1"))
    rec.check("C2: an ast-extracted call pushes r.line = the call site (line 6), not the caller's start (line 4)",
              row is not None and row.get("l") == 6, by2)


# =====================================================================================================
# D — the incremental cache
# =====================================================================================================
TREE_INC = {"pkg/__init__.py": "",
            "pkg/a.py": "from pkg.b import helper\n\n\ndef alpha():\n    helper()\n    return helper()\n\n\n"
                        "class Svc:\n    def run(self):\n        self.step()\n\n    def step(self):\n        return 1\n",
            "pkg/b.py": "def helper():\n    return 42\n\n\ndef beta():\n    alpha()\n"}


def d_cases(Sm, EC, rec):
    rec.check("D1: extract_cache.CACHE_VERSION is 2 (the per-file node shape gained call_lines)",
              EC.CACHE_VERSION == 2, EC.CACHE_VERSION)
    root = write_tree(tmp() / "inc", TREE_INC)
    err = io.StringIO()
    with flags(**dict(OFF, DEEPMAP_INCREMENTAL_SYNC="1")), contextlib.redirect_stderr(err):
        full = Sm._rebuild_code_ast(root)
        cold = Sm._rebuild_code_ast_incremental(root)
        warm = Sm._rebuild_code_ast_incremental(root)
    with_lines = [n for n in full[0] if n.get("call_lines")]
    rec.check("D2: (not vacuous) the full extraction carries call_lines", len(with_lines) >= 3, with_lines)
    rec.check("D2b: incremental (cold, then warm) == full, call_lines included",
              tuple(full) == tuple(cold) == tuple(warm), (full[0], warm[0]))
    nf = len(TREE_INC)
    rec.check("D2c: ... and the warm run reused every file", f"{nf}/{nf} files reused" in err.getvalue(),
              err.getvalue())
    # D3: the cache an older build wrote for the SAME bytes: version 1, the old ast semantics stamp, no call_lines
    cp = EC.cache_path(root)
    cur = json.loads(cp.read_text(encoding="utf-8"))
    old = copy.deepcopy(cur)
    old["version"] = 1
    for rel, ent in old["files"].items():
        if rel.startswith("\x00"):
            ent["hash"] = "2026-09-r7"
        for n in ent.get("nodes") or []:
            n.pop("call_lines", None)
    cp.write_text(json.dumps(old, sort_keys=True), encoding="utf-8")
    err = io.StringIO()
    with flags(**dict(OFF, DEEPMAP_INCREMENTAL_SYNC="1")), contextlib.redirect_stderr(err):
        again = Sm._rebuild_code_ast_incremental(root)
    rec.check("D3: an older build's cache (version 1, no call_lines) is discarded: incremental == full again",
              tuple(again) == tuple(full), [n.get("call_lines") for n in again[0]])
    rec.check("D3b: ... every file re-parsed once", f"0/{nf} files reused" in err.getvalue(), err.getvalue())
    # D4: a version-1 file is rejected by load_cache on its own (the CACHE_VERSION bump, apart from the stamp)
    v1 = copy.deepcopy(cur)
    v1["version"] = 1
    cp.write_text(json.dumps(v1, sort_keys=True), encoding="utf-8")
    rec.check("D4: load_cache rejects a version-1 cache file", EC.load_cache(root, extractor="ast") == {"files": {}},
              list((EC.load_cache(root, extractor="ast").get("files") or {}))[:3])


# =====================================================================================================
# E — mutation teeth
# =====================================================================================================
SYNC_MUTANTS = [
    ("name lookup before key", '(("key", str(b)), ("name", nm),', '(("name", nm), ("key", str(b)),', "a"),
    ("the line inside the MERGE pattern", '"MERGE (a)-[r:CALLS]->(b) SET r.confidence=row.c, r.line=row.l"',
     '"MERGE (a)-[r:CALLS {line: row.l}]->(b) SET r.confidence=row.c"', "b"),
    ("an unknown line sent as l=None",
     '                    if line is not None:\n                        row["l"] = line\n',
     '                    row["l"] = line\n', "b"),
    ("a bool accepted as a line", "if type(v) is int and v > 0:", "if isinstance(v, int) and v > 0:", "a"),
    ("the push drops the line", "edges_by_anchor.setdefault(a, []).append((b, conf, lines.get((a, b))))",
     "edges_by_anchor.setdefault(a, []).append((b, conf))", "b"),
    ("the ast path keeps the LAST call line", "(callee not in lines or ln < lines[callee])",
     "(callee not in lines or ln > lines[callee])", "c"),
    ("the ast path records no lines", '            entry["call_lines"] = {"name": lines}\n', "            pass\n", "c"),
]


def run_mutant(src, which, EC_mut=None, tag=""):
    Sm = load_module(BUILD / "sync.py", f"dm_calllines_mut_{tag}_{os.getpid()}", src)
    if EC_mut is not None:
        Sm.extract_cache = EC_mut
    rec = Rec(loud=False)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if "a" in which:
                a_cases(Sm, rec)
                a_unkey(Sm, rec)
            if "b" in which:
                b_cases(Sm, rec)
            if "c" in which:
                c_cases(Sm, rec)
            if "d" in which:
                d_cases(Sm, Sm.extract_cache, rec)
    except Exception as exc:  # noqa: BLE001 — a crash is a caught mutant too
        rec.failed += 1
        rec.labels.append(f"crash: {exc!r}")
    return rec


def sec_e():
    print("\n-- E: mutation teeth (each mutant must turn a check RED) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    for i, (label, old, new, which) in enumerate(SYNC_MUTANTS):
        n = src.count(old)
        if not LOUD.check(f"E: mutant '{label}' — its anchor is found exactly once in sync.py", n == 1,
                          f"found {n} times"):
            continue
        rec = run_mutant(src.replace(old, new), which, tag=str(i))
        LOUD.check(f"E: mutant '{label}' is caught ({rec.failed} check(s) RED)", rec.failed > 0, rec.labels[:2])
    # the cache stamps: an older build's cache must not be reused when neither stamp moved
    ec_src = (BUILD / "extract_cache.py").read_text(encoding="utf-8")
    anchors = [("CACHE_VERSION = 2\n", ec_src), ('_AST_EXTRACT_SEMANTICS = "', src)]
    if not LOUD.check("E: the cache-stamp anchors are found exactly once",
                      all(t.count(a) == 1 for a, t in anchors), [t.count(a) for a, t in anchors]):
        return
    ec_v1 = load_module(BUILD / "extract_cache.py", f"dm_calllines_ec_v1_{os.getpid()}",
                        ec_src.replace("CACHE_VERSION = 2\n", "CACHE_VERSION = 1\n"))
    rec = run_mutant(src, "d", EC_mut=ec_v1, tag="v1")
    LOUD.check(f"E: mutant 'CACHE_VERSION stays 1' is caught ({rec.failed} check(s) RED)", rec.failed > 0,
               rec.labels[:2])
    i = src.find('_AST_EXTRACT_SEMANTICS = "')
    j = src.find('"', i + len('_AST_EXTRACT_SEMANTICS = "'))
    src_old = src[:i] + '_AST_EXTRACT_SEMANTICS = "2026-09-r7' + src[j:]
    rec = run_mutant(src_old, "d", EC_mut=ec_v1, tag="both")
    LOUD.check(f"E: mutant 'neither cache stamp moved' is caught ({rec.failed} check(s) RED)", rec.failed > 0,
               rec.labels[:2])


# =====================================================================================================
def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — :CALLS call-site lines (sync side, no Neo4j)")
    print("=" * 64)
    home = tmp()
    try:
        with flags(DEEPMAP_HOME=str(home / "home"), DEEPMAP_AUTO_GITIGNORE="0"):
            Sm = load_module(BUILD / "sync.py", f"dm_calllines_sync_{os.getpid()}")
            EC = Sm.extract_cache
            print("\n-- A: sync._call_lines (pure) --")
            for fn in (lambda: a_cases(Sm, LOUD), lambda: a_unkey(Sm, LOUD)):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    LOUD.check("A: runs without raising", False, repr(exc))
            print("\n-- B: push_to_neo4j / _write_calls (recording fake) --")
            try:
                b_cases(Sm, LOUD)
            except Exception as exc:  # noqa: BLE001
                LOUD.check("B: runs without raising", False, repr(exc))
            print("\n-- C: the ast path --")
            try:
                c_cases(Sm, LOUD)
            except Exception as exc:  # noqa: BLE001
                LOUD.check("C: runs without raising", False, repr(exc))
            print("\n-- D: the incremental cache --")
            try:
                d_cases(Sm, EC, LOUD)
            except Exception as exc:  # noqa: BLE001
                LOUD.check("D: runs without raising", False, repr(exc))
            try:
                sec_e()
            except Exception as exc:  # noqa: BLE001
                LOUD.check("E: runs without raising", False, repr(exc))
    finally:
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
