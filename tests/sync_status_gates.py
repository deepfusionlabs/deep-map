#!/usr/bin/env python3
"""auto-sync sync-core — the records, locks, atomic writes, build recipe, env drift lock and source fingerprint
that DeepMap's background refresh shares with every other writer of a project's graph (build/sync_status.py +
build/sync.py).

  A  sync_status.atomic_write_json: temp + os.replace; a Windows sharing violation (PermissionError winerror 5/32)
     on replace is retried 5x; an always-failing replace -> False, no raise, the old target intact, no temp left;
     a reader thread re-reading the file 500x while it is rewritten never sees a partial file.
  B  every sync() exit path writes the right .deepmap/sync_last.json and sync.last_outcome (synced READY / DEGRADED,
     verdict FAILED, engine-downgrade with and without a timeout, instance busy, foreign-owner, legacy-owner,
     base-claim, index failure, a DB exception -> outcome "error" rc 3 and re-raised, any other exception rc 1);
     the non-holder paths (queued / busy-unqueued / route-problem) write NO file; the record's rc equals what
     sync._main returns under the same stubs.
  C  .deepmap/sync_running.json exists while the pass runs (origin manual, phase extract, HEAD, fingerprint) and
     is gone after a return and after an exception.
  D  a record writer that returns False or raises never changes ok / last_verdict / last_not_pushed.
  E  the manifest: its key set for a fixed input is a superset of the pre-change key set (a literal here); the new
     keys are present; stale_kept / embeddings_pending only when given; written through the atomic writer.
  F  build_recipe: only non-default graph-shaping knobs (DEEPMAP_PRECISE_CALLS=1 -> "on"; DEEPMAP_EXCLUDE_TESTS=0
     not recorded), python X.Y, realpath(DEEPMAP_HOME), the route without userinfo, the model revision from the
     seam — and the NEO4J_PASSWORD value appears nowhere in the manifest.
  F2 the recipe's route source is decided by URI (review r0): an "env" route whose URI is the project's own
     registered instance is recorded "registry" — through the real sync() call site with no module named
     "provision" loaded (setup's shape: provision runs as __main__), in a child that imports provision AFTER the
     injection, and for an operator URI spelled localhost + userinfo; another port, or a project with no instance,
     stays "env". DeepMap's real background runner over that manifest never ends in skipped:recipe:route.
  G  env drift lock: every DEEPMAP_* string literal in the sync pipeline modules is in EXACTLY ONE of
     GRAPH_SHAPING_ENV / NON_SHAPING_ENV / INTERNAL_ENV / ROUTE_ENV (a new knob must be classified). The module set
     is DERIVED (review r0), not listed: sync / enrich / sync_status and every build/ module they import —
     module-level or lazily inside a function, by importlib / _load / _lazy / _sibling, run by path ("x.py"), or
     named as a typed-call language reader (LanguageSpec(..., reader)) — except the named non-pipeline modules
     (_NOT_PIPELINE, each with its reason); it must cover the spec's 18 modules and every reader module
     typed_calls.LANGUAGES names, and a synthetic tree proves a lazily imported new module's knob (and a registry
     reader's) is caught.
  H  canonical_env_value / recipe_env / recipe_conflicts / replay_env tables (a legacy manifest + an explicit
     non-default knob is a conflict; an explicit default-equal value is not; an unset knob never is). Set-valued
     knobs (PRECISE / TYPED / AUDIT / SHAPES): an explicit value is canonical by what it selects, never '' — so an
     opt-out recorded before a simulated default flip still replays as the opt-out after it; conflicts compare
     selections; with the product's readers unloadable a value is kept as spelled and never judged a conflict.
  I  model_cache_state on a fake HF_HUB_CACHE (complete; missing weights; missing modules.json; a missing module
     config; Normalize needs none; revision = the snapshot dir).
  J  every git subprocess sync.py starts runs with GIT_OPTIONAL_LOCKS=0 (a recorder around the real subprocess.run).
  K  sync_status imports only the standard library at module level; importing it imports neither sync, neo4j nor
     torch.
  L  the source fingerprint (real git, temp repos): a docs-only commit -> equal; a source commit -> different;
     committing the dirty work the last fingerprint saw -> equal (also with core.autocrlf=true and a CRLF working
     file); a new file in an untracked dir, a deleted source, a .gitignore change -> different; node_modules/,
     .deepmap/, an in-tree DEEPMAP_HOME, a commit outside a subdir root -> equal; git_busy for every marker; a
     real conflicted merge -> value None (merge, then "unmerged" once MERGE_HEAD is gone); a non-git tree -> "fs";
     _source_state's captured_epoch is taken before its first git call. (The spec's tests/sync_fingerprint_gates.py
     lives here, as section L.)
  L2 the fingerprint's inputs are at least what extraction reads (review r0): a tsconfig-only commit (TS import
     placement), package.json, a tsconfig.<name>.json `extends` target, .graphifyignore, nest-cli.json -> different;
     editor settings -> equal; a root marker in the sync root (.deepmap-root) -> different, but not "dirty"; one in
     a subdirectory -> equal; a submodule (gitlink) bump, new commits checked out in it, an uncommitted edit or an
     untracked file inside it, an untracked nested repository -> different, and the dirty check sees them; a
     vendored submodule -> equal; non-git: .deepmap-root and tsconfig.json count; every *.json / next.config.*
     file name a recognizer module reads is a graph input (drift lock).
  L3 MSBuild inputs (the C# reader, build/csharp_msbuild.py): a .csproj, Directory.Build.props / .targets, an imported
     .props / .targets / .projitems and a packages.config move the fingerprint and read as dirty, uncommitted and
     committed, flags off too (the rule is unconditional, as tsconfig.json's is), in any letter case; auto_sync's
     freshness turns stale on a project-file-only commit; an unrelated file (a .sln, Web.config, nuget.config, a
     .user file, generated obj/ and bin/ outside git) moves nothing, and a tree with no project file keeps the value
     it had. Drift lock: every file-name literal in the MSBuild reader and every file read_group opens on a recorded
     tree is a graph input; the lock is shown failing on a name the rule does not know.
  M  commands/refresh.md names the third refused prune (the reason `no-root-marker`, a word sync.py really records):
     run from the repository root or add .deepmap-root, and --force-prune cannot override it. (No commands/ in the
     tree under test is a failure.)

Hermetic: temp projects, a temp DEEPMAP_HOME, NEO4J_URI on a closed loopback port nothing dials (every driver is a
fake), no network, never ~/.deepmap. Git runs only inside the temp repos this gate creates.
Usage: python tests/sync_status_gates.py [--root <tree>]   (--root: load build/ from another tree — the RED-first
       run against the pre-change snapshot; a --root without build/ is a usage error, exit 2)
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
_GIT = shutil.which("git")
_SECRET_PW = "pw-Zq8-never-in-a-manifest-71"
_SECRET_URI_PW = "uri-Kx3-never-recorded-19"

# The manifest keys sync wrote BEFORE auto-sync for the fixed input of section E (captured from v0.29.0's
# write_graph_build_manifest; a literal, never `git show`).
_PRE_CHANGE_KEYS = frozenset({
    "built_at_utc", "nodes", "calls_edges", "embedded", "extractor", "provenance", "note", "deepmap_version",
    "built_at_sha", "dirty", "built_at_dirty", "dirty_paths_sample", "dirty_paths_count", "graph_engines",
    "stale_cleanup_skipped", "graph_nodes", "verdict", "reasons", "coverage", "extractor_requested",
    "graphify_version"})
_NEW_KEYS = frozenset({"source_fingerprint", "source_fingerprint_kind", "edits_seen_through_utc",
                       "edits_seen_through_epoch", "build_recipe", "sync_wall_s"})
# The spec's pipeline modules: the floor the derived set (_pipeline_modules) must cover.
_PIPELINE = ("sync.py graphify_extract.py graphify_case_shim.py summarize.py extract_cache.py framework_edges.py "
             "language_recognizers.py ruby_rails_edges.py csharp_edges.py java_edges.py go_edges.py "
             "typescript_edges.py edge_resolve.py embeddings.py indexes.py verdict.py enrich.py sync_status.py").split()
# build/ modules the sync pipeline imports for something OTHER than building the graph: their DEEPMAP_* knobs never
# shape it, so the drift lock does not follow them. Anything else sync / enrich / sync_status reach is scanned.
_NOT_PIPELINE = {
    "port_registry": "routing, ports and project identity (its graph-relevant knob, DEEPMAP_HOME, is ROUTE_ENV)",
    "provision": "setup, heal and the instance lifecycle (sync imports it only to record the base store's owner)",
    "deps_bootstrap": "DeepMap's own Python environment",
    "mcp_bounds": "the MCP servers",
    "retrieval": "search, after the graph is built",
    "retrieval_log": "RM-1's retrieval log",
    "tune_weights": "RM-1's offline tuner (sync's optional auto-tune)",
    "learned_prior": "RM-1's ranking prior",
    "instance_state": ".deepmap/instance.json flags",
}


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/sync_status_gates.py [--root <tree holding build/>] (got {tree!r})",
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


@contextlib.contextmanager
def _patched(obj, **attrs):
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


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return fn(*a, **k), buf.getvalue()


def _git(repo, *args, env=None):
    return subprocess.run([_GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull, *args],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60, env=env)


def _init_repo(path: Path, *, autocrlf: str = "false") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "core.autocrlf", autocrlf)
    return path


def _commit_all(repo, msg="c"):
    _git(repo, "add", "-A")
    return _git(repo, "commit", "-q", "--no-verify", "-m", msg)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# =====================================================================================================
# A — atomic_write_json
# =====================================================================================================
def gate_a_atomic(ST, base: Path) -> None:
    print("\n-- A: atomic_write_json — replace, Windows retry, never a partial file --")
    d = base / "atomic"
    d.mkdir()
    target = d / "rec.json"
    check("A: a plain write lands and parses", ST.atomic_write_json(target, {"v": 1}) is True
          and _read(target) == {"v": 1}, _read(target))
    check("A: no temp file is left behind", [p.name for p in d.iterdir()] == ["rec.json"],
          [p.name for p in d.iterdir()])
    calls = []
    real = ST._replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            e = PermissionError(13, "sharing violation")
            e.winerror = 32
            raise e
        return real(src, dst)

    with _patched(ST, _replace=flaky, _REPLACE_RETRY_S=0.01):
        ok = ST.atomic_write_json(target, {"v": 2})
    check("A: a sharing violation (winerror 32) twice, then success -> True after 3 replace attempts",
          ok is True and len(calls) == 3 and _read(target) == {"v": 2}, (ok, len(calls), _read(target)))
    calls.clear()

    def always(src, dst):
        calls.append(1)
        e = PermissionError(13, "sharing violation")
        e.winerror = 32
        raise e

    with _patched(ST, _replace=always, _REPLACE_RETRY_S=0.01):
        try:
            ok, raised = ST.atomic_write_json(target, {"v": 3}), None
        except Exception as exc:  # noqa: BLE001
            ok, raised = None, exc
    check("A: a replace that always fails -> False, no exception, 1 + 5 attempts",
          ok is False and raised is None and len(calls) == 6, (ok, raised, len(calls)))
    check("A: ... the old target is intact (never partial) and no temp file is left",
          _read(target) == {"v": 2} and sorted(p.name for p in d.iterdir()) == ["rec.json"],
          (_read(target), sorted(p.name for p in d.iterdir())))
    calls.clear()

    def denied(src, dst):
        calls.append(1)
        raise PermissionError(13, "denied")              # no winerror: not a sharing violation

    with _patched(ST, _replace=denied):
        ok = ST.atomic_write_json(target, {"v": 4})
    check("A: a PermissionError that is not a sharing violation is not retried -> False after 1 attempt",
          ok is False and len(calls) == 1, (ok, len(calls)))
    # a record its writer removes never lingers because a reader had it open at that instant (Windows: unlink of an
    # open file is a sharing violation — retried)
    rec_path = d / "sync.preempt.1.json"
    ST.atomic_write_json(rec_path, {"pid": 1})
    fh = open(rec_path, encoding="utf-8")
    t = threading.Timer(0.25, fh.close)
    t.start()
    ST.clear_preempt(rec_path)
    t.join()
    fh.close()
    check("A: clear_preempt removes the record even while a reader holds it open for 0.25 s", not rec_path.exists())
    # a reader re-reading the file 500x while it is rewritten never sees a partial document
    big = {"pad": "x" * 20000}
    ST.atomic_write_json(target, dict(big, n=0))
    partial, reads, stop = [], [0], threading.Event()

    def reader():
        while reads[0] < 500 and not stop.is_set():
            try:
                with open(target, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue                                  # a Windows open during the swap: not a partial read
            reads[0] += 1
            try:
                obj = json.loads(text)
                if obj.get("pad") != big["pad"]:
                    partial.append("content")
            except ValueError:
                partial.append(text[:40])

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    wrote = 0
    deadline = time.monotonic() + 60
    while t.is_alive() and time.monotonic() < deadline:
        if ST.atomic_write_json(target, dict(big, n=wrote + 1)):
            wrote += 1
    stop.set()
    t.join(5)
    check("A: 500 reads during repeated rewrites never saw a partial file", reads[0] >= 500 and not partial
          and wrote > 0, (reads[0], partial[:3], wrote))


# =====================================================================================================
# B / C / D / F — the full sync() path with a stubbed pipeline and a fake driver
# =====================================================================================================
class _FakeVerdict:
    READY, DEGRADED, FAILED = "READY", "DEGRADED", "FAILED"

    def __init__(self, verdict="READY", reasons=()):
        self.v, self.rs = verdict, list(reasons)

    def extension_census(self, root):
        return {}

    def build_coverage(self, root, nodes, census=None):
        return {}

    def classify(self, **k):
        return self.v, list(self.rs)

    def ml_stack_ok(self):
        return True

    def format_block(self, v, rs, **k):
        return f"[deepmap] verdict: {v}"

    def note_engine_nodes(self, root, nodes):
        return None


class _Drv:
    def verify_connectivity(self):
        return None

    def close(self):
        return None

    def session(self):
        raise AssertionError("an unstubbed query reached the fake driver")


def _node(rel="m.py", name="f", line=1):
    return {"key": f"{rel}::{name}:{line}", "label": "Function", "name": name, "source_file": rel,
            "start_line": line, "body_summary": name}


def _project(base: Path, name: str, *, git=False) -> Path:
    root = base / name
    root.mkdir(parents=True)
    (root / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    if git:
        _init_repo(root)
        _commit_all(root, "init")
    return root


@contextlib.contextmanager
def _pipeline(S, *, verdict="READY", reasons=(), nodes=None, engine="graphify", info=None, owner=None,
              push=None, holds=True, waits=True, inst_lock=True, rebuild=None, extra=None):
    """Stub everything _sync_pass touches except the code under test (records, verdict wiring, manifest)."""
    nodes = [_node()] if nodes is None else nodes
    fake_verdict = _FakeVerdict(verdict, reasons)

    def _rebuild(root):
        if rebuild is not None:
            rebuild(root)
        return S._Extraction(list(nodes), [], [], engine, dict({"requested": "auto", "resolved": engine},
                                                               **(info or {})))

    def _push(drv, n, c, i, t=None):
        if push is not None:
            return push(n)
        return (len(n), 0)

    attrs = dict(
        _rebuild_code=_rebuild, _apply_recognizers=lambda root, n: (n, []),
        _read_owning_root=lambda d: owner, _graph_has_nodes=lambda d: True, _graph_holds_nodes=lambda d: holds,
        _write_owning_root=lambda d, ident: None, _record_base_store_owner=lambda *a, **k: None,
        _remove_stale_short_names=lambda d: 0, _fts_rebuild_pending=lambda d: False, _fts_changed_docs=lambda d, n: 0,
        _mark_fts_rebuild=lambda d, p: None, push_to_neo4j=_push, _reuse_rekeyed_embeddings=lambda d, n: 0,
        _rebuild_code_fts=lambda d: True, _count_embedded_nodes=lambda d, fallback=0: len(nodes),
        _graph_node_count=lambda d: len(nodes), _maybe_auto_tune=lambda root: None,
        _unkey_receiver_calls=lambda root, n, c: (c, 0), _attach_bare_calls=lambda root, n, c: 0,
        _attach_local_defs=lambda root, n, c: 0, _attach_local_def_calls=lambda root, n, c: 0,
        indexes=SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: waits,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False),
        embeddings=SimpleNamespace(generate_embeddings=lambda d: len(nodes)))
    if not inst_lock:
        attrs["_acquire_instance_lock"] = lambda *a, **k: None
    attrs.update(extra or {})
    saved_verdict = sys.modules.get("verdict")
    sys.modules["verdict"] = fake_verdict
    try:
        with _patched(S, **attrs):
            yield
    finally:
        if saved_verdict is None:
            sys.modules.pop("verdict", None)
        else:
            sys.modules["verdict"] = saved_verdict


def _run(S, root, **kw):
    """sync(root) under the current stubs -> (ok, exc, record-file, S.last_outcome, output)."""
    buf = io.StringIO()
    ok = exc = None
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        try:
            ok = S.sync(root, driver=_Drv(), **kw)
        except Exception as e:  # noqa: BLE001
            exc = e
    return ok, exc, _read(Path(root) / ".deepmap" / "sync_last.json"), getattr(S, "last_outcome", "<missing>"), \
        buf.getvalue()


def _main_rc(S, root):
    """sync._main under the same stubs (it builds its own driver: hand it the fake one)."""
    real = S.sync

    def with_driver(target, **k):
        return real(target, driver=_Drv(), **k)

    buf = io.StringIO()
    with _patched(S, sync=with_driver), contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        try:
            return S._main(["sync.py", str(root)])
        except Exception as exc:  # noqa: BLE001
            return f"raised {type(exc).__name__}"


def gate_b_exit_paths(S, base: Path) -> None:
    print("\n-- B: every sync() exit path -> sync_last.json + last_outcome (rc as sync._main computes it) --")
    try:
        from neo4j.exceptions import ServiceUnavailable as _SU
    except Exception:  # noqa: BLE001
        _SU = None
    prior_graphify = {"graph_engines": ["graphify"], "extractor": "graphify", "nodes": 5}
    scen = [
        # (name, pipeline kwargs, prior manifest, sync kwargs, expected outcome, rc, extra checks)
        ("synced READY", dict(verdict="READY"), None, {}, "synced", 0,
         lambda r: r.get("verdict") == "READY" and r.get("pushed_nodes") == 1 and r.get("not_pushed_kind") is None),
        ("synced DEGRADED", dict(verdict="DEGRADED", reasons=["LOW COVERAGE for X"]), None, {}, "synced", 0,
         lambda r: r.get("verdict") == "DEGRADED" and r.get("reasons") == ["LOW COVERAGE for X"]),
        ("verdict FAILED", dict(verdict="FAILED", reasons=["0 code nodes"]), None, {}, "failed", 1,
         lambda r: r.get("verdict") == "FAILED"),
        ("engine-downgrade (timed out)", dict(engine="ast", info={"fallback_reason": "timeout", "timed_out": True,
                                                                  "timeout_s": 60}),
         prior_graphify, {}, "not-pushed", 4,
         lambda r: r.get("not_pushed_kind") == "engine-downgrade" and r.get("engine_timed_out") is True
         and str(r.get("reason") or "").startswith("engine-downgrade")),
        ("engine-downgrade (no timeout)", dict(engine="ast", info={"fallback_reason": "graphify crashed"}),
         prior_graphify, {}, "not-pushed", 4,
         lambda r: r.get("not_pushed_kind") == "engine-downgrade" and r.get("engine_timed_out") is False),
        ("instance busy", dict(inst_lock=False), None, {}, "not-pushed", 4,
         lambda r: r.get("not_pushed_kind") == "busy-instance"),
        ("foreign-owner", dict(owner=str((base / "another-project").resolve())), None, {}, "refused", 1,
         lambda r: r.get("refusal_kind") == "foreign-owner" and "REFUSING" in str(r.get("reason"))),
        ("legacy-owner", dict(extra={"_resolve_route": lambda root: ("bolt://127.0.0.1:9", True, None),
                                     "_legacy_verdict": lambda *a, **k: (False, "the base graph is another's")}),
         None, {}, "refused", 1, lambda r: r.get("refusal_kind") == "legacy-owner"),
        ("base-claim", dict(owner=str((base / "another-project").resolve()),
                            extra={"_is_base_claim_route": lambda root, uri: True}), None, {}, "refused", 1,
         lambda r: r.get("refusal_kind") == "base-claim"),
        ("index failure", dict(waits=False), None, {}, "failed", 1,
         lambda r: r.get("refusal_kind") == "index-failure"),
    ]
    for i, (name, pk, prior, skw, want_outcome, want_rc, extra_ok) in enumerate(scen):
        root = _project(base, f"b{i}")
        if prior is not None:
            (root / ".deepmap").mkdir(exist_ok=True)
            (root / ".deepmap" / "graph_build.json").write_text(json.dumps(prior), encoding="utf-8")
        with _pipeline(S, **pk):
            ok, exc, rec, lo, out = _run(S, root, **skw)
            main_rc = _main_rc(S, root)
            rec2 = _read(root / ".deepmap" / "sync_last.json")
        good = (exc is None and isinstance(rec, dict) and rec.get("outcome") == want_outcome
                and rec.get("rc") == want_rc and rec.get("origin") == "manual" and rec.get("schema") == 1)
        check(f"B[{name}]: sync_last.json outcome {want_outcome!r}, rc {want_rc}, origin manual", good,
              (exc, rec, out[-400:]))
        check(f"B[{name}]: the record's own fields", bool(isinstance(rec, dict) and extra_ok(rec)), rec)
        check(f"B[{name}]: last_outcome is that record", isinstance(lo, dict) and isinstance(rec, dict)
              and lo.get("id") == rec.get("id") and lo.get("outcome") == rec.get("outcome"), (lo, rec))
        check(f"B[{name}]: the record's rc equals sync._main's exit code under the same stubs",
              isinstance(rec2, dict) and rec2.get("rc") == main_rc == want_rc, (rec2 and rec2.get("rc"), main_rc))
        check(f"B[{name}]: no holder record is left (sync_running.json removed)",
              not (root / ".deepmap" / "sync_running.json").exists())
    # exceptions escaping the pass: recorded, then re-raised unchanged
    for name, exc_obj, want_rc in (("OSError (a DB / OS error)", OSError("connection reset"), 3),
                                   ("ServiceUnavailable (neo4j DriverError)", _SU("down") if _SU else None, 3),
                                   ("ValueError (a code bug)", ValueError("bug"), 1)):
        if exc_obj is None:
            print(f"  [skip] B[{name}]: the neo4j driver is not installed")
            continue
        root = _project(base, f"bx{want_rc}{type(exc_obj).__name__}")

        def boom(n, e=exc_obj):
            raise e

        with _pipeline(S, push=boom):
            ok, exc, rec, lo, out = _run(S, root)
        check(f"B[{name}]: re-raised unchanged", exc is exc_obj, (exc, out[-300:]))
        check(f"B[{name}]: recorded outcome 'error', rc {want_rc}",
              isinstance(rec, dict) and rec.get("outcome") == "error" and rec.get("rc") == want_rc, rec)
        check(f"B[{name}]: sync_running.json removed after the exception",
              not (root / ".deepmap" / "sync_running.json").exists())
    # non-holder paths: no pass ran -> no sync_last.json, a small last_outcome
    root = _project(base, "b_route")
    with _pipeline(S, extra={"_resolve_route": lambda r: (None, False, "no safe route to this project's Neo4j")}):
        ok, exc, rec, lo, out = _run(S, root)
    check("B[route-problem]: returns False, writes no sync_last.json, last_outcome route-problem",
          ok is False and rec is None and isinstance(lo, dict) and lo.get("outcome") == "route-problem"
          and "no safe route" in str(lo.get("reason")), (ok, rec, lo))
    for queued in (True, False):
        root = _project(base, f"b_busy_{queued}")
        held = S._acquire_sync_lock(root)
        try:
            with _pipeline(S, extra={"_request_rerun": (lambda r, **k: queued)}):
                with _patched(sys.modules["sync_status"], HOLDER_SETTLE_S=0.2) if "sync_status" in sys.modules \
                        else contextlib.nullcontext():
                    ok, exc, rec, lo, out = _run(S, root)
        finally:
            S._release_sync_lock(held)
        want = "queued" if queued else "busy-unqueued"
        check(f"B[{want}]: returns True, writes no sync_last.json, last_outcome {want!r}",
              ok is True and rec is None and isinstance(lo, dict) and lo.get("outcome") == want,
              (ok, rec, lo, out[-300:]))


def gate_c_running(S, base: Path) -> None:
    print("\n-- C: sync_running.json while the pass runs, gone afterwards --")
    if not _GIT:
        check("C: git is installed (needed for the HEAD check)", False)
        return
    root = _project(base, "c_git", git=True)
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    seen = []
    with _pipeline(S, rebuild=lambda r: seen.append(_read(Path(r) / ".deepmap" / "sync_running.json"))):
        ok, exc, rec, lo, out = _run(S, root)
    rr = seen[0] if seen else None
    check("C: during extraction the holder record exists", isinstance(rr, dict), (seen, out[-300:]))
    rr = rr or {}
    check("C: ... origin manual, phase extract, this pid with a start stamp",
          rr.get("origin") == "manual" and rr.get("phase") == "extract" and rr.get("pid") == os.getpid()
          and rr.get("pid_created") is not None and rr.get("schema") == 1, rr)
    check("C: ... head_at_start = HEAD, source_fingerprint = 32 hex", rr.get("head_at_start") == head
          and isinstance(rr.get("source_fingerprint"), str) and len(rr.get("source_fingerprint")) == 32, rr)
    check("C: removed after a normal return", ok is True and not (root / ".deepmap" / "sync_running.json").exists())
    root2 = _project(base, "c_exc")
    with _pipeline(S, push=lambda n: (_ for _ in ()).throw(RuntimeError("mid-push failure"))):
        ok, exc, rec, lo, out = _run(S, root2)
    check("C: removed after an exception too", isinstance(exc, RuntimeError)
          and not (root2 / ".deepmap" / "sync_running.json").exists(), (exc,))


def gate_d_record_failures(S, ST, base: Path) -> None:
    print("\n-- D: a failing record writer never changes ok / last_verdict / last_not_pushed --")

    def raiser(*a, **k):
        raise RuntimeError("record writer exploded")

    for label, pk in (("READY", dict(verdict="READY")), ("instance busy", dict(inst_lock=False))):
        r0 = _project(base, f"d0_{label.replace(' ', '_')}")
        with _pipeline(S, **pk):
            ok0, exc0, _rec0, _lo, _o = _run(S, r0)
        baseline = (ok0, S.last_verdict, S.last_not_pushed)
        for how, patch in (("returning False", dict(write_running=lambda *a, **k: False,
                                                    update_running=lambda *a, **k: False,
                                                    write_last=lambda *a, **k: False,
                                                    clear_running=lambda *a, **k: False,
                                                    atomic_write_json=lambda *a, **k: False)),
                           ("raising", dict(write_running=raiser, update_running=raiser, write_last=raiser,
                                            clear_running=raiser))):
            r1 = _project(base, f"d1_{label.replace(' ', '_')}_{how.split()[0]}")
            with _pipeline(S, **pk), _patched(ST, **patch):
                ok1, exc1, _rec1, _lo1, out1 = _run(S, r1)
            got = (ok1, S.last_verdict, S.last_not_pushed)
            check(f"D[{label}, writers {how}]: same ok / last_verdict / last_not_pushed, no exception",
                  exc1 is None and got == baseline, (exc1, got, baseline, out1[-300:]))


def gate_e_manifest(S, ST, base: Path) -> None:
    print("\n-- E: the manifest — pre-change keys kept, new keys added, atomic write --")
    root = _project(base, "e")
    state = {"sha": "a" * 40, "dirty": False, "dirty_paths_sample": [], "dirty_paths_count": 0,
             "content_hash": None, "captured_utc": "2026-10-22T10:00:00Z", "captured_epoch": 1792663200.0,
             "fingerprint": {"value": "b" * 32, "kind": "git"}}
    fixed = dict(n_nodes=1, n_edges=2, n_embedded=3, extractor="graphify", verdict="READY", reasons=[],
                 coverage={}, extraction={"requested": "auto", "graphify_version": "0.8.39"}, source_state=state,
                 graph_engines=["graphify"], graph_nodes=5, stale_cleanup_skipped="blast-radius")
    calls = []
    real = getattr(ST, "atomic_write_text", None)

    def rec_write(path, text):
        calls.append(str(path))
        return real(path, text)

    with _patched(ST, atomic_write_text=rec_write):
        p = S.write_graph_build_manifest(root, **fixed, build_recipe={"schema": 1, "engine": "graphify"},
                                         sync_wall_s=12.34)
    m = _read(root / ".deepmap" / "graph_build.json") or {}
    keys = set(m)
    pre = set(_PRE_CHANGE_KEYS) if S._CODE_VERSION else set(_PRE_CHANGE_KEYS) - {"deepmap_version"}
    check("E: every pre-change key is still written", pre <= keys, sorted(pre - keys))
    check("E: the auto-sync keys are written", _NEW_KEYS <= keys, sorted(_NEW_KEYS - keys))
    check("E: their values", m.get("source_fingerprint") == "b" * 32 and m.get("source_fingerprint_kind") == "git"
          and m.get("edits_seen_through_utc") == "2026-10-22T10:00:00Z"
          and m.get("edits_seen_through_epoch") == 1792663200.0 and m.get("sync_wall_s") == 12.3
          and m.get("build_recipe") == {"schema": 1, "engine": "graphify"}, m)
    check("E: stale_kept / embeddings_pending absent when not given",
          "stale_kept" not in m and "embeddings_pending" not in m, sorted(keys))
    check("E: written through sync_status.atomic_write_text (the atomic path), once",
          p is not None and calls == [str(root / ".deepmap" / "graph_build.json")], (p, calls))
    S.write_graph_build_manifest(root, **fixed, stale_kept=3, embeddings_pending=True)
    m2 = _read(root / ".deepmap" / "graph_build.json") or {}
    check("E: stale_kept and embeddings_pending written when given", m2.get("stale_kept") == 3
          and m2.get("embeddings_pending") is True, m2)
    S.write_graph_build_manifest(root, **fixed, stale_kept=None, embeddings_pending=False)
    m3 = _read(root / ".deepmap" / "graph_build.json") or {}
    check("E: embeddings_pending=False is not written", "embeddings_pending" not in m3 and "stale_kept" not in m3,
          sorted(m3))
    text = (root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8")
    check("E: content format unchanged (indent=2, sort_keys)", text == json.dumps(m3, indent=2, sort_keys=True),
          text[:200])
    with _patched(ST, atomic_write_text=lambda path, text: False):
        p_fail = S.write_graph_build_manifest(root, **fixed)
    check("E: a failed atomic write -> None (never raises)", p_fail is None, p_fail)


def gate_f_recipe(S, ST, base: Path) -> None:
    print("\n-- F: build_recipe through a real sync() pass --")
    root = _project(base, "f")
    home = Path(os.environ["DEEPMAP_HOME"])
    with _env(DEEPMAP_PRECISE_CALLS="1", DEEPMAP_EXCLUDE_TESTS="0", NEO4J_PASSWORD=_SECRET_PW,
              NEO4J_URI=f"bolt://neo4j:{_SECRET_URI_PW}@127.0.0.1:9"), \
            _patched(ST, model_cache_state=lambda env=None: {"complete": True, "revision": "rev-from-seam",
                                                             "path": None, "missing": []}), \
            _pipeline(S, verdict="READY"):
        ok, exc, rec, lo, out = _run(S, root)
    path = root / ".deepmap" / "graph_build.json"
    m = _read(path) or {}
    r = m.get("build_recipe") or {}
    check("F: the manifest carries a build_recipe", isinstance(m.get("build_recipe"), dict), (ok, exc, sorted(m)))
    check("F: env records only the non-default knobs: DEEPMAP_PRECISE_CALLS=1 as 'on', EXCLUDE_TESTS=0 not at all",
          r.get("env") == {"DEEPMAP_PRECISE_CALLS": "on"}, r.get("env"))
    check("F: python X.Y of this interpreter", r.get("python") == f"{sys.version_info[0]}.{sys.version_info[1]}",
          r.get("python"))
    check("F: deepmap_home = realpath(DEEPMAP_HOME)", r.get("deepmap_home") == os.path.realpath(str(home)),
          (r.get("deepmap_home"), os.path.realpath(str(home))))
    check("F: the route: source env, URI without userinfo", r.get("route") == {"source": "env",
                                                                             "uri": "bolt://127.0.0.1:9"},
          r.get("route"))
    check("F: the model revision comes from model_cache_state (the seam)",
          r.get("embed_model_revision") == "rev-from-seam" and bool(r.get("embed_model")), r)
    check("F: engine / extractor_requested / version recorded", r.get("engine") == "graphify"
          and r.get("extractor_requested") == "auto" and r.get("deepmap_version") == S._CODE_VERSION, r)
    check("F: the graph revision of this code is recorded (graph_rev == sync_status.GRAPH_REV, a whole number >= 1: "
          "revision 1, the census of languages this install cannot parse, ships with it)",
          r.get("graph_rev") == getattr(ST, "GRAPH_REV", None) and type(r.get("graph_rev")) is int
          and r.get("graph_rev") >= 1, (r.get("graph_rev"), getattr(ST, "GRAPH_REV", None)))
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    check("F: neither the NEO4J_PASSWORD value nor the URI's password appears in the manifest",
          bool(text) and _SECRET_PW not in text and _SECRET_URI_PW not in text, text[:200])
    last = (root / ".deepmap" / "sync_last.json").read_text(encoding="utf-8") \
        if (root / ".deepmap" / "sync_last.json").exists() else ""
    check("F: ... nor in sync_last.json", bool(last) and _SECRET_PW not in last and _SECRET_URI_PW not in last)
    check("F: sync_wall_s recorded for the pass", isinstance(m.get("sync_wall_s"), (int, float)), m.get("sync_wall_s"))


# =====================================================================================================
# F2 — the recipe's route source is decided by URI
# =====================================================================================================
def _free_pair() -> int:
    """A loopback port p with p and p + 1 both free right now (never 7687 / 7474 / 8787 / 8788)."""
    import socket
    for port in range(17611, 17999, 2):
        free = True
        for q in (port, port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
                try:
                    sk.bind(("127.0.0.1", q))
                except OSError:
                    free = False
        if free:
            return port
    return 17689


def _child_env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEEPMAP_") and k != "NEO4J_URI"}
    env.update(DEEPMAP_HOME=str(home), PYTHONIOENCODING="utf-8", DEEPMAP_MANAGED_VENV="0", DEEPMAP_AUTO_SYNC="0")
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    for k in [k for k, v in extra.items() if v is None]:
        env.pop(k, None)
    return env


def gate_f2_route_source(S, build: Path, base: Path) -> None:
    print("\n-- F2: the recipe's route source is decided by URI, not by which modules are loaded --")
    home = Path(os.environ["DEEPMAP_HOME"])
    root = _project(base, "f2")
    try:
        import port_registry as PR
    except Exception as exc:  # noqa: BLE001
        check("F2: port_registry imports", False, exc)
        return
    port = _free_pair()
    try:
        pair = PR.allocate_neo4j_ports(str(root), prefer=(port, port + 1), base_adopt_ok=lambda: False)
    except Exception as exc:  # noqa: BLE001
        pair = exc
    managed = PR.resolve_neo4j_route(str(root), honor_env=False)
    check("F2: setup: the project's own pair is registered in the temp registry",
          pair == (port, port + 1) and managed.source == "registry" and managed.uri == f"bolt://127.0.0.1:{port}",
          (pair, managed))
    uri = f"bolt://127.0.0.1:{port}"
    # the real sync() call site, setup's shape: NEO4J_URI = the registered pair, no module named "provision"
    with _env(NEO4J_URI=uri, DEEPMAP_EXTRACTOR="ast"), _pipeline(S, verdict="READY", engine="ast"):
        prov_loaded = "provision" in sys.modules
        ok, exc, rec, lo, out = _run(S, root)
    m = _read(root / ".deepmap" / "graph_build.json") or {}
    route = (m.get("build_recipe") or {}).get("route")
    check("F2: setup's shape (NEO4J_URI = the registered pair, provision not loaded as 'provision') -> the real "
          "sync() records route source 'registry'",
          ok is True and route == {"source": "registry", "uri": uri}, (prov_loaded, ok, exc, route))
    rrs = getattr(S, "_recipe_route_source")
    import inspect
    takes_uri = "uri" in inspect.signature(rrs).parameters

    def src(u):
        with _env(NEO4J_URI=u):
            r = S._resolve_route(root)
            return rrs(root, getattr(r, "source", None), r[0]) if takes_uri else rrs(root, getattr(r, "source", None))

    got = {u: src(u) for u in (f"bolt://neo4j:{_SECRET_URI_PW}@localhost:{port}", f"bolt://127.0.0.1:{port + 7}")}
    check("F2: an operator URI naming the same endpoint (localhost spelling, with userinfo) -> 'registry'",
          got[f"bolt://neo4j:{_SECRET_URI_PW}@localhost:{port}"] == "registry", got)
    check("F2: an operator URI to another port -> 'env'", got[f"bolt://127.0.0.1:{port + 7}"] == "env", got)
    other = _project(base, "f2_unregistered")
    with _env(NEO4J_URI=uri):
        r = S._resolve_route(other)
        o = rrs(other, getattr(r, "source", None), r[0]) if takes_uri else rrs(other, getattr(r, "source", None))
    check("F2: a project with no instance of its own + NEO4J_URI -> 'env'", o == "env", o)
    # a child that imports provision AFTER the injection (its import-time _USER_SET_NEO4J_URI is then True)
    code = ("import inspect, json, sys; sys.path.insert(0, sys.argv[1]); import provision; import sync; "
            "r = sync._resolve_route(sys.argv[2]); f = sync._recipe_route_source; "
            "s = f(sys.argv[2], r.source, r[0]) if 'uri' in inspect.signature(f).parameters "
            "else f(sys.argv[2], r.source); "
            "print(json.dumps({'src': s, 'user_set': provision._USER_SET_NEO4J_URI}))")
    out = subprocess.run([sys.executable, "-c", code, str(build), str(root)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=180, env=_child_env(home, NEO4J_URI=uri))
    try:
        res = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        res = None
    check("F2: provision imported after the injection (its _USER_SET_NEO4J_URI True) -> still 'registry'",
          isinstance(res, dict) and res.get("src") == "registry", (res, out.stderr[-400:]))
    # DeepMap's own background runner over that manifest (a post-merge check: needs build/auto_sync.py)
    if not (build / "auto_sync.py").is_file():
        print("  (build/auto_sync.py is not in this tree: the runner check is skipped)")
        return
    hf = base / "f2_hf_empty"
    hf.mkdir(exist_ok=True)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import auto_sync; rc = auto_sync.run(sys.argv[2]); "
            "lr = auto_sync.read_state(sys.argv[2]).get('last_run') or {}; "
            "print(json.dumps({'rc': rc, 'outcome': lr.get('outcome'), 'reason': lr.get('reason')}))")
    out = subprocess.run([sys.executable, "-c", code, str(build), str(root)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300,
                         env=_child_env(home, NEO4J_URI=None, DEEPMAP_AUTO_SYNC="1", DEEPMAP_HEAL="0",
                                        CLAUDE_CODE_SESSION_ATTENDED="1", HF_HUB_CACHE=str(hf)))
    try:
        res = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        res = None
    check("F2: the real background runner over that manifest (no NEO4J_URI) never ends in skipped:recipe:route",
          isinstance(res, dict) and res.get("outcome") not in (None, "skipped:recipe:route"),
          (res, out.stderr[-600:]))


# =====================================================================================================
# G — env drift lock
# =====================================================================================================
_LOADERS = ("import_module", "_load", "_lazy", "__import__", "_sibling")   # f("x") imports build/x.py


def _module_refs(path: Path, mods: set) -> set:
    """The build/ modules `path` imports — anywhere (module level or lazily inside a function), as `import x` /
    `from x import`, `importlib.import_module("x")` / `_load("x")` / `_lazy("x")` / `_sibling("x")` /
    `__import__("x")` — or names as a script it runs by path (a string constant "x.py"), or names as a typed-call
    language reader (`LanguageSpec(name, exts, family, "x", ...)` / `reader="x"`: typed_calls loads it by that name
    through `_sibling(spec.reader)`, a non-constant argument no loader rule above can see — S2 review r1 finding 2)."""
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call):
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if fname in _LOADERS and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                out.add(n.args[0].value.split(".")[0])
            elif fname == "LanguageSpec":
                reader = n.args[3] if len(n.args) > 3 else next(
                    (k.value for k in n.keywords if k.arg == "reader"), None)
                if isinstance(reader, ast.Constant) and isinstance(reader.value, str):
                    out.add(reader.value.split(".")[0])
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".py") \
                and n.value[:-3] in mods:
            out.add(n.value[:-3])
    return out & mods


def _pipeline_modules(build: Path) -> list:
    """sync / enrich / sync_status plus every build/ module they reach (_module_refs, transitively), not descending
    into _NOT_PIPELINE. File names, sorted."""
    mods = {p.stem for p in build.glob("*.py")}
    seen, todo = set(), [m for m in ("sync", "enrich", "sync_status") if m in mods]
    while todo:
        m = todo.pop()
        if m in seen or m in _NOT_PIPELINE:
            continue
        seen.add(m)
        todo.extend(_module_refs(build / f"{m}.py", mods) - seen)
    return sorted(f"{m}.py" for m in seen)


def _deepmap_literals(build: Path, names) -> dict:
    import re
    pat = re.compile(r"^DEEPMAP_[A-Z0-9_]*[A-Z0-9]$")
    found = {}
    for name in names:
        for n in ast.walk(ast.parse((build / name).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and pat.match(n.value):
                found.setdefault(n.value, set()).add(name)
    return found


def gate_g_drift(ST, build: Path) -> None:
    print("\n-- G: every DEEPMAP_* literal of the sync pipeline is classified exactly once --")
    derived = _pipeline_modules(build)
    missing = sorted(set(_PIPELINE) - set(derived))
    check(f"G: the derived pipeline ({len(derived)} modules) covers the spec's {len(_PIPELINE)}", not missing,
          {"missing": missing, "derived": derived})
    unscanned = sorted(p.name for p in build.glob("*.py") if p.name not in derived and p.stem not in _NOT_PIPELINE)
    print(f"  (not reached from sync / enrich / sync_status, so not scanned: {', '.join(unscanned) or 'none'})")
    # the derivation itself: a NEW module sync imports lazily inside a function, whose knob is unclassified
    synth = Path(tempfile.mkdtemp(prefix="dm_drift_synth_")).resolve()
    try:
        (synth / "sync.py").write_text("import sync_status\n\n\ndef f():\n    import newpipe\n    return newpipe\n\n\n"
                                       "def h():\n    return _sibling('typedreg')\n", encoding="utf-8")
        (synth / "enrich.py").write_text("def g():\n    import importlib\n    return importlib.import_module("
                                         "'newpipe2')\n", encoding="utf-8")
        (synth / "sync_status.py").write_text("X = 1\n", encoding="utf-8")
        (synth / "newpipe.py").write_text("KNOB = 'DEEPMAP_NEWKNOB'\n", encoding="utf-8")
        (synth / "newpipe2.py").write_text("import os\nV = os.environ.get('DEEPMAP_NEWKNOB2')\n", encoding="utf-8")
        # typed_calls' shape: a language registry whose readers are loaded by NAME (_sibling(spec.reader))
        (synth / "typedreg.py").write_text(
            "LANGUAGES = {s.name: s for s in (\n    LanguageSpec('x', ('.x',), 'x', 'readpos', wave=2),\n"
            "    LanguageSpec('y', ('.y',), 'y', reader='readkw'),\n)}\n\n\ndef load(spec):\n"
            "    return _sibling(spec.reader)\n", encoding="utf-8")
        (synth / "readpos.py").write_text("import os\nS = os.environ.get('DEEPMAP_READERKNOB')\n", encoding="utf-8")
        (synth / "readkw.py").write_text("K = 'DEEPMAP_READERKNOB2'\n", encoding="utf-8")
        (synth / "provision.py").write_text("H = 'DEEPMAP_NOT_A_GRAPH_KNOB'\n", encoding="utf-8")
        (synth / "unrelated.py").write_text("U = 'DEEPMAP_UNRELATED'\n", encoding="utf-8")
        sd = _pipeline_modules(synth)
        sf = _deepmap_literals(synth, sd)
        check("G: derivation self-test — a module sync imports lazily, and one enrich imports via importlib, are "
              "scanned (their knobs found); a non-pipeline module (provision) and an unreached one are not",
              {"newpipe.py", "newpipe2.py"} <= set(sd) and {"DEEPMAP_NEWKNOB", "DEEPMAP_NEWKNOB2"} <= set(sf)
              and "provision.py" not in sd and "unrelated.py" not in sd, (sd, sorted(sf)))
        check("G: derivation self-test — a module loaded through _sibling('x'), and the typed-call language readers "
              "its LanguageSpec registry names (positional and reader=), are scanned (their knobs found)",
              {"typedreg.py", "readpos.py", "readkw.py"} <= set(sd)
              and {"DEEPMAP_READERKNOB", "DEEPMAP_READERKNOB2"} <= set(sf), (sd, sorted(sf)))
        check("G: ... (the spec's fixed module list would not have scanned them)",
              "newpipe.py" not in _PIPELINE and "newpipe2.py" not in _PIPELINE)
    finally:
        shutil.rmtree(str(synth), ignore_errors=True)   # exactly the temp dir this check created
    # the real registry: every reader module typed_calls.LANGUAGES names that exists in build/ is scanned (whatever
    # way its LanguageSpec spells it — the AST rule above, cross-checked against the loaded registry)
    try:
        tc = ST._lazy("typed_calls")
        readers = sorted({str(s.reader) for s in getattr(tc, "LANGUAGES", {}).values()})
    except Exception as exc:  # noqa: BLE001
        readers = None
        print(f"  (typed_calls not loadable: {exc!r})")
    present = sorted(f"{r}.py" for r in (readers or ()) if (build / f"{r}.py").is_file())
    check("G: every typed-call reader module typed_calls.LANGUAGES names that exists in build/ is scanned "
          f"({', '.join(present) or 'none present'})",
          readers is not None and bool(present) and set(present) <= set(derived),
          {"not scanned": sorted(set(present) - set(derived)), "readers": readers})
    found = _deepmap_literals(build, derived)
    sets = {"GRAPH_SHAPING_ENV": set(ST.GRAPH_SHAPING_ENV), "NON_SHAPING_ENV": set(ST.NON_SHAPING_ENV),
            "INTERNAL_ENV": set(ST.INTERNAL_ENV), "ROUTE_ENV": set(ST.ROUTE_ENV)}
    bad = {k: [s for s, v in sets.items() if k in v] for k in found}
    unclassified = sorted(k for k, v in bad.items() if len(v) == 0)
    doubled = sorted(k for k, v in bad.items() if len(v) > 1)
    check(f"G: {len(found)} DEEPMAP_* names found in {len(derived)} modules", len(found) >= 30, sorted(found))
    check("G: none unclassified (a new knob must be put in exactly one set)", not unclassified,
          {k: sorted(found[k]) for k in unclassified})
    check("G: none in two sets", not doubled, doubled)
    allnames = [n for v in sets.values() for n in v]
    check("G: the four sets are disjoint", len(allnames) == len(set(allnames)))
    for knob in ("DEEPMAP_EXTRACTOR", "DEEPMAP_PRECISE_CALLS", "DEEPMAP_RECOGNIZERS", "DEEPMAP_EMBED_THREADS",
                 "DEEPMAP_PRECISE_FIXES", "DEEPMAP_VECTOR_QUANTIZATION", "DEEPMAP_EXCLUDE_TESTS",
                 # typed-receiver call linking (slice S2) and the D0 cross-family filter
                 "DEEPMAP_TYPED_CALLS", "DEEPMAP_CALLS_AUDIT", "DEEPMAP_TYPED_CALLS_SHAPES",
                 "DEEPMAP_TYPED_CALLS_REPLACE", "DEEPMAP_TYPED_CALLS_TESTS", "DEEPMAP_TYPED_CALLS_NONPROD",
                 "DEEPMAP_TYPED_CALLS_MAX_FILES", "DEEPMAP_CALLS_FAMILY_FILTER",
                 # the C# reader's interface-dispatch modes (read only for a language with iface_dispatch)
                 "DEEPMAP_TYPED_CALLS_IFACE",
                 # the C# property / constructor supplement (graphify_extract.csharp_members_enabled, default ON)
                 "DEEPMAP_CSHARP_MEMBERS"):
        check(f"G: {knob} shapes the graph", knob in sets["GRAPH_SHAPING_ENV"])
    check("G: DEEPMAP_CSHARP_MEMBERS is a default-ON knob (canonical 'on'; only an OFF word turns it off)",
          "DEEPMAP_CSHARP_MEMBERS" in ST._DEFAULT_ON_KNOBS and ST.GRAPH_SHAPING_ENV.get("DEEPMAP_CSHARP_MEMBERS") == "on")
    # DEEPMAP_TYPED_CALLS_IFACE through typed_calls._iface_modes (review CR-3): an ON word selects every mode like
    # unset (recorded explicit), an unknown token is ignored, a value naming no mode selects every mode, an OFF word
    # selects none — so `=1` or a typo never records (or builds) "off"
    iface = "DEEPMAP_TYPED_CALLS_IFACE"
    rows = ((None, ""), ("  ", ""), ("1", "impl,static"), (" ON ", "impl,static"), ("yes", "impl,static"),
            ("true", "impl,static"), ("statc", "impl,static"), ("static,statc", "static"), ("impl", "impl"),
            ("impl,static", "impl,static"), ("0", "off"), ("off", "off"), ("No", "off"))
    bad = [(raw, ST.canonical_env_value(iface, raw), want) for raw, want in rows
           if ST.canonical_env_value(iface, raw) != want]
    check("G: DEEPMAP_TYPED_CALLS_IFACE canonical values: ON word / unknown-only value = every mode, a known mode "
          "beside a typo = that mode, OFF word = off", not bad, bad)
    rt = [(raw, ST.canonical_env_value(iface, ST.canonical_env_value(iface, raw))) for raw, _w in rows
          if raw is not None and raw.strip()
          and ST.canonical_env_value(iface, ST.canonical_env_value(iface, raw)) != ST.canonical_env_value(iface, raw)]
    check("G: ... every recorded DEEPMAP_TYPED_CALLS_IFACE value reads back to itself", not rt, rt)
    check("G: ... DEEPMAP_TYPED_CALLS_IFACE=1 does not conflict with a graph built with it unset (both select every "
          "mode); =0 does",
          not ST.recipe_conflicts({}, {iface: "1"}) and not ST.recipe_conflicts({}, {iface: "statc"})
          and bool(ST.recipe_conflicts({}, {iface: "0"})),
          (ST.recipe_conflicts({}, {iface: "1"}), ST.recipe_conflicts({}, {iface: "0"})))
    check("G: DEEPMAP_HOME is a route knob (recorded as deepmap_home), not 'non-shaping'",
          "DEEPMAP_HOME" in sets["ROUTE_ENV"])


# =====================================================================================================
# H — canonical values, recipe_env, recipe_conflicts, replay_env
# =====================================================================================================
# ---- the typed-receiver call-linking knobs (slice S2) + DEEPMAP_PRECISE_CALLS through its own reader -----------------
# A set-valued knob's canonical value is "" ONLY when it is unset (= the code's default set at build time). An
# EXPLICIT value's canonical value is what it selects — "off" for nothing, "on" for PRECISE's every-file mode, else
# the selected names sorted — even when that equals today's default (S2 review r1 finding 5: an explicit opt-out
# must survive a later default flip). The SELECTED sets below are spelled out; none depends on the default.
def _render(chosen, everything=None) -> str:
    chosen = frozenset(chosen)
    if not chosen:
        return "off"
    if everything is not None and everything in chosen:
        return "on"
    return ",".join(sorted(chosen))


def _precise_render(ST, chosen) -> str:
    gx = ST._lazy("graphify_extract")
    return _render(chosen, getattr(gx, "_PRECISE_ALL", "*"))


def _typed_knob_table(ST) -> list:
    wave1 = {"python", "typescript", "javascript"}
    return [
        # S1 review finding 6: PRECISE through graphify_extract.precise_calls_families (a typo selects nothing)
        ("DEEPMAP_PRECISE_CALLS", "0", "off"), ("DEEPMAP_PRECISE_CALLS", " No ", "off"),
        ("DEEPMAP_PRECISE_CALLS", "python,pyhton", _precise_render(ST, {"python"})),
        ("DEEPMAP_PRECISE_CALLS", "all", "off"), ("DEEPMAP_PRECISE_CALLS", "  ", ""),
        ("DEEPMAP_TYPED_CALLS", None, ""), ("DEEPMAP_TYPED_CALLS", "  ", ""),
        ("DEEPMAP_TYPED_CALLS", "1", _render(wave1)), ("DEEPMAP_TYPED_CALLS", "ON", _render(wave1)),
        ("DEEPMAP_TYPED_CALLS", "0", "off"), ("DEEPMAP_TYPED_CALLS", "false", "off"),
        ("DEEPMAP_TYPED_CALLS", " Python, ts", "python"),
        ("DEEPMAP_TYPED_CALLS", "typescript,python,typescript", "python,typescript"),
        ("DEEPMAP_TYPED_CALLS", "bogus", "off"),
        ("DEEPMAP_CALLS_AUDIT", None, ""), ("DEEPMAP_CALLS_AUDIT", "yes", _render(wave1)),
        ("DEEPMAP_CALLS_AUDIT", "0", "off"),
        ("DEEPMAP_CALLS_AUDIT", "javascript, TypeScript", "javascript,typescript"),
        ("DEEPMAP_TYPED_CALLS_SHAPES", None, ""), ("DEEPMAP_TYPED_CALLS_SHAPES", "k, m", "K,M"),
        # every shape, named explicitly: what unset selects today, yet recorded (explicit)
        ("DEEPMAP_TYPED_CALLS_SHAPES", "R1,R2,R3,R4,S,K,M,C", "C,K,M,R1,R2,R3,R4,S"),
        ("DEEPMAP_TYPED_CALLS_SHAPES", "0", "off"), ("DEEPMAP_TYPED_CALLS_SHAPES", "zz", "off"),
        ("DEEPMAP_TYPED_CALLS_MAX_FILES", None, "20000"), ("DEEPMAP_TYPED_CALLS_MAX_FILES", "500", "500"),
        ("DEEPMAP_TYPED_CALLS_MAX_FILES", " 0500 ", "500"), ("DEEPMAP_TYPED_CALLS_MAX_FILES", "0", "20000"),
        ("DEEPMAP_TYPED_CALLS_MAX_FILES", "-3", "20000"), ("DEEPMAP_TYPED_CALLS_MAX_FILES", "many", "20000"),
        ("DEEPMAP_TYPED_CALLS_REPLACE", None, "on"), ("DEEPMAP_TYPED_CALLS_REPLACE", "0", "off"),
        ("DEEPMAP_TYPED_CALLS_REPLACE", "maybe", "on"),
        ("DEEPMAP_TYPED_CALLS_TESTS", None, "off"), ("DEEPMAP_TYPED_CALLS_TESTS", "1", "on"),
        ("DEEPMAP_TYPED_CALLS_NONPROD", "Yes", "on"), ("DEEPMAP_TYPED_CALLS_NONPROD", "maybe", "off"),
        ("DEEPMAP_CALLS_FAMILY_FILTER", None, "off"), ("DEEPMAP_CALLS_FAMILY_FILTER", "on", "on"),
    ]


def _typed_knob_locks(ST) -> None:
    """The classification agrees with the readers that decide the graph (typed_calls._config, sync's family-filter
    reader) — two values with one canonical form build one graph, and every recorded value replays to itself."""
    tc = ST._lazy("typed_calls")
    cfg, registry = getattr(tc, "_config", None), getattr(tc, "LANGUAGES", None)
    if not callable(cfg) or registry is None:
        check("H: typed_calls (its _config / LANGUAGES) is importable from build/", False)
        return
    raws = (None, "", "1", "true", " YES ", "on", "0", "off", "no", "false", "maybe")
    bad = []
    for name, attr in (("DEEPMAP_TYPED_CALLS_REPLACE", "replace"), ("DEEPMAP_TYPED_CALLS_TESTS", "tests"),
                       ("DEEPMAP_TYPED_CALLS_NONPROD", "nonprod")):
        for raw in raws:
            graph_on = bool(getattr(cfg({} if raw is None else {name: raw}, registry), attr))
            if graph_on != (ST.canonical_env_value(name, raw) == "on"):
                bad.append((name, raw, graph_on))
    check("H: REPLACE / TESTS / NONPROD: canonical 'on' exactly where typed_calls' reader turns the switch on",
          not bad, bad)
    check("H: DEEPMAP_TYPED_CALLS_MAX_FILES' default == typed_calls.MAX_FILES_DEFAULT",
          ST.GRAPH_SHAPING_ENV.get("DEEPMAP_TYPED_CALLS_MAX_FILES") == str(getattr(tc, "MAX_FILES_DEFAULT", None)))
    pairs, unset_pairs, conflicts = [], [], []
    gx = ST._lazy("graphify_extract")
    for name, picks in (("DEEPMAP_TYPED_CALLS", ("python", "1", "0", "no", "typescript,javascript", "bogus")),
                        ("DEEPMAP_CALLS_AUDIT", ("javascript", "on", "off", "0")),
                        ("DEEPMAP_TYPED_CALLS_SHAPES", ("K,M", "m, k", "R1", "0", "R1,R2,R3,R4,S,K,M,C")),
                        ("DEEPMAP_PRECISE_CALLS", ("1", "0", "off", "python", "python,pyhton", "all"))):
        fn = {"DEEPMAP_TYPED_CALLS": lambda v: frozenset(tc.typed_langs({"DEEPMAP_TYPED_CALLS": v})),
              "DEEPMAP_CALLS_AUDIT": lambda v: frozenset(tc.audit_langs({"DEEPMAP_CALLS_AUDIT": v})),
              "DEEPMAP_TYPED_CALLS_SHAPES": lambda v: frozenset(cfg({"DEEPMAP_TYPED_CALLS_SHAPES": v},
                                                                    registry).shapes),
              "DEEPMAP_PRECISE_CALLS": lambda v: frozenset(gx.precise_calls_families(v))}[name]
        for a in picks:
            # an explicit value is never canonical "" (that is unset): it is recorded even when it selects what
            # unset selects today, so a later default flip never replays it as the new default
            if ST.canonical_env_value(name, a) in ("", None):
                unset_pairs.append((name, a))
            # ... and against a graph built with the knob unset it conflicts exactly when it selects another set
            if bool(ST.recipe_conflicts({}, {name: a})) != (fn(a) != fn("")):
                conflicts.append((name, a, "(unset build)", ST.recipe_conflicts({}, {name: a})))
            for b in picks:
                same_graph = fn(a) == fn(b)
                same_canon = ST.canonical_env_value(name, a) == ST.canonical_env_value(name, b)
                if same_graph != same_canon:
                    pairs.append((name, a, b, same_graph))
                # a graph built with b (recorded as recipe_env records it) vs a session exporting a
                built = ST.recipe_env({name: b})
                if bool(ST.recipe_conflicts(built, {name: a})) != (not same_graph):
                    conflicts.append((name, a, b, built))
    check("H: language / shape / PRECISE knobs — two EXPLICIT values share a canonical form exactly when the reader "
          "selects the same set with them", not pairs, pairs)
    check("H: ... and an explicit value is never canonical '' (unset): `=0` is recorded 'off' even while the default "
          "selects nothing", not unset_pairs, unset_pairs)
    check("H: ... recipe_conflicts compares what the two values SELECT: a session value conflicts with the recorded "
          "build (or an unset build) exactly when the reader selects another set", not conflicts, conflicts[:6])
    _flip_rows(ST, tc, gx)
    _fallback_rows(ST)
    replay = [(n, v, c) for n in ("DEEPMAP_TYPED_CALLS", "DEEPMAP_CALLS_AUDIT", "DEEPMAP_TYPED_CALLS_SHAPES",
                                  "DEEPMAP_TYPED_CALLS_MAX_FILES", "DEEPMAP_PRECISE_CALLS")
              for v in ("1", "python", "k,m", "0", "77", "js, python") for c in [ST.canonical_env_value(n, v)]
              if c and ST.canonical_env_value(n, c) != c]
    check("H: every typed-knob / PRECISE canonical value reads back to itself (replay_env)", not replay, replay)


def _flip_rows(ST, tc, gx) -> None:
    """S2 review r1 finding 5: a recipe recorded BEFORE a default flip (slice S8) still rebuilds the graph the user
    built after it. The flip is simulated by patching the product's default sets (typed_calls._TYPED_DEFAULT_LANGS /
    _AUDIT_DEFAULT_LANGS, graphify_extract._PRECISE_DEFAULT_FAMILIES)."""
    knobs = ("DEEPMAP_TYPED_CALLS", "DEEPMAP_CALLS_AUDIT", "DEEPMAP_PRECISE_CALLS")
    before = ST.recipe_env({"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "no", "DEEPMAP_PRECISE_CALLS": "off"})
    check("H flip: before the flip (every default set empty) an explicit opt-out is recorded 'off' for each knob",
          {k: before.get(k) for k in knobs} == {k: "off" for k in knobs}, before)
    chose = ST.recipe_env({"DEEPMAP_TYPED_CALLS": "python"})
    unset = ST.recipe_env({})
    flipped = frozenset({"python"})
    with _patched(tc, _TYPED_DEFAULT_LANGS=flipped, _AUDIT_DEFAULT_LANGS=flipped), \
            _patched(gx, _PRECISE_DEFAULT_FAMILIES=flipped):
        check("H flip: the simulated flip took (unset now selects python for each knob)",
              tc.typed_langs({}) == flipped and tc.audit_langs({}) == flipped
              and gx.precise_calls_families("") == flipped)
        env: dict = {}
        done = ST.replay_env(before, env)
        got = (tc.typed_langs(env), tc.audit_langs(env), gx.precise_calls_families(env.get("DEEPMAP_PRECISE_CALLS")))
        check("H flip: the background refresh (session leaves the knobs unset) replays the opt-out — the rebuilt graph "
              "selects NOTHING, never the new default",
              sorted(done) == sorted(knobs) and got == (frozenset(), frozenset(), frozenset()), (done, env, got))
        still = ST.recipe_conflicts(before, {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "false",
                                             "DEEPMAP_PRECISE_CALLS": "0"})
        check("H flip: ... a session that still exports the opt-out (any OFF spelling) is no conflict", still == [],
              still)
        moved = ST.recipe_conflicts(before, {"DEEPMAP_TYPED_CALLS": "python"})
        check("H flip: ... a session that now selects python is a conflict naming both values",
              moved == [("DEEPMAP_TYPED_CALLS", "off", "python")], moved)
        env2: dict = {}
        ST.replay_env(chose, env2)
        check("H flip: an explicit selection recorded before the flip replays to the same selection after it",
              chose == {"DEEPMAP_TYPED_CALLS": "python"} and tc.typed_langs(env2) == flipped, (chose, env2))
        env3: dict = {}
        none = ST.replay_env(unset, env3)
        check("H flip: a graph built with the knobs unset replays nothing (the refresh takes the new default) and "
              "is no conflict with a session that leaves them unset",
              not any(k in unset for k in knobs) and not any(k in none for k in knobs)
              and ST.recipe_conflicts(unset, {}) == [] and tc.typed_langs(env3) == flipped, (unset, none))
        check("H flip: after the flip an explicit default-equal value is still recorded (absolute), and an explicit "
              "OFF value too", ST.recipe_env({"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_PRECISE_CALLS": "0"})
              == {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_PRECISE_CALLS": "off"})


def _fallback_rows(ST) -> None:
    """S2 review r1 finding 3: when the product's reader cannot be loaded (ST._lazy gives None for typed_calls and
    graphify_extract), a set-valued knob is kept as spelled, never judged a conflict, and still replays to what it
    selected; a recipe recorded on the reader path never reads as a conflict there."""
    real = ST._lazy

    def no_readers(name):
        return None if name in ("typed_calls", "graphify_extract") else real(name)
    reader_rec = ST.recipe_env({"DEEPMAP_TYPED_CALLS": "1", "DEEPMAP_PRECISE_CALLS": "JS, python"})
    with _patched(ST, _lazy=no_readers):
        cv = ST.canonical_env_value
        spelled = {v: cv("DEEPMAP_TYPED_CALLS", v) for v in (" 1 ", "Python", "0", "bogus")}
        spelled["PRECISE JS, python"] = cv("DEEPMAP_PRECISE_CALLS", "JS, python")
        spelled["SHAPES k"] = cv("DEEPMAP_TYPED_CALLS_SHAPES", "k")
        check("H fallback: no reader -> an explicit set-knob value is kept as spelled (stripped); unset stays ''",
              spelled == {" 1 ": "1", "Python": "Python", "0": "0", "bogus": "bogus",
                          "PRECISE JS, python": "JS, python", "SHAPES k": "k"}
              and cv("DEEPMAP_TYPED_CALLS", None) == "" and cv("DEEPMAP_PRECISE_CALLS", "  ") == "", spelled)
        rc = ST.recipe_conflicts
        judged = {
            "reader-path recipe vs the same session value": rc(reader_rec, {"DEEPMAP_TYPED_CALLS": "1",
                                                                             "DEEPMAP_PRECISE_CALLS": "python,js"}),
            "reader-path recipe vs another session value": rc(reader_rec, {"DEEPMAP_TYPED_CALLS": "0"}),
            "an unset build vs an explicit value": rc({}, {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_CALLS_AUDIT": "on",
                                                         "DEEPMAP_TYPED_CALLS_SHAPES": "K"}),
        }
        check("H fallback: no reader -> recipe_conflicts never calls a set-valued knob a conflict (it cannot judge; "
              "a sync without that reader cannot apply the knob either)", all(v == [] for v in judged.values()),
              judged)
        other = rc({}, {"DEEPMAP_EXCLUDE_TESTS": "1", "DEEPMAP_TYPED_CALLS_MAX_FILES": "500"})
        check("H fallback: ... while the other knobs are judged as ever (stdlib rules)",
              other == [("DEEPMAP_EXCLUDE_TESTS", "off", "on"), ("DEEPMAP_TYPED_CALLS_MAX_FILES", "20000", "500")],
              other)
        fb_rec = ST.recipe_env({"DEEPMAP_TYPED_CALLS": "Python", "DEEPMAP_PRECISE_CALLS": "off"})
    tc = ST._lazy("typed_calls")
    gx = ST._lazy("graphify_extract")
    env: dict = {}
    ST.replay_env(fb_rec, env)
    check("H fallback: a value recorded without the reader replays to what it selected once the reader is back, and "
          "is no conflict with the same selection spelled the reader's way",
          fb_rec == {"DEEPMAP_TYPED_CALLS": "Python", "DEEPMAP_PRECISE_CALLS": "off"}
          and tc.typed_langs(env) == frozenset({"python"}) and gx.precise_calls_families(env["DEEPMAP_PRECISE_CALLS"])
          == frozenset() and ST.recipe_conflicts(fb_rec, {"DEEPMAP_TYPED_CALLS": "python",
                                                          "DEEPMAP_PRECISE_CALLS": "0"}) == [], (fb_rec, env))


def gate_h_tables(ST) -> None:
    print("\n-- H: canonical_env_value / recipe_env / recipe_conflicts / replay_env --")
    cv = ST.canonical_env_value
    table = [
        ("DEEPMAP_EXTRACTOR", None, "auto"), ("DEEPMAP_EXTRACTOR", "  ", "auto"), ("DEEPMAP_EXTRACTOR", " AST ", "ast"),
        ("DEEPMAP_EXCLUDE_TESTS", None, "off"), ("DEEPMAP_EXCLUDE_TESTS", "1", "on"),
        ("DEEPMAP_EXCLUDE_TESTS", "Yes", "on"), ("DEEPMAP_EXCLUDE_TESTS", "0", "off"),
        ("DEEPMAP_EXCLUDE_TESTS", "maybe", "off"),
        ("DEEPMAP_FALSE_CALL_FILTER", None, "on"), ("DEEPMAP_FALSE_CALL_FILTER", "0", "off"),
        ("DEEPMAP_FALSE_CALL_FILTER", "OFF", "off"), ("DEEPMAP_FALSE_CALL_FILTER", "whatever", "on"),
        ("DEEPMAP_VECTOR_QUANTIZATION", "false", "off"), ("DEEPMAP_PRECISE_FIXES", "no", "off"),
        ("DEEPMAP_RECOGNIZERS", None, ""), ("DEEPMAP_RECOGNIZERS", "all", ""), ("DEEPMAP_RECOGNIZERS", "1", ""),
        ("DEEPMAP_RECOGNIZERS", "off", "off"), ("DEEPMAP_RECOGNIZERS", " Ruby, python ,ruby", "python,ruby"),
        ("DEEPMAP_RECOGNIZERS", ",", "off"),
        ("DEEPMAP_PRECISE_CALLS", None, ""), ("DEEPMAP_PRECISE_CALLS", "1", "on"),
        ("DEEPMAP_PRECISE_CALLS", "JS, python", "js,python"),
        ("DEEPMAP_EMBED_THREADS", None, "8"), ("DEEPMAP_EMBED_THREADS", "4", "4"),
        ("DEEPMAP_EMBED_THREADS", "0004", "4"), ("DEEPMAP_EMBED_THREADS", "999", "64"),
        ("DEEPMAP_EMBED_THREADS", "12345", "64"), ("DEEPMAP_EMBED_THREADS", "0", "8"),
        ("DEEPMAP_EMBED_THREADS", "Auto", "auto"), ("DEEPMAP_EMBED_THREADS", "-3", "8"),
    ]
    table += _typed_knob_table(ST)
    bad = [(n, raw, cv(n, raw), want) for n, raw, want in table if cv(n, raw) != want]
    check(f"H: canonical_env_value over {len(table)} cases", not bad, bad)
    _typed_knob_locks(ST)
    check("H: recipe_env records only non-default knobs", ST.recipe_env(
        {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_EXCLUDE_TESTS": "0", "DEEPMAP_EXTRACTOR": "auto",
         "DEEPMAP_EMBED_THREADS": "8", "DEEPMAP_FALSE_CALL_FILTER": "0", "DEEPMAP_AUTO_SYNC": "0"})
          == {"DEEPMAP_PRECISE_CALLS": "on", "DEEPMAP_FALSE_CALL_FILTER": "off"})
    rc = ST.recipe_conflicts
    check("H: legacy manifest (no recipe) + an explicit non-default knob -> a conflict",
          rc({}, {"DEEPMAP_PRECISE_CALLS": "1"}) == [("DEEPMAP_PRECISE_CALLS", "", "on")],
          rc({}, {"DEEPMAP_PRECISE_CALLS": "1"}))
    check("H: legacy manifest + an explicit DEFAULT-equal knob -> no conflict",
          rc({}, {"DEEPMAP_EXCLUDE_TESTS": "0", "DEEPMAP_EXTRACTOR": "AUTO", "DEEPMAP_EMBED_THREADS": "8"}) == [])
    check("H: recorded 'on' + the env unset -> no conflict (replay supplies it)",
          rc({"DEEPMAP_PRECISE_CALLS": "on"}, {}) == [])
    check("H: recorded 'on' + the env blank -> no conflict (blank = unset)",
          rc({"DEEPMAP_PRECISE_CALLS": "on"}, {"DEEPMAP_PRECISE_CALLS": "  "}) == [])
    check("H: recorded 'on' + the env explicitly 'true' (same canonical) -> no conflict",
          rc({"DEEPMAP_PRECISE_CALLS": "on"}, {"DEEPMAP_PRECISE_CALLS": "true"}) == [])
    check("H: recorded 'on' + the env explicitly '0' -> a conflict naming both values",
          rc({"DEEPMAP_PRECISE_CALLS": "on"}, {"DEEPMAP_PRECISE_CALLS": "0"}) == [("DEEPMAP_PRECISE_CALLS", "on",
                                                                                  _precise_render(ST, set()))])
    check("H: a non-shaping knob never conflicts", rc({}, {"DEEPMAP_AUTO_TUNE": "1", "DEEPMAP_HOOKS": "0"}) == [])
    env = {"DEEPMAP_EXTRACTOR": "graphify", "DEEPMAP_RECOGNIZERS": ""}
    done = ST.replay_env({"DEEPMAP_PRECISE_CALLS": "on", "DEEPMAP_EXTRACTOR": "ast", "DEEPMAP_RECOGNIZERS": "off",
                          "NOT_A_KNOB": "x", "DEEPMAP_AUTO_SYNC": "1"}, env)
    check("H: replay_env sets recorded knobs the env leaves unset or blank, never one it sets, never a non-knob",
          sorted(done) == ["DEEPMAP_PRECISE_CALLS", "DEEPMAP_RECOGNIZERS"] and env == {
              "DEEPMAP_EXTRACTOR": "graphify", "DEEPMAP_RECOGNIZERS": "off", "DEEPMAP_PRECISE_CALLS": "on"},
          (done, env))
    check("H: a replayed canonical value reads back to itself",
          all(ST.canonical_env_value(k, v) == v for k, v in env.items()))


# =====================================================================================================
# I — model_cache_state
# =====================================================================================================
def gate_i_model(ST, base: Path) -> None:
    print("\n-- I: model_cache_state on a fake HF_HUB_CACHE --")
    import importlib
    emb = importlib.import_module("embeddings")
    db = importlib.import_module("deps_bootstrap")
    model = emb.MODEL_NAME

    def mk(case, *, rev="0123abcd", drop=(), modules=None, refs=True):
        hub = base / f"hub_{case}"
        mdir = hub / ("models--" + model.replace("/", "--"))
        snap = mdir / "snapshots" / rev
        snap.mkdir(parents=True)
        if refs:
            (mdir / "refs").mkdir()
            (mdir / "refs" / "main").write_text(rev, encoding="utf-8")
        mods = modules if modules is not None else [
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
            {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
            {"idx": 2, "name": "2", "path": "2_Normalize", "type": "sentence_transformers.models.Normalize"}]
        for f in db.MODEL_KEY_FILES:
            if f in drop:
                continue
            (snap / f).write_text(json.dumps(mods) if f == "modules.json" else "{}", encoding="utf-8")
        for m in mods:
            if m.get("path") and not str(m.get("type")).endswith("Normalize") and f"{m['path']}/config.json" not in drop:
                (snap / m["path"]).mkdir(parents=True, exist_ok=True)
                (snap / m["path"] / "config.json").write_text("{}", encoding="utf-8")
        return {"HF_HUB_CACHE": str(hub)}

    st = ST.model_cache_state(mk("complete"))
    check("I: complete cache -> complete, revision = the snapshot dir, nothing missing",
          st.get("complete") is True and st.get("revision") == "0123abcd" and st.get("missing") == [], st)
    st = ST.model_cache_state(mk("noweights", drop=("model.safetensors",)))
    check("I: missing weights -> incomplete, names model.safetensors",
          st.get("complete") is False and "model.safetensors" in st.get("missing", []), st)
    st = ST.model_cache_state(mk("nomodules", drop=("modules.json",)))
    check("I: missing modules.json -> incomplete, names modules.json",
          st.get("complete") is False and "modules.json" in st.get("missing", []), st)
    st = ST.model_cache_state(mk("nopool", drop=("1_Pooling/config.json",)))
    check("I: a module's missing config (1_Pooling) -> incomplete, names it",
          st.get("complete") is False and "1_Pooling/config.json" in st.get("missing", []), st)
    st = ST.model_cache_state(mk("norefs", refs=False))
    check("I: no refs/main -> incomplete, revision None", st.get("complete") is False and st.get("revision") is None,
          st)
    st = ST.model_cache_state({"HF_HUB_CACHE": str(base / "hub_absent")})
    check("I: no cache at all -> incomplete, never raises", st.get("complete") is False, st)
    check("I: Normalize needs no config (complete case had none)",
          not (base / "hub_complete" / ("models--" + model.replace("/", "--")) / "snapshots" / "0123abcd"
               / "2_Normalize" / "config.json").exists())


# =====================================================================================================
# J — GIT_OPTIONAL_LOCKS=0 on every git subprocess sync.py starts
# =====================================================================================================
def gate_j_git_env(S, base: Path) -> None:
    print("\n-- J: every git call sync.py makes runs with GIT_OPTIONAL_LOCKS=0 --")
    if not _GIT:
        check("J: git is installed", False)
        return
    repo = _project(base, "j", git=True)
    (repo / "m.py").write_text("def f():\n    return 2\n", encoding="utf-8")       # dirty -> hash-object runs
    (repo / "graphify-out").mkdir()
    rec = []
    real_run = subprocess.run

    def run(argv, *a, **k):
        if argv and "git" in Path(str(argv[0])).name.lower():
            rec.append((list(argv), (k.get("env") or {}).get("GIT_OPTIONAL_LOCKS"), "env" in k))
        return real_run(argv, *a, **k)

    proxy = SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired, PIPE=subprocess.PIPE,
                            DEVNULL=subprocess.DEVNULL)
    with _env(GIT_OPTIONAL_LOCKS=None), _patched(S, subprocess=proxy):
        S._git_head_sha(repo)
        S._git_worktree_changes(repo)
        S._dir_has_tracked_files(repo / "graphify-out")
        S._project_identity(repo)
        fn = getattr(S, "source_fingerprint", None)
        if fn:
            fn(repo)
        fn = getattr(S, "git_busy", None)
        if fn:
            fn(repo)
        S._source_state(repo)
    subs = sorted({" ".join(a[a.index("-C") + 2:a.index("-C") + 3] if "-C" in a else a[1:2]) for a, _v, _e in rec})
    check("J: the probes ran git (rev-parse, status, ls-files, hash-object ...)", len(rec) >= 8, subs)
    bad = [(" ".join(a[-6:]), v) for a, v, _e in rec if v != "0"]
    check("J: every one of them ran with GIT_OPTIONAL_LOCKS=0", not bad, bad[:6])
    names = {x for a, _v, _e in rec for x in a}
    check("J: ... including the fingerprint's ls-files and hash-object and git_busy's --git-path",
          {"ls-files", "hash-object", "--git-path"} <= names, sorted(n for n in names if not os.path.isabs(n))[:30])


# =====================================================================================================
# K — import hygiene
# =====================================================================================================
def gate_k_imports(build: Path) -> None:
    print("\n-- K: sync_status is stdlib-only at module level and pulls in nothing heavy --")
    p = build / "sync_status.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    tops = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            tops += [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            tops.append(n.module.split(".")[0])
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    nonstd = sorted(t for t in tops if std and t not in std)
    check("K: every module-level import is the standard library", not nonstd and bool(tops), (tops, nonstd))
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import sync_status as s; "
            "s.process_identity(); s.read_running(sys.argv[2]); s.live_preempts(sys.argv[2]); "
            "print(json.dumps(sorted(m for m in ('sync', 'neo4j', 'torch', 'graphify_extract', 'sentence_transformers') "
            "if m in sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code, str(build), str(build)], capture_output=True, text=True,
                         timeout=120)
    try:
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        loaded = None
    check("K: importing and using it loads neither sync, neo4j, torch nor graphify_extract", loaded == [],
          (loaded, out.stderr[-300:]))


# =====================================================================================================
# L — the source fingerprint (real git)
# =====================================================================================================
def gate_l_fingerprint(S, base: Path) -> None:
    print("\n-- L: source_fingerprint / git_busy (real git, temp repos) --")
    fp_fn = getattr(S, "source_fingerprint", None)
    busy_fn = getattr(S, "git_busy", None)
    check("L: sync.source_fingerprint and sync.git_busy exist", callable(fp_fn) and callable(busy_fn))
    if not (callable(fp_fn) and callable(busy_fn)) or not _GIT:
        return

    def fpv(root):
        return fp_fn(root).get("value")

    repo = _init_repo(base / "l1")
    (repo / "app").mkdir()
    (repo / "app" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# r\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _commit_all(repo, "init")
    f0 = fp_fn(repo)
    check("L: a git repo -> kind git, a 32-hex value, HEAD recorded",
          f0.get("kind") == "git" and isinstance(f0.get("value"), str) and len(f0["value"]) == 32
          and f0.get("head") == _git(repo, "rev-parse", "HEAD").stdout.strip(), f0)
    (repo / "README.md").write_text("# r\nmore docs\n", encoding="utf-8")
    _commit_all(repo, "docs")
    check("L: a docs-only commit (HEAD moved) -> equal", fpv(repo) == f0["value"])
    (repo / "app" / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    dirty = fpv(repo)
    check("L: an uncommitted source edit -> different", dirty != f0["value"] and dirty is not None)
    _commit_all(repo, "src")
    check("L: committing the dirty work the last fingerprint saw -> equal", fpv(repo) == dirty)
    f1 = fpv(repo)
    (repo / "newdir" / "deep").mkdir(parents=True)
    (repo / "newdir" / "deep" / "n.py").write_text("x = 1\n", encoding="utf-8")
    check("L: a new file inside a new untracked directory -> different", fpv(repo) != f1)
    shutil.rmtree(repo / "newdir")
    check("L: ... and removing it again -> equal", fpv(repo) == f1)
    (repo / "notes").mkdir()
    (repo / "notes" / "todo.txt").write_text("x\n", encoding="utf-8")
    check("L: a new untracked dir of non-source files -> equal", fpv(repo) == f1)
    (repo / "app" / "a.py").unlink()
    check("L: a deleted tracked source -> different", fpv(repo) != f1)
    _git(repo, "checkout", "--", "app/a.py")
    check("L: ... restored -> equal", fpv(repo) == f1)
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    check("L: a .gitignore change -> different", fpv(repo) != f1)
    _git(repo, "checkout", "--", ".gitignore")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("module.exports = 1\n", encoding="utf-8")
    check("L: node_modules/x.js -> equal (the vendor filter)", fpv(repo) == f1)
    (repo / ".deepmap").mkdir()
    (repo / ".deepmap" / "junk.py").write_text("x = 1\n", encoding="utf-8")
    home = repo / "dmhome"
    (home / "gout").mkdir(parents=True)
    (home / "gout" / "g.py").write_text("x = 1\n", encoding="utf-8")
    with _env(DEEPMAP_HOME=str(home)):
        check("L: .deepmap/ and an in-tree DEEPMAP_HOME -> equal", fpv(repo) == f1)
    # a subdirectory sync root: a commit outside it changes nothing
    sub = repo / "app"
    s0 = fpv(sub)
    (repo / "other.py").write_text("def o():\n    return 1\n", encoding="utf-8")
    _commit_all(repo, "outside")
    check("L: sync root = a subdir, a commit outside it -> equal", fpv(sub) == s0 and s0 is not None)
    (sub / "a.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    check("L: ... an edit inside it -> different", fpv(sub) != s0)
    # core.autocrlf=true and a CRLF working file: the fingerprint of the dirty file equals its committed blob
    crlf = _init_repo(base / "l_crlf", autocrlf="true")
    (crlf / "c.py").write_bytes(b"def c():\n    return 1\n")
    _commit_all(crlf, "init")
    (crlf / "c.py").write_bytes(b"def c():\r\n    return 2\r\n")
    seen = fpv(crlf)
    _commit_all(crlf, "crlf work")
    check("L: core.autocrlf=true, a CRLF working file: committing the work the fingerprint saw -> equal",
          seen is not None and fpv(crlf) == seen, (seen, fpv(crlf)))
    # git_busy markers
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    for marker, op, is_dir in (("rebase-merge", "rebase", True), ("rebase-apply", "rebase", True),
                               ("MERGE_HEAD", "merge", False), ("CHERRY_PICK_HEAD", "cherry-pick", False),
                               ("REVERT_HEAD", "revert", False), ("BISECT_LOG", "bisect", False),
                               ("sequencer", "sequence", True)):
        p = gitdir / marker
        if is_dir:
            p.mkdir()
        else:
            p.write_text("0" * 40 + "\n", encoding="utf-8")
        got, fp = busy_fn(repo), fp_fn(repo)
        sub_got = busy_fn(sub)
        if is_dir:
            shutil.rmtree(p)
        else:
            p.unlink()
        check(f"L: git_busy with {marker} -> {op!r} (also from a subdir); the fingerprint is unknown",
              got == op and sub_got == op and fp.get("git_busy") == op and fp.get("value") is None,
              (got, sub_got, fp))
    check("L: no marker -> git_busy None", busy_fn(repo) is None)
    nogit = base / "l_nogit"
    nogit.mkdir()
    check("L: git_busy on a non-git directory -> None", busy_fn(nogit) is None)
    # a real conflicted merge
    mr = _init_repo(base / "l_merge")
    (mr / "x.py").write_text("v = 0\n", encoding="utf-8")
    _commit_all(mr, "base")
    base_branch = _git(mr, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(mr, "checkout", "-q", "-b", "side")
    (mr / "x.py").write_text("v = 1\n", encoding="utf-8")
    _commit_all(mr, "side")
    _git(mr, "checkout", "-q", base_branch)
    (mr / "x.py").write_text("v = 2\n", encoding="utf-8")
    _commit_all(mr, "main")
    merged = _git(mr, "merge", "side")
    fpm = fp_fn(mr)
    check("L: a real conflicted merge -> value None, git_busy merge", merged.returncode != 0
          and fpm.get("value") is None and fpm.get("git_busy") in ("merge", "unmerged"), (merged.stdout[-200:], fpm))
    mgd = Path(_git(mr, "rev-parse", "--absolute-git-dir").stdout.strip())
    for name in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE"):
        try:
            (mgd / name).unlink()
        except OSError:
            pass
    fpu = fp_fn(mr)
    check("L: ... with MERGE_HEAD gone, the unmerged index entries alone -> git_busy 'unmerged', value None",
          fpu.get("git_busy") == "unmerged" and fpu.get("value") is None and fpu.get("unmerged", 0) > 0, fpu)
    # non-git
    ng = base / "l_fs"
    ng.mkdir()
    (ng / "s.py").write_text("def s():\n    return 1\n", encoding="utf-8")
    (ng / "notes.md").write_text("n\n", encoding="utf-8")
    g0 = fp_fn(ng)
    check("L: a non-git tree -> kind fs, a value", g0.get("kind") == "fs" and isinstance(g0.get("value"), str), g0)
    (ng / "notes.md").write_text("n2\n", encoding="utf-8")
    check("L: non-git: a non-source edit -> equal", fpv(ng) == g0.get("value"))
    (ng / "s.py").write_text("def s():\n    return 2\n", encoding="utf-8")
    check("L: non-git: a source edit -> different", fpv(ng) != g0.get("value"))
    # captured_epoch precedes the first git call
    times = []
    real_run = subprocess.run

    def run(argv, *a, **k):
        if argv and "git" in Path(str(argv[0])).name.lower():
            times.append(time.time())
        return real_run(argv, *a, **k)

    proxy = SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired, PIPE=subprocess.PIPE,
                            DEVNULL=subprocess.DEVNULL)
    with _patched(S, subprocess=proxy):
        state = S._source_state(repo)
    check("L: _source_state.captured_epoch <= its first git call's time, captured_utc set, fingerprint inside",
          bool(times) and isinstance(state.get("captured_epoch"), float) and state["captured_epoch"] <= times[0]
          and isinstance(state.get("captured_utc"), str) and isinstance(state.get("fingerprint"), dict),
          (times[:1], {k: state.get(k) for k in ("captured_epoch", "captured_utc")}))


def gate_l2_inputs(S, build: Path, base: Path) -> None:
    print("\n-- L2: the fingerprint's inputs are at least what extraction reads --")
    fp_fn = getattr(S, "source_fingerprint", None)
    if not callable(fp_fn) or not _GIT:
        check("L2: sync.source_fingerprint exists and git is available", False)
        return

    def fpv(root):
        return fp_fn(root).get("value")

    repo = _init_repo(base / "l2_ts")
    (repo / "src").mkdir()
    (repo / "lib").mkdir()
    (repo / "src" / "a.ts").write_text("import { b } from '@app/b';\nexport const a = () => b();\n", encoding="utf-8")
    (repo / "src" / "b.ts").write_text("export const b = () => 1;\n", encoding="utf-8")
    (repo / "lib" / "b.ts").write_text("export const b = () => 2;\n", encoding="utf-8")
    (repo / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}\n',
                                        encoding="utf-8")
    (repo / "package.json").write_text('{"name": "app"}\n', encoding="utf-8")
    _commit_all(repo, "init")
    f0 = fpv(repo)
    head0 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["lib/*"]}}}\n',
                                        encoding="utf-8")
    dirty = S._git_worktree_changes(repo)
    check("L2: an uncommitted tsconfig.json edit is 'dirty' (it changes the typed edges)",
          dirty[0] is True and "tsconfig.json" in dirty[1], dirty)
    _commit_all(repo, "paths -> lib")
    f1 = fpv(repo)
    check("L2: a commit that changes only tsconfig.json paths -> different (it re-places the TypeScript imports)",
          f0 is not None and f1 is not None and f1 != f0
          and _git(repo, "rev-parse", "HEAD").stdout.strip() != head0, (f0, f1))
    for rel, text in (("package.json", '{"name": "app", "imports": {"#x": "./src/b.ts"}}\n'),
                      ("tsconfig.base.json", '{"compilerOptions": {"strict": true}}\n'),
                      (".graphifyignore", "lib/\n"), ("nest-cli.json", '{"projects": {}}\n')):
        before = fpv(repo)
        (repo / rel).write_text(text, encoding="utf-8")
        check(f"L2: {rel} changed or added -> different", fpv(repo) != before, rel)
        _commit_all(repo, rel)
    base_v = fpv(repo)
    (repo / ".vscode").mkdir()
    (repo / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")
    check("L2: editor settings (.vscode/settings.json) -> equal", fpv(repo) == base_v)
    (repo / ".deepmap-root").write_text("", encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check("L2: a root marker added in the sync root (.deepmap-root: prune safety) -> different, yet not 'dirty'",
          fpv(repo) != base_v and d[0] is False, (d,))
    (repo / ".deepmap-root").unlink()
    (repo / "src" / "pyproject.toml").write_text("[x]\n", encoding="utf-8")
    check("L2: a marker file in a subdirectory (not the sync root) -> equal", fpv(repo) == base_v)
    (repo / "src" / "pyproject.toml").unlink()
    # submodules (gitlinks) and nested repositories
    sup = _init_repo(base / "l2_sup")
    (sup / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    sub = _init_repo(sup / "libsub")
    (sub / "s.py").write_text("def s():\n    return 1\n", encoding="utf-8")
    _commit_all(sub, "sub init")
    _git(sup, "add", "app.py", "libsub")              # an embedded repository is added as a gitlink
    _git(sup, "commit", "-q", "--no-verify", "-m", "init")
    mode = _git(sup, "ls-files", "-s", "libsub").stdout.split()[:1]
    check("L2: setup: libsub is a gitlink (mode 160000) in the superproject", mode == ["160000"], mode)
    g0 = fpv(sup)
    check("L2: a clean submodule -> a value (not unknown)", isinstance(g0, str), fp_fn(sup))
    (sub / "s.py").write_text("def s():\n    return 2\n", encoding="utf-8")
    d = S._git_worktree_changes(sup)
    g1 = fpv(sup)
    check("L2: an uncommitted edit inside the submodule -> different, and 'dirty' names the submodule",
          g1 is not None and g1 != g0 and d[0] is True and "libsub" in d[1], (g1, d))
    _commit_all(sub, "sub change")
    g2 = fpv(sup)
    check("L2: new commits checked out in the submodule (not yet recorded) -> different from the clean build",
          g2 is not None and g2 != g0, g2)
    _git(sup, "add", "libsub")
    _git(sup, "commit", "-q", "--no-verify", "-m", "bump libsub")
    g3 = fpv(sup)
    check("L2: a submodule bump commit -> different from before the bump", g3 is not None and g3 != g0, (g0, g3))
    (sub / "new.py").write_text("x = 1\n", encoding="utf-8")
    check("L2: an untracked source file inside the submodule -> different", fpv(sup) not in (None, g3))
    (sub / "new.py").unlink()
    check("L2: ... removed again -> equal", fpv(sup) == g3)
    nest = _init_repo(sup / "nested")
    (nest / "n.py").write_text("def n():\n    return 1\n", encoding="utf-8")
    g4 = fpv(sup)
    check("L2: an untracked nested repository holding source -> different", g4 is not None and g4 != g3, g4)
    (nest / "n.py").write_text("def n():\n    return 2\n", encoding="utf-8")
    check("L2: ... an edit inside it -> different again", fpv(sup) not in (None, g4))
    shutil.rmtree(nest)
    vend = _init_repo(sup / "node_modules" / "dep")
    (vend / "i.js").write_text("module.exports = 1\n", encoding="utf-8")
    _commit_all(vend, "dep")
    check("L2: a vendored nested repository (node_modules/dep) -> equal (the vendor filter)", fpv(sup) == g3)
    # non-git
    ng = base / "l2_fs"
    ng.mkdir()
    (ng / "s.ts").write_text("export const s = 1;\n", encoding="utf-8")
    n0 = fpv(ng)
    (ng / ".deepmap-root").write_text("", encoding="utf-8")
    n1 = fpv(ng)
    (ng / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    check("L2: non-git: .deepmap-root added -> different; tsconfig.json added -> different again",
          None not in (n0, n1) and n1 != n0 and fpv(ng) not in (None, n1), (n0, n1))
    # the drift lock: every config file name a recognizer module reads is a graph input
    import re
    pat = re.compile(r"^[A-Za-z0-9_][\w.-]*\.json$|^next\.config\.[a-z]+$")
    names = {}
    for p in sorted(set(build.glob("*_edges.py")) | {build / "language_recognizers.py", build / "edge_resolve.py"}):
        if not p.is_file():
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and pat.match(n.value):
                names.setdefault(n.value, set()).add(p.name)
    exts = S._graph_input_exts()
    bad = sorted(n for n in names if not S._affects_graph(n, set(), exts))
    check(f"L2: every config file name the recognizers read ({len(names)}) is a graph input — dirty + fingerprint",
          len(names) >= 5 and not bad, {"not inputs": {b: sorted(names[b]) for b in bad}, "all": sorted(names)})
    check("L2: ... in a subdirectory too (apps/web/tsconfig.app.json), never under node_modules",
          S._affects_graph("apps/web/tsconfig.app.json", set(), exts)
          and not S._affects_graph("node_modules/x/package.json", set(), exts))


# The MSBuild reader's file-name literals: anything spelled like a project-system file, a whole name or a bare suffix
# (the extension in lower case, so a namespace such as System.Net.Http.Json stays out).
_MSB_FILE_LITERAL = (r"^(?:[A-Za-z0-9_][\w.-]*)?\.(?:csproj|props|targets|projitems|config|proj|shproj|sln|slnx|nuspec"
                     r"|msbuild|rsp|json|xml)$")


def _msbuild_literals(texts) -> dict:
    """{name: {module}} for every string literal in `texts` ({module: source}) spelled like a project-system file."""
    import re
    pat = re.compile(_MSB_FILE_LITERAL)
    out: dict = {}
    for mod, text in texts.items():
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and pat.match(n.value):
                out.setdefault(n.value, set()).add(mod)
    return out


def _msbuild_tree() -> dict:
    """A .NET tree in memory: the files MSBuild evaluates for src/App, and bait the reader must not start reading
    without the rule learning it (a read of one fails the lock)."""
    proj = """<Project Sdk="Microsoft.NET.Sdk">
  <Import Project="../../build/common.props" />
  <Import Project="../Shared/Shared.projitems" Label="Shared" />
  <PropertyGroup><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
  <ItemGroup><PackageReference Include="Acme.Core" Version="1.0.0" /><Using Include="Acme.Core" /></ItemGroup>
  <Import Project="../../build/common.targets" />
</Project>
"""
    empty = "<Project></Project>\n"
    return {"src/App/App.csproj": proj, "Directory.Build.props": empty, "Directory.Build.targets": empty,
            "Directory.Packages.props": '<Project><ItemGroup><PackageVersion Include="Acme.Core" Version="1.0.0" />'
                                        "</ItemGroup></Project>\n",
            "build/common.props": empty, "build/common.targets": empty, "src/Shared/Shared.projitems": empty,
            "src/App/packages.config": '<packages><package id="Acme.Old" version="1.0" /></packages>\n',
            # bait: no part of the reader reads these today
            "global.json": "{}\n", "nuget.config": "<configuration />\n", ".editorconfig": "root = true\n",
            "App.sln": "\n", "src/App/App.csproj.user": empty, "src/App/app.config": "<configuration />\n",
            "src/App/Properties/launchSettings.json": "{}\n", "src/App/obj/project.assets.json": "{}\n"}


def _msbuild_opened(M, tree: dict) -> list:
    """The files read_group opens for src/App on `tree`, through its own read / listdir seams."""
    opened: list = []

    def read(rel):
        rel = str(rel)
        opened.append(rel)
        got = tree.get(rel)
        return None if got is None else got.encode("utf-8")

    def listdir(rel_dir):
        d = str(rel_dir or "").strip("/")
        pre = d + "/" if d else ""
        return sorted({k[len(pre):] for k in tree if k.startswith(pre) and "/" not in k[len(pre):]})

    M.read_group("deepmap-gate-in-memory-root", "src/App", read=read, listdir=listdir)
    return sorted(set(opened))


def _msbuild_not_inputs(S, build: Path, extra=None) -> tuple:
    """(names, not inputs): the literal side of the drift lock over the reader's sources (+ `extra`, {module: source})."""
    texts = {}
    for name in ("csharp_msbuild.py", "receiver_facts_csharp.py"):
        if (build / name).is_file():
            texts[name] = (build / name).read_text(encoding="utf-8")
    texts.update(extra or {})
    names = _msbuild_literals(texts)
    exts = S._graph_input_exts()
    return names, sorted(n for n in names if not S._affects_graph(n, set(), exts))


def gate_l3_msbuild_inputs(S, build: Path, base: Path) -> None:
    print("\n-- L3: MSBuild project files are graph inputs (the C# reader reads them) --")
    fp_fn = getattr(S, "source_fingerprint", None)
    if not callable(fp_fn) or not _GIT:
        check("L3: sync.source_fingerprint exists and git is available", False)
        return

    def fpv(root):
        return fp_fn(root).get("value")

    empty = "<Project></Project>\n"
    inputs = {"src/App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><ImplicitUsings>enable'
                                    "</ImplicitUsings></PropertyGroup></Project>\n",
              "Directory.Build.props": empty, "Directory.Build.targets": empty, "Directory.Packages.props": empty,
              "build/common.props": empty, "build/common.targets": empty, "src/App/packages.config": "<packages />\n",
              "src/Shared/Shared.projitems": empty}
    repo = _init_repo(base / "l3_git")
    for rel, text in inputs.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    (repo / "src" / "App" / "Program.cs").write_text("class P { static void Main() { } }\n", encoding="utf-8")
    (repo / "README.md").write_text("# app\n", encoding="utf-8")
    _commit_all(repo, "init")
    v0 = fpv(repo)
    check("L3: setup: a C# repository has a fingerprint", isinstance(v0, str), v0)
    # each kind of input moves the fingerprint by itself; together they read as dirty, each one named
    for rel in ("src/App/App.csproj", "Directory.Build.props", "build/common.targets", "src/App/packages.config",
                "src/Shared/Shared.projitems"):
        (repo / rel).write_text(inputs[rel] + "<!-- a -->\n", encoding="utf-8")
        v1 = fpv(repo)
        check(f"L3: an uncommitted edit of {rel} moves the fingerprint", v1 not in (None, v0), v1)
        (repo / rel).write_text(inputs[rel], encoding="utf-8")
    for rel, text in inputs.items():
        (repo / rel).write_text(text + "<!-- a -->\n", encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check(f"L3: uncommitted edits of all {len(inputs)} inputs read as dirty, each one named",
          d[0] is True and d[2] == len(inputs) and set(inputs) <= set(d[1]), d)
    for rel, text in inputs.items():
        (repo / rel).write_text(text, encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check("L3: ... undone: the fingerprint is back, the tree clean", fpv(repo) == v0 and d[0] is False, d)
    # a commit that touches only a project file, as the auto-sync freshness check sees it
    was = v0
    try:
        import auto_sync as AS
    except Exception as exc:  # noqa: BLE001
        AS = None
        check("L3: build/auto_sync.py imports", False, f"{type(exc).__name__}: {exc}")
    for rel, edit in (("src/App/App.csproj", inputs["src/App/App.csproj"].replace("enable", "disable")),
                      ("Directory.Build.props", '<Project><ItemGroup><Using Include="A.B" Alias="Tk" /></ItemGroup>'
                                                "</Project>\n"),
                      ("build/common.targets", '<Project><ItemGroup><Using Remove="System.Linq" /></ItemGroup>'
                                               "</Project>\n"),
                      ("src/App/packages.config", '<packages><package id="Acme.Core" version="1.0" /></packages>\n')):
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / rel).write_text(edit, encoding="utf-8")
        _commit_all(repo, rel)
        f = AS.freshness(repo, {"source_fingerprint": was, "built_at_sha": head}) if AS is not None else {}
        now = f.get("fingerprint")
        check(f"L3: a commit that changes only {rel} -> different, and auto_sync.freshness reads the graph as stale",
              now not in (None, was) and f.get("state") == "stale", f)
        was = now or was
    # added, deleted, spelled in another letter case
    (repo / "src" / "Lib").mkdir()
    (repo / "src" / "Lib" / "Lib.CsProj").write_text(empty, encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check("L3: an untracked Lib.CsProj (any letter case) moves it and reads as dirty",
          fpv(repo) not in (None, was) and d[0] is True and "src/Lib/Lib.CsProj" in d[1], d)
    (repo / "src" / "Lib" / "Lib.CsProj").unlink()
    (repo / "Directory.Build.targets").unlink()
    check("L3: a deleted project file moves it", fpv(repo) not in (None, was))
    _commit_all(repo, "no targets")
    # not inputs
    was = fpv(repo)
    for rel in ("App.sln", "src/App/Web.config", "nuget.config", "src/App/App.csproj.user", "global.json",
                "src/App/appsettings.json", "docs/notes.md", "src/App/App.csproj.bak"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n", encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check("L3: unrelated files (.sln, Web.config, nuget.config, .user, global.json, appsettings, docs) move nothing",
          fpv(repo) == was and d[0] is False, d)
    _commit_all(repo, "unrelated")
    check("L3: ... and committed: a docs-and-settings commit -> equal", fpv(repo) == was)
    for rel in ("vendor/lib/Lib.csproj", "node_modules/dep/Directory.Build.props"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(empty, encoding="utf-8")
    d = S._git_worktree_changes(repo)
    check("L3: a project file under vendor/ or node_modules/ -> equal (the vendor filter)",
          fpv(repo) == was and d[0] is False, d)
    # unconditional: the rule reads no knob
    with _env(DEEPMAP_TYPED_CALLS="csharp"):
        on = fpv(repo)
    check("L3: the fingerprint is the same with DEEPMAP_TYPED_CALLS=csharp (one rule, flags off too)", on == was, on)
    # a tree with no project file: its fingerprint does not change at all
    plain = _init_repo(base / "l3_plain")
    (plain / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (plain / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit_all(plain, "init")
    blob = _git(plain, "ls-files", "-s", "a.py").stdout.split()[1]
    check("L3: a repository with no project file keeps its value (no extra refresh for it)",
          fpv(plain) == S._fingerprint_value({"a.py": blob}), blob)
    # outside git
    ng = base / "l3_fs"
    (ng / "src" / "obj").mkdir(parents=True)
    (ng / "src" / "bin").mkdir()
    (ng / "src" / "A.cs").write_text("class A { }\n", encoding="utf-8")
    (ng / "src" / "A.csproj").write_text(empty, encoding="utf-8")
    n0 = fpv(ng)
    (ng / "src" / "A.csproj").write_text("<Project><!-- a --></Project>\n", encoding="utf-8")
    n1 = fpv(ng)
    check("L3: non-git: a .csproj edit -> different", None not in (n0, n1) and n1 != n0, (n0, n1))
    (ng / "src" / "obj" / "A.csproj.nuget.g.props").write_text(empty, encoding="utf-8")
    (ng / "src" / "bin" / "A.targets").write_text(empty, encoding="utf-8")
    check("L3: non-git: what a build generates under obj/ and bin/ -> equal", fpv(ng) == n1, n1)
    # the rule's own truth table, in a subdirectory and in another letter case
    exts = S._graph_input_exts()
    table = {"a/b/App.csproj": True, "DIRECTORY.BUILD.PROPS": True, "a/Directory.Build.targets": True,
             "a/PACKAGES.CONFIG": True, "a/Shared.projitems": True, "a/x.props": True, "a/x.targets": True,
             "a/Directory.Build.rsp": True, "DIRECTORY.BUILD.RSP": True, "a/x.rsp": False,
             "a/Web.config": False, "a/app.config": False, "a/x.sln": False, "a/x.csproj.user": False,
             "a/x.vbproj": False, "a/xprops": False}
    wrong = {k: v for k, v in table.items() if S._affects_graph(k, set(), exts) is not v}
    wrong.update({k: True for k in inputs if not S._fingerprint_input(k, set(), exts)})
    check("L3: the rule's truth table (names, letter case, what stays out; every input of this tree)", not wrong, wrong)
    # the drift lock, part 1: every file-name literal in the reader
    names, bad = _msbuild_not_inputs(S, build)
    must = {"directory.build.props", "directory.build.targets", ".csproj"}
    check(f"L3: drift lock: the reader's {len(names)} file-name literals are found (it must not go blind)",
          must <= {n.lower() for n in names}, sorted(names))
    check(f"L3: drift lock: every file name the MSBuild reader spells ({len(names)}) is a graph input",
          not bad, {"not inputs": {b: sorted(names[b]) for b in bad}, "all": sorted(names)})
    # part 2: what it really opens, whatever its source spells
    tree = _msbuild_tree()
    try:
        import csharp_msbuild as M
    except Exception as exc:  # noqa: BLE001
        check("L3: build/csharp_msbuild.py imports", False, f"{type(exc).__name__}: {exc}")
        return
    check("L3: setup: csharp_msbuild was loaded from the tree under test",
          Path(M.__file__).resolve().parent == build.resolve(), M.__file__)
    opened = _msbuild_opened(M, tree)
    seen = {"src/App/App.csproj", "Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props",
            "build/common.props", "build/common.targets", "src/Shared/Shared.projitems", "src/App/packages.config"}
    check("L3: drift lock: read_group opens the project, Directory.*, its imports and packages.config (a real reading)",
          seen <= set(opened), sorted(seen - set(opened)))
    bad_opened = sorted(r for r in opened if not S._affects_graph(r, set(), exts))
    check(f"L3: drift lock: every file read_group opens ({len(opened)}) is a graph input", not bad_opened,
          {"not inputs": bad_opened, "opened": opened})
    # the lock's teeth: on a name the rule does not know it fails, by either road
    _, bad_x = _msbuild_not_inputs(S, build, {"synthetic.py": 'PIN = "global.json"\nCFG = "nuget.config"\n'})
    check("L3: drift lock teeth: a new name in the reader's source that is no graph input is reported",
          {"global.json", "nuget.config"} <= set(bad_x), bad_x)
    orig = S._is_graph_config
    try:
        S._is_graph_config = lambda name: False
        _, bad_y = _msbuild_not_inputs(S, build)
        lost = sorted(r for r in opened if not S._affects_graph(r, set(), exts))
    finally:
        S._is_graph_config = orig
    check("L3: drift lock teeth: with the rule blind to these names both sides of the lock fail",
          len(bad_y) >= 3 and set(seen) <= set(lost), (bad_y, lost))


def gate_m_refresh_doc(tree: Path, build: Path) -> None:
    print("\n-- M: /deep-map:refresh names the refused prune --force-prune cannot override --")
    doc = tree / "commands" / "refresh.md"
    text = doc.read_text(encoding="utf-8") if doc.is_file() else ""
    bullets = text.split("\n- ")
    hit = [b for b in bullets if "no-root-marker" in b]
    b = " ".join(hit[0].split()) if hit else ""
    check("M: sync.py really records the reason the doc names",
          '"no-root-marker"' in (build / "sync.py").read_text(encoding="utf-8"))
    check("M: refresh.md has one bullet for the no-root-marker refusal", len(hit) == 1, len(hit))
    check("M: ... its remedy: run from the repository root, or add .deepmap-root",
          "repository root" in b and "`.deepmap-root`" in b, b)
    check("M: ... and --force-prune cannot override it", "`--force-prune` cannot override" in b, b)
    wipe = [x for x in bullets if x.startswith("**A refused prune**")]
    check("M: the wipe-guard bullet keeps its own remedy (--force-prune), and does not claim this refusal",
          len(wipe) == 1 and "--force-prune" in wipe[0] and "no-root-marker" not in wipe[0], wipe)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    build = tree / "build"
    print("=" * 72)
    print(f"DeepMap — auto-sync sync-core: records, atomic writes, recipe, drift lock, fingerprint (root: {tree})")
    print("=" * 72)
    sys.path.insert(0, str(build))
    base = Path(tempfile.mkdtemp(prefix="dm_sync_status_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_")}
    try:
        scrub.update(NEO4J_URI="bolt://127.0.0.1:9", NEO4J_PASSWORD=None, DEEPMAP_HOME=str(base / "home"),
                     GIT_CONFIG_NOSYSTEM="1", DEEPMAP_AUTO_SYNC="0")
        with _env(**scrub):
            (base / "home").mkdir()
            import sync as S                              # noqa: E402 — from the tree under test
            check("setup: sync was loaded from the tree under test",
                  Path(S.__file__).resolve().parent == build.resolve(), S.__file__)
            try:
                import sync_status as ST                  # noqa: E402
                check("setup: build/sync_status.py imports", Path(ST.__file__).resolve().parent == build.resolve())
            except Exception as exc:  # noqa: BLE001
                ST = None
                check("setup: build/sync_status.py imports", False, f"{type(exc).__name__}: {exc}")
            gates = [(gate_b_exit_paths, (S, base)), (gate_c_running, (S, base)), (gate_e_manifest, (S, ST, base)),
                     (gate_f_recipe, (S, ST, base)), (gate_f2_route_source, (S, build, base)),
                     (gate_j_git_env, (S, base)), (gate_l_fingerprint, (S, base)), (gate_l2_inputs, (S, build, base)),
                     (gate_l3_msbuild_inputs, (S, build, base)), (gate_m_refresh_doc, (tree, build))]
            if ST is not None:
                gates = [(gate_a_atomic, (ST, base))] + gates + [
                    (gate_d_record_failures, (S, ST, base)), (gate_g_drift, (ST, build)), (gate_h_tables, (ST,)),
                    (gate_i_model, (ST, base)), (gate_k_imports, (build,))]
            for gate, args in gates:
                try:
                    gate(*args)
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
            home = base / "home"
            check("hermetic: nothing under the temp DEEPMAP_HOME names 7687 / 8787",
                  not any(("7687" in p.name or "8787" in p.name) for p in home.rglob("*")))
    finally:
        shutil.rmtree(str(base), ignore_errors=True)   # exactly the temp dir this gate created
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
