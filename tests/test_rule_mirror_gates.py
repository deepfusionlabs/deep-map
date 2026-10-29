#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j drift locks for the hand copies of THE test-code rule, build/sync.py:_is_test_path (KL-architecture:002).

sync._is_test_path decides what DEEPMAP_EXCLUDE_TESTS drops, which callers count as test code (CALLS_PLUS, typed
linking, the TypeScript mounts) and which nodes enrichment spends its budget on last. Four modules keep a fallback
copy of it for when `import sync` fails: enrich._is_test_file, graphify_extract._test_path_predicate,
typescript_edges._test_path_predicate and verdict._test_path_predicate. The .NET test-project suffix rule
(src/Shop.Tests/, sync._DOTNET_TEST_DIR_SUFFIXES) reached those copies by hand in 2026-10 with no lock, and the
verdict's own test-directory check never learned it. No Neo4j, no network; temp trees only (git is used for the
tracked-file cases and skipped when absent).

  A  each copy's constants equal sync's (_TEST_DIR_PARTS as a set, _DOTNET_TEST_DIR_SUFFIXES as the same tuple), and no
     other module under build/ or hooks/ spells them (a new copy is added here first)
  B  with sync importable, each predicate answers through sync._is_test_path (a recorder seam for enrich)
  C  with sync blocked (sys.modules['sync'] = None, the way extraction_calls_r10_gates gets graphify_extract's), each
     fallback agrees with sync._is_test_path on tests/exclude_tests_gates.py's should / shouldnt paths (read from that
     file, so the lists stay one), the .NET probes and probes generated from sync's own constants
  D  verdict under DEEPMAP_EXCLUDE_TESTS: an engine-skipped directory inside a .NET test project (src/Shop.Tests/build/)
     is no longer reported as skipped or unindexed source (a false DEGRADED, since sync drops those files on purpose);
     a production directory named like one still is; a test FILE in such a directory is not counted either; the
     default (switch off) never consults the rule
"""
from __future__ import annotations

import ast
import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import sync as S  # noqa: E402  (imports graphify_extract, enrich, the recognizers)
import enrich as EN  # noqa: E402
import graphify_extract as GX  # noqa: E402
import typescript_edges as TE  # noqa: E402
import verdict as V  # noqa: E402

_PASS = _FAIL = 0
_GIT = shutil.which("git")
_TMP = Path(tempfile.mkdtemp(prefix="dm_testrule_mirror_"))

# The four fallback copies of THE rule, and where each keeps its constants.
_COPIES = {
    "enrich": (EN, "_TEST_DIRS", "_DOTNET_TEST_DIR_SUFFIXES"),
    "graphify_extract": (GX, "_TEST_DIRS_FALLBACK", "_DOTNET_TEST_DIR_SUFFIXES_FALLBACK"),
    "typescript_edges": (TE, "_TEST_DIR_PARTS", "_DOTNET_TEST_DIR_SUFFIXES"),
    "verdict": (V, "_TEST_DIR_PARTS", "_DOTNET_TEST_DIR_SUFFIXES"),
}
# The .NET probes the review named (2026-10), beside exclude_tests_gates' own lists.
_DOTNET_PROBES = ["src/Shop.Tests/X.cs", "Shop.UnitTests/Fakes/FakeRepo.cs", "src/Shop.Testing.Abstractions/X.cs",
                  "src/Contest/X.cs", "api.test/router.ts"]


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' -- ' + str(detail)[:900]) if detail != '' else ''}")


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
def _sync_blocked():
    """`import sync` raises ImportError inside the block (each copy's fallback path), restored after."""
    saved = sys.modules.get("sync")
    sys.modules["sync"] = None
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["sync"] = saved
        else:
            sys.modules.pop("sync", None)


@contextlib.contextmanager
def _patched(obj, name, value):
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


def _excl_lists() -> tuple:
    """(should, shouldnt) as tests/exclude_tests_gates.py:gate_is_test_path spells them (parsed, never imported)."""
    src = (REPO / "tests" / "exclude_tests_gates.py").read_text(encoding="utf-8")
    found: dict = {}
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, ast.FunctionDef) and fn.name == "gate_is_test_path":
            for st in ast.walk(fn):
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) \
                        and st.targets[0].id in ("should", "shouldnt"):
                    found[st.targets[0].id] = list(ast.literal_eval(st.value))
    return found.get("should") or [], found.get("shouldnt") or []


def _generated_probes() -> list:
    """Paths built from sync's own constants, so a new suffix or test dir is probed without editing this file."""
    out = []
    for s in S._DOTNET_TEST_DIR_SUFFIXES:
        stem = "Shop" + s[0] + s[1:2].upper() + s[2:]               # .unittests -> Shop.Unittests
        out += [f"src/{stem}/X.cs", f"src/{stem.upper()}/x.cs", f"a\\{stem.lower()}\\b\\c.cs",
                f"src/{stem}", f"src/{stem}.cs", f"src/{stem}Helpers/X.cs", f"src/{stem}.Common/X.cs"]
    for d in sorted(S._TEST_DIR_PARTS):
        out += [f"pkg/{d}/x.go", f"{d.upper()}/X.JAVA", f"pkg/{d}", f"pkg/{d}x/y.py", f"pkg/x{d}/y.py"]
    out += ["", "x", "/", "//a//", "a/b/c.py", "Test.java", "test.java", "tests", "TESTS/x", "x.test.mts",
            "x.spec.cts", "x.TEST.TS", "Test_x.PY", "a/b/test_.py", "test_x.pyi", "x_TEST.go", "x_spec.RB",
            # production directories a well-meaning mirror edit might add (sync keeps every one)
            "pkg/fixtures/x.py", "pkg/testdata/x.go", "pkg/mocks/x.ts", "pkg/__fixtures__/x.js", "pkg/test-utils/x.ts",
            "pkg/testutil/x.go", "src/Shop.Mocks/X.cs", "src/Shop.Fakes/X.cs", "src/Shop.TestUtilities/X.cs"]
    return out


def _rm_tree(path: Path) -> None:
    """Remove this gate's own temp tree. git writes its object files read-only, and on Windows rmtree cannot
    delete a read-only file, so each failure is retried once after clearing the flag."""
    def retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _source_files() -> list:
    out = []
    for d in (BUILD, REPO / "hooks"):
        out += sorted(p for p in d.glob("*.py") if p.is_file())
    return out


# ================================================================================================
def gate_a_constants() -> None:
    print("\n-- A: each copy's constants == sync's; no unlocked copy anywhere under build/ or hooks/ --")
    want_dirs, want_suf = set(S._TEST_DIR_PARTS), S._DOTNET_TEST_DIR_SUFFIXES
    check("sync._DOTNET_TEST_DIR_SUFFIXES is a non-empty tuple of dotted lower-case suffixes (str.endswith needs a tuple)",
          isinstance(want_suf, tuple) and want_suf and all(isinstance(s, str) and s.startswith(".") and s == s.lower()
                                                            for s in want_suf), want_suf)
    for name, (mod, dirs_attr, suf_attr) in _COPIES.items():
        dirs, suf = getattr(mod, dirs_attr, None), getattr(mod, suf_attr, None)
        check(f"{name}.{dirs_attr} == sync._TEST_DIR_PARTS",
              isinstance(dirs, (set, frozenset)) and set(dirs) == want_dirs,
              (sorted(dirs) if isinstance(dirs, (set, frozenset)) else dirs, sorted(want_dirs)))
        check(f"{name}.{suf_attr} == sync._DOTNET_TEST_DIR_SUFFIXES (the same tuple)",
              isinstance(suf, tuple) and suf == want_suf, (suf, want_suf))
    # Every module that spells the constants must be one this gate locks (or sync itself).
    locked = {"sync.py"} | {f"{n}.py" for n in _COPIES}
    suf_re = re.compile(r"""["']\.integrationtests["']""")
    dirs_re = re.compile(r"""["']__mocks__["']""")
    holders_suf, holders_dirs = set(), set()
    for p in _source_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(REPO).as_posix()
        if suf_re.search(text):
            holders_suf.add(rel)
        if dirs_re.search(text):
            holders_dirs.add(rel)
    want_holders = {f"build/{f}" for f in locked}
    check("the scan finds every locked copy of the suffix tuple (the scan works)",
          want_holders <= holders_suf, sorted(want_holders - holders_suf))
    check("no unlocked module under build/ or hooks/ spells the .NET suffix tuple (add a new copy to _COPIES first)",
          holders_suf <= want_holders, sorted(holders_suf - want_holders))
    check("no unlocked module under build/ or hooks/ spells the test-dir set",
          holders_dirs <= want_holders, sorted(holders_dirs - want_holders))


# ================================================================================================
def gate_b_through_sync() -> None:
    print("\n-- B: with sync importable, each predicate answers through sync._is_test_path --")
    for name, mod in (("graphify_extract", GX), ("typescript_edges", TE), ("verdict", V)):
        fn = getattr(mod, "_test_path_predicate", None)
        pred = fn() if callable(fn) else None
        check(f"{name}._test_path_predicate() IS sync._is_test_path", pred is S._is_test_path, pred)
    seen: list = []

    def recorder(sf):
        seen.append(sf)
        return sf == "probe/zz.marker"            # no fallback copy would call this path test code
    with _patched(S, "_is_test_path", recorder):
        got = EN._is_test_file("probe/zz.marker")
    check("enrich._is_test_file answers through sync._is_test_path (a recorder seam)",
          got is True and seen == ["probe/zz.marker"], (got, seen))


# ================================================================================================
def gate_c_fallbacks_agree() -> None:
    print("\n-- C: with sync blocked, each fallback agrees with sync._is_test_path --")
    should, shouldnt = _excl_lists()
    check("exclude_tests_gates' should / shouldnt lists were read (and hold the .NET cases)",
          len(should) >= 30 and len(shouldnt) >= 30 and "src/Shop.Tests/X.cs" in should
          and "src/Shop.Testing.Abstractions/X.cs" in shouldnt, (len(should), len(shouldnt)))
    check("premise: sync._is_test_path calls every `should` path test code and no `shouldnt` path",
          all(S._is_test_path(p) for p in should) and not any(S._is_test_path(p) for p in shouldnt),
          [p for p in should if not S._is_test_path(p)] + [p for p in shouldnt if S._is_test_path(p)])
    want_dotnet = {"src/Shop.Tests/X.cs": True, "Shop.UnitTests/Fakes/FakeRepo.cs": True,
                   "src/Shop.Testing.Abstractions/X.cs": False, "src/Contest/X.cs": False, "api.test/router.ts": True}
    check("premise: sync._is_test_path on the .NET probes",
          {p: S._is_test_path(p) for p in _DOTNET_PROBES} == want_dotnet, {p: S._is_test_path(p) for p in _DOTNET_PROBES})
    corpus = list(dict.fromkeys(should + shouldnt + _DOTNET_PROBES + _generated_probes()))
    with _sync_blocked():
        fallbacks = {"enrich._is_test_file": EN._is_test_file,
                     "graphify_extract._test_path_predicate()": GX._test_path_predicate(),
                     "typescript_edges._test_path_predicate()": TE._test_path_predicate(),
                     "verdict._test_path_predicate()": (V._test_path_predicate()
                                                        if callable(getattr(V, "_test_path_predicate", None))
                                                        else None)}
        for label, fb in fallbacks.items():
            if not callable(fb):
                check(f"{label}: a fallback exists", False, fb)
                continue
            if label != "enrich._is_test_file":
                check(f"{label}: is the fallback, not sync's function", fb is not S._is_test_path, fb)
            bad = []
            for p in corpus:
                try:
                    got = bool(fb(p))
                except Exception as e:  # noqa: BLE001
                    got = f"raised {e!r}"
                if got != S._is_test_path(p):
                    bad.append((p, got, S._is_test_path(p)))
            check(f"{label} agrees with sync._is_test_path on {len(corpus)} paths", not bad, bad[:12])
            try:
                none_ok = bool(fb(None)) is False
            except Exception as e:  # noqa: BLE001
                none_ok = f"raised {e!r}"
            check(f"{label}(None) is False and does not raise", none_ok is True, none_ok)


# ================================================================================================
def _tree(name: str, files: dict, *, git: bool) -> Path:
    root = _TMP / name
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    if git and _GIT:
        def run(*a):
            return subprocess.run([_GIT, "-C", str(root), *a], capture_output=True, timeout=60)
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "t")
        run("config", "core.autocrlf", "false")
        run("add", "-A", "-f")
        run("commit", "-q", "-m", "fixture")
    return root


def _cs(ns: str, cls: str) -> str:
    return (f"namespace {ns}\n{{\n    public class {cls}\n    {{\n        public int Build{cls}() {{ return {len(cls)}; }}\n"
            f"    }}\n}}\n")


def _node(sf: str, name: str, label: str = "Class") -> dict:
    return {"key": sf.replace("/", "_").replace(".", "_") + "_" + name.lower(), "label": label, "name": name,
            "source_file": sf, "start_line": 1, "documented": False, "body_summary": f"{name}: {label.lower()}"}


def _verdict(root, nodes):
    census = V.extension_census(root)
    cov = V.build_coverage(root, nodes, census=census)
    v, rs = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                       extractor="graphify", ml_ok=True, coverage=cov, extraction={"requested": "auto"})
    return v, rs, cov


def _named(reasons, needle) -> bool:
    return any(needle in r for r in reasons)


def gate_d_verdict() -> None:
    print("\n-- D: the verdict under DEEPMAP_EXCLUDE_TESTS uses THE rule (.NET test projects; test files) --")
    noise = GX._engine_noise_dir()
    if noise is None:
        print("  [SKIP] the graphify engine's directory-skip rule is unavailable (graphify not importable)")
        return
    prod = {f"src/Shop/Order{i}.cs": _cs("Shop", f"Order{i}") for i in range(8)}
    prod_nodes = [_node(f"src/Shop/Order{i}.cs", f"Order{i}") for i in range(8)]

    # D1  git: a .NET test project's engine-skipped build/ (tracked, re-included by the rescue, dropped by sync)
    if _GIT:
        files = dict(prod)
        files.update({f"src/Shop.Tests/build/OrderBuilder{i}.cs": _cs("Shop.Tests.Build", f"OrderBuilder{i}")
                      for i in range(4)})
        files["src/Shop.Tests/Fakes/FakeRepo.cs"] = _cs("Shop.Tests.Fakes", "FakeRepo")
        root = _tree("d1_dotnet_git", files, git=True)
        with _env(DEEPMAP_EXCLUDE_TESTS=None):
            v0, rs0, cov0 = _verdict(root, prod_nodes)
        check("premise: tests kept, src/Shop.Tests/build/ with no nodes is a real gap (listed, DEGRADED)",
              (cov0.get("skipped_dirs") or {}).get("src/Shop.Tests/build", {}).get("files") == 4
              and v0 == V.DEGRADED and _named(rs0, "src/Shop.Tests/build/"), (v0, rs0, cov0.get("skipped_dirs")))
        with _env(DEEPMAP_EXCLUDE_TESTS="1"):
            v1, rs1, cov1 = _verdict(root, prod_nodes)
        check("DEEPMAP_EXCLUDE_TESTS=1: src/Shop.Tests/build/ (dropped on purpose) is not a skipped dir",
              "src/Shop.Tests/build" not in (cov1.get("skipped_dirs") or {}), cov1.get("skipped_dirs"))
        check("...and the verdict is READY (was a false DEGRADED)", v1 == V.READY, (v1, rs1))
        seen: list = []
        rule = S._is_test_path

        def recorder(sf):
            seen.append(sf)
            return rule(sf)
        with _patched(S, "_is_test_path", recorder), _env(DEEPMAP_EXCLUDE_TESTS="1"):
            V.build_coverage(root, prod_nodes)
        check("the verdict asks sync._is_test_path itself (a recorder seam saw the test project's files)",
              "src/Shop.Tests/build/OrderBuilder0.cs" in seen, seen[:8])

        # D2  production directories named like test projects stay findings
        files = dict(prod)
        files.update({"src/Shop.Testing.Abstractions/build/Helper.cs": _cs("Shop.Testing.Abstractions", "Helper"),
                      "src/Contest/build/Score.cs": _cs("Contest", "Score"),
                      "src/Shop.Latest/build/Feed.cs": _cs("Shop.Latest", "Feed")})
        root = _tree("d2_lookalikes_git", files, git=True)
        with _env(DEEPMAP_EXCLUDE_TESTS="1"):
            v2, rs2, cov2 = _verdict(root, prod_nodes)
        sk = cov2.get("skipped_dirs") or {}
        check("DEEPMAP_EXCLUDE_TESTS=1: production look-alikes (Shop.Testing.Abstractions, Contest, Shop.Latest) "
              "stay skipped-dir findings",
              all(sk.get(d, {}).get("files") == 1 for d in ("src/Shop.Testing.Abstractions/build", "src/Contest/build",
                                                            "src/Shop.Latest/build")), sk)
        check("...and the verdict is DEGRADED naming them", v2 == V.DEGRADED and _named(rs2, "src/Contest/build/")
              and _named(rs2, "src/Shop.Testing.Abstractions/build/"), (v2, rs2))

        # D3  git: a .NET test project's env/ (never re-included -> unindexed)
        files = dict(prod)
        files.update({f"src/Shop.IntegrationTests/env/Settings{i}.cs": _cs("Shop.IntegrationTests.Env", f"Settings{i}")
                      for i in range(3)})
        root = _tree("d3_dotnet_env_git", files, git=True)
        with _env(DEEPMAP_EXCLUDE_TESTS=None):
            _v, _r, cov3a = _verdict(root, prod_nodes)
        check("premise: tests kept, src/Shop.IntegrationTests/env/ is an unindexed (never-rescued) dir",
              (cov3a.get("unindexed_dirs") or {}).get("src/Shop.IntegrationTests/env", {}).get("kind") == "never-rescued",
              cov3a.get("unindexed_dirs"))
        with _env(DEEPMAP_EXCLUDE_TESTS="1"):
            v3, rs3, cov3 = _verdict(root, prod_nodes)
        check("DEEPMAP_EXCLUDE_TESTS=1: it is not listed and the verdict is READY",
              not (cov3.get("unindexed_dirs") or {}) and v3 == V.READY, (v3, rs3, cov3.get("unindexed_dirs")))
    else:
        print("  [SKIP] git is not installed (D1-D3)")

    # D4  non-git: a .NET test project's engine-skipped out/ (nothing is re-included in a non-git tree)
    files = dict(prod)
    files.update({f"src/Shop.UnitTests/out/FakeRepo{i}.cs": _cs("Shop.UnitTests.Out", f"FakeRepo{i}") for i in range(3)})
    root = _tree("d4_dotnet_nongit", files, git=False)
    with _env(DEEPMAP_EXCLUDE_TESTS=None):
        v4a, _r, cov4a = _verdict(root, prod_nodes)
    check("premise: tests kept, src/Shop.UnitTests/out/ is an unindexed non-git dir (DEGRADED)",
          (cov4a.get("unindexed_dirs") or {}).get("src/Shop.UnitTests/out", {}).get("kind") == "non-git"
          and v4a == V.DEGRADED, (v4a, cov4a.get("unindexed_dirs")))
    with _env(DEEPMAP_EXCLUDE_TESTS="1"):
        v4, rs4, cov4 = _verdict(root, prod_nodes)
    check("DEEPMAP_EXCLUDE_TESTS=1: it is not listed and the verdict is READY (was a false DEGRADED)",
          not (cov4.get("unindexed_dirs") or {}) and v4 == V.READY, (v4, rs4, cov4.get("unindexed_dirs")))

    # D5  non-git: THE rule judges each FILE, so test files inside an ordinary engine-skipped dir are not counted
    files = dict(prod)
    files.update({"build/core.cs": _cs("Core", "Engine"), "build/tests/EngineCheck.cs": _cs("Core.Tests", "EngineCheck"),
                  "build/test_tool.py": "def tool():\n    return 7\n", "out/test_only.py": "def only():\n    return 8\n"})
    root = _tree("d5_files_nongit", files, git=False)
    with _env(DEEPMAP_EXCLUDE_TESTS=None):
        _v, _r, cov5a = _verdict(root, prod_nodes)
    un5a = cov5a.get("unindexed_dirs") or {}
    check("premise: tests kept, build/ counts 3 files and out/ 1",
          un5a.get("build", {}).get("files") == 3 and un5a.get("out", {}).get("files") == 1, un5a)
    with _env(DEEPMAP_EXCLUDE_TESTS="1"):
        _v, rs5, cov5 = _verdict(root, prod_nodes)
    un5 = cov5.get("unindexed_dirs") or {}
    check("DEEPMAP_EXCLUDE_TESTS=1: build/ counts only its production file (build/tests/ and test_tool.py dropped)",
          un5.get("build", {}).get("files") == 1, un5)
    check("...and out/, holding only a test file (test_only.py), is not listed", "out" not in un5, un5)
    check("...the remaining production file is still named (DEGRADED)", _named(rs5, "build/"), rs5)

    # D6  the default never consults the rule (no lazy sync import, the old path unchanged)
    real = getattr(V, "_test_path_predicate", None)
    if not callable(real):
        check("verdict._test_path_predicate exists (the verdict's handle on THE rule)", False, real)
        return
    calls: list = []

    def counting():
        calls.append(1)
        return real()
    with _patched(V, "_test_path_predicate", counting):
        with _env(DEEPMAP_EXCLUDE_TESTS=None):
            V.build_coverage(root, prod_nodes)
        off = len(calls)
        with _env(DEEPMAP_EXCLUDE_TESTS="1"):
            V.build_coverage(root, prod_nodes)
        on = len(calls) - off
    check("DEEPMAP_EXCLUDE_TESTS unset: build_coverage never asks for the test rule", off == 0, off)
    check("DEEPMAP_EXCLUDE_TESTS=1: it does (once per finding kind at most)", 1 <= on <= 2, on)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    print("=" * 72)
    print("DeepMap: test-rule mirror gates (sync._is_test_path and its fallback copies; no Neo4j)")
    print("=" * 72)
    try:
        with _env(DEEPMAP_EXCLUDE_TESTS=None):
            gate_a_constants()
            gate_b_through_sync()
            gate_c_fallbacks_agree()
            gate_d_verdict()
    finally:
        _rm_tree(_TMP)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} - {_PASS}/{total} gates passed ({time.time() - t0:.1f} s).")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
