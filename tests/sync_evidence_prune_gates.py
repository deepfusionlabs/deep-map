#!/usr/bin/env python3
"""auto-sync sync-core — the evidence-only prune of DeepMap's background refresh, and the re-run request merge (H4).

An unattended refresh never runs the full prune; its prune is EVIDENCE-ONLY: the stale nodes go only when every one
of them is provably stale (in a file this run re-read, or in a file that is gone) and the run is trustworthy. A
refused evidence prune keeps the nodes AND the verdict (H7: READY stays READY; the manifest says stale_kept).

  1  _evidence_decision table: each refusal "evidence:<why>" in its order, and the one prune case.
  2  property, 500 seeded random graphs: evidence prune => full prune (the same live set), full refuses =>
     evidence refuses (both plans read the same fake graph).
  3  real sync() passes over a fake graph driver: an ast line-shift re-key -> pruned to exactly the live set; a
     deleted file -> pruned; a file that still exists but was not extracted -> nothing deleted, manifest
     stale_cleanup_skipped "evidence" + stale_kept, verdict READY, no "STALE NODES KEPT" reason; an excluded file,
     a re-read file that lost more than half its symbols, engine failures -> nothing deleted.
  4  "full" (cleanup_stale=True) still runs _plan_prune exactly as before and a refused full prune is still
     DEGRADED + "STALE NODES KEPT".
  5  re-run request merge: prune_policy max-merge, origin manual-wins, both merge orders, a legacy request; an
     unattended request always writes cleanup_stale false, so the PRE-CHANGE holder rule `cleanup_stale or
     request["cleanup_stale"]` (simulated literally) never turns it into a full prune.
  6  ValueError: an unknown prune_policy; unattended + switch_project / force_prune / "full".

Hermetic: temp projects, a temp DEEPMAP_HOME, every driver a fake, no network, never ~/.deepmap.
Usage: python tests/sync_evidence_prune_gates.py [--root <tree>]   (a --root without build/ is a usage error, exit 2)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/sync_evidence_prune_gates.py [--root <tree holding build/>] (got {tree!r})",
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


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---- a fake graph driver: keyed nodes (key, source_file, name); answers the prune queries ------------------------
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _GraphSess:
    def __init__(self, g):
        self.g = g

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        g = self.g
        keys = set(p.get("keys") or [])
        stale = [n for n in g.nodes if n["key"] not in keys]
        if "DETACH DELETE n" in cypher and "NOT n.key IN $keys" in cypher:
            g.deleted.extend(sorted(n["key"] for n in stale))
            g.nodes = [n for n in g.nodes if n["key"] in keys]
            return _Res()
        if "DELETE r" in cypher:
            return _Res()
        if "AS total" in cypher and "AS deleted" in cypher:
            return _Res([{"total": len(g.nodes), "deleted": len(stale)}])
        if "count(n) AS c" in cypher and "n.source_file AS sf" in cypher:
            per = {}
            for n in stale:
                per[n["sf"]] = per.get(n["sf"], 0) + 1
            return _Res([{"sf": k, "c": v} for k, v in sorted(per.items())])
        if "RETURN DISTINCT n.source_file AS sf" in cypher:
            return _Res([{"sf": s} for s in sorted({n["sf"] for n in stale})])
        if "collect(DISTINCT n.name)" in cypher:
            files = set(p.get("files") or [])
            per = {}
            for n in g.nodes:
                if n["sf"] in files:
                    per.setdefault(n["sf"], set()).add(n["name"])
            return _Res([{"sf": k, "names": sorted(v)} for k, v in sorted(per.items())])
        raise AssertionError(f"unexpected query: {cypher[:120]}")


class _GraphDrv:
    def __init__(self, nodes=()):
        self.nodes = [dict(key=k, sf=sf, name=nm) for k, sf, nm in nodes]
        self.deleted: list = []

    def session(self):
        return _GraphSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None

    def keys(self):
        return sorted(n["key"] for n in self.nodes)


def _n(rel, name, line):
    return {"key": f"{rel}::{name}:{line}", "label": "Function", "name": name, "source_file": rel,
            "start_line": line, "body_summary": name}


class _FakeVerdict:
    READY, DEGRADED, FAILED = "READY", "DEGRADED", "FAILED"

    def extension_census(self, root):
        return {}

    def build_coverage(self, root, nodes, census=None):
        return {}

    def classify(self, **k):
        return "READY", []

    def ml_stack_ok(self):
        return True

    def format_block(self, v, rs, **k):
        return f"[deepmap] verdict: {v}\n" + "\n".join(f"          - {r}" for r in rs)

    def note_engine_nodes(self, root, nodes):
        return None


@contextlib.contextmanager
def _pipeline(S, drv, nodes, *, engine="ast", info=None):
    """Stub the extraction and the Neo4j writes; keep the prune plans + _cleanup_stale (under test) real."""

    def _push(d, n, c, i, t=None):
        have = {x["key"] for x in drv.nodes}
        for x in n:
            if x["key"] not in have:
                drv.nodes.append({"key": x["key"], "sf": x["source_file"], "name": x["name"]})
        return (len(n), 0)

    # the graph is THIS project's (what a background refresh normally finds — an unattended sync never adopts an
    # unowned one): its owner is the identity _sync_pass computes for the root just before it reads the owner
    ident = {}
    real_identity = S._project_identity

    def _identity(root):
        ident["v"] = real_identity(root)
        return ident["v"]

    attrs = dict(
        _rebuild_code=lambda root: S._Extraction(list(nodes), [], [], engine,
                                                 dict({"requested": engine, "resolved": engine}, **(info or {}))),
        _apply_recognizers=lambda root, n: (n, []), _project_identity=_identity,
        _read_owning_root=lambda d: ident.get("v"), _graph_has_nodes=lambda d: True, _graph_holds_nodes=lambda d: True,
        _write_owning_root=lambda d, ident: None, _record_base_store_owner=lambda *a, **k: None,
        _remove_stale_short_names=lambda d: 0, _fts_rebuild_pending=lambda d: False, _fts_changed_docs=lambda d, n: 0,
        _mark_fts_rebuild=lambda d, p: None, push_to_neo4j=_push, _reuse_rekeyed_embeddings=lambda d, n: 0,
        _rebuild_code_fts=lambda d: True, _count_embedded_nodes=lambda d, fallback=0: len(drv.nodes),
        _graph_node_count=lambda d: len(drv.nodes), _maybe_auto_tune=lambda root: None,
        _unkey_receiver_calls=lambda root, n, c: (c, 0), _attach_bare_calls=lambda root, n, c: 0,
        _attach_local_defs=lambda root, n, c: 0, _attach_local_def_calls=lambda root, n, c: 0,
        indexes=SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False),
        embeddings=SimpleNamespace(generate_embeddings=lambda d: 0))
    saved = sys.modules.get("verdict")
    sys.modules["verdict"] = _FakeVerdict()
    try:
        with _patched(S, **attrs):
            yield
    finally:
        if saved is None:
            sys.modules.pop("verdict", None)
        else:
            sys.modules["verdict"] = saved


def _project(base: Path, name: str, files=("m.py",), prior_engine="ast") -> Path:
    root = base / name
    root.mkdir(parents=True)
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / ".deepmap").mkdir()
    (root / ".deepmap" / "graph_build.json").write_text(
        json.dumps({"graph_engines": [prior_engine], "extractor": prior_engine, "nodes": 3}), encoding="utf-8")
    return root


def _sync(S, root, drv, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        ok = S.sync(root, driver=drv, **kw)
    return ok, buf.getvalue()


# =====================================================================================================
# 1 — the pure decision
# =====================================================================================================
def gate_decision(S) -> None:
    print("\n-- 1: _evidence_decision — every refusal, in order, and the one prune case --")
    fn = getattr(S, "_evidence_decision", None)
    check("1: sync._evidence_decision exists", callable(fn))
    if not callable(fn):
        return
    ok = {"suspicious": 0, "exempt": {"re-extracted": 3, "deleted": 1, "excluded": 0}, "shrunk": []}

    def d(census=ok, *, engine="graphify", prior="graphify", extraction=None, total=10, would=4):
        return fn(census, engine=engine, prior_engine=prior, extraction=extraction or {}, existing_total=total,
                  would_delete=would)

    table = [
        ("clean census, same engine", d(), ("prune", None)),
        ("no prior engine (first build)", d(prior=None), ("prune", None)),
        ("census unreadable", d(None), ("skip", "evidence:census-unreadable")),
        ("a stale node in an existing, unextracted file", d(dict(ok, suspicious=1)),
         ("skip", "evidence:unextracted-files")),
        ("an excluded stale node", d(dict(ok, exempt=dict(ok["exempt"], excluded=2))), ("skip", "evidence:excluded")),
        ("a re-read file lost > half its names", d(dict(ok, shrunk=[("m.py", 3, 2)])), ("skip", "evidence:file-shrank")),
        ("engine failures", d(extraction={"failed_files": 2}), ("skip", "evidence:engine-failures")),
        ("an ast fallback", d(extraction={"fallback_reason": "graphify crashed"}), ("skip", "evidence:fallback")),
        ("an engine timeout", d(extraction={"timed_out": True}), ("skip", "evidence:fallback")),
        ("the engine changed (ast over graphify)", d(engine="ast", prior="graphify"), ("skip", "evidence:engine-change")),
        ("the engine changed (graphify over ast)", d(engine="graphify", prior="ast"),
         ("skip", "evidence:engine-change")),
        ("order: unextracted before excluded", d(dict(ok, suspicious=1, exempt=dict(ok["exempt"], excluded=1))),
         ("skip", "evidence:unextracted-files")),
        ("order: shrunk before engine failures", d(dict(ok, shrunk=[("a", 2, 2)]), extraction={"failed_files": 1}),
         ("skip", "evidence:file-shrank")),
    ]
    for name, got, want in table:
        check(f"1: {name} -> {want}", got == want, got)


# =====================================================================================================
# 2 — evidence ⊆ full, over 500 random graphs
# =====================================================================================================
def gate_property(S, base: Path) -> None:
    print("\n-- 2: 500 seeded random graphs — evidence prune => full prune; full refuses => evidence refuses --")
    if not callable(getattr(S, "_plan_prune_evidence", None)):
        check("2: sync._plan_prune_evidence exists", False)
        return
    rng = random.Random(20261022)
    root = base / "prop"
    root.mkdir()
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    files = [f"f{i}.py" for i in range(6)] + ["tests/test_a.py", "vendor/v.js"]
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
    viol, both_prune, n_ev_prune, n_full_prune = [], 0, 0, 0
    for it in range(500):
        on_disk = {rel for rel in files if rng.random() < 0.75}
        for rel in files:
            p = root / rel
            if rel in on_disk:
                p.write_text("x\n", encoding="utf-8")
            elif p.exists():
                p.unlink()
        prior, live = [], []
        for rel in files:
            k = rng.randint(0, 4)
            for j in range(k):
                prior.append((f"{rel}::n{j}:{j + 1}", rel, f"n{j}"))
        extracted = [rel for rel in files if rel in on_disk and rng.random() < 0.8]
        for rel in extracted:
            k = rng.randint(1, 4)
            shift = rng.choice((0, 0, 1))
            for j in range(k):
                live.append(_n(rel, f"n{j}" if rng.random() < 0.85 else f"z{j}", j + 1 + shift))
        if not live:
            live.append(_n("f0.py", "only", 1))
        engines = ("graphify", "ast")
        engine, prior_engine = rng.choice(engines), rng.choice(engines + (None,))
        extraction = rng.choice(({}, {}, {}, {"failed_files": 1}, {"fallback_reason": "x"}, {"timed_out": True}))
        excl = rng.random() < 0.3
        with _env(DEEPMAP_EXCLUDE_TESTS="1" if excl else None):
            drv = _GraphDrv(prior)
            full = S._plan_prune(drv, root, live, engine, prior_engine, False)
            ev = S._plan_prune_evidence(drv, root, live, engine, prior_engine, extraction)
        if ev["decision"] == "prune":
            n_ev_prune += 1
        if full["decision"] == "prune":
            n_full_prune += 1
        if ev["decision"] == "prune" and full["decision"] != "prune":
            viol.append((it, "evidence pruned, full refused", ev["reason"], full["reason"]))
        if full["decision"] != "prune" and ev["decision"] == "prune":
            viol.append((it, "full refused, evidence pruned"))
        if ev["decision"] == "prune" and full["decision"] == "prune":
            both_prune += 1
            if ev["live"] != full["live"]:
                viol.append((it, "different live sets"))
    check("2: no violation over 500 graphs (evidence prunes only what full prunes; never when full refuses)",
          not viol, viol[:5])
    check(f"2: the sample exercised both outcomes ({n_ev_prune} evidence prunes, {n_full_prune} full prunes, "
          f"{both_prune} both)",
          0 < n_ev_prune < 500 and n_full_prune > n_ev_prune and both_prune == n_ev_prune,
          (n_ev_prune, n_full_prune, both_prune))


# =====================================================================================================
# 3 / 4 — real sync() passes over a fake graph
# =====================================================================================================
def gate_passes(S, base: Path) -> None:
    print("\n-- 3: real sync(prune_policy='evidence') passes over a fake graph --")

    def run(name, *, prior, live, files, engine="ast", info=None, env=None, policy="evidence", cleanup=False):
        root = _project(base, name, files=files, prior_engine=engine)
        drv = _GraphDrv(prior)
        with _env(**(env or {})), _pipeline(S, drv, live, engine=engine, info=info):
            ok, out = _sync(S, root, drv, prune_policy=policy, cleanup_stale=cleanup) if policy else \
                _sync(S, root, drv, cleanup_stale=cleanup)
        m = _read(root / ".deepmap" / "graph_build.json") or {}
        last = _read(root / ".deepmap" / "sync_last.json") or {}
        return ok, out, drv, m, last

    # a) an ast line shift re-keys every function of m.py
    prior = [("m.py::f:1", "m.py", "f"), ("m.py::g:5", "m.py", "g")]
    live = [_n("m.py", "f", 2), _n("m.py", "g", 6)]
    ok, out, drv, m, last = run("re_key", prior=prior, live=live, files=("m.py",))
    check("3a: line-shift re-key -> pruned: the graph holds exactly the live keys",
          ok is True and drv.keys() == ["m.py::f:2", "m.py::g:6"] and sorted(drv.deleted) == ["m.py::f:1",
                                                                                             "m.py::g:5"],
          (ok, drv.keys(), drv.deleted, out[-300:]))
    check("3a: ... recorded: prune_policy evidence, pruned 2, verdict READY, nothing kept",
          last.get("prune_policy") == "evidence" and last.get("pruned") == 2 and m.get("verdict") == "READY"
          and "stale_kept" not in m and "stale_cleanup_skipped" not in m, (last, m.get("verdict")))
    # b) a deleted file
    prior = [("m.py::f:1", "m.py", "f"), ("gone.py::h:1", "gone.py", "h")]
    ok, out, drv, m, last = run("deleted", prior=prior, live=[_n("m.py", "f", 1)], files=("m.py",))
    check("3b: a deleted file's nodes -> pruned", drv.keys() == ["m.py::f:1"] and drv.deleted == ["gone.py::h:1"],
          (drv.keys(), drv.deleted))
    # c) a file that still exists but was not extracted -> keep everything, READY + stale_kept
    prior = [("m.py::f:1", "m.py", "f"), ("other.py::k:1", "other.py", "k"), ("m.py::old:9", "m.py", "old")]
    ok, out, drv, m, last = run("unextracted", prior=prior, live=[_n("m.py", "f", 1)], files=("m.py", "other.py"))
    check("3c: an existing, unextracted file -> NOTHING deleted (not even the provable one)",
          drv.deleted == [] and "other.py::k:1" in drv.keys() and "m.py::old:9" in drv.keys(), drv.deleted)
    check("3c: manifest stale_cleanup_skipped 'evidence', stale_kept 2, graph_nodes recorded",
          m.get("stale_cleanup_skipped") == "evidence" and m.get("stale_kept") == 2
          and isinstance(m.get("graph_nodes"), int), {k: m.get(k) for k in ("stale_cleanup_skipped", "stale_kept",
                                                                             "graph_nodes")})
    check("3c: H7 — the verdict stays READY and carries no 'STALE NODES KEPT' reason",
          m.get("verdict") == "READY" and not any("STALE NODES KEPT" in str(r) for r in m.get("reasons") or [])
          and S.last_verdict == "READY", (m.get("verdict"), m.get("reasons")))
    check("3c: the kept nodes are announced on stderr, naming /deep-map:refresh",
          "kept 2 stale node(s)" in out and "/deep-map:refresh" in out, out[-400:])
    check("3c: sync_last: stale_cleanup_skipped evidence, stale_kept 2, outcome synced",
          last.get("stale_cleanup_skipped") == "evidence" and last.get("stale_kept") == 2
          and last.get("outcome") == "synced", last)
    # d) an excluded file (DEEPMAP_EXCLUDE_TESTS) -> no prune
    prior = [("m.py::f:1", "m.py", "f"), ("tests/test_x.py::t:1", "tests/test_x.py", "t")]
    ok, out, drv, m, last = run("excluded", prior=prior, live=[_n("m.py", "f", 1)],
                                files=("m.py", "tests/test_x.py"), env={"DEEPMAP_EXCLUDE_TESTS": "1"})
    check("3d: a stale node a test filter now excludes -> nothing deleted",
          drv.deleted == [] and m.get("stale_kept") == 1, (drv.deleted, m.get("stale_kept")))
    # e) a re-read file that lost more than half of its names
    prior = [("m.py::f:1", "m.py", "f"), ("m.py::g:3", "m.py", "g"), ("m.py::h:5", "m.py", "h")]
    ok, out, drv, m, last = run("shrank", prior=prior, live=[_n("m.py", "f", 2)], files=("m.py",))
    check("3e: a re-read file lost 2 of 3 names (a half-written file) -> nothing deleted",
          drv.deleted == [] and m.get("stale_cleanup_skipped") == "evidence", (drv.deleted, out[-300:]))
    prior = [("m.py::f:1", "m.py", "f"), ("m.py::g:3", "m.py", "g"), ("m.py::h:5", "m.py", "h")]
    ok, out, drv, m, last = run("shrank_one", prior=prior,
                                live=[_n("m.py", "f", 2), _n("m.py", "g", 4)], files=("m.py",))
    check("3e: ... losing 1 of 3 names (a real deletion) -> pruned",
          sorted(drv.deleted) == ["m.py::f:1", "m.py::g:3", "m.py::h:5"], drv.deleted)
    # f) engine failures
    prior = [("m.py::f:1", "m.py", "f")]
    ok, out, drv, m, last = run("failed", prior=prior, live=[_n("m.py", "f", 2)], files=("m.py",),
                                engine="graphify", info={"failed_files": 3})
    check("3f: the engine failed on some files -> nothing deleted", drv.deleted == []
          and m.get("stale_cleanup_skipped") == "evidence", drv.deleted)
    # g) prune_policy "none" -> no prune, no census, no stale_kept
    prior = [("m.py::f:1", "m.py", "f")]
    ok, out, drv, m, last = run("none", prior=prior, live=[_n("m.py", "f", 2)], files=("m.py",), policy="none")
    check("3g: prune_policy 'none' -> nothing deleted, nothing reported kept",
          drv.deleted == [] and "stale_kept" not in m and "stale_cleanup_skipped" not in m, (drv.deleted, m))
    # h) through a real UnattendedControl (no preempt): the same evidence prune
    ST = sys.modules.get("sync_status")
    if ST is not None:
        root = _project(base, "unattended", files=("m.py",))
        drv = _GraphDrv([("m.py::f:1", "m.py", "f")])
        ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
        with _pipeline(S, drv, [_n("m.py", "f", 2)]):
            ok, out = _sync(S, root, drv, prune_policy="evidence", unattended=ctl)
        last = _read(root / ".deepmap" / "sync_last.json") or {}
        check("3h: an unattended sync: evidence prune runs, origin unattended, trigger recorded",
              drv.deleted == ["m.py::f:1"] and last.get("origin") == "unattended"
              and last.get("trigger") == "session-start" and last.get("prune_policy") == "evidence", (drv.deleted, last))

    print("\n-- 4: 'full' (cleanup_stale=True) is exactly the old prune --")
    calls = []
    real_plan = S._plan_prune
    real_ev = getattr(S, "_plan_prune_evidence", None)

    def rec_plan(*a, **k):
        r = real_plan(*a, **k)
        calls.append(("full", r["decision"], r["reason"]))
        return r

    def rec_ev(*a, **k):
        calls.append(("evidence",))
        return real_ev(*a, **k)

    prior = [("m.py::f:1", "m.py", "f")] + [(f"o{i}.py::k:1", f"o{i}.py", "k") for i in range(9)]
    root = _project(base, "full_refused", files=("m.py",) + tuple(f"o{i}.py" for i in range(9)))
    drv = _GraphDrv(prior)
    with _pipeline(S, drv, [_n("m.py", "f", 2)]), _patched(S, _plan_prune=rec_plan, _plan_prune_evidence=rec_ev):
        ok, out = _sync(S, root, drv, cleanup_stale=True)
    m = _read(root / ".deepmap" / "graph_build.json") or {}
    check("4: cleanup_stale=True calls _plan_prune once and never the evidence plan",
          [c[0] for c in calls] == ["full"], calls)
    check("4: a refused full prune (blast radius) deletes nothing, verdict DEGRADED with 'STALE NODES KEPT'",
          drv.deleted == [] and m.get("verdict") == "DEGRADED" and m.get("stale_cleanup_skipped") == "blast-radius"
          and any("STALE NODES KEPT" in str(r) for r in m.get("reasons") or []), (drv.deleted, m.get("verdict"),
                                                                                m.get("reasons")))
    check("4: ... and writes no stale_kept (the full path's manifest is unchanged)", "stale_kept" not in m, sorted(m))
    calls.clear()
    root = _project(base, "full_ok", files=("m.py",))
    drv = _GraphDrv([("m.py::f:1", "m.py", "f")])
    with _pipeline(S, drv, [_n("m.py", "f", 2)]), _patched(S, _plan_prune=rec_plan, _plan_prune_evidence=rec_ev):
        ok, out = _sync(S, root, drv, cleanup_stale=True)
    check("4: a normal full prune still prunes", drv.deleted == ["m.py::f:1"] and calls == [("full", "prune", None)],
          (drv.deleted, calls))


# =====================================================================================================
# 5 — the re-run request merge (H4)
# =====================================================================================================
def gate_merge(S, base: Path) -> None:
    print("\n-- 5: re-run request merge (H4) --")
    import inspect
    params = inspect.signature(S._request_rerun).parameters
    check("5: _request_rerun takes prune_policy / origin / trigger",
          {"prune_policy", "origin", "trigger"} <= set(params), list(params))
    if not {"prune_policy", "origin", "trigger"} <= set(params):
        return
    path_of = S._rerun_request_path
    n = [0]

    def fresh():
        n[0] += 1
        r = base / f"merge{n[0]}"
        (r / ".deepmap").mkdir(parents=True)
        return r

    def req(r):
        return _read(path_of(r))

    U = dict(cleanup_stale=False, force_prune=False, prune_policy="evidence", origin="unattended",
             trigger="session-start")
    r = fresh()
    S._request_rerun(r, cleanup_stale=True, force_prune=False)
    check("5: a manual --stale-cleanup request writes prune_policy full, origin manual",
          {k: req(r).get(k) for k in ("cleanup_stale", "prune_policy", "origin")} == {
              "cleanup_stale": True, "prune_policy": "full", "origin": "manual"}, req(r))
    r = fresh()
    S._request_rerun(r, cleanup_stale=False, force_prune=False)
    check("5: a manual plain request writes prune_policy none", req(r).get("prune_policy") == "none", req(r))
    r = fresh()
    S._request_rerun(r, **U)
    check("5: an unattended request: cleanup_stale false, prune_policy evidence, origin unattended, trigger",
          {k: req(r).get(k) for k in ("cleanup_stale", "prune_policy", "origin", "trigger")} == {
              "cleanup_stale": False, "prune_policy": "evidence", "origin": "unattended", "trigger": "session-start"},
          req(r))
    r = fresh()
    S._request_rerun(r, **dict(U, cleanup_stale=True))
    check("5: an unattended request never writes cleanup_stale true (even when asked)",
          req(r).get("cleanup_stale") is False, req(r))
    r = fresh()
    S._request_rerun(r, cleanup_stale=False, force_prune=False)
    S._request_rerun(r, **U)
    check("5: manual(none) then unattended(evidence) -> evidence, origin manual, trigger of the manual (None)",
          {k: req(r).get(k) for k in ("cleanup_stale", "prune_policy", "origin", "trigger")} == {
              "cleanup_stale": False, "prune_policy": "evidence", "origin": "manual", "trigger": None}, req(r))
    r = fresh()
    S._request_rerun(r, **U)
    S._request_rerun(r, cleanup_stale=True, force_prune=True)
    check("5: unattended(evidence) then manual(--stale-cleanup --force-prune) -> full, manual, cleanup + force kept",
          {k: req(r).get(k) for k in ("cleanup_stale", "force_prune", "prune_policy", "origin")} == {
              "cleanup_stale": True, "force_prune": True, "prune_policy": "full", "origin": "manual"}, req(r))
    r = fresh()
    S._request_rerun(r, cleanup_stale=True, force_prune=False)
    S._request_rerun(r, **U)
    check("5: manual(full) then unattended(evidence) -> still full and manual (max-merge, never lowered)",
          req(r).get("prune_policy") == "full" and req(r).get("origin") == "manual"
          and req(r).get("cleanup_stale") is True, req(r))
    r = fresh()
    S._request_rerun(r, **U)
    S._request_rerun(r, **dict(U, prune_policy="none"))
    check("5: unattended(evidence) then unattended(none) -> evidence, unattended, cleanup false",
          req(r).get("prune_policy") == "evidence" and req(r).get("origin") == "unattended"
          and req(r).get("cleanup_stale") is False, req(r))
    for legacy_cs, want in ((True, "full"), (False, "evidence")):
        r = fresh()
        path_of(r).write_text(json.dumps({"ts": 1.0, "pid": 1, "cleanup_stale": legacy_cs, "force_prune": False}),
                              encoding="utf-8")
        S._request_rerun(r, **U)
        check(f"5: a legacy request (cleanup_stale {legacy_cs}, no policy/origin) then unattended -> {want}, manual",
              req(r).get("prune_policy") == want and req(r).get("origin") == "manual"
              and req(r).get("cleanup_stale") is legacy_cs, req(r))
    ep = S._effective_policy
    tab = [(("none", None), "none"), (("full", None), "full"), (("none", {"cleanup_stale": True}), "full"),
           (("none", {"cleanup_stale": False}), "none"), (("evidence", {"cleanup_stale": False}), "evidence"),
           (("none", {"prune_policy": "evidence"}), "evidence"), (("full", {"prune_policy": "evidence"}), "full"),
           (("evidence", {"prune_policy": "none", "cleanup_stale": True}), "evidence"),
           (("none", {"prune_policy": "bogus", "cleanup_stale": True}), "full")]
    bad = [(a, ep(*a), w) for a, w in tab if ep(*a) != w]
    check(f"5: _effective_policy over {len(tab)} cases (max; a request without a policy: full iff cleanup_stale)",
          not bad, bad)
    # the PRE-CHANGE holder rule, simulated literally, against every unattended request the new writer produces
    produced = []
    for policy in ("evidence", "none"):
        r = fresh()
        S._request_rerun(r, **dict(U, prune_policy=policy))
        produced.append(req(r))
        S._request_rerun(r, **dict(U, prune_policy="evidence"))
        produced.append(req(r))
    old_holder_full = [p for p in produced if (False or bool(p.get("cleanup_stale")))]
    check("5: an older DeepMap holder (`cleanup_stale = cleanup_stale or bool(request.get('cleanup_stale'))`) never "
          "turns an unattended request into a full prune", not old_holder_full and len(produced) == 4,
          old_holder_full)


# =====================================================================================================
# 6 — ValueError
# =====================================================================================================
def gate_errors(S, base: Path) -> None:
    print("\n-- 6: ValueError for impossible combinations --")
    ST = sys.modules.get("sync_status")
    root = base / "errs"
    root.mkdir()

    def raises(**kw):
        try:
            S.sync(root, driver=_GraphDrv(), **kw)
        except ValueError:
            return True
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"
        return False

    check("6: an unknown prune_policy -> ValueError", raises(prune_policy="most") is True)
    if ST is None:
        check("6: sync_status importable (UnattendedControl)", False)
        return
    ctl = ST.UnattendedControl(root, trigger="session-start")
    check("6: unattended + switch_project -> ValueError", raises(unattended=ctl, switch_project=True) is True)
    check("6: unattended + force_prune -> ValueError", raises(unattended=ctl, force_prune=True) is True)
    check("6: unattended + prune_policy 'full' -> ValueError", raises(unattended=ctl, prune_policy="full") is True)
    try:
        ST.UnattendedControl(root, trigger="session-start", prune_policy="full")
        got = False
    except ValueError:
        got = True
    check("6: UnattendedControl(prune_policy='full') -> ValueError", got)
    check("6: nothing was written for a rejected call", not (root / ".deepmap").exists())


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    print("=" * 72)
    print(f"DeepMap — auto-sync: evidence-only prune ⊆ full prune, READY + stale_kept, H4 merge (root: {tree})")
    print("=" * 72)
    sys.path.insert(0, str(tree / "build"))
    base = Path(tempfile.mkdtemp(prefix="dm_evidence_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_")}
    try:
        scrub.update(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_HOME=str(base / "home"), DEEPMAP_AUTO_SYNC="0")
        with _env(**scrub):
            (base / "home").mkdir()
            import sync as S                         # noqa: E402 — from the tree under test
            check("setup: sync was loaded from the tree under test",
                  Path(S.__file__).resolve().parent == (tree / "build").resolve(), S.__file__)
            for gate, args in ((gate_decision, (S,)), (gate_property, (S, base)), (gate_passes, (S, base)),
                               (gate_merge, (S, base)), (gate_errors, (S, base))):
                try:
                    gate(*args)
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
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
