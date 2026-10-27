#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: legacy UPPERCASE source filenames must still be extracted (2026-08 Delphi field report).
No Neo4j and no network. Sections A-F are hermetic (the engine is stubbed); section G deliberately
drives the REAL engine, because the worst defect in this feature is invisible to any stub.

THE RESIDUAL LIMITATION THIS CLOSES. Fixing DeepMap's own allowlist made `.pas` mappable, but the
UPSTREAM engine keys its extractor table by the EXACT suffix — `_DISPATCH.get(path.suffix)`, and file
discovery filters on `p.suffix in set(_DISPATCH)`. A legacy Windows Delphi tree of `UNIT1.PAS` /
`MAIN.DPR` therefore extracted **zero** nodes, and the only honest thing DeepMap could do was report
DEGRADED and ask a human to rename their repo. Measured on a real 2-file sample: 0 nodes before,
8 nodes after — same engine, same files.

The fix must satisfy three properties, and this gate locks all three:

  1. IT ACTUALLY WORKS — an odd-case suffix is aliased onto the extractor already registered for its
     lowercase form, so the engine discovers and parses the file.
  2. IT CANNOT REGRESS A NORMAL REPO — the parent scans first and only routes through the shim when
     odd-case source files genuinely exist. Every all-lowercase repo (including every benchmark repo)
     runs the byte-identical `python -m graphify update <root> --no-cluster` command it always did.
     This is the keystone: it is why the change is not benchmark-gated.
  3. IT CAN NEVER MAKE THINGS WORSE — `_DISPATCH` is a PRIVATE upstream symbol. A missing, renamed,
     empty or malformed table, or an unimportable engine, must degrade to "alias nothing" and let the
     run proceed exactly as an unpatched graphify would.
  4. IT SURVIVES INTO THE ENGINE'S WORKER PROCESSES (section G). graphify parallelises per-file
     extraction through a ProcessPoolExecutor once a run has >= 20 uncached files, and resolves the
     extractor inside the child. Under `spawn` (Windows always, macOS by default) that child re-imports
     the engine fresh, so a parent-only patch is GONE there: the files are discovered and then
     extracted to nothing. The measured cliff was exact — 19 uppercase files fine, 20 -> ZERO nodes —
     which means a small sample "proves" a fix that no-ops on every real codebase. Hence the alias is
     applied at MODULE IMPORT time from DEEPMAP_CASE_ALIASES (a spawn worker re-imports the shim as
     __mp_main__ and inherits the environment), and hence section G runs the real engine over 25 files.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import graphify_extract as G  # noqa: E402
import graphify_case_shim as S  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _write(root: Path, rel: str, body: str = "unit U;\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# --- A. detection: find exactly the files the engine would skip, and nothing else ----------------
def gate_detection() -> None:
    print("\n-- A. odd_case_source_suffixes: precise detection --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/main.py", "def f():\n    pass\n")
        _write(root, "src/app.ts", "export const x = 1;\n")
        _write(root, "README.md", "# hi\n")
        # THE keystone: a normal all-lowercase repo must yield NOTHING to alias, so extract() keeps
        # the byte-identical engine invocation and no benchmark repo can shift.
        check("an all-lowercase repo yields NO alias suffixes (byte-identical invocation)",
              G.odd_case_source_suffixes(root) == [], G.odd_case_source_suffixes(root))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/UNIT1.PAS")
        _write(root, "src/MAIN.DPR", "program Main;\n")
        _write(root, "src/Helpers.Inc", "const A = 1;\n")
        _write(root, "src/lower.pas")                     # already fine -> must NOT be listed
        got = G.odd_case_source_suffixes(root)
        check("uppercase/mixed Delphi suffixes are detected", got == [".DPR", ".Inc", ".PAS"], got)
        check("an already-lowercase suffix is NOT listed", ".pas" not in got, got)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "docs/NOTES.TXT", "x\n")
        _write(root, "img/LOGO.PNG", "x\n")
        _write(root, "data/CONFIG.JSON", "{}\n")
        # .json is parsed by the engine but EXCLUDED_BY_DESIGN — aliasing it would import data junk.
        check("non-source uppercase extensions are ignored (.TXT/.PNG/.JSON)",
              G.odd_case_source_suffixes(root) == [], G.odd_case_source_suffixes(root))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "node_modules/pkg/LIB.JS", "x\n")
        _write(root, "vendor/OLD.RB", "x\n")
        _write(root, "graphify-out/CACHE.PY", "x\n")
        check("vendored/generated dirs contribute no alias suffixes",
              G.odd_case_source_suffixes(root) == [], G.odd_case_source_suffixes(root))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "My.Project.Dir").mkdir()                 # a DIRECTORY with a dotted, cased name
        _write(root, "My.Project.Dir/x.py", "x = 1\n")
        check("a dotted DIRECTORY name is not mistaken for a source file",
              G.odd_case_source_suffixes(root) == [], G.odd_case_source_suffixes(root))

    check("a nonexistent root degrades to [] instead of raising",
          G.odd_case_source_suffixes(REPO / "no" / "such" / "dir") == [])


def gate_env_switch() -> None:
    print("\n-- B. DEEPMAP_CASE_INSENSITIVE_EXT: default ON, explicit opt-out --")
    prev = os.environ.pop("DEEPMAP_CASE_INSENSITIVE_EXT", None)
    try:
        check("default (unset) is ON", G.case_insensitive_ext_enabled() is True)
        for val in ("0", "false", "no", "off", "OFF"):
            os.environ["DEEPMAP_CASE_INSENSITIVE_EXT"] = val
            check(f"{val!r} disables it", G.case_insensitive_ext_enabled() is False)
        for val in ("1", "true", "on", ""):
            os.environ["DEEPMAP_CASE_INSENSITIVE_EXT"] = val
            check(f"{val!r} keeps it ON", G.case_insensitive_ext_enabled() is True)
    finally:
        os.environ.pop("DEEPMAP_CASE_INSENSITIVE_EXT", None)
        if prev is not None:
            os.environ["DEEPMAP_CASE_INSENSITIVE_EXT"] = prev


# --- C. the shim's aliasing, including every "engine is not what we expect" path -----------------
def _with_fake_engine(table, fn):
    """Run fn() with `graphify.extract` resolving to a fake carrying `table` as its `_DISPATCH`
    (table=None => the attribute is absent entirely). Restores sys.modules afterwards."""
    saved = {k: sys.modules.get(k) for k in ("graphify", "graphify.extract")}
    try:
        pkg = types.ModuleType("graphify")
        ext = types.ModuleType("graphify.extract")
        if table is not None:
            ext._DISPATCH = table
        pkg.extract = ext
        sys.modules["graphify"] = pkg
        sys.modules["graphify.extract"] = ext
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def gate_alias() -> None:
    print("\n-- C. alias_suffixes: aliases onto the lowercase extractor; degrades, never raises --")

    def pascal():
        return "PASCAL_EXTRACTOR"

    table = {".pas": pascal, ".dpr": pascal, ".py": lambda: "PY"}
    out = _with_fake_engine(dict(table), lambda: S.alias_suffixes([".PAS", ".DPR"]))
    check("odd-case suffixes alias onto the lowercase key",
          out == {".PAS": ".pas", ".DPR": ".dpr"}, out)

    # the mutation must actually land in the engine's table (that is what makes discovery work)
    live = dict(table)
    _with_fake_engine(live, lambda: S.alias_suffixes([".PAS"]))
    check("the engine's own table is mutated (so collect_files discovers the file)",
          live.get(".PAS") is live.get(".pas"), sorted(live))

    out = _with_fake_engine(dict(table), lambda: S.alias_suffixes([".py"]))
    check("an already-registered suffix is left alone", out == {}, out)

    out = _with_fake_engine(dict(table), lambda: S.alias_suffixes([".ZZZ"]))
    check("a suffix the engine cannot parse is NOT invented", out == {}, out)

    # graphify registers Fortran as BOTH `.f90` and `.F90`; resolution is case-insensitive over the
    # table's own keys, so a variant still finds its extractor whichever case the engine used.
    out = _with_fake_engine({".F90": pascal}, lambda: S.alias_suffixes([".f90"]))
    check("resolves against a MIXED-case key the engine itself registered",
          out == {".f90": ".F90"}, out)

    print("     (degradation paths - each must yield {} and never raise)")
    check("no _DISPATCH attribute -> {}",
          _with_fake_engine(None, lambda: S.alias_suffixes([".PAS"])) == {})
    check("_DISPATCH is not a dict -> {}",
          _with_fake_engine(["nope"], lambda: S.alias_suffixes([".PAS"])) == {})
    check("_DISPATCH is empty -> {}", _with_fake_engine({}, lambda: S.alias_suffixes([".PAS"])) == {})
    check("junk suffix inputs are survived",
          _with_fake_engine(dict(table), lambda: S.alias_suffixes([None, "", 5, ".PAS"]))
          == {".PAS": ".pas"})
    check("a non-str key in the engine table is skipped, not fatal",
          _with_fake_engine({7: pascal, ".pas": pascal}, lambda: S.alias_suffixes([".PAS"]))
          == {".PAS": ".pas"})


# --- D. the wiring: which command extract() actually runs ----------------------------------------
def _capture_cmd(root: Path, graph: dict | None = None):
    """Run G.extract(root) with the subprocess + engine presence stubbed; return the argv used."""
    captured: list = []
    real_run, real_spec = G.subprocess.run, importlib.util.find_spec

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        out = root / "graphify-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text(json.dumps(graph or {"nodes": [], "links": []}),
                                        encoding="utf-8")
        return _Proc()

    def fake_spec(name, *a, **kw):
        return object() if name == "graphify" else real_spec(name, *a, **kw)

    G.subprocess.run, importlib.util.find_spec = fake_run, fake_spec
    try:
        G.extract(root)
    finally:
        G.subprocess.run, importlib.util.find_spec = real_run, real_spec
    return captured[0] if captured else []


def gate_wiring() -> None:
    print("\n-- D. extract() wiring: the shim is used ONLY when it is needed --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/main.py", "def f():\n    pass\n")
        cmd = _capture_cmd(root)
        # The no-regression keystone, asserted on the REAL command line.
        check("all-lowercase repo -> the byte-identical `-m graphify update` invocation",
              cmd[1:2] == ["-m"] and cmd[2:6] == ["graphify", "update", str(root), "--no-cluster"],
              cmd)
        check("...and the shim is NOT on that command line",
              not any("graphify_case_shim" in str(c) for c in cmd), cmd)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/UNIT1.PAS")
        _write(root, "src/MAIN.DPR", "program Main;\n")
        cmd = _capture_cmd(root)
        check("odd-case repo -> routed through the case shim",
              any("graphify_case_shim.py" in str(c) for c in cmd), cmd)
        check("...with the root passed first", str(root) in cmd, cmd)
        check("...and the detected suffixes passed explicitly",
              cmd[-2:] == [".DPR", ".PAS"], cmd)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/UNIT1.PAS")
        os.environ["DEEPMAP_CASE_INSENSITIVE_EXT"] = "0"
        try:
            cmd = _capture_cmd(root)
        finally:
            os.environ.pop("DEEPMAP_CASE_INSENSITIVE_EXT", None)
        check("the opt-out restores the raw engine invocation even with odd-case files",
              cmd[1:2] == ["-m"] and not any("case_shim" in str(c) for c in cmd), cmd)

    # F3e freshness guard must still hold on the shim path: a STALE graph.json (not rewritten by this
    # run) is rejected, so a broken case-shim run can never be reported as a successful sync.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/UNIT1.PAS")
        out = root / "graphify-out"
        out.mkdir(parents=True)
        stale = out / "graph.json"
        stale.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
        old = stale.stat().st_mtime - 10_000
        os.utime(stale, (old, old))
        real_run, real_spec = G.subprocess.run, importlib.util.find_spec

        class _P:
            returncode, stdout, stderr = 1, "", "boom"

        G.subprocess.run = lambda cmd, **kw: _P()
        importlib.util.find_spec = lambda name, *a, **kw: (object() if name == "graphify"
                                                           else real_spec(name, *a, **kw))
        try:
            res = G.extract(root)
        finally:
            G.subprocess.run, importlib.util.find_spec = real_run, real_spec
        check("the F3e STALE-graph guard still rejects on the shim path", res is None, res)


def gate_shim_cli() -> None:
    print("\n-- E. shim CLI contract --")
    check("no args -> usage + non-zero exit, never a traceback", S.main([]) == 2)
    src = (REPO / "build" / "graphify_case_shim.py").read_text(encoding="utf-8")
    check("the shim never renames/moves/deletes the user's files",
          not any(tok in src for tok in ("os.rename", "shutil.move", "os.replace", "unlink",
                                         "rmtree")),
          "found a mutating call")
    check("the shim's own diagnostics go to stderr (graphify owns stdout)",
          "file=sys.stderr" in src and "[deepmap] case-insensitive extraction" in src)


# --- F. the rest of the pipeline must be case-blind too (found by the 2026-08 sweep) -------------
# NOTE ON PLATFORM: `Path.rglob("*.rb")` is case-SENSITIVE on POSIX and case-INSENSITIVE on Windows.
# That asymmetry is precisely why these defects survived — they are invisible from a Windows dev box
# and silently drop whole layers on a contributor's Linux/macOS machine or in CI. So each glob site is
# locked BOTH behaviourally (meaningful on POSIX) AND at source level (meaningful on every platform).
def gate_pipeline_case_blindness() -> None:
    print("\n-- F. downstream discovery/dispatch is case-insensitive on every platform --")
    import language_recognizers as LR
    import ruby_rails_edges as RB
    import sync as SY

    # 1. the recognizer registry's file walker — all 5 recognizers depend on it
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "app/models/ORDER.RB", "class Order < ApplicationRecord\nend\n")
        _write(root, "app/models/customer.rb", "class Customer < ApplicationRecord\nend\n")
        ruby = [e for e in LR.REGISTRY if e.name == "ruby"][0]
        rels = {rel for rel, _ in LR.iter_source(root, ruby)}
        check("iter_source yields an UPPERCASE-extension source file (ORDER.RB)",
              "app/models/ORDER.RB" in rels, sorted(rels))
        check("...and still yields the ordinary lowercase one",
              "app/models/customer.rb" in rels, sorted(rels))

    src = (REPO / "build" / "language_recognizers.py").read_text(encoding="utf-8")
    check("iter_source no longer globs a case-sensitive per-extension pattern",
          'rglob(f"*{ext}")' not in src, "the case-sensitive glob is back")
    check("...and compares suffixes lowercased", "p.suffix.lower() not in wanted" in src)

    # 2. the Ruby recognizer's per-file dispatch — pure, so meaningful on every platform
    nodes, edges = RB.recognize("<%= render 'shared/row' %>\n", "app/views/orders/INDEX.HTML.ERB")
    check("an UPPERCASE .ERB template still routes to the view recognizer",
          any(n.get("label") == "Module" for n in nodes), nodes)
    check("...and still emits its :RENDERS edge",
          any(getattr(e, "edge_type", None) == "RENDERS" for e in edges), edges)

    check("a routes file is detected case-insensitively (CONFIG/ROUTES.RB)",
          RB._is_routes_file("CONFIG/ROUTES.RB", ""), "not recognized as a routes file")
    check("...and a split routes dir too (Config/Routes/admin.rb)",
          RB._is_routes_file("Config/Routes/admin.rb", ""))
    check("a non-routes ruby file is still NOT a routes file",
          not RB._is_routes_file("app/models/order.rb", "class Order\nend\n"))

    # 3. the ast path's own Python discovery (shared by the full walk AND the incremental cache)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "APP.PY", "def f():\n    pass\n")
        _write(root, "ok.py", "def g():\n    pass\n")
        rels = {rel for _, rel in SY._iter_ast_files(root)}
        check("_iter_ast_files yields an UPPERCASE .PY file", "APP.PY" in rels, sorted(rels))
        check("...alongside the lowercase one", "ok.py" in rels, sorted(rels))

    sync_src = (REPO / "build" / "sync.py").read_text(encoding="utf-8")
    check("_iter_ast_files no longer uses the case-sensitive rglob('*.py')",
          'rglob("*.py")' not in sync_src, "the case-sensitive glob is back")

    # 4. The JUnit filename rule is the ONE place case-sensitivity is CORRECT, and this locks it that
    # way. It matches a bare `Test`/`Tests` with no separator anchor, so the capital T is its only
    # discriminator: lowercasing it (attempted during this sweep, then reverted) makes Latest.java /
    # Contest.java / Attest.java match and silently drops those production classes from the graph
    # under DEEPMAP_EXCLUDE_TESTS. Uppercase-.JAVA trees are still covered by the DIRECTORY rule,
    # which does lowercase its components — so nothing is lost by leaving this rule case-sensitive.
    check("FooTest.java is a test path", SY._is_test_path("src/FooTest.java"))
    check("Latest.java is NOT (the over-match a 'consistency' lowercase would cause)",
          not SY._is_test_path("src/Latest.java"))
    check("Contest.java is NOT either", not SY._is_test_path("src/Contest.java"))
    check("an uppercase java test under src/test/ is STILL caught by the directory rule",
          SY._is_test_path("src/test/java/com/app/USERTEST.JAVA"))
    check("a non-test java file is still not a test path",
          not SY._is_test_path("src/main/java/com/acme/Order.java"))


# --- G. THE REGRESSION LOCK: the real engine, ABOVE its parallel threshold ------------------------
# This gate exists because a hermetic gate cannot catch the defect it locks. graphify hands per-file
# work to a ProcessPoolExecutor once a run has >= 20 uncached files, and the worker resolves the
# extractor INSIDE the child process. Under `spawn` (Windows always, macOS by default) that child
# re-imports the engine fresh, so an alias applied only in the shim's parent process is GONE in the
# worker: files are discovered and then extracted to nothing. The cliff is exact — 19 uppercase files
# extracted fine while 20 produced ZERO nodes — so the original 2-file sample "proved" a fix that
# silently no-opped on every real legacy codebase, i.e. on exactly the repo class this feature exists
# for. Everything below the threshold, and every stub, passes either way. Only a real run above the
# threshold distinguishes them, so this gate drives the real engine over 25 files.
def gate_real_engine_above_parallel_threshold() -> None:
    print("\n-- G. real engine, 25 UPPERCASE files (above graphify's ProcessPoolExecutor threshold) --")
    if importlib.util.find_spec("graphify") is None:
        print("     (graphify not installed — real-engine gate skipped)")
        return
    unit = ("unit Unit{i};\n"
            "interface\n"
            "type\n"
            "  TSvc{i} = class\n"
            "  public\n"
            "    function Run(A: Integer): Integer;\n"
            "    function Calc(A: Integer): Integer;\n"
            "  end;\n"
            "implementation\n"
            "function TSvc{i}.Calc(A: Integer): Integer;\n"
            "begin\n"
            "  Result := A * 2;\n"
            "end;\n"
            "function TSvc{i}.Run(A: Integer): Integer;\n"
            "begin\n"
            "  Result := Calc(A);\n"
            "end;\n"
            "end.\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(25):
            _write(root, f"src/UNIT{i}.PAS", unit.format(i=i))
        got = G.extract(root)
        nodes, calls = (got[0], got[1]) if got else ([], [])
        # The exact regression: this was 0 with a parent-only patch.
        check("25 uppercase .PAS files extract to a NON-EMPTY graph", len(nodes) > 0,
              "0 nodes — the alias did not survive into the ProcessPoolExecutor workers")
        check("...every unit is represented (>= 25 nodes)", len(nodes) >= 25, len(nodes))
        names = {n["name"] for n in nodes}
        check("...classes are typed from an uppercase file",
              any(n["label"] == "Class" and n["name"] == "TSvc0" for n in nodes), sorted(names)[:6])
        check("...methods resolve as Class.method", "TSvc0.Run" in names, sorted(names)[:6])
        check("...and intra-unit CALLS survive", len(calls) > 0, calls[:3])

    # The control: the SAME tree in lowercase must not need the shim at all, and must not be worse.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(25):
            _write(root, f"src/unit{i}.pas", unit.format(i=i))
        check("the lowercase control repo needs no aliasing",
              G.odd_case_source_suffixes(root) == [], G.odd_case_source_suffixes(root))
        got = G.extract(root)
        check("...and still extracts normally", bool(got) and len(got[0]) >= 25,
              len(got[0]) if got else None)


def main() -> int:
    print("=" * 78)
    print("CASE-INSENSITIVE EXTRACTION GATES - legacy UPPERCASE source files (no Neo4j, no engine)")
    print("=" * 78)
    # extract() writes graphify's output under DEEPMAP_HOME (graphify_extract.graphify_out_dir) —
    # keep this run off the real ~/.deepmap.
    home, saved = tempfile.mkdtemp(prefix="dm_case_home_"), os.environ.get("DEEPMAP_HOME")
    os.environ["DEEPMAP_HOME"] = home
    try:
        gate_detection()
        gate_env_switch()
        gate_alias()
        gate_wiring()
        gate_shim_cli()
        gate_pipeline_case_blindness()
        gate_real_engine_above_parallel_threshold()
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} - {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
