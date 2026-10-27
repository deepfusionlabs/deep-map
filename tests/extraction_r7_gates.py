#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 W5 gates: the graphify-path extraction fixes and the default-OFF extraction flags.
No Neo4j, no network, no spend. The REAL-engine section runs the installed graphify (skipped when absent)
under a temporary DEEPMAP_HOME, so nothing is written to ~/.deepmap.

Fixture: tests/fixtures/graphify_r7/extraction_r7.json — five small source trees, the REAL graph.json
graphify 0.8.39 produced for each, and `golden` = map_graph(graph, root) on the PRE-round-7 code.

Default ON (correctness / coverage — ROUND7 I3):
  A  T3#10   a Python import the engine left as a bare module name (`import helper`, `from pkg import m`)
             is resolved against the graph's Python modules instead of being dropped (DEEPMAP_RESOLVED_
             IMPORTS=0 keeps the old tuples byte for byte).
  D  T4#4    a node for an @overload group points at the IMPLEMENTATION def, not the first stub.
  E  T5#4 / T4d-F3  compiler-guaranteed false CALLS are dropped: a Go non-_test caller never binds a
             _test.go symbol; a Go selector call `pkg.F()` never binds the caller package's own top-level
             F; a case-sensitive language never binds a callee whose name differs in case on the call
             line; a bare no-receiver call never binds another file's Method (Python/Go/JS/TS/Rust).
  F  T8#10   graphify reuses one id for a Go file and a same-named type: the file keeps a :Module node.
  G  T5#2    a TS/JS declaration graphify lost to an id collision (`type $Parse` vs `const _parse`) is
             recovered as a node (nodes only; its body calls behind DEEPMAP_SUPPLEMENTAL_TS_RUST=1).
  H  T5#3    Rust trait methods (default bodies + required signatures) become :Method nodes (nodes only;
             default-body calls behind DEEPMAP_SUPPLEMENTAL_TS_RUST=1).
Default OFF (ROUND7 I2 — byte-identical graph when off, proven in B):
  C1 DEEPMAP_MODULE_DOCSTRINGS=1   .py Module nodes take the real module docstring (T2#5).
  C2 DEEPMAP_SUMMARY_NORMALIZE=1   Python docstring summaries = real first sentence, no r\"\"\"/indent
                                    runs, word-boundary cut (T4d-F5).
  C3 DEEPMAP_GRAPHIFY_CALLS_PLUS=1 alias.f() / module.f() via the file's import table, nested-closure calls
                                    attributed to the enclosing node, module-level calls on the Module
                                    node (T3#3, T4d-F4).
I  the REAL engine on the same trees.   J  sync._rebuild_code carries the new counters in `.info`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "graphify_r7" / "extraction_r7.json"
GO_GORM = REPO / "tests" / "fixtures" / "graphify_multilang" / "go_gorm.json"
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
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail != '' else ''}")


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


DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
_ROOTS: dict = {}
_TMP = Path(tempfile.mkdtemp(prefix="dm_r7x_"))


def root_of(name: str) -> Path:
    """The fixture tree `name` materialized (LF bytes) under this run's temp dir."""
    if name not in _ROOTS:
        root = _TMP / f"src_{name}"
        for rel, text in DATA[name]["files"].items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        _ROOTS[name] = root
    return _ROOTS[name]


def mapped(name: str, *, root=True, stats=None):
    g = json.loads(json.dumps(DATA[name]["graph"]))          # a fresh copy each time
    return GX.map_graph(g, root=root_of(name) if root else None, stats=stats)


def by_name(nodes) -> dict:
    out: dict = {}
    for n in nodes:
        out.setdefault(n["name"], []).append(n)
    return out


def golden(name: str):
    gd = DATA[name]["golden"]
    return gd["nodes"], [tuple(c) for c in gd["calls"]], [tuple(i) for i in gd["imports"]]


# ================================================================================================
def gate_a_bare_imports() -> None:
    print("\n-- A (T3#10): bare Python module imports are resolved, not dropped --")
    stats: dict = {}
    nodes, _calls, imports = mapped("py", stats=stats)
    keys = {n["key"] for n in nodes}
    got = set(imports)
    check("`import helper` / `from helper import f` -> the helper.py module node (key form)",
          ("scripts_run", "scripts_helper") in got, sorted(got))
    check("`import helpers as _hs` -> the helpers.py module node", ("scripts_run", "scripts_helpers") in got,
          sorted(got))
    check("`from pkg import mod` -> the package's __init__.py module node", ("scripts_run", "pkg_init") in got,
          sorted(got))
    check("the import graphify already resolved (`import pkg.mod`) is unchanged", ("scripts_run", "pkg_mod") in got)
    check("a stdlib import (`typing`) is still dropped (no module in the repo)",
          not any(t in ("typing", "Typing") for _s, t in imports), sorted(got))
    check("every key-form target is a node of the SAME returned list",
          all(t in keys for _s, t in imports), [t for _s, t in imports if t not in keys])
    check("the imports list stays distinct", len(imports) == len(got), imports)
    check("stats: imports_bare_resolved counts the newly bound imports (3)",
          stats.get("imports_bare_resolved") == 3, stats)
    check("stats: imports_resolved counts every key-form import (4)", stats.get("imports_resolved") == 4, stats)

    # pure mode: the module index needs only node paths, not source files
    n2, _c2, i2 = mapped("py", root=False)
    check("pure mode (no root) resolves the same imports", set(i2) == got, sorted(set(i2) ^ got))

    with _env(DEEPMAP_RESOLVED_IMPORTS="0"):
        _n, _c, legacy = mapped("py")
    check("DEEPMAP_RESOLVED_IMPORTS=0: the old name tuples, byte for byte (bare imports still dropped)",
          legacy == [("scripts_run", "mod")], legacy)

    # the W7 consumer binds them EXACTLY (EXTRACTED), no fan-out
    try:
        import edge_resolve as ER
        res = ER.Resolver(nodes)
        rows = ER.resolve_imports(imports, res, {n["key"] for n in nodes}, {n["key"] for n in nodes})
        pairs = {(r[1], r[2]): r[3] for r in rows}
        check("edge_resolve binds run.py -> helper.py / helpers.py / pkg/__init__.py as EXTRACTED",
              all(pairs.get(("scripts_run", k)) == "EXTRACTED" for k in ("scripts_helper", "scripts_helpers",
                                                                         "pkg_init")), pairs)
    except Exception as e:  # noqa: BLE001
        check("edge_resolve consumer runs", False, f"{type(e).__name__}: {e}")

    # ambiguity: two helper.py modules. The importer's own directory wins (a script's sys.path[0]); with
    # no same-directory candidate the NAME goes on (edge_resolve then grades it AMBIGUOUS across both).
    graph = {"nodes": [
        {"id": "a_run", "label": "run.py", "file_type": "code", "source_file": "a/run.py", "source_location": "L1"},
        {"id": "a_helper", "label": "helper.py", "file_type": "code", "source_file": "a/helper.py",
         "source_location": "L1"},
        {"id": "b_helper", "label": "helper.py", "file_type": "code", "source_file": "b/helper.py",
         "source_location": "L1"},
        {"id": "c_main", "label": "main.py", "file_type": "code", "source_file": "c/main.py", "source_location": "L1"},
    ], "links": [
        {"source": "a_run", "target": "helper", "relation": "imports", "source_location": "L1"},
        {"source": "c_main", "target": "helper", "relation": "imports", "source_location": "L1"},
    ]}
    _n, _c, amb = GX.map_graph(graph)
    check("two same-named modules: the importer's own directory wins (a/run.py -> a/helper.py)",
          ("a_run", "a_helper") in amb, amb)
    check("two same-named modules, none beside the importer: the NAME form goes on (-> AMBIGUOUS downstream)",
          ("c_main", "helper") in amb, amb)
    # a TS import of a bare package is NOT resolved against Python modules (language family)
    graph["nodes"].append({"id": "web_app", "label": "app.ts", "file_type": "code", "source_file": "web/app.ts",
                           "source_location": "L1"})
    graph["links"].append({"source": "web_app", "target": "helper", "relation": "imports_from",
                           "source_location": "L1"})
    _n, _c, amb2 = GX.map_graph(graph)
    check("a non-Python importer's bare target is left alone (still dropped)",
          not any(s == "web_app" for s, _t in amb2), amb2)


def gate_b_flags_off_identity() -> None:
    print("\n-- B (I2): every round-7 flag OFF -> nodes and calls byte-identical to the pre-round-7 mapper --")
    gnodes, gcalls, _gi = golden("py")
    for label, env in (("flags unset", {f: None for f in _FLAGS}),
                       ("flags explicitly 0", dict({f: None for f in _FLAGS}, DEEPMAP_MODULE_DOCSTRINGS="0",
                                                   DEEPMAP_SUMMARY_NORMALIZE="0",
                                                   DEEPMAP_GRAPHIFY_CALLS_PLUS="0",
                                                   DEEPMAP_SUPPLEMENTAL_TS_RUST="0"))):
        with _env(**env):
            nodes, calls, _imports = mapped("py")
        check(f"{label}: py nodes == golden (summaries, documented, lines, keys)", nodes == gnodes,
              [(a, b) for a, b in zip(nodes, gnodes) if a != b][:2] or (len(nodes), len(gnodes)))
        check(f"{label}: py calls == golden", calls == gcalls, (calls, gcalls))
    # the TS/Rust call supplement is OFF by default: no CALLS appear for the recovered nodes
    with _env(**{f: None for f in _FLAGS}):
        _n, tcalls, _i = mapped("ts")
        _n, rcalls, _i = mapped("rs")
    check("TS: no CALLS by default (golden has none; recovered nodes add none)",
          tcalls == golden("ts")[1] == [], tcalls)
    check("Rust: no CALLS by default (trait-method nodes add none)", rcalls == golden("rs")[1] == [], rcalls)


def gate_c_flags_on() -> None:
    print("\n-- C1 (T2#5): DEEPMAP_MODULE_DOCSTRINGS=1 — .py Module nodes take the real module docstring --")
    base = {f: None for f in _FLAGS}
    with _env(**dict(base, DEEPMAP_MODULE_DOCSTRINGS="1")):
        nodes, calls, _imports = mapped("py")
    bn = by_name(nodes)
    helpers = bn.get("helpers", [{}])[0]
    check("shebang + coding line: the docstring is found (was a name echo)",
          helpers.get("body_summary") == "Helpers: feature flags for the app." and helpers.get("documented") is True,
          helpers)
    mod = bn.get("mod", [{}])[0]
    check("a '# TODO' comment inside a function is NOT the module summary (no docstring -> undocumented)",
          not str(mod.get("body_summary", "")).startswith("#") and mod.get("documented") is False, mod)
    todo = bn.get("todo_later", [{}])[0]
    check("docstring + a TODO comment: the docstring wins (was the TODO)",
          todo.get("body_summary") == "Queue retry helpers." and todo.get("documented") is True, todo)
    helper = bn.get("helper", [{}])[0]
    check("a plain module docstring is unchanged", helper.get("body_summary") == "Helper module docstring.", helper)
    gnodes, gcalls, _ = golden("py")
    check("non-Module nodes are untouched by the flag",
          [n for n in nodes if n["label"] != "Module"] == [n for n in gnodes if n["label"] != "Module"])
    check("calls are untouched by the flag", calls == gcalls)

    print("\n-- C2 (T4d-F5): DEEPMAP_SUMMARY_NORMALIZE=1 — real first sentence, no r\"\"\"/indent runs --")
    with _env(**dict(base, DEEPMAP_SUMMARY_NORMALIZE="1")):
        nodes, calls, _imports = mapped("py")
    bn = by_name(nodes)
    ld = bn.get("long_doc", [{}])[0].get("body_summary", "")
    check("r\"\"\" prefix gone and the full first sentence kept (graphify cut it at 80 chars)",
          ld == "Return the configured retry policy for the given queue, honouring overrides and defaults set "
                "elsewhere.", ld)
    lo = bn.get("Loader", [{}])[0].get("body_summary", "")
    check("indentation runs collapsed, first sentence only", lo == "Load things from disk.", lo)
    ll = bn.get("Loader.load", [{}])[0].get("body_summary", "")
    check("a one-line docstring -> its first sentence", ll == "Load one item.", ll)
    check("no summary starts with a string prefix/quote", not any(
        str(n["body_summary"]).lstrip().startswith(("r\"", "r'", '"""', "'''")) for n in nodes))
    check("documented flags unchanged", [n["documented"] for n in nodes] == [n["documented"] for n in gnodes])
    check("calls unchanged", calls == gcalls)
    # word-boundary cut for a >300-char first sentence
    long_word = "alpha " * 70
    fn = getattr(GX, "_first_sentence", None)
    if callable(fn):
        out = fn("Frobnicate " + long_word + "end.")
        check("a >300-char first sentence is cut at a word boundary (<= 300 chars, no split word)",
              len(out) <= 300 and not out.endswith("alph") and out.split()[-1].rstrip("…") in ("alpha", "Frobnicate"),
              out[-40:])
    else:
        check("graphify_extract._first_sentence exists", False)

    print("\n-- C3 (T3#3/T4d-F4): DEEPMAP_GRAPHIFY_CALLS_PLUS=1 — alias/module calls, nested closures, module level --")
    with _env(**dict(base, DEEPMAP_GRAPHIFY_CALLS_PLUS="1")):
        nodes, calls, _imports = mapped("py")
        _n2, calls2, _i2 = mapped("py2")
    cs = set(calls)
    # R10/P5: an addition resolves to exactly ONE node, so it is keyed to that node even WITHOUT
    # DEEPMAP_PRECISE_CALLS (it used to be the bare name, which the push fanned out to every same-named node)
    check("alias call `_hs.feature_enabled(1)` -> helpers.py:feature_enabled (keyed)",
          ("scripts_run_main", "scripts_helpers_feature_enabled") in cs, calls)
    check("module call `pkg.mod.g()` / `m2.g()` -> pkg/mod.py:g (keyed)", ("scripts_run_main", "pkg_mod_g") in cs, calls)
    check("a nested closure's call `local_t()` is attributed to the enclosing main() (keyed)",
          ("scripts_run_main", "scripts_run_local_t") in cs, calls)
    check("a module-level call `_hs.feature_enabled(3)` is recorded on the Module node (keyed)",
          ("scripts_run", "scripts_helpers_feature_enabled") in cs, calls)
    check("graphify's own edge is kept once (main -> f)", calls.count(("scripts_run_main", "f")) == 1, calls)
    check("a call to the nested def itself (`inner()`) is not an edge (it is no node)",
          not any(t == "inner" for _s, t in calls), calls)
    check("py2: the nested `_match` closure's call `helper_top(p)` is attributed to StateMachineMatcher.match (keyed)",
          ("pkg2_matcher_statemachinematcher_match", "pkg2_matcher_helper_top") in set(calls2), calls2)
    with _env(**dict(base, DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS="1")):
        _n, pcalls, _i = mapped("py")
    check("precise mode: the supplemented calls carry the resolved node KEY",
          ("scripts_run_main", "scripts_helpers_feature_enabled") in set(pcalls)
          and ("scripts_run_main", "pkg_mod_g") in set(pcalls), pcalls)


def gate_d_overload() -> None:
    print("\n-- D (T4#4): an @overload group's node points at the implementation --")
    stats: dict = {}
    nodes, _calls, _imports = mapped("py2", stats=stats)
    bn = by_name(nodes)
    inv = bn.get("Context.invoke", [{}])[0]
    check("Context.invoke start_line = 12 (the implementation; graphify said 7, the first @t.overload stub)",
          inv.get("start_line") == 12, inv.get("start_line"))
    top = bn.get("top", [{}])[0]
    check("top start_line = 29 (graphify said 26, the first @overload stub)", top.get("start_line") == 29,
          top.get("start_line"))
    nm = bn.get("Context.name", [{}])[0]
    check("a property getter/setter pair (not an overload) keeps the getter line 17", nm.get("start_line") == 17,
          nm.get("start_line"))
    check("stats: overloads_moved == 2", stats.get("overloads_moved") == 2, stats)
    n0, _c, _i = mapped("py2", root=False)
    check("pure mode (no source) keeps graphify's line", by_name(n0)["Context.invoke"][0]["start_line"] == 7)


def gate_e_false_calls() -> None:
    print("\n-- E (T5#4/T4d-F3): compiler-guaranteed false CALLS are dropped --")
    stats: dict = {}
    nodes, calls, _imports = mapped("py2", stats=stats)
    cs = set(calls)
    check("a bare `_match(path)` never binds the test file's _Routing._match method",
          ("pkg2_matcher_statemachinematcher_match", "_Routing._match") not in cs, calls)
    check("a receiver call `_Routing()._match(1)` in the same file is kept",
          ("tests_test_routing_test_it", "_Routing._match") in cs, calls)
    check("a class instantiation `_Routing()` is kept", ("tests_test_routing_test_it", "_Routing") in cs, calls)
    m = by_name(nodes)["StateMachineMatcher.match"][0]
    check("the dropped callee no longer appears in the caller's name-derived summary",
          "routing match" not in m["body_summary"], m["body_summary"])

    gstats: dict = {}
    _n, gcalls, _i = mapped("go", stats=gstats)
    gs = set(gcalls)
    eng = [n for n in _n if n["name"] == "Engine.LoadHTMLGlob"][0]["key"]
    check("Go: a non-_test caller never binds a _test.go symbol (Engine.LoadHTMLGlob -/-> must)",
          (eng, "must") not in gs, gcalls)
    check("Go: a _test.go caller still binds its _test.go helper (TestX -> must)",
          ("context_test_testx", "must") in gs, gcalls)
    check("Go: a selector call `template.New(...)` never binds the caller package's own func New",
          (eng, "New") not in gs, gcalls)
    check("Go: a selector call into ANOTHER package (`util.Helper()`) is kept", (eng, "Helper") in gs, gcalls)
    check("Go: a method call `e.Reset()` is kept", (eng, "Engine.Reset") in gs, gcalls)
    check("Go: a bare call to a same-package function (`local()`) is kept", (eng, "local") in gs, gcalls)
    check("stats: calls_filtered counts the dropped Go calls (2)", gstats.get("calls_filtered") == 2, gstats)
    _n, pure, _i = mapped("go", root=False)
    check("pure mode: the _test.go rule still applies (needs no source line)", (eng, "must") not in set(pure), pure)
    with _env(DEEPMAP_FALSE_CALL_FILTER="0"):
        _n, off, _i = mapped("go")
    check("DEEPMAP_FALSE_CALL_FILTER=0 restores graphify's calls byte for byte", off == golden("go")[1], off)

    # exact case: graphify resolves casefolded names; a case-sensitive language cannot call `compute` as
    # `Compute`. Java: a bare call can be an implicit-this method call -> the bare-call rule is NOT applied.
    tmp = _TMP / "case"
    (tmp / "a").mkdir(parents=True, exist_ok=True)
    (tmp / "a" / "a.go").write_bytes(b"package a\n\nfunc Run() {\n\tv := Compute(1)\n\t_ = v\n}\n")
    (tmp / "a" / "b.go").write_bytes(b"package a\n\nfunc compute(x int) int { return x }\n")
    (tmp / "j").mkdir(parents=True, exist_ok=True)
    (tmp / "j" / "Child.java").write_bytes(b"class Child extends Base {\n  void go() {\n    helper();\n  }\n}\n")
    (tmp / "j" / "Base.java").write_bytes(b"class Base {\n  void helper() {}\n}\n")
    graph = {"nodes": [
        {"id": "a_a", "label": "a.go", "file_type": "code", "source_file": "a/a.go", "source_location": "L1"},
        {"id": "a_a_run", "label": "Run()", "file_type": "code", "source_file": "a/a.go", "source_location": "L3"},
        {"id": "a_b", "label": "b.go", "file_type": "code", "source_file": "a/b.go", "source_location": "L1"},
        {"id": "a_b_compute", "label": "compute()", "file_type": "code", "source_file": "a/b.go",
         "source_location": "L3"},
        {"id": "j_child", "label": "Child.java", "file_type": "code", "source_file": "j/Child.java",
         "source_location": "L1"},
        {"id": "j_child_child", "label": "Child", "file_type": "code", "source_file": "j/Child.java",
         "source_location": "L1"},
        {"id": "j_child_child_go", "label": ".go()", "file_type": "code", "source_file": "j/Child.java",
         "source_location": "L2"},
        {"id": "j_base", "label": "Base.java", "file_type": "code", "source_file": "j/Base.java",
         "source_location": "L1"},
        {"id": "j_base_base", "label": "Base", "file_type": "code", "source_file": "j/Base.java",
         "source_location": "L1"},
        {"id": "j_base_base_helper", "label": ".helper()", "file_type": "code", "source_file": "j/Base.java",
         "source_location": "L2"},
    ], "links": [
        {"source": "a_a", "target": "a_a_run", "relation": "contains", "source_location": "L3"},
        {"source": "a_b", "target": "a_b_compute", "relation": "contains", "source_location": "L3"},
        {"source": "a_a_run", "target": "a_b_compute", "relation": "calls", "source_location": "L4"},
        {"source": "j_child", "target": "j_child_child", "relation": "contains", "source_location": "L1"},
        {"source": "j_child_child", "target": "j_child_child_go", "relation": "method", "source_location": "L2"},
        {"source": "j_base", "target": "j_base_base", "relation": "contains", "source_location": "L1"},
        {"source": "j_base_base", "target": "j_base_base_helper", "relation": "method", "source_location": "L2"},
        {"source": "j_child_child_go", "target": "j_base_base_helper", "relation": "calls",
         "source_location": "L3"},
    ]}
    # an external test package (`package a_test`) lives in the same dir and legitimately calls `a.Run()`
    (tmp / "a" / "x_test.go").write_bytes(b"package a_test\n\nfunc TestRun(t *testing.T) {\n\ta.Run()\n}\n")
    graph["nodes"] += [
        {"id": "a_x_test", "label": "x_test.go", "file_type": "code", "source_file": "a/x_test.go",
         "source_location": "L1"},
        {"id": "a_x_test_testrun", "label": "TestRun()", "file_type": "code", "source_file": "a/x_test.go",
         "source_location": "L3"}]
    graph["links"] += [
        {"source": "a_x_test", "target": "a_x_test_testrun", "relation": "contains", "source_location": "L3"},
        {"source": "a_x_test_testrun", "target": "a_a_run", "relation": "calls", "source_location": "L4"}]
    _n, ccalls, _i = GX.map_graph(graph, root=tmp)
    check("case-sensitive language: `Compute(1)` never binds `compute`", ("a_a_run", "compute") not in ccalls, ccalls)
    check("Java: a bare call to an inherited method in another file is KEPT (implicit this)",
          ("j_child_child_go", "Base.helper") in ccalls, ccalls)
    check("Go: an external test package's `a.Run()` (same dir, _test.go caller) is KEPT",
          ("a_x_test_testrun", "Run") in ccalls, ccalls)


def gate_f_go_module_ids() -> None:
    print("\n-- F (T8#10): a Go file whose id graphify reuses for a same-named type keeps its :Module --")
    nodes, _calls, _imports = mapped("go")
    mods = {n["source_file"]: n for n in nodes if n["label"] == "Module"}
    check("models/customer.go has a :Module node", "models/customer.go" in mods, sorted(mods))
    check("server/server.go has a :Module node", "server/server.go" in mods, sorted(mods))
    cls = {n["name"]: n for n in nodes if n["label"] == "Class"}
    check("the type keeps graphify's id as its key (Customer -> models_customer)",
          cls.get("Customer", {}).get("key") == "models_customer", cls.get("Customer"))
    keys = [n["key"] for n in nodes]
    check("node keys are unique", len(keys) == len(set(keys)), keys)
    check("the file node's key is distinct from the type's",
          mods.get("models/customer.go", {}).get("key") not in (None, "models_customer"), mods.get("models/customer.go"))
    check("the file Module is named after the file stem", mods.get("models/customer.go", {}).get("name") == "customer")
    meth = {n["name"]: n for n in nodes if n["label"] == "Method"}
    check("the type's method still maps (Customer.Save)", "Customer.Save" in meth, sorted(meth))

    g = json.loads(GO_GORM.read_text(encoding="utf-8"))
    n2, _c2, _i2 = GX.map_graph(g)
    m2 = {n["source_file"] for n in n2 if n["label"] == "Module"}
    check("go_gorm.json (pure): model/order.go gets its :Module", "model/order.go" in m2, sorted(m2))
    c2 = {n["name"] for n in n2 if n["label"] == "Class"}
    check("go_gorm.json: the Order type is still a Class", "Order" in c2, sorted(c2))
    check("go_gorm.json: Order.Total still maps", any(n["name"] == "Order.Total" for n in n2))

    # the typed-edge layer: a route declared in server/server.go anchors on the file Module (0 persisted before)
    try:
        import go_edges
        import edge_resolve as ER
        src = DATA["go"]["files"]["server/server.go"]
        _supp, typed = go_edges.recognize(src, "server/server.go")
        res = ER.Resolver(nodes)
        rows = ER.resolve_typed_edges(typed, res, {n["key"] for n in nodes})
        routes = [r for r in rows if r[0] == "ROUTES_TO"]
        check("the server.go route (http.HandleFunc) now persists as a ROUTES_TO row", len(routes) >= 1,
              (typed, rows))
    except Exception as e:  # noqa: BLE001
        check("go recognizer + edge_resolve run on the mapped nodes", False, f"{type(e).__name__}: {e}")

    tn, _tc, _ti = mapped("ts")
    check("no collision -> no re-keyed file node (TS keys all graphify ids or recovered @L keys)",
          not any(k.endswith("::file") for k in (n["key"] for n in tn)))


def gate_g_ts_collisions() -> None:
    print("\n-- G (T5#2): TS declarations graphify lost to an id collision are recovered (nodes only) --")
    stats: dict = {}
    nodes, calls, _imports = mapped("ts", stats=stats)
    bn = by_name(nodes)
    check("`export const _parse = (...) =>` (collided with `type $Parse`) -> a Function node at L2",
          any(n["label"] == "Function" and n["start_line"] == 2 for n in bn.get("_parse", [])), bn.get("_parse"))
    check("`export const parse = _parse(Error)` -> a node at L5", any(n["start_line"] == 5 for n in bn.get("parse", [])
                                                                     if n["label"] != "Module"), bn.get("parse"))
    check("`export const $ZodThing = $constructor(...)` (collided with the interface) -> a node at L8",
          any(n["start_line"] == 8 for n in bn.get("$ZodThing", [])), bn.get("$ZodThing"))
    check("the interface node itself (L6) is unchanged", any(n["start_line"] == 6 for n in bn.get("$ZodThing", [])))
    gk = {n["key"]: n for n in golden("ts")[0]}
    check("every golden node is still there, unchanged", all(gk[k] in nodes for k in gk))
    keys = [n["key"] for n in nodes]
    check("node keys stay unique", len(keys) == len(set(keys)), keys)
    check("stats: supplemental_nodes == 3", stats.get("supplemental_nodes") == 3, stats)
    check("no CALLS by default", calls == [], calls)
    with _env(DEEPMAP_SUPPLEMENTAL_TS_RUST="1"):
        n1, c1, _i = mapped("ts")
    names = {n["key"]: n["name"] for n in n1}
    got = {(names.get(s), t) for s, t in c1}
    check("DEEPMAP_SUPPLEMENTAL_TS_RUST=1: parse -> _parse and $ZodThing -> $constructor",
          ("parse", "_parse") in got and ("$ZodThing", "$constructor") in got, sorted(got))


def gate_h_rust_traits() -> None:
    print("\n-- H (T5#3): Rust trait methods become :Method nodes (nodes only) --")
    stats: dict = {}
    nodes, calls, _imports = mapped("rs", stats=stats)
    meth = {n["name"]: n for n in nodes if n["label"] == "Method"}
    check("default method Formatter.begin_string (L2)", meth.get("Formatter.begin_string", {}).get("start_line") == 2,
          sorted(meth))
    check("default method Formatter.write_char_escape (L3)",
          meth.get("Formatter.write_char_escape", {}).get("start_line") == 3, sorted(meth))
    check("required signature Formatter.required (L6)", meth.get("Formatter.required", {}).get("start_line") == 6,
          sorted(meth))
    check("the impl method CompactFormatter.required is unchanged", "CompactFormatter.required" in meth)
    gk = {n["key"]: n for n in golden("rs")[0]}
    check("every golden node is still there, unchanged", all(gk[k] in nodes for k in gk))
    check("stats: supplemental_nodes == 3", stats.get("supplemental_nodes") == 3, stats)
    check("no CALLS by default", calls == [], calls)
    with _env(DEEPMAP_SUPPLEMENTAL_TS_RUST="1"):
        n1, c1, _i = mapped("rs")
    names = {n["key"]: n["name"] for n in n1}
    got = {(names.get(s), t) for s, t in c1}
    check("DEEPMAP_SUPPLEMENTAL_TS_RUST=1: write_char_escape's `self.begin_string()` -> Formatter.begin_string",
          ("Formatter.write_char_escape", "Formatter.begin_string") in got, sorted(got))


def gate_i_real_engine() -> None:
    print("\n-- I: the REAL engine on the same trees --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    home = _TMP / "home"
    with _env(DEEPMAP_HOME=str(home), **{f: None for f in _FLAGS}):
        res = {}
        for name in ("py", "py2", "go", "ts", "rs"):
            r, rep = GX.extract_with_report(root_of(name))
            res[name] = (r, rep)
    for name, (r, rep) in res.items():
        check(f"{name}: the engine ran and produced nodes", r is not None and bool(r[0]), rep.get("cause"))
    if not all(r is not None for r, _ in res.values()):
        return
    pn, _pc, pi = res["py"][0]
    pk = {n["key"]: n for n in pn}
    targets = {pk[t]["source_file"] for s, t in pi if t in pk and pk.get(s, {}).get("name") == "run"}
    check("REAL py: run.py imports helper.py, helpers.py, pkg/__init__.py and pkg/mod.py",
          {"scripts/helper.py", "scripts/helpers.py", "scripts/pkg/__init__.py", "scripts/pkg/mod.py"} <= targets,
          sorted(targets))
    n2, c2, _ = res["py2"][0]
    b2 = by_name(n2)
    check("REAL py2: Context.invoke -> line 12, top -> line 29",
          b2["Context.invoke"][0]["start_line"] == 12 and b2["top"][0]["start_line"] == 29,
          (b2["Context.invoke"][0]["start_line"], b2["top"][0]["start_line"]))
    k2 = {n["key"]: n["name"] for n in n2}
    check("REAL py2: StateMachineMatcher.match -/-> _Routing._match",
          ("StateMachineMatcher.match", "_Routing._match") not in {(k2.get(s), t) for s, t in c2}, c2)
    n3, c3, _ = res["go"][0]
    k3 = {n["key"]: n["name"] for n in n3}
    g3 = {(k3.get(s), t) for s, t in c3}
    check("REAL go: LoadHTMLGlob -/-> must / New; -> Helper, Engine.Reset, local kept",
          not ({("Engine.LoadHTMLGlob", "must"), ("Engine.LoadHTMLGlob", "New")} & g3)
          and {("Engine.LoadHTMLGlob", "Helper"), ("Engine.LoadHTMLGlob", "Engine.Reset"),
               ("Engine.LoadHTMLGlob", "local")} <= g3, sorted(g3))
    mods = {n["source_file"] for n in n3 if n["label"] == "Module"}
    check("REAL go: models/customer.go and server/server.go have :Module nodes",
          {"models/customer.go", "server/server.go"} <= mods, sorted(mods))
    n4 = res["ts"][0][0]
    b4 = by_name(n4)
    check("REAL ts: _parse (L2), parse (L5), $ZodThing (L8) recovered",
          any(n["start_line"] == 2 for n in b4.get("_parse", []))
          and any(n["start_line"] == 5 for n in b4.get("parse", []) if n["label"] != "Module")
          and any(n["start_line"] == 8 for n in b4.get("$ZodThing", [])), {k: b4.get(k) for k in ("_parse", "parse")})
    n5 = res["rs"][0][0]
    check("REAL rs: Formatter.begin_string / write_char_escape / required are :Method nodes",
          {"Formatter.begin_string", "Formatter.write_char_escape", "Formatter.required"}
          <= {n["name"] for n in n5 if n["label"] == "Method"})
    rep3 = res["go"][1]
    check("REAL go: the report counts the dropped calls and the TS/Rust report the recovered nodes",
          int(rep3.get("calls_filtered") or 0) >= 2 and int(res["ts"][1].get("supplemental_nodes") or 0) >= 3
          and int(res["rs"][1].get("supplemental_nodes") or 0) >= 3,
          (rep3.get("calls_filtered"), res["ts"][1].get("supplemental_nodes"), res["rs"][1].get("supplemental_nodes")))


def gate_j_rebuild_code_info() -> None:
    print("\n-- J: sync._rebuild_code carries the new counters in .info --")
    try:
        spec = importlib.util.spec_from_file_location("sync_r7x", BUILD / "sync.py")
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
    except Exception as e:  # noqa: BLE001
        check("sync.py imports", False, f"{type(e).__name__}: {e}")
        return
    node = {"key": "k", "label": "Function", "name": "f", "source_file": "a.py", "start_line": 1,
            "documented": False, "body_summary": "f: function"}
    report = {"cause": None, "calls_filtered": 3, "imports_bare_resolved": 2, "supplemental_nodes": 4,
              "overloads_moved": 1, "resolved_imports": True}
    import verdict as V
    V._ENGINE_KEYS.clear()
    orig = S.graphify_extract.extract_with_report
    S.graphify_extract.extract_with_report = lambda root, **kw: (([node], [], []), dict(report))
    try:
        with _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_EXCLUDE_TESTS=None, DEEPMAP_INCLUDE_VENDOR="1"):
            ex = S._rebuild_code(_TMP)
    finally:
        S.graphify_extract.extract_with_report = orig
    info = getattr(ex, "info", {}) or {}
    for k in ("calls_filtered", "imports_bare_resolved", "supplemental_nodes", "overloads_moved"):
        check(f".info[{k!r}] carried through", info.get(k) == report[k], info)
    check("_rebuild_code hands the verdict the ENGINE's node keys for the root (T8d-B1)",
          V._ENGINE_KEYS.get(V._root_key(_TMP)) == frozenset({"k"}), V._ENGINE_KEYS)
    V._ENGINE_KEYS.clear()
    # a fallback run drops the graphify-only counters (the graphify graph is not the one kept)
    fail_report = dict(report, cause="boom")
    S.graphify_extract.extract_with_report = lambda root, **kw: (None, dict(fail_report))
    try:
        with _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_EXCLUDE_TESTS=None, DEEPMAP_INCLUDE_VENDOR="1"):
            ex2 = S._rebuild_code(_TMP / "no_such_dir")
    finally:
        S.graphify_extract.extract_with_report = orig
    info2 = getattr(ex2, "info", {}) or {}
    check("an ast fallback carries none of graphify's round-7 counters",
          not any(k in info2 for k in ("calls_filtered", "imports_bare_resolved", "supplemental_nodes",
                                       "overloads_moved")), info2)


def main() -> int:
    for f in _FLAGS:                      # a flag set in the operator's shell must not colour the run
        os.environ.pop(f, None)
    print("extraction_r7_gates — round-7 W5 graphify-path fixes + default-OFF extraction flags")
    try:
        for g in (gate_a_bare_imports, gate_b_flags_off_identity, gate_c_flags_on, gate_d_overload,
                  gate_e_false_calls, gate_f_go_module_ids, gate_g_ts_collisions, gate_h_rust_traits,
                  gate_i_real_engine, gate_j_rebuild_code_info):
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
