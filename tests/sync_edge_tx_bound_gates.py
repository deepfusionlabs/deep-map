#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: EVERY edge write of a sync rides in a BOUNDED write transaction (review finding #3).

Round 1 made the :CALLS rebuild transactional and bounded it at _EDGE_TX_MAX_ROWS rows per write
transaction, but the typed framework edges and :IMPORTS were pruned and re-created — ALL of them — in
the LAST group's transaction and never counted. A large JS/TS repo (last-segment `index` import
fan-out: tens of thousands of :IMPORTS) then put one unbounded transaction on the 512m per-instance
heap (~4 KB of transaction state per rebuilt edge, ~358 MB cap), so every re-sync could die on a
memory-pool error where the old per-edge autocommit succeeded.

  E0  the budget-split push leaves the IDENTICAL graph a single-transaction push does (an interpreter of
      the Cypher it sends, over a prior graph with phantom and out-of-scope edges of all 14 types).
  E1  the split push persists EXACTLY the edge set a single-transaction push does (every type).
  E2  every transaction's created rows (CALLS + typed + IMPORTS) stay within the budget.
  E3  a node's typed/IMPORTS prune and re-create share ONE transaction with its :CALLS (per-anchor
      atomic swap), and every live key is pruned for every edge type exactly once.
  E4  typed/IMPORTS writes are label-qualified UNWIND batches (no per-edge key-only MATCH).
  E5  a failure inside a later group rolls back only that group (committed groups stay) and propagates.
  E6  one anchor bigger than the budget is never merged into a neighbour's transaction, and its rows are
      written in chunks within the budget (round 3: it used to get ONE transaction of any size).
  E7  the graph-wide DELETES (the stale prune, the --switch-project wipe) are bounded too: relationships
      then nodes in CALL { ... } IN TRANSACTIONS batches (live: ONE prune of a 1.2k-node / 90k-edge graph
      peaked at 201 MB of the ~358 MB per-instance cap), the owner stamp deleted LAST, with fallbacks for
      servers without the scope clause / without CALL IN TRANSACTIONS.
  E8  (round 3, review finding #1) the budget counts the EXISTING edges a transaction prunes, not only the
      rows it creates: round 2 sized a group by its new rows alone, so the first re-sync after an upgrade
      that shrank the edge set (v0.27.4's cross-language IMPORTS fan-out -> W5's resolved imports), or
      after DEEPMAP_RECOGNIZERS disabled a recognizer, put every old edge's delete into ONE transaction
      (~2.2 KB each) — a MemoryPoolOutOfMemoryError that rolls back and repeats on every later sync. Proven
      on the interpreter: per transaction, (edges deleted + rows written) <= the budget, the graph is
      identical to the single-transaction push, the existing degree is read once and matched by key alone
      (like the prune), a hub whose OLD edges alone exceed the budget is pre-pruned in batches, and the
      degree read falls back on an older server / propagates a real error before any write.
  E9  (2026-10 review, F1-relabel-twin) a node the graph holds under a label this extraction no longer gives its
      key is removed by the push before it upserts: the interpreter models the census (one read per label, by
      label and key) and the delete (relationships out, in, then the nodes, each matched by label + key + none
      of the key's live labels, batched like the stale prune, with both fallback spellings); the twin goes with
      every relationship, the live node, a stale key and an out-of-scope node stay, the full-text pending stamp
      is set before the first delete, `.relabeled` counts the twins, and every budget leaves the same graph.

Hermetic: a recording fake driver that models explicit transactions; the real sync.push_to_neo4j and
the real edge_resolve. No Neo4j.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

from framework_edges import ALLOWED_EDGE_TYPES, TypedEdge  # noqa: E402

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _load():
    spec = importlib.util.spec_from_file_location("dm_sync_edgetx", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- recording fake with explicit transactions -------------------------------------------------------
class _Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _Rec:
    def __init__(self, fail_on=None):
        self.stmts = []          # (ctx, cypher, params)
        self.txs = {}
        self.fail_on = fail_on   # predicate(txid, cypher) -> raise


class _Tx:
    def __init__(self, rec, tid):
        self.rec, self.tid = rec, tid
        rec.txs[tid] = {"committed": False, "closed": False}

    def run(self, cypher, **p):
        self.rec.stmts.append((("tx", self.tid), cypher, p))
        if self.rec.fail_on is not None and self.rec.fail_on(self.tid, cypher):
            raise RuntimeError("simulated failure mid edge rebuild")
        return _Result()

    def commit(self):
        self.rec.txs[self.tid].update(committed=True, closed=True)

    def rollback(self):
        self.rec.txs[self.tid]["closed"] = True

    def close(self):
        self.rec.txs[self.tid]["closed"] = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
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


def _etype(cypher):
    if "[r:" not in cypher:
        return None
    return cypher.split("[r:", 1)[1].split("]", 1)[0]


def _creates(rec):
    """[(ctx, type, anchor, other, conf)] for every created edge row (any type)."""
    out = []
    for ctx, c, p in rec.stmts:
        if "MERGE" in c and "[r:" in c:
            t = _etype(c)
            for r in p.get("rows") or []:
                out.append((ctx, t, r.get("a"), r.get("b"), r.get("c")))
    return out


def _prunes(rec):
    """[(ctx, type, set(anchor keys))] for every edge prune."""
    out = []
    for ctx, c, p in rec.stmts:
        if "DELETE r" in c and "[r:" in c:
            out.append((ctx, _etype(c), set(p.get("keys") or [])))
    return out


def _node(key, name, label, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name}


def _fixture(n=40):
    nodes = [_node("base.py::module", "base", "Module", "base.py"),
             _node("base.py::Base:1", "Base", "Class", "base.py"),
             _node("base.py::helper:5", "helper", "Function", "base.py", 5)]
    calls, imports, typed = [], [], []
    for i in range(n):
        f = f"m{i}.py"
        nodes += [_node(f"{f}::module", f"m{i}", "Module", f),
                  _node(f"{f}::C{i}:1", f"C{i}", "Class", f),
                  _node(f"{f}::C{i}.run:2", f"C{i}.run", "Method", f, 2)]
        calls.append((f"{f}::C{i}.run:2", "helper"))
        imports.append((f, "base"))
        typed.append(TypedEdge(f"C{i}", f, "Base", "INHERITS", "NAME", {}))
        typed.append(TypedEdge(f"C{i}", f, "Base", "CASCADES", "NAME", {"via": "dependent"}))
    return nodes, calls, imports, typed


def _push(budget, fixture, fail_on=None):
    S = _load()
    S._EDGE_TX_MAX_ROWS = budget
    rec = _Rec(fail_on=fail_on)
    err = None
    try:
        S.push_to_neo4j(_Drv(rec), *fixture)
    except RuntimeError as e:
        err = e
    return rec, err


def gate_equivalence_and_bounds() -> None:
    print("\n-- E1/E2: a split push persists the single-transaction edge set, within the budget --")
    fx = _fixture()
    one, _ = _push(10**9, fx)
    base = {(t, a, b, c) for _ctx, t, a, b, c in _creates(one)}
    types = {t for t, *_ in base}
    check("fixture: the single-transaction push writes CALLS + INHERITS + CASCADES + IMPORTS",
          {"CALLS", "INHERITS", "CASCADES", "IMPORTS"} <= types and len(base) == 160,
          f"{sorted(types)} {len(base)}")
    check("fixture: that push is ONE transaction", len(one.txs) == 1, len(one.txs))
    split, _ = _push(25, fx)
    got = {(t, a, b, c) for _ctx, t, a, b, c in _creates(split)}
    check("E1: the budget-split push persists EXACTLY the same edges", got == base,
          f"missing={sorted(base - got)[:3]} extra={sorted(got - base)[:3]}")
    per_tx = {}
    for ctx, *_ in _creates(split):
        per_tx[ctx] = per_tx.get(ctx, 0) + 1
    check("E2: several transactions were used", len(split.txs) >= 6, len(split.txs))
    check("E2: EVERY transaction's created rows (all edge types) are within the budget",
          per_tx and max(per_tx.values()) <= 25, sorted(per_tx.values(), reverse=True)[:5])
    check("E2: every transaction committed", all(t["committed"] for t in split.txs.values()), split.txs)
    check("E2: no edge write ran outside a transaction",
          all(ctx[0] == "tx" for ctx, *_ in _creates(split))
          and all(ctx[0] == "tx" for ctx, *_ in _prunes(split)))


def gate_atomic_per_anchor() -> None:
    print("\n-- E3: each anchor's prune + re-create share its group's transaction; full-vocabulary prune --")
    fx = _fixture()
    rec, _ = _push(25, fx)
    pr = _prunes(rec)
    where = {}
    for ctx, t, keys in pr:
        for k in keys:
            where.setdefault((t, k), []).append(ctx)
    bad = [(t, a) for ctx, t, a, _b, _c in _creates(rec) if ctx not in where.get((t, a), [])]
    check("every created row's anchor was pruned for that type IN THE SAME transaction", bad == [], bad[:5])
    live = {n["key"] for n in fx[0]}
    vocab = set(ALLOWED_EDGE_TYPES) | {"CALLS"}
    counts = {(t, k): len(where.get((t, k), [])) for t in vocab for k in live}
    missing = [x for x, n in counts.items() if n == 0]
    dup = [x for x, n in counts.items() if n > 1]
    check("every live key is pruned for EVERY edge type (anti-phantom, full vocabulary)", missing == [],
          missing[:5])
    check("...exactly once (no redundant second prune in another transaction)", dup == [], dup[:5])
    # the anchor's CALLS and its typed edges: a node that declares both is swapped in ONE transaction
    calls_tx = {k: ctxs[0] for (t, k), ctxs in where.items() if t == "CALLS"}
    typed_tx = {k: ctxs[0] for (t, k), ctxs in where.items() if t != "CALLS"}
    split = [k for k in live if calls_tx.get(k) != typed_tx.get(k)]
    check("a node's :CALLS prune and its typed/IMPORTS prunes are in the same transaction", split == [],
          split[:5])


def gate_label_qualified() -> None:
    print("\n-- E4: typed/:IMPORTS writes are label-qualified UNWIND batches --")
    fx = _fixture()
    rec, _ = _push(25, fx)
    typed = [c for _ctx, c, _p in rec.stmts if "MERGE" in c and "[r:" in c and "[r:CALLS]" not in c]
    check("typed/IMPORTS creates are UNWIND batches", typed and all(c.startswith("UNWIND $rows") for c in typed),
          typed[:2])
    check("...each endpoint MATCHed by label (no key-only per-edge MATCH)",
          typed and all("{key:$a}" not in c and "(a:" in c and "(b:" in c for c in typed), typed[:2])
    n_creates = len(_creates(rec))
    check("statement count is O(batches), not O(edges)", len(rec.stmts) < n_creates, f"{len(rec.stmts)} stmts "
          f"for {n_creates} rows")


def gate_failure_rolls_back_group() -> None:
    print("\n-- E5: a failure in a later group rolls back that group only, and propagates --")
    fx = _fixture()
    rec, err = _push(25, fx, fail_on=lambda tid, c: tid == 3 and "[r:INHERITS]" in c and "MERGE" in c)
    check("the failure propagates (fail loud)", isinstance(err, RuntimeError), repr(err))
    check("transactions 1-2 committed", rec.txs.get(1, {}).get("committed") and rec.txs.get(2, {}).get("committed"),
          rec.txs)
    check("transaction 3 was NOT committed (its anchors keep their old edges)",
          3 in rec.txs and not rec.txs[3]["committed"], rec.txs.get(3))
    check("no transaction was started after the failure", max(rec.txs) == 3, sorted(rec.txs))


def _hub_fixture():
    # x (1 row) comes right BEFORE the hub, so a grouping that only closes a group AFTER it overflows
    # would put x's edges into the hub's transactions
    nodes = [_node("x.py::x:1", "x", "Function", "x.py"), _node("hub.py::hub:1", "hub", "Function", "hub.py")]
    nodes += [_node(f"t.py::t{i}:{i + 1}", f"t{i}", "Function", "t.py", i + 1) for i in range(30)]
    nodes += [_node("y.py::y:1", "y", "Function", "y.py")]
    calls = [("hub.py::hub:1", f"t{i}") for i in range(30)] + [("x.py::x:1", "t0"), ("y.py::y:1", "t1")]
    return nodes, calls, [], []


def gate_oversized_anchor() -> None:
    print("\n-- E6: an anchor bigger than the budget is isolated AND written within the budget --")
    rec, _ = _push(10, _hub_fixture())
    by_tx, rows_tx = {}, {}
    for ctx, _t, a, _b, _c in _creates(rec):
        by_tx.setdefault(ctx, set()).add(a)
        rows_tx[ctx] = rows_tx.get(ctx, 0) + 1
    hub_tx = [ctx for ctx, anchors in by_tx.items() if "hub.py::hub:1" in anchors]
    hub_rows = {(a, b) for _ctx, t, a, b, _c in _creates(rec) if a == "hub.py::hub:1" and t == "CALLS"}
    check("all 30 of the hub's calls are written", len(hub_rows) == 30, len(hub_rows))
    check("...in transactions that hold no other anchor's rows",
          hub_tx and all(by_tx[c] == {"hub.py::hub:1"} for c in hub_tx), [by_tx[c] for c in hub_tx])
    check("EVERY transaction stays within the budget — the hub's included (round 2 gave it one "
          "transaction of any size)", rows_tx and max(rows_tx.values()) <= 10, sorted(rows_tx.values()))
    check("every transaction committed", all(t["committed"] for t in rec.txs.values()), rec.txs)


# ---- E0: a tiny interpreter of the push's Cypher, to prove the RESULTING GRAPH is identical -----------
_NODE_UPSERT = re.compile(r"^UNWIND \$rows AS row MERGE \(x:(\w+) \{key:row\.key\}\)")
# an anchored prune: forward types at the source (a), reversed ones at the target; one type (inside a
# group's transaction) or several (the batched pre-prune of an oversized anchor)
_PRUNE = re.compile(r"^MATCH (?:\(a\)-\[r:([\w|]+)\]->\(\)|\(\)-\[r:([\w|]+)\]->\(a\)) WHERE a\.key IN \$keys (.*)$")
_BATCHED = re.compile(r"^CALL (?:\(r\) \{ DELETE r \}|\{ WITH r DELETE r \}) IN TRANSACTIONS OF (\d+) ROWS$")
# the existing-degree read: MATCH (a) WHERE a.key IN $keys ... RETURN <key>, <degree>
_DEGREE = re.compile(r"^MATCH \(a\) WHERE a\.key IN \$keys (.*) RETURN (\w+), (\w+)$")
_FWD = re.compile(r"\(a\)-\[\w*:([\w|]+)\]->\(\)")
_REV = re.compile(r"\(\)-\[\w*:([\w|]+)\]->\(a\)|\(a\)<-\[\w*:([\w|]+)\]-\(\)")
_END = re.compile(r"\((a|b)(?::(\w+))? \{key:row\.(a|b)\}\)")
_MERGE = re.compile(r"MERGE \((a|b)\)-\[r:(\w+)\]->\((a|b)\)")
# the relabel-twin census (sync._relabel_twins: per label one key-only scan, then a confirming key seek over the
# candidates only, returning labels and the class shell rule's file, name and line) and its deletes
# (sync._delete_relabel_twins through _run_batched: relationships out, then in, then the nodes; each spelling
# _run_batched may send: the scope clause, the importing WITH, the plain statement): the relabel twins matched by
# label + key + none of the key's live labels, the class shells by :Class + key + the same file, name and line
_TWIN_SCAN = re.compile(r"^MATCH \(n:(\w+)\) WHERE n\.key IS NOT NULL RETURN n\.key AS k$")
_TWIN_CENSUS = re.compile(r"^UNWIND \$keys AS twin_key MATCH \(n:(\w+) \{key: twin_key\}\) "
                          r"RETURN n\.key AS k, labels\(n\) AS ls, n\.source_file AS sf, n\.name AS name, "
                          r"n\.start_line AS line$")
_TWIN_DELETE = re.compile(r"^UNWIND \$cand AS twin MATCH \(n:(\w+) \{key: twin\.k\}\) "
                          r"WHERE (NONE\(l IN labels\(n\) WHERE l IN twin\.ls\)|n\.source_file = twin\.sf AND "
                          r"n\.name = twin\.name AND n\.start_line = twin\.line)"
                          r"(?: MATCH (\(n\)-\[r\]->\(\)|\(\)-\[r\]->\(n\)))? (.*)$")
_TWIN_ACTION = re.compile(r"^(?:CALL \((r|n)\) \{ (DELETE r|DETACH DELETE n) \} IN TRANSACTIONS OF (\d+) ROWS"
                          r"|CALL \{ WITH (r|n) (DELETE r|DETACH DELETE n) \} IN TRANSACTIONS OF (\d+) ROWS"
                          r"|(DELETE r|DETACH DELETE n))$")
# the full-text rebuild's pending stamp (sync._mark_fts_rebuild(driver, True)), set before a twin delete
_META_STAMP = re.compile(r"^MERGE \(m:_DeepMapMeta \{id:'singleton'\}\) SET m\.(\w+) = true$")


class _SyntaxErr(Exception):
    code = "Neo.ClientError.Statement.SyntaxError"


class _Graph:
    """A tiny model of what the push's Cypher does to a graph. Anchors are matched BY KEY ALONE (every
    label), as Neo4j does for `a.key IN $keys`. `ops` counts the edges deleted + edge rows merged by the
    statements applied since the last reset — the transaction-state measure the budget bounds."""

    def __init__(self, reject=()):
        self.nodes, self.rels, self.unknown = set(), {}, []
        self.reject = tuple(reject)      # statement fragments this "server" rejects as a syntax error
        self.ops = 0
        self.batched = []                # (rows per inner transaction, edges deleted) per batched delete
        self.degree_reads = []           # params of every existing-degree read
        self.twin_scans = []             # the label of each relabel-twin census scan (key-only, sends no key)
        self.twin_census = []            # (label, keys asked) per confirming census seek
        self.twin_batched = []           # (out|in|node, rows per inner transaction or None, removed) per twin delete
        self.props = {}                  # (label, key) -> {"sf", "name", "line"}: what the upsert wrote (or a fixture)
        self.meta = {}                   # properties set on the :_DeepMapMeta singleton
        self.trace = []                  # the statement kinds in the order they took effect

    def run(self, cypher, p):
        c = " ".join(cypher.split())
        if any(r in c for r in self.reject):
            raise _SyntaxErr(f"Invalid input: {c[:60]}")
        m = _NODE_UPSERT.match(c)
        if m:
            self.nodes |= {(m.group(1), r["key"]) for r in p["rows"]}
            for r in p["rows"]:
                self.props[(m.group(1), r["key"])] = {"sf": r.get("sf"), "name": r.get("name"), "line": r.get("line")}
            self.trace.append("upsert")
            return []
        m = _TWIN_SCAN.match(c)
        if m:
            self.twin_scans.append(m.group(1))
            self.trace.append("census")
            return [{"k": k} for (lb, k) in sorted(self.nodes) if lb == m.group(1)]
        m = _TWIN_CENSUS.match(c)
        if m:
            # a node here carries exactly one label, so its labels(n) is [that label]
            keys = set(p["keys"])
            self.twin_census.append((m.group(1), len(p["keys"])))
            self.trace.append("census")
            out = []
            for (lb, k) in sorted(self.nodes):
                if lb == m.group(1) and k in keys:
                    pr = self.props.get((lb, k)) or {}
                    out.append({"k": k, "ls": [lb], "sf": pr.get("sf"), "name": pr.get("name"), "line": pr.get("line")})
            return out
        m = _TWIN_DELETE.match(c)
        if m:
            lb, guard, side, tail = m.groups()
            a = _TWIN_ACTION.match(tail)
            if a and a.group(1):
                var, action, rows = a.group(1), a.group(2), int(a.group(3))
            elif a and a.group(4):
                var, action, rows = a.group(4), a.group(5), int(a.group(6))
            elif a:
                var, action, rows = ("r" if a.group(7) == "DELETE r" else "n"), a.group(7), None
            else:
                var = action = rows = None
            if (var, action) != (("r", "DELETE r") if side else ("n", "DETACH DELETE n")):
                self.unknown.append(c)
                return []
            if guard.startswith("NONE"):
                # NONE(l IN labels(n) WHERE l IN twin.ls): the node's one label is none of its key's live labels
                hit = {(lb, t["k"]) for t in p["cand"] if (lb, t["k"]) in self.nodes and lb not in t["ls"]}
            else:
                # a class shell: the same file, name and line as the candidate says (a node without them never)
                hit = {(lb, t["k"]) for t in p["cand"] if (lb, t["k"]) in self.nodes
                       and (self.props.get((lb, t["k"])) or {}) == {"sf": t["sf"], "name": t["name"],
                                                                     "line": t["line"]}}
            if side:
                end = 1 if side.startswith("(n)") else 2
                gone = [rk for rk in self.rels if rk[end] in hit]
                kind = "out" if end == 1 else "in"
            else:
                gone = [rk for rk in self.rels if rk[1] in hit or rk[2] in hit]     # DETACH
                self.nodes -= hit
                for h in hit:
                    self.props.pop(h, None)
                kind = "node"
            for rk in gone:
                del self.rels[rk]
            if rows is None:
                self.ops += len(gone)            # the plain spelling: one unbatched statement
            self.twin_batched.append((kind, rows, len(hit) if kind == "node" else len(gone)))
            self.trace.append(f"twin_{kind}")
            return []
        m = _META_STAMP.match(c)
        if m:
            self.meta[m.group(1)] = True
            self.trace.append("stamp")
            return []
        m = _DEGREE.match(c)
        if m:
            fwd = {t for g in _FWD.findall(c) for t in g.split("|")}
            rev = {t for pair in _REV.findall(c) for g in pair if g for t in g.split("|")}
            keys = set(p["keys"])
            self.degree_reads.append(p)
            self.trace.append("degree")
            out = []
            for n in sorted(x for x in self.nodes if x[1] in keys):
                d = sum(1 for (et, na, nb) in self.rels if (et in fwd and na == n) or (et in rev and nb == n))
                if d > 0:
                    out.append({m.group(2): n[1], m.group(3): d})
            return out
        m = _PRUNE.match(c)
        if m:
            types, side = (m.group(1), 1) if m.group(1) else (m.group(2), 2)
            tail = m.group(3)
            b = _BATCHED.match(tail)
            if tail != "DELETE r" and not b:
                self.unknown.append(c)
                return []
            types, keys = set(types.split("|")), set(p["keys"])
            gone = [rk for rk in self.rels if rk[0] in types and rk[side][1] in keys]
            for rk in gone:
                del self.rels[rk]
            if b:
                self.batched.append((int(b.group(1)), len(gone)))
            else:
                self.ops += len(gone)
            self.trace.append("prune")
            return []
        mm, ends = _MERGE.search(c), _END.findall(c)
        if c.startswith("UNWIND $rows AS row MATCH") and mm and len(ends) == 2:
            frm, et, to = mm.groups()
            for row in p["rows"]:
                cand = {v: [n for n in self.nodes if n[1] == row[k] and (not lb or n[0] == lb)]
                        for v, lb, k in ends}
                for na in cand[frm]:
                    for nb in cand[to]:
                        props = self.rels.setdefault((et, na, nb), {})
                        props["confidence"] = row["c"]
                        if "r += row.attrs" in c:
                            props.update(row.get("attrs") or {})
                        self.ops += 1
            self.trace.append("merge")
            return []
        self.unknown.append(c)
        return []


class _GTx:
    def __init__(self, g, log, tid):
        self.g, self.log, self.tid, self.buf = g, log, tid, []

    def run(self, cypher, **p):
        self.buf.append((cypher, p))
        return _Result()

    def commit(self):
        self.g.ops = 0
        for c, p in self.buf:             # applied only on commit: a rolled-back group changes nothing
            self.g.run(c, p)
        self.log.append(self.tid)
        self.g.tx_ops = getattr(self.g, "tx_ops", []) + [self.g.ops]
        self.buf = []

    def rollback(self):
        self.buf = []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _GSess:
    def __init__(self, g, log):
        self.g, self.log = g, log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        before = self.g.ops
        out = _Result(self.g.run(cypher, p) or [])
        if self.g.ops != before:          # an edge write outside an explicit transaction: its own "tx"
            self.g.auto_ops = getattr(self.g, "auto_ops", []) + [self.g.ops - before]
            self.g.ops = before
        return out

    def begin_transaction(self, *a, **k):
        return _GTx(self.g, self.log, len(self.log) + 1)


class _GDrv:
    def __init__(self, g):
        self.g, self.log = g, []

    def session(self, *a, **k):
        return _GSess(self.g, self.log)


def _prior(fx):
    g = _Graph()
    for n in fx[0]:
        g.nodes.add((n["label"], n["key"]))
    g.nodes.add(("Class", "other.py::X:1"))                       # NOT part of this sync
    live, peer, out = ("Class", "m0.py::C0:1"), ("Class", "m1.py::C1:1"), ("Class", "other.py::X:1")
    for et in sorted(ALLOWED_EDGE_TYPES | {"CALLS"}):
        rev = et == "CASCADES"                                       # reversed: anchored at its TARGET
        g.rels[(et, peer, live) if rev else (et, live, peer)] = {"confidence": "INFERRED"}   # phantom
        g.rels[(et, live, out) if rev else (et, out, live)] = {"confidence": "INFERRED"}     # out of scope
    return g


def _phantoms():
    live, peer = ("Class", "m0.py::C0:1"), ("Class", "m1.py::C1:1")
    return [(et, peer, live) if et == "CASCADES" else (et, live, peer)
            for et in sorted(ALLOWED_EDGE_TYPES | {"CALLS"})]


def _out_of_scope():
    live, out = ("Class", "m0.py::C0:1"), ("Class", "other.py::X:1")
    return [(et, live, out) if et == "CASCADES" else (et, out, live)
            for et in sorted(ALLOWED_EDGE_TYPES | {"CALLS"})]


def gate_semantic_equivalence() -> None:
    print("\n-- E0: the budget-split push leaves the SAME graph as one transaction (phantoms pruned) --")
    fx = _fixture()
    views = {}
    for budget in (10**9, 25, 3, 1):
        g = _prior(fx)
        S = _load()
        S._EDGE_TX_MAX_ROWS = budget
        drv = _GDrv(g)
        S.push_to_neo4j(drv, *fx)
        views[budget] = (dict(g.rels), len(drv.log), list(g.unknown), list(getattr(g, "tx_ops", [])),
                         list(g.batched), list(getattr(g, "auto_ops", [])))
    ref, n_ref, unk = views[10**9][:3]
    check("the interpreter understood every statement", not unk and all(not v[2] for v in views.values()),
          unk[:2])
    check("reference: one transaction", n_ref == 1, n_ref)
    for budget in (25, 3, 1):
        rels, ntx, _, tx_ops, batched, auto_ops = views[budget]
        check(f"budget {budget}: {ntx} transactions, graph IDENTICAL to the single-transaction push",
              ntx > 1 and rels == ref, f"ntx={ntx} diff={set(rels) ^ set(ref)}"[:400])
        check(f"budget {budget}: every transaction's deleted + written edges <= {budget}, every batched "
              f"delete's inner transaction <= {budget} rows, no unbatched edge write outside a transaction",
              tx_ops and max(tx_ops) <= budget and all(n <= budget for n, _d in batched) and not auto_ops,
              f"tx_ops={sorted(tx_ops, reverse=True)[:5]} batched={batched[:5]} auto={auto_ops[:5]}")
    phantom_left = [rk for rk in _phantoms() if rk in ref]
    check("every phantom edge anchored at a live node is pruned (all 14 types)", phantom_left == [],
          phantom_left[:3])
    kept = [rk for rk in _out_of_scope() if rk in ref]
    check("edges anchored OUTSIDE this sync are kept (all 14 types)", len(kept) == 14, len(kept))
    check("the fresh edges are present (e.g. m0 INHERITS Base, CASCADES reversed Base <- m0 class)",
          ("INHERITS", ("Class", "m0.py::C0:1"), ("Class", "base.py::Base:1")) in ref
          and ("CASCADES", ("Class", "base.py::Base:1"), ("Class", "m0.py::C0:1")) in ref, "")


# ---- E8: the budget counts the edges a transaction PRUNES --------------------------------------------
def _interp(fx, g, budget):
    S = _load()
    S._EDGE_TX_MAX_ROWS = budget
    drv = _GDrv(g)
    err = None
    try:
        S.push_to_neo4j(drv, *fx)
    except Exception as e:  # noqa: BLE001 — surfaced by the checks
        err = e
    return drv, err


def _upgrade_prior(fx, fanout=30):
    """The graph an older sync left: this fixture's own edges + `fanout` now-gone AMBIGUOUS :IMPORTS per
    module (v0.27.4's cross-language `index` fan-out, which W5's resolved imports no longer produce)."""
    g = _Graph()
    S = _load()
    S._EDGE_TX_MAX_ROWS = 10**9
    S.push_to_neo4j(_GDrv(g), *fx)
    mods = [n["key"] for n in fx[0] if n["label"] == "Module"]
    for i, m in enumerate(mods):
        for j in range(1, fanout + 1):
            g.rels[("IMPORTS", ("Module", m), ("Module", mods[(i + j) % len(mods)]))] = {"confidence": "AMBIGUOUS"}
    g.tx_ops, g.batched, g.auto_ops, g.degree_reads = [], [], [], []
    return g


def gate_prune_budget() -> None:
    print("\n-- E8: the budget counts the EXISTING edges each transaction prunes --")
    fx = _fixture()
    ref = _upgrade_prior(fx)
    stale_before = sum(1 for (et, _a, _b), p in ref.rels.items() if et == "IMPORTS" and p["confidence"] == "AMBIGUOUS")
    _interp(fx, ref, 10**9)
    check("fixture: the prior graph holds 1,000+ now-stale :IMPORTS anchored at this sync's modules",
          stale_before >= 1000, stale_before)
    g = _upgrade_prior(fx)
    drv, err = _interp(fx, g, 100)
    tx_ops = list(getattr(g, "tx_ops", []))
    check("the upgrade re-sync completes", err is None, repr(err))
    check("EVERY write transaction's pruned + written edges stay within the budget (100) — round 2 "
          "counted only the new rows, so one transaction deleted hundreds of old edges",
          tx_ops and max(tx_ops) <= 100, sorted(tx_ops, reverse=True)[:6])
    check("...and the graph is IDENTICAL to the single-transaction push (no stale :IMPORTS left)",
          g.rels == ref.rels and not any(p["confidence"] == "AMBIGUOUS" for (et, _a, _b), p in g.rels.items()
                                         if et == "IMPORTS"),
          f"diff={len(set(g.rels) ^ set(ref.rels))}")
    reads = g.degree_reads
    live = {n["key"] for n in fx[0]}
    check("the existing degree is read ONCE, for exactly this sync's nodes",
          len(reads) == 1 and set(reads[0].get("keys") or []) == live, [len(r.get("keys") or []) for r in reads])
    check("the interpreter understood every statement", not g.unknown, g.unknown[:2])

    # the read counts EXACTLY what the prunes will delete: E0's prior has 14 phantoms anchored at C0 (13
    # forward types + CALLS out of C0, CASCADES INTO C0 — reversed, anchored at its target) and 14 edges
    # that touch C0 but are anchored OUTSIDE this sync (never pruned, so never counted)
    S = _load()
    gp = _prior(fx)
    got = S._existing_edge_counts(_GDrv(gp), [n["key"] for n in fx[0]])
    check("the degree read counts exactly the anchored edges the prunes delete ({C0: 14}; a reversed "
          "CASCADES counted at its target, edges anchored outside the sync not at all)",
          got == {"m0.py::C0:1": 14}, got)

    # a hub whose OLD edges alone exceed the budget: its old edges are pruned in bounded batches first,
    # then its new rows are written. The node with the SAME KEY under another label is counted too (the degree
    # read matches by key alone, as the prune does), and since F1 (2026-10) it is a relabel twin: the push
    # deletes it with its edges, in the twin delete's own bounded batches, before the upsert
    nodes = [_node("hub.py::hub:1", "hub", "Function", "hub.py"), _node("t.py::t0:1", "t0", "Function", "t.py"),
             _node("z.py::z:1", "z", "Function", "z.py")]
    fx2 = (nodes, [("hub.py::hub:1", "t0"), ("z.py::z:1", "t0")], [], [])

    def _hub_prior():
        g2 = _Graph()
        for n in nodes:
            g2.nodes.add((n["label"], n["key"]))
        g2.nodes.add(("Method", "hub.py::hub:1"))                  # stale: same key, another label
        for i in range(300):
            old = ("Function", f"old.py::o{i}:{i + 1}")
            g2.nodes.add(old)
            g2.rels[("CALLS", ("Function", "hub.py::hub:1"), old)] = {"confidence": "AMBIGUOUS"}
            g2.rels[("INHERITS", ("Method", "hub.py::hub:1"), old)] = {"confidence": "INFERRED"}
        g2.rels[("CALLS", ("Function", "z.py::z:1"), ("Function", "old.py::o0:1"))] = {"confidence": "INFERRED"}
        return g2

    ref2 = _hub_prior()
    _interp(fx2, ref2, 10**9)
    g2 = _hub_prior()
    _drv2, err2 = _interp(fx2, g2, 100)
    tx2 = list(getattr(g2, "tx_ops", []))
    check("hub with 600 old edges (two labels, one key), budget 100: completes", err2 is None, repr(err2))
    check("...no write transaction exceeds the budget", tx2 and max(tx2) <= 100, sorted(tx2, reverse=True)[:4])
    S2 = _load()
    twin_edges = sum(d for kind, _n, d in g2.twin_batched if kind in ("out", "in"))
    check("...its old edges went in BATCHED deletes: the live node's in the pre-prune's inner transactions of "
          "<= 100 rows, the same-key :Method node's (a relabel twin) in the twin delete's of <= "
          "_DELETE_REL_BATCH rows; 600 in all",
          g2.batched and all(n <= 100 for n, _d in g2.batched)
          and g2.twin_batched and all(n is not None and n <= max(S2._DELETE_REL_BATCH, S2._DELETE_NODE_BATCH)
                                      for _k, n, _d in g2.twin_batched)
          and sum(d for _n, d in g2.batched) + twin_edges >= 600,
          f"prune={g2.batched} twin={g2.twin_batched}")
    check("...the same-key node under the old label is gone (removed as a relabel twin), the live one stays",
          ("Method", "hub.py::hub:1") not in g2.nodes and ("Function", "hub.py::hub:1") in g2.nodes
          and ("Method", "hub.py::hub:1") not in ref2.nodes, sorted(n for n in g2.nodes if "hub" in n[1]))
    check("...no unbatched edge write ran outside a transaction", not getattr(g2, "auto_ops", []),
          getattr(g2, "auto_ops", []))
    check("...and the graph is IDENTICAL to the single-transaction push (hub -> t0 only; every stale edge of "
          "both same-key nodes gone; z's edge rebuilt)",
          g2.rels == ref2.rels and set(k for k in g2.rels if k[1][1] == "hub.py::hub:1")
          == {("CALLS", ("Function", "hub.py::hub:1"), ("Function", "t.py::t0:1"))},
          sorted(g2.rels)[:6])

    # the degree read: an older server without COUNT { } falls back; a real error propagates before any write
    g3 = _upgrade_prior(fx)
    g3.reject = ("COUNT {",)
    _drv3, err3 = _interp(fx, g3, 100)
    tx3 = list(getattr(g3, "tx_ops", []))
    check("a server without COUNT { } subqueries: the fallback degree read runs, the push stays bounded "
          "and identical", err3 is None and tx3 and max(tx3) <= 100 and g3.rels == ref.rels
          and len(g3.degree_reads) == 1, f"err={err3!r} tx={sorted(tx3, reverse=True)[:3]}")

    class _Down(Exception):
        code = "Neo.TransientError.General.DatabaseUnavailable"

    class _DownSess(_Sess):
        def run(self, cypher, **p):
            if "RETURN" in cypher and "MERGE" not in cypher:
                raise _Down("database unavailable")
            return super().run(cypher, **p)

    class _DownDrv(_Drv):
        def session(self, *a, **k):
            return _DownSess(self.rec)

    S = _load()
    rec = _Rec()
    raised = False
    try:
        S.push_to_neo4j(_DownDrv(rec), *fx)
    except _Down:
        raised = True
    check("a database error in the degree read propagates (fail loud, never guessed as 0)", raised)
    check("...before ANY edge prune or write ran", not rec.txs and not _prunes(rec) and not _creates(rec),
          f"txs={len(rec.txs)} prunes={len(_prunes(rec))}")


# ---- E7: graph-wide deletes (stale prune, project-switch wipe) are batched too -----------------------
class _DelSess:
    def __init__(self, log, reject=()):
        self.log, self.reject = log, reject

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        c = " ".join(cypher.split())
        if any(r in c for r in self.reject):
            raise _SyntaxErr(f"Invalid input: {c[:40]}")
        self.log.append((c, p))
        return _Result()


class _DelDrv:
    def __init__(self, reject=()):
        self.log, self.reject = [], reject

    def session(self, *a, **k):
        return _DelSess(self.log, self.reject)


def gate_batched_deletes() -> None:
    print("\n-- E7: graph-wide deletes run in bounded inner transactions --")
    S = _load()
    d = _DelDrv()
    S._cleanup_stale(d, ["k1", "k2"])
    stmts = [c for c, _p in d.log]
    check("stale prune: every delete statement is batched (CALL { ... } IN TRANSACTIONS)",
          stmts and all("IN TRANSACTIONS" in c for c in stmts), stmts)
    check("stale prune: relationships are deleted BEFORE their nodes (a hub node never puts its whole "
          "degree into one transaction)",
          len(stmts) >= 2 and "DELETE r" in stmts[0] and "DETACH DELETE n" in stmts[-1], stmts)
    check("stale prune: the keyed, not-live filter is kept on every statement (keyless meta survives)",
          all("n.key IS NOT NULL" in c and "NOT n.key IN $keys" in c for c in stmts)
          and all(p.get("keys") == ["k1", "k2"] for _c, p in d.log), stmts)

    S = _load()
    d = _DelDrv()
    S._wipe_all(d)
    stmts = [c for c, _p in d.log]
    check("switch wipe: relationships + nodes deleted in batches",
          len(stmts) >= 3 and all("IN TRANSACTIONS" in c for c in stmts[:-1]), stmts)
    check("switch wipe: the owner stamp (:_DeepMapMeta) is deleted LAST — a wipe that dies half-way still "
          "names the old owner, so the next sync can never adopt the leftovers",
          stmts and "_DeepMapMeta" in stmts[-1] and all("NOT n:_DeepMapMeta" in c for c in stmts[1:-1]),
          stmts)

    # an older server without the `CALL (x) { }` scope clause: fall back to the importing-WITH form, then
    # to the plain statement — the delete must still happen
    S = _load()
    d = _DelDrv(reject=("CALL (",))
    S._cleanup_stale(d, ["k1"])
    stmts = [c for c, _p in d.log]
    check("no scope-clause support -> the importing-WITH batched form runs instead",
          stmts and all("IN TRANSACTIONS" in c and "CALL { WITH" in c for c in stmts), stmts)
    S = _load()
    d = _DelDrv(reject=("CALL (", "CALL { WITH"))
    S._cleanup_stale(d, ["k1"])
    stmts = [c for c, _p in d.log]
    check("no CALL IN TRANSACTIONS at all -> the plain delete still runs (never skipped)",
          stmts and any("DETACH DELETE n" in c for c in stmts) and not any("IN TRANSACTIONS" in c for c in stmts),
          stmts)

    class _Boom(Exception):
        code = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"

    class _BoomSess(_DelSess):
        def run(self, cypher, **p):
            raise _Boom("out of memory")

    class _BoomDrv(_DelDrv):
        def session(self, *a, **k):
            return _BoomSess(self.log)

    S = _load()
    raised = False
    try:
        S._cleanup_stale(_BoomDrv(), ["k1"])
    except _Boom:
        raised = True
    check("a NON-syntax database error propagates (fail loud, never retried as another form)", raised)


# ---- E9: a node under a label its key no longer has (a relabel twin) is removed by the push ------------
_TWIN_FN = ("Function", "m0.py::C0.run:2")     # this sync pushes the key as a :Method
_TWIN_MOD = ("Class", "m1.py::module")         # this sync pushes the key as a :Module
_STALE = ("Function", "gone.py::f:1")          # a key this sync does not push at all: the prune's business
# a class shell: a stale :Class with the file, name and line of the :Class this sync pushes as m5.py::C5:1
_SHELL = ("Class", "m5.py#C5@L1")
# stale classes that duplicate nothing this sync pushes (another line; another file): the prune's business
_NOT_SHELLS = {("Class", "m6.py::C6:9"): {"sf": "m6.py", "name": "C6", "line": 9},
               ("Class", "x6.py::C6:1"): {"sf": "x6.py", "name": "C6", "line": 1}}


def _twin_prior(fx):
    g = _prior(fx)
    g.nodes |= {_TWIN_FN, _TWIN_MOD, _STALE, _SHELL} | set(_NOT_SHELLS)
    g.props[_SHELL] = {"sf": "m5.py", "name": "C5", "line": 1}
    g.props.update(_NOT_SHELLS)
    helper, live = ("Function", "base.py::helper:5"), ("Method", "m0.py::C0.run:2")
    g.rels[("CALLS", _TWIN_FN, helper)] = {"confidence": "AMBIGUOUS"}                       # out of the twin
    g.rels[("CALLS", ("Method", "m2.py::C2.run:2"), _TWIN_FN)] = {"confidence": "INFERRED"}  # into it
    g.rels[("IMPORTS", ("Module", "m3.py::module"), _TWIN_MOD)] = {"confidence": "INFERRED"}
    g.rels[("CALLS", _STALE, helper)] = {"confidence": "INFERRED"}
    g.rels[("CALLS", live, helper)] = {"confidence": "INFERRED"}                             # the live node's
    g.rels[("INHERITS", _SHELL, ("Class", "base.py::Base:1"))] = {"confidence": "INFERRED"}  # out of the shell
    g.rels[("CALLS", ("Method", "m4.py::C4.run:2"), _SHELL)] = {"confidence": "AMBIGUOUS"}   # into it
    return g


def gate_relabel_twins() -> None:
    print("\n-- E9: a relabel twin is removed by the push (census + delete through the interpreter) --")
    fx = _fixture()
    views, errs = {}, {}
    for budget in (10**9, 25, 3, 1):
        g = _twin_prior(fx)
        _drv, errs[budget] = _interp(fx, g, budget)
        views[budget] = g
    g = views[10**9]
    check("the push completes at every budget", all(e is None for e in errs.values()),
          {b: repr(e) for b, e in errs.items() if e is not None})
    check("the interpreter understood every statement (census, twin delete, pending stamp included)",
          all(not v.unknown for v in views.values()), [v.unknown[:1] for v in views.values() if v.unknown])
    check("the census scanned every node-key label exactly once, each scan naming its label",
          sorted(g.twin_scans) == sorted(set(g.twin_scans))
          and set(g.twin_scans) >= {"Function", "Method", "Class", "Module", "Route"}, g.twin_scans)
    check("...and sent keys only for the candidates: the :Function key pushed as :Method, and under :Class the "
          "key pushed as :Module plus the 4 stale class keys (1 + 5 keys, two seeks)",
          sorted(g.twin_census) == [("Class", 5), ("Function", 1)], g.twin_census)
    check("both twins are gone, each with every relationship it had (out and in)",
          _TWIN_FN not in g.nodes and _TWIN_MOD not in g.nodes
          and not any(_TWIN_FN in (a, b) or _TWIN_MOD in (a, b) for (_t, a, b) in g.rels),
          sorted(rk for rk in g.rels if _TWIN_FN in rk[1:] or _TWIN_MOD in rk[1:])[:3])
    check("the class shell is gone with both its relationships; the stale classes that duplicate nothing (another "
          "line, another file) and the out-of-scope class stay",
          _SHELL not in g.nodes and not any(_SHELL in (a, b) for (_t, a, b) in g.rels)
          and set(_NOT_SHELLS) <= g.nodes and ("Class", "other.py::X:1") in g.nodes,
          sorted(n for n in g.nodes if n[0] == "Class" and not n[1].startswith(("m", "base")))[:6])
    check("the live nodes under their pushed labels stay, with their rebuilt edges",
          ("Method", "m0.py::C0.run:2") in g.nodes and ("Module", "m1.py::module") in g.nodes
          and ("CALLS", ("Method", "m0.py::C0.run:2"), ("Function", "base.py::helper:5")) in g.rels, "")
    check("a stale key (not in this extraction) and a node outside this sync are left to the prune",
          _STALE in g.nodes and ("CALLS", _STALE, ("Function", "base.py::helper:5")) in g.rels
          and ("Class", "other.py::X:1") in g.nodes, "")
    kinds = [k for k, _n, _d in g.twin_batched]
    check("the delete: relationships out, then in, then the nodes, per label and then for the shells, each batched "
          "IN TRANSACTIONS", len(kinds) == 9
          and all(kinds[i:i + 3] == ["out", "in", "node"] for i in range(0, len(kinds), 3))
          and all(n is not None for _k, n, _d in g.twin_batched), g.twin_batched)
    t = g.trace

    def first(k):
        return t.index(k) if k in t else -1

    check("order: the census before any write, the pending stamp before the first delete, the deletes "
          "before the upsert",
          0 <= first("census") < first("stamp") < first("twin_out") < first("upsert")
          and g.meta.get("fts_rebuild_pending") is True, t[:12])
    S = _load()
    S._EDGE_TX_MAX_ROWS = 10**9
    res = S.push_to_neo4j(_GDrv(_twin_prior(fx)), *fx)
    check("the push result is still the plain pair; `.relabeled` counts the twins and the shell (3), `.shells` the "
          "shell (1)", len(tuple(res)) == 2 and res[0] == len(fx[0]) and getattr(res, "relabeled", None) == 3
          and getattr(res, "shells", None) == 1,
          (tuple(res), getattr(res, "relabeled", None), getattr(res, "shells", None)))
    for budget in (25, 3, 1):
        v = views[budget]
        check(f"budget {budget}: the same nodes and edges as the single-transaction push",
              v.nodes == g.nodes and v.rels == g.rels,
              f"nodes={sorted(v.nodes ^ g.nodes)[:3]} rels={sorted(set(v.rels) ^ set(g.rels))[:3]}")
    again = _twin_prior(fx)
    _interp(fx, again, 10**9)
    n_before = len(again.twin_batched)
    _interp(fx, again, 10**9)
    check("a second push finds no twin and deletes nothing", len(again.twin_batched) == n_before,
          again.twin_batched[n_before:])
    clean = _prior(fx)
    _interp(fx, clean, 10**9)
    check("no twin: no pending stamp and no twin delete", not clean.twin_batched and not clean.meta,
          (clean.twin_batched, clean.meta))
    for reject, label in ((("CALL (",), "no scope clause -> the importing-WITH batched form"),
                          (("CALL (", "CALL { WITH"), "no CALL IN TRANSACTIONS -> the plain delete")):
        v = _twin_prior(fx)
        v.reject = reject
        _drv, err = _interp(fx, v, 10**9)
        batched = [n is not None for _k, n, _d in v.twin_batched]
        check(f"{label}: understood, the twins still removed, the same graph",
              err is None and not v.unknown and v.nodes == g.nodes and v.rels == g.rels
              and len(batched) == 9 and (all(batched) if len(reject) == 1 else not any(batched)),
              f"err={err!r} unknown={v.unknown[:1]} twin={v.twin_batched}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — bounded edge transactions (all edge types; no Neo4j)")
    print("=" * 68)
    for gate in (gate_semantic_equivalence, gate_equivalence_and_bounds, gate_atomic_per_anchor,
                 gate_label_qualified, gate_failure_rolls_back_group, gate_oversized_anchor,
                 gate_prune_budget, gate_batched_deletes, gate_relabel_twins):
        try:
            gate()
        except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate
            check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
