#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go recognizer — Gin/Echo/Chi routers + GORM models -> typed edges + ensured nodes (plugin #5).

Go's framework wiring is declarative the same way: HTTP routes are `router.GET("/x", handler)` calls,
GORM associations live in struct fields + `gorm:"..."` tags, "inheritance" is struct EMBEDDING, and
dependency injection is a `NewXxx(dep ...)` constructor function. graphify parses Go at the node/call
layer (struct types as :Class, `Type.Method` methods — verified in
tests/fixtures/graphify_multilang/go.json) but yields NO relationship for any of that. This module
reads those conventions HEURISTICALLY (line-oriented regex — no go/types, no build) and emits per file:

  typed edges (the value):
    ROUTES_TO   <router>.GET/POST/PUT/PATCH/DELETE("/path", handler) (Gin/Echo/Fiber/Chi) and
                http.HandleFunc("/path", handler) -> module -> the NAMED handler. A bare handler is
                LOCAL and resolves in its PACKAGE (this file first -> EXTRACTED, then any file of the
                same directory); an imported `pkg.Handler` resolves in the package that import names;
                a method value `h.GetX` resolves to `<type of h>.GetX` when the file shows h's type
                (`h := pkg.NewT(...)`, `&T{}`), else to any `*.GetX` :Method. Only a path starting
                with "/" and a named handler qualify — an inline `func(...) {}` literal is anonymous
                (no node) and is skipped, never guessed.
    HAS_MANY    a slice field `Orders []Order` / `[]*Order` (a GORM has-many) -> owner -> element type.
    REFERENCES  a struct field `Customer Customer` / `*Customer` (a GORM belongs-to or has-one) ->
                owner -> type.
    CASCADES    a field tagged `gorm:"constraint:OnDelete:CASCADE"` (or `SET NULL`) — the "is deleted
                with" blast-radius edge, emitted principal -> dependent like every recognizer's. The tag
                is a DB FOREIGN KEY constraint, so its principal is the table the key REFERENCES: a
                has-many slice or a has-one field (the key lives on the element) -> owner -> element;
                a BELONGS-TO field (the key lives on the declaring struct: `CompanyID` beside
                `Company Company`, or a `foreignKey:` naming one of its fields or DB columns) ->
                element -> owner, and only for a BARE element (a `pkg.T` belongs-to emits no cascade —
                see package scoping below). _belongs_to_key replays GORM's guess as far as this file
                shows it; GORM also reads the element's schema, which is usually in another file. NOTE:
                persisted REVERSED (dependent)-[:CASCADES]->(principal) by edge_resolve so
                `deepmap_impact("Order")` (reverse reachability) lists what a delete of Order cascades
                to. Only an EXPLICIT OnDelete constraint is a cascade — a bare foreign key is not
                (guessing would fabricate).
    INHERITS    an ANONYMOUS embedded struct field (`type Admin struct { User }`, `gorm.Model`) ->
                the embedding struct -> the embedded type. Go has NO inheritance keyword; embedding is
                the closest structural relation. (Interface satisfaction is IMPLICIT in Go and cannot
                be known without full type analysis, so IMPLEMENTS is deliberately NEVER emitted.)
    DEPENDS_ON  a `func NewOrderService(repo Repo, log Logger) *OrderService` constructor -> the built
                type -> each non-scalar parameter type (the idiomatic Go DI shape).

  supplemental nodes (the fallback): one ensured :Class node per `type X struct` declaration, named
  BARE exactly as graphify names Go struct types, with a deterministic body_summary. When graphify
  parsed the file, merge_supplemental's (name, source_file) dedupe drops these; when graphify missed
  the repo they keep the model layer present + searchable.

Honesty model: `//` line + `/* */` block comments stripped string-aware before matching; a derived
name that resolves to nothing DROPS at push, never fabricates (a bare name is looked up in its own
package only, so a belongs-to principal that package does not node-ify drops too — see package
scoping below); scalar / stdlib value types are never emitted as an entity ref or a dependency; only
an explicit OnDelete constraint is a cascade, and its direction follows the foreign key (a
belongs-to's principal is the type it references).

Package scoping (2026-09 live validation, #59/#102/#103): Go resolves names by PACKAGE, so every
edge carries a TypedEdge.scope for edge_resolve — a bare name is declared in this file's own package
(directory); `pkg.T` is declared in the package its import path names, and a stdlib-qualified
`http.Handler` names no repo symbol at all, so it is never emitted (it used to demodulize to a bare
`Handler` and bind to a same-named repo METHOD). The scope says where the edge's DST resolves. A SRC
resolves in THIS file's package only: edge_resolve takes it from this file, and — for CASCADES /
HAS_MANY / REFERENCES only — else from the single same-named Go :Class of this file's directory
(edge_resolve._src_in_package, fix-all r283; graded at most INFERRED), else drops the edge. A src is
normally declared here (the struct whose body holds the field, or this file's module), except a
constructor's built type (`func NewX() *X` with X in another file of the package: that DEPENDS_ON
edge drops) and the src of a belongs-to CASCADES, which is the PRINCIPAL (the referenced type). That
cascade is emitted only for a BARE principal. It binds this package's own declaration even when
another package declares the same name (two in this directory — build-tagged twins, an external
`_test` package — are ambiguous and drop), and it DROPS, never binding another package's type, when
this package does not node-ify it: a type alias or defined type, a declaration neither the engine
nor _STRUCT_RE turns into a :Class, a dot import, generated code not on disk at sync time (for a
same-name alias or a dot import, whose type really is the other package's, that loses a true edge:
recall, never a wrong bind). A `pkg.T` principal is never emitted: edge_resolve would look its src
up in THIS package, so its qualifier — the only evidence of which package is meant — would be lost
and a same-named local type would bind wrongly; a cross-package belongs-to cascade is therefore not
in the graph (its REFERENCES edge still goes out).

Pure: recognize(source_text, rel_path) only — no fs, no network; the registry's shared walker owns
file I/O, ignore dirs (vendor/, node_modules/) and per-file error isolation.
"""
from __future__ import annotations

import re
from pathlib import Path

import sys as _sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from framework_edges import TypedEdge  # noqa: E402  (the shared edge shape; endpoints name+file)

# ---- Go / Gin / GORM vocabulary ----------------------------------------------
_STRUCT_RE = re.compile(r'^\s*type\s+([A-Z]\w*)\s+struct\s*\{')
_FIELD_RE = re.compile(r'^\s*([A-Z]\w*)\s+([\*\[\]]*[\w./\[\]\*]+?)\s*(?:`([^`]*)`)?\s*(?://.*)?$')
_EMBED_RE = re.compile(r'^\s*(\*?[A-Za-z_][\w.]*)\s*(?:`([^`]*)`)?\s*(?://.*)?$')
_ROUTE_RE = re.compile(
    r'\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch|Delete|Handle|HandleFunc)\s*'
    r'\(\s*"([^"]*)"\s*,\s*([A-Za-z_][\w.]*)\s*\)')
_CTOR_FUNC_RE = re.compile(r'^\s*func\s+New(\w+)\s*\(([^)]*)\)\s*(?:\(?\s*[\*]?\s*([A-Za-z_][\w.]*))?')
_CASCADE_RE = re.compile(r'constraint:[^"`]*OnDelete:(CASCADE|SET\s+NULL)', re.IGNORECASE)
_GORM_TAG_RE = re.compile(r'\bgorm:"([^"]*)"')
# `CarrierID, DepotID uint` — one declaration naming several fields (any of them can be a foreign key).
_MULTI_FIELD_RE = re.compile(r'^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+)\s+[\*\[\]A-Za-z_]')
# GORM's default column naming (schema/naming.go toDBName) title-cases golint's common initialisms first,
# in THIS order: Go's strings.Replacer takes, at each position, the earliest-listed match, exactly as a
# regex alternation does (HTTPS -> "Http" + "S").
_GORM_INITIALISMS = ("API", "ASCII", "CPU", "CSS", "DNS", "EOF", "GUID", "HTML", "HTTP", "HTTPS", "ID", "IP",
                     "JSON", "LHS", "QPS", "RAM", "RHS", "RPC", "SLA", "SMTP", "SSH", "TLS", "TTL", "UID", "UI",
                     "UUID", "URI", "URL", "UTF8", "VM", "XML", "XSRF", "XSS")
_INITIALISM_RE = re.compile("|".join(_GORM_INITIALISMS))
# gorm.Model — the embed nearly every GORM model carries — is a fixed, documented struct, so a model that
# embeds it still shows all its fields. Its key holds a "/", which neither a struct name nor the `pkg.T` an
# embed names can (see _embed_name), so no other struct collides with it.
_GORM_IMPORTS = frozenset({"gorm.io/gorm", "github.com/jinzhu/gorm"})
_GORM_MODEL = "gorm.io/gorm.Model"
_GORM_MODEL_FIELDS = ("ID", "CreatedAt", "UpdatedAt", "DeletedAt")

# stdlib / value types that are never a GORM entity ref or an injected dependency.
_NON_ENTITY_TYPES = {
    "Time", "Duration", "Context", "Reader", "Writer", "ReadWriter", "Buffer", "Builder",
    "WaitGroup", "Mutex", "RWMutex", "Once", "Cond", "Map", "Value", "Pool", "URL", "IP",
    "Regexp", "Template", "File", "Cmd", "Int", "Int64", "Float64", "Bool", "String", "Rat",
    "Decimal", "UUID", "RawMessage", "Number",
}

# The first element of every Go standard-library import path. A stdlib package is never part of the
# repo, so a `pkg.X` that names one is never an edge target (the Go toolchain rule is "no dot in the
# first element"; a go.mod `module myapp` breaks that rule, hence the explicit list).
_GO_STDLIB_ROOTS = frozenset({
    "archive", "bufio", "builtin", "bytes", "cmp", "compress", "container", "context", "crypto",
    "database", "debug", "embed", "encoding", "errors", "expvar", "flag", "fmt", "go", "hash",
    "html", "image", "index", "io", "iter", "log", "maps", "math", "mime", "net", "os", "path",
    "plugin", "reflect", "regexp", "runtime", "slices", "sort", "strconv", "strings", "structs",
    "sync", "syscall", "testing", "text", "time", "unicode", "unique", "unsafe", "weak", "C",
})
# Stdlib package NAMES, for a qualifier with no import to anchor it (a snippet, a failed import parse).
_GO_STDLIB_NAMES = frozenset(_GO_STDLIB_ROOTS | {
    "http", "url", "fs", "sql", "driver", "json", "xml", "csv", "hex", "base64", "binary", "pem",
    "gob", "template", "filepath", "exec", "signal", "user", "atomic", "big", "rand", "tls",
    "x509", "sha1", "sha256", "sha512", "md5", "hmac", "rsa", "ecdsa", "ed25519", "elliptic",
    "multipart", "textproto", "mail", "smtp", "rpc", "httptest", "httputil", "cookiejar", "slog",
    "utf8", "utf16", "heap", "list", "ring", "gzip", "zip", "tar", "zlib", "pprof", "trace",
    "fstest", "iotest", "quick", "tabwriter", "scanner", "color", "png", "jpeg", "gif", "draw",
    "crc32", "fnv", "netip", "ioutil", "bits", "cmplx",
})

_IMPORT_ONE_RE = re.compile(r'^\s*import\s+(?:([A-Za-z_]\w*|\.)\s+)?"([^"]+)"')
_IMPORT_SPEC_RE = re.compile(r'^\s*(?:([A-Za-z_]\w*|\.)\s+)?"([^"]+)"')
# A receiver variable whose type the file shows: `h := pkg.NewT(...)`, `h := &pkg.T{...}`,
# `var h = NewT()`, `var h *pkg.T`. The type makes a method-value handler `h.M` resolve to `T.M`.
_VAR_CTOR_RE = re.compile(r'\b([A-Za-z_]\w*)\s*:?=\s*&?\s*(?:[A-Za-z_]\w*\.)?New([A-Z]\w*)\s*\(')
_VAR_LIT_RE = re.compile(r'\b([A-Za-z_]\w*)\s*:?=\s*&?\s*(?:[A-Za-z_]\w*\.)?([A-Z]\w*)\s*\{')
_VAR_DECL_RE = re.compile(r'^\s*var\s+([A-Za-z_]\w*)\s+\*?(?:[A-Za-z_]\w*\.)?([A-Z]\w*)\b')


def demodulize(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1]


def _type_ref(raw: str) -> tuple:
    """A Go field/param type -> (qualifier, bare_type, is_collection). Strips slice/pointer/map
    wrappers. `[]*Order`->('', 'Order', True); `*models.Customer`->('models', 'Customer', False);
    `map[string]Tag`->('', 'Tag', True); `int`->('', 'int', False)."""
    t = (raw or "").strip()
    is_coll = t.startswith("[]") or t.startswith("map[")
    t = re.sub(r'^\[\]', '', t)
    t = re.sub(r'^map\[[^\]]*\]', '', t)
    t = t.lstrip("*").strip()
    qual = t.rsplit(".", 1)[0] if "." in t else ""
    t = demodulize(t)
    m = re.match(r'[A-Za-z_]\w*', t)
    return qual, (m.group(0) if m else ""), is_coll


def _import_name(path: str) -> str:
    """The package name an import path binds by default: its last element, skipping a `/vN` major
    version and a `.vN` / `go-` decoration (gopkg.in/yaml.v3 -> yaml, go-chi/chi/v5 -> chi)."""
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) > 1 and re.fullmatch(r'v\d+', parts[-1]):
        parts = parts[:-1]
    name = re.sub(r'\.v\d+$', '', parts[-1] if parts else "")
    if name.startswith("go-"):
        name = name[3:]
    return name.rsplit("-", 1)[-1]


def _parse_imports(lines: list) -> dict:
    """{package name (alias or derived) -> import path} for the file's `import` declarations
    (single-line and parenthesized; blank `_` imports bind nothing; dot imports are ignored)."""
    out: dict = {}
    in_block = False
    for s in lines:
        t = s.strip()
        if in_block:
            if t.startswith(")"):
                in_block = False
                continue
            m = _IMPORT_SPEC_RE.match(s)
        elif re.match(r'^import\s*\(', t):
            in_block = True
            rest = t.split("(", 1)[1].strip()
            m = _IMPORT_SPEC_RE.match(rest) if rest else None
            if rest.endswith(")"):
                in_block = False
        else:
            m = _IMPORT_ONE_RE.match(s)
        if m:
            alias, path = m.group(1), m.group(2)
            if alias in ("_", "."):
                continue
            out[alias or _import_name(path)] = path
    return out


def _is_stdlib(path: str) -> bool:
    first = (path or "").split("/", 1)[0]
    return "." not in first and first in _GO_STDLIB_ROOTS


def _qualified_scope(qual: str, imports: dict):
    """The resolution scope for a package-qualified TYPE `qual.T`, or None when it names a package
    outside the repo (stdlib) — such a type is never an edge target."""
    path = imports.get(qual)
    if path is not None:
        return None if _is_stdlib(path) else "import:" + path
    if qual in _GO_STDLIB_NAMES:
        return None
    return "pkgname:" + qual


def _strip_comments(lines: list) -> list:
    """Strip `//` line comments and `/* ... */` block spans BEFORE matching, string-aware. Go raw
    strings use backticks; a `//` inside a "..." or `...` string is not a comment."""
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
            if ch in '"`':
                q = ch
                buf.append(ch)
                i += 1
                while i < n:
                    if q == '"' and s[i] == "\\" and i + 1 < n:
                        buf.append(s[i:i + 2])
                        i += 2
                        continue
                    buf.append(s[i])
                    i += 1
                    if s[i - 1] == q:
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


class _StructCtx:
    def __init__(self, name: str, line: int):
        self.name = name
        self.line = line
        self.facts: list = []


def _summary(ctx: _StructCtx) -> str:
    base = f"{ctx.name}: Go struct"
    if ctx.facts:
        base += "; " + "; ".join(ctx.facts[:8])
    return base[:300]


def recognize(source: str, rel: str) -> tuple:
    """PURE per-file core (the registry contract): Go source -> (supplemental_nodes, typed_edges).
    Never raises on malformed source — unmatched lines are simply skipped."""
    nodes: list = []
    edges: list = []
    ctxs: list = []
    cur: _StructCtx | None = None      # the struct whose body we are inside
    depth = 0                          # brace depth inside the current struct body
    stem = Path(rel).stem

    def emit(src, dst, et, conf, attrs, scope="package"):
        if src and dst:
            edges.append(TypedEdge(src, rel, dst, et, conf, attrs, scope))

    lines = _strip_comments(source.splitlines())
    imports = _parse_imports(lines)
    var_types = _receiver_types(lines)
    fields = _Fields(_struct_shapes(lines, imports))   # GORM's field lookup over THIS file's structs

    def emit_type(src, qual, bare, et, attrs, type_is_principal=False) -> None:
        """A TYPE target: bare -> this package; `qual.T` -> the package `qual` imports (a stdlib
        package holds no repo symbol, so that edge is never emitted).

        type_is_principal (a GORM belongs-to CASCADES): the TYPE is the principal, so the edge runs
        the other way round — TYPE -> src, the dependent declaring struct (this file) being the dst.
        edge_resolve resolves a SRC in this file first, else in this file's own package only (the
        single same-named Go :Class of its directory — edge_resolve._src_in_package), so only a BARE
        principal — declared in this package — is emitted. A `pkg.T` principal is not: its qualifier,
        the only evidence of which package is meant, would be discarded, and its src would be looked
        up in THIS package, where a same-named local type would bind wrongly. Its REFERENCES edge
        still goes out."""
        scope = _qualified_scope(qual, imports) if qual else "package"
        if scope is None:
            return
        if not type_is_principal:
            emit(src, bare, et, "NAME", attrs, scope)
        elif not qual:
            emit(bare, src, et, "NAME", attrs, "package")

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue

        # -- routes: <router>.METHOD("/path", handler) — anywhere in the file --------------------
        for verb, path, handler in _ROUTE_RE.findall(raw):
            if not (path.startswith("/") or path == ""):
                continue                                # a real route path; filters cache.Get("k",v)
            method = "*" if verb.upper().startswith("HANDLE") else verb.upper()
            attrs = {"method": method}
            if path:
                attrs["path"] = path
            if "." not in handler:
                # A bare handler is a func of THIS package — this file (EXTRACTED) or a sibling file.
                emit(stem, handler, "ROUTES_TO", "LOCAL", attrs, "package")
                continue
            attrs["handler"] = handler                  # the raw qualified form, kept for the reader
            qual, name = handler.rsplit(".", 1)
            ipath = imports.get(qual)
            if ipath is not None:                       # pkg.Handler: a func of the imported package
                if not _is_stdlib(ipath):
                    emit(stem, name, "ROUTES_TO", "NAME", attrs, "import:" + ipath)
                continue
            # A method value on a receiver variable: `T.Method` when the file shows the type of the
            # variable, else any `*.Method` (graded by how many there are).
            typ = var_types.get(qual)
            emit(stem, f"{typ}.{name}" if typ else name, "ROUTES_TO", "NAME", attrs, "method")

        # -- constructor functions: func NewX(deps...) *X -> DEPENDS_ON X -> dep types ------------
        cm = _CTOR_FUNC_RE.match(raw)
        if cm:
            ret = cm.group(3) or ""
            # The built type: the declared return type, unless that is another package's type
            # (`func NewRouter() http.Handler`) — then the NewX name is the only local evidence.
            cls = cm.group(1) if (not ret or "." in ret) else ret
            for p in _split_top_commas(cm.group(2) or ""):
                toks = p.split()
                if len(toks) < 2:
                    continue
                qual, t, _ = _type_ref(toks[-1])
                if t and t[:1].isupper() and t not in _NON_ENTITY_TYPES and t != cls:
                    emit_type(cls, qual, t, "DEPENDS_ON", {"via": "ctor", "param": toks[0]})

        # -- struct declarations + their field bodies --------------------------------------------
        sm = _STRUCT_RE.match(raw)
        if sm:
            cur = _StructCtx(sm.group(1), i)
            ctxs.append(cur)
            depth = 1                                   # the opening '{' of the struct body
            continue
        if cur is not None:
            depth += raw.count("{") - raw.count("}")
            if depth <= 0:
                cur = None
                continue
            _scan_field(line, cur, emit_type, fields)

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


def _split_top_commas(s: str) -> list:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _receiver_types(lines: list) -> dict:
    """{variable -> type name} for the variables whose type the file shows (last binding wins):
    `h := pkg.NewT(...)` / `h := &pkg.T{...}` / `var h *pkg.T`. Only feeds method-value handlers."""
    out: dict = {}
    for s in lines:
        for rx in (_VAR_CTOR_RE, _VAR_LIT_RE, _VAR_DECL_RE):
            for var, typ in rx.findall(s):
                out[var] = typ
    return out


def _names(csv: str) -> list:
    return [n.strip() for n in (csv or "").split(",") if n.strip()]


def _db_name(name: str) -> str:
    """GORM's default DB column for a Go field name (NamingStrategy.toDBName with no NameReplacer):
    initialisms title-cased, then snake_case that keeps a run of capitals (or capitals before a digit)
    together — `CompanyID` / `CompanyId` -> `company_id`, `APIKeyID` -> `api_key_id`, `SKU_ID` -> `sku_id`,
    `SHA256Hash` -> `sha256_hash`; `Companyid` -> `companyid` (so GORM never reads it as company_id)."""
    if not name:
        return ""
    v = _INITIALISM_RE.sub(lambda m: m.group(0)[0] + m.group(0)[1:].lower(), name)
    buf: list = []
    last, cur = False, "A" <= v[0] <= "Z"
    for i in range(len(v) - 1):
        nxt = v[i + 1]
        nxt_up = "A" <= nxt <= "Z"
        if cur:
            if not (last and (nxt_up or "0" <= nxt <= "9")) and i > 0 and v[i - 1] != "_" and nxt != "_":
                buf.append("_")
            buf.append(v[i].lower())
        else:
            buf.append(v[i])
        last, cur = cur, nxt_up
    if cur and not last and len(v) > 1:
        buf.append("_")
    buf.append(v[-1].lower() if cur else v[-1])
    return "".join(buf)


def _gorm_settings(tag: str) -> dict:
    """A field's `gorm:"..."` settings -> {UPPERCASE key: value}, split the way GORM's ParseTagSetting
    splits them: `;` between settings, the first `:` between key and value, keys case-insensitive
    (`foreignKey:X` and `foreignkey:X` are one setting)."""
    m = _GORM_TAG_RE.search(tag or "")
    out: dict = {}
    for part in (m.group(1) if m else "").split(";"):
        key, _, val = part.partition(":")
        key = key.strip().upper()
        if key:
            out[key] = val.strip()
    return out


def _embed_name(raw: str, imports: dict) -> str:
    """The struct an embed names, as _Fields keys it: a bare same-package name, gorm.Model, or the raw
    `pkg.T` (a struct no lookup in this file can see); "" for an unexported embed, which GORM skips."""
    qual, base, _ = _type_ref(raw)
    if not base[:1].isupper():
        return ""
    if not qual:
        return base
    path = imports.get(qual)
    if base == "Model" and (path in _GORM_IMPORTS or (path is None and qual == "gorm")):
        return _GORM_MODEL
    return f"{qual}.{base}"


def _struct_shapes(lines: list, imports: dict) -> list:
    """Pre-pass: [(struct name, [(exported field name, its `column:` or "")], [(struct it embeds — see
    _embed_name —, its `embeddedPrefix:` or "")])] for every `type X struct {` of the file, tracked exactly
    as recognize() tracks struct bodies. It exists because GORM ignores field ORDER — a belongs-to's foreign
    key may be declared AFTER its association field. GORM flattens an anonymous embedded struct and a
    named `gorm:"embedded"` one into the owner's columns alike, so both are embeds here."""
    shapes: list = []
    cur = None
    depth = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        sm = _STRUCT_RE.match(raw)
        if sm:
            cur = (sm.group(1), [], [])
            shapes.append(cur)
            depth = 1
            continue
        if cur is None:
            continue
        depth += raw.count("{") - raw.count("}")
        if depth <= 0:
            cur = None
            continue
        fm = _FIELD_RE.match(line)
        mm = None if fm else _MULTI_FIELD_RE.match(line)
        em = None if (fm or mm) else _EMBED_RE.match(line)
        if fm:
            settings = _gorm_settings(fm.group(3) or "")
            if "EMBEDDED" in settings:                  # its columns are this struct's, not a relation
                ename = _embed_name(fm.group(2), imports)
                if ename:
                    cur[2].append((ename, settings.get("EMBEDDEDPREFIX", "")))
            else:
                cur[1].append((fm.group(1), settings.get("COLUMN", "")))
        elif mm:                                        # GORM maps exported fields only
            cur[1].extend((n, "") for n in _names(mm.group(1)) if n[:1].isupper())
        elif em:
            ename = _embed_name(em.group(1), imports)
            if ename:
                cur[2].append((ename, _gorm_settings(em.group(2) or "").get("EMBEDDEDPREFIX", "")))
    return shapes


class _Fields:
    """GORM's field lookup (schema.LookUpField) over the `type X struct` declarations of ONE file.

    Kept per struct NAME — same-named declarations (the function-local types of a test file) are merged,
    the granularity edge_resolve resolves them at too — and walked LAZILY: a lookup probes the struct's own
    fields, then, breadth-first, the structs it embeds (anonymously or `gorm:"embedded"`; an
    `embeddedPrefix:` prefixes the column, never the Go name), and is memoized per (struct, key). Every walk
    stops after _STEPS steps (a state entered, an embed entry or a distinct prefix examined) and then reports
    itself cut short, so a file costs O(lines + lookups x _STEPS) — never O(cascade fields x same-named
    declarations x embeds): materialized per field, that walk took minutes on one valid Go file
    (ISS-build:014 repair round 2). gorm.Model's fields are known; any other embed this file does not
    declare leaves the struct's field set OPEN (complete() is False)."""

    _STEPS = 64                                # per walk: embeds may cycle (through pointers) or fan out

    def __init__(self, shapes):
        # struct -> {(Go field name, its `column:` tag or ""): None} over every same-named declaration
        self._own: dict = {_GORM_MODEL: {(f, ""): None for f in _GORM_MODEL_FIELDS}}
        self._names: dict = {_GORM_MODEL: set(_GORM_MODEL_FIELDS)}   # struct -> its own Go field names
        self._emb: dict = {}                    # struct -> {embedded struct: None} (first-seen order)
        self._emb_pre: dict = {}                # struct -> {embeddedPrefix: {embedded struct: None}}
        self._cols: dict = {}                   # struct -> {DB column: Go field name}, on first need
        self._memo: dict = {}                   # (struct, key) -> (Go field name or None, walk cut short)
        self._done: dict = {}                   # struct -> complete()
        for sname, own, embeds in shapes:
            mine = self._own.setdefault(sname, {})
            names = self._names.setdefault(sname, set())
            for fname, column in own:
                mine.setdefault((fname, column), None)
                names.add(fname)
            emb = self._emb.setdefault(sname, {})
            by_pre = self._emb_pre.setdefault(sname, {})
            for ename, pre in embeds:
                emb.setdefault(ename, None)
                by_pre.setdefault(pre, {}).setdefault(ename, None)

    def lookup(self, struct: str, key: str):
        """LookUpField(key) on `struct`: the Go name of the field whose DB column is `key`, else of the field
        whose Go name is `key` — both EXACT, as GORM compares them. None when this file shows no such
        field (the struct is not declared here, or holds none)."""
        return self._find(struct, key)[0]

    def lacks(self, struct: str, key: str) -> bool:
        """True only when `struct` PROVABLY has no field `key`: every field of it is visible here and the
        walk was not cut short. Otherwise a miss may be a field this file cannot see."""
        hit, cut = self._find(struct, key)
        return hit is None and not cut and self.complete(struct)

    def complete(self, struct: str) -> bool:
        """`struct` is declared here and so is every struct it embeds, transitively (within _STEPS)."""
        if struct not in self._done:
            ok, todo, seen, left = struct in self._own, [struct], {struct}, self._STEPS
            while ok and todo:
                for ename in self._emb.get(todo.pop(), ()):
                    left -= 1
                    if left <= 0 or ename not in self._own:
                        ok = False
                        break
                    if ename not in seen:
                        seen.add(ename)
                        todo.append(ename)
            self._done[struct] = ok
        return self._done[struct]

    def _find(self, struct: str, key: str) -> tuple:
        mk = (struct, key)
        if mk not in self._memo:
            if not key or struct not in self._own:
                self._memo[mk] = (None, False)
            else:
                hit, cut = self._walk(struct, key, True)       # FieldsByDBName first ...
                if hit is None:
                    hit, cut_name = self._walk(struct, key, False)   # ... then FieldsByName
                    cut = cut or cut_name
                self._memo[mk] = (hit, cut)
        return self._memo[mk]

    def _columns(self, struct: str) -> dict:
        cols = self._cols.get(struct)
        if cols is None:
            cols = self._cols[struct] = {}
            for fname, column in self._own.get(struct, ()):
                cols.setdefault(column or _db_name(fname), fname)
        return cols

    def _walk(self, struct: str, key: str, by_column: bool) -> tuple:
        """One breadth-first pass (own fields first, then the embeds by depth) -> (hit, cut short). By
        column, a state is (struct, the embeddedPrefix gathered on the way), and only an embed whose prefix
        continues `key` is entered — no column below any other one can equal `key`."""
        todo, seen, i, left = [(struct, "")], {(struct, "")}, 0, self._STEPS
        while i < len(todo):
            s, pre = todo[i]
            i += 1
            left -= 1
            if by_column:
                hit = self._columns(s).get(key[len(pre):])
                groups = []
                if hit is None:
                    for p, names in self._emb_pre.get(s, {}).items():
                        left -= 1                       # one step per distinct embeddedPrefix
                        if left <= 0:
                            return None, True
                        if key.startswith(p, len(pre)):
                            groups.append((pre + p, names))
            else:
                hit = key if key in self._names.get(s, ()) else None
                groups = [("", self._emb.get(s, ()))]
            if hit is not None:
                return hit, False
            for npre, names in groups:
                for ename in names:
                    left -= 1
                    if left <= 0:
                        return None, True
                    st = (ename, npre)
                    if st not in seen:
                        seen.add(st)
                        todo.append(st)
        return None, False


def _belongs_to_key(owner: str, fname: str, qual: str, elem: str, tag: str, fields: _Fields) -> str:
    """A SINGLE association field `fname elem` of struct `owner` whose tag carries an OnDelete
    constraint -> the Go field name(s) of its foreign key when GORM reads it as BELONGS-TO (the key
    lives on `owner`, so the constraint's principal is the ELEMENT), else "" — HAS-ONE (the key lives
    on the element, `owner` is the principal). GORM's guessRelation, as far as this file shows it:
      * a polymorphic association (`polymorphic:`, or `polymorphicType:` WITH `polymorphicId:`) is
        has-one (or has-many), never belongs-to;
      * has-one is guessed FIRST (belongs-to first only for a self-referential field — a self-loop
        either way): the element — a struct declared in this file, never a `pkg.T` — holds every
        `foreignKey:` field, else `<Owner><PK>`, plus `<Owner>ID` / `<Owner>Id` / its DB column for a
        single key. <PK> is each `references:` field, looked up on the OWNER (GORM's primary side) —
        one the owner provably lacks fails the step, as GORM then finds no primary field — else ID;
      * then belongs-to: `owner` holds the key the same way — every `foreignKey:` field, else
        `<Field><PK>` (+ `<Field>ID` / `<Field>Id` / its column), <PK> from `references:` looked up
        on the element;
      * otherwise has-one: the key is on an element this file does not show.
    Every key is looked up like GORM's LookUpField — the exact DB column (its `column:` tag, else GORM's
    default naming, behind any `embeddedPrefix:`), then the exact Go name — through the struct's own
    fields and the structs it embeds (see _Fields). A `references:` field this file cannot see (on an
    element declared elsewhere, or behind an embed declared elsewhere) is taken as written. Not seen from
    one file: a principal's primary key other than ID without `references:`, a non-default NamingStrategy,
    and a key that only an embed declared in another file holds."""
    settings = _gorm_settings(tag)
    if "POLYMORPHIC" in settings or ("POLYMORPHICTYPE" in settings and "POLYMORPHICID" in settings):
        return ""
    fks = _names(settings.get("FOREIGNKEY", ""))
    refs = _names(settings.get("REFERENCES", ""))
    local = "" if qual else elem                        # a `pkg.T` element is no struct of this file

    def step(primary: str, foreign: str, prefix: str) -> str:
        """One guess: the foreign key's Go field name(s) in `foreign`, or "" when this file shows the
        step failing (or cannot show it succeeding)."""
        pks = []
        for ref in refs:
            hit = fields.lookup(primary, ref)
            if hit is None and fields.lacks(primary, ref):
                return ""
            pks.append(hit or ref)
        if fks:
            found = [fields.lookup(foreign, k) for k in fks]
            return ",".join(found) if all(found) else ""
        found = []
        for pk in pks or ["ID"]:
            names = [prefix + pk]
            if len(pks) <= 1:                           # GORM adds these for a single primary key only
                names += [prefix + "ID", prefix + "Id", _db_name(prefix + "ID")]
            hit = next((h for h in (fields.lookup(foreign, n) for n in names) if h), None)
            if hit is None:
                return ""
            found.append(hit)
        return ",".join(found)

    if local and step(owner, local, owner):             # has-one wins when the element shows the key
        return ""
    return step(local, owner, fname)


def _scan_field(line: str, cur: "_StructCtx", emit_type, fields: _Fields) -> None:
    """One struct-body line -> a GORM relation / cascade / embedding edge (or nothing). `fields` (GORM's
    field lookup over this file's structs) orients a single field's cascade."""
    fm = _FIELD_RE.match(line)
    if fm:
        fname, ftype, tag = fm.group(1), fm.group(2), fm.group(3) or ""
        qual, elem, is_coll = _type_ref(ftype)
        if elem and elem[:1].isupper() and elem not in _NON_ENTITY_TYPES:
            et = "HAS_MANY" if is_coll else "REFERENCES"
            emit_type(cur.name, qual, elem, et, {"field": fname})
            cur.facts.append(f"{et.lower()} {fname}")
            if _CASCADE_RE.search(tag):
                fk = "" if is_coll else _belongs_to_key(cur.name, fname, qual, elem, tag, fields)
                if fk:      # belongs-to: the element is the principal -> element -> owner (bare only)
                    emit_type(cur.name, qual, elem, "CASCADES",
                              {"via": "gorm", "field": fname, "foreign_key": fk}, type_is_principal=True)
                    cur.facts.append(f"cascade {elem} -> {cur.name}")
                else:       # has-many / has-one: the key is on the element -> owner -> element
                    emit_type(cur.name, qual, elem, "CASCADES", {"via": "gorm", "field": fname})
                    cur.facts.append(f"cascade {cur.name} -> {elem}")
        return
    em = _EMBED_RE.match(line)
    if em:                                              # an anonymous embedded field -> INHERITS
        qual, base, _ = _type_ref(em.group(1))
        if base and base[:1].isupper() and base not in _NON_ENTITY_TYPES:
            emit_type(cur.name, qual, base, "INHERITS", {"via": "embed"})
            cur.facts.append("embeds " + base)
