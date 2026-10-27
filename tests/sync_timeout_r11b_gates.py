#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11b gates (WE) for two LOW follow-ups of round 11 / M1 (the graphify time budget):

  V1  tests/sync_timeout_r11_gates.py section F handed sys.executable to install.ps1's Invoke-Setup, which splits
      its $PyCmd at the first space (the installer's own convention: "python" / "py -3"). On a box whose
      interpreter path has a space (C:\\Program Files\\..., a venv under "My Projects") every Invoke-Setup check
      went RED ("The term 'C:\\...\\py' is not recognized") and the rc-1 "throw" check passed for the wrong reason.
      The gate now hands Invoke-Setup a space-free command for a working Python (sys.executable, its 8.3 short
      path, a space-free python/python3 on PATH, or `py -3`) — or skips that check visibly — and a throw must be
      Invoke-Setup's own. Proven here by running that section with sys.executable pointed at a real venv whose
      path has a space.
  V4  the size-aware budget (engine_source_file_count) must count what the engine parses:
      a. an engine-skipped directory the case shim re-includes (build/ holding tracked source) is walked WHOLE by
         the engine — every source file in it, not only the tracked ones that qualified it (DeepMap drops the rest
         only afterwards) — so the budget counts it whole, by the very rule handed to the shim; a nested skipped
         dir that is not on a qualifying path (build/out/) stays uncounted, as the engine never walks it;
      b. symbolic links: the verifier's premise (graphify follows symlinked dirs once the root has a symlinked
         child) holds for `graphify extract` / a full build, NOT for the `graphify update` DeepMap runs — it calls
         watch._rebuild_code(follow_symlinks=False), which hands detect() an explicit False. Locked here against
         the installed engine (a future engine that changes it turns this RED), plus a behavioral check where the
         OS lets an unprivileged process create a directory symlink (skipped visibly elsewhere).

Hermetic unless noted (temp dirs + a temp DEEPMAP_HOME; no Neo4j / network). Needs graphify for V4 (the engine's
own skip rule), git for V4a, PowerShell for V1 — each skipped visibly where missing. The REAL engine runs in V4a's
agreement check when graphify is installed.
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_CREATED: list = []          # every temp dir THIS gate made — removed by exact path at the end


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail != '' else ''}")


def _mkdtemp(prefix: str) -> Path:
    p = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    _CREATED.append(p)
    return p


def _rmtree(path) -> None:
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    if path and Path(path).exists():
        shutil.rmtree(str(path), onerror=onerr)


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


@contextlib.contextmanager
def _patch(obj, **attrs):
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


def _write(root: Path, rel: str, text: str = "def f():\n    return 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _has_graphify() -> bool:
    try:
        return importlib.util.find_spec("graphify") is not None and GX._engine_noise_dir() is not None
    except Exception:
        return False


# =====================================================================================================
# V1 — sync_timeout_r11_gates section F with an interpreter path that has a space
# =====================================================================================================
def _load_r11():
    spec = importlib.util.spec_from_file_location(f"dm_r11b_st11_{time.time_ns()}", REPO / "tests" /
                                                  "sync_timeout_r11_gates.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _spaced_python(base: Path):
    """A working Python whose path contains a space: a venv (Windows — its python.exe is a launcher that runs
    from any path) or a symlink to sys.executable (POSIX). None when neither can be made."""
    home = base / "py space"
    try:
        if os.name == "nt":
            r = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(home / "v")], capture_output=True,
                               timeout=300, stdin=subprocess.DEVNULL)
            exe = home / "v" / "Scripts" / "python.exe"
            return str(exe) if r.returncode == 0 and exe.is_file() else None
        home.mkdir(parents=True, exist_ok=True)
        exe = home / "python3"
        os.symlink(os.path.realpath(sys.executable), exe)
        return str(exe)
    except Exception:
        return None


def gate_v1_space_in_interpreter() -> None:
    print("\n-- V1: sync_timeout_r11_gates F — Invoke-Setup gets a space-free Python command --")
    pwsh = (shutil.which("powershell") if os.name == "nt" else None) or shutil.which("pwsh")
    if not pwsh:
        print("  [skip] no PowerShell here — install.ps1's Invoke-Setup check does not run in section F either")
        return
    m = _load_r11()
    pick = getattr(m, "_space_free_python", None)
    check("sync_timeout_r11_gates has _space_free_python()", callable(pick))
    if callable(pick):
        cmd = pick()
        runs = False
        if cmd:
            argv = cmd.split(" ", 1)
            try:
                runs = subprocess.run([*argv, "-c", "import sys; sys.exit(5)"], capture_output=True, timeout=60,
                                      stdin=subprocess.DEVNULL).returncode == 5
            except Exception:
                runs = False
        check("... it returns a command with no space in its executable that runs Python (or None -> a visible skip)",
              cmd is None or (" " not in cmd.split(" ", 1)[0] and runs), cmd)
    base = _mkdtemp("dm_r11b_v1_")
    spaced = _spaced_python(base)
    if not spaced:
        print("  [skip] could not make a Python whose path has a space (venv / symlink failed)")
        return
    check("the spaced interpreter really has a space in its path and runs",
          " " in spaced and subprocess.run([spaced, "-c", "import sys; sys.exit(3)"], capture_output=True,
                                           timeout=60).returncode == 3, spaced)
    buf = io.StringIO()
    real, before = sys.executable, m._FAIL
    try:
        sys.executable = spaced
        with contextlib.redirect_stdout(buf):
            m.gate_f_installers()
    finally:
        sys.executable = real
        for p in list(getattr(m, "_CREATED", [])):
            m._rmtree(p)
    out = buf.getvalue()
    fails = [ln for ln in out.splitlines() if "[FAIL]" in ln]
    check("section F with sys.executable = a path WITH a space: no false RED (every Invoke-Setup check green or a "
          "visible skip)", m._FAIL == before and not fails, "\n".join(fails)[:700])
    check("... and the Invoke-Setup checks actually ran (not silently skipped) when a space-free command exists",
          ("Invoke-Setup rc 4 -> continue" in out) or ("[skip] no space-free Python command" in out), out[-700:])


# =====================================================================================================
# V4a — a re-included (engine-skipped) directory is counted WHOLE
# =====================================================================================================
def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL,
                          env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


TRACKED = ("a.py", "pkg/b.py", "build/tool.py", "dist/x.py")
UNTRACKED = ("build/gen1.py", "build/gen2.py", "build/gen3.py", "build/gen4.py", "build/out/deep.py")
# the engine parses: a.py, pkg/b.py, build/* (build/ re-included whole) — not build/out/ (a nested skipped dir on no
# qualifying path) and not dist/ (DeepMap never re-includes dist/)
ENGINE_PARSES = {"a.py", "pkg/b.py", "build/tool.py", "build/gen1.py", "build/gen2.py", "build/gen3.py",
                 "build/gen4.py"}


def _rescue_tree(base: Path) -> Path:
    root = base / "proj"
    root.mkdir(parents=True)
    for i, rel in enumerate(TRACKED + UNTRACKED):
        _write(root, rel, f"def f{i}():\n    return {i}\n\n\ndef g{i}():\n    return f{i}()\n")
    _git(root, "init", "-q")
    _git(root, "add", "--", *TRACKED)
    _git(root, "commit", "-q", "-m", "x")
    return root


def _run_extract_stub(root: Path, engine, **kw):
    """extract_with_report with the engine child faked (graphify reported installed + supported)."""
    real_find = importlib.util.find_spec

    def find_spec(name, *a, **k):
        if name == "graphify":
            return SimpleNamespace(name="graphify")
        return real_find(name, *a, **k)

    buf = io.StringIO()
    with _patch(importlib.util, find_spec=find_spec), \
            _patch(GX, _run_engine=lambda cmd, *, cwd, env, timeout: engine(cmd, cwd, env, timeout),
                   graphify_support=lambda: (True, "0.8.39")), \
            contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        res, report = GX.extract_with_report(root, **kw)
    return res, report, buf.getvalue()


def gate_v4a_rescued_dirs() -> None:
    print("\n-- V4a: the budget counts a re-included directory WHOLE (what the engine parses) --")
    if not _has_graphify():
        print("  [skip] graphify is not installed — the engine's skip rule (and the re-include) is unavailable")
        return
    if not shutil.which("git"):
        print("  [skip] git is not installed — nothing is re-included on a non-git tree")
        return
    base = _mkdtemp("dm_r11b_v4a_")
    home = base / "home"
    root = _rescue_tree(base)
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_GRAPHIFY_TIMEOUT=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None,
              DEEPMAP_CASE_INSENSITIVE_EXT=None):
        skipped = GX.skipped_source_dirs(root)
    check("fixture: build/ (tracked build/tool.py) is re-included; dist/ is not",
          sorted(skipped) == ["build"] and skipped["build"] == ["build/tool.py"], skipped)
    seen: dict = {}

    def engine(cmd, cwd, env, timeout):
        seen.update(timeout=timeout, rescue=env.get(GX.ENV_RESCUE))
        return GX._EngineResult(cmd, 1, "", "")

    with _env(DEEPMAP_HOME=str(home), DEEPMAP_GRAPHIFY_TIMEOUT=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None,
              DEEPMAP_CASE_INSENSITIVE_EXT=None):
        _res, report, _out = _run_extract_stub(root, engine)
    try:
        handed = json.loads(seen.get("rescue") or "{}")
    except ValueError:
        handed = {}
    check("the shim is handed build/ to re-include", handed.get("dirs") == ["build"], seen.get("rescue"))
    check(f"the budget counts every file the engine will parse: {len(ENGINE_PARSES)} (a.py, pkg/b.py and ALL of "
          f"build/ — was 3: the tracked build/tool.py only)", report.get("source_files") == len(ENGINE_PARSES),
          report.get("source_files"))

    def count(**kw):
        try:
            return GX.engine_source_file_count(root, **kw)
        except TypeError as e:
            return f"TypeError: {e}"

    n_plain, n_resc, n_name = count(), count(rescue=(["build"], [])), count(rescue=([], ["build"]))
    check("engine_source_file_count: 2 without the rescue, 7 with build/ re-included by path, 7 by name "
          "(build/out/ and dist/ never counted)", (n_plain, n_resc, n_name) == (2, 7, 7), (n_plain, n_resc, n_name))
    try:
        sized = GX.graphify_timeout(root, rescue=(["build"], []))
    except TypeError as e:
        sized = {"error": str(e)}
    check("graphify_timeout(rescue=...) sizes from the same count", sized.get("source_files") == 7, sized)
    rtest, spec = getattr(GX, "_rescued_dir_test", None), getattr(GX, "_rescue_spec", None)
    check("graphify_extract has _rescued_dir_test / _rescue_spec", callable(rtest) and callable(spec))
    if callable(rtest) and callable(spec):
        rs = rtest(root, (["build/out"], []))
        check("_rescued_dir_test: a nested rescued path matches only at that path (build/out, not out/ elsewhere)",
              rs("out", str(root / "build")) and not rs("out", str(root)) and not rs("build", str(root)), "")
        check("_rescue_spec: garbage -> None (never raises)", spec("{nope") is None and spec("") is None
              and spec(json.dumps({"root": "r", "names": ["build"]})) == ([], ["build"]))
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_GRAPHIFY_TIMEOUT=None, DEEPMAP_RESCUE_SKIPPED_DIRS="0",
              DEEPMAP_CASE_INSENSITIVE_EXT=None):
        _res, report0, _out = _run_extract_stub(root, engine)
    check("DEEPMAP_RESCUE_SKIPPED_DIRS=0: nothing re-included, the count is the plain walk (2)",
          report0.get("source_files") == 2 and not seen.get("rescue"), (report0.get("source_files"), seen))

    # the REAL engine: the budget's count is exactly the set of source files the engine parsed
    buf = io.StringIO()
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_GRAPHIFY_TIMEOUT=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None,
              DEEPMAP_CASE_INSENSITIVE_EXT=None), contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        res, rep = GX.extract_with_report(root)
    gpath = Path(str(rep.get("graphify_out") or "")) / "graph.json"
    parsed = set()
    if gpath.is_file():
        g = json.loads(gpath.read_text(encoding="utf-8"))
        for n in g.get("nodes", []):
            sf = str(n.get("source_file") or "").replace("\\", "/")
            if sf and sf.lower().endswith(GX.SOURCE_CODE_EXTS):
                try:
                    sf = Path(sf).resolve().relative_to(root).as_posix() if os.path.isabs(sf) else sf
                except (ValueError, OSError):
                    pass
                parsed.add(sf)
    check("REAL engine: it parsed build/ WHOLE (tracked + untracked) and neither build/out/ nor dist/",
          parsed == ENGINE_PARSES, sorted(parsed ^ ENGINE_PARSES) or buf.getvalue()[-500:])
    check("REAL engine: the budget's count == the number of source files it parsed",
          rep.get("source_files") == len(parsed) == len(ENGINE_PARSES), (rep.get("source_files"), len(parsed)))
    kept = {n["source_file"] for n in (res[0] if res else [])}
    check("REAL engine: DeepMap still keeps only the qualifying files (the untracked build/gen*.py are dropped after)",
          kept == {"a.py", "pkg/b.py", "build/tool.py"}, sorted(kept))


# =====================================================================================================
# V4b — symbolic links: `graphify update` never follows them, and neither does the count
# =====================================================================================================
def gate_v4b_symlinks() -> None:
    print("\n-- V4b: symlinked directories — the engine DeepMap runs does not follow them; neither does the count --")
    if not _has_graphify():
        print("  [skip] graphify is not installed")
        return
    import graphify.watch as W
    main_src = ""
    try:
        import graphify.__main__ as M
        main_src = inspect.getsource(M)
    except Exception:
        main_src = ""
    try:
        default = inspect.signature(W._rebuild_code).parameters["follow_symlinks"].default
    except Exception as e:  # noqa: BLE001
        default = f"<unreadable: {e}>"
    check("the installed engine: watch._rebuild_code(follow_symlinks=...) defaults to False (not None = auto)",
          default is False, default)
    i = main_src.find('cmd == "update"')
    call = re.search(r"_rebuild_code\(([^)]*)\)", main_src[i:i + 6000]) if i >= 0 else None
    check("... and `graphify update` calls it without follow_symlinks (so detect() gets an explicit False)",
          call is not None and "follow_symlinks" not in call.group(1), call.group(0) if call else main_src[:0])
    wsrc = inspect.getsource(W._rebuild_code)
    check("... which _rebuild_code hands to detect() as is", "detect(watch_path, follow_symlinks=follow_symlinks)"
          in wsrc, re.findall(r"detect\([^)]*\)", wsrc))
    csrc = inspect.getsource(GX.engine_source_file_count)
    check("engine_source_file_count walks without following symlinks (os.walk's default)",
          "followlinks" not in csrc.split('"""', 2)[-1], "")
    # behavioral, where an unprivileged process may create a directory symlink (POSIX; Windows with Developer Mode)
    base = _mkdtemp("dm_r11b_v4b_")
    root = base / "proj"
    _write(root, "a.py")
    outside = base / "elsewhere"
    for k in range(3):
        _write(outside, f"m{k}.py")
    try:
        os.symlink(str(outside), str(root / "linked"), target_is_directory=True)
        os.symlink(str(root / "a.py"), str(root / "CLAUDE.md"))      # a symlinked direct child of the root
    except (OSError, NotImplementedError) as e:
        print(f"  [skip] cannot create a symlink here ({type(e).__name__}) — the engine-source checks above stand in")
        return
    check("a symlinked dir (root with a symlinked child) is NOT counted — 1 file",
          GX.engine_source_file_count(root) == 1, GX.engine_source_file_count(root))
    buf = io.StringIO()
    with _env(DEEPMAP_HOME=str(base / "home"), DEEPMAP_GRAPHIFY_TIMEOUT=None), contextlib.redirect_stderr(buf), \
            contextlib.redirect_stdout(buf):
        _res, rep = GX.extract_with_report(root)
    gpath = Path(str(rep.get("graphify_out") or "")) / "graph.json"
    parsed = set()
    if gpath.is_file():
        for n in json.loads(gpath.read_text(encoding="utf-8")).get("nodes", []):
            sf = str(n.get("source_file") or "").replace("\\", "/")
            if sf.lower().endswith(GX.SOURCE_CODE_EXTS):
                parsed.add(sf.rsplit("/", 1)[-1])
    check("REAL engine: `graphify update` parsed a.py only (nothing behind the symlink) — the count agrees",
          parsed == {"a.py"} and rep.get("source_files") == 1, (sorted(parsed), rep.get("source_files"),
                                                                 buf.getvalue()[-300:]))


def main() -> int:
    print("sync_timeout_r11b_gates — V1 (space-free Invoke-Setup command) + V4 (the budget counts what the engine "
          "parses)")
    try:
        for gate in (gate_v1_space_in_interpreter, gate_v4a_rescued_dirs, gate_v4b_symlinks):
            try:
                gate()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        for p in _CREATED:
            _rmtree(p)
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
