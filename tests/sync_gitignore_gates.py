#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for sync's AUTO-GITIGNORE of its regenerated artifact directories.

Field friction (a user's project): `deepmap sync` drops two artifact trees into the USER's project
— `<root>/graphify-out/` (the graphify subprocess's cache: `graph.json` + hundreds of
`cache/ast/v*/*.json`) and `<root>/.deepmap/` (DeepMap's own runtime state) — and never gitignores
them, so the user gets hundreds of untracked files cluttering `git status` (799, in the report).

The fix (`build/sync.py`): a best-effort, never-raises helper `_ensure_artifact_gitignore(dir)` drops a
SELF-IGNORING nested `.gitignore` (body `*`) into each artifact dir so the whole dir — including the
`.gitignore` itself — is invisible to `git status`, with ZERO new entries. It is sentinel-guarded (only
writes when the file is absent or already DeepMap-owned → never clobbers a user/third-party
`.gitignore`), LF-only, and default-ON with a `DEEPMAP_AUTO_GITIGNORE=0` opt-out. These gates lock:

  - the helper: writes the exact self-ignoring body, is idempotent, returns False for a non-dir,
  - NON-CLOBBER: a foreign `.gitignore` is left untouched; a stale DeepMap-owned one is refreshed,
  - LF newlines (no CRLF even on Windows),
  - the default-ON env opt-out truth table,
  - BEHAVIORAL (git): a populated graphify-out/ + .deepmap/ contribute ZERO lines to `git status`,
  - BENCHMARK non-regression: a sync-written `.deepmap/.gitignore` does not trip `assert_pinned_clean`.

Pure + hermetic: throwaway temp dirs / a temp git repo (no Neo4j, no spend). git-dependent checks skip
cleanly where git is absent, exactly like tests/sync_manifest_gates.py.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


# --------------------------------------------------------------------------- #
def gate_helper_basic(S) -> None:
    print("\n-- _ensure_artifact_gitignore: writes the self-ignoring body; idempotent; non-dir -> False --")
    fn = getattr(S, "_ensure_artifact_gitignore", None)
    sentinel = getattr(S, "_ARTIFACT_GI_SENTINEL", None)
    body = getattr(S, "_ARTIFACT_GI_BODY", None)
    check("_ensure_artifact_gitignore exists", callable(fn))
    check("_ARTIFACT_GI_SENTINEL is a comment str", isinstance(sentinel, str) and sentinel.startswith("#"),
          repr(sentinel))
    check("_ARTIFACT_GI_BODY is a str", isinstance(body, str), repr(body))
    if not (callable(fn) and isinstance(sentinel, str) and isinstance(body, str)):
        return

    # The body must be a self-ignoring nested .gitignore: first line = sentinel, and a bare `*` rule.
    lines = [ln for ln in body.splitlines() if ln.strip()]
    check("body first line == sentinel", lines[:1] == [sentinel], repr(lines[:1]))
    check("body contains a bare '*' rule (ignores the whole dir incl. itself)",
          any(ln.strip() == "*" for ln in lines), repr(lines))

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        # Non-dir target -> False, nothing written.
        missing = rd / "nope"
        check("non-existent dir -> False (best-effort)", fn(missing) is False)
        check("non-existent dir -> no .gitignore materialised", not (missing / ".gitignore").exists())
        # A file (not a dir) at the path -> False, untouched.
        afile = rd / "afile"
        afile.write_text("hi", encoding="utf-8")
        check("a FILE at the path -> False (guarded by .is_dir())", fn(afile) is False)

        # Empty dir -> writes the exact body.
        d = rd / ".deepmap"
        d.mkdir()
        check("empty dir -> True", fn(d) is True)
        gi = d / ".gitignore"
        check("empty dir -> .gitignore written", gi.is_file())
        got = gi.read_text(encoding="utf-8")
        check("written content == _ARTIFACT_GI_BODY", got == body, repr(got))

        # Idempotent: a 2nd call is a stable no-op-equivalent (True, content unchanged).
        check("2nd call -> True (idempotent)", fn(d) is True)
        check("2nd call -> content unchanged", gi.read_text(encoding="utf-8") == body)

        # LF only — no CRLF even on Windows (explicit newline='\n').
        raw = gi.read_bytes()
        check("no CRLF in the written .gitignore (LF only)", b"\r" not in raw, repr(raw[:40]))


def gate_non_clobber(S) -> None:
    print("\n-- _ensure_artifact_gitignore: NEVER clobbers a foreign .gitignore; refreshes a stale ours --")
    fn = getattr(S, "_ensure_artifact_gitignore", None)
    sentinel = getattr(S, "_ARTIFACT_GI_SENTINEL", None)
    body = getattr(S, "_ARTIFACT_GI_BODY", None)
    if not (callable(fn) and isinstance(sentinel, str) and isinstance(body, str)):
        check("(prereqs present)", False, "helper/constants missing")
        return

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)

        # A FOREIGN .gitignore (first line is NOT the sentinel) must be left byte-for-byte untouched.
        d1 = rd / "graphify-out"
        d1.mkdir()
        foreign = "# my own rules\n!keep-me.json\n"
        (d1 / ".gitignore").write_text(foreign, encoding="utf-8")
        check("foreign .gitignore -> False (refused)", fn(d1) is False)
        check("foreign .gitignore -> content preserved (no clobber)",
              (d1 / ".gitignore").read_text(encoding="utf-8") == foreign)

        # A STALE DeepMap-owned .gitignore (first line == sentinel, body differs) is refreshed to body.
        d2 = rd / ".deepmap"
        d2.mkdir()
        stale = sentinel + "\n# an older auto-note\n*\n"
        (d2 / ".gitignore").write_text(stale, encoding="utf-8")
        check("stale DeepMap-owned .gitignore -> True (refreshed)", fn(d2) is True)
        check("stale DeepMap-owned .gitignore -> rewritten to current body",
              (d2 / ".gitignore").read_text(encoding="utf-8") == body)


def gate_tracked_files_guard(S) -> None:
    print("\n-- F3c: never `*`-ignore a dir the user TRACKS deliverables in (git ls-files guard) --")
    fn = getattr(S, "_ensure_artifact_gitignore", None)
    if not callable(fn):
        check("(helper present)", False, "helper missing")
        return
    if not shutil.which("git"):
        check("(git on PATH for the tracked-files guard)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        _make_repo(rd)
        # graphify-out/ holds a TRACKED deliverable (a project that commits e.g. graph_enriched.json
        # there) — a `*` .gitignore would silently hide any NEW file added to it. The guard must refuse.
        go = rd / "graphify-out"
        go.mkdir()
        (go / "graph_enriched.json").write_text("{}", encoding="utf-8")
        _git("add", "graphify-out/graph_enriched.json", cwd=rd)
        _git("commit", "-q", "-m", "track deliverable", cwd=rd)
        check("dir with a TRACKED file -> _ensure_artifact_gitignore returns False (no `*` ignore)",
              fn(go) is False)
        check("dir with a TRACKED file -> no .gitignore written (would have hidden new deliverables)",
              not (go / ".gitignore").exists())
        # A sibling artifact dir with NO tracked files is STILL ignored (the normal, common case).
        dm = rd / ".deepmap"
        dm.mkdir()
        (dm / "graph_build.json").write_text("{}", encoding="utf-8")   # untracked
        check("dir with NO tracked files -> still writes the ignore (True)", fn(dm) is True)
        check("dir with NO tracked files -> .gitignore written", (dm / ".gitignore").is_file())


def gate_env_flag(S) -> None:
    print("\n-- _auto_gitignore_enabled: default-ON; opt out with 0/false/no/off --")
    fn = getattr(S, "_auto_gitignore_enabled", None)
    check("_auto_gitignore_enabled exists", callable(fn))
    if not callable(fn):
        return
    saved = os.environ.get("DEEPMAP_AUTO_GITIGNORE")
    try:
        cases_on = [None, "", "1", "true", "yes", "on", "anything-else", " 1 "]
        cases_off = ["0", "false", "no", "off", "FALSE", "Off", " no "]
        for v in cases_on:
            if v is None:
                os.environ.pop("DEEPMAP_AUTO_GITIGNORE", None)
            else:
                os.environ["DEEPMAP_AUTO_GITIGNORE"] = v
            check(f"enabled for {v!r} (default-ON)", fn() is True)
        for v in cases_off:
            os.environ["DEEPMAP_AUTO_GITIGNORE"] = v
            check(f"disabled for {v!r}", fn() is False)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_AUTO_GITIGNORE", None)
        else:
            os.environ["DEEPMAP_AUTO_GITIGNORE"] = saved


def gate_behavioral_git(S) -> None:
    print("\n-- BEHAVIORAL (git): populated graphify-out/ + .deepmap/ contribute ZERO status lines --")
    fn = getattr(S, "_ensure_artifact_gitignore", None)
    if not callable(fn):
        check("(helper present)", False, "helper missing")
        return
    if not shutil.which("git"):
        check("(git on PATH for behavioral checks)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        _make_repo(rd)

        # Simulate a real sync's artifact trees.
        go = rd / "graphify-out"
        (go / "cache" / "ast" / "v0.9.2").mkdir(parents=True)
        (go / "graph.json").write_text("{}", encoding="utf-8")
        (go / ".graphify_root").write_text(str(rd), encoding="utf-8")
        for i in range(6):
            (go / "cache" / "ast" / "v0.9.2" / f"{i:040x}.json").write_text("{}", encoding="utf-8")
        dm = rd / ".deepmap"
        dm.mkdir()
        (dm / "graph_build.json").write_text('{"nodes": 1}', encoding="utf-8")
        (dm / "sync.lock").write_text("", encoding="utf-8")

        # BEFORE: git sees the untracked artifact dirs (the friction).
        before = _git("status", "--porcelain", cwd=rd).stdout
        check("BEFORE fix: git status shows the untracked artifacts (the reported friction)",
              ("graphify-out" in before) and (".deepmap" in before), repr(before[:200]))

        # Apply the fix.
        check("helper wrote graphify-out/.gitignore", fn(go) is True)
        check("helper wrote .deepmap/.gitignore", fn(dm) is True)

        # AFTER: ZERO status lines mention either dir (self-ignored, including the nested .gitignore).
        after = _git("status", "--porcelain", cwd=rd).stdout
        offenders = [ln for ln in after.splitlines()
                     if "graphify-out" in ln or ".deepmap" in ln]
        check("AFTER fix: git status has ZERO graphify-out/.deepmap entries", offenders == [],
              repr(offenders[:5]))
        check("AFTER fix: the nested .gitignore files are themselves self-ignored (not '??')",
              "gitignore" not in after, repr(after[:200]))

        # And they DO appear under --ignored (proves they're ignored, not merely absent).
        ignored = _git("status", "--ignored", "--porcelain", cwd=rd).stdout
        check("--ignored lists graphify-out (confirms it is ignored, not missing)",
              "graphify-out" in ignored, repr(ignored[:200]))


def gate_benchmark_nonregression(S, B) -> None:
    print("\n-- BENCHMARK non-regression: a sync-written .deepmap/.gitignore does not trip assert_pinned_clean --")
    fn = getattr(S, "_ensure_artifact_gitignore", None)
    apc = getattr(B, "assert_pinned_clean", None)
    check("assert_pinned_clean present", callable(apc))
    if not (callable(fn) and callable(apc)):
        return
    if not shutil.which("git"):
        check("(git on PATH for benchmark-compat checks)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        sha = _make_repo(rd)
        # The sync pipeline writes the provenance sidecar AND (now) the nested ignore into .deepmap/.
        dm = rd / ".deepmap"
        dm.mkdir()
        (dm / "graph_build.json").write_text('{"built_at_sha": "%s"}' % sha, encoding="utf-8")
        check("helper wrote .deepmap/.gitignore", fn(dm) is True)
        # assert_pinned_clean must still see a CLEAN tree at the pinned SHA (untracked/ignored sidecars
        # are not tracked changes) -> None. The graph_build.json is now IGNORED rather than merely '??'.
        reason = apc(rd, sha)
        check("assert_pinned_clean(repo, HEAD) -> None (clean; not tripped by the ignore)",
              reason is None, repr(reason))
        # read_graph_build_sha reads the manifest BY PATH -> unaffected by the ignore.
        rgb = getattr(B, "read_graph_build_sha", None)
        if callable(rgb):
            got, src = rgb(rd)
            check("read_graph_build_sha still reads the ignored sidecar by path",
                  got == sha and "manifest" in src.lower(), f"{got} ({src})")


# --------------------------------------------------------------------------- #
# WIRING: prove sync() actually CALLS the helper at both sites (a refactor that drops a call site goes
# RED). Drive the real sync() with a fully-stubbed pipeline + a fake driver (no Neo4j) — mirroring
# tests/switch_guard_gates.py — but keep the REAL _ensure_artifact_gitignore so the .gitignore files
# are actually produced. DEEPMAP_HOME is redirected to a temp dir so port_registry/instance-lock I/O
# never touches the operator's real ~/.deepmap.
# --------------------------------------------------------------------------- #
class _FakeSession:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        if "_DeepMapMeta) RETURN" in cypher:     # _read_owning_root -> no owner (fresh instance)
            return []
        if "count(n)" in cypher:                 # _graph_has_nodes -> empty graph
            return [{"c": 0}]
        return []


class _FakeDriver:
    def session(self):
        return _FakeSession(self)

    def verify_connectivity(self):
        pass

    def close(self):
        pass


def _wire_sync():
    """A freshly-loaded sync module with every Neo4j/extraction collaborator stubbed EXCEPT the auto-
    gitignore helper under test. The lock stub mimics the real lock's `.deepmap/` mkdir side effect and
    the extraction stub mimics graphify creating `graphify-out/`, so both call-site guards are live."""
    S = _load("sync", BUILD)

    def _fake_lock(root):
        (Path(root) / ".deepmap").mkdir(parents=True, exist_ok=True)   # real _acquire_sync_lock does this
        return object()

    def _rebuild(root):
        go = Path(root) / "graphify-out"                               # real graphify writes this tree
        (go / "cache").mkdir(parents=True, exist_ok=True)
        (go / "graph.json").write_text("{}", encoding="utf-8")
        return ([], [], [], "graphify")

    S._acquire_sync_lock = _fake_lock
    S._acquire_instance_lock = lambda *a, **k: object()
    S._release_sync_lock = lambda h: None
    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, nodes: (nodes, [])
    S.push_to_neo4j = lambda *a, **k: (0, 0)
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None,
                                wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda *a, **k: 0
    S._maybe_auto_tune = lambda root: None
    S.write_graph_build_manifest = lambda *a, **k: None
    return S


def gate_sync_wiring() -> None:
    print("\n-- WIRING: sync() drops BOTH nested .gitignore files (and honours the opt-out) --")
    home = Path(tempfile.mkdtemp(prefix="dm_gi_home_"))
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI", "DEEPMAP_AUTO_GITIGNORE")}
    os.environ["DEEPMAP_HOME"] = str(home)
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"
    os.environ.pop("DEEPMAP_AUTO_GITIGNORE", None)
    try:
        # default-ON: a full sync() writes BOTH self-ignoring .gitignore files.
        S = _wire_sync()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with contextlib.redirect_stderr(io.StringIO()):
                ok = S.sync(td, driver=_FakeDriver())
            gi_dm = root / ".deepmap" / ".gitignore"
            gi_go = root / "graphify-out" / ".gitignore"
            check("sync() returns True (stubbed pipeline)", ok is True, str(ok))
            check("sync() wrote .deepmap/.gitignore (call site 1)", gi_dm.is_file())
            check("sync() wrote graphify-out/.gitignore (call site 2)", gi_go.is_file())
            check("both hold the self-ignoring body",
                  gi_dm.is_file() and gi_go.is_file()
                  and gi_dm.read_text(encoding="utf-8") == S._ARTIFACT_GI_BODY
                  and gi_go.read_text(encoding="utf-8") == S._ARTIFACT_GI_BODY)

        # opt-out: DEEPMAP_AUTO_GITIGNORE=0 -> sync() writes NEITHER file (sync still succeeds).
        os.environ["DEEPMAP_AUTO_GITIGNORE"] = "0"
        S = _wire_sync()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with contextlib.redirect_stderr(io.StringIO()):
                ok = S.sync(td, driver=_FakeDriver())
            check("opt-out: sync() still returns True", ok is True, str(ok))
            check("opt-out: NO .deepmap/.gitignore written",
                  not (root / ".deepmap" / ".gitignore").exists())
            check("opt-out: NO graphify-out/.gitignore written",
                  not (root / "graphify-out" / ".gitignore").exists())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — sync auto-gitignore gates (no Neo4j, no spend)")
    print("=" * 64)

    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    S = _load("sync", BUILD)
    B_spec = importlib.util.spec_from_file_location("dm_run_benchmark_gi", BENCH / "run_benchmark.py")
    B = importlib.util.module_from_spec(B_spec)
    B_spec.loader.exec_module(B)

    gate_helper_basic(S)
    gate_non_clobber(S)
    gate_tracked_files_guard(S)
    gate_env_flag(S)
    gate_behavioral_git(S)
    gate_benchmark_nonregression(S, B)
    gate_sync_wiring()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
