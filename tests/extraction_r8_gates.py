#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-8 W5 gates (J2): every graphify-output correction gives the same answer on graphify 0.8.39 AND
0.8.51 — both ends of SUPPORTED_GRAPHIFY_SPEC. No Neo4j, no network, no spend.

Why: graphify's `update --no-cluster` writer changed inside the supported range. 0.8.39 writes a repeated
link per def / per colliding declaration and BOTH nodes that share an id; 0.8.40 collapses repeated links
(`build.dedupe_edges`) and 0.8.41+ also collapses nodes by id (`dedupe_nodes`, last one wins). Three
round-7 corrections read exactly those repeats, so on 0.8.51 — what every installer and CI install — they
did nothing (the round-7 review: extraction_r7_gates RED 4/111 on Linux CI; click's Context.invoke stayed
on its first @overload stub, gin lost 14 file :Module nodes, zod lost 353 of 381 recovered declarations).

Fixture: tests/fixtures/graphify_r8/extraction_r8.json — the REAL graph.json 0.8.39 and 0.8.51 wrote for
four round-8 trees, plus 0.8.51's graph for the five round-7 trees (their 0.8.39 graphs are
tests/fixtures/graphify_r7/extraction_r7.json).

  A  the five round-7 trees: both engine shapes map to the SAME corrected graph (@overload lines, Go file
     :Module nodes, TS id-collision recovery) — and the pre-round-8 mapper did not on 0.8.51's shape.
  B  the round-8 trees on both shapes:
       ts8     a TS `const $` whose engine id IS the file's (zod's packages/tsc/bench/*.ts) keeps the
               file's :Module and its imports; every declaration kind the engine does / does not make a
               node of, each colliding with a kept type (function expression / arrow / object recovered;
               number literal, `var`, generator, a class inside a function body, overload signatures not)
       go8     the ids graphify disambiguates across files (`binding_binding_go_binding_binding`)
       py8     @overload scope: module-level stubs under `if TYPE_CHECKING:` move to the implementation; a
               class-level `if` block (the engine makes the stubs a separate Function) and a stub-only
               group whose same-named def lives in another class stay put
       rescue8 the false-call filter reads the caller's whole body before ruling a pair out (the engine
               records ONE line per (caller, callee) — in both versions)
  C  hermetic unit checks of the new helpers (restore evidence rules, engine file ids, the no-grammar
     degrade, the rescue span bound, the engine child never has the project root on sys.path).
  D  the REAL installed engine (whichever version) on all nine trees: the corrected facts hold and equal
     the fixture's answer for the same engine version when there is one.
  E  optional cross-engine run: DEEPMAP_R8_ALT_PYTHON=<an interpreter with another graphify> extracts the
     same trees there with this repo's build/ and must map to the same graph (the J2 matrix, one command).
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

R7 = json.loads((REPO / "tests" / "fixtures" / "graphify_r7" / "extraction_r7.json").read_text(encoding="utf-8"))
R8 = json.loads((REPO / "tests" / "fixtures" / "graphify_r8" / "extraction_r8.json").read_text(encoding="utf-8"))
R7_TREES = ("py", "py2", "go", "ts", "rs")
R8_TREES = ("ts8", "go8", "py8", "rescue8")
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
_FLAGS = ("DEEPMAP_MODULE_DOCSTRINGS", "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_GRAPHIFY_CALLS_PLUS",
          "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_PRECISE_CALLS", "DEEPMAP_RESOLVED_IMPORTS",
          "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_CLEAN_OWNER_NAMES", "DEEPMAP_EXCLUDE_TESTS",
          "DEEPMAP_INCLUDE_VENDOR", "DEEPMAP_EXTRACTOR", "DEEPMAP_RESCUE_SKIPPED_DIRS",
          "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_FALSE_CALL_FILTER")

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")


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


_TMP = Path(tempfile.mkdtemp(prefix="dm_r8x_"))
_ROOTS: dict = {}


def files_of(name: str) -> dict:
    return R7[name]["files"] if name in R7_TREES else R8["trees"][name]["files"]


def root_of(name: str) -> Path:
    """Tree `name` materialized (LF bytes) as <tmp>/<name> — the dir name the fixture was captured under
    (the engine keys a root-level TS declaration and a Go type on it)."""
    if name not in _ROOTS:
        root = _TMP / "t" / name
        for rel, text in files_of(name).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        _ROOTS[name] = root
    return _ROOTS[name]


def graph_of(name: str, ver: str) -> dict:
    g = R7[name]["graph"] if (ver == "0.8.39" and name in R7_TREES) else R8["graphs"][ver][name]
    return json.loads(json.dumps(g))                       # a fresh copy each time


def mapped(name: str, ver: str, *, root=True, gx=GX):
    st: dict = {}
    n, c, i = gx.map_graph(graph_of(name, ver), root=root_of(name) if root else None, stats=st)
    return n, c, i, st


def facts(nodes) -> list:
    """Key-independent node identity (a Go type's engine id depends on the capture dir's name)."""
    return sorted((n["label"], n["name"], n["source_file"], n["start_line"]) for n in nodes)


def named(nodes, pairs) -> list:
    k = {n["key"]: n["name"] for n in nodes}
    return sorted((k.get(s, s), k.get(t, t)) for s, t in pairs)


def by_name(nodes) -> dict:
    out: dict = {}
    for n in nodes:
        out.setdefault(n["name"], []).append(n)
    return out


def _pre_round8_module():
    """The PRE-round-8 mapper, when a copy is handed in (DEEPMAP_R8_PRE_BUILD=<dir holding the old
    graphify_extract.py>) — used to show section A's RED half on 0.8.51's shape. None otherwise."""
    d = os.environ.get("DEEPMAP_R8_PRE_BUILD")
    if not d:
        return None
    spec = importlib.util.spec_from_file_location("gx_pre_r8", str(Path(d) / "graphify_extract.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, d)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(d)
    return mod


# ================================================================================================
def gate_a_r7_trees_both_shapes() -> None:
    print("\n-- A: the five round-7 trees map to the same corrected graph on 0.8.39 and 0.8.51 --")
    check("the fixture holds both ends of SUPPORTED_GRAPHIFY_SPEC",
          GX.SUPPORTED_GRAPHIFY_MIN <= (0, 8, 39) and (0, 8, 51) < GX.SUPPORTED_GRAPHIFY_BELOW
          and set(R8["graphs"]) == {"0.8.39", "0.8.51"}, GX.SUPPORTED_GRAPHIFY_SPEC)
    res = {v: {t: mapped(t, v) for t in R7_TREES} for v in ("0.8.39", "0.8.51")}
    # the 0.8.51 fixture really is the collapsed shape (else this section proves nothing)
    g51 = graph_of("py2", "0.8.51")
    trip = [(l["source"], l["target"], l["relation"]) for l in g51["links"]]
    check("0.8.51's py2 graph has no repeated (source, target, relation) link (dedupe_edges)",
          len(trip) == len(set(trip)), len(trip) - len(set(trip)))
    ids = [n["id"] for n in graph_of("go", "0.8.51")["nodes"]]
    check("0.8.51's go graph has no repeated node id (dedupe_nodes) — the file nodes are gone",
          len(ids) == len(set(ids)) and not any(n.get("label") == "customer.go" for n in graph_of("go", "0.8.51")["nodes"]))
    for v in ("0.8.39", "0.8.51"):
        n2, _c, _i, st = res[v]["py2"]
        b2 = by_name(n2)
        check(f"{v}: Context.invoke -> line 12 and top -> line 29 (the implementations, not the first stub)",
              b2["Context.invoke"][0]["start_line"] == 12 and b2["top"][0]["start_line"] == 29,
              (b2["Context.invoke"][0]["start_line"], b2["top"][0]["start_line"]))
        check(f"{v}: overloads_moved == 2", st.get("overloads_moved") == 2, st)
        ng, _c, _i, stg = res[v]["go"]
        mods = {n["source_file"]: n for n in ng if n["label"] == "Module"}
        check(f"{v}: models/customer.go and server/server.go keep a :Module node",
              {"models/customer.go", "server/server.go"} <= set(mods), sorted(mods))
        check(f"{v}: the file Module is re-keyed <id>::file and the type keeps the engine id",
              mods.get("models/customer.go", {}).get("key", "").endswith("::file")
              and any(n["name"] == "Customer" and not n["key"].endswith("::file") for n in ng))
        check(f"{v}: file_ids_split == 2", stg.get("file_ids_split") == 2, stg)
        nt, ct, _i, stt = res[v]["ts"]
        bt = by_name(nt)
        check(f"{v}: TS _parse (L2), parse (L5), $ZodThing (L8) recovered",
              any(n["start_line"] == 2 for n in bt.get("_parse", []))
              and any(n["start_line"] == 5 for n in bt.get("parse", []) if n["label"] != "Module")
              and any(n["start_line"] == 8 for n in bt.get("$ZodThing", [])), {k: bt.get(k) for k in ("_parse", "parse")})
        check(f"{v}: supplemental_nodes == 3 and still no TS CALLS by default",
              stt.get("supplemental_nodes") == 3 and ct == [], (stt.get("supplemental_nodes"), ct))
    check("file_nodes_restored: 0 on 0.8.39 (both nodes written), 2 on 0.8.51 (the writer dropped them)",
          res["0.8.39"]["go"][3].get("file_nodes_restored") == 0
          and res["0.8.51"]["go"][3].get("file_nodes_restored") == 2,
          (res["0.8.39"]["go"][3].get("file_nodes_restored"), res["0.8.51"]["go"][3].get("file_nodes_restored")))
    for t in R7_TREES:
        a, b = res["0.8.39"][t], res["0.8.51"][t]
        check(f"{t}: identical nodes / calls / imports on both engine shapes",
              facts(a[0]) == facts(b[0]) and named(a[0], a[1]) == named(b[0], b[1])
              and named(a[0], a[2]) == named(b[0], b[2]),
              (set(facts(a[0])) ^ set(facts(b[0])), set(named(a[0], a[1])) ^ set(named(b[0], b[1]))))
    for t in ("py", "py2", "ts", "rs"):          # same capture dir name -> the keys must agree too
        check(f"{t}: node dicts (keys, lines, summaries) byte-identical across shapes",
              sorted(res["0.8.39"][t][0], key=lambda n: n["key"]) == sorted(res["0.8.51"][t][0], key=lambda n: n["key"]))
    # the typed-edge layer still anchors on the rebuilt file Module (0.8.51 shape)
    try:
        import go_edges
        import edge_resolve as ER
        ng = res["0.8.51"]["go"][0]
        _supp, typed = go_edges.recognize(R7["go"]["files"]["server/server.go"], "server/server.go")
        rows = ER.resolve_typed_edges(typed, ER.Resolver(ng), {n["key"] for n in ng})
        check("0.8.51: the server.go route persists as a ROUTES_TO row on the rebuilt :Module",
              any(r[0] == "ROUTES_TO" for r in rows), (typed, rows))
    except Exception as e:  # noqa: BLE001
        check("go recognizer + edge_resolve run on the 0.8.51-shaped nodes", False, f"{type(e).__name__}: {e}")
    pre = _pre_round8_module()
    if pre is not None:
        n2, _c, _i, _s = mapped("py2", "0.8.51", gx=pre)
        ng, _c, _i, _s = mapped("go", "0.8.51", gx=pre)
        nt, _c, _i, stp = mapped("ts", "0.8.51", gx=pre)
        check("RED control: the PRE-round-8 mapper leaves Context.invoke on line 7 on 0.8.51's shape",
              by_name(n2)["Context.invoke"][0]["start_line"] == 7)
        check("RED control: the PRE-round-8 mapper gives models/customer.go no :Module on 0.8.51's shape",
              not any(n["source_file"] == "models/customer.go" and n["label"] == "Module" for n in ng))
        check("RED control: the PRE-round-8 mapper recovers 0 TS declarations on 0.8.51's shape",
              stp.get("supplemental_nodes") == 0, stp)


def gate_b_r8_trees_both_shapes() -> None:
    print("\n-- B: the round-8 trees on both engine shapes --")
    res = {v: {t: mapped(t, v) for t in R8_TREES} for v in ("0.8.39", "0.8.51")}
    for t in R8_TREES:
        a, b = res["0.8.39"][t], res["0.8.51"][t]
        check(f"{t}: node dicts / calls / imports byte-identical on both engine shapes",
              sorted(a[0], key=lambda n: n["key"]) == sorted(b[0], key=lambda n: n["key"])
              and sorted(a[1]) == sorted(b[1]) and sorted(a[2]) == sorted(b[2]),
              (set(facts(a[0])) ^ set(facts(b[0])), set(a[1]) ^ set(b[1]), set(a[2]) ^ set(b[2])))
    for v in ("0.8.39", "0.8.51"):
        n, _c, imports, st = res[v]["ts8"]
        mods = {x["source_file"]: x for x in n if x["label"] == "Module"}
        lo = mods.get("bench/lots-of-objects.ts", {})
        check(f"{v} ts8: bench/lots-of-objects.ts keeps its :Module although `const $` shares its engine id",
              lo.get("key") == "bench_lots_of_objects::file", mods.get("bench/lots-of-objects.ts"))
        dollar = [x for x in n if x["name"] == "$"]
        check(f"{v} ts8: `$` stays a node on the engine id", [x["key"] for x in dollar] == ["bench_lots_of_objects"],
              dollar)
        check(f"{v} ts8: the file's imports come from the file :Module, never from `$`",
              imports and all(s == "bench_lots_of_objects::file" for s, _t in imports), imports)
        got = {(x["name"], x["start_line"], x["label"]) for x in n if "@" in x["key"]}
        check(f"{v} ts8: recovered exactly the function expression, the arrow and the object const",
              got == {("_parse", 2, "Function"), ("_arrow", 4, "Function"), ("_obj", 30, "Class")}, sorted(got))
        names = {x["name"] for x in n}
        check(f"{v} ts8: never a node for a number literal, a `var`, a generator or a class in a function body",
              not ({"_lit", "$thing", "_gen", "_inner"} & names), sorted({"_lit", "$thing", "_gen", "_inner"} & names))
        check(f"{v} ts8: overload signatures (same kind) are not recovered; `over` stays one node on its body",
              [x["start_line"] for x in n if x["name"] == "over"] == [26], [x for x in n if x["name"] == "over"])
        check(f"{v} ts8: a METHOD `Svc.lookup` never pairs with the module const `lookup` (one node each)",
              sorted((x["label"], x["name"]) for x in n if x["name"].endswith("lookup"))
              == [("Class", "lookup"), ("Method", "Svc.lookup")])
        check(f"{v} ts8: supplemental_nodes == 3, file_ids_split == 1", st.get("supplemental_nodes") == 3
              and st.get("file_ids_split") == 1, st)
        n, _c, _i, st = res[v]["go8"]
        mods = {x["source_file"]: x["key"] for x in n if x["label"] == "Module"}
        check(f"{v} go8: binding/binding.go keeps its :Module under the DISAMBIGUATED id (+ ::file)",
              mods.get("binding/binding.go") == "binding_binding_go_binding_binding::file", mods)
        check(f"{v} go8: render/render.go keeps its :Module (plain id) and binding_nomsgpack.go is untouched",
              mods.get("render/render.go") == "render_render::file"
              and mods.get("binding/binding_nomsgpack.go") == "binding_binding_nomsgpack", mods)
        n, _c, _i, st = res[v]["py8"]
        lines = {(x["label"], x["name"]): x["start_line"] for x in n}
        check(f"{v} py8: module-level stubs under `if TYPE_CHECKING:` -> conv points at the implementation (9)",
              lines.get(("Function", "conv")) == 9, lines)
        check(f"{v} py8: class-level stubs under `if` (a separate engine Function) stay on their line (16); "
              f"the implementation A.get is its own node (19)",
              lines.get(("Function", "get")) == 16 and lines.get(("Method", "A.get")) == 19, lines)
        check(f"{v} py8: a stub-only group never borrows another class's same-named def (B.put 25, C.put 31)",
              lines.get(("Method", "B.put")) == 25 and lines.get(("Method", "C.put")) == 31, lines)
        check(f"{v} py8: overloads_moved == 1", st.get("overloads_moved") == 1, st)
        n, c, _i, st = res[v]["rescue8"]
        got = set(named(n, c))
        check(f"{v} rescue8: run() keeps -> Store.helper (its bare `helper(1)` is the recorded line, but "
              f"`obj.helper(2)` is in the same body)", ("run", "Store.helper") in got, sorted(got))
        check(f"{v} rescue8: only() (a bare `helper(3)` and nothing else) still drops it",
              ("only", "Store.helper") not in got, sorted(got))
        check(f"{v} rescue8: Go Use() keeps -> New (`new(` is recorded, `New()` follows)", ("Use", "New") in got,
              sorted(got))
        check(f"{v} rescue8: Go Only() (a `new(` only) still drops it", ("Only", "New") not in got, sorted(got))
        check(f"{v} rescue8: calls_filtered == 2", st.get("calls_filtered") == 2, st)
    pre = _pre_round8_module()
    if pre is not None:
        n, c, _i, _s = mapped("rescue8", "0.8.39", gx=pre)
        check("RED control: the PRE-round-8 filter dropped run() -> Store.helper and Use() -> New (both engines)",
              not ({("run", "Store.helper"), ("Use", "New")} & set(named(n, c))), named(n, c))
        n, _c, _i, _s = mapped("go8", "0.8.51", gx=pre)
        check("RED control: the PRE-round-8 mapper lost binding.go's and render.go's :Module on 0.8.51",
              not ({"binding/binding.go", "render/render.go"} & {x["source_file"] for x in n if x["label"] == "Module"}))
        n, _c, _i, _s = mapped("py8", "0.8.51", gx=pre)
        check("RED control: the PRE-round-8 mapper left conv on its first stub (5) on 0.8.51",
              [x["start_line"] for x in n if x["name"] == "conv"] == [5])


def gate_c_units() -> None:
    print("\n-- C: hermetic unit checks --")
    fid = GX._engine_file_id
    check("_engine_file_id: models/customer.go -> models_customer, gin.go -> gin, "
          "packages/tsc/bench/lots-of-objects.ts -> bench_lots_of_objects, a\\b\\c.go -> b_c",
          (fid("models/customer.go"), fid("gin.go"), fid("packages/tsc/bench/lots-of-objects.ts"), fid("a\\b\\c.go"))
          == ("models_customer", "gin", "bench_lots_of_objects", "b_c"))
    rs = GX._restore_swallowed_file_nodes
    raw = [{"id": "m_c", "label": "c.go", "file_type": "code", "source_file": "m/c.go", "source_location": "L1"},
           {"id": "m_c", "label": "C", "file_type": "code", "source_file": "m/c.go", "source_location": "L3"}]
    links = [{"source": "m_c", "target": "m_c", "relation": "contains", "source_file": "m/c.go", "source_location": "L3"}]
    out, k = rs(raw, links)
    check("nothing missing (the 0.8.39 shape) -> the SAME list object and 0 restored", out is raw and k == 0)
    one = [raw[1]]
    out, k = rs(one, links)
    check("the self-link restores the dropped file node before the type (0.8.39's order)",
          k == 1 and [n["label"] for n in out] == ["c.go", "C"] and out[0]["id"] == "m_c"
          and out[0]["source_file"] == "m/c.go" and out[0]["source_location"] == "L1", out)
    nolsf = [dict(links[0])]
    nolsf[0].pop("source_file")
    out, k = rs(one, nolsf)
    check("a writer without link source files: the self-link + the node's own file still restore it", k == 1, out)
    scope = [{"id": "f", "label": "f.v", "file_type": "code", "source_file": "f.v", "source_location": "L1"},
             {"id": "f_top", "label": "top", "file_type": "code", "source_file": "f.v", "source_location": "L2"},
             {"id": "f_top_x", "label": "x", "file_type": "code", "source_file": "f.v", "source_location": "L3"}]
    out, k = rs(scope, [{"source": "f_top", "target": "f_top_x", "relation": "contains", "source_file": "f.v"}])
    check("a scope node that `contains` things in a file that HAS a file node is never mistaken for a file",
          k == 0 and out is scope)
    lone = [{"id": "q_w", "label": "Widget", "file_type": "code", "source_file": "q/other.go", "source_location": "L4"}]
    out, k = rs(lone, [{"source": "q_w", "target": "q_x", "relation": "contains", "source_file": "q/w.go"}])
    check("a non-self `contains` needs the file's PLAIN engine id (q/w.go -> q_w): restored as q/w.go",
          k == 1 and out[0]["source_file"] == "q/w.go" and out[0]["label"] == "w.go", out)
    out, k = rs(lone, [{"source": "q_w", "target": "q_x", "relation": "contains", "source_file": "q/zz.go"}])
    check("...and never under an id that is not that file's (q/zz.go -> q_zz != q_w)", k == 0, out)

    # TS without a grammar: the source path degrades to nothing (never a wrong node); 0.8.39's links still work
    orig = GX._Source.ts_tree
    GX._Source.ts_tree = lambda self, sf: None
    try:
        _n, _c, _i, s39 = mapped("ts", "0.8.39")
        _n, _c, _i, s51 = mapped("ts", "0.8.51")
    finally:
        GX._Source.ts_tree = orig
    check("no tree-sitter grammar: 0.8.39's repeated links still recover 3; 0.8.51's shape recovers 0 (no guess)",
          s39.get("supplemental_nodes") == 3 and s51.get("supplemental_nodes") == 0,
          (s39.get("supplemental_nodes"), s51.get("supplemental_nodes")))

    # the rescue reads the caller's OWN body only: a genuine site in the NEXT function does not count
    d = _TMP / "span"
    (d / "p").mkdir(parents=True, exist_ok=True)
    (d / "p" / "s.py").write_bytes(b"class S:\n    def helper(self):\n        return 1\n")
    (d / "p" / "a.py").write_bytes(b"def only():\n    return helper()\n\n\ndef other(o):\n    return o.helper()\n")
    (d / "g").mkdir(parents=True, exist_ok=True)
    (d / "g" / "n.go").write_bytes(b"package g\n\nfunc New() int { return 1 }\n")
    (d / "g" / "u.go").write_bytes(b"package g\n\nfunc Only() int {\n\t_ = new(int)\n\treturn 0\n}\n\n"
                                   b"func After() int {\n\treturn New()\n}\n")
    g = {"nodes": [
        {"id": "p_s", "label": "s.py", "file_type": "code", "source_file": "p/s.py", "source_location": "L1"},
        {"id": "p_s_s", "label": "S", "file_type": "code", "source_file": "p/s.py", "source_location": "L1"},
        {"id": "p_s_s_helper", "label": ".helper()", "file_type": "code", "source_file": "p/s.py", "source_location": "L2"},
        {"id": "p_a", "label": "a.py", "file_type": "code", "source_file": "p/a.py", "source_location": "L1"},
        {"id": "p_a_only", "label": "only()", "file_type": "code", "source_file": "p/a.py", "source_location": "L1"},
        {"id": "p_a_other", "label": "other()", "file_type": "code", "source_file": "p/a.py", "source_location": "L5"},
        {"id": "g_n", "label": "n.go", "file_type": "code", "source_file": "g/n.go", "source_location": "L1"},
        {"id": "g_n_new", "label": "New()", "file_type": "code", "source_file": "g/n.go", "source_location": "L3"},
        {"id": "g_u", "label": "u.go", "file_type": "code", "source_file": "g/u.go", "source_location": "L1"},
        {"id": "g_u_only", "label": "Only()", "file_type": "code", "source_file": "g/u.go", "source_location": "L3"},
        {"id": "g_u_after", "label": "After()", "file_type": "code", "source_file": "g/u.go", "source_location": "L8"},
    ], "links": [
        {"source": "p_s", "target": "p_s_s", "relation": "contains", "source_location": "L1"},
        {"source": "p_s_s", "target": "p_s_s_helper", "relation": "method", "source_location": "L2"},
        {"source": "p_a", "target": "p_a_only", "relation": "contains", "source_location": "L1"},
        {"source": "p_a", "target": "p_a_other", "relation": "contains", "source_location": "L5"},
        {"source": "p_a_only", "target": "p_s_s_helper", "relation": "calls", "source_location": "L2"},
        {"source": "g_n", "target": "g_n_new", "relation": "contains", "source_location": "L3"},
        {"source": "g_u", "target": "g_u_only", "relation": "contains", "source_location": "L3"},
        {"source": "g_u", "target": "g_u_after", "relation": "contains", "source_location": "L8"},
        {"source": "g_u_only", "target": "g_n_new", "relation": "calls", "source_location": "L4"},
    ]}
    st: dict = {}
    n, c, _i = GX.map_graph(g, root=d, stats=st)
    got = set(named(n, c))
    check("a genuine `o.helper()` in the NEXT Python function never rescues only()'s bare call",
          ("only", "S.helper") not in got, sorted(got))
    check("a genuine `New()` in the NEXT Go function never rescues Only()'s `new(`", ("Only", "New") not in got,
          sorted(got))
    check("both drops are counted", st.get("calls_filtered") == 2, st)
    with _env(DEEPMAP_FALSE_CALL_FILTER="0"):
        _n, off, _i = GX.map_graph(json.loads(json.dumps(g)), root=d)
    check("DEEPMAP_FALSE_CALL_FILTER=0 keeps both, byte for byte", len(off) == 2, off)

    # the engine child never gets the project root on sys.path (a root-level types.py shadowed `types`)
    seen: dict = {}
    home = _TMP / "home_c"
    root = _TMP / "shadow"
    root.mkdir(parents=True, exist_ok=True)
    (root / "types.py").write_bytes(b"import enum\n")
    (root / "a.py").write_bytes(b"def f():\n    return 1\n")

    def fake_engine(cmd, *, cwd, env, timeout):
        seen.update(cmd=list(cmd), cwd=cwd, safe=env.get("PYTHONSAFEPATH"))
        out = Path(env["GRAPHIFY_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text(json.dumps({"nodes": [
            {"id": "a", "label": "a.py", "file_type": "code", "source_file": "a.py", "source_location": "L1"},
            {"id": "a_f", "label": "f()", "file_type": "code", "source_file": "a.py", "source_location": "L1"}],
            "links": [{"source": "a", "target": "a_f", "relation": "contains", "source_location": "L1"}]}),
            encoding="utf-8")
        return GX._EngineResult(list(cmd), 0, "", "")

    orig_run, orig_spec = GX._run_engine, importlib.util.find_spec
    GX._run_engine = fake_engine
    # hermetic: extract_with_report asks importlib whether graphify is installed before anything else
    importlib.util.find_spec = lambda name, *a, **k: (object() if name == "graphify" else orig_spec(name, *a, **k))
    try:
        with _env(DEEPMAP_HOME=str(home), PYTHONSAFEPATH=None):
            res, _rep = GX.extract_with_report(root)
            # R11/L1: the 3.10 expectation is the output dir under THIS gate's DEEPMAP_HOME, so it is computed
            # inside the same env — outside it, graphify_out_dir named the real ~/.deepmap and the check below
            # was RED on Python 3.10 (the declared minimum) while the product was right
            gate_out_dir = str(GX.graphify_out_dir(root))
    finally:
        GX._run_engine, importlib.util.find_spec = orig_run, orig_spec
    check("the 3.10 expectation (the engine's own output dir) lies under the gate's DEEPMAP_HOME, not the real one",
          Path(gate_out_dir).resolve().is_relative_to(home.resolve()), (gate_out_dir, str(home)))
    check("the engine runs with PYTHONSAFEPATH=1 (3.11+: the root never lands on sys.path)",
          seen.get("safe") == "1", seen)
    check("the command line itself is unchanged (`python -m graphify update <root> --no-cluster`)",
          seen.get("cmd", [])[1:3] == ["-m", "graphify"] and seen.get("cmd", [])[-1] == "--no-cluster", seen)
    want_cwd = str(root.resolve()) if sys.version_info >= (3, 11) else gate_out_dir
    check("the engine's working dir: the root on 3.11+, its own output dir on 3.10 (ignores PYTHONSAFEPATH)",
          seen.get("cwd") == want_cwd, (seen.get("cwd"), want_cwd))
    check("...and the mapped result comes back", bool(res) and len(res[0]) == 2, res)


def _real(tag: str, names, home: Path) -> dict:
    out = {}
    with _env(DEEPMAP_HOME=str(home), **{f: None for f in _FLAGS}):
        for name in names:
            out[name] = GX.extract_with_report(root_of(name))
    return out


def gate_d_real_engine() -> None:
    print("\n-- D: the REAL installed engine on all nine trees --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    ver = GX.graphify_version()
    print(f"  (graphify {ver})")
    res = _real("real", R7_TREES + R8_TREES, _TMP / "home_d")
    for name, (r, rep) in res.items():
        check(f"REAL {name}: the engine ran and produced nodes", r is not None and bool(r[0]), rep.get("cause"))
    if not all(r is not None for r, _ in res.values()):
        return
    n2 = by_name(res["py2"][0][0])
    check("REAL py2: Context.invoke -> 12, top -> 29",
          n2["Context.invoke"][0]["start_line"] == 12 and n2["top"][0]["start_line"] == 29)
    mods = {n["source_file"] for n in res["go"][0][0] if n["label"] == "Module"}
    check("REAL go: models/customer.go and server/server.go have :Module nodes",
          {"models/customer.go", "server/server.go"} <= mods, sorted(mods))
    bt = by_name(res["ts"][0][0])
    check("REAL ts: _parse / parse / $ZodThing recovered",
          any(n["start_line"] == 2 for n in bt.get("_parse", []))
          and any(n["start_line"] == 5 for n in bt.get("parse", []) if n["label"] != "Module")
          and any(n["start_line"] == 8 for n in bt.get("$ZodThing", [])))
    m8 = {n["source_file"] for n in res["ts8"][0][0] if n["label"] == "Module"} | \
         {n["source_file"] for n in res["go8"][0][0] if n["label"] == "Module"}
    check("REAL ts8/go8: bench/lots-of-objects.ts, binding/binding.go, render/render.go keep :Module nodes",
          {"bench/lots-of-objects.ts", "binding/binding.go", "render/render.go"} <= m8, sorted(m8))
    check("REAL py8: conv -> 9", [n["start_line"] for n in res["py8"][0][0] if n["name"] == "conv"] == [9])
    gotr = set(named(res["rescue8"][0][0], res["rescue8"][0][1]))
    check("REAL rescue8: run -> Store.helper and Use -> New kept; only / Only dropped",
          {("run", "Store.helper"), ("Use", "New")} <= gotr
          and not ({("only", "Store.helper"), ("Only", "New")} & gotr), sorted(gotr))
    # a project whose ROOT holds a module named like a stdlib one (types.py): `python -m graphify` used to
    # put the root first on sys.path, the engine's own `import enum` then failed (rc=1) and the whole
    # extraction fell back to ast — the TS file vanished from the graph
    sh = _TMP / "shadow_real"
    sh.mkdir(parents=True, exist_ok=True)
    (sh / "types.py").write_bytes(b"import enum\n\n\nclass Color(enum.Enum):\n    RED = 1\n")
    (sh / "web.ts").write_bytes(b"export function hello() {\n  return 1;\n}\n")
    with _env(DEEPMAP_HOME=str(_TMP / "home_d2"), **{f: None for f in _FLAGS}):
        r, rep = GX.extract_with_report(sh)
    check("REAL: a root-level types.py no longer breaks the engine (the TS function is in the graph)",
          r is not None and any(n["name"] == "hello" and n["source_file"] == "web.ts" for n in r[0]),
          rep.get("cause"))
    if ver in R8["graphs"]:
        for name in R7_TREES + R8_TREES:
            r = res[name][0]
            f = mapped(name, ver)
            same = facts(r[0]) == facts(f[0]) and named(r[0], r[1]) == named(f[0], f[1])
            check(f"REAL {name} == the fixture's graphify {ver} answer (the fixture is this engine's real output)",
                  same, (set(facts(r[0])) ^ set(facts(f[0])), set(named(r[0], r[1])) ^ set(named(f[0], f[1]))))
    else:
        print(f"  (no fixture capture for graphify {ver}: the fixture comparison is skipped)")


_ALT_CHILD = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import graphify_extract as GX
out = {"version": GX.graphify_version(), "trees": {}}
for root in sys.argv[3:]:
    r, rep = GX.extract_with_report(Path(root))
    out["trees"][Path(root).name] = None if r is None else [r[0], [list(x) for x in r[1]], [list(x) for x in r[2]]]
Path(sys.argv[2]).write_text(json.dumps(out), encoding="utf-8")
"""


def gate_e_alt_engine() -> None:
    print("\n-- E: optional cross-engine run (DEEPMAP_R8_ALT_PYTHON) --")
    alt = os.environ.get("DEEPMAP_R8_ALT_PYTHON")
    if not alt or not _HAS_ENGINE:
        print("  [SKIP] DEEPMAP_R8_ALT_PYTHON not set (or no local engine)")
        return
    names = R7_TREES + R8_TREES
    here = _real("here", names, _TMP / "home_e1")
    child = _TMP / "alt_child.py"
    child.write_text(_ALT_CHILD, encoding="utf-8")
    dump = _TMP / "alt.json"
    env = {k: v for k, v in os.environ.items() if k not in _FLAGS}
    env.update(DEEPMAP_HOME=str(_TMP / "home_e2"), PYTHONUTF8="1")
    proc = subprocess.run([alt, str(child), str(BUILD), str(dump)] + [str(root_of(n)) for n in names],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
    if proc.returncode != 0 or not dump.is_file():
        check("the alternate interpreter extracted the trees", False, (proc.returncode, proc.stderr[-800:]))
        return
    other = json.loads(dump.read_text(encoding="utf-8"))
    print(f"  (local graphify {GX.graphify_version()} vs alternate graphify {other.get('version')})")
    check("the alternate interpreter runs a DIFFERENT graphify", other.get("version") != GX.graphify_version(),
          other.get("version"))
    for name in names:
        a = here[name][0]
        b = other["trees"].get(name)
        if a is None or b is None:
            check(f"{name}: both engines produced a graph", False)
            continue
        check(f"{name}: same nodes / calls / imports under both engines",
              facts(a[0]) == facts(b[0]) and named(a[0], a[1]) == named(b[0], b[1])
              and named(a[0], a[2]) == named(b[0], b[2]),
              (set(facts(a[0])) ^ set(facts(b[0])), set(named(a[0], a[1])) ^ set(named(b[0], b[1]))))


def main() -> int:
    for f in _FLAGS:
        os.environ.pop(f, None)
    print("extraction_r8_gates — J2: graphify-output corrections hold on graphify 0.8.39 AND 0.8.51")
    try:
        for g in (gate_a_r7_trees_both_shapes, gate_b_r8_trees_both_shapes, gate_c_units, gate_d_real_engine,
                  gate_e_alt_engine):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
