#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Track C — framework/runtime relationship edges (extractor-INDEPENDENT AST recognizers).

DeepMap's persisted graph has one edge type today: :CALLS (a literal call graph). Most real wiring in
modern code is *declared* (decorators, config, registration) and resolved by a FRAMEWORK at runtime —
an HTTP route->handler, a Celery @task, a FastAPI Depends(), a signal receiver. There is no literal call
to follow, so both grep AND the call graph miss it. This module recovers those relationships as typed,
confidence-graded edges.

Why re-parse the AST directly (not reuse the extractor output): the DEFAULT engine is now graphify
(build/sync.py `_rebuild_code`, DEEPMAP_EXTRACTOR=auto), and graphify DROPS decorator information — a route handler
arrives as a plain Function, with no decorator/method/path. So route/DI/decorator recognizers MUST read
the Python source AST themselves, regardless of which extractor produced the nodes.

Purity + contract (mirrors graphify_extract.map_graph / extract):
  * edges_from_module(tree, rel) -> list[TypedEdge]   -- PURE: ast in -> typed edges out, no fs/net.
  * compose_route_prefixes(root, edges, exclude=None) -> list[TypedEdge] -- the cross-module route-prefix
    post-pass (APIRouter/include_router, Blueprint/register_blueprint mounts that span files); the registry's
    Python entry runs it as its `finalize`, the legacy shell below runs it too (byte-equal if run() gets no `exclude`).
  * extract_framework_edges(workspace_root) -> list[TypedEdge]  -- the impure shell (walks *.py).

Endpoints are resolvable NAMES + source_file (never graph keys), so the same edges resolve whether
graphify (opaque ids) or the stdlib ast extractor (relpath::name:line) produced the nodes — both set
`name` + posix `source_file` identically. Name-resolution + confidence grading happen later, at push
time (build/edge_resolve.py), reusing the CALLS confidence model.

Round 7 (2026-09 sweep T8d-X1 / T4d-G1) added, all default ON (recognizer coverage, off the ranking path):
  * APScheduler: @sched.scheduled_job(trigger, ...) and sched.add_job(func, trigger, ...) -> SCHEDULED_AS
    (attrs {"trigger": ...} when the trigger is a literal or a trigger class).
  * requests/httpx-style hooks: r.register_hook("<event>", handler), hooks={"<event>": [...]} /
    event_hooks={...} keyword dicts, and s.hooks["<event>"].append(handler) -> HOOKS {"event": ...}.
  * route PREFIXES: a route declared on an APIRouter(prefix=...) / Blueprint(url_prefix=...) carries the
    composed path, including include_router(prefix=...) / register_blueprint(url_prefix=...) mounts (Flask's
    override-then-join rule, nested blueprints/routers, a mount in another module). Only ROUTES_TO `attrs`
    change (never an endpoint); routes on anything else keep attrs exactly {method, path}. A router
    mounted at several places gets `paths` (all, sorted) with `path` = the first; a non-literal prefix
    part is left out of the path and named in `prefix_unresolved` (never guessed).

2026-10 (the Django / SQLAlchemy 2.0 track), recognizer coverage, off the ranking path:
  * Django URLconfs (_UrlConfReader): every path() / re_path() / url() entry of a URL pattern list (a
    module-level `urlpatterns` and the lists it includes or concatenates; the functions imported from
    django.urls / django.conf.urls, never a pathlib / os `path`) whose view names a symbol -> ROUTES_TO
    module -> view, attrs {method '*', path, framework 'django'} (+ url_name, paths / url_names,
    prefix_unresolved). A function view, a class-based view's CLASS (X.as_view()), through login_required(...)
    style decorators and `x = View.as_view()` aliases; `admin.site.urls`, `router.urls`, lambdas and factory
    calls name no view. A view defined in the URLconf is LOCAL (EXTRACTED); an imported one is placed in the
    module that defines it by compose_route_prefixes (INFERRED; outside the tree it binds nothing). include()
    composes the path and the url namespace across modules ('polls.urls', an imported module or pattern
    list, (patterns, app_name) pairs, namespace=, inline lists; i18n_patterns' language prefix is named in
    prefix_unresolved). A regex pattern is kept without its ^ / $ anchors.
  * SQLAlchemy 2.0 typed declarations (`x: Mapped[...] = relationship(...)`, Mapped imported from
    SQLAlchemy): a collection annotation -> HAS_MANY, a scalar one -> REFERENCES, the target from the
    annotation when relationship() names none; mapped_column(ForeignKey(...)) -> REFERENCES like Column().
    A ForeignKey("orders.id") names the class whose __tablename__ is 'orders' (this module's, else the one
    class of the tree), ForeignKey(Order.id) names Order.
  * CASCADES (newly emitted by Python): relationship(cascade=... delete ...) and backref(..., cascade=...),
    ForeignKey(ondelete="CASCADE" | "SET NULL"), Django ForeignKey / OneToOneField(on_delete=CASCADE |
    SET_NULL) — principal-first like every recognizer's. Django 'app_label.Model' names Model in that app's
    models module (django.contrib's 'auth.Group' binds nothing), 'self' the declaring class.
  * An ORM model or a view this module imports carries the import as a "spec:" scope that
    compose_route_prefixes places ("exact:module:<path>"), so it binds the imported class — never a
    same-named schema class elsewhere — and an import from outside the tree (django.contrib.auth's User)
    binds nothing. An in-tree module whose definition the post-pass cannot follow is "pref:" (that module
    first, else the name rule); an absolute import naming the sync root's own package resolves inside it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


# The persisted relationship vocabulary Track C introduces (whitelist; edge_type is in Cypher LABEL
# position so it can never be a bound parameter — edge_resolve substitutes only from this set).
ALLOWED_EDGE_TYPES = {
    "INHERITS", "IMPLEMENTS", "ROUTES_TO", "REFERENCES", "HAS_MANY",
    "DEPENDS_ON", "HOOKS", "SCHEDULED_AS", "TRIGGERS", "DISPATCHES", "IMPORTS",
    "CASCADES",   # Rails `dependent:` (ruby_rails_edges) + EF `OnDelete` (csharp_edges) + JPA `CascadeType.REMOVE`
                  # (java_edges) + GORM `constraint:OnDelete` (go_edges) + TypeORM `onDelete` / remove `cascade`
                  # (typescript_edges) + SQLAlchemy relationship `cascade=` / ForeignKey `ondelete=` and Django
                  # `on_delete=` (framework_edges); emitted principal-first (a GORM belongs-to's, a TypeORM
                  # `onDelete`'s or a foreign key's principal is the type it references), persisted REVERSED:
                  # (dependent)->(principal)
    "RENDERS",    # Rails view -> partial/template render (ruby_rails_edges _recognize_views, P1-3) — blast-radius for templates
}


class TypedEdge(NamedTuple):
    """A framework/runtime relationship, endpoints as resolvable name+file (not keys).

    src_name/src_file : the OWNING symbol (a class, a handler, or the module stem) + its posix relpath.
    dst_name          : the TARGET symbol name to resolve (base class / handler / provider / receiver).
    edge_type         : one of ALLOWED_EDGE_TYPES.
    conf_hint         : LOCAL (literal, expect a same-file definition) | NAME (a bare name) |
                        DYNAMIC (heuristic/reflection -> always AMBIGUOUS). Final grade is set at push.
    attrs             : edge properties (e.g. {"method":"POST","path":"/x"}); {} when none.
    scope             : where dst_name may resolve. "" (default) = same file first, then the global
                        name index. Go package scopes (go_edges): "package" = the src file's own
                        directory; "import:<path>" = the repo directory that Go import path names;
                        "pkgname:<name>" = directories named <name>; "method" = any :Method whose
                        last segment is dst_name (a method value on an unknown receiver). TypeScript
                        module scopes (typescript_edges): "module:<path>" = the module a relative
                        import names (its root-relative path, no extension; an index file is its
                        directory) and "modsuffix:<path>" = any module whose path ends with <path> (an
                        alias no tsconfig maps) — a handler that is a member of an imported module object
                        resolves there only; "exact:<module scope>" = an imported type or name defined
                        in that module (typescript_edges' finalize found it there) binds there or
                        nowhere; "pref:<module scope>" = that module's own symbol first, else the
                        default rule (finalize could not read the definition; a "modsuffix:" guess never
                        narrows); "spec:<specifier>" / "pref:spec:<specifier>" = an import finalize has
                        not placed in the tree, which binds nothing. A "#..." note after a module path is
                        what finalize follows (a barrel's binding, a default export), never part of the
                        path; an "L<src line>,<dst line>|" prefix (INHERITS / IMPLEMENTS) picks, among
                        same-named nodes of one file, the one declared at that line (0 = no hint).
                        Python uses the same import scopes (this module): a Django view or an ORM
                        model imported from another module is emitted "spec:<python module>" ('.views',
                        'shop.models'; a Django 'app_label.Model' is "spec:<app_label>.models") and
                        compose_route_prefixes places it ("exact:module:<path>", the file that defines it;
                        "pref:module:<path>" when it finds that module of the tree but cannot follow the
                        definition) — an import from outside the tree stays "spec:" and binds nothing, and
                        a post-pass that does not finish clears "spec:" (the name rule); for an ORM
                        principal (on_delete / ondelete / a backref) it names the SRC.
                        A Go scope also confines the SRC to src_file's package: when src_file
                        does not define src_name (a GORM belongs-to's principal), a CASCADES / HAS_MANY
                        / REFERENCES src falls back only to the single same-named :Class in
                        src_file's directory, never to one in another directory
                        (edge_resolve._src_in_package). For such a src (a TypeORM onDelete principal)
                        a TypeScript import scope places it in its module instead.
                        Resolution hint only — never persisted.
    """
    src_name: str
    src_file: str
    dst_name: str
    edge_type: str
    conf_hint: str
    attrs: dict
    scope: str = ""


# ---- decorator / call vocabulary --------------------------------------------
# HTTP route methods (the trailing identifier of the decorator: @app.get -> 'get', @bp.route -> 'route').
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_ROUTE_DECOS = _HTTP_METHODS | {"route", "api_route", "websocket"}
_CLI_DECOS = {"command"}                       # click @cli.command()/@group.command()
# Flask/FastAPI lifecycle & middleware hooks (decorator trailing id).
_HOOK_DECOS = {
    "before_request", "after_request", "before_first_request", "after_this_request",
    "teardown_request", "teardown_appcontext", "errorhandler", "context_processor",
    "on_event", "middleware",
}
_TASK_DECOS = {"task", "shared_task", "periodic_task"}   # Celery
_SCHED_DECOS = {"scheduled_job"}                          # APScheduler @sched.scheduled_job(trigger, ...)
_SCHED_CALLS = {"add_job"}                                # APScheduler sched.add_job(func, trigger, ...)
_HOOK_KWARGS = {"hooks", "event_hooks"}                   # requests hooks={...} / httpx event_hooks={...}
_DI_CALLS = {"depends", "security"}                       # FastAPI Depends()/Security()
_ORM_HAS_MANY = {"relationship", "manytomanyfield"}       # SQLAlchemy / Django
_ORM_REFERENCES = {"foreignkey", "onetoonefield"}         # Django (+ SQLAlchemy ForeignKey inside Column)
_ORM_COLUMNS = {"column", "mapped_column"}                # SQLAlchemy Column(...) / 2.0 mapped_column(...)
# SQLAlchemy 2.0 typed declarations: the annotation wrapper -> whether it is ALWAYS a collection
# (WriteOnlyMapped['Post'] / DynamicMapped['Post'] name the element type directly).
_SQLA_MAPPED = {"Mapped": False, "WriteOnlyMapped": True, "DynamicMapped": True}
_SQLA_COLLECTIONS = {"list", "List", "set", "Set", "Sequence", "MutableSequence", "Collection",
                     "AbstractSet", "MutableSet"}
_SQLA_MAPPINGS = {"dict", "Dict", "Mapping", "MutableMapping"}   # attribute_keyed_dict collections: the VALUE type
_NOT_A_MODEL = {"str", "int", "float", "bool", "bytes", "None", "Any", "object", "dict", "list", "set",
                "tuple", "Decimal", "datetime", "date", "time", "UUID", "uuid"}
_SQLA_CASCADE_DELETE = {"all", "delete", "delete-orphan"}       # relationship(cascade=...) tokens that delete
_FK_ON_DELETE = {"CASCADE", "SET NULL"}                        # ForeignKey(ondelete=...) — TypeORM / GORM parity
_DJ_ON_DELETE = {"CASCADE", "SET_NULL"}                        # Django ForeignKey(on_delete=models.X)

# Route-prefix carriers (compose_route_prefixes). A ROUTER carries a prefix its routes inherit; an APP is a
# mount root with none. Mount calls attach a child router to a parent (router or app) with an optional
# prefix: FastAPI CONCATENATES (parent + mount prefix + the router's own prefix + the route path); Flask's
# register_blueprint url_prefix OVERRIDES the blueprint's own and nests with a single-slash join.
_ROUTER_CTORS = {"APIRouter": "fastapi", "Blueprint": "flask"}
_APP_CTORS = {"FastAPI": "fastapi", "Flask": "flask"}
_MOUNT_CALLS = {"include_router": ("fastapi", "prefix"), "register_blueprint": ("flask", "url_prefix")}
_MOUNT_WORDS = tuple(_MOUNT_CALLS)
_FLASK_URL_PREFIX_POS = 5      # Blueprint(name, import_name, static_folder, static_url_path, template_folder, url_prefix)
_MAX_MOUNT_DEPTH = 12          # a mount chain deeper than this is not followed (cycles are cut separately)
_MAX_PATHS = 32                # composed paths kept per route (a router mounted at many places)
_MAX_IMPORT_HOPS = 6           # re-export hops followed when resolving an imported router
_MAX_EXPANSIONS = 2000         # prefix expansions per mount / include() graph before cycle-cut results are memoized

# Django URLconfs (_UrlConfReader). Route functions count only when this module imports them from Django
# (a local `path` from pathlib / os, or a def of that name, is never one). The value is "is the route a regex".
_DJ_URL_MODULES = {"django.urls", "django.conf.urls"}
_DJ_ROUTE_FUNCS = {"path": False, "re_path": True, "url": True}
# Decorators applied to a view inside urlpatterns (login_required(views.x), cache_page(60)(views.x),
# admin.site.admin_view(view)): the route still runs the wrapped view. Any other call names no view.
_DJ_VIEW_WRAPPERS = frozenset({
    "login_required", "permission_required", "user_passes_test", "staff_member_required", "csrf_exempt",
    "csrf_protect", "ensure_csrf_cookie", "requires_csrf_token", "cache_page", "cache_control", "never_cache",
    "vary_on_cookie", "vary_on_headers", "require_GET", "require_POST", "require_safe", "require_http_methods",
    "xframe_options_exempt", "xframe_options_deny", "xframe_options_sameorigin", "gzip_page", "condition",
    "etag", "last_modified", "sensitive_post_parameters", "atomic", "non_atomic_requests", "admin_view",
})
_DJ_LANG_PREFIX = {"i18n_patterns"}              # adds a language-code prefix it cannot know -> prefix_unresolved
_DJ_LIST_CALLS = {"append": 0, "insert": 1}      # urlpatterns.append(path(...)) / .insert(i, path(...)): the element
_DJ_MAX_DEPTH = 8                                # wrapper / alias / nested-include depth followed


def _trailing_id(node) -> "str | None":
    """The trailing identifier of an expression: Name('x')->'x', Attribute(...attr='get')->'get',
    Call(func=...)-> the func's trailing id. Mirrors sync._deco_name so classification agrees."""
    if isinstance(node, ast.Call):
        node = node.func
    return getattr(node, "attr", None) or getattr(node, "id", None)


def _str_const(node) -> "str | None":
    """A string literal's value, else None (py38+ ast.Constant)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _first_str_arg(call: ast.Call) -> "str | None":
    for a in call.args:
        s = _str_const(a)
        if s is not None:
            return s
    return None


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _methods_from_call(call: ast.Call) -> "list[str] | None":
    """methods=[...] on @app.route/@app.api_route -> uppercased list, else None."""
    m = _kw(call, "methods")
    if isinstance(m, (ast.List, ast.Tuple)):
        out = [s.upper() for s in (_str_const(e) for e in m.elts) if s]
        return out or None
    return None


def _src_text(expr) -> str:
    """Short source text of an expression, for an honest `prefix_unresolved` attr (never evaluated)."""
    parts = []
    node = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))[:120]
    try:
        return ast.unparse(expr)[:120]          # py3.9+
    except Exception:  # noqa: BLE001
        return type(expr).__name__


def _trigger_of(call, pos: int) -> "str | None":
    """An APScheduler trigger: trigger= keyword or the positional at `pos` — a literal ('interval',
    'cron', 'date') or a trigger object's class (CronTrigger(...) -> 'CronTrigger'); else None."""
    if call is None:
        return None
    t = _kw(call, "trigger")
    if t is None and len(call.args) > pos:
        t = call.args[pos]
    if t is None:
        return None
    s = _str_const(t)
    if s:
        return s
    if isinstance(t, ast.Call):
        return _trailing_id(t)
    return None


def _slice_str(sub) -> "str | None":
    """The string key of a subscript `x["k"]` (py3.8 wraps it in ast.Index)."""
    sl = getattr(sub, "slice", None)
    if sl is not None and type(sl).__name__ == "Index":     # py3.8
        sl = getattr(sl, "value", None)
    return _str_const(sl)


# ---- ORM helpers (SQLAlchemy classic + 2.0 typed declarations, Django model fields) --------------
def _parse_expr(text: str):
    """A forward-reference string ("list[Item]", "Customer | None") as an expression, or None."""
    try:
        return ast.parse(text.strip(), mode="eval").body
    except (SyntaxError, ValueError):
        return None


def _subscript_slice(sub):
    sl = getattr(sub, "slice", None)
    if sl is not None and type(sl).__name__ == "Index":     # py3.8
        sl = getattr(sl, "value", None)
    return sl


def _type_name(expr, depth: int = 0) -> "str | None":
    """The model class an annotation element names: Item / models.Item / "Item" / "models.Item" -> 'Item';
    a builtin or plain value type (str, int, Any, datetime, ...) -> None."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and depth < 3:
        return _type_name(_parse_expr(expr.value), depth + 1)
    if isinstance(expr, (ast.Name, ast.Attribute)):
        name = _trailing_id(expr)
        return name if name and name not in _NOT_A_MODEL else None
    return None


def _relation_shape(t, depth: int = 0) -> "tuple | None":
    """(is a collection, element class) of the type inside Mapped[...]: list["Item"] / List[Item] / Set[...] /
    dict[str, "Item"] -> (True, 'Item'); "Customer" / Optional["Customer"] / Customer | None /
    Union[Customer, None] -> (False, 'Customer'); a string is read as the expression it spells. Else None."""
    if depth > 4 or t is None:
        return None
    if isinstance(t, ast.Constant) and isinstance(t.value, str):
        return _relation_shape(_parse_expr(t.value), depth + 1)
    if isinstance(t, ast.Subscript):
        outer = _trailing_id(t.value)
        inner = _subscript_slice(t)
        if outer in _SQLA_COLLECTIONS:
            name = _type_name(inner)
            return (True, name) if name else None
        if outer in _SQLA_MAPPINGS:
            elts = inner.elts if isinstance(inner, ast.Tuple) else []
            name = _type_name(elts[-1]) if elts else None
            return (True, name) if name else None
        if outer == "Optional":
            return _relation_shape(inner, depth + 1)
        if outer == "Union":
            elts = [e for e in (inner.elts if isinstance(inner, ast.Tuple) else [inner]) if not _is_none(e)]
            return _relation_shape(elts[0], depth + 1) if len(elts) == 1 else None
        return None
    if isinstance(t, ast.BinOp) and isinstance(t.op, ast.BitOr):
        sides = [s for s in (t.left, t.right) if not _is_none(s)]
        return _relation_shape(sides[0], depth + 1) if len(sides) == 1 else None
    name = _type_name(t)
    return (False, name) if name else None


def _is_none(e) -> bool:
    return (isinstance(e, ast.Constant) and e.value is None) or (isinstance(e, ast.Name) and e.id == "None")


def _class_ref(expr, owner: "str | None" = None) -> "str | None":
    """The model class a relation call names: "Order" / "app_label.Order" / "pkg.models.Order" -> 'Order',
    Django's "self" -> `owner`, Order / models.Order -> 'Order', SQLAlchemy's lambda: Order -> 'Order'.
    A call result (get_user_model()) or anything else names no class -> None."""
    if isinstance(expr, ast.Lambda):
        expr = expr.body
    s = _str_const(expr)
    if s is not None:
        s = s.strip()
        if s == "self":
            return owner
        name = s.rsplit(".", 1)[-1]
        return name if name.isidentifier() else None
    if isinstance(expr, (ast.Name, ast.Attribute)):
        return _trailing_id(expr)
    return None


def _cascade_rule(expr) -> "str | None":
    """relationship(cascade="all, delete-orphan") -> 'all, delete-orphan' when the setting deletes dependents
    ('all' includes 'delete'); 'save-update, merge' and anything non-literal -> None."""
    s = _str_const(expr)
    if not s:
        return None
    toks = [t.strip().lower() for t in s.split(",") if t.strip()]
    return ", ".join(toks) if _SQLA_CASCADE_DELETE & set(toks) else None


def _fk_table(ref: str) -> str:
    """The table of a ForeignKey string: 'parent.id' -> 'parent', 'schema.parent.id' -> 'parent'."""
    parts = [p for p in ref.strip().split(".") if p]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _camel(table: str) -> "str | None":
    """'order_item' -> 'OrderItem', 'parent' -> 'Parent' (the class Flask-SQLAlchemy's default table name
    comes from); not an identifier -> None."""
    name = "".join(w[:1].upper() + w[1:] for w in table.split("_") if w)
    return name if name.isidentifier() else None


def _table_class(table: str, tables: dict, wider: "dict | None" = None) -> "str | None":
    """The class mapped to `table`: the ONE class of this module whose __tablename__ is `table`, else the one
    class of the tree (`wider`, compose_route_prefixes' map: table -> classes), else the CamelCased name."""
    here = [c for c, t in (tables or {}).items() if t == table]
    if len(here) == 1:
        return here[0]
    there = (wider or {}).get(table) or ()
    if not here and len(there) == 1:
        return next(iter(there))
    return _camel(table)


def _tablename(cls: ast.ClassDef) -> "str | None":
    """A class body's literal `__tablename__ = "x"` (or `__tablename__: str = "x"`), else None."""
    for st in cls.body:
        if isinstance(st, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__"
                                              for t in st.targets):
            return _str_const(st.value)
        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) \
                and st.target.id == "__tablename__":
            return _str_const(st.value)
    return None


def _import_origins(tree) -> dict:
    """local name -> {(level, module, imported name | None)} for every import anywhere in `tree`: `import a.b`
    binds 'a' -> (0, 'a', None); `import a.b as x` -> (0, 'a.b', None); `from .m import n as y` -> (1, 'm', 'n')."""
    out: dict = {}
    for n in (ast.walk(tree) if tree is not None else ()):
        if isinstance(n, ast.Import):
            for a in n.names:
                local = a.asname or a.name.split(".", 1)[0]
                out.setdefault(local, set()).add((0, a.name if a.asname else local, None))
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name != "*":
                    out.setdefault(a.asname or a.name, set()).add((n.level or 0, n.module or "", a.name))
    return out


class _Prefix(NamedTuple):
    """One route-prefix value. `present` is Flask's None-vs-set distinction (an unset url_prefix does
    not override); `text` is the literal part (None when unknown); `unresolved` names the non-literal
    source expressions that were left out of the path."""
    present: bool
    text: "str | None"
    unresolved: tuple


_ABSENT = _Prefix(False, None, ())
_CONFLICT = ("<conflict>",)          # a name bound twice in one module to different things -> never resolved


def _prefix_of(expr) -> _Prefix:
    if expr is None:
        return _ABSENT
    if isinstance(expr, ast.Constant) and expr.value is None:
        return _ABSENT
    s = _str_const(expr)
    if s is not None:
        return _Prefix(True, s, ())
    return _Prefix(True, None, (_src_text(expr),))


class _FrameworkVisitor(ast.NodeVisitor):
    """Walks one module's AST and collects TypedEdges. Tracks class/function context so handler names
    match exactly what the node extractors emit (Class.method for methods, bare for module-level)."""

    def __init__(self, rel: str):
        self.rel = rel
        self.stem = Path(rel).stem            # the Module node's name (== py.stem in both extractors)
        self.class_stack: list = []
        self.method_stack: list = []          # the method names of each enclosing class (self.m targets)
        self.class_nodes: list = []           # the ClassDef of each enclosing class (_class_values, on demand)
        self.func_stack: list = []            # enclosing handler names (for reflection src)
        self.local_stack: list = []           # names bound inside each enclosing def (params, assignments)
        self.edges: list = []
        self.module_defs: set = set()         # module-level def names (a bare callable target is LOCAL)
        self.module_vars: set = set()         # module-level assigned names that are NOT defs (variables)
        self.module_classes: dict = {}        # module-level class -> its method names (Klass.m targets)
        self.module_class_nodes: dict = {}    # module-level class -> its ClassDef (Klass.x lookups)
        self._class_cache: dict = {}          # id(ClassDef) -> (property names, assigned attribute names)
        self._tree = None                     # the module AST (prescan) for the lazy import scan (_is_module)
        self._module_refs = None              # (names only a plain `import` binds, dotted module paths) | None
        self._origins = None                  # local name -> {(level, module, imported name)} (_from_sqlalchemy)
        self.tables: dict = {}                # module-level class -> its literal __tablename__ (ForeignKey targets)
        # route-prefix facts (compose_route_prefixes): name bindings, mounts, and one entry per ROUTES_TO
        self.decls: dict = {}                 # var -> (kind, role, own _Prefix) | _CONFLICT
        self.imports: dict = {}               # local -> (kind, level, module, name) | _CONFLICT
        self.mounts: list = []                # (parent_ref, child_ref, _Prefix | None, kind)
        self.routes: list = []                # (handler, receiver var | None, raw path | None)
        self.syms = ({}, [])                  # _module_symbols (prescan): an ORM target's / a view's import
        self.urls = None                      # _UrlFacts of a Django URLconf, set by finish()

    def prescan(self, tree) -> None:
        """Module-level definitions, so a registration BEFORE the def (sched.add_job(f) above def f)
        still knows `f` is local."""
        self._tree = tree
        self.syms = _module_symbols(tree)
        for st in getattr(tree, "body", ()):
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.module_defs.add(st.name)
            elif isinstance(st, ast.ClassDef):
                self.module_classes[st.name] = _method_names(st)
                self.module_class_nodes[st.name] = st
                table = _tablename(st)
                if table:
                    self.tables[st.name] = table
            elif isinstance(st, (ast.Assign, ast.AnnAssign)):
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    if isinstance(t, ast.Name):
                        self.module_vars.add(t.id)
        self.module_vars -= self.module_defs

    def finish(self, tree) -> None:
        """After the walk: the module's symbol table, and its Django URLconf — one ROUTES_TO per routed view,
        attrs {method '*', path, framework 'django'} (+ paths / url_name / url_names / prefix_unresolved), the
        path composed from this module's own include()s (compose_route_prefixes adds the other modules').
        A URLconf this reader cannot read contributes no route, never costs the module's other edges."""
        try:
            urls = _UrlConfReader(tree, self.syms).read()
            if urls is None:
                return
            self.urls = urls
            facts = _facts_of(self)
            world = _UrlWorld(lambda r: facts if r == self.rel else None, {_module_dotted(self.rel): self.rel})
            world.add_includes(self.rel)
            placed = [(r.dst, r.conf, r.scope) for r in urls.routes]
            attrs = world.route_attrs(self.rel, urls.routes, placed)
        except Exception:  # noqa: BLE001 — an unreadable URLconf shape degrades to "no Django routes here"
            self.urls = None
            return
        for (dst, conf, scope), a in zip(placed, attrs):
            self.routes.append((dst, None, None))                    # never FastAPI-prefixed; keeps the alignment
            self.edges.append(TypedEdge(self.stem, self.rel, dst, "ROUTES_TO", conf, a, scope))

    # -- helpers ----------------------------------------------------------
    def _here(self) -> str:
        """The nearest enclosing symbol name to attribute a module/function-level wiring edge to."""
        return self.func_stack[-1] if self.func_stack else self.stem

    def _owner(self) -> str:
        """The CONTAINER a call-site registration belongs to: the enclosing class, else the module."""
        return self.class_stack[-1] if self.class_stack else self.stem

    def _emit(self, src_name, dst_name, edge_type, conf_hint, attrs=None, scope: str = ""):
        if dst_name:
            self.edges.append(TypedEdge(src_name, self.rel, dst_name, edge_type, conf_hint, attrs or {}, scope))

    def _orm_ref(self, name, expr=None) -> tuple:
        """(class name, scope) of an ORM relation's other model: a class this module defines -> (name, "")
        (same-file first); one it imports — `from app.models.order import Order` (TYPE_CHECKING imports too) /
        `models.Order` through a module import -> (the imported name, "spec:<module>"), which
        compose_route_prefixes places in the tree ("exact:module:<path>": bound there, not to a same-named
        schema class elsewhere; an import from outside the tree binds nothing); anything else -> (name, ""),
        edge_resolve's global rule (a string forward reference, a star import)."""
        if not name or name in self.module_classes:
            return name, ""
        chain = _attr_chain(expr) if isinstance(expr, ast.Attribute) else None
        if chain is not None:
            binds = self.syms[0].get(chain[0]) or []
            got = _member_spec(binds[0], chain[1]) if len(binds) == 1 else None
            return (got[1], "spec:" + got[0]) if got else (name, "")
        binds = self.syms[0].get(name) or []
        if len(binds) == 1 and binds[0][0] == "from":
            _k, level, module, orig = binds[0]
            return orig, "spec:" + _spec(level, module)
        return name, ""

    def _dj_ref(self, expr, owner: str) -> tuple:
        """(class name, scope) of a Django relation field's model. A lazy 'app_label.Model' names Model in that
        app's models module ("spec:<app_label>.models", which compose_route_prefixes places through its
        re-exports), never any same-named class: an app the tree does not hold — django.contrib's 'auth.Group',
        'contenttypes.ContentType' — binds nothing. Model defined in this module, whose directory is that app,
        stays same-file. Anything else as _orm_ref ('self', 'Author', Author, models.Author)."""
        label, dot, model = (_str_const(expr) or "").strip().partition(".")
        if dot and label.isidentifier() and model.isidentifier():
            if model in self.module_classes and label in Path(self.rel).parts[:-1]:
                return model, ""
            return model, "spec:" + label + ".models"
        return self._orm_ref(_class_ref(expr, owner), expr)

    def _callable_target(self, expr) -> "tuple | None":
        """A registered callable -> (dst name, conf hint), or None when it names no symbol (a lambda, a
        literal, a call result). `self.m`/`cls.m` binds the enclosing class's own method (LOCAL) — an
        inherited or dynamic one stays a bare NAME, never a fabricated Class.m; a module-level def is
        LOCAL; anything else (an imported name, `mod.f`) is a NAME resolved at push. A bare name that is
        a VARIABLE — a parameter or assignment of an enclosing def, or a module-level non-def assignment
        (`job = partial(f, 1)`) — names no symbol of its own: resolving it by name would bind whatever
        unrelated def shares the spelling, so it yields nothing. The same holds for `self.x` / `Klass.x`:
        a PROPERTY (`@property def x`) is read as the getter's value, never the getter method (binding it
        would mint an EXTRACTED edge to the getter), and an attribute the class itself ASSIGNS (`self.x =
        ...`, a class-level `x = ...` or field) holds data or a runtime callback, never a symbol named x.
        Only an own METHOD binds (Class.m); a name the class neither defines nor assigns (an inherited
        method, e.g. Qt's `self.close`) stays a bare NAME."""
        if isinstance(expr, ast.Name):
            if expr.id in self.module_defs and not any(expr.id in s for s in self.local_stack):
                return expr.id, "LOCAL"
            if expr.id in self.module_vars or any(expr.id in s for s in self.local_stack):
                return None
            return expr.id, "NAME"
        if isinstance(expr, ast.Attribute):
            base, m = expr.value, expr.attr
            if isinstance(base, ast.Name):
                if base.id in ("self", "cls") and self.class_stack:
                    props, assigned = self._class_values(self.class_nodes[-1])
                    if m in props:
                        return None
                    if m in self.method_stack[-1]:
                        return f"{self.class_stack[-1]}.{m}", "LOCAL"
                    if m in assigned:
                        return None
                    return m, "NAME"
                cls_node = self.module_class_nodes.get(base.id)
                if cls_node is not None:
                    props, assigned = self._class_values(cls_node)
                    if m in props:
                        return None
                    if m in self.module_classes.get(base.id, ()):
                        return f"{base.id}.{m}", "LOCAL"
                    if m in assigned:
                        return None
            return m, "NAME"
        return None

    def _class_values(self, cls: ast.ClassDef) -> tuple:
        """(property names, assigned attribute names) of `cls` — the `self.x` that are values, not methods.
        Computed on first use (only a registration handed `self.x` / `Klass.x` asks), then cached."""
        got = self._class_cache.get(id(cls))
        if got is None:
            got = self._class_cache[id(cls)] = (_property_names(cls), _assigned_attrs(cls))
        return got

    def _is_module(self, expr) -> bool:
        """`expr` is a MODULE object: a name bound only by a plain `import` anywhere in the file (`import
        sqlite3`, `import psycopg2 as pg` — never re-bound by a from-import, a module-level assignment / def /
        class, or an enclosing def's parameter or local), or a dotted path such an import makes reachable
        (`import mysql.connector` -> `mysql.connector`). An attribute OF a module (`flask.template_rendered`,
        a signal object) is not one. The import scan runs once, on the first question."""
        if self._module_refs is None:
            names, paths, froms = _import_bindings(self._tree)
            names -= froms | self.module_vars | self.module_defs | set(self.module_classes)
            self._module_refs = (names, paths)
        names, paths = self._module_refs
        parts: list = []
        node = expr
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in names \
                or any(node.id in s for s in self.local_stack):
            return False
        return not parts or ".".join([node.id, *reversed(parts)]) in paths

    def _bind(self, table: dict, name: str, value) -> None:
        old = table.get(name)
        table[name] = value if (old is None or old == value) else _CONFLICT

    # -- classes: inheritance + ORM --------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef):
        for base in node.bases:
            bid = _trailing_id(base)
            if not bid:
                continue                       # Generic[T], complex bases -> skip
            et = "IMPLEMENTS" if (bid in {"Protocol", "ABC", "ABCMeta"} or bid.endswith("Interface")) \
                else "INHERITS"
            self._emit(node.name, bid, et, "LOCAL")
        self._orm_fields(node)
        self.class_stack.append(node.name)
        self.method_stack.append(_method_names(node))
        self.class_nodes.append(node)
        self.generic_visit(node)
        self.class_nodes.pop()
        self.method_stack.pop()
        self.class_stack.pop()

    def _orm_fields(self, cls: ast.ClassDef):
        """SQLAlchemy/Django relations declared as class attributes (an annotated `x: T = ...` too):
        x = relationship("Other")            -> HAS_MANY
        x = models.ManyToManyField("Tag")    -> HAS_MANY
        x = models.ForeignKey("Author")      -> REFERENCES ('app_label.Author' -> Author, 'self' -> this class)
        x = Column(ForeignKey("parent.id"))  -> REFERENCES (the class whose __tablename__ is 'parent', else the
                                                CamelCased table name; ForeignKey(Parent.id) -> Parent);
                                                mapped_column(ForeignKey(...)) the same.
        SQLAlchemy 2.0 typed declarations (`x: Mapped[...] = relationship(...)`, Mapped imported from SQLAlchemy):
        the annotation gives the target when relationship() names none, and the cardinality — a collection
        (Mapped[list["Item"]] / Set / WriteOnlyMapped['Item'] / dict values) -> HAS_MANY, a scalar
        (Mapped["Customer"] / Optional[...] / "Customer | None") -> REFERENCES. A backref= on a typed
        relationship adds the reverse relation (src = the target class).
        Cascades, emitted principal-first like every recognizer's (persisted reversed by edge_resolve):
        relationship(cascade="all, delete-orphan" | "...delete...") -> CASCADES this class -> target (a
        backref(..., cascade=...) -> target -> this class); ForeignKey(..., ondelete="CASCADE" | "SET NULL")
        and Django ForeignKey / OneToOneField(on_delete=CASCADE | SET_NULL) -> CASCADES referenced class -> this
        class (the referenced class is the principal, as for a TypeORM onDelete)."""
        for stmt in cls.body:
            n = len(self.edges)
            try:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                    tgt = stmt.targets[0] if len(stmt.targets) == 1 else None
                    self._orm_call(cls, stmt.value, tgt.id if isinstance(tgt, ast.Name) else None, None)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Call):
                    self._orm_call(cls, stmt.value, stmt.target.id if isinstance(stmt.target, ast.Name) else None,
                                   stmt.annotation)
            except Exception:  # noqa: BLE001 — one unreadable field never costs the module's other edges
                del self.edges[n:]

    def _orm_call(self, cls: ast.ClassDef, call: ast.Call, attr, ann) -> None:
        raw = _trailing_id(call) or ""
        fn = raw.lower()
        if fn in _ORM_HAS_MANY:
            shape = self._mapped_shape(ann, call) if (raw == "relationship" and ann is not None) else None
            texpr = call.args[0] if call.args else (_kw(call, "argument") or _kw(call, "to"))
            explicit = _class_ref(texpr, cls.name)
            texpr = texpr.body if isinstance(texpr, ast.Lambda) else texpr
            if shape is None:                                         # classic: a relationship is HAS_MANY
                target, scope = self._dj_ref(texpr, cls.name) if fn == "manytomanyfield" \
                    else self._orm_ref(explicit, texpr)
                self._emit(cls.name, target, "HAS_MANY", "NAME", None, scope)
                many = None
            else:                                                     # typed: the annotation says which
                many = shape[0]
                target, scope = self._orm_ref(explicit, texpr) if explicit else self._orm_ref(shape[1])
                self._emit(cls.name, target, "HAS_MANY" if many else "REFERENCES", "NAME", None, scope)
            if fn == "relationship" and target:
                self._sqla_cascades(cls.name, target, scope, call, attr, many)
        elif fn in _ORM_REFERENCES:
            texpr = call.args[0] if call.args else _kw(call, "to")
            dst, scope = self._dj_ref(texpr, cls.name)
            self._emit(cls.name, dst, "REFERENCES", "NAME", None, scope)
            rule = _kw(call, "on_delete")
            if rule is None and len(call.args) > 1:
                rule = call.args[1]                                   # ForeignKey(Author, models.CASCADE)
            rule = _trailing_id(rule) if isinstance(rule, (ast.Name, ast.Attribute)) else None
            if dst and rule in _DJ_ON_DELETE:                         # the referenced model is the principal
                self._emit(dst, cls.name, "CASCADES", "NAME",
                           {"strategy": rule, "via": "django", **({"property": attr} if attr else {})}, scope)
        elif fn in _ORM_COLUMNS:
            fk = self._find_call(call, {"foreignkey"})
            if fk is not None:
                dst, literal, dexpr = self._fk_target(fk)
                dst, scope = (dst, "") if literal else self._orm_ref(dst, dexpr)
                self._emit(cls.name, dst, "REFERENCES", "NAME", {"foreign_key": literal} if literal else {}, scope)
                rule = _str_const(_kw(fk, "ondelete"))
                rule = " ".join(rule.upper().split()) if rule else None
                if dst and rule in _FK_ON_DELETE:                     # the referenced table is the principal
                    extra = {"property": attr} if attr else {}
                    if literal:
                        extra["foreign_key"] = literal
                    self._emit(dst, cls.name, "CASCADES", "NAME", {"strategy": rule, "via": "sqlalchemy", **extra},
                               scope)

    def _sqla_cascades(self, owner: str, target: str, scope: str, call: ast.Call, attr, many) -> None:
        """relationship(cascade=...) and backref(...): the CASCADES they declare, and (typed only) the reverse
        relation a backref creates on the target class. `scope` is the target's import scope (_orm_ref)."""
        rule = _cascade_rule(_kw(call, "cascade"))
        if rule:
            self._emit(owner, target, "CASCADES", "NAME",
                       {"strategy": rule, "via": "sqlalchemy", **({"property": attr} if attr else {})}, scope)
        br = _kw(call, "backref")
        br_call = br if isinstance(br, ast.Call) and (_trailing_id(br) or "") == "backref" else None
        br_name = _str_const(br_call.args[0] if (br_call is not None and br_call.args) else br)
        if br_call is not None:
            rule = _cascade_rule(_kw(br_call, "cascade"))
            if rule:                                                  # the target's collection cascades to us
                self._emit(target, owner, "CASCADES", "NAME",
                           {"strategy": rule, "via": "sqlalchemy", **({"property": br_name} if br_name else {})},
                           scope)
        if many is None or (br_call is None and not br_name) or target == owner:
            return                                                    # classic: the reverse cardinality is unknown
        if not many or _kw(call, "secondary") is not None:            # many-to-one / many-to-many -> a collection
            scalar = br_call is not None and isinstance(_kw(br_call, "uselist"), ast.Constant) \
                and _kw(br_call, "uselist").value is False
            self._emit(target, owner, "REFERENCES" if scalar else "HAS_MANY", "NAME", None, scope)
        else:                                                         # one-to-many -> the reverse is a scalar
            self._emit(target, owner, "REFERENCES", "NAME", None, scope)

    def _fk_target(self, fk: ast.Call) -> tuple:
        """(the class a ForeignKey points at, its string literal or None, the class expression or None):
        ForeignKey("auth_user.id") -> the class declared here with __tablename__ 'auth_user' (compose_route_prefixes
        widens this to the whole tree), else the CamelCased table name; ForeignKey(User.id) /
        ForeignKey(User.__table__.c.id) -> User; ForeignKey(models.User.id) -> User through the module `models`
        (its expression `models.User`, so _orm_ref gives it that module's import scope) — never the module."""
        arg = fk.args[0] if fk.args else _kw(fk, "column")
        ref = _str_const(arg)
        if ref is not None:
            return _table_class(_fk_table(ref), self.tables), ref, None
        chain = _attr_chain(arg) if isinstance(arg, ast.Attribute) else None
        if chain is None:
            return None, None, None
        segs = [chain[0], *chain[1][:-1]]                             # drop the column
        node = arg.value
        while len(segs) > 1 and segs[-1] in ("c", "columns", "__table__"):
            segs.pop()
            node = node.value
        return segs[-1], None, node

    def _mapped_shape(self, ann, call: "ast.Call | None" = None) -> "tuple | None":
        """(is a collection, element class) of a SQLAlchemy 2.0 `Mapped[...]` / `WriteOnlyMapped[...]` /
        `DynamicMapped[...]` annotation (a whole-string annotation too), or None: not that wrapper, a wrapper
        this module does not import from SQLAlchemy (another library's `Mapped`), or a shape it cannot read.
        `call` is the relationship() it annotates (_sqla_by_call)."""
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            ann = _parse_expr(ann.value)
        if not isinstance(ann, ast.Subscript):
            return None
        wrapper = _trailing_id(ann.value)
        if wrapper not in _SQLA_MAPPED or not (self._from_sqlalchemy(ann.value) or self._sqla_by_call(ann.value, call)):
            return None
        shape = _relation_shape(_subscript_slice(ann))
        if shape is None:
            return None
        return (True, shape[1]) if _SQLA_MAPPED[wrapper] else shape

    def _from_sqlalchemy(self, expr) -> bool:
        """`expr` (a `Mapped` name or `so.Mapped`) is SQLAlchemy's: imported from sqlalchemy (any submodule),
        an attribute of a module alias of it (`import sqlalchemy.orm as so`), a project re-export (a relative
        import, or an absolute one from a package on this file's own path), or a name this file never binds
        while it imports sqlalchemy (a star import). Anything imported from another library is not."""
        if self._origins is None:
            self._origins = _import_origins(self._tree)
        root = expr
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return False
        if root.id in self.module_defs or root.id in self.module_classes or root.id in self.module_vars:
            return False                                              # this file's own `Mapped`
        got = self._origins.get(root.id)
        if not got:
            imports_sqla = any(not level and module.split(".")[0] == "sqlalchemy"
                               for found in self._origins.values() for level, module, _o in found) \
                or any(not level and module.split(".")[0] == "sqlalchemy" for level, module in self.syms[1])
            return root is expr and imports_sqla
        own_dirs = set(Path(self.rel).parts[:-1])
        for level, module, _orig in got:
            top = module.split(".")[0] if module else ""
            if top == "sqlalchemy":
                continue
            if root is expr and (level or top in own_dirs):
                continue
            return False
        return True

    def _sqla_by_call(self, mapped, call) -> bool:
        """A `Mapped` name this file imports from a project module elsewhere in the tree (`from core.db import
        Base, Mapped, relationship` in models/order.py) is SQLAlchemy's when the relationship() it annotates is
        (_from_sqlalchemy: SQLAlchemy needs its own Mapped there to read the annotation at all), or when this
        file imports that function from the very module it imports `Mapped` from (both re-exported together).
        Never this file's own `Mapped`, and never with a relationship() from another library (neomodel)."""
        if call is None or not isinstance(mapped, ast.Name) or mapped.id in self.module_defs \
                or mapped.id in self.module_classes or mapped.id in self.module_vars:
            return False
        if self._from_sqlalchemy(call.func):
            return True
        if not isinstance(call.func, ast.Name):
            return False
        mine = {(level, module) for level, module, _o in self._origins.get(mapped.id) or ()}
        theirs = {(level, module) for level, module, _o in self._origins.get(call.func.id) or ()}
        return len(mine) == 1 and mine == theirs and all(module for _l, module in mine)

    @staticmethod
    def _find_call(call: ast.Call, names: set) -> "ast.Call | None":
        for a in list(call.args) + [k.value for k in call.keywords]:
            if isinstance(a, ast.Call) and (_trailing_id(a) or "").lower() in names:
                return a
        return None

    # -- name bindings (route-prefix facts only; they emit no edge) -------
    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            self._decl(node.targets[0].id, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            self._decl(node.target.id, node.value)
        self.generic_visit(node)

    def _decl(self, var: str, call: ast.Call) -> None:
        ctor = _trailing_id(call) or ""
        if ctor in _ROUTER_CTORS:
            kind = _ROUTER_CTORS[ctor]
            if kind == "fastapi":
                own = _prefix_of(_kw(call, "prefix"))
                own = own if own.present else _Prefix(True, "", ())      # APIRouter(prefix="") default
            else:
                p = _kw(call, "url_prefix")
                if p is None and len(call.args) > _FLASK_URL_PREFIX_POS:
                    p = call.args[_FLASK_URL_PREFIX_POS]
                own = _prefix_of(p)
            self._bind(self.decls, var, (kind, "router", own))
        elif ctor in _APP_CTORS:
            self._bind(self.decls, var, (_APP_CTORS[ctor], "app", _ABSENT))

    def visit_Import(self, node: ast.Import):
        for a in node.names:
            if a.asname:
                self._bind(self.imports, a.asname, ("module", 0, a.name, None))
            else:
                top = a.name.split(".", 1)[0]
                self._bind(self.imports, top, ("module", 0, top, None))

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for a in node.names:
            if a.name == "*":
                continue
            self._bind(self.imports, a.asname or a.name, ("from", node.level or 0, node.module or "", a.name))

    # -- functions: routes / hooks / tasks / DI / receivers --------------
    def _func(self, node):
        name = node.name if not self.class_stack else f"{self.class_stack[-1]}.{node.name}"
        for deco in node.decorator_list:
            self._classify_decorator(deco, name)
        self._depends(node, name)
        self.func_stack.append(name)
        self.local_stack.append(_bound_names(node))
        self.generic_visit(node)
        self.local_stack.pop()
        self.func_stack.pop()

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def _classify_decorator(self, deco, handler: str):
        trailing = (_trailing_id(deco) or "").lower()
        call = deco if isinstance(deco, ast.Call) else None
        if trailing in _ROUTE_DECOS:
            path = _first_str_arg(call) if call else None
            if trailing in ("route", "api_route", "websocket"):
                methods = _methods_from_call(call) if call else None
                method = ",".join(methods) if methods else ("WS" if trailing == "websocket" else "*")
            else:
                method = trailing.upper()
            attrs = {"method": method}
            if path:
                attrs["path"] = path
            self.routes.append((handler, _receiver_var(deco), path))
            self._emit(self.stem, handler, "ROUTES_TO", "LOCAL", attrs)
        elif trailing in _CLI_DECOS:
            path = _first_str_arg(call) if call else None
            self.routes.append((handler, None, None))                # never prefixed; keeps the alignment
            self._emit(self.stem, handler, "ROUTES_TO", "LOCAL",
                       {"method": "CLI", "path": path or handler})
        elif trailing in _HOOK_DECOS:
            # @app.on_event('startup') -> 'startup'; @app.errorhandler(404) (non-str arg) / bare
            # @app.before_request -> the decorator name.
            event = (_first_str_arg(call) if call else None) or trailing
            self._emit(self.stem, handler, "HOOKS", "LOCAL", {"event": event})
        elif trailing in _TASK_DECOS:
            self._emit(self.stem, handler, "SCHEDULED_AS", "LOCAL", {"task": handler})
        elif trailing in _SCHED_DECOS:                   # APScheduler @sched.scheduled_job('interval')
            trig = _trigger_of(call, 0)
            self._emit(self.stem, handler, "SCHEDULED_AS", "LOCAL", {"trigger": trig} if trig else {})
        elif trailing == "receiver":                    # Django @receiver(post_save, sender=X)
            sig = _trailing_id(call.args[0]) if (call and call.args) else None
            self._emit(self.stem, handler, "TRIGGERS", "NAME", {"signal": sig} if sig else {})

    def _depends(self, node, handler: str):
        """FastAPI dependency injection: a parameter whose default (or Annotated marker) is
        Depends(provider)/Security(provider) -> DEPENDS_ON handler->provider."""
        a = node.args
        for default in list(a.defaults) + list(a.kw_defaults):
            self._depends_from(default, handler)
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            ann = getattr(arg, "annotation", None)
            if isinstance(ann, ast.Subscript) and (_trailing_id(ann.value) or "") == "Annotated":
                sl = ann.slice.value if isinstance(ann.slice, ast.Index) else ann.slice   # py38/py39+
                elts = sl.elts if isinstance(sl, ast.Tuple) else []
                for e in elts:
                    self._depends_from(e, handler)

    def _depends_from(self, expr, handler: str):
        if isinstance(expr, ast.Call) and (_trailing_id(expr) or "").lower() in _DI_CALLS and expr.args:
            provider = _trailing_id(expr.args[0])
            self._emit(handler, provider, "DEPENDS_ON", "LOCAL")

    # -- module/function-level calls: signals + reflection + registrations --
    def visit_Call(self, node: ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            attr = f.attr
            if attr == "connect" and node.args:            # blinker/Flask/Django/Qt  sig.connect(handler)
                self._connect(node, f)
            elif attr == "on" and len(node.args) >= 2:     # pyee/emitter  ee.on("evt", handler)
                tgt = self._callable_target(node.args[1])
                if tgt:
                    ev = _str_const(node.args[0])
                    self._emit(self.stem, tgt[0], "TRIGGERS", tgt[1], {"event": ev} if ev else {})
            elif attr in _SCHED_CALLS:                     # APScheduler  sched.add_job(func, trigger)
                self._add_job(node)
            elif attr == "register_hook":                  # requests  r.register_hook("response", h)
                self._register_hook(node)
            elif attr in ("append", "extend") and isinstance(f.value, ast.Subscript) \
                    and isinstance(f.value.value, ast.Attribute) and f.value.value.attr == "hooks":
                self._hooks_append(node, f)                # requests  s.hooks["response"].append(h)
            elif attr in _MOUNT_CALLS and node.args:       # include_router / register_blueprint
                kind, kw = _MOUNT_CALLS[attr]
                p = _kw(node, kw)
                self.mounts.append((_ref(f.value), _ref(node.args[0]),
                                    _prefix_of(p) if p is not None else None, kind))
        for k in node.keywords:                            # requests.get(..., hooks={"response": [f]})
            if k.arg in _HOOK_KWARGS:
                for ev, h in _hook_mapping(k.value):
                    self._emit_hook(ev, h)
        # reflection: getattr(obj, "name")()  -> DISPATCHES (heuristic, AMBIGUOUS-only)
        if isinstance(f, ast.Call) and (_trailing_id(f) or "") == "getattr" and len(f.args) >= 2:
            dst = _str_const(f.args[1])
            if dst:
                self._emit(self._here(), dst, "DISPATCHES", "DYNAMIC")
        self.generic_visit(node)

    def _connect(self, node: ast.Call, f: ast.Attribute) -> None:
        """sig.connect(handler, ...): the handler through the same variable guard as every registration (it
        was once taken verbatim, so `sqlite3.connect(path)` bound any symbol named `path` — ISS-build:015). A
        MODULE's connect() (`sqlite3.connect(path)`, `psycopg2.connect(dsn)`, `mysql.connector.connect(...)`)
        is PEP 249's connection constructor, never a signal registration -> nothing, whatever its argument."""
        if self._is_module(f.value):
            return
        tgt = self._callable_target(node.args[0])
        if tgt:
            sig = _trailing_id(f.value)
            self._emit(self.stem, tgt[0], "TRIGGERS", tgt[1], {"signal": sig} if sig else {})

    def _add_job(self, node: ast.Call) -> None:
        """sched.add_job(func, trigger=None, ...) / add_job(func=..., trigger=...): the job callable, or
        APScheduler's textual 'package.module:callable' reference. A lambda or a literal non-reference
        names no symbol -> nothing."""
        fn = _kw(node, "func")
        from_pos = fn is None
        if from_pos:
            fn = node.args[0] if node.args else None
        if fn is None:
            return
        ref = _str_const(fn)
        if ref is not None:
            if ":" not in ref:
                return                                     # not APScheduler's 'module:callable' form
            name = ref.rsplit(":", 1)[1].rsplit(".", 1)[-1].strip()
            if not name.isidentifier():
                return
            tgt = (name, "NAME")
        else:
            tgt = self._callable_target(fn)
        if not tgt:
            return
        trig = _trigger_of(node, 1 if from_pos else 0)
        self._emit(self._owner(), tgt[0], "SCHEDULED_AS", tgt[1], {"trigger": trig} if trig else {})

    def _register_hook(self, node: ast.Call) -> None:
        """requests' Request.register_hook(event, hook): the event must be a string LITERAL (torch's
        one-argument tensor.register_hook(fn) and a computed event are not this shape -> nothing)."""
        ev = node.args[0] if node.args else _kw(node, "event")
        hk = node.args[1] if len(node.args) >= 2 else _kw(node, "hook")
        ev_s = _str_const(ev) if ev is not None else None
        if ev_s and hk is not None:
            self._emit_hook(ev_s, hk)

    def _hooks_append(self, node: ast.Call, f: ast.Attribute) -> None:
        ev = _slice_str(f.value)
        if not ev or not node.args:
            return
        arg = node.args[0]
        items = arg.elts if (f.attr == "extend" and isinstance(arg, (ast.List, ast.Tuple))) else \
            ([arg] if f.attr == "append" else [])
        for h in items:
            self._emit_hook(ev, h)

    def _emit_hook(self, event: str, handler_expr) -> None:
        tgt = self._callable_target(handler_expr)
        if tgt:
            self._emit(self._owner(), tgt[0], "HOOKS", tgt[1], {"event": event})


def _method_names(cls: ast.ClassDef) -> set:
    return {s.name for s in cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}


_PROPERTY_ACCESSORS = {"setter", "getter", "deleter"}      # @p.setter / @p.getter / @p.deleter (p is a property)


def _is_property(fn) -> bool:
    """A def a decorator turns into a PROPERTY: @property, @functools.cached_property, @hybrid_property,
    @abstractproperty, ... (a trailing id ending in 'property'), or a @p.setter / .getter / .deleter."""
    for d in fn.decorator_list:
        t = (_trailing_id(d) or "").lower()
        if t.endswith("property") or t in _PROPERTY_ACCESSORS:
            return True
    return False


def _property_names(cls: ast.ClassDef) -> set:
    """The names of `cls`'s properties — reading one yields the getter's VALUE, never a callable."""
    return {s.name for s in cls.body
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_property(s)}


def _assigned_attrs(cls: ast.ClassDef) -> set:
    """Attribute names `cls` itself ASSIGNS: a class-level `x = ...` / `x: T` (a field) and every `self.x` /
    `cls.x` store in its methods (assignment, augmented, unpacking, a for/with target), nested classes excluded.
    Reading one yields whatever was stored — data or a runtime callback — never a symbol named x."""
    out: set = set()
    for st in cls.body:
        if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                out.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    stack = list(cls.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ClassDef):
            continue                                        # a nested class's `self` is another object
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) \
                and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
            out.add(node.attr)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _import_bindings(tree) -> tuple:
    """(names a plain `import` binds, the dotted module paths it makes reachable, names a from-import binds)
    anywhere in `tree`, a function-level import included. `import a.b.c` binds `a` and reaches `a.b` and
    `a.b.c`; `import a.b as x` binds `x`. An absent tree yields nothing."""
    names: set = set()
    paths: set = set()
    froms: set = set()
    for n in (ast.walk(tree) if tree is not None else ()):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    names.add(a.asname)
                    continue
                parts = a.name.split(".")
                names.add(parts[0])
                paths.update(".".join(parts[:i]) for i in range(2, len(parts) + 1))
        elif isinstance(n, ast.ImportFrom):
            froms.update(a.asname or a.name for a in n.names)
    return names, paths, froms


def _bound_names(fn) -> set:
    """VARIABLE names a def binds in its OWN scope: its parameters, every name its body assigns / loops
    / deletes, and its nested def/class names (never node-ified, so resolving one by name would bind an
    unrelated symbol). A function-level IMPORT is deliberately not a variable — it names a real symbol
    elsewhere. Nested scopes (defs, classes, lambdas) are not entered."""
    a = fn.args
    out = {x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
    out |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _module_statements(body):
    """Every statement that runs at module level: the body, and the bodies of module-level if / try / with /
    for / while blocks (an `if settings.DEBUG: urlpatterns += [...]`), never a def or class body."""
    for st in body:
        yield st
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            inner = getattr(st, field, None)
            if isinstance(inner, list):
                yield from _module_statements(inner)
        for h in getattr(st, "handlers", None) or ():
            yield from _module_statements(h.body)


def _store_names(target) -> list:
    """The names an assignment / for / with target binds: `a` -> ['a'], `a, (b, *c)` -> ['a', 'b', 'c'];
    an attribute or subscript target binds no name."""
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)] \
        if target is not None else []


def _header_bindings(st) -> list:
    """The VARIABLE names a module-level statement binds besides a def / class / import / `name = value`: a for
    target, a with ... as target, an unpacking assignment, an except ... as name, a match capture, and a walrus
    (:=) outside its nested blocks. Each holds a runtime value, never a symbol of its own name."""
    out: list = []
    if isinstance(st, (ast.For, ast.AsyncFor)):
        out += _store_names(st.target)
    elif isinstance(st, (ast.With, ast.AsyncWith)):
        for item in st.items:
            out += _store_names(item.optional_vars)
    elif isinstance(st, ast.Assign):
        out += [n for t in st.targets if not isinstance(t, ast.Name) for n in _store_names(t)]
    for h in getattr(st, "handlers", None) or ():
        if getattr(h, "name", None):
            out.append(h.name)
    for case in getattr(st, "cases", None) or ():                    # match (py3.10+)
        out += [n for p in ast.walk(case.pattern) for n in (getattr(p, "name", None), getattr(p, "rest", None))
                if isinstance(n, str)]
    stack = [c for f, c in ast.iter_fields(st)
             if f not in ("body", "orelse", "finalbody", "handlers", "cases")]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, ast.NamedExpr):
            out += _store_names(node.target)
            stack.append(node.value)
        elif isinstance(node, ast.AST) and not isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef,
                                                                 ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))
    return out


def _module_symbols(tree) -> tuple:
    """({name: [binding, ...]}, [star-import (level, module)]) for the names a module binds at module level, in
    source order. A binding is ("def",) for a def / class, ("from", level, module, imported name), ("import",
    dotted module the name stands for), or ("alias", value expression) for an assignment (None when the value
    is not one plain expression: an augmented assignment, or a variable a for / with / unpacking / except /
    match / walrus binds — _header_bindings — so a route handed `handler` from `for handler in ...` names no
    view, never a same-named def elsewhere)."""
    syms: dict = {}
    stars: list = []
    for st in _module_statements(getattr(tree, "body", ())):
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            syms.setdefault(st.name, []).append(("def",))
        elif isinstance(st, ast.ImportFrom):
            for a in st.names:
                if a.name == "*":
                    stars.append((st.level or 0, st.module or ""))
                else:
                    syms.setdefault(a.asname or a.name, []).append(("from", st.level or 0, st.module or "", a.name))
        elif isinstance(st, ast.Import):
            for a in st.names:
                local = a.asname or a.name.split(".", 1)[0]
                syms.setdefault(local, []).append(("import", a.name if a.asname else local))
        elif isinstance(st, (ast.Assign, ast.AnnAssign)) and st.value is not None:
            for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                if isinstance(t, ast.Name):
                    syms.setdefault(t.id, []).append(("alias", st.value))
        elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name):
            syms.setdefault(st.target.id, []).append(("alias", None))
        if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for name in _header_bindings(st):
                syms.setdefault(name, []).append(("alias", None))
    return syms, stars


def _spec(level: int, module: str) -> str:
    """An import's module as one string: (1, 'views') -> '.views', (0, 'polls.views') -> 'polls.views', (1, '') -> '.'."""
    return "." * (level or 0) + (module or "")


def _unspec(spec: str) -> tuple:
    """The inverse of _spec: '..x.y' -> (2, 'x.y')."""
    s = str(spec or "")
    level = len(s) - len(s.lstrip("."))
    return level, s[level:]


def _member_spec(binding, rest: list) -> "tuple | None":
    """(module spec, member name) of `a.b.c` whose root `a` has this import binding: `from . import views` +
    views.detail -> ('.views', 'detail'); `import polls.views` + polls.views.detail -> ('polls.views', 'detail').
    None when the root is not an import or nothing follows it."""
    if not rest:
        return None
    if binding[0] == "from":
        _k, level, module, orig = binding
        base = _spec(level, f"{module}.{orig}" if module else orig)
    elif binding[0] == "import":
        base = binding[1]
    else:
        return None
    return ".".join([base, *rest[:-1]]), rest[-1]


def _attr_chain(expr) -> "tuple | None":
    """`a.b.c` -> ('a', ['b', 'c']); anything not rooted at a Name -> None."""
    rest: list = []
    node = expr
    while isinstance(node, ast.Attribute):
        rest.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return node.id, list(reversed(rest))


def _unwrap_view(expr, depth: int = 0):
    """The view a urlpatterns entry runs: X.as_view(...) -> X, login_required(X) / cache_page(60)(X) -> X
    (the decorators in _DJ_VIEW_WRAPPERS); any other call -> None (a factory's result names no view)."""
    while isinstance(expr, ast.Call) and depth < _DJ_MAX_DEPTH:
        depth += 1
        f = expr.func
        if isinstance(f, ast.Attribute) and f.attr == "as_view":
            expr = f.value
        elif (_trailing_id(f) or "") in _DJ_VIEW_WRAPPERS and expr.args:
            expr = expr.args[0]                   # login_required(v) / cache_page(60)(v): the curried form too
        else:
            return None
    return None if isinstance(expr, ast.Call) else expr


def _scope_module(rel: str) -> str:
    """A file as edge_resolve's module scopes name it (edge_resolve._module_path): its root-relative path without
    the extension, an `index` file naming its directory — 'polls/views.py' -> 'polls/views'."""
    p = str(rel or "").replace("\\", "/")
    head, _, base = p.rpartition("/")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if stem == "index":
        return head
    return f"{head}/{stem}" if head else stem


def _placed_scope(where: str, exact: bool) -> str:
    """The import scope of a symbol placed in module `where`: "exact:module:<M>" when its definition was found
    there, "pref:module:<M>" when only the module was (that module first, else the global rule); "" (the global
    rule) for a root-level index.py, which edge_resolve's module path names as '' and so could never match."""
    m = _scope_module(where)
    return (("exact:" if exact else "pref:") + "module:" + m) if m else ""


class _Seg(NamedTuple):
    """One URL pattern string: a path() route or a re_path() / url() regex; `text` None when not a literal
    (then `unresolved` names the expression, never guessed)."""
    regex: bool
    text: "str | None"
    unresolved: tuple


class _DjRoute(NamedTuple):
    """One Django ROUTES_TO edge as its module emitted it: the pattern list holding it, its own pattern, its
    url name, and the view endpoint (dst, conf_hint, scope) before the post-pass places an imported view."""
    list_id: str
    seg: _Seg
    url_name: "str | None"
    dst: str
    conf: str
    scope: str


class _UrlFacts(NamedTuple):
    """A module's URLconf facts: pattern lists (a module-level name like `urlpatterns`, or '#<n>' for an inline
    include([...]) / i18n_patterns(...)) -> items ("route", index into `routes`) | ("include", _Seg, namespace,
    target, ask the target's app_name); the routes in edge order; the module's `app_name`."""
    lists: dict
    routes: list
    app_name: "str | None"


class _UrlConfReader:
    """Django URLconf -> ROUTES_TO, one edge per path() / re_path() / url() entry whose view names a symbol:
    src = this module (as every Python route), dst = a function view or a class-based view's CLASS
    (X.as_view()). A view defined in this file is LOCAL (EXTRACTED at push); an imported one is NAME with a
    "spec:<module>" scope that compose_route_prefixes places in the tree ("exact:module:<path>", INFERRED) —
    unplaced (outside the tree, or no post-pass) it binds nothing; a name bound nowhere (a star import) is
    NAME with no scope, edge_resolve's global rule. include() composes the path and the url namespace.

    A pattern list is `urlpatterns`, an inline include([...]) / i18n_patterns(...), a module-level list holding
    a path() / re_path() / url() entry, or a list one of those includes or concatenates; any other module-level
    name (`debug = list(api_patterns)`) is not one, so it never adds a path to the routes it mentions."""

    def __init__(self, tree, syms: tuple):
        self.tree = tree
        self.syms, self.stars = syms
        self.lists: dict = {}
        self.routes: list = []
        self._inline = 0
        self._seen: set = set()
        self.funcs = self._django_funcs()

    def _django_funcs(self) -> dict:
        """local name -> 'path' | 're_path' | 'url' | 'include' for the names every binding of which imports
        that function from Django (a star import from django.urls counts for a name this module never binds)."""
        out: dict = {}
        names = set(_DJ_ROUTE_FUNCS) | {"include"}
        for local, binds in self.syms.items():
            if binds and all(b[0] == "from" and not b[1] and b[2] in _DJ_URL_MODULES and b[3] in names
                             for b in binds) and len({b[3] for b in binds}) == 1:
                out[local] = binds[0][3]
        if any(not lv and m in _DJ_URL_MODULES for lv, m in self.stars):
            for n in names:
                if n not in self.syms:
                    out.setdefault(n, n)
        return out

    def _module_of(self, root: str, rest: list) -> "str | None":
        """The dotted module `root.rest...` names through root's single import binding, else None."""
        binds = self.syms.get(root) or []
        if len(binds) != 1:
            return None
        b = binds[0]
        if b[0] == "from" and not b[1]:
            return ".".join([f"{b[2]}.{b[3]}" if b[2] else b[3], *rest])
        if b[0] == "import":
            return ".".join([b[1], *rest])
        return None

    def _func(self, f) -> "str | None":
        """The Django URL function `f` calls: a name bound to it, or `urls.path` / `django.urls.path` through a
        module import of django.urls / django.conf.urls."""
        if isinstance(f, ast.Name):
            return self.funcs.get(f.id)
        if not isinstance(f, ast.Attribute) or f.attr not in (set(_DJ_ROUTE_FUNCS) | {"include"}):
            return None
        chain = _attr_chain(f.value)
        return f.attr if chain is not None and self._module_of(*chain) in _DJ_URL_MODULES else None

    def _imports_django_urls(self) -> bool:
        return bool(self.funcs) or any(
            (b[0] == "from" and not b[1] and f"{b[2]}.{b[3]}" in _DJ_URL_MODULES)
            or (b[0] == "import" and (b[1] in _DJ_URL_MODULES or b[1] == "django"))
            for bs in self.syms.values() for b in bs)

    # -- reading ----------------------------------------------------------
    def read(self) -> "_UrlFacts | None":
        if not self._imports_django_urls():
            return None
        app = None
        for st in _module_statements(getattr(self.tree, "body", ())):
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and st.value is not None:
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    if not isinstance(t, ast.Name):
                        continue
                    if t.id == "app_name" and _str_const(st.value) is not None:
                        app = _str_const(st.value)
                    self._items(st.value, t.id)
            elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and isinstance(st.op, ast.Add):
                self._items(st.value, st.target.id)
            elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) \
                    and isinstance(st.value.func, ast.Attribute) and isinstance(st.value.func.value, ast.Name):
                call, owner = st.value, st.value.func.value.id
                if call.func.attr == "extend" and call.args:
                    self._items(call.args[0], owner)
                elif call.func.attr in _DJ_LIST_CALLS and len(call.args) > _DJ_LIST_CALLS[call.func.attr]:
                    self._element(call.args[_DJ_LIST_CALLS[call.func.attr]], owner)
        lists = self._pattern_lists()
        if not lists:
            return None
        return _UrlFacts(lists, self.routes, app)

    def _pattern_lists(self) -> dict:
        """The lists that are URL pattern lists (see the class docstring), each with only its items."""
        keep = {lid for lid, items in self.lists.items()
                if lid == "urlpatterns" or lid.startswith("#") or any(it[0] == "route" or it[5] for it in items)}
        todo = list(keep)
        while todo:
            for it in self.lists.get(todo.pop(), ()):
                if it[0] == "include" and it[3][0] == "list" and it[3][1] not in keep:
                    keep.add(it[3][1])
                    todo.append(it[3][1])
        return {lid: items for lid, items in self.lists.items() if lid in keep and items}

    def _add(self, owner: str, item) -> None:
        self.lists.setdefault(owner, []).append(item)

    def _new_list(self) -> str:
        self._inline += 1
        lid = f"#{self._inline}"
        self.lists.setdefault(lid, [])
        return lid

    def _items(self, expr, owner: str, depth: int = 0) -> None:
        """The pattern-list items an expression contributes to the list `owner`."""
        if depth > _DJ_MAX_DEPTH or expr is None:
            return
        if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
            for e in expr.elts:
                self._element(e, owner, depth + 1)
        elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            self._items(expr.left, owner, depth + 1)
            self._items(expr.right, owner, depth + 1)
        elif isinstance(expr, ast.Starred):
            self._items(expr.value, owner, depth + 1)
        elif isinstance(expr, ast.Name):
            if expr.id != owner:                          # `urlpatterns = urlpatterns + [...]` adds nothing new
                target = self._list_target(expr)
                if target is not None:
                    self._add(owner, ("include", _Seg(False, "", ()), None, target, False, False))
        elif isinstance(expr, ast.Call):
            if self._func(expr.func) in _DJ_ROUTE_FUNCS:
                self._element(expr, owner, depth + 1)
            elif (_trailing_id(expr) or "") in _DJ_LANG_PREFIX:
                lid = self._new_list()
                for a in expr.args:
                    self._element(a, lid, depth + 1)
                self._add(owner, ("include", _Seg(False, None, (_trailing_id(expr),)), None, ("list", lid),
                                  False, False))
            elif self._func(expr.func) is None:           # format_suffix_patterns(urlpatterns), do_urls(...)
                for a in expr.args:
                    self._items(a, owner, depth + 1)

    def _element(self, e, owner: str, depth: int = 0) -> None:
        if not isinstance(e, ast.Call) or self._func(e.func) not in _DJ_ROUTE_FUNCS:
            self._items(e, owner, depth)
            return
        if id(e) in self._seen:
            return
        self._seen.add(id(e))
        regex = _DJ_ROUTE_FUNCS[self._func(e.func)]
        route = e.args[0] if e.args else (_kw(e, "route") or _kw(e, "regex"))
        view = e.args[1] if len(e.args) > 1 else _kw(e, "view")
        name = _str_const(_kw(e, "name")) or (_str_const(e.args[3]) if len(e.args) > 3 else None)
        text = _str_const(route)
        seg = _Seg(regex, text, () if (text is not None or route is None) else (_src_text(route),))
        if isinstance(view, ast.Call) and self._func(view.func) == "include":
            self._include(view, seg, owner, depth)
            return
        target = self._view(view)
        if target is None:
            return
        dst, conf, scope = target
        self._add(owner, ("route", len(self.routes)))
        self.routes.append(_DjRoute(owner, seg, name, dst, conf, scope))

    def _include(self, call: ast.Call, seg: _Seg, owner: str, depth: int) -> None:
        """path(prefix, include(...)): a module ('polls.urls' / an imported urls module), a pattern list (a
        module-level name, an imported one, an inline [...]), or a (that, app_name) pair; namespace= names it."""
        arg = call.args[0] if call.args else _kw(call, "arg")
        ns = _str_const(_kw(call, "namespace")) or (_str_const(call.args[1]) if len(call.args) > 1 else None)
        if isinstance(arg, ast.Tuple) and len(arg.elts) in (2, 3) and _str_const(arg.elts[1]) is not None:
            ns = ns or _str_const(arg.elts[1])
            arg = arg.elts[0]
        if isinstance(arg, (ast.List, ast.Tuple)):
            lid = self._new_list()
            self._items(arg, lid, depth + 1)
            target = ("list", lid)
        elif _str_const(arg) is not None:
            target = ("module", _str_const(arg).strip())
        else:
            target = self._list_target(arg)
        if target is not None:
            self._add(owner, ("include", seg, ns, target, ns is None, True))

    def _list_target(self, expr) -> "tuple | None":
        """An included object: this module's pattern list ("list", name), or an imported module / list
        ("import", module spec, name) the post-pass resolves. `router.urls` / `admin.site.urls` -> None."""
        if isinstance(expr, ast.Name):
            binds = self.syms.get(expr.id) or []
            if binds and all(b[0] == "alias" for b in binds):
                return ("list", expr.id)
            if len(binds) == 1 and binds[0][0] == "from":
                _k, level, module, orig = binds[0]
                return ("import", _spec(level, module), orig)
            if len(binds) == 1 and binds[0][0] == "import":
                return ("module", binds[0][1])
            return None
        chain = _attr_chain(expr)
        binds = (self.syms.get(chain[0]) or []) if chain is not None else []
        got = _member_spec(binds[0], chain[1]) if len(binds) == 1 else None
        return ("import", got[0], got[1]) if got else None

    def _view(self, expr, depth: int = 0) -> "tuple | None":
        """(dst, conf_hint, scope) of a view expression, or None when it names no symbol (a lambda, a factory
        call, a variable, `admin.site.urls` / `router.urls` — an included URLconf object, not a view)."""
        expr = _unwrap_view(expr)
        if expr is None or depth > _DJ_MAX_DEPTH:
            return None
        if isinstance(expr, ast.Name):
            binds = self.syms.get(expr.id) or []
            if any(b[0] == "def" for b in binds):
                return expr.id, "LOCAL", ""
            if len(binds) == 1 and binds[0][0] == "from":
                _k, level, module, orig = binds[0]
                return orig, "NAME", "spec:" + _spec(level, module)
            if len(binds) == 1 and binds[0][0] == "alias" and binds[0][1] is not None:
                return self._view(binds[0][1], depth + 1)       # detail = DetailView.as_view() above
            if binds:
                return None                                   # a module, a variable, or bound two ways
            return expr.id, "NAME", ""                        # bound nowhere here: a star import's name
        chain = _attr_chain(expr)
        if chain is None or chain[1][-1:] == ["urls"]:
            return None
        binds = self.syms.get(chain[0]) or []
        got = _member_spec(binds[0], chain[1]) if len(binds) == 1 else None
        if got is None:
            return None
        return got[1], "NAME", "spec:" + got[0]


def _receiver_var(deco) -> "str | None":
    """`@router.get(...)` / `@router.get` -> 'router' (the decorated object's variable), else None."""
    f = deco.func if isinstance(deco, ast.Call) else deco
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id
    return None


def _ref(expr) -> "tuple | None":
    """A mount operand -> ('name', var) | ('attr', module_alias, var) | None (anything else)."""
    if isinstance(expr, ast.Name):
        return ("name", expr.id)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        return ("attr", expr.value.id, expr.attr)
    return None


def _hook_mapping(expr) -> list:
    """hooks={'response': h} / {'response': [h1, h2]} / dict(response=h) -> [(event, handler_expr)].
    Only string-literal events; anything else (None, a call result) -> []."""
    pairs = []
    if isinstance(expr, ast.Dict):
        pairs = [(_str_const(k), v) for k, v in zip(expr.keys, expr.values) if k is not None]
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "dict":
        pairs = [(k.arg, k.value) for k in expr.keywords if k.arg]
    out = []
    for ev, v in pairs:
        if not ev:
            continue
        for h in (v.elts if isinstance(v, (ast.List, ast.Tuple)) else [v]):
            out.append((ev, h))
    return out


# ---- route-prefix composition ---------------------------------------------
class _Facts(NamedTuple):
    decls: dict
    imports: dict
    mounts: list
    routes: list
    urls: "_UrlFacts | None" = None       # a Django URLconf's pattern lists + routes
    syms: "tuple | None" = None           # _module_symbols: where a Django view really lives
    tables: "dict | None" = None          # class -> __tablename__ (ForeignKey string targets)


def _facts_of(v: _FrameworkVisitor) -> _Facts:
    return _Facts(v.decls, v.imports, v.mounts, v.routes, v.urls, v.syms, v.tables)


def _analyse(tree, rel: str) -> _FrameworkVisitor:
    v = _FrameworkVisitor(rel)
    v.prescan(tree)
    v.visit(tree)
    v.finish(tree)
    return v


def _module_dotted(rel: str) -> str:
    """'app/routers/users.py' -> 'app.routers.users'; 'app/__init__.py' -> 'app' (root-relative)."""
    p = rel[:-3] if rel.lower().endswith(".py") else rel
    parts = [x for x in p.split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _RouteWorld:
    """The mount graph over a set of modules: resolves a router operand to its declaring (rel, var) —
    through imports and re-exports when a loader is available — and composes each router's full
    prefix(es) from the mounts that reach it. Pure given its loader; never raises."""

    def __init__(self, loader, dotted_to_rel: dict):
        self._loader = loader
        self._d2r = dotted_to_rel
        self._facts: dict = {}
        self._suffix: dict = {}
        self.mounts_into: dict = {}          # child node -> [(parent node | None, _Prefix | None, kind)]
        self._memo: dict = {}
        self._spent = 0                      # prefix expansions so far (_keep: the cycle-cut memo budget)

    def facts(self, rel):
        if rel not in self._facts:
            try:
                self._facts[rel] = self._loader(rel)
            except Exception:  # noqa: BLE001
                self._facts[rel] = None
        return self._facts[rel]

    def rel_of(self, dotted: str, exact_only: bool = False):
        if not dotted:
            return None
        if dotted in self._d2r:
            return self._d2r[dotted]
        if exact_only:
            return None
        if dotted not in self._suffix:          # src-layout: 'app.x' may live at 'src/app/x.py'
            hits = [r for d, r in self._d2r.items() if d.endswith("." + dotted)]
            self._suffix[dotted] = hits[0] if len(hits) == 1 else None
        return self._suffix[dotted]

    def _abs(self, rel: str, level: int, module: str):
        """(dotted target, relative?) of an import in `rel`; None when it climbs above the root."""
        if not level:
            return module, False
        pkg = _module_dotted(rel)
        parts = pkg.split(".") if pkg else []
        if not rel.lower().endswith("__init__.py"):
            parts = parts[:-1]                   # a plain module's package is its parent
        up = level - 1
        if up > len(parts):
            return None
        base = parts[:len(parts) - up] if up else parts
        return ".".join(base + ([module] if module else [])), True

    def resolve(self, rel: str, ref, depth: int = 0):
        """A router operand in module `rel` -> its declaring node (rel, var), or None (unknown)."""
        if ref is None or depth > _MAX_IMPORT_HOPS:
            return None
        f = self.facts(rel)
        if f is None:
            return None
        if ref[0] == "name":
            var = ref[1]
            d = f.decls.get(var)
            if d is not None:
                return (rel, var) if d is not _CONFLICT else None
            imp = f.imports.get(var)
            if imp is None or imp is _CONFLICT or imp[0] != "from":
                return None
            target = self._abs(rel, imp[1], imp[2])
            if target is None:
                return None
            mod, relative = target
            full = f"{mod}.{imp[3]}" if mod else imp[3]
            if self.rel_of(full, relative):
                return None                      # `from pkg import submodule` — a module, not a router
            r2 = self.rel_of(mod, relative)
            return self.resolve(r2, ("name", imp[3]), depth + 1) if r2 else None
        if ref[0] == "attr":
            imp = f.imports.get(ref[1])
            if imp is None or imp is _CONFLICT:
                return None
            if imp[0] == "module":
                r2 = self.rel_of(imp[2])
            else:
                target = self._abs(rel, imp[1], imp[2])
                if target is None:
                    return None
                mod, relative = target
                r2 = self.rel_of(f"{mod}.{imp[3]}" if mod else imp[3], relative)
            return self.resolve(r2, ("name", ref[2]), depth + 1) if r2 else None
        return None

    def decl(self, node):
        if node is None:
            return None
        f = self.facts(node[0])
        d = f.decls.get(node[1]) if f is not None else None
        return None if (d is None or d is _CONFLICT) else d

    def add_mounts(self, rel: str) -> None:
        f = self.facts(rel)
        for parent_ref, child_ref, prefix, kind in (f.mounts if f is not None else ()):
            child = self.resolve(rel, child_ref)
            cd = self.decl(child)
            if cd is None or cd[1] != "router" or cd[0] != kind:
                continue                          # an unresolvable / non-router child is never guessed
            parent = self.resolve(rel, parent_ref)
            pd = self.decl(parent)
            if pd is None or pd[1] != "router" or pd[0] != kind:
                parent = None                     # an app, or an unknown object: a mount ROOT
            self.mounts_into.setdefault(child, []).append((parent, prefix, kind))
        self._memo.clear()

    def _keep(self, clean: bool) -> bool:
        """Memoize a prefix result? A clean one always. A cycle-cut one (a valid, finite prefix set, but one
        that depends on where the walk came from) only once the world has spent _MAX_EXPANSIONS expansions:
        a cycle the name-level mount graph merely implies (the same router variable in several test functions)
        keeps its exact paths, while mutually mounted / include()d lists stop costing one walk per simple path
        (12 of them once took hours) and are expanded once each from then on."""
        self._spent += 1
        return clean or self._spent > _MAX_EXPANSIONS

    def full(self, node, visiting=frozenset()) -> tuple:
        """(prefixes, clean): every full prefix of router `node` as (text | None, unresolved tuple).
        `clean` is False when a cycle was cut below (memoized then only past the work budget: _keep)."""
        if node in self._memo:
            return self._memo[node]
        kind, _role, own = self.decl(node)
        out: list = []
        clean = True
        if len(visiting) < _MAX_MOUNT_DEPTH:
            for parent, prefix, _k in self.mounts_into.get(node, ()):
                if parent is not None and (parent in visiting or parent == node):
                    clean = False
                    continue
                if parent is None:
                    pres = [(None, ())]
                else:
                    pres, c = self.full(parent, visiting | {node})
                    clean = clean and c
                for ptext, punres in pres:
                    if kind == "fastapi":
                        p = prefix or _ABSENT
                        text = (ptext or "") + (p.text or "") + (own.text or "")
                        out.append((text, punres + p.unresolved + own.unresolved))
                    else:
                        eff = prefix if (prefix is not None and prefix.present) else own
                        out.append((_flask_join(ptext, eff.text), punres + eff.unresolved))
        else:
            clean = False
        if not out:
            out = [((own.text or "") if kind == "fastapi" else own.text, own.unresolved)]
        res = (list(dict.fromkeys(out))[:_MAX_PATHS], clean)
        if self._keep(clean):
            self._memo[node] = res
        return res

    def route_paths(self, rel: str, var, raw) -> "list | None":
        """[(path, unresolved)] for a route declared with `@<var>.<verb>(raw)` in `rel`, or None when
        `var` is not a known router (the edge then keeps its attrs)."""
        if var is None or raw is None:
            return None
        node = self.resolve(rel, ("name", var))
        d = self.decl(node)
        if d is None or d[1] != "router":
            return None
        pres, _clean = self.full(node)
        if d[0] == "fastapi":
            return [((t or "") + raw, u) for t, u in pres]
        return [(_flask_rule(t, raw), u) for t, u in pres]


def _seg_text(seg: _Seg) -> str:
    """A pattern's contribution to the composed path: a path() route verbatim; a regex without its '^' / '$'
    anchors (Django matches an include()'s regex prefix, then the included pattern after it)."""
    t = seg.text or ""
    if seg.regex:
        if t.startswith("^"):
            t = t[1:]
        elif t.startswith("\\A"):
            t = t[2:]
        if t.endswith("\\Z"):
            t = t[:-2]
        elif t.endswith("$") and not t.endswith("\\$"):
            t = t[:-1]
    return t


class _UrlWorld(_RouteWorld):
    """The include() graph over a set of Django URLconfs (one module for edges_from_module, the tree for
    compose_route_prefixes): each pattern list's composed prefixes and url namespaces, and where an imported
    view is really defined (followed through re-exports, star imports and `x = View.as_view()` aliases)."""

    _EXT = ("ext",)                           # outside the tree (or a module, not a symbol): binds nothing
    _AMB = ("amb",)                           # a module the tree holds under several paths: the global rule

    def __init__(self, loader, dotted_to_rel: dict, root_parts: tuple = ()):
        super().__init__(loader, dotted_to_rel)
        self.parents: dict = {}               # (rel, list id) -> [((rel, list id), _Seg, namespace | None)]
        self._pre: dict = {}
        self._amb: dict = {}
        self._root = tuple(root_parts)        # the sync root's own trailing directory names ('mypkg')

    def _in_root(self, dotted: str) -> "str | None":
        """An absolute import that names the sync root's own package (`from mypkg.models import User` with the
        root directory mypkg/, whose modules are keyed 'models'): the rest of it ('models', '' for the package
        itself), the longest root suffix first; None when it does not start with the root's name."""
        parts = dotted.split(".") if dotted else []
        for k in range(min(len(parts), len(self._root)), 0, -1):
            if parts[:k] == list(self._root[-k:]):
                return ".".join(parts[k:])
        return None

    def rel_of(self, dotted: str, exact_only: bool = False):
        """_RouteWorld.rel_of, and an absolute import that names the sync root's own package resolved inside it
        (a sync run in the package directory itself: backend/app opened directly)."""
        got = super().rel_of(dotted, exact_only)
        if got is None and not exact_only:
            inner = self._in_root(dotted)
            if inner is not None:
                got = self._d2r.get("") if inner == "" else super().rel_of(inner)
        return got

    def _ambiguous(self, dotted: str) -> bool:
        """`from models import User` in a tree whose `models` is several files (app1/models.py, app2/models.py
        under a source root rel_of cannot see): the import is IN the tree, just not placeable."""
        if dotted not in self._amb:
            self._amb[dotted] = any(
                bool(d) and d not in self._d2r and sum(1 for x in self._d2r if x.endswith("." + d)) > 1
                for d in (dotted, self._in_root(dotted)) if d is not None)
        return self._amb[dotted]

    def urls(self, rel) -> "_UrlFacts | None":
        f = self.facts(rel) if rel else None
        return getattr(f, "urls", None) if f is not None else None

    def _urlconf(self, rel) -> "tuple | None":
        u = self.urls(rel)
        return ((rel, "urlpatterns"), rel) if u is not None and "urlpatterns" in u.lists else None

    def target(self, rel: str, tgt, depth: int = 0) -> "tuple | None":
        """An include() target -> (pattern-list node, the included module when it is a whole URLconf) | None."""
        if depth > _MAX_IMPORT_HOPS:
            return None
        if tgt[0] == "list":
            u = self.urls(rel)
            return ((rel, tgt[1]), None) if u is not None and tgt[1] in u.lists else None
        t = self._abs(rel, *_unspec(tgt[1]))
        if t is None:
            return None
        dotted, relative = t
        if tgt[0] == "module":
            return self._urlconf(self.rel_of(dotted, relative))
        whole = self.rel_of(f"{dotted}.{tgt[2]}" if dotted else tgt[2], relative)
        if whole:
            return self._urlconf(whole)                               # include(polls.urls): a module
        r2 = self.rel_of(dotted, relative)
        u = self.urls(r2)
        if u is not None and tgt[2] in u.lists:
            return (r2, tgt[2]), None                                 # an imported pattern list
        f = self.facts(r2) if r2 else None
        binds = ((f.syms or ({}, []))[0].get(tgt[2]) or []) if f is not None else []
        if len(binds) == 1 and binds[0][0] == "from":                 # re-exported from another module
            _k, level, module, orig = binds[0]
            return self.target(r2, ("import", _spec(level, module), orig), depth + 1)
        return None

    def add_includes(self, rel: str) -> None:
        u = self.urls(rel)
        for lid, items in (u.lists.items() if u is not None else ()):
            for it in items:
                if it[0] != "include":
                    continue
                _k, seg, ns, tgt, ask, _direct = it
                got = self.target(rel, tgt)
                if got is None:
                    continue                                          # outside the tree / unknown: never guessed
                node, whole = got
                if ask and whole is not None:
                    tu = self.urls(whole)
                    ns = tu.app_name if tu is not None else None      # include('polls.urls') -> polls' app_name
                self.parents.setdefault(node, []).append(((rel, lid), seg, ns))
        self._pre.clear()

    def prefixes(self, node, visiting=frozenset()) -> tuple:
        """([(prefix text, unresolved parts, namespaces)], clean) of a pattern list; a list nothing includes is
        a root (''). `clean` is False when a cycle was cut below (memoized then only past the work budget:
        _keep, so mutually include()ing lists stay polynomial)."""
        if node in self._pre:
            return self._pre[node]
        out: list = []
        clean = True
        for parent, seg, ns in self.parents.get(node, ()):
            if parent == node or parent in visiting or len(visiting) >= _MAX_MOUNT_DEPTH:
                clean = False
                continue
            pres, c = self.prefixes(parent, visiting | {node})
            clean = clean and c
            for text, unres, nss in pres:
                out.append((text + _seg_text(seg), unres + seg.unresolved, nss + ((ns,) if ns else ())))
        if not out:
            out = [("", (), ())]
        res = (list(dict.fromkeys(out))[:_MAX_PATHS], clean)
        if self._keep(clean):
            self._pre[node] = res
        return res

    def route_attrs(self, rel: str, routes: list, placed: list) -> list:
        """The ROUTES_TO attrs of each route; routes of one module that reach the same view share one set
        (every path and url name of that view), as their edges collapse into one at push. A route whose own
        pattern is not a literal (`path(settings.ADMIN_URL, ...)`) adds no `path` — as a Flask route with a
        non-literal rule has none — and names the expression in `prefix_unresolved`."""
        merged: dict = {}
        for r, p in zip(routes, placed):
            acc = merged.setdefault(p, (set(), set(), set()))
            for text, unres, nss in self.prefixes((rel, r.list_id))[0]:
                if r.seg.text is not None:
                    acc[0].add("/" + text + _seg_text(r.seg))
                acc[1].update(unres + r.seg.unresolved)
                if r.url_name:
                    acc[2].add(":".join(nss + (r.url_name,)))
        out = []
        for p in placed:
            paths, unres, names = (sorted(x) for x in merged[p])
            paths = paths[:_MAX_PATHS]
            attrs = {"method": "*", **({"path": paths[0]} if paths else {}), "framework": "django"}
            if len(paths) > 1:
                attrs["paths"] = paths
            if unres:
                attrs["prefix_unresolved"] = ", ".join(unres)
            if names:
                attrs["url_name"] = names[0]
                if len(names) > 1:
                    attrs["url_names"] = names[:_MAX_PATHS]
            out.append(attrs)
        return out

    # -- where an imported view is defined ---------------------------------
    def place(self, rel: str, r: _DjRoute) -> tuple:
        """(dst, conf_hint, scope) of a route's view after following its import: a view defined in module M
        -> (its name, NAME, "exact:module:<M>"), in this module -> LOCAL; a module M of the tree whose
        definition of it this pass cannot follow (bound twice, a PEP 562 __getattr__, the hop limit) -> NAME,
        "pref:module:<M>" (M's own symbol first, else the global rule); an import of a module outside the
        tree (django.contrib.auth.views) or one that names a module keeps its "spec:" scope (binds nothing).
        A name bound nowhere (a star import) is placed only when a star-imported module of the tree defines it;
        an import of a module the tree holds under several paths falls back to the global rule (no scope)."""
        here = (r.dst, r.conf, r.scope)
        got = None
        if r.scope.startswith("spec:"):
            got = self._place(rel, *_unspec(r.scope[len("spec:"):]), r.dst, 0)
        elif r.conf == "NAME" and not r.scope:
            got = self._from_stars(rel, r.dst, 0)
        if got == self._AMB:
            return r.dst, "NAME", ""
        if not got or got[0] not in ("def", "mod"):
            return here
        where, name = got[1], (got[2] if got[0] == "def" else r.dst)
        if where == rel:
            return (name, "LOCAL", "") if got[0] == "def" else (name, "NAME", "")
        return name, "NAME", _placed_scope(where, got[0] == "def")

    def _place(self, rel: str, level: int, module: str, name: str, depth: int):
        """("def", rel, name) — defined there; ("mod", rel) — that module of the tree, the definition not
        followed (placed "pref:", never "exact:": the name rule decides, as before this pass); _EXT — outside
        the tree, or the name is a module (`from . import views`), not a symbol; _AMB — a module the tree holds
        under several paths; None — unknown (a hop limit)."""
        if depth > _MAX_IMPORT_HOPS:
            return None
        t = self._abs(rel, level, module)
        if t is None:
            return self._EXT                                          # a relative import above the root
        dotted, relative = t
        if self.rel_of(f"{dotted}.{name}" if dotted else name, relative):
            return self._EXT                                          # `from . import views`: a module
        r2 = self.rel_of(dotted, relative)
        if r2 is None:
            return self._AMB if (not relative and self._ambiguous(dotted)) else self._EXT
        return self._symbol(r2, name, depth + 1) or ("mod", r2)

    def _from_stars(self, rel: str, name: str, depth: int):
        f = self.facts(rel)
        for level, module in ((f.syms or ({}, []))[1] if f is not None else ()):
            got = self._place(rel, level, module, name, depth + 1)
            if got and got[0] == "def":
                return got
        return None

    def _symbol(self, rel: str, name: str, depth: int):
        """Where module `rel`'s `name` is defined: ("def", ...), _EXT, or None (not followed)."""
        if depth > _MAX_IMPORT_HOPS:
            return None
        f = self.facts(rel)
        binds = ((f.syms or ({}, []))[0].get(name) or []) if f is not None else []
        if any(b[0] == "def" for b in binds):
            return ("def", rel, name)
        if not binds:
            return self._from_stars(rel, name, depth)
        if len(binds) != 1:
            return None
        b = binds[0]
        if b[0] == "from":                                            # a re-export
            return self._place(rel, b[1], b[2], b[3], depth + 1)
        if b[0] == "alias" and b[1] is not None:                      # order_list = OrderList.as_view()
            expr = _unwrap_view(b[1])
            if isinstance(expr, ast.Name):
                return self._symbol(rel, expr.id, depth + 1)
            chain = _attr_chain(expr) if expr is not None else None
            roots = ((f.syms or ({}, []))[0].get(chain[0]) or []) if chain is not None else []
            got = _member_spec(roots[0], chain[1]) if len(roots) == 1 else None
            if got is not None:
                return self._place(rel, *_unspec(got[0]), got[1], depth + 1)
        return None


def _flask_join(state, prefix):
    """Flask's nested-blueprint join of a parent state url_prefix and a child's (either may be None)."""
    if state is not None and prefix is not None:
        return state.rstrip("/") + "/" + prefix.lstrip("/")
    return prefix if prefix is not None else state


def _flask_rule(prefix, rule: str) -> str:
    """Flask BlueprintSetupState.add_url_rule: '/'.join((prefix.rstrip('/'), rule.lstrip('/')))."""
    if prefix is None:
        return rule
    if rule:
        return "/".join((prefix.rstrip("/"), rule.lstrip("/")))
    return prefix


def _composed_attrs(attrs: dict, composed: list) -> dict:
    paths = sorted({p for p, _u in composed if p})
    unres = sorted({x for _p, u in composed for x in u})
    new = dict(attrs)
    if paths:
        new["path"] = paths[0]
    else:
        new.pop("path", None)
    if len(paths) > 1:
        new["paths"] = paths
    else:
        new.pop("paths", None)
    if unres:
        new["prefix_unresolved"] = ", ".join(unres)
    else:
        new.pop("prefix_unresolved", None)
    return new


def _recompose(edges: list, world: _RouteWorld, rels) -> list:
    """Rewrite the ROUTES_TO attrs of the modules in `rels` from `world`. A module's route facts align
    1:1, in order, with its ROUTES_TO edges; any misalignment leaves that module untouched. Returns the
    ORIGINAL list object when nothing changed."""
    idx_by_rel: dict = {}
    for i, e in enumerate(edges):
        if e.edge_type == "ROUTES_TO" and e.src_file in rels:
            idx_by_rel.setdefault(e.src_file, []).append(i)
    out = None
    for rel, idxs in idx_by_rel.items():
        f = world.facts(rel)
        if f is None or len(f.routes) != len(idxs):
            continue
        if any(edges[i].dst_name != f.routes[k][0] for k, i in enumerate(idxs)):
            continue
        for k, i in enumerate(idxs):
            _handler, var, raw = f.routes[k]
            composed = world.route_paths(rel, var, raw)
            if composed is None:
                continue
            e = edges[i]
            new = _composed_attrs(e.attrs or {}, composed)
            if new != (e.attrs or {}):
                if out is None:
                    out = list(edges)
                out[i] = e._replace(attrs=new)
    return edges if out is None else out


def edges_from_module(tree: ast.AST, rel: str) -> "list[TypedEdge]":
    """PURE: one module's AST -> its framework/runtime TypedEdges. No fs, no network. The analog of
    graphify_extract.map_graph — unit-testable against a captured source with no live parse pipeline.
    Route prefixes compose from what THIS module declares (its own routers and mounts, its own Django
    include()s); compose_route_prefixes adds the mounts that live in other modules and places imported views."""
    v = _analyse(tree, rel)
    if not v.routes or not any(d is not _CONFLICT and d[1] == "router" for d in v.decls.values()):
        return v.edges
    facts = _facts_of(v)
    world = _RouteWorld(lambda r: facts if r == rel else None, {_module_dotted(rel): rel})
    world.add_mounts(rel)
    return _recompose(v.edges, world, {rel})


# ---- macOS metadata is never source -------------------------------------------------------------------
# On a volume without extended attributes (exFAT / FAT USB drives, SMB / NFS shares) macOS writes an
# AppleDouble twin, a small BINARY `._<name>` file, next to every file it touches; macOS-made zip / tar
# archives carry the same twins, a zip under a `__MACOSX/` directory that mirrors the tree. They were taken
# for source: a "skipped ._x.py" line per file (NUL bytes), nodes from whatever under `__MACOSX/` parses, junk
# recognizer nodes (an `.erb` twin is a view), and census files that could raise a false LOW COVERAGE. Netatalk
# (AFP shares on Linux NAS boxes) keeps the same twins in a `.AppleDouble/` directory inside every directory a
# Mac touches, under the ORIGINAL names (`.AppleDouble/x.py`, binary). graphify's own file discovery has no
# dot-file rule, so on the engine path a twin even took its original's node id (graphify_case_shim.py).
# THE rule for every walk that decides what is source: sync's ast walk and this module's use this function,
# language_recognizers.walk_files too; verdict.py, graphify_extract.py and graphify_case_shim.py keep private
# copies (they import neither module) — the four are drift-locked by tests/macos_appledouble_gates.py.
_MACOS_METADATA_DIRS = frozenset({"__MACOSX", ".AppleDouble"})


def is_macos_metadata(rel, is_dir: bool = False) -> bool:
    """True iff `rel`, a path RELATIVE to the walked root (posix or Windows separators), is macOS metadata:
    a file whose name starts with `._` (an AppleDouble twin), or anything with a `__MACOSX` or `.AppleDouble`
    directory component. With `is_dir`, `rel` names a directory, which is metadata only when it is (or is
    under) one of those two — a directory named `._x` is not a twin (macOS writes twins as files), and any
    other dot-file or dot-dir (.github, .hidden.py) is never matched. Exact names (macOS and Netatalk write
    them in that case). Never raises."""
    if not isinstance(rel, str):
        return False
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if is_dir:
        return not _MACOS_METADATA_DIRS.isdisjoint(parts)
    return parts[-1].startswith("._") or not _MACOS_METADATA_DIRS.isdisjoint(parts[:-1])


def _iter_py(root: Path, exclude=None):
    """(rel, path) for every *.py under `root`, sorted, skipping __pycache__/.venv components RELATIVE
    to the root (the registry walker's discipline), case-insensitive suffix, never macOS metadata
    (is_macos_metadata) — and, before it is read, every file the caller's `exclude` predicate rules out (a
    predicate that raises excludes nothing)."""
    for py in sorted(p for p in root.rglob("*") if p.suffix.lower() == ".py"):
        try:
            rel_path = py.relative_to(root)
        except ValueError:
            rel_path = py
        if any(part in {"__pycache__", ".venv"} for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        if is_macos_metadata(rel):
            continue
        try:
            if exclude is not None and exclude(rel):
                continue
        except Exception:  # noqa: BLE001 — a failing predicate excludes nothing (as language_recognizers._excluded)
            pass
        yield rel, py


def _load_facts(path: Path, rel: str) -> "_Facts | None":
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:  # noqa: BLE001 — an unparseable module contributes no mounts
        return None
    return _facts_of(_analyse(tree, rel))


def _is_django_route(e) -> bool:
    return getattr(e, "edge_type", "") == "ROUTES_TO" and (getattr(e, "attrs", None) or {}).get("framework") == "django"


def _compose_mounts(edges: list, world: _RouteWorld, mount_rels: list) -> list:
    """FastAPI include_router / Flask register_blueprint mounts in any module -> the ROUTES_TO paths."""
    for rel in mount_rels:
        world.add_mounts(rel)
    if not world.mounts_into:
        return edges
    return _recompose(edges, world, {e.src_file for e in edges if e.edge_type == "ROUTES_TO"})


def _compose_django(edges: list, world: _UrlWorld, include_rels: list) -> list:
    """Django include() across modules -> every Django route's composed paths and url names; an imported
    view -> the module that defines it. A module whose Django edges do not line up 1:1 with its facts (in
    order, same view) keeps its own paths, its imported views degraded to the global name rule (_unscoped)."""
    by_rel: dict = {}
    for i, e in enumerate(edges):
        if _is_django_route(e):
            by_rel.setdefault(e.src_file, []).append(i)
    for rel in dict.fromkeys(list(include_rels) + list(by_rel)):
        world.add_includes(rel)
    out = None
    for rel, idxs in by_rel.items():
        u = world.urls(rel)
        if u is None or len(u.routes) != len(idxs) or any(
                edges[i].dst_name != r.dst or edges[i].scope != r.scope for i, r in zip(idxs, u.routes)):
            news = [(i, _unscoped_edge(edges[i])) for i in idxs]
        else:
            placed = [world.place(rel, r) for r in u.routes]
            news = [(i, edges[i]._replace(dst_name=dst, conf_hint=conf, scope=scope, attrs=attrs)) for i, (
                dst, conf, scope), attrs in zip(idxs, placed, world.route_attrs(rel, u.routes, placed))]
        for i, new in news:
            if new != edges[i]:
                if out is None:
                    out = list(edges)
                out[i] = new
    return edges if out is None else out


_ORM_EDGE_TYPES = ("HAS_MANY", "REFERENCES", "CASCADES")


def _is_orm_fact(e) -> bool:
    """A Python ORM edge the post-pass may place: an imported model ("spec:" scope) or a ForeignKey table."""
    if getattr(e, "edge_type", "") not in _ORM_EDGE_TYPES or not str(getattr(e, "src_file", "")).lower().endswith(".py"):
        return False
    return str(getattr(e, "scope", "") or "").startswith("spec:") or "foreign_key" in (getattr(e, "attrs", None) or {})


def _unscoped_edge(e):
    """`e` with its "spec:" import scope cleared: edge_resolve's global name rule instead of binding nothing."""
    return e._replace(scope="") if str(getattr(e, "scope", "") or "").startswith("spec:") else e


def _unscoped(edges: list, which) -> list:
    """`edges` with the "spec:" scope cleared on every edge `which` selects (Django routes, Python ORM edges):
    when the post-pass that places those imports did not run to the end, an imported view or model degrades to
    the name rule — what it was before the import scopes existed — never to an edge that binds nothing.
    Returns the ORIGINAL list object when nothing changes."""
    out = None
    for i, e in enumerate(edges):
        if which(e):
            new = _unscoped_edge(e)
            if new is not e:
                if out is None:
                    out = list(edges)
                out[i] = new
    return edges if out is None else out


def _compose_orm(edges: list, world: "_UrlWorld", table_rels: list) -> list:
    """Python ORM edges whose other model this module could not see:
      * an imported model ("spec:<module>" scope, forward or — for a principal — on the src) -> the module that
        defines it ("exact:module:<path>", followed through re-exports; "pref:module:<path>" when only that module
        of the tree was found); outside the tree it keeps its "spec:" scope and binds nothing (a
        django.contrib.auth User is never the project's own User);
      * a ForeignKey("orders.id") -> the class whose __tablename__ is 'orders': this module's own first, else the
        ONE class of the tree that has it (bound in its module), else the CamelCased table name (never a guess
        between two classes)."""
    wider: dict = {}
    for rel in table_rels:
        f = world.facts(rel)
        for cls, table in ((f.tables or {}) if f is not None else {}).items():
            wider.setdefault(table, set()).add((cls, rel))
    out = None
    for i, e in enumerate(edges):
        if not _is_orm_fact(e):
            continue
        a = e.attrs or {}
        f = world.facts(e.src_file)
        lit = a.get("foreign_key")
        new = e
        if lit and (e.edge_type == "REFERENCES" or a.get("via") == "sqlalchemy"):
            table = _fk_table(lit)
            here = [c for c, t in ((f.tables or {}) if f is not None else {}).items() if t == table]
            there = wider.get(table) or set()
            scope = ""
            if len(here) == 1:
                cls = here[0]
            elif not here and len(there) == 1:
                cls, where = next(iter(there))
                scope = "" if where == e.src_file else _placed_scope(where, True)
            else:
                cls = _camel(table)
            if cls:
                new = e._replace(dst_name=cls, scope=scope) if e.edge_type == "REFERENCES" \
                    else e._replace(src_name=cls, scope=scope)
        elif e.scope.startswith("spec:") and f is None:                # its module unread here: the name rule
            new = _unscoped_edge(e)
        elif e.scope.startswith("spec:"):
            local = (f.syms or ({}, []))[0]
            on_src = any(b[0] == "def" for b in local.get(e.dst_name, ()))   # the dst is ours: the import is the src
            name = e.src_name if on_src else e.dst_name
            got = world._place(e.src_file, *_unspec(e.scope[len("spec:"):]), name, 0)
            if got == world._AMB:                                     # in the tree, not placeable: the global rule
                new = e._replace(scope="")
            elif got and got[0] in ("def", "mod"):
                where, real = got[1], (got[2] if got[0] == "def" else name)
                scope = "" if where == e.src_file else _placed_scope(where, got[0] == "def")
                new = e._replace(src_name=real, scope=scope) if on_src else e._replace(dst_name=real, scope=scope)
        if new != e:
            if out is None:
                out = list(edges)
            out[i] = new
    return edges if out is None else out


def compose_route_prefixes(workspace_root, edges, exclude=None) -> "list[TypedEdge]":
    """The Python recognizer's whole-run post-pass (the registry Python entry's `finalize`; the legacy shell
    runs it too) — the facts no single module shows:
      * FastAPI / Flask route prefixes: when some module mounts a router (include_router / register_blueprint),
        resolve every mount operand through imports (relative and absolute, re-exports, function-level imports
        in a Flask app factory) and recompute the ROUTES_TO paths of the affected routes;
      * Django URLconfs: include() in any module ('polls.urls', an imported urls module or pattern list,
        (patterns, app_name) pairs, namespace=) -> each Django route's composed `path` / `paths` and
        `url_name` / `url_names`, and an imported view -> the module that defines it (its "spec:" scope
        becomes "exact:module:<path>", followed through re-exports, star imports and `x = View.as_view()`);
      * SQLAlchemy: a ForeignKey("orders.id") -> the one class of the tree whose __tablename__ is 'orders'.
    A route is only ever re-attributed a PATH (a Django one also its imported view's real module); an
    unresolvable operand is skipped, never guessed.
    `exclude` (optional): a predicate over root-relative posix paths — language_recognizers.run hands it the
    caller's rule (for a sync, sync._recognizer_exclusion: the rule its walk honours). An excluded module is
    never read, so it is neither a mount site nor an import target: an in-tree copy of the project (a
    .worktrees/ checkout, a gitignored build/lib/) can neither add a path to a project route nor make its
    import ambiguous. A predicate that raises excludes nothing; None keeps the static __pycache__/.venv skip.
    Returns the ORIGINAL list object when there is nothing to compose (no route, mount, include or foreign
    key anywhere). Each part is isolated: one that fails keeps what the others did, and the imports it did not
    place ("spec:" scopes on Django views / ORM models) degrade to the name rule (_unscoped). Never raises."""
    try:
        if not edges:
            return edges
        routes = any(getattr(e, "edge_type", "") == "ROUTES_TO" for e in edges)
        django = any(_is_django_route(e) for e in edges)
        orm = any(_is_orm_fact(e) for e in edges)
        fks = orm and any("foreign_key" in (getattr(e, "attrs", None) or {}) for e in edges)
        if not (routes or orm):
            return edges
        root = Path(workspace_root)
        root_parts: tuple = ()             # the root's trailing package-like directory names (_UrlWorld._in_root)
        try:
            for part in reversed(root.resolve().parts[1:]):
                if not part.isidentifier() or len(root_parts) >= 4:
                    break
                root_parts = (part,) + root_parts
        except (OSError, RuntimeError, ValueError):
            root_parts = ()
        files = dict(_iter_py(root, exclude))
        mount_rels, include_rels, table_rels = [], [], []
        for rel, path in files.items():
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if routes and any(w in text for w in _MOUNT_WORDS):
                mount_rels.append(rel)
            if django and "include" in text:
                include_rels.append(rel)
            if fks and "__tablename__" in text:
                table_rels.append(rel)
        if not (mount_rels or django or orm):
            return edges
        d2r = {}
        for rel in files:
            d2r.setdefault(_module_dotted(rel), rel)
        cache: dict = {}

        def load(r):
            if r not in cache:
                cache[r] = _load_facts(files[r], r) if r in files else None
            return cache[r]
    except Exception as e:  # noqa: BLE001 — a post-pass must never cost the route edges
        print(f"[deepmap sync] Python post-pass (route prefixes, Django URLs, ORM placement) skipped "
              f"({type(e).__name__})", file=sys.stderr)
        try:
            return _unscoped(edges, lambda x: _is_django_route(x) or _is_orm_fact(x))
        except Exception:  # noqa: BLE001
            return edges
    out = edges
    for what, wanted, run, placed in (
            ("route-prefix composition", mount_rels,
             lambda x: _compose_mounts(x, _RouteWorld(load, d2r), mount_rels), None),
            ("Django URL composition", django,
             lambda x: _compose_django(x, _UrlWorld(load, d2r, root_parts), include_rels), _is_django_route),
            ("ORM model placement", orm,
             lambda x: _compose_orm(x, _UrlWorld(load, d2r, root_parts), table_rels), _is_orm_fact)):
        if not wanted:
            continue
        try:
            out = run(out)
        except Exception as e:  # noqa: BLE001 — one part failing keeps what the others did
            print(f"[deepmap sync] {what} skipped ({type(e).__name__})", file=sys.stderr)
            try:
                out = _unscoped(out, placed) if placed is not None else out
            except Exception:  # noqa: BLE001
                pass
    return out


def extract_framework_edges(workspace_root) -> "list[TypedEdge]":
    """Impure shell (analog of graphify_extract.extract): walk *.py under `workspace_root`, parse each,
    and collect framework/runtime edges. Reuses the AST path's file-walk discipline (build/sync.py `_iter_ast_files`):
    sorted rglob, skip __pycache__/.venv COMPONENTS and macOS metadata (is_macos_metadata), utf-8-sig
    (BOM-safe), posix relpath, per-file isolation so one unparseable file never aborts the sweep. Runs
    regardless of DEEPMAP_EXTRACTOR. Cross-module route prefixes are composed afterwards
    (compose_route_prefixes)."""
    root = Path(workspace_root)
    edges: list = []
    # Case-insensitive for the same reason as sync._iter_ast_files (rglob is case-sensitive on POSIX).
    for py in sorted(p for p in root.rglob("*") if p.suffix.lower() == ".py"):
        if any(part in {"__pycache__", ".venv"} for part in py.parts):
            continue
        try:
            rel = py.relative_to(root).as_posix()
        except ValueError:
            rel = py.as_posix()
        if is_macos_metadata(rel):
            continue                    # an AppleDouble twin / __MACOSX/ / .AppleDouble/ file: never source
        try:
            tree = ast.parse(py.read_text(encoding="utf-8-sig", errors="replace"))
            edges.extend(edges_from_module(tree, rel))
        except Exception as e:  # noqa: BLE001 — skip exactly the one bad file, never abort the sweep
            print(f"[deepmap sync] framework-edges skipped {rel} ({type(e).__name__})", file=sys.stderr)
            continue
    return compose_route_prefixes(root, edges)
