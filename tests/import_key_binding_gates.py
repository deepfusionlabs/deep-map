#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for how edge_resolve binds a KEY-FORM :IMPORTS target (round 3, W7). No Neo4j, no network.

Round 3 made graphify_extract.map_graph hand on the engine's RESOLVED import target as a node KEY
(default ON; `DEEPMAP_RESOLVED_IMPORTS=0` restores bare names), and edge_resolve.resolve_imports binds a
target that is a node key of this sync to exactly that node, EXTRACTED — which removes the N^2 AMBIGUOUS
fan-out of N same-named `index` modules. That property is kept and locked here (K1).

The defect this file adds a gate for (found live on graphify 0.8.39): graphify gives an import it can NOT
resolve — a bare package specifier such as Node's `util` / `events` or the npm `config` package — the
sanitized specifier as its target id, and a ROOT-level file gets the same unprefixed id (`util.py` ->
`util`, `config.py` -> `config`). So in a repo with a root `util.py` and a JS front-end,
`import util from 'util'` "resolved" to the Python module, map_graph emitted it in key form, and the
consumer persisted web/app.js -[:IMPORTS {confidence: EXTRACTED}]-> util.py: a fabricated cross-language
edge at the firmest grade (the #61 class, one layer over). Without the unrelated root file the same import
is a dangling external and is dropped. graphify never resolves an import ACROSS language families, so a
key-form target in another known family is an id collision, not a resolution.

  K1  a same-family node-key target binds EXACTLY that node, EXTRACTED, with no fan-out — including the
      families that legitimately mix extensions (.vue/.tsx -> .ts, .kt -> .java, .razor -> .cs,
      .erb -> .rb, .pyx -> .py, .m -> .h).
  K2  a node-key target in ANOTHER known language family is dropped — never a cross-language edge.
  K3  an importer or target of UNKNOWN family keeps the exact binding (conservative, as same_family).
  K4  the NAME rule is unchanged: family-filtered last-segment lookup, >1 candidate -> AMBIGUOUS;
      self-imports and malformed tuples dropped; 2-tuples and lists accepted (normalize_imports).
  K5  through map_graph on a graphify-shaped graph: the collision yields no cross-family row, and the
      JS importer's rows are the SAME whether or not the unrelated root Python file exists; in the
      opt-out name mode no cross-family row either.
  K6  through the real sync.push_to_neo4j (recording driver): no :IMPORTS statement row crosses a
      language family; the genuine same-family import persists once, EXTRACTED.
  K7  REAL engine (skipped when graphify is absent): the field shape end to end.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import edge_resolve as ER        # noqa: E402
import graphify_extract as GX    # noqa: E402

_PASS = _FAIL = 0
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:600]}"))


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


def _n(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": name}


def _rows(nodes, imports, live=None):
    keys = [n["key"] for n in nodes]
    return ER.resolve_imports(imports, ER.Resolver(nodes), keys if live is None else live, set(keys))


def _pairs(rows) -> dict:
    return {(r[1], r[2]): r[3] for r in rows}


# =====================================================================================================
# K1 — the W5 contract: a same-family node key binds exactly, EXTRACTED, no fan-out
# =====================================================================================================
def gate_k1_same_family_exact() -> None:
    print("\n-- K1: a same-family node-KEY target binds exactly that node, EXTRACTED --")
    n = 5
    nodes = [_n(f"web_p{i}_index", "Module", "index", f"web/p{i}/index.ts") for i in range(n)]
    imports = [(f"web_p{i}_index", f"web_p{(i + 1) % n}_index") for i in range(n)]
    got = _pairs(_rows(nodes, imports))
    want = {(f"web_p{i}_index", f"web_p{(i + 1) % n}_index"): "EXTRACTED" for i in range(n)}
    check(f"{n} `index` modules, one key-form import each -> exactly {n} EXTRACTED rows (no fan-out)",
          got == want, got)

    mixes = [  # (importer file, target file) pairs of ONE family with different extensions
        ("web/App.vue", "web/store.ts"), ("web/Page.tsx", "web/api.js"), ("jvm/App.kt", "jvm/Repo.java"),
        ("ui/Index.razor", "ui/Service.cs"), ("app/views/show.html.erb", "app/models/order.rb"),
        ("ext/fast.pyx", "ext/slow.py"), ("objc/View.m", "objc/View.h"), ("c/main.cpp", "c/util.h"),
        ("pas/Main.dpr", "pas/Unit1.pas"),
    ]
    bad = []
    for src_f, dst_f in mixes:
        ns = [_n("s_key", "Module", "s", src_f), _n("d_key", "Module", "d", dst_f)]
        p = _pairs(_rows(ns, [("s_key", "d_key")]))
        if p != {("s_key", "d_key"): "EXTRACTED"}:
            bad.append((src_f, dst_f, p))
    check("a key-form import between two extensions of ONE family still binds exactly (not over-filtered)",
          not bad, bad)

    ns = [_n("web_app", "Module", "app", "web/app.ts"), _n("web_lib", "Module", "lib", "web/lib.ts"),
          _n("web_lib_helper", "Function", "helper", "web/lib.ts", 3)]
    p = _pairs(_rows(ns, [("web_app", "web_lib"), ("web_app", "web_lib_helper")]))
    check("a key-form import of a SYMBOL (TS named import) binds that symbol, alongside its module",
          p == {("web_app", "web_lib"): "EXTRACTED", ("web_app", "web_lib_helper"): "EXTRACTED"}, p)


# =====================================================================================================
# K2 — a node key of ANOTHER family is an engine id collision -> dropped
# =====================================================================================================
def _collision_nodes():
    return [
        _n("util", "Module", "util", "util.py"), _n("util_format", "Function", "format", "util.py", 1),
        _n("events", "Module", "events", "events.py"),
        _n("config", "Module", "config", "config.py"),
        _n("index", "Module", "index", "index.js"),
        _n("web_app", "Module", "app", "web/app.js"),
        _n("web_config", "Module", "config", "web/config.js"),
        _n("tools_build", "Module", "build", "tools/build.py"),
        _n("svc_main", "Module", "main", "svc/main.go"),
    ]


def gate_k2_cross_family_dropped() -> None:
    print("\n-- K2: a node key in ANOTHER language family is not a resolution -> dropped --")
    nodes = _collision_nodes()
    fam = {n["key"]: ER.lang_family(n["source_file"]) for n in nodes}
    imports = [("web_app", "util"), ("web_app", "events"), ("web_app", "config"),
               ("tools_build", "index"), ("svc_main", "util_format")]
    rows = _rows(nodes, imports)
    cross = [(r[1], r[2], r[3]) for r in rows if fam.get(r[1]) and fam.get(r[2]) and fam[r[1]] != fam[r[2]]]
    check("JS `import util from 'util'` / 'events' / 'config' never bind the root Python modules",
          not {("web_app", "util"), ("web_app", "events"), ("web_app", "config")} & set(_pairs(rows)),
          _pairs(rows))
    check("a Python import never binds the root JS `index` module, a Go file never binds a Python function",
          not {("tools_build", "index"), ("svc_main", "util_format")} & set(_pairs(rows)), _pairs(rows))
    check("no :IMPORTS row crosses a known language family", not cross, cross)
    check("...and the collision is DROPPED, not re-resolved as a name (web/app.js -> web/config.js would "
          "bind only because an unrelated root config.py exists)", rows == [], _pairs(rows))


# =====================================================================================================
# K3 — unknown family on either side keeps the exact binding
# =====================================================================================================
def gate_k3_unknown_family_kept() -> None:
    print("\n-- K3: an importer or target of UNKNOWN language family keeps the exact binding --")
    nodes = [_n("gpu_kernel", "Module", "kernel", "gpu/kernel.hlsl"),
             _n("config", "Module", "config", "config.py"),
             _n("web_app", "Module", "app", "web/app.js"),
             _n("build_rules", "Module", "rules", "build/Rules")]
    p = _pairs(_rows(nodes, [("gpu_kernel", "config"), ("web_app", "build_rules")]))
    check("unknown-family importer (.hlsl) -> Python key: kept, EXTRACTED",
          p.get(("gpu_kernel", "config")) == "EXTRACTED", p)
    check("JS importer -> extension-less target: kept, EXTRACTED",
          p.get(("web_app", "build_rules")) == "EXTRACTED", p)


# =====================================================================================================
# K4 — the NAME rule is unchanged
# =====================================================================================================
def gate_k4_name_rule_unchanged() -> None:
    print("\n-- K4: a NAME-form target keeps the family-filtered name rule --")
    nodes = [_n("web/app.ts::app:1", "Module", "app", "web/app.ts"),
             _n("web/index.ts::index:1", "Module", "index", "web/index.ts"),
             _n("web/lib/index.ts::index:1", "Module", "index", "web/lib/index.ts"),
             _n("tools/index.py::index:1", "Module", "index", "tools/index.py"),
             _n("tools/run.py::run:1", "Module", "run", "tools/run.py")]
    p = _pairs(_rows(nodes, [("web/app.ts::app:1", "index")]))
    check("TS name `index` -> the two TS `index` modules, AMBIGUOUS, never the Python one", p == {
        ("web/app.ts::app:1", "web/index.ts::index:1"): "AMBIGUOUS",
        ("web/app.ts::app:1", "web/lib/index.ts::index:1"): "AMBIGUOUS"}, p)
    p = _pairs(_rows(nodes, [("tools/run.py", "pkg.index")]))
    check("ast shape (file relpath, dotted module) -> the module's last segment, single -> EXTRACTED",
          p == {("tools/run.py::run:1", "tools/index.py::index:1"): "EXTRACTED"}, p)
    p = _pairs(_rows(nodes, [("web/app.ts::app:1", "web/app.ts::app:1"), ("web/app.ts::app:1", "app")]))
    check("a self-import (by key or by name) is dropped", p == {}, p)
    p = _pairs(_rows(nodes, [["web/app.ts::app:1", "web/index.ts::index:1"], ("web/app.ts::app:1",),
                             None, 7, ("web/app.ts::app:1", 3), (None, "index")]))
    check("a list 2-tuple is accepted; malformed entries are dropped without raising",
          p == {("web/app.ts::app:1", "web/index.ts::index:1"): "EXTRACTED"}, p)
    live_only = [n["key"] for n in nodes if n["key"] != "web/app.ts::app:1"]
    p = _pairs(_rows(nodes, [("web/app.ts::app:1", "web/index.ts::index:1")], live=live_only))
    check("an importer outside the live scope gets no row (subdir sync)", p == {}, p)


# =====================================================================================================
# K5 — through map_graph (graphify shape): the collision, the invariance, the opt-out
# =====================================================================================================
def _graphify_graph(with_root_py: bool) -> dict:
    """The graphify 0.8.39 shape of the field case: web/app.js imports the Node builtin `util`, the npm
    `config` package and its sibling `./local`. graphify ids an unresolved specifier by the sanitized
    specifier (`util`, `config`) — the SAME id a root-level util.py / config.py gets — and leaves the
    link dangling when no such node exists."""
    nodes = [
        {"id": "web_app", "label": "app.js", "file_type": "code", "source_file": "web/app.js",
         "source_location": "L1"},
        {"id": "web_app_run", "label": "run()", "file_type": "code", "source_file": "web/app.js",
         "source_location": "L4"},
        {"id": "web_local", "label": "local.js", "file_type": "code", "source_file": "web/local.js",
         "source_location": "L1"},
        {"id": "web_local_local", "label": "local()", "file_type": "code", "source_file": "web/local.js",
         "source_location": "L1"},
        {"id": "web_config", "label": "config.js", "file_type": "code", "source_file": "web/config.js",
         "source_location": "L1"},
    ]
    links = [
        {"source": "web_app", "target": "web_app_run", "relation": "contains"},
        {"source": "web_local", "target": "web_local_local", "relation": "contains"},
        {"source": "web_app", "target": "util", "relation": "imports_from"},
        {"source": "web_app", "target": "config", "relation": "imports_from"},
        {"source": "web_app", "target": "web_local", "relation": "imports_from"},
        {"source": "web_app", "target": "web_local_local", "relation": "imports"},
    ]
    if with_root_py:
        for stem in ("util", "config"):
            nodes.append({"id": stem, "label": f"{stem}.py", "file_type": "code",
                          "source_file": f"{stem}.py", "source_location": "L1"})
            nodes.append({"id": f"{stem}_load", "label": "load()", "file_type": "code",
                          "source_file": f"{stem}.py", "source_location": "L1"})
            links.append({"source": stem, "target": f"{stem}_load", "relation": "contains"})
    return {"nodes": nodes, "links": links}


def _app_rows(graph, resolved=None) -> dict:
    with _env(DEEPMAP_RESOLVED_IMPORTS=resolved):
        nodes, _calls, imports = GX.map_graph(graph)
    by = {n["key"]: n["source_file"] for n in nodes}
    rows = _rows(nodes, imports)
    return {(by[r[1]], by[r[2]], r[3]) for r in rows if by.get(r[1]) == "web/app.js"}, imports


def gate_k5_through_map_graph() -> None:
    print("\n-- K5: through map_graph — the engine's id collision never becomes a cross-language edge --")
    with_py, imports = _app_rows(_graphify_graph(True))
    without_py, _ = _app_rows(_graphify_graph(False))
    check("precondition: map_graph hands on the colliding ids in KEY form (the producer side of the bug)",
          ("web_app", "util") in imports and ("web_app", "config") in imports, imports)
    want = {("web/app.js", "web/local.js", "EXTRACTED")}
    check("with a root util.py / config.py: web/app.js imports only its real sibling (module + symbol), "
          "EXTRACTED", with_py == want, sorted(with_py))
    check("web/app.js's :IMPORTS are IDENTICAL with and without the unrelated root Python files",
          with_py == without_py, (sorted(with_py), sorted(without_py)))
    opt_out, _ = _app_rows(_graphify_graph(True), resolved="0")
    check("opt-out name mode (DEEPMAP_RESOLVED_IMPORTS=0): still no row into a Python module",
          not {r for r in opt_out if not r[1].endswith(".js")}, sorted(opt_out))


# =====================================================================================================
# K6 — through the real sync.push_to_neo4j with a recording driver
# =====================================================================================================
_IMPORTS_MERGE = re.compile(r"MERGE \((a|b)\)-\[r:IMPORTS\]->\((a|b)\)")


class _Rec:
    def __init__(self):
        self.imports: list = []

    def run(self, cypher, **params):
        m = _IMPORTS_MERGE.search(cypher)
        if m and "UNWIND" in cypher:
            src, dst = m.group(1), m.group(2)
            for row in params.get("rows", ()):
                self.imports.append((row[src], row[dst], row.get("c")))


class _Tx:
    def __init__(self, rec):
        self.rec = rec

    def run(self, cypher, **params):
        self.rec.run(cypher, **params)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sess(_Tx):
    def begin_transaction(self, *a, **k):
        return _Tx(self.rec)

    def execute_write(self, fn, *a, **k):
        return fn(_Tx(self.rec), *a, **k)

    write_transaction = execute_write


class _Drv:
    def __init__(self, rec):
        self.rec = rec

    def session(self, *a, **k):
        return _Sess(self.rec)

    def close(self):
        pass


def gate_k6_push_to_neo4j() -> None:
    print("\n-- K6: the real sync.push_to_neo4j persists no cross-language :IMPORTS --")
    try:
        spec = importlib.util.spec_from_file_location("dm_sync_w7_import_key", BUILD / "sync.py")
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
    except Exception as e:  # noqa: BLE001
        check("sync.py imports", False, f"{type(e).__name__}: {e}")
        return
    with _env(DEEPMAP_RESOLVED_IMPORTS=None):
        nodes, calls, imports = GX.map_graph(_graphify_graph(True))
    rec = _Rec()
    S.push_to_neo4j(_Drv(rec), nodes, calls, imports, [])
    by = {n["key"]: n["source_file"] for n in nodes}
    persisted = {(by.get(a), by.get(b), c) for a, b, c in rec.imports}
    cross = {p for p in persisted if ER.lang_family(p[0]) != ER.lang_family(p[1])}
    check("no persisted :IMPORTS row crosses a language family", not cross, sorted(cross))
    check("the genuine sibling import persists, EXTRACTED",
          ("web/app.js", "web/local.js", "EXTRACTED") in persisted, sorted(persisted))
    check("each persisted :IMPORTS pair is written once", len(rec.imports) == len(set(rec.imports)),
          rec.imports)


# =====================================================================================================
# K7 — REAL engine
# =====================================================================================================
_FIELD = {
    "util.py": "def format():\n    return 1\n",
    "events.py": "class EventEmitter:\n    def emit(self):\n        return 1\n",
    "config.py": "SETTING = 1\n\ndef load_config():\n    return SETTING\n",
    "web/app.js": ("import util from 'util';\nimport { EventEmitter } from 'events';\n"
                   "import config from 'config';\nimport { local } from './local';\n"
                   "export function run() { return util.format('x') + config.get('y') + local(); }\n"),
    "web/local.js": "export function local() { return 1; }\n",
    "web/config.js": "export const cfg = 1;\n",
    "api/app.py": "import config\nfrom api import helpers\n\ndef serve():\n    return helpers.h()\n",
    "api/__init__.py": "",
    "api/helpers.py": "def h():\n    return 1\n",
}


def _real_rows(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    res, report = GX.extract_with_report(root)
    if not res:
        return None, report
    nodes, _calls, imports = res
    by = {n["key"]: n["source_file"] for n in nodes}
    return {(by[r[1]], by[r[2]], r[3]) for r in _rows(nodes, imports)}, imports


def gate_k7_real_engine(base: Path) -> None:
    print("\n-- K7 REAL: the installed engine's id collision never reaches the graph --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine section skipped)")
        return
    rows, imports = _real_rows(base / "with_py", _FIELD)
    if rows is None:
        check("the real engine produced a graph", False, imports)
        return
    check("precondition: the engine emits the colliding ids for web/app.js (util / events / config)",
          {("web_app", "util"), ("web_app", "events"), ("web_app", "config")} & set(imports), imports)
    cross = {r for r in rows if ER.lang_family(r[0]) != ER.lang_family(r[1])}
    check("no :IMPORTS row crosses a language family (web/app.js never -> util.py / events.py / config.py)",
          not cross, sorted(cross))
    check("the genuine same-family imports survive, EXTRACTED",
          ("web/app.js", "web/local.js", "EXTRACTED") in rows, sorted(rows))
    js_only = {k: v for k, v in _FIELD.items() if k.startswith("web/")}
    rows_js, _ = _real_rows(base / "js_only", js_only)
    app = {r for r in (rows or ()) if r[0] == "web/app.js"}
    app_js = {r for r in (rows_js or ()) if r[0] == "web/app.js"}
    check("web/app.js's :IMPORTS are the same with and without the unrelated root Python files",
          rows_js is not None and app == app_js, (sorted(app), sorted(app_js or ())))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("DeepMap — key-form :IMPORTS binding gates (round 3, W7; no Neo4j)")
    print("=" * 78)
    home = Path(tempfile.mkdtemp(prefix="dmik_home_"))
    base = Path(tempfile.mkdtemp(prefix="dmik_"))
    try:
        with _env(DEEPMAP_HOME=str(home), DEEPMAP_RESOLVED_IMPORTS=None, DEEPMAP_PRECISE_CALLS=None,
                  DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_CASE_INSENSITIVE_EXT=None,
                  DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None, DEEPMAP_EXTRACTOR=None,
                  DEEPMAP_RECOGNIZERS=None):
            for gate in (gate_k1_same_family_exact, gate_k2_cross_family_dropped,
                         gate_k3_unknown_family_kept, gate_k4_name_rule_unchanged,
                         gate_k5_through_map_graph, gate_k6_push_to_neo4j):
                try:
                    gate()
                except Exception as exc:  # a crash is a RED result, not a harness error
                    import traceback
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
            try:
                gate_k7_real_engine(base)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check("gate_k7_real_engine ran without raising", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
