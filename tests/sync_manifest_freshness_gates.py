#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: the graph-build manifest's freshness contract (ROUND-2 D4 + review findings).

Round 1 captured the SHA at extraction start, but on a dirty tree it wrote only built_at_base_sha +
built_at_dirty and NO built_at_sha — which the notify hook and doctor/status read, so a dirty build
(the normal case right after an edit + /deep-map:refresh) silently switched staleness detection off.
Its dirty check also counted DeepMap's OWN untracked .mcp.json (written by setup/heal/mcp-start), so
after setup built_at_sha was never recorded again, even on a clean tree.

D4: ALWAYS record built_at_sha (HEAD at extraction start) + `dirty` + `dirty_paths_sample`; the dirty
check ignores DeepMap's own artifacts. "Dirty" means an uncommitted change that can change the graph:
source the extractors read (or a .gitignore, which feeds the vendor filter) — not notes, editor
settings or the harness's own sidecars, and not vendored code the graph drops anyway. The benchmark's
SHA pin must still FAIL CLOSED on a graph built from a dirty tree.

  F1   clean tree + untracked .mcp.json / .deepmap-root / .deepmap/ -> built_at_sha, dirty=false.
  F2   modified tracked source -> built_at_sha (still!) + dirty=true + the path in the sample.
  F3   untracked/modified NON-source (notes, README, .oss-kit/, .vscode/) -> dirty=false.
  F4   an untracked source file in a new directory -> dirty=true (found inside the new dir).
  F5   untracked vendored source (node_modules/) -> dirty=false (the graph drops it anyway).
  F6   a modified .gitignore -> dirty=true (it changes what the vendor filter drops).
  F7   a staged rename is parsed correctly (-z rename records carry two paths).
  F8   a sub-project root only counts changes under itself; sample paths are relative to it.
  F9   a non-ASCII file name round-trips into the sample.
  F10  the sample is bounded; dirty_paths_count carries the total.
  F11  the real sync() records the state captured at extraction START (review repro: .mcp.json).
  F12  the extraction facts W5 added (self-call rebinding, rescue drops, engine error) are persisted.
  F13  benchmark.assert_graph_at_pinned_sha fails CLOSED on a dirty (or unknown-dirty) manifest and
       still passes a clean one and a pre-D4 manifest.
  F14  git output is read locale-independently: under a non-UTF-8 ANSI code page with
       core.quotePath=false, a tracked non-ASCII file still blocks the `*` auto-gitignore, and the dirty
       check still names a non-ASCII file.
  F15  (round 3) W5's new extraction facts are persisted: the graphify engine version, the engine-skipped
       dirs NOT re-included because that version is unsupported (graphify_rescue_blocked), and whether
       imports bound by the engine-resolved target key (+ both counts; absent on an ast fallback);
       malformed numbers never break the manifest; a real sync() pass carries them from _rebuild_code.

Hermetic: throwaway `git init` repos, temp DEEPMAP_HOME, stubbed pipeline + fake driver. Skipped
(not failed) when git is absent.
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

_PASS = _FAIL = 0
_GIT = shutil.which("git")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sync():
    return _load(BUILD / "sync.py", "dm_sync_fresh")


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


def _git(repo, *args):
    return subprocess.run([_GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def _repo(td: Path, name="proj") -> Path:
    repo = td / name
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "billing.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    (repo / "tools.py").write_text("def tool():\n    return 2\n", encoding="utf-8")
    (repo / "README.md").write_text("# proj\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c0")
    return repo


def _head(repo) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip().lower()


def _manifest_now(S, root) -> dict:
    """write_graph_build_manifest with no source_state: the provenance is computed at write time."""
    out = S.write_graph_build_manifest(root, n_nodes=2, n_edges=1, n_embedded=2, extractor="graphify")
    if out is None:
        return {}
    return json.loads(Path(out).read_text(encoding="utf-8"))


def _write(p: Path, text="def x():\n    pass\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def gate_f1_to_f10(td: Path) -> None:
    print("\n-- F1-F10: what counts as 'dirty' (and what never does) --")
    S = _sync()

    repo = _repo(td, "f1")
    _write(repo / ".mcp.json", '{"mcpServers": {"deepmap-search": {"type": "http"}}}')
    _write(repo / ".deepmap-root", "")
    _write(repo / ".deepmap" / "sync.lock", "{}")
    d = _manifest_now(S, repo)
    check("F1 clean tree + DeepMap's own untracked files -> built_at_sha == HEAD",
          d.get("built_at_sha") == _head(repo), d)
    check("F1 ...dirty == false and built_at_dirty == false",
          d.get("dirty") is False and d.get("built_at_dirty") is False, d)
    check("F1 ...an empty dirty_paths_sample", d.get("dirty_paths_sample") == [], d.get("dirty_paths_sample"))

    repo = _repo(td, "f2")
    _write(repo / "tools.py", "def tool():\n    return 3\n")
    d = _manifest_now(S, repo)
    check("F2 modified tracked source -> built_at_sha is STILL recorded (== HEAD at extraction)",
          d.get("built_at_sha") == _head(repo), d)
    check("F2 ...dirty == true (+ the built_at_dirty alias)",
          d.get("dirty") is True and d.get("built_at_dirty") is True, d)
    check("F2 ...the changed path is in the sample", "tools.py" in (d.get("dirty_paths_sample") or []), d)

    repo = _repo(td, "f3")
    _write(repo / "notes.txt", "todo")
    _write(repo / "README.md", "# proj\nchanged\n")
    _write(repo / ".oss-kit" / "state.json", "{}")
    _write(repo / ".vscode" / "settings.json", "{}")
    d = _manifest_now(S, repo)
    check("F3 untracked/modified NON-source files do not make the build dirty",
          d.get("dirty") is False and d.get("built_at_sha") == _head(repo), d)

    repo = _repo(td, "f4")
    _write(repo / "newpkg" / "deep" / "mod.py")
    d = _manifest_now(S, repo)
    check("F4 an untracked source file inside a NEW directory -> dirty",
          d.get("dirty") is True and "newpkg/deep/mod.py" in (d.get("dirty_paths_sample") or []), d)

    repo = _repo(td, "f5")
    _write(repo / "node_modules" / "lib" / "index.js", "module.exports = 1;\n")
    _write(repo / "public" / "js" / "app.min.js", "var a=1;\n")
    d = _manifest_now(S, repo)
    check("F5 untracked vendored/minified source -> not dirty (the graph drops it)",
          d.get("dirty") is False, d)

    repo = _repo(td, "f6")
    _write(repo / ".gitignore", "*.log\nlegacy/\n")
    d = _manifest_now(S, repo)
    check("F6 a modified .gitignore -> dirty (it feeds the vendor filter)",
          d.get("dirty") is True and ".gitignore" in (d.get("dirty_paths_sample") or []), d)

    repo = _repo(td, "f7")
    _git(repo, "mv", "tools.py", "renamed.py")
    d = _manifest_now(S, repo)
    sample = d.get("dirty_paths_sample") or []
    check("F7 a staged rename -> dirty, with the NEW path in the sample", d.get("dirty") is True
          and "renamed.py" in sample, d)
    check("F7 ...and no mangled path (the -z rename record's second path parsed as a path)",
          set(sample) <= {"renamed.py", "tools.py"}, sample)

    repo = _repo(td, "f8")
    _write(repo / "tools.py", "def tool():\n    return 9\n")          # outside the sub-project
    d = _manifest_now(S, repo / "app")
    check("F8 a change OUTSIDE a sub-project root does not dirty that sub-project's manifest",
          d.get("dirty") is False and d.get("built_at_sha") == _head(repo), d)
    _write(repo / "app" / "billing.py", "def charge():\n    return 5\n")
    d = _manifest_now(S, repo / "app")
    check("F8 ...a change inside it does, with the path relative to the sub-project root",
          d.get("dirty") is True and "billing.py" in (d.get("dirty_paths_sample") or []), d)

    repo = _repo(td, "f9")
    name = "módulo_א.py"
    _write(repo / name)
    d = _manifest_now(S, repo)
    check("F9 a non-ASCII untracked source file is reported by its real name",
          d.get("dirty") is True and name in (d.get("dirty_paths_sample") or []), d.get("dirty_paths_sample"))

    repo = _repo(td, "f10")
    for i in range(30):
        _write(repo / "gen" / f"g{i:02d}.py")
    d = _manifest_now(S, repo)
    check("F10 the sample is bounded", d.get("dirty") is True and 0 < len(d.get("dirty_paths_sample") or [])
          <= 10, len(d.get("dirty_paths_sample") or []))
    check("F10 ...and dirty_paths_count carries the total", d.get("dirty_paths_count") == 30,
          d.get("dirty_paths_count"))


class _Result(list):
    def single(self):
        return self[0] if self else None


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **k):
        return _Result()


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        pass

    def close(self):
        pass


def _wire(S, on_embed=None):
    node = {"key": "tools.py::tool:1", "label": "Function", "name": "tool", "source_file": "tools.py",
            "start_line": 1, "body_summary": "tool"}
    S._rebuild_code = lambda root: ([dict(node)], [], [], "graphify")
    S._apply_recognizers = lambda root, nodes: (nodes, [])
    S.push_to_neo4j = lambda d, n, c, i, typed_edges=None: (len(n), 0)
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True)

    def _emb(d):
        if on_embed:
            on_embed()
        return 1

    S.embeddings = SimpleNamespace(generate_embeddings=_emb)
    S._count_embedded_nodes = lambda d, fallback=0: 1
    S._maybe_auto_tune = lambda root: None


def gate_f11_real_sync(td: Path) -> None:
    print("\n-- F11: the real sync() records the state captured at extraction START --")
    S = _sync()
    _wire(S)
    repo = _repo(td, "f11")
    _write(repo / ".mcp.json", '{"mcpServers": {}}')          # what setup / heal / mcp-start leave behind
    with _env(NEO4J_URI="bolt://localhost:7687"), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        ok = S.sync(repo, driver=_Drv())
    d = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    check("sync succeeded", ok is True)
    check("review repro: a clean tree + DeepMap's untracked .mcp.json -> built_at_sha + dirty=false",
          d.get("built_at_sha") == _head(repo) and d.get("dirty") is False, d)

    S = _sync()
    repo = _repo(td, "f11b")
    _wire(S, on_embed=lambda: _write(repo / "late.py"))       # an edit lands AFTER extraction
    with _env(NEO4J_URI="bolt://localhost:7687"), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        S.sync(repo, driver=_Drv())
    d = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    check("an edit made after extraction does not mark THIS build dirty (it read the clean tree)",
          d.get("dirty") is False and d.get("built_at_sha") == _head(repo), d)

    S = _sync()
    repo = _repo(td, "f11c")
    _write(repo / "tools.py", "def tool():\n    return 7\n")
    _wire(S)
    with _env(NEO4J_URI="bolt://localhost:7687"), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        S.sync(repo, driver=_Drv())
    d = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    check("a build from uncommitted source: built_at_sha == HEAD + dirty=true (staleness stays on)",
          d.get("built_at_sha") == _head(repo) and d.get("dirty") is True
          and "tools.py" in (d.get("dirty_paths_sample") or []), d)


def gate_f12_extraction_fields(td: Path) -> None:
    print("\n-- F12: the manifest persists the extraction facts (W5) --")
    S = _sync()
    repo = _repo(td, "f12")
    info = {"requested": "auto", "resolved": "graphify", "fallback_reason": None,
            "self_call_rebind": True, "self_calls_rebound": 73, "rescue_dropped": 4,
            "engine_error": None, "rescued_dirs": ["build"]}
    out = S.write_graph_build_manifest(repo, n_nodes=1, n_edges=0, n_embedded=1, extractor="graphify",
                                       extraction=info)
    d = json.loads(Path(out).read_text(encoding="utf-8")) if out else {}
    check("self-call rebinding recorded (on + count)",
          d.get("graphify_self_call_rebind") is True and d.get("graphify_self_calls_rebound") == 73, d)
    check("files dropped from re-included dirs recorded", d.get("graphify_rescue_dropped") == 4, d)
    info2 = {"requested": "graphify", "resolved": "graphify", "engine_error": "graphify exited 1: boom",
             "self_call_rebind": False}
    out = S.write_graph_build_manifest(repo, n_nodes=0, n_edges=0, n_embedded=0, extractor="graphify",
                                       extraction=info2)
    d = json.loads(Path(out).read_text(encoding="utf-8")) if out else {}
    check("a forced-graphify engine error is recorded", "boom" in str(d.get("extractor_engine_error")), d)
    check("self-call rebinding switched OFF is recorded as false (not dropped)",
          d.get("graphify_self_call_rebind") is False, d)


def gate_f15_round3_extraction_fields(td: Path) -> None:
    print("\n-- F15: the manifest persists W5's round-3 extraction facts (engine version, blocked rescue, "
          "resolved imports) --")
    S = _sync()
    repo = _repo(td, "f15")

    def _write_info(info, extractor="graphify"):
        out = S.write_graph_build_manifest(repo, n_nodes=3, n_edges=1, n_embedded=3, extractor=extractor,
                                           extraction=info)
        return json.loads(Path(out).read_text(encoding="utf-8")) if out else {}

    d = _write_info({"requested": "auto", "resolved": "graphify", "graphify_version": "0.8.39",
                     "rescued_dirs": ["build", "adapter/out"], "resolved_imports": True,
                     "imports_resolved": 240, "imports_by_name": 12})
    check("the graphify engine version is recorded", d.get("graphify_version") == "0.8.39", d)
    check("the re-included (rescued) dirs are recorded", d.get("graphify_rescued_dirs") == ["build", "adapter/out"], d)
    check("resolved-import binding: on + both counts",
          d.get("graphify_resolved_imports") is True and d.get("graphify_imports_resolved") == 240
          and d.get("graphify_imports_by_name") == 12, d)
    check("no rescue was blocked -> no graphify_rescue_blocked key", "graphify_rescue_blocked" not in d, d)

    d = _write_info({"requested": "auto", "resolved": "graphify", "graphify_version": "0.9.68",
                     "rescue_blocked": ["build"], "resolved_imports": False, "imports_resolved": 0,
                     "imports_by_name": 3660})
    check("a rescue blocked by an unsupported graphify version is recorded (+ that version)",
          d.get("graphify_rescue_blocked") == ["build"] and d.get("graphify_version") == "0.9.68", d)
    check("resolved-import binding switched OFF is recorded as false with its counts (not dropped)",
          d.get("graphify_resolved_imports") is False and d.get("graphify_imports_resolved") == 0
          and d.get("graphify_imports_by_name") == 3660, d)

    d = _write_info({"requested": "auto", "resolved": "ast", "fallback_reason": "graphify exited 1",
                     "graphify_version": "0.8.39"}, extractor="ast")
    check("an ast fallback records no resolved-import fields (graphify's graph was not kept)",
          not any(k in d for k in ("graphify_resolved_imports", "graphify_imports_resolved",
                                   "graphify_imports_by_name")), d)

    d = _write_info({"requested": "auto", "resolved": "graphify", "resolved_imports": True,
                     "imports_resolved": "n/a", "imports_by_name": None, "failed_files": "x",
                     "self_calls_rebound": object(), "graphify_version": 8.39})
    check("malformed counts never break the manifest (never raises; bad numbers are left out)",
          d.get("graphify_resolved_imports") is True and d.get("graphify_imports_by_name") == 0
          and "graphify_failed_files" not in d and "graphify_self_calls_rebound" not in d
          and d.get("graphify_version") == "8.39", d)

    # end to end: _rebuild_code's .info reaches the manifest through the real sync() pass
    S = _sync()
    _wire(S)
    node = {"key": "tools.py::tool:1", "label": "Function", "name": "tool", "source_file": "tools.py",
            "start_line": 1, "body_summary": "tool"}
    info = {"requested": "auto", "resolved": "graphify", "graphify_version": "0.8.39",
            "rescue_blocked": ["out"], "resolved_imports": True, "imports_resolved": 7, "imports_by_name": 1}
    S._rebuild_code = lambda root: S._Extraction([dict(node)], [], [], "graphify", dict(info))
    repo = _repo(td, "f15b")
    with _env(NEO4J_URI="bolt://localhost:7687"), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        ok = S.sync(repo, driver=_Drv())
    d = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    check("a real sync() pass persists them from _rebuild_code's info",
          ok is True and d.get("graphify_version") == "0.8.39" and d.get("graphify_rescue_blocked") == ["out"]
          and d.get("graphify_resolved_imports") is True and d.get("graphify_imports_resolved") == 7
          and d.get("graphify_imports_by_name") == 1, d)


def gate_f13_benchmark(td: Path) -> None:
    print("\n-- F13: the benchmark SHA pin fails CLOSED on a dirty build --")
    B = _load(REPO / "benchmark" / "run_benchmark.py", "dm_bench_fresh")
    rd = td / "f13"
    (rd / ".deepmap").mkdir(parents=True)
    sha = "a" * 40

    def _m(**fields):
        (rd / ".deepmap" / "graph_build.json").write_text(json.dumps(fields), encoding="utf-8")
        with _env(DEEPMAP_GRAPH_SHA=None):
            return B.assert_graph_at_pinned_sha(rd, sha)

    r = _m(built_at_sha=sha, dirty=True, dirty_paths_sample=["src/x.py"])
    check("built_at_sha == pinned but dirty=true -> FAIL CLOSED", isinstance(r, str) and "FAIL CLOSED" in r, r)
    check("...and the reason names the uncommitted change", isinstance(r, str) and "src/x.py" in r, r)
    r = _m(built_at_sha=sha, built_at_dirty=True)
    check("the built_at_dirty alias alone also fails closed", isinstance(r, str) and "FAIL CLOSED" in r, r)
    r = _m(built_at_sha=sha, dirty=None)
    check("dirty unknown (null) -> FAIL CLOSED (unverifiable)", isinstance(r, str) and "FAIL CLOSED" in r, r)
    check("a clean D4 manifest passes", _m(built_at_sha=sha, dirty=False, dirty_paths_sample=[]) is None)
    check("a pre-D4 manifest (no dirty field) still passes", _m(built_at_sha=sha) is None)
    check("a mismatched SHA still fails closed", _m(built_at_sha="b" * 40, dirty=False) is not None)


_CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import importlib.util
spec = importlib.util.spec_from_file_location("dm_sync_child", sys.argv[1] + "/sync.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
from pathlib import Path
repo = Path(sys.argv[2])
print(json.dumps({"tracked": S._dir_has_tracked_files(repo / "graphify-out"),
                  "changes": list(S._git_worktree_changes(repo)),
                  "enc": __import__("locale").getpreferredencoding(False)}))
'''


def gate_f14_locale(td: Path) -> None:
    print("\n-- F14: git output is read locale-independently (non-UTF-8 ANSI code page, quotePath=false) --")
    repo = _repo(td, "f14")
    _git(repo, "config", "core.quotePath", "false")
    name = "אב.json"                  # UTF-8 D7 90 D7 91: 0x90 is undefined in cp1252 AND cp1255
    _write(repo / "graphify-out" / name, "{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tracked deliverable under graphify-out")
    src = "א_mod.py"
    _write(repo / src)
    script = td / "f14_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8=0", str(script), str(BUILD), str(repo)],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        check("the locale child ran", False, (p.stdout + p.stderr)[-600:])
        return
    check(f"graphify-out holding a tracked non-ASCII file is seen as TRACKED (locale {out.get('enc')}) — "
          f"the `*` auto-gitignore must not cover it", out.get("tracked") is True, out)
    dirty, sample, _count = out.get("changes") or [None, [], 0]
    check("the dirty check reports a non-ASCII untracked source file by its real name",
          dirty is True and src in (sample or []), out)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — graph-build manifest freshness gates (D4; no Neo4j)")
    print("=" * 68)
    if not _GIT:
        print("  (git not installed — every section needs a git repo; skipped)")
        print("\nRESULT: GREEN — 0/0 gates passed.")
        return 0
    td = Path(tempfile.mkdtemp(prefix="dm_fresh_"))
    try:
        with _env(DEEPMAP_HOME=str(td / "home"), DEEPMAP_EXTRACTOR=None, DEEPMAP_LEARNING=None,
                  DEEPMAP_AUTO_TUNE=None, DEEPMAP_AUTO_GITIGNORE="0", DEEPMAP_INCLUDE_VENDOR=None):
            for gate in (gate_f1_to_f10, gate_f11_real_sync, gate_f12_extraction_fields,
                         gate_f15_round3_extraction_fields, gate_f13_benchmark, gate_f14_locale):
                try:
                    gate(td)
                except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate
                    check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
