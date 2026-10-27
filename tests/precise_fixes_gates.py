#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 gates (typed-receiver call-linking design §5 "Precise fixes and filters", §6.1, §7.1, §9 S1): the two
corrections of precise CALLS. Both are ON wherever DEEPMAP_PRECISE_CALLS is on for the caller's family;
DEEPMAP_PRECISE_FIXES=0 restores today's precise output byte for byte (frozen — also precise_family_gates B). No
Neo4j, no network, no spend.

  TS value-over-type re-key — TypeScript declares a type and a value of one name side by side (zod's
      `export type OK<T> = ...` + `export const OK = ...`); graphify gives both ONE id, keeps the TYPE, and binds
      every `OK(...)` to it, so precise CALLS keyed 14 zod calls to a type alias and the const (recovered from
      source as `<id>@OK:L<n>`) had no caller. A precise key to a TS/JS :Class whose declaration line is a `type` /
      `interface` now binds the one VALUE declaration of the same file that collided with it (same engine id) and
      that the caller calls by name — read with tree-sitter, by scope: a bare `OK(` / `OK<T>(` / `OK?.(` that no
      enclosing parameter, local, nested declaration or catch binding shadows (and, in another file, a name the file
      imports), or `ns.OK(` through a namespace the caller's file imports and nothing shadows; never `new OK(`, a
      member call on anything else (`r.OK(`, `this.OK(`, an unbound `ns.OK(`), a longer chain. Anything else
      becomes the name, exactly the flag-off tuple. A real class, an enum, a type line declaring another name, a
      value in ANOTHER file, two candidate values, an unreadable file: never re-keyed to a value.
  P1b — graphify 0.8.39 also links `Order()` to a placeholder node named after an annotation (`o: Order`) that
      map_graph does not keep, so precise CALLS gave the caller the key AND the bare name (the probe tree: 11 tuples
      on 0.8.39, 9 on 0.8.51). That name is dropped when the caller holds a precise key whose last name segment is
      the placeholder's label and its body has no more `Order(` sites (comments not read; strings counted) than
      such keys. Two sites, no key of that label, a name another path also produced, an unreadable or over-long
      body (a :Module caller: a file over 5000 lines), a call form the counter cannot read (`Order?.(`,
      `Order[int](`, `Order \\` + `(`, `Order.call(`, a tagged template, `new Order` without parentheses, a call
      after a JS `//` that may be a regex literal): the name stays. Each span is scanned once, whatever the
      number of candidate names.

  J3 on keys (review of S1b) — a call written WITHOUT a receiver never reaches a method in Python, JS / TS, Go or
      Rust, but graphify binds a bare `get_host(environ)` (werkzeug's `from ..wsgi import get_host`) to the same
      file's MapAdapter.get_host, and precise CALLS made that a firm EXTRACTED edge (werkzeug Map.bind_to_environ,
      click Command._main_shell_completion -> Parameter.shell_complete, both engines). The push-time J3 rule only
      judges NAMES, so it never saw these keys. A precise key to a :Method the caller cannot reach bare
      (sync._bare_unreachable: another file, or another class of the caller's file) is now DROPPED when the
      engine's line calls the name only bare, no other line of the caller's body calls it through a receiver,
      and the name cannot be a VALUE holding a bound method (R9/K11: a parameter, a local, a module value, a Go
      package var, a name a TS file imports...). A :Module caller is read over its whole file. The same class, a
      class-body caller, Java / C# / Ruby (an implicit this), a :Function target, a line with no visible call, a
      body longer than the reader's span, a :Module file over 5000 lines: the key stays. DEEPMAP_PRECISE_FIXES=0
      or DEEPMAP_FALSE_CALL_FILTER=0 restore today's keys; with PRECISE off nothing changes.

Sections: F TS re-key on the zod-shaped capture (both engines) · F2 the re-key's scope rules on a captured tree
(both engines) · G TS re-key shapes (hand-made graphs) · H P1b on the captures (both engines) · I P1b shapes
(hand-made graphs) · K P1b's counter: unreadable call forms keep the name; one scan per span; the Module cap ·
L J3 on keys on a captured tree (both engines, and the installed engine) · M J3-on-keys shapes (hand-made graphs) ·
J stats, the sync-log line and the unknown-family warning.
Fixtures: tests/fixtures/typed_calls/<0839|0851>/precise/ (see precise_family_gates); bare_keys.json is BARE_TREE's
capture (`python tests/precise_fixes_gates.py --capture` under each engine after editing BARE_TREE).
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import precise_family_gates as PF  # noqa: E402  (fixtures, trees, the canonical map_graph runner)

GX = PF.GX
BUCKETS = PF.BUCKETS
_PASS = _FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail != '' else ''}")


ON = {"DEEPMAP_PRECISE_CALLS": "1"}
OFF_FIXES = {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_PRECISE_FIXES": "0"}


def run(graph, root, env, stats=None) -> list:
    return PF.outputs(GX, graph, root, env, stats=stats)["calls"]


def frozen_calls(bucket: str, eid: str, mode: str) -> list:
    fz = PF.frozen(bucket)
    return fz["entries"][eid][mode]["calls"] if fz else None


# ================================================================================================
# F — the TS value-over-type re-key on the zod-shaped capture
# ================================================================================================
TYPE_OK, TYPE_DIRTY = "src_parseutil_ok", "src_parseutil_dirty"
VALUE_OK, VALUE_DIRTY = "src_parseutil_ok@OK:L10", "src_parseutil_dirty@DIRTY:L7"


def gate_f_rekey_capture() -> None:
    print("\n-- F: zod's `type OK<T>` + `const OK`: precise calls bind the const, both engines --")
    per_ver = {}
    for ver, b in BUCKETS.items():
        graph = PF.precise_graph(b, "rekey_ts")
        root = PF.root_for(b, "precise/rekey_ts", PF.TREES["rekey_ts"])
        before = frozen_calls(b, "precise/rekey_ts", "p1")
        typed = [c for c in before if c[2] and c[1] in (TYPE_OK, TYPE_DIRTY)]
        check(f"{ver}: today's PRECISE=1 keys 14 calls to the TYPE aliases (13 OK + 1 DIRTY: zod's 14)",
              len(typed) == 14 and sum(1 for c in typed if c[1] == TYPE_OK) == 13, typed)
        st: dict = {}
        got = run(graph, root, ON, st)
        per_ver[ver] = got
        want = []
        for s, c, k in before:
            if k and c == TYPE_OK:
                want.append([s, VALUE_OK, True])
            elif k and c == TYPE_DIRTY:
                want.append([s, VALUE_DIRTY, True])
            elif k and c in ("src_fmt_a_fmt", "src_parseutil_shape"):
                want.append([s, {"src_fmt_a_fmt": "Fmt", "src_parseutil_shape": "Shape"}[c], False])
            else:
                want.append([s, c, k])
        check(f"{ver}: the 13 OK calls bind `const OK` and the DIRTY call `const DIRTY`; every other tuple is "
              f"today's, in today's order", got == want, [g for g, w in zip(got, want) if g != w] or (got, want))
        check(f"{ver}: no precise key reaches a type alias any more",
              not any(k and c in (TYPE_OK, TYPE_DIRTY) for _s, c, k in got))
        check(f"{ver}: `Fmt(x)` keyed to `type Fmt` in fmt_a.ts -> the name, never fmt_b.ts's `const Fmt` "
              f"(a value in ANOTHER file)", ["src_fmt_use_show", "Fmt", False] in got
              and not any(c == "src_fmt_b_fmt" for _s, c, _k in got), [c for c in got if c[0] == "src_fmt_use_show"])
        check(f"{ver}: `Shape()` keyed to `interface Shape` (no value of that name) -> the name",
              ["src_types_makeshape", "Shape", False] in got)
        check(f"{ver}: `OK<any>(input)` (a generic call) and a commented `// OK(x)` above `OK(x)` bind the const",
              ["src_types_zodnumber_parse", VALUE_OK, True] in got and ["src_types_wrapok", VALUE_OK, True] in got)
        nodes = {n["key"]: n for n in GX.map_graph(PF.precise_graph(b, "rekey_ts"), root=root)[0]}
        check(f"{ver}: each re-keyed target is a value node of the type's own file",
              all(nodes[v]["source_file"] == nodes[t]["source_file"] == "src/parseUtil.ts"
                  and nodes[v]["label"] == "Function" for v, t in ((VALUE_OK, TYPE_OK), (VALUE_DIRTY, TYPE_DIRTY))))
        check(f"{ver}: stats: precise_type_rekeyed 14, precise_type_names 2",
              st.get("precise_type_rekeyed") == 14 and st.get("precise_type_names") == 2,
              {k: v for k, v in st.items() if k.startswith("precise")})
        check(f"{ver}: PRECISE=js (the TS family) == PRECISE=1 on this all-TS tree",
              run(graph, root, {"DEEPMAP_PRECISE_CALLS": "js"}) == got)
        check(f"{ver}: PRECISE=python: no TS caller is precise -> the default tuples, no re-key",
              run(graph, root, {"DEEPMAP_PRECISE_CALLS": "python"}) == frozen_calls(b, "precise/rekey_ts", "base"))
        check(f"{ver}: DEEPMAP_PRECISE_FIXES=0 -> today's keys to the type aliases, byte for byte",
              run(graph, root, OFF_FIXES) == before)
    if len(per_ver) == 2:
        a, b2 = (sorted(map(tuple, per_ver[v])) for v in BUCKETS)
        check("0.8.39 and 0.8.51 give the identical precise tuples on the zod-shaped tree", a == b2,
              set(a) ^ set(b2))


# ================================================================================================
# F2 — the re-key's scope rules on a captured tree (review of S1, finding 1)
# ================================================================================================
SCOPE_TYPE, SCOPE_VALUE = "src_ok_ok", "src_ok_ok@OK:L2"
SCOPE_WANT = {   # caller -> (callee under PRECISE=1 with the fixes, why)
    "src_ok_res_wrap": ("OK", "`r.OK(1)` in a method, `r` a parameter: a member call -> the name (it used to bind the "
                              "const: a key to a module-level def from a member call)"),
    "src_ok_shadow": ("OK", "`function shadow(OK: ...) { return OK(2); }`: the parameter shadows the const -> the name "
                            "(it used to be a false same-file EXTRACTED caller of the const)"),
    "src_ok_local": ("OK", "`const OK = ...; return OK(3);` inside the caller: a local -> the name"),
    "src_ok_nested": (SCOPE_VALUE, "an arrow's `OK` parameter shadows only inside the arrow: the outer `OK(4)` binds "
                                   "the const"),
    "src_ok_firstnew": (SCOPE_VALUE, "the engine's one link sits on `new OK(6)`; the body's `OK(7)` binds the const"),
    "src_noimp_noimport": ("OK", "a bare `OK(1)` in a file that never imports OK -> the name"),
    "src_use_viabare": (SCOPE_VALUE, "`import { OK } from \"./ok\"` + a bare `OK(2)` binds the const"),
}


def gate_f2_rekey_scope_capture() -> None:
    print("\n-- F2: the re-key reads the caller's scope (captured tree, both engines) --")
    per_ver = {}
    for ver, b in BUCKETS.items():
        if not PF.fixture_path(b, "rekey_scope_ts").is_file():
            check(f"{ver}: the rekey_scope_ts tree is captured", False)
            continue
        graph = PF.precise_graph(b, "rekey_scope_ts")
        root = PF.root_for(b, "precise/rekey_scope_ts", PF.TREES["rekey_scope_ts"])
        before = frozen_calls(b, "precise/rekey_scope_ts", "p1")
        check(f"{ver}: today's PRECISE=1 keys the 7 linked callers to the type alias",
              sorted(s for s, c, k in before if k and c == SCOPE_TYPE) == sorted(SCOPE_WANT) and len(before) == 7,
              before)
        st: dict = {}
        got = run(graph, root, ON, st)
        per_ver[ver] = got
        for caller, (callee, why) in SCOPE_WANT.items():
            mine = [c for c in got if c[0] == caller]
            check(f"{ver}: {why}", mine == [[caller, callee, callee == SCOPE_VALUE]], mine)
        check(f"{ver}: the tuples keep today's order (only the callee changes)",
              [c[0] for c in got] == [c[0] for c in before], got)
        check(f"{ver}: stats: precise_type_rekeyed 3, precise_type_names 4",
              st.get("precise_type_rekeyed") == 3 and st.get("precise_type_names") == 4,
              {k: v for k, v in st.items() if k.startswith("precise")})
        check(f"{ver}: DEEPMAP_PRECISE_FIXES=0 -> today's keys to the type alias, byte for byte",
              run(graph, root, OFF_FIXES) == before)
    if len(per_ver) == 2:
        a, b2 = (sorted(map(tuple, per_ver[v])) for v in BUCKETS)
        check("0.8.39 and 0.8.51 give the identical precise tuples on the scope tree", a == b2, set(a) ^ set(b2))


# ================================================================================================
# G — re-key shapes on hand-made graphs (must and must-not)
# ================================================================================================
def make_graph(files: dict, entities: list, calls: list) -> dict:
    """A graph.json in graphify's shape: a file node per file, `entities` [(id, label, file, line, owner id or
    None)] contained in their file (or methods of `owner`), `calls` [(src, tgt, line)]."""
    fid = {rel: "f_" + rel.replace("/", "_").replace(".", "_") for rel in files}
    nodes = [{"id": fid[rel], "label": rel.rsplit("/", 1)[-1], "file_type": "code", "source_file": rel,
              "source_location": "L1"} for rel in files]
    links = []
    for nid, label, rel, line, owner in entities:
        nodes.append({"id": nid, "label": label, "file_type": "code", "source_file": rel,
                      "source_location": f"L{line}"})
        links.append({"source": owner or fid[rel], "target": nid, "relation": "method" if owner else "contains",
                      "source_file": rel, "source_location": f"L{line}"})
    for s, t, line in calls:
        rel = next(n["source_file"] for n in nodes if n["id"] == s)
        links.append({"source": s, "target": t, "relation": "calls", "source_file": rel,
                      "source_location": f"L{line}"})
    return {"nodes": nodes, "links": links}


def shape(name: str, files: dict, entities: list, calls: list, env=None, *, write=None) -> list:
    root = PF.write_tree(PF.tmp() / "shapes" / name, files if write is None else write)
    return run(make_graph(files, entities, calls), root, ON if env is None else env)


TS_OK = """\
export type OK<T> = { v: T };
export const OK = <T>(v: T): OK<T> => ({ v });
export function make() {
  return CALL;
}
"""


def gate_g_rekey_shapes() -> None:
    print("\n-- G: re-key shapes (hand-made graphs, real source) --")
    ents = [("a_ok", "OK", "a.ts", 1, None), ("a_make", "make()", "a.ts", 3, None)]
    for i, (call, want, why) in enumerate((
            ("OK(1)", ["a_make", "a_ok@OK:L2", True], "a value call binds the const"),
            ("OK<number>(1)", ["a_make", "a_ok@OK:L2", True], "a generic call binds the const"),
            ("OK<Array<number>>([1])", ["a_make", "a_ok@OK:L2", True], "a nested generic call binds the const"),
            ("OK?.(1)", ["a_make", "a_ok@OK:L2", True], "an optional call binds the const"),
            ("ns.OK(1)", ["a_make", "OK", False], "`ns` is bound nowhere in the file: a member call -> the name"),
            ("this.OK(1)", ["a_make", "OK", False], "a member call on `this` -> the name"),
            ("new OK(1)", ["a_make", "OK", False], "`new OK(` is not a value call -> the name"),
            ("new ns.OK(1)", ["a_make", "OK", False], "`new ns.OK(` neither -> the name"),
            ("`OK(${1})`", ["a_make", "OK", False], "`OK(` inside a template string is no call -> the name"))):
        got = shape(f"ok_{i}", {"a.ts": TS_OK.replace("CALL", call)}, ents, [("a_make", "a_ok", 4)])
        check(f"`return {call};` keyed to `type OK` -> {why}", got == [want], got)
    # the caller's own scope (review of S1, finding 1): a binding of `OK` that encloses the call shadows the const
    head = "export type OK<T> = { v: T };\nexport const OK = <T>(v: T): OK<T> => ({ v });\n"
    const, name = ["a_make", "a_ok@OK:L2", True], ["a_make", "OK", False]
    for i, (body, marker, want, why) in enumerate((
            ("export function make(r: any) {\n  return r.OK(1);\n}\n", "OK(1)", name,
             "an unbound `r.OK(1)` (a parameter's member) -> the name"),
            ("export function make(OK: (x: number) => number) {\n  return OK(1);\n}\n", "OK(1)", name,
             "a shadowing parameter `OK` -> the name"),
            ("export function make() {\n  const OK = (x: number) => x;\n  return OK(1);\n}\n", "OK(1)", name,
             "a local `const OK` -> the name"),
            ("export function make(deps: any) {\n  const { OK } = deps;\n  return OK(1);\n}\n", "OK(1)", name,
             "a destructured local `{ OK }` -> the name"),
            ("export function make(xs: any[]) {\n  for (const OK of xs) {\n    OK(1);\n  }\n}\n", "OK(1)", name,
             "a `for (const OK of ...)` binding -> the name"),
            ("export function make() {\n  try {\n    return 0;\n  } catch (OK) {\n    return OK(1);\n  }\n}\n",
             "OK(1)", name, "a `catch (OK)` binding -> the name"),
            ("export function make() {\n  function OK(x: number) {\n    return x;\n  }\n  return OK(1);\n}\n",
             "OK(1)", name, "a nested `function OK` -> the name"),
            ("export function make(xs: any[]) {\n  return xs.map((OK: any) => OK(1));\n}\n", "OK(1)", name,
             "an arrow parameter `OK` shadows the only call -> the name"),
            ("export function make(xs: any[]) {\n  xs.map((OK: any) => OK(0));\n  return OK(1);\n}\n", "OK(1)",
             const, "an arrow parameter shadows only inside the arrow: the outer `OK(1)` binds the const"),
            ("export function make() {\n  const a = new OK(0);\n  return [a, OK(1)];\n}\n", "new OK(0)", const,
             "the engine's ONE link sits on `new OK(0)` (0.8.51 keeps one link per pair): the body's `OK(1)` still "
             "binds the const"),
            ("export function make() {\n  const a = new OK(0);\n  return a;\n}\n", "new OK(0)", name,
             "...and with no other call there, `new OK(0)` alone -> the name"))):
        src = head + body
        line = next(n for n, t in enumerate(src.split("\n"), 1) if marker in t)
        got = shape(f"scope_{i}", {"a.ts": src}, ents, [("a_make", "a_ok", line)])
        check(why, got == [want], got)
    # another file: a bare call needs the name imported; `ns.OK(` needs `ns` imported (and not shadowed)
    a_ts = {"a.ts": head}
    for i, (b_ts, want, why) in enumerate((
            ('import * as ns from "./a";\n\nexport function use() {\n  return ns.OK(1);\n}\n', True,
             "`import * as ns from \"./a\"` + `ns.OK(1)` binds the const"),
            ('const ns = require("./a");\n\nexport function use() {\n  return ns.OK(1);\n}\n', True,
             "`const ns = require(\"./a\")` + `ns.OK(1)` binds the const"),
            ('import { OK } from "./a";\n\nexport function use() {\n  return OK(1);\n}\n', True,
             "`import { OK } from \"./a\"` + a bare `OK(1)` binds the const"),
            ('export function use() {\n  return OK(1);\n}\n', False,
             "a bare `OK(1)` the file never imports -> the name"),
            ('import * as ns from "./a";\n\nexport function use(r: any) {\n  return r.OK(1);\n}\n', False,
             "`r.OK(1)` beside an unrelated namespace import -> the name"),
            ('import * as ns from "./a";\n\nexport function use(ns: any) {\n  return ns.OK(1);\n}\n', False,
             "a parameter `ns` shadows the namespace import -> the name"),
            ('import * as ns from "./a";\n\nexport function use() {\n  return ns.inner.OK(1);\n}\n', False,
             "a two-step chain `ns.inner.OK(1)` -> the name"),
            ('import { OK } from "./a";\n\nexport function use(OK: any) {\n  return OK(1);\n}\n', False,
             "a parameter shadows the imported `OK` -> the name"))):
        line = next(n for n, t in enumerate(b_ts.split("\n"), 1) if "OK(1)" in t)
        start = next(n for n, t in enumerate(b_ts.split("\n"), 1) if "function use" in t)
        got = shape(f"cross_{i}", dict(a_ts, **{"b.ts": b_ts}),
                    [("a_ok", "OK", "a.ts", 1, None), ("b_use", "use()", "b.ts", start, None)],
                    [("b_use", "a_ok", line)])
        check(f"another file: {why}", got == ([["b_use", "a_ok@OK:L2", True]] if want else [["b_use", "OK", False]]),
              got)
    # no tree-sitter tree for the CALLER's file (no grammar, an unreadable file): no call is proven -> the name
    real_ts_tree = GX._Source.ts_tree
    try:
        GX._Source.ts_tree = lambda self, sf: None if str(sf) == "b.ts" else real_ts_tree(self, sf)
        got = shape("no_tree", dict(a_ts, **{"b.ts": 'import { OK } from "./a";\n\nexport function use() {\n'
                                                       '  return OK(1);\n}\n'}),
                    [("a_ok", "OK", "a.ts", 1, None), ("b_use", "use()", "b.ts", 3, None)], [("b_use", "a_ok", 4)])
    finally:
        GX._Source.ts_tree = real_ts_tree
    check("the caller's file has no tree-sitter tree: no call of the const is proven -> the name (never a guess)",
          got == [["b_use", "OK", False]], got)
    # only the caller's own lines and the engine's link lines are read: a link line outside the caller (here line 1)
    # does not make the code between it and the caller (another function's `OK(1)`) the caller's
    far = head + ("export function other() {\n  return OK(1);\n}\nexport function make() {\n  return new OK(0);\n}\n"
                  "export function tail() {\n  return 0;\n}\n")
    got = shape("far_link", {"a.ts": far}, [("a_ok", "OK", "a.ts", 1, None), ("a_other", "other()", "a.ts", 3, None),
                                            ("a_make", "make()", "a.ts", 6, None), ("a_tail", "tail()", "a.ts", 9, None)],
                [("a_other", "a_ok", 4), ("a_make", "a_ok", 1)])
    check("a caller's link recorded on line 1: another function's `OK(1)` between that line and the caller is not "
          "read -> `new OK(0)` alone gives the name (the other function's own call binds the const)",
          got == [["a_other", "a_ok@OK:L2", True], ["a_make", "OK", False]], got)
    # a type node whose declaration line declares ANOTHER name is not read as that type (mutant Md)
    other = ("export type Other = { v: number };\nexport const OK = (v: number) => v;\n"
             "export function make() {\n  return OK(1);\n}\n")
    got = shape("other_name", {"a.ts": other}, ents, [("a_make", "a_ok", 4)])
    check("a node `OK` whose line declares `type Other` is not a type of its own name: the key stays",
          got == [["a_make", "a_ok", True]], got)
    # a type key that becomes the name goes BEFORE the caller's own keys (mutant Ms): the push grades a repeated pair by
    # its last tuple, so `Fmt`'s fan-out after the key to fmt_b's const would demote that EXTRACTED edge
    fmt = {"fmt_a.ts": "export type Fmt = (x: number) => string;\n",
           "fmt_b.ts": "export const Fmt = (x: number): string => String(x);\n",
           "use.ts": 'import { Fmt } from "./fmt_b";\n\nexport function show(x: number) {\n  return Fmt(x);\n}\n'}
    got = shape("fmt_order", fmt, [("a_fmt", "Fmt", "fmt_a.ts", 1, None), ("b_fmt", "Fmt()", "fmt_b.ts", 1, None),
                                   ("u_show", "show()", "use.ts", 3, None)],
                [("u_show", "b_fmt", 4), ("u_show", "a_fmt", 4)])
    check("a key to `type Fmt` (no value in fmt_a.ts) becomes the name, placed BEFORE the caller's key to fmt_b's "
          "const", got == [["u_show", "Fmt", False], ["u_show", "b_fmt", True]], got)
    # precise is decided by the CALLER's file (mutant Mq): a hand-made cross-family link, keyed under the caller's family
    xf = {"web/a.ts": "export function run() {\n  return helper(1);\n}\n", "py/b.py": "def helper(x):\n    return x\n"}
    xf_ents = [("w_run", "run()", "web/a.ts", 1, None), ("p_helper", "helper()", "py/b.py", 1, None)]
    got_js = shape("xfam_js", xf, xf_ents, [("w_run", "p_helper", 2)], {"DEEPMAP_PRECISE_CALLS": "js"})
    got_py = shape("xfam_py", xf, xf_ents, [("w_run", "p_helper", 2)], {"DEEPMAP_PRECISE_CALLS": "python"})
    check("a .ts caller's link to a .py def: keyed under PRECISE=js (the caller's family), the name under "
          "PRECISE=python (the callee's)", got_js == [["w_run", "p_helper", True]] and got_py == [["w_run", "helper", False]],
          (got_js, got_py))
    got = shape("ok_fixes0", {"a.ts": TS_OK.replace("CALL", "OK(1)")}, ents, [("a_make", "a_ok", 4)], OFF_FIXES)
    check("DEEPMAP_PRECISE_FIXES=0: the key to the type alias stays (today)", got == [["a_make", "a_ok", True]], got)
    got = shape("ok_py", {"a.ts": TS_OK.replace("CALL", "OK(1)")}, ents, [("a_make", "a_ok", 4)],
                {"DEEPMAP_PRECISE_CALLS": "python"})
    check("PRECISE=python: a TS caller is not precise -> the plain name, no re-key", got == [["a_make", "OK", False]],
          got)
    # a real class: `new Box(1)` (and even a bare `Box(1)`) keeps the key — the re-key reads only type / interface
    box = "export class Box {\n  open() {\n    return 1;\n  }\n}\nexport function make() {\n  return CALL;\n}\n"
    for call in ("new Box(1)", "Box(1)"):
        got = shape(f"box_{len(call)}", {"a.ts": box.replace("CALL", call)},
                    [("a_box", "Box", "a.ts", 1, None), ("a_box_open", ".open()", "a.ts", 2, "a_box"),
                     ("a_make", "make()", "a.ts", 6, None)], [("a_make", "a_box", 7)])
        check(f"a real `class Box` with `{call}` keeps its key (a class is never re-keyed)",
              got == [["a_make", "a_box", True]], got)
    enum = "export enum Color {\n  Red,\n}\nexport function paint() {\n  return Color(1);\n}\n"
    got = shape("enum", {"a.ts": enum},
                [("a_color", "Color", "a.ts", 1, None), ("a_paint", "paint()", "a.ts", 4, None)],
                [("a_paint", "a_color", 5)])
    check("an `enum` target keeps its key (only `type` / `interface` are re-keyed)",
          got == [["a_paint", "a_color", True]], got)
    two = ("export type Pair = [number, number];\nexport const Pair = (a: number): Pair => [a, a];\n"
           "export function Pair(a: number) {\n  return a;\n}\nexport function use() {\n  return Pair(1);\n}\n")
    got = shape("two", {"a.ts": two}, [("a_pair", "Pair", "a.ts", 1, None), ("a_use", "use()", "a.ts", 6, None)],
                [("a_use", "a_pair", 7)])
    check("two value declarations of the name in the file -> the name (never a guess between them)",
          got == [["a_use", "Pair", False]], got)
    coll = ("export type $Parse = (x: number) => number;\nexport const _parse = (x: number): number => x;\n"
            "export function run() {\n  return _parse(1);\n}\n")
    got = shape("coll", {"a.ts": coll}, [("a_parse", "$Parse", "a.ts", 1, None), ("a_run", "run()", "a.ts", 3, None)],
                [("a_run", "a_parse", 4)])
    check("zod's `type $Parse` + `const _parse` (one engine id): `_parse(1)` binds the const it names",
          got == [["a_run", "a_parse@_parse:L2", True]], got)
    coll2 = ("export type $Parse = (x: number) => number;\nexport const _parse = (x: number): number => x;\n"
             "export function run() {\n  return $Parse(1);\n}\n")
    got = shape("coll2", {"a.ts": coll2}, [("a_parse", "$Parse", "a.ts", 1, None), ("a_run", "run()", "a.ts", 3, None)],
                [("a_run", "a_parse", 4)])
    check("...but a caller that never names the value (`$Parse(1)`) gets the name, not `_parse`",
          got == [["a_run", "$Parse", False]], got)
    iface = ("export interface Repo {\n  find(): number;\n}\nexport const Repo = { find: () => 1 };\n"
             "export function use() {\n  return Repo();\n}\n")
    got = shape("iface", {"a.ts": iface}, [("a_repo", "Repo", "a.ts", 1, None), ("a_use", "use()", "a.ts", 5, None)],
                [("a_use", "a_repo", 6)])
    check("`interface Repo` + `const Repo`: a call binds the const (declaration merging)",
          got == [["a_use", "a_repo@Repo:L4", True]], got)
    got = shape("missing", {"a.ts": TS_OK.replace("CALL", "OK(1)")}, ents, [("a_make", "a_ok", 4)],
                write={"other.ts": "export const x = 1;\n"})
    check("the target's file cannot be read -> no evidence it is a type: the key stays",
          got == [["a_make", "a_ok", True]], got)
    js = "class Box {\n  open() {\n    return 1;\n  }\n}\nfunction make() {\n  return Box();\n}\n"
    got = shape("js", {"a.js": js}, [("a_box", "Box", "a.js", 1, None), ("a_box_open", ".open()", "a.js", 2, "a_box"),
                                     ("a_make", "make()", "a.js", 6, None)], [("a_make", "a_box", 7)])
    check("a JavaScript class keeps its key (no type declaration there)", got == [["a_make", "a_box", True]], got)
    dup = ("export type OK<T> = { v: T };\nexport const OK = <T>(v: T): OK<T> => ({ v });\n"
           "export function make() {\n  ok2();\n  return OK(1);\n}\nexport function ok2() {\n  return 2;\n}\n")
    got = shape("dup", {"a.ts": dup}, [("a_ok", "OK", "a.ts", 1, None), ("a_make", "make()", "a.ts", 3, None),
                                       ("a_ok2", "ok2()", "a.ts", 8, None)],
                [("a_make", "a_ok2", 4), ("a_make", "a_ok", 5)])
    check("the re-key keeps the caller's other tuples and their order",
          got == [["a_make", "a_ok2", True], ["a_make", "a_ok@OK:L2", True]], got)


# ================================================================================================
# H — P1b on the captures
# ================================================================================================
def gate_h_p1b_capture() -> None:
    print("\n-- H: P1b drops 0.8.39's placeholder duplicate name, both engines --")
    res = {}
    for ver, b in BUCKETS.items():
        graph = PF.precise_graph(b, "probe_py")
        root = PF.root_for(b, "precise/probe_py", PF.TREES["probe_py"])
        before = frozen_calls(b, "precise/probe_py", "p1")
        st: dict = {}
        got = run(graph, root, ON, st)
        res[ver] = got
        check(f"{ver}: probe tree, today's PRECISE=1: {11 if b == '0839' else 9} tuples",
              len(before) == (11 if b == "0839" else 9), len(before))
        check(f"{ver}: probe tree with P1b: 9 tuples", len(got) == 9, got)
        check(f"{ver}: P1b only REMOVES tuples (every kept tuple is today's, in today's order)",
              got == [c for c in before if c in got] and all(c in before for c in got))
        check(f"{ver}: stats precise_placeholder_dups == {2 if b == '0839' else 0}",
              st.get("precise_placeholder_dups") == (2 if b == "0839" else 0), st.get("precise_placeholder_dups"))
    if len(res) == 2:
        a, c = (sorted(map(tuple, res[v])) for v in BUCKETS)
        check("the probe tree gives the IDENTICAL 9 tuples on 0.8.39 and 0.8.51 (design §9 S1, D1-G5)", a == c,
              set(a) ^ set(c))
    graph = PF.precise_graph("0839", "p1b_py")
    root = PF.root_for("0839", "precise/p1b_py", PF.TREES["p1b_py"])
    got = {tuple(c) for c in run(graph, root, ON)}
    before = {tuple(c) for c in frozen_calls("0839", "precise/p1b_py", "p1")}
    o, r = "shop_models_order", "shop_models_repo"
    for caller, gone in (("shop_svc_once", "Order"), ("shop_svc_commented", "Order"), ("shop_svc_both", "Order"),
                         ("shop_svc_both", "Repo")):
        check(f"0.8.39 {caller}: one `{gone}(` site beside its key -> the placeholder's name is dropped",
              (caller, gone, False) in before and (caller, gone, False) not in got
              and (caller, o if gone == "Order" else r, True) in got)
    check("0.8.39 twice: TWO `Order(` sites, one key -> the name stays (design §5)",
          {("shop_svc_twice", "Order", False), ("shop_svc_twice", o, True)} <= got)
    check("0.8.39 widget: a placeholder with no key of that label (`Widget`) -> the name stays",
          ("shop_svc_widget", "Widget", False) in got)
    check("0.8.39 Builder.build / build_two: the engine linked only the placeholder (no key) -> the name stays",
          {("shop_svc_builder_build", "Order", False), ("shop_svc_builder_build_two", "Order", False)} <= got)
    check("0.8.39 p1b tree: nothing else changes (exactly the four names go)", before - got == {
        ("shop_svc_once", "Order", False), ("shop_svc_commented", "Order", False),
        ("shop_svc_both", "Order", False), ("shop_svc_both", "Repo", False)} and got <= before, before ^ got)
    g51 = PF.precise_graph("0851", "p1b_py")
    r51 = PF.root_for("0851", "precise/p1b_py", PF.TREES["p1b_py"])
    check("0.8.51 p1b tree (no placeholders): P1b changes nothing",
          run(g51, r51, ON) == frozen_calls("0851", "precise/p1b_py", "p1"))
    check("PRECISE=js on the Python trees: no Python caller is precise -> the default tuples (P1b off)",
          run(graph, root, {"DEEPMAP_PRECISE_CALLS": "js"}) == frozen_calls("0839", "precise/p1b_py", "base"))
    check("PRECISE=python == PRECISE=1 on the all-Python p1b tree",
          run(graph, root, {"DEEPMAP_PRECISE_CALLS": "python"}) == run(graph, root, ON))
    check("DEEPMAP_PRECISE_FIXES=0: the duplicate names come back (today, byte for byte)",
          run(graph, root, OFF_FIXES) == frozen_calls("0839", "precise/p1b_py", "p1"))


# ================================================================================================
# I — P1b shapes on hand-made graphs
# ================================================================================================
def ph(nid: str, label: str, rel: str, line: int) -> dict:
    """A 0.8.39-style placeholder: a code node no `contains` / `method` link defines (map_graph drops it)."""
    return {"id": nid, "label": label, "file_type": "code", "source_file": rel, "source_location": f"L{line}"}


def p1b_shape(name: str, files: dict, entities: list, placeholders: list, calls: list, env=None, root=True):
    g = make_graph(files, entities, [])
    g["nodes"] += placeholders
    for s, t, line in calls:
        g["links"].append({"source": s, "target": t, "relation": "calls", "source_location": f"L{line}"})
    r = PF.write_tree(PF.tmp() / "p1b" / name, files) if root else None
    return run(g, r, ON if env is None else env)


MODELS = {"models.py": "class Order:\n    def save(self):\n        return 1\n"}
MODEL_ENT = [("models_order", "Order", "models.py", 1, None), ("models_order_save", ".save()", "models.py", 2,
                                                                "models_order")]


def gate_i_p1b_shapes() -> None:
    print("\n-- I: P1b shapes (hand-made graphs, real source) --")
    once = "from models import Order\n\n\ndef run():\n    return Order()  # Order() again in a comment\n"
    args = (dict(MODELS, **{"a.py": once}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
            [ph("order", "Order", "a.py", 1)], [("a_run", "order", 5), ("a_run", "models_order", 5)])
    check("one site (a trailing comment is not read): the duplicate name goes, the key stays",
          p1b_shape("once", *args) == [["a_run", "models_order", True]], p1b_shape("once", *args))
    check("...the same graph without source (pure map_graph): nothing is read, nothing is dropped",
          p1b_shape("once_pure", *args, root=False) == [["a_run", "Order", False], ["a_run", "models_order", True]])
    check("...DEEPMAP_PRECISE_FIXES=0: the name stays", p1b_shape("once_f0", *args, env=OFF_FIXES)
          == [["a_run", "Order", False], ["a_run", "models_order", True]])
    s = "from models import Order\n\n\ndef run():\n    print(\"Order()\")\n    return Order()\n"
    got = p1b_shape("string", dict(MODELS, **{"a.py": s}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
                    [ph("order", "Order", "a.py", 1)], [("a_run", "order", 6), ("a_run", "models_order", 6)])
    check("an `Order(` inside a string counts as a site (conservative): two sites, one key -> the name stays",
          ["a_run", "Order", False] in got, got)
    low = "from models import Order\n\n\ndef run():\n    return Order()\n"
    got = p1b_shape("case", dict(MODELS, **{"a.py": low}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
                    [ph("order_l", "order", "a.py", 1)], [("a_run", "order_l", 5), ("a_run", "models_order", 5)])
    check("a placeholder label that differs in case (`order` vs `Order`) is not the key's name -> it stays",
          ["a_run", "order", False] in got, got)
    long_body = "from models import Order\n\n\ndef run():\n    x = Order()\n" + "    x = x\n" * 420 + "    return x\n"
    got = p1b_shape("long", dict(MODELS, **{"a.py": long_body}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
                    [ph("order", "Order", "a.py", 1)], [("a_run", "order", 5), ("a_run", "models_order", 5)])
    check("a body longer than the reader's span (unknown rest) -> the name stays",
          ["a_run", "Order", False] in got, got)
    both = "def helper():\n    return 1\n\n\ndef run(obj):\n    helper()\n    obj.helper()\n"
    for order, label in (((("a_run", "helper_ph", 7), ("a_run", "a_helper", 6))), "placeholder link first"), \
            (((("a_run", "a_helper", 6), ("a_run", "helper_ph", 7))), "engine key first"):
        got = p1b_shape(f"both_{label[:3]}", {"a.py": both},
                        [("a_helper", "helper()", "a.py", 1, None), ("a_run", "run()", "a.py", 5, None)],
                        [ph("helper_ph", "helper", "a.py", 1)], list(order))
        f0 = p1b_shape(f"both_{label[:3]}_f0", {"a.py": both},
                       [("a_helper", "helper()", "a.py", 1, None), ("a_run", "run()", "a.py", 5, None)],
                       [ph("helper_ph", "helper", "a.py", 1)], list(order), env=OFF_FIXES)
        check(f"a name R10/P1 also produced (`helper()` AND `obj.helper()`) is never dropped ({label})",
              got == f0 and ["a_run", "helper", False] in got, (got, f0))
    one = "from models import Order\n\n\ndef save():\n    return 0\n\n\ndef run(obj):\n    return obj.save()\n"
    one_args = (dict(MODELS, **{"a.py": one}), MODEL_ENT + [("a_save", "save()", "a.py", 4, None),
                                                            ("a_run", "run()", "a.py", 8, None)],
                [ph("save_ph", "save", "a.py", 9)],
                [("a_run", "a_save", 9), ("a_run", "models_order_save", 9), ("a_run", "save_ph", 9)])
    got = p1b_shape("p1name", *one_args)
    check("one `obj.save()` site the engine linked to a module-level save (R10/P1: the name), a method key AND a "
          "placeholder: the name stays (P1 produced it too), exactly as with DEEPMAP_PRECISE_FIXES=0",
          got == p1b_shape("p1name_f0", *one_args, env=OFF_FIXES) and ["a_run", "save", False] in got
          and ["a_run", "models_order_save", True] in got, got)
    meth = ("from repo import Repo\n\n\ndef run(r: Repo):\n    return r.find()\n")
    got = p1b_shape("method", {"repo.py": "class Repo:\n    def find(self):\n        return 1\n", "a.py": meth},
                    [("repo_repo", "Repo", "repo.py", 1, None),
                     ("repo_repo_find", ".find()", "repo.py", 2, "repo_repo"), ("a_run", "run()", "a.py", 4, None)],
                    [ph("find_ph", "find", "a.py", 5)], [("a_run", "repo_repo_find", 5), ("a_run", "find_ph", 5)])
    check("the label is matched against the key's LAST name segment (`Repo.find` vs `find`): one site -> dropped",
          got == [["a_run", "repo_repo_find", True]], got)
    fn_args = ({"repo.py": "class Repo:\n    def find(self):\n        return 1\n", "a.py": meth},
               [("repo_repo", "Repo", "repo.py", 1, None), ("repo_repo_find", ".find()", "repo.py", 2, "repo_repo"),
                ("a_run", "run()", "a.py", 4, None)],
               [ph("find_fn", ".find()", "a.py", 5)], [("a_run", "repo_repo_find", 5), ("a_run", "find_fn", 5)])
    got = p1b_shape("method_fn", *fn_args)
    check("a callable-labelled node (`.find()`) is a node map_graph KEEPS, never a placeholder: its name (R10/P1's "
          "member-call fall-back) stays exactly as with DEEPMAP_PRECISE_FIXES=0",
          got == p1b_shape("method_fn0", *fn_args, env=OFF_FIXES) and ["a_run", "find", False] in got, got)
    mod = "from models import Order\n\n\ndef typed(o: Order):\n    return o\n\n\nx = Order()\n"
    got = p1b_shape("module", dict(MODELS, **{"m.py": mod}), MODEL_ENT + [("m_typed", "typed()", "m.py", 4, None)],
                    [ph("order", "Order", "m.py", 4)], [("f_m_py", "order", 8), ("f_m_py", "models_order", 8)])
    check("a :Module caller is read over its whole file: one `Order(` -> the duplicate name goes",
          got == [["f_m_py", "models_order", True]], got)
    mod2 = mod.replace("    return o\n", "    return Order()\n")
    got = p1b_shape("module2", dict(MODELS, **{"m.py": mod2}), MODEL_ENT + [("m_typed", "typed()", "m.py", 4, None)],
                    [ph("order", "Order", "m.py", 4)], [("f_m_py", "order", 8), ("f_m_py", "models_order", 8)])
    check("...a second `Order(` anywhere in that file (here in a def) -> the name stays (conservative)",
          ["f_m_py", "Order", False] in got, got)


# ================================================================================================
# K — P1b's site counter: unreadable call forms keep the name; one scan per span; the :Module cap
# ================================================================================================
DBG = {"dbg.ts": "export class Debug {\n  log() {\n    return 1;\n  }\n}\nexport function debug(x: string) {\n"
                 "  return x;\n}\n"}
DBG_ENT = [("g_debug", "Debug", "dbg.ts", 1, None), ("g_debug_log", ".log()", "dbg.ts", 2, "g_debug"),
           ("g_dbgfn", "debug()", "dbg.ts", 6, None)]


def js_p1b(name: str, body: str, key: str, label: str, key_marker: str, ph_marker: str, env=None) -> list:
    """P1b on a TS caller `run` (a.ts, line 3) holding ONE precise key (`key`, linked on the line holding
    `key_marker`) and a placeholder named `label` (linked on the line holding `ph_marker`). A function `tail` follows
    `run`: a non-Python caller's body is read up to the file's next engine node, and the last one's is unbounded
    (its name always stays)."""
    src = 'import { Debug, debug } from "./dbg";\n\n' + body + "export function tail() {\n  return 0;\n}\n"
    lines = src.split("\n")
    at = lambda m: next(n for n, t in enumerate(lines, 1) if m in t)   # noqa: E731
    return p1b_shape(name, dict(DBG, **{"a.ts": src}),
                     DBG_ENT + [("a_run", "run()", "a.ts", 3, None), ("a_tail", "tail()", "a.ts", at("function tail"),
                                                                       None)],
                     [ph(f"{label}_ph", label, "a.ts", 1)],
                     [("a_run", key, at(key_marker)), ("a_run", f"{label}_ph", at(ph_marker))], env=env)


def gate_k_p1b_counter() -> None:
    print("\n-- K: P1b's counter: unreadable call forms keep the name; one scan per span; the :Module cap --")
    # TS / JS: `new X(` sites are sites (mutant Mn), and a lone site is dropped (P1b works there at all)
    got = js_p1b("js_new1", "export function run() {\n  return new Debug();\n}\n", "g_debug", "Debug", "new Debug",
                 "new Debug")
    check("TS: one `new Debug()` site beside one key: the placeholder's name goes", got == [["a_run", "g_debug", True]],
          got)
    src = 'import { Debug } from "./dbg";\n\nexport function run() {\n  return new Debug();\n}\n'
    got = p1b_shape("js_last", dict(DBG, **{"a.ts": src}), DBG_ENT + [("a_run", "run()", "a.ts", 3, None)],
                    [ph("Debug_ph", "Debug", "a.ts", 1)], [("a_run", "g_debug", 4), ("a_run", "Debug_ph", 4)])
    check("TS: the same caller as the file's LAST engine node (its body's end is unknown): the name stays",
          ["a_run", "Debug", False] in got, got)
    got = js_p1b("js_new2", "export function run() {\n  const d = new Debug();\n  return Debug();\n}\n", "g_debug",
                 "Debug", "new Debug", "return Debug")
    check("TS: `new Debug()` AND `Debug()` (two sites) beside one key: the name stays", ["a_run", "Debug", False] in got,
          got)
    got = js_p1b("js_one", "export function run(u: string) {\n  return debug(u);\n}\n", "g_dbgfn", "debug", "debug(u)",
                 "debug(u)")
    check("TS: one `debug(u)` site beside one key: the name goes (no doubt on a plain call)",
          got == [["a_run", "g_dbgfn", True]], got)
    for i, (second, why) in enumerate((
            ("  if (/^https?:\\/\\//.test(u)) return debug(u);\n", "a call after a regex literal holding `//`"),
            ("  return debug?.(u);\n", "an optional call `debug?.(u)`"),
            ("  return debug.call(null, u);\n", "`debug.call(...)`"),
            ("  return debug`u`;\n", "a tagged template"))):
        body = "export function run(u: string) {\n  debug(u);\n" + second + "}\n"
        got = js_p1b(f"js_doubt_{i}", body, "g_dbgfn", "debug", "  debug(u);", "  debug(u);")
        check(f"TS must-not: `debug(u)` plus {why} (a second site the counter cannot read) beside one key: the name "
              f"stays", ["a_run", "debug", False] in got, got)
    got = js_p1b("js_new_noparen", "export function run() {\n  const d = new Debug();\n  return new Debug;\n}\n",
                 "g_debug", "Debug", "const d", "const d")
    check("TS must-not: `new Debug()` plus `new Debug` (no parentheses) beside one key: the name stays",
          ["a_run", "Debug", False] in got, got)
    # Python: the same rule
    for i, (second, why) in enumerate((
            ("    return Order[int]()\n", "a subscripted call `Order[int]()`"),
            ("    b = Order \\\n        (2)\n    return b\n", "a backslash-continued call `Order \\` + `(2)`"),
            ("    b = (Order\n         (2))\n    return b\n", "a call whose `(` opens the next line"))):
        src = "from models import Order\n\n\ndef run():\n    a = Order()\n" + second
        got = p1b_shape(f"py_doubt_{i}", dict(MODELS, **{"a.py": src}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
                        [ph("order", "Order", "a.py", 1)], [("a_run", "order", 5), ("a_run", "models_order", 5)])
        check(f"Python must-not: `Order()` plus {why} beside one key: the name stays", ["a_run", "Order", False] in got,
              got)
    src = "from models import Order\n\n\ndef run(x: Order[int]):\n    return Order()\n"
    got = p1b_shape("py_ann", dict(MODELS, **{"a.py": src}), MODEL_ENT + [("a_run", "run()", "a.py", 4, None)],
                    [ph("order", "Order", "a.py", 4)], [("a_run", "order", 5), ("a_run", "models_order", 5)])
    check("Python: an annotation `x: Order[int]` (subscripted, not called) is no doubt: one `Order()` -> the name goes",
          got == [["a_run", "models_order", True]], got)
    # a :Module caller over a file longer than 5000 lines: the rest is not scanned, the name stays (it used to be read
    # whole, once per candidate name)
    big = "from models import Order\n" + "y = 1\n" * 5000 + "x = Order()\n"
    n_big = big.count("\n")
    got = p1b_shape("module_big", dict(MODELS, **{"m.py": big}), MODEL_ENT, [ph("order", "Order", "m.py", 1)],
                    [("f_m_py", "order", n_big), ("f_m_py", "models_order", n_big)])
    check("a :Module caller in a file over 5000 lines: the name stays (no whole-file scan)",
          ["f_m_py", "Order", False] in got, got)
    # one scan per caller span, whatever the number of candidate names (each used to re-scan the span)
    n_cls = 30
    models = "".join(f"class C{i}:\n    pass\n\n\n" for i in range(n_cls))
    ents = [(f"models_c{i}", f"C{i}", "models.py", 1 + 4 * i, None) for i in range(n_cls)]
    filler = "y = 1\n" * 400

    def scan(k: int) -> tuple:
        src = "from models import *\n" + filler + "".join(f"x{i} = C{i}()\n" for i in range(k))
        base = 1 + 400
        calls = []
        for i in range(k):
            calls += [("f_m_py", f"ph{i}", base + 1 + i), ("f_m_py", f"models_c{i}", base + 1 + i)]
        phs = [ph(f"ph{i}", f"C{i}", "m.py", 1) for i in range(k)]
        real = GX._strip_trailing_comment
        n = [0]

        def counting(line, py):
            n[0] += 1
            return real(line, py)

        GX._strip_trailing_comment = counting
        try:
            got = p1b_shape(f"memo_{k}", {"models.py": models, "m.py": src}, ents, phs, calls)
        finally:
            GX._strip_trailing_comment = real
        return got, n[0]

    got1, n1 = scan(1)
    got30, n30 = scan(n_cls)
    check(f"{n_cls} candidate names in one :Module caller: every duplicate name goes (one site each)",
          got30 == [["f_m_py", f"models_c{i}", True] for i in range(n_cls)], got30[:4])
    check(f"...and the caller's lines are read once, not once per name ({n30} line reads for {n_cls} names vs {n1} for "
          f"one)", n30 <= n1 + 2 * n_cls + 50, (n1, n30))


# ================================================================================================
# L — J3 on keys (review of S1b): a precise key a bare call cannot reach is dropped (captured tree)
# ================================================================================================
BARE_FIXTURE = "bare_keys"
BARE_TREE = {
    "pkg/__init__.py": "",
    "pkg/wsgi.py": "def get_host(environ):\n    return environ\n",
    "pkg/completion.py": "def shell_complete(cmd, args):\n    return [cmd, args]\n",
    # werkzeug routing/map.py's shape: a bare call of an imported function, keyed by the engine to the same file's
    # method of ANOTHER class
    "pkg/routing.py": """\
from .wsgi import get_host


class Map:
    def bind_to_environ(self, environ):
        return get_host(environ).lower()

    def bind_both(self, environ, adapter):
        a = get_host(environ)
        return adapter.get_host(a)

    def get_own(self):
        return 1

    def use_own(self):
        return get_own()


class MapAdapter:
    def get_host(self, domain):
        return domain

    def check(self, x):
        return x

    def match(self, environ):
        return self.get_host(environ)


_default = MapAdapter()
check = _default.check


def use_alias():
    return check(1)


def use_param(get_host):
    return get_host(2)


def module_level(environ):
    return get_host(environ)
""",
    # click core.py's shape: a function-local import, keyed to another class's method of that name
    "pkg/core.py": """\
class Command:
    def main_complete(self, args):
        from .completion import shell_complete

        return shell_complete(self, args)


class Parameter:
    def shell_complete(self, ctx):
        return [ctx]
""",
    # a classic script calling a global function another script defines (no import binds `format` here)
    "web/legacy.js": "function show(x) {\n  return format(x);\n}\n\nclass Formatter {\n  format(x) {\n    return x;\n  }\n}\n",
    "web/globals.js": "function format(x) {\n  return x;\n}\n",
    "web/view.ts": ("export class View {\n  draw() {\n    return paint(1);\n  }\n}\n\nexport class Canvas {\n"
                    "  paint(n: number) {\n    return n;\n  }\n}\n\ndeclare function paint(n: number): number;\n"),
    # a name the TS file imports from a project path may be any value (R9/K11): the key stays
    "web/fmt.ts": "export function format(x: string) {\n  return x;\n}\n",
    "web/printer.ts": """\
import { format } from "./fmt";

export class Printer {
  print(x: string) {
    return format(x);
  }
}

export class Formatter {
  format(x: string) {
    return x;
  }
}
""",
    "go/util.go": "package main\n\nfunc Helper() int {\n\treturn 2\n}\n",
    "go/main.go": ("package main\n\ntype A struct{}\n\ntype B struct{}\n\nfunc (a A) Run() int {\n\treturn Helper()\n}\n\n"
                   "func (b B) Helper() int {\n\treturn 1\n}\n\nfunc main() {\n\tA{}.Run()\n}\n"),
    "go/alias.go": ("package main\n\ntype C struct{}\n\nfunc (c C) Ping() int {\n\treturn 3\n}\n\nvar Ping = C{}.Ping\n\n"
                    "func usePing() int {\n\treturn Ping()\n}\n"),
    "rs/util.rs": "pub fn tick() -> i32 {\n    2\n}\n",
    "rs/lib.rs": ("mod util;\nuse util::tick;\n\npub struct S;\n\nimpl S {\n    pub fn tick(&self) -> i32 {\n        1\n    }\n}"
                  "\n\npub struct T;\n\nimpl T {\n    pub fn run(&self) -> i32 {\n        tick()\n    }\n\n"
                  "    pub fn both(&self, s: &S) -> i32 {\n        let a = tick();\n        a + s.tick()\n    }\n}\n"),
}
BARE_DROPPED = {   # (caller, the engine's target) -> why the bare call cannot reach it
    ("pkg_routing_map_bind_to_environ", "pkg_routing_mapadapter_get_host"):
        "werkzeug: Map's bare `get_host(environ)` (the imported function) never reaches MapAdapter.get_host",
    ("pkg_core_command_main_complete", "pkg_core_parameter_shell_complete"):
        "click: a bare `shell_complete(...)` of a function-local import never reaches Parameter.shell_complete",
    ("pkg_routing_module_level", "pkg_routing_mapadapter_get_host"):
        "a module-level function's bare `get_host(...)` never reaches a method",
    ("web_legacy_show", "web_legacy_formatter_format"): "JS: a bare `format(x)` in a script never reaches a method",
    ("web_view_view_draw", "web_view_canvas_paint"): "TS: a bare `paint(1)` (a declared global) never reaches Canvas.paint",
    ("go_a_run", "go_b_helper"): "Go: a bare `Helper()` in A.Run never reaches B's method Helper",
    ("rs_lib_t_run", "rs_lib_s_tick"): "Rust: a bare `tick()` (`use util::tick`) never reaches S::tick(&self)",
}
BARE_KEPT = {      # precise keys that stay
    ("pkg_routing_map_bind_both", "pkg_routing_mapadapter_get_host"):
        "the engine's line is bare, but the body also calls `adapter.get_host(a)`",
    ("pkg_routing_map_use_own", "pkg_routing_map_get_own"): "a method of the caller's OWN class (J3 keeps it)",
    ("pkg_routing_mapadapter_match", "pkg_routing_mapadapter_get_host"): "`self.get_host(...)` is a receiver call",
    ("pkg_routing_use_alias", "pkg_routing_mapadapter_check"):
        "`check = _default.check` at module level: the bare name holds a bound method (R9/K11)",
    ("pkg_routing_use_param", "pkg_routing_mapadapter_get_host"): "a parameter `get_host` may hold a bound method",
    ("pkg_routing_use_param", "pkg_wsgi_get_host"): "a :Function target is never judged",
    ("pkg_routing_module_level", "pkg_wsgi_get_host"): "a :Function target (the true one) stays beside the drop",
    ("web_printer_printer_print", "web_printer_formatter_format"):
        "TS: `format` is imported from a project path, which may export any value (R9/K11 keeps it)",
    ("go_alias_useping", "go_c_ping"): "Go: a package-level `var Ping = C{}.Ping` holds a method value",
    ("rs_lib_t_both", "rs_lib_s_tick"): "Rust: the body also calls `s.tick()`",
    ("go_main_main", "go_a_run"): "Go: `A{}.Run()` is a receiver call",
}
_BARE_LANG = {"pkg": "python", "web": "js", "go": "go", "rs": "rust"}


def _bare_sha() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(BARE_TREE, sort_keys=True).encode("utf-8")).hexdigest()


def _bare_fixture(bucket: str) -> Path:
    return PF.fixture_path(bucket, BARE_FIXTURE)


def _bare_root() -> Path:
    return PF.root_for("bare", BARE_FIXTURE, BARE_TREE)


def _keyed(calls: list) -> set:
    return {(s, c) for s, c, k in calls if k}


def _bare_checks(tag: str, graph: dict, root: Path) -> list:
    """The J3-on-keys checks on one graph of BARE_TREE; returns the PRECISE=1 tuples."""
    before = run(graph, root, OFF_FIXES)
    check(f"{tag}: today's PRECISE=1 keys every shape (the engine binds each bare call to the method)",
          set(BARE_DROPPED) | set(BARE_KEPT) <= _keyed(before), sorted((set(BARE_DROPPED) | set(BARE_KEPT)) - _keyed(before)))
    st: dict = {}
    got = run(graph, root, ON, st)
    for pair, why in BARE_DROPPED.items():
        check(f"{tag}: dropped — {why}", not any((s, c) == pair for s, c, _k in got), [c for c in got if c[0] == pair[0]])
    for pair, why in BARE_KEPT.items():
        check(f"{tag}: kept — {why}", [pair[0], pair[1], True] in got, [c for c in got if c[0] == pair[0]])
    check(f"{tag}: the rule only REMOVES those keys: every other tuple is today's, in today's order",
          got == [c for c in before if (c[0], c[1]) not in BARE_DROPPED], [c for c in got if c not in before])
    check(f"{tag}: stats precise_bare_dropped == {len(BARE_DROPPED)}", st.get("precise_bare_dropped") == len(BARE_DROPPED),
          st.get("precise_bare_dropped"))
    for fam in ("python", "js", "go,rust"):
        fams = set(fam.split(","))
        sel = run(graph, root, {"DEEPMAP_PRECISE_CALLS": fam})
        ref = run(graph, root, {"DEEPMAP_PRECISE_CALLS": fam, "DEEPMAP_PRECISE_FIXES": "0"})
        gone = {p for p in BARE_DROPPED if _BARE_LANG[p[0].split("_", 1)[0]] in fams}
        check(f"{tag}: PRECISE={fam}: only that family's keys go ({len(gone)}), the other families keep today's tuples",
              sel == [c for c in ref if not (c[2] and (c[0], c[1]) in gone)], [c for c in sel if c not in ref])
    check(f"{tag}: DEEPMAP_PRECISE_FIXES=0 -> today's keys, byte for byte", run(graph, root, OFF_FIXES) == before)
    fcf0 = {"DEEPMAP_FALSE_CALL_FILTER": "0"}
    check(f"{tag}: DEEPMAP_FALSE_CALL_FILTER=0 (J3's own opt-out) -> today's keys",
          run(graph, root, {**ON, **fcf0}) == run(graph, root, {**OFF_FIXES, **fcf0}))
    base = run(graph, root, {})
    check(f"{tag}: PRECISE unset: the default tuples (no key; the rule is a precise fix) — DEEPMAP_PRECISE_FIXES "
          f"changes nothing there", not any(k for _s, _c, k in base)
          and base == run(graph, root, {"DEEPMAP_PRECISE_FIXES": "0"}), base)
    # the push (sync._resolve_calls): the finding's EXTRACTED edge is gone, its sibling key stays EXTRACTED / INFERRED
    try:
        import sync as S
    except Exception as e:  # noqa: BLE001
        check(f"{tag}: build/sync.py imports", False, f"{type(e).__name__}: {e}")
        return got
    with PF.clean_env(**ON):
        nodes, calls, _i = GX.map_graph(json.loads(json.dumps(graph)), root=root)
        on_edges = S._resolve_calls(nodes, calls)
    with PF.clean_env(**OFF_FIXES):
        nodes0, calls0, _i0 = GX.map_graph(json.loads(json.dumps(graph)), root=root)
        off_edges = S._resolve_calls(nodes0, calls0)
    werk = ("pkg_routing_map_bind_to_environ", "pkg_routing_mapadapter_get_host")
    check(f"{tag}: pushed: Map.bind_to_environ -> MapAdapter.get_host was EXTRACTED, now no edge",
          off_edges.get(werk) == "EXTRACTED" and werk not in on_edges, (off_edges.get(werk), on_edges.get(werk)))
    check(f"{tag}: pushed: no dropped pair has an edge; every kept pair keeps its grade",
          not [p for p in BARE_DROPPED if p in on_edges]
          and all(on_edges.get(p) == off_edges.get(p) is not None for p in BARE_KEPT),
          ([p for p in BARE_DROPPED if p in on_edges], {p: (off_edges.get(p), on_edges.get(p)) for p in BARE_KEPT}))
    return got


def gate_l_bare_keys_capture() -> None:
    print("\n-- L: J3 on keys — a precise key a bare call cannot reach is dropped (captured tree, both engines) --")
    per_ver = {}
    for ver, b in BUCKETS.items():
        p = _bare_fixture(b)
        if not p.is_file():
            check(f"{b}: the bare_keys capture exists ({p.name})", False)
            continue
        fx = json.loads(p.read_text(encoding="utf-8"))
        check(f"{b}: the capture is graphify {ver}'s, of THIS tree (re-capture after editing BARE_TREE)",
              fx.get("engine") == ver and fx.get("tree_sha256") == _bare_sha(), (fx.get("engine"), fx.get("tree_sha256")))
        per_ver[ver] = _bare_checks(ver, fx["graph"], _bare_root())
    if len(per_ver) == 2:
        a, c = (sorted(map(tuple, per_ver[v])) for v in BUCKETS)
        check("0.8.39 and 0.8.51 give the identical precise tuples on the bare-keys tree", a == c, set(a) ^ set(c))


def _bare_real_graph():
    """(graph.json the installed engine writes for BARE_TREE, its root) — or (None, root)."""
    root = PF.write_tree(PF.tmp() / "bare_real", BARE_TREE)
    with PF.clean_env(DEEPMAP_HOME=str(PF.tmp() / "home_bare")), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, _rep = GX.extract_with_report(root)
        gp = GX.graphify_out_dir(root) / "graph.json"
        if res is None or not gp.is_file():
            return None, root
        return json.loads(gp.read_text(encoding="utf-8")), root


def gate_l_real_engine() -> None:
    print("\n-- L (real engine): the installed graphify on the bare-keys tree --")
    if not PF._HAS_ENGINE:
        print("  [SKIP] graphify is not installed — the real-engine part of L is not run")
        return
    ver = GX.graphify_version()
    graph, root = _bare_real_graph()
    if graph is None:
        check(f"real engine ({ver}) produced a graph of the bare-keys tree", False)
        return
    _bare_checks(f"real {ver}", graph, root)
    b = BUCKETS.get(ver)
    if b is None or not _bare_fixture(b).is_file():
        print(f"  (no bare_keys capture for graphify {ver}; the comparison with a capture is skipped)")
        return
    fx = json.loads(_bare_fixture(b).read_text(encoding="utf-8"))["graph"]
    bad = [m for m in ("base", "p1") if sorted(map(tuple, run(graph, root, PF.FROZEN_MODES[m])))
           != sorted(map(tuple, run(fx, root, PF.FROZEN_MODES[m])))]
    check(f"real {ver}: the same calls as the {b} capture (default and PRECISE=1)", not bad, bad)


def do_capture() -> int:
    ver = GX.graphify_version() if PF._HAS_ENGINE else None
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    graph, root = _bare_real_graph()
    if graph is None:
        print("--capture: the engine failed on the bare-keys tree")
        return 1
    p = _bare_fixture(b)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                      "tests/precise_fixes_gates.BARE_TREE, written by extract_with_report (absolute paths made "
                      "root-relative; map_graph reads none of them); re-capture with "
                      "`python tests/precise_fixes_gates.py --capture` under this engine."),
           "engine": ver, "tree_sha256": _bare_sha(), "graph": PF._relative(graph, root)}
    p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(f"--capture: wrote {p.name} for graphify {ver} ({len(graph.get('nodes', []))} nodes, "
          f"{len(graph.get('links', []))} links)")
    return 0


# ================================================================================================
# M — J3-on-keys shapes (hand-made graphs, real source)
# ================================================================================================
def gate_m_bare_shapes() -> None:
    print("\n-- M: J3 on keys — shapes (hand-made graphs, real source) --")
    xm = [("a_x", "X", "a.py", 1, None), ("a_x_run", ".run_it()", "a.py", 2, "a_x")]
    head = "class X:\n    def run_it(self):\n        return 1\n\n\n"
    got = shape("m_module", {"a.py": head + "run_it()\n"}, xm, [("f_a_py", "a_x_run", 6)])
    check("a :Module caller's bare `run_it()` keyed to X.run_it -> dropped", got == [], got)
    got = shape("m_module_f0", {"a.py": head + "run_it()\n"}, xm, [("f_a_py", "a_x_run", 6)], OFF_FIXES)
    check("...DEEPMAP_PRECISE_FIXES=0: the key stays (today)", got == [["f_a_py", "a_x_run", True]], got)
    got = run(make_graph({"a.py": head + "run_it()\n"}, xm, [("f_a_py", "a_x_run", 6)]), None, ON)
    check("...the same graph without source (pure map_graph): nothing is read, the key stays",
          got == [["f_a_py", "a_x_run", True]], got)
    # a :Module belongs to no class, whatever its name: `X.tools.py`'s stem `X.tools` does not make it class X's
    dotted = [("d_x", "X", "X.tools.py", 1, None), ("d_x_run", ".run_it()", "X.tools.py", 2, "d_x")]
    got = shape("m_module_dotted", {"X.tools.py": head + "run_it()\n"}, dotted, [("f_X_tools_py", "d_x_run", 6)])
    check("a :Module whose name reads like a member of X (`X.tools.py`) is still no class: its bare `run_it()` "
          "keyed to X.run_it -> dropped", got == [], got)
    # a :Module's statements are scattered between the defs: its whole file is read, so a later module-level
    # `X().run_it()` (after another def) keeps the key; a file over 5000 lines is not read (the key stays)
    later = head + "run_it()\n\n\ndef later():\n    return 0\n\n\nX().run_it()\n"
    got = shape("m_module_later", {"a.py": later}, xm + [("a_later", "later()", "a.py", 9, None)],
                [("f_a_py", "a_x_run", 6)])
    check("a :Module caller with a bare `run_it()` AND, after a def, a module-level `X().run_it()`: kept (its whole "
          "file is read)", got == [["f_a_py", "a_x_run", True]], got)
    big = head + "run_it()\n" + "y = 1\n" * 5000
    got = shape("m_module_big", {"a.py": big}, xm, [("f_a_py", "a_x_run", 6)])
    check("a :Module caller in a file over 5000 lines: not read whole -> the key stays", got == [["f_a_py", "a_x_run", True]],
          got)
    # a body longer than the reader's span: the unread rest may call it through a receiver -> the key stays
    long_body = ("class X:\n    def run_it(self):\n        return 1\n\n\nclass Y:\n    def go(self, x):\n        run_it()\n"
                 + "        y = 1\n" * 420 + "        return x.run_it()\n")
    ents_y = xm + [("a_y", "Y", "a.py", 6, None), ("a_y_go", ".go()", "a.py", 7, "a_y")]
    got = shape("m_long", {"a.py": long_body}, ents_y, [("a_y_go", "a_x_run", 8)])
    check("a caller body longer than the reader's span (`x.run_it()` past it): the rest is unknown -> the key stays",
          got == [["a_y_go", "a_x_run", True]], got)
    # a class body runs its earlier defs by bare name (the caller is the :Class node): the same class keeps the key
    body = "class A:\n    def deco(fn):\n        return fn\n\n    x = deco(1)\n"
    got = shape("m_classbody", {"a.py": body}, [("a_a", "A", "a.py", 1, None), ("a_a_deco", ".deco()", "a.py", 2, "a_a")],
                [("a_a", "a_a_deco", 5)])
    check("a class-body `deco(1)` keyed to the class's own deco: kept (J3 keeps the caller's own class)",
          got == [["a_a", "a_a_deco", True]], got)
    two = ("class X:\n    def run_it(self):\n        return 1\n\n\nclass Y:\n    def go(self, x):\n        LINE\n"
           "        return 0\n")
    ents = xm + [("a_y", "Y", "a.py", 6, None), ("a_y_go", ".go()", "a.py", 7, "a_y")]
    for i, (line, want, why) in enumerate((
            ("run_it()", [], "a bare `run_it()` in Y.go keyed to X.run_it -> dropped"),
            ("x.run_it() + run_it()", [["a_y_go", "a_x_run", True]],
             "the engine's line holds `x.run_it()` AND a bare `run_it()`: a receiver call -> kept"),
            ("r = (x.\n            run_it())", [["a_y_go", "a_x_run", True]],
             "`x.` ending the previous line makes `run_it()` a receiver call (R9/K10) -> kept"),
            ("pass", [["a_y_go", "a_x_run", True]], "the engine's line shows no call of the name -> kept (never a guess)"))):
        src = two.replace("LINE", line)
        at = next(n for n, t in enumerate(src.split("\n"), 1) if "run_it(" in t and "def " not in t) \
            if "run_it(" in line else 8
        got = shape(f"m_two_{i}", {"a.py": src}, ents, [("a_y_go", "a_x_run", at)])
        check(why, got == want, got)
    # one genuine link keeps the pair (the false-call filter's rule): a second link of the pair, on a line outside the
    # caller's body that calls the name through a receiver, keeps the key
    src = two.replace("LINE", "run_it()") + "\n\nOUT = X().run_it()\n"
    out_line = src.split("\n").index("OUT = X().run_it()") + 1
    got = shape("m_twolinks", {"a.py": src}, ents, [("a_y_go", "a_x_run", 8), ("a_y_go", "a_x_run", out_line)])
    check("two links of one pair, one ruled out and one on a receiver call: the key stays (one genuine site keeps it)",
          got == [["a_y_go", "a_x_run", True]], got)
    st: dict = {}
    run(make_graph({"a.py": src}, ents, [("a_y_go", "a_x_run", 8), ("a_y_go", "a_x_run", out_line)]),
        PF.write_tree(PF.tmp() / "shapes" / "m_twolinks_st", {"a.py": src}), ON, st)
    check("...and that pair is not counted as dropped", st.get("precise_bare_dropped") == 0, st.get("precise_bare_dropped"))
    # the same class: a bare call J3 does not judge
    own = "class X:\n    def run_it(self):\n        return 1\n\n    def go(self):\n        return run_it()\n"
    got = shape("m_own", {"a.py": own}, xm + [("a_x_go", ".go()", "a.py", 5, "a_x")], [("a_x_go", "a_x_run", 6)])
    check("a bare call keyed to the caller's OWN class's method: kept (J3 never judges the same class)",
          got == [["a_x_go", "a_x_run", True]], got)
    # a local value (R9/K11): `run_it = x.run_it; run_it()` is a method call written bare
    loc = head + "def go(x):\n    run_it = x.run_it\n    return run_it()\n"
    got = shape("m_local", {"a.py": loc}, xm + [("a_go", "go()", "a.py", 6, None)], [("a_go", "a_x_run", 8)])
    check("a local `run_it = x.run_it` then `run_it()`: kept (the name holds a bound method)",
          got == [["a_go", "a_x_run", True]], got)
    # a :Function target is never judged (P1's business)
    fn = "def helper():\n    return 1\n\n\nclass Y:\n    def go(self):\n        return helper()\n"
    got = shape("m_fn", {"a.py": fn}, [("a_helper", "helper()", "a.py", 1, None), ("a_y", "Y", "a.py", 5, None),
                                       ("a_y_go", ".go()", "a.py", 6, "a_y")], [("a_y_go", "a_helper", 7)])
    check("a bare call keyed to a module-level :Function: kept", got == [["a_y_go", "a_helper", True]], got)
    # an owner-less method (its owner node is not kept): J3 keeps it
    ol = "class X:\n    def run_it(self):\n        return 1\n\n\ndef go():\n    return run_it()\n"
    got = shape("m_ownerless", {"a.py": ol}, [("a_x", "", "a.py", 1, None), ("a_x_run", ".run_it()", "a.py", 2, "a_x"),
                                              ("a_go", "go()", "a.py", 6, None)], [("a_go", "a_x_run", 7)])
    check("a :Method whose owner has no name (an owner-less `run_it`): kept, as J3 keeps it",
          got == [["a_go", "a_x_run", True]], got)
    # languages with an implicit this are never judged
    java = ("class A {\n  void run() {\n    helper();\n  }\n}\n\nclass B {\n  void helper() {\n  }\n}\n")
    got = shape("m_java", {"A.java": java}, [("j_a", "A", "A.java", 1, None), ("j_a_run", ".run()", "A.java", 2, "j_a"),
                                             ("j_b", "B", "A.java", 7, None), ("j_b_helper", ".helper()", "A.java", 8, "j_b")],
                [("j_a_run", "j_b_helper", 3)])
    check("Java (an implicit this): a bare `helper()` keyed to another class's method keeps its key",
          got == [["j_a_run", "j_b_helper", True]], got)
    # a JS/TS value declared in the caller's file (R9/K11): `const fmt = new F().fmt; fmt()` reaches the method
    ts = ("export class F {\n  fmt(x: number) {\n    return x;\n  }\n}\n\nconst fmt = new F().fmt;\n\n"
          "export function show() {\n  return fmt(1);\n}\n")
    got = shape("m_ts_const", {"a.ts": ts}, [("t_f", "F", "a.ts", 1, None), ("t_f_fmt", ".fmt()", "a.ts", 2, "t_f"),
                                             ("t_show", "show()", "a.ts", 9, None)], [("t_show", "t_f_fmt", 10)])
    check("TS: `const fmt = new F().fmt` beside the method, then a bare `fmt(1)`: kept", got == [["t_show", "t_f_fmt", True]],
          got)
    got = shape("m_ts_bare", {"a.ts": ts.replace("const fmt = new F().fmt;\n\n", "")},
                [("t_f", "F", "a.ts", 1, None), ("t_f_fmt", ".fmt()", "a.ts", 2, "t_f"), ("t_show", "show()", "a.ts", 7, None)],
                [("t_show", "t_f_fmt", 8)])
    check("...without that const, a bare `fmt(1)` keyed to F.fmt -> dropped", got == [], got)


# ================================================================================================
# J — stats and the sync-log line
# ================================================================================================
def gate_j_log() -> None:
    print("\n-- J: extract_with_report names the fixes in the sync log, and warns about unknown family names --")
    res, text = sync_log({"DEEPMAP_PRECISE_CALLS": "1"})
    check("extract_with_report mapped the graph", res is not None)
    check("the sync log names the TS re-key (14 to the value, 2 to the name) and how to turn the fixes off",
          "precise CALLS fixes" in text and "14" in text and "DEEPMAP_PRECISE_FIXES=0" in text, text[-800:])
    check("a valid PRECISE value gives no unknown-family warning", "unknown" not in text.lower(), text[-800:])
    for value, bad in (("python,typescript", ["typescript"]), ("pyhton", ["pyhton"]), ("all", ["all"]),
                       ("js,ts,javascript", ["javascript", "ts"])):
        _res, text = sync_log({"DEEPMAP_PRECISE_CALLS": value})
        warn = [ln for ln in text.splitlines() if "DEEPMAP_PRECISE_CALLS" in ln and "unknown" in ln.lower()]
        check(f"PRECISE={value!r}: ONE stderr line names the ignored name(s) {bad}, the known families and the "
              f"`1` spelling", len(warn) == 1 and all(f"'{b}'" in warn[0] for b in bad) and "'js'" in warn[0]
              and "python" in warn[0] and "1" in warn[0], warn or text[-600:])
    for value in ("python,js", "1", "0", ""):
        _res, text = sync_log({"DEEPMAP_PRECISE_CALLS": value})
        check(f"PRECISE={value!r}: no unknown-family warning",
              not [ln for ln in text.splitlines() if "DEEPMAP_PRECISE_CALLS" in ln and "unknown" in ln.lower()],
              text[-600:])
    if _bare_fixture("0839").is_file():
        bare = json.loads(_bare_fixture("0839").read_text(encoding="utf-8"))["graph"]
        res, text = sync_log({"DEEPMAP_PRECISE_CALLS": "1"}, graph=bare, files=BARE_TREE, name="bare")
        check(f"the sync log counts the {len(BARE_DROPPED)} keys to a method a bare call cannot reach (bare-keys tree)",
              res is not None and "precise CALLS fixes" in text
              and f"{len(BARE_DROPPED)} key(s) to a method a bare call cannot reach dropped" in text, text[-800:])
        _res, text = sync_log({"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_PRECISE_FIXES": "0"}, graph=bare, files=BARE_TREE,
                              name="bare_f0")
        check("...and with DEEPMAP_PRECISE_FIXES=0 there is no fixes line", "precise CALLS fixes" not in text, text[-800:])
    else:
        check("the bare_keys capture exists for the sync-log check", False)


def sync_log(env: dict, graph=None, files=None, name: str = "rekey") -> tuple:
    """(map result, stderr) of extract_with_report on a capture (default: 0.8.39's rekey_ts) under `env`, the engine
    faked."""
    graph = PF.precise_graph("0839", "rekey_ts") if graph is None else graph
    fake_root = PF.write_tree(PF.tmp() / f"logroot_{name}", PF.TREES["rekey_ts"] if files is None else files)
    out = PF.tmp() / f"logout_{name}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def fake_engine(cmd, *, cwd, env, timeout):
        (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")   # a fresh graph.json, this run
        return _P()

    import importlib.util as ilu
    real_find_spec = ilu.find_spec

    def find_spec(name, *a, **k):        # the engine itself is never run here: "installed" for the guard
        return object() if name == "graphify" else real_find_spec(name, *a, **k)

    saved = (GX._run_engine, GX.graphify_out_dir, GX.graphify_version)
    err = io.StringIO()
    try:
        GX._run_engine = fake_engine
        GX.graphify_out_dir = lambda root: out
        GX.graphify_version = lambda: "0.8.39"
        ilu.find_spec = find_spec
        with PF.clean_env(DEEPMAP_HOME=str(PF.tmp() / "loghome"), DEEPMAP_RESCUE_SKIPPED_DIRS="0", **env), \
                contextlib.redirect_stderr(err):
            res, _rep = GX.extract_with_report(fake_root)
    finally:
        GX._run_engine, GX.graphify_out_dir, GX.graphify_version = saved
        ilu.find_spec = real_find_spec
    return res, err.getvalue()


def main(argv=()) -> int:
    if "--capture" in argv:
        try:
            return do_capture()
        finally:
            PF.cleanup()
    print("precise_fixes_gates — S1: TS value-over-type re-key + P1b (0.8.39 placeholder duplicates); S1b review: "
          "J3 on precise keys")
    try:
        for g in (gate_f_rekey_capture, gate_f2_rekey_scope_capture, gate_g_rekey_shapes, gate_h_p1b_capture,
                  gate_i_p1b_shapes, gate_k_p1b_counter, gate_l_bare_keys_capture, gate_l_real_engine,
                  gate_m_bare_shapes, gate_j_log):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        PF.cleanup()
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
