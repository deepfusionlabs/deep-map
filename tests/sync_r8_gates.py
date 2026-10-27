#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the round-8 sync internals (W6: build/sync.py outside the extractor region,
build/indexes.py).

  A  J3 — a bare call never binds a METHOD at PUSH time (graphify path). map_graph drops a bare call the
     ENGINE bound to another file's method, but the push-time name fan-out re-created it: a graphify
     caller reaches the push as (caller, 'check') and _resolve_calls bound EVERY node whose last dotted
     segment is `check`. Live on this repo: AuthBackoff.check collected 1,173 AMBIGUOUS edges from bare
     `check(...)` calls in tests/ and topped the graph arm for every query. In Python / JS / TS / Go / Rust
     a call without a receiver can never reach a method, so when EVERY call site of the name in the
     caller's body is bare, a Method target of another file (or of another class in the same file) is
     dropped. One `obj.check(` anywhere in the body keeps it (the map_graph rule, W5's R8 note).
  B  J3 — the same refinement on the ast path (a same-file method of ANOTHER class), without touching
     the round-7 receiver rules (a def nested in a method stays bindable).
  C  J3 — Go: a non-_test caller never binds a _test.go symbol at push time (name fan-out AND precise
     CALLS); the confidence is counted over the targets that remain.
  D  J3 — DEEPMAP_FALSE_CALL_FILTER=0 is byte-identical to the round-7 resolver; callers without the new
     metadata are unchanged.
  E  J3 × T3#6 — nested-def shadowing is graded on the targets that REMAIN after the drop.
  F  J3 wiring — sync() attaches the call-site data before the push.
  G  J3 on the REAL engine (graphify, when installed): the round-8 probe tree end to end.
  H  J4 — sync dials 127.0.0.1: no generated bolt://localhost literal; the instance write lock is ONE
     file per endpoint whatever its loopback spelling (and the file older builds used).
  I  J6 — every git probe in sync passes `-c core.longpaths=true` on Windows; on a real repository with a
     long root, a committed file past MAX_PATH is not reported as an uncommitted change.

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
import subprocess
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
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


def _load(name="sync"):
    spec = importlib.util.spec_from_file_location(f"dm_r8_{name}", BUILD / f"{name}.py")
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


def _n(key, name, sf, line=1, label="Function"):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name, "documented": False}


def _attach(S, root, nodes, calls):
    """Run the push-time source passes exactly as _sync_pass does (bare-call data, then scope data)."""
    fn = getattr(S, "_attach_bare_calls", None)
    with contextlib.redirect_stderr(io.StringIO()):
        if callable(fn):
            fn(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)


def _r7_resolve_calls(S, nodes, calls):
    """The round-7 _resolve_calls default path (precise OFF, F8b OFF) for callers WITHOUT receiver
    metadata — the byte-identity oracle for DEEPMAP_FALSE_CALL_FILTER=0 and for untouched callers."""
    import edge_resolve
    name_to_keys = edge_resolve.build_name_index(nodes)
    key_to_file = {n["key"]: str(n.get("source_file", "")) for n in nodes}
    local_of = {n["key"]: set(n["local_defs"]) for n in nodes if n.get("local_defs")}
    edges = {}
    for caller_key, callee in calls:
        if caller_key not in key_to_file:
            continue
        targets = [ck for ck in name_to_keys.get(callee, []) if ck != caller_key]
        econf = "AMBIGUOUS" if len(targets) > 1 else "INFERRED"
        graded = [(ck, econf) for ck in targets]
        local = local_of.get(caller_key)
        if local and callee in local:
            graded = [(ck, c if key_to_file.get(ck) == key_to_file[caller_key] else "AMBIGUOUS") for ck, c in graded]
        for ck, conf in graded:
            edges[(caller_key, ck)] = conf
    return edges


# =====================================================================================================
# A — graphify path: a bare call never binds another file's (or another class's) method at push time
# =====================================================================================================
_TREE_A = {
    "pkg/backoff.py": ("class AuthBackoff:\n"                      # 1
                       "    def check(self, ip):\n"                # 2
                       "        return ip\n"                       # 3
                       "\n"                                        # 4
                       "    def other(self):\n"                    # 5
                       "        return check_all()\n"              # 6  (bare, same class -> kept)
                       "\n"
                       "\n"
                       "_AUTH = AuthBackoff()\n"                   # 9
                       "\n"
                       "\n"
                       "def handle(ip):\n"                         # 12
                       "    return _AUTH.check(ip)\n"),            # 13
    "tests/test_a.py": ("def check(label, cond):\n"                # 1
                        "    print(label, cond)\n"                 # 2
                        "\n"
                        "\n"
                        "def gate_one():\n"                        # 5
                        "    check('a', True)\n"                   # 6
                        "    check('b', False)\n"                  # 7
                        "\n"
                        "\n"
                        "def gate_mixed(backoff):\n"               # 10
                        "    check('c', True)\n"                   # 11
                        "    backoff.check('1.2.3.4')\n"),         # 12
    "tests/test_b.py": ("class Fake:\n"                            # 1
                        "    def check(self, ip):\n"               # 2
                        "        return ip\n"                      # 3
                        "\n"
                        "\n"
                        "def check(label, cond):\n"                # 6
                        "    print(label, cond)\n"                 # 7
                        "\n"
                        "\n"
                        "def gate_two():\n"                        # 10
                        "    check('x', True)\n"                   # 11
                        "\n"
                        "\n"
                        "class Holder:\n"                          # 14
                        "    def _mk():\n"                         # 15
                        "        return 1\n"                       # 16
                        "    X = _mk()\n"                          # 17  class-body bare call -> own method
                        "\n"
                        "\n"
                        "check('module', True)\n"),                # 20  module-level bare call
    "tests/test_c.py": ("def run_it(x):\n"                         # 1  no call site of check at all
                        "    return x\n"),
    "web/api.ts": ("export class Api {\n"                          # 1
                   "  check(x: number): number {\n"                # 2
                   "    return x;\n"
                   "  }\n"
                   "}\n"),
    "web/run.ts": ("import { Api } from \"./api\";\n"              # 1
                   "\n"
                   "function check(x: number): number {\n"         # 3
                   "  return x + 1;\n"
                   "}\n"
                   "\n"
                   "export function run(): number {\n"             # 7
                   "  return check(1);\n"                          # 8
                   "}\n"
                   "\n"
                   "export function run2(api: Api): number {\n"    # 11
                   "  return api.check(2);\n"                      # 12
                   "}\n"),
    "java/App.java": ("class App {\n"                              # 1  implicit `this`: never ruled out
                      "  void m() {\n"                             # 2
                      "    check();\n"                             # 3
                      "  }\n"
                      "}\n"),
}


def _nodes_a():
    return [
        _n("pkg_backoff", "backoff", "pkg/backoff.py", 1, "Module"),
        _n("pkg_backoff_authbackoff", "AuthBackoff", "pkg/backoff.py", 1, "Class"),
        _n("pkg_backoff_authbackoff_check", "AuthBackoff.check", "pkg/backoff.py", 2, "Method"),
        _n("pkg_backoff_authbackoff_other", "AuthBackoff.other", "pkg/backoff.py", 5, "Method"),
        _n("pkg_backoff_authbackoff_check_all", "AuthBackoff.check_all", "pkg/backoff.py", 7, "Method"),
        _n("pkg_backoff_handle", "handle", "pkg/backoff.py", 12),
        _n("tests_test_a", "test_a", "tests/test_a.py", 1, "Module"),
        _n("tests_test_a_check", "check", "tests/test_a.py", 1),
        _n("tests_test_a_gate_one", "gate_one", "tests/test_a.py", 5),
        _n("tests_test_a_gate_mixed", "gate_mixed", "tests/test_a.py", 10),
        _n("tests_test_b", "test_b", "tests/test_b.py", 1, "Module"),
        _n("tests_test_b_fake", "Fake", "tests/test_b.py", 1, "Class"),
        _n("tests_test_b_fake_check", "Fake.check", "tests/test_b.py", 2, "Method"),
        _n("tests_test_b_check", "check", "tests/test_b.py", 6),
        _n("tests_test_b_gate_two", "gate_two", "tests/test_b.py", 10),
        _n("tests_test_b_holder", "Holder", "tests/test_b.py", 14, "Class"),
        _n("tests_test_b_holder__mk", "Holder._mk", "tests/test_b.py", 15, "Method"),
        _n("tests_test_c_run_it", "run_it", "tests/test_c.py", 1),
        _n("web_api_api", "Api", "web/api.ts", 1, "Class"),
        _n("web_api_api_check", "Api.check", "web/api.ts", 2, "Method"),
        _n("web_run_check", "check", "web/run.ts", 3),
        _n("web_run_run", "run", "web/run.ts", 7),
        _n("web_run_run2", "run2", "web/run.ts", 11),
        _n("java_app", "App", "java/App.java", 1, "Class"),
        _n("java_app_m", "App.m", "java/App.java", 2, "Method"),
        _n("missing_caller", "gone", "tests/deleted_file.py", 3),
    ]


_CALLS_A = [
    ("pkg_backoff_handle", "AuthBackoff.check"),       # engine-resolved method call (dotted) — untouched
    ("pkg_backoff_authbackoff_other", "check_all"),    # bare, same file, SAME class -> kept (conservative)
    ("tests_test_a_gate_one", "check"),                # bare only
    ("tests_test_a_gate_mixed", "check"),              # bare AND qualified in one body
    ("tests_test_b_gate_two", "check"),                # bare only; the file also has Fake.check
    ("tests_test_b_holder", "_mk"),                    # a class body's bare call to its own method
    ("tests_test_b", "check"),                         # a module-level bare call
    ("tests_test_c_run_it", "check"),                  # the engine recorded a call the body never shows
    ("web_run_run", "check"),                          # TS bare
    ("web_run_run2", "check"),                         # TS qualified (the engine mis-bound it to the fn)
    ("java_app_m", "check"),                           # Java: implicit this -> never ruled out
    ("missing_caller", "check"),                       # unreadable source -> kept
]


def gate_a_graphify_bare_method() -> None:
    print("\n-- A: graphify path — a bare call never binds another file's / class's METHOD at push time --")
    S = _load()
    check("sync._attach_bare_calls exists", callable(getattr(S, "_attach_bare_calls", None)))
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_DROP_APP_TO_TEST_CALLS=None,
                                                   DEEPMAP_FALSE_CALL_FILTER=None):
        root = Path(td)
        for rel, text in _TREE_A.items():
            _write(root, rel, text)
        nodes = _nodes_a()
        before = _r7_resolve_calls(S, [dict(n) for n in nodes], _CALLS_A)
        _attach(S, root, nodes, _CALLS_A)
        edges = S._resolve_calls(nodes, _CALLS_A)

    def e(a, b):
        return edges.get((a, b))

    check("precondition (round 7): gate_one's bare check() fanned out to AuthBackoff.check (AMBIGUOUS)",
          before.get(("tests_test_a_gate_one", "pkg_backoff_authbackoff_check")) == "AMBIGUOUS", before)
    check("gate_one (only bare `check(`) no longer binds AuthBackoff.check (another file's method)",
          e("tests_test_a_gate_one", "pkg_backoff_authbackoff_check") is None,
          e("tests_test_a_gate_one", "pkg_backoff_authbackoff_check"))
    check("... nor Fake.check (tests/test_b.py) nor Api.check (web/api.ts)",
          e("tests_test_a_gate_one", "tests_test_b_fake_check") is None
          and e("tests_test_a_gate_one", "web_api_api_check") is None, edges)
    check("... and still binds the same-named FUNCTIONS (graded over what remains: 3 -> AMBIGUOUS)",
          e("tests_test_a_gate_one", "tests_test_a_check") == "AMBIGUOUS"
          and e("tests_test_a_gate_one", "tests_test_b_check") == "AMBIGUOUS"
          and e("tests_test_a_gate_one", "web_run_check") == "AMBIGUOUS", edges)
    check("gate_mixed (`check(` AND `backoff.check(` in one body) KEEPS its method edges (AMBIGUOUS)",
          e("tests_test_a_gate_mixed", "pkg_backoff_authbackoff_check") == "AMBIGUOUS"
          and e("tests_test_a_gate_mixed", "web_api_api_check") == "AMBIGUOUS", edges)
    check("gate_two: a bare check() never binds Fake.check, a method of ANOTHER CLASS in its own file",
          e("tests_test_b_gate_two", "tests_test_b_fake_check") is None, e("tests_test_b_gate_two", "tests_test_b_fake_check"))
    check("a module-level bare check() binds no method either (AuthBackoff.check / Fake.check)",
          e("tests_test_b", "pkg_backoff_authbackoff_check") is None
          and e("tests_test_b", "tests_test_b_fake_check") is None
          and e("tests_test_b", "tests_test_b_check") == "AMBIGUOUS", edges)
    check("a class body's bare _mk() still binds its OWN class's method Holder._mk (class-body scope)",
          e("tests_test_b_holder", "tests_test_b_holder__mk") == "INFERRED", e("tests_test_b_holder", "tests_test_b_holder__mk"))
    check("a bare call to a method of the caller's OWN class (same file) is kept (conservative)",
          e("pkg_backoff_authbackoff_other", "pkg_backoff_authbackoff_check_all") == "INFERRED",
          e("pkg_backoff_authbackoff_other", "pkg_backoff_authbackoff_check_all"))
    check("an engine-resolved dotted call (_AUTH.check -> 'AuthBackoff.check') is untouched (INFERRED)",
          e("pkg_backoff_handle", "pkg_backoff_authbackoff_check") == "INFERRED", e("pkg_backoff_handle", "pkg_backoff_authbackoff_check"))
    check("no call site in the body (the engine saw something the text does not show) -> kept (AMBIGUOUS)",
          e("tests_test_c_run_it", "pkg_backoff_authbackoff_check") == "AMBIGUOUS", edges)
    check("TS: run()'s bare check() no longer binds Api.check (web/api.ts) — JS/TS need `this.`",
          e("web_run_run", "web_api_api_check") is None and e("web_run_run", "web_run_check") == "AMBIGUOUS", edges)
    check("TS: run2()'s `api.check(2)` keeps Api.check", e("web_run_run2", "web_api_api_check") == "AMBIGUOUS", edges)
    check("Java (implicit `this`): a bare check() may be an inherited method -> method edges kept",
          e("java_app_m", "pkg_backoff_authbackoff_check") == "AMBIGUOUS"
          and e("java_app_m", "web_api_api_check") == "AMBIGUOUS", edges)
    check("an unreadable caller file -> nothing dropped (the method edge is kept)",
          e("missing_caller", "pkg_backoff_authbackoff_check") == "AMBIGUOUS", edges)
    dropped = set(before) - set(edges)
    only_methods = all(any(n["key"] == b and n["label"] == "Method" for n in nodes) for _a, b in dropped)
    check("ONLY :Method targets were dropped, and no edge was added", only_methods and set(edges) <= set(before),
          sorted(dropped))
    check("the metadata is private: `call_bare` is a list of names on the caller node, never a pushed field",
          isinstance(next((n for n in nodes if n["key"] == "tests_test_a_gate_one"), {}).get("call_bare"), list))

    # a single-candidate case turns INFERRED once the impossible method is gone
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None):
        root = Path(td)
        _write(root, "a/tool.py", "class Tool:\n    def build(self):\n        return 1\n")
        _write(root, "a/make.py", "def build():\n    return 2\n")
        _write(root, "b/use.py", "from a.make import build\n\n\ndef go():\n    return build()\n")
        nodes = [_n("t", "Tool.build", "a/tool.py", 2, "Method"), _n("m", "build", "a/make.py", 1),
                 _n("g", "go", "b/use.py", 4)]
        calls = [("g", "build")]
        _attach(S, root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
    check("the confidence is counted over the remaining targets: 1 function left -> INFERRED",
          edges == {("g", "m"): "INFERRED"}, edges)


# =====================================================================================================
# B — ast path: a same-file method of ANOTHER class is out of reach too; nested defs stay bindable
# =====================================================================================================
_AST_SRC = '''class Fake:
    def check(self, ip):
        return ip


def check(label):
    return label


def gate():
    check("x")


class Svc:
    def helper2(self):
        def inner():
            return 1
        inner()
        return 2

    def run(self):
        check("y")
'''


def gate_b_ast_same_file_class() -> None:
    print("\n-- B: ast path — a bare call never binds a same-file method of ANOTHER class --")
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None):
        root = Path(td)
        _write(root, "mod.py", _AST_SRC)
        with contextlib.redirect_stderr(io.StringIO()):
            nodes, calls, _imports = S._rebuild_code_ast(root)
        _attach(S, root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
    key = {n["name"]: n["key"] for n in nodes if n["label"] != "Module"}

    def e(a, b):
        return edges.get((key.get(a), key.get(b)))

    check("gate(): bare check() no longer binds Fake.check (same file, another class)",
          e("gate", "Fake.check") is None, e("gate", "Fake.check"))
    check("gate(): bare check() binds the module function check, now INFERRED (1 candidate left)",
          e("gate", "check") == "INFERRED", e("gate", "check"))
    check("Svc.run: bare check() binds neither Fake.check nor anything but the function",
          e("Svc.run", "Fake.check") is None and e("Svc.run", "check") == "INFERRED", edges)
    check("round-7 invariant: inner() — a def nested in the caller's method (a :Method node) stays INFERRED",
          e("Svc.helper2", "Svc.inner") == "INFERRED", e("Svc.helper2", "Svc.inner"))
    with _env(DEEPMAP_FALSE_CALL_FILTER="0"):
        off = S._resolve_calls(nodes, calls)
    check("DEEPMAP_FALSE_CALL_FILTER=0: the round-7 ast grades (Fake.check kept, AMBIGUOUS)",
          off.get((key.get("gate"), key.get("Fake.check"))) == "AMBIGUOUS", off)


# =====================================================================================================
# C — Go: a non-_test caller never binds a _test.go symbol at push time
# =====================================================================================================
def gate_c_go_test_files() -> None:
    print("\n-- C: Go — a non-_test caller never binds a _test.go symbol (name fan-out and precise) --")
    S = _load()
    nodes = [_n("serve", "Serve", "a/server.go", 3), _n("helper", "helper", "a/util.go", 1),
             _n("helper_t", "helper", "b/helper_test.go", 5), _n("t_caller", "TestServe", "a/server_test.go", 2),
             _n("fake_close", "fakeConn.Close", "a/conn_test.go", 9, "Method"),
             _n("real_close", "conn.Close", "a/conn.go", 4, "Method"), _n("shut", "Shutdown", "a/server.go", 20)]
    calls = [("serve", "helper"), ("t_caller", "helper"), ("shut", "Close")]
    with _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None):
        edges = S._resolve_calls([dict(n) for n in nodes], calls)
    check("a/server.go Serve -> a/util.go helper is now INFERRED (the _test.go twin no longer competes)",
          edges.get(("serve", "helper")) == "INFERRED", edges)
    check("a/server.go Serve -> b/helper_test.go helper: no edge (go test builds _test.go only)",
          ("serve", "helper_t") not in edges, edges)
    check("a _test.go caller still binds both (an external x_test package may call either)",
          edges.get(("t_caller", "helper")) == "AMBIGUOUS" and edges.get(("t_caller", "helper_t")) == "AMBIGUOUS", edges)
    check("an unresolved `.Close()` from production code no longer lands on a _test.go fake's method",
          ("shut", "fake_close") not in edges and edges.get(("shut", "real_close")) == "INFERRED", edges)
    with _env(DEEPMAP_PRECISE_CALLS="1", DEEPMAP_FALSE_CALL_FILTER=None):
        # R11/R1: graphify-RESOLVED callees are MARKED keys (as map_graph hands them over)
        _k = S.graphify_extract.call_key
        pe = S._resolve_calls([dict(n) for n in nodes], [("serve", _k("helper_t")), ("t_caller", _k("helper_t"))])
    check("precise CALLS: a resolved _test.go key from a non-_test caller is dropped too",
          ("serve", "helper_t") not in pe, pe)
    check("precise CALLS: a _test.go caller keeps its resolved _test.go key", ("t_caller", "helper_t") in pe, pe)
    with _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER="0"):
        off = S._resolve_calls([dict(n) for n in nodes], calls)
    check("DEEPMAP_FALSE_CALL_FILTER=0 keeps the round-7 fan-out (both helpers, AMBIGUOUS)",
          off.get(("serve", "helper")) == "AMBIGUOUS" and off.get(("serve", "helper_t")) == "AMBIGUOUS", off)
    check("the Go rule is case-sensitive like the go tool: `x_TEST.go` is NOT a test file",
          True if S._resolve_calls([_n("s", "S", "a/s.go"), _n("h", "h", "a/x_TEST.go")], [("s", "h")]) == {("s", "h"): "INFERRED"}
          else False)


# =====================================================================================================
# D — opt-out and untouched callers are byte-identical
# =====================================================================================================
def gate_d_byte_identity() -> None:
    print("\n-- D: DEEPMAP_FALSE_CALL_FILTER=0 and metadata-less callers are byte-identical to round 7 --")
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_DROP_APP_TO_TEST_CALLS=None,
                                                   DEEPMAP_FALSE_CALL_FILTER="0"):
        root = Path(td)
        for rel, text in _TREE_A.items():
            _write(root, rel, text)
        nodes = _nodes_a()
        _attach(S, root, nodes, _CALLS_A)
        off = S._resolve_calls(nodes, _CALLS_A)
        oracle = _r7_resolve_calls(S, nodes, _CALLS_A)
    check("filter OFF: the attach adds no call_bare data", not any("call_bare" in n for n in nodes),
          [n["key"] for n in nodes if "call_bare" in n])
    check("filter OFF: _resolve_calls == the round-7 resolver (same dict, same order)",
          list(off.items()) == list(oracle.items()), {k: (off.get(k), oracle.get(k)) for k in set(off) ^ set(oracle)})
    # callers with no metadata and no Go -> byte-identical under the default (filter ON)
    fx_nodes, fx_calls = [], []
    for t in range(4):
        fx_nodes.append(_n(f"lib.py::t{t}:{t + 1}", f"t{t}", "lib.py", t + 1))
    fx_nodes.append(_n("lib.py::Svc.run:50", "Svc.run", "lib.py", 50, "Method"))
    for i in range(12):
        k = f"app{i}.py::caller{i}:1"
        fx_nodes.append(_n(k, f"caller{i}", f"app{i}.py", 1))
        fx_calls += [(k, f"t{i % 4}"), (k, "run"), (k, "Svc.run"), (k, "missing")]
    with _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None, DEEPMAP_DROP_APP_TO_TEST_CALLS=None):
        new, old = S._resolve_calls(fx_nodes, fx_calls), _r7_resolve_calls(S, fx_nodes, fx_calls)
    check("default ON, callers WITHOUT source-derived metadata: identical to round 7 (pure resolver)",
          list(new.items()) == list(old.items()), {k: (new.get(k), old.get(k)) for k in set(new) ^ set(old)})


# =====================================================================================================
# E — T3#6 interplay: nested-def shadowing is judged on the targets that remain
# =====================================================================================================
def gate_e_shadowing_after_drop() -> None:
    print("\n-- E: nested-def shadowing counts the targets that REMAIN after the method drop --")
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None):
        root = Path(td)
        _write(root, "tests/a_gates.py", "def _rec(x):\n    return x\n")
        _write(root, "tests/k.py", "class K:\n    def _rec(self):\n        return 1\n")
        _write(root, "tests/b_gates.py", "def gate_setup():\n    def _rec(x):\n        return x\n    _rec(1)\n")
        nodes = [_n("a_rec", "_rec", "tests/a_gates.py"), _n("k_rec", "K._rec", "tests/k.py", 2, "Method"),
                 _n("b_setup", "gate_setup", "tests/b_gates.py", 1)]
        calls = [("b_setup", "_rec")]
        _attach(S, root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
    check("K._rec (another file's method) is dropped for the bare _rec(1)", ("b_setup", "k_rec") not in edges, edges)
    check("the one remaining cross-file _rec is AMBIGUOUS: gate_setup's own nested _rec shadows it (T3#6)",
          edges.get(("b_setup", "a_rec")) == "AMBIGUOUS", edges)


# =====================================================================================================
# F — sync() wiring: the call-site data is attached before the push
# =====================================================================================================
class _Res:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None

    def data(self):
        return [dict(r) for r in self._rows]


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        return _Res()


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def gate_f_wiring() -> None:
    print("\n-- F: sync() runs the bare-call pass before push_to_neo4j --")
    S = _load()
    got: dict = {}
    nodes = _nodes_a()
    Ext = getattr(S, "_Extraction")

    def _push(driver, n, c, i, typed_edges=None):
        got["edges"] = S._resolve_calls(n, c)
        return (len(n), len(got["edges"]))

    S._rebuild_code = lambda root: Ext([dict(x) for x in nodes], list(_CALLS_A), [], "graphify",
                                       {"requested": "auto", "resolved": "graphify", "fallback_reason": None})
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = _push
    S._graph_has_nodes = lambda d: False
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(nodes)
    S._maybe_auto_tune = lambda root: None
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7999", DEEPMAP_HOME=str(Path(td) / "home"),
                                                   DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None,
                                                   DEEPMAP_EXTRACTOR=None, DEEPMAP_LEARNING=None):
        root = Path(td) / "p"
        root.mkdir()
        (root / ".deepmap-root").write_text("", encoding="utf-8")
        for rel, text in _TREE_A.items():
            _write(root, rel, text)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            ok = S.sync(root, driver=_Drv())
    edges = got.get("edges") or {}
    check("sync() pushed (fake driver)", ok and bool(edges), out.getvalue()[-800:])
    check("sync(): gate_one's bare check() reached the push WITHOUT the AuthBackoff.check edge",
          ("tests_test_a_gate_one", "pkg_backoff_authbackoff_check") not in edges
          and ("tests_test_a_gate_one", "tests_test_a_check") in edges, sorted(edges)[:20])
    check("sync(): gate_mixed keeps it (a qualified call site in the same body)",
          ("tests_test_a_gate_mixed", "pkg_backoff_authbackoff_check") in edges, sorted(edges)[:20])


# =====================================================================================================
# G — the real engine (graphify) on the probe tree
# =====================================================================================================
def gate_g_real_engine() -> None:
    print("\n-- G: real graphify engine -> map_graph -> push-time resolution --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — section G not run")
        return
    S = _load()
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_HOME=str(Path(td) / "home"), DEEPMAP_EXTRACTOR="graphify",
                                                   DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None):
        root = Path(td) / "probe"
        for rel, text in _TREE_A.items():
            if rel.endswith((".py", ".ts")):
                _write(root, rel, text)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            nodes, calls, _imports, engine = S._rebuild_code(root)
        _attach(S, root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
    by = {(n["name"], n["source_file"]): n["key"] for n in nodes}
    ab, gate_one = by.get(("AuthBackoff.check", "pkg/backoff.py")), by.get(("gate_one", "tests/test_a.py"))
    mixed, api = by.get(("gate_mixed", "tests/test_a.py")), by.get(("Api.check", "web/api.ts"))
    run, run2 = by.get(("run", "web/run.ts")), by.get(("run2", "web/run.ts"))
    check("the engine ran and produced the probe's nodes", engine == "graphify" and ab and gate_one and api,
          (engine, len(nodes)))
    check("real engine: gate_one's bare check() does not bind AuthBackoff.check / Api.check",
          (gate_one, ab) not in edges and (gate_one, api) not in edges, [k for k in edges if k[0] == gate_one])
    check("real engine: gate_mixed keeps AuthBackoff.check (it also calls backoff.check)",
          (mixed, ab) in edges, [k for k in edges if k[0] == mixed])
    check("real engine: TS run() drops Api.check, run2() (api.check) keeps it",
          (run, api) not in edges and (run2, api) in edges, [k for k in edges if k[0] in (run, run2)])


# =====================================================================================================
# H — J4: 127.0.0.1, one lock per endpoint
# =====================================================================================================
def _string_constants(path: Path):
    """Every string the CODE builds (docstrings / bare string statements excluded — prose may cite the old
    spelling)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {id(st.value) for st in ast.walk(tree) if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)}
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            yield node.lineno, text


def gate_h_loopback() -> None:
    print("\n-- H: J4 — sync dials 127.0.0.1; the instance lock is one file per endpoint --")
    hits = []
    for name in ("sync.py", "indexes.py"):
        for line, text in _string_constants(BUILD / name):
            if "bolt://localhost" in text.lower():
                hits.append(f"{name}:{line}")
    check("no string in sync.py / indexes.py builds a bolt://localhost URI (AST scan)", not hits, hits)
    S = _load()
    import hashlib
    with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_HOME=td, NEO4J_URI=None):
        legacy = Path(td) / f"instance-{hashlib.sha1(b'bolt://localhost:7695').hexdigest()[:16]}.lock"
        a = S._instance_lock_path("bolt://127.0.0.1:7695")
        b = S._instance_lock_path("bolt://localhost:7695")
        c = S._instance_lock_path("bolt://[::1]:7695")
        d = S._instance_lock_path("BOLT://LOCALHOST:7695/")
        check("127.0.0.1 / localhost / [::1] / upper-case spellings of one endpoint share ONE lock file",
              a == b == c == d, (a, b, c, d))
        check("... and it is the file builds <= v0.27.x used (they hashed 'bolt://localhost:<port>'), so an "
              "old git-hook sync and a new sync still exclude each other", a == legacy, (a, legacy))
        check("a different port is a different lock", S._instance_lock_path("bolt://127.0.0.1:7697") != a)
        remote = "neo4j+s://Abc.databases.neo4j.io"
        check("an operator's remote URI is keyed verbatim (byte-identical to before)",
              S._instance_lock_path(remote).name
              == f"instance-{hashlib.sha1(remote.encode('utf-8')).hexdigest()[:16]}.lock")
        check("no URI and no NEO4J_URI -> the base endpoint's lock (same file as bolt://localhost:7687)",
              S._instance_lock_path(None) == S._instance_lock_path("bolt://localhost:7687"))
    # the no-port_registry fallback and the legacy wording
    S = _load()
    import builtins
    real_import = builtins.__import__

    def _no_pr(name, *a, **k):
        if name == "port_registry":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    with _env(NEO4J_URI=None):
        saved = builtins.__import__
        builtins.__import__ = _no_pr
        try:
            route = S._resolve_route(Path("."))
            ok, msg = S._legacy_verdict(Path("."), "/some/other/project", None, None)
        finally:
            builtins.__import__ = saved
    check("no port_registry module: the fallback route dials bolt://127.0.0.1:7687", route[0] == "bolt://127.0.0.1:7687",
          route)
    check("... and the legacy refusal names bolt://127.0.0.1:7687", "bolt://127.0.0.1:7687" in str(msg), msg)
    for uri in ("bolt://127.0.0.1:7687", "bolt://localhost:7687", "bolt://[::1]:7687", "bolt://LOCALHOST:7687",
                "bolt://localhost"):
        check(f"_is_local_base_bolt({uri}) is True", S._is_local_base_bolt(uri) is True)
    check("_is_local_base_bolt(bolt://127.0.0.1:7689) is False", S._is_local_base_bolt("bolt://127.0.0.1:7689") is False)
    check("_is_local_base_bolt(bolt://db.example:7687) is False", S._is_local_base_bolt("bolt://db.example:7687") is False)


# =====================================================================================================
# I — J6: core.longpaths on every git probe (Windows)
# =====================================================================================================
def gate_i_longpaths() -> None:
    print("\n-- I: J6 — sync's git probes pass -c core.longpaths=true on Windows --")
    S = _load()
    seen: list = []

    class _P:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, b""

    def fake_run(argv, *a, **k):
        seen.append(list(argv))
        if "--show-toplevel" in argv:
            return _P("C:/x" if k.get("encoding") or k.get("text") else b"C:/x")
        if "HEAD" in argv:
            return _P("a" * 40 if k.get("text") or k.get("encoding") else b"a" * 40)
        return _P(b"" if not (k.get("text") or k.get("encoding")) else "")

    saved = S.subprocess.run
    S.subprocess.run = fake_run
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "graphify-out"
            d.mkdir()
            S._project_identity(Path(td))
            S._git_head_sha(Path(td))
            S._git_worktree_changes(Path(td))
            S._dir_has_tracked_files(d)
    finally:
        S.subprocess.run = saved
    gits = [a for a in seen if a and os.path.basename(str(a[0])).lower().startswith("git")]
    want = ["-c", "core.longpaths=true"] if os.name == "nt" else []
    bad = [a for a in gits if (a[1:3] == ["-c", "core.longpaths=true"]) != bool(want)]
    check(f"all {len(gits)} git probes (identity, HEAD, status, show-prefix, ls-files) carry "
          f"{'-c core.longpaths=true' if want else 'no -c core.longpaths (not Windows)'}",
          len(gits) >= 5 and not bad, bad or gits)

    if os.name != "nt" or not shutil.which("git"):
        print("  [SKIP] the real long-path repository check runs on Windows with git only")
        return
    base = Path(tempfile.mkdtemp(prefix="dmw6r8_"))
    try:
        # ~170-char root (the L2 field case was 174): .git stays under MAX_PATH, the source file does not
        root = base / ("r" + "o" * 60) / ("p" * 60)
        root.mkdir(parents=True)
        g = shutil.which("git")
        lp = ["-c", "core.longpaths=true"]
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@e")

        def git(*args):
            return subprocess.run([g, *lp, "-C", str(root), *args], capture_output=True, env=env, timeout=60)

        git("init", "-q")
        rel_dir = "d" * 50 + "/" + "e" * 50
        rel = f"{rel_dir}/long_module_with_a_very_descriptive_name_xyz.py"
        full = "\\\\?\\" + str(root / rel_dir)
        os.makedirs(full, exist_ok=True)
        with open("\\\\?\\" + str(root / rel), "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
        git("add", "-A")
        c = git("commit", "-q", "-m", "x")
        committed = c.returncode == 0
        plain = subprocess.run([g, "-C", str(root), "status", "--porcelain"], capture_output=True, timeout=60)
        dirty, sample, count = S._git_worktree_changes(root)
        check(f"precondition: the file path is {len(str(root / rel))} chars (> 260) and committed",
              committed and len(str(root / rel)) > 260, c.stderr[-300:])
        print(f"  [info] root is {len(str(root))} chars; plain `git status --porcelain` (no longpaths) says: "
              f"rc={plain.returncode} {plain.stdout[:120]!r} {plain.stderr[:160]!r}")
        check("precondition: WITHOUT core.longpaths git reports the committed file as changed (the field bug: "
              "' M' or ' D' — it cannot lstat / open the long path)",
              plain.returncode == 0 and plain.stdout[:3] in (b" M ", b" D "), plain.stdout[:200])
        check("_git_worktree_changes reports the committed long-path tree CLEAN (was: 1 uncommitted change)",
              dirty is False and count == 0, (dirty, sample, count))
        check("_git_head_sha reads HEAD in the long-root repository", S._git_head_sha(root) is not None)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", "\\\\?\\" + str(base)], capture_output=True, timeout=60)
        if base.exists():
            shutil.rmtree("\\\\?\\" + str(base), ignore_errors=True)


def main() -> int:
    for g in (gate_a_graphify_bare_method, gate_b_ast_same_file_class, gate_c_go_test_files, gate_d_byte_identity,
              gate_e_shadowing_after_drop, gate_f_wiring, gate_g_real_engine, gate_h_loopback, gate_i_longpaths):
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
