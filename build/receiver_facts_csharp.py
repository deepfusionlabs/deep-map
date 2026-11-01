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
            engine's one node; a `params` parameter makes the arity open, (required, -1)); flags "static",
            "abstract" (no body: an interface / abstract / partial-declaration / extern member). A return type of
            Task / ValueTask / IAsyncEnumerable is unknown (an `await` result is never typed).
  bindings  params: `T x` (an untyped lambda parameter: no type; a `params` parameter, which this grammar writes
            outside any `parameter` node, is one too). locals: `T x = ...` -> ann (a C# local's static
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
            declaration holding the directive; "" = the compilation unit)] (every directive TEXT as written, blanks
            removed; a leading `global::` is dropped in the compilation unit and on a `global using`, where every
            directive is absolute anyway, and KEPT on one written inside a namespace declaration, where it is what
            makes the name absolute: R44, CsBinder.frame_meanings), class_ns / class_fqn (per class index),
            ext [(class index, method, this-type)], error (a top-level parse error). A `using X = A.B;` alias is an
            Import("named") instead, and a `global using X = A.B;` one is also in galias [(X, A.B)] (every file of
            the project sees it); every alias is in aliases [(X, A.B, the index of the namespace declaration holding
            it (0: the compilation unit), global?)]. cvis {class index: the namespace declarations enclosing it, the
            compilation unit first, each (its full name, its own non-global using namespaces): the lookup order of a
            simple type name written there}; cvalias {class index: aligned with cvis, each declaration's own
            non-global aliases ((X, A.B), ...) sorted: an alias is in effect only inside its declaration}. For the
            core's C# rules (typed_calls lookup_rules overloads / virtual_dispatch / iface_dispatch): mods
            {(method, its MethodFacts line): the modifier words (an explicit interface implementation has none)},
            explicit {(method, line): the TypeRef of an explicit interface implementation's specifier (`void
            IRun.Run()`: IRun; `void IRepo<Order>.Save(..)`: IRepo<Order>); None when two members of that name share
            the line; absent: no explicit implementation}, convops [the class indexes declaring a conversion operator
            (`implicit operator` / `explicit operator`)] and mparams {(method, line):
            (((TypeRef | None, passing "" | ref | out | in | this | "ref readonly", optional), ...), variadic)} (None:
            two members of that name on one line; `ref readonly`, C# 12, takes a value argument, unlike `ref`), args
            [per scope: per site, the arguments lowered like receivers (a literal with its precise type: `5L` long) or
            None (named / ref / out / in arguments)], variant {interface class index:
            per type parameter "" | in | out}, reassigned [per scope: the names its span may rebind — an
            assignment's target (compound, parenthesized, every element of a deconstruction `(x, y) = ...`), a ref /
            out argument, the operand of any other `ref x` (a ref local or return may write x), a `++` / `--`
            operand], sealed [the class indexes declared sealed or static]; for comparing
            signatures across a generic base list (rule overloads): msig {(method, line): per parameter its written
            type as a tagged tuple (`sigt`: a class / method type parameter tagged as one, names kept as written)},
            tporder {class index: its own type parameters in order}, tpkind {class index: {type parameter: "s"
            (constrained `struct` / `unmanaged`: `T?` is Nullable<T>), "?" (constrained by a type parameter) or "a"
            (anything else, or nothing: `T?` an annotation, C# 9+)}}, bsig {class index: per base-list entry, aligned
            with ClassFacts.bases, its tagged tuple}. R22: marity {(method, line): its own type-parameter count
            (`Save<T>` 1: arity is part of a C# signature)}, msigp {(method, line): () or (the `params` parameter's
            sigt,)} and mparamsp {(method, line): () or ((TypeRef | None, "", False),)}: the signature is msig + msigp,
            the parameter list mparams[0] + mparamsp (msig / mparams[0] stay aligned without it; `params` is no part of
            a signature, the array type is). R24: pp_usings / pp_statics / pp_aliases / pp_galias (the indexes into
            usings / statics / aliases / galias of a directive written inside a `#if` / `#elif` / `#else` branch: it may
            or may not be compiled, so it is possible, never certain; every directive stays in its list) and cvpp {class
            index: aligned with cvis, does that declaration hold such a directive: its using sets are not complete}.
            R28: filelocal [the class indexes declared with C# 11's `file` modifier]. R29: frames [one per namespace
            declaration in source order, the compilation unit first: (full name, enclosing frame index (-1 for the
            compilation unit), usings, pp usings, aliases, pp aliases, statics, pp statics), each a sorted tuple of the
            NON-global directives written directly in it, a branch's only in its pp_ tuple] and cframe {class index: the
            frame directly enclosing it (a nested class: its outer class's)}.

THE BINDER (CsBinder: C# name lookup over every .cs file's facts; Phase B asks it, never the engine)
  A project type is a top-level type declared in a C# file, under its full name (namespace + name) AND its own generic
  arity (R30: `Result` and `Result<T>` are two types). A file's types belong to every compilation that holds it
  (_compilers: its own .csproj group, the nearest directory above it holding a *.csproj, unless that project's
  <Compile Remove> takes it out; every project whose <Compile> link reaches it; a file no project is known to compile
  stays in its own group, "" for a tree with no .csproj): a full name declared in the caller's own group wins; else
  exactly one other group (or one definition) must declare it; else it is ambiguous (unknown). Two non-partial types
  of one full name and arity in one group are ambiguous; partial parts are one type. A C# 11 `file` type (R28) is
  found only from its own file (beside another type of its full name there: unknown).
  * in_group(rel) (R25): does rel's own project compile it? csharp_msbuild.compile_state of the group's reading: True,
    False (a readable, certain <Compile Remove>, a default exclude a file sets, an old-style project listing it
    nowhere), None (maybe). A False file's global directives and types hold for no compilation of its group; a None
    file's global usings are possible, its global aliases and statics unknown, its types still count.
  * cu_reading(rel): (token, whole), the compilation-unit identity of one file. token is its group and whole True when
    its group's project(s) are all that compile it, as far as this pass reads; a file another project also compiles
    (under a <Compile Include> of a project other than its group: MsbUsings.links, read from every .csproj of the tree),
    one its own project takes out (in_group False), or one in no project's directory while the tree holds a .csproj
    gets a per-file token and whole False: its real usings are not its group's. A tree with no .csproj keeps the plain
    reading (group "", whole).
  * msbuild(group): the `global using` directives MSBuild adds to that group's compilation, read from its .csproj,
    Directory.Build.props / .targets and SDKs by build/csharp_msbuild.py (msbuild_raw: an MsbUsings: certain
    namespaces, possible ones, aliases, alias names it may define, statics, complete?), its `complete` cleared when a
    file of the group that may or may not be compiled writes a global directive (`compile_unread`) or a file of it
    writes one in a preprocessor branch (`pp_global`). Its certain namespaces join the group's `global using`s, its
    aliases the group's global aliases (an alias it may define: target "" = unknown), its statics the group's `global
    using static`s; its possible namespaces are index slot 6, with the global usings of a maybe-compiled file and of a
    branch.
  * frame_meanings(rel, fidx) / decl_meanings(rel, class index) / using_meaning(rel, fidx, text, kind) (R44): what
    each directive MEANS, the absolute full name it names or None (unknown). C# resolves a directive's name as if its
    own declaration had no using directives: one of the compilation unit, or a `global::` text, is absolute; one
    written inside a namespace declaration D (a block, or after a file-scoped namespace) is looked up from D's
    namespace outward, its first segment H per level L (D's namespace, each implicit dotted parent, each enclosing
    declaration's namespace, the global namespace; `namespace A.B { using U; }`: A.B.U, then A.U, then U), the
    first that exists winning (_directive_walk: a project namespace L.H, or a project type L.H for a using static or
    an alias target, exists; an alias H of an enclosing declaration, or of the compilation unit at the global level,
    may come first: unknown; a level whose namespace is capable (R27 / R32: external code may declare L.H there):
    unknown; at the global level a using names its text, a dotted using static only when H is surely a global
    namespace (System, Microsoft, the head of a certain compilation-unit using), an alias target by _alias_meaning
    (members only)). Two texts may have one meaning, and one text two meanings in two declarations: the core compares
    meanings (typed_calls cs_scope_levels), never texts; every lookup here reads them too. Assumptions: the code
    compiles (a using namespace's head is never a TYPE an outer using imports: CS0138), a namespace or type some
    project file declares exists for every project (ProjectReference is not read: a project that does not reference
    the declaring one sees another meaning; the older F3 residual of types, now also of namespaces), and no `extern
    alias` directive is read (`X::Y` is unknown, but `X.Y` with X an extern alias reads as the namespace X: rare,
    an assembly aliased against another).
  * resolve_free(rel, name, arity): C#'s namespace-or-type-name lookup, once per namespace DECLARATION of the file
    that directly holds a type (the compilation unit for top-level code or a file without types; R29: a block's usings
    and aliases never apply to another block of the same name or to a dotted child declared separately), all
    answers alike, else unknown. Per declaration innermost out: its namespace's members (a type of that name and
    arity, or a namespace at arity 0; both: unknown; a type _CS_KNOWN_NS lists there: None; nothing in a capable
    namespace: unknown, R27), its aliases (arity 0; one in a preprocessor branch: unknown), its using-namespace and
    `using static` directives together, by MEANING (R44; one unknown: every name reaching that level is unknown; one
    project type: it; two holders: unknown; a nested type of a project static
    type, or a static that may supply one — external, unread, with a base list, in a branch — : unknown, R23; a
    project type a branch's using may supply: unknown, R24), then its implicit dotted parents' members; last the
    compilation unit: the global namespace's members, its aliases with the group's global ones, its usings with every
    `global using` of the group (MSBuild's certain ones; a type only a possible one holds: unknown). A namespace
    answers namespace(token); no project type or namespace answers None (the core then tries the LanguageSpec
    builtins; an unbound name types nothing). arity None (a spelling of unknown arity): any hit is unknown.
    A namespace declaration's using that is not transparent (below) may import an external TYPE of the name, which C#
    finds before anything further out: once one is passed, a project type, or an alias, found at a later level (an
    implicit dotted parent, an enclosing declaration, the global namespace, the compilation unit) answers unknown
    (a block's `using System.Timers;` over a parent namespace's project type Timer: C# binds System.Timers.Timer).
    A project NAMESPACE found there still answers: the name is then a qualifier (`Acme.Core.Order`), and an
    external type of that name would have to hold the rest of the qualified name as nested types for the code to
    compile. Assumption: no external type named like a project namespace holds such nested types. R44: transparent is
    read on each using's MEANING (an unknown meaning is not transparent, but its level answers unknown before shadow
    matters); a directive's own meaning is resolved without shadow (C# resolves it ignoring its declaration's
    directives, and an outer using imports types only, never a using's namespace head in code that compiles).
  * alias_in_effect(rel, class index, name, arity) / resolve_scoped(...): the same lookup for a name written in that
    class's declaration (None: the compilation unit's), per namespace declaration innermost out (its members, ITS
    aliases, its usings and statics, its implicit dotted parents' members), then the global namespace (members, the
    file's and the group's aliases, the usings): an alias applies only inside its declaration and at arity 0, and
    loses to a nearer member or using. ("alias", target) / ("member", Resolution) / ("none",) / ("unknown",): a
    capable enclosing namespace (R27), a static that may supply the name, a branch's directive, or a namespace
    declaration's non-transparent using passed before an alias of the name or a project type further out is unknown
    (it may hold the name; a project namespace still answers, as in resolve_free). resolve_scoped
    gives the Resolution (an alias's target resolved in its own declaration's namespace chain, members only, as C#
    resolves it without that declaration's own directives; an enclosing declaration that writes any directive, met
    before a member is found, may supply its first segment: unknown; None: no alias in effect and no project type).
    R44: alias_in_effect gives an alias written inside a namespace declaration as ("alias", its target's absolute
    MEANING) or ("unknown",), never the relative text (a generic target keeps its text); a `global::` target is
    absolute.
  * ns_info(), ext_root(ns), capable(ns), transparent(u): which namespaces an external assembly may share. type_ns =
    those holding a top-level project type directly; used_ext = every imported namespace (usings, global, MSBuild)
    holding none and no ancestor of a type_ns entry (R44: a using inside a namespace declaration counts with its text
    and every namespace it may MEAN, to a fixed point: _relative_used); package_roots = the first segment
    (lower-cased) of every package id any .csproj of the tree names; owned_roots = the first segment of every
    project's AssemblyName, RootNamespace and project file name (MsbUsings.names). ext_root(N), THE one concept
    (R32): N's root is System / Microsoft, is owned by no project (exact case; a tree with no .csproj owns nothing),
    or is shared with a package (an id's first segment, case-insensitive, starts with the root or is a prefix of it:
    `AcmeCorp.Core` shares `Acme`).
    capable(N): ext_root(N) or N is related to (equal to, an ancestor or a descendant of) a used_ext namespace: a
    namespace the project declares types in may hold external types too, so holding a project type decides nothing.
    transparent(U): U holds a project type, not ext_root(U), related to no used_ext namespace, and its first segment
    holds project types in two or more namespaces (one alone may be a library's namespace the project extends): it
    supplies only project types. Assumption (R21 / R32): a package whose id shares nothing with the namespace root it
    declares, under a root the repository's own assemblies are named after, stays unseen.
  * resolve_import(rel, imp): a `using X = A.B.C;` alias -> the type or namespace A.B.C, resolved where the alias is
    written (R44, _import_scoped: the compilation unit's and a `global using` alias absolutely, a namespace
    declaration's by _alias_target; every declaration writing it alike, else unknown); an alias the table does not
    name: fully qualified, or relative to the file's namespaces (one answer, else unknown).
  * submodule(token, attr, arity): the type or namespace `<namespace>.attr`.
  * partials() (R31): the parts of every partial type per COMPILING project (one full name, one generic arity), for
    the core's `partials` rule; a part two projects compile into their own types (one that would join two groups)
    leaves every group it would join: partials_unmerged() lists them (the core reads a lookup on them as open).
    static_imports(rel): the file's `using static` types (R44: each by its MEANING) and the group's `global using
    static` (MSBuild's included; one MSBuild may add unread, one in a branch, or one of unknown meaning: an unknown
    entry). extensions(rel, m): every extension method
    named m in the project's compiled files [(rel, class index, this-type)].
  Project references are not read for types (a full name another group declares counts whether or not this project
  references it; R44: nor for namespaces); global usings a NuGet package's props add are not read (R45: the core's
  U3 compares two complete readings of different projects only when neither has a package: MsbUsings.pkgsig empty
  over the readable ProjectReference closure); an alias the tree does not hold, an unreadable directory: unknown. One
  csharp_msbuild.tree is shared by every group of a binder (a shared Directory.Build.props is parsed once).

facts() returns None for an unreadable file and may raise on a reader bug (the core then gives that file no facts
and goes on); canary() and the binder never raise. Imports only the stdlib at import time (typed_calls,
csharp_msbuild, tree-sitter, the C# grammar and graphify_extract are imported lazily; the grammar is OPTIONAL —
graphifyy's own dependency, never declared by DeepMap: without it the canary fails and typed_calls disables csharp).
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
                   "sealed": [], "msig": {}, "tporder": {}, "bsig": {}, "galias": [], "cvis": {}, "tpkind": {},
                   "aliases": [], "cvalias": {}, "explicit": {}, "convops": [], "marity": {}, "msigp": {},
                   "mparamsp": {}, "pp_usings": set(), "pp_statics": set(), "pp_aliases": set(), "pp_galias": set(),
                   "cvpp": {}, "filelocal": [], "frames": [], "cframe": {}}
        self.frames: list = [("", [])]    # namespace declarations (the compilation unit first): (full name, usings)
        self.falias: list = [[]]          # per frame: its non-global `using X = A.B;` aliases [(X, A.B)]
        self.fstack: list = [0]           # the frames enclosing the declaration being read, outermost first
        self.fparent: list = [-1]         # per frame: the frame enclosing it (-1: the compilation unit's)
        self.fmore: list = [{"ppu": [], "ppa": [], "st": [], "ppst": []}]   # per frame: R24 / R29 detail
        self.in_pp = 0                    # > 0 inside a #if / #elif / #else branch (R24)
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

    @staticmethod
    def params_tail(pl):
        """(type node, name node) of a parameter list's `params` parameter, or None: this grammar writes it outside
        any `parameter` node (`params` then the type and the name as fields of the list itself)."""
        if pl is None:
            return None
        kids = pl.children
        for i, c in enumerate(kids):
            if not c.is_named and c.type == "params":
                typ = nm = None
                for j in range(i + 1, len(kids)):
                    f = pl.field_name_for_child(j)
                    if f == "type" and typ is None:
                        typ = kids[j]
                    elif f == "name" and nm is None:
                        nm = kids[j]
                return (typ, nm) if nm is not None else None
        return None

    def params(self, pl, tps):
        """[(name, TypeRef | None, is_this, optional, is_params)] of a parameter list, the `params` parameter last
        (params_tail)."""
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
        tail = self.params_tail(pl)
        if tail is not None:
            typ, nm = tail
            out.append((self.text(nm), None if typ is None else self.typeref(typ, tps), False, False, True))
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
            if how == "ref" and "readonly" in mods:
                how = "ref readonly"               # C# 12: a value argument binds, as to `in` (`ref` refuses one)
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
        self.cs["cvalias"] = {idx: tuple(tuple(sorted(set(self.falias[f]))) for f in chain)
                              for idx, chain in self.cs["cvis"].items()}     # aligned with cvis
        self.cs["convops"] = sorted(set(self.cs["convops"]))
        frames = []
        for f, (full, uses) in enumerate(self.frames):
            mo = self.fmore[f]
            ppu, ppa = set(mo["ppu"]), set(mo["ppa"])
            frames.append((full, self.fparent[f], tuple(sorted(set(uses) - ppu)), tuple(sorted(ppu)),
                           tuple(sorted(set(self.falias[f]) - ppa)), tuple(sorted(ppa)),
                           tuple(sorted(set(mo["st"]) - set(mo["ppst"]))), tuple(sorted(set(mo["ppst"])))))
        self.cs["frames"] = frames                       # R29: every namespace declaration, the CU first
        self.cs["cvpp"] = {idx: tuple(bool(frames[f][3] or frames[f][5] or frames[f][7]) for f in chain)
                           for idx, chain in self.cs["cvis"].items()}           # R24: aligned with cvis
        self.cs["cvis"] = {idx: tuple((self.frames[f][0], tuple(sorted(set(self.frames[f][1])))) for f in chain)
                           for idx, chain in self.cs["cvis"].items()}
        for k in ("pp_usings", "pp_statics", "pp_aliases", "pp_galias"):
            self.cs[k] = frozenset(self.cs[k])
        self.cs["filelocal"] = sorted(self.cs["filelocal"])
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
        self.fparent.append(self.fstack[-1])
        self.frames.append((full, []))
        self.falias.append([])
        self.fmore.append({"ppu": [], "ppa": [], "st": [], "ppst": []})
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
                self.in_pp += 1                          # R24: a directive here is possible, never certain
                try:
                    self.items([k for k in c.named_children if k != _field(c, "condition")], cur)
                finally:
                    self.in_pp -= 1
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
        pp, f, glob = self.in_pp > 0, self.fstack[-1], "global" in anon
        if spec.startswith("global::") and (glob or f == 0):
            spec = spec[len("global::"):]        # the compilation unit's directives are absolute anyway (R44: one
        #                                          written inside a namespace declaration keeps it: absolute there)
        if alias is not None:
            local = self.text(alias)
            self.imports.setdefault(local, []).append(T.Import(local, "named", spec, spec.rsplit(".", 1)[-1],
                                                               _line(c)))
            if pp:
                self.cs["pp_aliases"].add(len(self.cs["aliases"]))
            self.cs["aliases"].append((local, spec, f, glob))
            if glob:
                if pp:
                    self.cs["pp_galias"].add(len(self.cs["galias"]))
                self.cs["galias"].append((local, spec))  # every file of the project sees it
            else:
                self.falias[self.fstack[-1]].append((local, spec))   # in effect only in this declaration
                if pp:
                    self.fmore[f]["ppa"].append((local, spec))
        elif "static" in anon:
            if pp:
                self.cs["pp_statics"].add(len(self.cs["statics"]))
            self.cs["statics"].append((spec, glob, decl_ns))
            if not glob:
                self.fmore[f]["ppst" if pp else "st"].append(spec)
        else:
            if pp:
                self.cs["pp_usings"].add(len(self.cs["usings"]))
            self.cs["usings"].append((spec, glob, decl_ns))
            if not glob:
                self.frames[self.fstack[-1]][1].append(spec)
                if pp:
                    self.fmore[f]["ppu"].append(spec)

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
        self.cs["cframe"][idx] = self.fstack[-1]         # R29: the declaration directly enclosing it
        if "file" in mods:
            self.cs["filelocal"].append(idx)             # R28: C# 11 `file`: visible in this file only
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
                if t == "conversion_operator_declaration":
                    self.cs["convops"].append(ci)      # a user-defined conversion from / to this type
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
        tail = self.params_tail(pl)                    # R22: msig + msigp / mparams[0] + mparamsp is the signature
        self.note_member("msigp", name, line, () if tail is None else (self.sigt(tail[0], tps, morder),))
        self.note_member("mparamsp", name, line, () if tail is None else (
            (None if tail[0] is None else self.typeref(tail[0], mtps), "", False),))
        self.note_member("marity", name, line, len(morder))      # R22: a method's own generic arity
        spec_n = _child(m, "explicit_interface_specifier")             # `void IRun.Run()`: no modifiers either
        if spec_n is not None:
            self.note_member("explicit", name, line, self.typeref(next(iter(spec_n.named_children), None), mtps))
        if (name, line) in self.cs["explicit"] and self.cs["mods"].get((name, line)) is None:
            self.cs["explicit"][(name, line)] = None      # two members of that name on one line: which is unknown
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
# The roots of the namespaces both .NET reference packs declare and a project most often extends: an enclosing
# namespace under them may hold external types (CsBinder.capable), a using of one may import external types
# (CsBinder.transparent never holds for it).
CS_EXT_ROOTS = frozenset({"System", "Microsoft"})
_MSB_FIELDS = ("certain", "possible", "aliases", "alias_unknown", "statics", "complete", "reasons")


def _msb_unread(why):
    """The empty, incomplete MSBuild reading (csharp_msbuild's when importable, else an equal stand-in)."""
    try:
        return getattr(_sibling("csharp_msbuild"), "_unread")(why)
    except Exception:  # noqa: BLE001
        from collections import namedtuple
        return namedtuple("MsbUsings", _MSB_FIELDS)(frozenset(), frozenset(), {}, frozenset(), (), False, (why,))


_NO_WALK_DIRS = frozenset({".git", ".vs", "node_modules", "bin", "obj"})     # csharp_msbuild's walk skips the same
_GLOBAL_USING = re.compile(r"\bglobal\s+using\s+(static\s+)?(?:(\w+)\s*=\s*)?(?:global::)?([\w.]*)")
_FRAME_LEN = 8                    # a reader `frames` entry (R29)
_NS_ROUNDS = 16                   # R44: ns_info's fixed point over what relative usings may mean (_relative_used)


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
        self._msb: dict = {}
        self._raw: dict = {}
        self._nsinfo = None
        self._tree = None
        self._links = None
        self._linked: dict = {}
        self._ing: dict = {}
        self._gflags = None
        self._parts = None
        self._owned = frozenset()

    # -------------------------------------------------------------- indexes
    def cs(self, rel) -> dict:
        ff = self.facts.get(rel)
        got = getattr(ff, "_cs", None) if ff is not None else None
        return got if isinstance(got, dict) else {}

    def project_tree(self):
        """The one csharp_msbuild.tree of this binder (its parsed files and listings are shared across groups)."""
        if self._tree is None:
            self._tree = _sibling("csharp_msbuild").tree(self.root)
        return self._tree

    def project_dirs(self) -> tuple:
        """Every directory of the tree holding a *.csproj, a group with no C# file included; () when unreadable."""
        try:
            return tuple(self.project_tree().project_dirs())
        except Exception:  # noqa: BLE001
            return ()

    def msbuild_raw(self, grp):
        """csharp_msbuild.read_group of the .csproj(s) in directory grp (an MsbUsings), memoised; never raises (an
        error: the empty, incomplete reading)."""
        g = str(grp or "")
        if g not in self._raw:
            got = None
            try:
                got = _sibling("csharp_msbuild").read_group(self.root, g, files=self.project_tree())
                if not all(hasattr(got, f) for f in _MSB_FIELDS):
                    got = None
            except Exception:  # noqa: BLE001
                got = None
            self._raw[g] = got if got is not None else _msb_unread("error")
        return self._raw[g]

    def msbuild(self, grp):
        """The `global using` directives MSBuild adds to group grp's compilation (msbuild_raw), its `complete` also
        cleared when a file of the group the project may or may not compile (in_group None) writes a `global using` /
        alias / static (`compile_unread`, R25), or a file of it writes one inside a preprocessor branch
        (`pp_global`, R24): the compilation's whole using set is then not known. Never raises."""
        g = str(grp or "")
        if g not in self._msb:
            raw = self.msbuild_raw(g)
            extra = self.group_flags().get(g, frozenset())
            try:
                got = raw._replace(complete=False, reasons=tuple(sorted(set(raw.reasons) | extra))) if extra else raw
            except Exception:  # noqa: BLE001
                got = _msb_unread("error")
            self._msb[g] = got
        return self._msb[g]

    def group_flags(self) -> dict:
        """{group: {"compile_unread", "pp_global"}} (msbuild): one pass over the files' tables."""
        if self._gflags is None:
            out: dict = {}
            try:
                for rel in sorted(self.facts):
                    cs = self.cs(rel)
                    if not cs:
                        continue
                    g = self.group_of(rel)
                    glob = any(x[1] for x in cs.get("usings", ())) or any(x[1] for x in cs.get("statics", ())) \
                        or bool(cs.get("galias"))
                    if not glob:
                        continue
                    ig = self.in_group(rel)
                    if ig is None:
                        out.setdefault(g, set()).add("compile_unread")
                    pp = self._pp(cs)
                    if ig is not False and (any(i in pp["usings"] for i, x in enumerate(cs.get("usings", ())) if x[1])
                                            or any(i in pp["statics"] for i, x in enumerate(cs.get("statics", ()))
                                                   if x[1]) or pp["galias"]):
                        out.setdefault(g, set()).add("pp_global")
            except Exception:  # noqa: BLE001
                out = {}
            self._gflags = {g: frozenset(v) for g, v in out.items()}
        return self._gflags

    @staticmethod
    def _pp(cs) -> dict:
        """R24: the reader's pp_* index sets of one file's table (empty for a reader without them)."""
        out = {}
        for k in ("usings", "statics", "aliases", "galias"):
            got = cs.get(f"pp_{k}")
            out[k] = frozenset(i for i in got if isinstance(i, int)) if isinstance(got, (set, frozenset, list,
                                                                                        tuple)) else frozenset()
        return out

    def in_group(self, rel):
        """R25: does rel's own project compile it? True; False (a readable, certain <Compile Remove> or default
        exclude takes it out); None (maybe: csharp_msbuild.compile_state). True for a file in no project's directory.
        Never raises (an error: None)."""
        key = str(rel).replace("\\", "/")
        if key not in self._ing:
            try:
                grp = self.group_of(key)
                if grp not in self.project_dirs():
                    self._ing[key] = True
                else:
                    got = _sibling("csharp_msbuild").compile_state(self.msbuild_raw(grp), grp, key)
                    self._ing[key] = got if got in (True, False) else None
            except Exception:  # noqa: BLE001
                self._ing[key] = None
        return self._ing[key]

    def _compilers(self, rel) -> frozenset:
        """The groups whose compilation holds file rel (types index, partials): every project whose <Compile> link
        reaches it, its own group unless that project's Compile items take it out (in_group False). A file no
        project is known to compile stays in its own group (a tree with no .csproj: group "")."""
        key = str(rel).replace("\\", "/")
        grp = self.group_of(key)
        files, dirs = self._link_index()
        low = key.lower()
        owners = set(files.get(low, ()))
        cur = low
        while cur:
            cur = posixpath.dirname(cur)
            owners |= dirs.get(f"{cur}/" if cur else "", set())
        removed = grp in self.project_dirs() and self.in_group(key) is False
        if not removed and (grp in self.project_dirs() or not owners):
            owners.add(grp)
        return frozenset(owners)

    def index(self):
        if self._idx is None:
            types, nss, gusing, gstatic, ext, galias = {}, set(), {}, {}, {}, {}
            lext: dict = {}                             # a `file` class's extension methods (R28)
            gpossible: dict = {}
            byname: dict = {}
            for rel in sorted(self.facts):
                ff, cs = self.facts[rel], self.cs(rel)
                if not cs:
                    continue
                g = self.group_of(rel)
                comp = self._compilers(rel)
                if g not in gpossible:
                    gpossible[g] = frozenset()
                if not comp:
                    continue                            # compiled by no project (a readable <Compile Remove>)
                ig = True if g not in self.project_dirs() else self.in_group(rel)
                pp = self._pp(cs)
                for ns in cs.get("namespaces", ()):
                    parts = ns.split(".")
                    for i in range(1, len(parts) + 1):
                        nss.add(".".join(parts[:i]))
                fqns, tpo = cs.get("class_fqn", ()), cs.get("tporder") or {}
                fl = set(cs.get("filelocal") or ())
                for i, c in enumerate(ff.classes):
                    if c.outer < 0 and c.scope < 0 and i < len(fqns):
                        ar = len(tpo.get(i, ())) if isinstance(tpo.get(i), tuple) else len(c.type_params)
                        for cg in sorted(comp):
                            types.setdefault((fqns[i], ar), []).append((cg, rel, i, bool(c.open), i in fl))
                        byname.setdefault(fqns[i], set()).add(ar)
                for i, name, tref in cs.get("ext", ()):
                    (lext if i in fl else ext).setdefault(name, []).append((rel, i, tref))
                if ig is False:
                    continue                            # its global directives hold for no compilation of g
                sure = ig is True
                for k, (u, glob, _d) in enumerate(cs.get("usings", ())):
                    if glob:
                        if sure and k not in pp["usings"]:
                            gusing.setdefault(g, []).append(u)
                        else:
                            gpossible[g] = gpossible[g] | {u}
                for k, (u, glob, _d) in enumerate(cs.get("statics", ())):
                    if glob:
                        gstatic.setdefault(g, []).append(u if sure and k not in pp["statics"] else "")
                for k, (name, spec) in enumerate(cs.get("galias", ())):
                    galias.setdefault(g, {}).setdefault(name, set()).add(
                        spec if sure and k not in pp["galias"] else "")
            for g in sorted(gpossible):                 # what MSBuild adds to each group's compilation
                mb = self.msbuild(g)
                gusing.setdefault(g, []).extend(sorted(mb.certain))
                gstatic.setdefault(g, []).extend(mb.statics)
                for name, spec in sorted(dict(mb.aliases).items()):
                    galias.setdefault(g, {}).setdefault(name, set()).add(spec)
                for name in sorted(mb.alias_unknown):  # an alias it may define: target "" (unknown everywhere)
                    galias.setdefault(g, {}).setdefault(name, set()).add("")
                gpossible[g] = gpossible[g] | frozenset(mb.possible)
                for r in self.linked_in(g)[0]:          # a file it links in (maybe under a condition): what its
                    l_ns, l_st, l_al = self._globals_of(r)              # `global using`s say may hold, never surely
                    gpossible[g] |= l_ns
                    if l_st:
                        gstatic.setdefault(g, []).append("")
                    for name in l_al:
                        galias.setdefault(g, {}).setdefault(name, set()).add("")
            self._idx = (types, frozenset(nss), gusing, gstatic, ext, galias, gpossible,
                         {k: frozenset(v) for k, v in byname.items()}, lext)
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

    def _link_index(self):
        """({lower-cased file: {groups linking it}}, {lower-cased directory prefix: {groups}}) over every project's
        MsbUsings.links; "" (anywhere) is a prefix of everything."""
        if self._links is None:
            files, dirs = {}, {}
            for g in self.project_dirs():
                for lk in getattr(self.msbuild_raw(g), "links", ()):
                    low = str(lk).lower()
                    (dirs if not low or low.endswith("/") else files).setdefault(low, set()).add(g)
            self._links = (files, dirs)
        return self._links

    def _compiled_elsewhere(self, rel, grp) -> bool:
        """Does a project other than rel's group compile rel (a link reaching it), does rel's own project leave it out
        (in_group False), or does no project's directory hold it while the tree holds a .csproj (then one of them
        may, through a link this pass cannot read)?"""
        projects = self.project_dirs()
        if not grp and projects and "" not in projects:
            return True
        if grp in projects and self.in_group(rel) is False:
            return True
        files, dirs = self._link_index()
        low = rel.lower()
        owners = set(files.get(low, ()))
        cur = low
        while cur:
            cur = posixpath.dirname(cur)
            owners |= dirs.get(f"{cur}/" if cur else "", set())
        return bool(owners - {grp})

    def _globals_of(self, rel) -> tuple:
        """(namespaces, any `global using static`?, alias names) of the `global using` directives file rel writes: the
        reader's table when it read rel, else the file's text (a linked file the sync does not extract, in vendor/ or
        another skipped directory). Unreadable: nothing, which linked_in's caller marks incomplete."""
        cs = self.cs(rel) if rel in self.facts else None
        if cs:
            return (frozenset(u for u, glob, _d in cs.get("usings", ()) if glob),
                    any(glob for _u, glob, _d in cs.get("statics", ())), sorted(n for n, _s in cs.get("galias", ())))
        try:
            with open(os.path.join(self.root, rel), "rb") as fh:
                text = fh.read(4 * 1024 * 1024).decode("utf-8", "replace")
        except OSError:
            return frozenset(), False, []
        hits = _GLOBAL_USING.findall(text)
        return (frozenset(ns for st, al, ns in hits if ns and not st and not al), any(st for st, _a, _n in hits),
                sorted({al for _s, al, _n in hits if al}))

    def linked_in(self, grp) -> tuple:
        """(the .cs files outside group grp that its project compiles, repo-relative and sorted, every one found?):
        its <Compile Include> links (MsbUsings.links: an exact file, or all a directory prefix holds; a link never
        names the project's own directory). What those files say at assembly level (a `global using`, a friend) is the
        project's own. False: a link with no place ("": a computed or absolute path), out of the tree (".."), or to a
        file the tree does not hold (generated before the compile), so it may compile anything. Never raises (an
        error: ((), False)); bin / obj are not walked (a build's output is no input of the graph)."""
        key = str(grp or "")
        if key not in self._linked:
            try:
                found, whole = set(), True
                for lk in sorted(str(x) for x in getattr(self.msbuild_raw(key), "links", ())):
                    path = os.path.join(self.root, lk)
                    if not lk:
                        whole = False
                    elif lk.endswith("/"):
                        for cur, dirs, names in os.walk(path):
                            dirs[:] = sorted(d for d in dirs if d.lower() not in _NO_WALK_DIRS)
                            base = os.path.relpath(cur, self.root).replace(os.sep, "/")
                            found.update(posixpath.join(base, n) for n in names if n.lower().endswith(".cs"))
                    elif os.path.isfile(path):
                        found.add(lk)
                    else:
                        whole = False
                self._linked[key] = (tuple(sorted(found)), whole)
            except Exception:  # noqa: BLE001
                self._linked[key] = ((), False)
        return self._linked[key]

    def cu_globals(self, rel):
        """(certain `global using` namespaces, `global using static` specs ("" unknown), {global alias: {targets}}
        ("" unknown), namespaces that may be imported unseen) of the compilation(s) holding rel: THE rule (typed_calls'
        cs_group_tables asks it for such a file). An ordinary file: its group's (index: every file the project
        compiles, a file it may not compile and a directive in a preprocessor branch as possible / unknown, MSBuild's
        reading, and what the project links in as possible / unknown). A file some other project may compile, or one
        its own project leaves out (_compiled_elsewhere; a file in no project's directory is one) is compiled by each
        project whose link reaches it (and by its own, when its directory is a project's and it does not leave it
        out): only what EVERY one of them gives is certain, the rest possible (namespaces) or unknown (aliases,
        statics), with the file's own global aliases in effect everywhere. No project known to reach it: its group's
        files (group "" is no compilation) give nothing certain. A compilation unit is the same text in each
        compilation, so this is what holds in all of them."""
        key = str(rel).replace("\\", "/")
        grp = self.group_of(key)
        idx = self.index()

        def view(g):
            return (frozenset(idx[2].get(g, [])), frozenset(idx[3].get(g, [])), idx[5].get(g, {}),
                    frozenset(idx[6].get(g, ())))
        if not self._compiled_elsewhere(key, grp):
            gus, gst, gal, gpo = view(grp)
            return list(idx[2].get(grp, [])), list(idx[3].get(grp, [])), gal, gpo
        own = self.cs(key) or {}
        pp = self._pp(own)
        mine: dict = {}
        for k, (name, spec) in enumerate(own.get("galias", ())):
            mine.setdefault(name, set()).add("" if k in pp["galias"] else spec)
        owners = self._owners(key, grp)
        views = [view(g) for g in sorted(owners)] or [view(grp)]
        sure = frozenset.intersection(*[v[0] for v in views]) if owners else frozenset()
        maybe = frozenset().union(*[v[0] | v[3] for v in views]) - sure
        stat = sorted(frozenset().union(*[v[1] for v in views]))
        if not owners or any(v[1] != views[0][1] for v in views):
            stat = stat + [""] if stat else stat
        al: dict = {}
        for name in sorted(set().union(*[set(v[2]) for v in views])):
            specs = [frozenset(v[2].get(name, ())) for v in views]
            one = specs[0] if owners and all(s == specs[0] for s in specs) else None
            al[name] = set(one) if one else set().union(*specs) | {""}
        for name, specs in mine.items():               # the file's own: in effect in every compilation of it
            al[name] = set(specs)
        return sorted(sure), stat, al, maybe

    def _owners(self, key, grp) -> frozenset:
        """The project groups that compile file key: each whose <Compile> link reaches it, and its own group when that
        is a project's directory and its Compile items do not leave it out (in_group False). Empty when none is
        known."""
        files, dirs = self._link_index()
        low = key.lower()
        owners = set(files.get(low, ()))
        cur = low
        while cur:
            cur = posixpath.dirname(cur)
            owners |= dirs.get(f"{cur}/" if cur else "", set())
        if grp in self.project_dirs() and self.in_group(key) is not False:
            owners.add(grp)
        return frozenset(owners)

    def cu_reading(self, rel):
        """(token, whole): what identifies rel's compilation unit for comparing MSBuild readings. An ordinary file:
        (its group, True). A file _compiled_elsewhere (another project's link, its own project leaving it out, no
        project's directory): (a token of that FILE alone, False), since group "" is no one compilation and such a
        file's usings are not only its group's: two files never compare through it. Never raises; an error reads like
        such a file."""
        key = str(rel).replace("\\", "/")
        try:
            grp = self.group_of(key)
            return (f"{_NS_SEP}{key}", False) if self._compiled_elsewhere(key, grp) else (grp, True)
        except Exception:  # noqa: BLE001
            return f"{_NS_SEP}{key}", False

    def lookup_type(self, fq, grp, arity=None, rel=None):
        """("one", rel, idx) | ("amb",) | None for full name `fq` with `arity` type parameters seen from group `grp`
        (R30: `Result` and `Result<T>` are two types; arity None, a spelling of unknown arity: any type of that full
        name answers ("amb",)). A file-local type (R28) is seen only from its own file (rel): there, beside another
        type of that full name, ("amb",); with rel None, any file-local entry of the name answers ("amb",)."""
        idx = self.index()
        if arity is None:
            return ("amb",) if idx[7].get(fq) else None
        ents = idx[0].get((fq, arity))
        if not ents:
            return None
        local = [e for e in ents if e[4]]
        if local:
            if rel is None:
                return ("amb",)
            mine = [e for e in local if e[1] == rel]
            ents = [e for e in ents if not e[4]]
            if mine:
                return ("amb",) if ents else ("one", mine[0][1], mine[0][2])
            if not ents:
                return None
        own = [e for e in ents if e[0] == grp]
        if not own:
            groups, defs = {e[0] for e in ents}, {(e[1], e[2]) for e in ents}
            if len(groups) != 1 and len(defs) != 1:
                return ("amb",)
            own = ents
        defs = sorted({(e[1], e[2], e[3]) for e in own})
        if len(defs) == 1 or all(d[2] for d in defs):
            return ("one", defs[0][0], defs[0][1])
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

    def resolve_fq(self, fq, grp, arity=0, rel=None):
        """A full name: a type (symbol), a namespace (namespace token; only at arity 0), RES_UNKNOWN (both, ambiguous,
        or a spelling of unknown arity that names either) or None."""
        t = self.lookup_type(fq, grp, arity, rel)
        is_ns = fq in self.index()[1]
        if arity is None:
            return self.T.RES_UNKNOWN if (t is not None or is_ns) else None
        if arity:
            return self._answer(t, fq)
        if t is not None and is_ns:
            return self.T.RES_UNKNOWN
        if t is not None:
            return self._answer(t, fq)
        if is_ns:
            return self.T.Resolution("namespace", name=self.token(grp, fq))
        return None

    # -------------------------------------------------------------- the frames of one declaration (R29)
    def _frame_table(self, rel):
        """The reader's `frames` of rel (R29) as a list of 8-tuples, or None (a reader without them)."""
        fr = self.cs(rel).get("frames")
        if not isinstance(fr, (list, tuple)) or not fr:
            return None
        out = []
        for f in fr:
            if not (isinstance(f, tuple) and len(f) == _FRAME_LEN):
                return None
            out.append(f)
        return out if out[0][0] == "" else None

    def _chain(self, frames, f) -> list:
        """The frame indexes from the compilation unit down to frame f (outermost first); [] when broken."""
        out, seen = [], set()
        while 0 <= f < len(frames) and f not in seen:
            seen.add(f)
            out.append(f)
            f = frames[f][1]
        return list(reversed(out)) if out and out[-1] == 0 else []

    def _levels(self, rel, frames, fidx):
        """The lookup order of a simple name written in frame fidx of rel, as [(namespace, usings, pp usings, aliases,
        pp aliases, statics, pp statics, members-only parents)], the compilation unit first. R44: the usings and
        statics are their MEANINGS (frame_meanings: absolute full names, None unknown), the aliases their texts
        ((X, target): _alias_target resolves a target in its own declaration)."""
        chain = self._chain(frames, fidx)
        out = []
        for k, f in enumerate(chain):
            full, _p, us, ppu, al, ppa, st, ppst = frames[f]
            fm = self.frame_meanings(rel, f)
            if fm is not None:
                us, ppu, st, ppst = fm[1], fm[2], fm[5], fm[6]
            else:
                us, ppu, st, ppst = ((None,) * len(x) for x in (us, ppu, st, ppst))
            outer = frames[chain[k - 1]][0] if k else ""
            parts, low = full.split("."), (len(outer.split(".")) if outer else 0)
            implicit = [".".join(parts[:j]) for j in range(len(parts) - 1, low, -1)] if full else []
            out.append((full, tuple(us), tuple(ppu), tuple(al), tuple(ppa), tuple(st), tuple(ppst), implicit))
        return out

    # -------------------------------------------------------------- R44: what a directive MEANS
    def frame_meanings(self, rel, fidx):
        """R44: what every directive of namespace declaration fidx of rel (its `frames` index; 0 the compilation unit)
        MEANS: (namespace, usings, pp usings, aliases, pp aliases, statics, pp statics), each of the six aligned with
        that frame's text tuple (frames[fidx][2:8]) and holding, per directive, its meaning: the ABSOLUTE full name it
        names (a namespace for a using, a type for a using static, the target for an alias) or None (unknown: every
        name it could supply is unknown at that level). Two directives may mean one thing: compare sets of meanings,
        never texts. One exception, an alias whose target is no dotted name (generic `List<Order>`, an extern alias
        `X::Y`): its TEXT, which names the same type only where the whole lookup order is the same (typed_calls
        _cs_x_same), never an absolute name. None when unknown (no frames table, another index). Never raises."""
        try:
            frames = self._frame_table(rel)
            if frames is None or isinstance(fidx, bool) or not isinstance(fidx, int) or not 0 <= fidx < len(frames):
                return None
            full, _p, us, ppu, al, ppa, st, ppst = frames[fidx]

            def m(text, kind):
                return self._directive_meaning(rel, fidx, text, kind)
            return (full, tuple(m(u, "u") for u in us), tuple(m(u, "u") for u in ppu),
                    tuple(m(a[1], "a") if isinstance(a, tuple) and len(a) == 2 else None for a in al),
                    tuple(m(a[1], "a") if isinstance(a, tuple) and len(a) == 2 else None for a in ppa),
                    tuple(m(s, "s") for s in st), tuple(m(s, "s") for s in ppst))
        except Exception:  # noqa: BLE001
            return None

    def decl_meanings(self, rel, cls_idx):
        """R44: [frame_meanings(rel, f) for each namespace declaration enclosing class cls_idx of rel], the compilation
        unit FIRST (the chain cframe[cls_idx] -> frames[f][1] -> ... -> -1, reversed: the core's cs_frame_decls
        order); cls_idx None or < 0: [frame_meanings(rel, 0)] (top-level statements). None when unknown. Never
        raises."""
        try:
            frames = self._frame_table(rel)
            if frames is None:
                return None
            if isinstance(cls_idx, bool) or not isinstance(cls_idx, int) or cls_idx < 0:
                chain = [0]
            else:
                f = (self.cs(rel).get("cframe") or {}).get(cls_idx)
                if isinstance(f, bool) or not isinstance(f, int):
                    return None
                chain = self._chain(frames, f)
            if not chain:
                return None
            out = [self.frame_meanings(rel, f) for f in chain]
            return None if any(x is None for x in out) else out
        except Exception:  # noqa: BLE001
            return None

    def using_meaning(self, rel, fidx, text, kind="u"):
        """R44: the meaning of one directive text written in namespace declaration fidx of rel (kind "u" a using
        namespace, "s" a using static, "a" an alias target): frame_meanings' rule, for any text. Never raises."""
        return self._directive_meaning(rel, fidx, text, kind if kind in ("u", "s", "a") else "u")

    def _directive_meaning(self, rel, fidx, text, kind):
        key = ("dmean", rel, fidx, text, kind)
        if key not in self._memo:
            try:
                got = self._directive_walk(rel, fidx, text, kind)[0]
            except Exception:  # noqa: BLE001
                got = None
            self._memo[key] = got
        return self._memo[key]

    def _directive_walk(self, rel, fidx, text, kind):
        """(meaning or None, every namespace it may mean) of directive `text` (kind "u" / "s" / "a") written in frame
        fidx of rel. C# resolves a directive's name as if its own declaration had no using directives (C# spec,
        using directives): a directive of the compilation unit (fidx 0) or any `global::` text is absolute; one
        written inside a namespace declaration D is looked up from D's namespace outward, its first segment H per
        level L (D's namespace, each implicit dotted parent and enclosing declaration's namespace, the global one):
          1. L.H a namespace some project file declares: found (also a project type L.H: unknown);
          2. L.H a project type of arity 0: for a using, CS0138 (a using imports a namespace): unknown; for a using
             static / alias target found, unless capable(L) (an external NAMESPACE L.H would come first);
          3. an enclosing declaration of namespace L (never D itself) holding an alias H: unknown; for a type (a
             using static / an alias target) any directive there may import a TYPE H first: unknown (a using
             namespace passes them: a type head would not compile there);
          4. capable(L) (R27 / R32: an external assembly may declare L.H there): unknown (the walk goes on only to
             collect what it may mean);
          at the global namespace, nothing of the project found: a using means the text itself (only a namespace makes
          it compile, and a global namespace member comes before the compilation unit's directives), unless the
          compilation unit (the file's, the group's `global using` and MSBuild's aliases) has an alias H: unknown. A
          dotted using static means the text when H is surely a global namespace (System / Microsoft, the head of a
          certain compilation-unit using of the file, of the group's `global using`s or MSBuild's: such a member comes
          before every directive of the compilation unit), else unknown (a compilation-unit using may import a TYPE H);
          a single-segment one, unknown. An alias target follows _alias_meaning (members only: an external target is
          unknown).
        Unknown too: a `::` other than a leading `global::` (an extern alias), a generic or unreadable text. The
        possible set (ns_info: what an unknown using may name) holds the candidate of every level passed as unknown
        and the found one."""
        t = str(text or "")
        absolute = t.startswith("global::")
        if absolute:
            t = t[len("global::"):]
        if not t or "::" in t or "<" in t or not all(p.isidentifier() for p in t.split(".")):
            if kind == "a" and text:
                return str(text), frozenset()   # a generic / extern-alias target: its TEXT (comparable only within
            return None, frozenset()            # one lookup order, the core's _cs_x_same; never an absolute name)
        frames = self._frame_table(rel)
        if fidx == 0 or absolute:
            return t, frozenset({t})
        if frames is None or isinstance(fidx, bool) or not isinstance(fidx, int) or not 0 < fidx < len(frames):
            return None, frozenset()
        chain = self._chain(frames, fidx)
        if not chain:
            return None, frozenset()
        if kind == "a":
            outer = frozenset(frames[f][0] for f in chain[1:-1] if any(frames[f][2:8]))
            got = self._alias_meaning(rel, text, frames[fidx][0], outer)
            return got, frozenset({got} if got else ())
        grp, parts = self.group_of(rel), t.split(".")
        head = parts[0]
        enc_alias, enc_dir = {}, set()
        for f in chain[1:-1]:                           # the enclosing declarations (never D itself)
            full = frames[f][0]
            enc_alias.setdefault(full, set()).update(
                a[0] for a in tuple(frames[f][4]) + tuple(frames[f][5]) if isinstance(a, tuple) and a)
            if any(frames[f][2:8]):
                enc_dir.add(full)
        nss = self.index()[1]
        possible, level, unknown = set(), frames[fidx][0], False
        while True:
            cand = f"{level}.{head}" if level else head
            full = f"{level}.{t}" if level else t
            ty = self.lookup_type(cand, grp, 0, rel)
            if cand in nss:
                possible.add(full)
                return (None if unknown or ty is not None else full), frozenset(possible)
            if ty is not None:
                if kind == "u" or (level and self.capable(level)):
                    return None, frozenset(possible)
                possible.add(full)
                return (None if unknown else full), frozenset(possible)
            if not level:
                break
            if head in enc_alias.get(level, ()) or (kind != "u" and level in enc_dir):
                return None, frozenset(possible)
            if self.capable(level):
                unknown = True
                possible.add(full)
            level = level.rsplit(".", 1)[0] if "." in level else ""
        if head in self._cu_alias_names(rel):
            return None, frozenset(possible)
        if kind == "s" and (len(parts) < 2 or head not in self._global_heads(rel)):
            return None, frozenset(possible)
        possible.add(t)
        return (None if unknown else t), frozenset(possible)

    def _cu_alias_names(self, rel) -> frozenset:
        """The alias names in effect in rel's compilation unit: the file's own (frame 0, a branch's included), the
        group's `global using` aliases and MSBuild's (an alias it may define included: cu_globals)."""
        key = ("cualias", rel)
        if key not in self._memo:
            frames = self._frame_table(rel) or [("", -1, (), (), (), (), (), ())]
            names = {a[0] for a in tuple(frames[0][4]) + tuple(frames[0][5]) if isinstance(a, tuple) and a}
            names |= {a[0] for a in self.cs(rel).get("galias", ()) if isinstance(a, tuple) and a}
            names |= set(self.cu_globals(rel)[2])
            self._memo[key] = frozenset(names)
        return self._memo[key]

    def _global_heads(self, rel) -> frozenset:
        """First segments that are surely namespaces of the global namespace in rel's compilation: System, Microsoft
        (CS_EXT_ROOTS), and the head of every certain compilation-unit using of rel (frame 0, outside a branch), of
        the group's certain `global using`s and MSBuild's certain ones (cu_globals): a compilation-unit using names a
        namespace of the global namespace (resolved there with no directives), so its head exists."""
        key = ("gheads", rel)
        if key not in self._memo:
            frames = self._frame_table(rel)
            heads = set(CS_EXT_ROOTS)
            if frames is not None:
                heads |= {str(u).split(".")[0] for u in frames[0][2] if u and "::" not in str(u)}
            heads |= {str(u).split(".")[0] for u in self.cu_globals(rel)[0] if u and "::" not in str(u)}
            self._memo[key] = frozenset(h for h in heads if h)
        return self._memo[key]

    def _static_nested(self, rel, spec, name, arity):
        """R23: what `using static spec;` written in rel supplies for simple type name `name`: "found" (a nested type
        of that name and arity of the project type spec names: no Resolution form, so never a proof), "none" (that
        project type holds none and has no base list: an inherited nested type cannot supply one) or "maybe" (an
        external or unresolved spec, a base list, a spelling of unknown arity). R44: spec is the directive's MEANING
        (an absolute full name, frame_meanings), resolved as written."""
        try:
            res = self.resolve_fq(spec, self.group_of(rel), 0, rel) if spec else None
            if res is None or getattr(res, "kind", "") != "symbol":
                return "maybe"
            ff = self.facts.get(res.rel)
            if ff is None:
                return "maybe"
            tops = [i for i, c in enumerate(ff.classes) if c.outer < 0 and c.scope < 0 and c.name == res.name]
            if len(tops) != 1:
                return "maybe"
            parts = self.part_map().get((res.rel, tops[0]), [(res.rel, tops[0])])
            based = False
            for r2, i2 in parts:
                f2 = self.facts.get(r2)
                tpo = self.cs(r2).get("tporder") or {}
                if f2 is None or i2 >= len(f2.classes):
                    return "maybe"
                based = based or bool(f2.classes[i2].bases)
                for j, c in enumerate(f2.classes):
                    if c.outer == i2 and c.name == name:
                        own = tpo.get(j)
                        if arity is None or not isinstance(own, tuple) or len(own) == arity:
                            return "found"
            return "maybe" if based or arity is None else "none"
        except Exception:  # noqa: BLE001
            return "maybe"

    def _level_usings(self, rel, grp, name, arity, uses, statics, possible, pstatics):
        """One level's using-namespace and using-static directives (C#: looked up together, two holders CS0104):
        ("member", Resolution) one project type; ("unknown",) two holders, a project type a possible directive
        (preprocessor branch, MSBuild unread) may supply, a nested type of a `using static` type (R23), or no holder
        while a static directive may supply one (external, unread, in a branch); None: nothing here. R44: uses /
        possible / statics are MEANINGS (absolute full names; frame_meanings): one that is unknown (None) may name
        anything, so a level holding one answers unknown for every name reaching it."""
        if None in uses or None in possible:
            return ("unknown",)
        found = {}
        for u in dict.fromkeys(uses):
            t = self.lookup_type(f"{u}.{name}", grp, arity, rel)
            if t is not None:
                found[f"{u}.{name}"] = t
        maybe_ext = bool(pstatics)
        for s in dict.fromkeys(statics):
            got = self._static_nested(rel, s, name, arity) if s else "maybe"
            if got == "found":
                found[f"\x00static:{s}"] = ("amb",)
            elif got == "maybe":
                maybe_ext = True
        for u in dict.fromkeys(possible):
            if f"{u}.{name}" not in found and self.lookup_type(f"{u}.{name}", grp, arity, rel) is not None:
                return ("unknown",)
        if len(found) > 1:
            return ("unknown",)
        if found:
            fq2, t = next(iter(found.items()))
            return ("unknown",) if fq2.startswith("\x00") else ("member", self._answer(t, fq2))
        return ("unknown",) if maybe_ext else None

    def _members(self, ns, name, grp, arity, rel, known):
        """A namespace's members: ("member", Resolution), ("none",) (a type _CS_KNOWN_NS lists there: that external
        type is found first), ("unknown",) (R27: a capable namespace may hold an external type of the name) or None."""
        res = self.resolve_fq(f"{ns}.{name}" if ns else name, grp, arity, rel)
        if res is not None:
            return ("member", res)
        if name in known.get(ns, ()):
            return ("none",)
        if ns and self.capable(ns):
            return ("unknown",)
        return None

    # -------------------------------------------------------------- the binder interface
    def resolve_free(self, rel, name, arity=None):
        key = ("free", rel, name, arity)
        if key not in self._memo:
            try:
                self._memo[key] = self._resolve_free(rel, name, arity)
            except Exception:  # noqa: BLE001
                self._memo[key] = self.T.RES_UNKNOWN
        return self._memo[key]

    def _resolve_free(self, rel, name, arity):
        """R29: every namespace declaration of rel that directly holds a type (or the compilation unit, for top-level
        code or a file without types) answers with its own lookup order; all alike, else unknown."""
        cs = self.cs(rel)
        if not cs or not name:
            return None
        frames = self._frame_table(rel)
        if frames is None:
            return self.T.RES_UNKNOWN
        ff = self.facts.get(rel)
        cfr = cs.get("cframe") or {}
        starts = {cfr.get(i) for i, c in enumerate(getattr(ff, "classes", ()) or ()) if c.scope < 0}
        if None in starts or not starts or any(getattr(s, "cls", 0) < 0 for s in getattr(ff, "scopes", ()) or ()):
            starts.discard(None)
            starts.add(0)
        answers = [self._resolve_in(rel, frames, f, name, arity) for f in sorted(starts)]
        first = answers[0]
        return first if all(a == first for a in answers) else self.T.RES_UNKNOWN

    def _resolve_in(self, rel, frames, fidx, name, arity):
        """C#'s namespace-or-type-name lookup of simple name `name` (`arity` type arguments) written in frame fidx:
        per namespace declaration innermost out its members (R27: a capable one unknown), its aliases (arity 0; one
        in a preprocessor branch: unknown), its usings and `using static`s together (R23, R24), then its implicit
        dotted parents' members; last the compilation unit's (members, its aliases with the group's global ones,
        its usings with every global and MSBuild one, the possible ones as possible); R44: every using and static by
        its MEANING (_levels), an unknown one making its level unknown. Once a declaration's
        non-transparent using is passed (`shadow`), a project type or alias found later is unknown: that using may
        import an external type of the name, which C# finds first (a project namespace still answers: a qualifier
        resolves only when the rest of the name is found in it, see the module docstring)."""
        T = self.T
        grp = self.group_of(rel)
        known = getattr(T, "_CS_KNOWN_NS", None) or {}
        levels = self._levels(rel, frames, fidx)
        if not levels:
            return T.RES_UNKNOWN
        cs = self.cs(rel)
        gus, gst, gal, gpo = self.cu_globals(rel)
        pp = self._pp(cs)
        uses_all = list(cs.get("usings", ()))
        own_glob = [u for k, (u, glob, _d) in enumerate(uses_all) if glob and k not in pp["usings"]]
        own_pp = [u for k, (u, glob, _d) in enumerate(uses_all) if glob and k in pp["usings"]]
        st_all = list(cs.get("statics", ()))
        own_st = [u for k, (u, glob, _d) in enumerate(st_all) if glob and k not in pp["statics"]]
        own_ppst = [u for k, (u, glob, _d) in enumerate(st_all) if glob and k in pp["statics"]]

        shadow = []     # a namespace declaration's non-transparent using passed: it may supply a TYPE `name` first

        def conv(got):
            if got is None:
                return None
            if got[0] == "member":
                return T.RES_UNKNOWN if shadow and getattr(got[1], "kind", "") == "symbol" else got[1]
            return None if got[0] == "none" else T.RES_UNKNOWN

        for k in range(len(levels) - 1, 0, -1):
            ns, us, ppu, al, ppa, st, ppst, implicit = levels[k]
            got = self._members(ns, name, grp, arity, rel, known)
            if got is not None:
                return conv(got)
            if arity == 0 or arity is None:
                if any(a[0] == name for a in ppa) or (arity is None and any(a[0] == name for a in al)):
                    return T.RES_UNKNOWN
                specs = {a[1] for a in al if a[0] == name}
                if specs:
                    outer = frozenset(levels[j][0] for j in range(1, k) if any(levels[j][1:7]))
                    if len(specs) != 1 or shadow:
                        return T.RES_UNKNOWN
                    return self._alias_target(rel, next(iter(specs)), ns, outer)
            got = self._level_usings(rel, grp, name, arity, us, st, ppu, ppst)
            if got is not None:
                return conv(got)
            if not shadow and any(not self.transparent(u) for u in list(us) + list(ppu)):
                shadow.append(ns)
            for sub in implicit:
                got = self._members(sub, name, grp, arity, rel, known)
                if got is not None:
                    return conv(got)
        got = self._members("", name, grp, arity, rel, known)
        if got is not None:
            return conv(got)
        _ns, us, ppu, al, ppa, st, ppst, _i = levels[0]
        if arity == 0 or arity is None:
            here = {a[1] for a in al if a[0] == name} | set(gal.get(name, ()))
            if any(a[0] == name for a in ppa) or (here and (arity is None or shadow)):
                return T.RES_UNKNOWN
            if here:
                if len(here) != 1 or "" in here:
                    return T.RES_UNKNOWN
                spec = next(iter(here))
                got = self.resolve_fq(spec, grp, 0, rel)
                return got if got is not None else T.RES_UNKNOWN
        got = self._level_usings(rel, grp, name, arity, list(us) + own_glob + list(gus), list(st) + own_st + [
            s for s in gst if s], list(ppu) + own_pp + list(gpo), list(ppst) + own_ppst + [s for s in gst if not s])
        return conv(got) if got is not None else None

    def _qualified(self, rel, spec):
        """A written full name (an alias target, a `using static` type; arity 0): fully qualified, then relative to
        the file's namespaces; RES_UNKNOWN when nothing (or more than one thing) answers. R44: `global::N` is N in the
        global namespace only (absolute)."""
        T = self.T
        if spec and spec.startswith("global::"):
            spec = spec[len("global::"):]
            if not spec or "<" in spec or "::" in spec:
                return T.RES_UNKNOWN
            got = self.resolve_fq(spec, self.group_of(rel), 0, rel)
            return got if got is not None else T.RES_UNKNOWN
        if not spec or "<" in spec or "::" in spec:
            return T.RES_UNKNOWN
        grp = self.group_of(rel)
        outs = set()
        got = self.resolve_fq(spec, grp, 0, rel)
        if got is not None:
            outs.add(got)
        for ns in self.cs(rel).get("namespaces") or ():
            cur = ns
            while cur:
                g2 = self.resolve_fq(f"{cur}.{spec}", grp, 0, rel)
                if g2 is not None:
                    outs.add(g2)
                    break
                cur = cur.rsplit(".", 1)[0] if "." in cur else ""
        return next(iter(outs)) if len(outs) == 1 else T.RES_UNKNOWN

    def resolve_import(self, rel, imp):
        try:
            if getattr(imp, "kind", "") != "named":
                return self.T.RES_UNKNOWN
            spec = str(getattr(imp, "spec", "") or "")
            got = self._import_scoped(rel, str(getattr(imp, "local", "") or ""), spec)
            return got if got is not None else self._qualified(rel, spec)
        except Exception:  # noqa: BLE001
            return self.T.RES_UNKNOWN

    def _import_scoped(self, rel, local, spec):
        """R44: alias `local = spec` resolved in the declaration(s) that write it (_cs aliases: (X, target, frame,
        global?)): one of the compilation unit, or a `global using` alias, absolutely; one inside a namespace
        declaration by _alias_target (from that declaration's namespace outward); one answer for every such
        directive, else RES_UNKNOWN. None when the table names no such alias (the caller's file-wide _qualified)."""
        T = self.T
        frames = self._frame_table(rel)
        ents = [a for a in self.cs(rel).get("aliases", ())
                if isinstance(a, tuple) and len(a) >= 4 and a[0] == local and a[1] == spec]
        if frames is None or not ents:
            return None
        grp, outs = self.group_of(rel), set()
        for _x, sp, f, glob in ents:
            if isinstance(f, bool) or not isinstance(f, int) or not 0 <= f < len(frames):
                outs.add(T.RES_UNKNOWN)
            elif glob or f == 0:
                bare = sp[len("global::"):] if sp.startswith("global::") else sp
                got = self.resolve_fq(bare, grp, 0, rel) if bare and "<" not in bare and "::" not in bare else None
                outs.add(got if got is not None else T.RES_UNKNOWN)
            else:
                chain = self._chain(frames, f)
                outer = frozenset(frames[j][0] for j in chain[1:-1] if any(frames[j][2:8]))
                outs.add(self._alias_target(rel, sp, frames[f][0], outer) if chain else T.RES_UNKNOWN)
        return next(iter(outs)) if len(outs) == 1 else T.RES_UNKNOWN

    def submodule(self, module, attr, arity=None):
        try:
            if not isinstance(module, str) or _NS_SEP not in module:
                return None
            grp, fq = module.split(_NS_SEP, 1)
            return self.resolve_fq(f"{fq}.{attr}", grp, arity)
        except Exception:  # noqa: BLE001
            return None

    # -------------------------------------------------------------- namespaces an external assembly may share
    def ns_info(self):
        """(type_ns, used_ext, package_roots): the namespaces that directly hold a top-level project type; every
        namespace a using directive, a `global using` or an MSBuild Using item (certain or possible) of any group
        imports that holds no project type directly and is no proper ancestor of a type_ns entry (a container `using
        Shop;` is no evidence of an external assembly); the lower-cased first segment of every package id any project
        of the tree names (MsbUsings.packages, a project with no C# file included). Memoised; never raises (an error:
        all empty, and capable / transparent then answer their conservative side)."""
        if self._nsinfo is None:
            try:
                type_ns, used = set(), set()
                for rel in sorted(self.facts):
                    ff, cs = self.facts[rel], self.cs(rel)
                    if not cs:
                        continue
                    cns = cs.get("class_ns", ())
                    for i, c in enumerate(ff.classes):
                        if c.outer < 0 and c.scope < 0 and i < len(cns):
                            type_ns.add(str(cns[i]))
                    used.update(str(u) for u, _g, _d in cs.get("usings", ()))
                groups = sorted(set(self.index()[6]) | set(self.project_dirs()))
                for g in sorted(self.index()[6]):
                    mb = self.msbuild(g)
                    used.update(mb.certain)
                    used.update(mb.possible)
                anc = {".".join(t.split(".")[:k]) for t in type_ns if t for k in range(1, t.count(".") + 1)}
                ext = frozenset(u for u in used if u and u not in type_ns and u not in anc)
                roots = {str(pk).split(".")[0].lower() for g in groups
                         for pk in getattr(self.msbuild_raw(g), "packages", ())}
                owned = {str(n).strip().split(".")[0] for g in self.project_dirs()
                         for n in getattr(self.msbuild_raw(g), "names", ()) if str(n).strip()}
                self._owned = frozenset(o for o in owned if o)
                self._nsinfo = (frozenset(type_ns), ext, False, frozenset(r for r in roots if r))
                self._relative_used(frozenset(type_ns), frozenset(anc), used)
            except Exception:  # noqa: BLE001
                self._owned = frozenset()
                self._nsinfo = (frozenset(), frozenset(), True, frozenset())
            self._forget("extrel")
        return self._nsinfo[0], self._nsinfo[1], self._nsinfo[3]

    def _relative_used(self, type_ns, anc, used):
        """R44: a using written inside a namespace declaration names what _directive_walk finds, relative to that
        declaration: `used` (every using text) grows by every namespace each such using may MEAN (the found one, and
        each candidate passed as unknown), to a fixed point (a larger used_ext makes more namespaces capable, and so
        more candidates unknown); used_ext only grows, toward the conservative side (capable True, transparent
        False). No fixed point within _NS_ROUNDS: raises (ns_info then reads everything capable)."""
        used = set(used)
        for _round in range(_NS_ROUNDS):
            self._forget("extrel")
            more = set()
            for rel in sorted(self.facts):
                frames = self._frame_table(rel)
                for f in range(1, len(frames or ())):
                    for text in tuple(frames[f][2]) + tuple(frames[f][3]):
                        more |= self._directive_walk(rel, f, text, "u")[1]
            if more <= used:
                return
            used |= more
            ext = frozenset(u for u in used if u and u not in type_ns and u not in anc)
            self._nsinfo = (self._nsinfo[0], ext, False, self._nsinfo[3])
        raise RuntimeError("R44: used_ext found no fixed point")

    def _forget(self, *tags):
        """Drop the memo entries whose key starts with one of tags."""
        for k in [k for k in self._memo if isinstance(k, tuple) and k and k[0] in tags]:
            del self._memo[k]

    def owned_roots(self) -> frozenset:
        """R32: the first segments some project's AssemblyName, RootNamespace or project file name starts with."""
        self.ns_info()
        return self._owned

    def ext_root(self, ns) -> bool:
        """_ext_root (public)."""
        return self._ext_root(ns)

    def _ext_root(self, ns) -> bool:
        """R32, THE rule capable and transparent share: may external assemblies fill namespaces under ns's first
        segment? Yes for System / Microsoft (CS_EXT_ROOTS); for a root no project owns (no project's AssemblyName,
        RootNamespace or project file name has it as its first segment: exact case); and for a root a package shares
        (a package / <Reference> id's first segment, case-insensitive, starts with the root or is a prefix of it:
        `AcmeCorp.Core` shares `Acme`). A tree with no .csproj owns nothing. Assumption, documented beside R21: a
        package whose id shares nothing with the namespace root it declares, under a root the repository's own
        assemblies are named after, stays unseen."""
        root = str(ns).split(".")[0]
        if root in CS_EXT_ROOTS:
            return True
        self.ns_info()
        if self._nsinfo[2] or root not in self._owned:
            return True
        low = root.lower()
        return any(p.startswith(low) or low.startswith(p) for p in self._nsinfo[3])

    def _ext_related(self, ns) -> bool:
        """ns equals, is an ancestor of or a descendant of a used_ext namespace."""
        key = ("extrel", ns)
        if key not in self._memo:
            ext = self.ns_info()[1]
            parts = ns.split(".")
            self._memo[key] = (ns in ext or any(".".join(parts[:k]) in ext for k in range(1, len(parts)))
                               or any(e.startswith(ns + ".") for e in ext))
        return self._memo[key]

    def capable(self, ns) -> bool:
        """May an external assembly declare types in enclosing namespace ns? Yes when its root is one external
        assemblies may fill (ext_root, R32) or it is related to a namespace the project imports that holds no project
        type (ns_info used_ext). A project type in ns decides nothing: `namespace System.Timers` holding one project
        class still holds System.Timers.Timer. The global namespace never (an assumption, as before)."""
        try:
            if not ns:
                return False
            return self._ext_root(ns) or self._ext_related(ns)
        except Exception:  # noqa: BLE001
            return True

    def transparent(self, u) -> bool:
        """Does `using u;` import only project types (which the binder answers itself)? Yes when u holds a project type
        directly, its root is owned and unshared (not ext_root, R32), it is related to no namespace the project imports
        that holds none, and its first segment holds project types in two or more namespaces (a namespace the project
        declares alone under its first segment may be a library's own, extended: `namespace Serilog { static class
        MyLoggingExtensions }` beside `using Serilog;` for Serilog's own ILogger). Otherwise an external assembly may
        supply types there."""
        try:
            if not u:
                return False
            type_ns = self.ns_info()[0]
            root = u.split(".")[0]
            if self._nsinfo[2] or u not in type_ns or self._ext_root(u):
                return False
            if sum(1 for t in type_ns if t == root or t.startswith(root + ".")) < 2:
                return False
            return not self._ext_related(u)
        except Exception:  # noqa: BLE001
            return False

    # -------------------------------------------------------------- C#'s lookup order of one declaration
    def _frames(self, rel, cls_idx):
        """[(namespace, usings, aliases, pp usings, pp aliases, statics, pp statics)] of the namespace declarations
        enclosing class cls_idx of rel (the compilation unit first; aliases ((X, A.B), ...) non-global, as texts; the
        pp_ ones inside a preprocessor branch; R44: usings and statics as their MEANINGS, frame_meanings), or of the
        compilation unit alone (cls_idx None or < 0: top-level statements); None when unknown (a reader without
        `frames` / `cframe`)."""
        cs = self.cs(rel)
        frames = self._frame_table(rel)
        if not cs or frames is None:
            return None
        if not isinstance(cls_idx, int) or cls_idx < 0:
            fidx = 0
        else:
            fidx = (cs.get("cframe") or {}).get(cls_idx)
            if not isinstance(fidx, int):
                return None
        chain = self._chain(frames, fidx)
        if not chain:
            return None
        out = []
        for f in chain:                     # R44: usings and statics as their MEANINGS (frame_meanings), aliases as
            fm = self.frame_meanings(rel, f)        # texts (_alias_target resolves a target in its own declaration)
            if fm is None:
                return None
            out.append((frames[f][0], tuple(fm[1]), tuple(frames[f][4]), tuple(fm[2]), tuple(frames[f][5]),
                        tuple(fm[5]), tuple(fm[6])))
        return out

    def alias_in_effect(self, rel, cls_idx, name, arity=None):
        """What simple name `name` (`arity` type arguments written; None unknown), written in class cls_idx of rel
        (None / < 0: the compilation unit), means as far as aliases go — C#'s lookup, per namespace declaration
        innermost out: the namespace's members, that declaration's aliases (arity 0 only), its usings and `using
        static`s (then its implicit dotted parents' members); last the global namespace's members, the compilation
        unit's aliases (the file's, the group's `global using X = ...`, MSBuild's), its usings (with every global and
        MSBuild using). ("alias", target) an alias in effect; ("member", Resolution) a project type / namespace found
        first; ("none",) no alias in effect (an external or unbound name, or a type _CS_KNOWN_NS lists found first);
        ("unknown",) the pass cannot tell (two aliases or project types at one level, an alias MSBuild may define, a
        directive in a preprocessor branch, a capable enclosing namespace (R27), a `using static` that may supply a
        nested type, a namespace declaration's non-transparent using passed before an alias of `name` or a project
        type further out (a project namespace still answers: resolve_free), a spelling of unknown arity). R44: an
        alias written inside a namespace declaration answers ("alias", its target's MEANING) (_alias_meaning: the
        absolute full name, resolved from that declaration's namespace outward; `global::` absolute) or ("unknown",)
        when that meaning is unknown, never the relative text; a target that is no dotted name (generic: `List<Order>`)
        keeps its text, which the core compares only within one lookup order (_cs_x_same). A compilation-unit or
        group alias: its text (absolute). Never raises."""
        got, frame_ns = self._alias_walk(rel, cls_idx, name, arity)
        if got[0] != "alias" or not frame_ns:
            return got
        try:
            spec = str(got[1] or "")
            if "<" in spec or "::" in spec.replace("global::", "", 1):
                return got                          # a generic target / an extern alias: its text (_cs_x_same)
            frames = self._frames(rel, cls_idx) or []
            at = next((k for k, f in enumerate(frames) if k and f[0] == frame_ns), 0)
            outer = frozenset(frames[j][0] for j in range(1, at) if any(frames[j][1:]))
            m = self._alias_meaning(rel, spec, frame_ns, outer) if at else None
            return ("alias", m) if m else ("unknown",)
        except Exception:  # noqa: BLE001
            return ("unknown",)

    def resolve_scoped(self, rel, cls_idx, name, arity=None):
        """alias_in_effect as a Resolution: an alias -> its target, resolved in its own declaration's namespace chain
        (_alias_target: members only, innermost first; RES_UNKNOWN when no level holds its first segment or an
        enclosing declaration's directives come first); a member -> it; unknown -> RES_UNKNOWN; none -> None (no alias
        in effect and no project type: the caller must NOT read the file's alias imports for this name). Never
        raises."""
        T = self.T
        try:
            got, frame_ns = self._alias_walk(rel, cls_idx, name, arity)
            if got[0] == "alias":
                frames = self._frames(rel, cls_idx) or []
                at = next((k for k, f in enumerate(frames) if k and f[0] == frame_ns), 0)
                outer = frozenset(frames[j][0] for j in range(1, at) if any(frames[j][1:]))
                return self._alias_target(rel, got[1], frame_ns, outer)
            if got[0] == "member":
                return got[1]
            return None if got[0] == "none" else T.RES_UNKNOWN
        except Exception:  # noqa: BLE001
            return T.RES_UNKNOWN

    def _alias_target(self, rel, spec, frame_ns, outer=frozenset()):
        """An alias target `spec` written in the declaration of namespace frame_ns: C# resolves it as if that
        declaration had no using directives, so per namespace innermost out its members only, but at an enclosing
        DECLARATION that writes any directive (`outer`: their namespaces) that declaration's aliases and usings come
        next, and they may supply spec's first segment: unknown there (a capable level too, R27). R44: a `global::`
        target (kept by the reader inside a namespace declaration) is absolute: the global namespace's members only.
        The Resolution of _alias_meaning's full name (RES_UNKNOWN: unknown, or no project type / namespace of it)."""
        T = self.T
        got = self._alias_meaning(rel, spec, frame_ns, outer)
        if got is None:
            return T.RES_UNKNOWN
        res = self.resolve_fq(got, self.group_of(rel), 0, rel)
        return res if res is not None else T.RES_UNKNOWN

    def _alias_meaning(self, rel, spec, frame_ns, outer=frozenset()):
        """The full name alias target `spec` written in the declaration of namespace frame_ns means (_alias_target's
        walk), or None: unknown (a capable level, an enclosing declaration's directives met first, a head the project
        does not hold: an external target, a generic or unreadable text)."""
        spec = str(spec or "")
        absolute = spec.startswith("global::")
        if absolute:
            spec = spec[len("global::"):]
        if not spec or "<" in spec or "::" in spec or not all(p.isidentifier() for p in spec.split(".")):
            return None
        if absolute:
            return spec                             # `global::`: the global namespace's members, no directive
        grp, head = self.group_of(rel), spec.split(".")[0]
        level = frame_ns
        while True:
            if level and self.capable(level):
                return None                         # an external namespace there may hold `head` first
            if self.resolve_fq(f"{level}.{head}" if level else head, grp, 0, rel) is not None:
                return f"{level}.{spec}" if level else spec
            if not level or level in outer:         # an enclosing declaration's directives may supply `head`
                return spec if not level and "." in spec and head in self._global_heads(rel) \
                    and head not in self._cu_alias_names(rel) else None     # a global namespace comes first
            level = level.rsplit(".", 1)[0] if "." in level else ""

    def _alias_walk(self, rel, cls_idx, name, arity=None):
        """(alias_in_effect's answer, the namespace of the declaration holding the alias found ("" otherwise))."""
        key = ("alias", rel, cls_idx if isinstance(cls_idx, int) and cls_idx >= 0 else -1, name, arity)
        if key not in self._memo:
            try:
                self._memo[key] = self._alias_steps(rel, cls_idx, name, arity)
            except Exception:  # noqa: BLE001
                self._memo[key] = (("unknown",), "")
        return self._memo[key]

    def _alias_steps(self, rel, cls_idx, name, arity):
        frames = self._frames(rel, cls_idx) if name else None
        if frames is None or arity is None:
            return ("unknown",), ""
        grp = self.group_of(rel)
        known = getattr(self.T, "_CS_KNOWN_NS", None) or {}
        here = [{} for _ in frames]                     # per frame: {alias: {targets}} ("" unknown: a branch's)
        if arity == 0:
            for k, (_ns, _us, al, _ppu, ppa, _st, _ppst) in enumerate(frames):
                for a in al:
                    here[k].setdefault(a[0], set()).add(a[1])
                for a in ppa:
                    here[k].setdefault(a[0], set()).add("")
        gus, gst, gal, gpo = self.cu_globals(rel)
        if arity == 0:
            for a, specs in gal.items():                # the group's global aliases (MSBuild's included)
                here[0].setdefault(a, set()).update(specs)
        cs = self.cs(rel)
        pp = self._pp(cs)

        def outer(k):                                   # an alias of name in effect at frame k or further out
            return any(name in here[j] for j in range(0, k + 1))

        shadow = []     # a namespace declaration's non-transparent using passed: it may supply a TYPE `name` first

        def shadowed(got):
            """A project TYPE found after `shadow`: unknown (see _resolve_in)."""
            return (got is not None and got[0] == "member" and bool(shadow)
                    and getattr(got[1], "kind", "") == "symbol")

        def members(ns, _k):
            got = self._members(ns, name, grp, arity, rel, known)         # R27: a capable one is unknown
            return ("unknown",) if shadowed(got) else got

        def usings(us, st, ppu, ppst, k, possible=(), pstat=()):
            """One level's directives (k: aliases in effect further out are frames 0..k; -1: the compilation unit's,
            none further out)."""
            got = self._level_usings(rel, grp, name, arity, us, st, list(ppu) + list(possible),
                                     list(ppst) + list(pstat))
            if got is not None:
                return ("unknown",) if shadowed(got) else got
            if any(name in known.get(u, ()) for u in us):
                return ("none",)
            if k >= 0 and any(not self.transparent(u) for u in list(us) + list(ppu)):
                if outer(k):
                    return ("unknown",)                 # an external type of that name there would beat the alias
                shadow.append(k)                        # ... and any project type further out
            return None

        for k in range(len(frames) - 1, 0, -1):
            ns, us, _al, ppu, _ppa, st, ppst = frames[k]
            got = members(ns, k)
            if got is not None:
                return got, ""
            if name in here[k]:
                specs = here[k][name]
                return (("alias", next(iter(specs))), ns) if len(specs) == 1 and "" not in specs else (("unknown",), "")
            got = usings(us, st, ppu, ppst, k - 1)
            if got is not None:
                return got, ""
            parts, low = ns.split("."), (len(frames[k - 1][0].split(".")) if frames[k - 1][0] else 0)
            for j in range(len(parts) - 1, low, -1):    # the implicit parents of a dotted declaration
                got = members(".".join(parts[:j]), k - 1)
                if got is not None:
                    return got, ""
        got = members("", 0)
        if got is not None:
            return got, ""
        if name in here[0]:
            specs = here[0][name]
            return (("alias", next(iter(specs))), "") if len(specs) == 1 and "" not in specs else (("unknown",), "")
        _ns, us0, _al, ppu0, _ppa, st0, ppst0 = frames[0]
        uses_all = list(cs.get("usings", ()))
        own_glob = [u for k, (u, glob, _d) in enumerate(uses_all) if glob and k not in pp["usings"]]
        own_pp = [u for k, (u, glob, _d) in enumerate(uses_all) if glob and k in pp["usings"]]
        st_all = list(cs.get("statics", ()))
        own_st = [u for k, (u, glob, _d) in enumerate(st_all) if glob and k not in pp["statics"]]
        own_ppst = [u for k, (u, glob, _d) in enumerate(st_all) if glob and k in pp["statics"]]
        got = usings(list(us0) + own_glob + list(gus), list(st0) + own_st + [s for s in gst if s], list(ppu0) + own_pp,
                     list(ppst0) + own_ppst, -1, gpo, [s for s in gst if not s])
        return (got if got is not None else ("none",)), ""

    # -------------------------------------------------------------- the lookup_rules hooks
    def part_map(self) -> dict:
        """{(rel, class index): [(rel, class index), ...] its merged parts} of partials() (memoised)."""
        if self._parts is None:
            out = {}
            for group in self.partials():
                for p in group:
                    out[p] = list(group)
            self._parts = out
        return self._parts

    def partials(self):
        """[[(rel, class index), ...], ...]: the parts of every partial type, per COMPILING project (R31: a file's
        own group when it compiles it, every project whose <Compile> link reaches the file), one full name and one
        arity. A part that would join two groups (two projects compile it into their own type) leaves every group it
        would join: partials_unmerged lists them."""
        try:
            groups, drop = self._partials()
            groups = {k: v for k, v in groups.items() if k not in drop}
            return [v for _k, v in sorted(groups.items()) if len(v) > 1]
        except Exception:  # noqa: BLE001
            return []

    def partials_unmerged(self):
        """[(rel, class index)]: parts of a partial type that two projects compile into their own types (or that
        would join two groups): never merged (partials), so the core must read a member lookup on them as open."""
        try:
            groups, drop = self._partials()
            return sorted({p for k in drop for p in groups[k]})
        except Exception:  # noqa: BLE001
            return []

    def _partials(self):
        key = ("partials",)
        if key not in self._memo:
            try:
                groups: dict = {}
                of: dict = {}
                for rel in sorted(self.facts):
                    ff, cs = self.facts[rel], self.cs(rel)
                    fqns = cs.get("class_fqn", ())
                    tpo = cs.get("tporder") or {}
                    fl = set(cs.get("filelocal") or ())
                    for i, c in enumerate(ff.classes):
                        if c.open and c.scope < 0 and i < len(fqns):
                            ar = len(tpo[i]) if isinstance(tpo.get(i), tuple) else len(c.type_params)
                            o, local = i, False
                            while 0 <= o < len(ff.classes) and not local:    # a `file` type, or one nested in it:
                                local, o = o in fl, ff.classes[o].outer      # its file's own (R28)
                            for g in sorted(self._compilers(rel)):
                                k = (g, fqns[i], ar, rel if local else "")
                                groups.setdefault(k, []).append((rel, i))
                                of.setdefault((rel, i), set()).add(k)
                multi = {k for k, v in groups.items() if len(v) > 1}
                bad = {p for p, ks in of.items() if len(ks) > 1 and ks & multi}
                drop = {k for k in multi if any(p in bad for p in groups[k])}
                self._memo[key] = (groups, frozenset(drop))
            except Exception:  # noqa: BLE001
                self._memo[key] = ({}, frozenset())
        return self._memo[key]

    def static_imports(self, rel, cls_idx=None):
        """[Resolution, ...] of the file's `using static` types plus the group's `global using static` ones (a
        directive in a preprocessor branch, or one MSBuild may add unread: an unknown entry). R44: each directive by
        its MEANING (frame_meanings: one written inside a namespace declaration is relative to it; unknown: an
        unknown entry), resolved as an absolute name (`global::` + meaning, _qualified). cls_idx (R29 for statics):
        None, every namespace declaration of the file (as before); a class index of rel (-1: top-level code), only
        the declarations enclosing it, the compilation unit's included (a block's `using static` is in effect only in
        that block); a chain the binder cannot name: every declaration of the file."""
        try:
            cs = self.cs(rel)
            pp = self._pp(cs)
            frames = self._frame_table(rel)
            if frames is None:
                specs = [("" if k in pp["statics"] or not glob else f"global::{u}")
                         for k, (u, glob, _d) in enumerate(cs.get("statics", ()))]
                specs += [""] if any(not glob for _u, glob, _d in cs.get("statics", ())) else []
            else:
                specs = [("" if k in pp["statics"] else f"global::{u}")
                         for k, (u, glob, _d) in enumerate(cs.get("statics", ())) if glob]
                chain = list(range(len(frames)))
                if isinstance(cls_idx, int) and not isinstance(cls_idx, bool):
                    f0 = 0 if cls_idx < 0 else (cs.get("cframe") or {}).get(cls_idx)
                    got = self._chain(frames, f0) if isinstance(f0, int) and not isinstance(f0, bool) else []
                    chain = got or chain
                for f in chain:
                    fm = self.frame_meanings(rel, f)
                    specs += [f"global::{m}" if m else "" for m in fm[5]] if fm is not None else [""]
                    specs += ["" for _m in fm[6]] if fm is not None else []
            specs += [f"global::{s}" if s else "" for s in self.cu_globals(rel)[1]]
            if "static_unread" in tuple(self.msbuild(self.group_of(rel)).reasons):
                specs.append("")                    # a `global using static` MSBuild may add (unread): unknown
            return [self._qualified(rel, s) for s in dict.fromkeys(specs)]
        except Exception:  # noqa: BLE001
            return [self.T.RES_UNKNOWN]

    def extensions(self, rel, m):
        """[(rel, class index, this-type TypeRef), ...]: every extension method named m in the project's compiled
        files (one of a `file` class only from its own file, R28)."""
        try:
            local = [e for e in self.index()[8].get(m, ()) if e[0] == rel]
            if local:
                return local + list(self.index()[4].get(m, ()))
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
