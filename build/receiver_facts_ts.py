#!/usr/bin/env python3
"""TypeScript / JavaScript receiver reader for typed-receiver call linking (DEEPMAP_TYPED_CALLS; receiver-calls
design 3.2 / 3.3 / 5, slice S3c).

build/typed_calls.py (the core) loads this module for the `typescript` (.ts .tsx .mts .cts) and `javascript` (.js
.jsx .mjs .cjs) languages through `make_reader(spec)`. Phase A (`facts`) lowers each file's tree-sitter tree (the
sync's shared graphify_extract._Source.ts_tree; .mts / .cts parsed here with the TypeScript grammar from
source.raw) to the frozen IR (typed_calls.FileFacts); Phase B in the core types every receiver from those facts
and from this module's binder, which places imports in the tree.

WHAT THE FACTS SAY (the core decides every verdict from them; see the READER CONTRACT in build/typed_calls.py)
  imports   ESM `import X from` (default), `import { a as b }` (named), `import * as ns` (namespace),
            `import x = require()` and a top-level CommonJS `const x = require()` (module),
            `const { a: b } = require()` / `const x = require().a` (named). `import type` names are imported (an
            annotation still resolves) but a VALUE use of one is never a typed receiver.
  classes   class / abstract class declarations (top level or local to a function) and interfaces (always open).
            bases = the `extends` operand only (never `implements`). methods = method definitions, an abstract or
            overload-only signature, and a function-valued class field (`h = () => ...`: graphify 0.8.51 and the
            typescript recognizer make it the `Class.h` method node); get / set accessors carry "property" (never
            typed); a static member "static". fields = every typing of `f`: `f: T`, `f = new T()`, `f = inject(T)`
            (Angular, `inject` from @angular/core), a constructor parameter property (`private repo: Repo`), each
            `this.f = <expr>` in the class's own instance code (`this.f = p` reads p's annotation: an injected field,
            INFERRED); a static field, a compound or destructuring write is an untyped binding, and so is a write the
            reader cannot attribute to a declared type: through a `this` alias (`const self = this; self.f = ...`), a
            `this.f = ...` in a callback / initializer lexically inside the class (`function () {...}.bind(this)`),
            and in JavaScript (no declared type holds a field) ANY in-file `x.f = ...`. A string-literal computed name
            (`['save']()`) is a member name. open = an interface, an ambient / .d.ts class, an index signature, a
            computed `this[k] =` write, any other computed member name (`[Symbol.iterator]()`), or a same-named
            interface or namespace in the file (declaration merging) — so `absent` (a closed .ts chain without m)
            never fires there (`// @ts-nocheck` / `@ts-ignore` are the core's LanguageSpec.unchecked rule).
            dynamic = a constructor that returns a value, `Object.assign(this | a this alias, ...)` /
            `Object.defineProperty(this, ...)`, a mixin written onto its prototype in the file (see patches), or a
            parse error inside the class.
  scopes    every function-like node, its Scope.kind naming the syntax (the core's ATTRIBUTION: graphify's call
            walk enters function expressions and generator declarations, stops at the rest): "function_decl",
            "generator_decl", "function_expr" (a `function` / `function*` expression, also an object value or a
            class field value), "arrow" (also a class field value), "method_def" (a class / object-literal method,
            accessor or constructor). `this` is the class (SELF) only in a class method, an arrow nested in one or a
            function-valued class field ("class" in a static one); in a `function` expression, an object-literal
            method, an anonymous class or a non-function field initializer it is OTHER. A TS namespace body is a
            scope with no name, kind "namespace" (it shadows its declarations, never a caller).
  bindings  params: `x: T` / `x?: T` (JS: untyped); a destructured / rest parameter is poisoned. locals: one entry
            per declarator (`new T()`, `: T`, a call, an alias `= y` / `= this.f`, `= null`); any reassignment,
            `++`, compound write, for-in/of variable, catch parameter, destructuring, local function, class,
            interface, `type` alias, `enum` or `import X = N.Y` declaration of a name poisons it in its scope
            (design 3.2: single binding only; a local type declaration shadows an outer class of that name). `x as T`,
            `<T>x`, `await`, a ternary, a literal: untyped.
  types     `T`, `ns.T`, `T<...>` (generic: the core reads the head), `T | null | undefined`, `this` (self);
            arrays, tuples, functions, object / literal / intersection / conditional types and a type parameter in
            scope (class, method, function, enclosing ones) are unknown; predefined types are builtin (any /
            unknown / object are opaque in the core). A top-level `type Map = ...` named like a builtin type is
            bound (untyped), so it never reads as the builtin; any other alias stays unbound (it types nothing).
  sites     every call and `new` in a scope's own body: `f(...)` bare; `recv.m(...)` / `recv?.m(...)` member with
            recv lowered to SELF / SUPER / IDENT / FIELD / CALL / NEW / LITERAL / OTHER (`(x as T)`, `x[i]`, a
            type-only import used as a value, `this` outside a class method -> OTHER; `x!` and `(x)` are x; a chain
            deeper than _MAX_RECV_DEPTH ends in OTHER, below the IR validator's limit, so no file is ever dropped);
            `new T()` / `new ns.T()` ctor. Calls in decorators and default parameters are recorded nowhere (their
            caller's census and textual veto still count them, so they block a REPLACE of that name).
  all_names every call over a scope's full byte range (nested scopes, default parameters included), and for a
            method reached without being named as a call (`x["m"](...)`, `x.m.call / apply / bind(...)`) one more
            `m` with no site — so `m` is never REPLACEd in that caller.
  parse     tree-sitter's error recovery can move code between functions: a scope whose range overlaps an ERROR /
  errors    MISSING node, or that sits inside such a scope, keeps NO site (no edge, no removal) and +1 on every
            name of its census; a class whose span holds one is dynamic. At the top level, an error in an import, a
            declaration list or a program-level ERROR adds a star import (no unbound name resolves, not even to a
            builtin), and one in an export or CommonJS statement outside a declaration makes the exports unknown;
            an error inside a declaration (zod's `<out T>` variance, which this grammar does not parse) stays local.
  decls     the (line, byte col) of every declared function / method / signature name (the textual veto skips them).
  patches   `C.prototype.m = ...` (anywhere), `C.m = ...` (module level), `Object.defineProperty(C.prototype, 'm')`;
            a mixin: a call whose FIRST argument is `C.prototype` (Object.assign, defineProperties, setPrototypeOf,
            _.extend, a mixin helper — not Object.keys, console.log or another read-only use) patches every key its
            other arguments provably add (an object literal; a module-level object literal with its `x.k =`
            writes) and '*' (any member) for a source it cannot read; a whole `C.prototype = ...` write patches its
            keys and '*' — from any file, so a mixin onto an imported class reaches the core. A global object the
            project extends: `window.m` / `globalThis.m` / `self.m` / `global.m` writes (anywhere), Object.assign /
            defineProperty onto them, `declare global` members (interface Window -> window / globalThis; interface
            String -> String; a var / function -> window / globalThis; no file-level interface is made for them).

THE BINDER (imports -> project files; Phase B asks it, never the engine)
  A specifier is placed by typescript_edges.modules_for(...).resolve — THE rule typed-edge placement uses
  (relative paths, NodeNext `.js` names of `.ts` files, index files, the nearest tsconfig / jsconfig `paths` and
  `baseUrl` with `extends`, package.json `imports`, workspace packages). The file list is the recognizer walk
  (language_recognizers.walk_files with _TS_IGNORE) plus every TS / JS graph file. A specifier the tree does not
  hold (external_stems): a Node.js core module or a known third-party @scope is external by its package stem; else
  the package.json files above the importing file decide — a dependency one of them declares is external by its
  package stem, an undeclared one unknown (an alias no config maps: Vite / webpack `@utils/x`, `@helpers`); with no
  package.json above, spec_stems: a `$` / `~` / `#` / `_` specifier is unknown (SvelteKit `$lib`), any other gives
  its package stem AND every path segment to the core's conservative stem test (`@utils/format` names a project
  directory `utils`, so it is unknown). An alias the tsconfig does not map, an unreadable or missing file: unknown.
  What a module EXPORTS is read from the same tree-sitter parse that typed its receivers (the reader hangs a
  private `_tsjs` table on each FileFacts; it is not part of the IR): ESM declarations, `export { a as b }`,
  `export default`, re-exports (`export { a } from`, `export * from` — one star match only, `export * as ns from`)
  and CommonJS (`module.exports = X | { ... } | require()`, `exports.k = ...`, `module.exports.k = require()`
  barrels). A computed or Object.assign / defineProperty export write, a spread in the exported object or an export
  write inside a function makes the module's exports unknown.
  * a named import -> the module (or the re-exported module, depth <= 6, cycle-safe) that defines it:
    symbol(rel, local) | a module object (`export * as ns`, `exports.k = require()`) | external | unknown.
  * a default import -> the ESM default (`export default X` -> X; an expression -> unknown), else a CommonJS
    `module.exports = X` -> X, `module.exports = { ... }` / `exports.k` -> the module object.
  * a namespace import / require -> the module object: require of `module.exports = X` IS X (symbol); of an
    object or `exports.k` module, or of an ESM module, its export table. That table is handed to the core as
    module(rel) — then `ns.m()` reads the module's own top-level m — ONLY when every export key that is also a
    top-level name of the module means that name itself (no `export { g as f }` beside a local f, no
    `exports.f = other` beside a local f, no re-export colliding with a local, no `export *` target that may export
    a top-level name the module keeps private — ES: a private local never shadows a star export) and nothing
    exports dynamically: then a member the module exports and its top-level binding are one and the same. (Calling
    a member the module does not export is a compile error in TypeScript and a TypeError at run time.) Otherwise it
    is namespace(token):
    a type `ns.T`, `new ns.T()` and `ns.T.m()` still resolve through submodule(); a direct `ns.f()` call stays
    unknown in the core. `import * as ns` of a CommonJS `module.exports = X` module is unknown (interop).
  submodule(module | token, attr) answers a member of a module object exactly from its export table.
  resolve_free: None (TypeScript / JavaScript globals are the LanguageSpec builtins) — except a builtin global some
  file patches (`window.helper = helper`, `String.prototype.x = ...`, a `declare global` member): unknown in every
  file, so `window.helper()` is never an external call.

facts() returns None for an unreadable file and may raise on a reader bug (the core then gives that file no facts
and goes on); canary() and the binder never raise. Imports only the stdlib at import time (typed_calls,
tree-sitter, typescript_edges and language_recognizers are imported lazily).
"""
from __future__ import annotations

import bisect
import importlib
import json
import posixpath
import sys
import weakref
from pathlib import Path

READER_VERSION = "tsjs-2"
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts")
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")
_DECL_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
# The deepest Receiver / TypeRef nesting the reader builds (deeper -> OTHER / unknown). typed_calls.validate_facts
# rejects a FILE whose receiver or type nests deeper than 32 (_ok_recv / _ok_typeref), so these stay below it.
_MAX_RECV_DEPTH = 30
_MAX_TYPE_DEPTH = 30
_REEXPORT_DEPTH = 6
# Global objects a project may hang its own functions on (`window.helper = helper`; `self` in a worker, `global` in
# Node): a write there is a patch from any scope.
_GLOBAL_OBJECTS = frozenset({"window", "globalThis", "self", "global"})
# `declare global { interface Window { m(): T } }`: members of the global object
_GLOBAL_IFACES = frozenset({"Window", "WindowOrWorkerGlobalScope"})
# Calls that read `X.prototype` without adding members to it (anything else with `X.prototype` as its FIRST argument
# — Object.assign, _.extend, a mixin helper — may add members: a patch of every key it provably adds, else '*').
_PROTO_READONLY = frozenset({
    "keys", "values", "entries", "getOwnPropertyNames", "getOwnPropertyDescriptor", "getOwnPropertyDescriptors",
    "getOwnPropertySymbols", "getPrototypeOf", "isPrototypeOf", "hasOwn", "hasOwnProperty", "propertyIsEnumerable",
    "isFrozen", "isSealed", "isExtensible", "freeze", "seal", "preventExtensions", "ownKeys", "has", "get", "create",
    "call", "apply", "log", "info", "warn", "error", "debug", "dir", "trace", "assert", "expect", "is", "equal",
    "strictEqual", "deepEqual", "ok"})
# Node.js core modules (a bare `fs`, `path/posix`): never a project module and never declared in package.json
_NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib"})
_DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _sibling(name: str):
    """A build/ sibling module, imported lazily (build/ put on sys.path when this file was loaded by path)."""
    try:
        return importlib.import_module(name)
    except ImportError:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        return importlib.import_module(name)


_T = None


def _ir():
    """build/typed_calls.py: the frozen IR (imported lazily, once)."""
    global _T
    if _T is None:
        _T = _sibling("typed_calls")
    return _T


# ================================================================================================
# Grammars
# ================================================================================================

def dialect_of(rel) -> "str | None":
    """'tsx' | 'ts' | 'js' for a TS / JS path (case-insensitive), else None."""
    low = str(rel or "").lower()
    if low.endswith(".tsx"):
        return "tsx"
    if low.endswith((".ts", ".mts", ".cts")):
        return "ts"
    if low.endswith(_JS_EXTS):
        return "js"
    return None


_LANGS: dict = {}


class GrammarUnavailable(RuntimeError):
    """A tree-sitter binding or grammar is not importable here (an OPTIONAL dependency: graphifyy's own, never
    declared by DeepMap — tests/compliance_gates.py section 13). The reader's canary then fails and typed_calls
    disables the language: link() leaves every tuple of it unchanged."""


def _language(dialect: str):
    """The tree-sitter Language of a dialect (cached); raises GrammarUnavailable when the grammar is not
    importable."""
    if dialect not in _LANGS:
        try:
            import tree_sitter as ts
            if dialect == "js":
                import tree_sitter_javascript as g
            else:
                import tree_sitter_typescript as g
        except ImportError as exc:          # optional (see GrammarUnavailable): the language is disabled
            raise GrammarUnavailable(f"no tree-sitter grammar for {dialect}: {exc}") from exc
        if dialect == "js":
            _LANGS[dialect] = ts.Language(g.language())
        else:
            _LANGS[dialect] = ts.Language(g.language_tsx() if dialect == "tsx" else g.language_typescript())
    return _LANGS[dialect]


def parse_bytes(data: bytes, dialect: str):
    """The root node of `data` parsed with the dialect's grammar (raises GrammarUnavailable without one)."""
    try:
        import tree_sitter as ts
    except ImportError as exc:              # optional (see GrammarUnavailable)
        raise GrammarUnavailable(f"no tree-sitter binding: {exc}") from exc
    return ts.Parser(_language(dialect)).parse(data).root_node


def grammars_available() -> bool:
    """Do the tree-sitter binding and both grammars import here? (A cheap check; the canary is the real one.)"""
    try:
        _language("ts")
        _language("tsx")
        _language("js")
        return True
    except Exception:  # noqa: BLE001 — absent or broken: unavailable
        return False


def _tree_of(rel, source):
    """(root node, bytes) through the sync's shared reader: source.ts_tree(rel), else (.mts / .cts, which
    ts_tree does not parse, or a reader without trees) the dialect's grammar over source.raw(rel). None when the
    file is unreadable or no grammar is importable."""
    got = None
    try:
        got = source.ts_tree(rel)
    except Exception:  # noqa: BLE001
        got = None
    if got is not None:
        try:
            tree, data = got
            return tree.root_node, data
        except Exception:  # noqa: BLE001
            pass
    d = dialect_of(rel)
    if d is None:
        return None
    data = source.raw(rel)
    if data is None:
        return None
    return parse_bytes(data, d), data


# ================================================================================================
# Small AST helpers
# ================================================================================================

_CLASS_DECLS = frozenset({"class_declaration", "abstract_class_declaration"})
_SIGNATURES = frozenset({"method_signature", "abstract_method_signature"})
_FIELD_DEFS = frozenset({"public_field_definition", "field_definition"})
_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
_NONE_IDENTS = frozenset({"undefined"})
_LITERALS = {"string": "string", "template_string": "string", "number": "number", "regex": "RegExp",
             "array": "Array", "true": "boolean", "false": "boolean"}


def _text(n, data: bytes) -> str:
    return data[n.start_byte:n.end_byte].decode("utf-8", "replace")


def _line(n) -> int:
    return n.start_point[0] + 1


def _end_line(n) -> int:
    return n.end_point[0] + 1


def _col(n) -> int:
    return n.start_point[1]


def _field(n, name):
    try:
        return n.child_by_field_name(name)
    except Exception:  # noqa: BLE001
        return None


def _unwrap(n):
    """`(x)` and `x!` are x."""
    while n is not None and n.type in ("parenthesized_expression", "non_null_expression"):
        kids = [c for c in n.named_children if c.type != "comment"]
        if len(kids) != 1:
            return n
        n = kids[0]
    return n


_CASTS = frozenset({"as_expression", "satisfies_expression", "type_assertion"})


def _unwrap_write(n):
    """A WRITE target read through `(x)`, `x!` and the type assertions `x as T`, `x satisfies T`, `<T>x`: a write
    through `(x as any)` still writes x, and `(this as any).f = v` still writes this.f. Only write targets are read
    through a cast; a RECEIVER `(x as any).m()` stays OTHER (unknown)."""
    while n is not None:
        if n.type in ("parenthesized_expression", "non_null_expression"):
            kids = [c for c in n.named_children if c.type != "comment"]
        elif n.type in _CASTS:
            kids = [c for c in n.named_children if c.type not in ("comment", "type_arguments")]
            kids = kids[:1] if n.type != "type_assertion" else kids
        else:
            return n
        if len(kids) != 1:
            return n
        n = kids[0]
    return n


def _string_value(n, data: bytes):
    """The value of a plain string literal node, else None."""
    if n is None or n.type != "string":
        return None
    frags = [c for c in n.named_children if c.type != "comment"]
    if not frags:
        return ""
    if len(frags) == 1 and frags[0].type == "string_fragment":
        return _text(frags[0], data)
    return None


def _require_spec(n, data: bytes):
    """The specifier of `require('<spec>')`, else None."""
    n = _unwrap(n)
    if n is None or n.type != "call_expression":
        return None
    fn = _field(n, "function")
    if fn is None or fn.type != "identifier" or _text(fn, data) != "require":
        return None
    args = _field(n, "arguments")
    kids = [c for c in (args.named_children if args is not None else ()) if c.type != "comment"]
    if len(kids) != 1:
        return None
    return _string_value(kids[0], data)


def _is_exports_ref(n, data: bytes) -> bool:
    """`module.exports` or `exports`."""
    n = _unwrap(n)
    if n is None:
        return False
    if n.type == "identifier":
        return _text(n, data) == "exports"
    if n.type == "member_expression":
        o, p = _field(n, "object"), _field(n, "property")
        return (o is not None and o.type == "identifier" and _text(o, data) == "module"
                and p is not None and _text(p, data) == "exports")
    return False


def _prop_name(n, data: bytes):
    """The name of a property / method key: an identifier, `#private`, or a plain string; None when computed."""
    if n is None:
        return None
    if n.type in ("property_identifier", "private_property_identifier", "identifier", "type_identifier",
                  "shorthand_property_identifier"):
        return _text(n, data)
    if n.type == "string":
        v = _string_value(n, data)
        return v if v else None
    return None



def _params_node(fn):
    p = _field(fn, "parameters")
    if p is not None:
        return p
    p = _field(fn, "parameter")            # `x => ...`
    return p


def _arity(params_node, data: bytes) -> tuple:
    """(min, max) positional arguments a call passes (max -1: a rest parameter)."""
    if params_node is None:
        return (0, -1)
    if params_node.type == "identifier":
        return (1, 1)
    lo = hi = 0
    for c in params_node.named_children:
        t = c.type
        if t == "comment" or t == "decorator":
            continue
        pat = _field(c, "pattern") if t in ("required_parameter", "optional_parameter") else c
        if pat is not None and pat.type == "this":
            continue                                    # a TS `this:` parameter is no argument
        if pat is not None and pat.type == "rest_pattern" or t == "rest_pattern":
            return (lo, -1)
        optional = (t == "optional_parameter" or t == "assignment_pattern"
                    or (t == "required_parameter" and _field(c, "value") is not None))
        hi += 1
        if not optional:
            lo = hi
    return (lo, hi)


def _args_count(call, data: bytes) -> int:
    args = _field(call, "arguments")
    if args is None:
        return -1
    if args.type != "arguments":
        return 1                                        # a tagged template
    n = 0
    for c in args.named_children:
        if c.type == "comment":
            continue
        if c.type == "spread_element":
            return -1
        n += 1
    return n


def _type_param_names(n, data: bytes) -> frozenset:
    tp = _field(n, "type_parameters")
    if tp is None:
        return frozenset()
    out = set()
    for c in tp.named_children:
        if c.type == "type_parameter":
            nm = _field(c, "name")
            if nm is not None:
                out.add(_text(nm, data))
    return frozenset(out)


# ================================================================================================
# The module export table (private to this reader: FileFacts._tsjs)
# ================================================================================================

class ModInfo:
    """What a module exports, read from its parse. Descriptors: ("local", name) | ("reexport", spec, imported —
    "*" for `export * as`, "default") | ("require", spec) | ("expr",) (a value with no top-level name)."""
    __slots__ = ("esm", "default", "stars", "cjs_whole", "cjs_props", "has_esm", "dynamic", "top")

    def __init__(self):
        self.esm: dict = {}          # ESM export key -> descriptor ("default" never here)
        self.default = None          # the ESM default export's descriptor
        self.stars: list = []        # `export * from` specifiers, in order
        self.cjs_whole = None        # module.exports = ...: ("ident", X) | ("object", {key: desc}) | ("require",
        #                              spec) | ("other",)
        self.cjs_props: dict = {}    # exports.k = ... / module.exports.k = ...: key -> descriptor
        self.has_esm = False         # the module has import / export syntax
        self.dynamic = False         # an export written in a way not read (computed key, Object.assign, a spread,
        #                              inside a function, module.exports assigned twice): exports unknown
        self.top: frozenset = frozenset()   # every top-level name the file binds


def mod_info(ff):
    """The export table this reader hung on a FileFacts (None for another reader's facts)."""
    info = getattr(ff, "_tsjs", None)
    return info if isinstance(info, ModInfo) else None


# ================================================================================================
# The per-file reader
# ================================================================================================

class _Ctx:
    """Where the walk is: si = the scope call sites belong to (-1: module level); cls / self_kind = what `this`
    is (a class index and "inst" | "class"; -1 / "": not the class); tparams = type parameter names in scope;
    record = record call sites as sites of si; field_init = inside a non-function class field initializer; lex = the
    innermost class whose body lexically holds this code (-1: none) — a `this` there that is not the class's (a
    `function () {...}.bind(this)` callback, an object-literal method) may still be bound to an instance."""
    __slots__ = ("si", "cls", "self_kind", "tparams", "record", "field_init", "lex")

    def __init__(self, si=-1, cls=-1, self_kind="", tparams=frozenset(), record=False, field_init=False, lex=-1):
        self.si, self.cls, self.self_kind, self.tparams = si, cls, self_kind, tparams
        self.record, self.field_init, self.lex = record, field_init, lex

    def but(self, **kw) -> "_Ctx":
        d = {s: getattr(self, s) for s in self.__slots__}
        d.update(kw)
        return _Ctx(**d)


#: subtrees pass 2 never descends into for sites (types, enums, imports, comments)
_SKIP_TYPES = frozenset({"type_annotation", "type_alias_declaration", "type_arguments", "type_parameters",
                         "opting_type_annotation", "omitting_type_annotation", "asserts_annotation",
                         "type_predicate_annotation", "implements_clause", "enum_declaration",
                         "import_statement", "comment", "hash_bang_line", "interface_body",
                         "extends_type_clause", "index_signature"})


class _FileReader:
    """One file -> (FileFacts, ModInfo). Pass 1 reads the top-level statements (imports, exports, module-level
    functions and values); pass 2 walks the whole tree with an explicit stack (deep expressions never hit the
    recursion limit) for classes, scopes, bindings, sites, declarations and patches."""

    def __init__(self, rel: str, lang: str, data: bytes, root, T):
        self.rel, self.lang, self.data, self.root, self.T = rel, lang, data, root, T
        self.ts = dialect_of(rel) in ("ts", "tsx")
        self.ambient_file = rel.lower().endswith(_DECL_SUFFIXES)
        self.imports: dict = {}
        self.functions: dict = {}
        self.globals: dict = {}
        self.classes: list = []
        self.class_ranges: list = []         # (start byte, end byte) per class
        self.scopes: list = []
        self.ranges: list = []               # (start byte, end byte) per scope
        self.poison: list = []               # a set per scope
        self.ctor_of: dict = {}              # constructor scope index -> class index
        self.iface_names: dict = {}          # (enclosing scope, name) of interfaces / namespaces (merging)
        self.decls: set = set()
        self.patches: list = []
        self.calls: list = []                # (start byte, name) of every call / new
        self.errors: list = []               # (start, end) byte ranges of the parse errors (all_error_ranges)
        self.assigns: list = []              # (scope index, name) of every write to a plain name
        self.type_only: set = set()
        self.top_done: set = set()           # start bytes of the top-level declarations pass 1 bound
        self.cjs_done: set = set()           # start bytes of the export writes pass 1 read
        self.mod = ModInfo()
        self.this_alias: dict = {}           # (scope index, name) -> class index: `const self = this` in its code
        self.member_writes: list = []        # (scope index, object name | "", attr) of `<expr>.attr = ...` that is no
        #                                      class's own `this.attr` write
        self.assigned_objects: list = []     # (scope index, name) of `Object.assign(<name>, ...)`
        self.obj_literals: dict = {}         # module-level `const|let|var x = { ... }` -> its object node (None: 2+)
        self.proto_writes: list = []         # (X's parts, [source nodes], whole) of a call that may add members to
        #                                      X.prototype (it is the first argument) or a `X.prototype = src` write
        self.has_proto = b"prototype" in data   # no such text: no `X.prototype` anywhere (skip the mixin checks)

    # -------------------------------------------------------------- small builders
    def text(self, n) -> str:
        return _text(n, self.data)

    def arity(self, fn) -> tuple:
        """(min, max) positional arguments a call of function-like `fn` passes: TypeScript checks them (overloads
        are told apart by them); JavaScript has no overloads and any count is legal, so (0, -1)."""
        return _arity(_params_node(fn), self.data) if self.ts else (0, -1)

    def type_ref(self, parts):
        return self.T.t_name(parts[0]) if len(parts) == 1 else self.T.t_qual(*parts)

    def unknown(self, line=0, scope=-1):
        return self.T.Binding("unknown", line=line, scope=scope)

    def add_global(self, name, binding):
        if name:
            self.globals.setdefault(name, []).append(binding)

    def add_import(self, local, kind, spec, name, line):
        if local and spec is not None:
            self.imports.setdefault(local, []).append(self.T.Import(local, kind, spec, name, line))

    # -------------------------------------------------------------- types
    def typeref(self, n, tparams, depth=0):
        T = self.T
        if n is None or depth > _MAX_TYPE_DEPTH:
            return T.T_UNKNOWN
        t = n.type
        if t in ("type_annotation", "opting_type_annotation", "omitting_type_annotation", "parenthesized_type"):
            kids = [c for c in n.named_children if c.type != "comment"]
            return self.typeref(kids[0], tparams, depth + 1) if len(kids) == 1 else T.T_UNKNOWN
        if t in ("type_identifier", "identifier"):
            name = self.text(n)
            return T.T_UNKNOWN if name in tparams else T.t_name(name)
        if t == "nested_type_identifier":
            parts = tuple(p.strip() for p in self.text(n).split("."))
            if not parts or not all(parts) or parts[0] in tparams:
                return T.T_UNKNOWN
            return T.t_qual(*parts)
        if t == "generic_type":
            head = self.typeref(_field(n, "name"), tparams, depth + 1)
            if head.kind not in ("name", "qualified"):
                return T.T_UNKNOWN
            ta = _field(n, "type_arguments")
            args = tuple(self.typeref(c, tparams, depth + 1) for c in (ta.named_children if ta is not None else ())
                         if c.type != "comment")
            return T.t_generic(head, *args)
        if t == "predefined_type":
            return T.t_builtin(self.text(n).strip())
        if t == "literal_type":
            kids = n.named_children
            if len(kids) == 1 and kids[0].type in ("null", "undefined"):
                return T.t_builtin(kids[0].type)
            return T.T_UNKNOWN
        if t == "union_type":
            members = []
            stack = [n]
            while stack:
                x = stack.pop()
                if x.type == "union_type":
                    stack.extend(reversed([c for c in x.named_children if c.type != "comment"]))
                else:
                    members.append(self.typeref(x, tparams, depth + 1))
            return T.t_union(*members) if members else T.T_UNKNOWN
        if t == "this_type":
            return T.t_self()
        return T.T_UNKNOWN

    def ann(self, holder, field_name, tparams):
        """The TypeRef of a TS annotation field of `holder`, or None (none written, or JavaScript)."""
        if not self.ts:
            return None
        a = _field(holder, field_name)
        return self.typeref(a, tparams) if a is not None else None

    def dotted(self, n):
        """(parts, last node) of an identifier or a member chain of property identifiers (`a.b.C`), else (None,
        None)."""
        n = _unwrap(n)
        parts, last = [], None
        while n is not None and n.type == "member_expression":
            p = _field(n, "property")
            if p is None or p.type != "property_identifier":
                return None, None
            parts.append(self.text(p))
            last = last or p
            n = _unwrap(_field(n, "object"))
        if n is None or n.type != "identifier":
            return None, None
        parts.append(self.text(n))
        parts.reverse()
        return parts, (last or n)

    def type_of_parts(self, parts):
        if not parts or parts[0] in self.type_only:
            return None
        return self.T.t_name(parts[0]) if len(parts) == 1 else self.T.t_qual(*parts)

    def ctor_type(self, new_node):
        """The TypeRef `new <ctor>(...)` constructs (a name or a dotted name), or None (anything else, or a
        type-only import used as a value)."""
        parts, _last = self.dotted(_field(new_node, "constructor"))
        return self.type_of_parts(parts)

    # -------------------------------------------------------------- receivers and bindings
    def recv(self, n, ctx, depth=0):
        """The IR Receiver of an expression, sitting at nesting level `depth` of the Receiver being built: past
        _MAX_RECV_DEPTH the rest of a chain is OTHER (its calls are still sites of their own)."""
        T = self.T
        if n is None or depth > _MAX_RECV_DEPTH:
            return T.R_OTHER
        n = _unwrap(n)
        t = n.type
        if t in ("this", "super"):
            ok = ctx.cls >= 0 and not ctx.field_init and ctx.self_kind in ("inst", "class")
            return (T.R_SELF if t == "this" else T.R_SUPER) if ok else T.R_OTHER
        if t == "identifier":
            name = self.text(n)
            if name in _NONE_IDENTS or name in self.type_only:
                return T.R_OTHER
            return T.r_ident(name)
        if t == "member_expression":
            name = _prop_name(_field(n, "property"), self.data)
            if not name:
                return T.R_OTHER
            return T.r_field(self.recv(_field(n, "object"), ctx, depth + 1), name)
        if t == "call_expression":
            if any(c.type == "?." for c in n.children):
                return T.R_OTHER                          # `f?.()` may be undefined
            kind, name, inner, _node = self.callee(_field(n, "function"), ctx, depth + 1)
            if kind == "bare" and name not in ("require", "super"):
                return T.r_call(None, name)
            if kind == "member":
                return T.r_call(inner, name)
            return T.R_OTHER
        if t == "new_expression":
            tr = self.ctor_type(n)
            return T.r_new(tr) if tr is not None else T.R_OTHER
        if t in _LITERALS:
            return T.r_literal(_LITERALS[t])
        return T.R_OTHER

    def callee(self, fn, ctx, depth=0):
        """(kind, name, receiver, name node) of a call's function: ("bare", f, None, node) | ("member", m, recv,
        node) | (None, None, None, None); the receiver sits at nesting level `depth`."""
        if fn is None:
            return None, None, None, None
        f = _unwrap(fn) if fn.type == "parenthesized_expression" else fn
        t = f.type
        if t == "identifier":
            return "bare", self.text(f), None, f
        if t == "super":
            return "bare", "super", None, f
        if t == "member_expression":
            p = _field(f, "property")
            name = _prop_name(p, self.data)
            if not name:
                return None, None, None, None
            return "member", name, self.recv(_field(f, "object"), ctx, depth), p
        return None, None, None, None

    def angular_inject(self, call):
        """The TypeRef of `inject(T)` when `inject` is @angular/core's, else None."""
        fn = _field(call, "function")
        if fn is None or fn.type != "identifier":
            return None
        local = self.text(fn)
        if not any(i.kind == "named" and i.name == "inject" and i.spec == "@angular/core"
                   for i in self.imports.get(local, ())):
            return None
        args = _field(call, "arguments")
        kids = [c for c in (args.named_children if args is not None else ()) if c.type != "comment"]
        if len(kids) != 1:
            return None
        parts, _last = self.dotted(kids[0])
        return self.type_of_parts(parts)

    def value_binding(self, value, ann, si, ctx, line):
        """One Binding of a name: its annotation, else what its value says."""
        T = self.T
        if ann is not None:
            return T.Binding("ann", type=ann, line=line, scope=si)
        if value is None:
            return T.Binding("none", line=line, scope=si)
        v = _unwrap(value)
        t = v.type
        if t in ("null", "undefined") or (t == "identifier" and self.text(v) in _NONE_IDENTS):
            return T.Binding("none", line=line, scope=si)
        if t == "new_expression":
            tr = self.ctor_type(v)
            return T.Binding("ctor", type=tr, line=line, scope=si) if tr is not None else self.unknown(line, si)
        if t == "call_expression":
            tr = self.angular_inject(v)
            if tr is not None:
                return T.Binding("ctor", type=tr, line=line, scope=si)
            r = self.recv(v, ctx)
            return T.Binding("call", value=r, line=line, scope=si) if r.kind == "CALL" else self.unknown(line, si)
        if t in ("identifier", "member_expression", "this"):
            r = self.recv(v, ctx)
            return T.Binding("field", value=r, line=line, scope=si) if r.kind in ("IDENT", "FIELD", "SELF") \
                else self.unknown(line, si)
        return self.unknown(line, si)

    def pattern_names(self, n) -> list:
        """Every identifier a binding pattern (or a plain identifier) binds."""
        out, stack = [], [n]
        while stack:
            x = stack.pop()
            if x is None:
                continue
            t = x.type
            if t in ("identifier", "shorthand_property_identifier_pattern"):
                out.append(self.text(x))
            elif t == "pair_pattern":
                stack.append(_field(x, "value"))
            elif t in ("assignment_pattern", "object_assignment_pattern"):
                stack.append(_field(x, "left"))
            elif t in ("object_pattern", "array_pattern", "rest_pattern"):
                stack.extend(c for c in x.named_children if c.type != "comment")
            elif t in ("required_parameter", "optional_parameter"):
                stack.append(_field(x, "pattern"))
        return out

    # ======================================================================== pass 1: the top level
    def top_level(self):
        for st in self.root.named_children:
            self.top_statement(st)

    def top_statement(self, st):
        t = st.type
        if t == "import_statement":
            self.mod.has_esm = True
            self.read_import(st)
        elif t == "export_statement":
            self.mod.has_esm = True
            self.read_export(st)
        elif t in _DECLARATIONS:
            self.top_declaration(st)
        elif t in ("function_declaration", "generator_function_declaration", "function_signature"):
            self.top_function(st, (_line(st),))
        elif t == "enum_declaration":
            self.add_global(self.decl_name(st), self.unknown(_line(st)))
        elif t == "type_alias_declaration":
            self.type_alias(st)
        elif t == "expression_statement":
            kids = [c for c in st.named_children if c.type != "comment"]
            x = kids[0] if len(kids) == 1 else None
            if x is not None and x.type == "internal_module":
                self.add_global(self.decl_name(x), self.unknown(_line(x)))
            elif x is not None and x.type != "module":
                self.cjs_statement(x)
        elif t == "ambient_declaration":
            for c in st.named_children:
                if c.type in _DECLARATIONS:
                    self.top_declaration(c)
                elif c.type == "function_signature":
                    self.top_function(c, (_line(st),))
                elif c.type in ("enum_declaration", "internal_module"):
                    self.add_global(self.decl_name(c), self.unknown(_line(c)))
        elif t == "import_alias":
            nm = next((c for c in st.named_children if c.type == "identifier"), None)
            self.add_global(self.text(nm) if nm is not None else "", self.unknown(_line(st)))

    def type_alias(self, decl):
        """`type Map = ...`: a type alias named like a builtin type shadows it (an annotation `m: Map` must not read
        as the builtin and type `m.get()` external), so the name is bound, untyped. Any other alias stays unbound
        (an unbound type name types nothing) — so `type OK<T>` beside `const OK` leaves the const alone."""
        name = self.decl_name(decl)
        if name and name in self.T.LANGUAGES["typescript"].builtins:
            self.add_global(name, self.unknown(_line(decl)))

    def decl_name(self, decl) -> str:
        nm = _field(decl, "name")
        return self.text(nm).split(".")[0].strip() if nm is not None else ""

    def read_import(self, st):
        spec = _string_value(_field(st, "source"), self.data)
        all_type = any(c.type == "type" for c in st.children)
        line = _line(st)
        for c in st.named_children:
            if c.type == "import_require_clause":
                nm = next((x for x in c.named_children if x.type == "identifier"), None)
                rs = _string_value(_field(c, "source"), self.data)
                if nm is not None and rs is not None:
                    self.add_import(self.text(nm), "module", rs, "=", line)
                    if all_type:
                        self.type_only.add(self.text(nm))
                continue
            if c.type != "import_clause" or spec is None:
                continue
            for x in c.named_children:
                if x.type == "identifier":
                    self.add_import(self.text(x), "default", spec, "default", line)
                    if all_type:
                        self.type_only.add(self.text(x))
                elif x.type == "namespace_import":
                    nm = next((y for y in x.named_children if y.type == "identifier"), None)
                    if nm is not None:
                        self.add_import(self.text(nm), "namespace", spec, "*", line)
                        if all_type:
                            self.type_only.add(self.text(nm))
                elif x.type == "named_imports":
                    for sp in x.named_children:
                        if sp.type != "import_specifier":
                            continue
                        name, alias = _field(sp, "name"), _field(sp, "alias")
                        if name is None:
                            continue
                        imported = _prop_name(name, self.data) or self.text(name)
                        local = self.text(alias) if alias is not None else imported
                        self.add_import(local, "default" if imported == "default" else "named", spec, imported,
                                        line)
                        if all_type or any(y.type == "type" for y in sp.children):
                            self.type_only.add(local)

    def declared_names(self, decl) -> list:
        """The names a top-level declaration binds."""
        if decl.type in _DECLARATIONS:
            out = []
            for d in decl.named_children:
                if d.type == "variable_declarator":
                    out += self.pattern_names(_field(d, "name"))
            return out
        name = self.decl_name(decl)
        return [name] if name else []

    def read_export(self, st):
        m = self.mod
        line = _line(st)
        default = any(c.type == "default" for c in st.children)
        decl, value = _field(st, "declaration"), _field(st, "value")
        spec = _string_value(_field(st, "source"), self.data) if _field(st, "source") is not None else None
        if any(c.type == "=" for c in st.children):            # TypeScript `export = X`
            x = _unwrap(next((c for c in st.named_children if c.type not in ("comment", "decorator")), None))
            whole = ("ident", self.text(x)) if (x is not None and x.type == "identifier") else ("other",)
            m.cjs_whole = whole if m.cjs_whole is None else ("other",)
            return
        if decl is not None:
            t = decl.type
            if t in _DECLARATIONS:
                self.top_declaration(decl)
            elif t in ("function_declaration", "generator_function_declaration", "function_signature"):
                self.top_function(decl, (line, _line(decl)))
            elif t in ("enum_declaration", "internal_module"):
                self.add_global(self.decl_name(decl), self.unknown(_line(decl)))
            elif t == "type_alias_declaration":
                self.type_alias(decl)
            elif t == "ambient_declaration":
                for c in decl.named_children:
                    if c.type in _DECLARATIONS:
                        self.top_declaration(c)
            names = self.declared_names(decl) if decl.type != "ambient_declaration" else [
                nm for c in decl.named_children for nm in self.declared_names(c)]
            if default:
                m.default = ("local", names[0]) if names else ("expr",)
            else:
                for nm in names:
                    m.esm[nm] = ("local", nm)
            return
        if default:
            v = _unwrap(value)
            m.default = ("local", self.text(v)) if (v is not None and v.type == "identifier") else ("expr",)
            return
        star = any(c.type == "*" for c in st.children)
        for c in st.named_children:
            if c.type == "export_clause":
                for sp in c.named_children:
                    if sp.type != "export_specifier":
                        continue
                    name, alias = _field(sp, "name"), _field(sp, "alias")
                    if name is None:
                        continue
                    local = _prop_name(name, self.data) or self.text(name)
                    key = (_prop_name(alias, self.data) or self.text(alias)) if alias is not None else local
                    desc = ("reexport", spec, local) if spec is not None else ("local", local)
                    if key == "default":
                        m.default = desc
                    else:
                        m.esm[key] = desc
            elif c.type == "namespace_export" and spec is not None:
                star = False
                nm = next((x for x in c.named_children if x.type in ("identifier", "string")), None)
                if nm is not None:
                    m.esm[_prop_name(nm, self.data) or self.text(nm)] = ("reexport", spec, "*")
        if star and spec is not None:
            m.stars.append(spec)

    def top_function(self, fn, lines):
        nm = _field(fn, "name")
        if nm is None:
            return
        name = self.text(nm)
        ret = self.ann(fn, "return_type", _type_param_names(fn, self.data))
        self.functions.setdefault(name, []).append(self.T.FunctionFacts(
            name, _line(nm), ret, tuple(sorted({x for x in lines if x != _line(nm)})),
            self.arity(fn)))

    def top_declaration(self, decl):
        """A module-level const / let / var: an import (`require`), a function (an arrow / function expression)
        or a global value."""
        self.top_done.add(decl.start_byte)
        T = self.T
        for d in decl.named_children:
            if d.type != "variable_declarator":
                continue
            name_n, value = _field(d, "name"), _field(d, "value")
            line = _line(d)
            if name_n is None:
                continue
            spec = _require_spec(value, self.data) if value is not None else None
            if name_n.type == "identifier":
                name = self.text(name_n)
                v = _unwrap(value)
                if v is not None and v.type == "object":     # a possible mixin source (proto_writes)
                    self.obj_literals[name] = v if name not in self.obj_literals else None
                if spec is not None:
                    self.add_import(name, "module", spec, "=", line)
                elif v is not None and v.type == "member_expression" and \
                        _require_spec(_field(v, "object"), self.data) is not None and \
                        _prop_name(_field(v, "property"), self.data):
                    prop = _prop_name(_field(v, "property"), self.data)
                    self.add_import(name, "default" if prop == "default" else "named",
                                    _require_spec(_field(v, "object"), self.data), prop, line)
                elif v is not None and v.type in ("arrow_function", "function_expression", "generator_function",
                                                  "function"):
                    tp = _type_param_names(v, self.data)
                    ret = self.ann(v, "return_type", tp)
                    lines = {_line(v), _line(decl)}
                    self.functions.setdefault(name, []).append(T.FunctionFacts(
                        name, line, ret, tuple(sorted(x for x in lines if x != line)),
                        self.arity(v)))
                elif v is not None and v.type == "class":
                    self.add_global(name, self.unknown(line))
                else:
                    self.add_global(name, self.value_binding(value, self.ann(d, "type", frozenset()), -1, _Ctx(),
                                                             line))
            elif name_n.type == "object_pattern" and spec is not None:
                for e in name_n.named_children:
                    if e.type == "shorthand_property_identifier_pattern":
                        nm = self.text(e)
                        self.add_import(nm, "default" if nm == "default" else "named", spec, nm, line)
                    elif e.type == "pair_pattern" and _field(e, "value") is not None \
                            and _field(e, "value").type == "identifier" \
                            and _prop_name(_field(e, "key"), self.data):
                        key = _prop_name(_field(e, "key"), self.data)
                        self.add_import(self.text(_field(e, "value")), "default" if key == "default" else "named",
                                        spec, key, line)
                    else:
                        for nm in self.pattern_names(e):
                            self.add_global(nm, self.unknown(line))
            else:
                for nm in self.pattern_names(name_n):
                    self.add_global(nm, self.unknown(line))

    # -------------------------------------------------------------- CommonJS exports
    def cjs_entry(self, v):
        """The export descriptor of a CommonJS value (`x`, `require('./x')`, anything else)."""
        v = _unwrap(v)
        if v is None:
            return ("expr",)
        if v.type == "identifier":
            return ("local", self.text(v))
        spec = _require_spec(v, self.data)
        return ("require", spec) if spec is not None else ("expr",)

    def cjs_statement(self, x):
        """A top-level expression statement that writes module.exports / exports (or not)."""
        m = self.mod
        if x.type == "assignment_expression":
            left, right = _field(x, "left"), _field(x, "right")
            left_u = _unwrap(left)
            if _is_exports_ref(left_u, self.data):
                self.cjs_done.add(x.start_byte)
                if left_u.type == "identifier":            # `exports = ...` rebinds the local name only
                    m.dynamic = True
                    return
                r = _unwrap(right)
                while r is not None and r.type == "assignment_expression" and \
                        _is_exports_ref(_field(r, "left"), self.data):
                    self.cjs_done.add(r.start_byte)       # module.exports = exports = { ... }
                    r = _unwrap(_field(r, "right"))
                m.cjs_whole = self.cjs_whole_of(r) if m.cjs_whole is None else ("other",)
                return
            if left_u is not None and left_u.type in ("member_expression", "subscript_expression") and \
                    _is_exports_ref(_field(left_u, "object"), self.data):
                self.cjs_done.add(x.start_byte)
                key = _prop_name(_field(left_u, "property"), self.data) \
                    if left_u.type == "member_expression" else None
                if key is None:
                    m.dynamic = True
                    return
                m.cjs_props[key] = self.cjs_entry(right) if key not in m.cjs_props else ("expr",)
                return
        if x.type == "call_expression" and self.object_export_call(x):
            self.cjs_done.add(x.start_byte)
            m.dynamic = True

    def cjs_whole_of(self, r):
        """What `module.exports = <r>` makes the module object."""
        m = self.mod
        if r is None:
            return ("other",)
        if r.type == "identifier":
            return ("ident", self.text(r))
        if r.type == "object":
            entries: dict = {}
            for e in r.named_children:
                if e.type == "shorthand_property_identifier":
                    k = self.text(e)
                    entries[k] = ("local", k) if k not in entries else ("expr",)
                elif e.type in ("pair", "method_definition"):
                    k = _prop_name(_field(e, "key" if e.type == "pair" else "name"), self.data)
                    if k is None:
                        m.dynamic = True
                        continue
                    desc = self.cjs_entry(_field(e, "value")) if e.type == "pair" else ("expr",)
                    entries[k] = desc if k not in entries else ("expr",)
                elif e.type != "comment":
                    m.dynamic = True                        # a spread or anything not read
            return ("object", entries)
        spec = _require_spec(r, self.data)
        return ("require", spec) if spec is not None else ("other",)

    def object_export_call(self, call) -> bool:
        """`Object.assign(module.exports | exports, ...)` / `Object.defineProperty(ies)(exports, ...)`."""
        fn = _unwrap(_field(call, "function"))
        if fn is None or fn.type != "member_expression":
            return False
        o, p = _field(fn, "object"), _field(fn, "property")
        if o is None or o.type != "identifier" or self.text(o) != "Object" or p is None:
            return False
        if self.text(p) not in ("assign", "defineProperty", "defineProperties"):
            return False
        args = _field(call, "arguments")
        kids = [c for c in (args.named_children if args is not None else ()) if c.type != "comment"]
        return bool(kids) and _is_exports_ref(kids[0], self.data)

    # ======================================================================== pass 2: the whole tree
    def walk(self):
        stack = [(c, _Ctx(), True) for c in reversed(self.root.named_children)]   # (tokens matter to no handler)
        while stack:
            n, ctx, top = stack.pop()
            t = n.type
            if t in _SKIP_TYPES:
                if t in ("type_alias_declaration", "enum_declaration"):
                    self.poison_name(ctx, self.decl_name(n), top)    # a local `type X` / `enum X` shadows a class X
                self.signature_decls(n)
                continue
            handler = _HANDLERS.get(t)
            if handler is None:
                stack.extend((c, ctx, False) for c in reversed(n.named_children))
                continue
            kids = handler(self, n, ctx, top)
            if kids:
                stack.extend(reversed(kids))

    @staticmethod
    def _same(a, b) -> bool:
        return a is not None and b is not None and a.start_byte == b.start_byte and a.end_byte == b.end_byte \
            and a.type == b.type

    def signature_decls(self, n):
        """Method signatures in a type (`{ m(x): void }`, an interface body) are declarations, never calls."""
        stack = [n]
        while stack:
            x = stack.pop()
            if x.type in ("method_signature", "abstract_method_signature", "function_signature"):
                nm = _field(x, "name")
                if nm is not None:
                    self.decls.add((_line(nm), _col(nm)))
            stack.extend(x.named_children)

    def kids(self, n, ctx, top=False) -> list:
        return [(c, ctx, top) for c in n.named_children]

    def poison_name(self, ctx, name, top=False):
        """`name` is bound in a way the reader does not type, in ctx's scope (at module level: an untyped global,
        unless pass 1 bound that top-level statement)."""
        if not name:
            return
        if ctx.si >= 0:
            self.poison[ctx.si].add(name)
        elif not top:
            self.add_global(name, self.unknown())

    # -------------------------------------------------------------- statements
    def on_export(self, n, ctx, top):
        out = []
        for c in n.named_children:
            out.append((c, ctx.but(record=False), False) if c.type == "decorator" else (c, ctx, top))
        return out

    def on_decorator(self, n, ctx, top):
        return self.kids(n, ctx.but(record=False))

    def on_declaration(self, n, ctx, top):
        if ctx.si >= 0:
            for d in n.named_children:
                if d.type != "variable_declarator":
                    continue
                name_n, value = _field(d, "name"), _field(d, "value")
                if name_n is not None and name_n.type == "identifier":
                    sc = self.scopes[ctx.si]
                    sc.locals.setdefault(self.text(name_n), []).append(
                        self.value_binding(value, self.ann(d, "type", ctx.tparams), ctx.si, ctx, _line(d)))
                    self.note_this_alias(ctx, self.text(name_n), value)
                elif name_n is not None:
                    for nm in self.pattern_names(name_n):
                        self.poison[ctx.si].add(nm)
        elif n.start_byte not in self.top_done:
            for nm in self.declared_names(n):
                self.add_global(nm, self.unknown(_line(n)))
        return self.kids(n, ctx)

    def note_this_alias(self, ctx, name, value):
        """`const self = this` / `self = this` where `this` may be an instance of a class (its method, or code
        lexically inside it): a write through `self` is a write to that class's fields (finish)."""
        v = _unwrap(value)
        c = ctx.cls if ctx.cls >= 0 else ctx.lex
        if v is not None and v.type == "this" and c >= 0 and ctx.si >= 0 and name:
            self.this_alias[(ctx.si, name)] = c

    def on_import_alias(self, n, ctx, top):
        """`import X = N.Y` (a TS namespace alias): X is bound in a way the reader does not type — in a function or
        namespace it shadows an outer class X; at the top level pass 1 bound it (an untyped global)."""
        nm = next((c for c in n.named_children if c.type == "identifier"), None)
        if nm is not None:
            name = self.text(nm)
            if ctx.si >= 0:
                self.poison[ctx.si].add(name)
            elif name not in self.globals:
                self.add_global(name, self.unknown(_line(n)))
        return None

    def on_function_decl(self, n, ctx, top):
        nm = _field(n, "name")
        name = self.text(nm) if nm is not None else ""
        if nm is not None:
            self.decls.add((_line(nm), _col(nm)))
        if n.type == "function_signature":
            return None
        if not (top and ctx.si < 0):
            self.poison_name(ctx, name)
        lines = [_line(n)]
        p = n.parent
        if p is not None and p.type == "export_statement":
            lines.append(_line(p))
        tp = ctx.tparams | _type_param_names(n, self.data)
        kind = "generator_decl" if n.type == "generator_function_declaration" else "function_decl"
        return self.enter_function(n, ctx, name, _line(nm) if nm is not None else _line(n), lines, -1, "",
                                   kind, tp)

    def on_function_expr(self, n, ctx, top):
        """An arrow / function expression: named after what it is assigned to (a declarator, an object key, an
        assignment target), else anonymous (no node: its sites belong to the enclosing caller's full span)."""
        name, line, lines = "", _line(n), [_line(n)]
        p = n.parent
        if p is not None and p.type == "variable_declarator" and self._same(_field(p, "value"), n):
            nm = _field(p, "name")
            if nm is not None and nm.type == "identifier":
                name, line = self.text(nm), _line(p)
                gp = p.parent
                if gp is not None:
                    lines.append(_line(gp))
                    if gp.parent is not None and gp.parent.type == "export_statement":
                        lines.append(_line(gp.parent))
        elif p is not None and p.type == "pair" and self._same(_field(p, "value"), n):
            key = _field(p, "key")
            name = _prop_name(key, self.data) or ""
            line = _line(key) if key is not None else line
        elif p is not None and p.type == "assignment_expression" and self._same(_field(p, "right"), n):
            left = _unwrap(_field(p, "left"))
            if left is not None and left.type == "member_expression":
                pr = _field(left, "property")
                name = _prop_name(pr, self.data) or ""
                line = _line(pr) if pr is not None else line
            elif left is not None and left.type == "identifier":
                name, line = self.text(left), _line(left)
        own = _field(n, "name")
        if own is not None:
            self.decls.add((_line(own), _col(own)))
            if not name:
                name, line = self.text(own), _line(own)
        tp = ctx.tparams | _type_param_names(n, self.data)
        if n.type == "arrow_function":                   # `this` is the enclosing method's
            return self.enter_function(n, ctx, name, line, lines, ctx.cls, ctx.self_kind, "arrow", tp)
        return self.enter_function(n, ctx, name, line, lines, -1, "", "function_expr", tp)

    def on_method(self, n, ctx, top):
        """An object-literal method (class members go through on_class)."""
        return self.method(n, -1, ctx, frozenset(), [])

    def on_call(self, n, ctx, top):
        fn = _field(n, "function")
        kind, name, recv, node = self.callee(fn, ctx)
        indirect = self.indirect_name(fn, kind, name)
        if indirect:
            self.calls.append((n.start_byte, indirect))   # census only: no site, so that name is never REPLACEd
        if name:
            self.calls.append((n.start_byte, name))
            if ctx.record and ctx.si >= 0:
                self.scopes[ctx.si].sites.append(self.T.Site(_line(node), _col(node), name,
                                                             recv if kind == "member" else None, kind,
                                                             _args_count(n, self.data)))
        if kind == "member" and name in ("assign", "defineProperty", "defineProperties"):
            self.object_call(n, ctx, name)
        if self.has_proto:
            self.proto_call(n, name)
        return self.kids(n, ctx)

    def indirect_name(self, fn, kind, name):
        """The method a call reaches without naming it as a call: `x["m"](...)` (a string subscript) and
        `x.m.call / apply / bind(...)`. Not a typed site, but counted in the census (an engine may have recorded
        `m` for it, so no tuple of `m` is ever replaced in that caller)."""
        f = _unwrap(fn)
        if f is None:
            return None
        if kind is None and f.type == "subscript_expression":
            return _string_value(_unwrap(_field(f, "index")), self.data) or None
        if kind == "member" and name in ("call", "apply", "bind"):
            inner = _unwrap(_field(f, "object"))
            if inner is not None and inner.type == "member_expression":
                return _prop_name(_field(inner, "property"), self.data)
        return None

    def object_call(self, n, ctx, name):
        """Object.assign / defineProperty / defineProperties onto `this` (its class is dynamic), the exports (they are
        unknown), a global object (a patch of each key, '*' for one not read) or a name (a mixin source that grows,
        or a `this` alias: finish). Onto `X.prototype`: proto_call."""
        fn = _unwrap(_field(n, "function"))
        o = _field(fn, "object")
        if o is None or o.type != "identifier" or self.text(o) != "Object":
            return
        args = _field(n, "arguments")
        kids = [c for c in (args.named_children if args is not None else ()) if c.type != "comment"]
        if not kids:
            return
        a0 = _unwrap(kids[0])
        if a0.type == "this" and (ctx.cls >= 0 or ctx.lex >= 0):
            self.classes[ctx.cls if ctx.cls >= 0 else ctx.lex].dynamic = True
        elif _is_exports_ref(a0, self.data):
            if n.start_byte not in self.cjs_done:
                self.mod.dynamic = True
        elif a0.type == "identifier":
            nm = self.text(a0)
            self.assigned_objects.append((ctx.si, nm))
            if nm in _GLOBAL_OBJECTS:
                g = self.T.t_name(nm)
                if name == "defineProperty":
                    key = _string_value(_unwrap(kids[1]), self.data) if len(kids) > 1 else None
                    self.patches.append((g, key or "*"))
                    return
                for src in kids[1:]:
                    s = _unwrap(src)
                    keys, complete = self.literal_keys(s) if (s is not None and s.type == "object") else (set(), False)
                    self.patches.extend((g, k) for k in sorted(keys))
                    if not complete:
                        self.patches.append((g, "*"))

    def proto_of(self, n):
        """X's name parts when `n` is `X.prototype` (X a name or a dotted name), else None."""
        n = _unwrap(n)
        if n is None or n.type != "member_expression" or _prop_name(_field(n, "property"), self.data) != "prototype":
            return None
        parts, _last = self.dotted(_field(n, "object"))
        return parts or None

    def proto_call(self, n, name):
        """A call whose FIRST argument is `X.prototype` may add members to X's instances (Object.assign,
        defineProperties, setPrototypeOf, _.extend, a mixin helper) unless it only reads it (_PROTO_READONLY):
        a proto_write of its other arguments (finish). defineProperty(X.prototype, 'm') adds exactly m."""
        args = _field(n, "arguments")
        if args is None or args.type != "arguments":
            return
        kids = [c for c in args.named_children if c.type != "comment"]
        parts = self.proto_of(kids[0]) if kids else None
        if not parts or name in _PROTO_READONLY:
            return
        if name == "defineProperty":
            key = _string_value(_unwrap(kids[1]), self.data) if len(kids) > 1 else None
            if key:
                self.patches.append((self.type_ref(parts), key))
                return
            kids = kids[:1]                                  # an unknown key: any member ('*')
        self.proto_writes.append((parts, kids[1:], False))

    def literal_keys(self, obj) -> tuple:
        """(keys, complete) of an object literal: every pair / shorthand / method key (a string-literal computed one
        too); a spread, any other computed key or anything not read makes it incomplete."""
        keys, complete = set(), True
        for e in obj.named_children:
            t = e.type
            if t == "comment":
                continue
            if t == "shorthand_property_identifier":
                keys.add(self.text(e))
            elif t in ("pair", "method_definition"):
                k = _field(e, "key" if t == "pair" else "name")
                kn = self.member_name(k)
                if kn:
                    keys.add(kn)
                else:
                    complete = False
            else:
                complete = False
        return keys, complete

    def member_name(self, nm):
        """The name of a member key: an identifier, a plain string, or a computed string literal (`['save']`);
        None for any other computed key (`[Symbol.iterator]`, `[k]`)."""
        name = _prop_name(nm, self.data)
        if name is None and nm is not None and nm.type == "computed_property_name":
            kids = [c for c in nm.named_children if c.type != "comment"]
            name = (_string_value(kids[0], self.data) or None) if len(kids) == 1 else None
        return name

    def object_keys(self, src) -> tuple:
        """(keys, complete) of a mixin source (finish time): an object literal; a string literal (the one member a
        spy / stub helper replaces); a name bound once at module level to an object literal, with every
        `name.k = ...` write of the file (incomplete when the name is reassigned or Object.assign grows it);
        anything else is not read (incomplete)."""
        s = _unwrap(src)
        if s is not None and s.type == "object":
            return self.literal_keys(s)
        if s is not None and s.type == "string":         # jest.spyOn / sinon.stub(X.prototype, 'm'): m alone
            v = _string_value(s, self.data)
            return ({v}, True) if v else (set(), False)
        if s is None or s.type != "identifier":
            return set(), False
        nm = self.text(s)
        lit = self.obj_literals.get(nm)
        if lit is None:
            return set(), False
        keys, complete = self.literal_keys(lit)
        keys |= {a for _si, o, a in self.member_writes if o == nm}
        if any(o == nm for _si, o in self.assigned_objects) or any(a == nm for _si, a in self.assigns):
            complete = False
        return keys, complete

    def on_new(self, n, ctx, top):
        name, node = None, None
        parts, last = self.dotted(_field(n, "constructor"))
        if parts:
            name, node = parts[-1], last
        if name:
            self.calls.append((n.start_byte, name))
            if ctx.record and ctx.si >= 0:
                tr = self.type_of_parts(parts)
                self.scopes[ctx.si].sites.append(self.T.Site(
                    _line(node), _col(node), name, self.T.r_classref(tr) if tr is not None else None, "ctor",
                    _args_count(n, self.data)))
        return self.kids(n, ctx)

    def this_field(self, target, ctx):
        """The field name when the write target `target` is `this.<f>` (through parentheses, `!` and type
        assertions: `(this as any).f`) and `this` is the class here, else None."""
        t = _unwrap_write(target)
        if t is None or t.type != "member_expression" or ctx.cls < 0 or ctx.field_init:
            return None
        o = _unwrap_write(_field(t, "object"))
        if o is None or o.type != "this":
            return None
        return _prop_name(_field(t, "property"), self.data)

    def write_target(self, target, ctx, binding=None):
        """A write to `target`: a plain name (recorded; poisoned where it is declared after the walk), `this.f`
        (a field binding: `binding`, else untyped; a `this` lexically in a class but not its own — a callback, an
        initializer — an untyped binding of that class's f), a patch (`C.prototype.m` anywhere, `window.m` /
        `globalThis.m` anywhere, module-level `C.m`), an export; every other `<expr>.f = ...` is a member write
        (finish: a `this` alias writes its class's field; in JavaScript any of them may). A target is read through
        parentheses, `!` and type assertions: `(x as any) = v` writes x, `(this as any).f = v` binds f to v (a
        value the class's declared type of f does not describe — the field's bindings then disagree)."""
        t = _unwrap_write(target)
        if t is None:
            return
        if t.type == "identifier":
            self.assigns.append((ctx.si, self.text(t)))
            return
        if t.type in ("object_pattern", "array_pattern"):
            stack = [t]
            while stack:
                x = stack.pop()
                if x.type in ("identifier", "shorthand_property_identifier_pattern"):
                    self.assigns.append((ctx.si, self.text(x)))
                elif x.type == "member_expression":
                    self.write_target(x, ctx)
                else:
                    stack.extend(x.named_children)
            return
        if t.type == "subscript_expression":
            o = _unwrap_write(_field(t, "object"))
            if o is not None and o.type == "this" and (ctx.cls >= 0 or ctx.lex >= 0):
                self.classes[ctx.cls if ctx.cls >= 0 else ctx.lex].open = True
            elif _is_exports_ref(o, self.data):
                self.mod.dynamic = True
            elif o is not None and o.type == "identifier" and self.text(o) in _GLOBAL_OBJECTS:
                self.patches.append((self.T.t_name(self.text(o)), "*"))         # window[k] = ...
            return
        if t.type != "member_expression":
            return
        f = self.this_field(t, ctx)
        if f is not None:
            b = binding if (binding is not None and ctx.self_kind == "inst") else self.unknown(_line(t), ctx.si)
            self.classes[ctx.cls].fields.setdefault(f, []).append(b)
            return
        o = _unwrap_write(_field(t, "object"))
        prop = _prop_name(_field(t, "property"), self.data)
        if _is_exports_ref(o, self.data) or _is_exports_ref(t, self.data):
            return                                       # pass 1 judged the top-level ones (on_assign)
        if not prop:
            return
        if o is not None and o.type == "this":
            if ctx.lex >= 0:                             # `function () { this.f = ... }.bind(this)` in a class body
                cf = self.classes[ctx.lex]
                cf.fields.setdefault(prop, []).append(self.unknown(_line(t), cf.scope))
            self.member_writes.append((ctx.si, "", prop))
            return
        parts = self.proto_of(o)
        if parts:
            self.patches.append((self.type_ref(parts), prop))
        elif o is not None and o.type == "identifier":
            on = self.text(o)
            if on in _GLOBAL_OBJECTS or (ctx.si < 0 and on not in ("module", "exports")):
                self.patches.append((self.T.t_name(on), prop))
        self.member_writes.append((ctx.si, self.text(o) if (o is not None and o.type == "identifier") else "", prop))

    def on_assign(self, n, ctx, top):
        left, right = _field(n, "left"), _field(n, "right")
        lu = _unwrap_write(left)
        if lu is not None and (_is_exports_ref(lu, self.data) or (
                lu.type in ("member_expression", "subscript_expression")
                and _is_exports_ref(_field(lu, "object"), self.data))):
            if n.start_byte not in self.cjs_done:
                self.mod.dynamic = True                  # an export written inside a function / block
        elif n.type == "assignment_expression" and self.this_field(left, ctx) is not None:
            self.write_target(left, ctx, self.value_binding(right, None, ctx.si, ctx, _line(n)))
        else:
            self.write_target(left, ctx)
        if n.type == "assignment_expression" and lu is not None:
            if lu.type == "identifier":
                self.note_this_alias(ctx, self.text(lu), right)          # `self = this`
            parts = self.proto_of(lu) if self.has_proto else None
            if parts:
                self.proto_writes.append((parts, [right], True))         # the whole prototype replaced
        return self.kids(n, ctx)

    def on_update(self, n, ctx, top):
        arg = _field(n, "argument")
        if arg is None:
            arg = next((c for c in n.named_children if c.type != "comment"), None)
        self.write_target(arg, ctx)
        return self.kids(n, ctx)

    def on_for_in(self, n, ctx, top):
        left = _field(n, "left")
        if any(c.type in ("const", "let", "var") for c in n.children) or _field(n, "kind") is not None:
            for nm in self.pattern_names(left):
                self.poison_name(ctx, nm)
        else:
            self.write_target(left, ctx)
        return self.kids(n, ctx)

    def on_catch(self, n, ctx, top):
        p = _field(n, "parameter")
        if p is not None:
            for nm in self.pattern_names(p):
                self.poison_name(ctx, nm)
        return self.kids(n, ctx)

    def on_return(self, n, ctx, top):
        if ctx.si in self.ctor_of and any(c.type not in ("comment",) for c in n.named_children):
            self.classes[self.ctor_of[ctx.si]].dynamic = True     # the constructor returns another object
        return self.kids(n, ctx)

    def on_namespace(self, n, ctx, top):
        """A TS namespace body: a scope with no name (its declarations shadow file-level names)."""
        nm = _field(n, "name")
        if nm is not None:                               # `namespace Foo` merges into a class Foo: it is open
            self.iface_names[(ctx.si, self.text(nm).split(".")[0].strip())] = True
        body = _field(n, "body")
        if body is None:
            return None
        si = self.new_scope(n, ctx, "", _line(n), (), -1, "", "namespace", ctx.tparams)
        return [(body, _Ctx(si=si, tparams=ctx.tparams, record=True), False)]

    def on_skip(self, n, ctx, top):
        return None

    def on_ambient(self, n, ctx, top):
        """`declare ...`: the declaration keeps its top-level standing (pass 1 bound the top-level ones).
        `declare global { ... }` augments the GLOBAL types, never this file's: each member it declares is a patch of
        its global object (an interface Window -> window / globalThis; String -> String; a var / function ->
        window / globalThis), and no file-level class is made."""
        if any(c.type == "global" for c in n.children):
            body = next((c for c in n.named_children if c.type == "statement_block"), None)
            if body is not None:
                self.global_augment(body)
                self.signature_decls(body)
            return None
        return self.kids(n, ctx, top)

    def global_augment(self, body):
        glob = (self.T.t_name("window"), self.T.t_name("globalThis"))
        for st in body.named_children:
            if st.type == "export_statement":
                st = _field(st, "declaration") or st
            t = st.type
            if t == "interface_declaration":
                iname = self.decl_name(st)
                targets = glob if iname in _GLOBAL_IFACES else ((self.T.t_name(iname),) if iname else ())
                ib = _field(st, "body")
                for m in (ib.named_children if ib is not None else ()):
                    mn = self.member_name(_field(m, "name"))
                    for tg in (targets if mn else ()):
                        self.patches.append((tg, mn))
            elif t in _DECLARATIONS or t in ("function_signature", "function_declaration"):
                for nm in self.declared_names(st):
                    self.patches.extend((g, nm) for g in glob)

    # -------------------------------------------------------------- classes
    def on_class(self, n, ctx, top):
        T = self.T
        nm = _field(n, "name")
        name = self.text(nm) if nm is not None else ""
        tp = _type_param_names(n, self.data)
        p = n.parent
        decos = [c for c in n.children if c.type == "decorator"]
        if p is not None and p.type == "export_statement":
            decos += [c for c in p.children if c.type == "decorator"]
        cidx = -1
        if n.type in _CLASS_DECLS and name:
            lines = {_line(n)} | {_line(d) for d in decos}
            if p is not None and p.type in ("export_statement", "ambient_declaration"):
                lines.add(_line(p))
            cf = T.ClassFacts(name=name, line=_line(nm), end_line=_end_line(n),
                              kind="abstract" if n.type == "abstract_class_declaration" else "class",
                              bases=self.class_bases(n, tp | ctx.tparams),
                              open=self.ambient_file or (p is not None and p.type == "ambient_declaration"),
                              lines=tuple(sorted(x for x in lines if x != _line(nm))), scope=ctx.si,
                              type_params=tp)
            cidx = len(self.classes)
            self.classes.append(cf)
            self.class_ranges.append((n.start_byte, n.end_byte))
            if ctx.si >= 0:
                self.poison[ctx.si].add(name)
            elif not top:
                self.add_global(name, self.unknown(_line(n)))
        out = [(d, ctx.but(record=False), False) for d in decos if self._same(d.parent, n)]
        her = next((c for c in n.children if c.type == "class_heritage"), None)
        if her is not None:
            out.append((her, ctx, False))
        body = _field(n, "body")
        if body is not None:
            out += self.class_body(body, cidx, ctx, tp)
        return out

    def class_bases(self, n, tparams) -> tuple:
        """The `extends` operand (TS: the extends clause's value; JS: the heritage expression) as a TypeRef."""
        T = self.T
        her = next((c for c in n.children if c.type == "class_heritage"), None)
        if her is None:
            return ()
        ext = next((c for c in her.named_children if c.type == "extends_clause"), None)
        if ext is not None:
            v = _field(ext, "value")
        elif self.ts and any(c.type == "implements_clause" for c in her.named_children):
            return ()
        else:
            v = next((c for c in her.named_children if c.type != "comment"), None)
        parts, _last = self.dotted(v)
        if not parts:
            return (T.T_UNKNOWN,)
        if parts[0] in tparams:
            return (T.T_UNKNOWN,)
        return (T.t_name(parts[0]) if len(parts) == 1 else T.t_qual(*parts),)

    def class_body(self, body, cidx, ctx, ctp) -> list:
        out = []
        pending: list = []                               # decorators of the next member
        sigs: dict = {}
        for m in body.children:
            t = m.type
            if t == "decorator":
                pending.append(m)
                out.append((m, ctx.but(record=False), False))
                continue
            decos, pending = pending, []
            if t == "method_definition":
                out += self.method(m, cidx, ctx, ctp, decos)
            elif t in _FIELD_DEFS:
                out += self.field(m, cidx, ctx, ctp, decos)
            elif t in _SIGNATURES:
                nm = _field(m, "name")
                name = self.member_name(nm)
                if nm is not None:
                    self.decls.add((_line(nm), _col(nm)))
                if cidx >= 0 and not name:
                    self.classes[cidx].open = True       # `abstract [Symbol.iterator](): T`: a member not named
                if cidx >= 0 and name:
                    tp = ctp | ctx.tparams | _type_param_names(m, self.data)
                    sigs.setdefault(name, []).append(self.T.MethodFacts(
                        _line(nm), frozenset({"abstract"} | ({"static"} if any(c.type == "static" for c in
                                                                              m.children) else set())),
                        self.ann(m, "return_type", tp), self.arity(m),
                        tuple(sorted({_line(d) for d in decos}))))
            elif t == "index_signature":
                if cidx >= 0:
                    self.classes[cidx].open = True
            elif t == "class_static_block":
                out.append((m, ctx.but(cls=cidx, self_kind="class", field_init=True, record=ctx.si >= 0,
                                       lex=cidx if cidx >= 0 else ctx.lex), False))
        if cidx >= 0:
            meths = self.classes[cidx].methods
            for name, entries in sigs.items():
                if name not in meths:                     # overload signatures beside a body are not methods
                    meths[name] = entries
        return out

    def method(self, m, cidx, ctx, ctp, decos) -> list:
        T = self.T
        nm = _field(m, "name")
        name = self.member_name(nm)
        if nm is not None and not name and cidx >= 0:
            self.classes[cidx].open = True               # `[Symbol.iterator]() {}`: a member not named
        static = any(c.type == "static" for c in m.children)
        accessor = any(c.type in ("get", "set") for c in m.children)
        tp = ctx.tparams | ctp | _type_param_names(m, self.data)
        line = _line(nm) if nm is not None else _line(m)
        dlines = {_line(d) for d in decos}
        if nm is not None and name:
            self.decls.add((_line(nm), _col(nm)))
        if cidx >= 0 and name:
            flags = ({"static"} if static else set()) | ({"property"} if accessor else set())
            self.classes[cidx].methods.setdefault(name, []).append(T.MethodFacts(
                line, frozenset(flags), self.ann(m, "return_type", tp), self.arity(m),
                tuple(sorted(dlines | ({_line(m)} if _line(m) != line else set())))))
        self_kind = ("class" if static else "inst") if cidx >= 0 else ""
        out = self.enter_function(m, ctx, name or "", line, [_line(m)] + sorted(dlines), cidx, self_kind,
                                  "method_def", tp,
                                  ctor=cidx if (name == "constructor" and cidx >= 0 and not static) else -1)
        return out

    def field(self, f, cidx, ctx, ctp, decos) -> list:
        T = self.T
        nm = _field(f, "name") or _field(f, "property")
        name = self.member_name(nm)
        static = any(c.type == "static" for c in f.children)
        value = _field(f, "value")
        v = _unwrap(value)
        out: list = []                                   # (class_body walks the decorators)
        if name is None:
            if cidx >= 0:
                self.classes[cidx].open = True           # a computed member
            if value is not None:
                out.append((value, ctx.but(field_init=True, record=ctx.si >= 0, lex=cidx if cidx >= 0 else ctx.lex),
                            False))
            return out
        dlines = sorted({_line(d) for d in decos})
        self_kind = ("class" if static else "inst") if cidx >= 0 else ""
        if v is not None and v.type in ("arrow_function", "function_expression", "generator_function", "function"):
            tp = ctx.tparams | ctp | _type_param_names(v, self.data)
            if cidx >= 0:
                self.classes[cidx].methods.setdefault(name, []).append(T.MethodFacts(
                    _line(nm), frozenset({"static"} if static else ()), self.ann(v, "return_type", tp),
                    self.arity(v),
                    tuple(sorted(set(dlines) | {_line(f), _line(v)} - {_line(nm)}))))
            cls_this = cidx if v.type == "arrow_function" else -1
            out += self.enter_function(v, ctx.but(lex=cidx) if cidx >= 0 else ctx, name, _line(nm),
                                       [_line(v), _line(f)] + dlines, cls_this,
                                       self_kind if cls_this >= 0 else "",
                                       "arrow" if v.type == "arrow_function" else "function_expr", tp)
            return out
        fctx = ctx.but(cls=cidx, self_kind=self_kind, field_init=True, record=ctx.si >= 0,
                       tparams=ctx.tparams | ctp, lex=cidx if cidx >= 0 else ctx.lex)
        if cidx >= 0:
            cf = self.classes[cidx]
            if static:
                b = self.unknown(_line(f), cf.scope)
            else:
                b = self.value_binding(value, self.ann(f, "type", ctx.tparams | ctp), cf.scope, fctx, _line(f))
            cf.fields.setdefault(name, []).append(b)
        if value is not None:
            out.append((value, fctx, False))
        return out

    def on_interface(self, n, ctx, top):
        T = self.T
        nm = _field(n, "name")
        if nm is None:
            return None
        name = self.text(nm)
        tp = _type_param_names(n, self.data)
        allp = tp | ctx.tparams
        bases = []
        ext = next((c for c in n.children if c.type == "extends_type_clause"), None)
        for c in (ext.named_children if ext is not None else ()):
            if c.type != "comment":
                r = self.typeref(c, allp)
                bases.append(r.args[0] if r.kind == "generic" and r.args else r)
        cf = T.ClassFacts(name=name, line=_line(nm), end_line=_end_line(n), kind="interface", bases=tuple(bases),
                          open=True, lines=tuple(sorted({_line(n)} - {_line(nm)})), scope=ctx.si, type_params=tp)
        body = _field(n, "body")
        for m in (body.named_children if body is not None else ()):
            mn = _field(m, "name")
            mname = _prop_name(mn, self.data)
            if not mname:
                continue
            if m.type == "method_signature":
                self.decls.add((_line(mn), _col(mn)))
                mtp = allp | _type_param_names(m, self.data)
                cf.methods.setdefault(mname, []).append(T.MethodFacts(
                    _line(mn), frozenset({"abstract"}), self.ann(m, "return_type", mtp),
                    self.arity(m)))
            elif m.type == "property_signature":
                a = self.ann(m, "type", allp)
                cf.fields.setdefault(mname, []).append(
                    T.Binding("ann", type=a, line=_line(m), scope=ctx.si) if a is not None else self.unknown(
                        _line(m), ctx.si))
        self.classes.append(cf)
        self.class_ranges.append((n.start_byte, n.end_byte))
        self.iface_names[(ctx.si, name)] = True
        if ctx.si >= 0:
            self.poison[ctx.si].add(name)
        return None

    # -------------------------------------------------------------- scopes
    def new_scope(self, fn, ctx, name, line, lines, cls, self_kind, kind, tparams) -> int:
        T = self.T
        si = len(self.scopes)
        self.scopes.append(T.Scope(name=name or "", line=line, end_line=_end_line(fn),
                                   lines=tuple(sorted({x for x in lines if isinstance(x, int) and x > 0} - {line})),
                                   parent=ctx.si, cls=cls, self_kind=self_kind, type_params=frozenset(tparams),
                                   kind=kind))
        self.ranges.append((fn.start_byte, fn.end_byte))
        self.poison.append(set())
        return si

    def enter_function(self, fn, ctx, name, line, lines, cls, self_kind, kind, tparams, ctor=-1) -> list:
        """A new scope for a function-like node; returns what pass 2 walks next (its parameters' default values
        and decorators with sites off, then its body)."""
        si = self.new_scope(fn, ctx, name, line, lines, cls, self_kind, kind, tparams)
        if ctor >= 0:
            self.ctor_of[si] = ctor
        inner = _Ctx(si=si, cls=cls, self_kind=self_kind, tparams=frozenset(tparams), record=True,
                     lex=cls if cls >= 0 else ctx.lex)
        quiet = inner.but(record=False)
        out = self.params(_params_node(fn), si, quiet, ctor)
        body = _field(fn, "body")
        if body is not None:
            out.append((body, inner, False))
        return out

    def params(self, pn, si, quiet, ctor) -> list:
        T = self.T
        sc = self.scopes[si]
        out = []
        if pn is None:
            return out
        if pn.type == "identifier":
            sc.params[self.text(pn)] = T.Binding("param", None, line=_line(pn), scope=si)
            return out
        for c in pn.named_children:
            t = c.type
            if t in ("comment",):
                continue
            if t in ("required_parameter", "optional_parameter"):
                pat, value = _field(c, "pattern"), _field(c, "value")
                for d in c.children:
                    if d.type == "decorator":
                        out.append((d, quiet, False))
                if pat is not None and pat.type == "identifier":
                    name = self.text(pat)
                    typ = self.ann(c, "type", quiet.tparams)
                    sc.params[name] = T.Binding("param", typ, line=_line(c), scope=si)
                    if ctor >= 0 and any(x.type in ("accessibility_modifier", "readonly", "override")
                                         for x in c.children):
                        cf = self.classes[ctor]
                        cf.fields.setdefault(name, []).append(
                            T.Binding("ann", type=typ, line=_line(c), scope=cf.scope) if typ is not None
                            else self.unknown(_line(c), cf.scope))
                elif pat is not None and pat.type == "this":
                    continue
                elif pat is not None and pat.type == "rest_pattern":
                    for nm in self.pattern_names(pat):
                        sc.params[nm] = T.Binding("param", None, line=_line(c), scope=si)
                elif pat is not None:
                    for nm in self.pattern_names(pat):
                        self.poison[si].add(nm)
                    out.append((pat, quiet, False))
                if value is not None:
                    out.append((value, quiet, False))
            elif t == "identifier":
                sc.params[self.text(c)] = T.Binding("param", None, line=_line(c), scope=si)
            elif t == "assignment_pattern":
                left, right = _field(c, "left"), _field(c, "right")
                if left is not None and left.type == "identifier":
                    sc.params[self.text(left)] = T.Binding("param", None, line=_line(c), scope=si)
                else:
                    for nm in self.pattern_names(left):
                        self.poison[si].add(nm)
                if right is not None:
                    out.append((right, quiet, False))
            elif t == "rest_pattern":
                for nm in self.pattern_names(c):
                    sc.params[nm] = T.Binding("param", None, line=_line(c), scope=si)
            else:
                for nm in self.pattern_names(c):
                    self.poison[si].add(nm)
                out.append((c, quiet, False))
        return out

    # ======================================================================== assembly
    def all_error_ranges(self):
        """(start, end) of every ERROR / MISSING node of the whole tree (type annotations included), or None when
        there are too many to judge one by one (then every scope counts as holding one)."""
        if not self.root.has_error:
            return []
        out, stack = [], [self.root]
        while stack:
            x = stack.pop()
            if x.type == "ERROR" or x.is_missing:
                out.append((x.start_byte, max(x.end_byte, x.start_byte + 1)))
                if len(out) > 512:
                    return None
            if x.has_error:
                stack.extend(x.children)
        return out

    def top_level_errors(self, errs):
        """A parse error at the top level: in an import or a program-level ERROR, the file's names may be bound in
        ways not read (a star marker: no unbound name resolves, not even to a builtin); in an export or a CommonJS
        write outside a declaration, its exports are unknown (an error inside a declaration — a type parameter's
        `in` / `out` variance, a body — leaves the declared name and its export key intact)."""
        if errs is None:
            self.mod.dynamic = True
            self.imports.setdefault("*", []).append(self.T.Import("*", "star", "", "", 0))
            return
        if not errs:
            return
        decl_types = _CLASS_DECLS | {"interface_declaration", "type_alias_declaration", "function_declaration",
                                     "generator_function_declaration", "enum_declaration", "function_signature"}
        for st in self.root.named_children:
            if not any(es < st.end_byte and ee > st.start_byte for es, ee in errs):
                continue
            t = st.type
            if t in decl_types:
                continue
            if t == "export_statement":
                d = _field(st, "declaration")
                if d is not None and d.type in decl_types and all(
                        d.start_byte <= es and ee <= d.end_byte for es, ee in errs
                        if es < st.end_byte and ee > st.start_byte):
                    continue
                self.mod.dynamic = True
                continue
            if t in ("import_statement", "ERROR") or t in _DECLARATIONS:
                self.imports.setdefault("*", []).append(self.T.Import("*", "star", "", "", _line(st)))
            if t in ("ERROR", "expression_statement"):
                self.mod.dynamic = True

    def finish(self):
        T = self.T
        # every write to a plain name poisons it where it is declared (module level: an untyped global)
        for si, name in self.assigns:
            s = si
            while s >= 0:
                sc = self.scopes[s]
                if name in sc.params or name in sc.locals or name in self.poison[s]:
                    self.poison[s].add(name)
                    break
                s = sc.parent
            if s < 0 and (name in self.globals or name in self.functions or name in self.imports
                          or any(c.name == name and c.scope < 0 for c in self.classes)):
                self.add_global(name, self.unknown())
        # declaration merging (an interface beside a class of that name) opens the class (`// @ts-nocheck` /
        # `@ts-ignore` need nothing here: the core's LanguageSpec.unchecked rule never judges such a site absent)
        for cf in self.classes:
            if cf.kind != "interface" and self.iface_names.get((cf.scope, cf.name)):
                cf.open = True
        self.field_writes()
        self.mixins()
        # the census: every call over each scope's full byte range (+1 on every name next to a parse error)
        calls = sorted(self.calls)
        starts = [b for b, _n in calls]
        errs = self.all_error_ranges()

        def broken(s, e) -> bool:
            return errs is None or any(es < e and ee > s for es, ee in errs)

        # a parse error: tree-sitter's recovery may move code between functions, so a scope that holds one (or
        # sits inside one that does) keeps no site and +1 on its census; a class that holds one is dynamic
        tainted: list = []
        for si, sc in enumerate(self.scopes):
            s, e = self.ranges[si]
            counts: dict = {}
            for _b, name in calls[bisect.bisect_left(starts, s):bisect.bisect_left(starts, e)]:
                counts[name] = counts.get(name, 0) + 1
            bad = broken(s, e) or (sc.parent >= 0 and tainted[sc.parent])
            tainted.append(bad)
            if bad:
                counts = {n: c + 1 for n, c in counts.items()}
                sc.sites = []
            sc.all_names = counts
            sc.poisoned = frozenset(self.poison[si])
        if errs:
            for ci, cf in enumerate(self.classes):
                if broken(*self.class_ranges[ci]):
                    cf.dynamic = True
        self.top_level_errors(errs)
        top = set(self.functions) | set(self.globals) | set(self.imports)
        top |= {c.name for c in self.classes if c.scope < 0 and c.outer < 0}
        self.mod.top = frozenset(top)
        ff = T.FileFacts(rel=self.rel, lang=self.lang, imports=self.imports, classes=self.classes,
                         functions=self.functions, globals=self.globals, scopes=self.scopes,
                         decls=frozenset(self.decls), patches=self.patches, reader_version=READER_VERSION)
        ff._tsjs = self.mod
        return ff

    def field_writes(self):
        """`self.f = ...` through a `this` alias (`const self = this` in a class's code, the nearest binding of
        `self`) is an untyped binding of that class's field f; `Object.assign(self, ...)` makes it dynamic. In
        JavaScript, where no declared type holds a field, ANY in-file `x.f = ...` (no class's own `this.f`) is an
        untyped binding of field f of every class of the file that has one."""
        def alias_class(si, name):
            s = si
            while s >= 0:
                c = self.this_alias.get((s, name))
                if c is not None:
                    return c
                sc = self.scopes[s]
                if name in sc.params or name in sc.locals or name in self.poison[s]:
                    return None
                s = sc.parent
            return None

        for si, name, attr in self.member_writes:
            c = alias_class(si, name) if name else None
            if c is not None:
                cf = self.classes[c]
                cf.fields.setdefault(attr, []).append(self.unknown(0, cf.scope))
        for si, name in self.assigned_objects:
            c = alias_class(si, name)
            if c is not None:
                self.classes[c].dynamic = True
        if not self.ts:
            written = {attr for _si, _name, attr in self.member_writes}
            for cf in self.classes:
                for attr in sorted(written & set(cf.fields)):
                    cf.fields[attr].append(self.unknown(0, cf.scope))

    def mixins(self):
        """Every proto_write: a patch of each key its sources provably add, '*' (any member) when a source is not
        read or the whole prototype is replaced; a class of this file so written is dynamic."""
        for parts, sources, whole in self.proto_writes:
            tref = self.type_ref(parts)
            keys, star = set(), whole or not sources
            for src in sources:
                k, complete = self.object_keys(src)
                keys |= k
                star = star or not complete
            self.patches.extend((tref, k) for k in sorted(keys))
            if star:
                self.patches.append((tref, "*"))
            if len(parts) == 1:
                for cf in self.classes:
                    if cf.kind != "interface" and cf.scope < 0 and cf.name == parts[0]:
                        cf.dynamic = True

    def run(self):
        self.top_level()
        self.walk()
        return self.finish()


_HANDLERS = {
    "export_statement": _FileReader.on_export,
    "decorator": _FileReader.on_decorator,
    "lexical_declaration": _FileReader.on_declaration,
    "variable_declaration": _FileReader.on_declaration,
    "function_declaration": _FileReader.on_function_decl,
    "generator_function_declaration": _FileReader.on_function_decl,
    "function_signature": _FileReader.on_function_decl,
    "arrow_function": _FileReader.on_function_expr,
    "function_expression": _FileReader.on_function_expr,
    "generator_function": _FileReader.on_function_expr,
    "function": _FileReader.on_function_expr,
    "method_definition": _FileReader.on_method,
    "call_expression": _FileReader.on_call,
    "new_expression": _FileReader.on_new,
    "assignment_expression": _FileReader.on_assign,
    "augmented_assignment_expression": _FileReader.on_assign,
    "update_expression": _FileReader.on_update,
    "for_in_statement": _FileReader.on_for_in,
    "catch_clause": _FileReader.on_catch,
    "return_statement": _FileReader.on_return,
    "internal_module": _FileReader.on_namespace,
    "module": _FileReader.on_skip,
    "ambient_declaration": _FileReader.on_ambient,
    "global": _FileReader.on_skip,
    "class_declaration": _FileReader.on_class,
    "abstract_class_declaration": _FileReader.on_class,
    "class": _FileReader.on_class,
    "interface_declaration": _FileReader.on_interface,
    "import_alias": _FileReader.on_import_alias,
}


def read_facts(rel: str, lang: str, root, data: bytes):
    """FileFacts (with its ModInfo as `_tsjs`) of one parsed TS / JS file — pure in (rel, lang, the bytes, the
    grammar). Raises on a reader bug (the core then gives the file no facts)."""
    return _FileReader(rel, lang, data, root, _ir()).run()


# ================================================================================================
# The binder
# ================================================================================================

_TSJS = _TS_EXTS + _JS_EXTS
_MODS_BY_SOURCE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def package_stem(spec: str) -> str:
    """The top-level name of a package specifier (`@scope/pkg` for a scoped one, `lodash` for `lodash/fp`)."""
    s = str(spec or "").split("?", 1)[0]
    if s.startswith("@"):
        return "/".join(s.split("/")[:2])
    return s.split("/", 1)[0]


def _builtin_module(spec: str) -> bool:
    """`node:x`, or a bare Node.js core module (`fs`, `fs/promises`)."""
    s = str(spec or "")
    return s.startswith("node:") or (not s.startswith("@") and s.split("/", 1)[0] in _NODE_BUILTINS)


def spec_stems(spec) -> tuple:
    """The names the core's conservative external test (design 2.4: never external when one of them is the stem
    of a project file or directory) checks for a specifier the tree does not hold — with no package.json above the
    importing file to say what is a dependency (TsJsBinder.external_stems). () when it cannot name a package at
    all — a `$` / `~` / `#` / `_` alias (SvelteKit `$lib`, Nuxt `~`): unknown, never external. A known third-party
    scope or a Node.js core module keeps its package stem. Any other gives the package stem plus every path segment
    (its `@` stripped; the last one also without its extension): an alias no config maps (Vite / webpack / babel
    `@utils/format`, `@app/services/x`) names project directories in its segments."""
    s = str(spec or "").split("?", 1)[0]
    head = s[1:] if s.startswith("@") else s
    if not head or not (head[0].isascii() and head[0].isalnum()):
        return ()
    stem = package_stem(s)
    if _builtin_module(s) or _sibling("typescript_edges").is_package(s):
        return (stem,)
    out = [stem]
    segs = [p for p in s.split("/") if p]
    for i, p in enumerate(segs):
        p = p.lstrip("@")
        if not p:
            continue
        out.append(p)
        if i == len(segs) - 1 and "." in p.strip("."):
            out += [p.rsplit(".", 1)[0], p.split(".", 1)[0]]
    return tuple(dict.fromkeys(x for x in out if x))


def tree_files(root, graph_files) -> frozenset:
    """The TS / JS files module resolution sees: the recognizer walk (language_recognizers.walk_files with the
    TypeScript entry's ignore set) plus every TS / JS graph file — a superset of what the graph holds, so a
    specifier that names a file outside the graph resolves to it (and then binds nothing) instead of falling
    through to another candidate."""
    out = {f for f in graph_files if str(f).lower().endswith(_TSJS)}
    try:
        lr = _sibling("language_recognizers")
        for rel, _p in lr.walk_files(root, _TSJS, lr._TS_IGNORE):
            out.add(rel)
    except Exception:  # noqa: BLE001 — the graph files alone
        pass
    return frozenset(out)


def modules_of(root, graph_files, source=None):
    """typescript_edges.modules_for over tree_files (shared by the run's two binders through its source)."""
    key = (str(root), graph_files)
    if source is not None:
        try:
            got = _MODS_BY_SOURCE.get(source)
            if got is not None and got[0] == key:
                return got[1]
        except TypeError:
            source = None
    mods = _sibling("typescript_edges").modules_for(root, tree_files(root, graph_files))
    if source is not None:
        try:
            _MODS_BY_SOURCE[source] = (key, mods)
        except TypeError:
            pass
    return mods


class TsJsBinder:
    """Places TS / JS imports in the tree (see THE BINDER in the module docstring)."""

    def __init__(self, ctx, source=None):
        self.facts = ctx.facts
        self.graph_files = ctx.graph_files
        self.root = ctx.root
        self.source = source
        self._mods = None
        self._memo: dict = {}
        self._busy: set = set()
        self._pkg: dict = {}                 # directory -> (declared dependency names, a package.json above)
        self._patched = None                 # builtin globals the project patches (resolve_free)

    @property
    def mods(self):
        if self._mods is None:
            self._mods = modules_of(self.root, self.graph_files, self.source)
        return self._mods

    # -------------------------------------------------------------- specifiers
    def resolve_spec(self, rel, spec) -> tuple:
        """("file", rel2) (a graph file) | ("external", stems) | ("unknown",)."""
        key = ("spec", rel, spec)
        if key not in self._memo:
            try:
                got = self.mods.resolve(rel, spec)
            except Exception:  # noqa: BLE001
                got = ("unknown",)
            if got[0] == "file":
                out = ("file", got[1]) if got[1] in self.graph_files else ("unknown",)
            elif got[0] == "external":
                stems = self.external_stems(rel, spec)
                out = ("external", stems) if stems else ("unknown",)
            else:
                out = ("unknown",)
            self._memo[key] = out
        return self._memo[key]

    def external_stems(self, rel, spec) -> tuple:
        """The stems of a specifier _Modules places in no file of the tree (() = unknown). A Node.js core module or
        a known third-party scope: its package stem. Otherwise the package.json files above the importing file
        decide when there are any: a package one of them declares (dependencies, devDependencies,
        peerDependencies, optionalDependencies) is external by its package stem; one none declares is unknown (an
        alias no config maps — `@utils/format`, `@helpers` — or an undeclared package). With none above:
        spec_stems."""
        stems = spec_stems(spec)
        s = str(spec or "").split("?", 1)[0]
        if not stems or _builtin_module(s) or _sibling("typescript_edges").is_package(s):
            return stems
        deps, seen = self.declared(posixpath.dirname(str(rel)))
        if not seen:
            return stems
        return (package_stem(s),) if package_stem(s) in deps else ()

    def declared(self, d) -> tuple:
        """(the dependency names every package.json at or above directory `d` declares, whether there is one)."""
        if d in self._pkg:
            return self._pkg[d]
        deps, seen = set(), False
        try:
            p = Path(self.root) / d / "package.json" if d else Path(self.root) / "package.json"
            if p.is_file():
                seen = True
                data = json.loads(p.read_text(encoding="utf-8-sig"))
                for fld in (_DEP_FIELDS if isinstance(data, dict) else ()):
                    if isinstance(data.get(fld), dict):
                        deps.update(k for k in data[fld] if isinstance(k, str))
        except (OSError, ValueError):
            pass                                          # unreadable / not JSON: a package.json that declares nothing
        if d:
            up = self.declared(posixpath.dirname(d))
            deps |= up[0]
            seen = seen or up[1]
        self._pkg[d] = (frozenset(deps), seen)
        return self._pkg[d]

    def info(self, rel):
        ff = self.facts.get(rel)
        return mod_info(ff) if ff is not None else None

    # -------------------------------------------------------------- values: ("sym", rel, local) | ("mod", rel,
    # "esm" | "cjs") | ("ext", stems) | None
    def _guard(self, key, fn):
        if key in self._memo:
            return self._memo[key]
        if key in self._busy or len(self._busy) > 4 * _REEXPORT_DEPTH:
            return None
        self._busy.add(key)
        try:
            got = fn()
        finally:
            self._busy.discard(key)
        self._memo[key] = got
        return got

    def desc_value(self, rel, desc, depth):
        if desc is None or depth > _REEXPORT_DEPTH:
            return None
        k = desc[0]
        if k == "local":
            return ("sym", rel, desc[1])
        if k in ("reexport", "require"):
            r = self.resolve_spec(rel, desc[1])
            if r[0] == "external":
                return ("ext", r[1])
            if r[0] != "file":
                return None
            if k == "require":
                return self.module_object(r[1], "cjs", depth + 1)
            imp = desc[2]
            if imp == "*":
                return self.module_object(r[1], "esm", depth + 1)
            if imp == "default":
                return self.default_of(r[1], depth + 1)
            return self.export_of(r[1], imp, depth + 1)
        return None

    def export_of(self, rel, name, depth=0):
        """What module `rel` exports under `name`."""
        if name == "default":
            return self.default_of(rel, depth)
        return self._guard(("exp", rel, name), lambda: self._export_of(rel, name, depth))

    def _export_of(self, rel, name, depth):
        info = self.info(rel)
        if info is None or info.dynamic or depth > _REEXPORT_DEPTH:
            return None
        w = info.cjs_whole
        if w is not None:
            if w[0] == "object":
                return self.desc_value(rel, w[1].get(name) or info.cjs_props.get(name), depth)
            if w[0] == "require":
                r = self.resolve_spec(rel, w[1])
                if r[0] == "external":
                    return ("ext", r[1])
                return self.export_of(r[1], name, depth + 1) if r[0] == "file" else None
            return None                                   # members of `module.exports = X`: not a top-level name
        d = info.esm.get(name) or info.cjs_props.get(name)
        if d is not None:
            return self.desc_value(rel, d, depth)
        hits, ext, blind = [], [], False
        for s in info.stars:
            r = self.resolve_spec(rel, s)
            if r[0] == "file":
                v = self.export_of(r[1], name, depth + 1)
                if v is not None:
                    hits.append(v)
                elif self.info(r[1]) is None or self.info(r[1]).dynamic:
                    blind = True
            elif r[0] == "external":
                ext.append(r[1])
            else:
                blind = True
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1 and not ext and not blind:
            return hits[0]
        if not hits and len(ext) == 1 and not blind:
            return ("ext", ext[0])
        return None

    def default_of(self, rel, depth=0):
        return self._guard(("def", rel), lambda: self._default_of(rel, depth))

    def _default_of(self, rel, depth):
        info = self.info(rel)
        if info is None or info.dynamic or depth > _REEXPORT_DEPTH:
            return None
        if info.default is not None:
            return self.desc_value(rel, info.default, depth)
        w = info.cjs_whole
        if w is not None:
            if w[0] == "ident":
                return ("sym", rel, w[1])
            if w[0] == "object":
                return ("mod", rel, "cjs")
            if w[0] == "require":
                r = self.resolve_spec(rel, w[1])
                if r[0] == "external":
                    return ("ext", r[1])
                return self.module_object(r[1], "cjs", depth + 1) if r[0] == "file" else None
            return None
        return ("mod", rel, "cjs") if info.cjs_props else None

    def module_object(self, rel, how, depth=0):
        """The module object an `import * as` ("esm") or a require ("cjs") of `rel` binds."""
        info = self.info(rel)
        if info is None or info.dynamic or depth > _REEXPORT_DEPTH:
            return None
        w = info.cjs_whole
        if how == "esm":
            return ("mod", rel, "esm") if w is None else None
        if w is not None:
            if w[0] == "ident":
                return ("sym", rel, w[1])                 # `module.exports = X`: the import IS X
            if w[0] == "object":
                return ("mod", rel, "cjs")
            if w[0] == "require":
                r = self.resolve_spec(rel, w[1])
                if r[0] == "external":
                    return ("ext", r[1])
                return self.module_object(r[1], "cjs", depth + 1) if r[0] == "file" else None
            return None
        if info.has_esm and not info.cjs_props:
            return ("mod", rel, "esm")
        return ("mod", rel, "cjs")

    def plain(self, rel) -> bool:
        """The module's own top-level binding of every name it exports IS that export: no export key that is also
        a top-level name means another binding, nothing is exported dynamically, module.exports is not replaced
        by a value, and no `export *` target may export a top-level name the module does not export itself (ES:
        a private local never shadows a star export, so `ns.helper` is the star's helper) — module(rel) is then
        exact for the core's file_symbol."""
        key = ("plain", rel)
        if key not in self._memo:
            self._memo[key] = self._plain(rel)
        return self._memo[key]

    def _plain(self, rel) -> bool:
        info = self.info(rel)
        if info is None or info.dynamic:
            return False
        w = info.cjs_whole
        if w is not None and w[0] != "object":
            return False
        tables = [info.esm, info.cjs_props] + ([w[1]] if w is not None else [])
        for table in tables:
            for k, d in table.items():
                if k in info.top and tuple(d) != ("local", k):
                    return False
        if info.stars:
            own = set(info.esm) | set(info.cjs_props) | (set(w[1]) if w is not None else set())
            for name in sorted(info.top - own):
                if any(self.star_may_export(rel, s, name) for s in info.stars):
                    return False
        return True

    def star_may_export(self, rel, spec, name) -> bool:
        """May `export * from spec` (in rel) export `name`? True unless the target is a graph module that provably
        does not (no explicit export of it, no star of its own that may, nothing dynamic); cycles and depth say
        True."""
        r = self.resolve_spec(rel, spec)
        return r[0] != "file" or self.may_export(r[1], name, 0)

    def may_export(self, rel, name, depth) -> bool:
        key = ("may", rel, name)
        if key in self._memo:
            return self._memo[key]
        if key in self._busy or depth > _REEXPORT_DEPTH:
            return True
        info = self.info(rel)
        if info is None or info.dynamic:
            return True
        w = info.cjs_whole
        if w is not None and w[0] != "object":
            return True
        if name in info.esm or name in info.cjs_props or (w is not None and name in w[1]):
            got = True
        else:
            self._busy.add(key)
            try:
                got = False
                for s in info.stars:
                    r = self.resolve_spec(rel, s)
                    if r[0] != "file" or self.may_export(r[1], name, depth + 1):
                        got = True
                        break
            finally:
                self._busy.discard(key)
        self._memo[key] = got
        return got

    def to_resolution(self, v):
        T = _ir()
        if v is None:
            return T.RES_UNKNOWN
        if v[0] == "sym":
            return T.Resolution("symbol", rel=v[1], name=v[2]) if v[1] in self.graph_files else T.RES_UNKNOWN
        if v[0] == "mod":
            if v[1] not in self.graph_files:
                return T.RES_UNKNOWN
            return T.Resolution("module", rel=v[1]) if self.plain(v[1]) else \
                T.Resolution("namespace", name=f"{v[2]}|{v[1]}")
        if v[0] == "ext":
            return T.Resolution("external", stems=tuple(v[1])) if v[1] else T.RES_UNKNOWN
        return T.RES_UNKNOWN

    # -------------------------------------------------------------- the Binder interface
    def resolve_import(self, rel, imp):
        T = _ir()
        r = self.resolve_spec(rel, imp.spec)
        if r[0] == "external":
            return T.Resolution("external", stems=tuple(r[1])) if r[1] else T.RES_UNKNOWN
        if r[0] != "file":
            return T.RES_UNKNOWN
        rel2 = r[1]
        if imp.kind == "named":
            v = self.export_of(rel2, imp.name)
        elif imp.kind == "default":
            v = self.default_of(rel2)
        elif imp.kind == "namespace":
            v = self.module_object(rel2, "esm")
        elif imp.kind == "module":
            v = self.module_object(rel2, "cjs")
        else:
            v = None
        return self.to_resolution(v)

    def submodule(self, module, attr):
        """A member of a module object: `module` is a graph file (a module(rel) the core's file_symbol did not
        find `attr` in) or a namespace token ("esm|<rel>" / "cjs|<rel>")."""
        rel = str(module or "")
        if rel.startswith(("esm|", "cjs|")):
            rel = rel[4:]
        if rel not in self.graph_files:
            return None
        v = self.export_of(rel, attr)
        return self.to_resolution(v) if v is not None else None

    def resolve_free(self, rel, name):
        """None (TypeScript / JavaScript globals are the LanguageSpec builtins) — except a builtin global the project
        patches (`window.helper = helper`, `String.prototype.x = ...`, a `declare global` member): it is unknown in
        every file, so a member call on it is never external (the member called may be the project's). Once the
        core reads such patches itself (typed_calls.PATCH_RULES "loose": a patched attr is never an external member
        call, per attr), this coarser per-global rule stands down — one rule answers that question."""
        if "loose" in getattr(_ir(), "PATCH_RULES", ()):
            return None
        return _ir().RES_UNKNOWN if name in self.patched_globals() else None

    def patched_globals(self) -> frozenset:
        if self._patched is None:
            T = _ir()
            builtins = T.LANGUAGES["typescript"].builtins | T.LANGUAGES["javascript"].builtins
            out = set()
            for ff in self.facts.values():
                if mod_info(ff) is None:
                    continue
                bound = set(ff.functions) | set(ff.globals) | set(ff.imports) | {
                    c.name for c in ff.classes if c.scope < 0 and c.outer < 0}
                for tref, _attr in ff.patches:
                    if tref.kind == "name" and tref.name in builtins and tref.name not in bound:
                        out.add(tref.name)
            self._patched = frozenset(out)
        return self._patched


# ================================================================================================
# The reader
# ================================================================================================

_CANARY_TS = (b'import { Repo } from "./repo";\n'
              b"export class Svc extends Base<number> {\n"
              b"  constructor(private repo: Repo) { super(); }\n"
              b"  run(o: Order | null): void { this.repo.find(o); new Order(); }\n"
              b"}\n")
_CANARY_TSX = b"export function View(p: Props) { return <div onClick={() => p.go()} />; }\n"
_CANARY_JS = (b"const h = require('./h');\n"
              b"class K {\n"
              b"  m() { this.f = new X(); h.go(); }\n"
              b"}\n"
              b"module.exports = { K };\n")


def _canary_ok(lang: str) -> bool:
    T = _ir()
    if lang == "javascript":
        ff = read_facts("canary.js", "javascript", parse_bytes(_CANARY_JS, "js"), _CANARY_JS)
        sc = next((s for s in ff.scopes if s.name == "m"), None)
        k = ff.classes[0] if ff.classes else None
        info = mod_info(ff)
        return (not T.validate_facts(ff) and k is not None and k.name == "K"
                and [b.kind for b in k.fields.get("f", ())] == ["ctor"]
                and [i.kind for i in ff.imports.get("h", ())] == ["module"]
                and sc is not None and sc.self_kind == "inst"
                and [(x.member, x.recv) for x in sc.sites if x.kind == "member"] == [("go", T.r_ident("h"))]
                and info is not None and info.cjs_whole == ("object", {"K": ("local", "K")}))
    ff = read_facts("canary.ts", "typescript", parse_bytes(_CANARY_TS, "ts"), _CANARY_TS)
    sc = next((s for s in ff.scopes if s.name == "run"), None)
    k = ff.classes[0] if ff.classes else None
    ok = (not T.validate_facts(ff) and k is not None and k.name == "Svc"
          and k.bases == (T.t_name("Base"),)
          and [b.type for b in k.fields.get("repo", ())] == [T.t_name("Repo")]
          and sc is not None and sc.params.get("o") is not None
          and sc.params["o"].type == T.t_union(T.t_name("Order"), T.t_builtin("null"))
          and ("find", T.r_field(T.R_SELF, "repo")) in [(x.member, x.recv) for x in sc.sites]
          and any(x.kind == "ctor" and x.member == "Order" for x in sc.sites))
    if not ok:
        return False
    fx = read_facts("canary.tsx", "typescript", parse_bytes(_CANARY_TSX, "tsx"), _CANARY_TSX)
    return not T.validate_facts(fx) and any(x.member == "go" for s in fx.scopes for x in s.sites)


class TsJsReader:
    """The typed_calls Reader for one of the two languages (make_reader(spec))."""
    version = READER_VERSION

    def __init__(self, spec):
        self.spec = spec
        self.lang = str(getattr(spec, "name", "") or "typescript")
        self.source = None

    def canary(self) -> bool:
        try:
            return bool(_canary_ok(self.lang))
        except Exception:  # noqa: BLE001 — no grammar, a changed grammar: the language is disabled
            return False

    def facts(self, rel, source):
        self.source = source
        got = _tree_of(rel, source)
        if got is None:
            return None
        root, data = got
        return read_facts(rel, self.lang, root, data)

    def binder(self, ctx):
        return TsJsBinder(ctx, self.source)


def make_reader(spec):
    """The typed_calls reader entry point (LanguageSpec.reader == "receiver_facts_ts")."""
    return TsJsReader(spec)
