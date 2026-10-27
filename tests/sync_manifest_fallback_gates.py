#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the NON-GIT provenance fallback in
`build/sync.py:write_graph_build_manifest` (`_fs_content_hash`).

When `root` is not a git repo, `_git_head_sha` returns None and the manifest used to be skipped
entirely. The fix stamps a deterministic filesystem content-hash instead, so a non-git workspace
still gets `<root>/.deepmap/graph_build.json` — under `built_at_content_hash` + `provenance:
"content-hash"`, leaving `built_at_sha` (git-only) absent so the benchmark's SHA-pin/staleness paths
stay fail-closed.

These gates lock:
  - a POPULATED non-git tree -> a content-hash manifest is written (path returned, sidecar on disk),
  - keys: provenance=="content-hash", a 16-hex built_at_content_hash, NO built_at_sha,
  - determinism: identical trees -> identical hash; a one-byte change -> a different hash; empty -> None,
  - benchmark.read_graph_build_sha still returns (None, "unavailable") (fail-closed preserved),
  - the git path is unchanged: built_at_sha == HEAD + provenance=="git", no content-hash key.

Pure + hermetic: throwaway temp trees (no Neo4j, no spend). git-dependent checks skip when git is
absent, and the non-git checks skip if the temp tree happens to sit inside a parent git repo.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
BENCH = REPO / "benchmark"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _make_repo(td: Path) -> str:
    _git("init", "-q", cwd=td)
    _git("config", "user.email", "t@t.t", cwd=td)
    _git("config", "user.name", "t", cwd=td)
    _git("config", "commit.gpgsign", "false", cwd=td)
    (td / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=td)
    _git("commit", "-q", "-m", "init", cwd=td)
    return _git("rev-parse", "HEAD", cwd=td).stdout.strip().lower()


def gate_non_git_manifest(S, B) -> None:
    print("\n-- populated non-git tree -> content-hash provenance sidecar --")
    fn = getattr(S, "write_graph_build_manifest", None)
    head = getattr(S, "_git_head_sha", None)
    check("write_graph_build_manifest exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        (rd / "a.py").write_text("x = 1\n", encoding="utf-8")
        (rd / "pkg").mkdir()
        (rd / "pkg" / "b.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        if callable(head) and head(rd) is not None:
            check("(temp tree is not inside a git repo)", True, "skipped — temp under a repo")
            return
        out = fn(rd, n_nodes=5, n_edges=6, n_embedded=5, extractor="ast")
        mpath = rd / ".deepmap" / "graph_build.json"
        check("non-git populated tree -> manifest path returned",
              out is not None and Path(out) == mpath, str(out))
        check("sidecar exists on disk", mpath.is_file())
        if not mpath.is_file():
            return
        data = json.loads(mpath.read_text(encoding="utf-8"))
        check("provenance == 'content-hash'", data.get("provenance") == "content-hash",
              str(data.get("provenance")))
        ch = data.get("built_at_content_hash")
        check("built_at_content_hash is a 16-hex digest",
              isinstance(ch, str) and len(ch) == 16 and all(c in "0123456789abcdef" for c in ch),
              str(ch))
        check("built_at_sha absent (no git SHA on a non-git tree)", "built_at_sha" not in data)
        check("records counts + extractor",
              data.get("nodes") == 5 and data.get("calls_edges") == 6
              and data.get("embedded") == 5 and data.get("extractor") == "ast",
              str({k: data.get(k) for k in ("nodes", "calls_edges", "embedded", "extractor")}))
        gsha, src = B.read_graph_build_sha(rd)
        check("benchmark read_graph_build_sha -> (None, unavailable) for a content-hash manifest",
              gsha is None and "unavail" in str(src).lower(), f"{gsha} ({src})")


def gate_determinism(S) -> None:
    print("\n-- content-hash: deterministic + content-sensitive; empty -> None --")
    fn = getattr(S, "_fs_content_hash", None)
    check("_fs_content_hash exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        for rd in (Path(td1), Path(td2)):
            (rd / "a.py").write_text("x = 1\n", encoding="utf-8")
            (rd / "sub").mkdir()
            (rd / "sub" / "b.txt").write_text("hello\n", encoding="utf-8")
        h1, h2 = fn(Path(td1)), fn(Path(td2))
        check("identical trees -> identical hash", h1 is not None and h1 == h2, f"{h1} vs {h2}")
        (Path(td2) / "a.py").write_text("x = 2\n", encoding="utf-8")   # one byte changes
        h3 = fn(Path(td2))
        check("a one-byte content change -> a different hash", h3 is not None and h3 != h1,
              f"{h3} vs {h1}")
    with tempfile.TemporaryDirectory() as td:
        check("empty tree -> None (stay silent, no sidecar)", fn(Path(td)) is None)


def gate_git_path_unchanged(S) -> None:
    print("\n-- git repo path unchanged: built_at_sha == HEAD + provenance 'git' --")
    if not shutil.which("git"):
        check("(git on PATH for git-path checks)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        sha = _make_repo(rd)
        out = S.write_graph_build_manifest(rd, n_nodes=1, n_edges=1, n_embedded=1, extractor="ast")
        check("git repo -> manifest path returned", out is not None)
        data = json.loads((rd / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("built_at_sha == HEAD", data.get("built_at_sha") == sha,
              f"{data.get('built_at_sha')} vs {sha}")
        check("provenance == 'git'", data.get("provenance") == "git", str(data.get("provenance")))
        check("no content-hash key on the git path", "built_at_content_hash" not in data)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — non-git provenance content-hash fallback gates (no Neo4j)")
    print("=" * 64)
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    S = _load("sync", BUILD)
    B_spec = importlib.util.spec_from_file_location("dm_run_benchmark_smf", BENCH / "run_benchmark.py")
    B = importlib.util.module_from_spec(B_spec)
    B_spec.loader.exec_module(B)

    gate_non_git_manifest(S, B)
    gate_determinism(S)
    gate_git_path_unchanged(S)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
