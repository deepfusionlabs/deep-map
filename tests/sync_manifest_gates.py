#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the sync-side graph-build manifest (FOCUS 4a automation half).

`build/sync.py` writes a provenance sidecar `<repo>/.deepmap/graph_build.json` after a successful
rebuild, recording the git SHA the graph was built at (+ node/edge/embed counts + extractor). The
benchmark's `assert_graph_at_pinned_sha` reads it to verify the ON-arm graph and the OFF-arm grep are
the SAME SHA — automatically, without an operator honor-declaration. These gates lock:

  - `_git_head_sha`: a 40-hex SHA in a git repo; None (best-effort) outside one,
  - `write_graph_build_manifest`: writes the sidecar in a repo, returns None + writes nothing for an
    EMPTY non-git tree (a POPULATED non-git tree gets a content-hash sidecar — see
    tests/sync_manifest_fallback_gates.py), and NEVER raises (provenance is not a build precondition),
  - the PRODUCER->CONSUMER round-trip: what sync writes is exactly what the benchmark reads back.

Pure + hermetic: a throwaway temp git repo (no Neo4j, no spend). git-dependent checks skip cleanly
where git is absent (CI without git), exactly like run_all's `bash -n` / `claude validate` steps.
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
    """Init a throwaway git repo with one commit; return its HEAD SHA (lowercased)."""
    _git("init", "-q", cwd=td)
    _git("config", "user.email", "t@t.t", cwd=td)
    _git("config", "user.name", "t", cwd=td)
    _git("config", "commit.gpgsign", "false", cwd=td)
    (td / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=td)
    _git("commit", "-q", "-m", "init", cwd=td)
    return _git("rev-parse", "HEAD", cwd=td).stdout.strip().lower()


def gate_git_head_sha(S) -> None:
    print("\n-- sync._git_head_sha: 40-hex in a repo; None outside one --")
    fn = getattr(S, "_git_head_sha", None)
    check("_git_head_sha exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        # Non-repo: best-effort None (never raises).
        check("non-repo dir -> None (best-effort, no raise)", fn(rd) is None)
    if not shutil.which("git"):
        check("(git on PATH for repo checks)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        sha = _make_repo(rd)
        got = fn(rd)
        check("git repo -> the 40-hex HEAD SHA (lowercased)",
              got == sha and len(got) == 40 and all(c in "0123456789abcdef" for c in got), f"{got} vs {sha}")


def gate_write_manifest(S, B) -> None:
    print("\n-- sync.write_graph_build_manifest: sidecar in a repo; None + no write outside --")
    fn = getattr(S, "write_graph_build_manifest", None)
    check("write_graph_build_manifest exists", callable(fn))
    if not callable(fn):
        return
    # EMPTY non-git tree: nothing to hash -> returns None and writes NOTHING (best-effort, never a
    # blocker). A POPULATED non-git tree instead gets a content-hash sidecar (sync_manifest_fallback_gates).
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        out = fn(rd, n_nodes=1, n_edges=2, n_embedded=3, extractor="ast")
        check("empty non-repo -> None returned", out is None)
        check("empty non-repo -> no .deepmap sidecar written",
              not (rd / ".deepmap" / "graph_build.json").exists())

    if not shutil.which("git"):
        check("(git on PATH for manifest write checks)", True, "skipped — git absent")
        return

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        sha = _make_repo(rd)
        out = fn(rd, n_nodes=448, n_edges=2229, n_embedded=448, extractor="ast")
        manifest_path = rd / ".deepmap" / "graph_build.json"
        check("repo -> returns the manifest path", out is not None and Path(out) == manifest_path, str(out))
        check("repo -> sidecar exists", manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        check("built_at_sha == HEAD", data.get("built_at_sha") == sha, f"{data.get('built_at_sha')} vs {sha}")
        check("records node/edge/embed counts",
              data.get("nodes") == 448 and data.get("calls_edges") == 2229 and data.get("embedded") == 448,
              str({k: data.get(k) for k in ("nodes", "calls_edges", "embedded")}))
        check("records the extractor", data.get("extractor") == "ast", str(data.get("extractor")))
        check("records a UTC build timestamp", isinstance(data.get("built_at_utc"), str)
              and data["built_at_utc"].endswith("Z"), str(data.get("built_at_utc")))

        # THE INTEGRATION CONTRACT: what sync writes is exactly what the benchmark reads back.
        gsha, src = B.read_graph_build_sha(rd)
        check("benchmark reads back the SAME SHA sync wrote (producer->consumer round-trip)",
              gsha == sha and "manifest" in src.lower(), f"{gsha} ({src}) vs {sha}")
        check("and assert_graph_at_pinned_sha PASSES when pinned == built SHA",
              B.assert_graph_at_pinned_sha(rd, sha) is None)
        check("and FAILS CLOSED when pinned != built SHA",
              B.assert_graph_at_pinned_sha(rd, "c" * 40) is not None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — sync graph-build manifest gates (no Neo4j, no spend)")
    print("=" * 64)

    # Keep the throwaway repos from inheriting the caller's git identity/hooks.
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    S = _load("sync", BUILD)
    B_spec = importlib.util.spec_from_file_location("dm_run_benchmark_sm", BENCH / "run_benchmark.py")
    B = importlib.util.module_from_spec(B_spec)
    B_spec.loader.exec_module(B)

    gate_git_head_sha(S)
    gate_write_manifest(S, B)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
