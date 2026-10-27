#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ruby/Rails recognizer — ActiveRecord DSL -> typed edges + ensured model nodes (plugin #2).

The 2026-07 field report: on a Rails codebase, DeepMap's flagship `deepmap_impact` could not trace
CASCADE relationships — `has_many :items, dependent: :destroy` is *declarative DSL*, not a call, so
the generic engine (graphify) yields no relationship for it and the Python-only framework_edges
recognizers never see a `.rb` file. This module reads Rails model conventions HEURISTICALLY
(line-oriented regex — no Ruby runtime, no graphify fork; Rails conventions are strongly
regex-tractable) and emits, per file:

  typed edges (the value):
    INHERITS    class Foo < Parent                      (LOCAL when the parent is declared here)
    HAS_MANY    has_many/has_one/has_and_belongs_to_many :syms   owner -> owned model
    REFERENCES  belongs_to :sym                                  FK-holder -> referenced model
    CASCADES    ..., dependent: :destroy|:destroy_async|:delete_all|:delete|:nullify
                — the "is destroyed with" blast-radius edge. NOTE: persisted REVERSED
                (owned)-[:CASCADES]->(owner) by edge_resolve, so `deepmap_impact("Order")` (reverse
                reachability) lists what a destroy of Order cascades to. The TypedEdge here is still
                emitted DECLARATION-ANCHORED (src = the declaring model) so src resolves same-file.
    HOOKS       before_/after_/around_* :method callbacks        (SAME_FILE_STRICT: resolve-or-drop)

  supplemental nodes (the fallback): one ensured :Class node per class declaration, named EXACTLY
  as declared (`AuditLog` nested-module form, `Admin::Override` compact form — byte-matching
  graphify's shapes, verified against the captured fixture tests/fixtures/graphify_multilang/
  ruby_rails.json), with a DSL-derived deterministic body_summary. When graphify already parsed the
  file, merge_supplemental's (name, source_file) dedupe drops these (base wins); when graphify
  missed the file/repo (ast fallback, parse failure) they keep the model layer present + searchable.

Honesty model (the confidence wedge absorbs heuristic error):
  * name resolution can DROP an edge (unresolvable target), never fabricate one — a mis-inflected
    plural resolves to nothing and disappears (edge_resolve drops unresolved targets);
  * a name resolving to >1 symbol grades AMBIGUOUS (verify by reading);
  * `class_name:` overrides inflection (the raw namespaced string is kept in attrs);
  * targets are DEMODULIZED (`Billing::Invoice` -> `Invoice`) to match graphify's dominant bare
    shape; edge_resolve's name index also indexes the `::`-last segment so compact-form nodes
    (`Admin::Override`) resolve from a bare `Override`;
  * `dependent: :restrict_with_error/_exception` PREVENT deletion — deliberately NOT CASCADES.

Pure: recognize(source_text, rel_path) only — no fs, no network; the registry's shared walker owns
file I/O, ignore dirs (vendor/, node_modules/, tmp/, log/) and per-file error isolation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from framework_edges import TypedEdge  # noqa: E402  (the shared edge shape; endpoints name+file)

# ---- Rails vocabulary --------------------------------------------------------
_ASSOC_KINDS = {"has_many": "HAS_MANY", "has_one": "HAS_MANY",
                "has_and_belongs_to_many": "HAS_MANY", "belongs_to": "REFERENCES"}
_CASCADE_STRATEGIES = {"destroy", "destroy_async", "delete_all", "delete", "nullify"}
_MODEL_BASES = {"ApplicationRecord", "ActiveRecord::Base"}

_CLASS_RE = re.compile(r'^\s*class\s+([A-Z]\w*(?:::[A-Z]\w*)*)\s*(?:<\s*([A-Z]\w*(?:::[A-Z]\w*)*))?')
# [\s(] admits the paren form `belongs_to(:x)`. Options (class_name/dependent) accept BOTH the 1.9
# `key:` and the legacy `:key =>` hash-rocket syntax (F5) — pervasive on pre-2013 Rails apps, which the
# recognizer previously ignored, silently dropping ~all their REFERENCES/CASCADES. Drop-not-fabricate
# is unchanged: a missed edge, never a wrong one.
_ASSOC_RE = re.compile(r'^\s*(has_many|has_one|has_and_belongs_to_many|belongs_to)[\s(]+:(\w+)(.*)$')
_CLASS_NAME_RE = re.compile(r'(?::class_name\s*=>|class_name:)\s*[\'"]([A-Za-z0-9_:]+)[\'"]')
_DEPENDENT_RE = re.compile(r'(?::dependent\s*=>|dependent:)\s*:(\w+)')
# A `def` method (instance or `def self.x`) -> a supplemental :Method node in graphify's `Class.method`
# shape (F2a), so a file graphify MISSED (ast fallback / per-file parse error / heredoc truncation) keeps
# its methods searchable; on the happy path graphify emits the same node so it dedupes away base-wins.
_DEF_RE = re.compile(r'^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)')
_CALLBACK_RE = re.compile(
    r'^\s*((?:before|after|around)_(?:save|create|update|destroy|validation|commit|rollback|'
    r'touch|initialize|find|action|filter))\b(.*)$')
_CALLBACK_SYMS_RE = re.compile(r':(\w+[?!]?)')
# Option keys after which :symbols are CONDITIONS, not callback targets (`if: :active?` OR the legacy
# rocket `:if => :active?`). The rocket form was previously unmatched, so on a legacy callback the
# condition symbol (`:active?`) AND the option key (`:if`) leaked through as spurious HOOKS targets (F5).
_CALLBACK_OPTS_RE = re.compile(
    r':(?:if|unless|on|prepend|only|except)\s*=>|\b(?:if|unless|on|prepend|only|except):')
# A model-level named scope: `scope :active, -> { ... }` (also lambda/proc). It defines an
# ActiveRecord class method (Model.active) that graphify's def-parser never node-ifies (a scope is a
# macro call, not a `def`), so it is invisible today. The `^\s*scope :sym ,` + required callable is
# what keeps it clear of routes-mode `scope :x do` blocks and of `scope module: 'x'` (no leading :sym).
_MODEL_SCOPE_RE = re.compile(r'^\s*scope\s+:(\w+)\s*,\s*(?:->|lambda\b|proc\b|Proc\.new\b)')

# ---- Rails routes.rb vocabulary (config/routes.rb -> :ROUTES_TO + scope/lambda :Method nodes) --
# routes.rb is declarative DSL with NO class context, so the model scan above yields nothing for it;
# route parsing runs in its own mode. A route target (`orders#index`) lives in ANOTHER file
# (OrdersController), so ROUTES_TO is emitted conf_hint=NAME and edge_resolve resolves it cross-file
# (drop-if-unresolved). Namespaces are handled by edge_resolve's ::-segment index (we emit the
# DEMODULIZED controller), so route parsing never needs to prepend a module prefix for resolution.
_ROUTE_VERB_RE = re.compile(r'^\s*(get|post|put|patch|delete|match|root)\b(.*)$')
_CTRL_ACTION_RE = re.compile(r'''['"]([a-z0-9_/]+)#(\w+)['"]''')      # 'orders#index' / "admin/orders#show"
_RESOURCES_RE = re.compile(r'^\s*(resources|resource)\s+:(\w+)')
_SCOPE_SYM_RE = re.compile(r'^\s*(namespace|scope)\s+:(\w+)')
_SCOPE_STR_RE = re.compile(r'''^\s*scope\s+(?:module|path):\s*['"]?([\w/]+)['"]?''')
_CONSTRAINTS_RE = re.compile(r'^\s*constraints\b')
_DO_RE = re.compile(r'\bdo\b\s*(?:\|[^|]*\|)?\s*$')                   # a line that OPENS a do..end block
_LAMBDA_RE = re.compile(r'(?:->|\blambda\b|\bproc\b)\s*[({]')         # a lambda/proc literal on the line
_REST_ACTIONS = ("index", "show", "new", "create", "edit", "update", "destroy")
_REST_ACTIONS_SINGULAR = ("show", "new", "create", "edit", "update", "destroy")  # `resource` has no index
# `only:` / `except:` on a resources line (both the `key:` and legacy `:key =>` forms) restrict which
# REST actions exist — ignoring them fabricated a route to every unrouted action the controller happens
# to define (a leftover `destroy`, the `resources :x, only: [] do` member-only idiom; #60).
_ONLY_EXCEPT_RE = re.compile(r'(?:\b(only|except)\s*:(?!:)|:(only|except)\s*=>)\s*')
_CONTROLLER_OPT_RE = re.compile(r'''(?:\bcontroller\s*:(?!:)|:controller\s*=>)\s*['"]([\w/]+)['"]''')
_ACTION_LIST_RE = re.compile(r'''\[(?P<arr>[^\]]*)\]|%[iwIW](?:\[(?P<pb>[^\]]*)\]|\((?P<pp>[^)]*)\))|'''
                             r''':(?P<sym>\w+)|['"](?P<str>\w+)['"]''')
# P1-4: custom member/collection routes inside a `resources` block — the dominant pattern on an
# Active-Scaffold-heavy app. `member do; get :publish; end` and the inline `get :publish, on: :member`
# both map to the ENCLOSING resource's controller (OrdersController.publish), NAME-resolved cross-file.
_MEMBER_COLLECTION_RE = re.compile(r'^\s*(member|collection)\b')
_VERB_SYM_ACTION_RE = re.compile(r'^\s*(get|post|put|patch|delete|match)\s+:(\w+)')
_ON_MEMBER_COLLECTION_RE = re.compile(r'\bon:\s*:(member|collection)\b')
# F7: a plain two-segment 'controller/action' route SHORTHAND (`match 'photos/show'`, `get 'reports/
# monthly'`) is the ONLY dynamic-route form with a single nameable target — infer controller#action,
# graded DYNAMIC (edge_resolve -> AMBIGUOUS; cross-file resolve, drop-if-unresolved). The class excludes
# ':' so a dynamic ':id'/':action' segment never matches; the trailing quote anchor rejects a 3-segment
# namespaced path (`admin/widgets/create`); a single segment ('health') has no second group. The pure
# ':controller/:action(/:id)' catch-all is thus never inferred (no single target = forbidden fan-out).
_VERB_PATH_RE = re.compile(
    r'''^\s*(?:get|post|put|patch|delete|match)\s+['"]/?([a-z0-9_]+/[a-z0-9_]+)['"]''')

# Small irregulars table (misses DROP the edge — never fabricate; see module docstring).
_IRREGULAR = {
    "people": "person", "children": "child", "men": "man", "women": "woman",
    "mice": "mouse", "geese": "goose", "feet": "foot", "teeth": "tooth",
    "leaves": "leaf", "lives": "life", "data": "datum", "media": "medium",
    "statuses": "status", "aliases": "alias", "analyses": "analysis",
    "indices": "index", "vertices": "vertex", "matrices": "matrix", "quizzes": "quiz",
}


# Uncountable / already-singular -s words: naively stripping the trailing s mis-inflects them
# (news -> new, series -> serie), silently DROPPING the association edge. Never strip these. The -us
# family (status/campus/genus/virus/bonus…) is caught open-endedly by the endswith("us") rule below —
# this set is only for the irregular -s uncountables that rule can't cover.
_UNCOUNTABLE = {"news", "series", "species", "information", "equipment", "money", "rice", "fish",
                "sheep", "deer", "police", "staff"}


def singularize(word: str) -> str:
    """Naive Rails-convention singularizer: irregulars table + uncountable/-us guards + the common
    suffix rules. A wrong guess costs a DROPPED edge (unresolvable name), never a wrong one."""
    lw = word.lower()
    if lw in _IRREGULAR:
        return _IRREGULAR[lw]
    if lw in _UNCOUNTABLE:
        return word
    if lw.endswith("ies") and len(lw) > 3:
        return word[:-3] + "y"
    if lw.endswith(("xes", "ches", "shes", "sses", "zes")):
        return word[:-2]
    if lw.endswith("us"):                # status/campus/genus/virus/bonus: singular -us, keep the s
        return word
    if lw.endswith("s") and not lw.endswith("ss"):
        return word[:-1]
    return word


def classify_name(sym: str) -> str:
    """Rails `classify`: association symbol -> model class name (line_items -> LineItem). Singularize
    only the LAST underscore segment — Rails' inflector treats a compound like `activity_status` as
    <activity><status> and (de)pluralizes the FINAL word — so `activity_status` -> ActivityStatus and
    `order_statuses` -> OrderStatus resolve instead of mis-inflecting to `*Statu`/`*Statuse` (so a
    `belongs_to :activity_status` resolves to ActivityStatus instead of being silently dropped)."""
    parts = [p for p in sym.split("_") if p]
    if parts:
        parts[-1] = singularize(parts[-1])
    return "".join(p.capitalize() for p in parts)


def demodulize(name: str) -> str:
    """`Billing::Invoice` -> `Invoice` (graphify's dominant bare-name shape for nested modules)."""
    return name.rsplit("::", 1)[-1]


def camelize(name: str) -> str:
    """`order_items` -> `OrderItems` — Rails `camelize` WITHOUT singularization (controllers are
    plural, unlike models). Kept distinct from classify_name so a wrong plural never fabricates."""
    return "".join(p.capitalize() for p in name.split("_") if p)


def controller_class(spec: str) -> str:
    """A resource symbol or a `ns/ctrl` route string -> the DEMODULIZED controller class name.
    `orders` -> `OrdersController`; `admin/orders` -> `OrdersController` (the `admin` namespace is
    recovered at resolution by edge_resolve's ::-segment index, so we never prepend it here)."""
    last = spec.replace("::", "/").rsplit("/", 1)[-1]
    return camelize(last) + "Controller"


_IRREGULAR_PLURAL = {v: k for k, v in _IRREGULAR.items()}


def pluralize(word: str) -> str:
    """Naive Rails `pluralize` for a singular `resource :profile` (Rails routes it to the PLURAL
    ProfilesController). A wrong guess names no controller and DROPS the edge — never a wrong one."""
    lw = word.lower()
    head, _, last = lw.rpartition("_")
    if last in _IRREGULAR_PLURAL:
        plural = _IRREGULAR_PLURAL[last]
    elif last in _UNCOUNTABLE:
        plural = last
    elif last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
        plural = last[:-1] + "ies"
    elif last.endswith(("s", "x", "z", "ch", "sh")):
        plural = last + "es"
    else:
        plural = last + "s"
    return f"{head}_{plural}" if head else plural


def _unclosed(text: str) -> bool:
    return text.count("[") > text.count("]") or text.count("(") > text.count(")")


def _resource_actions(opts: str, kind: str):
    """The REST actions a `resources`/`resource` line really routes, from its `only:`/`except:`
    options -> (actions, parsed). `parsed` is False when an option is present but its value is not a
    literal list/symbol (a constant, a method call) — the caller then grades the edges DYNAMIC."""
    actions = list(_REST_ACTIONS if kind == "resources" else _REST_ACTIONS_SINGULAR)
    parsed = True
    for m in _ONLY_EXCEPT_RE.finditer(opts):
        key = m.group(1) or m.group(2)
        v = _ACTION_LIST_RE.match(opts, m.end())
        if not v:
            parsed = False
            continue
        if v.group("sym") or v.group("str"):
            names = {v.group("sym") or v.group("str")}
        else:
            body = v.group("arr") if v.group("arr") is not None else (v.group("pb") or v.group("pp") or "")
            names = set(re.findall(r'\w+', body))
        actions = [a for a in actions if (a in names) == (key == "only")]
    return actions, parsed


def _is_routes_file(rel: str, source: str) -> bool:
    """A Rails routes file: `config/routes.rb`, a split `config/routes/*.rb`, or any file whose body
    calls `…routes.draw` (covers `Rails.application.routes.draw` and engine `*.routes.draw`)."""
    # Path comparisons are case-INSENSITIVE (a `CONFIG/ROUTES.RB` still IS the routes file); the
    # `routes.draw` body probe stays case-sensitive because it matches real Ruby source, not a path.
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    return base == "routes.rb" or "config/routes/" in low or "routes.draw" in source


def _recognize_routes(source: str, rel: str) -> tuple:
    """config/routes.rb -> (:Method scope/lambda/table nodes, :ROUTES_TO edges). Route targets
    (`controller#action`) live in other files, so edges are conf_hint=NAME (cross-file resolve,
    drop-if-unresolved). Never raises: unmatched lines are skipped. The src of every edge is the
    nearest enclosing scope :Method node (or the top-level route-table node = file stem), which is a
    same-file supplemental node, so src always resolves same-file."""
    nodes: list = []
    edges: list = []
    base = rel.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] or "routes"
    stack: list = []                 # (name, line, resource_sym|None, is_member_collection) frames
    seen_keys: set = set()

    def dotted() -> str:
        parts = [fr[0] for fr in stack if fr[0]]
        return ".".join(parts) if parts else stem

    def current_resource():          # the nearest enclosing `resources`/`resource` symbol (P1-4)
        for fr in reversed(stack):
            if fr[2]:
                return fr[2]
        return None

    def add_node(name: str, line: int) -> None:
        if not name:
            return
        key = f"{rel}::{name}:{line}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        nodes.append({
            "key": key, "label": "Method", "name": name, "source_file": rel,
            "start_line": line, "documented": False,
            "body_summary": f"{name}: Rails route group (config/routes.rb)"[:300],
        })

    add_node(stem, 1)                # the top-level route table (dedupes to graphify's Module if present)

    src_lines = source.splitlines()
    for i, raw in enumerate(src_lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        anchor = dotted()            # the enclosing scope path BEFORE this line opens a new frame

        sym = _SCOPE_SYM_RE.match(line)          # namespace :x / scope :x
        scs = _SCOPE_STR_RE.match(line)          # scope module:/path: 'x'
        res = _RESOURCES_RE.match(line)

        # -- resources/resource: RESTful ROUTES_TO to <Controller>.<action> (unresolved ones drop) --
        if res:
            kind, rsym = res.group(1), res.group(2)
            opts = line[res.end():].split("#", 1)[0]
            if _ONLY_EXCEPT_RE.search(opts) and _unclosed(opts):
                # a multi-line `only: [` list: join its continuation lines (bounded) before parsing
                for nxt in src_lines[i:i + 30]:
                    opts += " " + nxt.split("#", 1)[0].strip()
                    if not _unclosed(opts):
                        break
            actions, parsed = _resource_actions(opts, kind)
            cm = _CONTROLLER_OPT_RE.search(opts)
            # `resource :profile` (singular) is served by the PLURAL ProfilesController in Rails.
            ctrl = controller_class(cm.group(1) if cm else
                                    (rsym if kind == "resources" else pluralize(rsym)))
            for act in actions:
                # An only:/except: whose value is not a literal list cannot be evaluated here: keep
                # the actions, but DYNAMIC-graded (-> AMBIGUOUS, verify by reading), never INFERRED.
                edges.append(TypedEdge(anchor, rel, f"{ctrl}.{act}", "ROUTES_TO",
                                       "NAME" if parsed else "DYNAMIC",
                                       {"method": "REST", "action": act, "resource": rsym}))

        # -- block opener: push a frame; emit a :Method node for a NAMED scope --------------------
        opened = bool(_DO_RE.search(raw))
        mcm = _MEMBER_COLLECTION_RE.match(line)          # member do / collection do (P1-4)
        if opened:
            if sym:
                name = sym.group(2)
            elif scs:
                name = scs.group(1).rsplit("/", 1)[-1]
            elif _CONSTRAINTS_RE.match(line):
                name = "constraints"
            elif res:
                name = res.group(2)
            elif mcm:
                name = mcm.group(1)  # "member"/"collection" — a searchable scope node
            else:
                name = ""            # Rails.application.routes.draw do / an anonymous block
            stack.append((name, i, (res.group(2) if res else None), bool(mcm)))
            if name:
                add_node(dotted(), i)

        # -- lambda / proc literal -> a searchable :Method node for the block ---------------------
        if _LAMBDA_RE.search(line):
            add_node(f"{anchor}.lambda_L{i}", i)

        # -- verb routes: get/post/put/patch/delete/match/root 'controller#action' ----------------
        vm = _ROUTE_VERB_RE.match(line)
        if vm and not opened:
            ca = _CTRL_ACTION_RE.search(line)
            if ca:
                edges.append(TypedEdge(anchor, rel, f"{controller_class(ca.group(1))}.{ca.group(2)}",
                                       "ROUTES_TO", "NAME",
                                       {"method": vm.group(1).upper(),
                                        "target": f"{ca.group(1)}#{ca.group(2)}"}))
            else:
                # P1-4: a custom member/collection action — `get :publish` inside a `member`/
                # `collection` block, or the inline `get :publish, on: :member`. No controller string,
                # so it binds to the ENCLOSING resource's controller (drop if there is none/unresolved).
                sa = _VERB_SYM_ACTION_RE.match(line)
                res_sym = current_resource()
                in_mc = any(fr[3] for fr in stack)
                if sa and res_sym and (in_mc or _ON_MEMBER_COLLECTION_RE.search(line)):
                    edges.append(TypedEdge(anchor, rel,
                                           f"{controller_class(res_sym)}.{sa.group(2)}",
                                           "ROUTES_TO", "NAME",
                                           {"method": vm.group(1).upper(), "action": sa.group(2),
                                            "resource": res_sym, "via": "member_collection"}))
                else:
                    # F7: a plain two-segment 'controller/action' shorthand (no #, no dynamic :segment,
                    # not 3-segment/single) -> infer controller#action, graded DYNAMIC (edge_resolve ->
                    # AMBIGUOUS; cross-file resolve, drop-if-unresolved). The only honestly-inferable
                    # dynamic-route subset; the Active-Scaffold catch-all stays unfabricated.
                    pm = _VERB_PATH_RE.match(line)
                    if pm:
                        c_seg, a_seg = pm.group(1).split("/", 1)
                        edges.append(TypedEdge(
                            anchor, rel, f"{controller_class(c_seg)}.{a_seg}", "ROUTES_TO", "DYNAMIC",
                            {"method": vm.group(1).upper(), "target": f"{c_seg}#{a_seg}",
                             "via": "path_shorthand"}))

        # -- block close --------------------------------------------------------------------------
        if line == "end" or line.startswith("end ") or line == "end;":
            if stack:
                stack.pop()

    return nodes, edges


# ---- Rails views (.erb -> :Module view nodes + RENDERS edges) --------------------------------------
# A change to a shared partial (`_form.html.erb`) breaks every view that renders it, but .erb is not
# code so graphify yields no node/edge for it. This recognizer emits, per .erb: ONE :Module view node
# (searchable, keyed by its Rails logical name, e.g. "orders/index" / "shared/form") and a RENDERS edge
# for each `render` of a partial/template — src = THIS view (same-file), dst = the rendered view's
# logical name (cross-file NAME resolve, drop-if-unresolved). Controller->implicit-template renders are
# NOT emitted (they are convention-generated; a static guess would fabricate — drop-not-fabricate).
# [\s(] admits the paren form `render(...)`; `:?keyword` + `(?::|=>)` admit BOTH `partial: 'x'` and the
# legacy `:partial => 'x'` (F5). `text:`/`json:`/`:text =>` still don't match (keyword not in the set),
# so a non-view render fabricates no edge.
_RENDER_KW_RE = re.compile(
    r'''\brender\b[\s(]+:?(?:partial|template|layout)\s*(?::|=>)\s*['"]([a-z0-9_][\w/]*)['"]''',
    re.IGNORECASE)
_RENDER_BARE_RE = re.compile(r'''\brender\b[\s(]+['"]([a-z0-9_][\w/]*)['"]''', re.IGNORECASE)


def _view_logical_name(rel: str) -> str:
    """A Rails view path -> its logical name: strip to the views/ root, drop the extensions and a
    leading `_` on the basename. app/views/orders/index.html.erb -> 'orders/index';
    app/views/shared/_form.html.erb -> 'shared/form'."""
    raw = rel.replace("\\", "/")
    if "/views/" in raw:
        sub = raw.rsplit("/views/", 1)[1]
    elif raw.startswith("views/"):
        sub = raw[len("views/"):]
    else:
        sub = raw
    parts = sub.split("/")
    stem = parts[-1].split(".", 1)[0].lstrip("_")     # index.html.erb -> index ; _form.html.erb -> form
    dirs = "/".join(parts[:-1])
    return f"{dirs}/{stem}" if dirs else stem


def _resolve_render_target(target: str, current_view: str) -> str:
    """A `render` argument -> the rendered view's logical name. A bare `render 'row'` is same-directory
    as the current view; a `render 'shared/form'` is views/-relative. Leading `_` on the basename is
    dropped so it matches the partial's :Module node name."""
    t = target.strip().strip("/")
    if "/" in t:
        d, b = t.rsplit("/", 1)
        return f"{d}/{b.lstrip('_')}"
    cur_dir = current_view.rsplit("/", 1)[0] if "/" in current_view else ""
    b = t.lstrip("_")
    return f"{cur_dir}/{b}" if cur_dir else b


def _recognize_views(source: str, rel: str) -> tuple:
    """.erb view -> ([one :Module view node], [RENDERS edges to rendered partials]). Never raises."""
    name = _view_logical_name(rel)
    node = {
        "key": f"{rel}::{name}:1", "label": "Module", "name": name, "source_file": rel,
        "start_line": 1, "documented": False,
        "body_summary": f"{name}: Rails view template ({rel})"[:300],
    }
    edges: list = []
    seen: set = set()
    for i, raw in enumerate(source.splitlines(), start=1):
        for rx in (_RENDER_KW_RE, _RENDER_BARE_RE):
            for m in rx.finditer(raw):
                dst = _resolve_render_target(m.group(1), name)
                if not dst or dst == name:            # never a self-render edge
                    continue
                key = (dst, i)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(TypedEdge(name, rel, dst, "RENDERS", "NAME", {"render": m.group(1)}))
    return [node], edges


class _ClassCtx:
    """The class currently collecting DSL facts (most-recent declaration heuristic)."""

    def __init__(self, name: str, parent, line: int):
        self.name = name          # EXACTLY as declared (compact form keeps `Admin::X`)
        self.parent = parent
        self.line = line
        self.facts: list = []     # human fragments for the body_summary
        self.is_model = parent in _MODEL_BASES if parent else False


def _summary(ctx: _ClassCtx) -> str:
    kind = "ActiveRecord model" if ctx.is_model else "Ruby class"
    base = f"{demodulize(ctx.name)}: {kind}"
    if ctx.facts:
        base += "; " + "; ".join(ctx.facts[:8])
    return base[:300]


def recognize(source: str, rel: str) -> tuple:
    """PURE per-file core (the registry contract): Ruby source -> (supplemental_nodes, typed_edges).
    Never raises on malformed source — unmatched lines are simply skipped."""
    # Case-INSENSITIVE: `rel` is a raw relpath (never lowercased upstream), so an `INDEX.HTML.ERB`
    # would otherwise fall through to the model/controller path and emit no view node or :RENDERS.
    if rel.lower().endswith(".erb"):             # Rails view template (P1-3): :Module node + RENDERS
        return _recognize_views(source, rel)
    if _is_routes_file(rel, source):
        return _recognize_routes(source, rel)
    nodes: list = []
    edges: list = []
    declared: set = set()      # demodulized class names declared in THIS file (INHERITS locality)
    ctxs: list = []
    cur: _ClassCtx | None = None
    method_names: set = set()  # Class.method names already emitted (scope + def within-file de-dup)

    lines = source.splitlines()
    for m in (r for r in (_CLASS_RE.match(ln) for ln in lines) if r):
        declared.add(demodulize(m.group(1)))

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        cm = _CLASS_RE.match(raw)
        if cm:
            cur = _ClassCtx(cm.group(1), cm.group(2), i)
            ctxs.append(cur)
            if cur.parent:
                conf = "LOCAL" if demodulize(cur.parent) in declared else "NAME"
                edges.append(TypedEdge(cur.name, rel, demodulize(cur.parent), "INHERITS", conf,
                                       {"raw_parent": cur.parent} if "::" in cur.parent else {}))
            continue
        if cur is None:
            continue

        am = _ASSOC_RE.match(raw)
        if am:
            kind, sym, rest = am.group(1), am.group(2), am.group(3) or ""
            cn = _CLASS_NAME_RE.search(rest)
            target = demodulize(cn.group(1)) if cn else classify_name(sym)
            attrs = {"assoc": kind, "symbol": sym}
            if cn:
                attrs["class_name"] = cn.group(1)
            edges.append(TypedEdge(cur.name, rel, target, _ASSOC_KINDS[kind], "NAME", dict(attrs)))
            dm = _DEPENDENT_RE.search(rest)
            frag = f"{kind} {sym}"
            if dm and dm.group(1) in _CASCADE_STRATEGIES:
                # Declaration-anchored (src = the declaring model, resolves same-file); persisted
                # REVERSED by edge_resolve so impact(owner) lists the cascade targets.
                edges.append(TypedEdge(cur.name, rel, target, "CASCADES", "NAME",
                                       dict(attrs, strategy=dm.group(1))))
                frag += f" (dependent: {dm.group(1)})"
            cur.facts.append(frag)
            continue

        cb = _CALLBACK_RE.match(raw)
        if cb:
            event, rest = cb.group(1), cb.group(2) or ""
            targets_part = _CALLBACK_OPTS_RE.split(rest)[0]   # symbols AFTER if:/unless: are conditions
            for sym in _CALLBACK_SYMS_RE.findall(targets_part):
                edges.append(TypedEdge(cur.name, rel, sym, "HOOKS", "LOCAL", {"event": event}))
            continue

        # A1: a model-level named scope -> a searchable :Method node (Model.scope). Node-only (a scope
        # body is a relation, not one of the typed edges); the graphify-shape name Class.method keeps
        # it bare-resolvable via edge_resolve's `.`-segment index and collision-free at merge. Emitted for
        # ANY enclosing class (not gated on is_model) so STI subclasses + ActiveSupport::Concern mixins,
        # which also carry scopes, are covered — matching how the assoc/callback handlers fire for any cur.
        sc = _MODEL_SCOPE_RE.match(raw)
        if sc:
            name = f"{demodulize(cur.name)}.{sc.group(1)}"
            if name not in method_names:
                method_names.add(name)
                nodes.append({
                    "key": f"{rel}::{name}:{i}", "label": "Method", "name": name, "source_file": rel,
                    "start_line": i, "documented": False,
                    "body_summary": f"{name}: ActiveRecord scope (class method)"[:300],
                })
            continue

        # F2a: a `def` method -> a supplemental :Method node (Class.method shape, the same shape as the
        # scope node + graphify's method nodes). On a normal graphify install these dedupe away
        # base-wins; when graphify missed/truncated the file they backfill the searchable method layer.
        # Class-scoped (needs an enclosing `class` — `cur`); de-duped within-file against scopes/defs.
        dfm = _DEF_RE.match(raw)
        if dfm:
            name = f"{demodulize(cur.name)}.{dfm.group(1)}"
            if name not in method_names:
                method_names.add(name)
                nodes.append({
                    "key": f"{rel}::{name}:{i}", "label": "Method", "name": name, "source_file": rel,
                    "start_line": i, "documented": False,
                    "body_summary": f"{name}: Ruby method (def)"[:300],
                })
            continue

    for ctx in ctxs:
        nodes.append({
            "key": f"{rel}::{ctx.name}:{ctx.line}",
            "label": "Class",
            "name": ctx.name,                     # byte-matching graphify's shape for BOTH forms
            "source_file": rel,
            "start_line": ctx.line,
            "documented": False,
            "body_summary": _summary(ctx),
        })
    return nodes, edges
