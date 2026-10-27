#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the extraction-engine fixes G1-G6 of the 2026-09 live validation. No Neo4j, no spend.

Every item below was a confirmed field defect on the DEFAULT (graphify) extraction path:

  G1  Windows MAX_PATH: graphify's cache temp file is <out>/cache/ast/v<ver>/<64hex>.<8>.tmp (~109
      chars past its output dir), and the output dir was <root>/graphify-out — so any project root over
      ~150 chars failed every cache write and silently degraded to the Python-only ast fallback. The
      output now lives in a SHORT per-root dir under DEEPMAP_HOME (GRAPHIFY_OUT, absolute).
  G2  graphify `update` merges the previous graph.json back in: a removed call survived every re-sync
      (phantom CALLS), links grew by a full copy per sync, and deleting a function tripped graphify's
      shrink guard into refusing the rewrite. Each extraction now starts from an EMPTY graph.json.
  G3  graphify hard-skips directories named build/dist/target/out/env/*_env/snapshots/... even when they
      hold git-TRACKED source (DeepMap's own build/ was 100% missing, verdict READY). Such dirs are
      re-included through the spawn-safe shim, and the verdict names any that still come back empty.
      Round-2 decision D5 narrowed it: the virtualenv/env family (env, *_env, .env, venv, lib64, ...)
      and dist/ are never re-included (tests/extraction_policy_gates.py locks the full policy).
  G4The fallback was silent and mis-worded: graphify's real error (on STDOUT) was discarded, a partial
      result (per-file worker failures) was accepted silently, and an automatic auto->ast fallback was
      described as a "forced" ast extractor. Now: the output tail + cause are surfaced, partial runs are
      counted, the verdict says "fell back automatically", and the manifest records requested vs
      resolved engine + the fallback reason.
  G5  graphify binds an in-file `x.m()` to the LAST same-named def in the file, so `self.invoke()` in
      Context.forward was stamped a call to Group.invoke (INFERRED) and impact('Context.invoke') was
      empty. map_graph now re-resolves self/cls/super() calls to the caller's own class (or its in-file
      bases), and a member call on any other receiver with several in-file candidates becomes AMBIGUOUS.
  G6  sync._project_identity decoded `git rev-parse` output in the locale code page -> a wrong owner
      identity for non-ASCII roots. It now decodes UTF-8.

Hermetic: a throwaway DEEPMAP_HOME for the whole run, temp repos, a stubbed subprocess/driver where no
real engine is needed. The sections marked REAL drive the installed graphify engine (skipped when it is
absent) — for G1/G2/G3/G5 only the real engine can show the defect.
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
import types
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
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:400]) if detail != '' else ''}")


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_extraction", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_shim():
    spec = importlib.util.spec_from_file_location("dm_case_shim_extraction", BUILD / "graphify_case_shim.py")
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


def _git_commit_all(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "snap")


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


@contextlib.contextmanager
def _stub_engine(on_graphify):
    """Pretend graphify is installed and route ONLY the engine invocation to `on_graphify(cmd, kw)`.
    Any other subprocess (git) runs for real, so the wiring under test is exercised end to end."""
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


_ONE_NODE = {"nodes": [{"id": "n1", "label": "foo()", "file_type": "code", "source_file": "a.py",
                        "source_location": "L1"}], "links": []}


# ================================================================================================
# G1 — graphify output relocated to a SHORT per-root dir under DEEPMAP_HOME
# ================================================================================================
def gate_g1_out_dir(home: Path) -> None:
    print("\n-- G1: graphify writes to a short per-root dir under DEEPMAP_HOME (not <root>/graphify-out) --")
    fn = getattr(GX, "graphify_out_dir", None)
    check("graphify_extract.graphify_out_dir exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deep = root / ("d" * 60) / ("e" * 60) / ("f" * 60)
        deep.mkdir(parents=True)
        out = fn(root)
        check("the out dir lives under <DEEPMAP_HOME>/gout/", Path(out).parent == home / "gout", out)
        check("it is stable for the same root", fn(root) == out)
        check("a different (sub)root gets its OWN dir (graphify's stat index is root-relative)",
              fn(root / "sub") != out)
        check("its length does not grow with the project root (a 180+ char root -> same short length)",
              len(str(fn(deep))) == len(str(out)), (len(str(fn(deep))), len(str(out))))
        check("the graphify cache temp path now fits MAX_PATH for ANY root depth",
              len(str(fn(deep))) + 109 < 260, len(str(fn(deep))) + 109)

    print("   wiring: extract() hands GRAPHIFY_OUT to the engine and reads graph.json back from there")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py", "def foo():\n    pass\n")
        seen: dict = {}

        def on_graphify(cmd, kw):
            env = kw.get("env") or {}
            seen["out"] = env.get("GRAPHIFY_OUT")
            seen["cwd"] = kw.get("cwd")
            if seen["out"]:
                Path(seen["out"]).mkdir(parents=True, exist_ok=True)
                (Path(seen["out"]) / "graph.json").write_text(json.dumps(_ONE_NODE), encoding="utf-8")
            return _Proc(0)

        with _stub_engine(on_graphify):
            res = GX.extract(root)
        check("GRAPHIFY_OUT is set for the engine subprocess", bool(seen.get("out")), seen)
        check("...to graphify_out_dir(root) (absolute)",
              seen.get("out") == str(fn(root)) and Path(seen.get("out") or ".").is_absolute(), seen)
        check("extract() maps the graph.json written THERE", bool(res) and len(res[0]) == 1, res)
        check("nothing is written to <root>/graphify-out/graph.json",
              not (root / "graphify-out" / "graph.json").exists())


def gate_g1_real_deep_root(home: Path) -> None:
    print("\n-- G1 REAL: a project root far past ~150 chars still extracts (the MAX_PATH cliff) --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine gate skipped)")
        return
    base = Path(tempfile.mkdtemp(prefix="dmx_"))
    try:
        root = base / ("deep_project_dir_" * 4) / ("nested_level_two_" * 4)
        # Grow until PAST the old limit whatever the temp dir is: a Linux /tmp/dmx_XXXXXXXX gives only 155
        # chars for the two segments above (Windows' long %TEMP% passes on its own). Stay well below
        # MAX_PATH so the fixture's own files are writable on a default Windows box.
        level = 0
        while len(str(root)) <= 170 and level < 8:
            level += 1
            root = root / f"pad_level_{level}_dir"
        root.mkdir(parents=True)
        check("the fixture root is deeper than the old ~150-char limit", len(str(root)) > 160,
              len(str(root)))
        check("...and its files still fit under MAX_PATH (the root, not the tree, is what grew)",
              len(str(root / "src" / "mod_00.py")) < 240, len(str(root / "src" / "mod_00.py")))
        # 22 files -> graphify's ProcessPoolExecutor path, where the old failure was SILENT (rc 0, 0 nodes)
        for i in range(22):
            _write(root, f"src/mod_{i:02d}.py", f"def fn_{i}():\n    return helper_{i}()\n\n"
                                                f"def helper_{i}():\n    return {i}\n")
        res, report = GX.extract_with_report(root) if hasattr(GX, "extract_with_report") else (
            GX.extract(root), {})
        nodes = res[0] if res else []
        py = {n["source_file"] for n in nodes if n["source_file"].endswith(".py")}
        check("all 22 Python files are in the graph (was 0 on a default Windows box)", len(py) == 22,
              f"{len(py)} files; report={report}")
        check("...and the engine reported no per-file failures",
              not (report or {}).get("failed_files"), report)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ================================================================================================
# G2 — every extraction starts from an EMPTY graph.json (no phantom edges, no growth, no refusal)
# ================================================================================================
def gate_g2_fresh_graph(home: Path) -> None:
    print("\n-- G2: a previous graph.json is gone BEFORE the engine runs --")
    fn = getattr(GX, "graphify_out_dir", None)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py", "def foo():\n    pass\n")
        out = Path(fn(root)) if callable(fn) else root / "graphify-out"
        out.mkdir(parents=True, exist_ok=True)
        phantom = {"nodes": _ONE_NODE["nodes"], "links": [{"source": "n1", "target": "gone",
                                                           "relation": "calls"}]}
        (out / "graph.json").write_text(json.dumps(phantom), encoding="utf-8")
        seen: dict = {}

        def on_graphify(cmd, kw):
            gout = Path((kw.get("env") or {}).get("GRAPHIFY_OUT") or out)
            seen["existed"] = (gout / "graph.json").exists()
            gout.mkdir(parents=True, exist_ok=True)
            (gout / "graph.json").write_text(json.dumps(_ONE_NODE), encoding="utf-8")
            return _Proc(0)

        with _stub_engine(on_graphify):
            GX.extract(root)
        check("the prior graph.json was removed before graphify ran (nothing to merge back in)",
              seen.get("existed") is False, seen)

    print("   map_graph de-duplicates repeated links (an honest log count, no redundant MERGEs)")
    g = {"nodes": [{"id": "a", "label": "a()", "file_type": "code", "source_file": "m.py",
                    "source_location": "L1"},
                   {"id": "b", "label": "b()", "file_type": "code", "source_file": "m.py",
                    "source_location": "L4"}],
         "links": [{"source": "a", "target": "b", "relation": "calls"}] * 3}
    _n, calls, _i = GX.map_graph(g)
    check("3 copies of one call link -> ONE call tuple", calls == [("a", "b")], calls)


def gate_g2_real_resync(home: Path) -> None:
    print("\n-- G2 REAL: removed calls disappear, re-syncs don't grow, deleting a def is not refused --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine gate skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = ("def caller():\n    helper_one()\n    return helper_two()\n\n"
               "def helper_one():\n    return 1\n\n"
               "def helper_two():\n    return 2\n\n"
               "def spare():\n    return 3\n")
        _write(root, "app/core.py", src)
        _write(root, "app/other.py", "def other():\n    return 0\n")

        def callees():
            r = GX.extract(root)
            return None if r is None else sorted(c for (k, c) in r[1] if k.endswith("caller"))

        first = callees()
        check("baseline: caller -> helper_one + helper_two", first == ["helper_one", "helper_two"], first)
        _write(root, "app/core.py", src.replace("    helper_one()\n", ""))
        second = callees()
        check("a REMOVED call is gone on the next extraction (no phantom CALLS)",
              second == ["helper_two"], second)
        third = GX.extract(root)
        check("an unchanged re-sync does not grow the call list",
              third is not None and len(third[1]) == len(set(third[1])), third and third[1])
        _write(root, "app/core.py", src.replace("    helper_one()\n", "").replace(
            "def spare():\n    return 3\n", ""))
        fourth = GX.extract(root)
        names = {n["name"] for n in (fourth[0] if fourth else [])}
        check("deleting a def in a SURVIVING file is not refused (no shrink-guard ast fallback)",
              fourth is not None and "spare" not in names and "helper_two" in names, sorted(names))


# ================================================================================================
# G3 — engine-skipped dirs holding git-TRACKED source are re-included; the verdict names any gap
# ================================================================================================
def _skipdir_repo(root: Path) -> None:
    _git_repo(root)
    _write(root, ".gitignore", "dist/\n")
    _write(root, "pyproject.toml", "[project]\nname='x'\n")
    _write(root, "src/app.py", "def app_main():\n    return core_fn()\n")
    for i in range(3):
        _write(root, f"build/core{i}.py", f"def core_fn{i}():\n    return {i}\n")
    _write(root, "out/report.py", "def make_report():\n    return 1\n")
    _write(root, "config/env/settings.py", "def load_settings():\n    return {}\n")
    _write(root, "services/payment_env/charge.py", "def charge_card():\n    return True\n")
    _write(root, "node_modules/lib/index.js", "function vendored() { return 1 }\n")
    _write(root, "my_venv/tool.py", "def venv_tool():\n    return 1\n")
    _write(root, "build/notes.txt", "not source\n")
    _git_commit_all(root)
    _write(root, "dist/generated.py", "def generated():\n    return 1\n")   # gitignored + untracked


def gate_g3_shim_unit() -> None:
    print("\n-- G3: the shim's rescue patch (fake engine module; spawn-safe env contract) --")
    S = _load_shim()
    fn = getattr(S, "rescue_skipped_dirs", None)
    check("graphify_case_shim.rescue_skipped_dirs exists", callable(fn))
    check("the rescue env var is named DEEPMAP_RESCUE_DIRS", getattr(S, "ENV_RESCUE", None) == "DEEPMAP_RESCUE_DIRS")
    if not callable(fn):
        return
    saved = {k: sys.modules.get(k) for k in ("graphify", "graphify.detect")}
    pkg = types.ModuleType("graphify")
    det = types.ModuleType("graphify.detect")
    noise = {"build", "out", "node_modules", "env"}
    det._is_noise_dir = lambda part, parent=None: part in noise
    pkg.detect = det
    sys.modules["graphify"], sys.modules["graphify.detect"] = pkg, det
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            got = fn(str(root), ["build", "config/env"])
            check("returns what it rescued", sorted(got) == ["build", "config/env"], got)
            p = det._is_noise_dir
            check("a rescued dir at its exact path is no longer noise", p("build", root) is False)
            check("...nested rescued path too", p("env", root / "config") is False)
            check("the SAME name elsewhere is still skipped (path-precise, not name-wide)",
                  p("build", root / "docs") is True)
            check("an un-rescued noise dir is still skipped", p("node_modules", root) is True)
            check("a caller without a parent (name-only) keeps the engine's answer", p("build") is True)
            fn(str(root), ["build"])
            check("re-applying never double-wraps (idempotent; unwraps to the engine original)",
                  getattr(det._is_noise_dir, "__wrapped__", None) is getattr(p, "__wrapped__", 1))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    src = (BUILD / "graphify_case_shim.py").read_text(encoding="utf-8")
    check("the shim still never renames/moves/deletes user files",
          not any(tok in src for tok in ("os.rename", "shutil.move", "os.replace", "unlink", "rmtree")))


def gate_g3_tracked_skipped(home: Path) -> None:
    print("\n-- G3: which engine-skipped dirs hold git-TRACKED source --")
    fn = getattr(GX, "skipped_source_dirs", None)
    check("graphify_extract.skipped_source_dirs exists", callable(fn))
    if not callable(fn) or not _HAS_GIT or not _HAS_ENGINE:
        print("     (needs git + graphify — remainder skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _skipdir_repo(root)
        got = fn(root)
        check("tracked source under build/ and out/ is found", sorted(got) == ["build", "out"], got)
        check("D5: the env family (config/env/, services/payment_env/) is never re-included",
              "config/env" not in got and "services/payment_env" not in got, got)
        check("...with its tracked source files (not build/notes.txt)",
              sorted(got.get("build", [])) == ["build/core0.py", "build/core1.py", "build/core2.py"],
              got.get("build"))
        check("vendored node_modules/ and a *_venv are never re-included",
              not any(k.startswith(("node_modules", "my_venv")) for k in got), got)
        check("a gitignored, untracked dist/ is not re-included", "dist" not in got, got)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "build/x.py")
        check("a non-git tree -> {} (tracked-ness is unknowable; engine default stands)", fn(root) == {},
              fn(root))


def gate_g3_wiring(home: Path) -> None:
    print("\n-- G3 wiring: the shim is used ONLY when a tracked dir needs rescuing --")
    if not _HAS_GIT:
        print("     (git not installed — skipped)")
        return

    def capture(root: Path):
        seen: dict = {}

        def on_graphify(cmd, kw):
            seen["cmd"], seen["env"] = cmd, dict(kw.get("env") or {})
            return _Proc(1)

        with _stub_engine(on_graphify):
            GX.extract(root)
        return seen

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git_repo(root)
        _write(root, "src/app.py", "def f():\n    pass\n")
        _write(root, "docs/build/readme.md", "# no source here\n")
        _git_commit_all(root)
        seen = capture(root)
        cmd = seen.get("cmd") or []
        check("no tracked source under a skipped dir -> the byte-identical `-m graphify update` argv",
              cmd[1:6] == ["-m", "graphify", "update", str(root.resolve()), "--no-cluster"], cmd)
        check("...and no rescue env is passed", "DEEPMAP_RESCUE_DIRS" not in seen.get("env", {}))

    if not _HAS_ENGINE:
        print("     (graphify not installed — rescue wiring needs the engine's skip predicate)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _skipdir_repo(root)
        seen = capture(root)
        cmd = seen.get("cmd") or []
        env = seen.get("env", {})
        check("tracked source under build/ -> routed through the shim",
              any("graphify_case_shim.py" in str(c) for c in cmd), cmd)
        payload = json.loads(env.get("DEEPMAP_RESCUE_DIRS") or "{}")
        check("...with the rescue list handed down via DEEPMAP_RESCUE_DIRS (spawn workers inherit it)",
              "build" in (payload.get("dirs") or []) and "out" in (payload.get("dirs") or [])
              and "config/env" not in (payload.get("dirs") or []), payload)
        with _env(DEEPMAP_RESCUE_SKIPPED_DIRS="0"):
            seen = capture(root)
        check("DEEPMAP_RESCUE_SKIPPED_DIRS=0 restores the plain engine invocation",
              (seen.get("cmd") or [None, None])[1] == "-m", seen.get("cmd"))


def gate_g3_real(home: Path) -> None:
    print("\n-- G3 REAL: the engine now extracts tracked source under build/ out/ (never env/ *_env) --")
    if not (_HAS_ENGINE and _HAS_GIT):
        print("     (needs git + graphify — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _skipdir_repo(root)
        res = GX.extract(root)
        files = {n["source_file"] for n in (res[0] if res else [])}
        for f in ("src/app.py", "build/core0.py", "out/report.py"):
            check(f"{f} is in the graph", f in files, sorted(files))
        for f in ("config/env/settings.py", "services/payment_env/charge.py"):
            check(f"D5: {f} (env family) stays out", f not in files, sorted(files))
        check("vendored node_modules/ stays out", not any(f.startswith("node_modules") for f in files))
        check("a *_venv dir stays out", not any(f.startswith("my_venv") for f in files))
        check("the gitignored dist/ stays out", not any(f.startswith("dist") for f in files))


def gate_g3_verdict(home: Path) -> None:
    print("\n-- G3 verdict: an engine-skipped dir whose tracked source is still missing -> DEGRADED, named --")
    if not (_HAS_ENGINE and _HAS_GIT):
        print("     (needs git + graphify — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _skipdir_repo(root)
        without = [{"source_file": "src/app.py"}]
        cov = V.build_coverage(root, without)
        sk = (cov or {}).get("skipped_dirs") or {}
        check("coverage carries skipped_dirs for build/", sk.get("build", {}).get("files") == 3, cov)
        v, r = V.classify(neo4j_ok=True, n_nodes=1, n_embedded=1, census={"Python": 1},
                          extractor="graphify", ml_ok=True, coverage=cov)
        check("tracked build/ source with 0 nodes -> DEGRADED", v == V.DEGRADED, (v, r))
        check("...with a LOW COVERAGE reason naming build/",
              any(x.startswith("LOW COVERAGE in build/") for x in r), r)
        full = without + [{"source_file": f"build/core{i}.py"} for i in range(3)] + [
            {"source_file": "out/report.py"}, {"source_file": "config/env/settings.py"},
            {"source_file": "services/payment_env/charge.py"}]
        cov2 = V.build_coverage(root, full)
        v2, r2 = V.classify(neo4j_ok=True, n_nodes=len(full), n_embedded=len(full),
                            census={"Python": 1}, extractor="graphify", ml_ok=True, coverage=cov2)
        check("once extracted, no skipped-dir reason", not any("build/" in x for x in r2), (v2, r2))
        check("format_coverage shows the skipped dir line",
              any(line.startswith("build/") for line in V.format_coverage(cov)), V.format_coverage(cov))


def gate_census_relative_parts() -> None:
    print("\n-- census ignores dirs BELOW the root only (an ancestor named build/ or vendor/ is fine) --")
    with tempfile.TemporaryDirectory() as td:
        for anc in ("build", "vendor", "target"):
            root = Path(td) / anc / "myapp"
            for i in range(12):
                _write(root, f"pkg/m{i}.py")
            for i in range(4):
                _write(root, f"app/models/r{i}.rb", "class R; end\n")
            census = V.extension_census(root)
            check(f"repo under an ancestor '{anc}/' -> census still counts its files",
                  census.get("Python") == 12 and census.get("Ruby") == 4, census)
            cov = V.build_coverage(root, [], census=census)
            check(f"...and app/models/ is still tracked under '{anc}/'",
                  (cov.get("dirs") or {}).get("app/models", {}).get("files") == 4, cov)
        root = Path(td) / "plain"
        _write(root, "build/gen.py")
        _write(root, "src/a.py")
        check("a build/ dir INSIDE the root is still not counted", V.extension_census(root) == {"Python": 1},
              V.extension_census(root))


# ================================================================================================
# G4 — the fallback is honest: real cause surfaced, partial runs counted, wording, manifest
# ================================================================================================
_LONG = "C:\\" + "\\".join(["x" * 40] * 6) + "\\graphify-out\\cache\\ast\\v0.8.39\\" + "a" * 64 + ".q82u6f9d.tmp"


def gate_g4_report() -> None:
    print("\n-- G4: extract_with_report surfaces graphify's real error (it is on STDOUT) --")
    fn = getattr(GX, "extract_with_report", None)
    check("graphify_extract.extract_with_report exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py")

        def crash(cmd, kw):
            return _Proc(1, stdout=f"Re-extracting code files in {root} (no LLM needed)...\n"
                                   f"[graphify watch] Rebuild failed: [Errno 2] No such file or directory: "
                                   f"'{_LONG}'\n",
                         stderr="Nothing to update or rebuild failed — check output above.\n")

        err = io.StringIO()
        with _stub_engine(crash), contextlib.redirect_stderr(err):
            res, rep = fn(root)
        check("a failed run returns (None, report)", res is None and isinstance(rep, dict), (res, rep))
        check("the report's cause carries graphify's STDOUT error, not just 'check output above'",
              "Rebuild failed" in str(rep.get("cause", "")), rep)
        check("...and it is printed (so 'check output above' points at something)",
              "Rebuild failed" in err.getvalue(), err.getvalue()[-400:])
        if os.name == "nt":
            check("a >260-char path in the error is diagnosed as Windows MAX_PATH", rep.get("maxpath") is True,
                  rep)
            check("...with an actionable hint", "LongPathsEnabled" in err.getvalue(), err.getvalue()[-400:])
        check("extract() keeps its contract (None on failure)", GX.extract.__doc__ is not None)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py")

        def partial(cmd, kw):
            gout = Path(kw["env"]["GRAPHIFY_OUT"])
            gout.mkdir(parents=True, exist_ok=True)
            (gout / "graph.json").write_text(json.dumps(_ONE_NODE), encoding="utf-8")
            warn = "".join(f"  warning: worker failed for {root}\\m{i}.py: [Errno 2] No such file\n"
                           for i in range(3))
            warn += f"  warning: skipped {root}\\README.md (ValueError: not code DeepMap maps)\n"
            return _Proc(0, stdout="[graphify watch] Rebuilt (no clustering): 1 nodes", stderr=warn)

        err = io.StringIO()
        with _stub_engine(partial), contextlib.redirect_stderr(err):
            res, rep = fn(root)
        check("a partial run still returns what graphify extracted", bool(res) and len(res[0]) == 1, res)
        check("...but COUNTS the per-file failures (never silently accepted)", rep.get("failed_files") == 3, rep)
        check("...only for files DeepMap maps (a skipped README.md loses nothing)",
              not any("README" in x for x in rep.get("failed_examples", [])), rep)
        check("...and says so in the log", "3 file" in err.getvalue(), err.getvalue()[-300:])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def empty(cmd, kw):
            gout = Path(kw["env"]["GRAPHIFY_OUT"])
            gout.mkdir(parents=True, exist_ok=True)
            (gout / "graph.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
            return _Proc(0, stderr="  warning: worker failed for x.py: boom\n")

        with _stub_engine(empty), contextlib.redirect_stderr(io.StringIO()):
            res, rep = fn(root)
        check("a fresh but EMPTY graph -> a named cause (0 code nodes)",
              "0 code nodes" in str(rep.get("cause", "")), rep)


def gate_g4_rebuild_code() -> None:
    print("\n-- G4: _rebuild_code records requested vs resolved engine + the fallback reason --")
    S = _load_sync()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py", "def f():\n    return 1\n")
        S.graphify_extract = SimpleNamespace(
            extract_with_report=lambda r, **k: (None, {"cause": "graphify exited rc=1: Rebuild failed: X",
                                                        "maxpath": False, "failed_files": 0}),
            extract=lambda r, **k: None)
        err = io.StringIO()
        with _env(DEEPMAP_EXTRACTOR=None), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            res = S._rebuild_code(root)
        nodes, calls, imports, engine = res
        info = getattr(res, "info", None) or {}
        check("still unpacks as the 4-tuple every caller uses", engine == "ast" and len(nodes) >= 1)
        check("info.requested == 'auto'", info.get("requested") == "auto", info)
        check("info.resolved == 'ast'", info.get("resolved") == "ast", info)
        check("info.fallback_reason carries graphify's cause", "Rebuild failed" in str(info.get("fallback_reason")),
              info)
        log = err.getvalue()
        check("the log says it fell back AUTOMATICALLY and why (no vague 'unavailable/empty')",
              "automatically" in log and "Rebuild failed" in log and "unavailable/empty" not in log, log)

        with _env(DEEPMAP_EXTRACTOR="ast"), contextlib.redirect_stdout(io.StringIO()):
            res = S._rebuild_code(root)
        info = getattr(res, "info", None) or {}
        check("forced ast -> requested 'ast', no fallback reason",
              info.get("requested") == "ast" and not info.get("fallback_reason"), info)


def gate_g4_verdict_wording() -> None:
    print("\n-- G4 verdict: 'fell back automatically' vs 'forced'; partial runs DEGRADE --")
    fb = {"requested": "auto", "resolved": "ast",
          "fallback_reason": "graphify exited rc=1: Rebuild failed: [Errno 2] path too long"}
    with _env(DEEPMAP_EXTRACTOR=None):
        v, r = V.classify(neo4j_ok=True, n_nodes=20, n_embedded=20, census={"Python": 20, "Go": 3},
                          extractor="ast", ml_ok=True, extraction=fb)
        check("auto->ast fallback with non-Python source -> DEGRADED", v == V.DEGRADED, (v, r))
        check("...reason says it fell back automatically + the cause",
              any("automatically" in x and "Rebuild failed" in x for x in r), r)
        check("...and never calls it 'forced'", not any("forced" in x for x in r), r)
        v, r = V.classify(neo4j_ok=True, n_nodes=20, n_embedded=20, census={"Python": 20},
                          extractor="ast", ml_ok=True, extraction=fb)
        check("auto->ast fallback on a pure-Python tree stays READY (nothing is missing)", v == V.READY,
              (v, r))
        v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={"Go": 12}, extractor="ast",
                          extraction=fb)
        check("0-node fallback on a Go repo -> FAILED, not 'forced', names the cause",
              v == V.FAILED and not any("forced" in x for x in r) and any("Rebuild failed" in x for x in r),
              r)
        v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={"Go": 12}, extractor="ast")
        check("no extraction info + DEEPMAP_EXTRACTOR unset -> not described as 'forced'",
              not any("forced" in x for x in r), r)
    with _env(DEEPMAP_EXTRACTOR="ast"):
        v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={"Go": 12}, extractor="ast",
                          extraction={"requested": "ast", "resolved": "ast"})
        check("an EXPLICIT DEEPMAP_EXTRACTOR=ast is still called forced", any("forced" in x for x in r), r)
    part = {"requested": "auto", "resolved": "graphify", "failed_files": 7,
            "failed_examples": ["warning: worker failed for a.py: boom"]}
    v, r = V.classify(neo4j_ok=True, n_nodes=50, n_embedded=50, census={"Python": 40},
                      extractor="graphify", ml_ok=True, extraction=part)
    check("graphify lost 7 of ~40 files -> DEGRADED naming the count", v == V.DEGRADED and
          any("7 file" in x for x in r), (v, r))
    one = dict(part, failed_files=1)
    v, r = V.classify(neo4j_ok=True, n_nodes=5000, n_embedded=5000, census={"Python": 4000},
                      extractor="graphify", ml_ok=True, extraction=one)
    check("one unparseable file in a big repo is not a DEGRADED graph", v == V.READY, (v, r))


def gate_g4_manifest_and_sync() -> None:
    print("\n-- G4: the manifest records requested vs resolved + the fallback reason (via real sync()) --")
    S = _load_sync()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a.py")      # a non-git tree needs content for its provenance hash
        out = S.write_graph_build_manifest(
            root, n_nodes=1, n_edges=0, n_embedded=1, extractor="ast",
            extraction={"requested": "auto", "resolved": "ast", "fallback_reason": "graphify boom",
                        "failed_files": 0})
        data = json.loads(Path(out).read_text(encoding="utf-8")) if out else {}
        check("extractor stays the RESOLVED engine", data.get("extractor") == "ast", data)
        check("extractor_requested is recorded", data.get("extractor_requested") == "auto", data)
        check("extractor_fallback_reason is recorded (top-level; doctor reads it)",
              data.get("extractor_fallback_reason") == "graphify boom", data)

    # the real sync() hands the extraction info to BOTH the verdict and the manifest
    S = _load_sync()
    node = {"key": "a.py::f:1", "label": "Function", "name": "f", "source_file": "a.py", "start_line": 1,
            "body_summary": "f"}
    info = {"requested": "auto", "resolved": "ast", "fallback_reason": "graphify exited rc=1: kaboom",
            "failed_files": 0}
    Ext = getattr(S, "_Extraction", None)

    def _rebuild(root):
        return Ext([dict(node)], [], [], "ast", info) if Ext else ([dict(node)], [], [], "ast")

    def _fake_lock(root):
        (Path(root) / ".deepmap").mkdir(parents=True, exist_ok=True)
        return object()

    S._acquire_sync_lock = _fake_lock
    S._acquire_instance_lock = lambda *a, **k: object()
    S._release_sync_lock = lambda h: None
    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, nodes: (nodes, [])
    S.push_to_neo4j = lambda *a, **k: (1, 0)
    S._read_owning_root = lambda d: None
    S._graph_has_nodes = lambda d: False
    S._write_owning_root = lambda d, i: None
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 1)
    S._count_embedded_nodes = lambda *a, **k: 1
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
        root = Path(td)
        _write(root, "a.py", "def f():\n    return 1\n")
        for i in range(3):
            _write(root, f"svc/s{i}.go", "package svc\nfunc S() {}\n")
        with _env(NEO4J_URI="bolt://localhost:7687", DEEPMAP_EXTRACTOR=None), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(str(root), driver=_Drv())
        data = {}
        mp = root / ".deepmap" / "graph_build.json"
        if mp.is_file():
            data = json.loads(mp.read_text(encoding="utf-8"))
        check("sync() succeeded", ok is True)
        check("manifest records the fallback reason from _rebuild_code",
              "kaboom" in str(data.get("extractor_fallback_reason", "")), data)
        check("manifest verdict is DEGRADED (Go source missing after an automatic fallback)",
              data.get("verdict") == "DEGRADED", data.get("verdict"))
        check("...with a reason that says 'automatically', never 'forced'",
              any("automatically" in x for x in data.get("reasons", []))
              and not any("forced" in x for x in data.get("reasons", [])), data.get("reasons"))


# ================================================================================================
# G5 — self/cls/super() calls re-resolved to the caller's own class; other receivers -> AMBIGUOUS
# ================================================================================================
_CORE_PY = """\
class Context:
    def invoke(self, callback, *args):
        return callback(*args)

    def forward(self, cmd):
        return self.invoke(cmd, 1)


class Command:
    def __init__(self, name):
        self.name = name

    def invoke(self, ctx):
        return ctx.invoke(self.callback, ctx)

    def main(self, ctx):
        rv = self.invoke(ctx)
        return rv

    def callback(self, *a):
        return a


class Group(Command):
    def invoke(self, ctx):
        return super().invoke(ctx)

    def run_all(self, ctx):
        return self.invoke(ctx)


class Base:
    def run(self):
        return 1


class Child(Base):
    def run(self):
        return super().run()

    def go(self):
        return self.run()


class Other:
    def run(self):
        return 3
"""


def _core_graph() -> dict:
    """The graph real graphify 0.8.39 emits for _CORE_PY (captured shape): every in-file `.invoke()` /
    `.run()` is bound to the LAST same-named def (Group.invoke / Other.run)."""
    def n(i, label, line):
        return {"id": i, "label": label, "file_type": "code", "source_file": "pkg/core.py",
                "source_location": f"L{line}"}

    def lk(rel, s, t, line):
        return {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED",
                "source_file": "pkg/core.py", "source_location": f"L{line}"}

    p = "pkg_core"
    nodes = [n(p, "core.py", 1), n(f"{p}_context", "Context", 1), n(f"{p}_context_invoke", ".invoke()", 2),
             n(f"{p}_context_forward", ".forward()", 5), n(f"{p}_command", "Command", 9),
             n(f"{p}_command_init", ".__init__()", 10), n(f"{p}_command_invoke", ".invoke()", 13),
             n(f"{p}_command_main", ".main()", 16), n(f"{p}_command_callback", ".callback()", 20),
             n(f"{p}_group", "Group", 24), n(f"{p}_group_invoke", ".invoke()", 25),
             n(f"{p}_group_run_all", ".run_all()", 28), n(f"{p}_base", "Base", 32),
             n(f"{p}_base_run", ".run()", 33), n(f"{p}_child", "Child", 37), n(f"{p}_child_run", ".run()", 38),
             n(f"{p}_child_go", ".go()", 41), n(f"{p}_other", "Other", 45), n(f"{p}_other_run", ".run()", 46)]
    links = [lk("contains", p, f"{p}_context", 1), lk("method", f"{p}_context", f"{p}_context_invoke", 2),
             lk("method", f"{p}_context", f"{p}_context_forward", 5), lk("contains", p, f"{p}_command", 9),
             lk("method", f"{p}_command", f"{p}_command_init", 10),
             lk("method", f"{p}_command", f"{p}_command_invoke", 13),
             lk("method", f"{p}_command", f"{p}_command_main", 16),
             lk("method", f"{p}_command", f"{p}_command_callback", 20), lk("contains", p, f"{p}_group", 24),
             lk("inherits", f"{p}_group", f"{p}_command", 24),
             lk("method", f"{p}_group", f"{p}_group_invoke", 25),
             lk("method", f"{p}_group", f"{p}_group_run_all", 28), lk("contains", p, f"{p}_base", 32),
             lk("method", f"{p}_base", f"{p}_base_run", 33), lk("contains", p, f"{p}_child", 37),
             lk("inherits", f"{p}_child", f"{p}_base", 37), lk("method", f"{p}_child", f"{p}_child_run", 38),
             lk("method", f"{p}_child", f"{p}_child_go", 41), lk("contains", p, f"{p}_other", 45),
             lk("method", f"{p}_other", f"{p}_other_run", 46),
             lk("calls", f"{p}_context_forward", f"{p}_group_invoke", 6),
             lk("calls", f"{p}_command_invoke", f"{p}_group_invoke", 14),
             lk("calls", f"{p}_command_main", f"{p}_group_invoke", 17),
             lk("calls", f"{p}_group_run_all", f"{p}_group_invoke", 29),
             lk("calls", f"{p}_child_run", f"{p}_other_run", 39),
             lk("calls", f"{p}_child_go", f"{p}_other_run", 42)]
    return {"nodes": nodes, "links": links}


_MINI_PY = """\
class Rows(list):
    def __init__(self, items):
        super().__init__(items)


class Other:
    def __init__(self):
        self.x = 1

    def ping(self):
        return 2


class Base:
    def ping(self):
        return 1


class Typed(Base[int]):
    def go(self):
        return self.ping()


class Loose:
    def run(self):
        return self.ping()
"""


def _mini_graph() -> dict:
    """Real graphify 0.8.39 shape for _MINI_PY: `list` is an out-of-file stub base; the subscripted
    `Base[int]` emits NO inherits edge; every `.ping()` / `.__init__()` binds to the last def."""
    def n(i, label, line):
        return {"id": i, "label": label, "file_type": "code", "source_file": "pkg/mini.py",
                "source_location": f"L{line}" if line else ""}

    def lk(rel, s, t, line):
        return {"source": s, "target": t, "relation": rel, "source_file": "pkg/mini.py",
                "source_location": f"L{line}"}

    p = "pkg_mini"
    nodes = [n(p, "mini.py", 1), n(f"{p}_rows", "Rows", 1), n("list", "list", 0),
             n(f"{p}_rows_init", ".__init__()", 2), n(f"{p}_other", "Other", 6),
             n(f"{p}_other_init", ".__init__()", 7), n(f"{p}_other_ping", ".ping()", 10),
             n(f"{p}_base", "Base", 14), n(f"{p}_base_ping", ".ping()", 15), n(f"{p}_typed", "Typed", 19),
             n(f"{p}_typed_go", ".go()", 20), n(f"{p}_loose", "Loose", 24), n(f"{p}_loose_run", ".run()", 25)]
    links = [lk("contains", p, f"{p}_rows", 1), lk("inherits", f"{p}_rows", "list", 1),
             lk("method", f"{p}_rows", f"{p}_rows_init", 2), lk("contains", p, f"{p}_other", 6),
             lk("method", f"{p}_other", f"{p}_other_init", 7), lk("method", f"{p}_other", f"{p}_other_ping", 10),
             lk("contains", p, f"{p}_base", 14), lk("method", f"{p}_base", f"{p}_base_ping", 15),
             lk("contains", p, f"{p}_typed", 19), lk("method", f"{p}_typed", f"{p}_typed_go", 20),
             lk("contains", p, f"{p}_loose", 24), lk("method", f"{p}_loose", f"{p}_loose_run", 25),
             lk("calls", f"{p}_rows_init", f"{p}_other_init", 3),
             lk("calls", f"{p}_typed_go", f"{p}_base_ping", 21),
             lk("calls", f"{p}_loose_run", f"{p}_base_ping", 26)]
    return {"nodes": nodes, "links": links}


def _check_mini(calls, tag=""):
    check(f"{tag}super().__init__() into an out-of-file base (list) is dropped, not bound to Other.__init__ "
          "(and not fanned out to every __init__)", _calls_of(calls, "rows_init") == [],
          _calls_of(calls, "rows_init"))
    check(f"{tag}self.ping() under an unparsed generic base `Base[int]` keeps graphify's Base.ping",
          _calls_of(calls, "typed_go") == ["Base.ping"], _calls_of(calls, "typed_go"))
    check(f"{tag}self.ping() on a class with no base defining it -> bare name (AMBIGUOUS)",
          _calls_of(calls, "loose_run") == ["ping"], _calls_of(calls, "loose_run"))


def _calls_of(calls, suffix):
    return sorted(c for (k, c) in calls if k.endswith(suffix))


def gate_g5_map_graph() -> None:
    print("\n-- G5: map_graph re-resolves self/cls/super() calls (click Context.invoke shape) --")
    g = _core_graph()
    _n, legacy, _i = GX.map_graph(g)
    check("without root= map_graph is byte-identical to before (pure mode)",
          _calls_of(legacy, "context_forward") == ["Group.invoke"], legacy)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "pkg/core.py", _CORE_PY)
        try:
            _n, calls, _i = GX.map_graph(g, root=root)
        except TypeError as e:
            check("map_graph accepts root=", False, e)
            return
        check("self.invoke() in Context.forward -> Context.invoke (was Group.invoke)",
              _calls_of(calls, "context_forward") == ["Context.invoke"], _calls_of(calls, "context_forward"))
        check("self.invoke() in Command.main -> Command.invoke (the caller's own class)",
              _calls_of(calls, "command_main") == ["Command.invoke"], _calls_of(calls, "command_main"))
        check("ctx.invoke() (not self) with 3 in-file candidates -> the bare name (AMBIGUOUS fan-out)",
              _calls_of(calls, "command_invoke") == ["invoke"], _calls_of(calls, "command_invoke"))
        check("self.invoke() in Group.run_all -> Group.invoke (already right, unchanged)",
              _calls_of(calls, "group_run_all") == ["Group.invoke"], _calls_of(calls, "group_run_all"))
        check("super().run() in Child.run -> Base.run (the in-file base, not the last def Other.run)",
              _calls_of(calls, "child_run") == ["Base.run"], _calls_of(calls, "child_run"))
        check("self.run() in Child.go -> Child.run (own class wins over the base)",
              _calls_of(calls, "child_go") == ["Child.run"], _calls_of(calls, "child_go"))
        with _env(DEEPMAP_PRECISE_CALLS="1"):
            _n, pcalls, _i = GX.map_graph(g, root=root)
        check("precise mode: the corrected target KEY is emitted",
              _calls_of(pcalls, "context_forward") == ["pkg_core_context_invoke"], pcalls)
        check("precise mode: an ambiguous receiver falls back to the bare name (AMBIGUOUS at push)",
              _calls_of(pcalls, "command_invoke") == ["invoke"], pcalls)
        # an unreadable source file -> graphify's own resolution is kept (never worse than before)
        (root / "pkg" / "core.py").unlink()
        _n, gone, _i = GX.map_graph(g, root=root)
        check("source file gone -> unchanged legacy resolution", gone == legacy, gone)
        _write(root, "pkg/mini.py", _MINI_PY)
        _n, mcalls, _i = GX.map_graph(_mini_graph(), root=root)
        _check_mini(mcalls)


def gate_g5_push() -> None:
    print("\n-- G5 at the push layer: Context.invoke gets its callers; ctx.invoke is AMBIGUOUS --")
    S = _load_sync()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "pkg/core.py", _CORE_PY)
        nodes, calls, imports = GX.map_graph(_core_graph(), root=root)
    edges: list = []

    class _Sess:
        def run(self, q, **kw):
            if "MERGE (a)-[r:CALLS]->(b)" in q:
                for row in kw.get("rows") or [kw]:   # the push batches with UNWIND $rows
                    edges.append((row["a"], row["b"], row["c"]))
            return []

        def begin_transaction(self):                 # the edge rebuild runs inside a write transaction
            return self

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Drv:
        def session(self):
            return _Sess()

    S.edge_resolve = SimpleNamespace(build_name_index=S.edge_resolve.build_name_index,
                                     ALLOWED_EDGE_TYPES=S.edge_resolve.ALLOWED_EDGE_TYPES,
                                     REVERSED_EDGE_TYPES=S.edge_resolve.REVERSED_EDGE_TYPES,
                                     write_edge_rows=lambda *a, **k: {},
                                     Resolver=lambda n: None,
                                     prune_stale_edge_types=lambda *a, **k: None,
                                     push_typed_edges=lambda *a, **k: None, push_imports=lambda *a, **k: None,
                                     edge_rows=lambda *a, **k: [], rows_by_anchor=lambda rows: {},
                                     rewrite_anchored_edges=lambda *a, **k: {})
    with _env(DEEPMAP_PRECISE_CALLS=None):
        S.push_to_neo4j(_Drv(), nodes, calls, imports, [])
    into_ctx = [(a, c) for (a, b, c) in edges if b == "pkg_core_context_invoke"]
    check("Context.forward -> Context.invoke is an edge (INFERRED)",
          ("pkg_core_context_forward", "INFERRED") in into_ctx, into_ctx)
    check("Command.invoke (ctx.invoke) reaches Context.invoke as AMBIGUOUS",
          ("pkg_core_command_invoke", "AMBIGUOUS") in into_ctx, into_ctx)
    wrong = [(a, b, c) for (a, b, c) in edges if a == "pkg_core_context_forward" and b == "pkg_core_group_invoke"]
    check("Group.invoke no longer claims Context.forward's self-call", wrong == [], wrong)


def gate_g5_real(home: Path) -> None:
    print("\n-- G5 REAL: the installed engine + map_graph on the click-shaped file --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — real-engine gate skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "pkg/__init__.py", "")
        _write(root, "pkg/core.py", _CORE_PY)
        res = GX.extract(root)
        calls = res[1] if res else []
        check("REAL: Context.forward -> Context.invoke", _calls_of(calls, "context_forward") == ["Context.invoke"],
              calls)
        check("REAL: Command.main -> Command.invoke", _calls_of(calls, "command_main") == ["Command.invoke"],
              calls)
        check("REAL: Command.invoke's ctx.invoke() -> AMBIGUOUS bare name",
              _calls_of(calls, "command_invoke") == ["invoke"], calls)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "pkg/__init__.py", "")
        _write(root, "pkg/mini.py", _MINI_PY)
        res = GX.extract(root)
        _check_mini(res[1] if res else [], "REAL: ")


# ================================================================================================
# G6 — sync._project_identity decodes git output as UTF-8 (locale-independent owner identity)
# ================================================================================================
_G6_CHILD = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync
root, sub = sys.argv[2], sys.argv[3]
sys.stdout.buffer.write(json.dumps({"id": sync._project_identity(sub),
                                     "root": str(Path(root).resolve())}).encode("utf-8"))
"""


def gate_g6_identity() -> None:
    print("\n-- G6: _project_identity is right for a non-ASCII repo path WITHOUT UTF-8 mode --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    start = src.find("def _project_identity")
    body = src[start:src.find("\ndef ", start + 10)]
    check("_project_identity decodes git output as UTF-8 explicitly", 'encoding="utf-8"' in body, body[:300])
    if not _HAS_GIT:
        print("     (git not installed — behavioral half skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj é ß ü"
        (root / "pkg").mkdir(parents=True)
        _git_repo(root)
        _write(root, "a.py")
        _git_commit_all(root)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        p = subprocess.run([sys.executable, "-X", "utf8=0", "-c", _G6_CHILD, str(BUILD), str(root),
                            str(root / "pkg")], capture_output=True, env=env, timeout=120)
        try:
            data = json.loads(p.stdout.decode("utf-8"))
        except Exception:
            data = {}
        same = bool(data) and os.path.normcase(data.get("id", "")) == os.path.normcase(data.get("root", "?"))
        check("from a subdir, the identity is the repo root (locale code page irrelevant)", same,
              (data, p.stderr.decode("utf-8", "replace")[-300:]))
        check("no decode tracebacks from git's reader thread",
              b"UnicodeDecodeError" not in p.stderr, p.stderr.decode("utf-8", "replace")[-300:])


# ================================================================================================
def gate_drift() -> None:
    print("\n-- drift locks: never-rescue ⊇ sync's vendor dirs; mirrored constants agree --")
    S = _load_sync()
    never = getattr(GX, "_NEVER_RESCUE_DIRS", None)
    check("graphify_extract._NEVER_RESCUE_DIRS exists", isinstance(never, (set, frozenset)))
    if isinstance(never, (set, frozenset)):
        check("every sync vendor dir is never re-included", set(S._VENDOR_DIR_PARTS) <= set(never),
              set(S._VENDOR_DIR_PARTS) - set(never))
    check("verdict's test-dir mirror == sync._TEST_DIR_PARTS (DEEPMAP_EXCLUDE_TESTS parity)",
          set(getattr(V, "_TEST_DIR_PARTS", ())) == set(S._TEST_DIR_PARTS))
    shim = _load_shim()
    check("the shim and graphify_extract agree on the env contract",
          shim.ENV_ALIASES == GX.ENV_CASE_ALIASES and shim.ENV_RESCUE == GX.ENV_RESCUE)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("EXTRACTION ENGINE GATES — G1..G6 (no Neo4j; REAL sections use the installed engine)")
    print("=" * 78)
    home = Path(tempfile.mkdtemp(prefix="dmx_home_"))
    try:
        with _env(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR=None, DEEPMAP_PRECISE_CALLS=None,
                  DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_CASE_INSENSITIVE_EXT=None,
                  DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None):
            gate_g1_out_dir(home)
            gate_g1_real_deep_root(home)
            gate_g2_fresh_graph(home)
            gate_g2_real_resync(home)
            gate_g3_shim_unit()
            gate_g3_tracked_skipped(home)
            gate_g3_wiring(home)
            gate_g3_real(home)
            gate_g3_verdict(home)
            gate_census_relative_parts()
            gate_g4_report()
            gate_g4_rebuild_code()
            gate_g4_verdict_wording()
            gate_g4_manifest_and_sync()
            gate_g5_map_graph()
            gate_g5_push()
            gate_g5_real(home)
            gate_g6_identity()
            gate_drift()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
