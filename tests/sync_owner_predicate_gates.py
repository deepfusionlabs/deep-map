#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: sync's owner check, owner stamp and base-owner record (ROUND 4, W6).

One owner predicate (binding decision F1): every "is this graph owner stamp THIS project?" question is
answered by port_registry.owner_is_this_project — alias/legacy-aware (the E1 pre-UTF-8-fix identity),
and containment counts as the same project ONLY when both sides map to the same project identity (an
owner path that no longer exists may still be an old, narrower stamp of this project). sync used its
own path-only `_same_project`, so:

  * review r3 #0/#5 (E1 half done): a graph stamped by a pre-fix build with the cp1252-garbled identity
    ('C:/Users/JosÃ©/proj') was refused as ANOTHER project's by /deep-map:refresh, /deep-map:setup and
    the git hook (on the registry route AND on the legacy route, where the legacy verdict had already
    accepted it) — the only remedy offered, --switch-project, wipes and rebuilds;
  * review r3 #7 (nested projects): a repo nested inside another DeepMap project (work/ and work/api)
    counted as the same project, so api's sync pushed api-relative keys into work's graph as duplicates
    and kept work's stamp; and a switch authorised with --switch-project kept the FOREIGN broader stamp
    over the replaced graph (the next sync refused again).

After a real push to DeepMap's own local base store (bolt 7687), sync now also refreshes the base
store's owner record (provision._record_base_owner, self-guarded), so a later setup while the base is
down can tell its owner (F2 reads it). And the opt-in precise-CALLS path no longer binds a graphify id
COLLISION across languages (W7's repro: py/app.py `format()` -> a root helpers.rb `format`).

  P1  sync._same_project IS the one predicate (delegates to port_registry.owner_is_this_project) and
      has F1's semantics on real directories (a vanished stamp is judged at its nearest existing
      ancestor — also in the no-port_registry mirror).
  P2  E1: a garbled owner stamp -> sync proceeds on the registry route AND the legacy route, re-stamps
      the UTF-8 identity.
  P3  F1 nested projects: refused both ways (escape-hatch and legacy route), zero mutation; a switch
      re-stamps the NEW project (never keeps the foreign broader stamp); same-repo stamps still pass.
  P4  base-owner record: after a real push over sync's own driver to local 7687 it names this project;
      after a refusal over ANOTHER project's live graph there it names THAT project (the live owner — F2
      reads the record while the base is down); never on a per-project port, an injected driver, a
      remote host or a 0-node run; a raising provision never fails the sync; end to end through the real
      provision guard.
  P5  precise CALLS: a key-form callee in another KNOWN language family is dropped (same family kept,
      unknown family kept, flag OFF byte-identical); the REAL engine's collision never reaches the graph.
  P6  a project whose own registry entry claims the shared base 7687, over ANOTHER project's graph there
      (a claim a <=v0.27.4 build wrote by mistake), is refused and told to run /deep-map:setup (F2
      releases the claim) — never "re-run with --switch-project", which would delete the real owner's
      graph — also inside setup's own sync (provision INJECTS NEO4J_URI: not the operator's escape
      hatch), and an explicit --switch-project is not applied there. The operator's escape hatch and a
      per-project instance keep the --switch-project advice (and the operator's switch still runs).
  P7  adopting an unowned non-empty graph names a remedy that works (D6): --stale-cleanup --force-prune,
      not --switch-project (a no-op once the graph is adopted — its owner is this project).
  P8  ROUND-5 G3 (review r4 #13): an owner stamp that is NOT an absolute path on THIS OS ('C:/Users/a/X'
      on POSIX; '/Users/a/X', '\\x' or 'C:rel' on Windows; any relative stamp) is a DIFFERENT project —
      never resolved against the cwd into a "vanished narrower stamp" of the project being synced. Holds
      in the no-port_registry mirror (_same_project_local), in sync's foreign-owner check (_same_project,
      even with a failing port_registry predicate), in the legacy verdict (+ its mirror) and end to end:
      refused on the escape hatch (an explicit --switch-project still replaces it) and on the legacy route
      (never switched), and sync.verify_legacy_owner (the CLI / provision / indexes helper) refuses it.
      sync decides it ITSELF: with a port_registry predicate / verdict stubbed to accept everything,
      _same_project and _legacy_verdict still refuse (in the policy's exact words), and still delegate a
      stamp that is absolute here. Stamps absolute on this OS keep F1's answers.

Hermetic: temp DEEPMAP_HOME + work dirs, a fake `neo4j` module / fake drivers, stubbed pipeline stages.
No sockets, no real Neo4j, never ~/.deepmap. The real graphify engine is used only in P5's last section
(skipped when it is not installed).
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

import port_registry as PR  # noqa: E402

_PASS = _FAIL = 0
_GIT = shutil.which("git")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + ascii(str(detail))[:600]) if detail else ''}")


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_owner_r4", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
    saved = {k: getattr(obj, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


def _norm(p) -> str:
    return os.path.normcase(str(Path(str(p)).resolve())).replace("\\", "/")


def _try(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001 — a crash is a RED value, not a harness error
        return f"raised {type(exc).__name__}: {exc}"


# ---- a fake graph: an owner singleton + a node count, recording every statement ---------------------
class _Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Graph:
    def __init__(self, owner=None, n_nodes=0):
        self.owner, self.n_nodes = owner, n_nodes
        self.stmts = []          # (cypher, params)
        self.stamped = []        # every root written into :_DeepMapMeta
        self.wiped = False       # set by the _wipe_all stub (a --switch-project replacement)
        self.pruned = 0          # set by the _cleanup_stale stub (a same-owner orphan prune)
        self.connectivity = 0

    def writes(self):
        return [c for c, _ in self.stmts if any(w in c for w in ("MERGE", "DELETE", "CREATE", " SET "))]


class _Tx:
    def __init__(self, g):
        self.g = g

    def run(self, cypher, **p):
        self.g.stmts.append((cypher, p))
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sess:
    def __init__(self, g):
        self.g = g

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        g = self.g
        g.stmts.append((cypher, p))
        if "_DeepMapMeta" in cypher and "owning_root" in cypher and "RETURN" in cypher:
            return _Result([{"owning_root": g.owner}] if g.owner else [])
        if "MERGE (m:_DeepMapMeta" in cypher:
            g.stamped.append(p.get("root"))
            g.owner = p.get("root")
            return _Result()
        if "count(n)" in cypher and "_DeepMapMeta" in cypher:
            return _Result([{"c": g.n_nodes}])
        return _Result()

    def begin_transaction(self, *a, **k):
        return _Tx(self.g)


class _Drv:
    def __init__(self, g):
        self.g = g

    def session(self, *a, **k):
        return _Sess(self.g)

    def verify_connectivity(self):
        self.g.connectivity += 1

    def close(self):
        pass


@contextlib.contextmanager
def _fake_neo4j(graph, opened):
    """A fake `neo4j` module whose GraphDatabase.driver records the URI sync opens."""
    saved = sys.modules.get("neo4j")

    def _driver(uri, **kw):
        opened.append(uri)
        return _Drv(graph)

    sys.modules["neo4j"] = SimpleNamespace(GraphDatabase=SimpleNamespace(driver=_driver))
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("neo4j", None)
        else:
            sys.modules["neo4j"] = saved


def _wire(S, pushed, *, nodes=None):
    """Stub the heavy stages; keep the real routing, owner guard, owner stamp and base-owner hook."""
    node = {"key": "a.py::f:1", "label": "Function", "name": "f", "source_file": "a.py",
            "start_line": 1, "body_summary": "f"}
    ns = [dict(node)] if nodes is None else nodes
    S._rebuild_code = lambda root: (list(ns), [], [], "ast")
    S._apply_recognizers = lambda root, n: (n, [])

    def _push(driver, n, c, i, typed_edges=None):
        pushed.append(len(n))
        return (len(n), 0)

    S.push_to_neo4j = _push
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 1
    S._maybe_auto_tune = lambda root: None
    S._wipe_all = lambda d: setattr(d.g, "wiped", True)
    S._cleanup_stale = lambda d, keys: setattr(d.g, "pruned", d.g.pruned + 1)


def _run(S, root, drv=None, **kw):
    err, out = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            ok = S.sync(root, driver=drv, **kw) if drv is not None else S.sync(root, **kw)
    except Exception as exc:  # noqa: BLE001 — a crash is a RED result, not a harness error
        ok = f"raised {type(exc).__name__}: {exc}"
    return ok, err.getvalue() + out.getvalue()


def _registry(home: Path, projects: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "registry.json").write_text(json.dumps(
        {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND, "projects": projects}), encoding="utf-8")


def _entry(root, mcp, bolt=None) -> dict:
    e = {"root": str(root).replace("\\", "/"), "search_port": mcp[0], "embed_port": mcp[1],
         "allocated_utc": "2026-09-01T00:00:00Z", "last_used": "2026-09-01T00:00:00Z"}
    if bolt:
        e.update({"bolt_port": bolt[0], "http_port": bolt[1], "neo4j_allocated_utc": "2026-09-01T00:00:00Z"})
    return e


def _git_repo(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run([_GIT, "init", "-q"], cwd=str(d), capture_output=True, timeout=60)
    (d / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return d.resolve()


def _plain_dir(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return d.resolve()


@contextlib.contextmanager
def _pinned_cp1252():
    """The legacy (pre-UTF-8-fix) code page pinned, portably, exactly as key_migration_gates does."""
    had = hasattr(PR, "_legacy_encodings")
    saved = getattr(PR, "_legacy_encodings", None)
    PR._legacy_encodings = lambda: ["cp1252"]
    try:
        yield
    finally:
        if had:
            PR._legacy_encodings = saved
        else:
            try:
                delattr(PR, "_legacy_encodings")
            except AttributeError:
                pass


# =====================================================================================================
# P1 — the ONE predicate
# =====================================================================================================
def gate_p1_one_predicate(td: Path) -> None:
    print("\n-- P1: sync._same_project IS port_registry.owner_is_this_project (F1 semantics) --")
    S = _load_sync()
    calls = []

    def _spy(root, owner, **k):
        calls.append((str(root), str(owner)))
        return "SPY"

    # Absolute on THIS OS (G3: a stamp that is not — 'C:/...' on POSIX — never reaches the predicate; P8).
    with _patched(PR, owner_is_this_project=_spy):
        got = S._same_project(str(td / "nowhere" / "one"), str(td / "nowhere" / "two"))
    check("sync._same_project delegates to port_registry.owner_is_this_project (one predicate)",
          bool(calls) and got is True, f"calls={calls} got={got!r}")

    work = _plain_dir(td / "p1", "work")                 # a non-git workspace dir ...
    api = _git_repo(work, "api")                         # ... holding its own git repository
    mono = _git_repo(td / "p1", "mono")                  # a git repo ...
    svc = _git_repo(mono, "svc")                         # ... with a nested repository inside it
    (mono / "pkg").mkdir(exist_ok=True)                  # a plain subdir of mono (same project)
    check("the same project path is the same project", S._same_project(str(mono), str(mono)))
    check("an existing subdir stamp inside the SAME repo is the same project (same identity)",
          S._same_project(str(mono), str(mono / "pkg")))
    check("an old narrower stamp whose dir no longer exists is the same project",
          S._same_project(str(mono), str(mono / "gone" / "src")))
    check("a nested repo is NOT its parent workspace's project (api vs owner work)",
          not S._same_project(str(api), str(work)))
    check("a parent workspace is NOT a nested repo's project (work vs owner api)",
          not S._same_project(str(work), str(api)))
    check("a nested git repo inside a git repo is NOT the outer project (svc vs owner mono)",
          not S._same_project(str(svc), str(mono)))
    check("an outer git repo is NOT the nested repo's project (mono vs owner svc)",
          not S._same_project(str(mono), str(svc)))
    sib = _plain_dir(td / "p1", "monoX")
    check("a shared string prefix is not containment (mono vs monoX)",
          not S._same_project(str(mono), str(sib)))
    check("an empty owner is never this project", not S._same_project(str(mono), ""))
    # A vanished narrower stamp is judged at its nearest EXISTING ancestor (port_registry's F1 rule).
    check("a vanished stamp under a same-repo subdir is the same project (mono vs mono/pkg/gone)",
          S._same_project(str(mono), str(mono / "pkg" / "gone")))
    check("a vanished stamp under a NESTED repo is that repo's, not the outer project (mono vs svc/gone)",
          not S._same_project(str(mono), str(svc / "gone")))

    # The fallback used only when port_registry is missing mirrors the same rule (no legacy identities).
    loc = getattr(S, "_same_project_local", None)
    check("sync._same_project_local exists (the no-port_registry mirror)", callable(loc))
    if callable(loc):
        for cur, own, want, what in (
                (mono, mono, True, "the same path"),
                (mono, mono / "pkg", True, "a same-repo subdir"),
                (mono, mono / "gone" / "src", True, "a vanished narrower stamp"),
                (mono, mono / "pkg" / "gone", True, "a vanished stamp under a same-repo subdir"),
                (mono, svc / "gone", False, "a vanished stamp under a nested repo (that repo's)"),
                (api, work, False, "a nested repo vs its non-git parent"),
                (work, api, False, "a non-git parent vs its nested repo"),
                (svc, mono, False, "a nested repo vs the outer repo"),
                (mono, svc, False, "the outer repo vs a nested repo"),
                (mono, sib, False, "a string-prefix sibling"),
                (mono, "", False, "an empty owner")):
            check(f"fallback mirror: {what} -> {want}", bool(loc(str(cur), str(own) if own else own)) is want)
        def _boom(*a, **k):
            raise RuntimeError("simulated predicate failure")

        with _patched(PR, owner_is_this_project=_boom):
            got_same = _try(S._same_project, str(mono), str(mono))
            got_nested = _try(S._same_project, str(api), str(work))
        check("a failing port_registry predicate never raises; the F1 mirror answers (same path -> True)",
              got_same is True, got_same)
        check("... and still refuses a nested project (-> False)", got_nested is False, got_nested)


# =====================================================================================================
# P2 — E1: a pre-fix garbled owner stamp is this project's own graph
# =====================================================================================================
def gate_p2_garbled_owner(td: Path) -> None:
    print("\n-- P2: a cp1252-garbled owner stamp (pre-UTF-8-fix build) is accepted + re-stamped --")
    with _pinned_cp1252():
        repo = _git_repo(td / "p2" / "José", "proj")
        ident = PR.project_identity(repo)
        legacy = PR.legacy_identities(repo)
        garbled = legacy[0] if legacy else None
        check("(fixture) the UTF-8 identity names José and the legacy one is garbled",
              "José" in ident and bool(garbled) and "Ã" in (garbled or ""), (ident, legacy))
        if not garbled:
            return
        key = PR.project_key(repo)

        # (a) the REGISTRY route: the migrated entry (UTF-8 key) with its own bolt pair.
        home = td / "p2" / "home_reg"
        _registry(home, {key: _entry(ident, (8911, 8912), (7695, 7478))})
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=garbled, n_nodes=5)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
            route = PR.resolve_neo4j_route(repo)
            ok, text = _run(S, repo, _Drv(g), cleanup_stale=True)
        check("(fixture) the route is the project's own registered instance", route.source == "registry",
              (route.source, route.uri, route.problem))
        check("registry route: sync proceeds (no REFUSING over its own pre-fix stamp)",
              ok is True and "REFUSING" not in text and pushed == [1], (ok, pushed, text[-600:]))
        check("registry route: no wipe (it is the same project's graph)", g.wiped is False)
        check("registry route: the owner is re-stamped with the UTF-8 identity",
              bool(g.stamped) and _norm(g.stamped[-1]) == _norm(ident), (g.stamped, ident))

        # (b) the LEGACY route (nothing registered -> 7687, verified before use).
        home = td / "p2" / "home_legacy"
        home.mkdir(parents=True, exist_ok=True)
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=garbled, n_nodes=5)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
            route = PR.resolve_neo4j_route(repo)
            ok, text = _run(S, repo, _Drv(g), cleanup_stale=True)
        check("(fixture) the route is the legacy 7687 route", route.source == "legacy-unverified",
              (route.source, route.uri))
        check("legacy route: sync proceeds past BOTH the legacy verdict and the foreign-owner check",
              ok is True and "REFUSING" not in text and pushed == [1], (ok, pushed, text[-600:]))
        check("legacy route: the owner is re-stamped with the UTF-8 identity",
              bool(g.stamped) and _norm(g.stamped[-1]) == _norm(ident), (g.stamped, ident))

        # (c) a garbled stamp of a DIFFERENT project is still foreign.
        other = _git_repo(td / "p2" / "Zoë", "other")
        other_garbled = (PR.legacy_identities(other) or [None])[0]
        home = td / "p2" / "home_other"
        _registry(home, {key: _entry(ident, (8911, 8912), (7695, 7478))})
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=other_garbled, n_nodes=5)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
            ok, text = _run(S, repo, _Drv(g))
        check("another project's garbled stamp is still refused (no extraction, no mutation)",
              ok is False and "REFUSING" in text and pushed == [] and not g.writes(),
              (ok, pushed, g.writes()[:3], text[-300:]))


# =====================================================================================================
# P3 — F1: nested projects are different projects
# =====================================================================================================
def gate_p3_nested(td: Path) -> None:
    print("\n-- P3: a repo nested in another DeepMap project is a DIFFERENT project --")
    work = _plain_dir(td / "p3", "work")
    api = _git_repo(work, "api")
    home = td / "p3" / "home"
    home.mkdir(parents=True, exist_ok=True)

    # (a) escape hatch (NEO4J_URI): api over work's graph -> refused, nothing extracted or written.
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(work), n_nodes=9)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
        ok, text = _run(S, api, _Drv(g), cleanup_stale=True)
    check("api over work's graph (escape hatch) -> REFUSED, the owner named",
          ok is False and "REFUSING" in text and str(work).replace("\\", "/") in text.replace("\\", "/"),
          (ok, text[-400:]))
    check("... no extraction ran and the graph was not touched", pushed == [] and not g.writes(),
          (pushed, g.writes()[:3]))

    # (b) and the other way round: work over api's graph.
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(api), n_nodes=9)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
        ok, text = _run(S, work, _Drv(g), cleanup_stale=True)
    check("work over the nested api's graph (escape hatch) -> REFUSED, nothing written",
          ok is False and "REFUSING" in text and pushed == [] and not g.writes(), (ok, pushed, text[-300:]))

    # (c) --switch-project replaces work's graph with api's AND stamps api (never keeps work's stamp).
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(work), n_nodes=9)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
        ok, text = _run(S, api, _Drv(g), switch_project=True)
    check("--switch-project -> wipe + push", ok is True and g.wiped is True and pushed == [1],
          (ok, g.wiped, pushed, text[-300:]))
    check("--switch-project -> the NEW project is the owner (not the foreign broader stamp)",
          bool(g.stamped) and _norm(g.stamped[-1]) == _norm(api), (g.stamped, str(api)))

    # (d) the LEGACY route (nothing registered): api must not read/write work's 7687 graph.
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(work), n_nodes=9)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        ok, text = _run(S, api, _Drv(g))
    check("legacy route: api over work's graph -> REFUSED with the /deep-map:setup remedy",
          ok is False and "/deep-map:setup" in text and pushed == [] and not g.writes(), (ok, text[-400:]))

    # (e) same-repo stamps still pass (and are re-stamped to the project identity).
    mono = _git_repo(td / "p3", "mono")
    (mono / "pkg").mkdir(exist_ok=True)
    for owner, what in ((str(mono / "pkg"), "an existing same-repo subdir stamp"),
                        (str(mono / "gone"), "an old narrower stamp whose dir is gone")):
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=owner, n_nodes=4)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
            ok, text = _run(S, mono, _Drv(g), cleanup_stale=True)
        check(f"{what} -> sync proceeds, no wipe", ok is True and pushed == [1] and not g.wiped,
              (ok, pushed, g.wiped, text[-300:]))
        check(f"{what} -> re-stamped with the project identity",
              bool(g.stamped) and _norm(g.stamped[-1]) == _norm(mono), (g.stamped, str(mono)))


# =====================================================================================================
# P4 — the base store's owner record after a real push to DeepMap's own local 7687
# =====================================================================================================
def _import_provision():
    import provision  # noqa: E402 — the SAME module object sync's lazy `import provision` gets
    return provision


def gate_p4_base_owner_record(td: Path) -> None:
    print("\n-- P4: a real push to DeepMap's own local base (7687) refreshes the base-owner record --")
    PV = _import_provision()
    proj = _git_repo(td / "p4", "proj")
    ident = PR.project_identity(proj)

    def _one(case, *, env, inject=False, owner=None, n_nodes=0, nodes=None, registry=None, raising=False):
        home = td / "p4" / f"home_{case}"
        home.mkdir(parents=True, exist_ok=True)
        if registry is not None:
            _registry(home, registry)
        S = _load_sync()
        pushed = []
        _wire(S, pushed, nodes=nodes)
        g = _Graph(owner=owner, n_nodes=n_nodes)
        rec = []

        def _record(target=None, *, identity=None):
            rec.append((target, identity))
            if raising:
                raise RuntimeError("simulated provision failure")

        opened = []
        with _patched(PV, _record_base_owner=_record), _env(DEEPMAP_HOME=str(home), **env):
            if inject:
                ok, text = _run(S, proj, _Drv(g))
            else:
                with _fake_neo4j(g, opened):
                    ok, text = _run(S, proj)
        return ok, text, rec, opened, pushed, g

    ok, text, rec, opened, pushed, g = _one("legacy", env={"NEO4J_URI": None})
    check("legacy 7687 route, own driver, real push -> _record_base_owner called once for this root",
          ok is True and opened and opened[0].endswith(":7687") and len(rec) == 1
          and _norm(PR.project_identity(rec[0][0] or "")) == _norm(ident), (ok, opened, rec, text[-300:]))
    check("... after the owner stamp was written", bool(g.stamped))

    ok, text, rec, opened, *_ = _one("env127", env={"NEO4J_URI": "bolt://127.0.0.1:7687"})
    check("NEO4J_URI to the local base (127.0.0.1:7687) -> recorded (the store just stamped is DeepMap's "
          "base; provision's own guard checks it is DeepMap's instance)", ok is True and len(rec) == 1,
          (ok, rec, text[-300:]))

    key = PR.project_key(proj)
    ok, text, rec, opened, *_ = _one("own", env={"NEO4J_URI": None},
                                     registry={key: _entry(ident, (8921, 8922), (7697, 7480))})
    check("its own per-project instance (7697) -> NOT recorded", ok is True and opened
          and opened[0].endswith(":7697") and rec == [], (ok, opened, rec))

    ok, text, rec, *_ = _one("remote", env={"NEO4J_URI": "bolt://db.example.invalid:7687"})
    check("a REMOTE host on 7687 -> NOT recorded", ok is True and rec == [], (ok, rec))

    ok, text, rec, *_ = _one("inject", env={"NEO4J_URI": None}, inject=True)
    check("an injected driver (the push may not be on the resolved URI) -> NOT recorded",
          ok is True and rec == [], (ok, rec))

    ok, text, rec, _o, pushed, _g = _one("zero", env={"NEO4J_URI": None}, nodes=[])
    check("a 0-node run (nothing pushed, nothing stamped) -> NOT recorded", rec == [], (ok, rec, pushed))

    other = _git_repo(td / "p4", "other")
    ok, text, rec, _o, pushed, g = _one("refused", env={"NEO4J_URI": None}, owner=str(other), n_nodes=5)
    check("a refused sync (another project's graph on the local 7687) -> nothing pushed, and the record "
          "names THAT project (the live owner just read — what F2 reads while the base is down), never "
          "this one", ok is False and pushed == [] and len(rec) == 1 and bool(rec[0][1])
          and _norm(rec[0][1]) == _norm(other), (ok, rec, pushed))
    check("... and nothing names this project (no owner-stamp write, no record of it)",
          not g.stamped and all(r[1] and _norm(r[1]) != _norm(ident) for r in rec), (g.stamped, rec))
    ok, text, rec, _o, pushed, g = _one("refused_remote", env={"NEO4J_URI": "bolt://db.example.invalid:7687"},
                                        owner=str(other), n_nodes=5)
    check("a refusal over a REMOTE host's graph -> NOT recorded", ok is False and rec == [], (ok, rec))
    ok, text, rec, _o, pushed, g = _one("refused_inject", env={"NEO4J_URI": None}, inject=True,
                                        owner=str(other), n_nodes=5)
    check("a refusal read through an injected driver -> NOT recorded", ok is False and rec == [], (ok, rec))

    ok, text, rec, *_ = _one("raising", env={"NEO4J_URI": None}, raising=True)
    check("a raising provision._record_base_owner never fails the sync", ok is True and len(rec) == 1,
          (ok, rec, text[-300:]))

    # End to end through the REAL provision._record_base_owner (its guard: DeepMap's own legacy instance
    # is the one running — stubbed True here — and the base store's data dir exists).
    home = td / "p4" / "home_e2e"
    home.mkdir(parents=True, exist_ok=True)
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph()
    opened = []
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        data = PV.paths(None)["data"]
        data.mkdir(parents=True, exist_ok=True)
        with _patched(PV, _legacy_base_instance_alive=lambda: True), _fake_neo4j(g, opened):
            ok, text = _run(S, proj)
        recorded = PV._base_owner_root()
        owner_is = PV._base_owner_is(proj)
    check("end to end: the base store's owner record names this project's identity",
          ok is True and recorded is not None and _norm(recorded) == _norm(ident), (ok, recorded, text[-300:]))
    check("end to end: provision._base_owner_is(project) is now True", owner_is is True)
    check("end to end: the record and the graph's owner stamp name the same project",
          bool(g.stamped) and recorded is not None and _norm(g.stamped[-1]) == _norm(recorded),
          (g.stamped, recorded))
    home2 = td / "p4" / "home_e2e_notalive"
    home2.mkdir(parents=True, exist_ok=True)
    S = _load_sync()
    _wire(S, [])
    g = _Graph()
    with _env(DEEPMAP_HOME=str(home2), NEO4J_URI=None):
        PV.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        with _patched(PV, _legacy_base_instance_alive=lambda: False), _fake_neo4j(g, []):
            ok, _t = _run(S, proj)
        rec2 = PV._base_owner_root()
    check("end to end: no record when DeepMap's own legacy instance is not the one running (a foreign "
          "Neo4j on 7687 proves nothing about the store)", ok is True and rec2 is None, (ok, rec2))
    # End to end: a refusal over ANOTHER project's live graph on DeepMap's own base records that owner.
    home3 = td / "p4" / "home_e2e_refused"
    home3.mkdir(parents=True, exist_ok=True)
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(other), n_nodes=5)
    with _env(DEEPMAP_HOME=str(home3), NEO4J_URI=None):
        PV.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        with _patched(PV, _legacy_base_instance_alive=lambda: True), _fake_neo4j(g, []):
            ok, _t = _run(S, proj)
        rec3 = PV._base_owner_root()
        is_mine, is_other = PV._base_owner_is(proj), PV._base_owner_is(other)
    check("end to end: a refused sync records the live foreign owner (the real provision guard)",
          ok is False and pushed == [] and rec3 is not None and _norm(rec3) == _norm(other), (ok, rec3))
    check("end to end: the record now proves the store is the OTHER project's (not this one's)",
          is_other is True and is_mine is False, (is_other, is_mine))


# =====================================================================================================
# P5 — precise CALLS never bind a graphify id collision across languages
# =====================================================================================================
def _n(key, name, sf, label="Function", line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name}


def gate_p5_precise_calls_family(td: Path) -> None:
    print("\n-- P5: precise CALLS drop a key-form callee in another language family --")
    nodes = [_n("py_app_run", "run", "py/app.py"), _n("py_util_fmt", "fmt", "py/util.py"),
             _n("py_app_local", "local", "py/app.py"),
             _n("helpers_format", "format", "helpers.rb"),
             _n("tpl_x", "x", "tpl/page.tmpl")]
    S = _load_sync()
    _k = S.graphify_extract.call_key                # R11/R1: graphify-RESOLVED callees are MARKED keys
    calls = [("py_app_run", _k("helpers_format")),      # the graphify id collision (Python -> Ruby)
             ("py_app_run", _k("py_util_fmt")),          # same family, cross-file
             ("py_app_run", _k("py_app_local")),         # same family, same file
             ("py_app_run", _k("tpl_x"))]                # unknown family (.tmpl) -> kept (conservative)
    with _env(DEEPMAP_PRECISE_CALLS="1", DEEPMAP_DROP_APP_TO_TEST_CALLS=None):
        edges = S._resolve_calls(nodes, calls)
    check("the cross-family key (py -> helpers.rb) is DROPPED", ("py_app_run", "helpers_format") not in edges,
          edges)
    check("a same-family cross-file key binds exactly, INFERRED",
          edges.get(("py_app_run", "py_util_fmt")) == "INFERRED", edges)
    check("a same-family same-file key binds exactly, EXTRACTED",
          edges.get(("py_app_run", "py_app_local")) == "EXTRACTED", edges)
    check("an unknown-family key is kept (never filtered)", ("py_app_run", "tpl_x") in edges, edges)
    ts = [_n("ts_a", "a", "web/a.ts"), _n("js_b", "b", "web/b.js"), _n("vue_c", "c", "web/c.vue")]
    with _env(DEEPMAP_PRECISE_CALLS="1"):
        e2 = S._resolve_calls(ts, [("ts_a", _k("js_b")), ("vue_c", _k("ts_a"))])
    check("families that mix extensions stay bound (.ts -> .js, .vue -> .ts)",
          ("ts_a", "js_b") in e2 and ("vue_c", "ts_a") in e2, e2)
    with _env(DEEPMAP_PRECISE_CALLS=None):
        off = S._resolve_calls(nodes, [("py_app_run", "format"), ("py_app_run", "fmt")])
    check("flag OFF: the name path is unchanged (language-blind by design: py -> the only `format`)",
          off == {("py_app_run", "helpers_format"): "INFERRED", ("py_app_run", "py_util_fmt"): "INFERRED"},
          off)

    # the REAL engine: W7's repro (graphify ids py/app.py's `format` call by the root helpers.rb's id)
    try:
        import graphify  # noqa: F401
        import graphify_extract as GX
        has_engine = True
    except Exception:
        has_engine = False
    if not has_engine:
        print("     (graphify not installed — real-engine section skipped)")
        return
    files = {"py/app.py": "from helpers import format\n\ndef run():\n    return format(2)\n",
             "helpers.rb": "def format(x)\n  x\nend\n"}

    def _extract(root, fs):
        for rel, text in fs.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        res, report = GX.extract_with_report(root)
        return res, report

    home = td / "p5" / "home"
    home.mkdir(parents=True, exist_ok=True)
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_PRECISE_CALLS="1", DEEPMAP_RESOLVED_IMPORTS=None,
              DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_EXTRACTOR=None,
              DEEPMAP_CASE_INSENSITIVE_EXT=None):
        res, report = _extract(td / "p5" / "mixed", files)
        if not res:
            check("the real engine produced a graph", False, report)
            return
        nodes_r, calls_r, _imports = res
        by = {n["key"]: n["source_file"] for n in nodes_r}
        cross_keyed = [c for c in calls_r if c[1] in by and S.edge_resolve.lang_family(by.get(c[0]))
                       != S.edge_resolve.lang_family(by[c[1]])]
        check("precondition: the engine hands on a cross-language KEY-form call (the id collision)",
              bool(cross_keyed), calls_r)
        edges_r = S._resolve_calls(nodes_r, calls_r)
        cross = [(a, b) for (a, b) in edges_r
                 if S.edge_resolve.lang_family(by.get(a)) != S.edge_resolve.lang_family(by.get(b))]
        check("no persisted :CALLS edge crosses a language family (py/app.py never -> helpers.rb)",
              not cross, sorted(edges_r))
        res2, report2 = _extract(td / "p5" / "py_only", {"py/app.py": files["py/app.py"]})
        edges_py = S._resolve_calls(res2[0], res2[1]) if res2 else None
        mine = {k for k in edges_r if by.get(k[0]) == "py/app.py"}
        check("py/app.py's CALLS are the same with and without the unrelated root helpers.rb",
              edges_py is not None and mine == set(edges_py), (sorted(mine), edges_py))


# =====================================================================================================
# P6 — the refusal on a project REGISTERED on the shared base 7687 never advises wiping the owner's graph
# =====================================================================================================
def gate_p6_base_claim_refusal_hint(td: Path) -> None:
    print("\n-- P6: a base-registered project over another project's graph -> /deep-map:setup, not a wipe --")
    proj = _git_repo(td / "p6", "claimant")
    owner = _git_repo(td / "p6", "realowner")
    home = td / "p6" / "home"
    _registry(home, {PR.project_key(proj): _entry(PR.project_identity(proj), (8931, 8932),
                                                   (PR.BASE_BOLT, PR.BASE_HTTP))})
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(owner), n_nodes=5)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        route = PR.resolve_neo4j_route(proj)
        ok, text = _run(S, proj, _Drv(g), cleanup_stale=True)
    check("(fixture) the claimant's own registered route is the base 7687",
          route.source == "registry" and route.bolt == PR.BASE_BOLT, (route.source, route.uri))
    check("refused, nothing extracted or written", ok is False and pushed == [] and not g.writes(),
          (ok, pushed, g.writes()[:3]))
    check("the remedy is /deep-map:setup (setup releases a base claim whose store is another project's)",
          "/deep-map:setup" in text, text[-500:])
    check("--switch-project is NOT offered as the fix (it would delete the real owner's graph) — named "
          "only as the destructive option it is",
          "Re-run with --switch-project" not in text and ("delete" in text.lower() or "wipe" in text.lower()),
          text[-500:])
    PV = _import_provision()
    base_uri = f"bolt://localhost:{PR.BASE_BOLT}"

    def _case(*, env_uri=None, operator=None, switch=False, drop_provision=False):
        """One sync of the claimant over the real owner's graph. `operator`: provision's own record of
        whether the operator set NEO4J_URI (False = provision injected it for its in-process sync)."""
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=str(owner), n_nodes=5)
        saved_mod = sys.modules.pop("provision", None) if drop_provision else None
        try:
            with contextlib.ExitStack() as st:
                if operator is not None and not drop_provision:
                    st.enter_context(_patched(PV, _USER_SET_NEO4J_URI=operator))
                st.enter_context(_env(DEEPMAP_HOME=str(home), NEO4J_URI=env_uri))
                ok, text = _run(S, proj, _Drv(g), switch_project=switch)
        finally:
            if saved_mod is not None:
                sys.modules["provision"] = saved_mod
        return ok, text, pushed, g

    # /deep-map:setup's OWN sync: provision injected NEO4J_URI (the claimed base) for its in-process sync —
    # that is not the operator's escape hatch, so the setup remedy (never the wipe hint) must be printed.
    ok, text, pushed, g = _case(env_uri=base_uri, operator=False)
    check("setup's in-process sync (NEO4J_URI injected by provision) -> refused with the /deep-map:setup "
          "remedy, never 'Re-run with --switch-project' (review r3 #6's exact path)",
          ok is False and pushed == [] and not g.writes() and "/deep-map:setup" in text
          and "Re-run with --switch-project" not in text, (ok, pushed, text[-500:]))
    # An explicit --switch-project over the claim does NOT delete the real owner's graph: the claim loses
    # to the live owner (F2), exactly as the legacy route never applies --switch-project to the base store.
    for label, kw in (("no NEO4J_URI", {}), ("provision-injected NEO4J_URI", {"env_uri": base_uri,
                                                                            "operator": False})):
        ok, text, pushed, g = _case(switch=True, **kw)
        check(f"--switch-project over a base claim ({label}) -> refused: no wipe, no push, no write",
              ok is False and g.wiped is False and pushed == [] and not g.writes(),
              (ok, g.wiped, pushed, g.writes()[:3], text[-400:]))
        check(f"... ({label}) the message says --switch-project is not applied and names /deep-map:setup",
              "/deep-map:setup" in text and "--switch-project" in text and "not applied" in text,
              text[-500:])
    # the refusal notes the live owner of DeepMap's base store (sync's own driver on 7687)
    S = _load_sync()
    _wire(S, [])
    g = _Graph(owner=str(owner), n_nodes=5)
    rec, opened = [], []
    with _patched(PV, _record_base_owner=lambda target=None, *, identity=None: rec.append((target, identity))), \
            _env(DEEPMAP_HOME=str(home), NEO4J_URI=None), _fake_neo4j(g, opened):
        ok, text = _run(S, proj)
    check("the claim refusal (own driver on the local base) records the base store's live owner — the "
          "real owner, not the claimant", ok is False and opened and opened[0].endswith(f":{PR.BASE_BOLT}")
          and len(rec) == 1 and bool(rec[0][1]) and _norm(rec[0][1]) == _norm(owner), (ok, opened, rec))
    # the operator's escape hatch (a shared instance chosen with NEO4J_URI) keeps the --switch-project advice
    ok, text, pushed, g = _case(env_uri=base_uri, operator=True)
    check("escape hatch (operator NEO4J_URI) -> the --switch-project advice is kept",
          ok is False and "Re-run with --switch-project" in text and "/deep-map:setup" not in text
          and pushed == [], (ok, text[-300:]))
    ok, text, pushed, g = _case(env_uri=base_uri, drop_provision=True)
    check("escape hatch with no provision in the process (any NEO4J_URI is the operator's) -> advice kept",
          ok is False and "Re-run with --switch-project" in text and pushed == [], (ok, text[-300:]))
    ok, text, pushed, g = _case(env_uri=base_uri, operator=True, switch=True)
    check("escape hatch + --switch-project -> the operator's switch runs (wipe + push + the new owner)",
          ok is True and g.wiped is True and pushed == [1] and bool(g.stamped)
          and _norm(g.stamped[-1]) == _norm(proj), (ok, g.wiped, pushed, g.stamped, text[-300:]))
    # a per-project instance holding a foreign stamp keeps the --switch-project advice too
    home2 = td / "p6" / "home2"
    _registry(home2, {PR.project_key(proj): _entry(PR.project_identity(proj), (8931, 8932), (7699, 7482))})
    S = _load_sync()
    _wire(S, [])
    g = _Graph(owner=str(owner), n_nodes=5)
    with _env(DEEPMAP_HOME=str(home2), NEO4J_URI=None):
        ok, text = _run(S, proj, _Drv(g))
    check("its own per-project instance -> the --switch-project advice is kept",
          ok is False and "Re-run with --switch-project" in text, (ok, text[-300:]))


# =====================================================================================================
# P7 — the unowned-graph adoption notice offers a remedy that works (D6)
# =====================================================================================================
def gate_p7_adoption_remedy(td: Path) -> None:
    print("\n-- P7: adopting an unowned non-empty graph names a remedy that actually works --")
    proj = _git_repo(td / "p7", "proj")
    home = td / "p7" / "home"
    home.mkdir(parents=True, exist_ok=True)
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=None, n_nodes=7)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
        ok, text = _run(S, proj, _Drv(g))
    check("(fixture) an unowned non-empty graph is adopted, with the notice",
          ok is True and "adopting" in text and bool(g.stamped), (ok, text[-300:]))
    # Once adopted, the graph's owner IS this project, so --switch-project is a no-op there (no wipe):
    S = _load_sync()
    _wire(S, [])
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
        ok2, _t2 = _run(S, proj, _Drv(g), switch_project=True)
    check("(evidence) after the adoption, --switch-project wipes nothing (the owner is this project now)",
          ok2 is True and g.wiped is False, (ok2, g.wiped))
    line = next((ln for ln in text.splitlines() if "adopting" in ln), "")
    check("the notice does not advise --switch-project (a no-op once the graph is adopted)",
          "--switch-project" not in line, line)
    check("the notice names the remedy that drops the other project's leftovers: --stale-cleanup "
          "--force-prune", "--stale-cleanup" in line and "--force-prune" in line, line)


# =====================================================================================================
# P8 — ROUND-5 G3: an owner stamp that is not an absolute path ON THIS OS is a DIFFERENT project
# =====================================================================================================
@contextlib.contextmanager
def _cwd(p: Path):
    saved = os.getcwd()
    os.chdir(str(p))
    try:
        yield
    finally:
        os.chdir(saved)


@contextlib.contextmanager
def _without_attr(obj, name: str):
    """Temporarily REMOVE an attribute (simulates a port_registry that lacks a predicate)."""
    had = hasattr(obj, name)
    saved = getattr(obj, name, None)
    if had:
        delattr(obj, name)
    try:
        yield
    finally:
        if had:
            setattr(obj, name, saved)


def _foreign_os_stamps(proj: Path) -> list:
    """(stamp, what) owner stamps that are NOT absolute on THIS OS and that Path(stamp).resolve() rebases
    onto the working directory — run from the project root (the normal case) they land AT or INSIDE the
    project (a 'vanished narrower stamp'), which is how the old rule read another machine's stamp as this
    project's own graph (review r4 #13)."""
    stamps = [(".", "a bare relative stamp '.'"),
              ("gone/x", "a relative stamp 'gone/x'")]
    if os.name == "nt":
        drive = Path(proj).drive                        # 'C:'
        tail = str(proj)[len(drive):]                   # '\\Users\\...\\proj' — root-relative on Windows
        posix = tail.replace("\\", "/")
        stamps += [(posix, "a POSIX stamp naming the same path without the drive (macOS '/Users/a/X')"),
                   (posix + "/gone", "a POSIX stamp strictly inside the project path (vanished)"),
                   (tail, "a root-relative Windows stamp (no drive letter)"),
                   (drive + "gone", "a drive-RELATIVE stamp ('C:gone')")]
    else:
        stamps += [("C:/Users/x/proj", "a Windows drive stamp with forward slashes"),
                   ("C:\\Users\\x\\proj", "a Windows drive stamp as sync writes it on Windows"),
                   ("C:" + str(proj), "a Windows drive stamp over this project's own path")]
    return stamps


def gate_p8_non_absolute_owner(td: Path) -> None:
    print("\n-- P8 (G3): an owner stamp that is not absolute ON THIS OS is a DIFFERENT project --")
    proj = _git_repo(td / "p8", "proj")
    home = td / "p8" / "home"
    home.mkdir(parents=True, exist_ok=True)
    S = _load_sync()
    loc = getattr(S, "_same_project_local", None)
    check("(fixture) sync._same_project_local exists", callable(loc))
    stamps = _foreign_os_stamps(proj)
    # sync keeps its OWN absolute-on-this-OS test (the mirror must work without port_registry): it must give
    # port_registry's answer for every stamp shape, or the two layers would disagree on who owns a graph.
    mine, theirs = getattr(S, "_abs_on_this_os", None), getattr(PR, "_abs_on_this_os", None)
    check("(fixture) sync._abs_on_this_os and port_registry._abs_on_this_os exist", callable(mine) and callable(theirs))
    if callable(mine) and callable(theirs):
        probes = [s for s, _ in stamps] + [str(proj), str(proj).replace("\\", "/"), str(proj / "gone"), "/",
                                           "//srv/share/x", "\\\\srv\\share\\x", "C:", "C:/", "C:\\", "c:\\x",
                                           "\\\\?\\C:\\x", "", None, 5, "/home/b/Y", "C:/Users/a/X"]
        bad = [(p, mine(p), theirs(p)) for p in probes if bool(mine(p)) is not bool(theirs(p))]
        check("sync._abs_on_this_os agrees with port_registry._abs_on_this_os on every stamp shape (this OS)",
              not bad, bad)
    with _cwd(proj):                                   # the normal case: run from the project root
        for stamp, what in stamps:
            lands = _norm(Path(stamp).resolve())
            check(f"(fixture) {what} is NOT absolute on this OS and resolve() lands in the project",
                  not Path(stamp).is_absolute() and (lands == _norm(proj) or lands.startswith(_norm(proj) + "/")),
                  (stamp, lands))
            if callable(loc):
                got = _try(loc, str(proj), stamp)
                check(f"mirror _same_project_local: {what} -> a different project (False)", got is False,
                      (stamp, got))
            got = _try(S._same_project, str(proj), stamp)
            check(f"_same_project (the sync foreign check): {what} -> a different project (False)",
                  got is False, (stamp, got))

            def _boom(*a, **k):
                raise RuntimeError("simulated predicate failure")

            with _patched(PR, owner_is_this_project=_boom):
                got = _try(S._same_project, str(proj), stamp)
            check(f"_same_project with a failing port_registry predicate: {what} -> False", got is False,
                  (stamp, got))
            with _without_attr(PR, "legacy_owner_verdict"):
                v = _try(S._legacy_verdict, proj, stamp, None)
            check(f"_legacy_verdict mirror (no port_registry verdict): {what} -> refused",
                  isinstance(v, tuple) and v[0] is False and "/deep-map:setup" in str(v[1]), (stamp, v))

        # Non-regression: stamps that ARE absolute on this OS keep F1's answers.
        fwd = str(proj).replace("\\", "/")
        for own, want, what in ((str(proj), True, "this project's own stamp"),
                                (fwd, True, "this project's own stamp with forward slashes"),
                                (str(proj / "gone" / "x"), True, "an ABSOLUTE vanished narrower stamp"),
                                (str(td / "p8" / "elsewhere"), False, "an absolute disjoint stamp")):
            if callable(loc):
                check(f"mirror keeps F1 for {what} -> {want}", _try(loc, str(proj), own) is want, own)
            check(f"_same_project keeps F1 for {what} -> {want}", _try(S._same_project, str(proj), own) is want,
                  own)

        # sync decides a cross-OS stamp ITSELF, before any port_registry policy: a predicate / verdict that
        # would (wrongly) accept it — an older port_registry, a regression there — cannot make sync merge
        # into another machine's graph. The stubs below accept EVERYTHING, so only sync's own guard (in
        # _same_project and in _legacy_verdict) can produce the refusal; each is also still the delegate
        # for a stamp that IS absolute here (the stub's answer comes back).
        def _lenient_pred(*a, **k):
            return True

        def _lenient_verdict(*a, **k):
            return True, None

        for stamp, what in stamps:
            with _patched(PR, owner_is_this_project=_lenient_pred):
                got = _try(S._same_project, str(proj), stamp)
            check(f"_same_project decides a cross-OS stamp itself (a predicate that accepts everything is not "
                  f"asked): {what} -> False", got is False, (stamp, got))
            with _patched(PR, legacy_owner_verdict=_lenient_verdict):
                v = _try(S._legacy_verdict, proj, stamp, None)
            check(f"_legacy_verdict decides a cross-OS stamp itself (a verdict that accepts everything is not "
                  f"asked): {what} -> refused with /deep-map:setup",
                  isinstance(v, tuple) and v[0] is False and "/deep-map:setup" in str(v[1]), (stamp, v))
            # ... in the policy's own words (the user reads the same refusal whichever layer decided it).
            for uri in (None, "bolt://localhost:7687"):
                mine = _try(S._legacy_verdict, proj, stamp, None, uri)
                theirs = _try(PR.legacy_owner_verdict, proj, stamp, None, uri=uri)
                check(f"_legacy_verdict's cross-OS refusal is worded exactly as port_registry's policy "
                      f"(uri={uri}): {what}", mine == theirs and isinstance(mine, tuple) and mine[0] is False,
                      (mine, theirs))
        with _patched(PR, owner_is_this_project=lambda *a, **k: "DELEGATED"):
            got = _try(S._same_project, str(proj), str(td / "p8" / "elsewhere"))
        check("_same_project still delegates an absolute stamp to the one predicate", got is True, got)
        with _patched(PR, legacy_owner_verdict=lambda *a, **k: ("DELEGATED", None)):
            v = _try(S._legacy_verdict, proj, str(td / "p8" / "elsewhere"), None)
        check("_legacy_verdict still delegates an absolute stamp to the policy", v == ("DELEGATED", None), v)

        pick = stamps[2][0]                             # the realistic cross-OS stamp for this OS
        # (a) the operator's escape hatch (one shared Neo4j across machines): refused, nothing touched.
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=pick, n_nodes=9)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
            ok, text = _run(S, proj, _Drv(g), cleanup_stale=True)
        check("escape hatch: a cross-OS owner stamp -> REFUSED (not merged into as 'this project')",
              ok is False and "REFUSING" in text, (ok, pick, text[-400:]))
        check("... no extraction ran, nothing written, the stamp kept", pushed == [] and not g.writes()
              and g.owner == pick, (pushed, g.writes()[:3], g.owner))
        # (b) ... and the operator's explicit --switch-project replaces it and stamps THIS project.
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        g = _Graph(owner=pick, n_nodes=9)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://localhost:7999"):
            ok, text = _run(S, proj, _Drv(g), switch_project=True)
        check("escape hatch + --switch-project: wipe + push, the owner re-stamped with this project",
              ok is True and g.wiped is True and pushed == [1] and bool(g.stamped)
              and _norm(g.stamped[-1]) == _norm(proj), (ok, g.wiped, pushed, g.stamped))
        # (c) the LEGACY route (nothing registered -> 7687): refused with the /deep-map:setup remedy, and
        # --switch-project is not applied there (it would delete that other machine's graph).
        for sw in (False, True):
            S = _load_sync()
            pushed = []
            _wire(S, pushed)
            g = _Graph(owner=pick, n_nodes=9)
            with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
                route = PR.resolve_neo4j_route(proj)
                ok, text = _run(S, proj, _Drv(g), switch_project=sw)
            check(f"(fixture) the legacy 7687 route (switch_project={sw})", route.source == "legacy-unverified",
                  (route.source, route.uri))
            check(f"legacy route (switch_project={sw}): a cross-OS stamp -> REFUSED with /deep-map:setup, "
                  f"no wipe, nothing written",
                  ok is False and "/deep-map:setup" in text and pushed == [] and not g.writes()
                  and g.wiped is False, (ok, pushed, g.wiped, text[-400:]))
        # (d) sync.verify_legacy_owner — what the CLI (reads), provision and indexes call on that route.
        S = _load_sync()
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
            v = _try(S.verify_legacy_owner, _Drv(_Graph(owner=pick, n_nodes=9)), proj)
            own = _try(S.verify_legacy_owner, _Drv(_Graph(owner=str(proj), n_nodes=9)), proj)
        check("verify_legacy_owner: a cross-OS stamp -> (False, a /deep-map:setup remedy)",
              isinstance(v, tuple) and v[0] is False and "/deep-map:setup" in str(v[1]), v)
        check("verify_legacy_owner: this project's own absolute stamp -> (True, None)", own == (True, None), own)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("DeepMap — sync owner predicate / base-owner record / precise-CALLS family (round 4, W6)")
    print("=" * 78)
    if not _GIT:
        print("git is not installed — these gates need real repositories; SKIPPED")
        print("RESULT: GREEN — 0/0 gates passed.")
        return 0
    td = Path(tempfile.mkdtemp(prefix="dmsop_")).resolve()
    try:
        with _env(DEEPMAP_HOME=str(td / "home_default"), NEO4J_URI=None, DEEPMAP_PRECISE_CALLS=None,
                  DEEPMAP_AUTO_GITIGNORE="0", DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None):
            for gate in (gate_p1_one_predicate, gate_p2_garbled_owner, gate_p3_nested,
                         gate_p4_base_owner_record, gate_p5_precise_calls_family,
                         gate_p6_base_claim_refusal_hint, gate_p7_adoption_remedy,
                         gate_p8_non_absolute_owner):
                try:
                    gate(td)
                except Exception as exc:  # noqa: BLE001 — a crash is a RED result, not a harness error
                    import traceback
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
