#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the sync internals fixed after the 2026-09 live validation (Y1-Y5).

  Y1  The edge rebuild is ATOMIC and BATCHED. It used to delete every live node's :CALLS in one
      autocommit and then re-create them one autocommit per edge, each edge MATCHing both endpoints with
      no label (two full node scans per edge, ~12 edges/s). Every refresh showed impact/search a near-
      empty edge set (918 -> 8), and a killed sync left it partial. Now: a caller's edges are deleted
      and re-created inside ONE write transaction (all edge types together when the repo fits the
      per-transaction budget), with label-qualified UNWIND batches, and the key indexes exist before
      the first push.
  Y2  A subdirectory sync used to key nodes relative to the subdirectory, so on a repo synced at its
      root it duplicated every node under it. A subdirectory of a git repo already synced higher up now
      syncs that directory (the outermost one); a sub-project set up on its own keeps its own root.
  Y3  A sync that found the lock held skipped and promised "the running sync will complete the
      update", but the holder never re-extracted, so a commit after its extraction was lost. The
      skipper now queues a re-run request, which the holder honours before it finishes (and a skipper
      that finds the lock free on its second try runs the sync itself).
  Y4  built_at_sha was read when the manifest was WRITTEN (end of sync), so a commit landing mid-sync
      was reported as "fresh". It is now the HEAD at extraction time, and a dirty tree is flagged
      (`dirty` / `built_at_dirty` + a path sample) next to it — ROUND-2 D4 keeps built_at_sha ALWAYS, so
      staleness detection stays on (tests/sync_manifest_freshness_gates.py covers the dirty rules).
  Y5  A 0-node sync overwrote a good manifest (verdict FAILED, extractor=ast) over an intact graph,
      erasing the engine memory the downgrade guard relies on — which was also erased by any refused
      downgrade. The live graph's manifest is now kept; graph_engines records which engines' nodes the
      graph still holds; sync.last_verdict reports this run's verdict to the CLI.

Hermetic: temp DEEPMAP_HOME, fake drivers, stubbed index/embedding stages, no sockets. The git parts
use a throwaway `git init` repo and are skipped when git is absent.
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

_PASS = _FAIL = 0
_GIT = shutil.which("git")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:400]) if detail else ''}")


def _load():
    spec = importlib.util.spec_from_file_location("dm_sync_internals", BUILD / "sync.py")
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


def _node(key, name, label="Function", sf="a.py", line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name}


# ---- a recording fake driver that knows about explicit transactions ----------------------------------
class _Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Rec:
    def __init__(self, fail_on=None):
        self.stmts = []          # (ctx, cypher, params); ctx = ("auto", n) or ("tx", txid)
        self.txs = {}            # txid -> {"committed": bool, "closed": bool}
        self.fail_on = fail_on   # predicate(cypher) -> raise inside a transaction


class _Tx:
    def __init__(self, rec, tid):
        self.rec, self.tid = rec, tid
        rec.txs[tid] = {"committed": False, "closed": False}

    def run(self, cypher, **p):
        self.rec.stmts.append((("tx", self.tid), cypher, p))
        if self.rec.fail_on is not None and self.rec.fail_on(cypher):
            raise RuntimeError("simulated failure mid edge rebuild")
        return _Result()

    def commit(self):
        self.rec.txs[self.tid]["committed"] = True
        self.rec.txs[self.tid]["closed"] = True

    def rollback(self):
        self.rec.txs[self.tid]["closed"] = True

    def close(self):
        self.rec.txs[self.tid]["closed"] = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):   # mirrors the neo4j driver: commit on success, else roll back
        st = self.rec.txs[self.tid]
        if not st["closed"]:
            if exc_type is None:
                st["committed"] = True
            st["closed"] = True
        return False


class _Sess:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.rec.stmts.append((("auto", len(self.rec.stmts)), cypher, p))
        return _Result()

    def begin_transaction(self, *a, **k):
        return _Tx(self.rec, len(self.rec.txs) + 1)


class _Drv:
    def __init__(self, rec):
        self.rec = rec

    def session(self, *a, **k):
        return _Sess(self.rec)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _rows(p):
    rows = p.get("rows")
    return rows if isinstance(rows, list) else [p]


def _calls_created(rec):
    """[(ctx, a, b, conf)] for every created :CALLS row, whatever the statement shape."""
    out = []
    for ctx, c, p in rec.stmts:
        if "MERGE" in c and "[r:CALLS]" in c:
            for r in _rows(p):
                out.append((ctx, r.get("a"), r.get("b"), r.get("c")))
    return out


def _calls_deletes(rec):
    """[(ctx, set(anchor keys))] for every :CALLS prune."""
    return [(ctx, set(p.get("keys") or [])) for ctx, c, p in rec.stmts
            if "[r:CALLS]" in c and "DELETE r" in c]


# =====================================================================================================
# Y1 — atomic, batched edge rebuild
# =====================================================================================================
def _fanout_fixture(n_callers=60, n_targets=5):
    nodes, calls = [], []
    for t in range(n_targets):
        nodes.append(_node(f"lib.py::t{t}:{t + 1}", f"t{t}", "Function", "lib.py", t + 1))
    nodes.append(_node("lib.py::Svc.run:50", "Svc.run", "Method", "lib.py", 50))
    for i in range(n_callers):
        k = f"app{i}.py::caller{i}:1"
        nodes.append(_node(k, f"caller{i}", "Function", f"app{i}.py", 1))
        for t in range(n_targets):
            calls.append((k, f"t{t}"))
        calls.append((k, "run"))          # short-name -> Svc.run (a Method endpoint)
    return nodes, calls


def gate_y1_atomic_batched() -> None:
    print("\n-- Y1: :CALLS rebuild runs in write transactions, batched + label-qualified --")
    S = _load()
    nodes, calls = _fanout_fixture()
    rec = _Rec()
    n_nodes, n_edges = S.push_to_neo4j(_Drv(rec), nodes, calls, [("app0.py", "lib")], [])
    created = _calls_created(rec)
    pairs = {(a, b) for _, a, b, _ in created}
    check("every call edge is still produced (60 callers x 6 targets)", len(pairs) == 360 and n_edges == 360,
          f"pairs={len(pairs)} n_edges={n_edges}")
    check("node count returned unchanged", n_nodes == len(nodes), str(n_nodes))
    check("every :CALLS create runs inside an explicit transaction (never autocommit)",
          created and all(ctx[0] == "tx" for ctx, *_ in created),
          sorted({ctx[0] for ctx, *_ in created}))
    dels = _calls_deletes(rec)
    check("every :CALLS prune runs inside an explicit transaction",
          dels and all(ctx[0] == "tx" for ctx, _ in dels), [ctx for ctx, _ in dels])
    # the prune and the re-create of the SAME caller are in the SAME transaction (the atomic swap)
    del_tx = {}
    for ctx, keys in dels:
        for k in keys:
            del_tx[k] = ctx
    same = all(del_tx.get(a) == ctx for ctx, a, _b, _c in created)
    check("each caller's :CALLS prune and re-create share ONE transaction", same)
    check("every transaction that ran was committed",
          rec.txs and all(t["committed"] for t in rec.txs.values()), rec.txs)
    typed_ctx = [ctx for ctx, c, _p in rec.stmts if ("[r:IMPORTS]" in c or "DELETE r" in c)
                 and "[r:CALLS]" not in c]
    check("typed-edge / :IMPORTS prunes + writes run inside the edge transaction too",
          typed_ctx and all(ctx[0] == "tx" for ctx in typed_ctx), typed_ctx[:5])
    check("a small repo rebuilds ALL its edges in ONE transaction (full atomic swap)",
          len(rec.txs) == 1, f"{len(rec.txs)} transactions")
    n_stmts = len(rec.stmts)
    check("statement count is O(batches), not O(edges) (360 edges + 66 nodes)", n_stmts < 40, str(n_stmts))
    creates = [c for _ctx, c, _p in rec.stmts if "MERGE" in c and "[r:CALLS]" in c]
    check("the :CALLS create MATCHes each endpoint BY LABEL (index seek, not two AllNodesScans)",
          creates and all("{key:$a}" not in c and "(a:" in c and "(b:" in c for c in creates),
          creates[:1])
    check("a Method endpoint is matched with the :Method label",
          any("(b:Method" in c for c in creates), creates)
    node_stmts = [c for _ctx, c, _p in rec.stmts if "MERGE (x:" in c]
    check("nodes are upserted in UNWIND batches per label (not one statement per node)",
          node_stmts and len(node_stmts) <= 4 and all("UNWIND" in c for c in node_stmts),
          f"{len(node_stmts)} node statements")


def gate_y1_semantics() -> None:
    print("\n-- Y1: resolution + confidence semantics are unchanged by batching --")
    S = _load()
    nodes = [_node("a.py::caller:1", "caller"), _node("a.py::foo:5", "foo", line=5),
             _node("b.py::foo:9", "foo", sf="b.py", line=9), _node("a.py::bar:20", "bar", line=20),
             _node("a.py::rec:30", "rec", line=30)]
    calls = [("a.py::caller:1", "foo"), ("a.py::caller:1", "bar"), ("a.py::caller:1", "bar"),
             ("a.py::rec:30", "rec"), ("ghost.py::g:1", "bar")]
    rec = _Rec()
    _n, n_edges = S.push_to_neo4j(_Drv(rec), nodes, calls, [], [])
    got = {(a, b): c for _ctx, a, b, c in _calls_created(rec)}
    check("multi-target 'foo' -> 2 AMBIGUOUS edges",
          got.get(("a.py::caller:1", "a.py::foo:5")) == "AMBIGUOUS"
          and got.get(("a.py::caller:1", "b.py::foo:9")) == "AMBIGUOUS", got)
    check("single-target 'bar' -> INFERRED, two call sites -> ONE edge",
          got.get(("a.py::caller:1", "a.py::bar:20")) == "INFERRED", got)
    check("recursion creates no self-edge", ("a.py::rec:30", "a.py::rec:30") not in got, got)
    check("a caller that is not a node of this sync gets no edge (it could never be pruned)",
          not any(a == "ghost.py::g:1" for a, _ in got), got)
    check("returned edge count == distinct created edges (3)", n_edges == 3 and len(got) == 3,
          f"n_edges={n_edges} got={len(got)}")
    rec2 = _Rec()
    S.push_to_neo4j(_Drv(rec2), [], [("x", "y")], [], [])
    check("0 nodes -> no statements at all (nothing to push, nothing to prune)", rec2.stmts == [],
          rec2.stmts[:3])


def gate_y1_budget_and_failure() -> None:
    print("\n-- Y1: big repos commit per caller group; a failure rolls the rebuild back --")
    S = _load()
    budget = getattr(S, "_EDGE_TX_MAX_ROWS", None)
    # ~2 KB of transaction state per counted row (an edge written OR an existing edge pruned) on Neo4j
    # 5.26, against a ~358 MB cap (70% of the 512m per-instance heap): 20k rows peaked at 42 MB live.
    check("the per-transaction edge budget stays well under the per-instance transaction cap",
          isinstance(budget, int) and 0 < budget <= 25_000, str(budget))
    nodes, calls = _fanout_fixture(n_callers=12, n_targets=4)     # 12 callers x 5 edges = 60 rows
    if budget is not None:
        S._EDGE_TX_MAX_ROWS = 10
    rec = _Rec()
    S.push_to_neo4j(_Drv(rec), nodes, calls, [], [])
    check("a push over the per-transaction budget uses several transactions",
          len(rec.txs) >= 3, f"{len(rec.txs)} transactions")
    dels = _calls_deletes(rec)
    del_tx = {}
    for ctx, keys in dels:
        for k in keys:
            del_tx[k] = ctx
    created = _calls_created(rec)
    check("...and each caller's prune + re-create still share one transaction",
          created and all(del_tx.get(a) == ctx for ctx, a, _b, _c in created))
    check("...every live caller's old :CALLS is pruned exactly once",
          sorted(k for _ctx, keys in dels for k in keys) == sorted({n["key"] for n in nodes}),
          f"{len(dels)} prunes")
    # every node's typed/:IMPORTS prunes ride in the SAME transaction as its :CALLS prune (the anchor's
    # whole edge set swaps atomically; round 1 put every typed prune in the LAST transaction — unbounded)
    typed_where = {}
    for ctx, c, p in rec.stmts:
        if "DELETE r" in c and "[r:CALLS]" not in c:
            for k in p.get("keys") or []:
                typed_where.setdefault(k, set()).add(ctx)
    check("each node's typed-edge prunes ride in its own :CALLS transaction",
          typed_where and all(typed_where.get(k) == {ctx} for k, ctx in del_tx.items()),
          [(k, typed_where.get(k), ctx) for k, ctx in list(del_tx.items())[:3]])

    # a failure in the middle of the edge rebuild: the transaction is never committed (the driver
    # rolls it back), so a reader keeps seeing the OLD edge set — never a half-deleted one.
    S = _load()
    nodes, calls = _fanout_fixture(n_callers=5, n_targets=3)
    rec = _Rec(fail_on=lambda c: "MERGE" in c and "[r:CALLS]" in c)
    raised = False
    try:
        S.push_to_neo4j(_Drv(rec), nodes, calls, [], [])
    except RuntimeError:
        raised = True
    check("a failure mid-rebuild propagates (fail loud)", raised)
    dels = _calls_deletes(rec)
    check("the :CALLS prune that ran before the failure was inside the failed transaction",
          dels and all(ctx[0] == "tx" for ctx, _ in dels), [ctx for ctx, _ in dels])
    check("that transaction was NOT committed (rolled back -> old edges intact)",
          rec.txs and not any(t["committed"] for t in rec.txs.values()), rec.txs)


def _wire_pipeline(S, rebuild, order=None):
    """Stub every stage of sync() except the ones under test."""
    order = order if order is not None else []
    S._rebuild_code = rebuild
    S._apply_recognizers = lambda root, nodes: (nodes, [])

    def _push(driver, n, c, i, typed_edges=None):
        order.append("push")
        return (len(n), 0)

    S.push_to_neo4j = _push
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: order.append("create_indexes"),
                                wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 1
    S._maybe_auto_tune = lambda root: None
    return order


def gate_y1_indexes_first() -> None:
    print("\n-- Y1: the node-key indexes exist BEFORE the first push --")
    S = _load()
    node = _node("a.py::f:1", "f")
    order = _wire_pipeline(S, lambda root: ([dict(node)], [], [], "ast"))
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687"), \
            contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        S.sync(td, driver=_Drv(_Rec()))
    check("create_all_indexes runs before push_to_neo4j",
          "create_indexes" in order and "push" in order
          and order.index("create_indexes") < order.index("push"), order)


# =====================================================================================================
# Y2 — a subdirectory sync syncs the repository root
# =====================================================================================================
def _git(repo, *args):
    return subprocess.run([_GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", *args],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def _git_repo(td):
    repo = Path(td) / "proj"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "billing.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    (repo / "tools.py").write_text("def tool():\n    return 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c0")
    return repo


def gate_y2_subdir() -> None:
    print("\n-- Y2: a subdirectory of a repo synced at its root syncs the root (root-relative keys) --")
    if not _GIT:
        print("     (git not installed — skipped)")
        return
    S = _load()
    seen = []
    node = _node("app/billing.py::charge:1", "charge", sf="app/billing.py")

    def _rebuild(root):
        seen.append(Path(root).resolve())
        return ([dict(node)], [], [], "ast")

    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        repo = _git_repo(td)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo, driver=_Drv(_Rec()))                    # the project is set up at its root
        before = (repo / ".deepmap" / "graph_build.json").stat().st_mtime_ns
        # a manifest an old subdirectory sync left behind must not pin the subdirectory
        (repo / "app" / ".deepmap").mkdir(parents=True)
        (repo / "app" / ".deepmap" / "graph_build.json").write_text('{"extractor": "ast"}', encoding="utf-8")
        seen.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(repo / "app", driver=_Drv(_Rec()))
        check("sync(<repo>/app) succeeds", ok is True, str(ok))
        check("extraction ran over the repository ROOT, not the subdirectory",
              seen == [repo.resolve()], [str(p) for p in seen])
        check("the root manifest is the one rewritten",
              (repo / ".deepmap" / "graph_build.json").stat().st_mtime_ns != before)
        check("the subdirectory's leftover manifest is left alone (not rewritten)",
              (repo / "app" / ".deepmap" / "graph_build.json").read_text(encoding="utf-8")
              == '{"extractor": "ast"}')
        check("the widening is announced", "syncs as a whole" in err.getvalue(), err.getvalue()[-300:])

    # a sub-project set up on its own (nothing above it synced) keeps its own root
    S = _load()
    seen.clear()
    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        repo = _git_repo(td)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo / "app", driver=_Drv(_Rec()))
        check("a subdirectory with nothing synced above it is synced as given (a sub-project)",
              seen == [(repo / "app").resolve()], [str(p) for p in seen])

    # a non-git subdirectory keeps its own root (it is its own project for routing as well)
    S = _load()
    seen.clear()
    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        plain = Path(td) / "plain" / "sub"
        plain.mkdir(parents=True)
        (plain / "x.py").write_text("def x():\n    pass\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(plain, driver=_Drv(_Rec()))
        check("a non-git directory is synced as given", seen == [plain.resolve()], [str(p) for p in seen])


# =====================================================================================================
# Y3 — a lock-skipped sync queues a re-run that the holder honours
# =====================================================================================================
def gate_y3_rerun() -> None:
    print("\n-- Y3: a sync skipped by the lock is not lost --")
    S = _load()
    node = _node("a.py::f:1", "f")
    state = {"rebuild": 0, "nested": []}

    def _rebuild(root):
        state["rebuild"] += 1
        if state["rebuild"] == 1:
            # a commit lands while this sync holds the lock: the git hook starts a second sync
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                state["nested"].append((S.sync(root, driver=_Drv(_Rec())), err2.getvalue()))
        return ([dict(node)], [], [], "ast")

    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(root, driver=_Drv(_Rec()))
        nested_ok, nested_err = state["nested"][0] if state["nested"] else (None, "")
        check("the concurrent sync returns True (not an error) and does not extract",
              nested_ok is True, f"{nested_ok} {nested_err[-200:]}")
        check("...and says it QUEUED a re-run (no false 'will complete' promise)",
              "queued" in nested_err.lower() and "will complete the update" not in nested_err,
              nested_err[-300:])
        check("the lock holder re-extracts before it finishes (2 passes)", state["rebuild"] == 2,
              str(state["rebuild"]))
        check("the holder succeeds", ok is True, str(ok))
        leftovers = [p.name for p in (root / ".deepmap").glob("*rerun*")]
        check("no re-run request is left behind once honoured", leftovers == [], leftovers)

    # lost-wakeup: the holder finished between the skipper's two lock attempts -> the skipper runs it
    S = _load()
    state = {"rebuild": 0}

    def _rebuild2(root):
        state["rebuild"] += 1
        return ([dict(node)], [], [], "ast")

    _wire_pipeline(S, _rebuild2)
    real_acquire = S._acquire_sync_lock
    attempts = {"n": 0}

    def _flaky(root):
        attempts["n"] += 1
        return None if attempts["n"] == 1 else real_acquire(root)

    S._acquire_sync_lock = _flaky
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(root, driver=_Drv(_Rec()))
        check("a skipper that finds the lock free on its second try runs the sync itself",
              ok is True and state["rebuild"] == 1, f"ok={ok} rebuild={state['rebuild']}")

    # bounded: a request that keeps arriving does not loop forever
    S = _load()
    state = {"rebuild": 0}

    def _rebuild3(root):
        state["rebuild"] += 1
        with contextlib.redirect_stderr(io.StringIO()):
            S.sync(root, driver=_Drv(_Rec()))          # another request on every pass
        return ([dict(node)], [], [], "ast")

    _wire_pipeline(S, _rebuild3)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            ok = S.sync(root, driver=_Drv(_Rec()))
        cap = getattr(S, "_MAX_SYNC_PASSES", None)
        check("re-runs are bounded", cap is not None and 1 < state["rebuild"] <= cap,
              f"passes={state['rebuild']} cap={cap}")


# =====================================================================================================
# Y4 — built_at_sha is the HEAD at extraction time; a dirty tree does not claim the commit
# =====================================================================================================
def gate_y4_sha() -> None:
    print("\n-- Y4: manifest SHA is captured at extraction time; dirty trees are flagged --")
    if not _GIT:
        print("     (git not installed — skipped)")
        return
    S = _load()
    node = _node("tools.py::tool:1", "tool", sf="tools.py")
    box = {}

    def _rebuild(root):
        return ([dict(node)], [], [], "ast")

    _wire_pipeline(S, _rebuild)

    def _embed_and_commit(d):
        # a commit lands AFTER extraction, while the (slow) embedding stage runs
        repo = box["repo"]
        (repo / "late.py").write_text("def late():\n    pass\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "late")
        return 0

    S.embeddings = SimpleNamespace(generate_embeddings=_embed_and_commit)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        repo = _git_repo(td)
        box["repo"] = repo
        sha0 = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo, driver=_Drv(_Rec()))
        sha1 = _git(repo, "rev-parse", "HEAD").stdout.strip()
        data = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("fixture: a commit really landed mid-sync", sha0 != sha1)
        check("built_at_sha is the HEAD the graph was EXTRACTED from (not the later commit)",
              data.get("built_at_sha") == sha0, f"{data.get('built_at_sha')} vs extracted {sha0}")
        check("a clean tree records built_at_dirty = false", data.get("built_at_dirty") is False, data)

    # a dirty tree: the graph holds uncommitted code -> built_at_sha (D4: always) + the dirty flag
    S = _load()
    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"),
                                                   DEEPMAP_AUTO_GITIGNORE="0"):
        repo = _git_repo(td)
        sha0 = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "tools.py").write_text("def tool():\n    return 3\n\ndef phantom():\n    pass\n",
                                       encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo, driver=_Drv(_Rec()))
        data = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("dirty tree -> built_at_sha is STILL the extracted HEAD (D4: staleness stays detectable)",
              data.get("built_at_sha") == sha0, data)
        check("dirty tree -> dirty = built_at_dirty = true, with the changed file in the sample",
              data.get("dirty") is True and data.get("built_at_dirty") is True
              and "tools.py" in (data.get("dirty_paths_sample") or []), data)

    # DeepMap's own artifacts (.deepmap/, graphify-out/) never make a tree dirty, even un-ignored
    S = _load()
    _wire_pipeline(S, _rebuild)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home"),
                                                   DEEPMAP_AUTO_GITIGNORE="0"):
        repo = _git_repo(td)
        (repo / "graphify-out").mkdir()
        (repo / "graphify-out" / "manifest.json").write_text("{}", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo, driver=_Drv(_Rec()))
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(repo, driver=_Drv(_Rec()))       # the 2nd run sees the 1st run's .deepmap/ files
        data = json.loads((repo / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("DeepMap's own artifact dirs do not count as uncommitted changes",
              data.get("built_at_dirty") is False and data.get("built_at_sha"), data)


# =====================================================================================================
# Y5 — a 0-node sync keeps the live graph's manifest; engine memory survives a refused downgrade
# =====================================================================================================
def _prior(root, **fields):
    d = Path(root) / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph_build.json").write_text(json.dumps(fields), encoding="utf-8")


def _manifest(root):
    return json.loads((Path(root) / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))


def gate_y5_zero_nodes() -> None:
    print("\n-- Y5: a 0-node sync over an intact graph keeps its manifest; last_verdict is FAILED --")
    S = _load()
    _wire_pipeline(S, lambda root: ([], [], [], "ast"))
    S._graph_has_nodes = lambda d: True           # the live graph still holds the previous build
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / ".deepmap-root").write_text("", encoding="utf-8")
        _prior(root, extractor="graphify", verdict="READY", nodes=19)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            S.sync(root, cleanup_stale=True, driver=_Drv(_Rec()))
        data = _manifest(root)
        check("the previous manifest is kept (extractor/verdict/nodes of the live graph)",
              data.get("extractor") == "graphify" and data.get("verdict") == "READY"
              and data.get("nodes") == 19, data)
        check("sync.last_verdict reports this run's FAILED verdict (the CLI exits 1 on it)",
              getattr(S, "last_verdict", None) == "FAILED", getattr(S, "last_verdict", "<missing>"))
        check("the kept manifest is explained", "unchanged" in err.getvalue(), err.getvalue()[-300:])

    # round 3 (W3's live finding): the verdict block of such a run printed "graph: 0 nodes, 0 CALLS
    # edges, 11 embedded" — this run's counts beside the KEPT graph's embedded total, a graph that
    # cannot exist. This run's numbers and the kept graph's must be told apart.
    S = _load()
    _wire_pipeline(S, lambda root: ([], [], [], "ast"))
    S._graph_has_nodes = lambda d: True
    S._count_embedded_nodes = lambda d, fallback=0: 11     # the KEPT graph's embedded total
    S._graph_node_count = lambda d: 19                      # the KEPT graph's size (when readable)
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        _prior(root, extractor="graphify", verdict="READY", nodes=19)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            S.sync(root, driver=_Drv(_Rec()))
        text = out.getvalue()
        mixed = [ln for ln in text.splitlines()
                 if "11 embedded" in ln and ("0 nodes" in ln or "0 CALLS" in ln)
                 and "this run" not in ln.lower()]
        check("no line pairs this run's 0 nodes / 0 CALLS with the kept graph's '11 embedded'", mixed == [],
              mixed)
        check("the verdict block does not describe the kept graph as 'graph: 0 nodes'",
              "graph: 0 nodes" not in text, text[-500:])
        check("the verdict itself is still printed (FAILED) and last_verdict is FAILED",
              "FAILED" in text and getattr(S, "last_verdict", None) == "FAILED", text[-400:])
        kept = [ln for ln in text.splitlines() if "left unchanged" in ln]
        check("one line says this run extracted 0 nodes and the existing graph (19 nodes, 11 embedded) was "
              "kept", len(kept) == 1 and "0 nodes" in kept[0] and "19 nodes" in kept[0] and "11 embedded" in kept[0],
              kept or text[-400:])

    S = _load()
    _wire_pipeline(S, lambda root: ([], [], [], "ast"))
    S._graph_has_nodes = lambda d: False          # nothing in the graph: FAILED is the truth
    S._count_embedded_nodes = lambda d, fallback=0: 0
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            S.sync(root, driver=_Drv(_Rec()))
        check("an EMPTY graph: the verdict block's graph line is the (true) empty graph",
              "graph: 0 nodes, 0 CALLS edges, 0 embedded" in out.getvalue(), out.getvalue()[-400:])

    S = _load()
    _wire_pipeline(S, lambda root: ([], [], [], "ast"))
    S._graph_has_nodes = lambda d: False          # nothing in the graph: FAILED is the truth
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / "notes.txt").write_text("hello", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(root, driver=_Drv(_Rec()))
        mp = root / ".deepmap" / "graph_build.json"
        check("an EMPTY graph still gets a FAILED manifest (it describes reality)",
              mp.is_file() and _manifest(root).get("verdict") == "FAILED",
              _manifest(root) if mp.is_file() else "no manifest")

    S = _load()
    _wire_pipeline(S, lambda root: ([_node("a.py::f:1", "f")], [], [], "ast"))
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            S.sync(root, driver=_Drv(_Rec()))
        check("a normal sync sets last_verdict too", getattr(S, "last_verdict", None) in ("READY", "DEGRADED"),
              getattr(S, "last_verdict", "<missing>"))

    # `python build/sync.py <dir>` applies the same FAILED -> exit 1 rule as `deepmap sync`
    S = _load()
    main_fn = getattr(S, "_main", None)
    check("sync.py has a testable _main(argv)", callable(main_fn))
    if callable(main_fn):
        def _fake_sync(verdict, ok=True):
            def _s(target, **kw):
                S.last_verdict = verdict
                return ok
            return _s
        S.sync = _fake_sync("FAILED")
        check("sync.py __main__: a FAILED verdict exits 1", main_fn(["sync.py", "x"]) == 1)
        S.sync = _fake_sync("DEGRADED")
        check("sync.py __main__: DEGRADED exits 0 (usable)", main_fn(["sync.py", "x"]) == 0)
        S.sync = _fake_sync(None, ok=False)
        check("sync.py __main__: a refused sync exits 1", main_fn(["sync.py", "x"]) == 1)


def gate_y5_engine_memory() -> None:
    print("\n-- Y5: the downgrade guard is not one-shot; a forced engine is not called a fallback --")
    node = _node("a.py::f:1", "f")

    def _one(root, *, force_prune=False):
        S = _load()
        Ext = getattr(S, "_Extraction", None)
        info = {"requested": "ast", "resolved": "ast", "fallback_reason": None}

        def _rebuild(r):
            return Ext([dict(node)], [], [], "ast", info) if Ext else ([dict(node)], [], [], "ast")

        _wire_pipeline(S, _rebuild)
        pruned = {"n": 0}
        S._cleanup_stale = lambda d, live: pruned.__setitem__("n", pruned["n"] + 1)
        S._prune_blast_radius = lambda d, live: (5, 10)      # exactly 50%: the blast guard does not fire
        # the live graph holds the graphify build (round 7: an EMPTY graph voids a stale engine memory)
        S._graph_has_nodes = lambda d: True
        err = io.StringIO()
        with _env(DEEPMAP_EXTRACTOR="ast"), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            S.sync(root, cleanup_stale=True, force_prune=force_prune, driver=_Drv(_Rec()))
        return pruned["n"], err.getvalue()

    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://localhost:7687",
                                                   DEEPMAP_HOME=str(Path(td) / "home")):
        root = Path(td) / "p"
        root.mkdir()
        (root / ".deepmap-root").write_text("", encoding="utf-8")
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        _prior(root, extractor="graphify", verdict="READY", nodes=10)
        n1, err1 = _one(root)
        check("1st forced-ast --stale-cleanup over a graphify graph: prune refused", n1 == 0, str(n1))
        check("the message says the engine was FORCED (DEEPMAP_EXTRACTOR=ast), not a fallback",
              "DEEPMAP_EXTRACTOR=ast" in err1 and "fell back" not in err1
              and "Fix the graphify install" not in err1, err1[-400:])
        check("the message names --force-prune as the deliberate engine switch", "--force-prune" in err1,
              err1[-300:])
        data = _manifest(root)
        # round 7 (T7#11): the downgrade refuses the PUSH too (an ast push next to graphify's nodes was a
        # second copy of every Python symbol), so the manifest still describes the kept graphify graph —
        # the engine memory the next run reads (tests/sync_r7_gates.py section E covers the rest).
        check("the refused downgrade keeps the manifest describing the graphify graph (engine memory intact)",
              data.get("extractor") == "graphify" and data.get("verdict") == "READY", data)
        n2, _ = _one(root)
        check("2nd identical sync: prune STILL refused (the guard is not one-shot)", n2 == 0, str(n2))
        n3, _ = _one(root, force_prune=True)
        check("--force-prune performs the prune", n3 == 1, str(n3))
        check("after the prune the graph holds only ast nodes", _manifest(root).get("graph_engines") == ["ast"],
              _manifest(root))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — sync internals gates (Y1-Y5; no Neo4j)")
    print("=" * 68)
    home = tempfile.mkdtemp(prefix="dm_syncint_home_")
    with _env(DEEPMAP_HOME=home, DEEPMAP_EXTRACTOR=None, DEEPMAP_INCREMENTAL_SYNC=None,
              DEEPMAP_PRECISE_CALLS=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None):
        for gate in (gate_y1_atomic_batched, gate_y1_semantics, gate_y1_budget_and_failure,
                     gate_y1_indexes_first, gate_y2_subdir, gate_y3_rerun, gate_y4_sha,
                     gate_y5_zero_nodes, gate_y5_engine_memory):
            try:
                gate()
            except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate, not a crashed run
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    shutil.rmtree(home, ignore_errors=True)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
