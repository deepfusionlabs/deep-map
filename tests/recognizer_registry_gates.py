#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the per-language recognizer registry (Ph1 of the Rails field-report fix). No Neo4j.

Locks the seam that generalizes framework_edges.py into a language-dispatched registry WITHOUT
touching framework_edges.py (its own gates staying green unedited is the migration's other half):

  - PYTHON PARITY (the keystone): run(root) returns byte-equal TypedEdges to the legacy
    framework_edges.extract_framework_edges(root), and ZERO supplemental nodes — so the Python-only
    path through sync is unchanged (merge_supplemental even returns the ORIGINAL nodes object).
  - registry discipline / drift-locks: every entry's edge_types ⊆ framework_edges.ALLOWED_EDGE_TYPES
    (the persist-time injection whitelist stays a static literal), impact_edge_types ⊆ edge_types,
    and retrieval.DEFAULT_IMPACT_EDGE_TYPES == {CALLS} ∪ union(entry.impact_edge_types) — adding a
    traversable type in the registry FORCES the retrieval literal edit, in lockstep, or this gate
    is RED (the review's centralization guarantee with zero new imports into frozen retrieval.py).
  - the shared walker: .py keeps framework_edges' exact {__pycache__, .venv} skip; new-language
    entries get the broad ignore set (vendor/, node_modules/, tmp/, log/); per-file isolation.
  - the supplemental-node validator: nonconforming nodes (missing body_summary, out-of-schema
    label) are DROPPED — never a KeyError inside push_to_neo4j, never an index-invisible label.
  - merge_supplemental: dedupe by (name, source_file), base extractor wins, identity when empty.
  - sync._apply_recognizers: the call-site seam honors DEEPMAP_EXCLUDE_TESTS for supplemental
    nodes (they must not re-anchor excluded test files' typed edges) and degrades, never raises.
  - edge_resolve.prune_stale_edge_types: the anti-phantom fix — edge types ABSENT from a batch are
    pruned on live keys (incl. the empty-batch DEEPMAP_RECOGNIZERS=0 path), so a removed relation
    or a disabled recognizer can never leave stale edges corrupting deepmap_impact.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import framework_edges as FE            # noqa: E402
import language_recognizers as LR       # noqa: E402
import edge_resolve as ER               # noqa: E402
import sync as S                        # noqa: E402
import ruby_rails_edges as RR           # noqa: E402
import csharp_edges as CS               # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _load_standalone(name: str):
    """Load a build module the way its standalone consumers do (spec_from_file_location)."""
    spec = importlib.util.spec_from_file_location(f"dmrr_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_FLASK_SRC = '''from flask import Flask
app = Flask(__name__)


class Base:
    pass


class Svc(Base):
    pass


@app.get("/items")
def read_items():
    return []
'''


def _py_tree(root: Path) -> None:
    (root / "a.py").write_text(_FLASK_SRC, encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text("class Child(Svc):\n    pass\n", encoding="utf-8")
    pc = root / "__pycache__"
    pc.mkdir()
    (pc / "junk.py").write_text("class Ignored(Base):\n    pass\n", encoding="utf-8")


def _node(name, sf, label="Class", **over):
    n = {"key": f"{sf}::{name}:1", "label": label, "name": name, "source_file": sf,
         "start_line": 1, "documented": False, "body_summary": f"{name}: x"}
    n.update(over)
    return n


class _Sess:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def gate_python_parity() -> None:
    print("\n-- PYTHON PARITY: run(root) == legacy extract_framework_edges(root); 0 supp nodes --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _py_tree(root)
        legacy = FE.extract_framework_edges(root)
        supp, edges = LR.run(root)
        check("legacy path found edges (fixture sanity)", len(legacy) >= 2, str(len(legacy)))
        check("registry edges == legacy edges (byte-equal TypedEdges, same order)",
              edges == legacy, f"{len(edges)} vs {len(legacy)}")
        check("Python contributes ZERO supplemental nodes", supp == [], str(supp[:2]))
        check("__pycache__ skipped (no edge from junk.py)",
              not any("__pycache__" in e.src_file for e in edges))


def gate_env_gating() -> None:
    print("\n-- DEEPMAP_RECOGNIZERS gating: off / CSV / default-on --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _py_tree(root)
        saved = os.environ.get("DEEPMAP_RECOGNIZERS")
        try:
            os.environ["DEEPMAP_RECOGNIZERS"] = "0"
            check("=0 -> whole layer off ([], [])", LR.run(root) == ([], []))
            os.environ["DEEPMAP_RECOGNIZERS"] = "python"
            supp, edges = LR.run(root)
            check("CSV 'python' -> python runs", len(edges) >= 2)
            os.environ["DEEPMAP_RECOGNIZERS"] = "nosuchlang"
            check("CSV of unknown names -> nothing runs", LR.run(root) == ([], []))
            os.environ.pop("DEEPMAP_RECOGNIZERS", None)
            supp, edges = LR.run(root)
            check("unset -> default ON", len(edges) >= 2)
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_RECOGNIZERS", None)
            else:
                os.environ["DEEPMAP_RECOGNIZERS"] = saved


def gate_drift_locks() -> None:
    print("\n-- drift-locks: registry vocab ⊆ ALLOWED; impact lockstep with retrieval --")
    allowed = set(FE.ALLOWED_EDGE_TYPES)
    for entry in LR.REGISTRY:
        check(f"{entry.name}: edge_types ⊆ ALLOWED_EDGE_TYPES",
              set(entry.edge_types) <= allowed, str(set(entry.edge_types) - allowed))
        check(f"{entry.name}: impact_edge_types ⊆ edge_types",
              set(entry.impact_edge_types) <= set(entry.edge_types),
              str(set(entry.impact_edge_types) - set(entry.edge_types)))
        check(f"{entry.name}: impact_edge_types is an ORDERED tuple",
              isinstance(entry.impact_edge_types, tuple))
    R = _load_standalone("retrieval")
    default_impact = list(R.DEFAULT_IMPACT_EDGE_TYPES)
    check("retrieval default impact set ⊆ ALLOWED ∪ {CALLS}",
          set(default_impact) <= (allowed | {"CALLS"}),
          str(set(default_impact) - (allowed | {"CALLS"})))
    check("CALLS is always first in the impact set", default_impact[:1] == ["CALLS"], str(default_impact))
    union_impact = {"CALLS"}
    for entry in LR.REGISTRY:
        union_impact |= set(entry.impact_edge_types)
    check("LOCKSTEP: retrieval.DEFAULT_IMPACT_EDGE_TYPES == {CALLS} ∪ registry impact types "
          "(adding a traversable type in the registry FORCES the retrieval literal edit)",
          set(default_impact) == union_impact,
          f"retrieval={sorted(default_impact)} registry={sorted(union_impact)}")


def gate_walker() -> None:
    print("\n-- shared walker: broad ignore set for new languages; per-file isolation --")
    calls: list = []

    def rec_rb(source, rel):
        calls.append(rel)
        if "boom" in source:
            raise ValueError("recognizer crash on this file")
        return [], []

    fake = LR.RecognizerEntry(
        name="rbtest", extensions=frozenset({".rb"}), edge_types=frozenset({"INHERITS"}),
        impact_edge_types=(), recognize=rec_rb, ignore_parts=LR._BROAD_IGNORE)
    saved_registry = LR.REGISTRY
    try:
        LR.REGISTRY = (fake,)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app" / "models").mkdir(parents=True)
            (root / "app" / "models" / "a.rb").write_text("class A\nend\n", encoding="utf-8")
            (root / "app" / "models" / "z_boom.rb").write_text("boom\n", encoding="utf-8")
            for ig in ("vendor/bundle", "node_modules/x", "tmp", "log"):
                d = root / Path(ig)
                d.mkdir(parents=True)
                (d / "gem.rb").write_text("class Vendored\nend\n", encoding="utf-8")
            supp, edges = LR.run(root)
            check("vendored/tmp/log .rb files never reach the recognizer",
                  all("vendor" not in c and "node_modules" not in c
                      and not c.startswith(("tmp/", "log/")) for c in calls), str(calls))
            check("real model files DO reach it", "app/models/a.rb" in calls, str(calls))
            check("a recognizer crash on one file skips ONLY that file (isolation)",
                  "app/models/z_boom.rb" in calls and (supp, edges) == ([], []))
    finally:
        LR.REGISTRY = saved_registry


def gate_validator() -> None:
    print("\n-- supplemental-node validator: drop nonconforming, normalize, never raise --")
    good = _node("Order", "app/models/order.rb")
    bad_label = _node("X", "a.rb", label="Widget")
    no_summary = _node("Y", "a.rb")
    no_summary["body_summary"] = ""
    backslash = _node("Z", "app\\models\\z.rb")
    out = LR.validate_supplemental([good, bad_label, no_summary, backslash, "junk", None,
                                    {"name": "frag"}], "rbtest")
    names = [n["name"] for n in out]
    check("good node kept", "Order" in names, str(names))
    check("out-of-schema label dropped (index-invisible)", "X" not in names, str(names))
    check("empty body_summary dropped (embed/push contract)", "Y" not in names, str(names))
    check("junk entries dropped, no raise", "frag" not in names and len(out) == 2, str(names))
    z = next(n for n in out if n["name"] == "Z")
    check("backslash source_file normalized to posix", z["source_file"] == "app/models/z.rb",
          z["source_file"])
    check("documented defaulted to False", z.get("documented") is False)


def gate_merge() -> None:
    print("\n-- merge_supplemental: dedupe (name, source_file), base wins, identity when empty --")
    base = [_node("Order", "app/models/order.rb", key="graphify-opaque-1")]
    supp_dup = [_node("Order", "app/models/order.rb")]
    supp_new = [_node("LineItem", "app/models/line_item.rb")]
    check("empty supplemental -> the ORIGINAL list object (byte-identity)",
          LR.merge_supplemental(base, []) is base)
    merged = LR.merge_supplemental(base, supp_dup)
    check("duplicate (name, source_file) -> base extractor wins (no AMBIGUOUS self-infliction)",
          merged is base and len(merged) == 1 and merged[0]["key"] == "graphify-opaque-1")
    merged = LR.merge_supplemental(base, supp_dup + supp_new)
    check("new node appended; duplicate still dropped",
          len(merged) == 2 and merged[1]["name"] == "LineItem", str([n["name"] for n in merged]))
    check("junk degrades to base, never raises", LR.merge_supplemental(base, [None, "x"]) is base)


def gate_apply_recognizers_seam() -> None:
    print("\n-- sync._apply_recognizers: Python-only identity; DEEPMAP_EXCLUDE_TESTS filter --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _py_tree(root)
        base_nodes = [_node("Svc", "a.py", label="Class")]
        merged, typed = S._apply_recognizers(root, base_nodes)
        check("Python-only repo -> the SAME nodes object (byte-identity through sync)",
              merged is base_nodes)
        check("typed edges == the legacy framework edges",
              typed == FE.extract_framework_edges(root))

    # Supplemental test-file nodes are filtered under DEEPMAP_EXCLUDE_TESTS.
    def rec_with_test_nodes(source, rel):
        return [_node("OrderSpec", "spec/models/order_spec.rb"),
                _node("Order", "app/models/order.rb")], []

    fake = LR.RecognizerEntry(
        name="rbtest", extensions=frozenset({".rb"}), edge_types=frozenset({"INHERITS"}),
        impact_edge_types=(), recognize=rec_with_test_nodes, ignore_parts=LR._BROAD_IGNORE)
    saved_registry, saved_env = LR.REGISTRY, os.environ.get("DEEPMAP_EXCLUDE_TESTS")
    try:
        LR.REGISTRY = (fake,)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.rb").write_text("class X\nend\n", encoding="utf-8")
            os.environ["DEEPMAP_EXCLUDE_TESTS"] = "1"
            merged, _ = S._apply_recognizers(root, [])
            names = [n["name"] for n in merged]
            check("flag ON: spec/ supplemental node EXCLUDED (no re-anchoring of test edges)",
                  "OrderSpec" not in names, str(names))
            check("flag ON: non-test supplemental node kept", "Order" in names, str(names))
            os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
            merged, _ = S._apply_recognizers(root, [])
            check("flag OFF: both kept", len(merged) == 2, str(len(merged)))
    finally:
        LR.REGISTRY = saved_registry
        if saved_env is None:
            os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
        else:
            os.environ["DEEPMAP_EXCLUDE_TESTS"] = saved_env

    # Degrade, never raise: a run() that explodes yields (nodes, []).
    saved_run = LR.run
    try:
        LR.run = lambda root: (_ for _ in ()).throw(RuntimeError("boom"))
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            merged, typed = S._apply_recognizers(Path("."), [{"name": "n"}])
        check("recognizer-layer crash degrades to (nodes, []), sync survives",
              merged == [{"name": "n"}] and typed == [])
    finally:
        LR.run = saved_run


def gate_prune_stale_types() -> None:
    print("\n-- edge_resolve.prune_stale_edge_types: the anti-phantom fix --")
    allowed = set(FE.ALLOWED_EDGE_TYPES)
    log: list = []
    n = ER.prune_stale_edge_types(_Sess(log), ["k1", "k2"], present_types=set())
    pruned_types = {c.split(":")[1].split("]")[0] for c, _ in log}
    check("EMPTY batch (DEEPMAP_RECOGNIZERS=0 path) -> EVERY whitelisted type pruned on live keys",
          pruned_types == allowed and n == len(allowed), str(sorted(pruned_types)))
    check("prunes are live-key-scoped", all("WHERE a.key IN $keys" in c for c, _ in log)
          and all(set(p.get("keys", [])) == {"k1", "k2"} for _, p in log))

    log2: list = []
    ER.prune_stale_edge_types(_Sess(log2), ["k1"], present_types={"INHERITS", "IMPORTS"})
    pruned2 = {c.split(":")[1].split("]")[0] for c, _ in log2}
    check("present types are NOT double-pruned here (their pushes prune them)",
          pruned2 == allowed - {"INHERITS", "IMPORTS"}, str(sorted(pruned2)))

    log3: list = []
    check("no live keys -> no queries (fresh/subdir safety)",
          ER.prune_stale_edge_types(_Sess(log3), [], present_types=set()) == 0 and log3 == [])

    log4: list = []
    ER.prune_stale_edge_types(_Sess(log4), ["k"], present_types={"BOGUS; DETACH DELETE n //"})
    check("interpolated types come ONLY from ALLOWED_EDGE_TYPES (injection-proof)",
          all(any(f":{et}]" in c for et in allowed) for c, _ in log4)
          and all("BOGUS" not in c for c, _ in log4))


def gate_conf_hint_strict_resolution() -> None:
    print("\n-- SAME_FILE_STRICT honors conf_hint: LOCAL routes drop cross-file; NAME routes resolve globally --")
    # A Rails-shaped graph: a route-block node in routes.rb, its controller action in ANOTHER file
    # (the field state config/routes.rb always has — the handler is never same-file).
    block = _node("admin", "config/routes.rb", label="Method")
    action = _node("OrdersController.index", "app/controllers/orders_controller.rb", label="Method")
    resolver = ER.Resolver([block, action])
    live = [block["key"], action["key"]]

    def _merges(log):   # only the created edges — prune-before-recreate always logs a prune too
        return [p for c, p in log if "MERGE" in c]

    # conf_hint="NAME" (a cross-file bare route target) -> resolves via the global name index.
    name_edge = FE.TypedEdge("admin", "config/routes.rb", "OrdersController.index",
                             "ROUTES_TO", "NAME", {})
    log_n: list = []
    counts_n = ER.push_typed_edges(_Sess(log_n), [name_edge], resolver, live)
    check("NAME-hinted ROUTES_TO resolves to the cross-file controller action (created)",
          counts_n.get("ROUTES_TO", 0) == 1, str(counts_n))
    merges_n = _merges(log_n)
    check("the cross-file NAME route grades INFERRED (single global match, never EXTRACTED)",
          len(merges_n) == 1 and merges_n[0].get("c") == "INFERRED",
          str([p.get("c") for p in merges_n]))

    # conf_hint="LOCAL" (a literal decorator/attribute-anchored route) -> MUST stay same-file -> drop.
    local_edge = FE.TypedEdge("admin", "config/routes.rb", "OrdersController.index",
                              "ROUTES_TO", "LOCAL", {})
    log_l: list = []
    counts_l = ER.push_typed_edges(_Sess(log_l), [local_edge], resolver, live)
    check("LOCAL-hinted ROUTES_TO cross-file is DROPPED (decorator route target must be same-file)",
          counts_l.get("ROUTES_TO", 0) == 0 and _merges(log_l) == [], str(counts_l))

    # A NAME route whose target exists NOWHERE still drops — the refinement never fabricates.
    ghost = FE.TypedEdge("admin", "config/routes.rb", "NoSuchController.index",
                         "ROUTES_TO", "NAME", {})
    log_g: list = []
    counts_g = ER.push_typed_edges(_Sess(log_g), [ghost], resolver, live)
    check("NAME route with no resolvable target still DROPS (drop-on-unresolved, never fabricate)",
          counts_g.get("ROUTES_TO", 0) == 0 and _merges(log_g) == [], str(counts_g))

    # HOOKS stays LOCAL everywhere it is emitted today, so the strict-drop is unchanged for it.
    hook = FE.TypedEdge("OrdersController.index", "app/controllers/orders_controller.rb",
                        "somewhere_else", "HOOKS", "LOCAL", {})
    log_h: list = []
    counts_h = ER.push_typed_edges(_Sess(log_h), [hook], resolver, live)
    check("LOCAL HOOKS with a non-same-file target still DROPS (no behavior change for hooks)",
          counts_h.get("HOOKS", 0) == 0 and _merges(log_h) == [], str(counts_h))


def gate_shipped_framework_features() -> None:
    print("\n-- shipped framework-awareness feature manifest (the docs' 'supported' list, machine-checked) --")
    # This is the executable mirror of docs/known-limitations.md's Ruby/C# 'supported' claims: it fails
    # RED the moment a shipped recognizer feature is removed, so the reconciled prose can never silently
    # drift back to 'deferred'. (The per-nuance behavior stays locked in the *_edges_gates suites.)

    # Ruby: config/routes.rb -> :ROUTES_TO + scope/lambda :Method nodes (v0.21, routes mode).
    r_nodes, r_edges = RR.recognize(
        "Rails.application.routes.draw do\n"
        "  get '/orders', to: 'orders#index'\n"
        "  scope :v2 do\n    resources :widgets\n  end\n"
        "  constraints(lambda { |r| true }) do\n    get 'ping', to: 'health#ping'\n  end\nend\n",
        "config/routes.rb")
    check("Ruby: config/routes.rb emits :ROUTES_TO (shipped v0.21)",
          any(e.edge_type == "ROUTES_TO" for e in r_edges))
    r_methods = {n["name"] for n in r_nodes if n["label"] == "Method"}
    check("Ruby: routes-file scope + lambda blocks emit :Method nodes (shipped v0.21)",
          "v2" in r_methods and any("lambda" in nm for nm in r_methods), str(sorted(r_methods)))

    # Ruby: model-level `scope :name, -> {}` -> :Method node (v0.24, A1).
    m_nodes, _ = RR.recognize(
        "class Order < ApplicationRecord\n  scope :active, -> { where(active: true) }\nend\n",
        "app/models/order.rb")
    check("Ruby: model-level `scope` emits an ActiveRecord :Method node (v0.24, A1)",
          any(n["label"] == "Method" and n["name"] == "Order.active" for n in m_nodes),
          str([n["name"] for n in m_nodes]))

    # C#: minimal-API routes -> :ROUTES_TO (v0.21).
    _, c_edges = CS.recognize('app.MapGet("/orders", GetOrders);\n', "Program.cs")
    check("C#: minimal-API app.MapGet -> :ROUTES_TO (shipped v0.21)",
          any(e.edge_type == "ROUTES_TO" and e.dst_name == "GetOrders" for e in c_edges))

    # C#: multi-statement Entity<X>(e => { ...; ...; }) block -> every statement's cascade (v0.21).
    lam = ("public class ShopContext : DbContext\n{\n"
           "  protected override void OnModelCreating(ModelBuilder modelBuilder)\n  {\n"
           "    modelBuilder.Entity<Order>(entity =>\n    {\n"
           "      entity.HasMany(o => o.LineItems).WithOne(li => li.Order).OnDelete(DeleteBehavior.Cascade);\n"
           "      entity.HasOne(o => o.Customer).WithMany(c => c.Orders).OnDelete(DeleteBehavior.Cascade);\n"
           "    });\n  }\n}\n")
    _, l_edges = CS.recognize(lam, "Data/ShopContext.cs")
    l_cas = {(e.src_name, e.dst_name) for e in l_edges if e.edge_type == "CASCADES"}
    check("C#: multi-statement Entity<X>(e => {...}) emits BOTH statements' cascades (shipped v0.21)",
          {("Order", "LineItem"), ("Customer", "Order")} <= l_cas, str(sorted(l_cas)))

    # C#: minimal-API route-GROUP prefix composition (v0.24, A2).
    _, g_edges = CS.recognize('var api = app.MapGroup("/api");\napi.MapGet("/orders", G);\n', "Program.cs")
    check("C#: MapGroup var-form composes the route prefix -> /api/orders (v0.24, A2)",
          any(e.attrs.get("path") == "/api/orders"
              for e in g_edges if e.edge_type == "ROUTES_TO"),
          str([(e.dst_name, e.attrs.get("path")) for e in g_edges if e.edge_type == "ROUTES_TO"]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — language-recognizer registry gates (Ph1, no Neo4j)")
    print("=" * 64)
    gate_python_parity()
    gate_env_gating()
    gate_drift_locks()
    gate_walker()
    gate_validator()
    gate_merge()
    gate_apply_recognizers_seam()
    gate_prune_stale_types()
    gate_conf_hint_strict_resolution()
    gate_shipped_framework_features()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
