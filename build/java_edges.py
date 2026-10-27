#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Java recognizer — Spring / JPA-Hibernate annotations -> typed edges + ensured nodes (plugin #4).

Like the Ruby/C# plugins, the wiring a Java service actually depends on is DECLARATIVE, not a call:
Spring MVC routes (`@GetMapping`), JPA relations (`@OneToMany` / `@ManyToOne`), delete cascades
(`cascade = CascadeType.REMOVE` / `orphanRemoval = true`), and constructor / `@Autowired` dependency
injection. graphify parses Java at the node/call layer (Class + `Class.method` methods, verified in
tests/fixtures/graphify_multilang/java.json) but yields NO relationship for any of that. This module
reads those conventions HEURISTICALLY (line-oriented regex — no javac, no Spring runtime; annotations
are strongly regex-tractable) and emits, per file:

  typed edges (the value):
    INHERITS    class Foo extends Bar / interface Foo extends Bar   (LOCAL when Bar is declared here)
    IMPLEMENTS  class Foo implements IBar, IBaz                      (explicit `implements` keyword)
    ROUTES_TO   @GetMapping/@PostMapping/.../@RequestMapping on a method -> controller -> Class.method
                (`Class.method`, byte-matching graphify's Method names), class-level @RequestMapping
                prefix composed in. SAME_FILE_STRICT / LOCAL: resolve-or-drop, like the C# route path.
    HAS_MANY    @OneToMany / @ManyToMany field -> owner -> the collection's element type
    REFERENCES  @ManyToOne / @OneToOne field -> holder -> the referenced entity type
    CASCADES    a relation with cascade = CascadeType.REMOVE|ALL, or orphanRemoval = true — the
                "is deleted with" blast-radius edge, owner -> element. NOTE: persisted REVERSED
                (element)-[:CASCADES]->(owner) by edge_resolve so `deepmap_impact("Order")` (reverse
                reachability) lists what a delete of Order cascades to. PERSIST/MERGE/REFRESH/DETACH
                alone do NOT delete dependents -> deliberately NOT CASCADES (mirrors the EF/Rails rule).
    DEPENDS_ON  constructor-injection param types, @Autowired fields, and (Lombok)
                @RequiredArgsConstructor/@AllArgsConstructor final fields -> class -> the injected type.
    SCHEDULED_AS  @Scheduled method -> Class.method (a background task).
    TRIGGERS      @EventListener method -> Class.method (a domain-event handler).

  supplemental nodes (the fallback): one ensured :Class node per class/interface/enum/record
  declaration, named BARE exactly as graphify names Java types, with a deterministic body_summary.
  When graphify parsed the file, merge_supplemental's (name, source_file) dedupe drops these (base
  wins); when graphify missed the repo they keep the entity/controller layer present + searchable.

Honesty model (the confidence wedge absorbs heuristic error):
  * `//` line comments and `/* */` block spans are stripped BEFORE any matching, string-aware, so
    comment/string text can never fabricate an edge;
  * name resolution can DROP an edge (unresolvable target), never fabricate one — a mis-derived type
    resolves to nothing and disappears;
  * a name resolving to >1 symbol grades AMBIGUOUS (verify by reading);
  * types are DEMODULIZED (`com.shop.Order` -> `Order`) to match graphify's bare-name shape;
  * BCL / java.util / scalar types are never emitted as entity refs or dependencies.

Pure: recognize(source_text, rel_path) only — no fs, no network; the registry's shared walker owns
file I/O, ignore dirs (target/, build/, .gradle/, node_modules/) and per-file error isolation.
"""
from __future__ import annotations

import re
from pathlib import Path

import sys as _sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from framework_edges import TypedEdge  # noqa: E402  (the shared edge shape; endpoints name+file)

# ---- Java / Spring / JPA vocabulary ------------------------------------------
_MODS = (r'(?:public|private|protected|abstract|final|static|sealed|non-sealed|strictfp|default|'
         r'synchronized|native|transient|volatile)')
_DECL_RE = re.compile(
    rf'^\s*(?:{_MODS}\s+)*'
    r'(class|interface|enum|record)\s+([A-Z]\w*)\s*(?:<[^>]*>)?\s*(?:\([^)]*\))?\s*(.*)$')
_EXTENDS_RE = re.compile(
    r'\bextends\s+([A-Za-z_][\w.]*(?:\s*<[^>]*>)?(?:\s*,\s*[A-Za-z_][\w.]*(?:\s*<[^>]*>)?)*)')
_IMPLEMENTS_RE = re.compile(
    r'\bimplements\s+([A-Za-z_][\w.]*(?:\s*<[^>]*>)?(?:\s*,\s*[A-Za-z_][\w.]*(?:\s*<[^>]*>)?)*)')
_CTOR_RE = re.compile(r'^\s*(?:(?:public|private|protected)\s+)+([A-Z]\w*)\s*\(([^)]*)\)')
_METHOD_RE = re.compile(
    rf'^\s*(?:{_MODS}\s+)*'
    r'(?:<[^>]*>\s*)?[\w.<>\[\],\s?]+?\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)')
_FIELD_RE = re.compile(
    rf'^\s*(?:{_MODS}\s+)*'
    r'([A-Za-z_][\w.]*\s*(?:<[^>]*>)?(?:\[\])?)\s+([a-zA-Z_]\w*)\s*[;=]')
_ANNOT_RE = re.compile(r'@(\w+)(?:\s*\(([^)]*)\))?')

_ROUTE_ANNOTS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                 "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
_HAS_MANY_ANNOTS = {"OneToMany", "ManyToMany"}
_REF_ANNOTS = {"ManyToOne", "OneToOne"}
_LOMBOK_CTOR_ANNOTS = {"RequiredArgsConstructor", "AllArgsConstructor"}
# Property/param types that are scalars / JDK values, never an entity ref or an injected dependency.
_NON_ENTITY_TYPES = {
    "String", "Integer", "Long", "Short", "Byte", "Double", "Float", "Boolean", "Character",
    "Object", "Number", "BigDecimal", "BigInteger", "LocalDate", "LocalDateTime", "LocalTime",
    "OffsetDateTime", "ZonedDateTime", "Instant", "Duration", "Date", "Calendar", "UUID", "Void",
    "Optional", "List", "Map", "Set", "Collection", "Iterable", "ArrayList", "HashSet", "HashMap",
    "Class", "Enum", "Exception", "RuntimeException", "Throwable", "CharSequence", "Comparable",
    "Serializable", "Stream", "CompletableFuture", "Future",
}


def demodulize(name: str) -> str:
    """`com.shop.Order` -> `Order` (graphify's bare-name shape for Java types)."""
    return (name or "").rsplit(".", 1)[-1]


def _clean_type(raw) -> str:
    """A base / field / param type token -> the bare resolvable type name: strip whitespace,
    generic arguments, array brackets, and package qualification. '' when nothing plausible remains."""
    t = (raw or "").strip()
    t = t.split("<", 1)[0].split("[", 1)[0].strip()
    t = demodulize(t)
    return t if re.fullmatch(r"[A-Za-z_]\w*", t or "") else ""


def _generic_arg(type_str: str) -> str:
    """`List<LineItem>` -> `LineItem`; `Map<Long, Order>` -> `Order` (the VALUE type)."""
    m = re.search(r'<(.+)>', type_str or "")
    if not m:
        return ""
    inner = _split_top_commas(m.group(1))
    return _clean_type(inner[-1]) if inner else ""


def _split_top_commas(s: str) -> list:
    """Split on TOP-LEVEL commas only (respects <> nesting) — for generic args / param lists."""
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


def _split_types(csv: str) -> list:
    """A comma list of base/interface names -> demodulized bare names (drops the empty/garbage)."""
    return [c for c in (_clean_type(t) for t in _split_top_commas(csv)) if c]


def _is_delete_cascade(arg: str) -> bool:
    """True when the JPA relation deletes/orphans its dependents: cascade REMOVE/ALL or
    orphanRemoval = true. PERSIST/MERGE/REFRESH/DETACH alone do NOT -> not a blast-radius cascade."""
    a = arg or ""
    if re.search(r'orphanRemoval\s*=\s*true', a):
        return True
    m = re.search(r'cascade\s*=\s*(\{[^}]*\}|CascadeType\.\w+)', a)
    if not m:
        return False
    return "CascadeType.REMOVE" in m.group(1) or "CascadeType.ALL" in m.group(1)


def _annot_path(arg: str) -> str:
    """The first string literal in an annotation's args (the mapping path/value)."""
    m = re.search(r'"([^"]*)"', arg or "")
    return m.group(1) if m else ""


def _strip_comments(lines: list) -> list:
    """Strip `//` line comments and `/* ... */` block spans BEFORE any matching, string-aware (a
    comment opener inside a string literal does not count). Block state carries across lines."""
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
                break
            if ch == "/" and i + 1 < n and s[i + 1] == "*":
                in_block = True
                i += 2
                continue
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def _split_leading_annots(line: str) -> tuple:
    """'@A @B("x") rest' -> ('@A @B("x")', ' rest'); ('', line) when the line does not start with
    annotations. Paren-balanced and string-aware, so an annotation arg containing ')' parses right."""
    i, n, last = 0, len(line), 0
    while True:
        j = i
        while j < n and line[j] in " \t":
            j += 1
        if j >= n or line[j] != "@":
            break
        k = j + 1
        while k < n and (line[k].isalnum() or line[k] in "_."):
            k += 1
        # optional (...) argument list, balanced + string-aware
        m = k
        while m < n and line[m] in " \t":
            m += 1
        if m < n and line[m] == "(":
            depth, in_str = 0, False
            while m < n:
                c = line[m]
                if in_str:
                    if c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        m += 1
                        break
                m += 1
            if depth != 0:
                break
            i = m
        else:
            i = k
        last = i
    return line[:last], line[last:]


class _ClassCtx:
    """The type currently collecting declaration facts (most-recent-declaration heuristic)."""

    def __init__(self, name: str, kind: str, bases: list, line: int):
        self.name = name
        self.kind = kind
        self.bases = bases
        self.line = line
        self.facts: list = []
        self.route_prefix: str = ""
        self.lombok_ctor: bool = False


def _kind_phrase(ctx: _ClassCtx) -> str:
    if ctx.kind == "interface":
        return "Java interface"
    if ctx.kind == "enum":
        return "Java enum"
    if ctx.kind == "record":
        return "Java record"
    return "Java class"


def _summary(ctx: _ClassCtx) -> str:
    base = f"{ctx.name}: {_kind_phrase(ctx)}"
    if ctx.facts:
        base += "; " + "; ".join(ctx.facts[:8])
    return base[:300]


def _compose_path(prefix: str, template: str) -> str:
    parts = [p.strip("/") for p in (prefix, template) if p]
    return "/".join(p for p in parts if p)


def recognize(source: str, rel: str) -> tuple:
    """PURE per-file core (the registry contract): Java source -> (supplemental_nodes, typed_edges).
    Never raises on malformed source — unmatched lines are simply skipped."""
    nodes: list = []
    edges: list = []
    declared: set = set()
    ctxs: list = []
    cur: _ClassCtx | None = None
    pending: list = []          # [(annot_name, annot_arg)] awaiting the next member

    lines = _strip_comments(source.splitlines())
    for ln in lines:
        dm = _DECL_RE.match(_split_leading_annots(ln)[1])
        if dm:
            declared.add(dm.group(2))

    def emit(src, dst, et, conf, attrs):
        if src and dst:
            edges.append(TypedEdge(src, rel, dst, et, conf, attrs))

    def di_from_params(params_str: str, cls: str) -> None:
        for p in _split_top_commas(params_str or ""):
            toks = p.replace("final ", " ").split()
            # drop leading param annotations, then the token before the parameter name is the type
            toks = [t for t in toks if not t.startswith("@")]
            if len(toks) < 2:
                continue
            t = _clean_type(toks[-2])
            if t and t[:1].isupper() and t not in _NON_ENTITY_TYPES and t != cls:
                emit(cls, t, "DEPENDS_ON", "NAME", {"via": "ctor", "param": toks[-1]})

    for i, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        leading, rest = _split_leading_annots(raw)
        if leading:
            pending.extend(_ANNOT_RE.findall(leading))
        if not rest.strip():
            continue                                   # an annotation-only line; the member follows
        annots = {a: arg for a, arg in pending}

        # -- type declarations -----------------------------------------------------------------
        dm = _DECL_RE.match(rest)
        if dm:
            kind, name, tail = dm.group(1), dm.group(2), dm.group(3) or ""
            cur = _ClassCtx(name, kind, [], i)
            ctxs.append(cur)
            em = _EXTENDS_RE.search(tail)
            im = _IMPLEMENTS_RE.search(tail)
            for b in (_split_types(em.group(1)) if em else []):
                # `extends` on an interface may be multiple; on a class it is the single superclass.
                conf = "LOCAL" if b in declared else "NAME"
                emit(name, b, "INHERITS", conf, {})
                cur.bases.append(b)
                cur.facts.append("inherits " + b)
            for b in (_split_types(im.group(1)) if im else []):
                emit(name, b, "IMPLEMENTS", "NAME", {})
                cur.bases.append(b)
                cur.facts.append("implements " + b)
            # class-level @RequestMapping("/api") -> the route prefix; Lombok ctor-gen annotations.
            if "RequestMapping" in annots:
                cur.route_prefix = _annot_path(annots["RequestMapping"])
            cur.lombok_ctor = any(a in _LOMBOK_CTOR_ANNOTS for a in annots)
            # record header params are constructor injection too: `record R(Dep d)`.
            pcm = re.search(re.escape(name) + r'\s*(?:<[^>]*>)?\s*\(([^)]*)\)', rest)
            if pcm and kind == "record":
                di_from_params(pcm.group(1), name)
            pending = []
            continue

        if cur is None:
            pending = []
            continue

        # -- constructors: parameter injection --------------------------------------------------
        cm = _CTOR_RE.match(rest)
        if cm and cm.group(1) == cur.name:
            di_from_params(cm.group(2), cur.name)
            pending = []
            continue

        # -- methods: routes / scheduled tasks / event handlers ---------------------------------
        mm = _METHOD_RE.match(rest)
        if mm:                                          # _METHOD_RE already requires a `(...)` sig
            method = mm.group(1)
            handler = f"{cur.name}.{method}"
            route_verb = next((v for a, v in _ROUTE_ANNOTS.items() if a in annots), None)
            if route_verb is None and "RequestMapping" in annots:
                route_verb = "*"
            if route_verb is not None:
                src_annot = annots.get(next((a for a in _ROUTE_ANNOTS if a in annots), ""), "") \
                    or annots.get("RequestMapping", "")
                path = _compose_path(cur.route_prefix, _annot_path(src_annot))
                attrs = {"method": route_verb}
                if path:
                    attrs["path"] = path
                emit(cur.name, handler, "ROUTES_TO", "LOCAL", attrs)
                cur.facts.append(f"route {route_verb} {path}".rstrip())
            if "Scheduled" in annots:
                emit(cur.name, handler, "SCHEDULED_AS", "LOCAL", {"task": handler})
            if "EventListener" in annots:
                emit(cur.name, handler, "TRIGGERS", "LOCAL", {"event": method})
            pending = []
            continue

        # -- fields: JPA relations / @Autowired + Lombok ctor injection -------------------------
        fm = _FIELD_RE.match(rest)
        if fm:
            ftype, fname = fm.group(1).strip(), fm.group(2)
            is_collection = "<" in ftype
            elem = _generic_arg(ftype) if is_collection else _clean_type(ftype)
            rel_annot = next((a for a in _HAS_MANY_ANNOTS | _REF_ANNOTS if a in annots), None)
            if rel_annot and elem and elem[:1].isupper() and elem not in _NON_ENTITY_TYPES:
                et = "HAS_MANY" if rel_annot in _HAS_MANY_ANNOTS else "REFERENCES"
                emit(cur.name, elem, et, "NAME", {"annotation": rel_annot, "property": fname})
                cur.facts.append(f"{rel_annot} {fname}")
                if _is_delete_cascade(annots.get(rel_annot, "")):
                    emit(cur.name, elem, "CASCADES", "NAME",
                         {"strategy": "REMOVE", "via": "jpa", "property": fname})
                    cur.facts.append(f"cascade {cur.name} -> {elem}")
            elif "Autowired" in annots and elem and elem[:1].isupper() \
                    and elem not in _NON_ENTITY_TYPES:
                emit(cur.name, elem, "DEPENDS_ON", "NAME", {"via": "field", "field": fname})
            elif cur.lombok_ctor and "final" in rest.split(fname)[0] and elem \
                    and elem[:1].isupper() and elem not in _NON_ENTITY_TYPES:
                emit(cur.name, elem, "DEPENDS_ON", "NAME", {"via": "ctor", "field": fname})
            pending = []
            continue

        pending = []                                   # a non-member line consumes nothing

    for ctx in ctxs:
        nodes.append({
            "key": f"{rel}::{ctx.name}:{ctx.line}",
            "label": "Class",
            "name": ctx.name,
            "source_file": rel,
            "start_line": ctx.line,
            "documented": False,
            "body_summary": _summary(ctx),
        })
    return nodes, edges
