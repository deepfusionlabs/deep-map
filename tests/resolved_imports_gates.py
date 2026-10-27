#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the RESOLVED-IMPORT-KEY contract (round 3, W5 -> W7). No Neo4j, no network, no spend.

The defect: graphify resolves every JS/TS import to the exact file it names (`../b/index` -> the node
of web/b/index.ts), but map_graph threw that away and handed on the target's bare NAME. In a tree of
N `index.ts` modules every import of one of them became the name `index`, which edge_resolve then bound
to all N same-named modules as AMBIGUOUS — N^2 :IMPORTS overall (90,000 AMBIGUOUS rows at N=300 in the
round-2 measurement), and wrong ones.

The contract (graphify_extract module docstring), default ON, `DEEPMAP_RESOLVED_IMPORTS=0` restores the
old name tuples byte for byte:
  * each import is a distinct 2-tuple (src_key, target);
  * when the engine resolved the import to a node DeepMap keeps, `target` IS that node's key, and that
    key is always the `key` of a node in the SAME node list (sync drops any import whose key target a
    vendor/test/rescue filter removed — drop_dangling_import_keys);
  * otherwise `target` is the bare name, exactly as before; targets with no node at all are dropped;
  * the consumer (edge_resolve.resolve_imports) binds a node-key target to exactly that node, EXTRACTED.

Sections: A map_graph tuples; B the W7 consumer end to end (N^2 -> N, all EXTRACTED); C the opt-out;
D drop_dangling_import_keys; E the rescue filter; F sync._rebuild_code wiring + info counts; G REAL engine
(skipped when graphify is absent).
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

_PASS = _FAIL = 0
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:400]) if detail != '' else ''}")


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


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, BUILD / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


N = 6


def _index_graph() -> dict:
    """A graphify-shaped graph (the real engine's shape, see section G): N `index.ts` modules under
    web/p<i>/, each defining fn_<i>(), each importing the NEXT two modules (imports_from -> the file node)
    plus the next module's function by name (a TS named import -> `imports` -> the symbol node). Also an
    external import (`react`, no node at all -> dropped) and one to a node DeepMap does not keep (a
    README heading -> name form)."""
    nodes, links = [], []
    for i in range(N):
        nodes.append({"id": f"p{i}_index", "label": "index.ts", "file_type": "code",
                      "source_file": f"web/p{i}/index.ts", "source_location": "L1"})
        nodes.append({"id": f"p{i}_index_fn_{i}", "label": f"fn_{i}()", "file_type": "code",
                      "source_file": f"web/p{i}/index.ts", "source_location": "L3"})
        links.append({"source": f"p{i}_index", "target": f"p{i}_index_fn_{i}", "relation": "contains"})
    for i in range(N):
        a, b = (i + 1) % N, (i + 2) % N
        links.append({"source": f"p{i}_index", "target": f"p{a}_index", "relation": "imports_from"})
        links.append({"source": f"p{i}_index", "target": f"p{b}_index", "relation": "imports_from"})
        links.append({"source": f"p{i}_index", "target": f"p{a}_index_fn_{a}", "relation": "imports"})
        # the same import emitted twice (graphify repeats re-exports) -> still ONE tuple
        links.append({"source": f"p{i}_index", "target": f"p{a}_index", "relation": "imports_from"})
    links.append({"source": "p0_index", "target": "react", "relation": "imports_from"})
    nodes.append({"id": "readme_setup", "label": "Setup", "file_type": "document",
                  "source_file": "README.md", "source_location": "L1"})
    links.append({"source": "p0_index", "target": "readme_setup", "relation": "imports"})
    return {"nodes": nodes, "links": links}


def _expected_keyed() -> set:
    exp = set()
    for i in range(N):
        a, b = (i + 1) % N, (i + 2) % N
        exp |= {(f"p{i}_index", f"p{a}_index"), (f"p{i}_index", f"p{b}_index"),
                (f"p{i}_index", f"p{a}_index_fn_{a}")}
    exp.add(("p0_index", "Setup"))
    return exp


def _expected_named() -> set:
    exp = {(f"p{i}_index", "index") for i in range(N)}
    exp |= {(f"p{i}_index", f"fn_{(i + 1) % N}") for i in range(N)}
    exp.add(("p0_index", "Setup"))
    return exp


# ================================================================================================
def gate_a_map_graph() -> None:
    print("\n-- A: map_graph emits the engine-resolved node KEY for an import of a kept node --")
    with _env(DEEPMAP_RESOLVED_IMPORTS=None):
        stats: dict = {}
        try:
            nodes, calls, imports = GX.map_graph(_index_graph(), stats=stats)
        except TypeError as e:
            check("map_graph accepts stats=", False, e)
            return
    keys = {n["key"] for n in nodes}
    got = set(imports)
    check("imports are exactly the resolved keys (+ the name form for a node DeepMap does not keep)",
          got == _expected_keyed(), sorted(got ^ _expected_keyed())[:8])
    check("the imports list is distinct (a repeated engine edge collapses)", len(imports) == len(got),
          len(imports))
    check("every key-form target is the key of a node in the SAME returned list",
          all(t in keys for _s, t in imports if t != "Setup"), [t for _s, t in imports if t not in keys])
    check("an import of a symbol (TS named import) keys the symbol node, not its name",
          ("p0_index", "p1_index_fn_1") in got and ("p0_index", "fn_1") not in got)
    check("no import is the bare, ambiguous name 'index' any more",
          not any(t == "index" for _s, t in imports), sorted(t for _s, t in imports)[:6])
    check("an import with NO node at all (external 'react') is still dropped",
          not any(t in ("react",) for _s, t in imports))
    check("an import of a node DeepMap does not keep keeps the NAME form (unchanged rule)",
          ("p0_index", "Setup") in got)
    check("stats: resolved_imports on, imports_resolved / imports_by_name counted",
          stats.get("resolved_imports") is True and stats.get("imports_resolved") == 3 * N
          and stats.get("imports_by_name") == 1, {k: stats.get(k) for k in
                                                  ("resolved_imports", "imports_resolved", "imports_by_name")})
    check("calls are untouched by the import contract (none in this graph)", calls == [])
    cnt = getattr(GX, "import_key_counts", None)
    check("graphify_extract.import_key_counts(imports, nodes) exists and agrees with the stats",
          callable(cnt) and cnt(imports, nodes) == (3 * N, 1), cnt(imports, nodes) if callable(cnt) else None)


def gate_b_consumer() -> None:
    print("\n-- B: the W7 consumer binds a key-form import EXACTLY (N^2 AMBIGUOUS -> N EXTRACTED) --")
    try:
        ER = _load("dm_edge_resolve_ri", "edge_resolve.py")
    except Exception as e:  # noqa: BLE001
        check("edge_resolve imports", False, f"{type(e).__name__}: {e}")
        return
    resolve = getattr(ER, "resolve_imports", None)
    if not callable(resolve):
        check("edge_resolve.resolve_imports exists (W7)", False)
        return

    def rows_for(env_value):
        with _env(DEEPMAP_RESOLVED_IMPORTS=env_value):
            nodes, _c, imports = GX.map_graph(_index_graph())
        keys = [n["key"] for n in nodes]
        return resolve(imports, ER.Resolver(nodes), keys, keys)

    rows = rows_for(None)
    pairs = {(r[1], r[2]) for r in rows}
    exp = {p for p in _expected_keyed() if p[1] != "Setup"}
    check("default: every persisted :IMPORTS row is the exact engine-resolved pair",
          pairs == exp, sorted(pairs ^ exp)[:8])
    check("default: every row is EXTRACTED (no AMBIGUOUS fan-out)",
          bool(rows) and all(r[3] == "EXTRACTED" for r in rows), sorted({r[3] for r in rows}))
    check(f"default: {3 * N} rows for {N} modules x 3 imports (linear, not quadratic)", len(rows) == 3 * N,
          len(rows))
    old = rows_for("0")
    amb = [r for r in old if r[3] == "AMBIGUOUS"]
    check("opt-out (the old name form) reproduces the fan-out this fixes: every `index` import is "
          "AMBIGUOUS across all other index modules", len(amb) == N * (N - 1), (len(amb), len(old)))
    check("...and it binds modules the source never imported (wrong edges)",
          any((r[1], r[2]) not in exp for r in old))


def gate_c_opt_out() -> None:
    print("\n-- C: DEEPMAP_RESOLVED_IMPORTS=0 restores the name tuples byte for byte --")
    for off in ("0", "false", "no", "off", "OFF"):
        with _env(DEEPMAP_RESOLVED_IMPORTS=off):
            stats: dict = {}
            _n, _c, imports = GX.map_graph(_index_graph(), stats=stats)
        check(f"DEEPMAP_RESOLVED_IMPORTS={off}: name tuples exactly as before",
              set(imports) == _expected_named() and len(imports) == len(set(imports)),
              sorted(set(imports) ^ _expected_named())[:6])
    check("...and the stats say so", stats.get("resolved_imports") is False
          and stats.get("imports_resolved") == 0 and stats.get("imports_by_name") == len(imports), stats)
    check("resolved_imports_enabled(): default ON", getattr(GX, "resolved_imports_enabled", lambda: None)()
          is True)
    with _env(DEEPMAP_RESOLVED_IMPORTS="1"):
        on = GX.map_graph(_index_graph())[2]
    with _env(DEEPMAP_RESOLVED_IMPORTS=None):
        default = GX.map_graph(_index_graph())[2]
    check("=1 is the default", on == default)
    # the pre-round-3 fixture shape (tests/graphify_map_gates.py) under the opt-out: unchanged output
    legacy = {"nodes": [{"id": "api", "label": "api.py", "file_type": "code", "source_file": "api.py",
                         "source_location": "L1"},
                        {"id": "orders", "label": "orders.py", "file_type": "code",
                         "source_file": "orders.py", "source_location": "L1"},
                        {"id": "orders_orderservice", "label": "OrderService", "file_type": "code",
                         "source_file": "orders.py", "source_location": "L3"}],
              "links": [{"source": "orders", "target": "orders_orderservice", "relation": "contains"},
                        {"source": "api", "target": "orders", "relation": "imports_from"},
                        {"source": "api", "target": "orders_orderservice", "relation": "imports"}]}
    with _env(DEEPMAP_RESOLVED_IMPORTS="0"):
        check("opt-out on the legacy fixture: [('api','orders'), ('api','OrderService')]",
              GX.map_graph(legacy)[2] == [("api", "orders"), ("api", "OrderService")],
              GX.map_graph(legacy)[2])
    with _env(DEEPMAP_RESOLVED_IMPORTS=None):
        check("default on the legacy fixture: [('api','orders'), ('api','orders_orderservice')]",
              GX.map_graph(legacy)[2] == [("api", "orders"), ("api", "orders_orderservice")],
              GX.map_graph(legacy)[2])


def gate_d_dangling() -> None:
    print("\n-- D: drop_dangling_import_keys — a key target removed by a filter takes its import with it --")
    fn = getattr(GX, "drop_dangling_import_keys", None)
    check("graphify_extract.drop_dangling_import_keys exists", callable(fn))
    if not callable(fn):
        return
    before = [{"key": "a"}, {"key": "b"}, {"key": "vend"}]
    after = [{"key": "a"}, {"key": "b"}]
    imports = [("a", "b"), ("a", "vend"), ("b", "somename"), ("a", "react")]
    out = fn(imports, before, after)
    check("the import whose KEY target was filtered out is dropped", ("a", "vend") not in out, out)
    check("key imports to surviving nodes and every name-form import are kept",
          out == [("a", "b"), ("b", "somename"), ("a", "react")], out)
    same = fn(imports, before, before)
    check("nothing removed -> the SAME list object (byte-identity downstream)", same is imports)
    check("empty imports -> unchanged", fn([], before, after) == [])
    check("malformed input never raises", fn([("x",), None, ("a", "vend")], before, after) is not None)


def gate_e_rescue_filter() -> None:
    print("\n-- E: the D5 rescue filter drops a key-form import INTO a dropped (non-qualifying) node --")
    fn = getattr(GX, "_keep_rescued_only", None)
    noise = GX._engine_noise_dir()
    if not callable(fn) or noise is None:
        print("     (engine skip rule unavailable — section skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        nodes = [{"key": "src_app", "source_file": "src/app.py"},
                 {"key": "build_core", "source_file": "build/core.py"},
                 {"key": "build_scratch", "source_file": "build/scratch.py"}]
        imports = [("src_app", "build_core"), ("src_app", "build_scratch"), ("build_scratch", "src_app"),
                   ("src_app", "scratch")]
        skipped = {"build": ["build/core.py"]}
        with _env(DEEPMAP_RESOLVED_IMPORTS=None):
            (n2, _c, i2), dropped = fn((nodes, [], imports), root, skipped)
        check("the non-qualifying build/scratch.py node is dropped", dropped == 1
              and {n["key"] for n in n2} == {"src_app", "build_core"}, (dropped, n2))
        check("its outgoing import AND the key-form import into it go too; a name-form import stays",
              i2 == [("src_app", "build_core"), ("src_app", "scratch")], i2)
        with _env(DEEPMAP_RESOLVED_IMPORTS="0"):
            (_n3, _c3, i3), _d = fn((nodes, [], imports), root, skipped)
        check("opt-out: only outgoing imports of a dropped node go (the round-2 rule, byte for byte)",
              i3 == [("src_app", "build_core"), ("src_app", "build_scratch"), ("src_app", "scratch")], i3)


class _Rep(dict):
    pass


def gate_f_rebuild_code() -> None:
    print("\n-- F: sync._rebuild_code keeps the contract through the vendor filter + records the counts --")
    S = _load("dm_sync_ri", "sync.py")
    nodes = [{"key": "app", "label": "Module", "name": "app", "source_file": "src/app.ts", "start_line": 1,
              "body_summary": "", "documented": False},
             {"key": "util", "label": "Module", "name": "util", "source_file": "src/util.ts", "start_line": 1,
              "body_summary": "", "documented": False},
             {"key": "jq", "label": "Module", "name": "jquery", "source_file": "public/jquery.js",
              "start_line": 1, "body_summary": "", "documented": False}]
    imports = [("app", "util"), ("app", "jq"), ("app", "lodash_name")]
    report = {"cause": None, "resolved_imports": True, "imports_resolved": 2, "imports_by_name": 1,
              "graphify_version": "0.8.39", "graphify_supported": True, "rescue_blocked": []}
    real = S.graphify_extract.extract_with_report
    S.graphify_extract.extract_with_report = lambda root, **k: ((list(nodes), [], list(imports)), dict(report))
    try:
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_INCLUDE_VENDOR=None,
                                                       DEEPMAP_EXCLUDE_TESTS=None):
            ex = S._rebuild_code(Path(td))
    finally:
        S.graphify_extract.extract_with_report = real
    info = getattr(ex, "info", {})
    check("the vendored public/jquery.js node is filtered out (default vendor filter)",
          {n["key"] for n in ex[0]} == {"app", "util"}, [n["key"] for n in ex[0]])
    check("...and the key-form import INTO it is dropped with it (never looked up as a name)",
          ("app", "jq") not in ex[2], ex[2])
    check("the resolved import and the name-form import survive",
          ex[2] == [("app", "util"), ("app", "lodash_name")], ex[2])
    check("info records resolved_imports + the post-filter counts",
          info.get("resolved_imports") is True and info.get("imports_resolved") == 1
          and info.get("imports_by_name") == 1,
          {k: info.get(k) for k in ("resolved_imports", "imports_resolved", "imports_by_name")})
    check("info carries the engine version", info.get("graphify_version") == "0.8.39", info)
    # a legacy stub report (no import keys) -> no import keys in info, nothing breaks
    S.graphify_extract.extract_with_report = lambda root, **k: ((list(nodes[:2]), [], [("app", "util")]),
                                                                  {"cause": None})
    try:
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_EXTRACTOR=None):
            ex2 = S._rebuild_code(Path(td))
    finally:
        S.graphify_extract.extract_with_report = real
    check("a report without the import keys -> no import keys in info, imports untouched",
          "resolved_imports" not in ex2.info and ex2[2] == [("app", "util")], (ex2.info, ex2[2]))


def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)


def gate_g_real(home: Path) -> None:
    print("\n-- G REAL: the installed engine resolves imports to the exact file; the key form keeps it --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine section skipped)")
        return
    base = Path(tempfile.mkdtemp(prefix="dmri_"))
    try:
        root = base / "proj"
        files = {
            "pkg/__init__.py": "",
            "pkg/helpers.py": "def util_fn():\n    return 1\n",
            "pkg/core.py": "from .helpers import util_fn\nimport pkg.helpers\nimport os\n\n"
                           "def run():\n    return util_fn()\n",
        }
        for i in range(5):
            a, b = (i + 1) % 5, (i + 2) % 5
            files[f"web/m{i}/index.ts"] = (f"import {{ f{a} }} from '../m{a}/index';\n"
                                           f"import {{ f{b} }} from '../m{b}';\n"
                                           f"export function f{i}() {{ return f{a}() + f{b}(); }}\n")
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        with _env(DEEPMAP_RESOLVED_IMPORTS=None):
            res, report = GX.extract_with_report(root)
        if not res:
            check("the real engine produced a graph", False, report)
            return
        nodes, _calls, imports = res
        by_key = {n["key"]: n for n in nodes}
        files_of = {(by_key[s]["source_file"], by_key[t]["source_file"]) for s, t in imports
                    if s in by_key and t in by_key and by_key[t]["label"] == "Module"}
        want = set()
        for i in range(5):
            want |= {(f"web/m{i}/index.ts", f"web/m{(i + 1) % 5}/index.ts"),
                     (f"web/m{i}/index.ts", f"web/m{(i + 2) % 5}/index.ts")}
        check("each index.ts imports exactly the two index.ts files it names (resolved by the engine)",
              want <= files_of and not {p for p in files_of if p[0].startswith("web/")} - want,
              sorted(files_of ^ want)[:6])
        check("Python `from .helpers import` / `import pkg.helpers` -> the helpers module node KEY",
              ("pkg/core.py", "pkg/helpers.py") in files_of, sorted(files_of)[:6])
        check("no import target is the bare ambiguous name 'index'", not any(t == "index" for _s, t in imports))
        check("the report counts them", report.get("resolved_imports") is True
              and report.get("imports_resolved", 0) >= 11 and "imports_by_name" in report,
              {k: report.get(k) for k in ("resolved_imports", "imports_resolved", "imports_by_name")})
        try:
            ER = _load("dm_edge_resolve_ri_real", "edge_resolve.py")
            keys = [n["key"] for n in nodes]
            rows = ER.resolve_imports(imports, ER.Resolver(nodes), keys, keys)
            check("through edge_resolve: every :IMPORTS row EXTRACTED, none AMBIGUOUS",
                  bool(rows) and all(r[3] == "EXTRACTED" for r in rows), sorted({r[3] for r in rows}))
        except Exception as e:  # noqa: BLE001
            check("edge_resolve consumes the real imports", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("RESOLVED-IMPORT-KEY GATES (W5 -> W7 contract; no Neo4j)")
    print("=" * 78)
    home = Path(tempfile.mkdtemp(prefix="dmri_home_"))
    try:
        with _env(DEEPMAP_HOME=str(home), DEEPMAP_RESOLVED_IMPORTS=None, DEEPMAP_PRECISE_CALLS=None,
                  DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_CASE_INSENSITIVE_EXT=None,
                  DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None, DEEPMAP_EXTRACTOR=None):
            gate_a_map_graph()
            gate_b_consumer()
            gate_c_opt_out()
            gate_d_dangling()
            gate_e_rescue_filter()
            gate_f_rebuild_code()
            gate_g_real(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
