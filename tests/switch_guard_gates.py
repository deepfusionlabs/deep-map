#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the project-switch safety guard (build/sync.py).

DeepMap pushes every project into ONE shared Neo4j; node keys are root-relative, so syncing a DIFFERENT
project would silently MERGE the two graphs (and --stale-cleanup would DETACH DELETE the first). The
guard records the owning project in a keyless :_DeepMapMeta singleton and REFUSES to switch owners
unless --switch-project authorises a clean wipe+rebuild. Since P1-6, --stale-cleanup is DECOUPLED: it
prunes orphans within the SAME owner but no longer authorises a cross-owner wipe (so /deep-map:refresh,
which passes --stale-cleanup, can never silently switch projects).

These gates drive sync() with a Cypher-capturing fake driver (no Neo4j) and prove the whole behaviour
matrix — the guard both FIRES on a foreign owner (incl. a broader one, ROUND-4 F1) and HOLDS for
same-project / an old narrower stamp / adopt — plus the
cross-instance write lock and the path-identity helpers. RED-first: the different-root-no-flag case
asserts ZERO graph mutation, so an inverted guard goes RED immediately.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_switch", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _P(logical: str) -> str:
    """A fixture path that is ABSOLUTE ON THIS OS: '/proj/A' on POSIX, 'C:/proj/A' on Windows. ROUND-5 G3:
    an owner stamp that is not absolute on this OS ('/proj/A' on Windows, 'C:/proj/A' on POSIX) is another
    machine's — a DIFFERENT project — so a fixture that only matched because resolve() rebased both sides
    onto the working directory would now (rightly) be refused."""
    return ("C:" + logical) if os.name == "nt" else logical


def _X(logical: str) -> str:
    """The same logical path as ANOTHER OS writes it (not absolute here): 'C:/proj/A' on POSIX,
    '/proj/A' on Windows."""
    return logical if os.name == "nt" else ("C:" + logical)


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


# ---- fake driver/session: capture every Cypher, serve canned owner/count rows ----
class _FakeSession:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.drv.queries.append((cypher, params))
        if "_DeepMapMeta) RETURN" in cypher:            # _read_owning_root
            return list(self.drv.owner_rows)
        if "count(n)" in cypher:                        # _graph_has_nodes
            return [{"c": self.drv.node_count}]
        if "DETACH DELETE" in cypher:                   # _wipe_all
            self.drv.wiped = True
            return []
        if "MERGE (m:_DeepMapMeta" in cypher:           # _write_owning_root
            self.drv.written_owner = params.get("root")
            return []
        return []


class _FakeDriver:
    def __init__(self, owner=None, node_count=0):
        self.owner_rows = [{"owning_root": owner}] if owner is not None else []
        self.node_count = node_count
        self.queries = []
        self.wiped = False
        self.written_owner = None

    def session(self):
        return _FakeSession(self)

    def verify_connectivity(self):
        pass

    def close(self):
        pass

    def mutated(self) -> bool:
        """Any graph-changing query recorded (a wipe, a meta write, or a MERGE/DELETE)."""
        return any(("DETACH DELETE" in c) or ("MERGE (m:_DeepMapMeta" in c) or self.wiped
                   for c, _ in self.queries) or self.written_owner is not None


NODE = {"key": "a.py::f:1", "label": "Function", "name": "f",
        "source_file": "a.py", "start_line": 1, "body_summary": "x"}


def _fresh_S(cur_id, nodes):
    """A sync module with every collaborator stubbed except the guard under test: no OS locks, no real
    extraction/push/index/embed, a controlled project identity. rec counts extraction/push calls."""
    S = _load_sync()
    rec = {"rebuilt": 0, "pushed": 0, "pruned": 0}
    S._acquire_sync_lock = lambda root: object()
    S._acquire_instance_lock = lambda *a, **k: object()
    S._release_sync_lock = lambda h: None
    S._project_identity = lambda root: cur_id

    def _rebuild(root):
        rec["rebuilt"] += 1
        return (list(nodes), [], [], "ast")

    def _push(driver, n, c, i, typed_edges=None):
        rec["pushed"] += 1
        return (len(n), 0)

    S._rebuild_code = _rebuild
    S.push_to_neo4j = _push
    S._cleanup_stale = lambda d, keys: rec.__setitem__("pruned", rec["pruned"] + 1)
    S._cleanup_is_safe = lambda root: True   # exercise the same-owner prune branch (P1-6)
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None,
                                wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._maybe_auto_tune = lambda root: None
    S.write_graph_build_manifest = lambda *a, **k: None
    return S, rec


def _run_sync(S, drv, **kw):
    """Drive sync() against a throwaway tempdir, capturing stderr. Returns (ok, stderr_text)."""
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        try:
            with contextlib.redirect_stderr(buf):
                ok = S.sync(td, driver=drv, **kw)
        except Exception as e:  # noqa: BLE001
            return (f"RAISED:{type(e).__name__}:{e}", buf.getvalue())
    return (ok, buf.getvalue())


def gate_behaviour_matrix() -> None:
    print("\n-- switch guard: refuse / switch / adopt behaviour matrix --")

    # (A) same project (owner == cur_id) -> normal sync, no wipe, owner re-stamped.
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=1)
    ok, _ = _run_sync(S, drv)
    check("same-root -> sync succeeds, extraction+push ran, NO wipe",
          ok is True and rec["rebuilt"] == 1 and rec["pushed"] == 1 and drv.wiped is False,
          f"ok={ok} rec={rec} wiped={drv.wiped}")
    check("same-root -> owner re-stamped to cur_id", drv.written_owner == _P("/proj/A"), str(drv.written_owner))

    # (B) THE SAFETY GATE: different project, NO flag -> refuse, ZERO graph mutation, no extraction.
    S, rec = _fresh_S(_P("/proj/B"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=5)
    ok, err = _run_sync(S, drv)
    check("different-root no flag -> sync returns False (deliberate refusal)", ok is False, str(ok))
    check("different-root no flag -> extraction NEVER ran (refuse precedes _rebuild_code)",
          rec["rebuilt"] == 0, str(rec))
    check("different-root no flag -> ZERO graph mutation (no push/wipe/meta-write)",
          rec["pushed"] == 0 and not drv.mutated(), f"pushed={rec['pushed']} mutated={drv.mutated()}")
    check("different-root no flag -> REFUSING message names the owner + --switch-project",
          "REFUSING" in err and _P("/proj/A") in err and "--switch-project" in err)

    # (C) different project + --switch-project -> clean wipe + rebuild + owner=B.
    S, rec = _fresh_S(_P("/proj/B"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=5)
    ok, _ = _run_sync(S, drv, switch_project=True)
    check("--switch-project -> wipe-all + push + owner=B, returns True",
          ok is True and drv.wiped is True and rec["pushed"] == 1 and drv.written_owner == _P("/proj/B"),
          f"ok={ok} wiped={drv.wiped} pushed={rec['pushed']} owner={drv.written_owner}")

    # (D) P1-6: different project + --stale-cleanup ONLY -> REFUSES the switch (decoupled from
    # --switch-project). No wipe, no extraction, zero mutation — a refresh can't silently switch owners.
    S, rec = _fresh_S(_P("/proj/B"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=5)
    ok, err = _run_sync(S, drv, cleanup_stale=True)
    check("--stale-cleanup ALONE no longer authorises a cross-owner switch (refuses)",
          ok is False and drv.wiped is False and rec["rebuilt"] == 0 and not drv.mutated(),
          f"ok={ok} wiped={drv.wiped} rec={rec} mutated={drv.mutated()}")
    check("--stale-cleanup-only refusal names --switch-project",
          "REFUSING" in err and "--switch-project" in err)

    # (D2) P1-6: SAME owner + --stale-cleanup -> prunes orphans within the owner (no refusal, no wipe).
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=5)
    ok, _ = _run_sync(S, drv, cleanup_stale=True)
    check("same-owner + --stale-cleanup -> prunes orphans (no refusal, no wipe)",
          ok is True and rec["pruned"] == 1 and drv.wiped is False,
          f"ok={ok} pruned={rec['pruned']} wiped={drv.wiped}")

    # (E) ROUND-4 F1 (the ONE owner predicate, port_registry.owner_is_this_project): an old NARROWER stamp
    # whose directory is gone is this project's -> allowed, no wipe, re-stamped with the project identity.
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A/src"), node_count=3)
    ok, _ = _run_sync(S, drv)
    check("an old narrower stamp (dir gone) -> allowed, no wipe, push ran",
          ok is True and drv.wiped is False and rec["pushed"] == 1,
          f"ok={ok} wiped={drv.wiped} pushed={rec['pushed']}")
    check("an old narrower stamp -> re-stamped with the project identity",
          drv.written_owner == _P("/proj/A"), str(drv.written_owner))

    # (E2) F1: a BROADER owner is a project of its own (a folder around this one — the old "subdir of
    # the owner" rule let a nested project merge its keys into the parent's graph) -> refused, untouched.
    S, rec = _fresh_S(_P("/proj/A/src"), [NODE])
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=3)
    ok, err = _run_sync(S, drv)
    check("a broader owner (another project around this one) -> refused, no extraction, zero mutation",
          ok is False and rec["rebuilt"] == 0 and not drv.mutated() and "REFUSING" in err,
          f"ok={ok} rec={rec} mutated={drv.mutated()}")

    # (F) 0-node switch -> authorised but nothing to replace with: DO NOT wipe (backstop holds).
    S, rec = _fresh_S(_P("/proj/B"), [])   # extraction yields 0 nodes
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=5)
    ok, err = _run_sync(S, drv, switch_project=True)
    check("0-node switch -> NO wipe (existing project preserved), no owner change",
          drv.wiped is False and drv.written_owner is None,
          f"wiped={drv.wiped} owner={drv.written_owner}")
    check("0-node switch -> extraction ran, push was a no-op, message says SKIPPED",
          rec["rebuilt"] == 1 and rec["pushed"] == 1 and "SKIPPED" in err, f"rec={rec}")

    # (G) legacy graph: owner None + non-empty graph -> adopt with a loud one-time notice.
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    drv = _FakeDriver(owner=None, node_count=7)
    ok, err = _run_sync(S, drv)
    check("legacy adopt -> proceeds, no wipe, owner stamped, adoption notice printed",
          ok is True and drv.wiped is False and drv.written_owner == _P("/proj/A") and "adopting" in err,
          f"ok={ok} wiped={drv.wiped} owner={drv.written_owner} notice={'adopting' in err}")

    # (H) fresh instance: owner None + EMPTY graph -> silent adopt (no notice).
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    drv = _FakeDriver(owner=None, node_count=0)
    ok, err = _run_sync(S, drv)
    check("fresh instance -> adopts silently (no legacy-adoption notice)",
          ok is True and drv.written_owner == _P("/proj/A") and "adopting" not in err,
          f"ok={ok} owner={drv.written_owner} notice={'adopting' in err}")


def gate_instance_lock() -> None:
    print("\n-- cross-instance write lock (serialises different projects on one Neo4j) --")

    # Lock-held -> skip fast: sync returns True, never extracts, releases the per-root lock.
    S, rec = _fresh_S(_P("/proj/A"), [NODE])
    S._acquire_instance_lock = lambda *a, **k: None    # instance lock is held by someone else
    released = {"n": 0}
    S._release_sync_lock = lambda h: released.__setitem__("n", released["n"] + 1)
    drv = _FakeDriver(owner=_P("/proj/A"), node_count=1)
    ok, err = _run_sync(S, drv)
    check("instance lock held -> sync skips (returns True), no extraction, no mutation",
          ok is True and rec["rebuilt"] == 0 and not drv.mutated(),
          f"ok={ok} rebuilt={rec['rebuilt']} mutated={drv.mutated()}")
    check("instance lock held -> per-root lock still released", released["n"] >= 1, str(released))

    # The lock primitive itself: a 2nd concurrent acquire fails; release frees it.
    home = Path(tempfile.mkdtemp(prefix="dm_instlock_"))
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI")}
    os.environ["DEEPMAP_HOME"] = str(home)
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"
    try:
        S2 = _load_sync()
        h1 = S2._acquire_instance_lock()
        h2 = S2._acquire_instance_lock()
        check("_acquire_instance_lock: first acquire succeeds", h1 is not None)
        check("_acquire_instance_lock: second concurrent acquire is refused (None)", h2 is None)
        S2._release_sync_lock(h1)
        h3 = S2._acquire_instance_lock()
        check("_acquire_instance_lock: freed after release (re-acquirable)", h3 is not None)
        S2._release_sync_lock(h3)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_path_identity() -> None:
    print("\n-- _same_project / _is_subdir (path-identity, Windows-safe) --")
    S = _load_sync()
    check("_same_project: equal paths", S._same_project(_P("/proj/A"), _P("/proj/A")))
    # ROUND-4 F1: containment is the same project only between paths of the same project identity
    # (tests/sync_owner_predicate_gates.py covers real repos: same-repo subdirs, nested repos, non-git
    # parents); an owner path that no longer exists still counts as an old NARROWER stamp.
    check("_same_project: a broader owner is NOT this project (F1: a project of its own)",
          not S._same_project(_P("/proj/A/src"), _P("/proj/A")))
    check("_same_project: an old narrower stamp whose dir is gone IS this project",
          S._same_project(_P("/proj/A"), _P("/proj/A/src")))
    check("_same_project: disjoint projects are NOT the same", not S._same_project(_P("/proj/A"), _P("/proj/B")))
    check("_same_project: shared string prefix is NOT a subdir (A vs AB)",
          not S._same_project(_P("/proj/A"), _P("/proj/AB")))
    # ROUND-5 G3: another OS's stamp of the SAME logical path is not absolute here -> a different project,
    # also run from the "project root" (tests/sync_owner_predicate_gates.py P8 covers it end to end).
    check("_same_project: another OS's stamp (not absolute here) is NOT this project (G3)",
          not S._same_project(_P("/proj/A"), _X("/proj/A")))
    check("_same_project: another OS's narrower stamp is NOT this project (G3)",
          not S._same_project(_P("/proj/A"), _X("/proj/A/src")))
    check("_is_subdir: strict child under parent", S._is_subdir(_P("/proj/A/src"), _P("/proj/A")))
    check("_is_subdir: equal is NOT a strict subdir", not S._is_subdir(_P("/proj/A"), _P("/proj/A")))
    check("_is_subdir: prefix sibling (AB) is NOT a subdir of A", not S._is_subdir(_P("/proj/AB"), _P("/proj/A")))


def gate_meta_node_shape() -> None:
    print("\n-- meta node is a keyless singleton (survives the stale-prune) --")
    S = _load_sync()
    drv = _FakeDriver()
    S._write_owning_root(drv, _P("/proj/A"))
    merge = [(c, p) for c, p in drv.queries if "MERGE (m:_DeepMapMeta" in c]
    check("_write_owning_root MERGEs the fixed-id singleton", bool(merge) and "id:'singleton'" in merge[0][0])
    check("_write_owning_root writes NO `key` property (so the stale-prune never deletes it)",
          bool(merge) and "key" not in merge[0][1] and ".key" not in merge[0][0].lower().split("set")[-1],
          str(merge[0][1] if merge else None))
    drv2 = _FakeDriver()
    S._cleanup_stale(drv2, ["k1", "k2"])
    prune = [c for c, _ in drv2.queries if "DETACH DELETE" in c]
    check("_cleanup_stale still filters on `n.key IS NOT NULL` (keyless meta node excluded)",
          bool(prune) and "n.key IS NOT NULL" in prune[0])


def gate_cli_flag() -> None:
    print("\n-- CLI exposes --switch-project on the sync subcommand --")
    text = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("deepmap_cli.py declares --switch-project", "--switch-project" in text)
    check("deepmap_cli.py threads switch_project into sync()", "switch_project=args.switch_project" in text)
    sync_line = [ln for ln in text.splitlines() if 'add_parser("sync")' in ln]
    check("--switch-project is on the sync subparser line",
          bool(sync_line) and "--switch-project" in sync_line[0])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("switch_guard_gates — project-switch safety (no Neo4j)")
    print("=" * 64)
    # The owner/switch guard protects a Neo4j instance several projects SHARE — since per-project
    # instances that is the operator escape hatch (an explicit NEO4J_URI), so the matrix runs there, with
    # a throwaway DEEPMAP_HOME (never the machine's registry). Without NEO4J_URI an unregistered project
    # is on the LEGACY 7687 route, whose owner check (ROUND-2 D1) refuses any graph that is not provably
    # its own — tests/sync_neo4j_route_gates.py covers that path.
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI")}
    os.environ["DEEPMAP_HOME"] = tempfile.mkdtemp(prefix="dm_switch_home_")
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"
    try:
        gate_behaviour_matrix()
        gate_instance_lock()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    gate_path_identity()
    gate_meta_node_shape()
    gate_cli_flag()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
