#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C#/.NET recognizer — EF Core / ASP.NET Core declarations -> typed edges + ensured nodes (plugin #3).

The 2026-07 field report: on a .NET project (two EF Core DbContexts), DeepMap's `deepmap_impact`
could not trace the declarative wiring — EF navigation properties, the fluent API
(`HasMany/WithOne/OnDelete`), attribute routes, and DI registrations are *declarations*, not calls,
so graphify yields no relationship for them (ground truth: the captured fixture
tests/fixtures/graphify_multilang/csharp_ef.json — no `calls` links at all, and map_graph reads
none of graphify's `inherits`/`implements`/`references` relations). This module reads those
conventions HEURISTICALLY (line-oriented regex — no Roslyn, no dotnet runtime; the shapes are
strongly regex-tractable) and emits, per file:

  typed edges (the value):
    INHERITS    class Foo : Bar          non-I-convention base (interface bases are INHERITS too —
                                         interface inheritance); LOCAL when declared in this file
                                         (a namespace-QUALIFIED base never grades LOCAL)
    IMPLEMENTS  class Foo : IBar         I-convention base (leading 'I' + uppercase) on a class
    HAS_MANY    DbSet<T> / collection navigation property / fluent .HasMany(...)   owner -> element
    REFERENCES  bare-class navigation property / fluent .HasOne(...)   holder -> referenced type
                (BCL/scalar property types are NEVER emitted — see _NON_ENTITY_TYPES)
    CASCADES    .OnDelete(DeleteBehavior.Cascade|ClientCascade|SetNull|ClientSetNull)
                — the "is deleted/affected with" blast-radius edge, principal -> dependent.
                NOTE: persisted REVERSED (dependent)-[:CASCADES]->(principal) by edge_resolve, so
                `deepmap_impact("Order")` (reverse reachability) lists what a delete of Order
                cascades to. Restrict/NoAction/ClientNoAction PREVENT deletion — deliberately NOT
                CASCADES (mirrors Rails restrict_with_error). EXPLICIT OnDelete only: EF's
                by-convention cascade on required relationships is NOT emitted (guessing model
                intent would fabricate blast-radius claims).
    ROUTES_TO   [HttpGet("...")] / [Route("...")] attribute routes: controller class -> action
                method (`Class.Method`, byte-matching graphify's Method names), with
                [controller]/[action] substituted in the composed path. SAME_FILE_STRICT:
                resolve-or-drop, like the Python route path.
    DEPENDS_ON  AddScoped/AddSingleton/AddTransient<IFoo, Foo>() (+ typeof(...) forms):
                registration site (enclosing class, or the file stem for top-level statements)
                -> BOTH the service and the implementation, with a lifetime attr.

  supplemental nodes (the fallback): one ensured :Class node per class/interface/record/struct
  declaration, named BARE exactly as graphify names C# types under BOTH namespace forms (verified
  against the captured fixture), with a DSL-derived deterministic body_summary. When graphify
  parsed the file, merge_supplemental's (name, source_file) dedupe drops these (base wins); when
  graphify missed the repo (the ast fallback is Python-only — a graphify-less C# repo has NO base
  nodes at all) they keep the entity layer present + searchable and give the cascade edges anchors.

The EF third-file shape (unlike Rails): the fluent relationship for Order<->LineItem is declared in
the DbContext file, where NEITHER endpoint is defined — so fluent-API edges are emitted with the
declaring file as src_file and rely on edge_resolve.GLOBAL_SRC_FALLBACK (:Class-labeled,
single-candidate global src resolution for CASCADES/HAS_MANY/REFERENCES only; ambiguous -> DROP;
grade capped at INFERRED). `IEntityTypeConfiguration<X>` classes anchor their anchor-less chains
on the interface's generic argument.

Honesty model (the confidence wedge absorbs heuristic error; review-hardened):
  * comment text can NEVER fabricate an edge: `//` line comments and `/* */` block spans are
    stripped BEFORE any matching, string-aware (a `//` inside a string literal is not a comment,
    and a `/*` inside a `//` comment opens nothing);
  * fluent-chain state is scoped to the `;`-terminated STATEMENT (split per segment), so an
    OnDelete can never bind to a neighbouring statement's relationship;
  * name resolution can DROP an edge (unresolvable target), never fabricate one — a wrong
    PascalCase singularization of a fluent HasMany lambda property resolves to nothing and
    disappears (HasOne properties are reference navigations and are NOT singularized). Since round 7
    the registry's cross-file post-pass (finalize / resolve_navigation_targets) first replaces that
    guess with the navigation property's DECLARED type (`HasMany(i => i.Lines)` on an Invoice that
    declares `List<InvoiceLine> Lines` -> InvoiceLine, following base classes), so the declared
    cascade is kept; the guess survives only when no single declared type is known;
  * a name resolving to >1 symbol grades AMBIGUOUS (verify by reading);
  * targets are DEMODULIZED (`Shop.Models.Order` -> `Order`) to match graphify's bare-name shape
    (the raw dotted string is kept in attrs when it was qualified);
  * a one-to-one whose principal cannot be determined (no generic HasForeignKey<T>) emits NO
    cascade — dropped, never guessed.

Pure: recognize(source_text, rel_path) only — no fs, no network; the registry's shared walker owns
file I/O, ignore dirs (bin/, obj/, .vs/, vendor/, node_modules/) and per-file error isolation.
"""
from __future__ import annotations

import re
from pathlib import Path

import sys as _sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from framework_edges import TypedEdge  # noqa: E402  (the shared edge shape; endpoints name+file)

# ---- C# / EF / ASP.NET vocabulary ---------------------------------------------
_MODS = r'(?:public|internal|protected|private|abstract|sealed|static|partial|new|file|readonly)'
_DECL_RE = re.compile(
    rf'^\s*(?:{_MODS}\s+)*'
    r'(class|interface|record(?:\s+(?:class|struct))?|struct)\s+'
    r'([A-Z]\w*)\s*(?:<[^>]*>)?\s*(?:\([^)]*\))?\s*(?::\s*([^{{]+?))?\s*(?:\{.*)?$')
_PROP_MODS = r'(?:public|internal|protected|private|static|virtual|override|required|new|sealed|abstract)'
_PROP_RE = re.compile(
    rf'^\s*((?:{_PROP_MODS}\s+)*)'
    r'([A-Za-z_][\w.]*(?:<[^={{]*?>)?\??)\s+'
    r'([A-Z]\w*)\s*\{\s*get\b')
_ATTR_RE = re.compile(r'\[\s*(\w+)(?:\s*\(\s*"([^"]*)")?')
_METHOD_RE = re.compile(
    rf'^\s*(?:(?:{_MODS}|async|override|virtual)\s+)*'
    r'[\w.<>\[\],\s?]+?\s+([A-Za-z_]\w*)\s*\(')
_ENTITY_RE = re.compile(r'\.Entity\s*<([^<>]+)>\s*\(')
_HASMANY_RE = re.compile(r'\.HasMany\s*(?:<([^<>]+)>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.(\w+))?')
_HASONE_RE = re.compile(r'\.HasOne\s*(?:<([^<>]+)>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.(\w+))?')
_WITHMANY_RE = re.compile(r'\.WithMany\s*\(')
_WITHONE_RE = re.compile(r'\.WithOne\s*\(')
_FK_GENERIC_RE = re.compile(r'\.HasForeignKey\s*<([^<>]+)>')
_ONDELETE_RE = re.compile(r'\.OnDelete\s*\(\s*DeleteBehavior\.(\w+)')
_DI_GENERIC_RE = re.compile(r'\.Add(Scoped|Singleton|Transient)\s*<([^;()]+?)>\s*\(')
_DI_TYPEOF_RE = re.compile(
    r'\.Add(Scoped|Singleton|Transient)\s*\(\s*typeof\(([^()]*)\)\s*(?:,\s*typeof\(([^()]*)\)\s*)?\)')
_ENTITY_CONFIG_RE = re.compile(r'IEntityTypeConfiguration\s*<([^<>]+)>')
# Minimal-API endpoints: RECV.MapGet("/x", Handler) -> ROUTES_TO. Only a NAMED method group (bare or
# dotted) is captured — an inline `() =>` / `async () =>` lambda is anonymous (no node) and skipped.
_MINIMAL_API_VAR_RE = re.compile(
    r'(\w+)\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*"([^"]*)"\s*,\s*'
    r'(?!async\b|static\b|await\b|new\b)([A-Za-z_]\w*(?:\.\w+)*)\s*\)')
# Route-GROUP prefixes (v0.24): `var api = xxx.MapGroup("/api")` binds a prefix; `api.MapX(...)` and
# the inline `xxx.MapGroup("/api").MapX(...)` chain compose it to "/api/...". One nesting level (base's
# own prefix folds in); nested/reassigned/double-chain groups pass through UNCOMPOSED (never fabricate
# a wrong path). A chained MapX is preceded by ')', so the var regex (which needs a \w receiver) can't
# double-match it — the two detectors are disjoint.
_MAPGROUP_ASSIGN_RE = re.compile(
    r'(?:var\s+)?(\w+)\s*=\s*(\w+)\s*\.MapGroup\s*\(\s*"([^"]*)"\s*\)')       # newvar, basevar, prefix
_MINIMAL_API_CHAIN_RE = re.compile(
    r'\.MapGroup\s*\(\s*"([^"]*)"\s*\)\s*'
    r'\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*"([^"]*)"\s*,\s*'
    r'(?!async\b|static\b|await\b|new\b)([A-Za-z_]\w*(?:\.\w+)*)\s*\)')       # prefix, verb, path, handler
_ENTITY_LAMBDA_RE = re.compile(r'\.Entity\s*<([^<>]+)>\s*\(\s*\w+\s*=>')   # Entity<X>(e => { ...; ...; })
# A constructor: access-modifier(s) then the ClassName then `(` (a method always has a RETURN type
# between the modifiers and its name, so this shape is unambiguous once name == the enclosing class).
_CTOR_RE = re.compile(r'^\s*(?:(?:public|internal|protected|private)\s+)+([A-Z]\w*)\s*\(([^)]*)\)')

_HTTP_ATTRS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE",
               "HttpPatch": "PATCH", "HttpHead": "HEAD", "HttpOptions": "OPTIONS"}
_CTRL_KEYWORDS = {"if", "for", "foreach", "while", "switch", "using", "return", "catch",
                  "lock", "new", "throw", "else", "do", "fixed"}
# EF deletes/updates the dependents for these; Restrict/NoAction/ClientNoAction PREVENT deletion.
_CASCADE_BEHAVIORS = {"Cascade", "ClientCascade", "SetNull", "ClientSetNull"}
_COLLECTION_TYPES = {"List", "IList", "ICollection", "IEnumerable", "IReadOnlyCollection",
                     "IReadOnlyList", "HashSet", "ISet", "ObservableCollection", "Collection"}
# Property types that are scalars/BCL values, never entity references (case-sensitive; lowercase
# keywords are additionally cut by the leading-uppercase requirement on targets).
_NON_ENTITY_TYPES = {
    "String", "Int16", "Int32", "Int64", "Byte", "SByte", "UInt16", "UInt32", "UInt64",
    "Decimal", "Double", "Single", "Boolean", "Char", "Object", "DateTime", "DateTimeOffset",
    "DateOnly", "TimeOnly", "TimeSpan", "Guid", "Uri", "Version", "CultureInfo", "JsonDocument",
    "JsonElement", "XElement", "XDocument", "Type", "Task", "ValueTask", "CancellationToken",
    "Stream", "Nullable", "Enum", "Array", "Delegate", "Action", "Func", "Exception",
}

# Small irregulars table for the PascalCase singularizer (misses DROP the edge — never fabricate).
_IRREGULAR = {
    "people": "person", "children": "child", "men": "man", "women": "woman",
    "mice": "mouse", "geese": "goose", "feet": "foot", "teeth": "tooth",
    "leaves": "leaf", "lives": "life", "data": "datum", "media": "medium",
    "statuses": "status", "aliases": "alias", "analyses": "analysis",
    "indices": "index", "vertices": "vertex", "matrices": "matrix", "quizzes": "quiz",
}


def _singular_word(word: str) -> str:
    """Ruby-convention suffix rules on ONE word, case-preserved on the stem."""
    lw = word.lower()
    if lw in _IRREGULAR:
        s = _IRREGULAR[lw]
        return s.capitalize() if word[:1].isupper() else s
    if lw.endswith("ies") and len(lw) > 3:
        return word[:-3] + "y"
    if lw.endswith(("xes", "ches", "shes", "sses", "zes")):
        return word[:-2]
    if lw.endswith("s") and not lw.endswith("ss"):
        return word[:-1]
    return word


def singularize_pascal(name: str) -> str:
    """PascalCase plural -> singular, on the LAST camel segment only (`AuditEntries` ->
    `AuditEntry`, `People` -> `Person`). A wrong guess costs a DROPPED edge (unresolvable name at
    push), never a wrong one."""
    parts = re.findall(r'[A-Z][a-z0-9]*|[a-z0-9]+|[A-Z]+(?![a-z])', name)
    if not parts:
        return name
    head = name[: len(name) - len(parts[-1])]
    return head + _singular_word(parts[-1])


def demodulize(name: str) -> str:
    """`Shop.Models.Order` -> `Order` (graphify's bare-name shape for C# types)."""
    return name.rsplit(".", 1)[-1]


def _clean_type(raw) -> str:
    """A base/generic-arg token -> the bare resolvable type name: strip whitespace, nullability,
    generic arguments, a primary-constructor base invocation `Bar(x)`, and namespace
    qualification. '' when nothing plausible remains."""
    t = (raw or "").strip().rstrip("?").strip()
    t = t.split("<", 1)[0].split("(", 1)[0].strip()
    t = demodulize(t)
    return t if re.fullmatch(r"[A-Za-z_]\w*", t or "") else ""


def _split_generic_args(s: str) -> list:
    """Split 'IRepo<Order>, Repo<Order>' on TOP-LEVEL commas only (respects <> nesting)."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _is_iface_name(name: str) -> bool:
    """The .NET I-convention: leading 'I' followed by another uppercase (IOrderService yes,
    Invoice no)."""
    return len(name) >= 2 and name[0] == "I" and name[1].isupper()


class _ClassCtx:
    """The type currently collecting declaration facts (most-recent-declaration heuristic)."""

    def __init__(self, name: str, kind: str, bases: list, line: int):
        self.name = name            # BARE, exactly as graphify names it (both namespace forms)
        self.kind = kind            # class | interface | record | struct
        self.bases = bases
        self.line = line
        self.facts: list = []       # human fragments for the body_summary
        self.route_prefix: str = ""
        self.config_entity: str = ""  # IEntityTypeConfiguration<X>: fluent chains anchor on X


class _FluentStmt:
    """Per-STATEMENT EF fluent-chain state. A chain may span lines until its terminating ';';
    recognize() feeds `;`-split segments so state can never leak across statements."""

    def __init__(self):
        self.entity = ""            # Entity<X>() principal-side anchor
        self.other = ""             # the HasMany/HasOne counterpart type (resolved or '')
        self.principal = ""         # cascade orientation, once determinable
        self.dependent = ""
        self.pending_one_to_one = False


def _kind_phrase(ctx: _ClassCtx) -> str:
    bases = {demodulize(b) for b in ctx.bases}
    if "DbContext" in bases:
        return "EF Core DbContext"
    if bases & {"ControllerBase", "Controller"} or ctx.name.endswith("Controller"):
        return "ASP.NET controller"
    if ctx.kind == "interface":
        return "C# interface"
    return "C# " + ("record" if ctx.kind.startswith("record") else ctx.kind)


def _summary(ctx: _ClassCtx) -> str:
    base = f"{ctx.name}: {_kind_phrase(ctx)}"
    if ctx.facts:
        base += "; " + "; ".join(ctx.facts[:8])
    return base[:300]


def _strip_comments(lines: list) -> list:
    """Strip `//` line comments and `/* ... */` block spans BEFORE any matching, string-aware:
    a comment opener inside a "..." string literal does not count (string contents are kept), and
    a `/*` inside a `//` comment opens nothing (a `//****` banner must not blank the file).
    Block state carries across lines. Comment text can then never fabricate an edge — the
    review-locked honesty rule. Verbatim multi-line strings are out of heuristic scope."""
    out, in_block = [], False
    for s in lines:
        buf: list = []
        i, n = 0, len(s)
        while i < n:
            if in_block:
                j = s.find("*/", i)
                if j < 0:
                    i = n
                else:
                    in_block = False
                    i = j + 2
                continue
            ch = s[i]
            if ch == '"':
                buf.append(ch)
                i += 1
                while i < n:
                    if s[i] == "\\" and i + 1 < n:
                        buf.append(s[i:i + 2])
                        i += 2
                        continue
                    buf.append(s[i])
                    i += 1
                    if s[i - 1] == '"':
                        break
                continue
            if ch == "/" and i + 1 < n and s[i + 1] == "/":
                break                                  # line comment: drop the rest of the line
            if ch == "/" and i + 1 < n and s[i + 1] == "*":
                in_block = True
                i += 2
                continue
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def _split_leading_attrs(line: str) -> tuple:
    """'[A] [B("x")] rest' -> ('[A] [B("x")]', ' rest'); ('', line) when the line does not start
    with attributes. Bracket-balanced and string-aware, so a nested route token
    ([Route("api/[controller]")]) or an attribute string containing ']' parses correctly."""
    i, n, last = 0, len(line), 0
    while True:
        j = i
        while j < n and line[j] in " \t":
            j += 1
        if j >= n or line[j] != "[":
            break
        depth, k, in_str = 0, j, False
        while k < n:
            c = line[k]
            if in_str:
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k >= n or depth != 0:
            break
        i = k + 1
        last = i
    return line[:last], line[last:]


def _decl_match(text: str):
    """Match a type declaration, tolerating a generic `where` constraint (with or without a base
    list) and a semicolon body (`public record Money(...) : IComparable;`)."""
    t = text.split(";", 1)[0]
    if " where " in t:
        t = t.split(" where ", 1)[0]
    return _DECL_RE.match(t)


def _compose_path(prefix: str, template: str, cls: str, method: str) -> str:
    """ASP.NET route composition with [controller]/[action] token substitution."""
    def sub(t: str) -> str:
        t = t.replace("[controller]", cls[:-len("Controller")] if cls.endswith("Controller") else cls)
        return t.replace("[action]", method)
    parts = [sub(p).strip("/") for p in (prefix, template) if p]
    return "/".join(p for p in parts if p)


def _compose_minimal(prefix: str, template: str) -> str:
    """Compose a minimal-API route-GROUP prefix with an endpoint template, PRESERVING the raw
    leading-slash convention (unlike _compose_path, which strips it). No prefix -> the template
    verbatim (byte-identical to the pre-v0.24 ungrouped path), so an ungrouped route never changes."""
    p = prefix.strip("/")
    if not p:
        return template
    t = template.strip("/")
    return "/" + p + ("/" + t if t else "")


def recognize(source: str, rel: str) -> tuple:
    """PURE per-file core (the registry contract): C# source -> (supplemental_nodes, typed_edges).
    Never raises on malformed source — unmatched lines are simply skipped."""
    nodes: list = []
    edges: list = []
    declared: set = set()          # bare type names declared in THIS file (INHERITS locality)
    ctxs: list = []
    cur: _ClassCtx | None = None
    stmt = _FluentStmt()
    pending_http: list = []        # [(verb, template)] awaiting the next method declaration
    pending_route: str = ""        # a [Route] template awaiting the next class/method declaration
    stem = Path(rel).stem          # the Module node's name (top-level-statement DI/route anchor)
    lambda_entity = ""             # inline Entity<X>(e => {...}) anchor — persists across ';' in-block
    lambda_open = False            # have we seen the lambda block's opening '{' yet
    lambda_bd = 0                  # brace balance within the active lambda block
    group_prefix: dict = {}        # var -> its MapGroup("/prefix") route-group prefix (persists in-file)

    lines = _strip_comments(source.splitlines())
    for ln in lines:
        m = _decl_match(_split_leading_attrs(ln)[1])
        if m:
            declared.add(m.group(2))

    def emit(src, dst, et, conf, attrs):
        if src and dst:
            edges.append(TypedEdge(src, rel, dst, et, conf, attrs))

    def di_from_params(params_str: str, cls: str) -> None:
        """Constructor / primary-constructor parameter types -> DEPENDS_ON cls -> type (NAME). BCL /
        scalar types are filtered (same _NON_ENTITY_TYPES gate as navigations); a wrong guess resolves
        to no node and DROPS at push, never fabricates."""
        for p in _split_generic_args(params_str or ""):
            toks = p.split("=", 1)[0].split()          # drop a default value, then Type + name tokens
            if len(toks) < 2:
                continue
            t = _clean_type(toks[-2])                  # the token before the parameter name
            if t and t[:1].isupper() and t not in _NON_ENTITY_TYPES and t != cls:
                emit(cls, t, "DEPENDS_ON", "NAME", {"via": "ctor", "param": toks[-1]})

    def fluent_target(generic, prop, singularize: bool) -> str:
        """HasMany<T>/HasOne<T> generic arg wins; else the lambda property — singularized ONLY
        for HasMany (collection navs are plural by convention; HasOne navs are singular and
        already ≈ the type name)."""
        if generic:
            return _clean_type(generic)
        if prop:
            return _clean_type(singularize_pascal(prop) if singularize else prop)
        return ""

    def fluent_scan(seg: str) -> None:
        """One `;`-delimited statement segment through the fluent-chain tracker."""
        nonlocal stmt
        em = _ENTITY_RE.search(seg)
        if em:
            stmt = _FluentStmt()
            stmt.entity = _clean_type(em.group(1))
        elif not stmt.entity and (".HasMany" in seg or ".HasOne" in seg):
            if cur is not None and cur.config_entity:
                stmt.entity = cur.config_entity     # config-class chain: the interface's <X>
            elif lambda_entity:
                stmt.entity = lambda_entity         # inline Entity<X>(e => {...}) block anchor
        hm = _HASMANY_RE.search(seg)
        if hm and stmt.entity and ".HasMany" in seg:
            tgt = fluent_target(hm.group(1), hm.group(2), singularize=True)
            if tgt:
                stmt.other = tgt
                stmt.principal, stmt.dependent = stmt.entity, tgt   # HasMany: entity is principal
                attrs = {"via": "fluent"}
                if hm.group(2):
                    attrs["property"] = hm.group(2)
                emit(stmt.entity, tgt, "HAS_MANY", "NAME", attrs)
        ho = _HASONE_RE.search(seg)
        if ho and stmt.entity and ".HasOne" in seg:
            tgt = fluent_target(ho.group(1), ho.group(2), singularize=False)
            if tgt:
                stmt.other = tgt
                stmt.principal = stmt.dependent = ""                # orientation pends With*/FK<T>
                stmt.pending_one_to_one = False
                attrs = {"via": "fluent"}
                if ho.group(2):
                    attrs["property"] = ho.group(2)
                emit(stmt.entity, tgt, "REFERENCES", "NAME", attrs)
        if _WITHMANY_RE.search(seg) and stmt.entity and stmt.other and not stmt.principal:
            stmt.principal, stmt.dependent = stmt.other, stmt.entity  # HasOne+WithMany inverts
        if _WITHONE_RE.search(seg) and stmt.entity and stmt.other and not stmt.principal:
            stmt.pending_one_to_one = True                            # needs HasForeignKey<T>
        fkm = _FK_GENERIC_RE.search(seg)
        if fkm and stmt.pending_one_to_one and not stmt.principal:
            fk = _clean_type(fkm.group(1))
            if fk == stmt.entity:
                stmt.principal, stmt.dependent = stmt.other, stmt.entity
            elif fk == stmt.other:
                stmt.principal, stmt.dependent = stmt.entity, stmt.other
        om = _ONDELETE_RE.search(seg)
        if om and om.group(1) in _CASCADE_BEHAVIORS and stmt.principal and stmt.dependent:
            emit(stmt.principal, stmt.dependent, "CASCADES", "NAME",
                 {"strategy": om.group(1), "via": "fluent"})
            if cur:
                cur.facts.append(f"cascade {stmt.principal} -> {stmt.dependent} ({om.group(1)})")

    for i, raw0 in enumerate(lines, start=1):
        if not raw0.strip() or raw0.lstrip().startswith("#"):
            continue

        # -- leading attributes (own line OR same line as the member they decorate) -------------
        attrs_txt, raw = _split_leading_attrs(raw0)
        if attrs_txt:
            for name, arg in _ATTR_RE.findall(attrs_txt):
                if name in _HTTP_ATTRS:
                    pending_http.append((_HTTP_ATTRS[name], arg or ""))
                elif name == "Route":
                    pending_route = arg or ""
            if not raw.strip():
                continue                       # a pure attribute line; the member follows
        line = raw.strip()
        if not line:
            continue

        # -- inline Entity<X>(e => {...}) lambda anchor + its brace balance (multi-statement fix) --
        # so a cascade in the SECOND+ statement of the block still resolves its principal entity.
        if not lambda_entity:
            _elm = _ENTITY_LAMBDA_RE.search(raw)
            if _elm:
                lambda_entity, lambda_open, lambda_bd = _clean_type(_elm.group(1)), False, 0
        _lambda_closing = False
        if lambda_entity:
            lambda_bd += raw.count("{") - raw.count("}")
            if "{" in raw:
                lambda_open = True
            if lambda_open and lambda_bd <= 0:
                _lambda_closing = True          # block ends on THIS line; keep the anchor for its segs

        # -- type declarations -----------------------------------------------------------------
        dm = _decl_match(raw)
        if dm:
            kind = "record" if dm.group(1).startswith("record") else dm.group(1)
            bases_raw = dm.group(3) or ""
            base_pairs = [(c, t.strip()) for t, c in
                          ((t, _clean_type(t)) for t in _split_generic_args(bases_raw))
                          if c] if bases_raw else []
            cur = _ClassCtx(dm.group(2), kind, [c for c, _ in base_pairs], i)
            ctxs.append(cur)
            cur.route_prefix = pending_route
            # IEntityTypeConfiguration<X> (the dominant fluent style in larger codebases): chains
            # in Configure() have NO Entity<X>() anchor — the interface's generic arg carries it.
            ecm = _ENTITY_CONFIG_RE.search(bases_raw)
            if ecm:
                cur.config_entity = _clean_type(ecm.group(1))
            pending_http, pending_route = [], ""
            for b, raw_t in base_pairs:
                # Interface bases on an interface ARE inheritance; on a class/record/struct the
                # I-convention splits IMPLEMENTS from the (single) INHERITS base.
                et = "INHERITS" if (kind == "interface" or not _is_iface_name(b)) else "IMPLEMENTS"
                # A QUALIFIED base (Legacy.Order) is never graded off a same-file bare-name
                # collision — the qualifier says it is a different type.
                conf = "LOCAL" if (b in declared and "." not in raw_t) else "NAME"
                emit(cur.name, b, et, conf, {"raw_parent": raw_t} if "." in raw_t else {})
                cur.facts.append(("implements " if et == "IMPLEMENTS" else "inherits ") + b)
            # C# 12 primary constructor — `class Foo(IBar bar)` — is constructor injection too.
            pcm = re.search(re.escape(dm.group(2)) + r'\s*(?:<[^>]*>)?\s*\(([^)]*)\)', raw)
            if pcm:
                di_from_params(pcm.group(1), cur.name)
            stmt = _FluentStmt()
            continue

        # -- DI registrations (any nesting; anchored on the enclosing class or the file stem) --
        di_hits = [(lt, _split_generic_args(args)) for lt, args in _DI_GENERIC_RE.findall(raw)]
        di_hits += [(lt, [a for a in (s1, s2) if a]) for lt, s1, s2 in _DI_TYPEOF_RE.findall(raw)]
        for lt, args in di_hits:
            src = cur.name if cur else stem
            names = [t for t in (_clean_type(a) for a in args) if t]
            if not names:
                continue
            service = names[0]
            emit(src, service, "DEPENDS_ON", "NAME", {"lifetime": lt.lower()})
            if len(names) > 1 and names[1] != service:
                emit(src, names[1], "DEPENDS_ON", "NAME",
                     {"lifetime": lt.lower(), "service": service})
            if cur:
                cur.facts.append(f"registers {names[-1]} ({lt.lower()})")

        # -- minimal-API endpoints (top-level statements), composing route-GROUP prefixes ----------
        # `var api = app.MapGroup("/api")` records api -> "/api"; then api.MapGet(...) and the inline
        # app.MapGroup("/api").MapGet(...) chain both compose to "/api/...". Ungrouped MapX is unchanged.
        for newvar, basevar, gp in _MAPGROUP_ASSIGN_RE.findall(raw):
            group_prefix[newvar] = _compose_minimal(group_prefix.get(basevar, ""), gp)
        minimal = [(gpfx, verb, path, handler)
                   for gpfx, verb, path, handler in _MINIMAL_API_CHAIN_RE.findall(raw)]
        minimal += [(group_prefix.get(recv, ""), verb, path, handler)
                    for recv, verb, path, handler in _MINIMAL_API_VAR_RE.findall(raw)]
        for gpfx, verb, path, handler in minimal:
            composed = _compose_minimal(gpfx, path)
            attrs = {"method": verb.upper()}
            if composed:
                attrs["path"] = composed
            # a same-file bare handler is LOCAL (resolve-or-drop same-file); a qualified method group
            # (OrderEndpoints.Create) legitimately lives in another file -> NAME (cross-file resolve).
            emit(cur.name if cur else stem, handler, "ROUTES_TO",
                 "NAME" if "." in handler else "LOCAL", attrs)

        # -- EF fluent chain: statement state survives line breaks, NEVER a ';' boundary --------
        segs = raw.split(";")
        for si, seg in enumerate(segs):
            fluent_scan(seg)
            if si < len(segs) - 1:
                stmt = _FluentStmt()
        if _lambda_closing:
            lambda_entity, lambda_open, lambda_bd = "", False, 0

        if cur is None:
            pending_http, pending_route = [], ""
            continue

        # -- constructor injection: `public OrderService(IDep dep, ...)` -> DEPENDS_ON per param -----
        cm = _CTOR_RE.match(raw)
        if cm and cm.group(1) == cur.name:
            di_from_params(cm.group(2), cur.name)
            pending_http, pending_route = [], ""
            continue

        # -- navigation properties ---------------------------------------------------------------
        pm = _PROP_RE.match(raw)
        if pm:
            mods, ptype, pname = pm.group(1) or "", pm.group(2), pm.group(3)
            if not any(k in mods.split() for k in ("static", "private", "protected")):
                outer = ptype.split("<", 1)[0].strip().rstrip("?")
                outer_bare = demodulize(outer)
                if "<" in ptype and outer_bare == "DbSet":
                    inner = _clean_type(ptype.split("<", 1)[1].rsplit(">", 1)[0])
                    if inner and inner[:1].isupper() and inner not in _NON_ENTITY_TYPES:
                        emit(cur.name, inner, "HAS_MANY", "NAME",
                             {"property": pname, "via": "DbSet"})
                        cur.facts.append(f"DbSet {pname}")
                elif "<" in ptype and outer_bare in _COLLECTION_TYPES:
                    inner = _clean_type(ptype.split("<", 1)[1].rsplit(">", 1)[0])
                    if inner and inner[:1].isupper() and inner not in _NON_ENTITY_TYPES:
                        emit(cur.name, inner, "HAS_MANY", "NAME", {"property": pname})
                        cur.facts.append(f"has many {pname}")
                elif "<" not in ptype:
                    bare = _clean_type(ptype)
                    if bare and bare[:1].isupper() and bare not in _NON_ENTITY_TYPES \
                            and bare != cur.name:
                        attrs = {"property": pname}
                        if "." in ptype:
                            attrs["raw_type"] = ptype.rstrip("?")
                        emit(cur.name, bare, "REFERENCES", "NAME", attrs)
                        cur.facts.append(f"references {pname}")
            pending_http, pending_route = [], ""
            continue

        # -- attribute-routed action methods -------------------------------------------------------
        if pending_http or pending_route:
            mm = _METHOD_RE.match(raw)
            if mm and mm.group(1) not in _CTRL_KEYWORDS:
                method = mm.group(1)
                handler = f"{cur.name}.{method}"
                entries = pending_http or [("*", pending_route)]
                for verb, template in entries:
                    path = _compose_path(cur.route_prefix,
                                         template if pending_http else pending_route,
                                         cur.name, method)
                    attrs = {"method": verb}
                    if path:
                        attrs["path"] = path
                    emit(cur.name, handler, "ROUTES_TO", "LOCAL", attrs)
                    cur.facts.append(f"route {verb} {path}".rstrip())
            pending_http, pending_route = [], ""

    for ctx in ctxs:
        nodes.append({
            "key": f"{rel}::{ctx.name}:{ctx.line}",
            "label": "Class",
            "name": ctx.name,                    # byte-matching graphify's bare C# shape
            "source_file": rel,
            "start_line": ctx.line,
            "documented": False,
            "body_summary": _summary(ctx),
        })
    return nodes, edges


_NAV_BASE_DEPTH = 6        # base-class hops followed when a navigation property is inherited


def resolve_navigation_targets(edges) -> list:
    """Cross-file post-pass over this recognizer's OWN edges (sweep T8d-B11). A fluent
    `HasMany(i => i.Lines)` names a PROPERTY; the per-file core can only singularize it ('Line'), which
    resolves to nothing when the element type is `InvoiceLine` — silently dropping the declared cascade.
    The entity's own file already told us the answer: its navigation property edge
    (HAS_MANY Invoice -> InvoiceLine {"property": "Lines"}, or REFERENCES for a reference navigation).

    For every fluent HAS_MANY / REFERENCES whose target is still the property-derived guess (an explicit
    HasMany<T> generic is never overridden), look the property up on the entity — then on its base
    classes (INHERITS edges, nearest level first) — and take the declared type when EXACTLY ONE is known
    (two same-named entities that disagree -> no rewrite, never pick one). The same statement's fluent
    CASCADES (emitted right after its relationship edge, in either orientation) follows the rewrite.
    Pure; returns the ORIGINAL list object when nothing changes."""
    if not edges:
        return edges
    navs: dict = {}                      # (entity, property) -> {declared target types}
    bases: dict = {}                     # entity -> {base type names}
    for e in edges:
        a = e.attrs or {}
        if e.edge_type in ("HAS_MANY", "REFERENCES") and a.get("property") \
                and a.get("via") not in ("fluent", "DbSet"):
            navs.setdefault((e.src_name, a["property"]), set()).add(e.dst_name)
        elif e.edge_type == "INHERITS":
            bases.setdefault(e.src_name, set()).add(e.dst_name)
    if not navs:
        return edges

    def declared(entity: str, prop: str):
        seen: set = set()
        level = [entity]
        for _ in range(_NAV_BASE_DEPTH + 1):
            found: set = set()
            nxt: list = []
            for c in level:
                if c in seen:
                    continue
                seen.add(c)
                t = navs.get((c, prop))
                if t:
                    found |= t
                else:
                    nxt.extend(sorted(bases.get(c, ())))
            if found:
                return next(iter(found)) if len(found) == 1 else None
            if not nxt:
                return None
            level = nxt
        return None

    out: list = []
    changed = False
    last: dict = {}                      # src_file -> (entity, guessed target, resolved target)
    for e in edges:
        a = e.attrs or {}
        if a.get("via") == "fluent" and e.edge_type in ("HAS_MANY", "REFERENCES"):
            prop = a.get("property")
            new = e.dst_name
            if prop:
                guess = _clean_type(singularize_pascal(prop) if e.edge_type == "HAS_MANY" else prop)
                if e.dst_name == guess:
                    new = declared(e.src_name, prop) or e.dst_name
            last[e.src_file] = (e.src_name, e.dst_name, new)
            if new != e.dst_name:
                e = e._replace(dst_name=new)
                changed = True
        elif a.get("via") == "fluent" and e.edge_type == "CASCADES":
            rec = last.get(e.src_file)
            if rec and rec[1] != rec[2]:
                entity, old, new = rec
                if (e.src_name, e.dst_name) == (entity, old):          # HasMany: entity is principal
                    e = e._replace(dst_name=new)
                    changed = True
                elif (e.src_name, e.dst_name) == (old, entity):        # HasOne+WithMany inverts
                    e = e._replace(src_name=new)
                    changed = True
        out.append(e)
    return out if changed else edges


def finalize(workspace_root, edges) -> list:
    """The registry post-pass hook (language_recognizers.RecognizerEntry.finalize); C# needs no
    filesystem access for it — only the edges of every .cs file together."""
    return resolve_navigation_targets(edges)
