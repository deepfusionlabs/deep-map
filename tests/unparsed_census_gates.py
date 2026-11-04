#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: a source suffix this install cannot turn into nodes is left out of every coverage count, named with its
cause, and never mistaken for missing source. No Neo4j.

The field report (a .NET repo, 2026-11): 12 .sql files, the rest of the graph healthy, verdict DEGRADED with
"LOW COVERAGE for SQL: 12 file(s) on disk but 0 extracted". graphify parses .sql with tree_sitter_sql and .dm with
tree_sitter_dm, both in graphifyy's optional extras, which DeepMap does not install; and no graphify release DeepMap
supports parses .pl .pm .r .pyi .cjs at all. Counting such files as source made a healthy graph read DEGRADED on
every sync.

ONE rule decides it (design .ai/design/2026-11/sql/design.md sections 5.1 and 6; KL-architecture:002):
graphify_extract.not_parsed_here() -> {suffix: (cause, package, detail)}:
  * an OPTIONAL_PARSERS suffix other than .sql whose grammar is not usable here: "package" (find_spec finds nothing)
    or "unusable" (importable, but tree_sitter.Language(mod.language()) fails; the error is the detail); a probe
    that raises counts the files, as before;
  * .sql only while DeepMap's own SQL reader is off (DEEPMAP_SQL_DECLARATIONS) and the engine has no usable SQL
    grammar: "off" (detail "default" or "env": how it is off);
  * every ENGINE_UNPARSED_EXTS suffix (.pyi .cjs .pl .pm .r): "no-parser".
The census (verdict._uncounted_exts) leaves those files out of every counter: the languages, the conventional dirs,
the engine-skipped / unindexed directory findings and the \\\\?\\ long-path walk. Coverage names them in
"not_parsed" {suffix: {"files", "cause", "package", "detail"}} (informational, never a reason), format_coverage gives
each a line with its cause, and with 0 nodes the FAILED reason names the cause instead of "is this the right path?".

Sections: A the field case (reader off) and the reader on, the "off" line's remedy following the release's default
(set the variable to 1 while the default is off, whatever off value was set; unset it only while the default is on);
B reader off + a usable engine SQL grammar (density over :Class / :Function only, the switch named, the advice worded
on whether the files declare anything); C .dm absent / usable / unusable; D the real engine (not-installed
results are not failed files); E the static lists and the rule's shape; E2 the engine-derived OPTIONAL_PARSERS lock;
E3 the section 8 lock (every SOURCE_CODE_EXTS suffix is a decided case; ENGINE_UNPARSED_EXTS is exactly what no
in-range engine dispatches); F an unrelated language still flagged; G a raising probe counts; H the probe runs once
per process; I / J / K conventional dirs, engine-skipped / unindexed dirs, the long-path walk; L Perl-only and
SQL-only repos FAILED with their cause (one "so"); M malformed input, and a 0.6.3 manifest's "parser_missing" block
printed with its 0.6.3 line; N 0 nodes beside other source the census cannot see;
O .pl / .r / .pyi / .cjs beside healthy code read READY with a line each.

Hermetic: a temp DEEPMAP_HOME; importlib.util.find_spec is stubbed for the two grammar modules only (every other
name is passed through) and graphify_extract._load_grammar (the load probe) where a section needs a usable or broken
grammar; graphify_extract's skipped / unindexed directory scans are stubbed where a section needs them.
Usage: python tests/unparsed_census_gates.py
"""
from __future__ import annotations

import atexit
import contextlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

_HOME = tempfile.mkdtemp(prefix="dm_unparsed_home_")
atexit.register(shutil.rmtree, _HOME, True)      # also when the module is only imported
os.environ["DEEPMAP_HOME"] = _HOME
for _k in ("DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_EXTRACTOR", "DEEPMAP_SQL_DECLARATIONS"):
    os.environ.pop(_k, None)

import verdict as V  # noqa: E402
import graphify_extract as G  # noqa: E402

_PASS = _FAIL = 0
_PARSERS = ("tree_sitter_sql", "tree_sitter_dm")
_ENV = "DEEPMAP_SQL_DECLARATIONS"


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:600]}"))


def note(text: str) -> None:
    print(f"     ({text})")


def _reset_cache() -> None:
    cache = getattr(G, "_PARSER_STATE", None)
    if isinstance(cache, dict):
        cache.clear()


@contextlib.contextmanager
def parsers(present=None, *, raises: bool = False, calls: dict | None = None, broken: dict | None = None):
    """The two optional grammar modules as this install sees them. importlib.util.find_spec answers for them:
    `present` {module: bool} (a module left out answers absent), or `raises` (the probe throws); every other name
    goes to the real find_spec. graphify_extract._load_grammar (the load probe) succeeds for a present module, or
    raises `broken[module]`'s text. The per-process probe cache is cleared before and after."""
    real = importlib.util.find_spec
    want = dict(present or {})
    bad = dict(broken or {})
    load0 = getattr(G, "_load_grammar", None)

    def fake(name, package=None):
        if name in _PARSERS:
            if calls is not None:
                calls[name] = calls.get(name, 0) + 1
            if raises:
                raise ValueError(f"{name}.__spec__ is None")
            return real("json") if want.get(name) else None
        return real(name, package)

    def load(module):
        if calls is not None:
            calls["load:" + module] = calls.get("load:" + module, 0) + 1
        if module in bad:
            raise RuntimeError(bad[module])
        return object()

    _reset_cache()
    importlib.util.find_spec = fake
    if load0 is not None:
        G._load_grammar = load
    try:
        yield
    finally:
        importlib.util.find_spec = real
        if load0 is not None:
            G._load_grammar = load0
        _reset_cache()


@contextlib.contextmanager
def reader(value):
    """DEEPMAP_SQL_DECLARATIONS for the block (None = unset)."""
    old = os.environ.get(_ENV)
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = old


@contextlib.contextmanager
def engine_dirs(skipped=None, unindexed=None):
    """graphify_extract.skipped_source_dirs / unindexed_source_dirs stubbed (the verdict's wrappers call them)."""
    s0, u0 = G.skipped_source_dirs, G.unindexed_source_dirs
    G.skipped_source_dirs = lambda root: {k: list(v) for k, v in (skipped or {}).items()}
    G.unindexed_source_dirs = lambda root: {k: dict(v) for k, v in (unindexed or {}).items()}
    try:
        yield
    finally:
        G.skipped_source_dirs, G.unindexed_source_dirs = s0, u0


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(root: Path, *, n_cs=20, n_sql=12, n_dm=0, n_go=0, n_pl=0, n_r=0, sql_dir="db", dm_dir="code",
          pl_dir="scripts") -> list:
    """A .NET-shaped tree: n_cs C# files (each one engine node), n_sql .sql, n_dm .dm, n_go .go, n_pl .pl, n_r .R.
    Returns the engine nodes sync would have pushed (C# only: no other parser ran)."""
    nodes = []
    for i in range(n_cs):
        rel = f"src/Shop/C{i}.cs"
        _w(root / rel, f"namespace Shop {{ public class C{i} {{ public void M() {{ }} }} }}\n")
        nodes.append({"key": f"cs{i}", "name": f"C{i}", "label": "Class", "source_file": rel})
    for i in range(n_sql):
        _w(root / sql_dir / f"q{i}.sql", f"CREATE TABLE t{i} (id INT PRIMARY KEY);\n")
    for i in range(n_dm):
        _w(root / dm_dir / f"m{i}.dm", f"/proc/p{i}()\n\treturn {i}\n")
    for i in range(n_go):
        _w(root / "tools" / f"g{i}.go", f"package tools\n\nfunc G{i}() {{}}\n")
    for i in range(n_pl):
        _w(root / pl_dir / f"p{i}.pl", f"sub p{i} {{ return {i}; }}\n")
    for i in range(n_r):
        _w(root / "analysis" / f"r{i}.R", f"f{i} <- function(x) x + {i}\n")
    return nodes


def _verdict(root: Path, nodes, extraction=None, extractor="graphify"):
    census = V.extension_census(root)
    kw = {"extraction": extraction} if extraction is not None else {}
    cov = V.build_coverage(root, nodes, census=census, engine_keys=[n["key"] for n in nodes], **kw)
    v, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                            extractor=extractor, ml_ok=True, mcp_ok=True, coverage=cov,
                            extraction=dict(extraction or {}, requested="auto"))
    return census, cov, v, reasons


def _low(reasons, what: str) -> list:
    return [r for r in reasons or [] if r.startswith("LOW COVERAGE") and what in r]


def _np(cov) -> dict:
    return (cov or {}).get("not_parsed") or {}


def _dash_free(text: str) -> bool:
    return "\u2014" not in text and "\u2013" not in text


def gate_a_field_case() -> None:
    print("\n-- A. THE FIELD CASE: 12 .sql + a healthy C# graph, reader OFF, no SQL grammar -> READY, named --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
            reader(None):
        root = Path(td)
        nodes = _tree(root)
        census, cov, v, reasons = _verdict(root, nodes)
        check("the census does not count the .sql files as source (no 'SQL' language)", "SQL" not in census, census)
        check("the census still counts the C# files exactly", census.get("C#") == 20, census)
        check("no 'LOW COVERAGE for SQL' reason, READY", not _low(reasons, "SQL") and v == V.READY and not reasons,
              (v, reasons))
        got = _np(cov).get(".sql")
        check("coverage.not_parsed['.sql'] = {files 12, cause 'off', package 'tree-sitter-sql', detail 'default'}",
              got == {"files": 12, "cause": "off", "package": "tree-sitter-sql", "detail": "default"}, _np(cov))
        check("no 'parser_missing' block any more (renamed 'not_parsed')", "parser_missing" not in cov, sorted(cov))
        check("the coverage dict stays JSON-serialisable (sync persists it in graph_build.json)", bool(json.dumps(cov)))
        info = [ln for ln in V.format_coverage(cov) if ".sql" in ln]
        check("doctor / status's coverage table gets ONE line about them", len(info) == 1, V.format_coverage(cov))
        ln = info[0] if info else ""
        check("...naming the count, the reader being off, how to turn it on, and the engine's missing grammar",
              "12 .sql" in ln and "SQL reader is off" in ln and "DEEPMAP_SQL_DECLARATIONS=1" in ln
              and "tree-sitter-sql" in ln and "not counted for coverage" in ln, ln)
        check("...and that the switch reads .sql only in a graph the graphify engine builds (the ast fall-back reads "
              "no SQL)", "graphify engine" in ln, ln)
        check("...which is no LOW COVERAGE reason and has no em / en dash", "LOW COVERAGE" not in ln and _dash_free(ln),
              ln)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
            reader("0"):
        root = Path(td)
        nodes = _tree(root)
        census, cov, v, reasons = _verdict(root, nodes)
        got = _np(cov).get(".sql") or {}
        check("DEEPMAP_SQL_DECLARATIONS=0: still uncounted, cause 'off', detail 'env'",
              "SQL" not in census and got.get("cause") == "off" and got.get("detail") == "env", (census, got))
    # Review finding: the env case said "(DEEPMAP_SQL_DECLARATIONS=0) ... unset the variable", whatever was set, and
    # unsetting it keeps the reader off while the default is off. The remedy follows the release's default.
    for value in ("0", "enable"):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
                reader(value):
            root = Path(td)
            nodes = _tree(root)
            census, cov, v, reasons = _verdict(root, nodes)
            got = _np(cov).get(".sql") or {}
            ln = " ".join(x for x in V.format_coverage(cov) if ".sql" in x)
            check(f"DEEPMAP_SQL_DECLARATIONS={value} (default off): the line says the variable is set to no on word "
                  f"and to set it to 1, never to unset it, never a literal '=0'",
                  got.get("detail") == "env" and "set to a value other than 1, true, yes or on" in ln
                  and "set DEEPMAP_SQL_DECLARATIONS=1 and run /deep-map:refresh" in ln and "unset" not in ln
                  and "DEEPMAP_SQL_DECLARATIONS=0" not in ln and _dash_free(ln), ln)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
            reader("0"):
        root = Path(td)
        nodes = _tree(root)
        default0 = G.SQL_DECLARATIONS_DEFAULT
        G.SQL_DECLARATIONS_DEFAULT = True
        try:
            census, cov, v, reasons = _verdict(root, nodes)
            ln = " ".join(x for x in V.format_coverage(cov) if ".sql" in x)
            why = V._unparsable_why([(".sql", 12, "off", "tree-sitter-sql", "env")])
        finally:
            G.SQL_DECLARATIONS_DEFAULT = default0
        check("...with the default flipped ON, DEEPMAP_SQL_DECLARATIONS=0: the line and the FAILED clause say to "
              "unset the variable", "switched off" in ln and "unset DEEPMAP_SQL_DECLARATIONS" in ln
              and "unset DEEPMAP_SQL_DECLARATIONS" in why, (ln, why))
    for on in ("1", "true", "ON"):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
                reader(on):
            root = Path(td)
            nodes = _tree(root)
            census = V.extension_census(root)
            cov = V.build_coverage(root, nodes, census=census, engine_keys=[n["key"] for n in nodes])
            check(f"reader ON ({_ENV}={on}): the 12 .sql files are counted (SQL is read by DeepMap)",
                  census.get("SQL") == 12 and ".sql" not in _np(cov), (census, _np(cov)))


def gate_b_reader_off_engine_parser() -> None:
    print("\n-- B. reader OFF + a usable engine SQL grammar: counted, density over :Class / :Function only --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}), reader(None):
        root = Path(td)
        nodes = _tree(root)
        check("not_parsed_here() leaves .sql out (the engine can read it)", ".sql" not in G.not_parsed_here(),
              G.not_parsed_here())
        mods = [{"key": f"sqlf{i}", "name": f"q{i}.sql", "label": "Module", "source_file": f"db/q{i}.sql"}
                for i in range(12)]
        census, cov, v, reasons = _verdict(root, nodes + mods, extraction={"sql": {"state": "off"}})
        check("the census counts the 12 .sql files", census.get("SQL") == 12, census)
        low = _low(reasons, "for SQL")
        check("12 file :Modules and 0 declarations -> 'LOW COVERAGE for SQL' (file nodes do not count)", bool(low),
              reasons)
        r = low[0] if low else ""
        check("...whose tail names DeepMap's switched-off reader and the variable, not 'check the extractor lines'",
              "DEEPMAP_SQL_DECLARATIONS" in r and "SQL reader" in r and "check the extractor lines" not in r, r)
        check("...worded on a condition (review finding: a folder of ALTER / INSERT scripts declares nothing): 'if "
              "they declare tables, views or procedures', 'set DEEPMAP_SQL_DECLARATIONS=1', the DML case named, no "
              "em / en dash", "if they declare tables, views or procedures" in r
              and "set DEEPMAP_SQL_DECLARATIONS=1 and run /deep-map:refresh" in r and "ALTER / INSERT" in r
              and _dash_free(r.split(" (", 1)[-1]), r)
        check("...and keeps the hook-visible head 'LOW COVERAGE for SQL'", r.startswith("LOW COVERAGE for SQL"), r)
        decls = [{"key": f"sqlt{i}", "name": f"t{i}", "label": "Class", "source_file": f"db/q{i}.sql"}
                 for i in range(12)]
        census, cov, v, reasons = _verdict(root, nodes + mods + decls, extraction={"sql": {"state": "off"}})
        check("12 :Modules + 12 :Class declarations -> no SQL reason, READY", not _low(reasons, "SQL")
              and v == V.READY, (v, reasons))
        census, cov, v, reasons = _verdict(root, nodes + mods)
        check("no extraction report (ast / a caller that passes none): today's rule, file nodes count, READY",
              not _low(reasons, "SQL") and v == V.READY, (v, reasons))


def gate_c_dm() -> None:
    print("\n-- C. .dm (tree_sitter_dm): absent, usable, importable but broken --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": False}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_dm=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("grammar absent: the .dm files are not counted ('DM' not in the census)", "DM" not in census, census)
        check("grammar absent: no 'LOW COVERAGE for DM', READY", not _low(reasons, "DM") and v == V.READY,
              (v, reasons))
        got = _np(cov).get(".dm")
        check("grammar absent: not_parsed['.dm'] = {6, 'package', 'tree-sitter-dm', ''}",
              got == {"files": 6, "cause": "package", "package": "tree-sitter-dm", "detail": ""}, _np(cov))
        ln = " ".join(x for x in V.format_coverage(cov) if ".dm" in x)
        check("grammar absent: 'DM: 6 .dm file(s) not in the graph (not counted for coverage): the extraction "
              "engine parses .dm only with the optional package tree-sitter-dm, which DeepMap does not install'",
              ln == "DM: 6 .dm file(s) not in the graph (not counted for coverage): the extraction engine parses "
                    ".dm only with the optional package tree-sitter-dm, which DeepMap does not install", ln)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_dm=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("grammar usable: the census counts 6 DM files", census.get("DM") == 6, census)
        check("grammar usable: 'LOW COVERAGE for DM' as before, DEGRADED", bool(_low(reasons, "for DM"))
              and v == V.DEGRADED, (v, reasons))
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True},
                                                      broken={"tree_sitter_dm": "Incompatible Language version 15"}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_dm=6)
        state = G.not_parsed_here().get(".dm")
        check("importable but Language() raises -> ('unusable', 'tree-sitter-dm', <the error>)",
              isinstance(state, tuple) and state[0] == "unusable" and state[1] == "tree-sitter-dm"
              and "Incompatible Language version 15" in state[2], state)
        census, cov, v, reasons = _verdict(root, nodes)
        got = _np(cov).get(".dm") or {}
        check("unusable: uncounted, READY, cause 'unusable' with the error as detail",
              "DM" not in census and v == V.READY and got.get("cause") == "unusable"
              and "Incompatible Language version 15" in str(got.get("detail")), (census, v, got))
        ln = " ".join(x for x in V.format_coverage(cov) if ".dm" in x)
        check("unusable: the line says tree-sitter-dm is installed but cannot be loaded (with the error)",
              "tree-sitter-dm is installed but cannot be loaded (RuntimeError: Incompatible Language version 15)"
              in ln, ln)


def _write_tree_for_engine(root: Path) -> None:
    _tree(root, n_cs=3, n_sql=12, n_dm=6)              # 21 files: the engine's worker-pool path


def gate_d_not_failed_files() -> None:
    print("\n-- D. the engine's per-file 'not installed' results are not failed files (real graphify) --")
    if importlib.util.find_spec("graphify") is None:
        note("graphify is not installed in this Python: real-engine section skipped")
        return
    absent = [m for m in _PARSERS if importlib.util.find_spec(m) is None]
    note(f"graphify {G.graphify_version()}; grammars not installed here: {absent or 'none'}")
    base = Path(tempfile.mkdtemp(prefix="dm_unp_"))   # short: graphify's cache paths must stay under MAX_PATH
    try:
        root = base / "t"
        _write_tree_for_engine(root)
        with reader(None):
            result, report = G.extract_with_report(root)
        check("the engine ran (no cause)", report.get("cause") is None, report.get("cause"))
        check("failed_files is 0: no .sql / .dm result is a failed file",
              int(report.get("failed_files") or 0) == 0, report.get("failed_examples"))
        nodes = (result or ([],))[0] if isinstance(result, tuple) else []
        exts = sorted({Path(str(n.get("source_file") or "")).suffix.lower() for n in nodes or []})
        if "tree_sitter_sql" in absent:
            check("with the SQL grammar absent and the reader off, no .sql node (the premise)", ".sql" not in exts,
                  exts)
        if "tree_sitter_dm" in absent:
            check("with the DM grammar absent the engine gives no .dm node (the premise)", ".dm" not in exts, exts)
        check("the C# files are extracted (the run is healthy)", ".cs" in exts, exts)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=max(1, len(nodes or [])), n_embedded=max(1, len(nodes or [])),
                                census={"C#": 3}, extractor="graphify", ml_ok=True, mcp_ok=True,
                                extraction={"requested": "auto", "failed_files": report.get("failed_files"),
                                            "failed_examples": report.get("failed_examples")})
        check("the extraction report raises no 'could not extract' reason", not any(
            "could not extract" in r for r in reasons), reasons)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def gate_e_static_lists() -> None:
    print("\n-- E. the static lists and the ONE rule's shape --")
    check(".sql / .dm / .pl / .pm / .r / .pyi / .cjs stay in verdict._SOURCE_EXTS (the drift locks read it)",
          all(e in V._SOURCE_EXTS for e in (".sql", ".dm", ".pl", ".pm", ".r", ".pyi", ".cjs")))
    missing = sorted(e for e in G.SOURCE_CODE_EXTS if e not in V._SOURCE_EXTS)
    check("census contains graphify_extract.SOURCE_CODE_EXTS (whatever the probe says)", not missing, missing)
    opt = getattr(G, "OPTIONAL_PARSERS", None)
    check("graphify_extract.OPTIONAL_PARSERS == {'.sql': 'tree_sitter_sql', '.dm': 'tree_sitter_dm'} (the engine "
          "module owns what the engine can parse)", opt == {".sql": "tree_sitter_sql", ".dm": "tree_sitter_dm"}, opt)
    moved = [n for n in ("_OPTIONAL_PARSERS", "_parser_present", "_parser_package", "_PARSER_PRESENT")
             if hasattr(V, n)]
    check("verdict holds no copy of the rule any more (moved to graphify_extract)", not moved, moved)
    unp = getattr(G, "ENGINE_UNPARSED_EXTS", None)
    check("graphify_extract.ENGINE_UNPARSED_EXTS == {.pyi, .cjs, .pl, .pm, .r}",
          unp == frozenset({".pyi", ".cjs", ".pl", ".pm", ".r"}), unp)
    check("every mapped / unparsed suffix is one DeepMap extracts",
          all(e in G.SOURCE_CODE_EXTS for e in list(opt or {}) + list(unp or ())), (opt, unp))
    fn = getattr(G, "not_parsed_here", None)
    check("graphify_extract.not_parsed_here exists", callable(fn))
    if not callable(fn):
        return
    with parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), reader(None):
        got = fn()
        check("not_parsed_here() -> {suffix: (cause, package, detail)} with three strings each",
              isinstance(got, dict) and all(isinstance(t, tuple) and len(t) == 3 and all(isinstance(x, str) for x in t)
                                            for t in got.values()), got)
        check("...the field install: .sql off, .dm package, .pyi .cjs .pl .pm .r no-parser",
              {k: v[0] for k, v in got.items()} == {".sql": "off", ".dm": "package", ".pyi": "no-parser",
                                                    ".cjs": "no-parser", ".pl": "no-parser", ".pm": "no-parser",
                                                    ".r": "no-parser"}, got)
        check("...packages are pip names ('tree-sitter-sql', 'tree-sitter-dm'), '' for no-parser",
              got.get(".sql", ("", "", ""))[1] == "tree-sitter-sql" and got.get(".dm", ("", "", ""))[1] ==
              "tree-sitter-dm" and got.get(".pl", ("", "x", ""))[1] == "", got)
        check("V._uncounted_exts() is exactly that rule's keys", V._uncounted_exts() == frozenset(got),
              V._uncounted_exts())
        pe = G.engine_parseable_exts()
        check("engine_parseable_exts() leaves out .sql (DeepMap's own), every not-parsed suffix and the "
              "upgradeable ones", not ({".sql", ".dm", ".pl", ".r", ".pyi", ".cjs"} & set(pe))
              and not (set(G.upgradeable_unparsed_exts()) & set(pe)) and {".cs", ".py", ".pas"} <= set(pe), sorted(pe))
    with parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), reader("1"):
        check("reader ON: .sql is not in not_parsed_here()", ".sql" not in G.not_parsed_here(), G.not_parsed_here())
    try:
        g0 = G.sql_reader_enabled
        G.sql_reader_enabled = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            got = G.not_parsed_here()
            check("a problem inside the rule -> {} (count everything), never an exception", got == {}, got)
        finally:
            G.sql_reader_enabled = g0
    except Exception as e:  # noqa: BLE001
        check("a problem inside the rule never raises", False, f"{type(e).__name__}: {e}")


def _optional_grammar_modules() -> dict:
    """{module: requirement} for every tree-sitter grammar the installed graphifyy declares ONLY under an extra
    (never among its core requirements), from the distribution's metadata: 'tree-sitter-sql; extra == "sql"' ->
    tree_sitter_sql. These are the grammars a bare `pip install graphifyy` (DeepMap's install) leaves out."""
    import importlib.metadata as md
    core, extra = set(), {}
    for req in md.requires("graphifyy") or []:
        name, _, marker = str(req).partition(";")
        m = re.match(r"\s*([A-Za-z0-9_.-]+)", name)
        if not m:
            continue
        mod = m.group(1).lower().replace("-", "_").replace(".", "_")
        if not mod.startswith("tree_sitter_"):
            continue
        if "extra" in marker:
            extra.setdefault(mod, str(req).strip())
        else:
            core.add(mod)
    return {m: r for m, r in extra.items() if m not in core}


def _optional_parser_drift(source_exts, opt_map, optional_mods, extractor_of) -> list:
    """The two-sided lock: a suffix DeepMap extracts whose engine extractor names an optional grammar module must
    be in `opt_map` with that module (else, without the extra, it reads 'LOW COVERAGE ... 0 extracted' on every
    sync: the field bug again); every `opt_map` entry must be such a suffix. [] when they agree."""
    exts = sorted({str(e).lower() for e in source_exts})
    named: dict = {}
    for ext in exts:
        fn = extractor_of(ext)
        if fn is None:
            continue
        try:
            src = inspect.getsource(fn)
        except Exception:  # noqa: BLE001: an unsourced extractor names nothing; the reverse side still checks
            continue
        mods = sorted(m for m in optional_mods if re.search(r"\b" + re.escape(m) + r"\b", src))
        if mods:
            named[ext] = mods
    problems = []
    for ext, mods in sorted(named.items()):
        if opt_map.get(ext) not in mods:
            problems.append(f"{ext}: its engine extractor needs {mods} (a graphifyy extra DeepMap does not install) "
                            f"but graphify_extract.OPTIONAL_PARSERS maps it to {opt_map.get(ext)!r}")
    for ext, mod in sorted(opt_map.items()):
        if ext not in exts:
            problems.append(f"{ext}: in graphify_extract.OPTIONAL_PARSERS but not in SOURCE_CODE_EXTS")
        elif mod not in named.get(ext, []):
            problems.append(f"{ext}: mapped to {mod}, but its engine extractor does not need that optional module")
    return problems


def _engine_lookup():
    import graphify.extract as GE
    getter = getattr(GE, "_get_extractor", None)
    table = getattr(GE, "_DISPATCH", None)
    return GE, getter, table


def gate_e2_engine_derived_lock() -> None:
    print("\n-- E2. drift lock: OPTIONAL_PARSERS is exactly what the ENGINE needs from its optional extras --")
    if importlib.util.find_spec("graphify") is None:
        note("graphify is not installed in this Python: engine-derived lock skipped")
        return
    _GE, getter, _table = _engine_lookup()
    check("the engine exposes its per-suffix extractor lookup (graphify.extract._get_extractor)", callable(getter),
          "moved: re-point this lock at the engine's new suffix -> extractor lookup")
    if not callable(getter):
        return

    def extractor_of(ext):
        try:
            return getter(Path("x" + ext))
        except Exception:  # noqa: BLE001
            return None

    mods = _optional_grammar_modules()
    note(f"graphify {G.graphify_version()}: grammars only in extras: {sorted(mods)}")
    check("the engine metadata names the SQL and DM grammars as extras (the premise)",
          {"tree_sitter_sql", "tree_sitter_dm"} <= set(mods), sorted(mods))
    opt = dict(getattr(G, "OPTIONAL_PARSERS", {}) or {})
    drift = _optional_parser_drift(G.SOURCE_CODE_EXTS, opt, mods, extractor_of)
    check("every extracted suffix whose engine parser is an optional extra is in OPTIONAL_PARSERS, and nothing "
          "else is", not drift, drift)
    tf = _optional_parser_drift(tuple(G.SOURCE_CODE_EXTS) + (".tf",), opt, mods, extractor_of)
    check("teeth: mapping Terraform (.tf, tree_sitter_hcl) without an OPTIONAL_PARSERS entry is reported",
          any(p.startswith(".tf:") and "tree_sitter_hcl" in p for p in tf), tf)
    no_dm = _optional_parser_drift(G.SOURCE_CODE_EXTS, {k: v for k, v in opt.items() if k != ".dm"}, mods,
                                   extractor_of)
    check("teeth: dropping .dm from the map is reported", any(p.startswith(".dm:") for p in no_dm), no_dm)
    bogus = _optional_parser_drift(G.SOURCE_CODE_EXTS, {**opt, ".py": "tree_sitter_sql"}, mods, extractor_of)
    check("teeth: a suffix whose extractor needs no optional module (.py) in the map is reported",
          any(p.startswith(".py:") for p in bogus), bogus)


def _classify_suffixes(source_exts, dispatched, unparsed, since, optional, usable, reader_exts) -> list:
    """Section 8's lock: every SOURCE_CODE_EXTS suffix is a decided case: (a) the installed engine dispatches it and
    needs no optional grammar for it, or its grammar is usable here; (b) in ENGINE_UNPARSED_EXTS; (c) in
    ENGINE_EXT_SINCE (a later in-range engine parses it); (d) in OPTIONAL_PARSERS with its grammar not usable here;
    (e) read by DeepMap itself (sql_reader.EXTS). Also: an ENGINE_UNPARSED_EXTS suffix the engine dispatches means
    the list is stale. Returns the problems ([] when every suffix is decided)."""
    problems = []
    for ext in sorted({str(e).lower() for e in source_exts}):
        a = ext in dispatched and (ext not in optional or usable.get(optional[ext], False))
        b = ext in unparsed
        c = ext in since
        d = ext in optional and not usable.get(optional[ext], False)
        e = ext in reader_exts
        if not (a or b or c or d or e):
            problems.append(f"{ext}: not dispatched by the installed engine and in no decided list "
                            f"(ENGINE_UNPARSED_EXTS / ENGINE_EXT_SINCE / OPTIONAL_PARSERS / sql_reader.EXTS)")
        if b and ext in dispatched:
            problems.append(f"{ext}: in ENGINE_UNPARSED_EXTS but the installed engine dispatches it")
    return problems


def gate_e3_section8_lock() -> None:
    print("\n-- E3. section 8 lock: every SOURCE_CODE_EXTS suffix is a decided case; ENGINE_UNPARSED_EXTS is exact --")
    if importlib.util.find_spec("graphify") is None:
        note("graphify is not installed in this Python: engine-derived lock skipped")
        return
    import sql_reader
    _GE, _getter, table = _engine_lookup()
    check("the engine exposes its suffix table (graphify.extract._DISPATCH)", isinstance(table, dict) and table,
          type(table))
    if not isinstance(table, dict):
        return
    dispatched = {str(k).lower() for k in table if isinstance(k, str)}
    source = {e.lower() for e in G.SOURCE_CODE_EXTS}
    unparsed = set(getattr(G, "ENGINE_UNPARSED_EXTS", ()) or ())
    since = set(G.ENGINE_EXT_SINCE)
    optional = dict(getattr(G, "OPTIONAL_PARSERS", {}) or {})
    usable = {m: importlib.util.find_spec(m) is not None for m in optional.values()}
    note(f"graphify {G.graphify_version()}: optional grammars usable here: {usable}")
    want = source - dispatched - since - set(sql_reader.EXTS)
    check("ENGINE_UNPARSED_EXTS == SOURCE_CODE_EXTS - the installed engine's table - ENGINE_EXT_SINCE - "
          "sql_reader.EXTS (the in-range tables only grew, so this holds at both ends)", unparsed == want,
          {"list": sorted(unparsed), "derived": sorted(want)})
    probs = _classify_suffixes(source, dispatched, unparsed, since, optional, usable, set(sql_reader.EXTS))
    check("every SOURCE_CODE_EXTS suffix is a decided case (a)-(e)", not probs, probs)
    teeth = _classify_suffixes(source, dispatched, unparsed - {".r"}, since, optional, usable, set(sql_reader.EXTS))
    check("teeth: dropping .r from ENGINE_UNPARSED_EXTS is reported", any(p.startswith(".r:") for p in teeth), teeth)
    teeth2 = _classify_suffixes(source, dispatched, unparsed | {".py"}, since, optional, usable, set(sql_reader.EXTS))
    check("teeth: listing a dispatched suffix (.py) as unparsed is reported", any(p.startswith(".py:") for p in teeth2),
          teeth2)
    with parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), reader(None):
        keys = set(G.not_parsed_here())
    check("not_parsed_here()'s suffixes are all SOURCE_CODE_EXTS", keys <= source, sorted(keys - source))


def gate_f_other_language_still_flagged() -> None:
    print("\n-- F. an unrelated language with 0 nodes still raises its LOW COVERAGE --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
            reader(None):
        root = Path(td)
        nodes = _tree(root, n_go=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("'LOW COVERAGE for Go' (6 files, 0 nodes)", bool(_low(reasons, "for Go")), reasons)
        check("...and no SQL reason beside it", not _low(reasons, "SQL"), reasons)
        check("the verdict is DEGRADED", v == V.DEGRADED, (v, reasons))


def gate_g_probe_raises() -> None:
    print("\n-- G. find_spec raising -> the files are counted (today's behaviour), no exception --")
    calls: dict = {}
    try:
        with tempfile.TemporaryDirectory() as td, parsers(raises=True, calls=calls), reader(None):
            root = Path(td)
            nodes = _tree(root, n_sql=0, n_dm=12)
            census, cov, v, reasons = _verdict(root, nodes)
            check("the census counts the 12 .dm files as before", census.get("DM") == 12, census)
            check("'LOW COVERAGE for DM' as before, DEGRADED", bool(_low(reasons, "DM")) and v == V.DEGRADED,
                  (v, reasons))
            check("the probe was asked (the stub saw tree_sitter_dm)", calls.get("tree_sitter_dm", 0) >= 1, calls)
        check("no exception escaped", True)
    except Exception as e:  # noqa: BLE001
        check("no exception escaped", False, f"{type(e).__name__}: {e}")


def gate_h_once_per_process() -> None:
    print("\n-- H. the grammar probe runs once per process (cached) --")
    calls: dict = {}
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True},
                                                      calls=calls), reader(None):
        root = Path(td)
        nodes = _tree(root, n_dm=2)
        for _ in range(3):
            _verdict(root, nodes)
        check("find_spec('tree_sitter_sql') asked exactly once over three syncs' worth of counting",
              calls.get("tree_sitter_sql") == 1, calls)
        check("find_spec('tree_sitter_dm') asked exactly once", calls.get("tree_sitter_dm") == 1, calls)
        check("the load probe of tree_sitter_dm ran exactly once", calls.get("load:tree_sitter_dm") == 1, calls)
        check("an absent module is never load-probed", "load:tree_sitter_sql" not in calls, calls)


def gate_i_conventional_dirs() -> None:
    print("\n-- I. the conventional-dir counter agrees (lib/ holding only .dm / only .pl) --")
    for present in (False, True):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": present}):
            root = Path(td)
            nodes = _tree(root, n_sql=0, n_dm=4, dm_dir="lib")
            census, cov, v, reasons = _verdict(root, nodes)
            lib = (cov.get("dirs") or {}).get("lib")
            if present:
                check("grammar usable: lib/ counts its 4 .dm files and is flagged as before",
                      (lib or {}).get("files") == 4 and any("LOW COVERAGE in lib/" in r for r in reasons),
                      (lib, reasons))
            else:
                check("grammar absent: lib/ holds no countable source (not listed)", lib is None, cov.get("dirs"))
                check("grammar absent: no 'LOW COVERAGE in lib/', READY",
                      not any("lib/" in r for r in reasons) and v == V.READY, (v, reasons))
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_pl=4, pl_dir="lib")
        census, cov, v, reasons = _verdict(root, nodes)
        check("lib/ holding only .pl (no in-range parser): not listed, READY",
              (cov.get("dirs") or {}).get("lib") is None and v == V.READY, (cov.get("dirs"), v, reasons))


def gate_j_engine_skipped_dirs() -> None:
    print("\n-- J. the engine-skipped and unindexed directory findings agree (.pl, never parsed here) --")
    skipped = {"build": [f"build/m{i}.pl" for i in range(5)],
               "out": [f"out/x{i}.pl" for i in range(3)] + [f"out/Y{i}.cs" for i in range(4)]}
    unindexed = {"dist": {"files": [f"dist/z{i}.pl" for i in range(6)], "kind": "never-rescued"}}
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}), \
            engine_dirs(skipped, unindexed):
        root = Path(td)
        nodes = _tree(root, n_sql=0)
        census, cov, v, reasons = _verdict(root, nodes)
        sd = cov.get("skipped_dirs") or {}
        ud = cov.get("unindexed_dirs") or {}
        check("build/ (only .pl) is no finding", "build" not in sd, sd)
        check("out/ counts only its 4 C# files", (sd.get("out") or {}).get("files") == 4, sd)
        check("dist/ (only .pl) is no finding", "dist" not in ud, ud)
        check("no LOW COVERAGE naming build/ or dist/", not any(("build/" in r or "dist/" in r) for r in reasons),
              reasons)
        got = _np(cov).get(".pl") or {}
        check("the note counts those .pl files (5 + 3 + 6), cause 'no-parser'",
              got.get("files") == 14 and got.get("cause") == "no-parser" and got.get("package") is None, got)


def gate_k_long_walk() -> None:
    print("\n-- K. the \\\\?\\ long-path walk agrees --")
    if os.name != "nt":
        note("verdict._walk_long walks through \\\\?\\ paths: Windows only, not run here")
        return
    for present in (False, True):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": present}):
            root = Path(td)
            _tree(root, n_cs=2, n_sql=0, n_dm=3, n_pl=2)
            got = sorted(Path(p).suffix for p in V._walk_long(str(root), V._dir_ignore_rule(), 1000))
            want = [".cs"] * 2 + ([".dm"] * 3 if present else [])
            check(f"DM grammar {'usable' if present else 'absent'}: _walk_long yields {sorted(set(want))} "
                  f"(never .pl)", got == sorted(want), got)


def gate_l_only_such_source() -> None:
    print("\n-- L. a repo of only unparsed source: FAILED with the cause, not 'is this the right path?' --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader(None):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=0, n_pl=6)
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check("Perl-only: FAILED", v == V.FAILED, (v, reasons))
        check("...the reason names the 6 .pl files and 'no graphify release DeepMap supports'",
              "6 .pl" in r and "no graphify release DeepMap supports" in r, reasons)
        check("...not the wrong-path guess, no 'optional package' claim for Perl", "right path" not in r
              and "optional package" not in r, reasons)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader(None):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=12)
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check("SQL-only, reader off: FAILED naming the 12 .sql files and DeepMap's SQL reader being off (with "
              "DEEPMAP_SQL_DECLARATIONS)", v == V.FAILED and "12 .sql" in r and "SQL reader" in r
              and "DEEPMAP_SQL_DECLARATIONS" in r and "right path" not in r, reasons)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader("0"):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=12)
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check("SQL-only, DEEPMAP_SQL_DECLARATIONS=0: FAILED with one 'so' (review finding: '..., so .sql is not read, "
              "so there is nothing to search'), the remedy 'set DEEPMAP_SQL_DECLARATIONS=1', no literal '=0'",
              v == V.FAILED and r.count(", so ") == 1 and "set DEEPMAP_SQL_DECLARATIONS=1" in r
              and "DEEPMAP_SQL_DECLARATIONS=0" not in r and "unset" not in r, reasons)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}), \
            reader(None):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=0, n_dm=3, n_pl=2)
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="graphify",
                                coverage=cov)
        r = " ".join(reasons)
        check("DM + Perl: each suffix with its own cause (tree-sitter-dm; no supported release)",
              v == V.FAILED and "tree-sitter-dm" in r and "no graphify release DeepMap supports" in r
              and "3 .dm" in r and "2 .pl" in r, reasons)


def gate_m_never_raises() -> None:
    print("\n-- M. malformed input never raises --")
    try:
        for blk in ({".dm": None, 3: "x"}, "garbage", {".pl": {"files": "many"}}, {".pl": {"files": True}},
                    {".sql": {"files": 2, "cause": 7}}, [1, 2]):
            lines = V.format_coverage({"languages": {}, "dirs": {}, "not_parsed": blk})
            check(f"format_coverage on a malformed not_parsed block ({blk!r}) returns a list", isinstance(lines, list),
                  lines)
            v, rs = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                               coverage={"not_parsed": blk})
            check(f"classify on it still returns FAILED ({blk!r})", v == V.FAILED, (v, rs))
        lines = V.format_coverage({"not_parsed": {".pl": {"files": 2, "cause": "no-parser", "package": None,
                                                         "detail": ""}, ".bad": "x"}})
        check("one bad entry never hides a good one", any("2 .pl" in ln for ln in lines), lines)
        # A 0.6.3 manifest (released 2026-11-01) carries "parser_missing" {suffix: {files, package}}: doctor / status
        # print its 0.6.3 line until the graph is rebuilt (review finding: the line had disappeared), and a
        # malformed one never raises.
        legacy = {"languages": {"C#": {"files": 200, "nodes": 900}},
                  "parser_missing": {".sql": {"files": 12, "package": "tree-sitter-sql"}}}
        want = ("SQL: 12 .sql file(s) not in the graph (not counted for coverage): the extraction engine parses .sql "
                "only with the optional package tree-sitter-sql, which DeepMap does not install")
        lines = V.format_coverage(legacy)
        check("a 0.6.3 coverage dict: its exact 0.6.3 line after the language rows",
              lines == ["C#: 200 files, 900 nodes", want], lines)
        both = dict(legacy, not_parsed={".pl": {"files": 2, "cause": "no-parser", "package": None, "detail": ""}})
        lines = V.format_coverage(both)
        check("...a coverage dict with 'not_parsed' reads only that block", not any(".sql" in ln for ln in lines)
              and any("2 .pl" in ln for ln in lines), lines)
        for blk in ("garbage", {".sql": {"files": "x"}}, {".sql": None}, {7: {"files": 3}}):
            lines = V.format_coverage({"parser_missing": blk})
            check(f"...a malformed parser_missing block ({blk!r}): a list, no line", lines == [], lines)
    except Exception as e:  # noqa: BLE001
        check("no exception escaped", False, f"{type(e).__name__}: {e}")


def gate_n_other_blind_spots() -> None:
    print("\n-- N. 0 nodes, census empty, unparsed files AND other source the census cannot see: no 'only' claim --")
    npd = {".pl": {"files": 12, "cause": "no-parser", "package": None, "detail": ""}}
    others = {
        "unindexed_dirs (8 C# files in an engine-skipped dist/)":
            {"unindexed_dirs": {"dist": {"files": 8, "nodes": 0, "kind": "non-git"}}},
        "skipped_dirs (5 tracked files in build/)": {"skipped_dirs": {"build": {"files": 5, "nodes": 0}}},
        "long_paths (3 files past MAX_PATH)": {"long_paths": {"files": 3, "examples": ["a/b.cs"]}},
        "unparsed (4 .cu files a later engine parses)":
            {"unparsed": {"version": "0.8.39", "exts": {".cu": {"files": 4, "since": "0.8.40"}}}},
    }
    for what, extra in others.items():
        cov = {"languages": {}, "dirs": {}, "not_parsed": npd, **extra}
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check(f"{what}: FAILED, and the .pl files are not called the only source", v == V.FAILED
              and "only source files" not in r, (v, reasons))
    cov = {"languages": {}, "dirs": {}, "not_parsed": npd, "unindexed_dirs": {}, "skipped_dirs": {},
           "long_paths": {"files": 0}}
    v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify", coverage=cov)
    check("control: empty / zero-file blocks beside it keep the 'only source files are 12 .pl' reason",
          v == V.FAILED and "only source files found are 12 .pl" in " ".join(reasons), reasons)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader(None), engine_dirs(unindexed={"dist": {"files": [f"dist/A{i}.cs" for i in range(8)],
                                                          "kind": "non-git"}}):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=12)
        for i in range(8):
            _w(root / "dist" / f"A{i}.cs", f"class A{i} {{ }}\n")
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="ast",
                                coverage=cov, extraction={"requested": "auto",
                                                          "fallback_reason": "graphify produced no code nodes"})
        check("end to end: coverage holds both the dist/ finding and the 12 uncounted .sql files",
              (cov.get("unindexed_dirs") or {}).get("dist", {}).get("files") == 8
              and _np(cov).get(".sql", {}).get("files") == 12, cov)
        check("end to end: FAILED without claiming the .sql files are the only source",
              v == V.FAILED and "only source files" not in " ".join(reasons), (v, reasons))


def gate_o_beside_healthy_code() -> None:
    print("\n-- O. .pl / .r / .pyi / .cjs beside healthy code: READY, one line each with its cause --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader(None):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_pl=6, n_r=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("6 .pl + 6 .R + healthy C#: no Perl / R in the census, READY", "Perl" not in census
              and "R" not in census and v == V.READY and not reasons, (census, v, reasons))
        lines = V.format_coverage(cov)
        pl = [ln for ln in lines if ln.startswith("Perl:")]
        rr = [ln for ln in lines if ln.startswith("R:")]
        spec = G.SUPPORTED_GRAPHIFY_SPEC
        check("'Perl: 6 .pl file(s) not in the graph (not counted for coverage): no graphify release DeepMap "
              "supports (<spec>) parses .pl'",
              pl == [f"Perl: 6 .pl file(s) not in the graph (not counted for coverage): no graphify release DeepMap "
                     f"supports ({spec}) parses .pl"], lines)
        check("...and the same for the 6 .r files (an upper-case .R counts as .r)",
              rr == [f"R: 6 .r file(s) not in the graph (not counted for coverage): no graphify release DeepMap "
                     f"supports ({spec}) parses .r"], lines)
        check("no line has an em / en dash", all(_dash_free(ln) for ln in lines), lines)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            reader(None):
        root = Path(td)
        nodes = []
        _w(root / "pkg" / "core.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        nodes += [{"key": "pa", "name": "a", "label": "Function", "source_file": "pkg/core.py"},
                  {"key": "pb", "name": "b", "label": "Function", "source_file": "pkg/core.py"}]
        for i in range(60):
            _w(root / "pkg" / "stubs" / f"s{i}.pyi", f"def s{i}() -> int: ...\n")
        for i in range(6):
            _w(root / "web" / f"tool{i}.config.cjs", f"module.exports = {{ n: {i} }};\n")
        _w(root / "web" / "app.ts", "export function app(): number { return 1; }\n")
        nodes.append({"key": "ta", "name": "app", "label": "Function", "source_file": "web/app.ts"})
        census, cov, v, reasons = _verdict(root, nodes)
        check("60 .pyi beside one .py, 6 *.config.cjs beside a .ts: no Python / JavaScript reason, READY",
              not _low(reasons, "Python") and not _low(reasons, "JavaScript") and v == V.READY, (census, v, reasons))
        check("the census counts only the .py file for Python and no JavaScript",
              census.get("Python") == 1 and "JavaScript" not in census, census)
        got = _np(cov)
        check("not_parsed names 60 .pyi and 6 .cjs as no-parser",
              (got.get(".pyi") or {}).get("files") == 60 and (got.get(".cjs") or {}).get("files") == 6
              and (got.get(".pyi") or {}).get("cause") == "no-parser", got)
        lines = V.format_coverage(cov)
        check("two lines: 'Python: 60 .pyi file(s) ...' and 'JavaScript: 6 .cjs file(s) ...'",
              any(ln.startswith("Python: 60 .pyi file(s) not in the graph") for ln in lines)
              and any(ln.startswith("JavaScript: 6 .cjs file(s) not in the graph") for ln in lines), lines)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("DeepMap: suffixes this install cannot parse and the coverage census (no Neo4j)")
    print("=" * 78)
    try:
        for g in (gate_a_field_case, gate_b_reader_off_engine_parser, gate_c_dm, gate_d_not_failed_files,
                  gate_e_static_lists, gate_e2_engine_derived_lock, gate_e3_section8_lock,
                  gate_f_other_language_still_flagged, gate_g_probe_raises, gate_h_once_per_process,
                  gate_i_conventional_dirs, gate_j_engine_skipped_dirs, gate_k_long_walk, gate_l_only_such_source,
                  gate_m_never_raises, gate_n_other_blind_spots, gate_o_beside_healthy_code):
            try:
                g()
            except Exception as e:  # noqa: BLE001: a crashing section is a failure, the others still run
                check(f"{g.__name__} ran without an exception", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'}: {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
