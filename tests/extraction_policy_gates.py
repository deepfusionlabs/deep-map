#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-2 gates for the extraction engine (2026-09 live-validation fix batch, owner W5). No Neo4j, no spend.

What these lock (each was a confirmed review finding or a binding round-2 decision, D5):

  D5a  The G3 rescue of engine-skipped directories never re-includes the virtualenv/env family
       (env, .env, *_env, venv, .venv, *_venv, site-packages, lib64) or generated output (dist,
       dist-protected). Before: a tracked ncc bundle in dist/index.js was re-included, duplicating every
       src/ symbol (AMBIGUOUS fan-out) and pulling bundled third-party code into search/impact.
  D5b  For the other skipped names (build, out, target, snapshots, ...) a file qualifies only when it is
       git-tracked, not gitignored, and passes DeepMap's vendor/minified filter (sync's rules) AND does not
       look generated (bundler runtime, tsc/babel CommonJS output, a sourceMappingURL trailer, minified
       lines, a .d.ts, a "DO NOT EDIT"/@generated header). Before: a tracked dist/app.min.js made the
       verdict DEGRADED ("could not re-include it") while sync dropped its nodes anyway.
  D5c  Extraction and verdict agree file-for-file: graphify walks a rescued directory whole, so anything
       under it that does not qualify (an untracked scratch file, a minified or generated file) is
       dropped from the result. The verdict flags only qualifying dirs, and says how to act on it. An
       UPPERCASE source file inside a re-included dir (build/UNIT1.PAS) is aliased too (a round-1
       residual). The census ignores what the engine never walks (an `env/` virtualenv, a TypeScript
       project's compiled out/), so it cannot raise a false per-language LOW COVERAGE.
  D6   Verdict remedies are runnable as written: the exact pip command for DeepMap's own interpreter
       instead of "run the installer", and /deep-map:doctor / /deep-map:refresh in colon form.
  G5  Self-call re-binding stays default ON with an opt-out, DEEPMAP_SELF_CALL_REBIND=0, which restores
       graphify's own binding byte for byte (parity for the STAGE-0 oracle / benchmark re-runs). The run
       report + _rebuild_code info say whether it ran and how many calls it moved.
  RS   Method names whose owner is a Rust/Go generic or reference type are stored clean
       (`&'a mut Serializer<W, F>.serialize_str` -> `Serializer.serialize_str`, `List[T].Push` ->
       `List.Push`); plausible owners (every Python/Java/C#/TS class) are untouched. Opt-out:
       DEEPMAP_CLEAN_OWNER_NAMES=0.
  G1+  Each graphify output dir carries an owner marker so `instance-rm --purge` can find every dir a
       project left under <DEEPMAP_HOME>/gout/ (graphify_extract.gout_dirs_for_project).
  G4+  DEEPMAP_EXTRACTOR=graphify with a failing engine: the verdict's FAILED reason names the engine's
       error (it was only printed before, so the persisted reason said nothing useful).
  E2E  The real sync() (real _rebuild_code + the installed engine; Neo4j stubbed) on a git repo with
       hand-written build/ source next to dist/ bundles and minified files: the graph holds build/core.py
       and none of the generated files. Since round 7 the manifest verdict is DEGRADED, naming only the
       never-rescued dirs that hold hand-written tracked source (.env/, config/env/, dist-protected/,
       services/payment_env/) — they are still never re-included, but no longer silently missing.

Hermetic: a throwaway DEEPMAP_HOME, temp git repos. REAL sections drive the installed graphify engine
(skipped when absent).
"""
from __future__ import annotations

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

import graphify_extract as GX  # noqa: E402
import verdict as V  # noqa: E402

_PASS = _FAIL = 0
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
_HAS_GIT = shutil.which("git") is not None


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail != '' else ''}")


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_policy", BUILD / "sync.py")
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


def _write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)


def _git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.autocrlf", "false")


def _git_commit_all(root: Path) -> None:
    _git(root, "add", "-A", "-f")
    _git(root, "commit", "-q", "-m", "snap")


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


@contextlib.contextmanager
def _stub_engine(on_graphify):
    """Route ONLY the engine invocation to `on_graphify(cmd, kw)`; git runs for real."""
    real_run, real_spec = GX.subprocess.run, importlib.util.find_spec

    def fake_run(cmd, *a, **kw):
        flat = " ".join(str(c) for c in cmd)
        if "graphify" in flat and ("-m" in cmd or "graphify_case_shim" in flat):
            return on_graphify(list(cmd), kw)
        return real_run(cmd, *a, **kw)

    def fake_spec(name, *a, **kw):
        return object() if name == "graphify" else real_spec(name, *a, **kw)

    GX.subprocess.run, importlib.util.find_spec = fake_run, fake_spec
    try:
        yield
    finally:
        GX.subprocess.run, importlib.util.find_spec = real_run, real_spec


# ---- fixture content ----------------------------------------------------------------------------
_NCC_BUNDLE = ("/******/ (() => { // webpackBootstrap\n/******/ \tvar __webpack_modules__ = ({\n"
               "/***/ 1: ((module) => { module.exports = 1; })\n/******/ \t});\n"
               "function __nccwpck_require__(id) { return __webpack_modules__[id]; }\n"
               "function run() { return helper(); }\nfunction helper() { return depFromNodeModules(); }\n"
               "function depFromNodeModules() { return 1; }\n/******/ })();\n")
_TSC_OUT = ('"use strict";\nObject.defineProperty(exports, "__esModule", { value: true });\n'
            "exports.helper = helper;\nfunction helper() { return 1; }\n"
            "//# sourceMappingURL=compiled.js.map\n")
_MINIFIED = "function a(){return 1}var b=\"" + ("x" * 3000) + "\";function c(){return a()}\n"
_GO_GENERATED = "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage gen\n\nfunc Gen() int { return 1 }\n"
_DTS = "export declare function helper(): number;\n"
_SRC_TS = "export function run() { return helper(); }\nexport function helper() { return 1; }\n"


def _policy_repo(root: Path) -> None:
    """The D5 fixture: hand-written tracked source under build/ and out/; generated / env-family /
    dist content that must never be re-included."""
    _git_repo(root)
    _write(root, ".gitignore", "build/local_gen.py\n")
    _write(root, "pyproject.toml", "[project]\nname='x'\n")
    _write(root, "src/main.ts", _SRC_TS)
    _write(root, "src/app.py", "def app_main():\n    return core_fn()\n")
    _write(root, "build/core.py", "def core_fn():\n    return 1\n")                # hand-written: rescue
    _write(root, "build/app.min.js", _MINIFIED)                                     # minified suffix
    _write(root, "build/types.d.ts", _DTS)                                          # tsc declarations
    _write(root, "out/tool.py", "def tool():\n    return 2\n")                     # hand-written: rescue
    _write(root, "out/bundle.js", _NCC_BUNDLE)                                      # bundler runtime
    _write(root, "out/compiled.js", _TSC_OUT)                                       # tsc CommonJS output
    _write(root, "dist/index.js", _NCC_BUNDLE)                                      # dist: never
    _write(root, "dist-protected/x.py", "def protected():\n    return 1\n")         # dist family: never
    _write(root, "config/env/settings.py", "def load_settings():\n    return {}\n")  # env: never
    _write(root, "services/payment_env/charge.py", "def charge_card():\n    return True\n")  # *_env
    _write(root, ".env/tool.py", "def dotenv_tool():\n    return 1\n")                # .env: never
    _write(root, "lib64/python3/x.py", "def venv_lib():\n    return 1\n")            # venv lib64: never
    _write(root, "target/gen.go", _GO_GENERATED)                                    # DO NOT EDIT header
    _git_commit_all(root)
    _write(root, "build/scratch.py", "def scratch():\n    return 0\n")              # untracked
    _write(root, "build/local_gen.py", "def local_gen():\n    return 0\n")          # gitignored


# ================================================================================================
# D5a — never-rescue family
# ================================================================================================
def gate_d5_never_rescue() -> None:
    print("\n-- D5a: the env/venv family and dist are NEVER re-included; build/out/target/snapshots may be --")
    nr = getattr(GX, "_never_rescue", None)
    check("graphify_extract._never_rescue exists", callable(nr))
    if not callable(nr):
        return
    for name in ("env", ".env", "payment_env", "venv", ".venv", "my_venv", "site-packages", "lib64",
                 "dist", "dist-protected", "node_modules", "ENV", "Dist"):
        check(f"'{name}' is never re-included", nr(name) is True)
    for name in ("build", "out", "target", "snapshots", "__snapshots__", "visual-tests", "Build"):
        check(f"'{name}' may be re-included (subject to the per-file filter)", nr(name) is False)


# ================================================================================================
# D5b — per-file qualification (vendor/minified mirror + generated-content check)
# ================================================================================================
def gate_d5_generated_detector() -> None:
    print("\n-- D5b: a file under an engine-skipped dir qualifies only if it looks hand-written --")
    fn = getattr(GX, "_looks_generated", None)
    check("graphify_extract._looks_generated exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cases = {
            "b/bundle.js": (_NCC_BUNDLE, True), "b/compiled.js": (_TSC_OUT, True),
            "b/min.js": (_MINIFIED, True), "b/gen.go": (_GO_GENERATED, True), "b/types.d.ts": (_DTS, True),
            "b/types.D.TS": (_DTS, True),
            "b/fb.py": ("# @generated by thrift\ndef f():\n    pass\n", True),
            "b/cs.cs": ("// <auto-generated>\n//   This code was generated by a tool.\n// </auto-generated>\n"
                        "class A {}\n", True),
            "b/core.py": ("def core_fn():\n    return 1\n", False),
            "b/hand.js": ("export function run() {\n  return helper();\n}\n", False),
            "b/hand.ts": (_SRC_TS, False),
            "b/django.py": ("# Generated by Django 4.2 on 2023-01-01\nfrom django.db import migrations\n", False),
            "b/main.go": ("package main\n\nfunc main() {}\n", False),
        }
        for rel, (text, want) in cases.items():
            p = _write(root, rel, text)
            got = fn(p)
            check(f"{rel}: {'generated' if want else 'hand-written'}", got is want, got)
        check("an unreadable path is not called generated (never raises)", fn(root / "missing.js") is False)
        # round 3: a compiler/bundler writes a source map NEXT TO its output — plain-looking compiled JS
        # (no runtime marker, no trailer) with a sibling `.js.map` is still tool output (the live E3 check
        # on graphify 0.9.68 listed such an out/ as 12 "hand-written" files).
        p = _write(root, "o/mod0.js", "exports.m0 = function () { return 0; };\n")
        check("o/mod0.js WITHOUT a sibling map: hand-written", fn(p) is False)
        _write(root, "o/mod0.js.map", "{}\n")
        check("o/mod0.js WITH a sibling o/mod0.js.map: generated", fn(p) is True)
        m = _write(root, "o/esm.mjs", "export const x = 1;\n")
        _write(root, "o/esm.mjs.map", "{}\n")
        check("o/esm.mjs with esm.mjs.map: generated", fn(m) is True)
        py = _write(root, "o/tool.py", "def tool():\n    return 1\n")
        _write(root, "o/tool.py.map", "{}\n")
        check("a sibling .map only counts for JavaScript (o/tool.py stays hand-written)", fn(py) is False)


def gate_d5_skipped_source_dirs(home: Path) -> None:
    print("\n-- D5b: skipped_source_dirs lists only qualifying hand-written tracked source --")
    if not (_HAS_GIT and _HAS_ENGINE):
        print("     (needs git + graphify — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _policy_repo(root)
        got = GX.skipped_source_dirs(root)
        check("exactly build/ and out/ qualify, each with its hand-written file only",
              got == {"build": ["build/core.py"], "out": ["out/tool.py"]}, got)
        check("dist/ (a tracked ncc bundle) is not re-included", "dist" not in got, got)
        check("the env family (env/, *_env, .env, lib64) is not re-included",
              not any(k.split("/")[-1] in ("env", "payment_env", ".env", "lib64") for k in got), got)
        check("target/ holding only a DO-NOT-EDIT generated file is not listed", "target" not in got, got)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git_repo(root)
        _write(root, "src/main.py", "def main():\n    return 1\n")
        _write(root, "dist/app.min.js", _MINIFIED)
        _write(root, "build/vendor.bundle.js", "function x(){return 1}\n")
        _git_commit_all(root)
        got = GX.skipped_source_dirs(root)
        check("the review repro: tracked dist/app.min.js + build/*.bundle.js only -> nothing to rescue",
              got == {}, got)
        cov = V.build_coverage(root, [{"source_file": "src/main.py"}])
        v, r = V.classify(neo4j_ok=True, n_nodes=1, n_embedded=1, census={"Python": 1},
                          extractor="graphify", ml_ok=True, coverage=cov)
        check("...so the verdict is READY (no false 'could not re-include' DEGRADED)", v == V.READY, (v, r))
        seen: dict = {}

        def on_graphify(cmd, kw):
            seen["cmd"] = cmd
            return _Proc(1)

        with _stub_engine(on_graphify), contextlib.redirect_stderr(io.StringIO()):
            GX.extract(root)
        check("...and the engine runs with the plain `-m graphify update` argv (no shim)",
              (seen.get("cmd") or [None, None])[1] == "-m", seen.get("cmd"))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git_repo(root)
        _write(root, ".gitignore", "out\n")
        _write(root, "src/main.py", "def main():\n    return 1\n")
        _write(root, "out/forced.py", "def forced():\n    return 1\n")
        _git_commit_all(root)                     # -f: out/forced.py is tracked despite the ignore
        got = GX.skipped_source_dirs(root)
        check("a tracked file under a bare .gitignore dir (sync's vendor rule drops it) is not rescued",
              got == {}, got)


# ================================================================================================
# D5c — extraction == verdict, file for file
# ================================================================================================
def gate_d5_real_extract(home: Path) -> None:
    print("\n-- D5c REAL: the rescued dirs contribute ONLY their qualifying files --")
    if not (_HAS_GIT and _HAS_ENGINE):
        print("     (needs git + graphify — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _policy_repo(root)
        res, rep = GX.extract_with_report(root)
        files = sorted({n["source_file"] for n in (res[0] if res else [])})
        for f in ("src/app.py", "src/main.ts", "build/core.py", "out/tool.py"):
            check(f"{f} is in the graph", f in files, files)
        for f in ("build/app.min.js", "build/types.d.ts", "build/scratch.py", "build/local_gen.py",
                  "out/bundle.js", "out/compiled.js", "dist/index.js", "dist-protected/x.py",
                  "config/env/settings.py", "services/payment_env/charge.py", ".env/tool.py",
                  "lib64/python3/x.py", "target/gen.go"):
            check(f"{f} is NOT in the graph", f not in files, files)
        check("the report names the rescued dirs", sorted((rep or {}).get("rescued_dirs") or []) == ["build", "out"],
              rep)
        names = {n["name"] for n in (res[0] if res else [])}
        check("no duplicate bundle / compiled / declaration copy of src/main.ts's helper",
              sum(1 for n in (res[0] if res else []) if n["name"] == "helper") <= 1, sorted(names))
        cov = V.build_coverage(root, res[0] if res else [])
        v, r = V.classify(neo4j_ok=True, n_nodes=len(res[0]) if res else 0, n_embedded=1,
                          census=V.extension_census(root), extractor="graphify", ml_ok=True, coverage=cov)
        # Round 7 (I3 — T1#0/T2#0/T3#0): D5 still never re-includes the env family / dist, but hand-written
        # tracked source there is no longer SILENTLY missing: the verdict names exactly those dirs (and
        # nothing the rescue brought in, nothing generated, not the lib64 virtualenv part).
        flagged = sorted((cov.get("unindexed_dirs") or {}))
        check("extraction and verdict agree: DEGRADED naming exactly the never-rescued dirs that hold "
              "hand-written tracked source (was a silent READY)",
              v == V.DEGRADED and flagged == [".env", "config/env", "dist-protected", "services/payment_env"]
              and not any(x.startswith(("LOW COVERAGE in build/", "LOW COVERAGE in out/")) for x in r),
              (v, r, flagged))

    print("   names-mode payload (a very long rescue list) still keeps only qualifying files")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _policy_repo(root)
        _write(root, "docs/build/untracked_helper.py", "def untracked_helper():\n    return 1\n")
        saved = GX._RESCUE_ENV_MAX
        GX._RESCUE_ENV_MAX = 10                  # force the degraded rescue-by-NAME payload
        try:
            res, _rep = GX.extract_with_report(root)
        finally:
            GX._RESCUE_ENV_MAX = saved
        files = sorted({n["source_file"] for n in (res[0] if res else [])})
        check("build/core.py still extracted in names mode", "build/core.py" in files, files)
        check("an untracked docs/build/ file walked by the name rescue is dropped",
              "docs/build/untracked_helper.py" not in files, files)


_PAS_UNIT = ("unit Unit1;\ninterface\ntype\n  TSvc = class\n  public\n    function Run(A: Integer): Integer;\n"
             "    function Calc(A: Integer): Integer;\n  end;\nimplementation\n"
             "function TSvc.Calc(A: Integer): Integer;\nbegin\n  Result := A * 2;\nend;\n"
             "function TSvc.Run(A: Integer): Integer;\nbegin\n  Result := Calc(A);\nend;\nend.\n")


def gate_d5_odd_case_in_rescued_dir(home: Path) -> None:
    print("\n-- D5c REAL: an UPPERCASE source file inside a re-included dir is aliased too --")
    if not (_HAS_GIT and _HAS_ENGINE):
        print("     (needs git + graphify — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git_repo(root)
        _write(root, "src/main.py", "def main():\n    return 1\n")
        _write(root, "build/UNIT1.PAS", _PAS_UNIT)
        _git_commit_all(root)
        seen: dict = {}
        real_run = GX.subprocess.run

        def spy(cmd, *a, **kw):
            if "graphify_case_shim" in " ".join(str(c) for c in cmd):
                seen["cmd"] = list(cmd)
            return real_run(cmd, *a, **kw)

        GX.subprocess.run = spy
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                res = GX.extract(root)
        finally:
            GX.subprocess.run = real_run
        files = {n["source_file"] for n in (res[0] if res else [])}
        check("the shim is told to alias .PAS (found inside the re-included build/)",
              ".PAS" in (seen.get("cmd") or []), seen.get("cmd"))
        check("build/UNIT1.PAS is extracted", "build/UNIT1.PAS" in files, sorted(files))
        cov = V.build_coverage(root, res[0] if res else [])
        check("...so the verdict has no skipped-dir gap for build/",
              (cov.get("skipped_dirs") or {}).get("build", {}).get("nodes", 0) >= 1, cov.get("skipped_dirs"))


def gate_d5_keep_rescued_unit() -> None:
    print("\n-- D5c unit: _keep_rescued_only drops non-qualifying rescued nodes + their edges --")
    fn = getattr(GX, "_keep_rescued_only", None)
    check("graphify_extract._keep_rescued_only exists", callable(fn))
    if not callable(fn) or not _HAS_ENGINE:
        print("     (needs the engine's skip rule — remainder skipped)")
        return
    nodes = [{"key": "k1", "source_file": "build/core.py"}, {"key": "k2", "source_file": "build/gen.js"},
             {"key": "k3", "source_file": "src/a.py"}, {"key": "k4", "source_file": "src/build_tools.py"}]
    calls = [("k3", "k2"), ("k2", "x"), ("k3", "k1"), ("k4", "core_fn")]
    # R11/R1: in precise mode map_graph hands the engine-resolved callees over as MARKED keys (CallKey)
    key_calls = [("k3", GX.call_key("k2")), ("k2", "x"), ("k3", GX.call_key("k1")), ("k4", "core_fn")]
    imports = [("k2", "os"), ("k3", "os")]
    skipped = {"build": ["build/core.py"]}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        with _env(DEEPMAP_PRECISE_CALLS="1"):
            (n, c, i), dropped = fn((nodes, key_calls, imports), root, skipped)
        check("the non-qualifying rescued node is dropped (a qualifying one and non-skipped ones stay)",
              [x["key"] for x in n] == ["k1", "k3", "k4"] and dropped == 1, (n, dropped))
        check("precise mode: its outgoing calls, incoming key-calls and imports go too",
              c == [("k3", "k1"), ("k4", "core_fn")] and i == [("k3", "os")], (c, i))
        with _env(DEEPMAP_PRECISE_CALLS=None):
            (_n, c2, _i), _d = fn((nodes, calls, imports), root, skipped)
        check("name mode: a callee NAME is never mistaken for a dropped key",
              ("k3", "k2") in c2 and ("k2", "x") not in c2, c2)
        same = fn((nodes, calls, imports), root, {})
        check("nothing rescued -> the SAME objects come back", same[0][0] is nodes and same[1] == 0)


def gate_d5_verdict_wording(home: Path) -> None:
    print("\n-- D5c: the skipped-dir reason is actionable as written (and honest about the opt-out) --")
    cov = {"languages": {}, "dirs": {}, "skipped_dirs": {"build": {"files": 3, "nodes": 0}}}
    with _env(DEEPMAP_RESCUE_SKIPPED_DIRS=None):
        v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=5, census={"Python": 5},
                          extractor="graphify", ml_ok=True, coverage=cov)
    reason = next((x for x in r if x.startswith("LOW COVERAGE in build/")), "")
    check("tracked source still missing -> DEGRADED with the LOW COVERAGE head", v == V.DEGRADED and reason, r)
    check("...remedy is runnable as written (names /deep-map:refresh, not an env-var re-run)",
          "/deep-map:refresh" in reason and "DEEPMAP_EXTRACTOR=ast" not in reason, reason)
    with _env(DEEPMAP_RESCUE_SKIPPED_DIRS="0"):
        v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=5, census={"Python": 5},
                          extractor="graphify", ml_ok=True, coverage=cov)
    reason = next((x for x in r if x.startswith("LOW COVERAGE in build/")), "")
    check("with DEEPMAP_RESCUE_SKIPPED_DIRS=0 the reason says re-inclusion is switched OFF",
          "DEEPMAP_RESCUE_SKIPPED_DIRS" in reason and "could not re-include" not in reason, reason)


def gate_d6_remedies() -> None:
    print("\n-- D6: verdict remedies are runnable as written (no installer path the user cannot run) --")
    # a Python the operator manages (DEEPMAP_MANAGED_VENV=0): without it, a Python that lacks the ML stack (CI installs
    # none) is sent to /deep-map:setup instead — tests/setup_bootstrap_gates.py section L
    with _env(DEEPMAP_MANAGED_VENV="0"):
        v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=0, census={"Python": 5}, extractor="graphify",
                          ml_ok=False)
    reason = next((x for x in r if x.startswith("VECTOR SEARCH DISABLED")), "")
    check("ML stack missing (DEEPMAP_MANAGED_VENV=0) -> the exact pip command for DeepMap's OWN interpreter (quoted, "
          "forward slashes: Git Bash eats an unquoted path's backslashes)",
          f'"{Path(sys.executable).as_posix()}" -m pip install' in reason and "sentence-transformers" in reason,
          reason)
    check("...then /deep-map:setup (colon form), and no bare 'install.ps1 / install.sh'",
          "/deep-map:setup" in reason and "install.ps1" not in reason, reason)
    v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=0, census={"Python": 5}, extractor="graphify",
                      ml_ok=True)
    reason = next((x for x in r if x.startswith("VECTOR SEARCH DISABLED")), "")
    check("ML stack present but nothing embedded -> /deep-map:refresh + /deep-map:doctor, no pip",
          "/deep-map:refresh" in reason and "/deep-map:doctor" in reason and "pip install" not in reason, reason)
    cov = {"languages": {"Go": {"files": 40, "nodes": 0}}, "dirs": {}}
    v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=5, census={"Go": 40, "Python": 5},
                      extractor="graphify", ml_ok=True, coverage=cov)
    reason = next((x for x in r if x.startswith("LOW COVERAGE for Go")), "")
    check("the per-language LOW COVERAGE reason names /deep-map:doctor (colon form)",
          "/deep-map:doctor" in reason, reason)


def gate_d5_census(home: Path) -> None:
    print("\n-- D5c: the census ignores what the engine never walks (env/venv family, out/ build output) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(12):
            _write(root, f"src/m{i}.ts", f"export function f{i}() {{ return {i}; }}\n")
        for i in range(15):
            _write(root, f"out/m{i}.js", _TSC_OUT)                         # compiled output (untracked)
        for i in range(30):
            _write(root, f"env/lib/python3.12/site-packages/pkg/m{i}.py")  # `python -m venv env`
            _write(root, f"tools_env/lib/m{i}.py")
        for i in range(12):
            _write(root, f"lib64/python3.12/m{i}.py")
            _write(root, f"dist-protected/m{i}.js", "function x(){}\n")
        census = V.extension_census(root)
        check("census counts only the project's own TypeScript", census == {"TypeScript": 12}, census)
        nodes = [{"source_file": f"src/m{i}.ts"} for i in range(12)]
        cov = V.build_coverage(root, nodes, census=census)
        v, r = V.classify(neo4j_ok=True, n_nodes=12, n_embedded=12, census=census, extractor="graphify",
                          ml_ok=True, coverage=cov)
        check("...so no false 'LOW COVERAGE for JavaScript/Python'",
              not any(x.startswith("LOW COVERAGE for") for x in r), (v, r))
        # Round 7 (I3): this is a NON-git tree, so nothing under an engine-skipped dir is re-included —
        # hand-written source there is named instead (tools_env/ has no virtualenv markers; dist-protected/
        # holds plain JS); the real `python -m venv env` and lib64/ stay unflagged.
        check("round 7: the non-git tree's hand-written tools_env/ and dist-protected/ are named, the "
              "virtualenv env/ and lib64/ are not",
              sorted((cov.get("unindexed_dirs") or {})) == ["dist-protected", "tools_env"],
              cov.get("unindexed_dirs"))


# ================================================================================================
# G5 — self-call re-binding opt-out
# ================================================================================================
_CORE_PY = """\
class Context:
    def invoke(self, callback):
        return callback()

    def forward(self, cmd):
        return self.invoke(cmd)


class Group:
    def invoke(self, ctx):
        return ctx
"""


def _core_graph() -> dict:
    def n(i, label, line):
        return {"id": i, "label": label, "file_type": "code", "source_file": "pkg/core.py",
                "source_location": f"L{line}"}

    def lk(rel, s, t, line):
        return {"source": s, "target": t, "relation": rel, "source_file": "pkg/core.py",
                "source_location": f"L{line}"}

    return {"nodes": [n("m", "core.py", 1), n("ctx", "Context", 1), n("ctx_inv", ".invoke()", 2),
                      n("ctx_fwd", ".forward()", 5), n("grp", "Group", 9), n("grp_inv", ".invoke()", 10)],
            "links": [lk("contains", "m", "ctx", 1), lk("method", "ctx", "ctx_inv", 2),
                      lk("method", "ctx", "ctx_fwd", 5), lk("contains", "m", "grp", 9),
                      lk("method", "grp", "grp_inv", 10), lk("calls", "ctx_fwd", "grp_inv", 6)]}


def gate_g5_opt_out(home: Path) -> None:
    print("\n-- G5: DEEPMAP_SELF_CALL_REBIND=0 restores graphify's own binding byte for byte --")
    en = getattr(GX, "self_call_rebind_enabled", None)
    check("graphify_extract.self_call_rebind_enabled exists", callable(en))
    with _env(DEEPMAP_SELF_CALL_REBIND=None):
        check("default ON", callable(en) and en() is True)
    with _env(DEEPMAP_SELF_CALL_REBIND="0"):
        check("'0' turns it off", callable(en) and en() is False)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "pkg/core.py", _CORE_PY)
        g = _core_graph()
        legacy = GX.map_graph(g)
        with _env(DEEPMAP_SELF_CALL_REBIND=None):
            on = GX.map_graph(g, root=root)
        with _env(DEEPMAP_SELF_CALL_REBIND="0"):
            off = GX.map_graph(g, root=root)
        check("default: self.invoke() in Context.forward -> Context.invoke",
              ("ctx_fwd", "Context.invoke") in on[1], on[1])
        check("opt-out: identical to pure mode (graphify's pick kept)", off == legacy, (off[1], legacy[1]))
        stats: dict = {}
        try:
            with _env(DEEPMAP_SELF_CALL_REBIND=None):
                GX.map_graph(g, root=root, stats=stats)
        except TypeError as e:
            check("map_graph accepts stats=", False, e)
        check("map_graph reports how many calls it re-bound", stats.get("self_calls_rebound") == 1, stats)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def fake(cmd, kw):
            gout = Path(kw["env"]["GRAPHIFY_OUT"])
            gout.mkdir(parents=True, exist_ok=True)
            (gout / "graph.json").write_text(json.dumps(_core_graph()), encoding="utf-8")
            return _Proc(0)

        _write(root, "pkg/core.py", _CORE_PY)
        err = io.StringIO()
        with _env(DEEPMAP_SELF_CALL_REBIND=None), _stub_engine(fake), contextlib.redirect_stderr(err):
            _res, rep = GX.extract_with_report(root)
        check("the run report records self_call_rebind=True + the count",
              rep.get("self_call_rebind") is True and rep.get("self_calls_rebound") == 1, rep)
        check("...and the log says so, naming the opt-out", "DEEPMAP_SELF_CALL_REBIND=0" in err.getvalue(),
              err.getvalue()[-400:])
        with _env(DEEPMAP_SELF_CALL_REBIND="0"), _stub_engine(fake), contextlib.redirect_stderr(io.StringIO()):
            _res, rep = GX.extract_with_report(root)
        check("opt-out: the report records self_call_rebind=False",
              rep.get("self_call_rebind") is False and not rep.get("self_calls_rebound"), rep)
        S = _load_sync()
        S.graphify_extract = SimpleNamespace(extract_with_report=lambda r, **k: (
            ([{"key": "k", "label": "Function", "name": "f", "source_file": "a.py", "start_line": 1,
               "body_summary": "f"}], [], []),
            {"self_call_rebind": True, "self_calls_rebound": 4}))
        with _env(DEEPMAP_EXTRACTOR=None), contextlib.redirect_stdout(io.StringIO()):
            ext = S._rebuild_code(root)
        info = getattr(ext, "info", {}) or {}
        check("_rebuild_code info carries self_call_rebind + self_calls_rebound",
              info.get("self_call_rebind") is True and info.get("self_calls_rebound") == 4, info)


# ================================================================================================
# RS — clean owner names for generic / reference owner types
# ================================================================================================
def _owner_graph() -> dict:
    def n(i, label, line, sf="src/lib.rs"):
        return {"id": i, "label": label, "file_type": "code", "source_file": sf, "source_location": f"L{line}"}

    def lk(rel, s, t):
        return {"source": s, "target": t, "relation": rel}

    nodes = [n("m", "lib.rs", 1), n("ser", "Serializer", 1),
             n("o1", "&'a mut Serializer<W, F>", 3), n("o1_s", ".serialize_str()", 4),
             n("o2", "Serializer<W, F>", 9), n("o2_new", ".new()", 10), n("o2_h", ".helper()", 11),
             n("o3", "Box<dyn Thing + 'de>", 22), n("o3_d", ".deserialize()", 23),
             n("o4", "crate::io::Reader", 26), n("o4_r", ".read()", 27),
             n("o5", "(A, B)", 30), n("o5_t", ".tuple_m()", 31),
             n("o6", "*const Node<T>", 34), n("o6_p", ".ptr_m()", 35),
             n("g", "srv.go", 1, "srv.go"), n("o7", "List[T]", 2, "srv.go"), n("o7_p", ".Push()", 3, "srv.go")]
    links = [lk("contains", "m", "ser"), lk("method", "o1", "o1_s"), lk("method", "o2", "o2_new"),
             lk("method", "o2", "o2_h"), lk("method", "o3", "o3_d"), lk("method", "o4", "o4_r"),
             lk("method", "o5", "o5_t"), lk("method", "o6", "o6_p"), lk("method", "o7", "o7_p"),
             lk("calls", "o1_s", "o2_h")]
    return {"nodes": nodes, "links": links}


def gate_rust_owner_names(home: Path) -> None:
    print("\n-- RS: generic/lifetime/sigil owners are stored clean; plausible owners untouched --")
    g = _owner_graph()
    with _env(DEEPMAP_CLEAN_OWNER_NAMES=None):
        nodes, calls, _i = GX.map_graph(g)
    by = {n["key"]: n["name"] for n in nodes}
    want = {"o1_s": "Serializer.serialize_str", "o2_new": "Serializer.new", "o2_h": "Serializer.helper",
            "o3_d": "Box.deserialize", "o4_r": "crate::io::Reader.read", "o5_t": "(A, B).tuple_m",
            "o6_p": "Node.ptr_m", "o7_p": "List.Push"}
    for k, w in want.items():
        check(f"{k}: stored as {w!r}", by.get(k) == w, by.get(k))
    check("a call into a cleaned method carries the clean callee name",
          ("o1_s", "Serializer.helper") in calls, calls)
    with _env(DEEPMAP_CLEAN_OWNER_NAMES="0"):
        raw, rcalls, _i = GX.map_graph(g)
    rby = {n["key"]: n["name"] for n in raw}
    check("DEEPMAP_CLEAN_OWNER_NAMES=0 keeps the raw owner label",
          rby.get("o1_s") == "&'a mut Serializer<W, F>.serialize_str" and rby.get("o7_p") == "List[T].Push", rby)
    print("   every captured multi-language fixture maps byte-identically with the switch on or off")
    fx = REPO / "tests" / "fixtures" / "graphify_multilang"
    for f in sorted(fx.glob("*.json")):
        gg = json.loads(f.read_text(encoding="utf-8"))
        with _env(DEEPMAP_CLEAN_OWNER_NAMES=None):
            a = GX.map_graph(gg)
        with _env(DEEPMAP_CLEAN_OWNER_NAMES="0"):
            b = GX.map_graph(gg)
        check(f"{f.name}: unchanged", a == b)
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine half skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "src/lib.rs",
               "pub struct Serializer<W, F> { w: W, f: F }\n\n"
               "impl<'a, W: Write, F: Formatter> ser::Serializer for &'a mut Serializer<W, F> {\n"
               "    fn serialize_str(self, v: &str) -> Result<(), Error> {\n        self.helper(v)\n    }\n}\n\n"
               "impl<W, F> Serializer<W, F> {\n    fn helper(&mut self, v: &str) -> Result<(), Error> { Ok(()) }\n}\n")
        res = GX.extract(root)
        names = sorted(n["name"] for n in (res[0] if res else []))
        check("REAL: Rust impl methods are named Serializer.serialize_str / Serializer.helper",
              "Serializer.serialize_str" in names and "Serializer.helper" in names, names)
        check("REAL: no stored name carries '<', '&' or a lifetime",
              not any(("<" in x or "&" in x or "'" in x) for x in names), names)


# ================================================================================================
# G1+ — gout owner marker so a purge finds every dir a project left behind
# ================================================================================================
def gate_gout_owner(home: Path) -> None:
    print("\n-- G1+: each graphify output dir records its owner; gout_dirs_for_project finds them --")
    fn = getattr(GX, "gout_dirs_for_project", None)
    check("graphify_extract.gout_dirs_for_project exists", callable(fn))
    if not callable(fn):
        return
    import port_registry
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        root.mkdir()
        if _HAS_GIT:
            _git_repo(root)
        _write(root, "a.py", "def f():\n    return 1\n")
        _write(root, "sub/b.py", "def g():\n    return 2\n")
        if _HAS_GIT:
            _git_commit_all(root)

        def fake(cmd, kw):
            gout = Path(kw["env"]["GRAPHIFY_OUT"])
            gout.mkdir(parents=True, exist_ok=True)
            (gout / "graph.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
            return _Proc(0)

        with _stub_engine(fake), contextlib.redirect_stderr(io.StringIO()):
            GX.extract_with_report(root)
            GX.extract_with_report(root / "sub")          # a subdir sync gets its own dir
        key = port_registry.project_key(root)
        marker = GX.graphify_out_dir(root) / "deepmap_owner.json"
        data = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        check("the owner marker records the sync root + the project key",
              data.get("project_key") == key and Path(data.get("root", "?")) == root.resolve(), data)
        found = sorted(str(p) for p in fn(key))
        want = sorted(str(p) for p in (GX.graphify_out_dir(root), GX.graphify_out_dir(root / "sub")))
        if _HAS_GIT:
            check("both the root and the subdir output dirs are found for the project", found == want,
                  (found, want))
        else:
            check("the root output dir is found for the project", str(GX.graphify_out_dir(root)) in found, found)
        check("another project's key finds nothing", fn("0" * 12) == [])
        check("every returned dir is under <DEEPMAP_HOME>/gout",
              all(Path(p).parent == home / "gout" for p in found), found)


# ================================================================================================
# G4+ — DEEPMAP_EXTRACTOR=graphify failure: the persisted reason names the engine's error
# ================================================================================================
def gate_graphify_mode_error(home: Path) -> None:
    print("\n-- G4+: mode=graphify with a failing engine -> FAILED reason names the engine error --")
    S = _load_sync()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.go", "package a\nfunc A() {}\n")
        S.graphify_extract = SimpleNamespace(extract_with_report=lambda r, **k: (
            None, {"cause": "graphify exited rc=1: Rebuild failed: boom-xyz", "maxpath": False}))
        with _env(DEEPMAP_EXTRACTOR="graphify"), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            ext = S._rebuild_code(root)
        info = getattr(ext, "info", {}) or {}
        check("info.engine_error carries the cause (no fallback happened)",
              "boom-xyz" in str(info.get("engine_error")) and not info.get("fallback_reason"), info)
        with _env(DEEPMAP_EXTRACTOR="graphify"):
            v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={"Go": 1}, extractor="graphify",
                              extraction=info)
        check("verdict FAILED names the engine error", v == V.FAILED and any("boom-xyz" in x for x in r), r)


# ================================================================================================
# E2E — the real sync() over the real engine (Neo4j stubbed)
# ================================================================================================
def gate_e2e_sync(home: Path) -> None:
    print("\n-- E2E: real sync() + real _rebuild_code + the installed engine (Neo4j stubbed) --")
    if not (_HAS_GIT and _HAS_ENGINE):
        print("     (needs git + graphify — skipped)")
        return
    S = _load_sync()
    pushed: dict = {}

    def _push(driver, nodes, calls, imports, *a, **k):
        pushed["nodes"] = list(nodes)
        return len(nodes), len(calls)

    def _fake_lock(root):
        (Path(root) / ".deepmap").mkdir(parents=True, exist_ok=True)
        return object()

    S._acquire_sync_lock = _fake_lock
    S._acquire_instance_lock = lambda *a, **k: object()
    S._release_sync_lock = lambda h: None
    S.push_to_neo4j = _push
    S._read_owning_root = lambda d: None
    S._graph_has_nodes = lambda d: False
    S._write_owning_root = lambda d, i: None
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 1)
    S._count_embedded_nodes = lambda *a, **k: max(1, len(pushed.get("nodes", [])))
    S._maybe_auto_tune = lambda root: None

    class _Drv:
        def verify_connectivity(self):
            return None

        def close(self):
            return None

        def session(self, *a, **k):
            # round 10 (W6, P2/P3): the pass reads the full-text index state (stale short_names, a pending
            # rebuild, rewritten texts) — this stub is an EMPTY graph, so every read answers no rows
            class _Empty:
                def __enter__(self):
                    return self

                def __exit__(self, *e):
                    return False

                def run(self, *q, **p):
                    return []
            return _Empty()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        _policy_repo(root)
        with _env(NEO4J_URI="bolt://localhost:7687", DEEPMAP_EXTRACTOR=None), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(str(root), driver=_Drv())
        files = sorted({n.get("source_file") for n in pushed.get("nodes", [])})
        mp = root / ".deepmap" / "graph_build.json"
        data = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else {}
        check("sync() succeeded", ok is True, ok)
        check("the pushed graph holds build/core.py and out/tool.py",
              "build/core.py" in files and "out/tool.py" in files, files)
        check("...and none of the generated / env / dist files",
              not any(f.startswith(("dist", "config/env", "services/payment_env", ".env", "lib64", "target"))
                      or f in ("build/app.min.js", "build/types.d.ts", "build/scratch.py", "out/bundle.js",
                               "out/compiled.js") for f in files), files)
        reasons = [str(x) for x in (data.get("reasons") or [])]
        check("the manifest verdict is DEGRADED naming only the never-rescued dirs with hand-written tracked "
              "source (round 7; was a silent READY) — never build/ or out/",
              data.get("verdict") == "DEGRADED"
              and all(any(x.startswith(f"LOW COVERAGE in {d}/") for x in reasons)
                      for d in (".env", "config/env", "dist-protected", "services/payment_env"))
              and not any(x.startswith(("LOW COVERAGE in build/", "LOW COVERAGE in out/")) for x in reasons),
              (data.get("verdict"), reasons))
        check("the manifest records the rescued dirs", data.get("graphify_rescued_dirs") == ["build", "out"],
              data.get("graphify_rescued_dirs"))


# ================================================================================================
def gate_drift() -> None:
    print("\n-- drift locks: the rescue filter mirrors sync's vendor filter --")
    S = _load_sync()
    check("graphify_extract._VENDOR_FILE_SUFFIXES == sync._VENDOR_FILE_SUFFIXES == verdict's",
          tuple(getattr(GX, "_VENDOR_FILE_SUFFIXES", ())) == tuple(S._VENDOR_FILE_SUFFIXES)
          == tuple(V._VENDOR_FILE_SUFFIXES))
    fn = getattr(GX, "_gitignore_dir_parts", None)
    check("graphify_extract._gitignore_dir_parts exists", callable(fn))
    if callable(fn):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".gitignore", "# c\nout\nlogs/\n/anchored\n!neg\n*.log\nsub/dir\nGen\n\n")
            check("...and parses a .gitignore exactly like sync._gitignore_dir_parts",
                  fn(root) == S._gitignore_dir_parts(root), (fn(root), S._gitignore_dir_parts(root)))
    never = getattr(GX, "_NEVER_RESCUE_DIRS", frozenset())
    check("never-rescue still covers every sync vendor dir", set(S._VENDOR_DIR_PARTS) <= set(never))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("EXTRACTION POLICY GATES — D5 rescue policy, G5 opt-out, clean owner names, gout owner, E2E")
    print("=" * 78)
    home = Path(tempfile.mkdtemp(prefix="dmp_home_"))
    try:
        with _env(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR=None, DEEPMAP_PRECISE_CALLS=None,
                  DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_CASE_INSENSITIVE_EXT=None,
                  DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None, DEEPMAP_INCLUDE_VENDOR=None,
                  DEEPMAP_EXCLUDE_TESTS=None):
            gate_d5_never_rescue()
            gate_d5_generated_detector()
            gate_d5_skipped_source_dirs(home)
            gate_d5_keep_rescued_unit()
            gate_d5_real_extract(home)
            gate_d5_odd_case_in_rescued_dir(home)
            gate_d5_verdict_wording(home)
            gate_d5_census(home)
            gate_d6_remedies()
            gate_g5_opt_out(home)
            gate_rust_owner_names(home)
            gate_gout_owner(home)
            gate_graphify_mode_error(home)
            gate_e2e_sync(home)
            gate_drift()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
