#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: a census extension whose engine parser is an OPTIONAL package that is not installed is not counted as
missing source. No Neo4j.

The field report (a .NET repo, 2026-11): 12 .sql files, the rest of the graph healthy, verdict DEGRADED with
"LOW COVERAGE for SQL — 12 file(s) on disk but 0 extracted". graphify parses .sql with tree_sitter_sql and .dm
with tree_sitter_dm, both in graphifyy's optional extras, which DeepMap does not install: without them the engine
returns {"nodes": [], "error": "... not installed"} for every such file, so those files can never reach the graph
and counting them as source made a healthy graph read DEGRADED on every sync.

The fix (build/verdict.py): an extension in verdict._OPTIONAL_PARSERS whose parser module is not importable
(importlib.util.find_spec, once per process; a probe that raises counts as importable) is left out of every
census counter: the language census, the conventional-dir counts, the engine-skipped / unindexed directory
findings and the \\\\?\\ long-path walk. The static _SOURCE_EXTS keeps both entries (the drift locks in
tests/delphi_pascal_gates.py), and with the parser present everything counts exactly as before. The uncounted
files stay visible as ONE informational line in doctor / status's coverage table (coverage "parser_missing"),
never a DEGRADED reason. graphify reports these files as an `error` result without a per-file warning line, so
they never reach sync's failed-files count (section D runs the real engine to lock that).

Section E2 derives the map from the ENGINE (graphifyy's metadata: which tree-sitter grammars are extras only; each
extracted suffix's extractor source: which of them it needs), so moving a suffix whose grammar is an extra into
SOURCE_CODE_EXTS (Terraform's .tf) or an in-range graphify making a core grammar optional turns it RED. Section N:
with 0 nodes the FAILED reason calls the uncounted files "the only source" only when coverage names no other code
the census cannot see (an engine-skipped dir, a path past MAX_PATH, a suffix only a later engine parses).

Hermetic: a temp DEEPMAP_HOME; importlib.util.find_spec is stubbed for the two parser modules only (every other
name is passed through); graphify_extract's skipped / unindexed directory scans are stubbed where a section
needs them.
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

_HOME = tempfile.mkdtemp(prefix="dm_sqlcensus_home_")
atexit.register(shutil.rmtree, _HOME, True)      # also when the module is only imported
os.environ["DEEPMAP_HOME"] = _HOME
os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
os.environ.pop("DEEPMAP_EXTRACTOR", None)

import verdict as V  # noqa: E402
import graphify_extract as G  # noqa: E402

_PASS = _FAIL = 0
_PARSERS = ("tree_sitter_sql", "tree_sitter_dm")


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:600]}"))


def note(text: str) -> None:
    print(f"     ({text})")


def _reset_cache() -> None:
    cache = getattr(V, "_PARSER_PRESENT", None)
    if isinstance(cache, dict):
        cache.clear()


@contextlib.contextmanager
def parsers(present=None, *, raises: bool = False, calls: dict | None = None):
    """importlib.util.find_spec answers for the two optional parser modules: `present` {module: bool} (a module
    left out answers absent), or `raises` (the probe throws). Every other name goes to the real find_spec. The
    verdict's per-process cache is cleared before and after."""
    real = importlib.util.find_spec
    want = dict(present or {})

    def fake(name, package=None):
        if name in _PARSERS:
            if calls is not None:
                calls[name] = calls.get(name, 0) + 1
            if raises:
                raise ValueError(f"{name}.__spec__ is None")
            return real("json") if want.get(name) else None
        return real(name, package)

    _reset_cache()
    importlib.util.find_spec = fake
    try:
        yield
    finally:
        importlib.util.find_spec = real
        _reset_cache()


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


def _tree(root: Path, *, n_cs=20, n_sql=12, n_dm=0, n_go=0, sql_dir="db") -> list:
    """A .NET-shaped tree: n_cs C# files (each one engine node), n_sql .sql, n_dm .dm, n_go .go. Returns the
    engine nodes sync would have pushed (C# only: no SQL / DM / Go parser ran)."""
    nodes = []
    for i in range(n_cs):
        rel = f"src/Shop/C{i}.cs"
        _w(root / rel, f"namespace Shop {{ public class C{i} {{ public void M() {{ }} }} }}\n")
        nodes.append({"key": f"cs{i}", "name": f"C{i}", "label": "Class", "source_file": rel})
    for i in range(n_sql):
        _w(root / sql_dir / f"q{i}.sql", f"CREATE TABLE t{i} (id INT PRIMARY KEY);\n")
    for i in range(n_dm):
        _w(root / "code" / f"m{i}.dm", f"/proc/p{i}()\n\treturn {i}\n")
    for i in range(n_go):
        _w(root / "tools" / f"g{i}.go", f"package tools\n\nfunc G{i}() {{}}\n")
    return nodes


def _verdict(root: Path, nodes):
    census = V.extension_census(root)
    cov = V.build_coverage(root, nodes, census=census, engine_keys=[n["key"] for n in nodes])
    v, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                            extractor="graphify", ml_ok=True, mcp_ok=True, coverage=cov,
                            extraction={"requested": "auto"})
    return census, cov, v, reasons


def _low(reasons, what: str) -> list:
    return [r for r in reasons or [] if r.startswith("LOW COVERAGE") and what in r]


def gate_a_sql_absent_is_ready() -> None:
    print("\n-- A. THE FIELD CASE: 12 .sql + a healthy C# graph, SQL parser NOT installed -> READY --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}):
        root = Path(td)
        nodes = _tree(root)
        census, cov, v, reasons = _verdict(root, nodes)
        check("the census does not count the .sql files as source (no 'SQL' language)",
              "SQL" not in census, census)
        check("the census still counts the C# files exactly", census.get("C#") == 20, census)
        check("no 'LOW COVERAGE for SQL' reason", not _low(reasons, "SQL"), reasons)
        check("the verdict is READY (nothing else is wrong)", v == V.READY and not reasons, (v, reasons))
        pm = cov.get("parser_missing") or {}
        check("coverage records the 12 uncounted .sql files and the package that would parse them",
              (pm.get(".sql") or {}).get("files") == 12 and (pm.get(".sql") or {}).get("package") == "tree-sitter-sql",
              pm)
        check("the coverage dict stays JSON-serialisable (sync persists it in graph_build.json)",
              bool(json.dumps(cov)))
        lines = V.format_coverage(cov)
        info = [ln for ln in lines if ".sql" in ln]
        check("doctor / status's coverage table gets ONE informational line about them", len(info) == 1, lines)
        ln = info[0] if info else ""
        check("...naming the count, the suffix and why (an optional package DeepMap does not install)",
              "12" in ln and "tree-sitter-sql" in ln and "DeepMap does not install" in ln, ln)
        check("...which is not a LOW COVERAGE reason and has no em dash",
              "LOW COVERAGE" not in ln and "—" not in ln, ln)


def gate_b_sql_present_unchanged() -> None:
    print("\n-- B. the same tree with the SQL parser INSTALLED -> today's LOW COVERAGE (rule unchanged) --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}):
        root = Path(td)
        nodes = _tree(root)
        census, cov, v, reasons = _verdict(root, nodes)
        check("the census counts the 12 .sql files", census.get("SQL") == 12, census)
        # 12 >= _LANG_MIN_FILES: the density rule speaks (the field report's line), not the 5-file zero rule.
        check("'LOW COVERAGE for SQL — 12 files on disk but only 0 extracted node(s)' as before",
              any("LOW COVERAGE for SQL" in r and "12 files on disk but only 0 extracted" in r for r in reasons),
              reasons)
        check("the verdict is DEGRADED", v == V.DEGRADED, (v, reasons))
        check("no parser_missing block (nothing is uncounted)", not cov.get("parser_missing"), cov.get("parser_missing"))
        check("no informational .sql line in the coverage table",
              not [ln for ln in V.format_coverage(cov) if "not install" in ln], V.format_coverage(cov))


def gate_c_dm_same() -> None:
    print("\n-- C. .dm (tree_sitter_dm) behaves the same --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": False}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_dm=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("parser absent: the .dm files are not counted ('DM' not in the census)", "DM" not in census, census)
        check("parser absent: no 'LOW COVERAGE for DM', READY", not _low(reasons, "DM") and v == V.READY,
              (v, reasons))
        pm = cov.get("parser_missing") or {}
        check("parser absent: coverage names 6 .dm files and tree-sitter-dm",
              (pm.get(".dm") or {}).get("files") == 6 and (pm.get(".dm") or {}).get("package") == "tree-sitter-dm", pm)
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": True, "tree_sitter_dm": True}):
        root = Path(td)
        nodes = _tree(root, n_sql=0, n_dm=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("parser present: the census counts 6 DM files", census.get("DM") == 6, census)
        check("parser present: 'LOW COVERAGE for DM' as before, DEGRADED",
              bool(_low(reasons, "for DM")) and v == V.DEGRADED, (v, reasons))


def gate_d_not_failed_files() -> None:
    print("\n-- D. the engine's per-file 'not installed' results are not failed files (real graphify) --")
    if importlib.util.find_spec("graphify") is None:
        note("graphify is not installed in this Python: real-engine section skipped")
        return
    absent = [m for m in _PARSERS if importlib.util.find_spec(m) is None]
    note(f"graphify {G.graphify_version()}; parsers not installed here: {absent or 'none'}")
    base = Path(tempfile.mkdtemp(prefix="dm_sqlc_"))   # short: graphify's cache paths must stay under MAX_PATH
    try:
        root = base / "t"
        _tree(root, n_cs=3, n_sql=12, n_dm=6)            # 21 files: the engine's worker-pool path
        result, report = G.extract_with_report(root)
        check("the engine ran (no cause)", report.get("cause") is None, report.get("cause"))
        check("failed_files is 0: no .sql / .dm result is a failed file",
              int(report.get("failed_files") or 0) == 0, report.get("failed_examples"))
        nodes = (result or ([],))[0] if isinstance(result, tuple) else []
        exts = sorted({Path(str(n.get("source_file") or "")).suffix.lower() for n in nodes or []})
        if "tree_sitter_sql" in absent:
            check("with the SQL parser absent the engine gives no .sql node (the premise)", ".sql" not in exts, exts)
        if "tree_sitter_dm" in absent:
            check("with the DM parser absent the engine gives no .dm node (the premise)", ".dm" not in exts, exts)
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
    print("\n-- E. the static census keeps both entries (the drift locks still hold) --")
    check(".sql and .dm stay in verdict._SOURCE_EXTS", V._SOURCE_EXTS.get(".sql") == "SQL"
          and V._SOURCE_EXTS.get(".dm") == "DM", {k: V._SOURCE_EXTS.get(k) for k in (".sql", ".dm")})
    missing = sorted(e for e in G.SOURCE_CODE_EXTS if e not in V._SOURCE_EXTS)
    check("census ⊇ graphify_extract.SOURCE_CODE_EXTS (whatever find_spec says)", not missing, missing)
    opt = getattr(V, "_OPTIONAL_PARSERS", {})
    check("the optional-parser map holds the field report's two (.sql -> tree_sitter_sql, .dm -> tree_sitter_dm)",
          opt.get(".sql") == "tree_sitter_sql" and opt.get(".dm") == "tree_sitter_dm", opt)
    check("every mapped suffix is one DeepMap extracts", all(e in G.SOURCE_CODE_EXTS for e in opt), opt)


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
        except Exception:  # noqa: BLE001 — an unsourced extractor names nothing; the reverse side still checks
            continue
        mods = sorted(m for m in optional_mods if re.search(r"\b" + re.escape(m) + r"\b", src))
        if mods:
            named[ext] = mods
    problems = []
    for ext, mods in sorted(named.items()):
        if opt_map.get(ext) not in mods:
            problems.append(f"{ext}: its engine extractor needs {mods} (a graphifyy extra DeepMap does not install) "
                            f"but verdict._OPTIONAL_PARSERS maps it to {opt_map.get(ext)!r}")
    for ext, mod in sorted(opt_map.items()):
        if ext not in exts:
            problems.append(f"{ext}: in verdict._OPTIONAL_PARSERS but not in graphify_extract.SOURCE_CODE_EXTS")
        elif mod not in named.get(ext, []):
            problems.append(f"{ext}: mapped to {mod}, but its engine extractor does not need that optional module")
    return problems


def gate_e2_engine_derived_lock() -> None:
    print("\n-- E2. drift lock: _OPTIONAL_PARSERS is exactly what the ENGINE needs from its optional extras --")
    if importlib.util.find_spec("graphify") is None:
        note("graphify is not installed in this Python: engine-derived lock skipped")
        return
    import graphify.extract as GE
    getter = getattr(GE, "_get_extractor", None)
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
    opt = dict(getattr(V, "_OPTIONAL_PARSERS", {}))
    drift = _optional_parser_drift(G.SOURCE_CODE_EXTS, opt, mods, extractor_of)
    check("every extracted suffix whose engine parser is an optional extra is in verdict._OPTIONAL_PARSERS, "
          "and nothing else is", not drift, drift)
    # The lock has teeth: each drift the review named is reported.
    tf = _optional_parser_drift(tuple(G.SOURCE_CODE_EXTS) + (".tf",), opt, mods, extractor_of)
    check("teeth: mapping Terraform (.tf, tree_sitter_hcl) without the optional-parser entry is reported",
          any(p.startswith(".tf:") and "tree_sitter_hcl" in p for p in tf), tf)
    no_dm = _optional_parser_drift(G.SOURCE_CODE_EXTS, {k: v for k, v in opt.items() if k != ".dm"}, mods,
                                   extractor_of)
    check("teeth: dropping .dm from the map is reported", any(p.startswith(".dm:") for p in no_dm), no_dm)
    bogus = _optional_parser_drift(G.SOURCE_CODE_EXTS, {**opt, ".py": "tree_sitter_sql"}, mods, extractor_of)
    check("teeth: a suffix whose extractor needs no optional module (.py) in the map is reported",
          any(p.startswith(".py:") for p in bogus), bogus)


def gate_n_sql_plus_other_blind_spots() -> None:
    print("\n-- N. 0 nodes, census empty, .sql uncounted AND other source the census cannot see: no 'only' claim --")
    pm = {".sql": {"files": 12, "package": "tree-sitter-sql"}}
    others = {
        "unindexed_dirs (8 C# files in an engine-skipped dist/)":
            {"unindexed_dirs": {"dist": {"files": 8, "nodes": 0, "kind": "non-git"}}},
        "skipped_dirs (5 tracked files in build/)": {"skipped_dirs": {"build": {"files": 5, "nodes": 0}}},
        "long_paths (3 files past MAX_PATH)": {"long_paths": {"files": 3, "examples": ["a/b.cs"]}},
        "unparsed (4 .cu files a later engine parses)":
            {"unparsed": {"version": "0.8.39", "exts": {".cu": {"files": 4, "since": "0.8.40"}}}},
    }
    for what, extra in others.items():
        cov = {"languages": {}, "dirs": {}, "parser_missing": pm, **extra}
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check(f"{what}: FAILED, and the .sql files are not called the only source", v == V.FAILED
              and "only source files" not in r, (v, reasons))
    cov = {"languages": {}, "dirs": {}, "parser_missing": pm, "unindexed_dirs": {}, "skipped_dirs": {},
           "long_paths": {"files": 0}}
    v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                            coverage=cov)
    check("control: empty / zero-file blocks beside it keep the 'only source files are 12 .sql' reason",
          v == V.FAILED and "only source files found are 12 .sql" in " ".join(reasons), reasons)
    for junk in ("garbage", {"dist": "x"}, {"dist": {"files": "many"}}, [1, 2]):
        try:
            cov = {"parser_missing": pm, "unindexed_dirs": junk, "skipped_dirs": junk, "long_paths": junk,
                   "unparsed": junk}
            v, _rs = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                                coverage=cov)
            check(f"malformed neighbour blocks ({junk!r}) never raise, still FAILED", v == V.FAILED, v)
        except Exception as e:  # noqa: BLE001
            check(f"malformed neighbour blocks ({junk!r}) never raise", False, f"{type(e).__name__}: {e}")
    # End to end (the review's probe): db/*.sql + an engine-skipped dist/ of C# in a non-git tree.
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}), \
            engine_dirs(unindexed={"dist": {"files": [f"dist/A{i}.cs" for i in range(8)], "kind": "non-git"}}):
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
              and (cov.get("parser_missing") or {}).get(".sql", {}).get("files") == 12, cov)
        check("end to end: FAILED without claiming the .sql files are the only source",
              v == V.FAILED and "only source files" not in " ".join(reasons), (v, reasons))


def gate_f_other_language_still_flagged() -> None:
    print("\n-- F. an unrelated language with 0 nodes still raises its LOW COVERAGE --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": False}):
        root = Path(td)
        nodes = _tree(root, n_go=6)
        census, cov, v, reasons = _verdict(root, nodes)
        check("'LOW COVERAGE for Go' (6 files, 0 nodes)", bool(_low(reasons, "for Go")), reasons)
        check("...and no SQL reason beside it", not _low(reasons, "SQL"), reasons)
        check("the verdict is DEGRADED", v == V.DEGRADED, (v, reasons))


def gate_g_probe_raises() -> None:
    print("\n-- G. find_spec raising -> treated as importable (today's behaviour), no exception --")
    calls: dict = {}
    try:
        with tempfile.TemporaryDirectory() as td, parsers(raises=True, calls=calls):
            root = Path(td)
            nodes = _tree(root)
            census, cov, v, reasons = _verdict(root, nodes)
            check("the census counts the 12 .sql files as before", census.get("SQL") == 12, census)
            check("'LOW COVERAGE for SQL' as before, DEGRADED", bool(_low(reasons, "SQL")) and v == V.DEGRADED,
                  (v, reasons))
            check("the probe was asked (the stub saw tree_sitter_sql)", calls.get("tree_sitter_sql", 0) >= 1, calls)
        check("no exception escaped", True)
    except Exception as e:  # noqa: BLE001
        check("no exception escaped", False, f"{type(e).__name__}: {e}")


def gate_h_once_per_process() -> None:
    print("\n-- H. the parser probe runs once per process (cached) --")
    calls: dict = {}
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}, calls=calls):
        root = Path(td)
        nodes = _tree(root, n_dm=2)
        for _ in range(3):
            _verdict(root, nodes)
        check("find_spec('tree_sitter_sql') asked exactly once over three syncs' worth of counting",
              calls.get("tree_sitter_sql") == 1, calls)
        check("find_spec('tree_sitter_dm') asked exactly once", calls.get("tree_sitter_dm") == 1, calls)


def gate_i_conventional_dirs() -> None:
    print("\n-- I. the conventional-dir counter agrees (lib/ holding only .sql) --")
    for present in (False, True):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": present, "tree_sitter_dm": True}):
            root = Path(td)
            nodes = _tree(root, n_sql=4, sql_dir="lib")
            census, cov, v, reasons = _verdict(root, nodes)
            lib = (cov.get("dirs") or {}).get("lib")
            if present:
                check("parser present: lib/ counts its 4 .sql files and is flagged as before",
                      (lib or {}).get("files") == 4 and any("LOW COVERAGE in lib/" in r for r in reasons), (lib, reasons))
            else:
                check("parser absent: lib/ holds no countable source (not listed)", lib is None, cov.get("dirs"))
                check("parser absent: no 'LOW COVERAGE in lib/', READY",
                      not any("lib/" in r for r in reasons) and v == V.READY, (v, reasons))


def gate_j_engine_skipped_dirs() -> None:
    print("\n-- J. the engine-skipped and unindexed directory findings agree --")
    skipped = {"build": [f"build/m{i}.sql" for i in range(5)],
               "out": [f"out/x{i}.sql" for i in range(3)] + [f"out/Y{i}.cs" for i in range(4)]}
    unindexed = {"dist": {"files": [f"dist/z{i}.sql" for i in range(6)], "kind": "never-rescued"}}
    for present in (False, True):
        with tempfile.TemporaryDirectory() as td, \
                parsers({"tree_sitter_sql": present, "tree_sitter_dm": True}), engine_dirs(skipped, unindexed):
            root = Path(td)
            nodes = _tree(root, n_sql=0)
            census, cov, v, reasons = _verdict(root, nodes)
            sd = cov.get("skipped_dirs") or {}
            ud = cov.get("unindexed_dirs") or {}
            if present:
                check("parser present: build/ (5 .sql) is a skipped-dir finding as before",
                      (sd.get("build") or {}).get("files") == 5 and any("LOW COVERAGE in build/" in r for r in reasons),
                      (sd, reasons))
                check("parser present: out/ counts 7 files", (sd.get("out") or {}).get("files") == 7, sd)
                check("parser present: dist/ (6 .sql) is an unindexed-dir finding as before",
                      (ud.get("dist") or {}).get("files") == 6, ud)
            else:
                check("parser absent: build/ (only .sql) is no finding", "build" not in sd, sd)
                check("parser absent: out/ counts only its 4 C# files", (sd.get("out") or {}).get("files") == 4, sd)
                check("parser absent: dist/ (only .sql) is no finding", "dist" not in ud, ud)
                check("parser absent: no LOW COVERAGE naming build/ or dist/",
                      not any(("build/" in r or "dist/" in r) for r in reasons), reasons)
                pm = cov.get("parser_missing") or {}
                check("parser absent: the note counts those .sql files too (5 + 3 + 6)",
                      (pm.get(".sql") or {}).get("files") == 14, pm)


def gate_k_long_walk() -> None:
    print("\n-- K. the \\\\?\\ long-path walk agrees --")
    if os.name != "nt":
        note("verdict._walk_long walks through \\\\?\\ paths: Windows only, not run here")
        return
    for present in (False, True):
        with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": present, "tree_sitter_dm": True}):
            root = Path(td)
            _tree(root, n_cs=2, n_sql=3)
            got = sorted(Path(p).suffix for p in V._walk_long(str(root), V._dir_ignore_rule(), 1000))
            want = [".cs"] * 2 + ([".sql"] * 3 if present else [])
            check(f"parser {'present' if present else 'absent'}: _walk_long yields {sorted(set(want))}",
                  got == sorted(want), got)


def gate_l_sql_only_repo() -> None:
    print("\n-- L. a repo of only .sql files, parser absent: FAILED says why, not 'is this the right path?' --")
    with tempfile.TemporaryDirectory() as td, parsers({"tree_sitter_sql": False, "tree_sitter_dm": True}):
        root = Path(td)
        _tree(root, n_cs=0, n_sql=12)
        census = V.extension_census(root)
        cov = V.build_coverage(root, [], census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census=census, extractor="graphify",
                                ml_ok=True, mcp_ok=True, coverage=cov, extraction={"requested": "auto"})
        r = " ".join(reasons)
        check("FAILED (nothing to search)", v == V.FAILED, (v, reasons))
        check("the reason names the 12 .sql files and the parser package DeepMap does not install",
              "12" in r and ".sql" in r and "tree-sitter-sql" in r and "does not install" in r, reasons)
        check("...not the wrong-path guess", "right path" not in r, reasons)


def gate_m_never_raises() -> None:
    print("\n-- M. malformed input never raises --")
    try:
        lines = V.format_coverage({"languages": {}, "dirs": {}, "parser_missing": {".sql": None, 3: "x"}})
        check("format_coverage on a malformed parser_missing block returns a list", isinstance(lines, list), lines)
        v, rs = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={}, extractor="graphify",
                           coverage={"parser_missing": "garbage"})
        check("classify on a malformed block still returns FAILED", v == V.FAILED, (v, rs))
    except Exception as e:  # noqa: BLE001
        check("no exception escaped", False, f"{type(e).__name__}: {e}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("DeepMap: optional engine parsers (SQL / DM) and the coverage census (no Neo4j)")
    print("=" * 78)
    try:
        for g in (gate_a_sql_absent_is_ready, gate_b_sql_present_unchanged, gate_c_dm_same, gate_d_not_failed_files,
                  gate_e_static_lists, gate_e2_engine_derived_lock, gate_f_other_language_still_flagged,
                  gate_g_probe_raises, gate_h_once_per_process, gate_i_conventional_dirs, gate_j_engine_skipped_dirs,
                  gate_k_long_walk, gate_l_sql_only_repo, gate_m_never_raises, gate_n_sql_plus_other_blind_spots):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a failure, the others still run
                check(f"{g.__name__} ran without an exception", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
