#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-9 W5 gates (K11 + K10, map-time side): the graphify-path false-call filter
(`graphify_extract._false_call_filter`, default ON, DEEPMAP_FALSE_CALL_FILTER=0 turns it off).
No Neo4j, no network, no spend. Section F runs the installed graphify (skipped when absent) under a
temporary DEEPMAP_HOME, so nothing is written to ~/.deepmap.

K11 — its 'bare-method' rule dropped a call the engine bound from a bare `name(` to another file's
METHOD, on the premise that a bare call can never reach a method in Python / Go / Rust / JS / TS. The
premise is false when `name` is a VALUE that holds a bound method or a method value:
    inst.py:   class Client: def get(self, url) ...;  _default = Client();  get = _default.get
    caller.py: from inst import get;  def fetch(url): return get(url)       <- really calls Client.get
    store.go:  var First = defaultQuery.First;  svc.go: func Load() error { return First() }
The engine binds both correctly and the filter deleted the true edge (deepmap_impact Client.get /
Query.First then reports no caller). A bare call is now ruled out only when the name CANNOT hold such a
value in the caller's scope:
  A  Python (ast): not a parameter/local of the caller, not a module-level value of the caller's module,
     and not imported — or imported from a module that provably binds it as a def/class (followed
     through relative imports, re-exports and star imports), or from a module outside the project
     (unless the method's own module binds that name as a value).
  B  Go: no package-level `var name` in the caller's package (directory; a _test.go var only counts for a
     test caller), and no local binding / parameter of that name in the caller.
  C  JS/TS: the caller file does not bind the name as a value (an import from a project path, a const /
     let / var, a destructuring, a parameter; an import from a bare package specifier is an external
     value), and the method's file does not export a value of that name. Rust: no `let` / parameter
     binding in the caller (a `use`d item is a fn, never an inherent method).
K10 — the same filter read only the text BEFORE the name on the same line, so the continuation line of a
receiver call split after the dot (gofmt's only legal multi-line chain form, `q.\\n\\t\\tFirst()`; JS/TS/Rust;
Python inside brackets or after `\\`) counted as a BARE call:
  D  `_call_sites` takes the previous code line (comments and blank lines skipped, a trailing comment
     cut) and a line that ends in `.` / `?.` / `::` / `->` makes the site qualified (never a float literal
     `1.`, an ellipsis `...` or a range `..`); the caller-body rescue and the value scan use it.
  D2 end to end: a genuine `(obj.\\n    helper(2))` in the caller's body keeps a recorded bare call (and a
     recorded continuation line is a receiver call); a Go selector `q.\\n\\t\\tNew()` no longer "rescues" a
     `util.New()` the engine bound to the caller package's own New.
  E  DEEPMAP_FALSE_CALL_FILTER=0 keeps the engine's calls byte for byte; every control still drops.
  F  the REAL engine on the reviewers' repro trees.
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


_TMP = Path(tempfile.mkdtemp(prefix="dm_r9bx_"))
_N = [0]


# ---- a tiny builder for engine-shaped graphs (the shape graphify 0.8.39 / 0.8.51 writes) -----------
def node(nid, label, sf, line):
    return {"id": nid, "label": label, "file_type": "code", "source_file": sf, "source_location": f"L{line}"}


def link(rel, s, t, line, conf="EXTRACTED"):
    return {"source": s, "target": t, "relation": rel, "source_location": f"L{line}", "confidence": conf}


def fnode(nid, sf):
    return node(nid, sf.rsplit("/", 1)[-1], sf, 1)


def run(files: dict, nodes: list, links: list, **env):
    """Materialize `files` under a fresh temp root, map the graph with that root, and return the
    (caller name, callee name) pairs + the stats."""
    _N[0] += 1
    root = _TMP / f"t{_N[0]}"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    st: dict = {}
    with _env(**{**{f: None for f in _FLAGS}, **env}):
        n, c, _i = GX.map_graph(json.loads(json.dumps({"nodes": nodes, "links": links})), root=root, stats=st)
    k = {x["key"]: x["name"] for x in n}
    return {(k.get(s, s), k.get(t, t)) for s, t in c}, st


# ================================================================================================
# A — Python
_BACKOFF = ("class AuthBackoff:\n    def check(self):\n        return True\n\n\n"
            "_default = AuthBackoff()\ncheck = _default.check\n")


def _py_backoff_graph(server_sf, handle_line, call_line, *, extra_nodes=(), extra_links=()):
    """pkg/backoff.py (AuthBackoff.check) + a caller file with handle() — the engine binds the bare
    `check()` to AuthBackoff.check (the reviewer's repro, 0.8.39's real shape)."""
    sid = server_sf.replace("/", "_").rsplit(".", 1)[0]
    nodes = [fnode("pkg_init", "pkg/__init__.py"), fnode("pkg_backoff", "pkg/backoff.py"),
             node("pkg_backoff_authbackoff", "AuthBackoff", "pkg/backoff.py", 1),
             node("pkg_backoff_authbackoff_check", ".check()", "pkg/backoff.py", 2),
             fnode(sid, server_sf), node(sid + "_handle", "handle()", server_sf, handle_line), *extra_nodes]
    links = [link("contains", "pkg_backoff", "pkg_backoff_authbackoff", 1),
             link("method", "pkg_backoff_authbackoff", "pkg_backoff_authbackoff_check", 2),
             link("contains", sid, sid + "_handle", handle_line),
             link("calls", sid + "_handle", "pkg_backoff_authbackoff_check", call_line), *extra_links]
    return nodes, links


def gate_a_python() -> None:
    print("\n-- A (K11, Python): a bare name that may hold a bound method keeps the engine's call --")
    want = ("handle", "AuthBackoff.check")

    # A1 the reviewer's repro: `from pkg.backoff import check` where backoff binds `check = _default.check`
    files = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
             "pkg/server.py": "from pkg.backoff import check\n\n\ndef handle():\n    if check():\n        return 1\n"
                              "    return 0\n"}
    nodes, links = _py_backoff_graph("pkg/server.py", 4, 5)
    links += [link("imports_from", "pkg_server", "pkg_backoff", 1),
              link("imports", "pkg_server", "pkg_backoff_authbackoff_check", 1)]
    got, st = run(files, nodes, links)
    check("A1 `from pkg.backoff import check` (a bound-method alias there) keeps handle -> AuthBackoff.check",
          want in got, sorted(got))
    check("A1 ...and it is not counted as a filtered call", not st.get("calls_filtered"), st)

    # A2 relative import
    files2 = dict(files, **{"pkg/server.py": files["pkg/server.py"].replace("from pkg.backoff", "from .backoff")})
    got, _st = run(files2, nodes, links)
    check("A2 the same through a RELATIVE import (`from .backoff import check`)", want in got, sorted(got))

    # A3 the alias lives in a module OTHER than the method's (a package __init__ re-binding it)
    files3 = {"pkg/__init__.py": "from pkg.client import Client\n\n_d = Client()\nget = _d.get\n",
              "pkg/client.py": "class Client:\n    def get(self, url):\n        return url\n",
              "app.py": "from pkg import get\n\n\ndef fetch(url):\n    return get(url)\n"}
    nodes3 = [fnode("app", "app.py"), node("app_fetch", "fetch()", "app.py", 4), fnode("pkg_init", "pkg/__init__.py"),
              fnode("pkg_client", "pkg/client.py"), node("pkg_client_client", "Client", "pkg/client.py", 1),
              node("pkg_client_client_get", ".get()", "pkg/client.py", 2)]
    links3 = [link("contains", "app", "app_fetch", 4), link("imports_from", "app", "pkg", 1),
              link("imports_from", "pkg_init", "pkg_client", 1),
              link("contains", "pkg_client", "pkg_client_client", 1),
              link("method", "pkg_client_client", "pkg_client_client_get", 2),
              link("imports", "pkg_init", "pkg_client_client", 1),
              link("calls", "app_fetch", "pkg_client_client_get", 5, "INFERRED")]
    got, _st = run(files3, nodes3, links3)
    check("A3 `from pkg import get` where pkg/__init__.py binds `get = _d.get` keeps fetch -> Client.get",
          ("fetch", "Client.get") in got, sorted(got))

    # A4 a re-export chain: app -> pkg/__init__ (`from .backoff import check`) -> the alias in backoff
    files4 = {"pkg/__init__.py": "from .backoff import check\n", "pkg/backoff.py": _BACKOFF,
              "app.py": "from pkg import check\n\n\ndef handle():\n    return check()\n"}
    nodes4, links4 = _py_backoff_graph("app.py", 4, 5)
    got, _st = run(files4, nodes4, links4)
    check("A4 a re-export chain (`from pkg import check`, pkg re-exports backoff's alias) keeps the call",
          want in got, sorted(got))

    # A5 a function-level import
    files5 = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
              "app.py": "def handle():\n    from pkg.backoff import check\n    return check()\n"}
    nodes5, links5 = _py_backoff_graph("app.py", 1, 3)
    got, _st = run(files5, nodes5, links5)
    check("A5 an import INSIDE the caller (`from pkg.backoff import check` in handle) keeps the call",
          want in got, sorted(got))

    # A6 a star import of the alias's module
    files6 = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
              "app.py": "from pkg.backoff import *\n\n\ndef handle():\n    return check()\n"}
    nodes6, links6 = _py_backoff_graph("app.py", 4, 5)
    got, _st = run(files6, nodes6, links6)
    check("A6 `from pkg.backoff import *` (the module binds `check` as a value) keeps the call", want in got,
          sorted(got))

    # A7 the caller's OWN module binds the alias
    files7 = {"pkg/__init__.py": "", "pkg/backoff.py": "class AuthBackoff:\n    def check(self):\n        return 1\n\n\n"
                                                        "_default = AuthBackoff()\n",
              "app.py": "from pkg.backoff import _default\n\ncheck = _default.check\n\n\ndef handle():\n"
                        "    return check()\n"}
    nodes7, links7 = _py_backoff_graph("app.py", 6, 7)
    got, _st = run(files7, nodes7, links7)
    check("A7 the caller's own module-level `check = _default.check` keeps the call", want in got, sorted(got))

    # A8 a parameter (the engine's real output for `def run(helper): return helper(1)`)
    files8 = {"store.py": "class Store:\n    def helper(self, x):\n        return x\n",
              "app.py": "def run(helper):\n    return helper(1)\n\n\ndef only():\n    return helper(3)\n"}
    nodes8 = [fnode("store", "store.py"), node("store_store", "Store", "store.py", 1),
              node("store_store_helper", ".helper()", "store.py", 2), fnode("app", "app.py"),
              node("app_run", "run()", "app.py", 1), node("app_only", "only()", "app.py", 5)]
    links8 = [link("contains", "store", "store_store", 1), link("method", "store_store", "store_store_helper", 2),
              link("contains", "app", "app_run", 1), link("contains", "app", "app_only", 5),
              link("calls", "app_run", "store_store_helper", 2, "INFERRED"),
              link("calls", "app_only", "store_store_helper", 6, "INFERRED")]
    got, st = run(files8, nodes8, links8)
    check("A8 a PARAMETER `helper` may hold a bound method (run(store.helper)) -> run -> Store.helper kept",
          ("run", "Store.helper") in got, sorted(got))
    check("A8 ...while only()'s bare `helper(3)` (bound to nothing in its scope) still drops", ("only", "Store.helper")
          not in got and st.get("calls_filtered") == 1, (sorted(got), st))

    # A9 the import's module is outside the project, but the METHOD's own module binds that name as a value
    files9 = {"inst.py": "class Client:\n    def get(self, url):\n        return url\n\n\n_default = Client()\n"
                         "get = _default.get\n",
              "caller.py": "from vendored.inst import get\n\n\ndef fetch(url):\n    return get(url)\n"}
    nodes9 = [fnode("caller", "caller.py"), node("caller_fetch", "fetch()", "caller.py", 4), fnode("inst", "inst.py"),
              node("inst_client", "Client", "inst.py", 1), node("inst_client_get", ".get()", "inst.py", 2)]
    links9 = [link("contains", "caller", "caller_fetch", 4), link("contains", "inst", "inst_client", 1),
              link("method", "inst_client", "inst_client_get", 2),
              link("calls", "caller_fetch", "inst_client_get", 5, "INFERRED")]
    got, _st = run(files9, nodes9, links9)
    check("A9 an import the project cannot resolve keeps the call when the method's module binds `get` as a "
          "value (the alias may be what was imported)", ("fetch", "Client.get") in got, sorted(got))

    print("   controls (still dropped — the name provably cannot hold a method):")
    # C1 the reviewer's external control: `from os.path import join` bound to Cache.join
    filesc = {"store.py": "class Cache:\n    def join(self, a):\n        return a\n",
              "use.py": "from os.path import join\n\n\ndef build(a):\n    return join(a, 'x')\n"}
    nodesc = [fnode("store", "store.py"), node("store_cache", "Cache", "store.py", 1),
              node("store_cache_join", ".join()", "store.py", 2), fnode("use", "use.py"),
              node("use_build", "build()", "use.py", 4)]
    linksc = [link("contains", "store", "store_cache", 1), link("method", "store_cache", "store_cache_join", 2),
              link("contains", "use", "use_build", 4), link("imports_from", "use", "os_path", 1),
              link("calls", "use_build", "store_cache_join", 5, "INFERRED")]
    got, st = run(filesc, nodesc, linksc)
    check("C1 `from os.path import join` (a module outside the project) never reaches Cache.join -> dropped",
          ("build", "Cache.join") not in got and st.get("calls_filtered") == 1, (sorted(got), st))

    # C2 the imported name is a DEF in its module
    filesd = {"a/__init__.py": "", "a/tool.py": "class Tool:\n    def build(self):\n        return 1\n",
              "a/make.py": "def build():\n    return 2\n",
              "b/use.py": "from a.make import build\n\n\ndef go():\n    return build()\n"}
    nodesd = [fnode("a_tool", "a/tool.py"), node("a_tool_tool", "Tool", "a/tool.py", 1),
              node("a_tool_tool_build", ".build()", "a/tool.py", 2), fnode("a_make", "a/make.py"),
              node("a_make_build", "build()", "a/make.py", 1), fnode("b_use", "b/use.py"),
              node("b_use_go", "go()", "b/use.py", 4)]
    linksd = [link("contains", "a_tool", "a_tool_tool", 1), link("method", "a_tool_tool", "a_tool_tool_build", 2),
              link("contains", "a_make", "a_make_build", 1), link("contains", "b_use", "b_use_go", 4),
              link("calls", "b_use_go", "a_tool_tool_build", 5, "INFERRED")]
    got, _st = run(filesd, nodesd, linksd)
    check("C2 `from a.make import build` where make.py DEFINES build -> the bare call is that def, "
          "go -> Tool.build dropped", ("go", "Tool.build") not in got, sorted(got))
    filesd2 = dict(filesd, **{"b/use.py": "from ..a.make import build\n\n\ndef go():\n    return build()\n"})
    got, _st = run(filesd2, nodesd, linksd)
    check("C2 ...and through a relative import of that def", ("go", "Tool.build") not in got, sorted(got))

    # C3 a re-export chain that ends in a def
    filese = {"pkg/__init__.py": "from .util import helper\n", "pkg/util.py": "def helper():\n    return 1\n",
              "store.py": "class Store:\n    def helper(self):\n        return 2\n",
              "app.py": "from pkg import helper\n\n\ndef run():\n    return helper()\n"}
    nodese = [fnode("store", "store.py"), node("store_store", "Store", "store.py", 1),
              node("store_store_helper", ".helper()", "store.py", 2), fnode("app", "app.py"),
              node("app_run", "run()", "app.py", 4), fnode("pkg_init", "pkg/__init__.py"),
              fnode("pkg_util", "pkg/util.py"), node("pkg_util_helper", "helper()", "pkg/util.py", 1)]
    linkse = [link("contains", "store", "store_store", 1), link("method", "store_store", "store_store_helper", 2),
              link("contains", "app", "app_run", 4), link("contains", "pkg_util", "pkg_util_helper", 1),
              link("calls", "app_run", "store_store_helper", 5, "INFERRED")]
    got, _st = run(filese, nodese, linkse)
    check("C3 a re-export chain that ends in a DEF (pkg re-exports util.helper) -> dropped",
          ("run", "Store.helper") not in got, sorted(got))

    # C3b an import cycle (`from . import check` inside pkg/__init__.py names a submodule) binds no value
    filesc3 = {"pkg/__init__.py": "from . import check\n", "pkg/check.py": "X = 1\n",
               "store.py": "class Store:\n    def check(self):\n        return 2\n",
               "app.py": "from pkg import check\n\n\ndef run():\n    return check()\n"}
    nodesc3 = [fnode("store", "store.py"), node("store_store", "Store", "store.py", 1),
               node("store_store_check", ".check()", "store.py", 2), fnode("app", "app.py"),
               node("app_run", "run()", "app.py", 4), fnode("pkg_init", "pkg/__init__.py"), fnode("pkg_check", "pkg/check.py")]
    linksc3 = [link("contains", "store", "store_store", 1), link("method", "store_store", "store_store_check", 2),
               link("contains", "app", "app_run", 4), link("calls", "app_run", "store_store_check", 5, "INFERRED")]
    got, _st = run(filesc3, nodesc3, linksc3)
    check("C3b an import cycle (`from . import check` in pkg/__init__.py) binds no value -> dropped",
          ("run", "Store.check") not in got, sorted(got))

    # C4 a nested def of the same name (the py2 `_match` shape) and a plain module-level def
    filesf = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
              "app.py": "def handle():\n    def check():\n        return 1\n    return check()\n"}
    nodesf, linksf = _py_backoff_graph("app.py", 1, 4)
    got, _st = run(filesf, nodesf, linksf)
    check("C4 a nested `def check` in the caller is a function, not a value -> dropped", want not in got, sorted(got))
    filesg = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
              "app.py": "def check():\n    return 1\n\n\ndef handle():\n    return check()\n"}
    nodesg, linksg = _py_backoff_graph("app.py", 5, 6)
    got, _st = run(filesg, nodesg, linksg)
    check("C4 ...nor a module-level `def check` of the caller's module -> dropped", want not in got, sorted(got))
    filesh = {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
              "app.py": "# check = _default.check  (a comment, not a binding)\n\n\ndef handle():\n"
                        "    return check()\n"}
    nodesh, linksh = _py_backoff_graph("app.py", 4, 5)
    got, _st = run(filesh, nodesh, linksh)
    check("C4 ...nor a name that is only mentioned in a comment -> dropped", want not in got, sorted(got))


# ================================================================================================
# B — Go
_STORE_GO = "package x\n\ntype Query struct{}\n\nfunc (q *Query) First() error { return nil }\n"


def _go_graph(svc_text_line_load, call_line, *, extra_files=()):
    nodes = [fnode("store", "x/store.go"), node("goalias_query", "Query", "x/store.go", 3),
             node("goalias_query_first", ".First()", "x/store.go", 5), fnode("svc", "x/svc.go"),
             node("svc_load", "Load()", "x/svc.go", svc_text_line_load)]
    nodes += [fnode(f.replace("/", "_").rsplit(".", 1)[0], f) for f in extra_files]
    links = [link("method", "goalias_query", "goalias_query_first", 5), link("contains", "svc", "svc_load", 3),
             link("calls", "svc_load", "goalias_query_first", call_line, "INFERRED")]
    return nodes, links


def gate_b_go() -> None:
    print("\n-- B (K11, Go): a package-level method value / local binding keeps the engine's call --")
    want = ("Load", "Query.First")
    svc = "package x\n\nfunc Load() error {\n\treturn First()\n}\n"
    got, st = run({"x/store.go": _STORE_GO + "\nvar defaultQuery = &Query{}\n\nvar First = defaultQuery.First\n",
                   "x/svc.go": svc}, *_go_graph(3, 4))
    check("B1 the reviewer's repro: `var First = defaultQuery.First` keeps Load -> Query.First", want in got,
          (sorted(got), st))
    got, _st = run({"x/store.go": _STORE_GO + "\nvar (\n\tdefaultQuery = &Query{}\n\tFirst        = defaultQuery.First\n)\n",
                    "x/svc.go": svc}, *_go_graph(3, 4))
    check("B2 the alias inside a package-level `var (...)` block", want in got, sorted(got))
    got, _st = run({"x/store.go": _STORE_GO, "x/svc.go": svc,
                    "x/defaults.go": "package x\n\nvar defaultQuery = &Query{}\n\nvar First, Second = defaultQuery.First, 1\n"},
                   *_go_graph(3, 4, extra_files=("x/defaults.go",)))
    check("B3 the alias in a THIRD file of the package (a multi-name `var First, Second = ...`)", want in got,
          sorted(got))
    got, _st = run({"x/store.go": _STORE_GO,
                    "x/svc.go": "package x\n\nfunc Load(q *Query) error {\n\tFirst := q.First\n\treturn First()\n}\n"},
                   *_go_graph(3, 5))
    check("B4 a LOCAL method value (`First := q.First`)", want in got, sorted(got))
    got, _st = run({"x/store.go": _STORE_GO,
                    "x/svc.go": "package x\n\nfunc Load(First func() error) error {\n\treturn First()\n}\n"},
                   *_go_graph(3, 4))
    check("B5 a PARAMETER of that name", want in got, sorted(got))

    print("   controls (still dropped):")
    got, st = run({"x/store.go": _STORE_GO + "\n// First is the first row (a comment is not a binding)\n",
                   "x/svc.go": "package x\n\nfunc Load() error {\n\tname := \"First\"\n\t_ = name\n\treturn First()\n}\n"},
                  *_go_graph(3, 6))
    check("C1 no binding anywhere (only a comment and a string literal mention it) -> dropped",
          want not in got and st.get("calls_filtered") == 1, (sorted(got), st))
    got, _st = run({"x/store.go": _STORE_GO, "x/svc.go": svc,
                    "x/alias_test.go": "package x\n\nvar First = (&Query{}).First\n"},
                   *_go_graph(3, 4, extra_files=("x/alias_test.go",)))
    check("C2 a var in a _test.go file is invisible to a non-test caller -> dropped", want not in got, sorted(got))
    got, _st = run({"x/store.go": _STORE_GO, "x/svc.go": svc,
                    "other/alias.go": "package other\n\nvar First = (&x.Query{}).First\n"},
                   *_go_graph(3, 4, extra_files=("other/alias.go",)))
    check("C3 a var in ANOTHER package (directory) -> dropped", want not in got, sorted(got))
    got, _st = run({"x/store.go": _STORE_GO,
                    "x/svc.go": "package x\n\nfunc Load(q *Query) error {\n\tf := q.\n\t\tFirst\n\t_ = f\n"
                                "\treturn First()\n}\n"},
                   *_go_graph(3, 7))
    check("C4 (K10) a method value `q.\\n\\t\\tFirst` on a continuation line is a member, not a binding -> "
          "dropped", want not in got, sorted(got))


# ================================================================================================
# C — JS/TS + Rust
def _ts_graph(caller_sf, caller_line, call_line, method_sf="client.ts"):
    cid = caller_sf.replace("/", "_").rsplit(".", 1)[0]
    mid = method_sf.replace("/", "_").rsplit(".", 1)[0]
    nodes = [fnode(cid, caller_sf), node(cid + "_fetchit", "fetchIt()", caller_sf, caller_line),
             fnode(mid, method_sf), node(mid + "_client", "Client", method_sf, 1),
             node(mid + "_client_get", ".get()", method_sf, 2)]
    links = [link("contains", cid, cid + "_fetchit", caller_line),
             link("method", mid + "_client", mid + "_client_get", 2),
             link("calls", cid + "_fetchit", mid + "_client_get", call_line, "INFERRED")]
    return nodes, links


_CLIENT_TS = "export class Client {\n  get(url: string) { return url; }\n}\nconst d = new Client();\n"


def gate_c_ts_rust() -> None:
    print("\n-- C (K11, JS/TS + Rust): a value binding of the name keeps the engine's call --")
    want = ("fetchIt", "Client.get")
    call = "export function fetchIt(url: string) {\n  return get(url);\n}\n"
    got, _st = run({"client.ts": _CLIENT_TS + "export const get = d.get.bind(d);\n",
                    "caller.ts": "import { get } from './client';\n\n" + call}, *_ts_graph("caller.ts", 3, 4))
    check("T1 `import { get } from './client'` (a project module) keeps fetchIt -> Client.get", want in got,
          sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS + "export const get = d.get.bind(d);\n",
                    "caller.ts": "import {\n  get,\n} from './client';\n\n" + call}, *_ts_graph("caller.ts", 5, 6))
    check("T2 ...through a MULTI-LINE import", want in got, sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS + "export const get = d.get.bind(d);\n",
                    "caller.ts": "import { get } from '@org/client';\n\n" + call}, *_ts_graph("caller.ts", 3, 4))
    check("T3 a bare specifier (a workspace package) still keeps it when the METHOD's file exports a value "
          "`get`", want in got, sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS,
                    "caller.ts": "import { api } from './api';\n\nconst get = api.get.bind(api);\n\n" + call},
                   *_ts_graph("caller.ts", 5, 6))
    check("T4 a module-level `const get = api.get.bind(api)` in the caller", want in got, sorted(got))

    print("   controls (still dropped):")
    got, st = run({"cache.ts": "export class Cache {\n  join(a: string) { return a; }\n}\n",
                   "use.ts": "import { join } from 'path';\n\nexport function build(a: string) {\n  return join(a, 'x');\n}\n"},
                  [fnode("cache", "cache.ts"), node("cache_cache", "Cache", "cache.ts", 1),
                   node("cache_cache_join", ".join()", "cache.ts", 2), fnode("use", "use.ts"),
                   node("use_build", "build()", "use.ts", 3)],
                  [link("method", "cache_cache", "cache_cache_join", 2), link("contains", "use", "use_build", 3),
                   link("imports_from", "use", "path", 1),
                   link("calls", "use_build", "cache_cache_join", 4, "INFERRED")])
    check("TC1 the reviewer's control: `import { join } from 'path'` never reaches Cache.join -> dropped",
          ("build", "Cache.join") not in got and st.get("calls_filtered") == 1, (sorted(got), st))
    got, _st = run({"client.ts": _CLIENT_TS,
                    "caller.ts": "import {\n  get,\n} from 'axios';\n\n" + call}, *_ts_graph("caller.ts", 5, 6))
    check("TC2 a multi-line import from a bare package (axios) -> dropped", want not in got, sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS,
                    "caller.ts": "// get is re-exported elsewhere\nexport { get } from './client';\n\n" + call},
                   *_ts_graph("caller.ts", 4, 5))
    check("TC3 a re-export (`export { get } from`) binds nothing locally; a comment is not a binding -> dropped",
          want not in got, sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS,
                    "caller.ts": "import { get as g } from './client';\n\n" + call}, *_ts_graph("caller.ts", 3, 4))
    check("TC4 `import { get as g }` binds g, not get -> the bare get() stays dropped", want not in got, sorted(got))
    got, _st = run({"client.ts": _CLIENT_TS,
                    "caller.ts": "import.meta.hot?.accept()\nconst get = api.get.bind(api)\n\n" + call},
                   *_ts_graph("caller.ts", 4, 5))
    check("T5 `import.meta.hot` is not an import statement: the `const get = ...` after it is still read -> kept",
          want in got, sorted(got))

    # Rust
    api = "pub struct Api;\nimpl Api {\n    pub fn check(&self, n: i32) -> i32 { n }\n}\n"
    rnodes = [fnode("src_api", "src/api.rs"), node("src_api_api", "Api", "src/api.rs", 1),
              node("src_api_api_check", ".check()", "src/api.rs", 3), fnode("src_svc", "src/svc.rs"),
              node("src_svc_load", "load()", "src/svc.rs", 3)]
    got, _st = run({"src/api.rs": api,
                    "src/svc.rs": "use crate::api::Api;\n\npub fn load(a: &Api) -> i32 {\n    let check = Api::check;\n"
                                  "    check(a, 2)\n}\n"},
                   rnodes, [link("method", "src_api_api", "src_api_api_check", 3),
                            link("contains", "src_svc", "src_svc_load", 3),
                            link("calls", "src_svc_load", "src_api_api_check", 5, "INFERRED")])
    check("R1 Rust: `let check = Api::check; check(a, 2)` keeps load -> Api.check", ("load", "Api.check") in got,
          sorted(got))
    got, _st = run({"src/api.rs": api,
                    "src/svc.rs": "use crate::util::check;\n\npub fn load() -> i32 {\n    check(2)\n}\n"},
                   rnodes, [link("method", "src_api_api", "src_api_api_check", 3),
                            link("contains", "src_svc", "src_svc_load", 3),
                            link("calls", "src_svc_load", "src_api_api_check", 4, "INFERRED")])
    check("RC1 Rust: a `use`d item is a fn, never an inherent method -> dropped", ("load", "Api.check") not in got,
          sorted(got))


# ================================================================================================
# D — K10: a receiver call split after the dot
def gate_d_trailing_dot() -> None:
    print("\n-- D (K10, map-time): a call on the continuation line of `recv.` is qualified, not bare --")
    cs = GX._call_sites
    check("unchanged: `q.First()` is qualified by q; `First()` with no previous line is bare",
          cs("\treturn q.First()", "First") == [("qualified", "q")] and cs("\t\tFirst()", "First") == [("bare", None)],
          (cs("\treturn q.First()", "First"), cs("\t\tFirst()", "First")))
    check("gofmt chain: `\\treturn q.` then `\\t\\tFirst()` -> qualified by q",
          cs("\t\tFirst()", "First", prev="\treturn q.") == [("qualified", "q")],
          cs("\t\tFirst()", "First", prev="\treturn q."))
    check("`\\t\\tWhere(\"x\").` then `First()` -> qualified (no simple qualifier)",
          cs("\t\tFirst()", "First", prev="\t\tWhere(\"x\").") == [("qualified", None)],
          cs("\t\tFirst()", "First", prev="\t\tWhere(\"x\")."))
    check("`?.` / `::` / `->` endings are receivers too; Python's `obj. \\` (explicit continuation) as well",
          cs("  check(2)", "check", prev="  return api?.") == [("qualified", "api")]
          and cs("    get()", "get", prev="    Foo::") == [("qualified", "Foo")]
          and cs("    helper(2))", "helper", prev="    return (obj. \\") == [("qualified", "obj")],
          (cs("  check(2)", "check", prev="  return api?."), cs("    get()", "get", prev="    Foo::"),
           cs("    helper(2))", "helper", prev="    return (obj. \\")))
    check("never a float literal `1.`, an ellipsis `...` or a range `..`",
          cs("\tFirst()", "First", prev="\tx := 1.") == [("bare", None)]
          and cs("    check()", "check", prev="        ...") == [("bare", None)]
          and cs("    check()", "check", prev="    let r = 0..") == [("bare", None)])
    check("a name that is NOT first on its line ignores the previous line (`x := First()`)",
          cs("\tx := First()", "First", prev="\treturn q.") == [("bare", None)])
    check("the previous line may be a callable (read only when needed)",
          cs("\t\tFirst()", "First", prev=lambda: "\treturn q.") == [("qualified", "q")])
    pl = GX._prev_code_line
    lines = ["func Load(q *Query) error {", "\treturn q. // the query", "\t\t// the row", "", "\t\tFirst()", "}"]
    check("_prev_code_line skips comment-only and blank lines and cuts a trailing // comment",
          pl(lines, 4, False) == "\treturn q.", pl(lines, 4, False))
    check("...but never cuts a `//` inside a string", pl(['\tu := "http://x".', "\t\tTrim()"], 1, False)
          == '\tu := "http://x".', pl(['\tu := "http://x".', "\t\tTrim()"], 1, False))
    check("Python: `#` comments are skipped/cut; the first line has no previous line",
          pl(["        return (obj.  # note", "            # more", "            helper(2))"], 2, True)
          == "        return (obj." and pl(["x()"], 0, True) is None)


def gate_d2_trailing_dot_mapped() -> None:
    print("\n-- D2 (K10, map-time): the caller-body rescue and the go-selector rule read continuation lines --")
    files = {"pkg/__init__.py": "", "pkg/store.py": "class Store:\n    def helper(self, x):\n        return x\n",
             "pkg/app.py": "from pkg.store import Store\n\n\ndef run(obj):\n    helper(1)\n    return (obj.\n"
                           "            helper(2))\n\n\ndef only():\n    return helper(3)\n"}
    nodes = [fnode("pkg_store", "pkg/store.py"), node("pkg_store_store", "Store", "pkg/store.py", 1),
             node("pkg_store_store_helper", ".helper()", "pkg/store.py", 2), fnode("pkg_app", "pkg/app.py"),
             node("pkg_app_run", "run()", "pkg/app.py", 4), node("pkg_app_only", "only()", "pkg/app.py", 10)]
    links = [link("contains", "pkg_store", "pkg_store_store", 1),
             link("method", "pkg_store_store", "pkg_store_store_helper", 2),
             link("contains", "pkg_app", "pkg_app_run", 4), link("contains", "pkg_app", "pkg_app_only", 10),
             link("calls", "pkg_app_run", "pkg_store_store_helper", 5, "INFERRED"),
             link("calls", "pkg_app_only", "pkg_store_store_helper", 11, "INFERRED")]
    got, st = run(files, nodes, links)
    check("rescue: run()'s recorded bare `helper(1)` is kept by `(obj.\\n    helper(2))` in its body",
          ("run", "Store.helper") in got, sorted(got))
    check("...only() (a bare `helper(3)` and nothing else) still drops", ("only", "Store.helper") not in got
          and st.get("calls_filtered") == 1, (sorted(got), st))
    links2 = [dict(x) for x in links[:-2]] + [link("calls", "pkg_app_run", "pkg_store_store_helper", 7, "INFERRED")]
    files2 = dict(files, **{"pkg/app.py": files["pkg/app.py"].replace("    helper(1)\n", "    pass\n")})
    got, _st = run(files2, nodes, links2)
    check("a recorded line that IS the continuation (`    helper(2))` after `(obj.`) is a receiver call -> kept",
          ("run", "Store.helper") in got, sorted(got))

    # prose is not code: a docstring sentence ending in `.` never turns the next docstring line into a
    # receiver call that "rescues" a bare call
    files3 = dict(files, **{"pkg/app.py": "def only():\n    \"\"\"Compute the value.\n    helper() must not be called.\n"
                                          "    \"\"\"\n    return helper(3)\n"})
    nodes3 = nodes[:4] + [node("pkg_app_only", "only()", "pkg/app.py", 1)]
    links3 = links[:2] + [link("contains", "pkg_app", "pkg_app_only", 1),
                          link("calls", "pkg_app_only", "pkg_store_store_helper", 5, "INFERRED")]
    got, _st = run(files3, nodes3, links3)
    check("a docstring line `helper() ...` after `\"\"\"Compute the value.` is prose, not a receiver call -> "
          "only() -> Store.helper still dropped", ("only", "Store.helper") not in got, sorted(got))

    # the go-selector direction: a continuation `q.\n\t\tNew()` is a selector, never the package's own New
    gfiles = {"y/n.go": "package y\n\nfunc New() int { return 1 }\n",
              "y/u.go": "package y\n\nfunc Use(q *T) int {\n\t_ = util.New()\n\treturn q.\n\t\tNew()\n}\n"}
    gnodes = [fnode("y_n", "y/n.go"), node("y_n_new", "New()", "y/n.go", 3), fnode("y_u", "y/u.go"),
              node("y_u_use", "Use()", "y/u.go", 3)]
    glinks = [link("contains", "y_n", "y_n_new", 3), link("contains", "y_u", "y_u_use", 3),
              link("calls", "y_u_use", "y_n_new", 4, "INFERRED")]
    got, _st = run(gfiles, gnodes, glinks)
    check("Go: `util.New()` bound to the package's own New is not 'rescued' by the selector `q.\\n\\t\\tNew()` "
          "(it was read as a bare New())", ("Use", "New") not in got, sorted(got))


# ================================================================================================
# E — the switch
def gate_e_switch() -> None:
    print("\n-- E: DEEPMAP_FALSE_CALL_FILTER=0 keeps the engine's calls; a pure map (no root) is unchanged --")
    files = {"store.py": "class Cache:\n    def join(self, a):\n        return a\n",
             "use.py": "from os.path import join\n\n\ndef build(a):\n    return join(a, 'x')\n"}
    nodes = [fnode("store", "store.py"), node("store_cache", "Cache", "store.py", 1),
             node("store_cache_join", ".join()", "store.py", 2), fnode("use", "use.py"), node("use_build", "build()", "use.py", 4)]
    links = [link("contains", "store", "store_cache", 1), link("method", "store_cache", "store_cache_join", 2),
             link("contains", "use", "use_build", 4), link("calls", "use_build", "store_cache_join", 5, "INFERRED")]
    got, st = run(files, nodes, links, DEEPMAP_FALSE_CALL_FILTER="0")
    check("filter off: the (false) external-import call is kept as the engine wrote it",
          ("build", "Cache.join") in got and not st.get("calls_filtered"), (sorted(got), st))
    with _env(**{f: None for f in _FLAGS}):
        _n, pure, _i = GX.map_graph(json.loads(json.dumps({"nodes": nodes, "links": links})))
    check("pure mode (no source root): the bare-method rule needs source, so the call is kept",
          ("use_build", "Cache.join") in set(pure), pure)


# ================================================================================================
# F — the REAL engine
_REAL = {
    "pyalias": {"pkg/__init__.py": "", "pkg/backoff.py": _BACKOFF,
                "pkg/server.py": "from pkg.backoff import check\n\n\ndef handle():\n    if check():\n        return 1\n"
                                 "    return 0\n"},
    "pyinst": {"inst.py": "class Client:\n    def get(self, url):\n        return url\n\n\n_default = Client()\n"
                          "get = _default.get\n",
               "caller.py": "from inst import get\n\n\ndef fetch(url):\n    return get(url)\n"},
    "pyreexp": {"pkg/__init__.py": "from pkg.client import Client\n\n_d = Client()\nget = _d.get\n",
                "pkg/client.py": "class Client:\n    def get(self, url):\n        return url\n",
                "app.py": "from pkg import get\n\n\ndef fetch(url):\n    return get(url)\n"},
    "goalias": {"go.mod": "module example.com/x\n\ngo 1.21\n",
                "store.go": _STORE_GO + "\nvar defaultQuery = &Query{}\n\nvar First = defaultQuery.First\n",
                "svc.go": "package x\n\nfunc Load() error {\n\treturn First()\n}\n"},
    "pyext": {"store.py": "class Cache:\n    def join(self, a):\n        return a\n",
              "use.py": "from os.path import join\n\n\ndef build(a):\n    return join(a, 'x')\n"},
    "tsext": {"cache.ts": "export class Cache {\n  join(a: string) { return a; }\n}\n",
              "use.ts": "import { join } from 'path';\n\nexport function build(a: string) {\n  return join(a, 'x');\n}\n"},
    "pytrail": {"pkg/__init__.py": "", "pkg/store.py": "class Store:\n    def helper(self, x):\n        return x\n",
                "pkg/app.py": "from pkg.store import Store\n\n\ndef run(obj):\n    helper(1)\n    return (obj.\n"
                              "            helper(2))\n\n\ndef only():\n    return helper(3)\n"},
}


def gate_f_real_engine() -> None:
    print("\n-- F: the REAL installed engine on the reviewers' repro trees --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    print(f"  (graphify {GX.graphify_version()})")
    res = {}
    with _env(DEEPMAP_HOME=str(_TMP / "h"), **{f: None for f in _FLAGS}):
        for name, files in _REAL.items():
            root = _TMP / "real" / name
            for rel, text in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(text.encode("utf-8"))
            r, rep = GX.extract_with_report(root)
            if r is None:
                check(f"REAL {name}: the engine produced a graph", False, rep.get("cause"))
                continue
            k = {n["key"]: n["name"] for n in r[0]}
            res[name] = {(k.get(s, s), k.get(t, t)) for s, t in r[1]}
    if len(res) != len(_REAL):
        return
    check("REAL pyalias: handle -> AuthBackoff.check kept (the imported bound-method alias)",
          ("handle", "AuthBackoff.check") in res["pyalias"], sorted(res["pyalias"]))
    check("REAL pyinst: fetch -> Client.get kept", ("fetch", "Client.get") in res["pyinst"], sorted(res["pyinst"]))
    check("REAL pyreexp: fetch -> Client.get kept (the alias in the package __init__)",
          ("fetch", "Client.get") in res["pyreexp"], sorted(res["pyreexp"]))
    check("REAL goalias: Load -> Query.First kept (`var First = defaultQuery.First`)",
          ("Load", "Query.First") in res["goalias"], sorted(res["goalias"]))
    check("REAL pyext / tsext: an external `join` never reaches Cache.join (still dropped)",
          ("build", "Cache.join") not in res["pyext"] and ("build", "Cache.join") not in res["tsext"],
          (sorted(res["pyext"]), sorted(res["tsext"])))
    check("REAL pytrail (K10): run -> Store.helper kept by `(obj.\\n helper(2))`; only -> Store.helper dropped",
          ("run", "Store.helper") in res["pytrail"] and ("only", "Store.helper") not in res["pytrail"],
          sorted(res["pytrail"]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    for f in _FLAGS:
        os.environ.pop(f, None)
    print("extraction_alias_r9b_gates — K11 bound-method aliases + K10 trailing-dot chains (map-time filter)")
    try:
        for g in (gate_a_python, gate_b_go, gate_c_ts_rust, gate_d_trailing_dot, gate_d2_trailing_dot_mapped,
                  gate_e_switch, gate_f_real_engine):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, repr(e))
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} checks passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
