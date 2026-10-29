#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Track C — name resolution + typed-edge persistence for framework/runtime edges.

Separated from build/sync.py so it is unit-testable with a fake session (no Neo4j), and so the
:CALLS path stays byte-for-byte unchanged: sync's CALLS resolution sources its `name_to_keys` from
build_name_index() here (the SAME loop push_to_neo4j once had inline). sync.push_to_neo4j persists the typed
edges and :IMPORTS through the bounded-transaction API at the end of this module: edge_rows resolves every
row once, rows_by_anchor groups them by ANCHOR node (a row's emitted src — normally the declaring node; for
a REVERSED type the principal, see REVERSED_EDGE_TYPES), and each group's transaction calls
rewrite_anchored_edges (an anchor too large for one transaction is written with write_edge_rows in pieces).
push_typed_edges / push_imports / prune_stale_edge_types are the older single-session form of the same
prune-then-write; sync no longer calls them (the gates still drive them). Nothing in this module alters
CALLS resolution or the returned CALLS count.

Confidence reuses the CALLS audit wedge (sync._edge_confidence) and adds the schema's already-defined
EXTRACTED tier for a literal, same-file relationship:
  EXTRACTED  a literal edge whose single target is defined in the SAME source file (e.g. a base class
             or route handler we can see) -> firmest;
  INFERRED   a single global name match (identical to today's single-candidate :CALLS);
  AMBIGUOUS  the target name maps to >1 symbol, OR a Stage-3 heuristic (reflection) -> verify by reading.

Candidates are bounded BEFORE grading (2026-09 live validation), so AMBIGUOUS means real uncertainty,
not a resolver artifact: only symbols of the source's language family qualify, each endpoint is narrowed
to its role (a TYPE endpoint anchors on the :Class, a route/task/hook OWNER on the class then the file
module), and a Go edge resolves by PACKAGE: its dst inside the package its TypedEdge.scope names, its src
(when the declaring file does not define it — a GORM belongs-to's principal) inside the declaring file's
own package. A TypeScript route handler that is a member of an imported module object resolves inside that
module (TypedEdge.scope "module:" / "modsuffix:"), never by name across the repo; an imported TypeScript
type or name binds in the module its import names: "exact:" when typescript_edges located the definition
there (bound there or dropped), "pref:" when it could not (that module first, else the global rule; a
guessed alias never narrows); an import it placed nowhere in the tree is external and dropped.

WRITES are batched and transaction-agnostic (2026-09, round 2). Resolution is pure (resolve_typed_edges /
resolve_imports -> rows); write_edge_rows persists rows as UNWIND batches of at most _EDGE_BATCH whose
MATCH names each endpoint's LABEL (an index seek on deepmap_key_<label>), instead of one statement per
edge MATCHing both endpoints by key alone (two full node scans per edge). Every writer only calls
`.run(...)` on what it is handed, so sync runs it inside its edge transaction; rewrite_anchored_edges +
rows_by_anchor let sync split the rebuild into bounded transactions whose prune and re-create each cover
exactly one group of anchor nodes.

A recognizer switched OFF with DEEPMAP_RECOGNIZERS (round 7, sweep T8#9) leaves typed edges on nodes that
are no longer extracted, which no live-scoped prune reaches; prune_disabled_recognizer_edges removes them
(bounded rounds, run from rewrite_anchored_edges while any recognizer is disabled; nothing is sent when
all are enabled, the default).
"""
from __future__ import annotations

import re

from framework_edges import ALLOWED_EDGE_TYPES

# Decorator-anchored edges whose target is ALWAYS defined in the same file (the decorator sits on the
# def): a route handler, a lifecycle hook, a scheduled task. If the target is not a node in that file
# (e.g. a route handler nested inside a test function, which the extractor never node-ifies) the edge is
# DROPPED, never resolved by name to a same-named symbol in another file. Inheritance / DI / signals are
# NOT here — their target can legitimately be imported from another module.
SAME_FILE_STRICT = {"ROUTES_TO", "HOOKS", "SCHEDULED_AS"}

# Edge types persisted with SWAPPED direction: the recognizer emits them PRINCIPAL-first (src = the
# principal, dst = the dependent), but the graph edge must point INTO the principal so
# reverse-reachability (`deepmap_impact`) surfaces the dependents. CASCADES:
# `has_many :items, dependent: :destroy` is declared ON Order, but the edge is
# (LineItem)-[:CASCADES]->(Order) — "is destroyed with" — so impact("Order") lists LineItem (the
# exact blast-radius question of the 2026-07 Rails field report). The principal is normally the
# DECLARING symbol (Rails `dependent:`, JPA, EF HasMany, a GORM has-many / has-one, a TypeORM remove
# `cascade`), so src resolves same-file; for a GORM belongs-to (the key sits on the dependent), a TypeORM
# `onDelete` on a @ManyToOne / a @JoinColumn @OneToOne, or an EF HasOne…WithMany / third-file chain it is
# the REFERENCED type, declared in another file and resolved through GLOBAL_SRC_FALLBACK (inside its own
# package for Go). Either way a row is ANCHORED at its emitted src —
# the edge TARGET for these types — so their prunes anchor there, and the sync that re-emits a row
# replaces it.
REVERSED_EDGE_TYPES = {"CASCADES"}

# Edge types whose SRC may fall back to the GLOBAL name index when it is not a node in the
# declaring file — the EF Core third-file shape (C# plugin, 2026-07 field report): the fluent
# relationship for Order<->LineItem is declared in ShopContext.cs, where NEITHER endpoint is
# defined, so the same-file src rule alone would silently drop every fluent-API cascade. Bounded
# hard: these are TYPE-level relationships, so global candidates are restricted to :Class-labeled
# nodes (graphify names a file-Module after its stem, and the dominant C# convention names the
# file after the class — Order.cs -> Module 'Order' + Class 'Order' — so an unfiltered lookup is
# ALWAYS ambiguous there; proven on real repositories), then SINGLE-candidate only (a still-ambiguous global
# src DROPS the edge — never fan out, never fabricate), and the confidence grade is capped at
# INFERRED (never EXTRACTED). A Go edge (it carries a TypedEdge.scope) falls back inside the declaring
# file's PACKAGE only (_src_in_package): Go resolves a bare name in its package, and the one Go src not
# defined in the declaring file — a GORM belongs-to's principal — is a bare, same-package type, so a
# same-named type of ANOTHER package is never a candidate (fix-all r283, the O7 residual: such a name
# used to drop the edge when two packages declared it, and to bind the other package's type when the
# principal's own package did not node-ify it). Rails/Python srcs are the declaring symbol, so they
# resolve same-file first and normally never need the fallback — but it DOES fire for them when the
# declaring file has no node of that name, i.e. a file the engine never extracted: on the graphify path a
# virtualenv's `class Group(models.Model)`, or another branch's copy under `.claude/worktrees/`, resolved
# onto the project's own Group (ISS-build:022). That is why sync keeps such files from the recognizers
# (sync._apply_recognizers / _recognizer_exclusion: its vendor/.gitignore rule, plus the dirs DeepMap
# never treats as project source whatever the flags, worktree checkouts included). That rule is narrower
# than graphify's own skip, so the fallback can still fire: under the opt-in DEEPMAP_INCLUDE_VENDOR=1 for
# a .gitignore'd dir or a vendor dir graphify itself skips (coverage/, .next/, .nuxt/, .turbo/,
# .svelte-kit/ reach every recognizer, node_modules/ only the Python one; docs/known-limitations.md,
# "Some directories graphify skips"), and by default for a dir only an anchored or glob .gitignore
# pattern ignores (sync reads bare names only) — keep that seam when relying on this.
GLOBAL_SRC_FALLBACK = {"CASCADES", "HAS_MANY", "REFERENCES"}

# Endpoint ROLES (2026-09 live validation, #57). The narrowing above used to cover only the
# GLOBAL_SRC_FALLBACK types, so on the default graphify path every OTHER relationship between types
# fanned out across the file-Module + Class (+ Java constructor Method `X.X`) that all answer to `X`
# in a file-per-class repo: one `BookController -> BookService` ctor dependency became 9 AMBIGUOUS
# edges, and a constructor even "routed to" a handler at the EXTRACTED grade.
#   TYPE_LEVEL  both endpoints are declared TYPES (inheritance, implementation, ORM relations).
#               DEPENDS_ON joins them for every language except Python, where it is FastAPI's
#               function-level Depends(provider) (see _is_type_level).
#   OWNER_SRC   the src is the CONTAINER the wiring is declared in (a controller class, a file
#               module, a Rails route scope) and the dst is the callable it wires.
TYPE_LEVEL = {"INHERITS", "IMPLEMENTS", "HAS_MANY", "REFERENCES", "CASCADES"}
OWNER_SRC = {"ROUTES_TO", "SCHEDULED_AS", "TRIGGERS", "HOOKS"}
_CALLABLE_LABELS = {"Method", "Function", "Route"}

# Batched writes (round 2). _EDGE_BATCH bounds the rows of ONE UNWIND statement (the same 1000 as
# sync's node/:CALLS batches). A node label reaches Cypher LABEL position only when it is a plain
# identifier; anything else matches that endpoint by key alone (still a bound parameter), never
# interpolated.
_EDGE_BATCH = 1000
_SAFE_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Language FAMILY by source-file extension (#61). A relationship declared in one language can only
# bind to a symbol of the same family — a Rails `has_many :orders` never means a Java `class Order`,
# and the old language-blind name index turned every shared domain noun of a polyglot repo into
# fabricated cross-language CASCADES/REFERENCES (while demoting the real edges to AMBIGUOUS). Grouped
# where one project legitimately mixes the extensions (Java+Kotlin, C#+VB, JS+TS, a Rails .erb view).
# An extension missing here maps to None and is never filtered (conservative).
_FAMILY_EXTS = {
    "python": (".py", ".pyi", ".pyx", ".pxd"),
    "ruby": (".rb", ".erb", ".rake", ".ru", ".gemspec"),
    "dotnet": (".cs", ".vb", ".fs", ".fsx", ".razor", ".cshtml"),
    "jvm": (".java", ".kt", ".kts", ".scala", ".groovy", ".gradle"),
    "go": (".go",),
    "js": (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
           ".astro"),
    "c": (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".m", ".mm",
          ".cu", ".cuh", ".metal"),   # CUDA / Metal: C/C++-family (indexed since the graphify 0.8.51 range)
    "rust": (".rs",), "php": (".php",), "swift": (".swift",), "dart": (".dart",),
    "shell": (".sh", ".bash"), "powershell": (".ps1", ".psm1"), "lua": (".lua", ".luau"),
    "perl": (".pl", ".pm"), "r": (".r",), "sql": (".sql",), "zig": (".zig",),
    "elixir": (".ex", ".exs"), "julia": (".jl",), "verilog": (".v", ".sv", ".svh"),
    "apex": (".cls", ".trigger"), "dm": (".dm",),
    "pascal": (".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc"),
    "fortran": (".f", ".f90", ".f95", ".f03", ".f08"),
}
_EXT_FAMILY = {ext: fam for fam, exts in _FAMILY_EXTS.items() for ext in exts}


def lang_family(path) -> "str | None":
    """The language family of a source path by its (case-INSENSITIVE) extension, or None."""
    p = str(path or "")
    base = p.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return None
    return _EXT_FAMILY.get("." + base.rsplit(".", 1)[-1].lower())


def _is_type_level(e) -> bool:
    if e.edge_type in TYPE_LEVEL:
        return True
    return e.edge_type == "DEPENDS_ON" and lang_family(e.src_file) not in (None, "python")


def _dirname(path: str) -> str:
    p = str(path or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _by_name(n) -> bool:
    """False for a node a NAME may never bind: one carrying the private `call_by_name: False` (graphify_extract's C#
    computed properties and constructors — a bare `Name()` / `Order()` in any language must not fan out to
    `ShoppingCartDto.Name` / `Order.Order`). A marked CallKey still binds it: the push resolves keys by key."""
    return n.get("call_by_name") is not False


def build_name_index(nodes) -> dict:
    """The EXACT index push_to_neo4j once built inline (sync._resolve_calls uses it for :CALLS): full name AND
    last dotted segment -> list of node keys — plus the last `::` segment (Ruby: `Admin::Order` is findable as
    `Order`; a no-op alias for Python/graphify names without `::`, so the CALLS-path dict stays
    byte-identical for them). Factored out so both the CALLS push and the typed-edge push share one
    resolver without changing CALLS behavior. A node with `call_by_name: False` is left out (_by_name); every
    other node is indexed exactly as before."""
    idx: dict = {}
    for n in nodes:
        if not _by_name(n):
            continue
        for nm in {n["name"], n["name"].split(".")[-1], n["name"].split("::")[-1]}:
            idx.setdefault(nm, []).append(n["key"])
    return idx


class Resolver:
    """Resolves a TypedEdge's name endpoints to graph node keys — extractor-independent (keys off
    `name` + `source_file`, which both the graphify and ast extractors set identically)."""

    def __init__(self, nodes):
        self.name_to_keys = build_name_index(nodes)
        self._by_file: dict = {}
        self._module_by_file: dict = {}
        self._label_by_key: dict = {}
        self._name_by_key: dict = {}
        self._file_by_key: dict = {}
        self._line_by_key: dict = {}
        self._labels_by_key: dict = {}          # key -> every label it carries (write-side MATCH)
        for n in nodes:
            sf = n.get("source_file", "")
            self._label_by_key[n["key"]] = n.get("label")
            self._line_by_key[n["key"]] = n.get("start_line")
            labels = self._labels_by_key.setdefault(n["key"], [])
            if n.get("label") not in labels:
                labels.append(n.get("label"))
            self._name_by_key[n["key"]] = n["name"]
            self._file_by_key[n["key"]] = str(sf or "").replace("\\", "/")
            if _by_name(n):                     # a C# property / constructor is never a (file, name) endpoint
                for nm in {n["name"], n["name"].split(".")[-1], n["name"].split("::")[-1]}:
                    self._by_file.setdefault((sf, nm), []).append(n["key"])
            if n.get("label") == "Module":
                self._module_by_file.setdefault(sf, n["key"])
        self._dirs = {_dirname(f) for f in self._file_by_key.values()}
        self._import_dirs: dict = {}            # import path -> resolved repo dir(s), memoized

    def targets(self, name: str) -> list:
        """Candidate node keys for a target name (== the CALLS resolver's name_to_keys lookup)."""
        return self.name_to_keys.get(name, [])

    def source_keys(self, name: str, source_file: str) -> list:
        """Owning-symbol keys: prefer an exact (file, name) match (precise), else fall back to the
        global name index (covers a source_file that differs across extractors)."""
        return self._by_file.get((source_file, name)) or self.name_to_keys.get(name, [])

    def targets_in_file(self, name: str, source_file: str) -> list:
        """Target keys for a name DEFINED IN a specific file — the same-file-first resolution that keeps
        a LOCAL relationship (route->handler, DI, a same-file base) from fanning out to same-named
        symbols in other files."""
        return self._by_file.get((source_file, name), [])

    def resolves_in_file(self, name: str, source_file: str) -> bool:
        return bool(self._by_file.get((source_file, name)))

    def module_key_for_file(self, source_file: str):
        return self._module_by_file.get(source_file)

    def class_keys(self, keys) -> list:
        """The :Class-labeled subset of `keys` — the semantically valid candidates for a TYPE-level
        relationship endpoint (GLOBAL_SRC_FALLBACK types)."""
        return [k for k in keys if self._label_by_key.get(k) == "Class"]

    def as_type(self, keys) -> list:
        """A TYPE endpoint: the :Class candidates when any exist (never the same-named file-Module or
        a constructor Method beside them); otherwise the non-callable ones (a file-Module standing in
        for a type the engine did not node-ify). A function or method is never a type -> may be []."""
        cls = self.class_keys(keys)
        return cls or [k for k in keys if self._label_by_key.get(k) not in _CALLABLE_LABELS]

    def as_owner(self, keys) -> list:
        """A declaring CONTAINER: the :Class candidates, else the file-Module, else unchanged (a Rails
        route-scope :Method node is the owner of its routes). Never widens."""
        cls = self.class_keys(keys)
        if cls:
            return cls
        mods = [k for k in keys if self._label_by_key.get(k) == "Module"]
        return mods or list(keys)

    def as_callable(self, keys) -> list:
        """A wired HANDLER: the callable candidates when any exist, else unchanged. Never widens."""
        calls = [k for k in keys if self._label_by_key.get(k) in _CALLABLE_LABELS]
        return calls or list(keys)

    def same_family(self, keys, source_file) -> list:
        """Drop candidates written in a DIFFERENT language family than `source_file` (#61). A
        candidate whose family is unknown is kept (conservative); an unknown source family filters
        nothing."""
        fam = lang_family(source_file)
        if fam is None:
            return list(keys)
        out = []
        for k in keys:
            kf = lang_family(self._file_by_key.get(k, ""))
            if kf is None or kf == fam:
                out.append(k)
        return out

    def name_of(self, key) -> str:
        return self._name_by_key.get(key, "")

    def label_of(self, key):
        return self._label_by_key.get(key)

    def labels_of(self, key) -> list:
        """Every label a node key carries in this sync (normally one; a Pascal unit can be both a
        :Module and a :Class under one key). [] for a key this resolver never saw."""
        return list(self._labels_by_key.get(key, ()))

    def is_node_key(self, value) -> bool:
        return isinstance(value, str) and value in self._labels_by_key

    def file_of(self, key) -> str:
        return self._file_by_key.get(key, "")

    def line_of(self, key):
        return self._line_by_key.get(key)

    def dir_of(self, key) -> str:
        return _dirname(self._file_by_key.get(key, ""))

    def dirs_for_import(self, import_path: str) -> set:
        """The repo directory a Go import path names: the LONGEST node directory that is a
        path-segment-aligned suffix of it (`github.com/acme/shop/internal/models` -> `internal/models`;
        the prefix is the go.mod module path). A stdlib / third-party package names no repo dir -> {}.
        The repo root itself never matches (its import path IS the module path, unknowable here)."""
        ip = str(import_path or "").strip().strip("/")
        if ip not in self._import_dirs:
            best = ""
            for d in self._dirs:
                if d and (ip == d or ip.endswith("/" + d)) and len(d) > len(best):
                    best = d
            self._import_dirs[ip] = {best} if best else set()
        return self._import_dirs[ip]

    def dirs_named(self, pkg: str) -> set:
        """Directories whose last segment is `pkg` (a Go package NAME with no import to anchor it)."""
        return {d for d in self._dirs if d and d.rsplit("/", 1)[-1] == pkg}


def _grade(conf_hint: str, targets: list, same_file: bool) -> str:
    if conf_hint == "DYNAMIC":
        return "AMBIGUOUS"
    if len(targets) > 1:
        return "AMBIGUOUS"
    if conf_hint == "LOCAL" and same_file:
        return "EXTRACTED"
    return "INFERRED"


def prune_stale_edge_types(session, live_keys, present_types) -> int:
    """Prune the whitelisted edge types ABSENT from this sync's batch, scoped to live source keys.

    push_typed_edges prunes only the types PRESENT in its batch (and nothing at all on an empty
    batch), so a type whose LAST source occurrence was removed — or whose recognizer was disabled
    (DEEPMAP_RECOGNIZERS=0) — left PHANTOM edges on LIVE nodes that corrupt deepmap_impact: exactly
    the :CALLS bug-hunt HIGH, one layer up. This prunes them; together with push_typed_edges /
    push_imports' present-type prunes the whole vocabulary is pruned at every live node. (sync's
    batched push does the same per anchor group via rewrite_anchored_edges.) Live-scoped, so a fresh
    build or a subdir sync deletes nothing out of scope — which also means it can NOT reach a node that
    dropped OUT of the extraction (a deleted file's nodes, the last file of a language, a disabled
    recognizer's own supplemental nodes): those nodes and their edges are removed by --stale-cleanup
    (which /deep-map:refresh passes), and a disabled recognizer's typed edges on them by
    prune_disabled_recognizer_edges. Include "IMPORTS" in `present_types` when the imports batch is
    non-empty (push_imports prunes it itself then). Returns the number of stale types pruned. The edge
    type is in Cypher LABEL position and is substituted ONLY from ALLOWED_EDGE_TYPES (the same
    discipline as push_typed_edges)."""
    live = set(live_keys)
    if not live:
        return 0
    stale = ALLOWED_EDGE_TYPES - {str(t) for t in (present_types or ())}
    _prune_types(session, list(live), stale)
    return len(stale)


def _prune_types(session, anchors, types) -> None:
    """Delete the `types` edges ANCHORED at `anchors` — one statement per type. A forward type is
    anchored at its source; a REVERSED type at its target (the principal: the row's emitted src). The
    anchors are matched by key alone, so a same-key node under another label (a stale earlier
    extraction) loses its edges too — the :CALLS prune's rule. Types outside ALLOWED_EDGE_TYPES are
    never interpolated."""
    if not anchors:
        return
    keys = list(anchors)
    for et in sorted(t for t in types if t in ALLOWED_EDGE_TYPES):
        if et in REVERSED_EDGE_TYPES:   # reversed types anchor at the principal (the edge target)
            session.run(f"MATCH ()-[r:{et}]->(a) WHERE a.key IN $keys DELETE r", keys=keys)
        else:
            session.run(f"MATCH (a)-[r:{et}]->() WHERE a.key IN $keys DELETE r", keys=keys)


def _narrow(resolver: Resolver, e, keys, end: str) -> list:
    """Apply the endpoint ROLE of `e` (#57) to a candidate list (`end` is "src" or "dst")."""
    if _is_type_level(e):
        return resolver.as_type(keys)
    if e.edge_type in OWNER_SRC:
        return resolver.as_owner(keys) if end == "src" else resolver.as_callable(keys)
    return list(keys)


def _module_path(path) -> str:
    """A JS/TS source file the way a module specifier names it: the path without its extension, and an
    index file names its directory (`api/users.js` -> `api/users`, `api/orders/index.ts` -> `api/orders`)."""
    p = str(path or "").replace("\\", "/")
    head, _, base = p.rpartition("/")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if stem == "index":
        return head
    return f"{head}/{stem}" if head else stem


def _pref_module(scope) -> tuple:
    """(module scope, exact) of a TypeScript import scope: "pref:<m>" prefers the module <m> and falls back to
    the global name rule (typescript_edges could not tell where the name is defined: a module outside the
    tree, an export it could not follow); "exact:<m>" binds in <m> or nowhere (finalize found the definition
    there). <m> is "module:<p>" / "modsuffix:<p>"; any other kind ("spec:<specifier>", an import finalize
    could not place in the tree) binds nothing. ("", False) for every other scope."""
    s = str(scope or "")
    if s.startswith("pref:"):
        return s[len("pref:"):], False
    if s.startswith("exact:"):
        return s[len("exact:"):], True
    return "", False


def _in_module(resolver: Resolver, keys, mscope: str, name: str) -> list:
    """The `keys` named exactly `name` that are defined in the module `mscope` names (see _module_path). A
    `#...` note after the path (what typescript_edges' finalize follows) is not part of it."""
    kind, _, arg = str(mscope).partition(":")
    arg = arg.split("#", 1)[0]
    if kind not in ("module", "modsuffix") or not arg:
        return []
    out = []
    for k in keys:
        if resolver.name_of(k) != name:
            continue
        mp = _module_path(resolver.file_of(k))
        if mp == arg or (kind == "modsuffix" and mp.endswith("/" + arg)):
            out.append(k)
    return out


def _line_hint(scope) -> tuple:
    """(scope, src line, dst line, strict): a TypeScript declaration-line hint "L<src>,<dst>|" taken off the
    front of a scope (0: none). typescript_edges sets it on INHERITS / IMPLEMENTS (both ends) and on every import
    its post-pass placed (the definition's line): an endpoint may be one of several same-named :Class nodes of a
    file (zod's `interface X` beside `const X = ...`). A dst line ending in "!" is strict: that module defines
    the name twice (an `exports.x = <expr>` beside a declaration of x), so only a node at the line is it."""
    s = str(scope or "")
    if s[:1] == "L" and "|" in s:
        head, _, rest = s.partition("|")
        a, _, b = head[1:].partition(",")
        strict = b.endswith("!")
        b = b[:-1] if strict else b
        if a.isdigit() and b.isdigit():
            return rest, int(a), int(b), strict
    return s, 0, 0, False


def _at_line(resolver: Resolver, keys, line: int, strict: bool = False) -> list:
    """The one candidate declared at `line` when several share the name and exactly one is there, else `keys`
    unchanged (a hint that matches nothing or several never drops or picks). A strict hint keeps only the
    candidates at `line`: none there drops the edge."""
    if strict and line:
        return [k for k in keys if resolver.line_of(k) == line]
    if not line or len(keys) < 2:
        return keys
    hit = [k for k in keys if resolver.line_of(k) == line]
    return hit if len(hit) == 1 else keys


def _pref_keys(resolver: Resolver, keys, mscope: str, exact: bool, name: str) -> list:
    """The candidates of a TypeScript import scope: an exact module's own symbol or nothing; a preferred
    module's own symbol, else every candidate (the global rule). A guessed alias ("modsuffix:") never narrows
    several candidates to the one whose path happens to end with it: an alias no tsconfig maps may name a
    directory laid out unlike its path (`@app/common` -> libs/common/src), so the global rule decides."""
    if exact:
        return _in_module(resolver, keys, mscope, name)
    if mscope.startswith("modsuffix:"):
        return list(keys)
    return _in_module(resolver, keys, mscope, name) or list(keys)


def _scoped_targets(e, resolver: Resolver) -> tuple:
    """DST candidates for an edge carrying a SCOPE: a Go package scope (go_edges; #59/#102/#103) or a
    TypeScript module scope (typescript_edges).

    Go names resolve by PACKAGE, not repo-wide: a bare identifier is declared in the source's own
    package (= its directory, any file in it) and `pkg.X` names the package its import path points
    at. Exact-name matches only — a bare `ListCustomers` is a package-level func or type, never some
    `T.ListCustomers` method. A TypeScript handler that is a member of an imported module object
    (`users.remove` with `users = require('./users')`) resolves inside THAT module: "module:<path>" is the
    module a relative import names, "modsuffix:<path>" any module whose path ends with <path> (an alias
    import); the name defined there wins, else a same-named method of a class defined there (a module that
    exports a class instance). Returns (keys, same_file); an unresolvable scope yields []."""
    kind, _, arg = str(e.scope).partition(":")
    dst = e.dst_name
    if kind in ("module", "modsuffix"):
        arg = arg.split("#", 1)[0]             # a note for typescript_edges' finalize, not part of the path
        if not arg:
            return [], False
        mname = dst.rsplit(".", 1)[-1]

        def in_module(k) -> bool:
            mp = _module_path(resolver.file_of(k))
            return mp == arg or (kind == "modsuffix" and mp.endswith("/" + arg))

        cands = [k for k in resolver.same_family(resolver.targets(mname), e.src_file)
                 if in_module(k) and (resolver.name_of(k) == mname or resolver.name_of(k).endswith("." + mname))]
        exact = [k for k in cands if resolver.name_of(k) == mname]
        return _narrow(resolver, e, exact or cands, "dst"), False
    if kind == "method":
        # A method VALUE (`h.GetCustomer`): dst is `Type.Method` when the recognizer saw the type of
        # the receiver, else the bare `Method`. The exact `Type.Method` wins; otherwise any :Method
        # named `<Type>.Method` in this language family, graded by how many there are.
        mname = dst.rsplit(".", 1)[-1]
        cands = [k for k in resolver.same_family(resolver.targets(mname), e.src_file)
                 if resolver.label_of(k) == "Method"
                 and resolver.name_of(k).endswith("." + mname)]
        exact = [k for k in cands if resolver.name_of(k) == dst] if "." in dst else []
        return _narrow(resolver, e, exact or cands, "dst"), False
    if kind == "package":
        in_file = [k for k in resolver.targets_in_file(dst, e.src_file) if resolver.name_of(k) == dst]
        in_file = _narrow(resolver, e, in_file, "dst")
        if in_file:
            return in_file, True
        dirs = {_dirname(e.src_file)}
    elif kind == "import":
        dirs = resolver.dirs_for_import(arg)
    elif kind == "pkgname":
        dirs = resolver.dirs_named(arg)
    else:
        return [], False                   # an unknown scope never degrades to a repo-wide guess
    cands = [k for k in resolver.same_family(resolver.targets(dst), e.src_file)
             if resolver.name_of(k) == dst and resolver.dir_of(k) in dirs]
    return _narrow(resolver, e, cands, "dst"), False


def _src_in_package(resolver: Resolver, e, keys) -> list:
    """The global-fallback SRC candidates of an edge carrying a Go package scope, narrowed to the PACKAGE of
    the file that declares the edge (its directory) — exact-name matches there only, the rule
    _scoped_targets applies to a bare dst ("package"). Every Go src is declared in that package: the struct
    whose body holds the field, the file's module, or a GORM belongs-to's PRINCIPAL, which go_edges emits
    only for a bare (same-package) type. So a principal name two packages declare binds its own package's
    type instead of dropping, and a bare principal its own package does not node-ify (a type alias, code
    generated after the sync) drops instead of binding another package's same-named type."""
    pkg = _dirname(e.src_file)
    return [k for k in keys if resolver.dir_of(k) == pkg and resolver.name_of(k) == e.src_name]


def resolve_typed_edges(typed_edges, resolver: Resolver, live_keys) -> list:
    """PURE: TypedEdges -> the edges to persist, as ordered, deduped rows
    (edge_type, src_key, dst_key, confidence, attrs). `src_key` is the row's ANCHOR, its emitted src:
    normally the declaring endpoint; for a REVERSED_EDGE_TYPES row the principal (see
    REVERSED_EDGE_TYPES), and the graph edge points dst -> src. No session and no Cypher here —
    push_typed_edges is the persisting shell, and a batched writer can consume the rows directly."""
    live = set(live_keys)
    rows: list = []
    if not typed_edges or not live:
        return rows
    seen: set = set()
    for e in typed_edges:
        if e.edge_type not in ALLOWED_EDGE_TYPES:
            continue
        # The SRC (owning symbol: a class, the module, or a handler) is normally defined in e.src_file —
        # framework_edges read it from that file. Resolve it same-file-FIRST so a nested route handler
        # the extractor never node-ified does not fan the edge out to same-named top-level symbols
        # elsewhere, then narrow it to its ROLE: a type-level relationship anchors on the :Class (never
        # the same-named file-Module or a Java constructor `X.X`), a route/task/hook owner prefers the
        # class, then the file module. For GLOBAL_SRC_FALLBACK types only (the EF third-file shape, a
        # GORM belongs-to's principal), a src absent from the declaring file may resolve via the global
        # name index — SINGLE same-language :Class candidate only (inside the declaring file's package
        # for a Go edge), graded at most INFERRED. Otherwise drop the edge.
        raw_scope = getattr(e, "scope", "")
        scope, src_line, dst_line, strict = _line_hint(raw_scope)
        pref, exact = _pref_module(scope)              # a TypeScript import scope (never a Go one)
        if pref and pref.partition(":")[0] not in ("module", "modsuffix"):
            continue                                   # an import placed nowhere in the tree: external, drop
        src_keys = [k for k in resolver.targets_in_file(e.src_name, e.src_file) if k in live]
        src_keys = _at_line(resolver, _narrow(resolver, e, src_keys, "src"), src_line)
        src_global = False
        if exact and src_keys and e.edge_type in GLOBAL_SRC_FALLBACK \
                and _in_module(resolver, resolver.targets(e.src_name), pref, e.src_name) \
                and not _in_module(resolver, resolver.targets(e.dst_name), pref, e.dst_name):
            # the exact module defines the src, not the dst: the import names the SRC (a TypeORM onDelete
            # principal, emitted principal first), so a class of its name in the declaring file (`User as Owner`
            # beside a local `class User`) is not it — the global fallback below binds it in that module
            src_keys = []
        if not src_keys and e.edge_type in GLOBAL_SRC_FALLBACK:
            # Ambiguity is judged over ALL nodes BEFORE the live filter, so an out-of-scope
            # duplicate (subdir sync) can never silently disambiguate a genuinely ambiguous name.
            gks = resolver.class_keys(resolver.same_family(resolver.targets(e.src_name), e.src_file))
            if pref:
                gks = _pref_keys(resolver, gks, pref, exact, e.src_name)   # the module its import names first
            elif scope:
                gks = _src_in_package(resolver, e, gks)    # a Go src resolves in its own PACKAGE only
            if len(gks) == 1 and gks[0] in live:
                src_keys, src_global = gks, True
        if not src_keys:
            continue
        # DST: prefer the same-file definition (a handler/base name like `read_item`/`main` repeats
        # across fastapi's tutorials — never fan out repo-wide). If it is not same-file: a decorator-
        # anchored edge DROPS (the target must be same-file); inheritance/DI/etc. fall back to the global
        # name index (their target can be imported from another module), restricted to the source's
        # language family and narrowed to the endpoint role. A Go edge resolves by package instead; a
        # TypeScript import-preferred ("pref:") edge takes the candidates of the module its import names
        # and falls back to that same global rule when the module defines none, and an "exact:" one binds
        # in its module or nowhere (_pref_keys). Such an import names the DST when the src is the declaring
        # file's own symbol, so the file's same-named symbol is never it (`import { create as createUser }`
        # beside a local `create`), neither directly nor through the "pref:" global fallback; a TypeORM onDelete
        # principal (the imported SRC) keeps its local dst. A strict line hint binds only a node at that line.
        if scope and not pref:
            tgt_keys, same_file = _scoped_targets(e if scope == raw_scope else e._replace(scope=scope), resolver)
        else:
            in_file = [] if (pref and not src_global) else _at_line(
                resolver, _narrow(resolver, e, resolver.targets_in_file(e.dst_name, e.src_file), "dst"), dst_line,
                strict)
            if in_file:
                tgt_keys, same_file = in_file, True
            elif e.edge_type in SAME_FILE_STRICT and e.conf_hint == "LOCAL":
                # A LOCAL (decorator/attribute-anchored) strict edge — a Flask route, an EF attribute
                # route, a Rails callback — sits literally on the handler def, so its target MUST be a
                # node in this file; not same-file -> drop, never resolve by name to a same-named symbol
                # elsewhere. A NAME/DYNAMIC-hinted strict edge (a Rails config/routes.rb route or a C#
                # minimal-API method-group handler, whose target is legitimately in ANOTHER file) falls
                # through to the global name index below — still drop-if-unresolved, graded at most
                # INFERRED. (A Go route carries a package scope instead — handled above.)
                continue                               # target not a node in this file -> drop, don't fabricate
            else:
                tgt_keys = resolver.same_family(resolver.targets(e.dst_name), e.src_file)
                if pref:
                    if not src_global:
                        here = str(e.src_file).replace("\\", "/")
                        tgt_keys = [k for k in tgt_keys if resolver.file_of(k) != here]
                    tgt_keys = _pref_keys(resolver, tgt_keys, pref, exact, e.dst_name)
                tgt_keys = _at_line(resolver, _narrow(resolver, e, tgt_keys, "dst"), dst_line, strict)
                same_file = False
        if not tgt_keys:
            continue                                   # external/unresolved target -> drop, never fabricate
        conf = _grade(e.conf_hint, tgt_keys, same_file)
        if src_global and conf == "EXTRACTED":
            conf = "INFERRED"       # a globally-resolved src is never a literal same-file fact
        attrs = e.attrs or {}
        for sk in src_keys:
            for tk in tgt_keys:
                if sk == tk or (sk, tk, e.edge_type) in seen:
                    continue
                seen.add((sk, tk, e.edge_type))
                rows.append((e.edge_type, sk, tk, conf, attrs))
    return rows


def push_typed_edges(session, typed_edges, resolver: Resolver, live_keys) -> dict:
    """MERGE the framework/runtime edges. Prune-then-recreate PER edge type, scoped to live source keys
    (the anti-phantom discipline of :CALLS): a relation removed from source disappears on re-sync; on a
    fresh build these nodes have no such edges yet so the prune deletes nothing.

    The edge type is in Cypher LABEL position, so it can NOT be a bound parameter — it is substituted
    ONLY from ALLOWED_EDGE_TYPES (the same interpolation the node-label MERGE already uses). Endpoints/
    attrs are always bound parameters. Name resolution + grading live in the pure resolve_typed_edges;
    the rows are written in label-qualified UNWIND batches (write_edge_rows). Only `.run` is called on
    `session`, so it may be a Transaction. Returns per-type created counts."""
    live = set(live_keys)
    if not typed_edges or not live:
        return {}
    present = {e.edge_type for e in typed_edges if e.edge_type in ALLOWED_EDGE_TYPES}
    _prune_types(session, list(live), present)         # prune BEFORE recreate (per type, live-scoped)
    return write_edge_rows(session, resolve_typed_edges(typed_edges, resolver, live), resolver)


def _match_labels(resolver: Resolver, key) -> list:
    """The labels to MATCH `key` under — every label it carries in this sync that is a plain
    identifier; [None] (match by key alone) for a key the resolver never saw or an unsafe label."""
    safe = [lb for lb in resolver.labels_of(key) if isinstance(lb, str) and _SAFE_LABEL.match(lb)]
    return safe or [None]


def _endpoint(var: str, label) -> str:
    return f"({var}:{label} {{key:row.{var}}})" if label else f"({var} {{key:row.{var}}})"


def write_edge_rows(session, rows, resolver: Resolver) -> dict:
    """Persist resolved edge rows `(edge_type, src_key, dst_key, confidence, attrs)` — the shape
    resolve_typed_edges / resolve_imports return, src = the row's anchor (its emitted src) — WITHOUT pruning.

    One UNWIND statement per (edge type, src label, dst label) and batch of at most _EDGE_BATCH rows;
    each endpoint is MATCHed under its label (an index seek on deepmap_key_<label>, not a full node
    scan). A key carrying two labels in this sync is MATCHed under both, which is what the old
    key-only MATCH created. A REVERSED_EDGE_TYPES row persists dst -> src. :IMPORTS carry no attrs.
    Rows with an edge type outside ALLOWED_EDGE_TYPES are skipped (never interpolated). Only `.run`
    is called. Returns per-type counts of rows written."""
    grouped: dict = {}
    counts: dict = {}
    for row in rows or ():
        et, sk, tk, conf, attrs = row
        if et not in ALLOWED_EDGE_TYPES:
            continue
        item = {"a": sk, "b": tk, "c": conf}
        if et != "IMPORTS":
            item["attrs"] = dict(attrs or {})
        for la in _match_labels(resolver, sk):
            for lb in _match_labels(resolver, tk):
                grouped.setdefault((et, la, lb), []).append(item)
        counts[et] = counts.get(et, 0) + 1
    for (et, la, lb), items in grouped.items():
        # Reversed persistence: the PRINCIPAL (a, e.g. Order) becomes the edge TARGET so
        # reverse-reachability from it surfaces the dependents (CASCADES).
        merge = f"MERGE (b)-[r:{et}]->(a)" if et in REVERSED_EDGE_TYPES else f"MERGE (a)-[r:{et}]->(b)"
        sets = "SET r.confidence=row.c" if et == "IMPORTS" else "SET r.confidence=row.c, r += row.attrs"
        cypher = (f"UNWIND $rows AS row MATCH {_endpoint('a', la)} MATCH {_endpoint('b', lb)} "
                  f"{merge} {sets}")
        for i in range(0, len(items), _EDGE_BATCH):
            session.run(cypher, rows=items[i:i + _EDGE_BATCH])
    return counts


def normalize_imports(imports, resolver: Resolver, node_keys) -> list:
    """Reconcile the two import tuple shapes structurally (no need to know which extractor ran):
    graphify -> (src_node_key, target_name); ast -> (source_file_rel, dotted_module). Returns
    (src_node_key, resolvable_target_name) pairs. Malformed entries are dropped (fuzz-safe)."""
    keys = set(node_keys)
    out: list = []
    for imp in imports:
        if not (isinstance(imp, (tuple, list)) and len(imp) >= 2):
            continue
        src, raw = imp[0], imp[1]
        if not isinstance(src, str) or not isinstance(raw, str):
            continue
        if src in keys:                                # graphify: source is already a node key
            out.append((src, raw))
        else:                                          # ast: source is a file relpath
            mkey = resolver.module_key_for_file(src)
            if mkey:
                out.append((mkey, raw.split(".")[-1]))  # resolve the module by its last dotted segment
    return out


def resolve_imports(imports, resolver: Resolver, live_keys, node_keys) -> list:
    """PURE: import tuples -> the :IMPORTS rows to persist, ordered and deduped, in resolve_typed_edges'
    row shape ("IMPORTS", src_key, dst_key, confidence, {}). An import is a literal declaration
    (EXTRACTED); a last-segment name resolving to >1 module is AMBIGUOUS. External/stdlib modules
    resolve to no node and are dropped. Candidates are limited to the importer's language family (a
    TypeScript `index` never binds a Python `index` module).

    A target that already IS a node key of this sync (graphify_extract's resolved-import-key contract)
    binds exactly that node, EXTRACTED, with no fan-out — when it is in the importer's language family
    (or either family is unknown). A node key of ANOTHER known family is dropped, never re-resolved:
    graphify ids an import it cannot resolve (Node's `util`/`events`, the npm `config` package) by the
    sanitized specifier, which is also the id of a ROOT-level file (`util.py` -> `util`), so that
    "resolution" is an id collision. The engine never resolves an import across language families, and
    without the colliding file the same import is a dangling external that map_graph drops anyway."""
    live = set(live_keys)
    rows: list = []
    if not imports or not live:
        return rows
    seen: set = set()
    for src_key, dst_name in normalize_imports(imports, resolver, node_keys):
        if src_key not in live:
            continue
        if resolver.is_node_key(dst_name):
            cands = resolver.same_family([dst_name], resolver.file_of(src_key))
        else:
            cands = resolver.same_family(resolver.targets(dst_name), resolver.file_of(src_key))
        tks = [tk for tk in cands if tk != src_key]
        conf = "AMBIGUOUS" if len(tks) > 1 else "EXTRACTED"
        for tk in tks:
            if (src_key, tk) in seen:
                continue
            seen.add((src_key, tk))
            rows.append(("IMPORTS", src_key, tk, conf, {}))
    return rows


def push_imports(session, imports, resolver: Resolver, live_keys, node_keys) -> int:
    """Persist :IMPORTS (previously extracted but dropped): prune-then-recreate, live-scoped, like the
    typed edges; resolution in the pure resolve_imports, writes batched (write_edge_rows). Only `.run`
    is called on `session`. Returns the number of edges written."""
    live = set(live_keys)
    if not imports:
        return 0
    if live:
        _prune_types(session, list(live), {"IMPORTS"})
    rows = resolve_imports(imports, resolver, live, node_keys)
    return write_edge_rows(session, rows, resolver).get("IMPORTS", 0)


# ---- bounded-transaction API (sync.push_to_neo4j) ------------------------------------------------
# A caller that splits the edge rebuild across several write transactions needs every edge to be
# pruned and re-created in the SAME transaction as the rest of its group. Every typed/:IMPORTS row is
# ANCHORED at its emitted src (row[1]): a forward edge is pruned at its source, a reversed one
# (CASCADES) at its target — the principal, which is the declaring node except for a GORM belongs-to
# or an EF HasOne…WithMany / third-file chain (there the referenced type, possibly in another file).
# So: resolve once (edge_rows), bucket by anchor (rows_by_anchor, to count rows per group), and per
# transaction call rewrite_anchored_edges with that group's anchors. Splitting the anchors across
# transactions persists exactly the graph a single rewrite would.
def edge_rows(typed_edges, imports, resolver: Resolver, live_keys, node_keys) -> list:
    """PURE: every typed row (resolve_typed_edges) followed by every :IMPORTS row (resolve_imports)."""
    return (resolve_typed_edges(typed_edges or [], resolver, live_keys)
            + resolve_imports(imports or [], resolver, live_keys, node_keys))


def rows_by_anchor(rows) -> dict:
    """PURE: {anchor key (the row's emitted src, row[1] — the principal for a reversed type): [its rows,
    in order]}."""
    out: dict = {}
    for row in rows or ():
        out.setdefault(row[1], []).append(row)
    return out


def rewrite_anchored_edges(session, anchors, rows, resolver: Resolver) -> dict:
    """Inside ONE transaction: prune the WHOLE typed/:IMPORTS vocabulary anchored at `anchors`, then
    write the `rows` anchored at them (rows anchored elsewhere are ignored — another group writes
    them). Only `.run` is called. Returns per-type counts written; no anchors -> no statements.

    While a recognizer is DISABLED (DEEPMAP_RECOGNIZERS), each call also runs one bounded round of
    prune_disabled_recognizer_edges in the same transaction until a round reports done — tracked on
    `resolver`, which sync builds once per push — so the stale typed edges of that recognizer go even
    where they hang off orphan nodes. With every recognizer enabled (the default) that sends nothing."""
    anchors = list(dict.fromkeys(anchors or ()))
    if not anchors:
        return {}
    if not getattr(resolver, "_disabled_prune_done", False):
        done = prune_disabled_recognizer_edges(session)
        try:
            resolver._disabled_prune_done = done
        except Exception:  # noqa: BLE001 — a resolver stand-in without attributes just re-checks
            pass
    _prune_types(session, anchors, ALLOWED_EDGE_TYPES)
    mine = set(anchors)
    return write_edge_rows(session, [r for r in rows or () if r[1] in mine], resolver)


# ---- a DISABLED recognizer's typed edges (sweep T8#9) ---------------------------------------------
# The live-scoped prunes above cannot reach a node that dropped out of the extraction. When a recognizer
# is switched off (DEEPMAP_RECOGNIZERS=python, =0, ...), the supplemental nodes only IT emitted (a Rails
# view, a routes.rb scope, a graphify-less C# entity) are no longer live, so the typed edges anchored at
# them survived every plain sync as phantoms in deepmap_impact (and the >50% wipe guard can refuse the
# --stale-cleanup that would remove the nodes). A recognizer's typed edges always anchor at a node of
# ITS language (resolution is language-family scoped — a reversed row's principal too, e.g. a Go
# belongs-to's referenced type is a .go node), so they are exactly the typed edges whose anchor node's
# source_file has that language's extension. Bounded per statement; never :CALLS or :IMPORTS
# (engine-owned); the edge type list is whitelist-interpolated like every other prune.
_DISABLED_PRUNE_BATCH = 5000


def disabled_recognizer_scope() -> tuple:
    """(forward types, reversed types, lowercase extensions) covering the recognizers DEEPMAP_RECOGNIZERS
    disables; three empty lists when none is disabled (the default). An entry's extensions are widened
    to its whole language family (a C#-declared relationship can anchor at a same-family .vb node).
    Never raises."""
    try:
        import language_recognizers as _lr   # lazy: keeps this module importable without the registry
        entries = _lr.disabled_entries()
    except Exception:  # noqa: BLE001
        return [], [], []
    types: set = set()
    exts: set = set()
    for entry in entries:
        types |= {str(t) for t in getattr(entry, "edge_types", ())}
        for ext in getattr(entry, "extensions", ()):
            low = str(ext).lower()
            exts.add(low)
            fam = _EXT_FAMILY.get(low)
            if fam:
                exts |= set(_FAMILY_EXTS[fam])
    types = {t for t in types if t in ALLOWED_EDGE_TYPES and t != "IMPORTS" and _SAFE_LABEL.match(t)}
    fwd = sorted(t for t in types if t not in REVERSED_EDGE_TYPES)
    rev = sorted(t for t in types if t in REVERSED_EDGE_TYPES)
    return fwd, rev, sorted(exts)


def _deleted_count(result) -> int:
    """The `d` a bounded delete returned; an unreadable result counts as a finished round (0), so a
    caller looping until done can never spin."""
    try:
        rec = result.single()
        return int(rec["d"] or 0)
    except Exception:  # noqa: BLE001
        return 0


def prune_disabled_recognizer_edges(session, limit: "int | None" = None) -> bool:
    """Delete, in at most `limit` (default _DISABLED_PRUNE_BATCH) relationships per statement, the typed edges of every DISABLED
    recognizer: a forward type anchored at its source, a reversed one (CASCADES) at its target — the
    anchor node (the principal) — whose source_file ends (case-insensitively) with that recognizer's extensions.
    Returns True when a round deleted fewer than `limit` per statement (done), False when more may
    remain (call again — rewrite_anchored_edges does, once per anchor group, until done). Nothing
    disabled -> no statement at all, True. Only `.run` is called, so it runs inside a transaction."""
    fwd, rev, exts = disabled_recognizer_scope()
    if not exts or not (fwd or rev):
        return True
    n = max(1, int(_DISABLED_PRUNE_BATCH if limit is None else limit))
    where = "WHERE any(x IN $exts WHERE toLower(coalesce(a.source_file, '')) ENDS WITH x)"
    done = True
    for types, pattern in ((fwd, "(a)-[r:{t}]->()"), (rev, "()-[r:{t}]->(a)")):
        if not types:
            continue
        cypher = (f"MATCH {pattern.format(t='|'.join(types))} {where} "
                  "WITH r LIMIT $n DELETE r RETURN count(*) AS d")
        if _deleted_count(session.run(cypher, exts=exts, n=n)) >= n:
            done = False
    return done
