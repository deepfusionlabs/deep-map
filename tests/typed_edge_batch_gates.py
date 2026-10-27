#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the BATCHED, transaction-agnostic typed-edge / :IMPORTS writes (2026-09 live validation,
round 2 — review finding "the final edge transaction is unbounded"). No Neo4j.

The :CALLS rebuild moved to label-qualified UNWIND batches inside write transactions (sync.py, Y1), but
edge_resolve.push_typed_edges / push_imports still sent ONE statement per edge, each MATCHing both
endpoints with NO label (`MATCH (a {key:$a}),(b {key:$b})` = two full node scans per edge) — so on a
big JS/TS repo (tens of thousands of :IMPORTS) the last edge transaction stayed open for the whole slow
per-edge phase. The fix, locked here:

  B1  O(batches) statements, not O(edges); every UNWIND chunk is bounded by edge_resolve._EDGE_BATCH.
  B2  every typed / :IMPORTS create MATCHes each endpoint BY LABEL (an index seek on deepmap_key_<label>)
      — a multi-label key (a Pascal unit that is both :Module and :Class) under each of its labels.
  B3  the persisted graph is EXACTLY the one the per-edge writer produced: an in-memory interpreter
      replays both writers over the same prior graph (phantom prunes of present AND absent types,
      reversed :CASCADES, attrs, both :IMPORTS tuple shapes, out-of-scope edges kept, :CALLS untouched).
  B4  transaction-AGNOSTIC: the writers only ever call `.run` on what they are handed (a Transaction
      inside sync's edge transaction, or a session).
  B5  injection discipline: an edge type outside ALLOWED_EDGE_TYPES and a node label that is not a
      plain identifier are never interpolated into Cypher.
  B6  the ANCHORED API sync can bound its transactions with: edge_rows / rows_by_anchor /
      rewrite_anchored_edges — each group's prune + re-create is self-contained, and splitting the
      anchors across transactions persists the same graph as one transaction.
  B7  end-to-end through sync.push_to_neo4j with the REAL recognizers: the round-1 resolution fixes
      (T1 :Class anchoring, T2 language family, T3 Rails only:/except:, T4 Go handlers, T5 Go stdlib
      types) are what actually lands in the graph.
  B8  :IMPORTS stay inside the source's language family, and an import whose target is already a node
      KEY binds exactly that node (no last-segment fan-out).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import edge_resolve as ER            # noqa: E402
import framework_edges as FE         # noqa: E402
import go_edges as GO                # noqa: E402
import java_edges as JV              # noqa: E402
import language_recognizers as LR    # noqa: E402
import ruby_rails_edges as RR        # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:600]}"))


# =====================================================================================================
# A tiny in-memory interpreter for EXACTLY the Cypher shapes the push emits. It executes both the old
# per-edge shape and the batched UNWIND shape, so the two writers can be compared on the graph they
# leave behind rather than on statement text.
# =====================================================================================================
_ENDPOINT = re.compile(r"\((a|b)(?::`?(\w+)`?)? \{key:(?:\$|row\.)(a|b)\}\)")
_MERGE_REL = re.compile(r"MERGE \((a|b)\)-\[r:(\w+)\]->\((a|b)\)")
_PRUNE_FWD = re.compile(r"^MATCH \(a\)-\[r:(\w+)\]->\(\) WHERE a\.key IN \$keys DELETE r$")
_PRUNE_REV = re.compile(r"^MATCH \(\)-\[r:(\w+)\]->\(a\) WHERE a\.key IN \$keys DELETE r$")
_NODE_UPSERT = re.compile(r"^UNWIND \$rows AS row MERGE \(x:`?(\w+)`? \{key:row\.key\}\)")
_NODE_MERGE_ONE = re.compile(r"^MERGE \(x:`?(\w+)`? \{key:\$key\}\)")


class MiniGraph:
    def __init__(self):
        self.nodes: set = set()          # (label, key)
        self.rels: dict = {}             # (type, (la, ka), (lb, kb)) -> props
        self.stmts: list = []            # (ctx, cypher, params)
        self.unknown: list = []

    def add_node(self, label, key):
        self.nodes.add((label, key))

    def add_rel(self, et, a, b, **props):
        self.rels[(et, a, b)] = dict(props)

    def view(self) -> dict:
        """{(type, from_label, from_key, to_label, to_key): props} — a comparable snapshot."""
        return {(t, a[0], a[1], b[0], b[1]): dict(p) for (t, a, b), p in self.rels.items()}

    def run(self, cypher, params, ctx=("auto", 0)):
        self.stmts.append((ctx, cypher, params))
        c = " ".join(str(cypher).split())
        m = _NODE_UPSERT.match(c)
        if m:
            for row in params["rows"]:
                self.nodes.add((m.group(1), row["key"]))
            return
        m = _NODE_MERGE_ONE.match(c)
        if m:
            self.nodes.add((m.group(1), params["key"]))
            return
        m = _PRUNE_FWD.match(c) or _PRUNE_REV.match(c)
        if m:
            keys, et = set(params["keys"]), m.group(1)
            side = 1 if m.re is _PRUNE_FWD else 2
            for rk in [rk for rk in self.rels if rk[0] == et and rk[side][1] in keys]:
                del self.rels[rk]
            return
        mm = _MERGE_REL.search(c)
        ends = _ENDPOINT.findall(c)
        if mm and len(ends) == 2 and "r.confidence=" in c:
            rows = params["rows"] if c.startswith("UNWIND $rows AS row ") else [params]
            with_attrs = "r += $attrs" in c or "r += row.attrs" in c
            frm, et, to = mm.groups()
            for row in rows:
                cand = {}
                for var, label, pname in ends:
                    key = row[pname]
                    cand[var] = [n for n in self.nodes if n[1] == key and (not label or n[0] == label)]
                for na in cand[frm]:
                    for nb in cand[to]:
                        props = self.rels.setdefault((et, na, nb), {})
                        props["confidence"] = row["c"]
                        if with_attrs:
                            props.update(row.get("attrs") or {})
            return
        self.unknown.append(c)


class RunOnly:
    """Exposes ONLY .run — what a neo4j Transaction and a Session have in common. Any other attribute
    access raises AttributeError, so a writer that is not transaction-agnostic fails loudly."""
    __slots__ = ("_g", "_ctx")

    def __init__(self, g, ctx=("tx", 1)):
        self._g = g
        self._ctx = ctx

    def run(self, cypher, **params):
        self._g.run(cypher, params, self._ctx)


# =====================================================================================================
# The fixture: a polyglot slice with the shapes that matter to the WRITE layer.
# =====================================================================================================
def _n(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name, "documented": False}


K = {
    "order_mod": "app/models/order.rb::order:1",
    "order": "app/models/order.rb::Order:1",
    "line_item": "app/models/line_item.rb::LineItem:1",
    "line_item_mod": "app/models/line_item.rb::line_item:1",
    "account": "app/models/account.rb::Account:1",
    "app_record": "app/models/application_record.rb::ApplicationRecord:1",
    "routes": "config/routes.rb::routes:1",
    "idx": "app/controllers/orders_controller.rb::OrdersController.index:3",
    "form1": "src/Unit1.pas::TForm1:10",
    "tbase": "src/Base.pas::TBase:1",                 # a key carried by BOTH a :Module and a :Class
    "cs_ctrl": "Controllers/BillingController.cs::BillingController:5",
    "cs_ctx": "Data/BillingContext.cs::BillingContext:4",
    "py_a": "pkg/a.py::a:1",
    "py_b": "pkg/b.py::b:1",
    "py_util1": "pkg/util.py::util:1",
    "py_util2": "lib/util.py::util:1",
    "out_scope": "other/x.py::X:1",                  # in the graph, NOT part of this sync
}


def _fixture_nodes():
    return [
        _n(K["order_mod"], "Module", "order", "app/models/order.rb"),
        _n(K["order"], "Class", "Order", "app/models/order.rb"),
        _n(K["line_item"], "Class", "LineItem", "app/models/line_item.rb"),
        _n(K["line_item_mod"], "Module", "line_item", "app/models/line_item.rb"),
        _n(K["account"], "Class", "Account", "app/models/account.rb"),
        _n(K["app_record"], "Class", "ApplicationRecord", "app/models/application_record.rb"),
        _n(K["routes"], "Module", "routes", "config/routes.rb"),
        _n(K["idx"], "Method", "OrdersController.index", "app/controllers/orders_controller.rb", 3),
        _n(K["form1"], "Class", "TForm1", "src/Unit1.pas", 10),
        _n(K["tbase"], "Module", "TBase", "src/Base.pas"),
        _n(K["tbase"], "Class", "TBase", "src/Base.pas"),
        _n(K["cs_ctrl"], "Class", "BillingController", "Controllers/BillingController.cs", 5),
        _n(K["cs_ctx"], "Class", "BillingContext", "Data/BillingContext.cs", 4),
        _n(K["py_a"], "Module", "a", "pkg/a.py"),
        _n(K["py_b"], "Module", "b", "pkg/b.py"),
        _n(K["py_util1"], "Module", "util", "pkg/util.py"),
        _n(K["py_util2"], "Module", "util", "lib/util.py"),
    ]


EVIL_TYPE = "EVIL]->() DETACH DELETE n //"


def _fixture_edges():
    TE = FE.TypedEdge
    return [
        TE("Order", "app/models/order.rb", "ApplicationRecord", "INHERITS", "NAME", {}),
        TE("Order", "app/models/order.rb", "LineItem", "HAS_MANY", "NAME", {"association": "line_items"}),
        TE("Order", "app/models/order.rb", "LineItem", "CASCADES", "NAME", {"dependent": "destroy"}),
        TE("routes", "config/routes.rb", "OrdersController.index", "ROUTES_TO", "NAME",
           {"method": "GET", "path": "/orders"}),
        TE("TForm1", "src/Unit1.pas", "TBase", "INHERITS", "NAME", {}),
        TE("BillingController", "Controllers/BillingController.cs", "BillingContext", "DEPENDS_ON",
           "NAME", {"via": "ctor"}),
        TE("Order", "app/models/order.rb", "Account", EVIL_TYPE, "NAME", {}),
    ]


def _fixture_imports():
    return [
        (K["order_mod"], "line_item"),     # graphify shape: (src node key, target name)
        ("pkg/a.py", "pkg.b"),             # ast shape: (source relpath, dotted module)
        ("pkg/a.py", "util"),              # -> two modules named util -> AMBIGUOUS x2
        ("pkg/a.py", "os"),                # external -> dropped
        (K["out_scope"], "b"),             # a non-live src -> dropped
        ("garbage",),                      # malformed -> dropped (fuzz-safe)
    ]


def _prior_graph(nodes) -> MiniGraph:
    """The graph BEFORE this sync: this sync's nodes + an out-of-scope node, and phantom edges."""
    g = MiniGraph()
    for n in nodes:
        g.add_node(n["label"], n["key"])
    g.add_node("Class", K["out_scope"])
    C = lambda k: ("Class", K[k])            # noqa: E731
    g.add_rel("INHERITS", C("order"), C("account"), confidence="INFERRED")     # present-type phantom
    g.add_rel("REFERENCES", C("order"), C("account"), confidence="INFERRED")   # ABSENT-type phantom
    g.add_rel("CASCADES", C("account"), C("order"), confidence="INFERRED")     # reversed, into live
    g.add_rel("RENDERS", ("Module", K["routes"]), C("order"), confidence="INFERRED")
    g.add_rel("INHERITS", C("out_scope"), C("order"), confidence="INFERRED")   # out of scope: KEEP
    g.add_rel("CALLS", C("order"), C("line_item"), confidence="INFERRED")      # not typed: KEEP
    g.add_rel("IMPORTS", ("Module", K["py_a"]), C("order"), confidence="EXTRACTED")   # phantom
    return g


def _sync_sequence(sess, nodes, imports, typed_edges):
    """EXACTLY sync._write_typed_and_imports' call sequence (the round-1 integration)."""
    live = []
    for n in nodes:
        if n["key"] not in live:
            live.append(n["key"])
    resolver = ER.Resolver(nodes)
    present = {e.edge_type for e in typed_edges} | ({"IMPORTS"} if imports else set())
    ER.prune_stale_edge_types(sess, live, present)
    counts = ER.push_typed_edges(sess, typed_edges, resolver, live)
    made = ER.push_imports(sess, imports, resolver, live, set(live))
    return counts, made


def _reference_per_edge(g: MiniGraph, nodes, imports, typed_edges) -> None:
    """The ROUND-1 per-edge writer's semantics, applied straight to the interpreter: prune the whole
    vocabulary anchored at live keys, then MERGE every resolved row with an UNLABELED key match."""
    live = []
    for n in nodes:
        if n["key"] not in live:
            live.append(n["key"])
    resolver = ER.Resolver(nodes)
    for et in sorted(FE.ALLOWED_EDGE_TYPES):
        side = 2 if et in ER.REVERSED_EDGE_TYPES else 1
        for rk in [rk for rk in g.rels if rk[0] == et and rk[side][1] in set(live)]:
            del g.rels[rk]
    for et, sk, tk, conf, attrs in ER.resolve_typed_edges(typed_edges, resolver, live):
        a, b = (tk, sk) if et in ER.REVERSED_EDGE_TYPES else (sk, tk)
        for na in [n for n in g.nodes if n[1] == a]:
            for nb in [n for n in g.nodes if n[1] == b]:
                props = g.rels.setdefault((et, na, nb), {})
                props["confidence"] = conf
                props.update(attrs or {})
    seen = set()
    live_set = set(live)
    for src, dst_name in ER.normalize_imports(imports, resolver, live_set):
        if src not in live_set:
            continue
        fam = ER.lang_family(resolver._file_by_key.get(src, ""))
        tks = [tk for tk in resolver.targets(dst_name) if tk != src
               and (fam is None or ER.lang_family(resolver._file_by_key.get(tk, "")) in (None, fam))]
        conf = "AMBIGUOUS" if len(tks) > 1 else "EXTRACTED"
        for tk in tks:
            if (src, tk) in seen:
                continue
            seen.add((src, tk))
            for na in [n for n in g.nodes if n[1] == src]:
                for nb in [n for n in g.nodes if n[1] == tk]:
                    g.rels.setdefault(("IMPORTS", na, nb), {})["confidence"] = conf


def _creates(g: MiniGraph, *, typed_only=True):
    out = []
    for ctx, c, p in g.stmts:
        if "MERGE (" in c and "-[r:" in c and "MERGE (x:" not in c:
            if typed_only and "[r:CALLS]" in c:
                continue
            out.append((ctx, c, p))
    return out


# =====================================================================================================
# B1 / B2 — batching + label-qualified MATCH
# =====================================================================================================
def gate_b1_b2_batched_labelled() -> None:
    print("\n-- B1/B2: O(batches) statements, each endpoint MATCHed by label --")
    n_cls = 2500
    nodes = [_n("lib/base.py::Base:1", "Class", "Base", "lib/base.py")]
    edges, imports = [], []
    for i in range(n_cls):
        sf = f"app/m{i}.py"
        nodes.append(_n(f"{sf}::m{i}:1", "Module", f"m{i}", sf))
        nodes.append(_n(f"{sf}::C{i}:3", "Class", f"C{i}", sf, 3))
        nodes.append(_n(f"{sf}::C{i}.run:5", "Method", f"C{i}.run", sf, 5))
        edges.append(FE.TypedEdge(f"C{i}", sf, "Base", "INHERITS", "NAME", {}))
        imports.append((f"{sf}::m{i}:1", "base"))
    nodes.append(_n("lib/base.py::base:1", "Module", "base", "lib/base.py"))
    g = MiniGraph()
    for n in nodes:
        g.add_node(n["label"], n["key"])
    counts, made = _sync_sequence(RunOnly(g), nodes, imports, edges)
    check("sanity: every INHERITS + IMPORTS row is still produced (2500 + 2500)",
          counts.get("INHERITS") == n_cls and made == n_cls, f"counts={counts} made={made}")
    persisted = [k for k in g.rels if k[0] in ("INHERITS", "IMPORTS")]
    check("...and every one of them is PERSISTED by the statements sent", len(persisted) == 2 * n_cls,
          len(persisted))
    n_stmts = len(g.stmts)
    check("statement count is O(batches), not O(edges) (13 prunes + a handful of UNWIND batches)",
          n_stmts <= 30, f"{n_stmts} statements for {2 * n_cls} edges")
    batch = getattr(ER, "_EDGE_BATCH", None)
    check("edge_resolve exposes a bounded UNWIND batch size (_EDGE_BATCH, 1..5000)",
          isinstance(batch, int) and 0 < batch <= 5000, batch)
    creates = _creates(g)
    sizes = [len(p.get("rows") or []) for _ctx, _c, p in creates]
    check("every typed / :IMPORTS create is an UNWIND $rows batch of 1.._EDGE_BATCH rows",
          creates and all(c.startswith("UNWIND $rows AS row ") for _ctx, c, _p in creates)
          and all(0 < s <= (batch or 1000) for s in sizes), sizes[:10])
    check("no create still MATCHes an endpoint by key alone (the two-AllNodesScans-per-edge shape)",
          creates and not any("{key:$a}" in c or "{key:$b}" in c or "(a {key:" in c or "(b {key:" in c
                              for _ctx, c, _p in creates), [c for _ctx, c, _p in creates][:2])
    inh = [c for _ctx, c, _p in creates if "[r:INHERITS]" in c]
    check("INHERITS rows MATCH (a:Class ...) and (b:Class ...) — the deepmap_key_class index seek",
          inh and all("(a:Class {key:row.a})" in c and "(b:Class {key:row.b})" in c for c in inh), inh[:1])
    imp = [c for _ctx, c, _p in creates if "[r:IMPORTS]" in c]
    check(":IMPORTS rows MATCH (a:Module ...) and (b:Module ...)",
          imp and all("(a:Module {key:row.a})" in c and "(b:Module {key:row.b})" in c for c in imp),
          imp[:1])
    row0 = (creates[0][2].get("rows") or [{}])[0] if creates else {}
    check("a batched row carries a / b / c (+ attrs for typed edges) — the per-edge parameter names",
          {"a", "b", "c"} <= set(row0), row0)


# =====================================================================================================
# B3 — the persisted graph equals the per-edge writer's
# =====================================================================================================
def gate_b3_same_graph() -> None:
    print("\n-- B3: the batched writer persists EXACTLY the per-edge writer's graph --")
    nodes, edges, imports = _fixture_nodes(), _fixture_edges(), _fixture_imports()
    g_new = _prior_graph(nodes)
    _sync_sequence(RunOnly(g_new), nodes, imports, edges)
    g_ref = _prior_graph(nodes)
    _reference_per_edge(g_ref, nodes, imports, edges)
    check("every statement the writers sent is understood by the interpreter", not g_new.unknown,
          g_new.unknown[:3])
    new, ref = g_new.view(), g_ref.view()
    check("final graph (edges + confidence + attrs) == the per-edge reference", new == ref,
          f"only-new={sorted(set(new) - set(ref))[:4]} only-ref={sorted(set(ref) - set(new))[:4]} "
          f"props-diff={[(k, new[k], ref[k]) for k in set(new) & set(ref) if new[k] != ref[k]][:3]}")

    def has(et, fl, fk, tl, tk):
        return new.get((et, fl, K[fk], tl, K[tk]))

    check("phantom INHERITS Order->Account (a PRESENT type) is pruned",
          not has("INHERITS", "Class", "order", "Class", "account"))
    check("phantom REFERENCES Order->Account (an ABSENT type) is pruned",
          not has("REFERENCES", "Class", "order", "Class", "account"))
    check("phantom reversed CASCADES Account->Order (anchored at the DECLARING Order) is pruned",
          not has("CASCADES", "Class", "account", "Class", "order"))
    check("phantom RENDERS from the live routes module is pruned",
          not has("RENDERS", "Module", "routes", "Class", "order"))
    check("phantom :IMPORTS from the live pkg/a.py module is pruned",
          not has("IMPORTS", "Module", "py_a", "Class", "order"))
    check("an OUT-OF-SCOPE node's edge into a live node survives (live-scoped prune)",
          has("INHERITS", "Class", "out_scope", "Class", "order"))
    check(":CALLS is not touched by the typed/:IMPORTS writers",
          has("CALLS", "Class", "order", "Class", "line_item"))
    cas = has("CASCADES", "Class", "line_item", "Class", "order")
    check("CASCADES persists REVERSED (LineItem)-[:CASCADES]->(Order) with its attrs",
          cas and cas.get("dependent") == "destroy" and cas.get("confidence") == "INFERRED", cas)
    check("...and never in the declaring direction", not has("CASCADES", "Class", "order", "Class",
                                                              "line_item"))
    rt = has("ROUTES_TO", "Module", "routes", "Method", "idx")
    check("ROUTES_TO keeps its method/path attrs", rt and rt.get("method") == "GET"
          and rt.get("path") == "/orders", rt)
    check("a multi-label target key (TBase is :Module AND :Class) gets the edge under BOTH labels",
          has("INHERITS", "Class", "form1", "Module", "tbase")
          and has("INHERITS", "Class", "form1", "Class", "tbase"))
    dep = has("DEPENDS_ON", "Class", "cs_ctrl", "Class", "cs_ctx")
    check("C# DEPENDS_ON Class->Class persisted with its attrs", dep and dep.get("via") == "ctor", dep)
    imp = {(fk, tk): p["confidence"] for (et, fl, fk, tl, tk), p in new.items() if et == "IMPORTS"}
    check(":IMPORTS: graphify shape order->line_item EXTRACTED; ast shape a->b EXTRACTED; "
          "a->util x2 AMBIGUOUS; os / non-live / malformed dropped",
          imp == {(K["order_mod"], K["line_item_mod"]): "EXTRACTED", (K["py_a"], K["py_b"]): "EXTRACTED",
                  (K["py_a"], K["py_util1"]): "AMBIGUOUS", (K["py_a"], K["py_util2"]): "AMBIGUOUS"}, imp)

    # Re-sync idempotence: the same batch over the resulting graph changes nothing.
    snap = g_new.view()
    g_new.stmts.clear()
    _sync_sequence(RunOnly(g_new), nodes, imports, edges)
    check("re-running the same sync over its own result leaves the graph unchanged",
          g_new.view() == snap)
    # A relationship removed from source disappears on re-sync (anti-phantom, one layer up).
    g_new.stmts.clear()
    _sync_sequence(RunOnly(g_new), nodes, imports, [e for e in edges if e.edge_type != "CASCADES"])
    check("dropping the has_many dependent: from source removes the persisted CASCADES on re-sync",
          not any(k[0] == "CASCADES" for k in g_new.view()))


# =====================================================================================================
# B4 / B5 — transaction-agnostic + injection discipline
# =====================================================================================================
def gate_b4_b5_agnostic_and_guarded() -> None:
    print("\n-- B4/B5: transaction-agnostic writers; nothing unsafe is interpolated --")
    nodes, edges, imports = _fixture_nodes(), _fixture_edges(), _fixture_imports()
    g = _prior_graph(nodes)
    raised = None
    try:
        _sync_sequence(RunOnly(g), nodes, imports, edges)
    except AttributeError as exc:
        raised = exc
    check("prune_stale_edge_types / push_typed_edges / push_imports call nothing but .run", raised is None,
          raised)
    all_text = " ".join(c for _ctx, c, _p in g.stmts)
    check("an edge type outside ALLOWED_EDGE_TYPES is never interpolated", "EVIL" not in all_text)

    # A label that is not a plain identifier is never interpolated; the endpoint falls back to a
    # key-only MATCH for that row (the edge is still written).
    bad = "Bad` {key:'x'}) DETACH DELETE n //"
    bn = [_n("x.py::A:1", "Class", "A", "x.py"), _n("x.py::B:2", bad, "B", "x.py", 2)]
    g2 = MiniGraph()
    for n in bn:
        g2.add_node(n["label"], n["key"])
    ER.push_typed_edges(RunOnly(g2), [FE.TypedEdge("A", "x.py", "B", "REFERENCES", "LOCAL", {})],
                        ER.Resolver(bn), [n["key"] for n in bn])
    text2 = " ".join(c for _ctx, c, _p in g2.stmts)
    check("an unsafe node label never reaches the Cypher text", "DETACH" not in text2 and "Bad`" not in text2,
          text2[:300])
    check("...and the edge is still written (key-only MATCH for that endpoint)",
          any(k[0] == "REFERENCES" for k in g2.rels), g2.rels)
    # Empty inputs send nothing (a non-Python graphify graph passes typed_edges=[]).
    g3 = MiniGraph()
    ER.push_typed_edges(RunOnly(g3), [], ER.Resolver(nodes), [n["key"] for n in nodes])
    ER.push_imports(RunOnly(g3), [], ER.Resolver(nodes), [n["key"] for n in nodes], set())
    check("empty typed edges / imports -> no statements", g3.stmts == [], g3.stmts)


# =====================================================================================================
# B6 — the anchored API sync bounds its transactions with
# =====================================================================================================
def gate_b6_anchored() -> None:
    print("\n-- B6: anchored rewrite — per-group prune + re-create, same graph as one transaction --")
    fns = [getattr(ER, f, None) for f in ("edge_rows", "rows_by_anchor", "rewrite_anchored_edges")]
    check("edge_resolve exposes edge_rows / rows_by_anchor / rewrite_anchored_edges", all(fns))
    if not all(fns):
        return
    nodes, edges, imports = _fixture_nodes(), _fixture_edges(), _fixture_imports()
    live = []
    for n in nodes:
        if n["key"] not in live:
            live.append(n["key"])
    resolver = ER.Resolver(nodes)
    rows = ER.edge_rows(edges, imports, resolver, live, set(live))
    typed = ER.resolve_typed_edges(edges, resolver, live)
    check("edge_rows == the typed rows followed by the :IMPORTS rows",
          rows[:len(typed)] == typed and all(r[0] == "IMPORTS" for r in rows[len(typed):])
          and len(rows) - len(typed) == 4, rows)
    by = ER.rows_by_anchor(rows)
    check("rows_by_anchor keys every row by its DECLARING endpoint (row[1]), order kept",
          sum(len(v) for v in by.values()) == len(rows)
          and all(r[1] == k for k, v in by.items() for r in v), by)

    # One transaction vs three: same final graph.
    g1 = _prior_graph(nodes)
    ER.rewrite_anchored_edges(RunOnly(g1, ("tx", 1)), live, rows, resolver)
    g_ref = _prior_graph(nodes)
    _sync_sequence(RunOnly(g_ref), nodes, imports, edges)
    check("rewrite_anchored_edges(all anchors) == the sync call sequence's graph",
          g1.view() == g_ref.view(), sorted(set(g1.view()) ^ set(g_ref.view()))[:4])
    groups = [live[0::3], live[1::3], live[2::3]]
    g3 = _prior_graph(nodes)
    for i, grp in enumerate(groups, 1):
        ER.rewrite_anchored_edges(RunOnly(g3, ("tx", i)), grp, rows, resolver)
    check("split across 3 transactions -> the SAME graph as one transaction", g3.view() == g1.view(),
          sorted(set(g3.view()) ^ set(g1.view()))[:4])
    ok_prune = ok_rows = True
    pruned_types: dict = {}
    for (kind, tid), c, p in g3.stmts:
        grp = set(groups[tid - 1])
        if "DELETE r" in c:
            ok_prune &= set(p.get("keys") or []) <= grp
            pruned_types.setdefault(tid, set()).add(c.split("[r:", 1)[1].split("]", 1)[0])
        elif "MERGE (" in c:
            for r in p.get("rows") or [p]:
                ok_rows &= r["a"] in grp        # row.a is ALWAYS the declaring endpoint (the anchor)
    check("each transaction prunes ONLY its own anchors", ok_prune)
    check("each transaction re-creates ONLY rows declared by its own anchors", ok_rows)
    check("each transaction prunes the WHOLE vocabulary for its anchors (13 types incl. :IMPORTS)",
          all(pruned_types.get(i) == set(FE.ALLOWED_EDGE_TYPES) for i in (1, 2, 3)), pruned_types)
    g0 = MiniGraph()
    ER.rewrite_anchored_edges(RunOnly(g0), [], rows, resolver)
    check("no anchors -> no statements", g0.stmts == [], g0.stmts)


# =====================================================================================================
# B7 — end-to-end through sync.push_to_neo4j with the real recognizers
# =====================================================================================================
class _FakeTx:
    def __init__(self, g, tid):
        self.g, self.tid = g, tid

    def run(self, cypher, **params):
        self.g.run(cypher, params, ("tx", self.tid))

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


class _FakeSess:
    def __init__(self, g):
        self.g = g
        self.n = 0

    def run(self, cypher, **params):
        self.g.run(cypher, params, ("auto", 0))

    def begin_transaction(self, *a, **k):
        self.n += 1
        return _FakeTx(self.g, self.n)

    def execute_write(self, fn, *a, **k):
        self.n += 1
        return fn(_FakeTx(self.g, self.n), *a, **k)

    write_transaction = execute_write

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDrv:
    def __init__(self, g):
        self.g = g

    def session(self, *a, **k):
        return _FakeSess(self.g)

    def close(self):
        pass


RAILS = {
    "app/models/order.rb": "class Order < ApplicationRecord\n  belongs_to :account\n"
                           "  has_many :line_items, dependent: :destroy\nend\n",
    "app/models/line_item.rb": "class LineItem < ApplicationRecord\n  belongs_to :order\nend\n",
    "app/models/account.rb": "class Account < ApplicationRecord\n  has_many :orders\nend\n",
    "app/models/application_record.rb": "class ApplicationRecord < ActiveRecord::Base\nend\n",
    "config/routes.rb": "Rails.application.routes.draw do\n  resources :orders, only: [:index, :show]\nend\n",
}
GO_MAIN = ('package main\n\nimport (\n\t"net/http"\n\n\t"github.com/gin-gonic/gin"\n\t"shop/go/handlers"\n)\n\n'
           'func main() {\n\tr := gin.Default()\n\tr.GET("/customers", handlers.ListCustomers)\n'
           '\tr.GET("/users", listUsers)\n\tr.GET("/missing", http.NotFound)\n}\n')
JAVA = {
    "java/Order.java": ("package com.shop;\n\npublic class Order extends BaseEntity {\n"
                        "    public Order() {\n    }\n}\n"),
    "java/BaseEntity.java": "package com.shop;\n\npublic abstract class BaseEntity {\n}\n",
}
GO_ROUTER = ("package handlers\n\nimport \"net/http\"\n\ntype Router struct {\n\tNotFound http.Handler\n}\n\n"
             "func (r *Router) Handler(method string, h http.Handler) {\n}\n")


def gate_b7_end_to_end() -> None:
    print("\n-- B7: sync.push_to_neo4j persists the round-1 resolution fixes (real recognizers) --")
    spec = importlib.util.spec_from_file_location("dm_sync_w7_batch", BUILD / "sync.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)

    nodes = [
        # Rails, file-per-class: graphify emits a Module AND a Class per model file (T1)
        _n("app/models/order.rb::order:1", "Module", "order", "app/models/order.rb"),
        _n("app/models/line_item.rb::line_item:1", "Module", "line_item", "app/models/line_item.rb"),
        _n("app/models/account.rb::account:1", "Module", "account", "app/models/account.rb"),
        _n("app/models/application_record.rb::application_record:1", "Module", "application_record",
           "app/models/application_record.rb"),
        _n("config/routes.rb::routes:1", "Module", "routes", "config/routes.rb"),
        _n("app/controllers/orders_controller.rb::OrdersController:1", "Class", "OrdersController",
           "app/controllers/orders_controller.rb"),
        _n("app/controllers/orders_controller.rb::OrdersController.index:2", "Method",
           "OrdersController.index", "app/controllers/orders_controller.rb", 2),
        _n("app/controllers/orders_controller.rb::OrdersController.show:4", "Method",
           "OrdersController.show", "app/controllers/orders_controller.rb", 4),
        _n("app/controllers/orders_controller.rb::OrdersController.destroy:6", "Method",
           "OrdersController.destroy", "app/controllers/orders_controller.rb", 6),
        # Java, file-per-class: Module + Class + constructor Method `X.X` all answer to `Order` (T1),
        # and the Java `Order` shares its name with the Rails model (T2 decoy)
        _n("java/Order.java::Order:1", "Module", "Order", "java/Order.java"),
        _n("java/Order.java::Order:3", "Class", "Order", "java/Order.java", 3),
        _n("java/Order.java::Order.Order:5", "Method", "Order.Order", "java/Order.java", 5),
        _n("java/BaseEntity.java::BaseEntity:1", "Module", "BaseEntity", "java/BaseEntity.java"),
        _n("java/BaseEntity.java::BaseEntity:3", "Class", "BaseEntity", "java/BaseEntity.java", 3),
        # Go
        _n("go/main.go::main:1", "Module", "main", "go/main.go"),
        _n("go/main.go::main:10", "Function", "main", "go/main.go", 10),
        _n("go/users.go::listUsers:5", "Function", "listUsers", "go/users.go", 5),
        _n("go/handlers/customers.go::ListCustomers:8", "Function", "ListCustomers",
           "go/handlers/customers.go", 8),
        _n("go/admin/customers.go::ListCustomers:3", "Function", "ListCustomers", "go/admin/customers.go", 3),
        _n("go/handlers/router.go::Router:5", "Class", "Router", "go/handlers/router.go", 5),
        _n("go/handlers/router.go::Router.Handler:9", "Method", "Router.Handler", "go/handlers/router.go", 9),
    ]
    typed = []
    supp = []
    for rel, src in RAILS.items():
        sn, se = RR.recognize(src, rel)
        supp.extend(LR.validate_supplemental(sn, "ruby"))
        typed.extend(se)
    for rel, src in (("go/main.go", GO_MAIN), ("go/handlers/router.go", GO_ROUTER)):
        sn, se = GO.recognize(src, rel)
        supp.extend(LR.validate_supplemental(sn, "go"))
        typed.extend(se)
    for rel, src in JAVA.items():
        sn, se = JV.recognize(src, rel)
        supp.extend(LR.validate_supplemental(sn, "java"))
        typed.extend(se)
    all_nodes = LR.merge_supplemental(nodes, supp)
    g = MiniGraph()
    S.push_to_neo4j(_FakeDrv(g), all_nodes, [], [], typed)
    edge_unknown = [c for c in g.unknown if "[r:" in c]
    check("every edge statement sync sent is understood (typed writes go through edge_resolve)",
          not edge_unknown, edge_unknown[:2])
    lab = {}
    for n in all_nodes:
        lab[n["key"]] = n
    E = [(t, a[1], b[1], p.get("confidence")) for (t, a, b), p in g.rels.items() if t != "CALLS"]

    def nm(k):
        return lab.get(k, {}).get("name")

    def la(k):
        return lab.get(k, {}).get("label")

    typed_creates = _creates(g)
    check("typed edges reached the graph via label-qualified UNWIND batches",
          typed_creates and all(c.startswith("UNWIND $rows AS row ") and "{key:$a}" not in c
                                for _ctx, c, _p in typed_creates), [c for _x, c, _p in typed_creates][:2])
    type_level = {"INHERITS", "IMPLEMENTS", "HAS_MANY", "REFERENCES", "CASCADES"}
    tl = [(t, la(a), la(b)) for t, a, b, _c in E if t in type_level]
    check("T1: every persisted type-level edge joins :Class -> :Class (never a file Module / ctor)",
          tl and all(x[1] == "Class" and x[2] == "Class" for x in tl), tl)
    jinh = [(a, b, c) for t, a, b, c in E if t == "INHERITS" and a.startswith("java/")]
    check("T1: Java `Order extends BaseEntity` persists ONCE, Class -> Class, INFERRED",
          jinh == [("java/Order.java::Order:3", "java/BaseEntity.java::BaseEntity:3", "INFERRED")], jinh)
    fam = {k: ER.lang_family(v.get("source_file")) for k, v in lab.items()}
    cross = [(t, nm(a), nm(b)) for t, a, b, _c in E if fam.get(a) != fam.get(b)]
    check("T2: no persisted edge crosses a language family (Rails Order never binds java/Order)",
          not cross, cross)
    check("T2: the Rails CASCADES persists reversed LineItem -> Order, INFERRED (not AMBIGUOUS)",
          [(nm(a), nm(b), c) for t, a, b, c in E if t == "CASCADES"] == [("LineItem", "Order", "INFERRED")],
          [(nm(a), nm(b), c) for t, a, b, c in E if t == "CASCADES"])
    routed = {nm(b) for t, a, b, _c in E if t == "ROUTES_TO" and b.startswith("app/")}
    check("T3: only:[:index, :show] -> routes to index/show, NEVER the unrouted destroy",
          routed == {"OrdersController.index", "OrdersController.show"}, routed)
    go_routes = {(nm(a), b) for t, a, b, _c in E if t == "ROUTES_TO" and b.startswith("go/")}
    check("T4: handlers.ListCustomers -> the imported package's func (not go/admin's); sibling-file "
          "listUsers resolves; owned once by the main.go Module",
          go_routes == {("main", "go/handlers/customers.go::ListCustomers:8"),
                        ("main", "go/users.go::listUsers:5")}
          and all(la(a) == "Module" for t, a, b, _c in E if t == "ROUTES_TO" and b.startswith("go/")),
          go_routes)
    check("T5: no edge lands on the Router.Handler METHOD (stdlib http.Handler is never a repo type)",
          not [e for e in E if e[2] == "go/handlers/router.go::Router.Handler:9"], E)


# =====================================================================================================
# B8 — :IMPORTS resolution hygiene
# =====================================================================================================
def gate_b8_imports() -> None:
    print("\n-- B8: :IMPORTS stay inside the language family; a node-KEY target binds exactly --")
    nodes = [_n("web/app.ts::app:1", "Module", "app", "web/app.ts"),
             _n("web/index.ts::index:1", "Module", "index", "web/index.ts"),
             _n("tools/index.py::index:1", "Module", "index", "tools/index.py"),
             _n("web/lib/index.ts::index:1", "Module", "index", "web/lib/index.ts")]
    live = [n["key"] for n in nodes]
    g = MiniGraph()
    for n in nodes:
        g.add_node(n["label"], n["key"])
    ER.push_imports(RunOnly(g), [("web/app.ts::app:1", "index")], ER.Resolver(nodes), live, set(live))
    got = {(a[1], b[1]): p["confidence"] for (t, a, b), p in g.rels.items() if t == "IMPORTS"}
    check("a TypeScript import of `index` never binds the Python tools/index.py module",
          ("web/app.ts::app:1", "tools/index.py::index:1") not in got, got)
    check("...it binds the two TS `index` modules, AMBIGUOUS", got == {
        ("web/app.ts::app:1", "web/index.ts::index:1"): "AMBIGUOUS",
        ("web/app.ts::app:1", "web/lib/index.ts::index:1"): "AMBIGUOUS"}, got)
    g2 = MiniGraph()
    for n in nodes:
        g2.add_node(n["label"], n["key"])
    ER.push_imports(RunOnly(g2), [("web/app.ts::app:1", "web/lib/index.ts::index:1")], ER.Resolver(nodes),
                    live, set(live))
    got2 = {(a[1], b[1]): p["confidence"] for (t, a, b), p in g2.rels.items() if t == "IMPORTS"}
    check("an import whose target is already a node KEY binds exactly that node, EXTRACTED",
          got2 == {("web/app.ts::app:1", "web/lib/index.ts::index:1"): "EXTRACTED"}, got2)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — batched typed-edge / :IMPORTS write gates (round 2; no Neo4j)")
    print("=" * 72)
    for gate in (gate_b1_b2_batched_labelled, gate_b3_same_graph, gate_b4_b5_agnostic_and_guarded,
                 gate_b6_anchored, gate_b7_end_to_end, gate_b8_imports):
        try:
            gate()
        except Exception as exc:  # a crash on the pre-fix code is a RED result, not a harness error
            import traceback
            traceback.print_exc()
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
