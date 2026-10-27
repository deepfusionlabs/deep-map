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
BUILD = REPO / "build"

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


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


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — stale-cleanup wipe-guard gates (F3d, no Neo4j)")
    print("=" * 64)
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    S = _load()
    gate_prune_decision(S)
    gate_read_prior_extractor(S)
    gate_blast_radius(S)
    gate_wiring()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
