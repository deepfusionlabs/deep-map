#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the stale-cleanup WIPE GUARD (F3d, the P0 of the 2026-07 field audit).

The hazard: `--stale-cleanup` (which `/deep-map:refresh` passes automatically) DETACH-DELETEs every
keyed node not in the freshly-extracted set. Three shipped guards (0-node refusal, repo-root marker,
owner-singleton) all MISS an engine DOWNGRADE — a polyglot Ruby+Python repo whose graphify install
broke, so the extractor falls back to `ast` (Python-only), yields a NON-ZERO partial extraction
(passes the 0-node guard), and the prune then deletes the graphify-built Ruby graph. This gate locks
the two added guards (both pure additions — a normal graphify->graphify re-sync still prunes true
orphans):

  - engine DOWNGRADE skip: prior graph built with 'graphify', this run resolved to 'ast' -> refuse;
  - BLAST RADIUS backstop: a prune deleting > _PRUNE_MAX_FRACTION of the keyed graph -> refuse;
  - --force-prune overrides BOTH (explicit operator intent to wipe);
  - _read_prior_extractor round-trips the .deepmap/graph_build.json extractor field;
  - _prune_blast_radius counts (total, would-delete) over a fake driver, fail-open to (0,0);
  - WIRING: the real sync() actually SKIPS _cleanup_stale (no DETACH DELETE) on a downgrade / a massive
    prune, and STILL prunes on a normal re-sync + under --force-prune.
  - REFUSAL RECORDS (2026-10-30): the other two refusals are recorded too. A --stale-cleanup refused for a
    missing repo-root marker sets stale_cleanup_skipped "no-root-marker" (sync_last.json + the manifest) and,
    with no prune plan, claims stale nodes only when the graph provably holds more nodes than the run pushed
    (DEGRADED, "at least N"); otherwise it says the stale-cleanup did not run and keeps the run's verdict. A
    0-node refusal is "zero-nodes" in sync_last.json (and in a manifest the pass writes: an empty graph's; a
    kept graph keeps its own). Exit-code fields (sync_last rc / outcome) are unchanged. The readers (the CLI's
    manifest lines, the hooks' stale_kept note, auto_sync.status_line) handle the new values.

Usage: python tests/sync_prune_guard_gates.py [--root <tree holding build/ and hooks/>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"          # main() points it at --root's build/ (the tree under test)
HOOKS = REPO / "hooks"

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail else ''}")


def _load():
    spec = importlib.util.spec_from_file_location("dm_sync_prune", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_prune_decision(S) -> None:
    print("\n-- _prune_decision: pure matrix (downgrade / blast-radius / force / normal) --")
    dec = getattr(S, "_prune_decision", None)
    frac = getattr(S, "_PRUNE_MAX_FRACTION", None)
    check("_prune_decision exists", callable(dec))
    check("_PRUNE_MAX_FRACTION is a fraction in (0,1)", isinstance(frac, float) and 0 < frac < 1, repr(frac))
    if not callable(dec):
        return
    # (engine, prior_engine, would_delete, existing_total, force_prune) -> (action, reason)
    d, _ = dec("graphify", "graphify", 5, 100, False)
    check("normal graphify->graphify, small prune -> prune", d == "prune")
    d, r = dec("ast", "graphify", 5, 100, False)
    check("engine DOWNGRADE graphify->ast -> skip(engine-downgrade)",
          d == "skip" and r == "engine-downgrade", f"{d}/{r}")
    d, r = dec("graphify", "graphify", 90, 100, False)
    check("BLAST RADIUS 90/100 (>50%) -> skip(blast-radius)",
          d == "skip" and r == "blast-radius", f"{d}/{r}")
    d, _ = dec("graphify", "graphify", 50, 100, False)
    check("exactly 50/100 (not OVER the fraction) -> prune", d == "prune")
    d, _ = dec("ast", "graphify", 90, 100, True)
    check("--force-prune overrides the downgrade guard -> prune", d == "prune")
    d, _ = dec("graphify", "graphify", 99, 100, True)
    check("--force-prune overrides the blast-radius guard -> prune", d == "prune")
    d, _ = dec("ast", None, 5, 100, False)
    check("no prior extractor recorded (first sync) is NOT a downgrade -> prune", d == "prune")
    d, _ = dec("graphify", "graphify", 5, 0, False)
    check("empty existing graph (0 total) -> prune (nothing to over-delete)", d == "prune")


def gate_read_prior_extractor(S) -> None:
    print("\n-- _read_prior_extractor: reads .deepmap/graph_build.json extractor, None when absent --")
    fn = getattr(S, "_read_prior_extractor", None)
    check("_read_prior_extractor exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("no .deepmap -> None", fn(root) is None)
        (root / ".deepmap").mkdir()
        check("no manifest -> None", fn(root) is None)
        (root / ".deepmap" / "graph_build.json").write_text(
            json.dumps({"extractor": "graphify", "nodes": 10}), encoding="utf-8")
        check("manifest extractor=graphify -> 'graphify'", fn(root) == "graphify")
        (root / ".deepmap" / "graph_build.json").write_text(
            json.dumps({"extractor": "ast"}), encoding="utf-8")
        check("manifest extractor=ast -> 'ast'", fn(root) == "ast")
        (root / ".deepmap" / "graph_build.json").write_text("{ not json", encoding="utf-8")
        check("corrupt manifest -> None (never raises)", fn(root) is None)


class _Result(list):
    def single(self):
        return self[0] if self else None


class _BlastSess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.cyphers.append(cypher)
        if "DETACH DELETE" in cypher:
            self.drv.detach = True
            return _Result()
        if "AS total" in cypher and "AS deleted" in cypher:
            if self.drv.raise_on_count:
                raise RuntimeError("boom")
            return _Result([{"total": self.drv.total, "deleted": self.drv.deleted}])
        if "_DeepMapMeta" in cypher and "RETURN" in cypher:
            return _Result()
        if "count(" in cypher:
            return _Result([{"c": self.drv.total}])
        return _Result()


class _BlastDrv:
    def __init__(self, total=100, deleted=90, raise_on_count=False):
        self.total = total
        self.deleted = deleted
        self.raise_on_count = raise_on_count
        self.detach = False
        self.cyphers = []

    def session(self):
        return _BlastSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def gate_blast_radius(S) -> None:
    print("\n-- _prune_blast_radius: (would-delete, total) over a fake driver; fail-open on error --")
    fn = getattr(S, "_prune_blast_radius", None)
    check("_prune_blast_radius exists", callable(fn))
    if not callable(fn):
        return
    d, t = fn(_BlastDrv(total=100, deleted=90), ["k1", "k2"])
    check("returns (would_delete, total) from the graph count", (d, t) == (90, 100), f"{d},{t}")
    d, t = fn(_BlastDrv(raise_on_count=True), ["k1"])
    check("a query error fails OPEN -> (0, 0) (never blocks a legitimate prune)", (d, t) == (0, 0), f"{d},{t}")


# --- WIRING: drive the REAL sync() with a stubbed pipeline + a fake driver ------------------------- #
def _wire(S, engine, drv):
    node = {"key": "a.rb::A:1", "label": "Class", "name": "A", "source_file": "a.rb",
            "start_line": 1, "body_summary": "A"}

    def _fake_lock(root):
        (Path(root) / ".deepmap").mkdir(parents=True, exist_ok=True)
        return object()

    S._acquire_sync_lock = _fake_lock
    S._acquire_instance_lock = lambda *a, **k: object()
    S._release_sync_lock = lambda h: None
    S._rebuild_code = lambda root: ([node], [], [], engine)
    S._apply_recognizers = lambda root, nodes: (nodes, [])
    S.push_to_neo4j = lambda *a, **k: (1, 0)
    S._read_owning_root = lambda drv_: None
    S._graph_has_nodes = lambda drv_: True
    S._write_owning_root = lambda drv_, ident: None
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None,
                                wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda *a, **k: 0
    S._maybe_auto_tune = lambda root: None
    S.write_graph_build_manifest = lambda *a, **k: None
    # REAL (under test): _read_prior_extractor, _prune_blast_radius, _prune_decision, _cleanup_stale,
    # _cleanup_is_safe.
    return node


def _run_sync(engine, prior_extractor, total, deleted, *, force_prune):
    """Fresh sync module; a temp repo with a repo-root marker and (optionally) a prior graph_build.json;
    drive sync(cleanup_stale=True, force_prune=...). Return (detach_delete_issued, stderr)."""
    S = _load()
    home = Path(tempfile.mkdtemp(prefix="dm_prune_home_"))
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI", "DEEPMAP_EXTRACTOR")}
    os.environ["DEEPMAP_HOME"] = str(home)
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"
    os.environ.pop("DEEPMAP_EXTRACTOR", None)
    try:
        _wire(S, engine, None)
        drv = _BlastDrv(total=total, deleted=deleted)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".deepmap-root").write_text("", encoding="utf-8")   # repo-root marker (cleanup safe)
            if prior_extractor is not None:
                (root / ".deepmap").mkdir(parents=True, exist_ok=True)
                (root / ".deepmap" / "graph_build.json").write_text(
                    json.dumps({"extractor": prior_extractor}), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                ok = S.sync(td, cleanup_stale=True, force_prune=force_prune, driver=drv)
            return drv.detach, err.getvalue(), ok
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


def gate_wiring() -> None:
    print("\n-- WIRING: real sync() SKIPS the prune on downgrade / massive; prunes normally + on --force --")
    # sync() must accept a force_prune kwarg.
    S = _load()
    import inspect
    params = inspect.signature(S.sync).parameters
    check("sync() accepts a force_prune keyword", "force_prune" in params, str(list(params)))

    # (1) engine DOWNGRADE (prior graphify, this run ast) -> prune SKIPPED (the P0 wipe averted).
    detach, err, ok = _run_sync("ast", "graphify", total=100, deleted=90, force_prune=False)
    check("downgrade graphify->ast: NO DETACH DELETE (wipe averted)", detach is False, f"detach={detach}")
    check("downgrade: sync still returns True (non-fatal, graph preserved)", ok is True)
    check("downgrade: a loud skip reason is logged", "DOWNGRADE" in err or "downgrade" in err, err[-200:])

    # (2) BLAST RADIUS >50% with no downgrade -> prune SKIPPED.
    detach, err, _ = _run_sync("graphify", "graphify", total=100, deleted=90, force_prune=False)
    check("massive prune (90/100): NO DETACH DELETE", detach is False, f"detach={detach}")
    check("massive prune: a blast-radius skip reason is logged",
          "prune would" in err.lower() or "blast" in err.lower() or "50%" in err, err[-200:])

    # (3) NORMAL re-sync (graphify->graphify, small orphan set) -> prune RUNS.
    detach, _, _ = _run_sync("graphify", "graphify", total=100, deleted=5, force_prune=False)
    check("normal re-sync (5/100 orphans): DETACH DELETE issued (true orphans still pruned)",
          detach is True, f"detach={detach}")

    # (4) --force-prune overrides the downgrade guard -> prune RUNS.
    detach, _, _ = _run_sync("ast", "graphify", total=100, deleted=90, force_prune=True)
    check("--force-prune: DETACH DELETE issued despite the downgrade (explicit operator override)",
          detach is True, f"detach={detach}")


# --- REFUSAL RECORDS: the no-root-marker and 0-node refusals, end to end through the real sync() ----------------- #
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


class _CountSess:
    """Answers the one graph read this section leaves real (sync._graph_node_count) and records DETACH DELETEs;
    every other statement (the meta writes) gets an empty result."""

    def __init__(self, g):
        self.g = g

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        if "DETACH DELETE" in cypher:
            self.g.detach = True
            return _Result()
        if "RETURN count(n) AS c" in cypher:
            if self.g.count_error:
                raise RuntimeError("count unreadable (fake)")
            return _Result([{"c": len(self.g.keys)}])
        return _Result()


class _CountDrv:
    def __init__(self, keys=(), count_error=False):
        self.keys = list(keys)
        self.count_error = count_error
        self.detach = False

    def session(self):
        return _CountSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


class _FakeVerdict:
    """The verdict module as sync calls it: READY for any pushed graph, FAILED for 0 nodes (verdict.classify's
    rule), so a DEGRADED is sync's own STALE NODES KEPT decision."""
    READY, DEGRADED, FAILED = "READY", "DEGRADED", "FAILED"

    def extension_census(self, root):
        return {}

    def build_coverage(self, root, nodes, census=None):
        return {}

    def classify(self, **k):
        return ("FAILED", ["NO CODE NODES (fake)"]) if not k.get("n_nodes") else ("READY", [])

    def ml_stack_ok(self):
        return True

    def format_block(self, v, rs, **k):
        return f"[deepmap] verdict: {v}\n" + "\n".join(f"          - {r}" for r in rs)

    def note_engine_nodes(self, root, nodes):
        return None


def _fn(rel, name, line):
    return {"key": f"{rel}::{name}:{line}", "label": "Function", "name": name, "source_file": rel,
            "start_line": line, "body_summary": name}


def _refusal_sync(S, root: Path, drv: _CountDrv, nodes, *, holds=None):
    """One real sync(cleanup_stale=True) over `root` with the extraction stubbed to `nodes` and the Neo4j writes
    stubbed onto `drv` (keys), the manifest, sync_last.json, the prune decision, _graph_node_count and the verdict
    wiring real. Returns (ok, stderr+stdout)."""
    ident = {}
    real_identity = S._project_identity

    def _identity(r):
        ident["v"] = real_identity(r)
        return ident["v"]

    def _push(d, n, c, i, t=None):
        for x in n:
            if x["key"] not in drv.keys:
                drv.keys.append(x["key"])
        return (len(n), 0)

    has = (lambda d: bool(drv.keys)) if holds is None else (lambda d: holds)
    attrs = dict(
        _rebuild_code=lambda r: S._Extraction(list(nodes), [], [], "ast", {"requested": "ast", "resolved": "ast"}),
        _apply_recognizers=lambda r, n: (n, []), _project_identity=_identity,
        _read_owning_root=lambda d: ident.get("v"), _graph_has_nodes=has, _graph_holds_nodes=has,
        _write_owning_root=lambda d, i: None, _record_base_store_owner=lambda *a, **k: None,
        _remove_stale_short_names=lambda d: 0, _fts_rebuild_pending=lambda d: False, _fts_changed_docs=lambda d, n: 0,
        _mark_fts_rebuild=lambda d, p: None, push_to_neo4j=_push, _reuse_rekeyed_embeddings=lambda d, n: 0,
        _rebuild_code_fts=lambda d: True, _count_embedded_nodes=lambda d, fallback=0: len(drv.keys),
        _maybe_auto_tune=lambda r: None, _unkey_receiver_calls=lambda r, n, c: (c, 0),
        _attach_bare_calls=lambda r, n, c: 0, _attach_local_defs=lambda r, n, c: 0,
        _attach_local_def_calls=lambda r, n, c: 0,
        indexes=SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False),
        embeddings=SimpleNamespace(generate_embeddings=lambda d: 0))
    saved = sys.modules.get("verdict")
    sys.modules["verdict"] = _FakeVerdict()
    buf = io.StringIO()
    try:
        with _patched(S, **attrs), contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            ok = S.sync(str(root), cleanup_stale=True, driver=drv)
    finally:
        if saved is None:
            sys.modules.pop("verdict", None)
        else:
            sys.modules["verdict"] = saved
    return ok, buf.getvalue()


def _project(base: Path, name: str, *, marker: bool, prior: bool = True) -> Path:
    """A project dir holding one source file; `marker` = a .deepmap-root file; `prior` = a previous manifest."""
    root = base / name
    root.mkdir(parents=True)
    if marker:
        (root / ".deepmap-root").write_text("", encoding="utf-8")
    (root / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    if prior:
        (root / ".deepmap").mkdir()
        (root / ".deepmap" / "graph_build.json").write_text(
            json.dumps({"graph_engines": ["ast"], "extractor": "ast", "nodes": 3, "verdict": "READY"}),
            encoding="utf-8")
    return root


def _jread(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_file(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_refusal_records(S, base: Path) -> None:
    print("\n-- REFUSAL RECORDS: no-root-marker / zero-nodes are recorded; no unproven stale claim --")
    two = [_fn("m.py", "f", 1), _fn("m.py", "g", 4)]
    old = [f"old.py::h{i}:{i}" for i in range(5)]           # a previous build's nodes this run does not extract

    # A: no marker, the graph provably holds MORE nodes than this run pushed (5 old + 2 pushed = 7)
    root = _project(base, "a_extra", marker=False)
    drv = _CountDrv(keys=old)
    ok, out = _refusal_sync(S, root, drv, two)
    last = _jread(root / ".deepmap" / "sync_last.json") or {}
    man = _jread(root / ".deepmap" / "graph_build.json") or {}
    reasons = [str(r) for r in man.get("reasons") or []]
    stale = [r for r in reasons if r.startswith("STALE NODES KEPT")]
    check("A: no marker + 5 nodes beyond this run's 2 -> sync_last.json records stale_cleanup_skipped "
          "'no-root-marker', stale_kept 5 (the proven lower bound), outcome synced rc 0 (exit contract kept), "
          "nothing deleted",
          ok is True and not drv.detach and last.get("stale_cleanup_skipped") == "no-root-marker"
          and last.get("stale_kept") == 5 and last.get("outcome") == "synced" and last.get("rc") == 0,
          ({k: last.get(k) for k in ("stale_cleanup_skipped", "stale_kept", "outcome", "rc")}, out[-400:]))
    check("A: ...the manifest records 'no-root-marker' and graph_nodes 7; nodes stays this run's 2",
          man.get("stale_cleanup_skipped") == "no-root-marker" and man.get("graph_nodes") == 7
          and man.get("nodes") == 2, {k: man.get(k) for k in ("stale_cleanup_skipped", "graph_nodes", "nodes")})
    check("A: ...verdict DEGRADED with ONE 'STALE NODES KEPT' reason saying 'at least 5' and '7 nodes in all'",
          man.get("verdict") == "DEGRADED" and S.last_verdict == "DEGRADED" and len(stale) == 1
          and "at least 5 node(s)" in stale[0] and "(7 nodes in all)" in stale[0], (man.get("verdict"), reasons))
    check("A: ...whose remedy is the repo root / a .deepmap-root file, never --force-prune (it cannot override "
          "a missing marker)",
          len(stale) == 1 and ".deepmap-root" in stale[0] and "repository root" in stale[0]
          and "--force-prune" not in stale[0], stale)
    check("A: ...the summary line names the excess as proven ('5 more than this run pushed')",
          "the graph holds 7 nodes, 5 more than this run pushed" in out and "no repo-root marker" in out, out[-600:])
    try:
        C = _load_file(BUILD / "deepmap_cli.py", "dm_cli_prune_refusal")
        lines = C._manifest_coverage_lines(root)
    except Exception as exc:  # noqa: BLE001
        lines = [f"<{type(exc).__name__}: {exc}>"]
    check("A: status/doctor's manifest lines show the DEGRADED verdict and the STALE NODES KEPT reason",
          "last-sync verdict: DEGRADED" in lines and any(ln.startswith("STALE NODES KEPT") and "at least 5" in ln
                                                         for ln in lines), lines)
    try:
        H = _load_file(HOOKS / "deepmap_hook_state.py", "dm_hook_state_prune_refusal")
        note = H.stale_kept_note(man)
    except Exception as exc:  # noqa: BLE001
        note = f"<{type(exc).__name__}: {exc}>"
    check("A: the hooks' background-refresh stale_kept note stays silent for this foreground refusal (no manifest "
          "stale_kept; its words are about the background refresh)",
          note == "" and "stale_kept" not in man and man.get("stale_cleanup_skipped") == "no-root-marker", note)
    try:
        A = _load_file(BUILD / "auto_sync.py", "dm_auto_sync_prune_refusal")
        line = A.status_line(root)
    except Exception as exc:  # noqa: BLE001
        line = f"<{type(exc).__name__}: {exc}>"
    check("A: auto_sync.status_line (the auto-sync line of status / doctor) reads the sync_last.json that records "
          "'no-root-marker' without raising",
          isinstance(line, tuple) and last.get("stale_cleanup_skipped") == "no-root-marker", line)

    # B: no marker, the graph holds ONLY this run's nodes (no excess): no stale claim, the run's own verdict
    root = _project(base, "b_exact", marker=False)
    drv = _CountDrv()
    ok, out = _refusal_sync(S, root, drv, two)
    last = _jread(root / ".deepmap" / "sync_last.json") or {}
    man = _jread(root / ".deepmap" / "graph_build.json") or {}
    reasons = [str(r) for r in man.get("reasons") or []]
    check("B: no marker, graph == this run's 2 nodes -> recorded 'no-root-marker' (sync_last + manifest), "
          "stale_kept 0, graph_nodes 2",
          ok is True and last.get("stale_cleanup_skipped") == "no-root-marker" and last.get("stale_kept") == 0
          and man.get("stale_cleanup_skipped") == "no-root-marker" and man.get("graph_nodes") == 2,
          ({k: last.get(k) for k in ("stale_cleanup_skipped", "stale_kept")},
           {k: man.get(k) for k in ("stale_cleanup_skipped", "graph_nodes")}))
    check("B: ...the verdict stays the run's own (READY), with no STALE NODES KEPT reason",
          man.get("stale_cleanup_skipped") == "no-root-marker" and man.get("verdict") == "READY"
          and not any("STALE NODES" in r for r in reasons), (man.get("verdict"), reasons))
    check("B: ...the summary line says the stale-cleanup did not run and why, and never that nodes this run did "
          "not extract are still in the graph",
          "the stale-cleanup did not run (no repo-root marker), and the graph holds 2 nodes, no more than this run "
          "pushed" in out and "did not extract are still in it" not in out, out[-600:])

    # C: no marker, the node count cannot be read: unknown -> no stale claim, said so
    root = _project(base, "c_unread", marker=False)
    drv = _CountDrv(keys=old, count_error=True)
    ok, out = _refusal_sync(S, root, drv, two)
    last = _jread(root / ".deepmap" / "sync_last.json") or {}
    man = _jread(root / ".deepmap" / "graph_build.json") or {}
    check("C: no marker, the graph's count unreadable -> recorded 'no-root-marker', stale_kept 0, no graph_nodes, "
          "verdict READY without a STALE NODES KEPT reason; the line says the count could not be read",
          ok is True and last.get("stale_cleanup_skipped") == "no-root-marker" and last.get("stale_kept") == 0
          and man.get("stale_cleanup_skipped") == "no-root-marker" and "graph_nodes" not in man
          and man.get("verdict") == "READY" and not any("STALE" in str(r) for r in man.get("reasons") or [])
          and "the graph's node count could not be read" in out,
          ({k: last.get(k) for k in ("stale_cleanup_skipped", "stale_kept")}, man.get("verdict"), out[-500:]))

    # D: 0 nodes over a graph holding a previous build: refused, recorded in sync_last.json; the manifest is kept
    root = _project(base, "d_zero_kept", marker=True)
    before = (root / ".deepmap" / "graph_build.json").read_bytes()
    drv = _CountDrv(keys=old)
    ok, out = _refusal_sync(S, root, drv, [])
    last = _jread(root / ".deepmap" / "sync_last.json") or {}
    check("D: 0 nodes over a kept graph -> sync_last.json records stale_cleanup_skipped 'zero-nodes'; outcome "
          "failed rc 1 as before (verdict FAILED: the exit contract is unchanged); nothing deleted",
          ok is True and not drv.detach and last.get("stale_cleanup_skipped") == "zero-nodes"
          and last.get("stale_kept") == 0 and last.get("outcome") == "failed" and last.get("rc") == 1,
          ({k: last.get(k) for k in ("stale_cleanup_skipped", "stale_kept", "outcome", "rc")}, out[-400:]))
    check("D: ...the kept graph's manifest is left byte for byte (it still describes that graph)",
          last.get("stale_cleanup_skipped") == "zero-nodes"
          and (root / ".deepmap" / "graph_build.json").read_bytes() == before)
    try:
        A = _load_file(BUILD / "auto_sync.py", "dm_auto_sync_prune_refusal_d")
        line = A.status_line(root)
        H = _load_file(HOOKS / "deepmap_hook_state.py", "dm_hook_state_prune_refusal_d")
        note = H.stale_kept_note(_jread(root / ".deepmap" / "graph_build.json"))
    except Exception as exc:  # noqa: BLE001
        line, note = f"<{type(exc).__name__}: {exc}>", None
    check("D: ...auto_sync.status_line and the hooks' stale_kept note read it without raising (no stale note)",
          isinstance(line, tuple) and note == "" and last.get("stale_cleanup_skipped") == "zero-nodes", (line, note))

    # E: 0 nodes over an EMPTY graph: the pass writes the (FAILED) manifest, and it records the refusal
    root = _project(base, "e_zero_empty", marker=True, prior=False)
    drv = _CountDrv()
    ok, out = _refusal_sync(S, root, drv, [], holds=False)
    last = _jread(root / ".deepmap" / "sync_last.json") or {}
    man = _jread(root / ".deepmap" / "graph_build.json") or {}
    check("E: 0 nodes over an empty graph -> the FAILED manifest and sync_last.json both record 'zero-nodes' "
          "(rc 1 as before), no graph_nodes",
          ok is True and man.get("verdict") == "FAILED" and man.get("stale_cleanup_skipped") == "zero-nodes"
          and "graph_nodes" not in man and last.get("stale_cleanup_skipped") == "zero-nodes" and last.get("rc") == 1,
          ({k: man.get(k) for k in ("verdict", "stale_cleanup_skipped")}, {k: last.get(k) for k in (
              "stale_cleanup_skipped", "rc")}, out[-400:]))


def _root_arg(argv: list) -> Path:
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir() or not (Path(tree) / "hooks").is_dir():
        print(f"usage: python tests/sync_prune_guard_gates.py [--root <tree holding build/ and hooks/>] "
              f"(got {tree!r})", file=sys.stderr)
        raise SystemExit(2)
    return Path(tree).resolve()


def main() -> int:
    global BUILD, HOOKS
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    BUILD, HOOKS = tree / "build", tree / "hooks"
    print("=" * 64)
    print(f"DeepMap — stale-cleanup wipe-guard gates (F3d, no Neo4j) (root: {tree})")
    print("=" * 64)
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    S = _load()
    if Path(S.__file__).resolve().parent != BUILD.resolve():   # a setup error only: never a passing check
        check("setup: sync was loaded from the tree under test", False, S.__file__)
    gate_prune_decision(S)
    gate_read_prior_extractor(S)
    gate_blast_radius(S)
    gate_wiring()
    base = Path(tempfile.mkdtemp(prefix="dm_prune_refusal_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_")}
    scrub.update(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_HOME=str(base / "home"), DEEPMAP_AUTO_SYNC="0")
    try:
        with _env(**scrub):
            (base / "home").mkdir()
            try:
                gate_refusal_records(_load(), base)
            except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                import traceback
                traceback.print_exc()
                check("gate_refusal_records ran without raising", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(str(base), ignore_errors=True)   # exactly the temp dir this section created
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
