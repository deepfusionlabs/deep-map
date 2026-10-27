#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 W5 gates: coverage honesty the setup verdict was still blind to. No Neo4j, no network, no spend;
temp trees only (git is used for the tracked-file cases and skipped when absent).

  A  T8d-B1   coverage counts ENGINE nodes only: recognizer :Class shells (a C# file the engine never
              parsed still gets one node per class from the C#/Java/Go/Ruby recognizers) no longer make a
              language look extracted; and a census language with ANY files but 0 engine nodes is flagged
              (the old rule waited for 10 files). ERB — a recognizer-only language by design — still counts.
  B  T1#0/T2#0/T3#0  git-tracked, hand-written source in a directory the engine always skips and DeepMap
              never re-includes (the env family: env/, .env/, venv/, *_env/, *_venv/; and dist/) is named
              DEGRADED instead of silently missing — unless it is a real virtualenv or generated output.
  C  T3#0     the same for a NON-git tree, where build/ out/ target/ snapshots/ ... are skipped by the engine
              and cannot be re-included (tracked-ness is unknowable): named DEGRADED unless the files are
              generated or byte-identical copies of source elsewhere in the tree (a setup.py build/lib).
  D  T10#14/T10d-8  (Windows) source files past the 260-character MAX_PATH limit are counted and named
              DEGRADED with the LongPathsEnabled remedy (os.walk skipped them silently -> READY).
  E  nothing changes for a healthy tree (READY stays READY).
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import verdict as V  # noqa: E402

_PASS = _FAIL = 0
_GIT = shutil.which("git")
_TMP = Path(tempfile.mkdtemp(prefix="dm_r7v_"))
_ENV_KEYS = ("DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_EXTRACTOR", "DEEPMAP_RESCUE_SKIPPED_DIRS", "DEEPMAP_RECOGNIZERS")


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


def tree(name: str, files: dict, *, git: bool = False) -> Path:
    root = _TMP / name
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    if git and _GIT:
        run = lambda *a: subprocess.run([_GIT, "-C", str(root), *a], capture_output=True, timeout=60)  # noqa: E731
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "t")
        run("config", "core.autocrlf", "false")
        run("add", "-A", "-f")
        run("commit", "-q", "-m", "fixture")
    return root


def engine_node(sf: str, name: str, line: int = 1, label: str = "Function", key: str | None = None) -> dict:
    """A node as the graphify engine maps it (an engine id key, never `<source_file>::...`)."""
    return {"key": key or (sf.replace("/", "_").replace(".", "_") + "_" + name.lower()), "label": label,
            "name": name, "source_file": sf, "start_line": line, "documented": False,
            "body_summary": f"{name}: {label.lower()}"}


def recognizer_node(sf: str, name: str, line: int = 1, label: str = "Class") -> dict:
    """A supplemental recognizer node (csharp/java/go/ruby_rails _edges: key `<rel>::<Name>:<line>`)."""
    return {"key": f"{sf}::{name}:{line}", "label": label, "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": f"{name}: class"}


def verdict_of(root, nodes, *, extractor="graphify", extraction=None, engine_keys=None):
    census = V.extension_census(root)
    cov = V.build_coverage(root, nodes, census=census, engine_keys=engine_keys)
    v, rs = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                       extractor=extractor, ml_ok=True, coverage=cov,
                       extraction=extraction or {"requested": "auto" if extractor == "graphify" else "ast"})
    return v, rs, cov


def has_reason(reasons, *needles) -> bool:
    return any(all(n in r for n in needles) for r in reasons)


# ================================================================================================
def gate_a_engine_nodes_only() -> None:
    print("\n-- A (T8d-B1): coverage counts ENGINE nodes; any-file languages with 0 engine nodes are flagged --")
    py = {f"src/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(20)}
    cs12 = {f"Models/C{i}.cs": f"namespace M {{ public class C{i} {{ public void Save() {{}} }} }}\n" for i in range(12)}
    root = tree("a_shells", {**py, **cs12})
    eng = [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)]
    nodes = eng + [recognizer_node(f"Models/C{i}.cs", f"C{i}") for i in range(12)]
    ekeys = {n["key"] for n in eng}
    v, rs, cov = verdict_of(root, nodes, engine_keys=ekeys)
    cs = (cov.get("languages") or {}).get("C#", {})
    check("12 C# files covered only by recognizer shells: C# engine nodes == 0", cs.get("nodes") == 0, cs)
    check("...the recognizer-only nodes are reported beside them", cs.get("recognizer_nodes") == 12, cs)
    check("...and the verdict is DEGRADED naming C# (was READY)", v == V.DEGRADED and has_reason(rs, "C#"), (v, rs))
    lines = V.format_coverage(cov)
    check("format_coverage says the C# nodes are recognizer-only",
          any(l.startswith("C#:") and "recognizer" in l for l in lines), lines)
    # the production path: sync._rebuild_code registers the engine's nodes for the root (one-shot)
    V.note_engine_nodes(root, eng)
    cov_r = V.build_coverage(root, nodes)
    check("note_engine_nodes(root, engine nodes) -> the next build_coverage counts engine nodes only",
          (cov_r["languages"].get("C#") or {}).get("nodes") == 0, cov_r["languages"].get("C#"))
    cov_n = V.build_coverage(root, nodes)
    check("...the registration is consumed once; with no engine information every node counts (as before)",
          (cov_n["languages"].get("C#") or {}).get("nodes") == 12, cov_n["languages"].get("C#"))
    tagged = eng + [dict(recognizer_node(f"Models/C{i}.cs", f"C{i}"), origin="recognizer") for i in range(12)]
    cov_t = V.build_coverage(root, tagged)
    check("a node tagged origin='recognizer' is never engine output",
          (cov_t["languages"].get("C#") or {}).get("nodes") == 0, cov_t["languages"].get("C#"))

    cs5 = {f"Svc/S{i}.cs": f"class S{i} {{ }}\n" for i in range(5)}
    root = tree("a_small", {**py, **cs5})
    v, rs, cov = verdict_of(root, [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)])
    check("5 C# files with 0 engine nodes: DEGRADED naming C# (the old rule waited for 10 files)",
          v == V.DEGRADED and has_reason(rs, "C#"), (v, rs))
    cs4 = {f"Svc/S{i}.cs": f"class S{i} {{ }}\n" for i in range(4)}
    root = tree("a_stray", {**py, **cs4})
    v, rs, cov = verdict_of(root, [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)])
    check("4 stray C# files stay silent (below the small-N floor, as before)", not has_reason(rs, "C#"), (v, rs))
    root = tree("a_small_ok", {**py, **cs5})
    nodes = [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)]
    nodes += [engine_node(f"Svc/S{i}.cs", f"S{i}", label="Class") for i in range(5)]
    v, rs, _cov = verdict_of(root, nodes)
    check("5 C# files the engine DID extract: READY", v == V.READY, (v, rs))

    erb = {f"app/views/o/v{i}.html.erb": "<%= render 'x' %>\n" for i in range(4)}
    root = tree("a_erb", {**py, **erb})
    eng = [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)]
    nodes = eng + [{"key": f"app/views/o/v{i}.html.erb::v{i}:1", "label": "Module", "name": f"v{i}",
                    "source_file": f"app/views/o/v{i}.html.erb", "start_line": 1, "documented": False,
                    "body_summary": "view"} for i in range(4)]
    v, rs, cov = verdict_of(root, nodes, engine_keys={n["key"] for n in eng})
    check("ERB (recognizer-only by design): its view nodes still count -> not flagged",
          not has_reason(rs, "ERB") and (cov["languages"].get("ERB") or {}).get("nodes") == 4, (v, rs, cov["languages"]))

    root = tree("a_ast", {**py, **cs12})
    eng = [{"key": f"src/m{i}.py::f{i}:1", "label": "Function", "name": f"f{i}", "source_file": f"src/m{i}.py",
            "start_line": 1, "documented": False, "body_summary": "x"} for i in range(20)]
    nodes = eng + [recognizer_node(f"Models/C{i}.cs", f"C{i}") for i in range(12)]
    v, rs, cov = verdict_of(root, nodes, extractor="ast", extraction={"requested": "ast"},
                            engine_keys={n["key"] for n in eng})
    check("forced ast + 12 C# recognizer shells: DEGRADED naming C# (was READY)",
          v == V.DEGRADED and has_reason(rs, "C#"), (v, rs))
    check("the ast engine's own `rel::name:line` Python nodes still count as engine nodes",
          (cov["languages"].get("Python") or {}).get("nodes") == 20, cov["languages"].get("Python"))

    tests_js = {"tests/helpers/a.js": "function a() {}\n", "tests/helpers/b.js": "function b() {}\n"}
    root = tree("a_tests", {**py, **tests_js})
    base = [engine_node(f"src/m{i}.py", f"f{i}") for i in range(20)]
    with _env(DEEPMAP_EXCLUDE_TESTS="1"):
        v, rs, _cov = verdict_of(root, base)
    check("DEEPMAP_EXCLUDE_TESTS=1: test-only files dropped on purpose are not a small-N gap",
          not has_reason(rs, "JavaScript"), (v, rs))


def gate_b_never_rescued_dirs() -> None:
    print("\n-- B (T1#0/T2#0/T3#0): tracked hand-written source under env-family / dist dirs is named --")
    if not _GIT:
        print("  [SKIP] git is not installed")
        return
    files = {"src/app.py": "def main():\n    return 1\n"}
    files.update({f"src/myapp/env/s{i}.py": f"SETTING_{i} = {i}\n\ndef get_{i}():\n    return {i}\n" for i in range(6)})
    files.update({f"trading_env/t{i}.py": f"def step_{i}():\n    return {i}\n" for i in range(4)})
    files["dist/pkg.py"] = "def packaged():\n    return 1\n"
    # a committed virtualenv (pyvenv.cfg) and a generated bundle are NOT flagged
    files["venv/pyvenv.cfg"] = "home = /usr/bin\n"
    files["venv/lib/python3.11/site-packages/thirdparty.py"] = "def x():\n    return 1\n"
    files["web/dist/bundle.js"] = "(()=>{var __webpack_modules__={};})();\n"
    root = tree("b_env", files, git=True)
    nodes = [engine_node("src/app.py", "main")]
    v, rs, cov = verdict_of(root, nodes)
    un = cov.get("unindexed_dirs") or {}
    check("src/myapp/env/ (6 tracked files) is listed", (un.get("src/myapp/env") or {}).get("files") == 6, un)
    check("trading_env/ (a *_env package, 4 files) is listed", (un.get("trading_env") or {}).get("files") == 4, un)
    check("dist/ with hand-written tracked source is listed", (un.get("dist") or {}).get("files") == 1, un)
    check("a committed virtualenv (pyvenv.cfg) is NOT listed", not any(d.startswith("venv") for d in un), un)
    check("a generated bundle under web/dist/ is NOT listed", "web/dist" not in un, un)
    check("the verdict is DEGRADED naming src/myapp/env/ (was READY)",
          v == V.DEGRADED and has_reason(rs, "src/myapp/env/", "env"), (v, rs))
    check("the reason says how to get it indexed (rename the directory)", has_reason(rs, "src/myapp/env/", "rename"), rs)
    v2, rs2, _ = verdict_of(root, nodes, extractor="ast", extraction={"requested": "ast"})
    check("an ast-built graph (whose walker reads those dirs) gets no unindexed-dir reason",
          not has_reason(rs2, "src/myapp/env/"), rs2)
    # the suggested rename must actually take the directory out of the engine's skip list
    import graphify_extract as GX
    noise = GX._engine_noise_dir()
    if noise is not None:
        names = ["env", ".env", "venv", ".venv", "dist", "dist-protected", "trading_env", "gym_venv"]
        bad = [(n, V._rename_suggestion(n)) for n in names
               if GX._is_engine_noise(noise, V._rename_suggestion(n), None)]
        check("every suggested rename is a directory name the engine does NOT skip", not bad, bad)
        check("...and every original name IS skipped (the premise)",
              all(GX._is_engine_noise(noise, n, None) for n in names),
              [n for n in names if not GX._is_engine_noise(noise, n, None)])


def gate_c_non_git_skipped_dirs() -> None:
    print("\n-- C (T3#0): a NON-git tree's engine-skipped dirs holding hand-written source are named --")
    files = {"pkg/__init__.py": "", "pkg/mod.py": "def g():\n    return 3\n",
             "src/app.py": "def main():\n    return 1\n",
             "build/core.py": "def core_logic():\n    return 42\n",
             "out/report.py": "def report():\n    return 'r'\n",
             # setup.py-style build output: a byte-identical copy of pkg/mod.py -> not flagged
             "target/lib/pkg/mod.py": "def g():\n    return 3\n",
             # generated code -> not flagged
             "snapshots/gen.py": "# Code generated by protoc-gen. DO NOT EDIT.\ndef gen():\n    return 0\n",
             # a non-git virtualenv -> not flagged; a non-venv env/ dir -> flagged
             ".venv/pyvenv.cfg": "home = /usr/bin\n", ".venv/lib/x.py": "X = 1\n",
             "config/env/settings.py": "DEBUG = False\n\ndef load():\n    return DEBUG\n"}
    root = tree("c_nongit", files, git=False)
    nodes = [engine_node("src/app.py", "main"), engine_node("pkg/mod.py", "g")]
    v, rs, cov = verdict_of(root, nodes)
    un = cov.get("unindexed_dirs") or {}
    check("build/ (unique hand-written source) is listed as non-git",
          (un.get("build") or {}).get("files") == 1 and (un.get("build") or {}).get("kind") == "non-git", un)
    check("out/ is listed", (un.get("out") or {}).get("files") == 1, un)
    check("target/ holding only a byte-identical copy of pkg/mod.py is NOT listed", "target" not in un, un)
    check("snapshots/ holding only generated code is NOT listed", "snapshots" not in un, un)
    check("a non-git .venv (pyvenv.cfg) is NOT listed", ".venv" not in un, un)
    check("config/env/ (not a virtualenv) is listed", (un.get("config/env") or {}).get("files") == 1, un)
    check("the verdict is DEGRADED naming build/ and saying it is not a git repository",
          v == V.DEGRADED and has_reason(rs, "build/", "git"), (v, rs))
    if _GIT:
        root2 = tree("c_git_ignored", {"src/app.py": "def main():\n    return 1\n",
                                       ".gitignore": "build/\n", "build/core.py": "def c():\n    return 1\n"},
                     git=True)
        v2, rs2, cov2 = verdict_of(root2, [engine_node("src/app.py", "main")])
        check("a git repo with a gitignored build/: nothing listed, READY",
              not (cov2.get("unindexed_dirs") or {}) and v2 == V.READY, (v2, rs2, cov2.get("unindexed_dirs")))


def gate_d_long_paths() -> None:
    print("\n-- D (T10#14/T10d-8): source files past the Windows 260-char limit are counted and named --")
    if os.name != "nt":
        fn = getattr(V, "_long_path_findings", None)
        check("POSIX: no MAX_PATH scan (verdict._long_path_findings exists and returns {})",
              callable(fn) and fn(_TMP, lambda p, parent=None: False, 100) == {}, fn)
        return
    bs = chr(92)
    pfx = bs + bs + "?" + bs
    root = _TMP / "d_long"
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    deep = os.path.join(str(root), "pkg", *(["d" * 60] * 4))
    os.makedirs(pfx + deep, exist_ok=True)
    with open(pfx + os.path.join(deep, "deep_mod.py"), "w", encoding="utf-8") as fh:
        fh.write("def deep():\n    return 1\n")
    # a file whose own name pushes a readable dir's path over 260
    longname = "x" * (262 - len(str(root / "pkg")) - 1 - 3) + ".py"
    with open(pfx + os.path.join(str(root), "pkg", longname), "w", encoding="utf-8") as fh:
        fh.write("def x():\n    return 1\n")
    nodes = [engine_node("pkg/ok.py", "ok")]
    orig = getattr(V, "_long_paths_enabled", None)
    try:
        V._long_paths_enabled = lambda: False           # the default on this class of machine
        v, rs, cov = verdict_of(root, nodes)
        lp = cov.get("long_paths") or {}
        check("both files past the limit are counted (a deep dir + a long file name)", lp.get("files") == 2, lp)
        check("the verdict is DEGRADED with the MAX_PATH / LongPathsEnabled remedy (was READY)",
              v == V.DEGRADED and has_reason(rs, "260", "LongPathsEnabled"), (v, rs))
        V._long_paths_enabled = lambda: True            # long paths enabled: they are readable, no signal
        v, rs, cov = verdict_of(root, nodes)
        check("LongPathsEnabled=1: no long-path block and no reason", not cov.get("long_paths")
              and not has_reason(rs, "LongPathsEnabled"), (v, rs, cov.get("long_paths")))
    finally:
        if orig is not None:
            V._long_paths_enabled = orig
        shutil.rmtree(pfx + str(root), ignore_errors=True)


def gate_e_healthy_unchanged() -> None:
    print("\n-- E: a healthy tree stays READY; the census itself is unchanged --")
    files = {f"src/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(5)}
    files.update({f"web/w{i}.ts": f"export function w{i}() {{ return {i}; }}\n" for i in range(3)})
    root = tree("e_ok", files, git=bool(_GIT))
    nodes = [engine_node(f"src/m{i}.py", f"f{i}") for i in range(5)]
    nodes += [engine_node(f"web/w{i}.ts", f"w{i}") for i in range(3)]
    v, rs, cov = verdict_of(root, nodes)
    check("READY with no reasons", v == V.READY and rs == [], (v, rs))
    check("census unchanged ({'Python': 5, 'TypeScript': 3})", V.extension_census(root) == {"Python": 5, "TypeScript": 3},
          V.extension_census(root))
    check("no unindexed_dirs / long_paths blocks", "unindexed_dirs" not in cov and "long_paths" not in cov, cov)


def main() -> int:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    print("verdict_r7_gates — engine-only coverage, unindexed dirs, long paths")
    try:
        for g in (gate_a_engine_nodes_only, gate_b_never_rescued_dirs, gate_c_non_git_skipped_dirs,
                  gate_d_long_paths, gate_e_healthy_unchanged):
            try:
                g()
            except Exception as e:  # noqa: BLE001
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
