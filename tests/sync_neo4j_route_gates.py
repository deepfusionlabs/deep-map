#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: sync and indexes follow the ONE Neo4j routing rule (ROUND-2 D1).

sync() and `python build/indexes.py` used to wrap port_registry.neo4j_uri_for in a bare
`except Exception: NEO4J_URI or bolt://localhost:7687`. The resolver now raises for a registry that
exists but can't be read, and for a project with no instance while another project owns 7687 — and
both callers swallowed that into 7687. /deep-map:refresh or the git post-commit hook then wrote this
project's graph into ANOTHER project's instance (or hit its owner guard, whose "--switch-project"
advice wipes that project). And an unregistered project got bolt 7687 with no check that the graph
there is its own.

  R1  a registry that exists but is unrecognised -> sync returns False with the registry message; no
      driver is created and nothing is read or written (never a silent 7687).
  R2  no instance of its own while ANOTHER registered project owns 7687 -> False, names /deep-map:setup.
  R3  the LEGACY 7687 route (nothing registered) is VERIFIED before use: a graph owned by another
      project, or an unowned non-empty graph, is refused with "run /deep-map:setup"; --switch-project
      does not override it; an unowned EMPTY graph or this project's own graph proceeds.
  R4  the escape hatch (NEO4J_URI) and the project's own registered instance keep today's behaviour
      (switch guard, legacy adoption).
  R5  sync.verify_legacy_owner(driver, root) -> (ok, reason) is the helper the CLI and provision call;
      an outage while reading the owner propagates (fail loud, never a fake verdict).
  R6  without an injected driver, sync opens the RESOLVED URI (the project's registered bolt port).
  R7  `python build/indexes.py` refuses the same way instead of creating indexes on 7687.
  R8  only a missing port_registry module falls back to NEO4J_URI / 7687.

Hermetic: temp DEEPMAP_HOME, a fake `neo4j` module where a driver is opened, fake drivers, stubbed
pipeline stages. No sockets, no real Neo4j.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import runpy
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


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_route", BUILD / "sync.py")
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


# ---- a fake graph: an owner singleton + a node count, recording every statement ---------------------
class _Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Graph:
    def __init__(self, owner=None, n_nodes=0, fail_reads=False):
        self.owner, self.n_nodes, self.fail_reads = owner, n_nodes, fail_reads
        self.stmts = []
        self.connectivity = 0
        self.uri = None

    def writes(self):
        return [c for c in self.stmts if any(w in c for w in ("MERGE", "DELETE", "CREATE", " SET "))]


class _Tx:
    def __init__(self, g):
        self.g = g

    def run(self, cypher, **p):
        self.g.stmts.append(cypher)
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
        self.g.stmts.append(cypher)
        if self.g.fail_reads:
            raise ConnectionError("simulated outage while reading the owner")
        if "_DeepMapMeta" in cypher and "owning_root" in cypher and "RETURN" in cypher:
            return _Result([{"owning_root": self.g.owner}] if self.g.owner else [])
        if "count(n)" in cypher and "_DeepMapMeta" in cypher:
            return _Result([{"c": self.g.n_nodes}])
        if "SHOW INDEXES" in cypher and "state" in cypher:     # every deepmap_* index ONLINE
            import indexes as _ix
            names = ([n for _, n in _ix.VECTOR_INDEXES] + [n for n, _, _ in _ix.FTS_INDEXES]
                     + [n for _, n in _ix.KEY_INDEXES])
            return _Result([{"name": n, "state": "ONLINE"} for n in names])
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
    """Install a fake `neo4j` module whose GraphDatabase.driver records the URI it is asked for."""
    saved = sys.modules.get("neo4j")

    def _driver(uri, **kw):
        opened.append(uri)
        graph.uri = uri
        return _Drv(graph)

    sys.modules["neo4j"] = SimpleNamespace(GraphDatabase=SimpleNamespace(driver=_driver))
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("neo4j", None)
        else:
            sys.modules["neo4j"] = saved


def _wire(S, pushed):
    """Stub the heavy stages; keep the real routing, owner guard and owner stamp."""
    node = {"key": "a.py::f:1", "label": "Function", "name": "f", "source_file": "a.py",
            "start_line": 1, "body_summary": "f"}
    S._rebuild_code = lambda root: ([dict(node)], [], [], "ast")
    S._apply_recognizers = lambda root, nodes: (nodes, [])

    def _push(driver, n, c, i, typed_edges=None):
        pushed.append(len(n))
        return (len(n), 0)

    S.push_to_neo4j = _push
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 1
    S._maybe_auto_tune = lambda root: None


def _registry(home: Path, projects: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "registry.json").write_text(json.dumps(
        {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND, "projects": projects}), encoding="utf-8")


def _project(td: Path, name: str) -> Path:
    p = td / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return p


def _run(S, root, drv=None, **kw):
    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        ok = S.sync(root, driver=drv, **kw) if drv is not None else S.sync(root, **kw)
    return ok, err.getvalue() + out.getvalue()


# =====================================================================================================
def gate_r1_unreadable_registry(td: Path) -> None:
    print("\n-- R1: an unrecognised registry fails LOUD (never a silent 7687) --")
    home = td / "home_r1"
    home.mkdir()
    (home / "registry.json").write_text("{ this is not json", encoding="utf-8")
    root = _project(td, "r1proj")
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph()
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        ok, text = _run(S, root, _Drv(g))
        lock7687 = S._instance_lock_path("bolt://localhost:7687").exists()
    check("sync returns False", ok is False, f"ok={ok}")
    check("the registry problem is printed (names the registry and /deep-map:setup)",
          "registry" in text.lower() and "/deep-map:setup" in text, text[-500:])
    check("nothing was read from or written to any Neo4j", g.stmts == [] and g.connectivity == 0,
          g.stmts[:3])
    check("nothing was pushed", pushed == [], pushed)
    check("no instance lock was taken for bolt 7687", not lock7687)
    opened = []
    g2 = _Graph()
    S = _load_sync()
    _wire(S, [])
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None), _fake_neo4j(g2, opened):
        ok2, _ = _run(S, root)
    check("without an injected driver no driver is even opened", ok2 is False and opened == [], opened)


def gate_r2_no_instance(td: Path) -> None:
    print("\n-- R2: no instance of its own while ANOTHER project owns 7687 -> refuse, name setup --")
    home = td / "home_r2"
    other = _project(td, "r2owner")
    _registry(home, {PR.project_key(other): {"root": str(other), "bolt_port": 7687, "http_port": 7474,
                                              "search_port": 8787, "embed_port": 8788}})
    root = _project(td, "r2proj")
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(other.resolve()), n_nodes=50)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        ok, text = _run(S, root, _Drv(g))
    check("sync returns False", ok is False, f"ok={ok}")
    check("the message says this project has no instance and names /deep-map:setup",
          "no DeepMap Neo4j instance" in text and "/deep-map:setup" in text, text[-500:])
    check("it never suggests --switch-project (that would wipe the other project)",
          "--switch-project" not in text, text[-400:])
    check("the other project's graph was not touched", g.stmts == [] and pushed == [], g.stmts[:3])


def gate_r3_legacy_verified(td: Path) -> None:
    print("\n-- R3: the legacy 7687 route is VERIFIED before a read or a write --")
    home = td / "home_r3"                   # no registry at all: every project routes legacy 7687
    home.mkdir()
    root = _project(td, "r3proj")
    other = _project(td, "r3other")

    cases = [
        ("another project's graph", _Graph(owner=str(other.resolve()), n_nodes=40), {}, False),
        ("another project's graph + --switch-project", _Graph(owner=str(other.resolve()), n_nodes=40),
         {"switch_project": True}, False),
        ("an unowned NON-empty graph (not provably ours)", _Graph(owner=None, n_nodes=40), {}, False),
        ("an unowned non-empty graph + --stale-cleanup", _Graph(owner=None, n_nodes=40),
         {"cleanup_stale": True}, False),
        ("an unowned EMPTY graph (fresh)", _Graph(owner=None, n_nodes=0), {}, True),
        ("this project's own graph", _Graph(owner=str(root.resolve()), n_nodes=40), {}, True),
    ]
    for label, g, kw, want in cases:
        S = _load_sync()
        pushed = []
        _wire(S, pushed)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
            ok, text = _run(S, root, _Drv(g), **kw)
        if want:
            check(f"{label}: proceeds (pushed + owner stamped)",
                  ok is True and pushed == [1] and any("_DeepMapMeta" in c and "MERGE" in c for c in g.stmts),
                  f"ok={ok} pushed={pushed} {text[-300:]}")
        else:
            check(f"{label}: REFUSED — nothing pushed, nothing written",
                  ok is False and pushed == [] and g.writes() == [], f"ok={ok} writes={g.writes()[:3]}")
            check(f"{label}: the refusal names /deep-map:setup",
                  "/deep-map:setup" in text, text[-400:])
            check(f"{label}: no 'Re-run with --switch-project to replace it' advice",
                  "to replace it" not in text, text[-400:])
            check(f"{label}: no graph wipe (DETACH DELETE) was issued",
                  not any("DETACH DELETE" in c for c in g.stmts), g.stmts)


def gate_r4_env_and_registry_unchanged(td: Path) -> None:
    print("\n-- R4: the escape hatch and a project's OWN registered instance keep today's behaviour --")
    home = td / "home_r4"
    home.mkdir()
    root = _project(td, "r4proj")
    other = _project(td, "r4other")
    # escape hatch: NEO4J_URI -> the owner/switch guard is the only protection, and --switch-project works
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(other.resolve()), n_nodes=40)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://shared-host:7687"):
        ok, text = _run(S, root, _Drv(g), switch_project=True)
    check("NEO4J_URI + foreign owner + --switch-project: the switch runs (wipe + push)",
          ok is True and pushed == [1] and any("DETACH DELETE" in c for c in g.stmts), f"ok={ok} {text[-300:]}")
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(other.resolve()), n_nodes=40)
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://shared-host:7687"):
        ok, text = _run(S, root, _Drv(g))
    check("NEO4J_URI + foreign owner, no switch: refused by the owner guard as before",
          ok is False and pushed == [] and "--switch-project" in text, text[-300:])
    # the project's own registered instance: an unowned populated graph is adopted as before
    home2 = td / "home_r4b"
    _registry(home2, {PR.project_key(root): {"root": str(root), "bolt_port": 7689, "http_port": 7476}})
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=None, n_nodes=40)
    with _env(DEEPMAP_HOME=str(home2), NEO4J_URI=None):
        ok, text = _run(S, root, _Drv(g))
    check("registered route + unowned populated graph: adopted (it is this project's own instance)",
          ok is True and pushed == [1], f"ok={ok} {text[-300:]}")


def gate_r5_verify_helper(td: Path) -> None:
    print("\n-- R5: sync.verify_legacy_owner(driver, root) -> (ok, reason) --")
    S = _load_sync()
    fn = getattr(S, "verify_legacy_owner", None)
    check("sync.verify_legacy_owner exists", callable(fn))
    if not callable(fn):
        return
    root = _project(td, "r5proj")
    other = _project(td, "r5other")
    sub = root / "pkg"
    sub.mkdir()
    # ROUND-4 F1: a subdirectory is the SAME project only when it maps to the same project identity —
    # i.e. inside the same git repository (r5proj is one). A folder under a NON-git dir, or a nested
    # repository, is a project of its own (the registry keys it separately).
    git = shutil.which("git")
    cases = [("own owner", _Graph(owner=str(root.resolve()), n_nodes=3), root, True),
             ("foreign owner", _Graph(owner=str(other.resolve()), n_nodes=3), root, False),
             ("unowned empty", _Graph(owner=None, n_nodes=0), root, True),
             ("unowned non-empty", _Graph(owner=None, n_nodes=7), root, False)]
    if git:
        subprocess.run([git, "init", "-q"], cwd=str(root), capture_output=True, timeout=60)
        nested = root / "nested"
        nested.mkdir()
        subprocess.run([git, "init", "-q"], cwd=str(nested), capture_output=True, timeout=60)
        plain = _project(td, "r5plain")
        (plain / "inner").mkdir()
        cases += [("a subdirectory of the owning repository", _Graph(owner=str(root.resolve()), n_nodes=3),
                   sub, True),
                  ("a repository nested in the owning project (F1: its own project)",
                   _Graph(owner=str(root.resolve()), n_nodes=3), nested, False),
                  ("a folder inside a NON-git owner (F1: its own project)",
                   _Graph(owner=str(plain.resolve()), n_nodes=3), plain / "inner", False)]
    else:
        print("     (git not installed — the same-identity subdirectory cases are skipped)")
    for label, g, where, want in cases:
        res = fn(_Drv(g), where)
        ok_shape = isinstance(res, tuple) and len(res) == 2
        check(f"{label}: returns an (ok, reason) pair", ok_shape, repr(res))
        if not ok_shape:
            continue
        ok, reason = res
        check(f"{label}: ok == {want}", bool(ok) is want, repr(res))
        if want:
            check(f"{label}: no reason when ok", reason is None, repr(reason))
        else:
            check(f"{label}: the reason names /deep-map:setup", isinstance(reason, str)
                  and "/deep-map:setup" in reason, repr(reason))
        check(f"{label}: read-only (no writes)", g.writes() == [], g.writes())
    raised = False
    try:
        fn(_Drv(_Graph(fail_reads=True)), root)
    except ConnectionError:
        raised = True
    except Exception as e:  # noqa: BLE001
        raised = f"wrong exception {type(e).__name__}"
    check("an outage while reading the owner propagates (fail loud, never a fake verdict)", raised is True,
          str(raised))


def gate_r6_opens_resolved_uri(td: Path) -> None:
    print("\n-- R6: without an injected driver, sync opens THIS project's registered bolt port --")
    home = td / "home_r6"
    root = _project(td, "r6proj")
    _registry(home, {PR.project_key(root): {"root": str(root), "bolt_port": 7695, "http_port": 7482}})
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    g = _Graph(owner=str(root.resolve()), n_nodes=1)
    opened = []
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None), _fake_neo4j(g, opened):
        ok, text = _run(S, root)
        lock_ok = S._instance_lock_path("bolt://127.0.0.1:7695").exists()
        lock_base = S._instance_lock_path("bolt://127.0.0.1:7687").exists()
        lock_legacy = S._instance_lock_path("bolt://localhost:7695").exists()
    # ROUND-8 J4: the registry route dials 127.0.0.1 (the address the instance binds), never localhost
    check("the driver is opened on bolt://127.0.0.1:7695", opened == ["bolt://127.0.0.1:7695"], opened)
    check("the sync succeeds", ok is True and pushed == [1], f"ok={ok} {text[-300:]}")
    check("the instance lock is keyed on the resolved URI (not 7687)", lock_ok and not lock_base,
          f"7695={lock_ok} 7687={lock_base}")
    check("...and it is the SAME lock file an older build's bolt://localhost:7695 sync takes (J4)", lock_legacy)


def _run_indexes_main(cwd: Path, graph: _Graph, opened: list):
    out = io.StringIO()
    code = None
    saved_cwd = os.getcwd()
    saved_argv = sys.argv[:]
    try:
        os.chdir(str(cwd))
        sys.argv = [str(BUILD / "indexes.py")]
        with _fake_neo4j(graph, opened), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                runpy.run_path(str(BUILD / "indexes.py"), run_name="__main__")
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        os.chdir(saved_cwd)
        sys.argv = saved_argv
    return code, out.getvalue()


def gate_r7_indexes_main(td: Path) -> None:
    print("\n-- R7: `python build/indexes.py` follows the same rule --")
    home = td / "home_r7"
    home.mkdir()
    (home / "registry.json").write_text("[1, 2, 3]", encoding="utf-8")        # unrecognised shape
    proj = _project(td, "r7proj")
    g, opened = _Graph(), []
    with _env(DEEPMAP_HOME=str(home), NEO4J_URI=None):
        code, text = _run_indexes_main(proj, g, opened)
    check("unrecognised registry: exits non-zero", code not in (0, None), f"code={code}")
    check("...prints the registry problem", "registry" in text.lower(), text[-400:])
    check("...never opens a driver (no 7687 fallback)", opened == [], opened)
    check("...creates no index anywhere", not any("CREATE" in c for c in g.stmts), g.stmts[:3])

    # the legacy route, another project's graph on 7687: refuse before creating indexes
    home2 = td / "home_r7b"
    home2.mkdir()
    other = _project(td, "r7other")
    g, opened = _Graph(owner=str(other.resolve()), n_nodes=9), []
    with _env(DEEPMAP_HOME=str(home2), NEO4J_URI=None):
        code, text = _run_indexes_main(proj, g, opened)
    check("legacy route onto another project's graph: exits non-zero", code not in (0, None), f"code={code}")
    check("...names /deep-map:setup", "/deep-map:setup" in text, text[-400:])
    check("...creates no index on it", not any("CREATE" in c for c in g.stmts), g.stmts[:4])

    # the project's own registered instance: indexes are created there
    home3 = td / "home_r7c"
    _registry(home3, {PR.project_key(proj): {"root": str(proj), "bolt_port": 7697, "http_port": 7484}})
    g, opened = _Graph(owner=str(proj.resolve()), n_nodes=1), []
    with _env(DEEPMAP_HOME=str(home3), NEO4J_URI=None):
        code, text = _run_indexes_main(proj, g, opened)
    check("registered route: the driver is opened on the project's own port (127.0.0.1, J4)",
          opened == ["bolt://127.0.0.1:7697"], opened)
    check("...and the indexes are created", any("CREATE" in c for c in g.stmts), g.stmts[:2])


def gate_r8_missing_module(td: Path) -> None:
    print("\n-- R8: only a MISSING port_registry module falls back to NEO4J_URI / 7687 --")
    home = td / "home_r8"
    home.mkdir()
    (home / "registry.json").write_text("{ garbage", encoding="utf-8")
    root = _project(td, "r8proj")
    S = _load_sync()
    pushed = []
    _wire(S, pushed)
    saved = sys.modules.get("port_registry")
    sys.modules["port_registry"] = None           # `import port_registry` -> ImportError
    try:
        g = _Graph(owner=str(root.resolve()), n_nodes=1)
        with _env(DEEPMAP_HOME=str(home), NEO4J_URI="bolt://byo:7999"):
            ok, text = _run(S, root, _Drv(g))
    finally:
        if saved is None:
            sys.modules.pop("port_registry", None)
        else:
            sys.modules["port_registry"] = saved
    check("module missing + NEO4J_URI -> the sync runs on the operator's URI", ok is True and pushed == [1],
          f"ok={ok} {text[-300:]}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — sync / indexes Neo4j routing gates (ROUND-2 D1; no Neo4j)")
    print("=" * 68)
    td = Path(tempfile.mkdtemp(prefix="dm_syncroute_"))
    try:
        with _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None,
                  DEEPMAP_AUTO_GITIGNORE="0", NEO4J_URI=None, DEEPMAP_HOME=str(td / "home_default")):
            for gate in (gate_r1_unreadable_registry, gate_r2_no_instance, gate_r3_legacy_verified,
                         gate_r4_env_and_registry_unchanged, gate_r5_verify_helper,
                         gate_r6_opens_resolved_uri, gate_r7_indexes_main, gate_r8_missing_module):
                try:
                    gate(td)
                except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate
                    check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
