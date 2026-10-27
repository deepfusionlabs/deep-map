#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-9 W6 gates (stage B): the PUSH-TIME call rules in build/sync.py.

K10 — J3's bare-call rule (`_attach_bare_calls`, default ON, DEEPMAP_FALSE_CALL_FILTER=0 turns it off)
read only the text BEFORE the callee name on the same line. A receiver call split after its dot — gofmt's
ONLY legal multi-line chain form (`return q.` / `\\t\\tFirst()`), and legal in JS/TS/Rust, and in Python
inside brackets or after `\\` — therefore counted as BARE: the true `Load -> Query.First` edge was
deleted and the engine's mis-bind `Load -> svc.First` was UPGRADED to INFERRED.
  A  `_text_call_forms` / `_text_value_mention` take the previous code line: a name that opens its line
     after a line ending in `.` / `?.` / `::` / `->` (comments and blank lines skipped, a trailing
     comment cut; never a float literal `1.`, a range `..` or a spread `...`) is qualified, never bare.
  B  end to end on graphify-shaped callers: Go, TS (`api.` / `api?.`), Rust; a genuinely bare call still
     drops the method.
  C  the push-time and the map-time rule (graphify_extract._call_sites, W5) classify the same lines alike.
K11 — J3 rested on "a call without a receiver never reaches a method". A bare name can be a VALUE holding a
bound method / a method value: `check = _default.check` imported by another module (stdlib `random`
does this), a closure variable, Go's package-level `var First = defaultQuery.First`, a JS global
`var check = new Api().check`. The push time now keeps every target when the name MAY be such a value:
  D  Python (graphify-shaped callers): a from-import (absolute / relative / star / inside the function /
     through a re-exporting package) of a name the imported module binds as a VALUE, a closure variable
     of an enclosing def; an import from outside the project keeps it only when the method's own module
     binds the name as a value; an import cycle binds nothing; past 4 re-export hops -> kept. Controls:
     an import of a DEF, an external import, still drop.
  D2 the reverse alias: the engine names a resolved callee by its TARGET, so `base_check()` after
     `from pkg.backoff import check as base_check` reaches the push as `check`, one tuple with the caller's
     bare `check()` of its own def -> kept when the aliased import is a VALUE and the caller calls the
     alias; a DEF alias, or a caller that never calls the alias, still drops; the ast engine records the
     written name, so no reverse alias applies there.
  E  Go: a package-level `var` of the name in the caller's package directory (line form, `var (...)`
     block, multi-name spec; a _test.go var only for a test caller). Other packages' vars do not count.
  F  JS: the method's own file declares a value of that name (`var check = new Api().check`).
  G  the ast path (receiver kinds, round 7): the same Python rule — `from pkg.backoff import check;
     check()` no longer binds only the unrelated `util.check` INFERRED while dropping AuthBackoff.check.
K12 — DEEPMAP_LOCAL_DEF_CALLS (default OFF): on the graphify path the callee string is the TARGET's name,
so `from pkg.base import helper as base_helper; ...; base_helper(2)` reaches the push as (run, 'helper')
next to the caller's own bare `helper(1)`; the flag bound 'helper' to the local def and dropped (name
path) or re-bound (precise CALLS) the real run -> pkg/base.py:helper edge.
  H  a name that some import of the file brings in under ANOTHER identifier is undecided for a caller that
     calls that identifier: today's resolution (flag OFF output) for that name, on the name path and under
     precise CALLS; the flag still binds a file with no such alias, and a caller that never calls it.
  I  the REAL engine (graphify, when installed): the reviewers' Go / TS / reverse-alias trees end to end.
  J  DEEPMAP_FALSE_CALL_FILTER=0: nothing attached, the round-7 resolution (graphify-shaped AND ast).

Hermetic: temp dirs, synthetic nodes, no sockets, no Neo4j; section I runs the installed engine under a
temporary DEEPMAP_HOME.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

_PASS = _FAIL = 0
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_DROP_APP_TO_TEST_CALLS", "DEEPMAP_FALSE_CALL_FILTER",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR", "DEEPMAP_EXTRACTOR",
          "DEEPMAP_INCREMENTAL_SYNC")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail else ''}")


def _load(name="sync"):
    spec = importlib.util.spec_from_file_location(f"dm_r9b_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    base = {k: None for k in _FLAGS}
    base.update(kv)
    saved = {k: os.environ.get(k) for k in base}
    try:
        for k, v in base.items():
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


def _write_tree(root: Path, tree: dict) -> None:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))


def _n(key, name, sf, line=1, label="Function"):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name, "documented": False}


def _mods(tree: dict) -> list:
    """A graphify-style Module node per file of the tree."""
    return [_n("mod:" + rel, Path(rel).stem, rel, 1, "Module") for rel in tree]


def _attach(S, root, nodes, calls) -> None:
    """The push-time source passes, in _sync_pass order."""
    with contextlib.redirect_stderr(io.StringIO()):
        S._attach_bare_calls(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)
        S._attach_local_def_calls(root, nodes, calls)


def _resolve(S, tree, nodes, calls, **env):
    """Write `tree` to a temp root, run the push-time passes and return (edges, nodes)."""
    nodes = [dict(n) for n in nodes]
    with tempfile.TemporaryDirectory() as td, _env(**env):
        root = Path(td)
        _write_tree(root, tree)
        _attach(S, root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
    return edges, nodes


def _r7_oracle(nodes, calls):
    """The round-7 name fan-out (no call-site metadata): the FALSE_CALL_FILTER=0 byte-identity oracle."""
    import edge_resolve
    name_to_keys = edge_resolve.build_name_index(nodes)
    key_to_file = {n["key"]: str(n.get("source_file", "")) for n in nodes}
    local_of = {n["key"]: set(n["local_defs"]) for n in nodes if n.get("local_defs")}
    edges = {}
    for ck, callee in calls:
        if ck not in key_to_file:
            continue
        targets = [t for t in name_to_keys.get(callee, []) if t != ck]
        econf = "AMBIGUOUS" if len(targets) > 1 else "INFERRED"
        graded = [(t, econf) for t in targets]
        if local_of.get(ck) and callee in local_of[ck]:
            graded = [(t, c if key_to_file.get(t) == key_to_file[ck] else "AMBIGUOUS") for t, c in graded]
        for t, c in graded:
            edges[(ck, t)] = c
    return edges


# =====================================================================================================
# A — K10 unit: the previous code line decides a call that opens its line
# =====================================================================================================
def gate_a_unit() -> None:
    print("\n-- A: K10 — a call that opens its line after `recv.` / `?.` / `::` / `->` is qualified --")
    S = _load()
    tcf = S._text_call_forms
    try:
        tcf("x()", "x", prev=None)
        has_prev = True
    except TypeError:
        has_prev = False
    check("_text_call_forms accepts the previous code line (prev=)", has_prev)
    if not has_prev:
        return
    cases = [
        ("\t\tFirst()", "First", "\t\tWhere(\"x\").", ["qualified"], "Go chain continuation (`Where(\"x\").`)"),
        ("\t\tFirst()", "First", "\treturn q.", ["qualified"], "Go `return q.` then `First()`"),
        ("\t\tWhere(\"x\").", "Where", "\treturn q.", ["qualified"], "a middle link of the chain"),
        ("    check(2);", "check", "  return api?.", ["qualified"], "TS optional chaining `api?.`"),
        ("        first()", "first", "    Store::", ["qualified"], "Rust path `Store::`"),
        ("    m()", "m", "  p->", ["qualified"], "`->` member"),
        ("\t\tFirst()", "First", S._prev_code_line(["\treturn q. // the chain", "\t\tFirst()"], 1, False),
         ["qualified"], "a trailing // comment is cut (prev as _prev_code_line gives it)"),
        ("        check()", "check", "    x = (obj.\\", ["qualified"], "Python explicit `\\` continuation"),
        ("\tFirst()", "First", "\tvar x = 1.", ["bare"], "a float literal `1.` ends a statement"),
        ("  check(1)", "check", "  f(a, ...", ["bare"], "a spread / ellipsis `...` is not a member"),
        ("  check(1)", "check", "  let r = 0..", ["bare"], "a range `..` is not a member"),
        ("\tFirst()", "First", "\tx := 1", ["bare"], "an ordinary previous statement"),
        ("\tFirst()", "First", "\ts := \"end.\"", ["bare"], "a string ending in '.' is not a member"),
        ("\tFirst()", "First", None, ["bare"], "no previous line -> bare (unchanged)"),
        ("\tFirst()", "First", "", ["bare"], "an empty previous line -> bare"),
        ("\tx := First()", "First", "\treturn q.", ["bare"], "a name NOT opening its line ignores prev"),
        ("\tq.First()", "First", "\tx := 1", ["qualified"], "a same-line receiver (unchanged)"),
    ]
    for text, name, prev, want, label in cases:
        got = tcf(text, name, prev=prev)
        check(f"_text_call_forms: {label}", got == want, (text, prev, got))
    lazy = []
    got = tcf("\t\tFirst()", "First", prev=lambda: lazy.append(1) or "\treturn q.")
    check("prev may be a zero-argument callable (read lazily)", got == ["qualified"] and lazy == [1], (got, lazy))
    lazy2 = []
    tcf("\tx := First()", "First", prev=lambda: lazy2.append(1) or "\treturn q.")
    check("... and is not read when the name does not open its line", lazy2 == [], lazy2)
    tvm = S._text_value_mention
    try:
        v1 = tvm("\t\tFirst", "First", prev="\tx := q.")
        v2 = tvm("\t\tFirst", "First", prev="\tx := 1")
        ok = True
    except TypeError:
        ok, v1, v2 = False, None, None
    check("_text_value_mention: `x := q.` / `First` is a member access, not a value", ok and v1 is False, v1)
    check("_text_value_mention: after an ordinary line `First` alone IS a value mention", ok and v2 is True, v2)
    lines = ["\treturn q. // c", "\t// a comment.", "", "   /* block */", "\t\tFirst()"]
    pcl = getattr(S, "_prev_code_line", None)
    check("_prev_code_line skips comment-only and blank lines and cuts the trailing comment",
          callable(pcl) and pcl(lines, 4, False) == "\treturn q.", pcl(lines, 4, False) if callable(pcl) else None)
    check("_prev_code_line: nothing before the first line -> None", callable(pcl) and pcl(lines, 0, False) is None)
    check("_prev_code_line: a Python `#` comment is cut, `//` is not a Python comment",
          callable(pcl) and pcl(["x = a // b  # half.", "y()"], 1, True) == "x = a // b",
          pcl(["x = a // b  # half.", "y()"], 1, True) if callable(pcl) else None)


# =====================================================================================================
# B — K10 end to end (graphify-shaped callers)
# =====================================================================================================
_GO_K10 = {
    "store.go": ("package p\n"                                            # 1
                 "\n"
                 "type Query struct{}\n"                                  # 3
                 "\n"
                 "func (q *Query) Where(s string) *Query { return q }\n"  # 5
                 "\n"
                 "func (q *Query) First() error { return nil }\n"),       # 7
    "svc.go": ("package p\n"                                              # 1
               "\n"
               "func First() error {\n"                                   # 3
               "\treturn nil\n"
               "}\n"
               "\n"
               "func Load(q *Query) error {\n"                            # 7
               "\treturn q.\n"                                            # 8
               "\t\tWhere(\"x\").\n"                                      # 9
               "\t\tFirst()\n"                                            # 10
               "}\n"
               "\n"
               "func LoadC(q *Query) error {\n"                           # 13
               "\treturn q. // chained\n"                                 # 14
               "\t\t// the terminal call\n"                               # 15
               "\n"                                                       # 16
               "\t\tFirst()\n"                                            # 17
               "}\n"
               "\n"
               "func Bare() error {\n"                                    # 20
               "\tvar x = 1.\n"                                           # 21
               "\t_ = x\n"                                                # 22
               "\tFirst()\n"                                              # 23
               "\treturn nil\n"
               "}\n"
               "\n"
               "func Bare2() error {\n"                                   # 27
               "\tvar y = 2.\n"                                           # 28
               "\tFirst()\n"                                              # 29
               "\treturn nil\n"
               "}\n"),
}
_GO_K10_NODES = _mods(_GO_K10) + [
    _n("q", "Query", "store.go", 3, "Class"), _n("q_where", "Query.Where", "store.go", 5, "Method"),
    _n("q_first", "Query.First", "store.go", 7, "Method"), _n("s_first", "First", "svc.go", 3),
    _n("s_load", "Load", "svc.go", 7), _n("s_loadc", "LoadC", "svc.go", 13), _n("s_bare", "Bare", "svc.go", 20),
    _n("s_bare2", "Bare2", "svc.go", 27)]
_GO_K10_CALLS = [("s_load", "First"), ("s_load", "Where"), ("s_loadc", "First"), ("s_bare", "First"),
                 ("s_bare2", "First")]

_TS_K10 = {
    "api.ts": ("export class Api {\n"                                     # 1
               "  check(x: number): number {\n"                           # 2
               "    return x;\n"
               "  }\n"
               "}\n"),
    "svc.ts": ("import { Api } from \"./api\";\n"                         # 1
               "\n"
               "function check(x: number): number {\n"                    # 3
               "  return x + 1;\n"
               "}\n"
               "\n"
               "export function load(api: Api): number {\n"               # 7
               "  return api.\n"                                          # 8
               "    check(2);\n"                                          # 9
               "}\n"
               "\n"
               "export function load2(api: Api): number {\n"              # 12
               "  return api?.\n"                                         # 13
               "    check(3) ?? 0;\n"                                     # 14
               "}\n"
               "\n"
               "export function bare(): number {\n"                       # 17
               "  return check(4);\n"                                     # 18
               "}\n"),
}
_TS_K10_NODES = _mods(_TS_K10) + [
    _n("api", "Api", "api.ts", 1, "Class"), _n("api_check", "Api.check", "api.ts", 2, "Method"),
    _n("t_check", "check", "svc.ts", 3), _n("t_load", "load", "svc.ts", 7), _n("t_load2", "load2", "svc.ts", 12),
    _n("t_bare", "bare", "svc.ts", 17)]
_TS_K10_CALLS = [("t_load", "check"), ("t_load2", "check"), ("t_bare", "check")]

_RS_K10 = {
    "src/store.rs": ("pub struct Store;\n"                                # 1
                     "\n"
                     "impl Store {\n"                                     # 3
                     "    pub fn first(&self) -> u8 {\n"                  # 4
                     "        1\n"
                     "    }\n"
                     "}\n"),
    "src/svc.rs": ("use crate::store::Store;\n"                           # 1
                   "\n"
                   "fn first() -> u8 {\n"                                 # 3
                   "    2\n"
                   "}\n"
                   "\n"
                   "pub fn load(s: &Store) -> u8 {\n"                     # 7
                   "    s.\n"                                             # 8
                   "        first()\n"                                    # 9
                   "}\n"),
}
_RS_K10_NODES = _mods(_RS_K10) + [
    _n("st", "Store", "src/store.rs", 1, "Class"), _n("st_first", "Store.first", "src/store.rs", 4, "Method"),
    _n("r_first", "first", "src/svc.rs", 3), _n("r_load", "load", "src/svc.rs", 7)]
_RS_K10_CALLS = [("r_load", "first")]


def gate_b_k10_e2e() -> None:
    print("\n-- B: K10 end to end — a trailing-dot receiver chain keeps the method (Go / TS / Rust) --")
    S = _load()
    edges, nodes = _resolve(S, _GO_K10, _GO_K10_NODES, _GO_K10_CALLS)
    load = next(n for n in nodes if n["key"] == "s_load")
    check("Go: Load's `q.` / `Where(\"x\").` / `First()` is not marked bare-only",
          "First" not in (load.get("call_bare") or []) and "Where" not in (load.get("call_bare") or []),
          load.get("call_bare"))
    check("Go: Load keeps the true Query.First edge (AMBIGUOUS beside the engine's svc.First)",
          edges.get(("s_load", "q_first")) == "AMBIGUOUS" and edges.get(("s_load", "s_first")) == "AMBIGUOUS", edges)
    check("Go: the engine's mis-bind Load -> svc.First is NOT upgraded to INFERRED",
          edges.get(("s_load", "s_first")) != "INFERRED", edges.get(("s_load", "s_first")))
    check("Go: the middle link Where(...) keeps Query.Where", edges.get(("s_load", "q_where")) == "INFERRED",
          edges.get(("s_load", "q_where")))
    check("Go: comment and blank lines between `q. // chained` and `First()` are skipped -> kept",
          edges.get(("s_loadc", "q_first")) == "AMBIGUOUS", edges.get(("s_loadc", "q_first")))
    check("Go control: a genuinely bare First() (after `_ = x`) still drops Query.First -> svc.First INFERRED",
          ("s_bare", "q_first") not in edges and edges.get(("s_bare", "s_first")) == "INFERRED", edges)
    check("Go control: after `var y = 2.` (a float literal) First() is bare -> dropped",
          ("s_bare2", "q_first") not in edges and edges.get(("s_bare2", "s_first")) == "INFERRED", edges)
    edges, _ = _resolve(S, _TS_K10, _TS_K10_NODES, _TS_K10_CALLS)
    check("TS: `api.` / `check(2)` keeps Api.check", edges.get(("t_load", "api_check")) == "AMBIGUOUS", edges)
    check("TS: `api?.` / `check(3)` keeps Api.check", edges.get(("t_load2", "api_check")) == "AMBIGUOUS", edges)
    check("TS control: a bare check(4) still drops Api.check -> the local check INFERRED",
          ("t_bare", "api_check") not in edges and edges.get(("t_bare", "t_check")) == "INFERRED", edges)
    edges, _ = _resolve(S, _RS_K10, _RS_K10_NODES, _RS_K10_CALLS)
    check("Rust: `s.` / `first()` keeps Store.first", edges.get(("r_load", "st_first")) == "AMBIGUOUS", edges)


# =====================================================================================================
# C — push time and map time agree (graphify_extract._call_sites, W5's K10)
# =====================================================================================================
def gate_c_parity() -> None:
    print("\n-- C: K10 — the push-time and the map-time line rules agree --")
    S = _load()
    import graphify_extract as GX
    if not callable(getattr(GX, "_call_sites", None)):
        check("graphify_extract._call_sites exists", False)
        return
    texts = [("\t\tFirst()", "First"), ("    check(2);", "check"), ("        first()", "first"), ("m()", "m"),
             ("\tx := First()", "First"), ("\tq.First()", "First")]
    prevs = ["\treturn q.", "\treturn q. // c", "  api?.", "  S::", "  p->", "\tvar x = 1.", "  f(a, ...",
             "  let r = 0..", "    x = (obj.\\", "\tx := 1", "", None, "  return a.b.", "  s = \"a.\"", "  x = 3.14.",
             "  items[0].", "  )."]
    bad = []
    for text, name in texts:
        for prev in prevs:
            try:
                mine = S._text_call_forms(text, name, prev=prev)
            except TypeError:
                mine = None
            theirs = [k for k, _q in GX._call_sites(text, name, prev=prev)]
            if mine != theirs:
                bad.append((text, prev, mine, theirs))
    check(f"_text_call_forms == graphify_extract._call_sites on {len(texts) * len(prevs)} (line, prev) pairs",
          not bad, bad[:6])
    pcl, gpl = getattr(S, "_prev_code_line", None), getattr(GX, "_prev_code_line", None)
    battery = [(["\treturn q. // c", "\t// x.", "", "\t\tFirst()"], 3, False),
               (["a = b  # c.", "", "# only a comment", "f()"], 3, True),
               (["s = 'http://x.'", "f()"], 1, False), (["x", "  * doc line.", "f()"], 2, False),
               (["one."] + [""] * 60 + ["f()"], 61, False)]
    diff = [(ls, i, py) for ls, i, py in battery
            if not (callable(pcl) and callable(gpl) and pcl(ls, i, py) == gpl(ls, i, py))]
    check("_prev_code_line == graphify_extract._prev_code_line (comments, blanks, strings, the 50-line bound)",
          not diff, [(i, pcl(ls, i, py) if callable(pcl) else None, gpl(ls, i, py)) for ls, i, py in diff][:4])


# =====================================================================================================
# D — K11 Python (graphify-shaped callers)
# =====================================================================================================
_BACKOFF = ("class AuthBackoff:\n"          # 1
            "    def check(self):\n"        # 2
            "        return 1\n"
            "\n"
            "\n"
            "_default = AuthBackoff()\n"    # 6
            "check = _default.check\n")     # 7
_PY_K11 = {
    "pkg/__init__.py": "",
    "pkg/backoff.py": _BACKOFF,
    "pkg/util.py": "def check():\n    return 2\n",
    "pkg/server.py": "from pkg.backoff import check\n\n\ndef handle():\n    if check():\n        return 1\n",
    "pkg/rel.py": "from .backoff import check\n\n\ndef handle_rel():\n    return check()\n",
    "pkg/star.py": "from pkg.backoff import *\n\n\ndef handle_star():\n    return check()\n",
    "pkg/fnimp.py": "def handle_fn():\n    from pkg.backoff import check\n    return check()\n",
    "pkg/closure.py": ("def make(obj):\n"            # 1
                       "    check = obj.check\n"     # 2
                       "\n"
                       "    class K:\n"              # 4
                       "        def run(self):\n"    # 5
                       "            return check()\n"
                       "\n"
                       "    return K\n"),
    "pkg/local.py": "def handle_local(obj):\n    check = obj.check\n    return check()\n",
    "pkg/defimp.py": "from pkg.util import check\n\n\ndef handle_def():\n    return check()\n",
    "pkg/plain.py": "def handle_plain():\n    return check()\n",
    "reex/__init__.py": "from pkg.backoff import check\n",
    "app/server2.py": "from reex import check\n\n\ndef handle_reex():\n    return check()\n",
    "cyc/__init__.py": "",
    "cyc/a.py": "from cyc.b import check\n",
    "cyc/b.py": "from cyc.a import check\n",
    "cyc/use.py": "from cyc.a import check\n\n\ndef handle_cyc():\n    return check()\n",
    "deep/__init__.py": "",
    "deep/d1.py": "from deep.d2 import check\n",
    "deep/d2.py": "from deep.d3 import check\n",
    "deep/d3.py": "from deep.d4 import check\n",
    "deep/d4.py": "from deep.d5 import check\n",
    "deep/d5.py": "from deep.d6 import check\n",
    "deep/d6.py": "def check():\n    return 6\n",
    "deep/use.py": "from deep.d1 import check\n\n\ndef handle_deep():\n    return check()\n",
    "broken/__init__.py": "",
    "broken/mod.py": "def (:\n",
    "broken/use.py": "from broken.mod import check\n\n\ndef handle_broken():\n    return check()\n",
    "lazy/__init__.py": "def __getattr__(name):\n    return None\n",
    "lazy/use.py": "from lazy import check\n\n\ndef handle_lazy():\n    return check()\n",
}
_PY_K11_NODES = _mods(_PY_K11) + [
    _n("ab", "AuthBackoff", "pkg/backoff.py", 1, "Class"), _n("ab_check", "AuthBackoff.check", "pkg/backoff.py", 2, "Method"),
    _n("u_check", "check", "pkg/util.py", 1), _n("d6_check", "check", "deep/d6.py", 1),
    _n("handle", "handle", "pkg/server.py", 4), _n("handle_rel", "handle_rel", "pkg/rel.py", 4),
    _n("handle_star", "handle_star", "pkg/star.py", 4), _n("handle_fn", "handle_fn", "pkg/fnimp.py", 1),
    _n("make", "make", "pkg/closure.py", 1), _n("k_run", "K.run", "pkg/closure.py", 5, "Method"),
    _n("handle_local", "handle_local", "pkg/local.py", 1), _n("handle_def", "handle_def", "pkg/defimp.py", 4),
    _n("handle_plain", "handle_plain", "pkg/plain.py", 1), _n("handle_reex", "handle_reex", "app/server2.py", 4),
    _n("handle_cyc", "handle_cyc", "cyc/use.py", 4), _n("handle_deep", "handle_deep", "deep/use.py", 4),
    _n("handle_broken", "handle_broken", "broken/use.py", 4), _n("handle_lazy", "handle_lazy", "lazy/use.py", 4)]
_KEEP_PY = [("handle", "`from pkg.backoff import check` (absolute)"), ("handle_rel", "`from .backoff import check`"),
            ("handle_star", "`from pkg.backoff import *`"), ("handle_fn", "an import inside the function"),
            ("k_run", "a closure variable of the enclosing def (`check = obj.check`)"),
            ("handle_local", "a local `check = obj.check` (unchanged)"),
            ("handle_reex", "`from reex import check`, re-exported by reex/__init__.py"),
            ("handle_deep", "a re-export chain past 4 hops (undecidable -> kept)"),
            ("handle_broken", "an import from a module that does not parse"),
            ("handle_lazy", "an import from a module with a module-level __getattr__")]
_DROP_PY = [("handle_def", "`from pkg.util import check` — a DEF"), ("handle_plain", "a name the file never binds"),
            ("handle_cyc", "an import cycle (binds nothing)")]

_EXT = {
    "ext/cache.py": "class Cache:\n    def join(self, *a):\n        return a\n",
    "ext/paths.py": "def join(*a):\n    return a\n",
    "ext/use.py": "from os.path import join\n\n\ndef go():\n    return join('a', 'b')\n",
    "extv/client.py": "class Client:\n    def get(self, url):\n        return url\n\n\n_c = Client()\nget = _c.get\n",
    "extv/fetch.py": "def get(url):\n    return url\n",
    "extv/use.py": "from requests import get\n\n\ndef fetch(url):\n    return get(url)\n",
}
_EXT_NODES = _mods(_EXT) + [
    _n("cache", "Cache", "ext/cache.py", 1, "Class"), _n("cache_join", "Cache.join", "ext/cache.py", 2, "Method"),
    _n("p_join", "join", "ext/paths.py", 1), _n("go", "go", "ext/use.py", 4),
    _n("client", "Client", "extv/client.py", 1, "Class"), _n("client_get", "Client.get", "extv/client.py", 2, "Method"),
    _n("f_get", "get", "extv/fetch.py", 1), _n("fetch", "fetch", "extv/use.py", 4)]


def gate_d_k11_python() -> None:
    print("\n-- D: K11 Python — a bare name that may hold a bound method keeps the method (graphify-shaped) --")
    S = _load()
    calls = [(k, "check") for k, _why in _KEEP_PY + _DROP_PY]
    edges, nodes = _resolve(S, _PY_K11, _PY_K11_NODES, calls)
    for k, why in _KEEP_PY:
        check(f"kept: {why} -> AuthBackoff.check (AMBIGUOUS)", edges.get((k, "ab_check")) == "AMBIGUOUS",
              {t: c for (a, t), c in edges.items() if a == k})
    for k, why in _DROP_PY:
        check(f"control, dropped: {why} -> no AuthBackoff.check edge", (k, "ab_check") not in edges,
              {t: c for (a, t), c in edges.items() if a == k})
    check("control: `from pkg.util import check` keeps util.check, graded over what remains",
          edges.get(("handle_def", "u_check")) == "AMBIGUOUS" and edges.get(("handle_def", "d6_check")) == "AMBIGUOUS",
          {t: c for (a, t), c in edges.items() if a == "handle_def"})
    marked = {n["key"] for n in nodes if "check" in (n.get("call_bare") or [])}
    check("call_bare marks exactly the controls", marked == {k for k, _w in _DROP_PY}, sorted(marked))
    edges, _ = _resolve(S, _EXT, _EXT_NODES, [("go", "join"), ("fetch", "get")])
    check("control: `from os.path import join` (outside the project) still drops Cache.join -> join INFERRED",
          ("go", "cache_join") not in edges and edges.get(("go", "p_join")) == "INFERRED", edges)
    check("an external import whose name the METHOD's own module binds as a value (`get = _c.get`) is kept",
          edges.get(("fetch", "client_get")) == "AMBIGUOUS", edges)


# D2 — the reverse alias on the J3 path: the engine names a resolved callee by its TARGET, so `base_check()`
# after `from pkg.backoff import check as base_check` reaches the push as `check` — one tuple with the
# caller's bare `check()` of its own def. The bare `check` site must not decide the tuple alone.
_REV = {
    "pkg/__init__.py": "",
    "pkg/backoff.py": _BACKOFF,
    "pkg/util.py": "def check():\n    return 2\n",
    "pkg/rev.py": ("from pkg.backoff import check as base_check\n"   # 1
                   "\n"
                   "\n"
                   "def check():\n"                                  # 4
                   "    return 0\n"
                   "\n"
                   "\n"
                   "def handle_rev():\n"                             # 8
                   "    return check(), base_check()\n"
                   "\n"
                   "\n"
                   "def handle_own():\n"                             # 12
                   "    return check()\n"),
    "pkg/revdef.py": ("from pkg.util import check as util_check\n"    # 1
                      "\n"
                      "\n"
                      "def check():\n"                               # 4
                      "    return 0\n"
                      "\n"
                      "\n"
                      "def handle_revdef():\n"                       # 8
                      "    return check(), util_check()\n"),
    "pkg/revfn.py": ("def check():\n"                                 # 1
                     "    return 0\n"
                     "\n"
                     "\n"
                     "def handle_revfn():\n"                          # 5
                     "    from pkg.backoff import check as bc\n"
                     "    return check(), bc()\n"),
}
_REV_NODES = _mods(_REV) + [
    _n("ab", "AuthBackoff", "pkg/backoff.py", 1, "Class"), _n("ab_check", "AuthBackoff.check", "pkg/backoff.py", 2, "Method"),
    _n("u_check", "check", "pkg/util.py", 1), _n("rev_check", "check", "pkg/rev.py", 4),
    _n("handle_rev", "handle_rev", "pkg/rev.py", 8), _n("handle_own", "handle_own", "pkg/rev.py", 12),
    _n("revdef_check", "check", "pkg/revdef.py", 4),
    _n("handle_revdef", "handle_revdef", "pkg/revdef.py", 8), _n("revfn_check", "check", "pkg/revfn.py", 1),
    _n("handle_revfn", "handle_revfn", "pkg/revfn.py", 5)]


def gate_d2_reverse_alias() -> None:
    print("\n-- D2: K11/K12 — a VALUE imported under another name still reaches the push as its own name --")
    S = _load()
    calls = [("handle_rev", "check"), ("handle_revdef", "check"), ("handle_revfn", "check"), ("handle_own", "check")]
    edges, nodes = _resolve(S, _REV, _REV_NODES, calls)

    def out(k):
        return {t: c for (a, t), c in edges.items() if a == k}

    check("graphify-shaped: `import check as base_check` (a VALUE: `check = _default.check`) + a bare local "
          "check() -> AuthBackoff.check kept", out("handle_rev").get("ab_check") == "AMBIGUOUS", out("handle_rev"))
    check("... the same, the aliased import inside the function", out("handle_revfn").get("ab_check") == "AMBIGUOUS",
          out("handle_revfn"))
    check("control: `import check as util_check` (a DEF) -> the method is still dropped",
          "ab_check" not in out("handle_revdef"), out("handle_revdef"))
    check("control: handle_own() in the same file calls only its own check(), never the alias -> dropped",
          "ab_check" not in out("handle_own"), out("handle_own"))
    marked = {n["key"] for n in nodes if "check" in (n.get("call_bare") or [])}
    check("call_bare marks exactly the two controls", marked == {"handle_revdef", "handle_own"}, sorted(marked))
    # the ast engine records the WRITTEN name (`base_check`), so its `check` is the caller's own def: no reverse
    # alias there — the method stays dropped (no over-keep)
    with tempfile.TemporaryDirectory() as td, _env():
        root = Path(td)
        _write_tree(root, _REV)
        with contextlib.redirect_stderr(io.StringIO()):
            anodes, acalls, _imports = S._rebuild_code_ast(root)
        _attach(S, root, anodes, acalls)
        aedges = S._resolve_calls(anodes, acalls)
    key = {(n["name"], n["source_file"]): n["key"] for n in anodes}
    hr, ab = key.get(("handle_rev", "pkg/rev.py")), key.get(("AuthBackoff.check", "pkg/backoff.py"))
    hn = next((n for n in anodes if n["key"] == hr), {})
    check("ast: the written `check()` beside `base_check()` binds no method (no call_alias for 'check')",
          hr and ab and (hr, ab) not in aedges and "check" not in (hn.get("call_alias") or []),
          ({t: c for (a, t), c in aedges.items() if a == hr}, hn.get("call_alias")))


# =====================================================================================================
# E — K11 Go: a package-level var holding a method value
# =====================================================================================================
def _go_pkg(d, var_src, extra=None):
    tree = {
        f"{d}/store.go": ("package p\n"                                   # 1
                          "\n"
                          "type Query struct{}\n"                         # 3
                          "\n"
                          "func (q *Query) First() error { return nil }\n"  # 5
                          "\n"
                          "var defaultQuery = &Query{}\n"                 # 7
                          + var_src),
        f"{d}/svc.go": "package p\n\nfunc Load() error {\n\treturn First()\n}\n",
        "other/first.go": "package other\n\nfunc First() error { return nil }\n",
    }
    tree.update(extra or {})
    nodes = _mods(tree) + [_n("q_first", "Query.First", f"{d}/store.go", 5, "Method"),
                           _n("o_first", "First", "other/first.go", 3), _n("load", "Load", f"{d}/svc.go", 3)]
    return tree, nodes


def gate_e_k11_go() -> None:
    print("\n-- E: K11 Go — a package-level `var First = defaultQuery.First` keeps Query.First --")
    S = _load()
    for label, var_src in (("`var First = defaultQuery.First`", "\nvar First = defaultQuery.First\n"),
                           ("a `var ( ... )` block", "\nvar (\n\tOther = 1\n\tFirst = defaultQuery.First\n)\n"),
                           ("a multi-name spec `var First, Second = ...`",
                            "\nvar First, Second = defaultQuery.First, defaultQuery.First\n")):
        tree, nodes = _go_pkg("p", var_src)
        edges, _ = _resolve(S, tree, nodes, [("load", "First")])
        check(f"Go: {label} in the caller's package -> Load keeps Query.First (AMBIGUOUS)",
              edges.get(("load", "q_first")) == "AMBIGUOUS", edges)
    tree, nodes = _go_pkg("p", "", {"p/vars_test.go": "package p\n\nvar First = defaultQuery.First\n",
                                    "p/svc_test.go": "package p\n\nfunc TestLoad() {\n\tFirst()\n}\n"})
    nodes.append(_n("tload", "TestLoad", "p/svc_test.go", 3))
    edges, _ = _resolve(S, tree, nodes, [("load", "First"), ("tload", "First")])
    check("Go control: a var only in a _test.go file does not count for a production caller -> dropped",
          ("load", "q_first") not in edges and edges.get(("load", "o_first")) == "INFERRED", edges)
    check("Go: ... but does for a _test.go caller of the same package -> kept",
          edges.get(("tload", "q_first")) == "AMBIGUOUS", edges)
    tree, nodes = _go_pkg("p", "", {"elsewhere/vars.go": "package elsewhere\n\nvar First = 1\n"})
    edges, _ = _resolve(S, tree, nodes, [("load", "First")])
    check("Go control: a var of another package (directory) does not count -> dropped",
          ("load", "q_first") not in edges, edges)
    tree, nodes = _go_pkg("p", "\nfunc init() {\n\tvar First = 1\n\t_ = First\n}\n")
    edges, _ = _resolve(S, tree, nodes, [("load", "First")])
    check("Go control: an indented (function-local) var in another file is not package-level -> dropped",
          ("load", "q_first") not in edges, edges)


# =====================================================================================================
# F — K11 JS: the method's own file declares a value of that name
# =====================================================================================================
_JS = {
    "web/a.js": "class Api {\n  check(x) {\n    return x;\n  }\n}\nvar check = new Api().check;\n",
    "web/c.js": "function check(x) {\n  return x + 1;\n}\n",
    "web/b.js": "function run() {\n  return check(1);\n}\n",
}
_JS_NODES = _mods(_JS) + [_n("api", "Api", "web/a.js", 1, "Class"), _n("api_check", "Api.check", "web/a.js", 2, "Method"),
                          _n("c_check", "check", "web/c.js", 1), _n("b_run", "run", "web/b.js", 1)]


def gate_f_k11_js() -> None:
    print("\n-- F: K11 JS — `var check = new Api().check` beside the method keeps Api.check --")
    S = _load()
    edges, _ = _resolve(S, _JS, _JS_NODES, [("b_run", "check")])
    check("JS: a global `var check = new Api().check` in the method's file -> run keeps Api.check",
          edges.get(("b_run", "api_check")) == "AMBIGUOUS", edges)
    tree = dict(_JS, **{"web/a.js": "class Api {\n  check(x) {\n    return x;\n  }\n}\n"})
    edges, _ = _resolve(S, tree, _JS_NODES, [("b_run", "check")])
    check("JS control: without that declaration the bare check(1) still drops Api.check",
          ("b_run", "api_check") not in edges and edges.get(("b_run", "c_check")) == "INFERRED", edges)


# =====================================================================================================
# G — K11 on the ast path (receiver kinds)
# =====================================================================================================
_AST_K11 = {
    "pkg/__init__.py": "",
    "pkg/backoff.py": _BACKOFF,
    "pkg/util.py": "def check():\n    return 2\n",
    "pkg/server.py": "from pkg.backoff import check\n\n\ndef handle():\n    if check():\n        return 1\n",
    "pkg/rel.py": "from .backoff import check\n\n\ndef handle_rel():\n    return check()\n",
    "pkg/star.py": "from pkg.backoff import *\n\n\ndef handle_star():\n    return check()\n",
    "pkg/svc.py": ("class Svc:\n"
                   "    def __init__(self, b):\n"
                   "        self.b = b\n"
                   "\n"
                   "    def m(self):\n"
                   "        check = self.b.check\n"
                   "        return check()\n"),
    "pkg/defimp.py": "from pkg.util import check\n\n\ndef handle_def():\n    return check()\n",
    "pkg/plain.py": "def handle_plain():\n    return check()\n",
    "reex/__init__.py": "from pkg.backoff import check\n",
    "app/server2.py": "from reex import check\n\n\ndef handle_reex():\n    return check()\n",
}


def gate_g_k11_ast() -> None:
    print("\n-- G: K11 on the ast path — an imported / local bound-method alias keeps the method --")
    S = _load()
    res = {}
    for fcf in (None, "0"):
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_FALSE_CALL_FILTER=fcf):
            root = Path(td)
            _write_tree(root, _AST_K11)
            with contextlib.redirect_stderr(io.StringIO()):
                nodes, calls, _imports = S._rebuild_code_ast(root)
            _attach(S, root, nodes, calls)
            res[fcf] = (S._resolve_calls(nodes, calls), nodes)
    edges, nodes = res[None]
    key = {(n["name"], n["source_file"]): n["key"] for n in nodes}
    ab, util = key.get(("AuthBackoff.check", "pkg/backoff.py")), key.get(("check", "pkg/util.py"))

    def out(caller, sf):
        k = key.get((caller, sf))
        return {t: c for (a, t), c in edges.items() if a == k}

    for caller, sf, why in (("handle", "pkg/server.py", "`from pkg.backoff import check`"),
                            ("handle_rel", "pkg/rel.py", "`from .backoff import check`"),
                            ("handle_star", "pkg/star.py", "`from pkg.backoff import *`"),
                            ("handle_reex", "app/server2.py", "a re-export through reex/__init__.py"),
                            ("Svc.m", "pkg/svc.py", "a local `check = self.b.check`")):
        o = out(caller, sf)
        check(f"ast: {why} -> AuthBackoff.check kept (AMBIGUOUS beside util.check)",
              o.get(ab) == "AMBIGUOUS" and o.get(util) == "AMBIGUOUS", o)
    o = out("handle", "pkg/server.py")
    check("ast: the unrelated util.check is no longer the ONE INFERRED edge of handle", o.get(util) != "INFERRED", o)
    for caller, sf, why in (("handle_def", "pkg/defimp.py", "`from pkg.util import check` (a def)"),
                            ("handle_plain", "pkg/plain.py", "a name the file never binds")):
        o = out(caller, sf)
        check(f"ast control: {why} -> AuthBackoff.check dropped, util.check INFERRED",
              ab not in o and o.get(util) == "INFERRED", o)
    off_edges, off_nodes = res["0"]
    check("ast, DEEPMAP_FALSE_CALL_FILTER=0: nothing attached (no call_alias / call_bare)",
          not any(("call_alias" in n or "call_bare" in n) for n in off_nodes))
    k = key.get(("handle", "pkg/server.py"))
    check("ast, DEEPMAP_FALSE_CALL_FILTER=0: the round-7 resolution (the bare alias call binds no method)",
          (k, ab) not in off_edges and off_edges.get((k, util)) == "INFERRED",
          {t: c for (a, t), c in off_edges.items() if a == k})


# =====================================================================================================
# H — K12: DEEPMAP_LOCAL_DEF_CALLS and a reverse import alias
# =====================================================================================================
_K12 = {
    "pkg/__init__.py": "",
    "pkg/app.py": ("from pkg.base import helper as base_helper\n"  # 1
                   "\n"
                   "\n"
                   "def helper(x):\n"                              # 4
                   "    return x\n"
                   "\n"
                   "\n"
                   "def run():\n"                                  # 8
                   "    a = helper(1)\n"
                   "    b = base_helper(2)\n"
                   "    return a, b\n"
                   "\n"
                   "\n"
                   "def run_nested():\n"                           # 14
                   "    def helper(y):\n"
                   "        return y\n"
                   "    return helper(3), base_helper(4)\n"),
    "pkg/base.py": "def helper(x):\n    return x * 2\n",
    "pkg/other.py": "def helper(x):\n    return x * 3\n",
    "pkg/plain.py": "def helper(x):\n    return x\n\n\ndef go():\n    return helper(5)\n",
}
_K12_NODES = _mods(_K12) + [
    _n("app_helper", "helper", "pkg/app.py", 4), _n("app_run", "run", "pkg/app.py", 8),
    _n("app_run_nested", "run_nested", "pkg/app.py", 14), _n("base_helper", "helper", "pkg/base.py", 1),
    _n("other_helper", "helper", "pkg/other.py", 1), _n("plain_helper", "helper", "pkg/plain.py", 1),
    _n("plain_go", "go", "pkg/plain.py", 5)]


def gate_h_k12() -> None:
    print("\n-- H: K12 — DEEPMAP_LOCAL_DEF_CALLS never drops / re-binds a call made through a reverse alias --")
    S = _load()
    calls = [("app_run", "helper"), ("app_run_nested", "helper"), ("plain_go", "helper")]
    off, _ = _resolve(S, _K12, _K12_NODES, calls)
    on, nodes = _resolve(S, _K12, _K12_NODES, calls, DEEPMAP_LOCAL_DEF_CALLS="1")

    def out(d, k):
        return {t: c for (a, t), c in d.items() if a == k}

    check("precondition: flag OFF, run fans out to all four helpers (AMBIGUOUS)",
          out(off, "app_run") == {t: "AMBIGUOUS" for t in ("app_helper", "base_helper", "other_helper", "plain_helper")},
          out(off, "app_run"))
    check("flag ON, name path: run keeps run -> pkg/base.py:helper (the `base_helper(2)` call)",
          out(on, "app_run").get("base_helper") is not None, out(on, "app_run"))
    check("flag ON, name path: 'helper' is undecided for run -> exactly the flag-OFF edges",
          out(on, "app_run") == out(off, "app_run"), (out(on, "app_run"), out(off, "app_run")))
    check("flag ON: a nested `def helper` beside `base_helper(4)` does not delete the base edge either",
          out(on, "app_run_nested") == out(off, "app_run_nested"), (out(on, "app_run_nested"), out(off, "app_run_nested")))
    check("flag ON, a file with no such alias: go() still binds ONLY its own helper (INFERRED)",
          out(on, "plain_go") == {"plain_helper": "INFERRED"}, out(on, "plain_go"))
    run = next(n for n in nodes if n["key"] == "app_run")
    check("flag ON: run carries no call_local entry for 'helper'", "helper" not in (run.get("call_local") or {}),
          run.get("call_local"))
    _k = S.graphify_extract.call_key          # R11/R1: graphify-RESOLVED callees are MARKED keys
    pcalls = [("app_run", _k("app_helper")), ("app_run", _k("base_helper")), ("plain_go", _k("other_helper"))]
    pe, _ = _resolve(S, _K12, _K12_NODES, pcalls, DEEPMAP_LOCAL_DEF_CALLS="1", DEEPMAP_PRECISE_CALLS="1")
    pe_off, _ = _resolve(S, _K12, _K12_NODES, pcalls, DEEPMAP_PRECISE_CALLS="1")
    check("precise CALLS: the engine's run -> pkg/base.py:helper key is KEPT (not re-bound to the local def)",
          ("app_run", "base_helper") in pe and pe.get(("app_run", "app_helper")) == "EXTRACTED", pe)
    check("precise CALLS: run's edges are exactly the flag-OFF ones (T3#6 grades the cross-file key AMBIGUOUS)",
          out(pe, "app_run") == out(pe_off, "app_run"), (out(pe, "app_run"), out(pe_off, "app_run")))
    check("precise CALLS, no alias: go()'s mis-bound other.helper is still re-bound to its own helper",
          pe.get(("plain_go", "plain_helper")) == "EXTRACTED" and ("plain_go", "other_helper") not in pe, pe)
    same = dict(_K12, **{"pkg/app.py": _K12["pkg/app.py"].replace("import helper as base_helper",
                                                                   "import helper as helper")
                                                          .replace("base_helper(", "helper(")})
    on2, _ = _resolve(S, same, _K12_NODES, calls, DEEPMAP_LOCAL_DEF_CALLS="1")
    off2, _ = _resolve(S, same, _K12_NODES, calls)
    check("`from pkg.base import helper as helper` (the SAME name) stays undecided as before (== flag OFF)",
          out(on2, "app_run") == out(off2, "app_run"), (out(on2, "app_run"), out(off2, "app_run")))
    import ast as _ast
    sc = S._PyModuleScopes(_ast.parse("import a.b\nimport c.d as e\nfrom m import x as y\nfrom n import z\n"
                                      "from p import w as w\n"))
    want = {"x": {"y"}, "d": {"e"}, "b": {"a"}}
    check("_PyModuleScopes.alias_of: ORIGINAL name -> the other identifiers it is bound under (not z / w)",
          getattr(sc, "alias_of", None) == want, getattr(sc, "alias_of", None))
    elsewhere = dict(_K12, **{"pkg/app.py": ("def helper(x):\n"                                   # 1
                                             "    return x\n"
                                             "\n"
                                             "\n"
                                             "def run():\n"                                       # 5
                                             "    return helper(1)\n"
                                             "\n"
                                             "\n"
                                             "def other():\n"                                     # 9
                                             "    from pkg.base import helper as base_helper\n"
                                             "    return base_helper(2)\n")})
    nodes2 = [n for n in _K12_NODES if n["source_file"] != "pkg/app.py"] + [
        _n("mod:pkg/app.py", "app", "pkg/app.py", 1, "Module"), _n("app_helper", "helper", "pkg/app.py", 1),
        _n("app_run", "run", "pkg/app.py", 5), _n("app_other", "other", "pkg/app.py", 9)]
    c2 = [("app_run", "helper"), ("app_other", "helper")]
    on3, _ = _resolve(S, elsewhere, nodes2, c2, DEEPMAP_LOCAL_DEF_CALLS="1")
    off3, _ = _resolve(S, elsewhere, nodes2, c2)
    check("the alias imported and called inside other(): other() keeps -> pkg/base.py:helper",
          out(on3, "app_other").get("base_helper") is not None and out(on3, "app_other") == out(off3, "app_other"),
          (out(on3, "app_other"), out(off3, "app_other")))
    check("... while run(), which never calls the alias, still binds ONLY its own helper (per-caller, no over-keep)",
          out(on3, "app_run") == {"app_helper": "INFERRED"}, out(on3, "app_run"))


# =====================================================================================================
# I — the real engine
# =====================================================================================================
def gate_i_real_engine() -> None:
    print("\n-- I: the REAL engine (graphify) on the reviewers' trees --")
    if importlib.util.find_spec("graphify") is None:
        print("  [SKIP] graphify is not installed in this interpreter — section I not run")
        return
    S = _load()

    def run(tree, **env):
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_HOME=str(Path(td) / "home"),
                                                       DEEPMAP_EXTRACTOR="graphify", **env):
            root = Path(td) / "t"
            _write_tree(root, tree)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                nodes, calls, _imports, engine = S._rebuild_code(root)
            _attach(S, root, nodes, calls)
            edges = S._resolve_calls(nodes, calls)
        by = {(n["name"], n["source_file"]): n["key"] for n in nodes}
        return engine, by, edges

    go = {"store.go": _GO_K10["store.go"],
          "svc.go": "package p\n\nfunc First() error {\n\treturn nil\n}\n\nfunc Load(q *Query) error {\n"
                    "\treturn q.\n\t\tWhere(\"x\").\n\t\tFirst()\n}\n"}
    engine, by, edges = run(go)
    load, qf, sf = by.get(("Load", "svc.go")), by.get(("Query.First", "store.go")), by.get(("First", "svc.go"))
    check("real engine, Go: Load keeps Query.First (the gofmt chain `q.` / `First()`)",
          engine == "graphify" and load and qf and (load, qf) in edges, (engine, [k for k in edges if k[0] == load]))
    check("real engine, Go: the mis-bind Load -> svc.First is not upgraded to INFERRED",
          edges.get((load, sf)) != "INFERRED", edges.get((load, sf)))
    ts = {"api.ts": _TS_K10["api.ts"],
          "svc.ts": "import { Api } from \"./api\";\n\nfunction check(x: number): number {\n  return x + 1;\n}\n\n"
                    "export function load(api: Api): number {\n  return api.\n    check(2);\n}\n"}
    engine, by, edges = run(ts)
    ld, ac = by.get(("load", "svc.ts")), by.get(("Api.check", "api.ts"))
    check("real engine, TS: load keeps Api.check (`api.` / `check(2)`)", ld and ac and (ld, ac) in edges,
          [k for k in edges if k[0] == ld])
    k11 = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF, "pkg/util.py": _REV["pkg/util.py"],
           "pkg/rev.py": _REV["pkg/rev.py"], "pkg/server.py": _PY_K11["pkg/server.py"]}
    engine, by, edges = run(k11)
    ab = by.get(("AuthBackoff.check", "pkg/backoff.py"))
    hs, hr, ho = (by.get(("handle", "pkg/server.py")), by.get(("handle_rev", "pkg/rev.py")),
                  by.get(("handle_own", "pkg/rev.py")))
    check("real engine, Python: `from pkg.backoff import check` (a bound-method alias) -> handle keeps "
          "AuthBackoff.check", hs and ab and (hs, ab) in edges, [k for k in edges if k[0] == hs])
    check("real engine, Python: `check(), base_check()` (reverse alias; the engine's own link is dropped at map "
          "time) -> the push keeps handle_rev -> AuthBackoff.check", hr and ab and (hr, ab) in edges,
          [k for k in edges if k[0] == hr])
    check("real engine, Python control: handle_own() calls only its own check() -> no AuthBackoff.check edge",
          ho and ab and (ho, ab) not in edges, [k for k in edges if k[0] == ho])
    k12 = {k: v for k, v in _K12.items() if k != "pkg/plain.py"}
    for env in ({"DEEPMAP_LOCAL_DEF_CALLS": "1"}, {"DEEPMAP_LOCAL_DEF_CALLS": "1", "DEEPMAP_PRECISE_CALLS": "1"}):
        engine, by, edges = run(k12, **env)
        r, b = by.get(("run", "pkg/app.py")), by.get(("helper", "pkg/base.py"))
        check(f"real engine, {' + '.join(env)}: run keeps run -> pkg/base.py:helper", r and b and (r, b) in edges,
              [k for k in edges if k[0] == r])


# =====================================================================================================
# J — DEEPMAP_FALSE_CALL_FILTER=0: nothing attached, the round-7 resolution
# =====================================================================================================
def gate_j_optout() -> None:
    print("\n-- J: DEEPMAP_FALSE_CALL_FILTER=0 — nothing attached, the round-7 resolution --")
    S = _load()
    for label, tree, nodes, calls in (
            ("Go K10", _GO_K10, _GO_K10_NODES, _GO_K10_CALLS), ("TS K10", _TS_K10, _TS_K10_NODES, _TS_K10_CALLS),
            ("Python K11", _PY_K11, _PY_K11_NODES, [(k, "check") for k, _w in _KEEP_PY + _DROP_PY]),
            ("JS K11", _JS, _JS_NODES, [("b_run", "check")])):
        edges, got = _resolve(S, tree, nodes, calls, DEEPMAP_FALSE_CALL_FILTER="0")
        meta = [n["key"] for n in got if "call_bare" in n or "call_alias" in n]
        oracle = _r7_oracle(got, calls)
        check(f"{label}: no call-site metadata, and _resolve_calls == the round-7 fan-out (same order)",
              not meta and list(edges.items()) == list(oracle.items()),
              (meta, {k: (edges.get(k), oracle.get(k)) for k in set(edges) ^ set(oracle)}))


# =====================================================================================================
# K — the cost guards of the ast-path K11 rule are EXACT (no change in what is kept)
# =====================================================================================================
_FACTS_SRC = (
    "from a import b as c, d\n"
    "from . import *\n"
    "import os.path as osp\n"
    "x = 1\n"
    "y: int = 2\n"
    "for i, (j, k) in []:\n"
    "    pass\n"
    "with open('f') as fh:\n"
    "    pass\n"
    "try:\n"
    "    pass\n"
    "except ValueError as err:\n"
    "    pass\n"
    "if (w := 3):\n"
    "    del x\n"
    "f = lambda q: (z := q)\n"
    "vals = [m for m in range(3)]\n"
    "\n"
    "\n"
    "@deco\n"
    "def outer(p, *a, kw=1, **k):\n"
    "    from e import g\n"
    "    global gl\n"
    "    inner_v = 1\n"
    "\n"
    "    def inner():\n"
    "        return inner_v\n"
    "    return inner\n"
    "\n"
    "\n"
    "class K:\n"
    "    attr = 1\n"
    "\n"
    "    async def m(self):\n"
    "        return self\n"
    "\n"
    "\n"
    "match x:\n"
    "    case {'k': cap, **rest}:\n"
    "        pass\n"
    "    case [first, *more]:\n"
    "        pass\n")


def gate_k_cost_guards() -> None:
    print("\n-- K: the ast-path cost guards are exact --")
    S = _load()
    import ast as _ast
    only = getattr(S, "_py_name_only_called", None)
    facts = getattr(S, "_py_file_facts", None)
    check("sync has _py_name_only_called and _py_file_facts", callable(only) and callable(facts))
    if not (callable(only) and callable(facts)):
        return
    binds = ["check = obj.check\ncheck()\n", "def f(check):\n    return check()\n", "def f(check=1):\n    check()\n",
             "for check in xs:\n    check()\n", "with a as check:\n    check()\n", "try:\n    pass\nexcept E as check:\n    check()\n",
             "global check\ncheck()\n", "from m import check\ncheck()\n", "from m import (\n    check,\n)\ncheck()\n",
             "from m import check as c\ncheck()\n", "import check\ncheck()\n", "(check := f)\ncheck()\n",
             "a, check = 1, 2\ncheck()\n", "check: T = f\ncheck()\n", "del check\ncheck()\n", "lambda check: check()\n",
             "match v:\n    case check:\n        check()\n", "from m import *\ncheck()\n", "nonlocal check\ncheck()\n",
             "f(check=1)\ncheck()\n", "@check\ndef g():\n    pass\n"]
    wrong = [src for src in binds if only(src, "check")]
    check(f"_py_name_only_called: every binding form (and a keyword arg / decorator) -> not provably call-only "
          f"({len(binds)} forms)", not wrong, wrong)
    calls_only = ["def check(a):\n    return a\n\n\ncheck(1)\nx = obj.check\nobj .check(2)\nprecheck = 1\ncheck_it = 2\n",
                  "class check(Base):\n    pass\ncheck ()\n"]
    bad = [src for src in calls_only if not only(src, "check")]
    check("_py_name_only_called: only calls / `def name(` / `class name(` / members / longer identifiers -> True",
          not bad, bad)
    tree = _ast.parse(_FACTS_SRC)
    defs, spans, imps, values = facts(tree)
    odefs = {}
    for d in _ast.walk(tree):
        if isinstance(d, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            for ln in {d.lineno, *(x.lineno for x in d.decorator_list)}:
                odefs.setdefault((ln, d.name), d)
    tab = S._PyImportValues(Path("."), []).table("x.py", tree)
    check("_py_file_facts: module values == _py_value_bindings", values == S._py_value_bindings(tree),
          (sorted(values), sorted(S._py_value_bindings(tree))))
    check("_py_file_facts: defs / function spans (in ast.walk order) / from-imports == the separate walks",
          defs == odefs and [id(d) for _a, _b, d in spans] == [id(d) for _a, _b, d in S._py_function_spans(tree)]
          and all(imps[k] == tab[k] for k in ("from", "orig", "stars")),
          (sorted(defs), sorted(odefs), imps, {k: tab[k] for k in ("from", "orig", "stars")}))


def main() -> int:
    for g in (gate_a_unit, gate_b_k10_e2e, gate_c_parity, gate_d_k11_python, gate_d2_reverse_alias, gate_e_k11_go,
              gate_f_k11_js, gate_g_k11_ast, gate_h_k12, gate_i_real_engine, gate_j_optout, gate_k_cost_guards):
        try:
            g()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a failing gate, never a silent pass
            import traceback
            traceback.print_exc()
            check(f"{g.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
