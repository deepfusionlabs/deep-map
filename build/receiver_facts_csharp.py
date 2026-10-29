#!/usr/bin/env python3
"""C# receiver reader for typed-receiver call linking (DEEPMAP_TYPED_CALLS=csharp; opt-in, wave 2: it becomes a
default only through its own pre-registered decision, see build/typed_calls.py).

build/typed_calls.py (the core) loads this module for the `csharp` language (.cs) through `make_reader(spec)`.
Phase A (`facts`) parses each file's bytes (source.raw, a UTF-8 BOM stripped: graphify_extract._Source.lines decodes
with utf-8-sig, so line 1's columns are the BOM-less text's) with tree-sitter's C# grammar and lowers the tree to the
frozen IR (typed_calls.FileFacts); Phase B in the core types every receiver from those facts, from this module's
binder (C# name lookup) and from the C#-only LanguageSpec.lookup_rules (interface dispatch, partial classes, member
scope, using static, extension methods).

WHAT THE FACTS SAY (the core decides every verdict from them; see the READER CONTRACT in build/typed_calls.py)
  scopes    one per function-like declaration, its Scope.kind naming the syntax (graphify's C# call walk stops only
            at method_declaration, so a lambda's, an anonymous method's and a local function's calls belong to the
            enclosing method): "method" (a method, an explicit interface implementation), "ctor" (a constructor,
            named after its class: graphify_extract.cs_ctor_name's last segment, the C5 `Owner.Owner` node), "accessor"
            (ONE scope per property / indexer / event that has any accessor body or an expression body, covering
            every accessor: the C5 `Owner.Prop` node), "local_function", "lambda", "anonymous_method". `line` = the
            declaration's first line, attribute lines included (graphify starts a C# node there); `lines` = the name
            line, and for an overload the FIRST overload's line (graphify merges overloads into one node at that
            line, so the core's two-scopes-one-node rule keeps such a node from ever being a typed caller).
            Destructors, operators and conversion operators get no scope (no node holds their calls). `self_kind` =
            "class" in a static member, else "inst"; a lambda / anonymous method / local function inherits its
            parent's class and self kind.
  classes   class / struct / record / record struct / interface / enum declarations at any depth (`outer` = the
            enclosing type). kind "interface" | "abstract" (an abstract class) | "struct" | "enum" | "class".
            bases = every base-list entry (syntax cannot tell a class from an interface; a primary-constructor base
            `Base(args)` is Base). `open` = partial. `dynamic` = the declaration holds a parse error. fields = the
            declared type (`ann`) of every field declarator, event field, property (auto or computed: what
            `x.Prop.M()` reads), event and primary-constructor / positional-record parameter: the static type, which
            is what Roslyn binds (a constructor assignment `_x = x` adds nothing). methods = every method under its
            name (overloads: several MethodFacts, each with the UNION arity and the first overload's line — the
            engine's one node); flags "static", "abstract" (no body: an interface / abstract / partial-declaration /
            extern member). A return type of Task / ValueTask / IAsyncEnumerable is unknown (an `await` result is
            never typed).
  bindings  params: `T x` (an untyped lambda parameter: no type). locals: `T x = ...` -> ann (a C# local's static
            type never changes, so a reassignment adds nothing), `var x = new T()`, `T x = new T()` (the type written
            exactly as declared) and a target-typed `T x = new()` -> ctor (exactly T until reassigned),
            `var x = f()` / `var x = a.f()` -> call, `var x = y` / `var x = a.b` -> field, any other `var` -> unknown
            (an `await`, a cast, `as`, a literal). An explicit `foreach (T x ...)`, `catch (T x)`, `out T x` and a
            `x is T y` pattern bind ann; `foreach (var ...)`, `out var`, a `var` / tuple deconstruction, a query range
            variable and a local function's name are poisoned. A local is a local only inside its C# scope (the
            nearest block; a switch section's: the switch body; a for / using / fixed / foreach / catch variable's:
            that statement; an expression variable's: the nearest block): a receiver name read outside every
            such span is no local there — the field of the enclosing type (FIELD(SELF, x)), else OTHER — never
            IDENT(x), whose bindings the core would read.
  sites     every invocation and `new` in a scope's own body: `M(...)` / `M<T>(...)` bare (`nameof(...)` is no
            call); `recv.M(...)` / `recv.M<T>(...)` / `recv?.M(...)` member, recv lowered to SELF (`this`), SUPER
            (`base`), FIELD(SELF, x) (x a field / property / primary-constructor parameter of the enclosing type in
            this file, not shadowed), IDENT (a parameter, a local, or a name the core resolves: a type, a namespace,
            a member of another part or a base class), FIELD, CALL, NEW, CLASSREF (`int.Parse`, `List<int>.Empty`),
            LITERAL or OTHER (a cast, `as`, `await`, `global::`, an element access, a name of an OUTER type's member);
            `x!` and `(x)` are x; `new T(...)` / `new T { }` and a target-typed `T x = new()` are ctor sites of T.
            A site's line / column is its invocation's start (the line graphify records for a call).
  all_names every call over a scope's full span (nested scopes included).
  errors    a scope whose node holds a parse error (tree-sitter ERROR / MISSING), or whose lines hold an `#if` /
            `#elif` / `#else` / `#endif` directive (the build configuration decides which calls exist), keeps NO site
            and +1 on every name of its census, and so does every scope nested in it; a top-level parse error adds a
            spec-less star import (no unbound name of the file resolves).
  decls     the (line, byte col) of every declared method / constructor / local function / delegate name and of a
            type name followed by a parameter list (the textual veto skips them).
  patches   every extension method `static R M(this T x, ...)` is a patch (T, M): onto a project type the core's
            extension rule decides it; onto anything else (a builtin, an external or generic type) PATCH_RULES loose
            makes every external member call of M unknown (a project extension may be what runs).
  _cs       a private table hung on each FileFacts (not part of the IR, like the TS reader's `_tsjs`):
            namespaces (every namespace this file declares), usings / statics [(name, global?, the namespace
            declaration holding the directive; "" = the compilation unit)], class_ns / class_fqn (per class index),
            ext [(class index, method, this-type)], error (a top-level parse error). A `using X = A.B;` alias is an
            Import("named") instead, and a `global using X = A.B;` one is also in galias [(X, A.B)] (every file of
            the project sees it). cvis {class index: the namespace declarations enclosing it, the compilation unit
            first, each (its full name, its own non-global using namespaces): the lookup order of a simple type name
            written there}. For the core's C# rules (typed_calls lookup_rules overloads / virtual_dispatch /
            iface_dispatch): mods {(method, its MethodFacts line): the modifier words} and mparams {(method, line):
            (((TypeRef | None, passing "" | ref | out | in | this, optional), ...), variadic)} (None: two members of
            that name on one line), args [per scope: per site, the arguments lowered like receivers (a literal with
            its precise type: `5L` long) or None (named / ref / out / in arguments)], variant {interface class index:
            per type parameter "" | in | out}, reassigned [per scope: the names its span may rebind — an
            assignment's target (compound, parenthesized, every element of a deconstruction `(x, y) = ...`), a ref /
            out argument, the operand of any other `ref x` (a ref local or return may write x), a `++` / `--`
            operand], sealed [the class indexes declared sealed or static]; for comparing
            signatures across a generic base list (rule overloads): msig {(method, line): per parameter its written
            type as a tagged tuple (`sigt`: a class / method type parameter tagged as one, names kept as written)},
            tporder {class index: its own type parameters in order}, tpkind {class index: {type parameter: "s"
            (constrained `struct` / `unmanaged`: `T?` is Nullable<T>), "?" (constrained by a type parameter) or "a"
            (anything else, or nothing: `T?` an annotation, C# 9+)}}, bsig {class index: per base-list entry, aligned
            with ClassFacts.bases, its tagged tuple}.

THE BINDER (CsBinder: C# name lookup over every .cs file's facts; Phase B asks it, never the engine)
  A project type is a top-level type declared in a C# file, under its full name (namespace + name). Types are
  grouped by the nearest directory above the file holding a *.csproj (the repo root when none): a full name declared
  in the caller's own group wins; else exactly one other group must declare it; else it is ambiguous (unknown). Two
  non-partial types of one full name in one group are ambiguous; partial parts are one type.
  * resolve_free(rel, name): for each namespace the file declares (the global namespace when none), innermost out:
    a type or a namespace of that name in that namespace (both: unknown), then the using directives written in that
    namespace's declaration (one type: it; several: unknown); after the global namespace's own members, a
    `global using X = A.B;` alias of the group (A.B fully qualified: its type or namespace, else unknown; two
    targets: unknown), then the compilation unit's usings and every `global using` of the group. A namespace
    answers namespace(token); no project type or namespace answers None (the core then tries the LanguageSpec
    builtins; an unbound name types nothing). Two of the file's namespaces answering differently: unknown.
  * resolve_import(rel, imp): a `using X = A.B.C;` alias -> the type or namespace A.B.C (fully qualified, then
    relative to the file's namespaces), else unknown.
  * submodule(token, attr): the type or namespace `<namespace>.attr`.
  * partials(): the parts of every partial type (one group, one full name, one generic arity), for the core's
    `partials` rule. static_imports(rel): the file's `using static` types and the group's `global using static`.
    extensions(rel, m): every extension method named m in the project [(rel, class index, this-type)].
  Project references and MSBuild usings (<Using Include=...>) are not read; an alias the tree does not hold, an
  unreadable directory: unknown.

facts() returns None for an unreadable file and may raise on a reader bug (the core then gives that file no facts
and goes on); canary() and the binder never raise. Imports only the stdlib at import time (typed_calls,
tree-sitter, the C# grammar and graphify_extract are imported lazily; the grammar is OPTIONAL — graphifyy's own
dependency, never declared by DeepMap: without it the canary fails and typed_calls disables csharp).
"""
from __future__ import annotations

import bisect
import importlib
import os
import posixpath
import re
import sys
from collections import Counter
from pathlib import Path

READER_VERSION = "cs-1"
_BOM = b"\xef\xbb\xbf"
# The deepest Receiver / TypeRef nesting the reader builds (deeper -> OTHER / unknown): typed_calls.validate_facts
# rejects a FILE whose receiver or type nests deeper than 32, so these stay below it.
_MAX_RECV_DEPTH = 30
_MAX_TYPE_DEPTH = 30
_PP_COND = re.compile(rb"^[ \t]*#[ \t]*(?:if|elif|else|endif)\b", re.M)
# a return type whose value is only reached through `await` (the core reads a generic's head only: never typed)
_AWAITED = frozenset({"Task", "ValueTask", "IAsyncEnumerable", "IAsyncEnumerator"})
_TYPE_DECLS = frozenset({"class_declaration", "struct_declaration", "record_declaration", "interface_declaration",
                         "enum_declaration"})
_PREPROC = frozenset({"preproc_if", "preproc_elif", "preproc_else"})
_LITERALS = {"string_literal": "string", "verbatim_string_literal": "string", "raw_string_literal": "string",
             "interpolated_string_expression": "string", "integer_literal": "int", "real_literal": "double",
             "character_literal": "char", "boolean_literal": "bool"}
_NO_CALL = frozenset({"nameof"})          # an invocation that is no call
# where a local's C# scope ends (cs-block-scoped-local-leak): a local declaration statement's local lives in its
# nearest block (a switch section's in the whole switch body); a for / using / fixed statement's own declaration, a
# foreach variable and a catch variable in that statement; an expression variable (`out T x`, `x is T y`) in the
# nearest block (C# scopes an if-condition's pattern variable to the enclosing statement list: the block is never
# narrower than that). No such node inside the scope: the whole scope (a lambda's expression body, an accessor's
# `=>`). The span is never narrower than C#'s: a name read outside every span of its declarations is no local there.
_LOCAL_SPAN = frozenset({"block", "switch_body"})
_STMT_SPAN = frozenset({"for_statement", "using_statement", "fixed_statement"})
_EXPR_SPAN = frozenset({"block"})
_INT_SUFFIX = {"u": "uint", "l": "long", "ul": "ulong", "lu": "ulong"}
_REAL_SUFFIX = {"f": "float", "d": "double", "m": "decimal"}


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
# The grammar (optional)
# ================================================================================================

_LANGS: dict = {}


class GrammarUnavailable(RuntimeError):
    """tree-sitter or its C# grammar is not importable here (an OPTIONAL dependency: graphifyy's own, never declared
    by DeepMap — tests/compliance_gates.py section 13). The reader's canary then fails and typed_calls disables
    csharp: link() leaves every C# tuple unchanged."""


def _language():
    """The tree-sitter Language of C# (cached); raises GrammarUnavailable when the grammar is not importable."""
    if "cs" not in _LANGS:
        try:
            import tree_sitter as ts
            import tree_sitter_c_sharp as grammar
        except ImportError as exc:          # optional (see GrammarUnavailable): the language is disabled
            raise GrammarUnavailable(f"no tree-sitter C# grammar: {exc}") from exc
        _LANGS["cs"] = ts.Language(grammar.language())
    return _LANGS["cs"]


def parse_bytes(data: bytes):
    """The root node of `data` parsed with the C# grammar (raises GrammarUnavailable without one)."""
    try:
        import tree_sitter as ts
    except ImportError as exc:              # optional (see GrammarUnavailable)
        raise GrammarUnavailable(f"no tree-sitter binding: {exc}") from exc
    return ts.Parser(_language()).parse(data).root_node


def _tree_of(rel, source):
    """(root node, bytes) of a .cs file read through the sync's shared reader (source.raw; a UTF-8 BOM stripped), or
    None when the file is unreadable. Raises GrammarUnavailable without the grammar."""
    data = source.raw(rel)
    if data is None:
        return None
    if data.startswith(_BOM):
        data = data[len(_BOM):]
    return parse_bytes(data), data


# ================================================================================================
# Small AST helpers
# ================================================================================================

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


def _child(n, typ):
    return next((c for c in n.named_children if c.type == typ), None)


def _anon(n) -> set:
    return {c.type for c in n.children if not c.is_named}


def _ctor_seg(owner: str) -> str:
    """The last name segment of graphify_extract's constructor node name for `owner` (C5: `Owner.Owner`)."""
    try:
        fn = getattr(_sibling("graphify_extract"), "cs_ctor_name", None)
        if callable(fn):
            return _ir().name_seg(fn(owner)) or owner
    except Exception:  # noqa: BLE001 — no graphify_extract here: the C5 shape
        pass
    return owner


# ================================================================================================
# The reader
# ================================================================================================

class _FileReader:
    """One file's facts: a walk over its declarations (namespaces, usings, types, members), a walk over each scope's
    body (bindings, raw sites, nested scopes), then the sites lowered once every binding of the file is known."""

    def __init__(self, rel, lang, root, data):
        self.T = _ir()
        self.rel, self.lang, self.root, self.data = rel, lang, root, data
        self.imports: dict = {}
        self.classes: list = []
        self.scopes: list = []
        self.decls: set = set()
        self.patches: list = []
        self.cs = {"namespaces": [], "usings": [], "statics": [], "class_ns": [], "class_fqn": [], "ext": [],
                   "error": False, "mods": {}, "mparams": {}, "args": [], "variant": {}, "reassigned": [],
                   "sealed": [], "msig": {}, "tporder": {}, "bsig": {}, "galias": [], "cvis": {}, "tpkind": {}}
        self.frames: list = [("", [])]    # namespace declarations (the compilation unit first): (full name, usings)
        self.fstack: list = [0]           # the frames enclosing the declaration being read, outermost first
        self.cls_fields: list = []          # per class: the field names THIS file declares for it
        self.names: list = []               # per scope: every name bound across its WHOLE span (params, poisoned,
        #                                     locals whose C# scope is the whole scope)
        self.blk: list = []                 # per scope: {name: [(start byte, end byte), ...]} of block-scoped locals
        self.snode: list = []               # per scope: its declaration node (a span walk stops there)
        self.reassigned: list = []          # per scope: names an assignment / ref / out argument in it rebinds
        self.own: list = []                 # per scope: Counter of the calls in its own body
        self.raw_sites: list = []           # (scope, node, "call" | "new" | "typed_new", type)
        self.raw_binds: list = []           # (scope, name, kind, type, value node, line)
        self.errored: set = set()
        self.tps: list = []                 # per scope: the type parameters in scope
        self.pp: list = []                  # the (sorted) lines of #if / #elif / #else / #endif directives
        at, line = 0, 1
        for mt in _PP_COND.finditer(data):
            line += data.count(b"\n", at, mt.start())
            at = mt.start()
            self.pp.append(line)

    def text(self, n) -> str:
        return self.data[n.start_byte:n.end_byte].decode("utf-8", "replace")

    # ------------------------------------------------------------------ types
    def qparts(self, n, depth=0):
        """['A', 'B', 'C'] of an identifier / qualified name, or None (a generic or alias-qualified part)."""
        if n is None or depth > _MAX_TYPE_DEPTH:
            return None
        if n.type == "identifier":
            return [self.text(n)]
        if n.type == "qualified_name":
            q, nm = _field(n, "qualifier"), _field(n, "name")
            head = self.qparts(q, depth + 1)
            if head is None or nm is None or nm.type != "identifier":
                return None
            return head + [self.text(nm)]
        return None

    def typeref(self, n, tps, depth=0):
        T = self.T
        if n is None or depth > _MAX_TYPE_DEPTH:
            return T.T_UNKNOWN
        t = n.type
        if t == "identifier":
            nm = self.text(n)
            return T.T_UNKNOWN if nm in tps else T.t_name(nm)
        if t == "predefined_type":
            return T.t_builtin(self.text(n))
        if t == "qualified_name":
            nm = _field(n, "name")
            if nm is not None and nm.type == "generic_name":
                head = self.qparts(_field(n, "qualifier"))
                ident = _child(nm, "identifier")
                if head is None or ident is None or head[0] in tps:
                    return T.T_UNKNOWN
                args = _child(nm, "type_argument_list")
                return T.t_generic(T.t_qual(*(head + [self.text(ident)])),
                                   *[self.typeref(a, tps, depth + 1) for a in (args.named_children if args else ())])
            parts = self.qparts(n)
            if parts is None or parts[0] in tps:
                return T.T_UNKNOWN
            return T.t_qual(*parts)
        if t == "generic_name":
            ident = _child(n, "identifier")
            if ident is None or self.text(ident) in tps:
                return T.T_UNKNOWN
            args = _child(n, "type_argument_list")
            return T.t_generic(T.t_name(self.text(ident)),
                               *[self.typeref(a, tps, depth + 1) for a in (args.named_children if args else ())])
        if t == "nullable_type":
            inner = _field(n, "type") or (n.named_children[0] if n.named_children else None)
            return T.t_nullable(self.typeref(inner, tps, depth + 1))
        return T.T_UNKNOWN                         # array, tuple, pointer, function pointer, `global::`, ref

    def head_name(self, n):
        """The last identifier of a type node (`A.B<T>` -> B), or None."""
        if n is None:
            return None
        if n.type == "identifier":
            return self.text(n)
        if n.type == "generic_name":
            ident = _child(n, "identifier")
            return self.text(ident) if ident is not None else None
        if n.type == "qualified_name":
            return self.head_name(_field(n, "name"))
        return None

    def type_params(self, n) -> frozenset:
        tl = _field(n, "type_parameters") or _child(n, "type_parameter_list")
        out = set()
        for tp in (tl.named_children if tl is not None else ()):
            nm = _field(tp, "name") or _child(tp, "identifier")
            if nm is not None:
                out.add(self.text(nm))
        return frozenset(out)

    def type_param_order(self, n) -> tuple:
        """The type parameters a declaration itself declares, in order (`class Repo<TKey, TVal>` -> (TKey, TVal))."""
        tl = _field(n, "type_parameters") or _child(n, "type_parameter_list")
        out = []
        for tp in (tl.named_children if tl is not None else ()):
            nm = (_field(tp, "name") or _child(tp, "identifier")) if tp.type == "type_parameter" else None
            if nm is not None:
                out.append(self.text(nm))
        return tuple(out)

    def tp_kinds(self, n, order, tps) -> dict:
        """{own type parameter: "s" (constrained `struct` / `unmanaged`: `T?` is Nullable<T>), "?" (constrained by a
        type parameter: unknown) or "a" (any other constraint, or none: `T?` is only an annotation, C# 9+)}."""
        out = {nm: "a" for nm in order}
        for cl in n.named_children:
            if cl.type != "type_parameter_constraints_clause":
                continue
            tgt = next((k for k in cl.named_children if k.type == "identifier"), None)
            nm = self.text(tgt) if tgt is not None else None
            if nm not in out:
                continue
            for k in cl.named_children:
                if k.type != "type_parameter_constraint":
                    continue
                txt = re.sub(r"\s+", "", self.text(k))
                if txt in ("struct", "unmanaged"):
                    out[nm] = "s"
                elif out[nm] == "a" and txt not in ("class", "class?", "notnull", "default") \
                        and not txt.startswith("new("):
                    head = re.split(r"[.<?\[]", txt, maxsplit=1)[0]
                    if head in tps or not head.isidentifier():
                        out[nm] = "?"
        return out

    def sigt(self, n, ctps, morder, depth=0):
        """A written parameter / base-list type as the core's signature comparison reads it (_cs msig / bsig; rule
        overloads): ("mtp", i) the method's i-th own type parameter, ("tp", name) a type parameter of the class or
        an outer class, ("b", keyword) a predefined type, ("n", parts) a (qualified) name, ("g", head, *args) a
        constructed generic, ("?", inner) a nullable type, ("[]", element, rank) an array, ("x", text) anything else
        (a tuple, a pointer, `global::`)."""
        if n is None or depth > _MAX_TYPE_DEPTH:
            return ("x", "")
        t = n.type
        if t == "identifier":
            nm = self.text(n)
            if nm in morder:
                return ("mtp", morder.index(nm))
            return ("tp", nm) if nm in ctps else ("n", (nm,))
        if t == "predefined_type":
            return ("b", self.text(n).strip())
        if t == "generic_name" or (t == "qualified_name" and getattr(_field(n, "name"), "type", "") == "generic_name"):
            nm = n if t == "generic_name" else _field(n, "name")
            head = [] if t == "generic_name" else self.qparts(_field(n, "qualifier"))
            ident = _child(nm, "identifier")
            if head is None or ident is None or (head and (head[0] in ctps or head[0] in morder)):
                return ("x", re.sub(r"\s+", "", self.text(n)))
            args = _child(nm, "type_argument_list")
            return ("g", ("n", tuple(head + [self.text(ident)]))) + tuple(
                self.sigt(a, ctps, morder, depth + 1) for a in (args.named_children if args else ()))
        if t == "qualified_name":
            parts = self.qparts(n)
            if parts and parts[0] not in ctps and parts[0] not in morder:
                return ("n", tuple(parts))
        elif t == "nullable_type":
            inner = _field(n, "type") or (n.named_children[0] if n.named_children else None)
            return ("?", self.sigt(inner, ctps, morder, depth + 1))
        elif t == "array_type":
            rank = _field(n, "rank")
            return ("[]", self.sigt(_field(n, "type"), ctps, morder, depth + 1),
                    re.sub(r"\s+", "", self.text(rank)) if rank is not None else "")
        return ("x", re.sub(r"\s+", "", self.text(n)))

    def mods(self, n) -> set:
        return {self.text(c).strip() for c in n.named_children if c.type == "modifier"}

    def params(self, pl, tps):
        """[(name, TypeRef | None, is_this, optional, is_params)] of a parameter list."""
        out = []
        for p in (pl.named_children if pl is not None else ()):
            if p.type != "parameter":
                continue
            nm = _field(p, "name")
            if nm is None:
                continue
            typ = _field(p, "type")
            mods = {self.text(c).strip() for c in p.children if c.type == "modifier"} | _anon(p)
            optional = any(c.type in ("default_expression", "equals_value_clause") for c in p.named_children) or \
                "=" in _anon(p)
            out.append((self.text(nm), None if typ is None else self.typeref(typ, tps), "this" in mods, optional,
                        "params" in mods))
        return out

    def sig(self, pl, tps) -> tuple:
        """(((TypeRef | None, passing: "" | ref | out | in | this, optional), ...), variadic) of a parameter list: the
        declared parameter types the core's applicability rule reads (_cs["mparams"]); variadic = a `params` array
        (this grammar writes it outside any `parameter` node)."""
        out = []
        for p in (pl.named_children if pl is not None else ()):
            if p.type != "parameter":
                continue
            typ = _field(p, "type")
            mods = {self.text(c).strip() for c in p.children if c.type == "modifier"} | _anon(p)
            how = next((k for k in ("ref", "out", "in", "this") if k in mods), "")
            optional = any(c.type in ("default_expression", "equals_value_clause") for c in p.named_children) or \
                "=" in _anon(p)
            out.append((None if typ is None else self.typeref(typ, tps), how, bool(optional)))
        return tuple(out), bool(pl is not None and "params" in _anon(pl))

    def note_member(self, table, name, line, value):
        """_cs[table][(name, line)] = value; a second member of that name on that line (one line holding two
        declarations) makes the entry None: unknown, never the wrong member's."""
        tab = self.cs[table]
        tab[(name, line)] = None if (name, line) in tab else value

    @staticmethod
    def arity(ps) -> tuple:
        if any(p[4] for p in ps):
            return (sum(1 for p in ps if not p[3] and not p[4]), -1)
        return (sum(1 for p in ps if not p[3]), len(ps))

    # ------------------------------------------------------------------ declarations
    def run(self):
        T = self.T
        self.items(self.root.named_children, "")
        self.cs["cvis"] = {idx: tuple((self.frames[f][0], tuple(sorted(set(self.frames[f][1])))) for f in chain)
                           for idx, chain in self.cs["cvis"].items()}
        self.finish_scopes()
        ff = T.FileFacts(self.rel, self.lang, imports=self.imports, classes=self.classes, functions={}, globals={},
                         scopes=self.scopes, decls=frozenset(self.decls), patches=self.patches,
                         reader_version=READER_VERSION)
        ff._cs = self.cs
        return ff

    def top_error(self):
        if not self.cs["error"]:
            self.cs["error"] = True
            self.imports.setdefault("*", []).append(self.T.Import("*", "star", "", "", 0))

    def add_ns(self, ns):
        if ns and ns not in self.cs["namespaces"]:
            self.cs["namespaces"].append(ns)

    def push_frame(self, full):
        self.frames.append((full, []))
        self.fstack.append(len(self.frames) - 1)

    def items(self, kids, ns):
        """Compilation-unit / namespace-body items under namespace `ns`."""
        cur, depth = ns, len(self.fstack)
        for c in kids:
            t = c.type
            if t == "using_directive":
                self.using(c, cur)
            elif t == "file_scoped_namespace_declaration":
                parts = self.qparts(_field(c, "name"))
                if parts is None:
                    self.top_error()
                    continue
                cur = ".".join(([cur] if cur else []) + parts)
                self.add_ns(cur)
                self.push_frame(cur)             # the usings after it are this namespace's
            elif t == "namespace_declaration":
                parts = self.qparts(_field(c, "name"))
                if parts is None:
                    self.top_error()
                    continue
                full = ".".join(([cur] if cur else []) + parts)
                self.add_ns(full)
                body = _field(c, "body")
                if body is not None:
                    self.push_frame(full)
                    self.items(body.named_children, full)
                    del self.fstack[len(self.fstack) - 1:]
                if c.has_error and body is None:
                    self.top_error()
            elif t in _TYPE_DECLS:
                self.type_decl(c, cur, -1)
            elif t == "delegate_declaration":
                nm = _field(c, "name")
                if nm is not None:
                    self.decls.add((_line(nm), _col(nm)))
            elif t in _PREPROC:
                self.items([k for k in c.named_children if k != _field(c, "condition")], cur)
            elif t == "ERROR" or c.is_missing:
                self.top_error()
        del self.fstack[depth:]

    def using(self, c, decl_ns):
        T = self.T
        anon = _anon(c)
        alias = _field(c, "name")
        target = next((k for k in c.named_children if k != alias and k.type in (
            "identifier", "qualified_name", "generic_name", "alias_qualified_name")), None)
        if target is None:
            return
        spec = re.sub(r"\s+", "", self.text(target))
        if spec.startswith("global::"):
            spec = spec[len("global::"):]
        if alias is not None:
            local = self.text(alias)
            self.imports.setdefault(local, []).append(T.Import(local, "named", spec, spec.rsplit(".", 1)[-1],
                                                               _line(c)))
            if "global" in anon:
                self.cs["galias"].append((local, spec))  # every file of the project sees it
        elif "static" in anon:
            self.cs["statics"].append((spec, "global" in anon, decl_ns))
        else:
            self.cs["usings"].append((spec, "global" in anon, decl_ns))
            if "global" not in anon:
                self.frames[self.fstack[-1]][1].append(spec)

    def type_decl(self, n, ns, outer):
        T = self.T
        name_n = _field(n, "name")
        if name_n is None:
            return
        name = self.text(name_n)
        mods = self.mods(n)
        tps = self.type_params(n)
        if outer >= 0:
            tps = tps | self.classes[outer].type_params
        t = n.type
        if t == "interface_declaration":
            kind = "interface"
        elif t == "enum_declaration":
            kind = "enum"
        elif t == "struct_declaration" or (t == "record_declaration" and "struct" in _anon(n)):
            kind = "struct"
        elif "abstract" in mods:
            kind = "abstract"
        else:
            kind = "class"
        idx = len(self.classes)
        cf = T.ClassFacts(name, _line(n), _end_line(n), kind=kind, lines=(_line(name_n),), outer=outer,
                          type_params=tps)
        cf.open = "partial" in mods
        cf.dynamic = bool(n.has_error)
        tl = _field(n, "type_parameters") or _child(n, "type_parameter_list")
        var = tuple(next((v for v in ("in", "out") if v in _anon(tp)), "") for tp in (tl.named_children if tl else ())
                    if tp.type == "type_parameter")
        if kind == "interface" and any(var):
            self.cs["variant"][len(self.classes)] = var      # `interface I<in T>` / `<out T>`: per-parameter variance
        if mods & {"sealed", "static"}:
            self.cs["sealed"].append(len(self.classes))      # no subclass can exist: its members are final
        self.cs["tporder"][idx] = self.type_param_order(n)  # what a base list's type arguments substitute (overloads)
        self.cs["tpkind"][idx] = self.tp_kinds(n, self.cs["tporder"][idx], tps)
        self.cs["cvis"][idx] = tuple(self.fstack)        # its namespace declarations (run(): their usings)
        self.classes.append(cf)
        self.cls_fields.append(set())
        outer_fqn = self.cs["class_fqn"][outer] if outer >= 0 else ns
        self.cs["class_ns"].append(ns)
        self.cs["class_fqn"].append(f"{outer_fqn}.{name}" if outer_fqn else name)
        bl = _child(n, "base_list")
        bases, bsig = [], []
        for b in (bl.named_children if bl is not None else ()):
            if b.type == "primary_constructor_base_type":
                b = _field(b, "type")
            if b is None or b.type in ("argument_list", "comment"):
                continue
            bases.append(self.typeref(b, tps))
            bsig.append(self.sigt(b, tps, ()))
        cf.bases = tuple(bases)
        self.cs["bsig"][idx] = tuple(bsig)               # per base-list entry, aligned with cf.bases
        pl = _child(n, "parameter_list")             # a primary constructor / positional record
        if pl is not None:
            self.decls.add((_line(name_n), _col(name_n)))
            for pname, ptype, _this, _opt, _pa in self.params(pl, tps):
                cf.fields.setdefault(pname, []).append(T.Binding("ann", ptype, line=_line(pl)))
                self.cls_fields[idx].add(pname)
        body = _field(n, "body")
        if body is not None and kind != "enum":
            self.members(body, idx, ns, tps)

    def member_nodes(self, body):
        """The member declarations of a declaration list, through #if wrappers."""
        stack, out = list(reversed(body.named_children)), []
        while stack:
            m = stack.pop()
            if m.type in _PREPROC:
                cond = _field(m, "condition")
                stack.extend(reversed([k for k in m.named_children if k != cond]))
            else:
                out.append(m)
        return out

    def members(self, body, ci, ns, tps):
        T = self.T
        cf = self.classes[ci]
        first: dict = {}                         # method name -> its first overload's line
        for m in self.member_nodes(body):
            t = m.type
            if t in ("field_declaration", "event_field_declaration"):
                vd = _child(m, "variable_declaration")
                if vd is None:
                    continue
                typ = self.typeref(_field(vd, "type"), tps)
                for d in vd.named_children:
                    nm = _field(d, "name") if d.type == "variable_declarator" else None
                    if nm is not None:
                        cf.fields.setdefault(self.text(nm), []).append(T.Binding("ann", typ, line=_line(d)))
                        self.cls_fields[ci].add(self.text(nm))
            elif t in ("property_declaration", "event_declaration"):
                self.property(m, ci, tps)
            elif t == "indexer_declaration":
                self.accessor_scope(m, ci, "this", None, tps)
            elif t == "method_declaration":
                self.method(m, ci, tps, first)
            elif t == "constructor_declaration":
                self.ctor(m, ci, tps)
            elif t in _TYPE_DECLS:
                self.type_decl(m, ns, ci)
            elif t == "delegate_declaration":
                nm = _field(m, "name")
                if nm is not None:
                    self.decls.add((_line(nm), _col(nm)))
            elif t in ("destructor_declaration", "conversion_operator_declaration"):
                nm = _field(m, "name") or _field(m, "type")
                if nm is not None:
                    self.decls.add((_line(nm), _col(nm)))
            elif t == "ERROR":
                cf.dynamic = True
        for name, mfs in cf.methods.items():           # overloads: one engine node -> the union arity on each
            if len(mfs) > 1:
                lo = min(mf.arity[0] for mf in mfs)
                hi = -1 if any(mf.arity[1] < 0 for mf in mfs) else max(mf.arity[1] for mf in mfs)
                cf.methods[name] = [mf._replace(arity=(lo, hi)) for mf in mfs]

    def new_scope(self, name, node, lines, parent, ci, self_kind, kind, params, tps):
        T = self.T
        sc = T.Scope(name, _line(node), _end_line(node), lines=tuple(lines), parent=parent, cls=ci,
                     self_kind=self_kind, params={p[0]: T.Binding("param", p[1]) for p in params}, kind=kind,
                     type_params=frozenset(tps))
        self.scopes.append(sc)
        self.names.append({p[0] for p in params})
        self.blk.append({})
        self.snode.append(node)
        self.reassigned.append(set())
        self.own.append(Counter())
        self.tps.append(frozenset(tps))
        si = len(self.scopes) - 1
        if node.has_error:
            self.errored.add(si)
        lo, hi = sc.line, sc.end_line
        k = bisect.bisect_left(self.pp, lo)
        if k < len(self.pp) and self.pp[k] <= hi:
            self.errored.add(si)
        return si

    def property(self, m, ci, tps):
        T = self.T
        cf = self.classes[ci]
        nm = _field(m, "name")
        if nm is None:
            return
        name = self.text(nm)
        cf.fields.setdefault(name, []).append(T.Binding("ann", self.typeref(_field(m, "type"), tps), line=_line(m)))
        self.cls_fields[ci].add(name)
        self.accessor_scope(m, ci, name, nm, tps)

    def accessor_scope(self, m, ci, name, name_n, tps):
        """ONE accessor scope over every accessor body / the expression body of a property / indexer / event."""
        bodies = []
        val = _field(m, "value")
        if val is not None and val.type == "arrow_expression_clause":
            bodies.append(val)
        acc = _field(m, "accessors") or _child(m, "accessor_list")
        for a in (acc.named_children if acc is not None else ()):
            if a.type == "accessor_declaration":
                b = _field(a, "body")
                if b is not None:
                    bodies.append(b)
        if not bodies:
            return
        static = "static" in self.mods(m)
        lines = (_line(name_n),) if name_n is not None else ()
        pl = _field(m, "parameters")                     # an indexer's parameters
        si = self.new_scope(name, m, lines, -1, ci, "class" if static else "inst", "accessor",
                            self.params(pl, tps) if pl is not None else [], tps)
        for b in bodies:
            self.walk(b, si)

    def method(self, m, ci, tps, first):
        T = self.T
        cf = self.classes[ci]
        nm = _field(m, "name")
        if nm is None:
            return
        name = self.text(nm)
        mods = self.mods(m)
        mtps = tps | self.type_params(m)
        ps = self.params(_field(m, "parameters"), mtps)
        body = _field(m, "body")
        flags = set()
        static = "static" in mods
        if static:
            flags.add("static")
        if body is None:
            flags.add("abstract")
        ret_n = _field(m, "returns") or _field(m, "type")
        returns = None
        if ret_n is not None and not (ret_n.type == "predefined_type" and self.text(ret_n) == "void"):
            returns = T.T_UNKNOWN if self.head_name(ret_n) in _AWAITED else self.typeref(ret_n, mtps)
        line = _line(m)
        f0 = first.setdefault(name, line)
        lines = (_line(nm),) + ((f0,) if f0 != line else ())
        cf.methods.setdefault(name, []).append(T.MethodFacts(line, frozenset(flags), returns, self.arity(ps), lines))
        self.note_member("mods", name, line, frozenset(mods))
        self.note_member("mparams", name, line, self.sig(_field(m, "parameters"), mtps))
        pl, morder = _field(m, "parameters"), self.type_param_order(m)
        self.note_member("msig", name, line, tuple(self.sigt(_field(p, "type"), tps, morder)
                                                   for p in (pl.named_children if pl is not None else ())
                                                   if p.type == "parameter"))   # aligned with mparams
        self.decls.add((_line(nm), _col(nm)))
        if static and ps and ps[0][2] and ps[0][1] is not None:          # an extension method
            self.patches.append((ps[0][1], name))
            self.cs["ext"].append((ci, name, ps[0][1]))
        if body is not None:
            si = self.new_scope(name, m, lines, -1, ci, "class" if static else "inst", "method", ps, mtps)
            self.walk(body, si)

    def ctor(self, m, ci, tps):
        cf = self.classes[ci]
        nm = _field(m, "name")
        if nm is not None:
            self.decls.add((_line(nm), _col(nm)))
        body = _field(m, "body")
        init = _child(m, "constructor_initializer")
        if body is None and init is None:
            return
        static = "static" in self.mods(m)
        ps = self.params(_field(m, "parameters"), tps)
        si = self.new_scope(_ctor_seg(cf.name), m, (_line(nm),) if nm is not None else (), -1, ci,
                            "class" if static else "inst", "ctor", ps, tps)
        if init is not None:
            self.walk(init, si)
        if body is not None:
            self.walk(body, si)

    # ------------------------------------------------------------------ scope bodies
    def walk(self, node, si):
        stack = [node]
        while stack:
            n = stack.pop()
            t = n.type
            if t in ("lambda_expression", "anonymous_method_expression"):
                self.nested_fn(n, si)
                continue
            if t == "local_function_statement":
                self.local_fn(n, si)
                continue
            if t in _TYPE_DECLS:
                continue
            if t == "invocation_expression":
                self.raw_sites.append((si, n, "call", None))
            elif t == "object_creation_expression":
                self.raw_sites.append((si, n, "new", None))
            elif t == "variable_declaration":
                self.declare(n, si)
            elif t == "assignment_expression":
                # `x = ...`, `x += ...`, `(x) = ...` and a deconstruction `(x, y) = ...` / `((x, y), z) = ...`
                self.reassigned[si].update(self.lvalue_names(_field(n, "left")))
            elif t == "argument" and _anon(n) & {"ref", "out"}:
                for k in n.named_children:
                    self.reassigned[si].update(self.lvalue_names(k))
            elif t == "ref_expression":
                # `ref x` outside an argument (`ref T r = ref x;`, `=> ref x`, `y = ref x`): a ref alias may write x
                for k in n.named_children:
                    self.reassigned[si].update(self.lvalue_names(k))
            elif t in ("prefix_unary_expression", "postfix_unary_expression") and _anon(n) & {"++", "--"}:
                for k in n.named_children:     # a user-defined ++ / -- returns a new value (it may be a subclass)
                    self.reassigned[si].update(self.lvalue_names(k))
            elif t == "foreach_statement":
                self.foreach(n, si)
            elif t in ("declaration_expression", "declaration_pattern", "recursive_pattern", "catch_declaration"):
                self.typed_name(n, si)
            elif t in ("var_pattern", "parenthesized_variable_designation", "tuple_pattern"):
                self.poison_all(n, si)
            elif t in ("from_clause", "let_clause", "join_clause", "join_into_clause", "query_continuation"):
                nm = _field(n, "name") or _child(n, "identifier")     # the range variable
                if nm is not None and nm.type == "identifier":
                    self.poison(si, self.text(nm))
            stack.extend(reversed(n.named_children))

    def lvalue_names(self, n, depth=0) -> set:
        """The local names an assignment / ref / out / ++ target `n` may rebind: an identifier; through parentheses,
        a tuple (a deconstruction's elements, recursively), a ref conditional's branches and a nested `ref`. A member
        or element access rebinds no local; a declaration (`var y` inside a deconstruction) declares a new one."""
        out: set = set()
        stack = [(n, depth)]
        while stack:
            k, d = stack.pop()
            if k is None or d > _MAX_RECV_DEPTH:
                continue
            if k.type == "identifier":
                out.add(self.text(k))
            elif k.type in ("parenthesized_expression", "tuple_expression", "argument", "ref_expression"):
                stack.extend((c, d + 1) for c in k.named_children if c.type != "name_colon")
            elif k.type == "conditional_expression":
                stack.extend((c, d + 1) for c in (_field(k, "consequence"), _field(k, "alternative")))
        return out

    def poison(self, si, x):
        sc = self.scopes[si]
        sc.poisoned = sc.poisoned | {x}
        self.names[si].add(x)

    def poison_all(self, n, si):
        """Poison the variables a deconstruction / `var` pattern / designation binds: identifiers in designation
        positions only (never a type name)."""
        stack = [n]
        while stack:
            k = stack.pop()
            if k.type == "identifier":
                self.poison(si, self.text(k))
            elif k.type in ("parenthesized_variable_designation", "tuple_pattern", "var_pattern"):
                stack.extend(k.named_children)
            elif k.type == "tuple_element":
                nm = _field(k, "name")
                if nm is not None:
                    stack.append(nm)

    def span_of(self, n, si, how):
        """(start byte, end byte) of the C# scope of a local declared at node n of scope si, or None when that scope
        is the whole scope (_LOCAL_SPAN above). how: "local" (a variable_declaration), "stmt" (the statement that
        declares it: foreach / catch), "expr" (an expression variable)."""
        stop = self.snode[si] if 0 <= si < len(self.snode) else None
        stop_at = (stop.start_byte, stop.end_byte, stop.type) if stop is not None else None
        if how == "stmt":
            return (n.start_byte, n.end_byte)
        p = n.parent
        if how == "local":
            if p is not None and p.type in _STMT_SPAN:
                return (p.start_byte, p.end_byte)
            if p is None or p.type != "local_declaration_statement":
                return None
            kinds = _LOCAL_SPAN
        else:
            kinds = _EXPR_SPAN
        hops = 0
        while p is not None and hops < 4096:
            if stop_at is not None and (p.start_byte, p.end_byte, p.type) == stop_at:
                return None
            if p.type in kinds:
                return (p.start_byte, p.end_byte)
            p = p.parent
            hops += 1
        return None

    def bind_name(self, si, x, span):
        """x is a local of scope si: across the whole scope (span None) or only inside `span`."""
        if span is None:
            self.names[si].add(x)
        else:
            self.blk[si].setdefault(x, []).append(span)

    def declare(self, vd, si):
        typ = _field(vd, "type")
        implicit = typ is None or typ.type == "implicit_type"
        tr = None if implicit else self.typeref(typ, self.tps[si])
        span = self.span_of(vd, si, "local")
        for d in vd.named_children:
            if d.type != "variable_declarator":
                continue
            nm = _field(d, "name")
            if nm is None or nm.type != "identifier":
                for k in d.named_children:               # `var (a, b) = ...`
                    if k.type in ("tuple_pattern", "parenthesized_variable_designation", "identifier"):
                        self.poison_all(k, si)
                continue
            x = self.text(nm)
            val = next((k for k in reversed(d.named_children) if k != nm and k.type != "bracketed_argument_list"),
                       None)
            self.bind_name(si, x, span)
            if not implicit:
                # `T x = new T(...)` / a target-typed `T x = new(...)`: a construction of exactly T, like `var x = new
                # T()` (ctor: its run-time class is T until a reassignment the core reads from _cs reassigned)
                kind = "ctor" if self.constructs(typ, val) else "ann"
                self.raw_binds.append((si, x, kind, tr, None, _line(d)))
                if val is not None and val.type == "implicit_object_creation_expression":
                    self.raw_sites.append((si, val, "typed_new", typ))
            elif val is None:
                self.raw_binds.append((si, x, "unknown", None, None, _line(d)))
            elif val.type == "object_creation_expression":
                self.raw_binds.append((si, x, "ctor", self.typeref(_field(val, "type"), self.tps[si]), None,
                                       _line(d)))
            elif val.type == "invocation_expression":
                self.raw_binds.append((si, x, "call", None, val, _line(d)))
            elif val.type in ("identifier", "member_access_expression", "this", "this_expression"):
                self.raw_binds.append((si, x, "field", None, val, _line(d)))
            else:
                self.raw_binds.append((si, x, "unknown", None, None, _line(d)))

    def constructs(self, typ, val) -> bool:
        """Does initializer `val` construct exactly the declared type `typ`: a target-typed `new(...)`, or `new T(...)`
        whose type is written exactly as the declaration's (`Repo r = new Repo()`; never `Base b = new Derived()`,
        `Shop.Repo r = new Repo()` or `Repo? r = new Repo()`)?"""
        if typ is None or val is None:
            return False
        if val.type == "implicit_object_creation_expression":
            return True
        if val.type != "object_creation_expression":
            return False
        vt = _field(val, "type")
        return vt is not None and re.sub(r"\s+", "", self.text(vt)) == re.sub(r"\s+", "", self.text(typ))

    def foreach(self, n, si):
        typ, left = _field(n, "type"), _field(n, "left")
        if left is not None and left.type == "identifier":
            x = self.text(left)
            if typ is None or typ.type == "implicit_type":
                self.poison(si, x)
            else:
                self.bind_name(si, x, self.span_of(n, si, "stmt"))
                self.raw_binds.append((si, x, "ann", self.typeref(typ, self.tps[si]), None, _line(n)))
        elif left is not None:
            self.poison_all(left, si)
            for k in left.named_children:
                if k.type == "identifier":
                    self.poison(si, self.text(k))

    def typed_name(self, n, si):
        """`out T x` / `x is T y` / `catch (T x)`: an explicit type binds ann; `var` poisons."""
        typ, nm = _field(n, "type"), _field(n, "name")
        if nm is None or nm.type != "identifier":
            return
        x = self.text(nm)
        if typ is None or typ.type == "implicit_type":
            self.poison(si, x)
            return
        if n.type == "catch_declaration":
            span = self.span_of(n.parent, si, "stmt") if n.parent is not None else None
        else:
            span = self.span_of(n, si, "expr")
        self.bind_name(si, x, span)
        self.raw_binds.append((si, x, "ann", self.typeref(typ, self.tps[si]), None, _line(n)))

    def nested_fn(self, n, parent):
        kind = "lambda" if n.type == "lambda_expression" else "anonymous_method"
        ps = []
        pnode = _field(n, "parameters") or _child(n, "parameter_list")
        if pnode is not None and pnode.type == "implicit_parameter":
            ps = [(self.text(pnode), None, False, False, False)]
        elif pnode is not None:
            ps = self.params(pnode, self.tps[parent])   # an implicitly typed `(a, b) =>`: parameters without a type
        sc = self.scopes[parent]
        si = self.new_scope("<lambda>" if kind == "lambda" else "<anonymous>", n, (), parent, sc.cls, sc.self_kind,
                            kind, ps, self.tps[parent])
        body = _field(n, "body") or _child(n, "block")
        if body is not None:
            self.walk(body, si)

    def local_fn(self, n, parent):
        nm = _field(n, "name")
        if nm is None:
            return
        name = self.text(nm)
        self.poison(parent, name)
        self.decls.add((_line(nm), _col(nm)))
        tps = self.tps[parent] | self.type_params(n)
        sc = self.scopes[parent]
        si = self.new_scope(name, n, (), parent, sc.cls, sc.self_kind, "local_function",
                            self.params(_field(n, "parameters"), tps), tps)
        body = _field(n, "body")
        if body is not None:
            self.walk(body, si)

    # ------------------------------------------------------------------ lowering (every binding known)
    def bound(self, si, x, pos=None) -> bool:
        """Is x a parameter / local of scope si's chain AT byte `pos` (a block-scoped local only inside its block's
        span; pos None: anywhere)?"""
        while 0 <= si < len(self.scopes):
            if x in self.names[si]:
                return True
            if any(pos is None or a <= pos < b for a, b in self.blk[si].get(x, ())):
                return True
            si = self.scopes[si].parent
        return False

    def block_local(self, si, x) -> bool:
        """Is x a block-scoped local somewhere in scope si's chain (whose bindings the core would read for an IDENT x,
        wherever the name is written)?"""
        while 0 <= si < len(self.scopes):
            if x in self.blk[si]:
                return True
            si = self.scopes[si].parent
        return False

    def outer_member(self, ci, x) -> bool:
        o = self.classes[ci].outer
        while o >= 0:
            if x in self.cls_fields[o] or x in self.classes[o].methods:
                return True
            o = self.classes[o].outer
        return False

    def member_of(self, fn):
        """(member name, receiver node or None, conditional?) of an invocation's function node, or None."""
        t = fn.type if fn is not None else ""
        if t == "identifier":
            return self.text(fn), None
        if t == "generic_name":
            ident = _child(fn, "identifier")
            return (self.text(ident), None) if ident is not None else None
        if t == "member_access_expression":
            nm = _field(fn, "name")
            if nm is not None and nm.type == "generic_name":
                nm = _child(nm, "identifier")
            expr = _field(fn, "expression")
            if nm is None or nm.type != "identifier" or expr is None:
                return None
            return self.text(nm), expr
        if t == "conditional_access_expression":
            b = _child(fn, "member_binding_expression")
            nm = _field(b, "name") if b is not None else None
            if nm is not None and nm.type == "generic_name":
                nm = _child(nm, "identifier")
            cond = _field(fn, "condition")
            if nm is None or nm.type != "identifier" or cond is None:
                return None
            return self.text(nm), cond
        return None

    def lower(self, e, si, depth=0):
        T = self.T
        if e is None or depth > _MAX_RECV_DEPTH:
            return T.R_OTHER
        t = e.type
        if t in ("this", "this_expression"):
            return T.R_SELF
        if t in ("base", "base_expression"):
            return T.R_SUPER
        if t == "identifier":
            x = self.text(e)
            if self.bound(si, x, e.start_byte):
                return T.r_ident(x)
            ci = self.scopes[si].cls if 0 <= si < len(self.scopes) else -1
            if ci >= 0 and x in self.cls_fields[ci]:
                return T.r_field(T.R_SELF, x)
            if ci >= 0 and self.outer_member(ci, x):
                return T.R_OTHER
            if self.block_local(si, x):
                return T.R_OTHER          # an inner block's local, out of its scope here: the core would read it
            return T.r_ident(x)
        if t == "member_access_expression":
            nm = _field(e, "name")
            if nm is None or nm.type != "identifier":
                return T.R_OTHER
            return T.r_field(self.lower(_field(e, "expression"), si, depth + 1), self.text(nm))
        if t == "conditional_access_expression":
            b = _child(e, "member_binding_expression")
            nm = _field(b, "name") if b is not None else None
            if nm is None or nm.type != "identifier" or len(e.named_children) != 2:
                return T.R_OTHER
            return T.r_field(self.lower(_field(e, "condition"), si, depth + 1), self.text(nm))
        if t == "invocation_expression":
            got = self.member_of(_field(e, "function"))
            if got is None or got[0] in _NO_CALL:
                return T.R_OTHER
            return T.r_call(None if got[1] is None else self.lower(got[1], si, depth + 1), got[0])
        if t == "object_creation_expression":
            return T.r_new(self.typeref(_field(e, "type"), self.tps[si]))
        if t == "parenthesized_expression":
            kids = [k for k in e.named_children if k.type != "comment"]
            return self.lower(kids[0], si, depth + 1) if len(kids) == 1 else T.R_OTHER
        if t == "postfix_unary_expression":
            kids = [k for k in e.named_children if k.type != "comment"]
            if len(kids) == 1 and self.text(e).rstrip().endswith("!"):
                return self.lower(kids[0], si, depth + 1)
            return T.R_OTHER
        if t == "predefined_type":
            return T.r_classref(T.t_builtin(self.text(e)))
        if t == "generic_name":
            return T.r_classref(self.typeref(e, self.tps[si]))
        if t in _LITERALS:
            return T.r_literal(_LITERALS[t])
        return T.R_OTHER

    def argc(self, n) -> int:
        al = _field(n, "arguments") or _child(n, "argument_list")
        if al is None:
            return -1
        return sum(1 for a in al.named_children if a.type == "argument")

    def literal(self, e):
        """A literal argument's precise type (`5L` long, `1.5f` float, `2m` decimal: the IR's LITERAL keeps only the
        literal kind), or None when e is no literal."""
        T = self.T
        t = e.type
        if t == "integer_literal":
            sfx = re.sub(r"[0-9a-fA-FxX_]", "", self.text(e).lower().replace("0x", "").replace("0b", ""))
            return T.r_literal(_INT_SUFFIX.get(sfx, "int") if sfx else "int")
        if t == "real_literal":
            sfx = self.text(e).strip().lower()[-1:]
            return T.r_literal(_REAL_SUFFIX.get(sfx, "double"))
        if t in _LITERALS:
            return T.r_literal(_LITERALS[t])
        return None

    def site_args(self, n, si):
        """The arguments of invocation / creation n, each lowered like a receiver (a literal with its precise type),
        for the core's applicability rule (_cs["args"]); None when any argument is named, passed by ref / out / in,
        declares a variable, or the list is unreadable (the core then cannot match it to parameters)."""
        al = _field(n, "arguments") or _child(n, "argument_list")
        if al is None:
            return None
        out = []
        for a in al.named_children:
            if a.type == "comment":
                continue
            if a.type != "argument":
                return None
            kids = [k for k in a.named_children if k.type != "comment"]
            if (_anon(a) & {":", "ref", "out", "in"}) or len(kids) != 1 or kids[0].type in (
                    "name_colon", "declaration_expression", "modifier"):
                return None
            lit = self.literal(kids[0])
            out.append(lit if lit is not None else self.lower(kids[0], si))
        return tuple(out)

    def finish_scopes(self):
        T = self.T
        n = len(self.scopes)
        for si in range(n):                            # a scope nested in an errored one keeps no site either
            p = self.scopes[si].parent
            if p >= 0 and p in self.errored:
                self.errored.add(si)
        sites: list = [[] for _ in range(n)]
        args: list = [[] for _ in range(n)]           # _cs["args"][si][k]: the arguments of sites[si][k]
        for si, node, kind, typ in self.raw_sites:
            if kind == "call":
                got = self.member_of(_field(node, "function"))
                if got is None or got[0] in _NO_CALL:
                    continue
                member, recv_n = got
                self.own[si][member] += 1
                if si in self.errored:
                    continue
                recv = None if recv_n is None else self.lower(recv_n, si)
                sites[si].append(T.Site(_line(node), _col(node), member, recv, "bare" if recv_n is None else "member",
                                        self.argc(node)))
                args[si].append(self.site_args(node, si))
                continue
            tnode = _field(node, "type") if kind == "new" else typ
            member = self.head_name(tnode)
            if member is None:
                continue
            self.own[si][member] += 1
            if si in self.errored:
                continue
            al = _field(node, "arguments") or _child(node, "argument_list")
            argc = sum(1 for a in al.named_children if a.type == "argument") if al is not None else -1
            sites[si].append(T.Site(_line(node), _col(node), member,
                                    T.r_classref(self.typeref(tnode, self.tps[si])), "ctor", argc))
            args[si].append(None)
        self.cs["args"] = args
        for si, x, kind, tr, val, line in self.raw_binds:
            if kind == "call":
                v = self.lower(val, si)
                b = T.Binding("call", value=v, line=line, scope=si) if v.kind == "CALL" else \
                    T.Binding("unknown", line=line, scope=si)
            elif kind == "field":
                b = T.Binding("field", value=self.lower(val, si), line=line, scope=si)
            else:
                b = T.Binding(kind, tr, line=line, scope=si)
            self.scopes[si].locals.setdefault(x, []).append(b)
        total = []
        for si in range(n):
            c = Counter(self.own[si])
            if si in self.errored:
                for m in list(c):
                    c[m] += 1
            total.append(c)
        for si in reversed(range(n)):                  # a parent's index is always smaller than its child's
            p = self.scopes[si].parent
            if p >= 0:
                total[p].update(total[si])
        for si, sc in enumerate(self.scopes):
            sc.sites = sites[si]
            sc.all_names = dict(total[si])
        rea = [set(r) for r in self.reassigned]
        for si in reversed(range(n)):                  # a nested scope's assignment rebinds its parents' names too
            p = self.scopes[si].parent
            if p >= 0:
                rea[p] |= rea[si]
        self.cs["reassigned"] = [sorted(r) for r in rea]


def read_facts(rel, lang, root, data):
    """typed_calls.FileFacts of one C# file's tree (`root`, over `data`)."""
    return _FileReader(rel, lang, root, data).run()


# ================================================================================================
# The binder (C# name lookup)
# ================================================================================================

_NS_SEP = "\x1f"


class CsBinder:
    """Resolves C# names to project files (see THE BINDER in the module docstring). Never raises."""

    def __init__(self, ctx):
        self.T = _ir()
        self.root = str(getattr(ctx, "root", "") or ".")
        lang = getattr(ctx, "lang", "csharp")
        self.facts = {r: f for r, f in dict(getattr(ctx, "facts", {}) or {}).items()
                      if getattr(f, "lang", "") == lang}
        self._idx = None
        self._grp: dict = {}
        self._has: dict = {}
        self._memo: dict = {}

    # -------------------------------------------------------------- indexes
    def cs(self, rel) -> dict:
        ff = self.facts.get(rel)
        got = getattr(ff, "_cs", None) if ff is not None else None
        return got if isinstance(got, dict) else {}

    def index(self):
        if self._idx is None:
            types, nss, gusing, gstatic, ext, galias = {}, set(), {}, {}, {}, {}
            for rel in sorted(self.facts):
                ff, cs = self.facts[rel], self.cs(rel)
                if not cs:
                    continue
                g = self.group_of(rel)
                for ns in cs.get("namespaces", ()):
                    parts = ns.split(".")
                    for i in range(1, len(parts) + 1):
                        nss.add(".".join(parts[:i]))
                fqns = cs.get("class_fqn", ())
                for i, c in enumerate(ff.classes):
                    if c.outer < 0 and c.scope < 0 and i < len(fqns):
                        types.setdefault(fqns[i], []).append((g, rel, i, bool(c.open)))
                for u, glob, _d in cs.get("usings", ()):
                    if glob:
                        gusing.setdefault(g, []).append(u)
                for u, glob, _d in cs.get("statics", ()):
                    if glob:
                        gstatic.setdefault(g, []).append(u)
                for i, name, tref in cs.get("ext", ()):
                    ext.setdefault(name, []).append((rel, i, tref))
                for name, spec in cs.get("galias", ()):
                    galias.setdefault(g, {}).setdefault(name, set()).add(spec)
            self._idx = (types, frozenset(nss), gusing, gstatic, ext, galias)
        return self._idx

    def has_csproj(self, d) -> bool:
        if d not in self._has:
            found = False
            try:
                with os.scandir(Path(self.root) / d if d else Path(self.root)) as it:
                    found = any(e.name.lower().endswith(".csproj") and e.is_file() for e in it)
            except (OSError, ValueError):
                found = False
            self._has[d] = found
        return self._has[d]

    def group_of(self, rel) -> str:
        """The nearest directory above `rel` (root-relative) holding a *.csproj, or "" (the repo root)."""
        d = posixpath.dirname(str(rel).replace("\\", "/"))
        if d not in self._grp:
            cur, out = d, ""
            while True:
                if self.has_csproj(cur):
                    out = cur
                    break
                if not cur:
                    break
                cur = posixpath.dirname(cur)
            self._grp[d] = out
        return self._grp[d]

    def lookup_type(self, fq, grp):
        """("one", rel, idx) | ("amb",) | None for full name `fq` seen from group `grp`."""
        ents = self.index()[0].get(fq)
        if not ents:
            return None
        own = [e for e in ents if e[0] == grp]
        if not own:
            groups = {e[0] for e in ents}
            if len(groups) != 1:
                return ("amb",)
            own = ents
        if len(own) == 1 or all(e[3] for e in own):
            e = sorted(own, key=lambda x: (x[1], x[2]))[0]
            return ("one", e[1], e[2])
        return ("amb",)

    def _answer(self, got, fq):
        T = self.T
        if got is None:
            return None
        if got[0] == "amb":
            return T.RES_UNKNOWN
        return T.Resolution("symbol", rel=got[1], name=fq.rsplit(".", 1)[-1])

    def token(self, grp, fq) -> str:
        return f"{grp}{_NS_SEP}{fq}"

    def resolve_fq(self, fq, grp):
        """A full name: a type (symbol), a namespace (namespace token), RES_UNKNOWN (both / ambiguous) or None."""
        t = self.lookup_type(fq, grp)
        is_ns = fq in self.index()[1]
        if t is not None and is_ns:
            return self.T.RES_UNKNOWN
        if t is not None:
            return self._answer(t, fq)
        if is_ns:
            return self.T.Resolution("namespace", name=self.token(grp, fq))
        return None

    # -------------------------------------------------------------- the binder interface
    def resolve_free(self, rel, name):
        key = ("free", rel, name)
        if key not in self._memo:
            try:
                self._memo[key] = self._resolve_free(rel, name)
            except Exception:  # noqa: BLE001
                self._memo[key] = self.T.RES_UNKNOWN
        return self._memo[key]

    def _resolve_free(self, rel, name):
        cs = self.cs(rel)
        if not cs or not name:
            return None
        nss = list(cs.get("namespaces") or ()) or [""]
        answers = []
        for ns in nss:
            answers.append(self._resolve_in(rel, cs, ns, name))
        first = answers[0]
        return first if all(a == first for a in answers) else self.T.RES_UNKNOWN

    def _resolve_in(self, rel, cs, ns, name):
        grp = self.group_of(rel)
        chain, cur = [], ns
        while cur:
            chain.append(cur)
            cur = cur.rsplit(".", 1)[0] if "." in cur else ""
        chain.append("")
        gus = self.index()[2].get(grp, [])
        for level in chain:
            fq = f"{level}.{name}" if level else name
            got = self.resolve_fq(fq, grp)
            if got is not None:
                return got
            if level == "" and name in self.index()[5].get(grp, {}):
                specs = self.index()[5][grp][name]  # a `global using X = A.B;` of the group (one written in this
                if len(specs) != 1:                 # file is its import): before the usings' namespaces
                    return self.T.RES_UNKNOWN
                got = self.resolve_fq(next(iter(specs)), grp)
                return got if got is not None else self.T.RES_UNKNOWN
            uses = [u for u, glob, d in cs.get("usings", ()) if d == level and not glob]
            if level == "":
                uses += [u for u, glob, d in cs.get("usings", ()) if glob] + list(gus)
            hits = {}
            for u in dict.fromkeys(uses):
                t = self.lookup_type(f"{u}.{name}", grp)
                if t is not None:
                    hits[f"{u}.{name}"] = t
            if len(hits) > 1:
                return self.T.RES_UNKNOWN
            if hits:
                fq2, t = next(iter(hits.items()))
                return self._answer(t, fq2)
        return None

    def _qualified(self, rel, spec):
        """A written full name (an alias target, a `using static` type): fully qualified, then relative to the
        file's namespaces; RES_UNKNOWN when nothing (or more than one thing) answers."""
        T = self.T
        if not spec or "<" in spec or "::" in spec:
            return T.RES_UNKNOWN
        grp = self.group_of(rel)
        got = self.resolve_fq(spec, grp)
        if got is not None:
            return got
        outs = set()
        for ns in self.cs(rel).get("namespaces") or ():
            cur = ns
            while cur:
                g2 = self.resolve_fq(f"{cur}.{spec}", grp)
                if g2 is not None:
                    outs.add(g2)
                    break
                cur = cur.rsplit(".", 1)[0] if "." in cur else ""
        return next(iter(outs)) if len(outs) == 1 else T.RES_UNKNOWN

    def resolve_import(self, rel, imp):
        try:
            if getattr(imp, "kind", "") != "named":
                return self.T.RES_UNKNOWN
            return self._qualified(rel, str(getattr(imp, "spec", "") or ""))
        except Exception:  # noqa: BLE001
            return self.T.RES_UNKNOWN

    def submodule(self, module, attr):
        try:
            if not isinstance(module, str) or _NS_SEP not in module:
                return None
            grp, fq = module.split(_NS_SEP, 1)
            return self.resolve_fq(f"{fq}.{attr}", grp)
        except Exception:  # noqa: BLE001
            return None

    # -------------------------------------------------------------- the lookup_rules hooks
    def partials(self):
        """[[(rel, class index), ...], ...]: the parts of every partial type (one group, full name and arity)."""
        try:
            groups: dict = {}
            for rel in sorted(self.facts):
                ff, cs = self.facts[rel], self.cs(rel)
                fqns = cs.get("class_fqn", ())
                for i, c in enumerate(ff.classes):
                    if c.open and c.scope < 0 and i < len(fqns):
                        groups.setdefault((self.group_of(rel), fqns[i], len(c.type_params)), []).append((rel, i))
            return [v for _k, v in sorted(groups.items()) if len(v) > 1]
        except Exception:  # noqa: BLE001
            return []

    def static_imports(self, rel):
        """[Resolution, ...] of the file's `using static` types plus the group's `global using static` ones."""
        try:
            cs = self.cs(rel)
            specs = [u for u, _g, _d in cs.get("statics", ())] + list(self.index()[3].get(self.group_of(rel), []))
            return [self._qualified(rel, s) for s in dict.fromkeys(specs)]
        except Exception:  # noqa: BLE001
            return [self.T.RES_UNKNOWN]

    def extensions(self, rel, m):
        """[(rel, class index, this-type TypeRef), ...]: every extension method named m in the project."""
        try:
            return list(self.index()[4].get(m, ()))
        except Exception:  # noqa: BLE001
            return []


# ================================================================================================
# The reader entry point
# ================================================================================================

_CANARY = (b"using Shop.Data;\n"
           b"namespace Shop.Orders;\n"
           b"public class Svc : Base, IFoo\n"
           b"{\n"
           b"    private readonly IRepo _repo;\n"
           b"    public Svc(IRepo repo) { _repo = repo; }\n"
           b"    public void Run(Order o) { _repo?.Save(o); new Order(); Gen<int>(); }\n"
           b"}\n")


def _canary_ok() -> bool:
    T = _ir()
    ff = read_facts("canary.cs", "csharp", parse_bytes(_CANARY), _CANARY)
    k = ff.classes[0] if ff.classes else None
    run = next((s for s in ff.scopes if s.name == "Run"), None)
    ctor = next((s for s in ff.scopes if s.kind == "ctor"), None)
    cs = getattr(ff, "_cs", {})
    return (not T.validate_facts(ff) and k is not None and k.name == "Svc"
            and k.bases == (T.t_name("Base"), T.t_name("IFoo"))
            and [b.type for b in k.fields.get("_repo", ())] == [T.t_name("IRepo")]
            and run is not None and run.params.get("o") is not None and run.params["o"].type == T.t_name("Order")
            and [(s.member, s.recv, s.kind) for s in run.sites] == [
                ("Save", T.r_field(T.R_SELF, "_repo"), "member"),
                ("Order", T.r_classref(T.t_name("Order")), "ctor"), ("Gen", None, "bare")]
            and ctor is not None and ctor.line == 6
            and cs.get("namespaces") == ["Shop.Orders"] and cs.get("usings") == [("Shop.Data", False, "")])


class CsReader:
    """The typed_calls Reader for C# (make_reader(spec))."""
    version = READER_VERSION

    def __init__(self, spec):
        self.spec = spec
        self.lang = str(getattr(spec, "name", "") or "csharp")

    def canary(self) -> bool:
        try:
            return bool(_canary_ok())
        except Exception:  # noqa: BLE001 — no grammar, a changed grammar: the language is disabled
            return False

    def facts(self, rel, source):
        got = _tree_of(rel, source)
        if got is None:
            return None
        root, data = got
        return read_facts(rel, self.lang, root, data)

    def binder(self, ctx):
        return CsBinder(ctx)


def make_reader(spec):
    """The typed_calls reader entry point (LanguageSpec.reader == "receiver_facts_csharp")."""
    return CsReader(spec)
