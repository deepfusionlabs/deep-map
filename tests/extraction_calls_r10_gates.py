#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-10 W5 gates: graphify-path CALLS keying under the default-OFF call flags (Phase M findings).
No Neo4j, no network, no spend. The REAL-engine section runs the installed graphify (skipped when absent)
under a temporary DEEPMAP_HOME, so nothing is written to ~/.deepmap.

Fixture: tests/fixtures/graphify_r10/extraction_calls_r10.json — small source trees, the REAL graph.json
graphify 0.8.39 wrote for each, and `frozen` = map_graph on the PRE-round-10 module.

  P1  DEEPMAP_PRECISE_CALLS: a MEMBER call the engine bound by name to a module-level def (click's
      `obj.shell_complete(...)` / `self._stream.isatty()`; `util.helper()` bound to the caller file's own
      helper; Rust `util::helper()` / TS `ns.helper()` likewise) is never keyed — it goes back to the bare
      name, so the push fans it out exactly as with the flag off. A bare call, or a member call through an
      import naming the target's module / package (Python), a package selector (Go), a `::` path (Rust) or
      an imported namespace (JS/TS) to ANOTHER file, keeps the key; a caller that calls the name both ways
      keeps the key AND gets the name; `os.isatty()` / a parameter shadowing an import never names a project
      def; comment lines are not read; a :Method target is untouched. Same Python rule as the push-time
      sync._unkey_receiver_calls (W6).
  P5  DEEPMAP_GRAPHIFY_CALLS_PLUS: each addition resolved to exactly ONE node is a KEYED call to that node
      with or without DEEPMAP_PRECISE_CALLS (it used to be the bare name, fanned out at push: flask 44
      calls -> 84 edges, 57 AMBIGUOUS); a call the engine already made to that node is not added again;
      keyed_calls_enabled() tells the push when a callee can be a key.
  P6  DEEPMAP_GRAPHIFY_CALLS_PLUS adds calls of NON-test callers only (sync._is_test_path) unless
      DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1.
  Z   every flag off: map_graph output is byte-identical to the pre-round-10 module; _call_sites unchanged.

Sections: A P1 · B P5 · C P6 · D push (sync._resolve_calls) · E real engine · Z identity.
"""
from __future__ import annotations

import contextlib
import hashlib
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

FIXTURE = REPO / "tests" / "fixtures" / "graphify_r10" / "extraction_calls_r10.json"
R7_FIXTURE = REPO / "tests" / "fixtures" / "graphify_r7" / "extraction_r7.json"
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
_FLAGS = ("DEEPMAP_MODULE_DOCSTRINGS", "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_GRAPHIFY_CALLS_PLUS",
          "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_PRECISE_CALLS",
          "DEEPMAP_RESOLVED_IMPORTS", "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_CLEAN_OWNER_NAMES",
          "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR", "DEEPMAP_EXTRACTOR", "DEEPMAP_RESCUE_SKIPPED_DIRS",
          "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_FALSE_CALL_FILTER", "DEEPMAP_LOCAL_DEF_CALLS",
          "DEEPMAP_DROP_APP_TO_TEST_CALLS")
_KEEP_N = ("id", "label", "source_file", "source_location", "file_type")
_KEEP_L = ("relation", "source", "target", "source_location")

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


DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
_TMP = Path(tempfile.mkdtemp(prefix="dm_r10x_"))
_ROOTS: dict = {}


def root_of(name: str) -> Path:
    if name not in _ROOTS:
        root = _TMP / f"src_{name}"
        for rel, text in DATA["trees"][name].items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        _ROOTS[name] = root
    return _ROOTS[name]


def graph_of(name: str) -> dict:
    g = DATA["graphs"][name]
    return {"nodes": [dict(zip(_KEEP_N, n)) for n in g["nodes"]],
            "links": [dict(zip(_KEEP_L, l)) for l in g["links"]]}


def mapped(name: str, *, root=True, stats=None, **env):
    base = {f: None for f in _FLAGS}
    base.update(env)
    with _env(**base):
        return GX.map_graph(graph_of(name), root=root_of(name) if root else None, stats=stats)


def nodes_sha(nodes) -> str:
    return hashlib.sha1(json.dumps(nodes, sort_keys=True).encode()).hexdigest()


def frozen(mode: str, name: str):
    f = DATA["frozen"][mode][name]
    return f["nodes_sha"], [tuple(c) for c in f["calls"]], [tuple(i) for i in f["imports"]]


# ================================================================================================
# A — P1: precise CALLS never key a member call to a module-level def
# ================================================================================================
P1_EXPECT = {
    "p1py": [
        ("pkg_shell_shellcomplete_get_completions", "shell_complete"),     # obj.shell_complete(ctx, 1)
        ("pkg_shell_shellcomplete_run", "pkg_shell_shell_complete"),       # shell_complete(self, None)
        ("pkg_shell_shellcomplete_mixed", "shell_complete"),               # obj.x(...) AND a bare x(...):
        ("pkg_shell_shellcomplete_mixed", "pkg_shell_shell_complete"),     #   the name first, then the key
        ("pkg_shell_shellcomplete_split", "shell_complete"),               # (obj.\n shell_complete(1, 2))
        ("pkg_shell_shellcomplete_via_module", "helper"),                  # util.helper() -> not OWN helper
        ("pkg_shell_shellcomplete_via_dotted", "helper"),                  # pkg.util.helper()
        ("pkg_shell_shellcomplete_via_super", "helper"),                   # super().helper()
        ("pkg_shell_shellcomplete_probe", "pkg_shell_wrapper_isatty"),     # a :Method target stays keyed
    ],
    "p1rs": [
        ("src_lib_s_run", "helper"),                                       # x.helper() — a method call
        ("src_lib_s_path", "helper"),                                      # util::helper() bound to OWN fn
        ("src_lib_s_bare", "src_lib_helper"),                              # helper()
    ],
    "p1go": [
        ("app_h_path", "util_util_helper"),                                # util.Helper() — package selector
        ("app_h_run", "Helper"),                                           # h.c.Helper() — a value chain
        ("app_h_val", "Helper"),                                           # x.Helper() — a value
    ],
    "p1ts": [
        ("src_a_a_run", "helper"),                                         # this.helper()
        ("src_a_a_vians", "helper"),                                       # ns.helper() bound to OWN helper
        ("src_a_a_bare", "src_a_helper"),                                  # helper()
        ("src_c_viaexports", "src_c_helper"),                              # exports.helper() (CommonJS self)
        ("src_c_viaobj", "helper"),                                        # o.helper()
    ],
    "p1tsd": [
        ("src_d_chain", "helper"),                                         # `o.` / `helper()` continuation
        ("src_d_commented", "src_d_helper"),                               # `// o.helper()` comments not read
    ],
    "p1py2": [
        ("pkg_use_via_mod", "pkg_util_helper"),                            # util.helper(), util = pkg.util
        ("pkg_use_via_pkg", "pkg_util_helper"),                            # pkg.helper() — a package re-export
        ("pkg_use_via_os", "isatty"),                                      # os.isatty() — os names no project file
        ("pkg_use_shadowed", "helper"),                                    # def shadowed(util): util.helper()
        ("pkg_use_both", "helper"),                                        # o.helper() AND util.helper():
        ("pkg_use_both", "pkg_util_helper"),                               #   the name first, then the key
    ],
}
P1_UNKEYED = {"p1py": 5, "p1rs": 2, "p1go": 2, "p1ts": 3, "p1tsd": 1, "p1py2": 2}
P1_BOTH = {"p1py": 1, "p1rs": 0, "p1go": 0, "p1ts": 0, "p1tsd": 0, "p1py2": 1}


def gate_a_precise_receiver() -> None:
    print("\n-- A (P1): DEEPMAP_PRECISE_CALLS keys no member call into a module-level def --")
    for name, want in P1_EXPECT.items():
        st: dict = {}
        _n, calls, _i = mapped(name, stats=st, DEEPMAP_PRECISE_CALLS="1")
        check(f"{name}: precise calls == the P1 expectation", calls == want, calls)
        check(f"{name}: stats precise_unkeyed == {P1_UNKEYED[name]}", st.get("precise_unkeyed") == P1_UNKEYED[name],
              st.get("precise_unkeyed"))
        check(f"{name}: stats precise_receiver_names == {P1_BOTH[name]}",
              st.get("precise_receiver_names") == P1_BOTH[name], st.get("precise_receiver_names"))
        _sha, pre, _pi = frozen("precise", name)
        # the ONLY changes vs the pre-round-10 precise output: an un-keyed pair lost its key for its name, a
        # both-ways caller gained its name; every other tuple is the same, in the same order
        node_keys = {n[0] for n in DATA["graphs"][name]["nodes"]}
        lost = [c for c in pre if c not in calls]
        gained = [c for c in calls if c not in pre]
        check(f"{name}: vs pre-round-10 precise, only member-call keys changed (a key -> its name; a name added)",
              len(lost) == P1_UNKEYED[name] and len(gained) == P1_UNKEYED[name] + P1_BOTH[name]
              and all(c[1] in node_keys for c in lost) and not any(c[1] in node_keys for c in gained)
              and [c for c in pre if c not in lost] == [c for c in calls if c not in gained], (lost, gained))

    # the recorded line and the audit: click's two cases, rebuilt as a hand graph over the same source shape
    py = dict(mapped("p1py", DEEPMAP_PRECISE_CALLS="1")[1])
    check("click shell_completion.py:287 shape — `obj.shell_complete(...)` is NOT an EXTRACTED key",
          py.get("pkg_shell_shellcomplete_get_completions") == "shell_complete", py)
    _n, calls, _i = mapped("p1py", DEEPMAP_PRECISE_CALLS="1")
    check("a Method target reached through a member call keeps its key (probe -> Wrapper.isatty)",
          ("pkg_shell_shellcomplete_probe", "pkg_shell_wrapper_isatty") in calls, calls)

    # pure mode (no source to read) keeps every engine key
    _n, pcalls, _i = mapped("p1py", root=False, DEEPMAP_PRECISE_CALLS="1")
    _sha, pre, _pi = frozen("precise", "p1py")
    check("pure mode (root=None): no source, every engine key kept (the pre-round-10 precise output)",
          pcalls == pre, pcalls)

    # flag off: P1 never runs (names everywhere, as always)
    for name in P1_EXPECT:
        sha, want_calls, want_imps = frozen("default", name)
        n, calls, imps = mapped(name)
        check(f"{name}: DEEPMAP_PRECISE_CALLS unset -> byte-identical to the pre-round-10 module",
              nodes_sha(n) == sha and calls == want_calls and imps == want_imps, calls)

    # order: the engine links via_mod -> util.helper (kept) BEFORE via_mod -> a same-file helper (un-keyed); the
    # name must still come first, or the push (last tuple wins) would demote util.helper to the fan-out grade
    g = graph_of("p1py2")
    g["nodes"].append({"id": "pkg_use_helper", "label": "helper()", "source_file": "pkg/use.py",
                       "source_location": "L1", "file_type": "code"})
    g["links"].append({"relation": "contains", "source": "pkg_use", "target": "pkg_use_helper", "source_location": "L1"})
    g["links"].append({"relation": "calls", "source": "pkg_use_via_mod", "target": "pkg_use_helper",
                       "source_location": "L7"})
    base = {f: None for f in _FLAGS}
    base.update(DEEPMAP_PRECISE_CALLS="1")
    with _env(**base):
        on, oc, _oi = GX.map_graph(json.loads(json.dumps(g)), root=root_of("p1py2"))
    vm = [c for c in oc if c[0] == "pkg_use_via_mod"]
    check("a caller's P1 name tuple precedes its keys (engine order: key first, then the un-keyed link)",
          vm == [("pkg_use_via_mod", "helper"), ("pkg_use_via_mod", "pkg_util_helper")], vm)
    import sync as S
    with _env(**base):
        oe = S._resolve_calls(on, oc)
    check("... so at the push util.helper keeps its key's grade (INFERRED), the same-file helper gets the fan-out",
          oe.get(("pkg_use_via_mod", "pkg_util_helper")) == "INFERRED"
          and oe.get(("pkg_use_via_mod", "pkg_use_helper")) == "AMBIGUOUS", oe)
    check("_names_before_keys keeps every other tuple's order",
          GX._names_before_keys([("a", "k1"), ("b", "k2"), ("a", "n"), ("c", "n"), ("b", "n2")], {("a", "n"), ("b", "n2")})
          == [("a", "n"), ("a", "k1"), ("b", "n2"), ("b", "k2"), ("c", "n")])

    # the verdict function directly: a Function target only, never raises
    unk = GX._receiver_unkeyer(GX._Source(root_of("p1py")), {n["id"]: n for n in graph_of("p1py")["nodes"]},
                               {"pkg_shell_shellcomplete_get_completions": "Method", "pkg_shell_wrapper_isatty": "Method",
                                "pkg_shell_shell_complete": "Function"})
    check("_receiver_unkeyer: a :Method target is never judged (None = keep)",
          unk("pkg_shell_shellcomplete_probe", "pkg_shell_wrapper_isatty") is None)
    check("_receiver_unkeyer: unknown ids keep the key (never raises)", unk("nope", "pkg_shell_shell_complete") is None)
    check("_receiver_unkeyer: obj.shell_complete(...) only -> 'name'",
          unk("pkg_shell_shellcomplete_get_completions", "pkg_shell_shell_complete") == "name")
    check("_py_module_names: stem + every package dir; none for __init__",
          GX._py_module_names("src/pkg/util.py") == {"src", "pkg", "util"}
          and GX._py_module_names("pkg/__init__.py") == {"pkg"})
    import ast as _ast
    tg = GX._py_import_targets(_ast.parse("import a.b\nimport c.d as e\nfrom . import x\nfrom p import q\n"
                                           "from .r import s as t2\nimport z\nz = 1\nimport y\nfrom w import y\n"))
    check("_py_import_targets: sync._import_aliases' rule (a two-way binding -> None)",
          tg == {"a": "a", "e": "c.d", "x": ".x", "q": "p.q", "t2": ".r.s", "z": "z", "y": None}, tg)
    import sync as S0
    for name in ("p1py", "p1py2", "p5py"):
        for rel, text in DATA["trees"][name].items():
            if not rel.endswith(".py") or not text:
                continue
            tree = _ast.parse(text)
            idx = GX._py_stmt_index(tree)
            check(f"{name}/{rel}: the statement index's import table == _py_import_targets == sync._import_aliases",
                  idx["imports"] == GX._py_import_targets(tree) == S0._import_aliases(tree), idx["imports"])
            defs = [d for d in _ast.walk(tree) if isinstance(d, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))]
            check(f"{name}/{rel}: the statement index finds every def/class (nested ones too)",
                  sorted((d.lineno, d.name) for d in defs) == sorted(
                      (ln, nm) for (ln, nm), ds in idx["defs_at"].items() for d in ds if d.lineno == ln))
            check(f"{name}/{rel}: _py_scope_calls' locals == _py_locally_bound for every scope",
                  all(GX._py_scope_calls(d)[1] == GX._py_locally_bound(d) for d in defs + [tree]))

    # _chain_of units (the member chain a qualified site ends with)
    co = GX._chain_of
    check("_chain_of('return self._stream.') == ('.', 'self', 2)", co("return self._stream.") == (".", "self", 2),
          co("return self._stream."))
    check("_chain_of('x = pkg.util.') == ('.', 'pkg', 2)", co("x = pkg.util.") == (".", "pkg", 2))
    check("_chain_of('util::') == ('::', 'util', 1)", co("util::") == ("::", "util", 1))
    check("_chain_of('return f().') -> no head", co("return f().") == (".", None, 0), co("return f()."))
    check("_chain_of('super().') -> no head", co("super().")[1] is None)
    check("_chain_of('a?.b?.') == ('?.', 'a', 2)", co("a?.b?.") == ("?.", "a", 2), co("a?.b?."))
    check("_chain_of reads a bounded window of a very long line", co("x" * 5000 + " = obj.")[1] == "obj")


# ================================================================================================
# B — P5: CALLS_PLUS additions are keyed in both modes
# ================================================================================================
def gate_b_calls_plus_keyed() -> None:
    print("\n-- B (P5): DEEPMAP_GRAPHIFY_CALLS_PLUS adds KEYED calls with or without precise CALLS --")
    kce = getattr(GX, "keyed_calls_enabled", None)
    check("graphify_extract.keyed_calls_enabled exists", callable(kce))
    if callable(kce):
        for p, c, want in ((None, None, False), ("1", None, True), (None, "1", True), ("1", "1", True),
                           ("0", "0", False), ("yes", None, True), (None, "on", True)):
            with _env(DEEPMAP_PRECISE_CALLS=p, DEEPMAP_GRAPHIFY_CALLS_PLUS=c):
                check(f"keyed_calls_enabled() PRECISE={p} PLUS={c} -> {want}", kce() is want)

    want_plus = [("app_main_run", "app_json_mod_dumps"), ("app_main_twice", "app_json_mod_dumps")]
    st: dict = {}
    nodes, calls, _i = mapped("p5py", stats=st, DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
    keys = {n["key"] for n in nodes}
    _sha, old, _oi = frozen("plus_names", "p5py")
    check("pre-round-10 (RED reference): CALLS_PLUS without precise made the bare NAME `dumps`",
          ("app_main_run", "dumps") in old, old)
    check("precise OFF: `j.dumps(1)` -> a KEYED call to exactly app/json_mod.py:dumps",
          ("app_main_run", "app_json_mod_dumps") in calls, calls)
    check("precise OFF: no CALLS_PLUS addition is a bare name (every addition names a node key)",
          all(t in keys for s, t in calls if (s, t) in set(want_plus) or t == "dumps") and
          not any(t == "dumps" for _s, t in calls), calls)
    check("precise OFF: the engine's own call stays a NAME (only the additions are keyed)",
          ("app_main_twice", "helper_local") in calls and ("app_main_twice", "app_main_helper_local") not in calls,
          calls)
    check("stats calls_plus == 2 (run, twice)", st.get("calls_plus") == 2, st.get("calls_plus"))

    st2: dict = {}
    _n2, pcalls, _i2 = mapped("p5py", stats=st2, DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS="1")
    check("precise ON: the same keyed additions + the engine call keyed",
          pcalls == [("app_main_twice", "app_main_helper_local")] + want_plus, pcalls)

    # an addition the engine already made (to the same node) is not added a second time — in both modes
    g = graph_of("p5py")
    g["links"].append({"relation": "calls", "source": "app_main_run", "target": "app_json_mod_dumps",
                       "source_location": "L5"})
    for prec in (None, "1"):
        base = {f: None for f in _FLAGS}
        base.update(DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS=prec)
        with _env(**base):
            _n3, c3, _i3 = GX.map_graph(json.loads(json.dumps(g)), root=root_of("p5py"))
        run_calls = [t for s, t in c3 if s == "app_main_run"]
        want = ["app_json_mod_dumps"] if prec else ["dumps"]
        check(f"precise={prec}: the engine's own run->dumps is the only run call (no duplicate key)",
              run_calls == want, run_calls)

    # the round-7 fixture: every addition keyed without precise (module caller, alias, dotted, closure)
    r7 = json.loads(R7_FIXTURE.read_text(encoding="utf-8"))
    r7root = _TMP / "r7_py"
    for rel, text in r7["py"]["files"].items():
        p = r7root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    base = {f: None for f in _FLAGS}
    base.update(DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
    with _env(**base):
        _n4, c4, _i4 = GX.map_graph(json.loads(json.dumps(r7["py"]["graph"])), root=r7root)
    for pair in (("scripts_run_main", "scripts_helpers_feature_enabled"), ("scripts_run_main", "pkg_mod_g"),
                 ("scripts_run_main", "scripts_run_local_t"), ("scripts_run", "scripts_helpers_feature_enabled")):
        check(f"r7 py fixture, precise OFF: addition {pair} is keyed", pair in c4, c4)
    check("r7 py fixture: no addition repeats graphify's own main -> helper.f edge",
          ("scripts_run_main", "scripts_helper_f") not in c4 and c4.count(("scripts_run_main", "f")) == 1, c4)

    # _keep_rescued_only drops an addition keyed into a dropped node even without precise (keyed_calls_enabled)
    if GX._engine_noise_dir() is not None:
        nodes5 = [{"key": "a_run", "source_file": "a.py"}, {"key": "build_x_f", "source_file": "build/x.py"}]
        res = (nodes5, [("a_run", GX.call_key("build_x_f")), ("a_run", "f")], [])   # R11/R1: a MARKED key
        with _env(DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS=None):
            (kn, kc, _ki), nd = GX._keep_rescued_only(res, _TMP, {"build": ["build/y.py"]})
        check("_keep_rescued_only: a CALLS_PLUS key into a dropped node goes with it (precise OFF)",
              nd == 1 and kc == [("a_run", "f")], (nd, kc))
        with _env(DEEPMAP_GRAPHIFY_CALLS_PLUS=None, DEEPMAP_PRECISE_CALLS=None):
            (_kn, kc2, _ki2), _nd = GX._keep_rescued_only(res, _TMP, {"build": ["build/y.py"]})
        check("_keep_rescued_only: both flags off -> a name tuple is never read as a key",
              kc2 == [("a_run", "build_x_f"), ("a_run", "f")], kc2)
    else:
        print("  [SKIP] _keep_rescued_only checks (graphify's skip rule unavailable)")

    doc = GX.graphify_calls_plus_enabled.__doc__ or ""
    check("the CALLS_PLUS docstring says keyed in both modes and non-test callers only",
          "keyed" in doc and "PRECISE_CALLS" in doc and "NON-test" in doc, doc)


# ================================================================================================
# C — P6: CALLS_PLUS only for non-test callers by default
# ================================================================================================
def gate_c_calls_plus_tests() -> None:
    print("\n-- C (P6): CALLS_PLUS skips test callers unless DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1 --")
    for prec in (None, "1"):
        st: dict = {}
        _n, calls, _i = mapped("p5py", stats=st, DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS=prec)
        check(f"precise={prec}: the test caller's `json_mod.dumps(1)` is NOT added",
              not any(s == "tests_test_main_test_run" for s, _t in calls), calls)
        check(f"precise={prec}: stats calls_plus_test_skipped == 1", st.get("calls_plus_test_skipped") == 1,
              st.get("calls_plus_test_skipped"))
        st2: dict = {}
        _n2, calls2, _i2 = mapped("p5py", stats=st2, DEEPMAP_GRAPHIFY_CALLS_PLUS="1", DEEPMAP_PRECISE_CALLS=prec,
                                  DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS="1")
        check(f"precise={prec}: DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1 adds it, keyed",
              ("tests_test_main_test_run", "app_json_mod_dumps") in calls2 and st2.get("calls_plus_test_skipped") == 0,
              calls2)
    _n3, calls3, _i3 = mapped("p5py", DEEPMAP_GRAPHIFY_CALLS_PLUS=None, DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS="1")
    _sha, want, _w = frozen("default", "p5py")
    check("DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS alone changes nothing", calls3 == want, calls3)

    import sync as S
    pred = GX._test_path_predicate()
    check("the predicate IS sync._is_test_path (the one test-code rule)", pred is S._is_test_path, pred)
    corpus = ["tests/test_x.py", "src/contest.py", "lib/latest.js", "pkg/a_test.go", "app/Latest.java",
              "app/UserTest.java", "spec/x.rb", "x.spec.ts", "src/testing/util.py", "test_a.py", "a/b/c.py",
              "__tests__/a.js", "e2e/run.ts", "src/tests_helper.py",
              # the .NET test-project directory rule (sync._DOTNET_TEST_DIR_SUFFIXES): a component ENDING with
              # .Tests / .UnitTests / ... is test code; a test-support library and a look-alike name are not
              "src/Shop.Tests/X.cs", "Shop.UnitTests/Fakes/FakeRepo.cs", "src/Shop.Testing.Abstractions/X.cs",
              "src/Contest/X.cs", "api.test/router.ts", "SRC/SHOP.INTEGRATIONTESTS/X.CS", "src\\Shop.Specs\\X.cs",
              "src/Shop.Tests.cs", "src/Shop.TestsHelpers/X.cs"]
    check("the corpus holds .NET test projects the rule must call test code (premise)",
          all(S._is_test_path(p) for p in ("src/Shop.Tests/X.cs", "Shop.UnitTests/Fakes/FakeRepo.cs",
                                           "api.test/router.ts"))
          and not any(S._is_test_path(p) for p in ("src/Shop.Testing.Abstractions/X.cs", "src/Contest/X.cs")))
    saved = sys.modules.get("sync")
    sys.modules["sync"] = None          # `import sync` raises ImportError -> the fallback mirror
    try:
        fb = GX._test_path_predicate()
    finally:
        if saved is not None:
            sys.modules["sync"] = saved
        else:
            sys.modules.pop("sync", None)
    check("without sync, the fallback mirror agrees with sync._is_test_path on the corpus",
          fb is not S._is_test_path and all(fb(p) == S._is_test_path(p) for p in corpus),
          [(p, fb(p), S._is_test_path(p)) for p in corpus if fb(p) != S._is_test_path(p)])


# ================================================================================================
# D — the push binds a keyed addition to exactly its node (sync._resolve_calls)
# ================================================================================================
def gate_d_push() -> None:
    print("\n-- D (P5): the push binds each keyed addition to exactly one node --")
    import sync as S
    nodes, calls, _i = mapped("p5py", DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
    _n0, names, _i0 = (lambda f: (None, f[1], None))(frozen("plus_names", "p5py"))
    base = {f: None for f in _FLAGS}
    base.update(DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
    with _env(**base):
        old_edges = S._resolve_calls(nodes, [c for c in names])
    fan = sorted(k for (s, k) in old_edges if s == "app_main_run")
    check("pre-round-10 reference: the name `dumps` fanned out to 2 nodes (json dumps + JSONProvider.dumps)",
          fan == ["app_json_mod_dumps", "app_provider_jsonprovider_dumps"], fan)

    # W6's side: the push treats a key-form callee as a key whenever keyed_calls_enabled() (P5 contract)
    orig = S._precise_calls_enabled
    S._precise_calls_enabled = GX.keyed_calls_enabled
    try:
        with _env(**base):
            edges = S._resolve_calls(nodes, calls)
    finally:
        S._precise_calls_enabled = orig
    run_edges = {k: v for (s, k), v in edges.items() if s == "app_main_run"}
    check("with the push keyed on keyed_calls_enabled(): run -> ONLY app/json_mod.py:dumps (no fan-out)",
          list(run_edges) == ["app_json_mod_dumps"], run_edges)
    check("... graded INFERRED (a cross-file binding, F3b), never AMBIGUOUS",
          run_edges.get("app_json_mod_dumps") == "INFERRED", run_edges)
    check("... and the engine's own name call is resolved exactly as before (twice -> helper_local)",
          edges.get(("app_main_twice", "app_main_helper_local")) == old_edges.get(("app_main_twice",
                                                                                     "app_main_helper_local")),
          edges)

    # the live push: requires sync to honour keys under keyed_calls_enabled (W6 integration of P5)
    with _env(**base):
        live = S._resolve_calls(nodes, calls)
    live_run = sorted(k for (s, k) in live if s == "app_main_run")
    check("sync's push binds a CALLS_PLUS key without DEEPMAP_PRECISE_CALLS (W6: _precise_calls_enabled -> "
          "graphify_extract.keyed_calls_enabled)", live_run == ["app_json_mod_dumps"], live_run)

    # the two P1 layers agree: W6's push-time pass (sync._unkey_receiver_calls) on this module's output leaves
    # every edge and grade as they are
    w6 = getattr(S, "_unkey_receiver_calls", None)
    if callable(w6):
        for name in P1_EXPECT:
            n1, c1, _i1 = mapped(name, DEEPMAP_PRECISE_CALLS="1")
            with _env(DEEPMAP_PRECISE_CALLS="1"):
                c2, _n = w6(root_of(name), n1, c1)
                e1 = S._resolve_calls(n1, c1)
                e2 = S._resolve_calls(n1, c2)
            check(f"{name}: sync._unkey_receiver_calls on the P1 output changes no edge or grade", e1 == e2,
                  {k: (e1.get(k), e2.get(k)) for k in set(e1) | set(e2) if e1.get(k) != e2.get(k)})
    else:
        print("  [SKIP] sync._unkey_receiver_calls is not present")

    # P1 end to end: an un-keyed member call fans out at the push exactly as with the flag off
    pn, pc, _pi = mapped("p1py", DEEPMAP_PRECISE_CALLS="1")
    dn, dc, _di = mapped("p1py")
    with _env(DEEPMAP_PRECISE_CALLS="1"):
        pe = S._resolve_calls(pn, pc)
    with _env(DEEPMAP_PRECISE_CALLS=None):
        de = S._resolve_calls(dn, dc)
    for caller in ("pkg_shell_shellcomplete_get_completions", "pkg_shell_shellcomplete_via_module"):
        p_t = {k: v for (s, k), v in pe.items() if s == caller}
        d_t = {k: v for (s, k), v in de.items() if s == caller}
        check(f"P1 push: {caller} gets the flag-off fan-out (targets and grades)", p_t == d_t and len(p_t) >= 1,
              (p_t, d_t))
    check("P1 push: a bare call keeps its single firm edge (run -> shell_complete EXTRACTED)",
          pe.get(("pkg_shell_shellcomplete_run", "pkg_shell_shell_complete")) == "EXTRACTED", pe)


# ================================================================================================
# E — the real engine
# ================================================================================================
def gate_e_real_engine() -> None:
    print("\n-- E: the REAL graphify engine on the p1py / p5py trees --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    home = _TMP / "home"
    base = {f: None for f in _FLAGS}
    base.update(DEEPMAP_HOME=str(home), DEEPMAP_PRECISE_CALLS="1", DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
    root = _TMP / "real_p1py"
    shutil.copytree(root_of("p1py"), root)
    with _env(**base):
        res, rep = GX.extract_with_report(root)
    calls = res[1] if res else []
    check("real engine p1py: extraction ran", res is not None and not rep.get("cause"), rep.get("cause"))
    check("real engine: `obj.shell_complete(...)` -> the bare name (not the module-level key)",
          ("pkg_shell_shellcomplete_get_completions", "shell_complete") in calls
          and ("pkg_shell_shellcomplete_get_completions", "pkg_shell_shell_complete") not in calls, calls)
    check("real engine: `util.helper()` is not keyed to the caller file's own helper",
          ("pkg_shell_shellcomplete_via_module", "pkg_shell_helper") not in calls, calls)
    check("real engine: a bare call keeps its key", ("pkg_shell_shellcomplete_run", "pkg_shell_shell_complete") in calls,
          calls)
    root5 = _TMP / "real_p5py"
    shutil.copytree(root_of("p5py"), root5)
    for prec in (None, "1"):
        with _env(**dict(base, DEEPMAP_PRECISE_CALLS=prec)):
            res5, rep5 = GX.extract_with_report(root5)
        c5 = res5[1] if res5 else []
        check(f"real engine p5py precise={prec}: CALLS_PLUS additions keyed, no test caller",
              ("app_main_run", "app_json_mod_dumps") in c5 and not any(s.startswith("tests_") for s, _t in c5), c5)


# ================================================================================================
# Z — identity: flags off / the _call_sites refactor
# ================================================================================================
def gate_z_identity() -> None:
    print("\n-- Z: every flag off -> byte-identical; _call_sites unchanged by the refactor --")
    for name in DATA["frozen"]["default"]:
        sha, want_calls, want_imps = frozen("default", name)
        n, calls, imps = mapped(name)
        check(f"{name}: flags off == the pre-round-10 module (nodes sha, calls, imports)",
              nodes_sha(n) == sha and calls == want_calls and imps == want_imps)
    bad = []
    for text, prev, h, sc, ia, hi in DATA["call_sites"]:
        got = ([list(x) for x in GX._call_sites(text, "helper", prev=prev)],
               [list(x) for x in GX._call_sites(text, "shell_complete", prev=prev)],
               [list(x) for x in GX._call_sites(text, "isatty", prev=prev)],
               [list(x) for x in GX._call_sites(text, "helper", ignore_case=True, prev=prev)])
        if got != (h, sc, ia, hi):
            bad.append((text, prev, got))
    check(f"_call_sites == the pre-round-10 output on {len(DATA['call_sites'])} (line, prev) pairs", not bad, bad[:3])
    det = GX._call_site_details("return obj.helper(1) + helper(2)", "helper")
    check("_call_site_details: kind/qualifier/operator/head/segments",
          det == [("qualified", "obj", ".", "obj", 1), ("bare", None, None, None, 0)], det)


def main() -> int:
    for f in _FLAGS:
        os.environ.pop(f, None)
    print("extraction_calls_r10_gates — round-10 W5: precise CALLS receiver guard (P1), keyed CALLS_PLUS (P5), "
          "non-test CALLS_PLUS (P6)")
    try:
        for g in (gate_a_precise_receiver, gate_b_calls_plus_keyed, gate_c_calls_plus_tests, gate_d_push,
                  gate_e_real_engine, gate_z_identity):
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
