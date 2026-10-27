#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the Ruby/Rails recognizer (Ph2 of the Rails field-report fix). No Neo4j, no Ruby runtime.

The field failure this locks out: `deepmap_impact` could not trace `dependent: :destroy` cascades
(declarative DSL — not a call), and the model layer was invisible. These gates lock:

  - recognizer emit correctness over REAL Rails model source: INHERITS / HAS_MANY / REFERENCES /
    CASCADES / HOOKS, `class_name:` (incl. `::`-namespaced) overriding inflection, cascade
    strategies (destroy/delete_all/nullify yes; restrict_with_error NO), callback condition symbols
    (`if: :active?`) NOT emitted as hooks, comments skipped, per-construct resilience;
  - the inflector: conventional plurals, the irregulars table, and the failure mode (a wrong guess
    -> unresolvable name -> DROPPED edge, never a fabricated one);
  - ensured :Class nodes byte-match graphify's name shapes for BOTH declaration forms (verified
    against the CAPTURED fixture tests/fixtures/graphify_multilang/ruby_rails.json — real
    `graphify update --no-cluster` output over this exact sample), so merge_supplemental dedupes
    them away when graphify parsed the file and they fill the hole when it didn't;
  - the FULL resolution path (fake session): CASCADES persists REVERSED — (owned)-[:CASCADES]->
    (owner) — so reverse-reachability from the owner (the field user's exact question,
    impact("Order")) surfaces the cascade targets; reversed prune anchors at the declaring side;
  - `::`-resolution: a bare `Override` resolves to graphify's compact-form `Admin::Override` node;
  - end-to-end registry: LR.run over a Rails tree emits nodes+edges; DEEPMAP_RECOGNIZERS=0 replays
    the pre-Ph2 field state (edges gone — and verdict coverage flags it, locked in
    verdict_coverage_gates).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import ruby_rails_edges as RR       # noqa: E402
import language_recognizers as LR   # noqa: E402
import edge_resolve as ER           # noqa: E402
import graphify_extract as GX       # noqa: E402
import framework_edges as FE        # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


ORDER_RB = """class Order < ApplicationRecord
  # the model layer the field report called invisible
  has_many :line_items, dependent: :destroy
  has_many :payments, dependent: :nullify
  has_many :audit_entries, dependent: :restrict_with_error
  belongs_to :customer
  has_one :invoice, class_name: 'Billing::Invoice', dependent: :destroy
  has_and_belongs_to_many :categories
  has_many :people
  validates :total, presence: true
  scope :recent, -> { where('created_at > ?', 1.week.ago) }
  before_save :normalize_totals, if: :active?
  after_destroy :audit_removal
  around_update :with_lock_guard
end
"""

STI_RB = """class PriorityOrder < Order
  before_create :flag_priority
end
"""

NESTED_RB = """module Admin
  class AuditLog < ApplicationRecord
    belongs_to :order
  end
end

class Admin::Override < ApplicationRecord
  belongs_to :order
end
"""

ROUTES_RB = """Rails.application.routes.draw do
  root 'home#index'
  get '/orders', to: 'orders#index'
  get '/orders/:id' => 'orders#show'
  resources :line_items
  namespace :admin do
    resources :reports
    get 'dashboard', to: 'admin/dashboard#show'
  end
  scope :v2 do
    resources :widgets
  end
  constraints(lambda { |r| r.subdomain == 'api' }) do
    get 'ping', to: 'health#ping'
  end
end
"""


# P1-4: custom member/collection routes inside a resources block (dominant on an Active-Scaffold app).
ROUTES_MC_RB = """Rails.application.routes.draw do
  resources :orders do
    member do
      get :publish
      post :cancel
    end
    collection do
      get :search
    end
    get :archive, on: :member
    put :bulk_update, on: :collection
  end
  resources :invoices
end
"""


def _edges(src, rel="app/models/order.rb"):
    return RR.recognize(src, rel)[1]


def _by_type(edges, t):
    return [e for e in edges if e.edge_type == t]


def gate_recognizer_emit() -> None:
    print("\n-- recognizer emit: associations / cascades / callbacks / inheritance --")
    nodes, edges = RR.recognize(ORDER_RB, "app/models/order.rb")

    inh = _by_type(edges, "INHERITS")
    check("INHERITS Order -> ApplicationRecord", len(inh) == 1
          and inh[0].src_name == "Order" and inh[0].dst_name == "ApplicationRecord", str(inh))

    hm = _by_type(edges, "HAS_MANY")
    hm_targets = {e.dst_name for e in hm}
    check("has_many/has_one/habtm -> HAS_MANY with classified targets",
          {"LineItem", "Payment", "AuditEntry", "Invoice", "Category", "Person"} <= hm_targets,
          str(hm_targets))
    check("irregular plural: :people -> Person", "Person" in hm_targets, str(hm_targets))
    check("class_name: 'Billing::Invoice' overrides inflection AND demodulizes",
          any(e.dst_name == "Invoice" and e.attrs.get("class_name") == "Billing::Invoice"
              for e in hm), str([e.attrs for e in hm]))

    refs = _by_type(edges, "REFERENCES")
    check("belongs_to -> REFERENCES Customer", len(refs) == 1 and refs[0].dst_name == "Customer",
          str(refs))

    cas = _by_type(edges, "CASCADES")
    cas_map = {e.dst_name: e.attrs.get("strategy") for e in cas}
    check("dependent: :destroy/:nullify -> CASCADES with strategy attrs",
          cas_map.get("LineItem") == "destroy" and cas_map.get("Payment") == "nullify"
          and cas_map.get("Invoice") == "destroy", str(cas_map))
    check("restrict_with_error is NOT a cascade (prevents deletion)",
          "AuditEntry" not in cas_map, str(cas_map))
    check("CASCADES emitted DECLARATION-anchored (src = the declaring model)",
          all(e.src_name == "Order" and e.src_file == "app/models/order.rb" for e in cas))

    hooks = _by_type(edges, "HOOKS")
    hook_map = {e.dst_name: e.attrs.get("event") for e in hooks}
    check("callbacks -> HOOKS (before_save/after_destroy/around_update)",
          hook_map.get("normalize_totals") == "before_save"
          and hook_map.get("audit_removal") == "after_destroy"
          and hook_map.get("with_lock_guard") == "around_update", str(hook_map))
    check("condition symbols (if: :active?) are NOT hook targets", "active" not in hook_map
          and "active?" not in hook_map, str(hook_map))
    check("hooks are LOCAL (SAME_FILE_STRICT resolve-or-drop at push)",
          all(e.conf_hint == "LOCAL" for e in hooks))

    # A1: model-level `scope :name, -> {}` -> a searchable :Method node (an ActiveRecord class method
    # graphify's def-parser never node-ifies, since a scope is a macro call, not a `def`).
    scope_methods = [n for n in nodes if n["label"] == "Method"]
    check("model `scope :recent, -> {}` -> a :Method node in the Class.scope shape (line 11)",
          any(n["name"] == "Order.recent" and n["start_line"] == 11
              and n["source_file"] == "app/models/order.rb" for n in scope_methods),
          str([(n["name"], n["start_line"]) for n in scope_methods]))
    check("scope :Method node carries the full push contract + a Method label",
          bool(scope_methods) and all(
              n["label"] == "Method" and n["documented"] is False
              and all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
              for n in scope_methods))

    sti = _edges(STI_RB, "app/models/priority_order.rb")
    check("STI: PriorityOrder INHERITS Order (NAME — cross-file)",
          any(e.edge_type == "INHERITS" and e.dst_name == "Order" and e.conf_hint == "NAME"
              for e in sti), str(sti))

    # Comments / junk resilience.
    junk = "# has_many :ghosts, dependent: :destroy\nclass X < ApplicationRecord\nend\n\x00garbage"
    n2, e2 = RR.recognize(junk, "a.rb")
    check("commented DSL ignored; malformed bytes never raise",
          not _by_type(e2, "HAS_MANY") and len(n2) == 1)


def gate_inflector() -> None:
    print("\n-- inflector: conventions + irregulars; miss == dropped edge (never fabricated) --")
    cases = {"line_items": "LineItem", "payments": "Payment", "categories": "Category",
             "boxes": "Box", "batches": "Batch", "addresses": "Address", "people": "Person",
             "children": "Child", "statuses": "Status", "analyses": "Analysis", "quizzes": "Quiz",
             "houses": "House", "cases": "Case", "order": "Order",
             # F6: `-us` singular words + compound `*_status` (a common Rails association name)
             # must NOT be mangled to `*Statu`; last-segment singularization + a -us guard.
             "status": "Status", "activity_status": "ActivityStatus", "audit_status": "AuditStatus",
             "campus": "Campus", "bus": "Bus", "genus": "Genus",
             "order_statuses": "OrderStatus", "activity_statuses": "ActivityStatus",
             "line_item_prices": "LineItemPrice",
             # uncountable -s words: identity, never strip.
             "news": "News", "series": "Series", "species": "Species"}
    for sym, want in cases.items():
        check(f"classify {sym} -> {want}", RR.classify_name(sym) == want, RR.classify_name(sym))
    check("demodulize Billing::Invoice -> Invoice", RR.demodulize("Billing::Invoice") == "Invoice")
    # The honesty contract: an unresolvable (mis-inflected) target DROPS at push.
    nodes = [{"key": "k1", "label": "Class", "name": "Order", "source_file": "o.rb"}]
    resolver = ER.Resolver(nodes)
    edge = FE.TypedEdge("Order", "o.rb", "NoSuchModel", "HAS_MANY", "NAME", {})
    log: list = []

    class _S:
        def run(self, c, **p):
            for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one entry per row
                log.append((c, row))
    counts = ER.push_typed_edges(_S(), [edge], resolver, ["k1"])
    check("unresolvable target -> edge DROPPED (0 created), never fabricated",
          counts.get("HAS_MANY", 0) == 0, str(counts))


def gate_ensured_nodes_match_graphify() -> None:
    print("\n-- ensured nodes byte-match graphify's name shapes (captured fixture) --")
    fx = json.loads((REPO / "tests" / "fixtures" / "graphify_multilang" / "ruby_rails.json")
                    .read_text(encoding="utf-8"))
    mapped_nodes, _, _ = GX.map_graph(fx)
    g_names = {(n["name"], n["source_file"]) for n in mapped_nodes if n["label"] == "Class"}
    check("FIXTURE ground truth: graphify DOES node-ify a def-less DSL-only model",
          ("Order", "app/models/order.rb") in g_names, str(g_names))
    check("fixture: nested-module form -> BARE name", ("AuditLog", "app/models/admin.rb") in g_names)
    check("fixture: compact form -> '::' name", ("Admin::Override", "app/models/admin.rb") in g_names)

    # The recognizer's ensured nodes must reproduce those EXACT shapes so dedupe works.
    n_nested, _ = RR.recognize(NESTED_RB, "app/models/admin.rb")
    names = {n["name"] for n in n_nested}
    check("recognizer reproduces both shapes (AuditLog + Admin::Override)",
          names == {"AuditLog", "Admin::Override"}, str(names))
    check("ensured nodes carry the full push contract",
          all(all(k in n and n[k] not in (None, "") for k in
                  ("key", "label", "name", "source_file", "start_line", "body_summary"))
              and n["label"] == "Class" and n["documented"] is False for n in n_nested))

    # Dedupe: graphify already has these nodes -> the ensured copies are dropped (base wins).
    merged = LR.merge_supplemental(mapped_nodes, LR.validate_supplemental(n_nested, "ruby"))
    check("graphify parsed the file -> ensured nodes dedupe away (base wins)",
          merged is mapped_nodes)
    # graphify missed the repo (the ast-fallback field state) -> ensured nodes fill the hole.
    merged2 = LR.merge_supplemental([], LR.validate_supplemental(n_nested, "ruby"))
    check("graphify missed the file -> ensured nodes fill the model layer", len(merged2) == 2)

    n_order, _ = RR.recognize(ORDER_RB, "app/models/order.rb")
    summ = next(n["body_summary"] for n in n_order if n["name"] == "Order")
    check("DSL-derived deterministic summary (model kind + associations + cascade)",
          "ActiveRecord model" in summ and "line_items" in summ and "destroy" in summ, summ)


class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def gate_reversed_cascades_persistence() -> None:
    print("\n-- CASCADES persists REVERSED: impact(owner) surfaces the cascade targets --")
    nodes = [
        {"key": "o.rb::Order:1", "label": "Class", "name": "Order", "source_file": "o.rb"},
        {"key": "l.rb::LineItem:1", "label": "Class", "name": "LineItem", "source_file": "l.rb"},
    ]
    resolver = ER.Resolver(nodes)
    live = [n["key"] for n in nodes]
    edge = FE.TypedEdge("Order", "o.rb", "LineItem", "CASCADES", "NAME", {"strategy": "destroy"})
    s = _Sess()
    counts = ER.push_typed_edges(s, [edge], resolver, live)
    check("CASCADES edge created", counts.get("CASCADES") == 1, str(counts))
    merges = [(c, p) for c, p in s.log if "MERGE" in c and "CASCADES" in c]
    check("persisted (b)-[:CASCADES]->(a): OWNED -> OWNER (a=declaring Order, b=LineItem)",
          len(merges) == 1 and "MERGE (b)-[r:CASCADES]->(a)" in merges[0][0]
          and merges[0][1].get("a") == "o.rb::Order:1" and merges[0][1].get("b") == "l.rb::LineItem:1",
          str(merges))
    check("strategy attr persisted", merges[0][1].get("attrs", {}).get("strategy") == "destroy")

    prunes = [c for c, _ in s.log if "DELETE r" in c and "CASCADES" in c]
    check("reversed prune anchors at the DECLARING side: MATCH ()-[r:CASCADES]->(a)",
          len(prunes) == 1 and "()-[r:CASCADES]->(a)" in prunes[0], str(prunes))

    # Non-reversed types keep the byte-identical legacy direction.
    s2 = _Sess()
    ER.push_typed_edges(s2, [FE.TypedEdge("Sub", "o.rb", "Order", "INHERITS", "NAME", {})],
                        ER.Resolver(nodes + [{"key": "s", "label": "Class", "name": "Sub",
                                              "source_file": "o.rb"}]), live + ["s"])
    m2 = [c for c, _ in s2.log if "MERGE" in c and "INHERITS" in c]
    check("non-reversed types unchanged: MERGE (a)-[r:INHERITS]->(b)",
          m2 and "MERGE (a)-[r:INHERITS]->(b)" in m2[0], str(m2))

    # Re-sync prune contract: a removed `dependent:` disappears (empty batch, stale-type prune).
    s3 = _Sess()
    ER.prune_stale_edge_types(s3, live, present_types=set())
    cas_prunes = [c for c, _ in s3.log if "CASCADES" in c]
    check("stale-type prune for CASCADES is also declaring-side-anchored",
          len(cas_prunes) == 1 and "()-[r:CASCADES]->(a)" in cas_prunes[0], str(cas_prunes))


def gate_namespaced_resolution() -> None:
    print("\n-- '::' resolution: bare names reach compact-form nodes --")
    nodes = [
        {"key": "a.rb::Admin::Override:7", "label": "Class", "name": "Admin::Override",
         "source_file": "a.rb"},
        {"key": "o.rb::Order:1", "label": "Class", "name": "Order", "source_file": "o.rb"},
    ]
    resolver = ER.Resolver(nodes)
    check("targets('Override') resolves to the Admin::Override node",
          resolver.targets("Override") == ["a.rb::Admin::Override:7"],
          str(resolver.targets("Override")))
    s = _Sess()
    counts = ER.push_typed_edges(
        s, [FE.TypedEdge("Order", "o.rb", "Override", "HAS_MANY", "NAME", {})],
        resolver, [n["key"] for n in nodes])
    check("HAS_MANY Order -> Override lands on the namespaced node (INFERRED)",
          counts.get("HAS_MANY") == 1
          and any(p.get("c") == "INFERRED" for c, p in s.log if "HAS_MANY" in c), str(s.log))


def gate_routes() -> None:
    print("\n-- config/routes.rb: :ROUTES_TO edges + scope/lambda :Method nodes (cross-file resolve) --")
    nodes, edges = RR.recognize(ROUTES_RB, "config/routes.rb")

    # (1) scope / lambda / table :Method nodes ------------------------------------------------
    node_names = {n["name"] for n in nodes}
    check("scope/namespace/constraints blocks -> :Method nodes",
          {"admin", "v2", "constraints"} <= node_names, str(node_names))
    check("the top-level route table -> a :Method node (file stem)", "routes" in node_names, str(node_names))
    check("a lambda/proc route block -> a searchable :Method node",
          any("lambda" in n["name"] for n in nodes), str(node_names))
    check("every routes node is a valid :Method with the full push contract",
          all(n["label"] == "Method" and n["documented"] is False
              and all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
              for n in nodes))

    # (2) ROUTES_TO edges: verbs, REST, controller derivation, anchoring, conf_hint ------------
    ro = _by_type(edges, "ROUTES_TO")
    check("all route edges are ROUTES_TO, conf_hint=NAME (cross-file resolve, drop-if-unresolved)",
          bool(ro) and all(e.conf_hint == "NAME" for e in ro), str(len(ro)))
    targets = {e.dst_name for e in ro}
    check("verb route 'orders#index' -> OrdersController.index", "OrdersController.index" in targets,
          str(sorted(targets))[:200])
    check("root 'home#index' -> HomeController.index", "HomeController.index" in targets)
    check("resources :line_items -> LineItemsController.{7 REST actions} (PLURAL controller, no singularize)",
          {f"LineItemsController.{a}" for a in RR._REST_ACTIONS} <= targets, str(sorted(targets))[:200])
    check("namespaced 'admin/dashboard#show' uses the DEMODULIZED controller -> DashboardController.show",
          "DashboardController.show" in targets)
    admin_targets = {e.dst_name for e in ro if e.src_name == "admin"}
    check("routes inside `namespace :admin` are anchored on the 'admin' scope :Method node",
          any(t.startswith("ReportsController.") for t in admin_targets), str(admin_targets)[:200])
    top_targets = {e.dst_name for e in ro if e.src_name == "routes"}
    check("top-level routes are anchored on the route-table node",
          "OrdersController.index" in top_targets, str(top_targets)[:120])

    # (3) cross-file resolution through the FULL push path (the field value) -------------------
    ctrl_nodes = [
        {"key": "app/controllers/orders_controller.rb::OrdersController.index:2",
         "label": "Method", "name": "OrdersController.index",
         "source_file": "app/controllers/orders_controller.rb"},
        {"key": "app/controllers/admin/reports_controller.rb::Admin::ReportsController.index:2",
         "label": "Method", "name": "Admin::ReportsController.index",
         "source_file": "app/controllers/admin/reports_controller.rb"},
    ]
    resolver = ER.Resolver(nodes + ctrl_nodes)
    live = [n["key"] for n in nodes] + [n["key"] for n in ctrl_nodes]
    s = _Sess()
    counts = ER.push_typed_edges(s, ro, resolver, live)
    merges = [(c, p) for c, p in s.log if "MERGE" in c and "ROUTES_TO" in c]
    made_dst_keys = {p["b"] for _, p in merges}
    check("ROUTES_TO resolves cross-file to OrdersController.index (created via the conf_hint refinement)",
          counts.get("ROUTES_TO", 0) >= 1 and ctrl_nodes[0]["key"] in made_dst_keys, str(counts))
    check("namespaced route resolves to Admin::ReportsController.index via the ::-segment index",
          ctrl_nodes[1]["key"] in made_dst_keys, str(len(made_dst_keys)))
    check("route actions with no matching controller node are DROPPED (drop-on-unresolved, never fabricate)",
          len(made_dst_keys) == 2 and len(made_dst_keys) < len(ro),
          f"{len(made_dst_keys)} resolved of {len(ro)}")
    check("resolved cross-file routes grade INFERRED (single global match)",
          all(p.get("c") == "INFERRED" for _, p in merges), str([p.get("c") for _, p in merges]))

    # (4) determinism + non-routes files still run the model scan ------------------------------
    check("recognize is deterministic on routes.rb",
          RR.recognize(ROUTES_RB, "config/routes.rb") == (nodes, edges))
    _, m_edges = RR.recognize(ORDER_RB, "app/models/order.rb")
    check("a normal model file still runs the model scan (no ROUTES_TO, CASCADES intact)",
          not _by_type(m_edges, "ROUTES_TO") and any(e.edge_type == "CASCADES" for e in m_edges))


def gate_routes_member_collection() -> None:
    print("\n-- P1-4: custom member/collection routes -> ROUTES_TO on the resource's controller --")
    nodes, edges = RR.recognize(ROUTES_MC_RB, "config/routes.rb")
    ro = [e for e in edges if e.edge_type == "ROUTES_TO"]
    targets = {e.dst_name for e in ro}
    # block form: member do; get :publish -> OrdersController.publish
    check("member `get :publish` -> OrdersController.publish", "OrdersController.publish" in targets,
          str(sorted(targets))[:200])
    check("member `post :cancel` -> OrdersController.cancel", "OrdersController.cancel" in targets)
    check("collection `get :search` -> OrdersController.search", "OrdersController.search" in targets)
    # inline on: form
    check("inline `get :archive, on: :member` -> OrdersController.archive",
          "OrdersController.archive" in targets)
    check("inline `put :bulk_update, on: :collection` -> OrdersController.bulk_update",
          "OrdersController.bulk_update" in targets)
    # the standard 7 REST actions still emit for the resource
    check("the 7 REST actions still emit alongside the custom ones",
          {f"OrdersController.{a}" for a in RR._REST_ACTIONS} <= targets, str(sorted(targets))[:200])
    # every custom-action edge is NAME-hinted (cross-file resolve, drop-if-unresolved) + records via
    mc = [e for e in ro if (e.attrs or {}).get("via") == "member_collection"]
    check("custom member/collection edges are conf_hint=NAME with a member_collection marker",
          len(mc) == 5 and all(e.conf_hint == "NAME" for e in mc), str(len(mc)))
    # a bare `get :symbol` with NO resource context / no member-collection / no on: is NOT fabricated
    stray = RR.recognize("Rails.application.routes.draw do\n  get :orphan\nend\n", "config/routes.rb")[1]
    check("a stray `get :orphan` (no resource, no member/collection) fabricates NO edge",
          not any(e.edge_type == "ROUTES_TO" for e in stray), str(stray))
    check("recognize is deterministic on the member/collection routes",
          RR.recognize(ROUTES_MC_RB, "config/routes.rb") == (nodes, edges))


def gate_views_renders() -> None:
    print("\n-- P1-3: .erb views -> :Module view node + RENDERS edges (view -> partial) --")
    # (1) logical-name derivation
    check("index.html.erb -> 'orders/index'",
          RR._view_logical_name("app/views/orders/index.html.erb") == "orders/index")
    check("_form.html.erb -> 'shared/form' (leading underscore stripped)",
          RR._view_logical_name("app/views/shared/_form.html.erb") == "shared/form")
    # (2) a view rendering partials -> RENDERS to the partials' logical names
    src = ("<h1>Orders</h1>\n"
           "<%= render 'shared/form' %>\n"          # views-relative -> shared/form
           "<%= render partial: 'row', collection: @orders %>\n"   # same-dir -> orders/row
           "<%= render template: 'orders/summary' %>\n"
           "<%= render text: 'not a partial' %>\n")  # text: is NOT a view render -> no edge
    nodes, edges = RR.recognize(src, "app/views/orders/index.html.erb")
    check("exactly one :Module view node emitted for the .erb",
          len(nodes) == 1 and nodes[0]["label"] == "Module" and nodes[0]["name"] == "orders/index",
          str(nodes))
    ro = [e for e in edges if e.edge_type == "RENDERS"]
    dsts = {e.dst_name for e in ro}
    check("render 'shared/form' -> RENDERS orders/index -> shared/form",
          "shared/form" in dsts and all(e.src_name == "orders/index" for e in ro), str(dsts))
    check("render partial: 'row' resolves same-directory -> orders/row", "orders/row" in dsts, str(dsts))
    check("render template: 'orders/summary' -> orders/summary", "orders/summary" in dsts, str(dsts))
    check("render text: '...' is NOT treated as a view render (no fabricated edge)",
          "not a partial" not in dsts and not any("not" in d for d in dsts), str(dsts))
    check("all RENDERS edges are conf_hint=NAME (cross-file resolve, drop-if-unresolved)",
          bool(ro) and all(e.conf_hint == "NAME" for e in ro))
    check("RENDERS is in the persist whitelist", "RENDERS" in FE.ALLOWED_EDGE_TYPES)
    # (3) a view that renders nothing -> just its own node, no edges; deterministic
    n2, e2 = RR.recognize("<p>static</p>\n", "app/views/home/show.html.erb")
    check("a render-free view -> one node, no RENDERS edges", len(n2) == 1 and not e2)
    check("recognize is deterministic on the .erb view",
          RR.recognize(src, "app/views/orders/index.html.erb") == (nodes, edges))


def gate_registry_end_to_end() -> None:
    print("\n-- registry end-to-end: LR.run over a Rails tree; =0 replays the field state --")
    import os
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "models").mkdir(parents=True)
        (root / "app" / "models" / "order.rb").write_text(ORDER_RB, encoding="utf-8")
        (root / "app" / "models" / "admin.rb").write_text(NESTED_RB, encoding="utf-8")
        (root / "vendor" / "bundle").mkdir(parents=True)
        (root / "vendor" / "bundle" / "gem.rb").write_text(ORDER_RB, encoding="utf-8")
        supp, edges = LR.run(root)
        check("ruby entry runs: ensured nodes + typed edges emitted",
              len(supp) >= 3 and any(e.edge_type == "CASCADES" for e in edges),
              f"supp={len(supp)} edges={len(edges)}")
        check("vendored gems never contribute", all("vendor" not in n["source_file"] for n in supp)
              and all("vendor" not in e.src_file for e in edges))
        saved = os.environ.get("DEEPMAP_RECOGNIZERS")
        try:
            os.environ["DEEPMAP_RECOGNIZERS"] = "0"
            check("DEEPMAP_RECOGNIZERS=0 replays the pre-Ph2 field state (no nodes, no edges)",
                  LR.run(root) == ([], []))
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_RECOGNIZERS", None)
            else:
                os.environ["DEEPMAP_RECOGNIZERS"] = saved


# F5: legacy `:key => value` hash-rocket DSL (pervasive on pre-2013 Rails). The recognizer must read
# both hash syntaxes; drop-not-fabricate is preserved (a missed edge, never a wrong one).
HASHROCKET_RB = """class LegacyOrder < ActiveRecord::Base
  belongs_to :account, :class_name => 'Billing::Account'
  has_many :line_items, :dependent => :destroy
  has_one :invoice, :class_name => 'Invoice', :dependent => :delete_all
  before_save :normalize, :if => :active?
end
"""

# F2a: `def` methods -> supplemental Class.method nodes so a graphify-missed / ast-fallback / truncated
# Ruby file keeps its method layer searchable (graphify names them Class.method on the happy path, so
# these dedupe away base-wins). Uses the SAME Class.method shape as the existing scope node.
DEFS_RB = """class TorahLesson < ApplicationRecord
  has_many :items, dependent: :destroy
  scope :recent, -> { where(active: true) }
  before_save :normalize

  def total_price
    items.sum(&:price)
  end

  def self.bulk_import(rows)
    rows.each { |r| create!(r) }
  end

  def active?
    status == 'active'
  end

  def normalize
    self.title = title.strip
  end
end
"""

# F7: plain two-segment 'controller/action' route shorthand (no #, no dynamic :segment) is the ONLY
# honestly-inferable dynamic-route subset. 3-segment namespaced, :segment-dynamic, single-segment and
# the pure ':controller/:action' catch-all all stay drop-not-fabricate (no single nameable target).
DYNROUTES_RB = """Rails.application.routes.draw do
  match 'photos/show'
  get 'reports/monthly'
  post 'admin/widgets/create'
  get 'orders/:id'
  get 'health'
  get ':controller/:action'
end
"""


def gate_hash_rocket() -> None:
    print("\n-- F5: legacy hash-rocket (:key => value) association/render/callback DSL is recognized --")
    _, edges = RR.recognize(HASHROCKET_RB, "app/models/legacy_order.rb")
    refs = _by_type(edges, "REFERENCES")
    check(":class_name => 'Billing::Account' (rocket) overrides inflection AND demodulizes",
          any(e.dst_name == "Account" and e.attrs.get("class_name") == "Billing::Account"
              for e in refs), str([(e.dst_name, e.attrs) for e in refs]))
    cas = {e.dst_name: e.attrs.get("strategy") for e in _by_type(edges, "CASCADES")}
    check(":dependent => :destroy (rocket) -> CASCADES LineItem", cas.get("LineItem") == "destroy", str(cas))
    check(":dependent => :delete_all (rocket) -> CASCADES Invoice", cas.get("Invoice") == "delete_all", str(cas))
    hooks = {e.dst_name: e.attrs.get("event") for e in _by_type(edges, "HOOKS")}
    check("rocket callback `before_save :normalize` -> HOOKS normalize",
          hooks.get("normalize") == "before_save", str(hooks))
    check("rocket condition `:if => :active?` is NOT a hook target (the spurious-HOOKS hazard)",
          "active" not in hooks and "active?" not in hooks, str(hooks))
    # render: both hash syntaxes + the paren form; text:/=>text still rejected (not a view render).
    view = ("<%= render :partial => 'shared/form' %>\n"
            "<%= render(:partial => 'row') %>\n"
            "<%= render(partial: 'stats') %>\n"
            "<%= render :template => 'orders/summary' %>\n"
            "<%= render 'legacy/bare' %>\n"
            "<%= render(:layout => 'wide') %>\n"
            "<%= render :text => 'not a view' %>\n")
    _, vedges = RR.recognize(view, "app/views/orders/index.html.erb")
    dsts = {e.dst_name for e in vedges if e.edge_type == "RENDERS"}
    check("render :partial => 'shared/form' (rocket) -> RENDERS shared/form", "shared/form" in dsts, str(dsts))
    check("render(:partial => 'row') (paren+rocket) -> RENDERS orders/row", "orders/row" in dsts, str(dsts))
    check("render(partial: 'stats') (paren+1.9) -> RENDERS orders/stats", "orders/stats" in dsts, str(dsts))
    check("render :template => 'orders/summary' -> RENDERS orders/summary", "orders/summary" in dsts, str(dsts))
    check("render 'legacy/bare' (bare) still works", "legacy/bare" in dsts, str(dsts))
    check("render(:layout => 'wide') -> RENDERS orders/wide", "orders/wide" in dsts, str(dsts))
    check("render :text => '...' is NOT a view render (no fabricated edge)",
          not any("not" in d for d in dsts), str(dsts))


def gate_def_method_nodes() -> None:
    print("\n-- F2a: `def` methods -> supplemental Class.method nodes (graphify-missed fallback) --")
    nodes, _ = RR.recognize(DEFS_RB, "app/models/torah_lesson.rb")
    method_nodes = [n for n in nodes if n["label"] == "Method"]
    methods = {n["name"] for n in method_nodes}
    check("instance defs -> Class.method nodes (Class.method shape, NOT bare name)",
          {"TorahLesson.total_price", "TorahLesson.active?", "TorahLesson.normalize"} <= methods, str(methods))
    check("class method `def self.bulk_import` -> TorahLesson.bulk_import",
          "TorahLesson.bulk_import" in methods, str(methods))
    check("model scope still emitted alongside defs (TorahLesson.recent)",
          "TorahLesson.recent" in methods, str(methods))
    check("no duplicate Method-node names within the file (scope/def within-file de-dup)",
          len(methods) == len(method_nodes), str(methods))
    check("every def/scope :Method node carries the full push contract + Method label",
          all(n["label"] == "Method" and n["documented"] is False
              and n["source_file"] == "app/models/torah_lesson.rb" and n["start_line"] >= 1
              and all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
              for n in method_nodes))
    # HAPPY PATH: graphify already emitted the Class.method node -> the def supplemental dedupes away
    # (base wins), so a normal graphify install is byte-identical.
    graphify_like = [
        {"key": "app/models/torah_lesson.rb::TorahLesson.total_price:6", "label": "Method",
         "name": "TorahLesson.total_price", "source_file": "app/models/torah_lesson.rb",
         "start_line": 6, "body_summary": "graphify summary"}]
    supp1 = LR.validate_supplemental(
        [n for n in method_nodes if n["name"] == "TorahLesson.total_price"], "ruby")
    merged = LR.merge_supplemental(graphify_like, supp1)
    check("graphify has Class.method -> def supplemental dedupes away (base wins, byte-identical)",
          merged is graphify_like)
    # MISSED PATH: graphify absent -> the def nodes fill the method layer.
    merged2 = LR.merge_supplemental([], LR.validate_supplemental(method_nodes, "ruby"))
    check("graphify missed the file -> def nodes fill the method layer",
          {"TorahLesson.total_price", "TorahLesson.bulk_import"} <= {n["name"] for n in merged2})
    # ERB / routes files must NOT get def nodes (different recognize() branches).
    n_erb, _ = RR.recognize("<%= 1 %>\n", "app/views/x/y.html.erb")
    check("a .erb view yields no def nodes", not [n for n in n_erb if n["label"] == "Method"])


def gate_dynamic_routes() -> None:
    print("\n-- F7: plain two-segment 'controller/action' shorthand -> bounded DYNAMIC ROUTES_TO --")
    nodes, edges = RR.recognize(DYNROUTES_RB, "config/routes.rb")
    ro = _by_type(edges, "ROUTES_TO")
    tmap = {e.dst_name: e.conf_hint for e in ro}
    check("match 'photos/show' -> PhotosController.show (conf_hint DYNAMIC)",
          tmap.get("PhotosController.show") == "DYNAMIC", str(tmap))
    check("get 'reports/monthly' -> ReportsController.monthly (DYNAMIC)",
          tmap.get("ReportsController.monthly") == "DYNAMIC", str(tmap))
    dynamic = [e for e in ro if e.conf_hint == "DYNAMIC"]
    check("exactly 2 DYNAMIC edges: 3-segment / :dynamic / single-segment / pure catch-all are NOT inferred",
          len(dynamic) == 2, str([e.dst_name for e in dynamic]))
    # DYNAMIC ROUTES_TO grades AMBIGUOUS and resolves cross-file / drops if unresolved (honest).
    ctrl = [{"key": "app/controllers/photos_controller.rb::PhotosController.show:2", "label": "Method",
             "name": "PhotosController.show", "source_file": "app/controllers/photos_controller.rb"}]
    resolver = ER.Resolver(nodes + ctrl)
    live = [n["key"] for n in nodes] + [ctrl[0]["key"]]
    s = _Sess()
    ER.push_typed_edges(s, ro, resolver, live)
    made = {p["b"]: p["c"] for c, p in s.log if "MERGE" in c and "ROUTES_TO" in c}
    check("DYNAMIC route resolves cross-file to PhotosController.show, graded AMBIGUOUS",
          made.get(ctrl[0]["key"]) == "AMBIGUOUS", str(made))
    check("a DYNAMIC route with no matching controller node DROPS (never fabricates)",
          len(made) == 1 and ctrl[0]["key"] in made, str(made))
    check("recognize is deterministic on the dynamic-routes file",
          RR.recognize(DYNROUTES_RB, "config/routes.rb") == (nodes, edges))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — Ruby/Rails recognizer gates (Ph2, no Neo4j / Ruby runtime)")
    print("=" * 64)
    gate_recognizer_emit()
    gate_inflector()
    gate_ensured_nodes_match_graphify()
    gate_reversed_cascades_persistence()
    gate_namespaced_resolution()
    gate_routes()
    gate_routes_member_collection()
    gate_views_renders()
    gate_registry_end_to_end()
    gate_hash_rocket()
    gate_def_method_nodes()
    gate_dynamic_routes()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
