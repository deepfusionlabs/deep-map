#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the graphify VERSION GUARD on the D5 directory re-include (round-3 decision E3). No Neo4j.

The defect (round-2 review, medium): DeepMap's re-include of git-tracked source in engine-skipped dirs
(build/, out/, target/ ...) and the verdict's census both lean on graphify 0.8's skip rule, which prunes
those dirs by NAME. The installers ran an unpinned `pip install graphifyy`, which now resolves to 0.9.x,
where `_is_noise_dir(part, parent=None)` decides out/ env/ *_env/ coverage/ snapshots/ from what is ON
DISK and answers "not noise" when no parent is given. On 0.9 the tracked build/core.py was never
re-included, compiled out/*.js and a .env/ tree entered the graph, and the census reported a false
"LOW COVERAGE for JavaScript" — extraction and verdict disagreed (the binding D5c rule).

Now (W8 pins the range in the installers; this is the runtime half). Round 4 (F3) narrowed the range to
exactly the validated releases, `graphifyy>=0.8.39,<0.8.52` (0.8.39 AND 0.8.51 validated) — see
tests/graphify_supported_range_gates.py; the checks below read that spec instead of repeating it:
  * graphify_extract.graphify_support() -> (supported, version) against SUPPORTED_GRAPHIFY_SPEC;
  * outside the range, extract_with_report SKIPS the re-include (no shim for it, no DEEPMAP_RESCUE_DIRS),
    warns with a runnable remedy, and reports `rescue_blocked`; the verdict names the uncovered dirs with
    the same remedy (DEGRADED, the "LOW COVERAGE in <dir>/ —" head the hooks read);
  * the census, the skipped-dir list and the rescue filter ask the engine's rule WITH the parent dir,
    exactly as the engine's own walker does, so they agree with what any engine version really walks.

Sections: A version parsing; B support table; C the installed engine; D extract_with_report wiring (stub
engine, real git); E verdict wording; F parent-aware rule vs a simulated graphify-0.9 rule; G the shim
is never handed a rescue on an unsupported engine.
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

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402
import verdict as V  # noqa: E402

_PASS = _FAIL = 0
_SPEC = "graphifyy>=0.8.39,<0.8.52"          # F3: the validated range (0.8.39 .. 0.8.51)
_BELOW = (0, 8, 52)
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
def _patched(obj, **attrs):
    saved = {k: getattr(obj, k, None) for k in attrs}
    had = {k: hasattr(obj, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if had[k]:
                setattr(obj, k, v)
            else:
                delattr(obj, k)


def _write(root: Path, rel: str, text: str = "def f():\n    return 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)


def _git_repo_commit(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "snap")


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


@contextlib.contextmanager
def _stub_engine(on_graphify):
    """graphify 'installed'; ONLY the engine subprocess is routed to `on_graphify`, git runs for real."""
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


def _rule_08(part, parent=None):
    """graphify 0.8.39's rule, for the tests that must not depend on the installed engine."""
    skip = {"venv", ".venv", "env", ".env", "node_modules", "__pycache__", ".git", "dist", "build",
            "target", "out", "site-packages", "lib64", "coverage", "snapshots", "__snapshots__"}
    return part in skip or part.endswith(("_venv", "_env", ".egg-info"))


_BUILD_OUTPUT_SUFFIXES_09 = (".class", ".jar", ".o", ".obj", ".exe", ".dll", ".pyc", ".js.map",
                             ".css.map", ".d.ts")


def _rule_09(part, parent=None):
    """A faithful subset of graphify 0.9.68's rule: build/dist/target by name; out/ only with build-output
    files in it, env/ only with virtualenv markers — and "not noise" for either when no parent is given."""
    if part in {"venv", ".venv", "node_modules", "__pycache__", ".git", "dist", "build", "target",
                "site-packages", "lib64", "__snapshots__"}:
        return True
    if part in ("env", ".env") or part.endswith("_env"):
        if parent is None:
            return False
        return (Path(parent) / part / "pyvenv.cfg").is_file()
    if part == "out":
        if parent is None:
            return False
        d = Path(parent) / part
        try:
            return any(f.name.lower().endswith(_BUILD_OUTPUT_SUFFIXES_09) for f in d.iterdir() if f.is_file())
        except OSError:
            return False
    return part.endswith(("_venv", ".egg-info"))


# ================================================================================================
def gate_a_parse() -> None:
    print("\n-- A: version strings -> comparable release tuples --")
    fn = getattr(GX, "_version_tuple", None)
    check("graphify_extract._version_tuple exists", callable(fn))
    if not callable(fn):
        return
    table = {"0.8.39": (0, 8, 39), "0.9": (0, 9, 0), "0.9.68": (0, 9, 68), "0.8.39.post1": (0, 8, 39),
             "0.9.0rc1": (0, 9, 0), "1": (1, 0, 0), "v0.8.40": (0, 8, 40), " 0.8.51 ": (0, 8, 51),
             "": None, None: None, "dev": None}
    for text, want in table.items():
        check(f"_version_tuple({text!r}) == {want}", fn(text) == want, fn(text))


def gate_b_support_table() -> None:
    print("\n-- B: graphify_support() — inside [0.8.39, 0.8.52) only --")
    fn = getattr(GX, "graphify_support", None)
    check("graphify_extract.graphify_support exists", callable(fn))
    check(f"SUPPORTED_GRAPHIFY_SPEC == {_SPEC!r} (the installers' pin, E3/F3)",
          getattr(GX, "SUPPORTED_GRAPHIFY_SPEC", None) == _SPEC, getattr(GX, "SUPPORTED_GRAPHIFY_SPEC", None))
    if not callable(fn):
        return
    table = {"0.8.39": True, "0.8.40": True, "0.8.51": True, "0.8.39.post1": True, "0.8.38": False,
             "0.8.5": False, "0.7.19": False, "0.8.52": False, "0.8.52rc1": False, "0.9.0": False,
             "0.9": False, "0.9.0rc1": False, "0.9.67": False, "0.9.68": False, "1.0.0": False}
    for v, want in table.items():
        with _patched(GX, graphify_version=lambda v=v: v):
            got = fn()
        check(f"graphify {v}: supported={want}", got == (want, v), got)
    with _patched(GX, graphify_version=lambda: None, _engine_noise_dir=lambda: _rule_08):
        check("no readable version + a name-based (0.8-style) rule -> supported", fn()[0] is True, fn())
    with _patched(GX, graphify_version=lambda: None, _engine_noise_dir=lambda: _rule_09):
        check("no readable version + a content-gated (0.9-style) rule -> NOT supported", fn()[0] is False,
              fn())
    with _patched(GX, graphify_version=lambda: None, _engine_noise_dir=lambda: None):
        check("no version and no engine rule -> NOT supported", fn()[0] is False, fn())


def gate_c_installed() -> None:
    print("\n-- C: the installed engine (this box) --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — skipped)")
        return
    fn = getattr(GX, "graphify_support", None)
    if not callable(fn):
        check("graphify_support exists", False)
        return
    ok, v = fn()
    print(f"     installed graphify: {v}")
    check("the installed version is readable from the package metadata", bool(v), v)
    t = GX._version_tuple(v) if v else None
    check("graphify_support() agrees with the version (in range <=> supported)",
          ok == (t is not None and (0, 8, 39) <= t < _BELOW), (ok, v))


def _skipdir_repo(root: Path) -> None:
    _write(root, "src/app.py", "def app():\n    return 1\n")
    _write(root, "build/core.py", "def core():\n    return 2\n")
    _write(root, "build/helpers.py", "def helper():\n    return 3\n")
    _git_repo_commit(root)


def _run_extract(root: Path, version: str, **env):
    seen: dict = {}

    def on_graphify(cmd, kw):
        e = kw.get("env") or {}
        seen["cmd"], seen["rescue"] = cmd, e.get(GX.ENV_RESCUE)
        out = Path(e["GRAPHIFY_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        g = {"nodes": [{"id": "src_app", "label": "app.py", "file_type": "code", "source_file": "src/app.py",
                        "source_location": "L1"},
                       {"id": "src_app_app", "label": "app()", "file_type": "code",
                        "source_file": "src/app.py", "source_location": "L1"}],
             "links": [{"source": "src_app", "target": "src_app_app", "relation": "contains"}]}
        (out / "graph.json").write_text(json.dumps(g), encoding="utf-8")
        return _Proc(0)

    err = io.StringIO()
    with _stub_engine(on_graphify), _patched(GX, graphify_version=lambda: version), _env(**env), \
            contextlib.redirect_stderr(err):
        res, report = GX.extract_with_report(root)
    return res, report, seen, err.getvalue()


def gate_d_extract_wiring() -> None:
    print("\n-- D: extract_with_report skips the re-include on an unsupported engine, loudly --")
    if not _HAS_GIT or not _HAS_ENGINE:
        print("     (git or the engine's skip rule unavailable — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        _skipdir_repo(root)
        res, rep, seen, err = _run_extract(root, "0.9.68", DEEPMAP_RESCUE_SKIPPED_DIRS=None)
        check("0.9.68: a result still comes back (the engine ran)", bool(res and res[0]), rep.get("cause"))
        check("0.9.68: NO DEEPMAP_RESCUE_DIRS handed to the engine", not seen.get("rescue"), seen.get("rescue"))
        check("0.9.68: the plain `-m graphify update` command (no shim needed)",
              "-m" in (seen.get("cmd") or []) and not any("graphify_case_shim" in str(c)
                                                           for c in seen.get("cmd") or []), seen.get("cmd"))
        check("0.9.68: report rescue_blocked == ['build'], rescued_dirs empty",
              rep.get("rescue_blocked") == ["build"] and not rep.get("rescued_dirs"),
              {k: rep.get(k) for k in ("rescue_blocked", "rescued_dirs")})
        check("0.9.68: report graphify_version / graphify_supported",
              rep.get("graphify_version") == "0.9.68" and rep.get("graphify_supported") is False, rep)
        check("0.9.68: the log names the skipped dir, the version and the runnable pin",
              "build/" in err and "0.9.68" in err and _SPEC in err
              and "-m pip install" in err and "/deep-map:refresh" in err, err[-600:])

        res, rep, seen, err = _run_extract(root, "0.8.39", DEEPMAP_RESCUE_SKIPPED_DIRS=None)
        payload = json.loads(seen.get("rescue") or "{}")
        check("0.8.39: the re-include runs (DEEPMAP_RESCUE_DIRS names build)", payload.get("dirs") == ["build"],
              seen.get("rescue"))
        check("0.8.39: through the shim", any("graphify_case_shim" in str(c) for c in seen.get("cmd") or []),
              seen.get("cmd"))
        check("0.8.39: rescued_dirs == ['build'], nothing blocked, no version warning",
              rep.get("rescued_dirs") == ["build"] and not rep.get("rescue_blocked")
              and "outside the range" not in err, (rep.get("rescued_dirs"), rep.get("rescue_blocked"), err[-300:]))

        res, rep, seen, err = _run_extract(root, "0.9.68", DEEPMAP_RESCUE_SKIPPED_DIRS="0")
        check("0.9.68 + DEEPMAP_RESCUE_SKIPPED_DIRS=0: nothing 'blocked' (the operator switched it off)",
              not rep.get("rescue_blocked") and not seen.get("rescue"), rep.get("rescue_blocked"))
        check("...but the engine-range warning still shows", "outside the range" in err, err[-300:])


def gate_e_verdict() -> None:
    print("\n-- E: the verdict names the uncovered dir and the same remedy (DEGRADED) --")
    if not _HAS_GIT or not _HAS_ENGINE:
        print("     (git or the engine's skip rule unavailable — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        _skipdir_repo(root)
        nodes = [{"source_file": "src/app.py"}, {"source_file": "src/app.py"}]
        for version, supported in (("0.9.68", False), ("0.8.39", True)):
            with _patched(GX, graphify_version=lambda v=version: v), _env(DEEPMAP_RESCUE_SKIPPED_DIRS=None):
                cov = V.build_coverage(root, nodes)
                verdict, reasons = V.classify(neo4j_ok=True, n_nodes=2, n_embedded=2,
                                              census=V.extension_census(root), extractor="graphify",
                                              coverage=cov)
            build_r = [r for r in reasons if r.startswith("LOW COVERAGE in build/ —")]
            check(f"{version}: DEGRADED with the 'LOW COVERAGE in build/ —' head the hooks read",
                  verdict == "DEGRADED" and len(build_r) == 1, (verdict, reasons))
            text = build_r[0] if build_r else ""
            if not supported:
                check("0.9.68: the reason says the engine is outside the supported range + the pip pin",
                      "0.9.68" in text and _SPEC in text and "-m pip install" in text
                      and "/deep-map:refresh" in text, text)
            else:
                check("0.8.39: the round-2 wording (re-include did not bring it in), no version remedy",
                      "outside the range" not in text and "re-include" in text, text)


def gate_f_parent_aware() -> None:
    print("\n-- F: census / skipped-dir list ask the engine's rule WITH the parent (simulated 0.9 rule) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        # a TypeScript out/ of compiled JS WITH build-output evidence (0.9 skips it, like 0.8)
        for i in range(12):
            _write(root, f"out/mod{i}.js", f"exports.m{i} = function() {{ return {i}; }};\n")
            _write(root, f"out/mod{i}.js.map", "{}\n")
        # a hexagonal adapter/out/ WITHOUT build evidence (0.9 walks it)
        _write(root, "adapter/out/port.py", "def port():\n    return 1\n")
        _write(root, "src/main.ts", "export function main() { return 1; }\n")
        with _patched(GX, _engine_noise_dir=lambda: _rule_09):
            census = V.extension_census(root)
            ignored = V._dir_ignore_rule()
        check("0.9 rule: compiled out/*.js (with .js.map) is NOT counted — the engine skips it",
              census.get("JavaScript", 0) == 0, census)
        check("0.9 rule: adapter/out/port.py IS counted — the engine walks it", census.get("Python", 0) == 1,
              census)
        try:
            with _patched(GX, _engine_noise_dir=lambda: _rule_09):
                r1 = ignored("out", root)
                r2 = ignored("out", root / "adapter")
            check("the census rule takes the parent: out/ with build output ignored, adapter/out/ kept",
                  r1 is True and r2 is False, (r1, r2))
        except TypeError as e:
            check("the census rule takes (part, parent)", False, e)
        # the same repo under the 0.8 rule: out/ is skipped by name wherever it is
        with _patched(GX, _engine_noise_dir=lambda: _rule_08):
            census08 = V.extension_census(root)
        check("0.8 rule: both out/ dirs are skipped by name (census counts neither)",
              census08.get("JavaScript", 0) == 0 and census08.get("Python", 0) == 0, census08)
        with _patched(GX, _engine_noise_dir=lambda: _rule_09, graphify_version=lambda: "0.9.68"):
            verdict, reasons = V.classify(neo4j_ok=True, n_nodes=2, n_embedded=2, census=census,
                                          extractor="graphify",
                                          coverage=V.build_coverage(root, [{"source_file": "src/main.ts"},
                                                                           {"source_file": "adapter/out/port.py"}],
                                                                    census=census))
        check("0.9 rule: no false 'LOW COVERAGE for JavaScript' from compiled out/*.js",
              not any("LOW COVERAGE for JavaScript" in r for r in reasons), reasons)

    if not _HAS_GIT:
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        _write(root, "adapter/out/port.py", "def port():\n    return 1\n")   # 0.9 walks it
        _write(root, "tools/out/gen.py", "def gen():\n    return 1\n")       # 0.9 skips it (evidence)
        _write(root, "tools/out/Gen.class", "x")
        _write(root, "build/core.py", "def core():\n    return 1\n")
        _git_repo_commit(root)
        with _patched(GX, _engine_noise_dir=lambda: _rule_09):
            found = GX.skipped_source_dirs(root)
        check("0.9 rule: skipped_source_dirs lists build/ and the evidence-gated tools/out/ ...",
              "build" in found and "tools/out" in found, found)
        check("...but not adapter/out/, which the engine walks", "adapter/out" not in found, found)
        with _patched(GX, _engine_noise_dir=lambda: _rule_08):
            found08 = GX.skipped_source_dirs(root)
        check("0.8 rule: every out/ is engine-skipped by name (unchanged round-2 listing)",
              {"build", "tools/out", "adapter/out"} <= set(found08), found08)


def gate_g_shim_contract() -> None:
    print("\n-- G: an unsupported engine never gets a narrowed skip rule from the shim --")
    if not _HAS_GIT or not _HAS_ENGINE:
        print("     (git or engine unavailable — skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        _skipdir_repo(root)
        _write(root, "src/UNIT1.PAS", "unit Unit1;\ninterface\nimplementation\nend.\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "pas")
        res, rep, seen, err = _run_extract(root, "0.9.68", DEEPMAP_RESCUE_SKIPPED_DIRS=None,
                                           DEEPMAP_CASE_INSENSITIVE_EXT=None)
        cmd = seen.get("cmd") or []
        check("odd-case aliasing still runs through the shim (it is not version-gated) ...",
              any("graphify_case_shim" in str(c) for c in cmd) and ".PAS" in cmd, cmd)
        check("...but the shim is handed NO rescue list on 0.9", not seen.get("rescue"), seen.get("rescue"))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("GRAPHIFY VERSION GUARD GATES — E3 (no Neo4j)")
    print("=" * 78)
    home = Path(tempfile.mkdtemp(prefix="dmvg_home_"))
    try:
        with _env(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None,
                  DEEPMAP_CASE_INSENSITIVE_EXT=None, DEEPMAP_SELF_CALL_REBIND=None,
                  DEEPMAP_CLEAN_OWNER_NAMES=None, DEEPMAP_RESOLVED_IMPORTS=None, DEEPMAP_EXCLUDE_TESTS=None):
            gate_a_parse()
            gate_b_support_table()
            gate_c_installed()
            gate_d_extract_wiring()
            gate_e_verdict()
            gate_f_parent_aware()
            gate_g_shim_contract()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
