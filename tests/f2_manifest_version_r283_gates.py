#!/usr/bin/env python3
"""F2 r283 — the graph-build manifest records the DeepMap version that BUILT the graph (`deepmap_version`).

Fix-all round 2, follow-up of O11 (round 1, ISS-benchmark:005). The benchmark's resume check (run_benchmark.resume_state)
reuses a capture only when it measured THIS DeepMap's graph, but <clone>/.deepmap/graph_build.json — the manifest sync
writes — recorded the source SHA, the engine and the engine's version, not which DeepMap built the graph: the
capture-time machine_spec.deepmap_version stood in for it. sync now stamps `deepmap_version`, the "version" in the plugin
root's .claude-plugin/plugin.json of the code that ran the sync — read the way port_registry.code_identity reads it for
a server's owner sidecar, ONCE when sync.py is imported (a checkout updated in place mid-sync must not stamp its newer
version on a graph the older code built). Additive only: omitted when unreadable, never raises; a manifest an older
build wrote (no field) keeps working — sync's own readers never needed it — and a 0-node run that keeps the graph keeps
its manifest, i.e. the version of the build that made that graph.

Hermetic on every OS: temp trees + a temp DEEPMAP_HOME; the version cases load sync.py from temp copies of build/ next
to a temp plugin.json (the tree's own plugin.json is only read); the full sync() pass runs with a fake driver (NEO4J_URI
names a closed loopback port nothing dials). Never touches ~/.deepmap, no Neo4j, no network.
Usage: python tests/f2_manifest_version_r283_gates.py [--root <tree>]   (--root: load build/ from another tree — the
       RED-first run against the pre-fix snapshot)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
FIELD = "deepmap_version"


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/f2_manifest_version_r283_gates.py [--root <tree holding build/>] (got {tree!r})",
              file=sys.stderr)
        raise SystemExit(2)
    return Path(tree).resolve()


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


def _rmtree(path: Path) -> None:
    """Delete the temp dir THIS gate created (its exact path) — git's read-only object files included (Windows
    refuses to unlink a read-only file, which a plain ignore_errors rmtree would silently leave behind)."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            pass
    if path and Path(path).exists():
        if sys.version_info >= (3, 12):                 # `onerror` is deprecated there (same 3-argument shape)
            shutil.rmtree(str(path), onexc=onerr)
        else:
            shutil.rmtree(str(path), onerror=onerr)


def _load_sync(build_dir: Path):
    """A FRESH sync module executed from `build_dir` (the version is read at import, so each case needs its own)."""
    spec = importlib.util.spec_from_file_location(f"_f2_sync_{time.time_ns()}", build_dir / "sync.py")
    mod = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def _copy_build(tree: Path, dest: Path) -> Path:
    """dest/build/ holding a copy of the tree's build/*.py (the code only — no caches, no engine output)."""
    b = dest / "build"
    b.mkdir(parents=True)
    for p in sorted((tree / "build").glob("*.py")):
        shutil.copy2(p, b / p.name)
    return b


def _plugin_version(tree: Path):
    data = json.loads((tree / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    return str(data.get("version")).strip()


def _manifest(root: Path) -> dict:
    return json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))


def _tree_with_file(base: Path, name: str) -> Path:
    rd = base / name
    rd.mkdir()
    (rd / "a.py").write_text("x = 1\n", encoding="utf-8")
    return rd


def _write(S, rd: Path):
    with contextlib.redirect_stderr(io.StringIO()):
        return S.write_graph_build_manifest(rd, n_nodes=3, n_edges=1, n_embedded=3, extractor="graphify")


# =====================================================================================================
# A — every manifest a sync writes names the DeepMap version that ran it
# =====================================================================================================
def gate_records_version(S, tree: Path, base: Path) -> None:
    print("\n-- write_graph_build_manifest records the DeepMap version (plugin.json) of the code that ran it --")
    want = _plugin_version(tree)
    check("setup: the tree under test has a readable plugin.json version", bool(want), want)
    rd = _tree_with_file(base, "nongit")
    out = _write(S, rd)
    data = _manifest(rd) if out else {}
    check(f"a content-hash manifest (non-git tree) carries {FIELD} == plugin.json's version",
          data.get(FIELD) == want, {k: data.get(k) for k in (FIELD, "provenance")})
    check("additive: the existing provenance keys are all still there",
          all(k in data for k in ("built_at_utc", "nodes", "calls_edges", "embedded", "extractor", "provenance",
                                  "built_at_content_hash", "note")) and data.get("nodes") == 3, sorted(data))
    if not shutil.which("git"):
        check("(git on PATH for the git-manifest case)", True, "skipped — git absent")
        return
    gd = _tree_with_file(base, "gitrepo")
    for argv in (["init", "-q"], ["config", "user.email", "t@t.t"], ["config", "user.name", "t"],
                 ["config", "commit.gpgsign", "false"], ["add", "-A"], ["commit", "-q", "-m", "i"]):
        subprocess.run(["git", *argv], cwd=str(gd), capture_output=True, timeout=60)
    out = _write(S, gd)
    data = _manifest(gd) if out else {}
    check(f"a git manifest (the benchmark clones' kind) carries {FIELD} beside built_at_sha",
          data.get(FIELD) == want and len(str(data.get("built_at_sha") or "")) == 40,
          {k: data.get(k) for k in (FIELD, "built_at_sha", "provenance")})


# =====================================================================================================
# B — an unreadable version leaves the field out; the manifest is still written, nothing raises
# =====================================================================================================
def gate_unreadable(tree: Path, base: Path) -> None:
    print("\n-- no readable version: the field is omitted, the manifest still writes, nothing raises --")
    t = base / "unreadable"
    build = _copy_build(tree, t)
    cases = [("no .claude-plugin/plugin.json at all", None), ("invalid JSON", "{not json"),
             ("a JSON list", "[1, 2]"), ("version null", '{"version": null}'),
             ("a blank version", '{"version": "   "}'), ("no version key", '{"name": "deep-map"}')]
    for i, (label, body) in enumerate(cases):
        pj = t / ".claude-plugin" / "plugin.json"
        if body is not None:
            pj.parent.mkdir(parents=True, exist_ok=True)
            pj.write_text(body, encoding="utf-8")
        err = None
        try:
            S = _load_sync(build)
            rd = _tree_with_file(base, f"unreadable_{i}")
            out = _write(S, rd)
            data = _manifest(rd) if out else {}
        except Exception as exc:  # noqa: BLE001 — a raise is exactly what the gate looks for
            err, out, data = exc, None, {}
        check(f"{label}: the manifest is written, without {FIELD} (never a raise)",
              err is None and out is not None and FIELD not in data and data.get("nodes") == 3,
              (err, out, data.get(FIELD)))


# =====================================================================================================
# C — the version is the one of the code that RAN (read once, at import), not the file's at write time
# =====================================================================================================
def gate_import_time(tree: Path, base: Path) -> None:
    print("\n-- the recorded version is the code's own, read once at import (not plugin.json at write time) --")
    t = base / "captured"
    build = _copy_build(tree, t)
    pj = t / ".claude-plugin" / "plugin.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"name": "deep-map", "version": "9.9.9"}), encoding="utf-8-sig")   # BOM-tolerant
    S1 = _load_sync(build)
    pj.write_text(json.dumps({"name": "deep-map", "version": "10.0.0"}), encoding="utf-8")   # updated in place
    rd = _tree_with_file(base, "captured_old")
    out = _write(S1, rd)
    got = _manifest(rd).get(FIELD) if out else None
    check("a checkout updated in place mid-sync: the manifest names the version that ran (9.9.9), not 10.0.0",
          got == "9.9.9", got)
    S2 = _load_sync(build)
    rd2 = _tree_with_file(base, "captured_new")
    out = _write(S2, rd2)
    got2 = _manifest(rd2).get(FIELD) if out else None
    check("... and the next process (a fresh import) records the new version, 10.0.0", got2 == "10.0.0", got2)


# =====================================================================================================
# D — a real sync() pass stamps it; an older build's manifest keeps working; a kept graph keeps its version
# =====================================================================================================
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Sess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.stmts.append(cypher)
        return _Res()

    def begin_transaction(self):
        return self

    def commit(self):
        return None


class _Drv:
    def __init__(self):
        self.stmts: list = []

    def session(self, *a, **k):
        return _Sess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _wire(S, nodes, *, holds=True):
    """The sync pass with its extraction / push / index / embed seams stubbed (the manifest writer stays real)."""
    def _rebuild(root):
        return S._Extraction([dict(n) for n in nodes], [], [], "graphify",
                             {"requested": "auto", "resolved": "graphify"})
    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = lambda driver, n, c, i, typed_edges=None: (len(n), 0)
    S._prune_blast_radius = lambda d, live: (0, len(nodes))
    S._cleanup_stale = lambda d, live: None
    S._graph_has_nodes = lambda d: holds
    S._graph_node_count = lambda d: len(nodes) or 7
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(nodes)
    S._maybe_auto_tune = lambda root: None


def _proj(base: Path, name: str, manifest: dict) -> Path:
    root = base / name
    (root / ".deepmap").mkdir(parents=True)
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / ".deepmap" / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def gate_sync_pass(tree: Path, base: Path) -> None:
    print("\n-- a full sync() pass stamps the version; an older build's manifest keeps working --")
    want = _plugin_version(tree)
    node = {"key": "a.py::f:1", "label": "Function", "name": "f", "source_file": "a.py", "start_line": 1,
            "body_summary": "f", "documented": False}
    old = {"extractor": "graphify", "graph_engines": ["graphify"], "verdict": "READY", "nodes": 50}   # pre-0.28.3
    S = _load_sync(tree / "build")
    root = _proj(base, "pass_old_manifest", old)
    check("control: sync reads an older build's manifest (no version field) — the engine memory is intact",
          S._read_prior_engines(root) == ["graphify"], S._read_prior_engines(root))
    _wire(S, [node])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        ok = S.sync(root, driver=_Drv())
    data = _manifest(root)
    check("the pass succeeds over the older manifest and rewrites it", ok is True and data.get("nodes") == 1,
          (ok, data.get("nodes"), buf.getvalue()[-400:]))
    check(f"the rewritten manifest records {FIELD} == the version of the code that ran the sync",
          data.get(FIELD) == want, data.get(FIELD))
    check("... beside the engine memory the downgrade guard reads", data.get("graph_engines") == ["graphify"],
          data.get("graph_engines"))

    kept = dict(old, **{FIELD: "0.0.1-older-build"})
    S = _load_sync(tree / "build")
    root = _proj(base, "pass_kept_graph", kept)
    _wire(S, [], holds=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        S.sync(root, driver=_Drv())
    data = _manifest(root)
    check("control: a 0-node run that keeps the graph keeps its manifest — and the version of the build that "
          "made that graph", data == kept, data)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    print("=" * 72)
    print(f"DeepMap — graph_build.json records the DeepMap version that built the graph (root: {tree})")
    print("=" * 72)
    sys.path.insert(0, str(tree / "build"))
    pj = tree / ".claude-plugin" / "plugin.json"
    pj_before = (pj.read_bytes(), pj.stat().st_mtime_ns) if pj.is_file() else None
    base = Path(tempfile.mkdtemp(prefix="dm_f2_manifest_")).resolve()
    try:
        with _env(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_HOME=str(base / "home"), DEEPMAP_EXTRACTOR=None,
                  DEEPMAP_AUTO_GITIGNORE="0", DEEPMAP_LEARNING=None):
            S = _load_sync(tree / "build")
            for gate, args in ((gate_records_version, (S, tree, base)), (gate_unreadable, (tree, base)),
                               (gate_import_time, (tree, base)), (gate_sync_pass, (tree, base))):
                try:
                    gate(*args)
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
            pj_after = (pj.read_bytes(), pj.stat().st_mtime_ns) if pj.is_file() else None
            check("hermetic: the tree's own plugin.json was only read (bytes and mtime unchanged)",
                  pj_before is not None and pj_after == pj_before, "")
    finally:
        _rmtree(base)                                   # exactly the temp dir this gate created
    check("cleanup: the gate's temp dir is gone (git's read-only objects included)", not base.exists(), str(base))
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
