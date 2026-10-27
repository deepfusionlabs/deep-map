#!/usr/bin/env python3
"""ISS-build:017 (D8) — a failed "does the graph hold nodes?" read means UNKNOWN, and sync fails SAFE on it.

Finding (DeepInit refresh 2026-09-29, validated against the code): `sync._graph_holds_nodes` mapped ANY error to
False — its docstring promised "the caller then keeps the old behaviour" — but `_sync_pass` reads False as "the
graph is EMPTY". One failed node-count query (a dropped Bolt connection, a transient DatabaseError) then:
  1. cleared the engine memory (the prior manifest's graph_engines) when this run resolved to another engine, so
     the engine-DOWNGRADE refusal (round 7, T7#11) was skipped: an 'ast' fallback pushed its nodes next to
     graphify's — a second copy of every Python symbol — and the manifest's graph_engines became ['ast'], erasing
     the guard's memory for every later run;
  2. on a 0-node run, read the kept graph as absent, so its manifest was overwritten with "0 nodes / FAILED" and
     graph_engines [] — exactly what the Y5 kept-manifest rule exists to prevent.
Now `_graph_holds_nodes` returns None when the count can't be read, and both callers fail safe: the engine memory
is kept (the downgrade refusal still applies — the run pushes nothing and exits 4, NOT PUSHED) and a 0-node run
leaves the manifest (and its graph_engines) unchanged and says why, while its FAILED verdict still reaches the CLI
(exit 1). Controls: a graph READ as empty still resets the memory (round 7) / gets the FAILED manifest (Y5); a
graph READ as holding nodes is still refused / kept.

Hermetic: temp project dirs + a temp DEEPMAP_HOME, an operator NEO4J_URI (verbatim route, no registry read), a fake
driver whose node-count query can fail, stubbed extraction / push / index / embedding stages. No Neo4j, no sockets.
Usage: python tests/o5_sync_graph_unknown_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import itertools
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
_SEQ = itertools.count()

# The query _graph_has_nodes (and the wording-only _graph_node_count) runs — the one read this finding is about.
_COUNT_Q = "WHERE NOT n:_DeepMapMeta RETURN count(n)"
_PRIOR = {"extractor": "graphify", "graph_engines": ["graphify"], "verdict": "READY", "nodes": 50}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


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

    def run(self, cypher, **params):
        self.drv.stmts.append(cypher)
        if _COUNT_Q in cypher:
            self.drv.count_reads += 1
            if self.drv.count is None:
                raise RuntimeError("simulated transient failure: connection reset by peer")
            return _Res([{"c": self.drv.count}])
        return _Res()


class _Drv:
    """A fake driver: `count` is the graph's real (non-meta) node count; None makes that read FAIL."""

    def __init__(self, count):
        self.count, self.stmts, self.count_reads = count, [], 0

    def session(self, *a, **k):
        return _Sess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _load_sync(build: Path):
    spec = importlib.util.spec_from_file_location(f"_o5_sync_unknown_{next(_SEQ)}", build / "sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _node(key, name, sf, line=1):
    return {"key": key, "label": "Function", "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": name}


def _wire(S, nodes, engine, info, order):
    """Stub every stage of sync() except the engine-memory / kept-manifest logic under test."""
    Ext = S._Extraction

    def _rebuild(root):
        order.append("extract")
        return Ext([dict(n) for n in nodes], [], [], engine, dict(info))

    def _push(driver, n, c, i, typed_edges=None):
        order.append("push")
        return (len(n), 0)

    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = _push
    # The owner stamp names THIS project, so the owner check never needs the node count (its own failure
    # mode — fail loud — is not what this gate is about).
    S._read_owning_root = lambda d: "this-project"
    S._same_project = lambda cur, owner: True
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S._cleanup_stale = lambda d, live: order.append("prune")
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 7
    S._maybe_auto_tune = lambda root: None


def _project(td: str, manifest: dict | None) -> Path:
    root = Path(td) / "proj"
    root.mkdir()
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    if manifest is not None:
        (root / ".deepmap").mkdir(parents=True, exist_ok=True)
        (root / ".deepmap" / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _manifest(root: Path) -> dict:
    p = root / ".deepmap" / "graph_build.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _scenario(build: Path, *, nodes, engine, info, count, manifest=_PRIOR):
    """Run `python build/sync.py <root>` (sync._main — the real exit contract) over a fake driver whose
    node-count read returns `count` (None = the read fails). Returns a result namespace."""
    S = _load_sync(build)
    order: list = []
    _wire(S, nodes, engine, info, order)
    drv = _Drv(count)
    real_sync = S.sync
    S.sync = lambda target, **kw: real_sync(target, driver=drv, **kw)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://127.0.0.1:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"),
                                                   DEEPMAP_EXTRACTOR=None, DEEPMAP_INCREMENTAL_SYNC=None,
                                                   DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None,
                                                   DEEPMAP_PRECISE_CALLS=None, DEEPMAP_AUTO_GITIGNORE="0"):
        root = _project(td, manifest)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            rc = S._main(["sync.py", str(root)])
        return SimpleNamespace(rc=rc, order=order, text=out.getvalue(), man=_manifest(root),
                               verdict=getattr(S, "last_verdict", "<missing>"),
                               not_pushed=getattr(S, "last_not_pushed", "<missing>"), reads=drv.count_reads)


def gate_helper(build: Path) -> None:
    print("\n-- _graph_holds_nodes: True / False when the count was read, None (UNKNOWN) when it was not --")
    S = _load_sync(build)
    got = {}
    for label, count in (("failing read", None), ("empty graph", 0), ("3 nodes", 3)):
        try:
            got[label] = S._graph_holds_nodes(_Drv(count))
        except Exception as exc:  # noqa: BLE001 — the helper must never raise
            got[label] = f"raised {type(exc).__name__}"
    check("a count read that FAILS is UNKNOWN (None), never 'the graph is empty' (False)",
          got["failing read"] is None, repr(got["failing read"]))
    check("a graph READ as empty -> False", got["empty graph"] is False, repr(got["empty graph"]))
    check("a graph READ as holding nodes -> True", got["3 nodes"] is True, repr(got["3 nodes"]))


def gate_engine_memory(build: Path) -> None:
    print("\n-- an ast fallback over a graphify graph whose node count can't be read: the downgrade refusal holds --")
    node = _node("a.py::f:1", "f", "a.py")
    info = {"requested": "auto", "resolved": "ast", "fallback_reason": "graphify crashed (exit 1)"}

    r = _scenario(build, nodes=[node], engine="ast", info=info, count=None)
    check("precondition: the node-count read was attempted (and failed)", r.reads >= 1, r.reads)
    check("NOTHING is pushed — no second, ast-keyed copy of every Python symbol next to graphify's",
          "push" not in r.order, r.order)
    check("the manifest still describes the kept graphify build (graph_engines not erased)",
          r.man == _PRIOR, r.man)
    check("the run is reported NOT PUSHED: engine-downgrade (python build/sync.py exits 4)",
          r.rc == 4 and "engine-downgrade" in str(r.not_pushed), (r.rc, r.not_pushed))
    check("... with verdict DEGRADED (the previous graph still answers)", r.verdict == "DEGRADED", r.verdict)
    check("the output says the node count could not be read and the engine memory was kept",
          "could not read" in r.text.lower() and "engine memory" in r.text.lower(), r.text[-900:])

    # not a downgrade (an ast graph re-synced by graphify): nothing to refuse, but the memory is still never erased
    prior_ast = {"extractor": "ast", "graph_engines": ["ast"], "verdict": "READY", "nodes": 50}
    r = _scenario(build, nodes=[node], engine="graphify", info={"requested": "auto", "resolved": "graphify"},
                  count=None, manifest=prior_ast)
    check("an unreadable count on an ast -> graphify re-sync: the push proceeds (an upgrade is not refused)",
          "push" in r.order and r.rc == 0, (r.order, r.rc))
    check("... and graph_engines keeps 'ast' (['ast', 'graphify']) — an unknown never erases the memory",
          r.man.get("graph_engines") == ["ast", "graphify"], r.man.get("graph_engines"))
    check("... the note does not claim a downgrade guard that does not apply here",
          "could not read" in r.text.lower() and "downgrade guard" not in r.text.lower(), r.text[-600:])

    # controls — the round-7 behaviour when the count IS read
    r = _scenario(build, nodes=[node], engine="ast", info=info, count=0)
    check("control: a graph READ as EMPTY voids the stale graphify memory — the ast graph is pushed",
          "push" in r.order and r.rc == 0, (r.order, r.rc))
    check("control: ... and the engine memory becomes what the graph now holds (['ast'])",
          r.man.get("graph_engines") == ["ast"], r.man)
    r = _scenario(build, nodes=[node], engine="ast", info=info, count=12)
    check("control: a graph READ as holding nodes -> the downgrade is refused, the manifest kept",
          "push" not in r.order and r.man == _PRIOR and r.rc == 4, (r.order, r.rc, r.man))


def gate_zero_node_run(build: Path) -> None:
    print("\n-- a 0-node run whose node count can't be read: the manifest (and graph_engines) is kept --")
    info = {"requested": "auto", "resolved": "ast", "fallback_reason": "graphify produced no code nodes"}

    r = _scenario(build, nodes=[], engine="ast", info=info, count=None)
    check("precondition: the node-count read was attempted (and failed)", r.reads >= 1, r.reads)
    check("the previous manifest is left unchanged (not rewritten as '0 nodes / FAILED / graph_engines []')",
          r.man == _PRIOR, r.man)
    check("this run's FAILED verdict still reaches the CLI (python build/sync.py exits 1)",
          r.verdict == "FAILED" and r.rc == 1, (r.verdict, r.rc))
    check("the output says the graph's state could not be read and the manifest was left unchanged",
          "could not be read" in r.text.lower() and "left unchanged" in r.text.lower(), r.text[-900:])
    check("the verdict block does not describe a graph it could not read as 'graph: 0 nodes'",
          "graph: 0 nodes" not in r.text, r.text[-600:])

    # controls — Y5 when the count IS read
    r = _scenario(build, nodes=[], engine="ast", info=info, count=0)
    check("control: a graph READ as EMPTY gets the FAILED manifest (it describes reality)",
          r.man.get("verdict") == "FAILED" and r.man.get("graph_engines") == [] and r.rc == 1, r.man)
    r = _scenario(build, nodes=[], engine="ast", info=info, count=12)
    check("control: a graph READ as holding nodes keeps its manifest (Y5)", r.man == _PRIOR and r.rc == 1,
          (r.rc, r.man))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    build = root / "build"
    sys.path.insert(0, str(build))
    print("=" * 72)
    print(f"DeepMap — ISS-build:017: an unreadable node count is UNKNOWN and sync fails safe (root: {root})")
    print("=" * 72)
    gate_helper(build)
    gate_engine_memory(build)
    gate_zero_node_run(build)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
