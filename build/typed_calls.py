#!/usr/bin/env python3
"""Typed-receiver call linking (DEEPMAP_TYPED_CALLS) and the untyped-receiver audit (DEEPMAP_CALLS_AUDIT) — the
whole-graph CORE (receiver-calls design, slice S3a: design sections 2, 4, 2.6-2.8).

A push-time pass. `link(root, nodes, calls)` runs in sync.sync between `_unkey_receiver_calls` and
`_attach_bare_calls` (slice S2 wires it). It types the receiver of each member call (`x.m()`, `self.f.m()`,
`super().m()`, `Cls.m()`, `mod.f()`) and constructor (`T()` / `new T()`) from the caller's own SOURCE — never from
the engine's links — and keys the call to the exact method / function / class node as a CallKey. It changes no
node text: every variant is an edge-only re-sync.

    calls, stats = typed_calls.link(root, nodes, calls)

* Never raises: on any internal error it returns its INPUT list (the same object) with every node attribute it
  had set rolled back, and records `stats["error"]`.
* Flags off (the default today): returns the SAME list object; reads nothing; touches no node.
* Flags on: the cyclic GC is paused for the run and restored after (on an error too) — nothing here builds a cycle
  worth collecting, while every full collection would traverse each AST the shared reader holds.
* Imports only the stdlib at import time. graphify_extract (CallKey, _Source), edge_resolve (lang_family, the
  push's name index), sync (_is_test_path) and the per-language readers are imported lazily inside functions, so
  this module stays outside the build/ lazy-import cycle. Every CallKey of this pass is made here
  (`_call_key` -> graphify_extract.call_key) and nothing here is cached or serialised (a CallKey that went through
  extract_cache would come back a plain str).

FLAGS (read at each call; `env=` overrides os.environ)
  DEEPMAP_TYPED_CALLS          unset -> _TYPED_DEFAULT_LANGS (empty until the S8 flip); 0/false/no/off -> none;
                               1/true/yes/on -> WAVE1_LANGS (python, typescript, javascript); a CSV of language
                               names (LANGUAGES keys) -> those. Unknown names are ignored (stats unknown_langs).
  DEEPMAP_TYPED_CALLS_SHAPES   unset -> every shape; a CSV of SHAPES codes -> only those (a site whose shape is
                               off counts as unknown: no edge, no removal). Per-shape A/B arms.
  DEEPMAP_TYPED_CALLS_REPLACE  default on; 0 -> add-only (never removes an engine tuple).
  DEEPMAP_TYPED_CALLS_TESTS    default off; 1 -> test callers (sync._is_test_path) get pure additions too.
  DEEPMAP_TYPED_CALLS_NONPROD  default off; 1 -> non-production callers (NONPROD_DIRS) get pure additions too.
  DEEPMAP_TYPED_CALLS_MAX_FILES  default 20000: a language with more graph files is skipped whole (a note +
                               langs_disabled[lang] = "max_files"), never half-indexed.
  DEEPMAP_TYPED_CALLS_IFACE    C# interface and virtual dispatch (LanguageSpec.lookup_rules iface_dispatch /
                               virtual_dispatch), read only when such a language is on: unset or 1/true/yes/on ->
                               static,impl; a CSV of IFACE_MODES -> static (the interface member / the declared
                               virtual member: Roslyn's own binding) and / or impl (its project implementers / the
                               overrides); 0/false/no/off -> neither. Unknown tokens are ignored (stats
                               unknown_iface); a value naming no mode at all selects both, like unset.
  DEEPMAP_CALLS_AUDIT          like DEEPMAP_TYPED_CALLS, default _AUDIT_DEFAULT_LANGS (empty for now). Grade-only.

WHAT IT DECIDES (design 2.4-2.8, 4)
  Site verdict (one per call site, `_Linker.site_verdict`): typed (every candidate class's member lookup reaches a
  project method node over a complete, non-dynamic chain; one target per class; |T| <= MAX_TARGETS) | external
  (a member call on an external module (`re.search()`, `os.path.join()`), on an instance of an external class
  named by a type, on a builtin or a literal; a bare call of an imported external or builtin function — and only
  through the conservative external test: an import that resolves to no project module AND whose top-level name
  is the stem of no project file or directory; otherwise UNKNOWN. A member call on an imported external OBJECT
  stays unknown (`current_user.save()` reaches a project class through a proxy), and so does anything an external
  call returns; so does a member m of a DECLARED external type (an annotation — any language: even a checked
  TypeScript one admits a subclass instance) when a project class whose chain is open — an external, unresolved or
  unlinearizable base — defines m: the value may be that subclass) | absent (a closed chain in a
  LanguageSpec.absent_ok language — .ts/.tsx, Java; never C# — defines no m, the caller is in such a language too, and
  no `@ts-nocheck` / nearby `@ts-ignore` / `@ts-expect-error` switches the check off) | unknown (everything else:
  |T| > 4, an external / unresolved / dynamic link before the hit, a field or property called, a dunder, a bare
  call of a project function, a disabled shape, a target of another language family).
  Merge per (caller C, bare name m) over SITES(C, m) = every site of m in C's FULL span (nested scopes included).
  ATTRIBUTION (the pass never moves a call to another caller; it only keys / corrects the target): a site is
  ATTRIBUTED to C when the engine that built the graph gives that call to C — C's own scope, plus a nested scope
  the engine walks into. graphify's call walk covers only a function's BODY and stops at its language's boundary
  node types (graphify/extract.py `function_boundary_types`, the same on 0.8.39 and 0.8.51): a Python nested def or
  method (incl. a local class's methods) and a JS/TS arrow, function declaration or method are DETACHED — the walk
  attributes their calls to nobody; a Python lambda / comprehension and a JS/TS function expression or generator
  declaration are attributed to C (LanguageSpec.nested_attributed / nested_detached, gate-locked against the real
  engine). A nested scope of any other kind (a reader's legacy or unknown Scope.kind) is UNCERTAIN. A Python
  default argument of a detached def (graphify's boundary covers the parameters) is detached too. graphify's
  SYMBOL-RESOLUTION post-pass then walks the WHOLE body of every top-level caller of a LanguageSpec.symbol_walk kind
  (an undecorated module-level Python def / async def; a root JS / TS `function f() {}` or `const f = () => {}`)
  with no boundary, and gives each BARE call it resolves to an imported symbol to that caller — so a detached bare
  site there whose name an import binds (Python: a non-external `from` import; JS / TS: any bare call, aliases
  resolve too) is UNCERTAIN: never emitted from C, and it blocks a REPLACE of its name (C keeps the engine's
  tuple: `def outer(): def inner(): raise Abort()` with Abort imported from a project file). With engine="ast"
  every site in the span is attributed (sync._Extractor's `ast.walk` duplicates nested calls into every enclosing
  def); any other engine gets graphify's rule, which is also safe on the ast path (a detached site's typed key
  lands on its own nested node there).
    REPLACE only if every site is typed/external/absent AND no site of m is in an uncertain scope AND the
    textual veto holds (the call-shaped `m(` count over C's span — from its first decorator / declarator line —
    outside strings and comments (template / f-string interpolations ARE code) and minus the declarations the
    reader saw, equals |SITES|; a span holding a line of MINIFIED_LINE+ characters is unjudgeable) AND the
    reader's own full-span count (Scope.all_names, when given) equals |SITES| AND the span is <=
    SPAN_MAX_LINES AND DEEPMAP_TYPED_CALLS_REPLACE is not 0: every tuple of C whose callee is m (the name, a
    name ending `.m` / `::m` / `#m`, or a CallKey whose node's last name segment is m) is removed, and m leaves
    C's `call_member`. Otherwise ADD: the existing tuples stay. A site typed to a target of another language
    family counts as unknown (it never triggers a REPLACE that could not be re-emitted). So does a site typed to a
    MEMBER OF AN INTERFACE (ClassFacts.kind "interface": a TS interface, a Python typing.Protocol or a class
    written under `if TYPE_CHECKING:`): that declaration never runs — the call runs whatever its run-time receiver
    implements (`cast(_OrjsonModule, import_module("orjson")).dumps()`, flask's LocalProxy globals) — so it is
    never a typed key and never REPLACEs the engine's tuples (stats skipped_interface). The one exception, C# with
    the reader's tables (LanguageSpec.lookup_rules iface_dispatch): a site whose WHOLE dispatch set is known (the
    interface member plus at most MAX_TARGETS implementers, both IFACE modes on, no class that may implement it
    unseen) no longer vetoes, and its set counts as the group's static targets; a C# static interface member with a
    body is an exact call, no interface member at all. A group with no tuple of
    m and no `call_member` m is MOOT: REPLACE and ADD emit the same, so it is not judged (no textual scan).
    DYNAMIC DISPATCH: a REPLACE never removes a run-time target the static binding misses. A typed site whose
    receiver may be (the class object of) a project SUBCLASS of its class — a declared type (an annotation, a
    parameter, a return annotation), `self` / `cls`; never a construction or a class named in the code — reaches
    every definition of m that a subclass's chain puts before that class (an override; a mixin ahead of it);
    `super().m()` in class K reaches the first definer of m after K in every subclass's chain. A prior tuple that
    reaches such an override (or a same-family :Method m whose class the pass cannot see — an unread file, a class
    without a node) is kept WHOLE, with its own grade, when everything else it reaches is a static target;
    otherwise it is removed and the overrides it reached are re-emitted as keys at AMBIGUOUS (a fan-out's grade:
    never an upgrade). When that reach cannot be known (an interface receiver: its implementers are no subclasses
    here; an ambiguous or unlinearizable subclass; an unresolved base ahead of the class) the group only ADDs
    (veto "dispatch"). A field read through such a receiver joins every subclass's bindings of it too: a subclass
    that rebinds it to an unrelated class makes it unknown; bindings that all hold instances of one class D or of
    its subclasses type it as D, whose overrides dispatch then keeps reachable.
    PATCH_RULES: a member patched onto exactly one project class is never typed or absent on it (a "*" patch —
    a mixin no file reads — opens every member); a member patched onto anything else (a builtin's prototype, a
    global, an external) makes every external member call of it in that language family unknown ("loose"; a
    test file's patch: only in test callers).
    LanguageSpec.static_split (TS / JS): a class object sees only static members, an instance only the others.
    Emission: (C, CallKey(t)) for the typed targets of C's ATTRIBUTED sites only, appended at the END of the
    list (the push keeps the last grade per pair, so the typed grade wins over a kept fan-out); a candidate set
    (2..4 targets) only where C already had a tuple of m, and only the targets those tuples could already bind
    (it narrows a fan-out, never creates or widens one); a target equal to C is skipped; a target of another
    language family is never emitted.
  Caller policy (2.6): test and non-production callers get CORRECTIONS only — a typed key is added only where C
  already had a tuple of m; pure additions are counted in `skipped_policy`. Removals and audits still apply.
  Grades (4): EXTRACTED only for a single target whose caller, target and every evidence link (the receiver's
  binding, each class walked up to the defining one, any import) sit in the caller's file and that did not go
  through a return annotation (R4), an injected field (`self.f = p`) or an INTERFACE receiver (a TS interface,
  a Python typing.Protocol or typing-only class: its run-time value need not be its subclass, so the static
  target may not be what the call runs); INFERRED for every other single target;
  AMBIGUOUS for each member of a candidate set. No other grade string is ever produced.
  Audit (2.7, grade-only): an input tuple (C, x) whose x is a CallKey or a qualified `Cls.m` name binding exactly
  one :Method t in C's file, where >= 2 :Method nodes of C's language family share m, no site of m in C is bare,
  SELF/SUPER, a class reference naming t's owner (or a subclass of it) or typed to t, and at least one site is an
  untyped receiver attributed to C — t goes into C's `call_weak`. The site verdicts are the same ones TYPED uses
  ("audit-only" = nothing is emitted or removed when TYPED is off for that language). A node the engine merged
  from TWO scopes (a property getter / setter pair, if / else redefinitions, @overload stubs: their reader Scopes
  map to one node) is never a TYPED caller — one scope's REPLACE could remove the tuples the other produced — but
  the audit, which only lowers grades, judges it over the UNION of those scopes' sites (stats audit_shared):
  werkzeug's EnvironBuilder.content_type getter / setter `self.headers.get()` -> Client.get.

INTEGRATION CONTRACT (slice S2, build/sync.py)
  * Call `calls, stats = typed_calls.link(root, nodes, calls, is_test=_is_test_path, engine=engine,
    sources=srcs)` after `_unkey_receiver_calls` and before `_attach_bare_calls` (guarded lazy import; a missing
    module is a no-op). `engine` = the RESOLVED extractor `_rebuild_code` returns ('graphify' | 'ast'; omitted
    -> graphify's attribution, see ATTRIBUTION). `sources` = {rel: the graphify_extract._Source the extraction
    already read and parsed that file with}, so the readers never read or parse a file twice (design 2.1); a
    file it does not hold (or no `sources`) is read through `source=` (one shared reader) or a fresh
    _Source(root) (read and parsed again: slower, same result).
  * Every callee this pass adds is a CallKey (graphify_extract.is_call_key) naming a node of `nodes` — the push
    must honour a marked key per tuple, whatever DEEPMAP_PRECISE_CALLS says.
  * Private node attributes (never pushed — _upsert_nodes writes a fixed field list):
      call_typed  {target key: grade} on a caller this pass keyed. `_resolve_calls` binds (C, t) with exactly that
                  grade (skipping bind_of / T3#6, keeping the family / F8b / Go `_test.go` drops).
      call_weak   sorted [target key, ...]: after every other rule, an existing (C, t) edge becomes AMBIGUOUS.
                  Never holds a key of the same caller's call_typed.
      call_member the existing G5 attribute; a REPLACE removes m from it (the key is deleted when it empties).
      call_lines  {"key": {target key: line}, "name": {...}} (contract C1): every key this pass emits gets the first
                  line of a site that produced it in "key" (the minimum with a line already there); "name", written
                  by graphify_extract.map_graph / the ast extractor, is kept as it is. sync._call_lines reads it for
                  the :CALLS edge's display-only r.line. A NEW dict is set, so the rollback restores the old one.
  * `stats` (JSON-serialisable; `seconds` is the only non-deterministic value) for one stderr line
    (`describe(stats)`) and the graph-build manifest's `call_linking` block — never `_Extraction.info`:
      version, engine, typed_langs, audit_langs, shapes, replace, tests, nonprod, unknown_langs, unknown_shapes,
      unknown_iface, langs_disabled {lang: reason}, files, files_failed, files_capped,
      sites {typed, external, absent, unknown}, by_lang {lang: {...}; C# also lookup {rule: hits}, dispatch {...}
      (interface dispatch), virtual {...} (virtual dispatch), overloads {...} (argument applicability)},
      by_shape {shape: {sites, emitted}}, groups {replace, add, moot},
      vetoes {unknown_site, nested, text, census, span, replace_off, dispatch}, removed, emitted, new_pairs,
      override_kept, override_keyed, override_unseen, sources_shared,
      grades {EXTRACTED, INFERRED, AMBIGUOUS}, candidate_sets, candidate_skipped, too_many, skipped_policy,
      skipped_family, skipped_self, skipped_nested, skipped_interface, call_member_cleared, audit_weak,
      audit_shared, notes [...], error, seconds.
    On an internal error every counter is the skeleton's (nothing was changed): only `error`, `notes` and
    `seconds` carry information — stamp nothing else from such a run.

READER CONTRACT (slices S3b Python, S3c TypeScript/JavaScript, S9 wave 2)
  A reader is a sibling module named by LanguageSpec.reader exposing `make_reader(spec) -> Reader` (subclass the
  `Reader` below or duck-type it):
    version            a str (stamped on FileFacts.reader_version for diagnostics)
    canary() -> bool   True iff its parser works here (a tiny fixed snippet parses to the expected facts). False
                       or an exception disables ONLY that language (langs_disabled[lang] = "canary").
    facts(rel, source) -> FileFacts | None   Phase A: a pure function of (rel, the file's bytes, reader version,
                       grammar versions). `source` is the shared reader of the sync (graphify_extract._Source:
                       raw(rel) / lines(rel) / tree(rel) / ts_tree(rel)); read each file only through it. None
                       or an exception -> the file has no facts (files_failed); a malformed FileFacts likewise.
                       Files over FILE_MAX_LINES are never handed to a reader.
    binder(ctx: BinderContext) -> Binder     Resolves this language's imports to project files (Python by import
                       ROOT, never by suffix; TypeScript through typescript_edges._Modules).
  The IR below is FROZEN (IR_VERSION). Conventions every reader keeps:
    * Lines are 1-based and numbered like the engine (graphify_extract._engine_lines: '\\n' only); columns are
      0-based UTF-8 BYTE offsets (what ast col_offset and tree-sitter give).
    * Scope = a function-like node (def, method, arrow / function expression, lambda). `line` = the def /
      declaration line; `lines` = every other line the engine may start its node on (decorators, the variable
      declarator line of an arrow); `end_line` = the span's last line. `parent` = the index of the nearest
      ENCLOSING scope (a method of a top-level class has -1: a class body is not a scope) and must be smaller
      than the scope's own index. A site inside a class body nested in a function belongs to that function.
    * Scope.kind (SCOPE_KINDS) names the SYNTAX, because it decides whether the engine attributes a nested
      scope's calls to the enclosing caller (ATTRIBUTION above). Python: "function" (a def outside a class body),
      "method" (a def in a class body, local classes included), "lambda", "comprehension" (incl. a generator
      expression). JavaScript / TypeScript: "function_decl" (`function f() {}`), "generator_decl"
      (`function* g() {}`), "function_expr" (a `function` / `function*` EXPRESSION, also as an object value or a
      class field value), "arrow" (also as a class field value), "method_def" (a class / object-literal method,
      getter, setter or constructor). Any other value (the legacy JS / TS "function" / "method") is uncertain:
      that scope's sites are never emitted from an enclosing caller and block a REPLACE of their names there.
    * `cls` = the index (FileFacts.classes) of the class whose `self` / `this` this scope sees: its method, or the
      method an arrow / a TS class field initializer is nested in (an arrow inherits `this`); -1 otherwise.
      `self_kind` says what SELF means in this scope: "inst" (a method; Python's first parameter while never
      rebound, `this`), "class" (classmethod / static method), "" (SELF unknown here — then never emit SELF;
      `this` in a JS `function` expression or an object-literal method is "").
    * Receivers: the expression naming the instance is SELF (never IDENT("self")) — the audit and G5 rely on it;
      a zero-argument `super()` / `super` is SUPER (a two-argument `super(X, self)` is SUPER only when X is the
      enclosing class, else OTHER: the core always walks the MRO after the CALLER's class); `x` is IDENT; `a.b`
      is FIELD(a, b); `f(...)` is CALL(None, f) and `a.f(...)` CALL(a, f); `new T()` NEW(T); a syntactic type
      reference CLASSREF(T); a name the reader KNOWS is an import of a module MODULE(name); a literal LITERAL;
      anything else OTHER.
    * Sites: (line, col, member, recv, kind, argc). kind "member": recv.member(...); "bare": member(...) with no
      receiver (recv None); "ctor": `new T(...)` (recv CLASSREF(T), member = T's last name). argc = positional
      argument count as written, -1 unknown. EVERY call the parser sees in a scope's own body is either a Site of
      that scope or counted in `all_names` only (all_names[m] = every call of m over the FULL span, nested scopes
      included; None = not provided). A call in a decorator or a default argument runs in the ENCLOSING scope:
      record it there (or nowhere) — the caller's textual veto still counts it (its span starts at the first
      decorator line) and so blocks a REPLACE of that name, which is the safe outcome. (Python: such a default
      argument of a nested def, recorded in the enclosing scope on the def's own lines, is detached under
      graphify — the core sees that from the lines. JS / TS: never record a nested function's parameter
      defaults or decorators as sites — graphify does not attribute them; the census still counts them.)
      `// @ts-ignore`, `// @ts-expect-error` and `// @ts-nocheck` need no reader help: the core never judges a
      site `absent` where LanguageSpec.unchecked markers switch the compiler's check off.
    * Bindings: params {name: Binding("param", type=TypeRef|None)}; locals {name: [Binding, ...]} = EVERY
      binding of the name in that scope (a name bound in any unreadable way — for / with / except / walrus /
      augassign / tuple target / del / global / nonlocal / local import / nested def or class / match capture —
      goes into `poisoned` instead). `x = f(...)` is Binding("call", value=CALL(...)); `x = new T()` / an
      annotated constructor is "ctor"; `x: T = ...` is "ann"; `x = y.z` is "field" (value = the receiver
      expression; a class or module value is never typed); `x = None` is "none"; anything else "unknown".
      FileFacts has no file-level `poisoned`: a module-level name bound in an unreadable way (a for / with /
      except / tuple-unpack / walrus target, a module-level `del` / `global`) MUST appear in `globals` as
      [Binding("unknown")] — left out, it reads as unbound, and an unbound builtin name (`input`, `open`) is
      judged external, which would REPLACE-remove the engine's tuple.
      Binding.scope = the index of the scope whose names the expression uses (-1: file level) — a class field
      bound in a method (`self.f = T()`, `self.f = p`) carries that method's scope.
    * Imports: {local: [Import, ...]} (a name imported twice keeps both entries); a star import is stored under
      the local "*" (then no unbound name of the file is ever builtin or free; a WRITTEN one — with its spec and
      line — also leaves unknown every file-level name bound before it, which it may rebind; a spec-less "*" is
      a reader's marker of an unread region). Classes: top-level ones have
      outer = -1 and scope = -1; `bases` = the classes whose MEMBERS it inherits (TS / JS `extends`, never
      `implements`), in declaration order, WITHOUT the language's neutral bases; `fields` =
      every binding of `self.f` / class-body `f` across the class; `methods` {name: [MethodFacts]} (overloads ->
      several; flags from {"static", "class", "property", "abstract"}; arity = (min, max) positional arguments a
      call passes, max -1 = unbounded); `dynamic` = attributes may appear at run time (Python __getattr__, a
      metaclass, an unknown class decorator, setattr in the body, Lombok); `open` = more members may exist
      than declared (TS declaration merging, index signature, `declare`) — only `absent` cares.
    * decls = {(line, col)} of every declared identifier (def / function / method / class / object-literal
      method names): the textual veto skips them. patches = [(TypeRef, attr)] for module-level `C.attr = ...`.
    * A TypeRef naming a type parameter in scope is kind "unknown" (Scope / ClassFacts.type_params are only a
      double check). Neutral / opaque / builtin names follow LanguageSpec. TypeScript / JavaScript: only
      `T | null | undefined` is a union that binds and a local has one binding (LanguageSpec.unions=False also
      enforces both here).
  Binder (subclass `Binder` or duck-type it; every method may return None / RES_UNKNOWN; exceptions are caught):
    resolve_import(rel, imp) -> Resolution  module(rel2) | symbol(rel2, name) | namespace(token) | external(stems)
                                            | unknown. rel2 must be a graph file (a node's source_file). For an
                                            external, the IMPORT's kind says what it is: module / package /
                                            namespace -> an external module (`mod.f()` is external); named /
                                            default / static -> an external symbol (only a bare call of it is).
    submodule(module, attr) -> Resolution|None  `module` is a rel or a namespace token: Python's `pkg.sub`.
    resolve_free(rel, name) -> Resolution|None  a name the file does not bind (Java same-package, Go package).
"""
from __future__ import annotations

import bisect
import gc
import importlib
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

IR_VERSION = 1
STATS_VERSION = 4

# ================================================================================================
# The frozen IR (IR_VERSION 1). Readers build it; Phase B (`_Linker`) consumes it.
# ================================================================================================

TYPEREF_KINDS = frozenset({"name", "qualified", "union", "nullable", "generic", "self", "classof", "builtin",
                           "unknown"})
RECEIVER_KINDS = frozenset({"SELF", "SUPER", "IDENT", "FIELD", "CALL", "NEW", "CLASSREF", "MODULE", "LITERAL",
                            "OTHER"})
BINDING_KINDS = frozenset({"ann", "ctor", "call", "field", "param", "none", "unknown"})
IMPORT_KINDS = frozenset({"module", "named", "default", "namespace", "package", "static", "star"})
SITE_KINDS = frozenset({"bare", "member", "ctor"})
SELF_KINDS = frozenset({"", "inst", "class"})
METHOD_FLAGS = frozenset({"static", "class", "property", "abstract"})
RESOLUTION_KINDS = frozenset({"module", "symbol", "namespace", "external", "unknown"})
# Scope.kind names the syntax (reader contract). Another value is legal IR and reads as UNCERTAIN attribution.
SCOPE_KINDS = frozenset({"function", "method", "lambda", "comprehension",                    # Python
                         "function_decl", "generator_decl", "function_expr", "arrow", "method_def",   # JS / TS
                         "ctor", "accessor", "local_function", "anonymous_method"})   # C# (with method, lambda)
ENGINES = ("graphify", "ast")


class TypeRef(NamedTuple):
    """A written type. kind: name (`name`) | qualified (`parts`: ("mod", "Cls")) | union (`args`: members) |
    nullable (`args`: (inner,)) | generic (`args`: (head, *params) — only the head is read) | self (the enclosing
    class; on a method's return: the RECEIVER's class) | classof (`args`: (inner,) — `Type[X]`, a class object) |
    builtin (`name`: a builtin / primitive type: `str`, `string`; None-like names mean "no value") | unknown."""
    kind: str
    name: str = ""
    parts: tuple = ()
    args: tuple = ()


class Receiver(NamedTuple):
    """The receiver expression of a call. kind: SELF | SUPER | IDENT (`name`) | FIELD (`inner`.`name`) | CALL
    (`inner`.`name`(...), inner None = a bare call `name(...)`) | NEW (`type`: new T()) | CLASSREF (`type`: a
    syntactic type reference) | MODULE (`name`: a local name the reader knows is a module import) | LITERAL
    (`type`: optional builtin TypeRef) | OTHER."""
    kind: str
    name: str = ""
    inner: "Receiver | None" = None
    type: "TypeRef | None" = None


class Binding(NamedTuple):
    """One binding of a name. kind: ann (`type`) | ctor (`type`) | call (`value`: a CALL receiver) | field
    (`value`: the bound expression) | param (`type`: the annotation or None) | none (`= None`) | unknown.
    `scope`: the index of the scope whose names the expression uses (-1: file level)."""
    kind: str
    type: "TypeRef | None" = None
    value: "Receiver | None" = None
    line: int = 0
    scope: int = -1


class Import(NamedTuple):
    """`local` bound by an import. kind: module (`import a.b as x`, CJS `x = require(...)`) | named (`from a import
    b`, `import { b }`) | default | namespace (`import * as ns`) | package (`import a.b` binding `a`) | static
    (Java static import) | star (stored under local "*"). `spec` as written (relative dots / './x' kept)."""
    local: str
    kind: str
    spec: str
    name: str = ""
    line: int = 0


class Site(NamedTuple):
    """One call site: `member` called via `recv` (kind "member"), bare (kind "bare", recv None) or constructed
    (kind "ctor", recv CLASSREF(T)). col is a 0-based UTF-8 byte offset; argc -1 = unknown."""
    line: int
    col: int
    member: str
    recv: "Receiver | None"
    kind: str
    argc: int = -1


class MethodFacts(NamedTuple):
    """One definition of a method (overloads -> several). flags ⊆ METHOD_FLAGS; returns = the return TypeRef;
    arity = (min, max) positional arguments a call passes (max -1 = unbounded); lines = decorator lines."""
    line: int
    flags: frozenset = frozenset()
    returns: "TypeRef | None" = None
    arity: tuple = (0, -1)
    lines: tuple = ()


class FunctionFacts(NamedTuple):
    """A module-level function (a conditional redefinition -> several entries -> never resolved)."""
    name: str
    line: int
    returns: "TypeRef | None" = None
    lines: tuple = ()
    arity: tuple = (0, -1)


@dataclass
class ClassFacts:
    """One class (interface / struct / trait ...). See the reader contract in the module docstring."""
    name: str
    line: int
    end_line: int = 0
    kind: str = "class"
    bases: tuple = ()
    fields: dict = field(default_factory=dict)       # {field: [Binding, ...]}
    methods: dict = field(default_factory=dict)      # {name: [MethodFacts, ...]}
    dynamic: bool = False
    open: bool = False
    lines: tuple = ()
    outer: int = -1                                  # enclosing class index (-1: none)
    scope: int = -1                                  # enclosing function scope index (-1: module level)
    type_params: frozenset = frozenset()


@dataclass
class Scope:
    """A function-like node: its span, bindings and call sites. See the reader contract."""
    name: str
    line: int
    end_line: int = 0
    lines: tuple = ()
    parent: int = -1
    cls: int = -1
    self_kind: str = ""
    params: dict = field(default_factory=dict)       # {name: Binding("param", ...)}
    locals: dict = field(default_factory=dict)       # {name: [Binding, ...]}
    poisoned: frozenset = frozenset()
    sites: list = field(default_factory=list)        # [Site, ...]
    all_names: "dict | None" = None                  # {name: full-span call count} or None
    type_params: frozenset = frozenset()
    kind: str = "function"


@dataclass
class FileFacts:
    """Phase A's output for one file."""
    rel: str
    lang: str
    imports: dict = field(default_factory=dict)      # {local: [Import, ...]}
    classes: list = field(default_factory=list)      # [ClassFacts, ...]
    functions: dict = field(default_factory=dict)    # {name: [FunctionFacts, ...]}
    globals: dict = field(default_factory=dict)      # {name: [Binding, ...]} module-level bindings
    scopes: list = field(default_factory=list)       # [Scope, ...]
    decls: frozenset = frozenset()                   # {(line, col)} of declared identifiers
    patches: list = field(default_factory=list)      # [(TypeRef, attr), ...]
    reader_version: str = ""


class Resolution(NamedTuple):
    """A binder's answer. kind: module (`rel`) | symbol (`rel`, `name`) | namespace (`name`: an opaque token) |
    external (`stems`: the top-level names the conservative stem test checks) | unknown."""
    kind: str
    rel: str = ""
    name: str = ""
    stems: tuple = ()


RES_UNKNOWN = Resolution("unknown")


@dataclass
class BinderContext:
    """What a reader's binder gets: this language's graph files, every file's facts (all languages read this
    sync, so a .ts import of a .js module resolves), every graph file and the node list (read-only)."""
    root: str
    lang: str
    rels: tuple
    facts: dict
    graph_files: frozenset
    nodes: tuple = ()


class Binder:
    """The default binder: resolves nothing (every import UNKNOWN)."""

    def resolve_import(self, rel, imp):  # noqa: D401 — interface
        return RES_UNKNOWN

    def submodule(self, module, attr):
        return None

    def resolve_free(self, rel, name):
        return None


class Reader:
    """The reader interface (a reader module's make_reader(spec) returns one)."""
    version = "0"

    def canary(self) -> bool:
        return True

    def facts(self, rel, source):
        return None

    def binder(self, ctx: BinderContext):
        return Binder()


# IR helpers (readers and gates use them; plain constructors work too)
def t_name(n: str) -> TypeRef:
    return TypeRef("name", n)


def t_qual(*parts: str) -> TypeRef:
    return TypeRef("qualified", parts=tuple(parts))


def t_union(*members: TypeRef) -> TypeRef:
    return TypeRef("union", args=tuple(members))


def t_nullable(inner: TypeRef) -> TypeRef:
    return TypeRef("nullable", args=(inner,))


def t_generic(head: TypeRef, *params: TypeRef) -> TypeRef:
    return TypeRef("generic", args=(head,) + tuple(params))


def t_self() -> TypeRef:
    return TypeRef("self")


def t_classof(inner: TypeRef) -> TypeRef:
    return TypeRef("classof", args=(inner,))


def t_builtin(n: str) -> TypeRef:
    return TypeRef("builtin", n)


T_UNKNOWN = TypeRef("unknown")
R_SELF = Receiver("SELF")
R_SUPER = Receiver("SUPER")
R_OTHER = Receiver("OTHER")


def r_ident(x: str) -> Receiver:
    return Receiver("IDENT", x)


def r_field(inner: Receiver, f: str) -> Receiver:
    return Receiver("FIELD", f, inner)


def r_call(inner: "Receiver | None", m: str) -> Receiver:
    return Receiver("CALL", m, inner)


def r_new(t: TypeRef) -> Receiver:
    return Receiver("NEW", type=t)


def r_classref(t: TypeRef) -> Receiver:
    return Receiver("CLASSREF", type=t)


def r_module(x: str) -> Receiver:
    return Receiver("MODULE", x)


def r_literal(builtin: str = "") -> Receiver:
    return Receiver("LITERAL", type=t_builtin(builtin) if builtin else None)


# ================================================================================================
# The language registry
# ================================================================================================

@dataclass(frozen=True)
class LanguageSpec:
    """One language. `reader` = the sibling module exposing make_reader(spec). `family` = an
    edge_resolve.lang_family name (a binder never resolves outside it; emission re-checks). `absent_ok`: the
    compiler rejects a call of an undeclared member (so a closed chain without m is `absent`). `mro`: c3 |
    linear | depth (Go embedding: a tie at one depth is unknown). `implicit_this`: a bare m() may be a member
    call on the enclosing class. `lexer`: the textual veto's string / comment mask (python | js | c | ruby).
    `builtins`: builtin TYPE names (constructors / global objects) — an unbound one is external; `builtin_funcs`:
    builtin functions — a bare call of an unbound one is external, its result unknown; `opaque`: type names that
    mean "anything" (never external); `neutral`: bases a class chain skips; `dunders`: `__x__` is never typed;
    `unions`: a union of project classes (an annotation, or a local bound to different classes) is a candidate set
    — False for TypeScript / JavaScript, where only `T | null | undefined` binds and a local has ONE binding
    (design 3.2), whatever the reader emits.
    Attribution under graphify (module docstring, ATTRIBUTION): `nested_attributed` = the Scope.kind values the
    engine's call walk enters (their calls belong to the enclosing caller); `nested_detached` = its boundary kinds
    (their calls belong to no enclosing caller); any other kind is uncertain. `header_detached`: a site the
    reader records in a scope on the lines of a DETACHED child scope is that child's header (a Python default
    argument: inside graphify's function_definition boundary). `unchecked`: comment markers that switch the
    compiler's member check off — the whole file for `unchecked[0]` entries, the next lines for `unchecked[1]`
    entries — so no site there is ever `absent`. `static_split`: a static member is reachable only through the
    class object and an instance member only through an instance (TS / JS); otherwise a member reaches both.
    graphify's SYMBOL-RESOLUTION post-pass (ATTRIBUTION): `symbol_walk` = the Scope.kind values of a TOP-LEVEL
    caller (parent -1) whose WHOLE body it walks — nested scopes and their headers included — giving every BARE call
    it resolves there to that caller; `symbol_imports_only`: it resolves only a name an import binds (Python: a
    `from` import; else any bare call — JS / TS resolve lexical aliases of imports too); `symbol_plain`: only an
    UNDECORATED caller is walked (a Python decorated def is a decorated_definition at the root, never walked).
    `lookup_rules` (C# only; every other language has none, so its results are byte-identical): iface_dispatch (a
    site typed to an interface's own member through an interface receiver links that member at INFERRED plus its
    project implementers: one -> INFERRED, 2..MAX_TARGETS -> AMBIGUOUS each, more -> none; a test-file implementer
    only for a test caller; with the reader's tables, a site whose whole set is known may REPLACE, and a static
    interface member with a body is an exact call; _Linker.iface_dispatch), partials (the parts of one partial type
    are ONE class: _Linker.merge_partials, binder.partials()), member_scope (a simple name is first a member of the
    enclosing class
    chain, a field there typed by its declaration; an external class base may hide one: _Linker.member_scope, the C#
    field rule in _field_type, no bare-call constructor), using_static (a bare call missing from a fully-project
    chain reaches exactly one `using static` project method: binder.static_imports(rel)), extensions (a member
    missing from a fully-project chain reaches exactly one project extension method on that chain:
    binder.extensions(rel, m); such a patch never marks the class patched), nested_types (a type name's head is
    first a nested type of the enclosing class, its project base classes, then of each outer class and its bases:
    _Linker.cs_type_head), overloads (a member hit with a rival of its name and arity — a base-class method of
    another signature, a project extension method that may apply — is typed only when the call's argument types are
    known to fit the hit, and then never EXTRACTED, nor when the hit is an override and a class between it and its
    slot's declarer holds a rival; signatures compare normalised, a generic base's type parameters substituted along
    the base lists: _Linker.cs_overloads, cs_sig_norm, _cs_tcmp) and virtual_dispatch (a call of a virtual /
    abstract / override class member through a receiver whose run-time class may be a subclass links the declared
    member at INFERRED plus the project overrides that may run — never a subclass closing a generic receiver class
    with other type arguments — counted like interface implementers; a REPLACE then keeps only those overrides
    reachable: _Linker.virtual_dispatch, override_reach). These read the reader's private
    `_cs` table (FileFacts._cs: per-site argument receivers, per-method modifiers and parameter types, signatures,
    type parameters, base lists); a reader without one gets the rules above only."""
    name: str
    exts: tuple
    family: str
    reader: str
    wave: int = 1
    absent_ok: bool = False
    mro: str = "linear"
    implicit_this: bool = False
    lexer: str = "c"
    builtins: frozenset = frozenset()
    builtin_funcs: frozenset = frozenset()
    opaque: frozenset = frozenset()
    neutral: frozenset = frozenset()
    dunders: bool = False
    unions: bool = True
    nested_attributed: frozenset = frozenset()
    nested_detached: frozenset = frozenset()
    header_detached: bool = False
    unchecked: tuple = ((), ())
    static_split: bool = False      # TS / JS: a static member lives on the class object only, an instance
    #                                 member on instances only (Python, Java: either reaches both)
    symbol_walk: frozenset = frozenset()
    symbol_imports_only: bool = False
    symbol_plain: bool = False
    lookup_rules: frozenset = frozenset()     # C# only (see the docstring)


_PY_BUILTIN_TYPES = frozenset({
    "str", "bytes", "int", "float", "complex", "bool", "list", "dict", "set", "frozenset", "tuple", "bytearray",
    "memoryview", "range", "slice", "object", "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "RuntimeError", "NotImplementedError", "OSError", "IOError", "StopIteration",
    "LookupError", "ArithmeticError", "ZeroDivisionError", "AssertionError", "ImportError", "ModuleNotFoundError",
    "NameError", "PermissionError", "FileNotFoundError", "FileExistsError", "TimeoutError", "ConnectionError",
    "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError", "RecursionError", "MemoryError", "OverflowError",
    "KeyboardInterrupt", "SystemExit", "GeneratorExit", "Warning", "DeprecationWarning", "UserWarning",
    "RuntimeWarning"})
_PY_BUILTIN_FUNCS = frozenset({
    "print", "len", "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr", "sorted", "reversed",
    "min", "max", "sum", "any", "all", "map", "filter", "zip", "enumerate", "iter", "next", "open", "repr", "hash",
    "id", "abs", "round", "divmod", "pow", "format", "vars", "dir", "callable", "chr", "ord", "hex", "oct", "bin",
    "globals", "locals", "input", "ascii", "compile", "eval", "exec", "breakpoint", "help", "__import__", "type",
    "super", "property", "staticmethod", "classmethod", "aiter", "anext"})
_JS_BUILTIN_TYPES = frozenset({
    "Object", "Array", "String", "Number", "Boolean", "Symbol", "BigInt", "Date", "RegExp", "Error", "TypeError",
    "RangeError", "SyntaxError", "ReferenceError", "Map", "Set", "WeakMap", "WeakSet", "WeakRef", "Promise", "JSON",
    "Math", "Reflect", "Proxy", "Intl", "ArrayBuffer", "SharedArrayBuffer", "DataView", "Uint8Array", "Int8Array",
    "Uint16Array", "Int16Array", "Uint32Array", "Int32Array", "Float32Array", "Float64Array", "BigInt64Array",
    "BigUint64Array", "URL", "URLSearchParams", "TextEncoder", "TextDecoder", "AbortController", "Headers",
    "FormData", "Blob", "console", "globalThis", "window", "document", "navigator", "process", "Buffer"})
_JS_BUILTIN_FUNCS = frozenset({
    "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "setImmediate", "require", "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI",
    "queueMicrotask", "structuredClone", "fetch", "alert"})

# graphify's call walk (extract.py `walk_calls`): Python stops at `function_definition` (a nested def, a method of
# a local class, async defs) and walks lambdas / comprehensions / class bodies; JS / TS stop at
# `function_declaration` / `arrow_function` / `method_definition` and walk function expressions, generator
# declarations and class bodies (field initializers). Locked against the installed engine by
# tests/typed_calls_core_gates.py (section R) on graphify 0.8.39 and 0.8.51.
_PY_ATTRIBUTED = frozenset({"lambda", "comprehension"})
_PY_DETACHED = frozenset({"function", "method"})
_JS_ATTRIBUTED = frozenset({"function_expr", "generator_decl"})
_JS_DETACHED = frozenset({"function_decl", "arrow", "method_def"})
# graphify's symbol-resolution post-pass (extract.py `_collect_python_symbol_resolution_facts` /
# `_collect_js_symbol_resolution_facts`, the same on 0.8.39 and 0.8.51): it walks the WHOLE body of each root
# `def` / `async def` (Python, never a decorated one) and each root `function f() {}` / `const f = () => {}` (JS /
# TS) with no function boundary, and gives every bare call it resolves to an imported symbol to that top-level
# caller. Locked against the installed engine by tests/typed_calls_core_gates.py (section R).
_PY_SYMBOL_WALK = frozenset({"function"})
_JS_SYMBOL_WALK = frozenset({"function_decl", "arrow"})
_TS_UNCHECKED = (("@ts-nocheck",), ("@ts-ignore", "@ts-expect-error"))
# C#: graphify's call walk stops only at method_declaration (the same on 0.8.39 and 0.8.51), so a lambda's, an
# anonymous method's and a local function's calls belong to the enclosing member; a method, a constructor and an
# accessor are members of their own (locked by tests/typed_calls_core_gates.py section R). The builtin list stays
# SHORT on purpose: a name read as external may drive a REPLACE removal, so only types no project redefines belong.
_CS_ATTRIBUTED = frozenset({"lambda", "anonymous_method", "local_function"})
_CS_DETACHED = frozenset({"method", "ctor", "accessor"})
_CS_BUILTIN_TYPES = frozenset({
    "Console", "Math", "Convert", "String", "Int32", "Int64", "Decimal", "Double", "Boolean", "Guid", "DateTime",
    "DateTimeOffset", "TimeSpan", "Task", "ValueTask", "List", "Dictionary", "HashSet", "Enumerable", "StringBuilder",
    "Exception", "ArgumentException", "ArgumentNullException", "InvalidOperationException",
    "NotImplementedException", "NotSupportedException", "Environment", "GC", "Activator", "Array", "Tuple",
    "Nullable", "Interlocked", "Volatile"})
_CS_LOOKUP_RULES = frozenset({"iface_dispatch", "partials", "member_scope", "using_static", "extensions",
                              "nested_types", "overloads", "virtual_dispatch"})
IFACE_MODES = ("static", "impl")

LANGUAGES: dict = {s.name: s for s in (
    LanguageSpec("python", (".py", ".pyi"), "python", "receiver_facts_py", wave=1, mro="c3", lexer="python",
                 builtins=_PY_BUILTIN_TYPES, builtin_funcs=_PY_BUILTIN_FUNCS, opaque=frozenset({"Any", "object"}),
                 neutral=frozenset({"object", "Generic", "Protocol", "ABC"}), dunders=True,
                 nested_attributed=_PY_ATTRIBUTED, nested_detached=_PY_DETACHED, header_detached=True,
                 symbol_walk=_PY_SYMBOL_WALK, symbol_imports_only=True, symbol_plain=True),
    LanguageSpec("typescript", (".ts", ".tsx", ".mts", ".cts"), "js", "receiver_facts_ts", wave=1, absent_ok=True,
                 lexer="js", builtins=_JS_BUILTIN_TYPES, builtin_funcs=_JS_BUILTIN_FUNCS,
                 opaque=frozenset({"any", "unknown", "object", "Object", "Function"}), unions=False,
                 nested_attributed=_JS_ATTRIBUTED, nested_detached=_JS_DETACHED, unchecked=_TS_UNCHECKED,
                 static_split=True, symbol_walk=_JS_SYMBOL_WALK),
    LanguageSpec("javascript", (".js", ".jsx", ".mjs", ".cjs"), "js", "receiver_facts_ts", wave=1, lexer="js",
                 builtins=_JS_BUILTIN_TYPES, builtin_funcs=_JS_BUILTIN_FUNCS,
                 opaque=frozenset({"any", "unknown", "object", "Object", "Function"}), unions=False,
                 nested_attributed=_JS_ATTRIBUTED, nested_detached=_JS_DETACHED, static_split=True,
                 symbol_walk=_JS_SYMBOL_WALK),
    # wave 2 (opt-in by name; each needs its own D3 before any default) — readers land in S9 and fill the
    # attribution tables from graphify's boundary types (until then every nested scope there is uncertain)
    LanguageSpec("go", (".go",), "go", "receiver_facts_go", wave=2, mro="depth"),
    LanguageSpec("java", (".java",), "jvm", "receiver_facts_java", wave=2, absent_ok=True, implicit_this=True,
                 neutral=frozenset({"Object"})),
    # C#: opt-in (DEEPMAP_TYPED_CALLS=csharp; =1 is wave 1 only). Never `absent`: extension methods, partial parts in
    # a file that fails to parse, source generators and `dynamic` make a closed-chain miss unsafe.
    LanguageSpec("csharp", (".cs",), "dotnet", "receiver_facts_csharp", wave=2, absent_ok=False, implicit_this=True,
                 builtins=_CS_BUILTIN_TYPES, opaque=frozenset({"object", "Object", "dynamic"}),
                 neutral=frozenset({"object", "Object"}), unions=False, nested_attributed=_CS_ATTRIBUTED,
                 nested_detached=_CS_DETACHED, lookup_rules=_CS_LOOKUP_RULES),
    LanguageSpec("rust", (".rs",), "rust", "receiver_facts_rust", wave=2),
    LanguageSpec("ruby", (".rb",), "ruby", "receiver_facts_ruby", wave=2, implicit_this=True, lexer="ruby"),
)}

WAVE1_LANGS = ("python", "typescript", "javascript")
SHAPES = ("R1", "R2", "R3", "R4", "S", "K", "M", "C")
_TYPED_DEFAULT_LANGS: frozenset = frozenset()     # unset DEEPMAP_TYPED_CALLS -> these (S8 flips per D3)
_AUDIT_DEFAULT_LANGS: frozenset = frozenset()     # unset DEEPMAP_CALLS_AUDIT -> these (S8 flips per D1)
MAX_FILES_DEFAULT = 20000
MAX_TARGETS = 4
SPAN_MAX_LINES = 5000             # == sync._BARE_SCAN_MAX_LINES (gate-locked; a longer caller span is not judged)
FILE_MAX_LINES = 200_000          # the sync's per-file parse cap
MINIFIED_LINE = 2000              # == graphify_extract._MINIFIED_LINE (gate-locked): a span holding a line this long
                                  # is bundler / minifier output — the textual veto does not judge it (ADD)
REEXPORT_DEPTH = 6
_EVAL_DEPTH = 24
_MRO_MAX = 64
NONPROD_DIRS = frozenset({"examples", "example", "docs", "doc", "docs_src", "samples", "sample", "demo", "demos"})
CALLER_LABELS = frozenset({"Function", "Method", "Route"})
TARGET_LABELS = CALLER_LABELS
CLASS_LABELS = frozenset({"Class"})
GRADES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
_GRADE_RANK = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}
_NONE_TYPES = frozenset({"None", "NoneType", "null", "undefined", "void", "never"})
# What an EXTERNAL value is (_V.ref of an "ext" value) decides what a call on it means:
#   module       an external module (`import re`, `import * as path`, `const fs = require('fs')`): mod.f() is external
#   module-attr  one attribute of an external module (`os.path`): a member call is external; deeper is unknown
#   symbol       a name imported from an external module: a BARE call is external, but a member call is UNKNOWN
#                (`from flask_login import current_user; current_user.save()` reaches a project class through a proxy)
#   type         an instance (or the class) of an external class named by an annotation / `new`: a member call is
#                external
#   builtin      a builtin type or an instance of one (`str`, `dict()`, `new Map()`, a literal's type)
#   builtin_func a builtin function (`print`, `len`): a bare call is external; its result is never typed
#   literal      a literal (`"x".join(...)`)
# A call RESULT of anything but a builtin type is unknown (`get_user_model()()` returns a project class).
_EXT_MEMBER_OK = frozenset({"module", "module-attr", "type", "builtin", "literal"})
# What a FileFacts patch means beyond "attr of this one project class may be replaced":
#   loose  an attr patched onto anything but exactly one project class (a builtin's prototype —
#          `String.prototype.cap = ...`, a `declare global { interface String { cap(): string } }` member —,
#          window / globalThis, an external or unresolved name) is never an EXTERNAL member call in that language
#          family: `'x'.cap()`, `s.cap()` (s: string) and `fs.readFile()` after `fs.readFile = mock` are unknown
#          (the member called may be the project's). A loose "*" (a mixin no file reads, onto such a value) makes
#          every external member call of the family unknown. A patch a TEST file writes (a mock, a prototype
#          pollution test) counts for test callers only.
#   star   attr "*" (a mixin whose keys no file reads: `Object.assign(K.prototype, mk())`, a whole
#          `K.prototype = ...`) patches every member of K: no member of K is ever typed or absent.
PATCH_RULES = frozenset({"loose", "star"})
_EXT_VALUE_OK = frozenset({"type", "builtin", "literal"})
_EXT_BARE_OK = frozenset({"symbol", "builtin", "builtin_func"})

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

# C# argument applicability (LanguageSpec.lookup_rules overloads): the predefined types compared by name, and the
# implicit numeric conversions (C# spec 10.2.3). A constant's own conversions (an int literal to byte when in range,
# 0 to an enum) and user-defined conversions are not read: such an argument is never known to fit.
_CS_KNOWN = frozenset({"string", "bool", "char", "sbyte", "byte", "short", "ushort", "int", "uint", "long", "ulong",
                       "float", "double", "decimal"})
_CS_WIDEN = {
    "sbyte": frozenset({"short", "int", "long", "float", "double", "decimal"}),
    "byte": frozenset({"short", "ushort", "int", "uint", "long", "ulong", "float", "double", "decimal"}),
    "short": frozenset({"int", "long", "float", "double", "decimal"}),
    "ushort": frozenset({"int", "uint", "long", "ulong", "float", "double", "decimal"}),
    "int": frozenset({"long", "float", "double", "decimal"}),
    "uint": frozenset({"long", "ulong", "float", "double", "decimal"}),
    "long": frozenset({"float", "double", "decimal"}),
    "ulong": frozenset({"float", "double", "decimal"}),
    "char": frozenset({"ushort", "int", "uint", "long", "ulong", "float", "double", "decimal"}),
    "float": frozenset({"double"}),
}
_CS_VIRTUAL = frozenset({"virtual", "abstract", "override"})
_CS_ACCESS = frozenset({"public", "protected", "internal", "private"})
_NOINFO = object()          # a reader keeps no `_cs` table (FileFacts._cs) for that file: the C# rules reading it skip
# C# signatures compared across a base list (rule overloads: a base definition of the hit's own signature is the one
# the hit hides or overrides, never a rival; rules virtual_dispatch / iface_dispatch: only a definition of the slot's
# own signature continues, breaks or implements it): the System names of the predefined types (`System.Int32` is
# `int`; `dynamic` is `object` in a signature), the predefined value types (`int?` is Nullable<int>, while `string?` is
# only an annotation), and the token of a type the comparison cannot place (a class type parameter no base list fixes,
# an unread type): it is never "the same type" as anything, and never proven another one either (_cs_tcmp: None).
_CS_SYSTEM_ALIAS = {"Int32": "int", "Int64": "long", "Int16": "short", "Byte": "byte", "SByte": "sbyte",
                    "UInt32": "uint", "UInt64": "ulong", "UInt16": "ushort", "Char": "char", "Boolean": "bool",
                    "Double": "double", "Single": "float", "Decimal": "decimal", "String": "string",
                    "Object": "object", "dynamic": "object", "IntPtr": "nint", "UIntPtr": "nuint"}
_CS_VALUE_KW = frozenset({"bool", "char", "sbyte", "byte", "short", "ushort", "int", "uint", "long", "ulong", "float",
                          "double", "decimal", "nint", "nuint"})
_CS_UNK = ("?t",)
# Types that exist in a namespace of every .NET 6+ reference set (the BCL, Microsoft.Extensions.Logging): two
# unqualified spellings of one of these names in scopes that both import its namespace are that one type (_cs_unq_same)
_CS_KNOWN_NS = {
    "System": frozenset({"Guid", "DateTime", "DateTimeOffset", "TimeSpan", "Uri", "Exception", "Action", "Func",
                         "EventArgs", "EventHandler", "IDisposable", "IServiceProvider", "Type", "Version", "Lazy",
                         "IComparable", "IEquatable", "Span", "ReadOnlySpan", "Memory", "ReadOnlyMemory"}),
    "System.Threading": frozenset({"CancellationToken", "CancellationTokenSource", "SemaphoreSlim", "Timer"}),
    "System.Threading.Tasks": frozenset({"Task", "ValueTask", "TaskCompletionSource"}),
    "System.Collections.Generic": frozenset({"IEnumerable", "IList", "List", "Dictionary", "IDictionary",
                                             "ICollection", "IReadOnlyList", "IReadOnlyCollection",
                                             "IReadOnlyDictionary", "HashSet", "ISet", "KeyValuePair",
                                             "IAsyncEnumerable", "Queue", "Stack"}),
    "System.IO": frozenset({"Stream", "TextReader", "TextWriter", "FileInfo", "DirectoryInfo"}),
    "System.Linq": frozenset({"IQueryable", "IOrderedQueryable", "IGrouping", "ILookup"}),
    "System.Linq.Expressions": frozenset({"Expression"}),
    "System.Text": frozenset({"StringBuilder", "Encoding"}),
    "System.Net.Http": frozenset({"HttpClient", "HttpRequestMessage", "HttpResponseMessage"}),
    "Microsoft.Extensions.Logging": frozenset({"ILogger", "ILoggerFactory"}),
}


def _cs_tcmp(a, b) -> "bool | None":
    """Two normalised C# signature types (_Linker.cs_sig_norm): True (one type), False (two types: PROVEN) or None (the
    pass cannot tell). False only for two keywords, two project classes, a keyword against a project class, another
    generic arity / array rank, a type parameter against anything else, `X?` of a known value type against X (and
    what those decide inside a generic or an array). A class type parameter is its own type (`("tpv", declaring
    class, name)`: in a generic declaration `M(T)` and `M(Cart)` are two signatures, whatever T is constructed with).
    A name the pass did not resolve ("n": an external type, or an alias it does not read: an MSBuild <Using Alias>,
    C# 12's alias of any type) is one type with another only when both have its last segment and: both are written
    under one qualifier; or one is qualified and the other's scope sees that namespace before any other using
    (_cs_unq_is); or both are unqualified in scopes whose usings resolve a simple name alike (_cs_unq_same). Every
    other comparison of a name is None: never "two types", since either may be an alias of the other."""
    if not isinstance(a, tuple) or not isinstance(b, tuple) or not a or not b or a == _CS_UNK or b == _CS_UNK:
        return None
    ta, tb = a[0], b[0]
    if ta == "x" or tb == "x":
        return True if a == b else None
    if ta == "?" or tb == "?":
        if ta == tb:
            return _cs_tcmp(a[1] if len(a) > 1 else None, b[1] if len(b) > 1 else None)
        q, o = (a, b) if ta == "?" else (b, a)
        if o[:1] == ("g",) and len(o) == 3 and o[1][:2] == ("n", "Nullable"):
            r = _cs_tcmp(q[1] if len(q) > 1 else None, o[2])   # a Nullable<X> not proven System's (an implicit
            return False if r is False else None                # `using System;`): maybe X?
        r = _cs_tcmp(q[1] if len(q) > 1 else None, o)
        return False if r is False or (r is True and len(q) > 2 and q[2] is True) else None
    if ta == "n" or tb == "n":
        if ta != tb:
            o = b if ta == "n" else a
            return False if o[0] in ("tpv", "mtp") else None
        if len(a) < 4 or len(b) < 4 or a[1] != b[1]:
            return True if a == b else None    # another last segment: either may be an alias
        qa, qb = a[2], b[2]
        if qa is None and qb is None:
            return _cs_unq_same(a[3], b[3], a[1])
        if qa is not None and qb is not None:
            return True if qa == qb else None  # another qualifier: relative, or a namespace alias it does not read
        q, lev = (qa, b[3]) if qa is not None else (qb, a[3])
        return _cs_unq_is(".".join(q), lev)
    if ta != tb:
        g, c = (a, b) if ta == "g" else (b, a)
        if g[0] == "g" and c[0] == "c" and len(g) > 1 and g[1] == c:
            return None                        # one project class key read with and without type arguments
        return False
    if ta in ("g", "[]"):
        if len(a) != len(b) or (ta == "[]" and a[2:] != b[2:]):
            return False
        pairs = zip(a[1:2], b[1:2]) if ta == "[]" else zip(a[1:], b[1:])
        got = [_cs_tcmp(x, y) for x, y in pairs]
        return False if False in got else (None if None in got else True)
    if ta == "c":
        return len(a) > 1 and len(b) > 1 and a[1] == b[1]
    return a == b                                  # ("b", keyword), ("mtp", position), ("tpv", class, name)


def _cs_ulevels(lev) -> "tuple | None":
    """The using levels of a scope's lookup order (cs_scope_levels: ("m", namespace) / ("u", namespaces)), nearest
    first: the non-empty ones, and always the compilation unit's (its usings with the project's global ones) last."""
    if not isinstance(lev, tuple) or not lev or lev[-1][:1] != ("u",):
        return None
    return tuple(v for k, v in lev[:-1] if k == "u" and v) + (lev[-1][1],)


def _cs_unq_same(la, lb, seg="") -> "bool | None":
    """Two unqualified spellings of external simple name seg, in scopes la / lb (cs_scope_levels): True when the name
    resolves alike — the same using levels; or the same ones but the compilation unit's, where one set holds the
    other (a type the smaller set imports is the only one of that name the larger may import there: two would be an
    ambiguity error) or both import a namespace known to hold seg (_CS_KNOWN_NS: that type, for the same reason);
    else None (another using may import another type of that name first). The members of an enclosing namespace are
    not read (an external type there: a known gap)."""
    ua, ub = _cs_ulevels(la), _cs_ulevels(lb)
    if ua is None or ub is None:
        return None
    if ua == ub:
        return True
    if len(ua) == len(ub) and ua[:-1] == ub[:-1]:
        if ua[-1] <= ub[-1] or ub[-1] <= ua[-1]:
            return True
        if any(seg in _CS_KNOWN_NS.get(ns, ()) for ns in ua[-1] & ub[-1]):
            return True
    return None


def _cs_unq_is(ns, lev) -> "bool | None":
    """Is an unqualified external name, in a scope of lookup order lev (cs_scope_levels), the type namespace ns holds?
    True when ns is one of the scope's enclosing namespaces, or imported by its nearest non-empty using level, before
    any other using level (a nearer using may import another type of that name first: None); None when the scope
    does not see ns (only an MSBuild implicit using may)."""
    if not isinstance(lev, tuple):
        return None
    for kind, val in lev:
        if kind == "m":
            if val == ns:
                return True
            continue
        if ns in val:
            return True
        if val:
            return None
    return None


# ================================================================================================
# Flags
# ================================================================================================

def _env_get(env, name) -> str:
    try:
        return str((env if env is not None else os.environ).get(name, "") or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable environment reads as unset
        return ""


def _lang_set(raw: str, default, registry) -> tuple:
    """(languages, unknown names) for a DEEPMAP_TYPED_CALLS-style value."""
    low = raw.strip().lower()
    if not low:
        return frozenset(n for n in default if n in registry), []
    if low in _FALSE:
        return frozenset(), []
    if low in _TRUE:
        return frozenset(n for n in WAVE1_LANGS if n in registry), []
    names = [x.strip() for x in low.split(",") if x.strip()]
    return frozenset(n for n in names if n in registry), sorted({n for n in names if n not in registry})


def typed_langs(env=None, registry=None) -> frozenset:
    """The languages DEEPMAP_TYPED_CALLS turns TYPED on for."""
    return _lang_set(_env_get(env, "DEEPMAP_TYPED_CALLS"), _TYPED_DEFAULT_LANGS, registry or LANGUAGES)[0]


def audit_langs(env=None, registry=None) -> frozenset:
    """The languages DEEPMAP_CALLS_AUDIT turns the audit on for."""
    return _lang_set(_env_get(env, "DEEPMAP_CALLS_AUDIT"), _AUDIT_DEFAULT_LANGS, registry or LANGUAGES)[0]


def enabled(env=None) -> bool:
    """True iff link() would do anything (a cheap check for callers)."""
    return bool(typed_langs(env) or audit_langs(env))


@dataclass
class _Config:
    typed: frozenset
    audit: frozenset
    shapes: frozenset
    replace: bool
    tests: bool
    nonprod: bool
    max_files: int
    unknown_langs: list
    unknown_shapes: list
    iface: frozenset = frozenset(IFACE_MODES)
    unknown_iface: list = field(default_factory=list)


def _iface_read(env, langs, registry) -> tuple:
    """(modes, unknown tokens) of DEEPMAP_TYPED_CALLS_IFACE (read only when a language with the iface_dispatch rule is
    on): unset or an ON-word -> every mode; an OFF-word -> none; else a CSV of IFACE_MODES. An unknown token is
    ignored and reported (stats unknown_iface); a value naming no known mode at all selects every mode, like unset
    (a typo never silently turns dispatch off)."""
    if not any("iface_dispatch" in getattr(registry.get(n), "lookup_rules", ()) for n in langs if n in registry):
        return frozenset(IFACE_MODES), []
    raw = _env_get(env, "DEEPMAP_TYPED_CALLS_IFACE").lower()
    if not raw or raw in _TRUE:
        return frozenset(IFACE_MODES), []
    if raw in _FALSE:
        return frozenset(), []
    names = [x.strip() for x in raw.split(",") if x.strip()]
    known = frozenset(n for n in names if n in IFACE_MODES)
    return (known or frozenset(IFACE_MODES)), sorted({n for n in names if n not in IFACE_MODES})


def _iface_modes(env, langs, registry) -> frozenset:
    """DEEPMAP_TYPED_CALLS_IFACE's modes (_iface_read; sync_status canonicalises the knob through this)."""
    return _iface_read(env, langs, registry)[0]


def _config(env, registry) -> _Config:
    typed, unk_t = _lang_set(_env_get(env, "DEEPMAP_TYPED_CALLS"), _TYPED_DEFAULT_LANGS, registry)
    audit, unk_a = _lang_set(_env_get(env, "DEEPMAP_CALLS_AUDIT"), _AUDIT_DEFAULT_LANGS, registry)
    raw_shapes = _env_get(env, "DEEPMAP_TYPED_CALLS_SHAPES")
    unk_s: list = []
    if not raw_shapes:
        shapes = frozenset(SHAPES)
    elif raw_shapes.lower() in _FALSE:
        shapes = frozenset()
    else:
        names = [x.strip().upper() for x in raw_shapes.split(",") if x.strip()]
        shapes = frozenset(n for n in names if n in SHAPES)
        unk_s = sorted({n for n in names if n not in SHAPES})
    try:
        max_files = int(_env_get(env, "DEEPMAP_TYPED_CALLS_MAX_FILES") or MAX_FILES_DEFAULT)
    except ValueError:
        max_files = MAX_FILES_DEFAULT
    if max_files <= 0:
        max_files = MAX_FILES_DEFAULT
    iface, unk_i = _iface_read(env, set(typed) | set(audit), registry)
    return _Config(typed=typed, audit=audit, shapes=shapes,
                   replace=_env_get(env, "DEEPMAP_TYPED_CALLS_REPLACE").lower() not in _FALSE,
                   tests=_env_get(env, "DEEPMAP_TYPED_CALLS_TESTS").lower() in _TRUE,
                   nonprod=_env_get(env, "DEEPMAP_TYPED_CALLS_NONPROD").lower() in _TRUE,
                   max_files=max_files, unknown_langs=sorted(set(unk_t) | set(unk_a)), unknown_shapes=unk_s,
                   iface=iface, unknown_iface=unk_i)


# ================================================================================================
# Small pure helpers
# ================================================================================================

_SEG_SPLIT = re.compile(r"::|\.|#")


def name_seg(name) -> str:
    """The last segment of a node name (`Cls.m` -> m, `A::B` -> B)."""
    return _SEG_SPLIT.split(str(name or ""))[-1]


def callee_matches(callee, m: str, name_of: dict, is_key) -> bool:
    """Does a CALLS callee denote name m: the name itself, a name ending `.m` / `::m`, or a CallKey whose node's
    last name segment is m (design 2.5)."""
    if not isinstance(callee, str) or not m:
        return False
    if is_key(callee):
        nm = name_of.get(callee)
        return nm is not None and name_seg(nm) == m
    return callee == m or callee.endswith(("." + m, "::" + m, "#" + m))


def _callee_seg(callee, name_of: dict):
    """The last name segment a callee denotes (a CallKey: its node's; a name: its own), or None. For an identifier
    m, callee_matches(callee, m) is exactly `_callee_seg(callee) == m` — the merge's per-caller index."""
    if not isinstance(callee, str) or not callee:
        return None
    if _is_call_key(callee):
        nm = name_of.get(callee)
        return name_seg(nm) if nm is not None else None
    return name_seg(callee)


def is_nonprod_path(rel) -> bool:
    """A non-production caller: a DIRECTORY component in NONPROD_DIRS (case-insensitive; never a filename and
    never a substring — `examples.py`, `src/docs_parser.py` and `mydocs/` are production)."""
    parts = str(rel or "").replace("\\", "/").split("/")
    return any(p.lower() in NONPROD_DIRS for p in parts[:-1])


def _norm_rel(p) -> str:
    return str(p or "").replace("\\", "/")


def _int(x, default=0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _arity_ok(arity, argc: int) -> bool:
    try:
        lo, hi = int(arity[0]), int(arity[1])
    except (TypeError, ValueError, IndexError):
        return True
    return lo <= argc and (hi < 0 or argc <= hi)


# ---- the textual veto's code masks (strings and comments -> spaces; columns and newlines kept) -----------------
# A mask hides what is NOT code and keeps what IS: a JS template substitution `${...}`, a Python f-string
# replacement field `{...}`, a C# `$"..{..}.."` hole and a Ruby `"#{...}"` are code (a call there is a call the
# engine records). Where a mask cannot tell, it keeps the text as code: an over-count only makes the veto fire
# (ADD), while a hidden call the reader also missed would let a REPLACE remove the engine's tuple for it. Every mask
# is regex-driven (C-level scanning; Python work per token, never per character) and bounded in nesting depth.
_MASK_NEST = 48                   # nested string / template / interpolation depth; deeper text is kept as code


class _Out:
    """The masked text, built from pieces: kept code and blanked (non-newline -> space) runs, joined once."""
    __slots__ = ("text", "parts", "at")

    def __init__(self, text: str):
        self.text, self.parts, self.at = text, [], 0

    def code(self, j: int) -> None:
        if j > self.at:
            self.parts.append(self.text[self.at:j])
            self.at = j

    def blank(self, j: int) -> None:
        if j > self.at:
            self.parts.append(_blanked(self.text, self.at, j))
            self.at = j

    def done(self) -> str:
        self.code(len(self.text))
        return "".join(self.parts)


def _blanked(text: str, i: int, j: int) -> str:
    """text[i:j] with every character but a newline replaced by a space (line by line: a per-character
    substitution is ~60x slower on a docstring)."""
    if text.find("\n", i, j) < 0:
        return " " * (j - i)
    return "\n".join(map(" ".__mul__, map(len, text[i:j].split("\n"))))


def _eol(text: str, i: int) -> int:
    j = text.find("\n", i)
    return len(text) if j < 0 else j


# ---- Python
_PY_END = {                       # the rest of a plain string after its opening quote(s); an unterminated
    "'": re.compile(r"[^'\\\n]*(?:\\[\s\S][^'\\\n]*)*(?:'|(?=\n)|\Z)"),      # single-line one ends at the newline
    '"': re.compile(r'[^"\\\n]*(?:\\[\s\S][^"\\\n]*)*(?:"|(?=\n)|\Z)'),
    "'''": re.compile(r"[^'\\]*(?:(?:\\[\s\S]|'(?!''))[^'\\]*)*(?:'''|\Z)"),
    '"""': re.compile(r'[^"\\]*(?:(?:\\[\s\S]|"(?!""))[^"\\]*)*(?:"""|\Z)'),
}
_PY_FLIT = {                      # an f-string's literal run (`{{` / `}}` are literal; `\{` leaves the `{` a field)
    "'": re.compile(r"(?:[^{}\\\n']+|\\[^{}]|\\(?=[{}])|\{\{|\}\})*"),
    '"': re.compile(r'(?:[^{}\\\n"]+|\\[^{}]|\\(?=[{}])|\{\{|\}\})*'),
    "'''": re.compile(r"(?:[^{}\\']+|\\[^{}]|\\(?=[{}])|\{\{|\}\}|'(?!''))*"),
    '"""': re.compile(r'(?:[^{}\\"]+|\\[^{}]|\\(?=[{}])|\{\{|\}\}|"(?!""))*'),
}
_PY_SPEC_STOP = {"'": re.compile(r"[{}\n']"), '"': re.compile(r'[{}\n"]')}
_PY_FEXPR = re.compile("(?P<p>(?<!\\w)[rRbBuUfFtT]{1,2})?(?P<q>'''|\"\"\"|'|\")|(?P<c>#)|(?P<o>[\\[({])"
                       "|(?P<x>[\\])}])|(?P<k>:)")


def _py_string(text: str, out: _Out, mt, nest: int) -> int:
    """Mask the string literal `mt` (a match whose groups `p` / `q` are its prefix and opening quote) and return
    the index past it. An f- / t-string's replacement fields stay code."""
    q, low, body = mt.group("q"), (mt.group("p") or "").lower(), mt.end()
    if ("f" in low or "t" in low) and nest < _MASK_NEST:
        out.blank(body)
        return _py_fstring(text, out, body, q, nest + 1)
    j = _PY_END[q].match(text, body).end()
    out.blank(j)
    return max(j, mt.start() + 1)


def _py_fstring(text: str, out: _Out, j: int, q: str, nest: int) -> int:
    """The body of an f-string (from j): literal text blanked, replacement fields kept as code."""
    n, lit, triple = len(text), _PY_FLIT[q], len(q) == 3
    while j < n:
        j = lit.match(text, j).end()
        out.blank(j)
        if j >= n:
            return n
        c = text[j]
        if c == "{":
            out.blank(j + 1)
            j = _py_field(text, out, j + 1, q, nest)
        elif c == "}":                            # a stray single `}` (invalid): literal
            out.blank(j + 1)
            j += 1
        elif c == "\n" and not triple:            # an unterminated single-line f-string ends at the newline
            return j
        elif text.startswith(q, j):
            out.blank(j + len(q))
            return j + len(q)
        else:                                     # a lone backslash before a brace
            out.blank(j + 1)
            j += 1
    return n


def _py_field(text: str, out: _Out, j: int, q: str, nest: int) -> int:
    """A replacement field's expression (from j) is code — nested strings masked, brackets tracked — up to its
    `:` format spec or closing `}` at depth 0. Returns the index past the field."""
    n, depth = len(text), 0
    while j < n:
        mt = _PY_FEXPR.search(text, j)
        if mt is None:
            out.code(n)
            return n
        k = mt.start()
        out.code(k)
        if mt.group("q") is not None:
            j = _py_string(text, out, mt, nest)
        elif mt.group("c") is not None:           # a comment in a multi-line field (3.12+)
            j = _eol(text, k)
            out.blank(j)
        elif mt.group("o") is not None:
            depth += 1
            out.code(k + 1)
            j = k + 1
        elif mt.group("x") is not None:
            if depth == 0 and text[k] == "}":
                out.blank(k + 1)
                return k + 1
            depth = max(0, depth - 1)
            out.code(k + 1)
            j = k + 1
        elif depth == 0:                          # `:` -> the format spec (literal, with nested fields)
            out.blank(k + 1)
            return _py_spec(text, out, k + 1, q, nest)
        else:
            out.code(k + 1)
            j = k + 1
    return n


def _py_spec(text: str, out: _Out, j: int, q: str, nest: int) -> int:
    n, triple, stop = len(text), len(q) == 3, _PY_SPEC_STOP[q[0]]
    while j < n:
        mt = stop.search(text, j)
        if mt is None:
            out.blank(n)
            return n
        k = mt.start()
        out.blank(k)
        c = text[k]
        if c == "{":
            out.blank(k + 1)
            j = _py_field(text, out, k + 1, q, nest) if nest < _MASK_NEST else k + 1
        elif c == "}":
            out.blank(k + 1)
            return k + 1
        elif c == "\n" and not triple:
            return k
        elif text.startswith(q, k):               # the spec ran into the string's end: the f-string closes it
            return k
        else:
            out.blank(k + 1)
            j = k + 1
    return n


_PY_HOT = re.compile(r"""[#'"]""")
_PY_PREFIX = frozenset("rRbBuUfFtT")


def _mask_python(text: str) -> str:
    out, n, j = _Out(text), len(text), 0
    search, ends = _PY_HOT.search, _PY_END
    parts = out.parts
    while j < n:
        mt = search(text, j)
        if mt is None:
            break
        k = mt.start()
        c = text[k]
        if c == "#":                              # a comment (no newline inside: one run of spaces)
            e = text.find("\n", k)
            e = n if e < 0 else e
            if k > out.at:
                parts.append(text[out.at:k])
            parts.append(" " * (e - k))
            out.at = j = e
            continue
        p = k                                     # the string prefix: up to 2 letters, not the tail of a name
        while p > k - 2 and p > 0 and text[p - 1] in _PY_PREFIX:
            p -= 1
        if p < k and p > 0 and (text[p - 1].isalnum() or text[p - 1] == "_"):
            p = k
        q = c * 3 if text.startswith(c * 3, k) else c
        pre = text[p:k]
        if "f" in pre or "F" in pre or "t" in pre or "T" in pre:
            out.code(k)
            out.blank(k + len(q))
            j = _py_fstring(text, out, k + len(q), q, 1)
            continue
        e = ends[q].match(text, k + len(q)).end()
        if k > out.at:
            parts.append(text[out.at:k])
        parts.append(_blanked(text, k, e))
        out.at = e
        j = max(e, k + 1)
    return out.done()


# ---- JavaScript / TypeScript
_JS_REGEX_PREV = frozenset("(,=:[!&|?{};+-*%<>~^")
_JS_REGEX_WORDS = frozenset({"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
                             "do", "else", "yield", "await"})
_JS_HOT = (re.compile(r"""//|/\*|['"`/]"""), re.compile(r"""//|/\*|['"`/{}]"""))   # top level / substitution
_JS_WORD_END = re.compile(r"[\w$]+\Z")
_STR_REST = {"'": re.compile(r"[^'\\\n]*(?:\\[\s\S][^'\\\n]*)*(?:'|(?=\n)|\Z)"),
             '"': re.compile(r'[^"\\\n]*(?:\\[\s\S][^"\\\n]*)*(?:"|(?=\n)|\Z)')}
_JS_REGEX_LIT = re.compile(r"/(?![*/])(?:[^/\\\[\n]|\\.|\[(?:[^\]\\\n]|\\.)*\])+/")
_JS_TLIT = re.compile(r"(?:[^`\\$]+|\\[\s\S]|\$(?!\{))*")
_JS_WS = frozenset(" \t\r\n\f\v ﻿  ")


def _js_code(text: str, out: _Out, j: int, nest: int) -> int:
    """JS code from j: comments / strings / regex literals masked, templates handled. Inside a template
    substitution (nest > 0) it stops AT the `}` that closes it (returned); at top level it runs to the end.
    Only the characters that open a non-code region (and braces in a substitution) are visited; a `/` is a regex
    literal by the token before it (`(` `,` `=` ... or a keyword like `return`; never after a value), found by
    looking back over whitespace and comments to the last kept code or masked value."""
    n, depth, search = len(text), 0, _JS_HOT[1 if nest else 0].search
    val_end, com = -1, []                         # the end of the last string / template / regex; comment spans
    while j < n:
        mt = search(text, j)
        if mt is None:
            break
        k, tok = mt.start(), mt.group(0)
        out.code(k)
        c = tok[0]
        if tok == "//":
            e = _eol(text, k)
            out.blank(e)
            com.append((k, e))
            j = e
        elif tok == "/*":
            e = text.find("*/", k + 2)
            e = n if e < 0 else e + 2
            out.blank(e)
            com.append((k, e))
            j = e
        elif c in "'\"":
            j = val_end = _STR_REST[c].match(text, k + 1).end()
            out.blank(j)
        elif c == "`":
            j = val_end = _js_template(text, out, k, nest + 1) if nest < _MASK_NEST else k + 1
        elif c == "/":
            rx = _JS_REGEX_LIT.match(text, k) if _js_regex_ok(text, k, val_end, com) else None
            if rx is not None:
                j = val_end = rx.end()
                out.blank(j)
            else:
                out.code(k + 1)
                j = k + 1
        elif c == "{":
            depth += 1
            out.code(k + 1)
            j = k + 1
        elif depth == 0:                          # the `}` closing this substitution
            return k
        else:
            depth -= 1
            out.code(k + 1)
            j = k + 1
    out.code(n)
    return n


def _js_regex_ok(text: str, k: int, val_end: int, com: list) -> bool:
    """May the `/` at k open a regex literal? Yes at the start, after a punctuator in _JS_REGEX_PREV or after a
    keyword in _JS_REGEX_WORDS; never right after a value (an identifier, `)`, `]`, a string / template /
    regex)."""
    p = k - 1
    while p >= 0:
        ch = text[p]
        if ch in _JS_WS:
            p -= 1
            continue
        while com and com[-1][0] > p:             # comments after p can no longer matter
            com.pop()
        if com and com[-1][1] > p:                # p is inside a comment: skip to before it
            p = com[-1][0] - 1
            continue
        break
    if p < 0:
        return True
    if p + 1 == val_end:
        return False
    ch = text[p]
    if ch == "$" or ch == "_" or ch.isalnum():
        w = _JS_WORD_END.search(text, max(0, p - 64), p + 1)
        return w is not None and w.group(0) in _JS_REGEX_WORDS
    return ch in _JS_REGEX_PREV


def _js_template(text: str, out: _Out, k: int, nest: int) -> int:
    """The template literal opened at k: the backticks are kept (a tagged template `` tag`..` `` is a call),
    the literal text blanked and every `${...}` substitution kept as code. Returns the index past it."""
    n = len(text)
    out.code(k + 1)
    j = k + 1
    while j < n:
        j = _JS_TLIT.match(text, j).end()
        out.blank(j)
        if j >= n:
            return n
        if text[j] == "`":
            out.code(j + 1)
            return j + 1
        out.code(j + 2)                           # `${`
        j = _js_code(text, out, j + 2, nest)
        if j >= n:
            return n
        out.code(j + 1)                           # the `}` closing the substitution
        j += 1
    return n


def _mask_js(text: str) -> str:
    out = _Out(text)
    _js_code(text, out, 0, 0)
    return out.done()


# ---- C family (Go / Java / C# / Rust)
_C_START = re.compile(r"""//|/\*|["'`]""")
_C_HOLE = re.compile(r"""\{\{|\}\}|[{}"]""")
_C_HOLE_INNER = re.compile(r"""[{}"']""")
_C_RAW_HOLE = re.compile(r"\{[^{}\n]*\}")


def _c_hole(text: str, out: _Out, j: int) -> int:
    """A C# interpolation hole's code from j (after its `{`) up to the matching `}` (blanked); nested strings
    masked. Returns the index past the hole."""
    n, depth = len(text), 0
    while j < n:
        h = _C_HOLE_INNER.search(text, j)
        if h is None:
            out.code(n)
            return n
        k = h.start()
        out.code(k)
        c = text[k]
        if c in "\"'":
            j = _STR_REST[c].match(text, k + 1).end()
            out.blank(j)
            continue
        if c == "{":
            depth += 1
        elif depth == 0:
            out.blank(k + 1)
            return k + 1
        else:
            depth -= 1
        out.code(k + 1)
        j = k + 1
    return n


def _c_interp(text: str, out: _Out, j: int, verbatim: bool) -> int:
    """The body of a C# interpolated string (from j): literal text blanked, every `{...}` hole kept as code.
    Returns the index past the closing quote."""
    n = len(text)
    while j < n:
        mt = _C_HOLE.search(text, j)
        if mt is None:
            out.blank(n)
            return n
        k, tok = mt.start(), mt.group(0)
        if tok in ("{{", "}}"):
            out.blank(mt.end())
            j = mt.end()
        elif tok == '"':
            if verbatim and text.startswith('""', k):
                out.blank(k + 2)
                j = k + 2
                continue
            b, esc = k - 1, 0                     # an odd run of backslashes escapes the quote (not verbatim)
            while not verbatim and b >= j and text[b] == "\\":
                b, esc = b - 1, esc + 1
            out.blank(k + 1)
            if esc % 2:
                j = k + 1
                continue
            return k + 1
        elif tok == "}":                          # a stray `}`: literal
            out.blank(k + 1)
            j = k + 1
        else:                                     # `{`: a hole
            out.blank(k + 1)
            j = _c_hole(text, out, k + 1)
    return n


def _c_interp_raw(text: str, out: _Out, j: int) -> int:
    """A C# 11 raw interpolated string `$\"\"\"...\"\"\"` (from its body): single-brace holes kept as code."""
    end = text.find('"""', j)
    stop = len(text) if end < 0 else end
    while j < stop:
        h = _C_RAW_HOLE.search(text, j, stop)
        if h is None:
            break
        out.blank(h.start() + 1)
        out.code(h.end() - 1)
        out.blank(h.end())
        j = h.end()
    j = stop if end < 0 else end + 3
    out.blank(j)
    return j


def _mask_c(text: str) -> str:
    """C family (Go / Java / C# / Rust): // and /* */ comments, "..." strings, Java/C# \"\"\" blocks, C# @"..."
    verbatim and $"...{hole}..." interpolated strings (holes are code), Rust r#"..."# raw, char literals ('a',
    '\\n'; a Rust lifetime is code) and Go `raw` strings."""
    out, n, j = _Out(text), len(text), 0
    while j < n:
        mt = _C_START.search(text, j)
        if mt is None:
            break
        i, ch = mt.start(), mt.group(0)
        out.code(i)
        if ch == "//":
            j = _eol(text, i)
        elif ch == "/*":
            e = text.find("*/", i + 2)
            j = n if e < 0 else e + 2
        elif ch == '"':
            pre = text[max(0, i - 2):i]
            dollar = pre.endswith(("$", "$@", "@$"))
            verbatim = pre.endswith(("@", "$@", "@$"))
            if text.startswith('"""', i):
                if dollar:
                    out.blank(i + 3)
                    j = _c_interp_raw(text, out, i + 3)
                    continue
                e = text.find('"""', i + 3)
                j = n if e < 0 else e + 3
            elif dollar:
                out.blank(i + 1)
                j = _c_interp(text, out, i + 1, verbatim)
                continue
            elif verbatim:
                j = i + 1
                while j < n:
                    if text[j] == '"':
                        if j + 1 < n and text[j + 1] == '"':
                            j += 2
                            continue
                        break
                    j += 1
                j = min(n, j + 1)
            else:
                k, hashes = i - 1, 0
                while k >= 0 and text[k] == "#":
                    hashes, k = hashes + 1, k - 1
                raw = k >= 0 and text[k] == "r" and (k == 0 or not (text[k - 1].isalnum() or text[k - 1] == "_"))
                if raw:
                    end = text.find('"' + "#" * hashes, i + 1)
                    j = n if end < 0 else end + 1 + hashes
                else:
                    j = _STR_REST['"'].match(text, i + 1).end()
        elif ch == "'":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "\\":
                end = text.find("'", i + 2, i + 14)
                j = end + 1 if end >= 0 else i + 1
            elif i + 2 < n and text[i + 2] == "'":
                j = i + 3
            else:                                 # a Rust lifetime / loop label: code
                out.code(i + 1)
                j = i + 1
                continue
        else:                                     # a Go raw string
            end = text.find("`", i + 1)
            j = n if end < 0 else end + 1
        out.blank(j)
        j = max(j, i + 1)
    return out.done()


# ---- Ruby
_RB_TOP = re.compile(r"""#|^=begin\b|['"`]""", re.M)
_RB_INNER = re.compile(r"""#|['"`{}]""")
_RB_SQ = re.compile(r"[^'\\]*(?:\\[\s\S][^'\\]*)*(?:'|\Z)")
_RB_DQ_RUN = {q: re.compile(r"(?:[^" + q + r"\\#]+|\\[\s\S]|#(?!\{))*") for q in ('"', "`")}


def _rb_interp(text: str, out: _Out, j: int, q: str, nest: int) -> int:
    """A Ruby double-quoted (or backtick) string body: literal text blanked, every `#{...}` kept as code."""
    n, run = len(text), _RB_DQ_RUN[q]
    while j < n:
        j = run.match(text, j).end()
        out.blank(j)
        if j >= n:
            return n
        if text[j] == q:
            out.blank(j + 1)
            return j + 1
        out.blank(j + 2)                          # `#{`
        if nest >= _MASK_NEST:
            return j + 2
        j = _rb_code(text, out, j + 2, nest + 1)
        if j >= n:
            return n
        out.blank(j + 1)                          # the closing `}`
        j += 1
    return n


def _rb_code(text: str, out: _Out, j: int, nest: int) -> int:
    """Ruby code from j; inside `#{...}` (nest > 0) it stops AT the closing `}` (returned)."""
    n, depth, tok = len(text), 0, (_RB_INNER if nest else _RB_TOP)
    while j < n:
        mt = tok.search(text, j)
        if mt is None:
            break
        k, c = mt.start(), mt.group(0)
        out.code(k)
        if c == "#":
            j = _eol(text, k)
            out.blank(j)
        elif c.startswith("=begin"):
            e = text.find("\n=end", k)
            j = n if e < 0 else _eol(text, e + 1)
            out.blank(j)
        elif c == "'":
            j = _RB_SQ.match(text, k + 1).end()
            out.blank(j)
        elif c in "\"`":
            out.blank(k + 1)
            j = _rb_interp(text, out, k + 1, c, nest)
        elif c == "{":
            depth += 1
            out.code(k + 1)
            j = k + 1
        else:                                     # `}`
            if nest and depth == 0:
                return k
            depth = max(0, depth - 1)
            out.code(k + 1)
            j = k + 1
    out.code(n)
    return n


def _mask_ruby(text: str) -> str:
    out = _Out(text)
    _rb_code(text, out, 0, 0)
    return out.done()


_MASKERS = {"python": _mask_python, "js": _mask_js, "c": _mask_c, "ruby": _mask_ruby}


def mask_code(text: str, lexer: str) -> str:
    """`text` with every string literal and comment replaced by spaces (newlines and columns kept; interpolated
    code kept) — the textual veto counts call-shaped `m(` only in what is left. An unknown lexer masks with the C
    rules. Never raises: on an internal failure the text comes back UNMASKED (all code: the veto's safe side)."""
    try:
        got = _MASKERS.get(lexer, _mask_c)(text)
        return got if len(got) == len(text) else text
    except Exception:  # noqa: BLE001 — a mask bug must never cost the sync; over-counting only vetoes REPLACE
        return text


# ---- call shapes: one scan per span collects every name (a Counter), never one scan per (caller, name)
_ID = r"(?<![\w$])([\w$]+)"
# type arguments: across lines, with `=>`, balanced `( )` (function types), `{ }` (object types) and one nested
# `< >` level; every repetition bounded, so a `<` comparison never scans far (an under-count only vetoes too)
_GENERIC = (r"<(?:=>|[^<>(){};]|\((?:=>|[^()<>{};]|<[^<>(){};]{0,200}>){0,200}\)|\{[^{}]{0,400}\}"
            r"|<(?:=>|[^<>(){};]|\([^()]{0,200}\)|\{[^{}]{0,400}\}){0,200}>){0,400}>")
_CALL_SHAPES = {
    "python": re.compile(_ID + r"(?:\s|\\\n)*\("),
    # TS / JS: `m(`, `m<T>(`, `m!(`, `m?.(` and a tagged template `` m`..` ``
    "js": re.compile(_ID + r"\s*(?:" + _GENERIC + r"\s*)?(?:!\s*)?(?:\?\.\s*)?[(`]"),
    # Java / C# / Go / Rust: `m(`, `m<T>(`, Rust's `m::<T>(`
    "c": re.compile(_ID + r"\s*(?:(?:::)?" + _GENERIC + r"\s*)?\("),
    "ruby": re.compile(r"(?<![\w$@])([\w]+[?!]?)(?:\s|\\\n)*\("),
}
_ONE_NAME = re.compile(r"[\w$]+[?!]?")


def _char_cols(line: str, bcols) -> list:
    """The character indexes of the UTF-8 byte columns `bcols` of `line` that are character boundaries (a lone
    surrogate counts one byte, as `encode("utf-8", "replace")` gives) — one pass over the line however many
    columns are asked."""
    if line.isascii():
        return [b for b in bcols if 0 <= b <= len(line)]
    want, out, b = set(bcols), [], 0
    for i, ch in enumerate(line):
        if b in want:
            out.append(i)
        o = ord(ch)
        b += 1 if o < 0x80 else (2 if o < 0x800 else (1 if 0xD800 <= o <= 0xDFFF else (3 if o < 0x10000 else 4)))
    if b in want:
        out.append(len(line))
    return out


class FileText:
    """One file's text for the textual veto, built ONCE per file: the raw lines, the masked text as one string,
    each line's start offset, the minified (MINIFIED_LINE+) line numbers and the char offsets of the declared
    identifiers. `counts(start, end)` = the call-shaped names of lines start..end, memoised per span — the veto
    of every (caller, name) of a span is one Counter lookup. A file with a minified line is not masked at all
    (every span over such a line is unjudgeable anyway; the others are counted on the raw text: over-counts
    only)."""
    __slots__ = ("lines", "masked", "offs", "long", "decl_pos", "rx", "memo")

    def __init__(self, lines, lexer: str, decls=frozenset(), masked=None):
        self.lines = list(lines)
        self.offs, at = [], 0
        for ln in self.lines:
            self.offs.append(at)
            at += len(ln) + 1
        self.long = [i + 1 for i, ln in enumerate(self.lines) if len(ln) >= MINIFIED_LINE]
        text = "\n".join(self.lines)
        if masked is None:
            masked = text if self.long else mask_code(text, lexer)
        self.masked = masked if len(masked) == len(text) else text
        by_line: dict = {}
        for d in decls or ():
            try:
                ln, bcol = int(d[0]), int(d[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 1 <= ln <= len(self.lines):
                by_line.setdefault(ln, set()).add(bcol)
        pos = set()
        for ln, bcols in by_line.items():          # each line's byte -> char map is built once (never per decl)
            line = self.lines[ln - 1]
            for c in _char_cols(line, bcols):
                pos.add(self.offs[ln - 1] + c)
        self.decl_pos = frozenset(pos)
        self.rx = _CALL_SHAPES.get(lexer) or _CALL_SHAPES["c"]
        self.memo: dict = {}

    def counts(self, start: int, end: int):
        """Counter {name: call-shaped occurrences in lines start..end (1-based, inclusive), declarations
        skipped}, or None when the span is unreadable or holds a minified line (unjudgeable)."""
        key = (start, end)
        got = self.memo.get(key, _MISSING)
        if got is not _MISSING:
            return got
        got = None
        if 1 <= start <= end <= len(self.lines):
            k = bisect.bisect_left(self.long, start)
            if not (k < len(self.long) and self.long[k] <= end):
                pos, endpos = self.offs[start - 1], self.offs[end - 1] + len(self.lines[end - 1])
                dp = self.decl_pos
                if dp:
                    got = Counter(mt.group(1) for mt in self.rx.finditer(self.masked, pos, endpos)
                                  if mt.start(1) not in dp)
                else:
                    got = Counter(self.rx.findall(self.masked, pos, endpos))
        self.memo[key] = got
        return got

    def count(self, start: int, end: int, m: str):
        """The call-shaped count of name m over lines start..end, or None (unjudgeable)."""
        got = self.counts(start, end)
        if got is None:
            return None
        if _ONE_NAME.fullmatch(m or ""):
            return got.get(m, 0)
        if not m:                                 # (never from a well-formed reader) the literal shape
            return None
        rx = re.compile(r"(?<![\w$])" + re.escape(m) + r"(?:\s|\\\n)*\(")
        return sum(1 for mt in rx.finditer(self.masked, self.offs[start - 1],
                                           self.offs[end - 1] + len(self.lines[end - 1]))
                   if mt.start() not in self.decl_pos)


_MISSING = object()


def span_text(masked: list, start: int, end: int):
    """(joined text, line start offsets) of lines start..end (1-based, inclusive) of `masked`, or None."""
    if start < 1 or end < start or end > len(masked):
        return None
    seg = "\n".join(masked[start - 1:end])
    offsets = [0]
    for ln in masked[start - 1:end - 1]:
        offsets.append(offsets[-1] + len(ln) + 1)
    return seg, offsets


def count_call_shaped(lines: list, masked: list, start: int, end: int, m: str, decls=frozenset(), span=None,
                      lexer=None):
    """How many times identifier m (not preceded by [\\w$]) is call-shaped by `lexer`'s rule (`m(`; plus
    `m<T>(` / `m!(` / `m?.(` / a tagged template in JS, `m::<T>(` in Rust; default: the Python rule) in lines
    start..end (1-based, inclusive) of the masked text, skipping the (line, UTF-8 byte col) positions in
    `decls`. None when the span is unreadable or holds a line of MINIFIED_LINE+ characters. (`span` is accepted
    for compatibility and ignored.)"""
    if not m or start < 1 or end < start or end > len(lines) or len(masked) != len(lines):
        return None
    return FileText(lines, lexer or "python", decls, masked="\n".join(masked)).count(start, end, m)


# ================================================================================================
# IR validation (a malformed FileFacts is the reader's bug: that file gets no facts, the sync goes on)
# ================================================================================================

def _nt(x, cls) -> bool:
    """A NamedTuple of the IR type `cls` — structural (a copy of this module loaded under another name counts)."""
    if type(x) is cls:
        return True
    return isinstance(x, tuple) and type(x).__name__ == cls.__name__ and getattr(x, "_fields", None) == cls._fields


def _dc(x, cls) -> bool:
    if type(x) is cls:
        return True
    return type(x).__name__ == cls.__name__ and all(hasattr(x, f) for f in cls.__dataclass_fields__)


def _ok_str(x) -> bool:
    return type(x) is str or isinstance(x, str)


def _ok_int(x) -> bool:
    return type(x) is int or (isinstance(x, int) and not isinstance(x, bool))


# (the validator runs on every file of a sync: each check below keeps its original order — so even an exception
# a malformed value raises is the same — with an exact `type(x) is ...` fast path in front of the general test)
def _ok_typeref(t, depth=0) -> bool:
    if depth > 32 or not (type(t) is TypeRef or _nt(t, TypeRef)) or t.kind not in TYPEREF_KINDS \
            or not (type(t.name) is str or _ok_str(t.name)):
        return False
    if not isinstance(t.parts, tuple) or not all(type(p) is str or _ok_str(p) for p in t.parts):
        return False
    return isinstance(t.args, tuple) and all(_ok_typeref(a, depth + 1) for a in t.args)


def _ok_recv(r, depth=0) -> bool:
    if depth > 32 or not (type(r) is Receiver or _nt(r, Receiver)) or r.kind not in RECEIVER_KINDS \
            or not (type(r.name) is str or _ok_str(r.name)):
        return False
    if r.inner is not None and not _ok_recv(r.inner, depth + 1):
        return False
    return r.type is None or _ok_typeref(r.type)


def _ok_binding(b, nscopes: int) -> bool:
    return ((type(b) is Binding or _nt(b, Binding)) and b.kind in BINDING_KINDS
            and (b.type is None or _ok_typeref(b.type)) and (b.value is None or _ok_recv(b.value))
            and (type(b.line) is int or _ok_int(b.line)) and (type(b.scope) is int or _ok_int(b.scope))
            and -1 <= b.scope < nscopes)


def _ok_site(x) -> bool:
    return ((type(x) is Site or _nt(x, Site)) and (type(x.line) is int or _ok_int(x.line))
            and (type(x.col) is int or _ok_int(x.col)) and (type(x.member) is str or _ok_str(x.member))
            and x.kind in SITE_KINDS and (type(x.argc) is int or _ok_int(x.argc))
            and (x.recv is None or _ok_recv(x.recv)))


def _ok_names(x) -> bool:
    return isinstance(x, (set, frozenset)) and all(_ok_str(v) for v in x)


def _ok_lines(x) -> bool:
    return isinstance(x, tuple) and all(_ok_int(v) for v in x)


def validate_facts(f) -> str:
    """'' when `f` is a well-formed FileFacts, else the first problem found (gates and readers use it)."""
    if not _dc(f, FileFacts):
        return "not a FileFacts"
    if not _ok_str(f.rel) or not _ok_str(f.lang):
        return "rel / lang"
    for nm, attr in (("imports", f.imports), ("functions", f.functions), ("globals", f.globals)):
        if not isinstance(attr, dict) or not all(_ok_str(k) for k in attr):
            return nm
    scopes, classes = f.scopes, f.classes
    if not isinstance(scopes, list) or not isinstance(classes, list):
        return "scopes / classes"
    ns, nc = len(scopes), len(classes)
    for k, v in f.imports.items():
        if not isinstance(v, list) or not all(_nt(i, Import) and i.kind in IMPORT_KINDS and _ok_str(i.spec)
                                              and _ok_str(i.name) and _ok_str(i.local) and _ok_int(i.line)
                                              for i in v):
            return f"import {k!r}"
    for k, v in f.functions.items():
        if not isinstance(v, list) or not all(_nt(x, FunctionFacts) and _ok_str(x.name) and _ok_int(x.line)
                                              and (x.returns is None or _ok_typeref(x.returns)) and _ok_lines(x.lines)
                                              for x in v):
            return f"function {k!r}"
    for k, v in f.globals.items():
        if not isinstance(v, list) or not all(_ok_binding(b, ns) for b in v):
            return f"global {k!r}"
    for ci, c in enumerate(classes):
        if not _dc(c, ClassFacts) or not _ok_str(c.name) or not _ok_int(c.line) or not _ok_int(c.end_line):
            return f"class #{ci}"
        if not isinstance(c.bases, (tuple, list)) or not all(_ok_typeref(b) for b in c.bases):
            return f"class #{ci} bases"
        if not isinstance(c.fields, dict) or not all(_ok_str(k) and isinstance(v, list)
                                                     and all(_ok_binding(b, ns) for b in v)
                                                     for k, v in c.fields.items()):
            return f"class #{ci} fields"
        if not isinstance(c.methods, dict) or not all(
                _ok_str(k) and isinstance(v, list) and all(
                    _nt(mf, MethodFacts) and _ok_int(mf.line) and isinstance(mf.flags, (set, frozenset))
                    and (mf.returns is None or _ok_typeref(mf.returns)) and _ok_lines(mf.lines)
                    and isinstance(mf.arity, tuple) and len(mf.arity) == 2 for mf in v)
                for k, v in c.methods.items()):
            return f"class #{ci} methods"
        if not (_ok_int(c.outer) and -1 <= c.outer < ci and _ok_int(c.scope) and -1 <= c.scope < ns):
            return f"class #{ci} outer / scope"
        if not isinstance(c.dynamic, bool) or not isinstance(c.open, bool) or not _ok_lines(c.lines) \
                or not _ok_names(c.type_params):
            return f"class #{ci} flags"
    for si, s in enumerate(scopes):
        if not _dc(s, Scope) or not _ok_str(s.name) or not _ok_int(s.line) or not _ok_int(s.end_line):
            return f"scope #{si}"
        if not (_ok_int(s.parent) and -1 <= s.parent < si and _ok_int(s.cls) and -1 <= s.cls < nc):
            return f"scope #{si} parent / cls"
        if s.self_kind not in SELF_KINDS or not _ok_lines(s.lines) or not _ok_names(s.poisoned) \
                or not _ok_names(s.type_params):
            return f"scope #{si} fields"
        if not isinstance(s.params, dict) or not all(_ok_str(k) and _ok_binding(b, ns) for k, b in s.params.items()):
            return f"scope #{si} params"
        if not isinstance(s.locals, dict) or not all(_ok_str(k) and isinstance(v, list)
                                                     and all(_ok_binding(b, ns) for b in v)
                                                     for k, v in s.locals.items()):
            return f"scope #{si} locals"
        if not isinstance(s.sites, list) or not all(map(_ok_site, s.sites)):
            return f"scope #{si} sites"
        if s.all_names is not None and not (isinstance(s.all_names, dict) and all(
                _ok_str(k) and _ok_int(v) for k, v in s.all_names.items())):
            return f"scope #{si} all_names"
    if not isinstance(f.decls, (set, frozenset)) or not all(
            isinstance(d, tuple) and len(d) == 2 and (type(d[0]) is int or _ok_int(d[0]))
            and (type(d[1]) is int or _ok_int(d[1])) for d in f.decls):
        return "decls"
    if not isinstance(f.patches, list) or not all(
            isinstance(p, tuple) and len(p) == 2 and _ok_typeref(p[0]) and _ok_str(p[1]) for p in f.patches):
        return "patches"
    return ""


# ================================================================================================
# Lazy siblings
# ================================================================================================

def _sibling(name: str):
    """A build/ sibling module, imported lazily (build/ is put on sys.path first if a caller loaded this file by
    path)."""
    try:
        return importlib.import_module(name)
    except ImportError:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        return importlib.import_module(name)


def _call_key(key: str):
    """THE one place this pass marks a callee as a node key (graphify_extract.call_key)."""
    return _sibling("graphify_extract").call_key(key)


def _is_call_key(value) -> bool:
    return isinstance(value, str) and getattr(value, "_DEEPMAP_CALL_KEY", False) is True


def _default_source(root):
    return _sibling("graphify_extract")._Source(root)


class _SharedSources:
    """THE file reader of one link() run when the sync hands over the sources its extraction already read and
    parsed (`sources` = {rel: graphify_extract._Source, or any reader with raw / lines / tree / ts_tree}): each
    file is answered by ITS shared reader, so no file is read or parsed twice (design 2.1); a file the map does
    not hold, a value without the method asked, or a shared reader that raises falls back to `fallback()` (the
    `source=` reader, else a fresh _Source(root), made only when first needed). The readers see one object with
    _Source's interface. `served` counts the files answered by a shared reader (stats sources_shared)."""

    _METHODS = ("raw", "lines", "line", "tree", "ts_tree")

    def __init__(self, sources, fallback):
        self._map = {}
        for rel, src in dict(sources).items():
            if isinstance(rel, str) and rel and any(callable(getattr(src, n, None)) for n in self._METHODS):
                self._map.setdefault(_norm_rel(rel), src)
        self._fallback, self._fb = fallback, None
        self.served: set = set()

    def fallback(self):
        if self._fb is None:
            self._fb = self._fallback()
        return self._fb

    def _ask(self, name, rel, *args):
        src = self._map.get(_norm_rel(rel))
        fn = getattr(src, name, None) if src is not None else None
        if callable(fn):
            try:
                got = fn(rel, *args)
                self.served.add(_norm_rel(rel))
                return got
            except Exception:  # noqa: BLE001 — a shared reader that fails costs only the re-read
                pass
        return getattr(self.fallback(), name)(rel, *args)

    def raw(self, rel):
        return self._ask("raw", rel)

    def lines(self, rel):
        return self._ask("lines", rel)

    def line(self, rel, lineno):
        return self._ask("line", rel, lineno)

    def tree(self, rel):
        return self._ask("tree", rel)

    def ts_tree(self, rel):
        return self._ask("ts_tree", rel)


def _default_is_test():
    try:
        fn = getattr(_sibling("sync"), "_is_test_path", None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 — no rule: every caller is treated as a test caller (corrections only)
        return None


# ================================================================================================
# Phase B: whole-graph resolution
# ================================================================================================

class _V:
    """A value in Phase B. kind: inst (keys: class keys) | cls (keys: {class key}) | module (ref: rel) | ns (ref:
    (lang, token)) | func (ref: (rel, FunctionFacts)) | ext (ref: why) | none | unk | unbound. ev = evidence
    files; weak = never EXTRACTED (an injected field); ret = went through a return annotation (R4); src = where a
    name came from (param | local | global | builtin | import | ctor | field | return); decl = the type came from a
    DECLARATION (an annotation, a return annotation) rather than a construction, so the runtime value may be an
    instance of a subclass."""
    __slots__ = ("kind", "keys", "ref", "ev", "weak", "ret", "src", "decl")

    def __init__(self, kind, keys=frozenset(), ref=None, ev=frozenset(), weak=False, ret=False, src="", decl=False):
        self.kind, self.keys, self.ref, self.ev = kind, frozenset(keys), ref, frozenset(ev)
        self.weak, self.ret, self.src, self.decl = weak, ret, src, decl

    def but(self, **kw) -> "_V":
        d = {s: getattr(self, s) for s in self.__slots__}
        d.update(kw)
        return _V(**d)

    def plus_ev(self, ev) -> "_V":
        return self if not ev else self.but(ev=self.ev | frozenset(ev))

    def same(self, other) -> bool:
        return self.kind == other.kind and self.keys == other.keys and self.ref == other.ref


_UNK = _V("unk")
_UNBOUND = _V("unbound")
_NONEV = _V("none")


def _join(vals) -> _V:
    """All bindings of one name: None values ignored; instances union (a candidate set); anything else must agree."""
    vals = [v for v in vals if v.kind != "none"]
    if not vals or any(v.kind in ("unk", "unbound") for v in vals):
        return _UNK
    k0 = vals[0].kind
    if any(v.kind != k0 for v in vals):
        return _UNK
    ev = frozenset().union(*(v.ev for v in vals))
    weak, ret, decl = any(v.weak for v in vals), any(v.ret for v in vals), any(v.decl for v in vals)
    if k0 == "inst":
        return _V("inst", frozenset().union(*(v.keys for v in vals)), ev=ev, weak=weak, ret=ret, src=vals[0].src,
                  decl=decl)
    if all(v.same(vals[0]) for v in vals):
        return vals[0].but(ev=ev, weak=weak, ret=ret, decl=decl)
    if k0 == "ext" and all(v.ref in _EXT_VALUE_OK for v in vals):
        return _V("ext", ref="type", ev=ev, weak=weak, ret=ret, src=vals[0].src, decl=decl)
    return _UNK


def _span_start(sc) -> int:
    """The first line of a caller's span: its def line or an earlier line its node may start on."""
    return min((sc.line,) + tuple(x for x in sc.lines if isinstance(x, int) and 0 < x <= sc.line))


def _agree(vals) -> bool:
    """Every non-None value is the same (kind, classes, ref) — an explicit union annotation is ONE value."""
    return len({(v.kind, v.keys, v.ref) for v in vals if v.kind != "none"}) <= 1


class _ClassInfo:
    __slots__ = ("key", "rel", "idx", "facts", "spec", "patched", "ambiguous", "node_name", "parts")

    def __init__(self, key, rel, idx, facts, spec, node_name):
        self.key, self.rel, self.idx, self.facts, self.spec = key, rel, idx, facts, spec
        self.patched, self.ambiguous, self.node_name = set(), False, node_name
        # a merged partial type (LanguageSpec.lookup_rules partials): {"rels": [the parts' files], "of": {id(a base
        # TypeRef / field Binding / MethodFacts): the part file it was written in}}; None for every other class
        self.parts = None


class _Look(NamedTuple):
    kind: str                     # hit | absent | unknown
    targets: tuple = ()           # node keys (hit)
    mfs: tuple = ()               # MethodFacts per target (None when bound by name)
    owner: "_ClassInfo | None" = None
    ev: frozenset = frozenset()
    why: str = ""
    is_class: bool = False        # the hit is a nested CLASS (a constructor)


class _Verdict(NamedTuple):
    kind: str                     # typed | external | absent | unknown
    targets: tuple = ()
    ev: frozenset = frozenset()
    weak: bool = False
    shape: str = ""
    why: str = ""
    recv_kind: str = ""
    recv_cls: str = ""            # the class key when the receiver is a class object
    # dynamic dispatch (a typed site): the receiver's class keys, and whether its run-time class may be a project
    # SUBCLASS of them ("sub": a declared type, `self` / `cls`, a return annotation; "super": `super().m()` in that
    # class; "": a construction or a class named in the code — exactly that class) — see _Linker.dispatch_reach
    recv_keys: frozenset = frozenset()
    dispatch: str = ""
    on_class: bool = False        # the receiver is a class object (static members)


_V_UNKNOWN = _Verdict("unknown")
_ATTRIBUTED = ("own", "attributed")      # a site the engine gives to the caller (its own scope or a walked-in one)


def _stats_skeleton(cfg: "_Config | None", engine=None) -> dict:
    return {
        "version": STATS_VERSION,
        "engine": engine,
        "typed_langs": sorted(cfg.typed) if cfg else [],
        "audit_langs": sorted(cfg.audit) if cfg else [],
        "shapes": [s for s in SHAPES if cfg and s in cfg.shapes],
        "replace": bool(cfg.replace) if cfg else True,
        "tests": bool(cfg.tests) if cfg else False,
        "nonprod": bool(cfg.nonprod) if cfg else False,
        "unknown_langs": list(cfg.unknown_langs) if cfg else [],
        "unknown_shapes": list(cfg.unknown_shapes) if cfg else [],
        "unknown_iface": list(cfg.unknown_iface) if cfg else [],
        "langs_disabled": {},
        "files": 0, "files_failed": 0, "files_capped": 0,
        "sites": {"typed": 0, "external": 0, "absent": 0, "unknown": 0},
        "by_lang": {},
        "by_shape": {s: {"sites": 0, "emitted": 0} for s in SHAPES},
        "groups": {"replace": 0, "add": 0, "moot": 0},
        "vetoes": {"unknown_site": 0, "nested": 0, "text": 0, "census": 0, "span": 0, "replace_off": 0,
                   "dispatch": 0},
        "removed": 0, "emitted": 0, "new_pairs": 0,
        "override_kept": 0, "override_keyed": 0, "override_unseen": 0, "sources_shared": 0,
        "grades": {g: 0 for g in GRADES},
        "candidate_sets": 0, "candidate_skipped": 0, "too_many": 0,
        "skipped_policy": 0, "skipped_family": 0, "skipped_self": 0, "skipped_nested": 0, "skipped_interface": 0,
        "call_member_cleared": 0, "audit_weak": 0, "audit_shared": 0,
        "notes": [], "error": None, "seconds": 0.0,
    }


def _lang_stats() -> dict:
    return {"files": 0, "sites": 0, "typed": 0, "external": 0, "absent": 0, "unknown": 0, "emitted": 0,
            "removed": 0, "weak": 0}


class _Linker:
    """One link() run: Phase A (facts through the readers) then Phase B (indexes, resolution, merge, audit)."""

    def __init__(self, root, nodes, calls, cfg, registry, readers, source, is_test, stats, engine=None):
        self.root, self.nodes, self.calls, self.cfg = root, nodes, calls, cfg
        self.registry, self.injected, self.source, self.stats = registry, readers or {}, source, stats
        self.is_test, self.engine = is_test, engine
        self.facts: dict = {}           # rel -> FileFacts
        self.lang_of: dict = {}         # rel -> lang name (files with facts)
        self.binders: dict = {}         # lang -> Binder
        self.classes: dict = {}         # class key -> _ClassInfo
        self.class_key: dict = {}       # (rel, class idx) -> class key
        self.owner_of: dict = {}        # method node key -> class key
        self.iface_only: dict = {}      # method node key -> True iff EVERY class claiming it is an interface
        self.cs_static_iface: set = set()   # C#: method nodes of a static interface member with a body (exact)
        self.loose_patched: dict = {}   # PATCH_RULES loose: (language family, written in a test file) -> attrs
        #                                 patched onto anything but one project class ("*": every attr)
        self.memo: dict = {}
        self.busy: set = set()
        # Every time an evaluation is CUT (a depth cap or a cycle guard returns unknown) this grows: a value
        # computed while it grew depends on where the evaluation started and is never memoised (a 12-call
        # fluent chain must not poison its receiver's sibling sites).
        self.cut = 0
        self.texts: dict = {}           # rel -> FileText (or None: unreadable)
        self.line_attr: dict = {}       # caller -> {emitted target: its first call line} (call_lines["key"])

    # ---------------------------------------------------------------- setup
    def spec_for_path(self, rel):
        low = rel.lower()
        for spec in self.registry.values():
            if low.endswith(tuple(e.lower() for e in spec.exts)):
                return spec
        return None

    def spec_of(self, rel):
        lang = self.lang_of.get(rel)
        return self.registry.get(lang) if lang else self.spec_for_path(rel)

    def index_nodes(self):
        self.node_by_key: dict = {}
        self.at: dict = {}
        self.by_file_seg: dict = {}
        for n in self.nodes:
            if not isinstance(n, dict):
                continue
            k = n.get("key")
            if not isinstance(k, str) or not k or k in self.node_by_key:
                continue
            self.node_by_key[k] = n
            rel, seg = _norm_rel(n.get("source_file")), name_seg(n.get("name"))
            self.at.setdefault((rel, _int(n.get("start_line")), seg), []).append(k)   # (file, line, last segment)
            self.by_file_seg.setdefault((rel, seg), []).append(k)
        self.file_of = {k: _norm_rel(n.get("source_file")) for k, n in self.node_by_key.items()}
        self.name_of = {k: str(n.get("name") or "") for k, n in self.node_by_key.items()}
        self.label_of = {k: str(n.get("label") or "") for k, n in self.node_by_key.items()}
        self.graph_files = frozenset(f for f in self.file_of.values() if f)
        stems = set()
        for f in self.graph_files:
            parts = f.split("/")
            stems.update(p.lower() for p in parts[:-1] if p)
            base = parts[-1].lower()
            if "." in base:
                stems.add(base.rsplit(".", 1)[0])
                stems.add(base.split(".", 1)[0])
            else:
                stems.add(base)
        self.stems = frozenset(s for s in stems if s)

    def node_at(self, rel, lines, name, labels):
        """THE node at one of `lines` of `rel` with a label in `labels` whose last name segment is `name`, or
        None (none, or more than one)."""
        hits = set()
        for ln in lines:                           # an index keyed by the name too: a minified line holding
            for k in self.at.get((rel, ln, name), ()):     # 1,000 functions costs one lookup per scope
                if self.label_of.get(k) in labels:
                    hits.add(k)
        return next(iter(hits)) if len(hits) == 1 else None

    def load_readers(self):
        """{lang: reader} for every language whose facts this run needs (each enabled language plus every
        registered language of the same family), canary-checked."""
        want = set(self.cfg.typed) | set(self.cfg.audit)
        fams = {self.registry[n].family for n in want if n in self.registry}
        need = sorted(n for n, s in self.registry.items() if n in want or s.family in fams)
        readers = {}
        made: dict = {}
        for lang in need:
            spec = self.registry[lang]
            try:
                if lang in self.injected:
                    rd = self.injected[lang]
                else:
                    mod_key = (spec.reader, lang)
                    if mod_key not in made:
                        mod = _sibling(spec.reader)
                        made[mod_key] = mod.make_reader(spec)
                    rd = made[mod_key]
            except Exception:  # noqa: BLE001 — no reader (wave 2 not built yet, an import error)
                self._disable(lang, "no reader")
                continue
            try:
                ok = rd.canary() is True
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                self._disable(lang, "canary")
                continue
            readers[lang] = rd
        return readers

    def _disable(self, lang, why):
        if lang in self.cfg.typed or lang in self.cfg.audit:
            self.stats["langs_disabled"][lang] = why
        else:                                      # read only to resolve into its files (same family)
            self.stats["notes"].append(f"{lang}: {why} (read only for same-family resolution; its files stay "
                                       f"unresolved)")

    def read_facts(self, readers):
        by_lang: dict = {}
        for rel in sorted(self.graph_files):
            spec = self.spec_for_path(rel)
            if spec is not None and spec.name in readers:
                by_lang.setdefault(spec.name, []).append(rel)
        for lang in sorted(by_lang):
            rels = by_lang[lang]
            if len(rels) > self.cfg.max_files:
                self._disable(lang, "max_files")
                self.stats["notes"].append(f"{lang}: {len(rels)} files > DEEPMAP_TYPED_CALLS_MAX_FILES="
                                           f"{self.cfg.max_files}; the language is skipped")
                readers.pop(lang, None)
                continue
            rd = readers[lang]
            ls = self.stats["by_lang"].setdefault(lang, _lang_stats())
            for rel in rels:
                try:
                    lines = self.source.lines(rel)
                except Exception:  # noqa: BLE001
                    lines = None
                if lines is None:
                    self.stats["files_failed"] += 1
                    continue
                if len(lines) > FILE_MAX_LINES:
                    self.stats["files_capped"] += 1
                    continue
                try:
                    f = rd.facts(rel, self.source)
                except Exception:  # noqa: BLE001 — a reader bug costs that file, never the sync
                    f = None
                if f is None or validate_facts(f) or _norm_rel(f.rel) != rel:
                    self.stats["files_failed"] += 1
                    continue
                self.facts[rel] = f
                self.lang_of[rel] = lang
                self.stats["files"] += 1
                ls["files"] += 1
        for lang in sorted(readers):
            rels = tuple(by_lang.get(lang, ()))
            try:
                b = readers[lang].binder(BinderContext(root=str(self.root), lang=lang, rels=rels, facts=self.facts,
                                                       graph_files=self.graph_files, nodes=tuple(self.nodes)))
            except Exception:  # noqa: BLE001
                b = None
            if b is None:
                self._disable(lang, "binder")
                for rel in rels:
                    self.facts.pop(rel, None)
                    self.lang_of.pop(rel, None)
                continue
            self.binders[lang] = b
        return readers

    def index_classes(self):
        self.local_cls: dict = {}       # rel -> {scope index: names of the classes defined in that scope}
        self.nested_of: dict = {}       # rel -> {(outer class index, name): [class indexes]}
        for rel in sorted(self.facts):
            ff, spec = self.facts[rel], self.spec_of(rel)
            for idx, cf in enumerate(ff.classes):
                if cf.scope >= 0:
                    self.local_cls.setdefault(rel, {}).setdefault(cf.scope, set()).add(cf.name)
                if cf.outer >= 0:
                    self.nested_of.setdefault(rel, {}).setdefault((cf.outer, cf.name), []).append(idx)
                key = self.node_at(rel, (cf.line,) + tuple(cf.lines), cf.name, CLASS_LABELS)
                if key is None:
                    cands = [k for k in self.by_file_seg.get((rel, cf.name), ())
                             if self.label_of.get(k) in CLASS_LABELS]
                    key = cands[0] if len(cands) == 1 else None
                if key is None:
                    continue
                if key in self.classes:
                    self.classes[key].ambiguous = True       # two definitions, one node: never resolved
                    continue
                self.classes[key] = _ClassInfo(key, rel, idx, cf, spec, self.name_of.get(key, cf.name))
                self.class_key[(rel, idx)] = key
        self.merge_partials()
        for key in sorted(self.classes):
            ci = self.classes[key]
            iface = ci.facts.kind == "interface"
            for m in sorted(ci.facts.methods):
                for t in self.method_nodes(ci, m)[0]:
                    self.owner_of.setdefault(t, key)
                    self.iface_only[t] = self.iface_only.get(t, True) and iface
        for key in sorted(self.classes):           # C# (iface_dispatch, with the reader's modifiers): a STATIC
            ci = self.classes[key]                 # interface member with a body (C# 8+) runs as written: an exact
            if ci.facts.kind != "interface" or ci.spec is None or "iface_dispatch" not in ci.spec.lookup_rules \
                    or self.cs_table(ci.rel) is None:      # call (`IFactory.Make()`), never interface dispatch
                continue
            for m in sorted(ci.facts.methods):
                every = ci.facts.methods[m]
                for t in self.method_nodes(ci, m)[0]:
                    defs = [mf for mf in every if t in self.method_nodes(ci, m, [mf])[0]] or every
                    mods = [self.cs_member(ci, mf, m, "mods") for mf in defs]
                    if all("static" in mf.flags and "abstract" not in mf.flags for mf in defs) and all(
                            isinstance(md, frozenset) and not (md & {"abstract", "virtual"}) for md in mods):
                        self.cs_static_iface.add(t)
                        self.iface_only[t] = False
        fam_of = _sibling("edge_resolve").lang_family
        for rel in sorted(self.facts):
            in_test = None
            spec_p = self.spec_of(rel)
            ext_rule = spec_p is not None and "extensions" in spec_p.lookup_rules
            for tref, attr in self.facts[rel].patches:
                v = self.eval_type(tref, rel, -1)
                if ext_rule and v.kind == "inst" and len(v.keys) == 1:
                    continue                         # a C# extension on a project type: the extension rule decides
                if v.kind == "inst" and len(v.keys) == 1:
                    self.classes[next(iter(v.keys))].patched.add(attr)
                else:                                # PATCH_RULES loose (per language family; a test file's own)
                    if in_test is None:
                        in_test = self.test_file(rel, unknown=False)
                    self.loose_patched.setdefault((fam_of(rel), in_test), set()).add(attr)

    def merge_partials(self):
        """LanguageSpec.lookup_rules partials (C#): the parts of one partial type, which the engine gives one :Class
        node each, become ONE class for the pass — the first part's key (by file, then line) holds the union of
        every part's bases, fields and methods (each still resolved in the file it was written in: `parts["of"]`),
        every part's (file, class index) maps to it, and the other parts' keys leave `classes`. A part that is
        ambiguous, or a group with fewer than two indexed parts, is left as it is."""
        for lang in sorted(self.binders):
            spec = self.registry.get(lang)
            if spec is None or "partials" not in spec.lookup_rules:
                continue
            fn = getattr(self.binders[lang], "partials", None)
            try:
                groups = fn() if callable(fn) else ()
            except Exception:  # noqa: BLE001 — no grouping: every part stays its own class
                groups = ()
            for group in groups or ():
                keys = []
                for item in group or ():
                    try:
                        key = self.class_key.get((_norm_rel(item[0]), int(item[1])))
                    except (TypeError, ValueError, IndexError):
                        key = None
                    if key is not None and key in self.classes and key not in keys:
                        keys.append(key)
                if len(keys) < 2 or any(self.classes[k].ambiguous for k in keys):
                    continue
                keys.sort(key=lambda k: (self.classes[k].rel, self.classes[k].facts.line))
                parts = [self.classes[k] for k in keys]
                head = parts[0]
                of: dict = {}
                bases, fields, methods = [], {}, {}
                for p in parts:
                    for b in p.facts.bases:
                        of[id(b)] = p.rel
                        bases.append(b)
                    for f, bs in p.facts.fields.items():
                        for b in bs:
                            of[id(b)] = p.rel
                        fields.setdefault(f, []).extend(bs)
                    for m, mfs in p.facts.methods.items():
                        for mf in mfs:
                            of[id(mf)] = p.rel
                        methods.setdefault(m, []).extend(mfs)
                cf = head.facts
                merged = ClassFacts(cf.name, cf.line, cf.end_line, kind=cf.kind, bases=tuple(bases), fields=fields,
                                    methods=methods, dynamic=any(p.facts.dynamic for p in parts), open=True,
                                    lines=cf.lines, outer=cf.outer, scope=cf.scope,
                                    type_params=frozenset().union(*(p.facts.type_params for p in parts)))
                ci = _ClassInfo(head.key, head.rel, head.idx, merged, head.spec, head.node_name)
                ci.parts = {"rels": sorted({p.rel for p in parts}), "of": of}
                self.classes[head.key] = ci
                for p in parts:
                    self.class_key[(p.rel, p.idx)] = head.key
                    if p.key != head.key:
                        del self.classes[p.key]
                st = self.stats["by_lang"].setdefault(lang, _lang_stats())
                st.setdefault("lookup", {}).setdefault("partials", 0)
                st["lookup"]["partials"] += 1

    @staticmethod
    def rel_of(ci, obj):
        """The file a base TypeRef / field Binding / MethodFacts of class `ci` was written in (a merged partial
        type's part; otherwise the class's own file)."""
        if ci.parts is None:
            return ci.rel
        return ci.parts["of"].get(id(obj), ci.rel)

    def test_file(self, rel, unknown=True) -> bool:
        """The caller rule link() got (sync._is_test_path) for rel; `unknown` when there is none or it fails."""
        if self.is_test is None:
            return unknown
        try:
            return bool(self.is_test(rel))
        except Exception:  # noqa: BLE001
            return unknown

    def loose(self, rel, m) -> bool:
        """PATCH_RULES loose: is member m patched, in rel's language family, onto anything but one project class
        (so an external member call of m may run the project's code)? A patch written in a TEST file (a test that
        mocks `fs.readFile` or pollutes `Object.prototype`) counts only for test callers; a production patch for
        every caller."""
        fam = _sibling("edge_resolve").lang_family(rel)
        attrs = set(self.loose_patched.get((fam, False), ()))
        if self.test_file(rel):
            attrs |= self.loose_patched.get((fam, True), set())
        return bool(attrs) and (m in attrs or "*" in attrs)

    # ---------------------------------------------------------------- dynamic dispatch (override reach)
    def children(self) -> dict:
        """{class key: sorted [class keys whose bases name it]} over every indexed class (built once)."""
        if "children" not in self.memo:
            out: dict = {}
            for key in sorted(self.classes):
                for b in self.bases(key):
                    if b[0] == "class" and b[1] != key:
                        out.setdefault(b[1], set()).add(key)
            self.memo["children"] = {k: sorted(v) for k, v in out.items()}
        return self.memo["children"]

    def descendants(self, key) -> tuple:
        """Every project class that (transitively, in the graph) inherits from class `key`, sorted; never `key`."""
        mk = ("desc", key)
        if mk not in self.memo:
            kids = self.children()
            seen, todo = set(), list(kids.get(key, ()))
            while todo:
                k = todo.pop()
                if k in seen or k == key:
                    continue
                seen.add(k)
                todo.extend(kids.get(k, ()))
            self.memo[mk] = tuple(sorted(seen))
        return self.memo[mk]

    def subtree_bound(self, vals):
        """Field bindings that DISAGREE but all hold instances of one project class D or of its subclasses (`self.type:
        ParamType = ...` beside a subclass's `self.type = BoolParamType()`): the field is a D whose run-time class may
        be any of them — inst({D}) marked `decl` (dynamic dispatch then keeps D's overrides reachable). None when no
        single such D exists (unrelated classes, an external or unknown binding: never typed, design 3.1 R3)."""
        vals = [v for v in vals if v.kind != "none"]
        if not vals or any(v.kind != "inst" or not v.keys for v in vals):
            return None
        keys = set().union(*(v.keys for v in vals))
        roots = [d for d in sorted(keys)
                 if d in self.classes and not self.classes[d].ambiguous
                 and keys <= {d} | set(self.descendants(d))]
        if len(roots) != 1:
            return None
        return _V("inst", {roots[0]}, ev=frozenset().union(*(v.ev for v in vals)), weak=any(v.weak for v in vals),
                  ret=any(v.ret for v in vals), src=vals[0].src, decl=True)

    def dispatch_reach(self, key, m, mode, on_class):
        """The method nodes a call of m reaches AT RUN TIME beyond the static binding, when the receiver is typed
        as (an instance / the class object of) class `key` but may be any project subclass of it (mode "sub":
        a declared type, `self` / `cls`, a return annotation) — every definition of m that a subclass's chain
        puts BEFORE `key` (its overrides, a mixin ahead of `key`) — or, for `super().m()` written in class `key`
        (mode "super"), the first definition of m after `key` in every subclass's chain (a cooperative mixin).
        A set of node keys, or None when the reach cannot be known: `key` is an interface / protocol (its
        implementers are not its subclasses here), a subclass is ambiguous, unlinearizable or has an unresolved
        base ahead of `key` (it may be a project class defining m). Wave-2 `depth` chains (Go embedding) have no
        overriding: empty."""
        mk = ("reach", key, m, mode, bool(on_class))
        if mk in self.memo:
            return self.memo[mk]
        out = self._dispatch_reach(key, m, mode, on_class)
        self.memo[mk] = out
        return out

    def _dispatch_reach(self, key, m, mode, on_class):
        ci = self.classes.get(key)
        if ci is None or ci.ambiguous:
            return None
        if ci.spec is None or ci.spec.mro not in ("c3", "linear"):
            return set()
        if ci.facts.kind == "interface":
            return None
        out: set = set()
        subs = self.descendants(key)
        for s in ((key,) + subs if mode == "super" else subs):
            si = self.classes.get(s)
            if si is None or si.ambiguous:
                return None
            levels = self.mro(s)
            if levels is None:
                return None
            entries = [e for lv in levels for e in lv]
            if ("class", key) not in entries:
                return None
            at = entries.index(("class", key))
            if mode == "super":
                for e in entries[at + 1:]:
                    if e[0] != "class":
                        if e[0] == "unres":
                            return None
                        continue                     # an external class: no project node to keep
                    x = self.classes.get(e[1])
                    if x is None or x.ambiguous:
                        return None
                    mfs = self.member_defs(x, m, on_class)
                    if mfs:
                        out.update(self.method_nodes(x, m, mfs)[0])
                        break
                continue
            for e in entries[:at]:
                if e[0] != "class":
                    if e[0] == "unres":
                        return None
                    continue
                x = self.classes.get(e[1])
                if x is None or x.ambiguous:
                    return None
                mfs = self.member_defs(x, m, on_class)
                if mfs:
                    out.update(self.method_nodes(x, m, mfs)[0])
        return out

    # ---------------------------------------------------------------- symbols
    def binder_of(self, rel):
        return self.binders.get(self.lang_of.get(rel)) or Binder()

    def has_star(self, ff) -> bool:
        return "*" in ff.imports

    @staticmethod
    def star_after(ff, lines) -> bool:
        """Does a WRITTEN star import of ff (`from m import *`: an Import "*" with a spec) run after every binding
        of a file-level name at `lines` — or may it (a star or a binding without a line)? Such a star may rebind the
        name (`from .models import Order` then `from .other import *`). A spec-less star is a reader's MARKER of an
        unread top-level region, not a binding: it only keeps unbound names from resolving (has_star)."""
        stars = [i for i in ff.imports.get("*") or () if getattr(i, "kind", "") == "star" and getattr(i, "spec", "")]
        if not stars:
            return False
        bound = [_int(x) for x in lines]
        star_lines = [_int(getattr(i, "line", 0)) for i in stars]
        if not bound or min(bound) <= 0 or min(star_lines) <= 0:
            return True
        return max(star_lines) > max(bound)

    def file_symbol(self, rel, x, depth=0) -> _V:
        """What file-level name x of rel denotes (classes, functions, globals, imports; then the binder's free
        names, then the language's builtins). _UNBOUND when the file binds nothing of that name."""
        mk = ("fs", rel, x)
        if mk in self.memo:
            return self.memo[mk]
        if mk in self.busy or depth > REEXPORT_DEPTH + _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        self.busy.add(mk)
        c0 = self.cut
        try:
            v = self._file_symbol(rel, x, depth)
        finally:
            self.busy.discard(mk)
        if self.cut == c0:
            self.memo[mk] = v
        return v

    def _file_symbol(self, rel, x, depth) -> _V:
        ff = self.facts.get(rel)
        if ff is None:
            return _UNK
        cls_idx = [i for i, c in enumerate(ff.classes) if c.name == x and c.outer < 0 and c.scope < 0]
        fns, gl, im = ff.functions.get(x), ff.globals.get(x), ff.imports.get(x)
        tables = sum(1 for t in (cls_idx, fns, gl, im) if t)
        if tables > 1:
            return _UNK
        if tables and self.star_after(ff, [ff.classes[i].line for i in cls_idx] + [f.line for f in fns or ()]
                                      + [b.line for b in gl or ()] + [i.line for i in im or ()]):
            return _UNK                            # a later `from m import *` may rebind x at run time
        if cls_idx:
            key = self.class_key.get((rel, cls_idx[0])) if len(cls_idx) == 1 else None
            if key is None or self.classes[key].ambiguous:
                return _UNK
            return _V("cls", {key}, ev={rel}, src="class")
        if fns:
            return _V("func", ref=(rel, fns[0]), ev={rel}, src="func") if len(fns) == 1 else _UNK
        if gl:
            v = _join([self.eval_binding(rel, b, depth=depth + 1) for b in gl])
            return v.but(src="global") if v.kind in ("inst", "ext") else _UNK
        if im:
            vals = [self.import_value(rel, imp, depth + 1) for imp in im]
            if any(v.kind in ("unk", "unbound") for v in vals) or any(not v.same(vals[0]) for v in vals):
                return _UNK
            return vals[0].but(ev=frozenset().union(*(v.ev for v in vals)), src="import")
        if self.has_star(ff):
            return _UNK
        try:
            res = self.binder_of(rel).resolve_free(rel, x)
        except Exception:  # noqa: BLE001
            res = None
        if res is not None:
            v = self.res_value(rel, res, depth + 1)
            return v if v.kind != "unbound" else _UNK
        spec = self.spec_of(rel)
        if spec is not None and x in spec.builtins:
            return _V("ext", ref="builtin", src="builtin")
        if spec is not None and x in spec.builtin_funcs:
            return _V("ext", ref="builtin_func", src="builtin")
        return _UNBOUND

    def import_value(self, rel, imp, depth) -> _V:
        if imp.kind == "star":
            return _UNK
        try:
            res = self.binder_of(rel).resolve_import(rel, imp)
        except Exception:  # noqa: BLE001
            res = None
        return self.res_value(rel, res, depth, imp_kind=imp.kind)

    def res_value(self, rel, res, depth, lang=None, imp_kind=None) -> _V:
        if depth > REEXPORT_DEPTH + _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        if not _nt(res, Resolution) or res.kind not in RESOLUTION_KINDS:
            return _UNK
        if res.kind == "module":
            return _V("module", ref=res.rel, ev={res.rel}) if res.rel in self.graph_files else _UNK
        if res.kind == "symbol":
            if res.rel not in self.graph_files:
                return _UNK
            v = self.file_symbol(res.rel, res.name, depth + 1)
            if v.kind == "unbound":
                v = self.submodule_value(res.rel, res.name, depth + 1)
            return v.plus_ev({res.rel}) if v.kind not in ("unk", "unbound") else _UNK
        if res.kind == "namespace":
            return _V("ns", ref=(lang if lang is not None else self.lang_of.get(rel, ""), res.name))
        if res.kind == "external":
            stems = [str(s).lower() for s in (res.stems or ())]
            if not stems or any(s in self.stems for s in stems):
                return _UNK                        # the conservative external test (design 2.4)
            return _V("ext", ref="module" if imp_kind in ("module", "package", "namespace") else "symbol",
                      src="import")
        return _UNK

    def submodule_value(self, module, attr, depth, lang=None) -> _V:
        binder = self.binders.get(lang) if lang is not None else self.binder_of(module)
        try:
            res = (binder or Binder()).submodule(module, attr)
        except Exception:  # noqa: BLE001
            res = None
        if res is None:
            return _UNK
        v = self.res_value(module if lang is None else "", res, depth + 1,
                           lang=lang if lang is not None else self.lang_of.get(module, ""), imp_kind="module")
        return v if v.kind in ("module", "ns", "cls", "func", "ext") else _UNK

    def module_member(self, mod_rel, attr, depth) -> _V:
        v = self.file_symbol(mod_rel, attr, depth + 1)
        if v.kind == "unbound":
            v = self.submodule_value(mod_rel, attr, depth + 1)
        return v.plus_ev({mod_rel}) if v.kind not in ("unk", "unbound") else _UNK

    def member_value(self, base: _V, attr, depth) -> _V:
        if depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        if base.kind == "module":
            return self.module_member(base.ref, attr, depth + 1).plus_ev(base.ev)
        if base.kind == "ns":
            lang, token = base.ref
            return self.submodule_value(token, attr, depth + 1, lang=lang).plus_ev(base.ev)
        if base.kind == "cls":
            nested = self.nested_class(next(iter(base.keys)), attr)
            return _V("cls", {nested}, ev=base.ev | {self.classes[nested].rel}) if nested else _UNK
        if base.kind == "inst":
            return self.field_type(base, attr, depth + 1)
        if base.kind == "ext" and base.ref == "module":
            return _V("ext", ref="module-attr", ev=base.ev, src="import")   # os.path
        return _UNK                                # an attribute of any other external value may be anything

    def nested_class(self, key, name):
        ci = self.classes.get(key)
        if ci is None:
            return None
        hits = self.nested_of.get(ci.rel, {}).get((ci.idx, name), ())
        k = self.class_key.get((ci.rel, hits[0])) if len(hits) == 1 else None
        return k if k is not None and not self.classes[k].ambiguous else None

    # ---------------------------------------------------------------- scopes and names
    def shadow(self, rel, si) -> frozenset:
        """Names a TypeRef cannot mean as a class in scope si: everything the scope chain binds, its type
        parameters and the classes defined inside it."""
        mk = ("sh", rel, si)
        if mk in self.memo:
            return self.memo[mk]
        ff = self.facts.get(rel)
        out = set()
        s = si
        while ff is not None and 0 <= s < len(ff.scopes):
            sc = ff.scopes[s]
            out.update(sc.params, sc.locals, sc.poisoned, sc.type_params)
            out.update(self.local_cls.get(rel, {}).get(s, ()))
            s = sc.parent
        self.memo[mk] = frozenset(out)
        return self.memo[mk]

    def scope_class_key(self, rel, si):
        ff = self.facts.get(rel)
        if ff is None or not 0 <= si < len(ff.scopes):
            return None
        sc = ff.scopes[si]
        return self.class_key.get((rel, sc.cls)) if sc.cls >= 0 else None

    def name_value(self, rel, si, x, depth) -> _V:
        """IDENT x in scope si: the scope chain (params, locals, poisoned, local classes), then the file."""
        ff = self.facts.get(rel)
        if ff is None:
            return _UNK
        s = si
        while 0 <= s < len(ff.scopes):
            sc = ff.scopes[s]
            if x in sc.poisoned:
                return _UNK
            if x in sc.params or x in sc.locals:
                return self.local_value(rel, s, x, depth + 1)
            if x in self.local_cls.get(rel, {}).get(s, ()):
                return _UNK
            s = sc.parent
        mv = self.member_scope(rel, si, x, depth)       # C#: a member of the class chain comes before the file's names
        if mv is not None:
            return mv
        v = self.file_symbol(rel, x, depth + 1)
        return _UNK if v.kind == "unbound" else v

    def local_value(self, rel, si, x, depth) -> _V:
        mk = ("lv", rel, si, x)
        if mk in self.memo:
            return self.memo[mk]
        if mk in self.busy or depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        self.busy.add(mk)
        c0 = self.cut
        try:
            sc = self.facts[rel].scopes[si]
            binds = ([sc.params[x]] if x in sc.params else []) + list(sc.locals.get(x, ()))
            vals = [self.eval_binding(rel, b, depth=depth + 1, home=si) for b in binds]
            spec = self.spec_of(rel)
            v = _join(vals) if (spec is None or spec.unions or _agree(vals)) else _UNK
            if v.kind in ("inst", "ext"):
                v = v.but(src="param" if (x in sc.params and not sc.locals.get(x)) else "local")
                if v.kind == "inst" and not v.decl and spec is not None and "virtual_dispatch" in spec.lookup_rules \
                        and x in self.cs_reassigned(rel, si):
                    v = v.but(decl=True)           # C#: `var x = new T()` later rebound may hold a subclass of T
            else:
                v = _UNK
        finally:
            self.busy.discard(mk)
        if self.cut == c0:
            self.memo[mk] = v
        return v

    def eval_binding(self, rel, b, depth=0, self_key=None, extra_shadow=frozenset(), home=None, ctx=None) -> _V:
        """One Binding's value (instances / externals only: a class, module or function value is never typed).
        `ctx`: the class whose nested types a type name may mean (eval_type), when the binding is a class member's."""
        if depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        k = b.kind
        if k == "none":
            return _NONEV
        if k == "unknown":
            return _UNK
        si = b.scope if b.scope >= 0 else (home if home is not None else -1)
        if k in ("ann", "param", "ctor"):
            if b.type is None:
                return _UNK
            v = self.eval_type(b.type, rel, si, self_key=self_key, depth=depth + 1, extra_shadow=extra_shadow,
                               ctx=ctx if b.scope < 0 else None)
            if v.kind not in ("inst", "ext", "none"):
                return _UNK
            if k == "param":
                return v.but(src="param", decl=True)
            return v.but(src="ctor") if k == "ctor" else v.but(src="local", decl=True)
        if b.value is None:
            return _UNK
        if k == "call":
            if b.value.kind != "CALL":
                return _UNK
            v = self.call_result(rel, si, b.value, depth + 1)
        else:                                       # field: the value of an expression
            v = self.eval_recv(rel, si, b.value, depth + 1)
        return v if v.kind == "inst" or (v.kind == "ext" and v.ref in _EXT_VALUE_OK) else _UNK

    # ---------------------------------------------------------------- types
    def eval_type(self, t, rel, si, self_key=None, depth=0, opaque=True, extra_shadow=frozenset(), ctx=None) -> _V:
        """The value a written type denotes in scope si of rel. `ctx` (LanguageSpec.lookup_rules nested_types only):
        the class whose nested types a type name's head may mean when it is not si's class (a field's or a method's
        declaring class, an outer class for a base list)."""
        if t is None:
            return _UNK
        if depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        spec = self.spec_of(rel)
        k = t.kind
        nested = spec is not None and "nested_types" in spec.lookup_rules
        if k == "name":
            nm = t.name
            if nm in _NONE_TYPES:
                return _NONEV
            if nm in extra_shadow or nm in self.shadow(rel, si):
                return _UNK
            hv = self.cs_type_head(rel, si, nm, ctx) if nested else None
            return self._type_of(hv if hv is not None else self.file_symbol(rel, nm, depth + 1), nm, spec, opaque)
        if k == "qualified":
            parts = t.parts
            if not parts or parts[0] in extra_shadow or parts[0] in self.shadow(rel, si):
                return _UNK
            v = self.cs_type_head(rel, si, parts[0], ctx) if nested else None
            if v is None:
                v = self.file_symbol(rel, parts[0], depth + 1)
            for p in parts[1:]:
                if v.kind not in ("module", "ns", "cls"):
                    return _UNK
                v = self.member_value(v, p, depth + 1)
            return self._type_of(v, parts[-1], spec, opaque)
        if k == "union":
            vals = [self.eval_type(a, rel, si, self_key, depth + 1, opaque, extra_shadow, ctx=ctx) for a in t.args]
            vals = [v for v in vals if v.kind != "none"]
            if not vals:
                return _UNK
            if all(v.kind == "inst" for v in vals):
                if len(vals) > 1 and spec is not None and not spec.unions:
                    return _UNK                     # TS / JS: `A | B` never binds (only `T | null | undefined`)
                return _join(vals)
            return vals[0] if len(vals) == 1 else _UNK
        if k == "nullable":
            return self.eval_type(t.args[0], rel, si, self_key, depth + 1, opaque, extra_shadow, ctx=ctx) \
                if t.args else _UNK
        if k == "generic":
            if not t.args:
                return _UNK
            v = self.eval_type(t.args[0], rel, si, self_key, depth + 1, opaque, extra_shadow, ctx=ctx)
            return v if v.kind == "inst" else _UNK      # a project generic Foo[T] -> Foo; List[X] / Promise<T> -> ?
        if k == "self":
            key = self_key or self.scope_class_key(rel, si)
            if key is None or key not in self.classes or self.classes[key].ambiguous:
                return _UNK
            return _V("inst", {key}, ev={self.classes[key].rel})
        if k == "classof":
            v = self.eval_type(t.args[0], rel, si, self_key, depth + 1, opaque, extra_shadow, ctx=ctx) \
                if t.args else _UNK
            return _V("cls", v.keys, ev=v.ev) if v.kind == "inst" and len(v.keys) == 1 else _UNK
        if k == "builtin":
            if t.name in _NONE_TYPES:
                return _NONEV
            if opaque and spec is not None and t.name in spec.opaque:
                return _UNK
            return _V("ext", ref="builtin", src="builtin")
        return _UNK

    @staticmethod
    def _type_of(v: _V, nm, spec, opaque) -> _V:
        if v.kind == "cls":
            return _V("inst", v.keys, ev=v.ev)
        if v.kind == "ext":
            if v.ref not in ("builtin", "symbol", "module-attr", "type") or (
                    opaque and spec is not None and nm in spec.opaque):
                return _UNK
            return _V("ext", ref="builtin" if v.ref == "builtin" else "type", ev=v.ev, src=v.src)
        return _UNK

    # ---------------------------------------------------------------- classes: MRO and lookup
    def bases(self, key) -> list:
        ci = self.classes[key]
        out = []
        for b in ci.facts.bases:
            head = b
            while head.kind == "generic" and head.args:
                head = head.args[0]
            v = self.eval_type(head, self.rel_of(ci, b), ci.facts.scope, depth=1, opaque=False,
                               extra_shadow=ci.facts.type_params, ctx=self.outer_key(key))
            nm = head.name if head.kind in ("name", "builtin") else (head.parts[-1] if head.parts else "")
            if v.kind == "inst" and len(v.keys) == 1:
                out.append(("class", next(iter(v.keys))))
            elif v.kind == "ext":
                if nm in ci.spec.neutral:
                    continue
                out.append(("ext", nm))
            else:
                out.append(("unres", nm))
        return out

    def mro(self, key):
        """Levels of entries ("class", key) / ("ext", name) / ("unres", name), or None (no linearization)."""
        mk = ("mro", key)
        if mk in self.memo:
            return self.memo[mk]
        if mk in self.busy:                            # re-entered from its own base evaluation: a cut
            self.cut += 1
            return None
        self.busy.add(mk)
        c0 = self.cut
        try:
            ci = self.classes.get(key)
            style = ci.spec.mro if ci is not None else "linear"
            if style == "c3":
                lin = self._c3(key, frozenset())
                levels = None if lin is None else tuple((e,) for e in lin)
            elif style == "depth":
                levels = self._depth(key)
            else:
                lin = self._linear(key, frozenset())
                levels = None if lin is None else tuple((e,) for e in lin)
            if levels is not None and sum(len(lv) for lv in levels) > _MRO_MAX:
                levels = None
        finally:
            self.busy.discard(mk)
        if self.cut == c0:
            self.memo[mk] = levels
        return levels

    def _c3(self, key, stack):
        mk = ("c3", key)
        if mk in self.memo:
            return self.memo[mk]
        if key in stack or len(stack) > _MRO_MAX:     # a class-hierarchy cycle: None from any entry point
            return None
        c0 = self.cut
        out = self._c3_of(key, stack)
        if self.cut == c0:
            self.memo[mk] = out
        return out

    def _c3_of(self, key, stack):
        bases = self.bases(key)
        seqs = []
        for b in bases:
            if b[0] == "class":
                sub = self._c3(b[1], stack | {key})
                if sub is None:
                    return None
                seqs.append(list(sub))
            else:
                seqs.append([b])
        seqs.append(list(bases))
        out = [("class", key)]
        while True:
            seqs = [s for s in seqs if s]
            if not seqs:
                break
            head = None
            for s in seqs:
                if not any(s[0] in t[1:] for t in seqs):
                    head = s[0]
                    break
            if head is None:
                return None
            out.append(head)
            for s in seqs:
                if s and s[0] == head:
                    s.pop(0)
            if len(out) > _MRO_MAX:
                return None
        return tuple(out)

    def _linear(self, key, stack):
        mk = ("lin", key)
        if mk in self.memo:
            return self.memo[mk]
        if key in stack or len(stack) > _MRO_MAX:
            return None
        c0 = self.cut
        out = self._linear_of(key, stack)
        if self.cut == c0:
            self.memo[mk] = out
        return out

    def _linear_of(self, key, stack):
        out, seen = [("class", key)], {("class", key)}
        for b in self.bases(key):
            sub = self._linear(b[1], stack | {key}) if b[0] == "class" else (b,)
            if sub is None:
                return None
            for e in sub:
                if e not in seen:
                    seen.add(e)
                    out.append(e)
            if len(out) > _MRO_MAX:
                return None
        return tuple(out)

    def _depth(self, key):
        levels, seen, cur = [], {key}, [("class", key)]
        while cur and len(levels) < 16:
            levels.append(tuple(cur))
            nxt = []
            for e in cur:
                if e[0] != "class":
                    continue
                for b in self.bases(e[1]):
                    if b[0] == "class":
                        if b[1] in seen:
                            continue
                        seen.add(b[1])
                    nxt.append(b)
            cur = nxt
        return tuple(levels)

    def mro_class_keys(self, key) -> set:
        """The classes of key's chain before any opaque entry (the audit's subclass test)."""
        out = set()
        for level in self.mro(key) or ():
            for e in level:
                if e[0] != "class":
                    return out
                out.add(e[1])
        return out

    @staticmethod
    def member_defs(ci, m, on_class) -> list:
        """The definitions of member m a receiver sees: all of them — except in a LanguageSpec.static_split
        language, where a class object sees only the static ones and an instance only the others."""
        mfs = ci.facts.methods.get(m) or []
        if ci.spec is not None and ci.spec.static_split:
            mfs = [mf for mf in mfs if ("static" in mf.flags) == bool(on_class)]
        return mfs

    def method_nodes(self, ci, m, mfs=None):
        """([node keys], {key: MethodFacts|None}) of method m of class ci (of its definitions `mfs`, default
        all): the node at the def line (or a decorator line); else the nodes named `<Owner>.m` in the class's
        file — only when `mfs` are all its definitions (a static and an instance `m` share that name: the
        engine's one node may be the other's). Never a same-named node elsewhere."""
        every = ci.facts.methods.get(m) or []
        mfs = every if mfs is None else mfs
        keys, by = [], {}
        for mf in mfs:
            k = self.node_at(self.rel_of(ci, mf), (mf.line,) + tuple(mf.lines), m, TARGET_LABELS)
            if k is not None and k not in by:
                keys.append(k)
                by[k] = mf
        if not keys and mfs and len(mfs) == len(every):
            owner = name_seg(ci.node_name)
            for rel in (ci.parts["rels"] if ci.parts is not None else (ci.rel,)):
                for k in self.by_file_seg.get((rel, m), ()):
                    if self.label_of.get(k) in TARGET_LABELS and self.name_of.get(k) == f"{owner}.{m}" \
                            and k not in by:
                        keys.append(k)
                        by[k] = None
        return keys, by

    def lookup(self, key, m, *, skip=0, argc=-1, on_class=False) -> _Look:
        """Member m on (instances of) class key, over its chain: hit (the method node(s)) | absent | unknown."""
        levels = self.mro(key)
        if levels is None:
            return _Look("unknown", why="mro")
        ev, closed = set(), True
        for li, level in enumerate(levels):
            if li < skip:
                continue
            hits = []
            for ent in level:
                if ent[0] != "class":
                    return _Look("unknown", why="external base")
                ci = self.classes.get(ent[1])
                if ci is None or ci.ambiguous or ci.facts.dynamic:
                    return _Look("unknown", why="dynamic")
                ev.add(ci.rel)
                if m in ci.patched:
                    return _Look("unknown", why="patched")
                if "*" in ci.patched:                 # PATCH_RULES star: a mixin no file reads
                    return _Look("unknown", why="patched")
                if m in ci.facts.fields:
                    return _Look("unknown", why="field")
                if ci.facts.open or not ci.spec.absent_ok:
                    closed = False
                if on_class:
                    nested = self.nested_class(ci.key, m)
                    if nested is not None:
                        hits.append(("class", nested, ci))
                        continue
                if self.member_defs(ci, m, on_class):
                    hits.append(("method", None, ci))
            if not hits:
                continue
            if len(hits) > 1:
                return _Look("unknown", why="tie")
            what, nested, ci = hits[0]
            if what == "class":
                return _Look("hit", (nested,), (None,), ci, frozenset(ev | {self.classes[nested].rel}),
                             is_class=True)
            mfs = self.member_defs(ci, m, on_class)
            if any("property" in mf.flags for mf in mfs):
                return _Look("unknown", why="property")
            keys, by = self.method_nodes(ci, m, mfs)
            if not keys:
                return _Look("unknown", why="no node")
            if argc >= 0:
                fit = [k for k in keys if by[k] is None or _arity_ok(by[k].arity, argc)]
                if not fit:
                    return _Look("unknown", why="arity")
                keys = fit
            keys = sorted(keys)
            return _Look("hit", tuple(keys), tuple(by[k] for k in keys), ci,
                         frozenset(ev | {self.file_of[k] for k in keys}))
        if closed:
            return _Look("absent", ev=frozenset(ev))
        return _Look("unknown", why="open")

    def field_type(self, base: _V, attr, depth) -> _V:
        """`inst.attr`: every binding of attr across each candidate class's whole chain, joined (R3); a property's
        return annotation counts (INFERRED); a plain method, a dynamic class or an opaque base -> unknown. When
        the instance may be one of a SUBCLASS (base.decl: a declared type, `self`, a return annotation), every
        project subclass's bindings of attr join too: a subclass that rebinds the field to an unrelated class
        makes it unknown (`self.session = OtherSession()` in SubHolder: Holder's `self.session.get()` may reach
        OtherSession.get); bindings that all hold instances of one class or of its subclasses type it as that
        class, marked `decl` (subtree_bound: click's `self.type: ParamType` beside Option's BoolParamType())."""
        results = []
        sub = bool(base.decl)
        for key in sorted(base.keys):
            mk = ("ft", key, attr, sub)
            if mk in self.memo:
                results.append(self.memo[mk])
                continue
            if mk in self.busy or depth > _EVAL_DEPTH:
                self.cut += 1
                return _UNK
            self.busy.add(mk)
            c0 = self.cut
            try:
                v = self._field_type(key, attr, depth, sub)
            finally:
                self.busy.discard(mk)
            if self.cut == c0:
                self.memo[mk] = v
            results.append(v)
        v = _join(results)
        return v.plus_ev(base.ev).but(weak=v.weak or base.weak, ret=v.ret or base.ret, src="field") \
            if v.kind in ("inst", "ext") else _UNK

    def _field_type(self, key, attr, depth, sub=False) -> _V:
        ci0 = self.classes.get(key)
        if ci0 is not None and ci0.spec is not None and "member_scope" in ci0.spec.lookup_rules:
            return self.cs_field_type(key, attr, depth)
        levels = self.mro(key)
        if levels is None:
            return _UNK
        vals, ev = [], set()
        own = len(levels)
        if sub:                                      # the instance may be a subclass's: its bindings count too
            levels = tuple(levels) + tuple((("class", s),) for s in self.descendants(key))
        for li, level in enumerate(levels):
            for ent in level:
                if ent[0] != "class":
                    return _UNK
                ci = self.classes.get(ent[1])
                if ci is None or ci.ambiguous or attr in ci.patched or "*" in ci.patched or (
                        li < own and ci.facts.dynamic):   # (a dynamic SUBCLASS still binds its fields as written)
                    return _UNK
                if li < own or attr in ci.facts.fields or attr in ci.facts.methods:
                    ev.add(ci.rel)                   # a subclass is evidence only where it binds attr itself
                sk = key if li < own else ci.key     # `Self` in a subclass's own binding is that subclass
                mfs = ci.facts.methods.get(attr)
                if mfs:
                    if not all("property" in mf.flags and mf.returns is not None for mf in mfs):
                        return _UNK
                    for mf in mfs:
                        rv = self.eval_type(mf.returns, ci.rel, ci.facts.scope, self_key=sk, depth=depth + 1,
                                            extra_shadow=ci.facts.type_params)
                        vals.append(rv.but(weak=True, ret=True, decl=True))
                for b in ci.facts.fields.get(attr, ()):
                    fv = self.eval_binding(ci.rel, b, depth=depth + 1, self_key=sk, extra_shadow=ci.facts.type_params,
                                           home=ci.facts.scope if ci.facts.scope >= 0 else None)
                    if b.kind == "param" or fv.src == "param":
                        fv = fv.but(weak=True)               # an injected field `self.f = p`: INFERRED
                    vals.append(fv)
        if not vals or not _agree(vals):
            bound = self.subtree_bound(vals)
            if bound is None:
                return _UNK                          # a field bound to two different classes is never typed (3.1 R3)
            return bound.plus_ev(ev)
        v = _join(vals)
        return v.plus_ev(ev) if v.kind in ("inst", "ext") else _UNK

    def cs_field_type(self, key, attr, depth) -> _V:
        """C# (LanguageSpec.lookup_rules member_scope): a field / property binds STATICALLY to its nearest declaration
        on the class chain, so the declared type of the first class declaring `attr` is its type (Roslyn's binding),
        whatever a later external base or a subclass declares; unknown when an external / unresolved base, a dynamic
        or ambiguous class or a method of that name comes first, or the declarations disagree."""
        levels = self.mro(key)
        if levels is None:
            return _UNK
        ev = set()
        for level in levels:
            for ent in level:
                if ent[0] != "class":
                    return _UNK
                ci = self.classes.get(ent[1])
                if ci is None or ci.ambiguous or ci.facts.dynamic or attr in ci.facts.methods:
                    return _UNK
                ev.add(ci.rel)
                binds = ci.facts.fields.get(attr)
                if not binds:
                    continue
                vals = [self.eval_binding(self.rel_of(ci, b), b, depth=depth + 1, self_key=key,
                                          extra_shadow=ci.facts.type_params,
                                          home=ci.facts.scope if ci.facts.scope >= 0 else None, ctx=ci.key)
                        for b in binds]
                if not _agree(vals):
                    return _UNK
                v = _join(vals)
                return v.plus_ev(ev | {self.rel_of(ci, b) for b in binds}) if v.kind in ("inst", "ext") else _UNK
        return _UNK

    def member_scope(self, rel, si, x, depth):
        """C# (LanguageSpec.lookup_rules member_scope): a simple name the scope chain does not bind is first a member
        of the enclosing class chain (another part of a partial type and an inherited project class included) — a
        field / property: its declared type; a method (a method group): unknown; a nested type: its class — and,
        when that chain's class base is external or unresolved (`OrdersController : ControllerBase`), unknown (an
        inherited member such as `Request` or `User` may be what the name means). A member of an outer type is
        unknown too. None: not a member, so the file's names decide."""
        spec = self.spec_of(rel)
        if spec is None or "member_scope" not in spec.lookup_rules:
            return None
        key = self.scope_class_key(rel, si)
        if key is None or key not in self.classes:
            return None
        st = self.stats["by_lang"].setdefault(spec.name, _lang_stats()).setdefault("lookup", {})
        levels = self.mro(key)
        if levels is None:
            return _UNK
        chain = []
        for e in (e for lv in levels for e in lv):
            if e[0] != "class":
                break                                # what follows an external / unresolved base is not read
            chain.append(e[1])
        for ck in chain:
            ci = self.classes.get(ck)
            if ci is None or ci.ambiguous:
                return _UNK
            if ci.facts.kind == "interface":
                continue                             # an interface's members are never reached by a simple name
            if x in ci.facts.fields:
                st["member_scope"] = st.get("member_scope", 0) + 1
                v = self.field_type(_V("inst", {key}, ev={self.classes[key].rel}, decl=True, src="self"), x,
                                    depth + 1)
                return v if v.kind in ("inst", "ext") else _UNK
            if x in ci.facts.methods:
                return _UNK
            nested = self.nested_class(ci.key, x)
            if nested is not None:
                return _V("cls", {nested}, ev={ci.rel, self.classes[nested].rel}, src="class")
        if self.member_open(key):
            return _UNK
        ff = self.facts.get(rel)
        sc = ff.scopes[si] if ff is not None and 0 <= si < len(ff.scopes) else None
        o = ff.classes[sc.cls].outer if sc is not None and 0 <= sc.cls < len(ff.classes) else -1
        while ff is not None and 0 <= o < len(ff.classes):
            if x in ff.classes[o].fields or x in ff.classes[o].methods:
                return _UNK
            o = ff.classes[o].outer
        return None

    def member_open(self, key) -> bool:
        """C#: may an instance of class `key` carry members this pass cannot see — its class-base chain (the FIRST
        base-list entry, which C# reserves for the base class) reaching an external or unresolved class? An external
        first entry named like an interface (`I` + an upper-case letter) is read as one: an interface contributes no
        member reachable by a simple name."""
        mk = ("mopen", key)
        if mk not in self.memo:
            out, seen, cur = False, set(), key
            while cur is not None and cur not in seen:
                seen.add(cur)
                bs = self.bases(cur) if cur in self.classes else []
                if not bs:
                    break
                b = bs[0]
                if b[0] == "class":
                    nxt = self.classes.get(b[1])
                    cur = b[1] if nxt is not None and nxt.facts.kind != "interface" else None
                    continue
                out = not re.match(r"I[A-Z]", str(b[1] or ""))
                break
            self.memo[mk] = out
        return self.memo[mk]

    # ---------------------------------------------------------------- C#: the reader's private table
    def cs_table(self, rel):
        """The C# reader's private `_cs` table of rel's FileFacts, or None (a reader that keeps none)."""
        t = getattr(self.facts.get(rel), "_cs", None)
        return t if isinstance(t, dict) else None

    def cs_member(self, ci, mf, m, table):
        """_cs[table][(m, mf.line)] for definition mf of member m of class ci (table mods: the modifier words;
        mparams: (((TypeRef | None, passing, optional), ...), variadic)): the entry, None when unknown (not recorded,
        two members of that name on one line) or _NOINFO when the file's reader keeps no such table."""
        if mf is None:
            return None
        t = self.cs_table(self.rel_of(ci, mf))
        if t is None or not isinstance(t.get(table), dict):
            return _NOINFO
        return t[table].get((m, mf.line))

    def cs_reassigned(self, rel, si) -> frozenset:
        """C#: the names an assignment (or a ref / out argument) in scope si's span rebinds (_cs["reassigned"]); empty
        for a reader that keeps no such table (its constructed locals stay exact, as before the table existed)."""
        t = self.cs_table(rel)
        try:
            return frozenset(t["reassigned"][si])
        except (TypeError, KeyError, IndexError):
            return frozenset()

    def outer_key(self, key):
        """The class key of class key's enclosing type, or None."""
        ci = self.classes.get(key)
        if ci is None or ci.facts.outer < 0:
            return None
        return self.class_key.get((ci.rel, ci.facts.outer))

    def class_parts(self, key) -> list:
        """[(rel, class index)] of every declaration of class key (several for a merged partial type)."""
        if "parts_of" not in self.memo:
            po: dict = {}
            for (r, i), k in sorted(self.class_key.items()):
                po.setdefault(k, []).append((r, i))
            self.memo["parts_of"] = po
        return self.memo["parts_of"].get(key, [])

    # ---------------------------------------------------------------- C#: nested type names (lookup_rules nested_types)
    def cs_nested(self, key, name):
        """The nested type `name` declared in class key (any part): ("one", class key) | ("unk",) (declared, but
        without one indexed node) | None (not declared there)."""
        found = []
        for rel, idx in self.class_parts(key):
            found.extend(self.class_key.get((rel, j)) for j in self.nested_of.get(rel, {}).get((idx, name), ()))
        if not found:
            return None
        ks = set(found)
        if None in ks or len(ks) != 1 or self.classes[next(iter(ks))].ambiguous:
            return ("unk",)
        return ("one", next(iter(ks)))

    def cs_base_chain(self, key) -> list:
        """key, then its project base classes in order (interfaces skipped unless key is one), up to the first external
        or unresolved entry: the classes whose nested types a simple name inside key may mean."""
        out = [key]
        iface = self.classes[key].facts.kind == "interface"
        for e in (e for lv in (self.mro(key) or ()) for e in lv):
            if e[0] != "class":
                break
            c = self.classes.get(e[1])
            if c is None:
                break
            if e[1] != key and (iface or c.facts.kind != "interface"):
                out.append(e[1])
        return out

    def cs_type_head(self, rel, si, name, ctx):
        """C# (lookup_rules nested_types): what a type name `name` written in scope si (or in class ctx) means when a
        nested type holds it — C#'s order: the nested types of the scope's class and of its project base classes,
        then of each outer class (and its bases), before any namespace-level name. A _V cls; _UNK when such a nested
        type is declared but cannot be placed; None when none is (the file's names decide: file_symbol). A nested
        type inherited from an EXTERNAL base is not seen (its members are not read)."""
        key = ctx if ctx is not None else self.scope_class_key(rel, si)
        seen = set()
        while key is not None and key in self.classes and key not in seen:
            seen.add(key)
            for k in self.cs_base_chain(key):
                got = self.cs_nested(k, name)
                if got is None:
                    continue
                if got[0] != "one":
                    return _UNK
                self._lookup_stat(rel, "nested_types")
                return _V("cls", {got[1]}, ev={self.classes[k].rel, self.classes[got[1]].rel}, src="class")
            key = self.outer_key(key)
        return None

    # ---------------------------------------------------------------- C#: argument applicability (rule overloads)
    def cs_supertypes(self, key):
        """({class key and every project class / interface on its chain}, closed: no external / unresolved entry)."""
        levels = self.mro(key)
        if levels is None:
            return {key}, False
        out, closed = set(), True
        for e in (e for lv in levels for e in lv):
            if e[0] == "class":
                out.add(e[1])
            else:
                closed = False
        return out, closed

    def cs_field_typeref(self, key, attr):
        """(TypeRef, its file, its class key) of field / property attr as the first class of key's chain declaring
        it writes it — one written type across its declarations — or None."""
        for e in (e for lv in (self.mro(key) or ()) for e in lv):
            if e[0] != "class":
                return None
            ci = self.classes.get(e[1])
            if ci is None or ci.ambiguous or attr in ci.facts.methods:
                return None
            binds = ci.facts.fields.get(attr)
            if not binds:
                continue
            ts = {b.type for b in binds if b.kind == "ann" and b.type is not None}
            if len(ts) != 1 or any(b.kind != "ann" for b in binds):
                return None
            return next(iter(ts)), self.rel_of(ci, binds[0]), ci.key
        return None

    def cs_decl_typeref(self, rel, si, r):
        """(TypeRef, its file, its scope index, its class context) of the DECLARED type of receiver r — a parameter
        or local of the scope chain written with one type, a field / property of a typed instance — or None."""
        if r is None:
            return None
        ff = self.facts.get(rel)
        if r.kind == "IDENT" and ff is not None:
            s = si
            while 0 <= s < len(ff.scopes):
                sc = ff.scopes[s]
                if r.name in sc.poisoned:
                    return None
                if r.name in sc.params or r.name in sc.locals:
                    binds = ([sc.params[r.name]] if r.name in sc.params else []) + list(sc.locals.get(r.name, ()))
                    ts = {b.type for b in binds if b.kind in ("ann", "param") and b.type is not None}
                    if len(ts) != 1 or any(b.kind not in ("ann", "param") for b in binds):
                        return None
                    return next(iter(ts)), rel, s, None
                s = sc.parent
            key = self.scope_class_key(rel, si)
            got = self.cs_field_typeref(key, r.name) if key in self.classes else None
            return (got[0], got[1], -1, got[2]) if got else None
        if r.kind == "FIELD":
            base = self.eval_recv(rel, si, r.inner, 0)
            if base.kind != "inst" or len(base.keys) != 1:
                return None
            got = self.cs_field_typeref(next(iter(base.keys)), r.name)
            return (got[0], got[1], -1, got[2]) if got else None
        return None

    def cs_arg_type(self, rel, si, r):
        """An argument's static type for the applicability rule: ("lit", builtin) a literal, ("b", builtin) a value
        declared with a predefined type, ("c", class key) a project class instance, or None (unknown)."""
        if r is None:
            return None
        if r.kind == "LITERAL":
            return ("lit", r.type.name) if r.type is not None and r.type.kind == "builtin" else None
        d = self.cs_decl_typeref(rel, si, r)
        if d is not None:
            if d[0].kind == "builtin":
                return ("b", d[0].name)
            if d[0].kind not in ("name", "qualified"):
                return None                        # generic, nullable, array ...: its arguments are not compared
        v = self.eval_recv(rel, si, r, 0)
        return ("c", next(iter(v.keys))) if v.kind == "inst" and len(v.keys) == 1 else None

    def cs_param_type(self, ci, rel_d, t):
        """A declared parameter type: ("b", builtin), ("b?", builtin) a nullable one, ("c", class key) or None (a type
        parameter, a generic, an array, an external or unresolved type)."""
        if t is None:
            return None
        if t.kind == "builtin":
            return ("b", t.name)
        if t.kind == "nullable" and t.args and t.args[0].kind == "builtin":
            return ("b?", t.args[0].name)          # `int?` takes an int (and what widens to one)
        if t.kind not in ("name", "qualified"):
            return None
        v = self.eval_type(t, rel_d, -1, opaque=False, ctx=ci.key)
        return ("c", next(iter(v.keys))) if v.kind == "inst" and len(v.keys) == 1 else None

    def cs_fits(self, a, p) -> bool:
        """Is an argument of static type a KNOWN to convert implicitly to parameter type p (identity, an implicit
        numeric conversion, a project class to itself, a base class or an interface it lists, anything to object)?
        Unknown counts as no: the rule then answers unknown, never a binding it cannot prove."""
        if a is None or p is None:
            return False
        if p == ("b", "object") or p == ("b", "dynamic"):
            return True
        if a[0] in ("lit", "b"):
            return p[0] in ("b", "b?") and (a[1] == p[1] or p[1] in _CS_WIDEN.get(a[1], ()))
        return p[0] == "c" and (a[1] == p[1] or p[1] in self.cs_supertypes(a[1])[0])

    @staticmethod
    def _cs_sig_params(s):
        return tuple(p[0] for p in s[0]) if isinstance(s, tuple) and s and isinstance(s[0], tuple) else None

    @staticmethod
    def _cs_sig_takes(params, variadic, argc) -> bool:
        """Can a parameter list (each (TypeRef, passing, optional)) take argc positional arguments (-1: unknown)?"""
        if argc < 0:
            return True
        need = sum(1 for p in params if not p[2])
        return need <= argc and (variadic or argc <= len(params))

    # ---------------------------------------------------------------- C#: signatures across a base list (overloads)
    def cs_base_sigs(self, key) -> dict:
        """{id(base TypeRef): the reader's tagged tuple of it (_cs bsig)} over every part of class key."""
        out: dict = {}
        for rel, idx in self.class_parts(key):
            t, ff = self.cs_table(rel), self.facts.get(rel)
            bs = t.get("bsig", {}).get(idx) if t is not None and isinstance(t.get("bsig"), dict) else None
            if ff is None or not isinstance(bs, tuple) or not 0 <= idx < len(ff.classes) \
                    or len(bs) != len(ff.classes[idx].bases):
                continue
            for b, s in zip(ff.classes[idx].bases, bs):
                out[id(b)] = s
        return out

    def cs_tporder(self, key):
        """Class key's own type parameters in order (_cs tporder), or None (unknown)."""
        ci = self.classes.get(key)
        t = self.cs_table(ci.rel) if ci is not None else None
        got = t.get("tporder", {}).get(ci.idx) if t is not None and isinstance(t.get("tporder"), dict) else None
        return got if isinstance(got, tuple) else None

    def cs_subst(self, key, target):
        """{type parameter of class target: its normalised type argument} as class key's base list (and its bases'
        lists, composed) writes it, seen from class key: `OrderHandler : BaseHandler<CreateOrder, int>` gives
        BaseHandler {TReq: CreateOrder, TRes: int}; key's own type parameters are identity tokens ("tpv", key, name),
        so `CartStore<T> : Store<T>` gives Store {T: CartStore's T} and target == key gives key's tokens (a generic
        declaration's `M(T)` is never the signature of `M(Cart)`). A parameter the path does not fix is absent (it
        compares as _CS_UNK: never the same type); None when no base-list path from key reaches target."""
        mk = ("csub", key, target)
        if mk in self.memo:
            return self.memo[mk]
        own = self.cs_tporder(key) if key in self.classes else None
        out, cur, subst, seen = None, key, {nm: ("tpv", key, nm) for nm in (own or ())}, set()
        for _ in range(_MRO_MAX):
            if cur == target:
                out = subst
                break
            ci = self.classes.get(cur)
            if ci is None or cur in seen:
                break
            seen.add(cur)
            sigs, nxt = self.cs_base_sigs(cur), None
            for b in ci.facts.bases:
                head = b.args[0] if b.kind == "generic" and b.args else b
                v = self.eval_type(head, self.rel_of(ci, b), ci.facts.scope, depth=1, opaque=False,
                                   extra_shadow=ci.facts.type_params, ctx=self.outer_key(cur))
                bk = next(iter(v.keys)) if v.kind == "inst" and len(v.keys) == 1 else None
                if bk is not None and bk in self.classes and (bk == target or target in self.mro_class_keys(bk)):
                    nxt = (b, bk)
                    break
            if nxt is None:
                break
            b, bk = nxt
            s, order = sigs.get(id(b)), self.cs_tporder(bk)
            new: dict = {}
            if isinstance(s, tuple) and s[:1] == ("g",) and order is not None and len(s) - 2 == len(order):
                for name, arg in zip(order, s[2:]):
                    new[name] = self.cs_sig_norm(arg, self.rel_of(ci, b), self.outer_key(cur), subst, scope=cur)
            cur, subst = bk, new
        self.memo[mk] = out
        return out

    def cs_sig_norm(self, t, rel, ctx, subst, depth=0, scope=None):
        """A tagged signature type (the reader's sigt) written in rel (class ctx's context; `scope`: the class whose
        declaration holds it), normalised for comparison (_cs_tcmp): ("c", class key, simple name) a project type,
        ("b", keyword) a predefined one (System.Int32 is int, IntPtr nint), ("n", last segment, qualifier parts or
        None, the scope's lookup order when written unqualified: cs_scope_levels) any other named type (an alias of
        the file or a `global using` alias of its .csproj group read as its target, a namespace alias expanded),
        ("g", head, *args), ("?", inner, value type?) a nullable VALUE type (`System.Nullable<X>` too) or one whose
        kind is unknown (a reference type's `?` is an annotation only: dropped, and so is a class type parameter's
        `T?` unless the parameter is constrained `struct` / `unmanaged`, C# 9+), ("[]", element, rank), ("mtp", i),
        ("tpv", class, name) a class type parameter (`subst`: cs_subst), ("x", text) or _CS_UNK (a class type
        parameter `subst` does not fix)."""
        if not isinstance(t, tuple) or not t or depth > _EVAL_DEPTH:
            return ("x", repr(t))
        tag = t[0]
        if tag == "tp":
            got = (subst or {}).get(t[1]) if len(t) > 1 else None
            return got if got is not None else _CS_UNK
        if tag == "b" and len(t) > 1:
            return ("b", _CS_SYSTEM_ALIAS.get(str(t[1]), str(t[1])))
        if tag == "n" and len(t) > 1 and isinstance(t[1], tuple) and t[1] and all(isinstance(p, str) for p in t[1]):
            parts = t[1]
            tr = t_name(parts[0]) if len(parts) == 1 else t_qual(*parts)
            v = self.eval_type(tr, rel, -1, depth=depth + 1, opaque=False, ctx=ctx)
            if v.kind == "inst" and len(v.keys) == 1:
                k = next(iter(v.keys))
                ck = self.classes.get(k)
                return ("c", k, str(getattr(ck.facts, "name", "") or "") if ck is not None else "")
            ff = self.facts.get(rel)
            imps = (ff.imports.get(parts[0]) or []) if ff is not None else []
            if imps:                               # the file's own alias, else a `global using` alias of the group
                targets = {str(getattr(i, "spec", "") or "") if getattr(i, "kind", "") == "named" else "" for i in imps}
            else:
                targets = self.cs_group_tables(rel)[1].get(parts[0], set())
            if len(targets) > 1:
                return ("x", ".".join(parts), "alias")         # two aliases of that name: unknown
            if targets:
                target = next(iter(targets))
                if not target or not all(p.isidentifier() for p in target.split(".")):
                    return ("x", ".".join(parts), target)      # an alias of a generic / unreadable target
                parts = tuple(target.split(".")) + tuple(parts[1:])
            seg, qual = parts[-1], (tuple(parts[:-1]) or None)
            if seg in _CS_SYSTEM_ALIAS and qual in (None, ("System",)):
                return ("b", _CS_SYSTEM_ALIAS[seg])
            return ("n", seg, qual, None if qual is not None else self.cs_scope_levels(rel, scope))
        if tag == "g" and len(t) > 1:
            out = ("g",) + tuple(self.cs_sig_norm(a, rel, ctx, subst, depth + 1, scope) for a in t[1:])
            h = out[1]
            if len(out) == 3 and h[:2] == ("n", "Nullable") and len(h) > 3 and (
                    h[2] == ("System",) or h[2] is None and _cs_unq_is("System", h[3])):
                return ("?", out[2], True)         # System.Nullable<X> is X? (X a value type)
            return out
        if tag == "?" and len(t) > 1:
            raw = t[1]
            inner = self.cs_sig_norm(raw, rel, ctx, subst, depth + 1, scope)
            is_tp = isinstance(raw, tuple) and len(raw) > 1 and raw[0] == "tp"
            tpk = self.cs_tp_kind(scope, raw[1]) if is_tp else None
            if tpk == "a":
                return inner                       # `T?` of a T not constrained `struct`: an annotation (C# 9+)
            if tpk == "s":
                return ("?", inner, True)          # `where T : struct`: Nullable<T>
            if inner[:1] == ("b",) and len(inner) > 1 and inner[1] not in _CS_VALUE_KW:
                return inner                       # `string?`, `object?`: the same type
            if inner[:1] == ("c",) and self.classes.get(inner[1]) is not None \
                    and self.classes[inner[1]].facts.kind not in ("struct", "enum"):
                return inner                       # a project class's `?`: an annotation
            if inner[:1] == ("[]",):
                return inner                       # an array is a reference type: its `?` is an annotation
            known = not is_tp and (
                (inner[:1] == ("b",) and len(inner) > 1 and inner[1] in _CS_VALUE_KW) or (
                    inner[:1] == ("c",) and self.classes.get(inner[1]) is not None))
            return ("?", inner, bool(known))       # a type parameter's constraint unknown: `T?` is T or Nullable<T>
        if tag == "[]" and len(t) > 2:
            return ("[]", self.cs_sig_norm(t[1], rel, ctx, subst, depth + 1, scope), t[2])
        if tag == "mtp":
            return t
        return ("x",) + tuple(str(x) for x in t[1:])

    def cs_tp_kind(self, key, name) -> "str | None":
        """C#: what type parameter `name` of class key (or of an outer class) is constrained to (_cs tpkind): "s"
        (`struct` / `unmanaged`: `T?` is Nullable<T>), "a" (anything else, or nothing: `T?` an annotation) or None
        (unknown: a constraint naming a type parameter, a reader without the table, no class declaring it)."""
        seen = 0
        while key is not None and key in self.classes and seen < _MRO_MAX:
            seen += 1
            order = self.cs_tporder(key)
            if order is None:
                return None
            if name in order:
                kinds = set()
                for rel, idx in self.class_parts(key):
                    tb = self.cs_table(rel)
                    tk = tb.get("tpkind") if tb is not None else None
                    got = tk.get(idx) if isinstance(tk, dict) else None
                    if not isinstance(got, dict) or got.get(name) not in ("s", "a", "?"):
                        return None
                    kinds.add(got[name])
                return "s" if "s" in kinds else (None if "?" in kinds or not kinds else "a")
            key = self.outer_key(key)
        return None

    def cs_group_of(self, rel) -> str:
        """The .csproj group of rel (the C# binder's group_of), or "" when its binder keeps none."""
        fn = getattr(self.binder_of(rel), "group_of", None)
        try:
            return str(fn(rel)) if callable(fn) else ""
        except Exception:  # noqa: BLE001
            return ""

    def cs_group_tables(self, rel) -> tuple:
        """(the `global using` namespaces, {alias: {targets}} of the `global using X = ...` aliases) of the C# files of
        rel's .csproj group: what every file of that project sees."""
        grp = self.cs_group_of(rel)
        mk = ("csgrp", grp)
        if mk not in self.memo:
            gu, ga = set(), {}
            for r in sorted(self.facts):
                tb = self.cs_table(r)
                if tb is None or self.cs_group_of(r) != grp:
                    continue
                for u in tb.get("usings") or ():
                    if isinstance(u, tuple) and len(u) >= 2 and u[1] and isinstance(u[0], str):
                        gu.add(u[0])
                for a in tb.get("galias") or ():
                    if isinstance(a, tuple) and len(a) == 2 and all(isinstance(x, str) for x in a):
                        ga.setdefault(a[0], set()).add(a[1])
            self.memo[mk] = (frozenset(gu), ga)
        return self.memo[mk]

    def cs_scope_levels(self, rel, key) -> "tuple | None":
        """C#: the lookup order of a simple type name written in class key's declaration in file rel (_cs cvis),
        nearest first: ("m", namespace) an enclosing namespace's members, ("u", namespaces) the usings of a namespace
        declaration enclosing it, the last ("u", ...) the compilation unit's usings with every `global using` of the
        .csproj group. MSBuild's implicit usings are not read. None when unknown (no part of class key in rel, two
        parts of it in rel under other usings, a reader without the table)."""
        mk = ("cslev", rel, key)
        if mk in self.memo:
            return self.memo[mk]
        tb = self.cs_table(rel)
        cv = tb.get("cvis") if tb is not None else None
        out = None
        if isinstance(cv, dict) and key is not None:
            got = {cv.get(i) for r, i in self.class_parts(key) if r == rel}
            frames = next(iter(got)) if len(got) == 1 else None
            if isinstance(frames, tuple) and frames and all(isinstance(f, tuple) and len(f) == 2 for f in frames):
                lev: list = []
                for k in range(len(frames) - 1, 0, -1):
                    ns, us = str(frames[k][0]), frames[k][1]
                    outer = str(frames[k - 1][0])
                    parts = ns.split(".")
                    lev.append(("m", ns))
                    if us:
                        lev.append(("u", frozenset(us)))
                    low = len(outer.split(".")) if outer else 0
                    lev.extend(("m", ".".join(parts[:j])) for j in range(len(parts) - 1, low, -1))
                lev.append(("m", ""))
                lev.append(("u", frozenset(frames[0][1]) | self.cs_group_tables(rel)[0]))
                out = tuple(lev)
        self.memo[mk] = out
        return out

    def cs_sig_of(self, ci, mf, m, s, subst):
        """((normalised type, by-ref?), ...) of definition mf of member m of class ci (its mparams `s`, its msig), the
        type parameters of its class read through `subst` (cs_subst); None when the reader kept no msig for it."""
        sig = self.cs_member(ci, mf, m, "msig")
        if not isinstance(sig, tuple) or not isinstance(s, tuple) or not s or len(sig) != len(s[0]):
            return None
        rel_d = self.rel_of(ci, mf)
        return tuple((self.cs_sig_norm(t, rel_d, ci.key, subst, scope=ci.key), p[1] in ("ref", "out", "in"))
                     for t, p in zip(sig, s[0]))

    def cs_same_sig(self, p, sig, mine, skip=0) -> bool:
        """Is a definition (raw parameter types p, normalised signature sig; its first `skip` parameters dropped: an
        extension's `this`) PROVEN one of the hit's own signatures (`mine`: [(raw, normalised)])? Normalised where both
        sides have one (cs_sig_cmp: only True counts; a signature the pass cannot tell stays a rival), else by the raw
        TypeRefs (a reader without msig: the rule as before)."""
        for q, qs in mine:
            if sig is not None and qs is not None:
                if self.cs_sig_cmp(sig[skip:], qs) is True:
                    return True
            elif p[skip:] == q:
                return True
        return False

    @staticmethod
    def cs_sig_cmp(a, b) -> "bool | None":
        """Two normalised signatures (cs_sig_of: ((type, by-ref?), ...)): True one signature, False two, None unknown
        (a missing side, a parameter type _cs_tcmp cannot tell)."""
        if a is None or b is None:
            return None
        if len(a) != len(b):
            return False
        got = [False if x[1] != y[1] else _cs_tcmp(x[0], y[0]) for x, y in zip(a, b)]
        return False if False in got else (None if None in got else True)

    def cs_def_sig(self, ci, mf, m, frame):
        """Definition mf of member m of class ci, normalised as class `frame` sees it (ci's type parameters as frame's
        base lists fix them, frame's own as identity tokens: cs_subst), or None when unknown (no mparams / msig, no
        base-list path from frame to ci)."""
        s = self.cs_member(ci, mf, m, "mparams")
        if self._cs_sig_params(s) is None:
            return None
        sub = self.cs_subst(frame, ci.key)
        return None if sub is None else self.cs_sig_of(ci, mf, m, s, sub)

    def cs_rival_kind(self, rel, key, m, look, argc, on_class, allow_ext) -> str:
        """LanguageSpec.lookup_rules overloads (C#): "none" | "rival" | "noinfo" — does member m, which lookup on class
        key bound to look's hit, have a RIVAL the compiler may bind instead: a definition of m of another signature,
        taking argc arguments, on a project class after the hit on key's chain (C# keeps every base overload a
        derived class does not hide or override), or — for an instance receiver (allow_ext) — a project extension
        method m whose `this` type is on that chain or cannot be placed (a generic `this T`, `object`), of another
        signature? Signatures are compared normalised (cs_sig_norm, _cs_tcmp; only a PROVEN one signature counts as
        the hit's own): a base class's type parameters read as the receiver's base lists fix them (`override
        Handle(CreateOrder r, CancellationToken ct)` has the signature of `BaseHandler<TReq, TRes>.Handle(TReq r,
        CancellationToken ct)` under `: BaseHandler<CreateOrder, int>`, so the definition it overrides is no rival;
        one the path leaves open is never the same type), a project type by its class, `System.Int32` as `int`, an
        alias as its target, an external name by its scope's usings (`System.Threading.CancellationToken` is
        `CancellationToken` under `using System.Threading;`). An external base's overloads are not read. "noinfo":
        the reader keeps no _cs table (the rule does not apply); any other language: "none"."""
        spec = self.spec_of(rel)
        if spec is None or "overloads" not in spec.lookup_rules or look.kind != "hit" or look.is_class \
                or look.owner is None or self.cs_table(rel) is None:
            return "none"
        mk = ("ovr", rel, key, m, look.owner.key, argc, bool(on_class), bool(allow_ext))
        if mk not in self.memo:
            self.memo[mk] = self._cs_rival_kind(rel, key, m, look.owner, argc, on_class, allow_ext)
        return self.memo[mk]

    def _cs_rival_kind(self, rel, key, m, X, argc, on_class, allow_ext) -> str:
        mine = []                                  # [(raw parameter types, normalised signature or None)]: the hit's
        for mf in self.member_defs(X, m, on_class):    # own, its class's type parameters as the receiver's chain
            s = self.cs_member(X, mf, m, "mparams")    # fixes them (cs_subst)
            if s is _NOINFO:
                return "noinfo"
            p = self._cs_sig_params(s)
            if p is None:
                return "rival"                     # the hit's own signature is unknown: nothing can be compared
            mine.append((p, self.cs_sig_of(X, mf, m, s, self.cs_subst(key, X.key) or {})))
        levels = self.mro(key)
        entries = [e for lv in (levels or ()) for e in lv]
        if ("class", X.key) not in entries:
            return "rival"
        iface_recv = key in self.classes and self.classes[key].facts.kind == "interface"
        for e in entries[entries.index(("class", X.key)) + 1:]:
            if e[0] != "class":
                break                              # an external base's overloads are not read (a known gap)
            c = self.classes.get(e[1])
            if c is None or c.ambiguous:
                return "rival"
            if c.facts.kind == "interface" and not iface_recv:
                continue                           # an interface member is no candidate of a class's member lookup
            subst = None
            for mf in self.member_defs(c, m, on_class):
                s = self.cs_member(c, mf, m, "mparams")
                p = self._cs_sig_params(s)
                if p is None:
                    return "rival"
                if subst is None:                  # c's type parameters as the receiver's base lists fix them
                    subst = self.cs_subst(key, c.key) or {}
                q = self.cs_sig_of(c, mf, m, s, subst)
                if not self._cs_sig_takes(s[0], s[1], argc) or self.cs_same_sig(p, q, mine):
                    continue                       # another arity, or the hit's own signature (hidden / overridden:
                return "rival"                     # `override Handle(CreateOrder r)` of `Handle(TReq r)`)
        if not allow_ext:
            return "none"
        fn = getattr(self.binder_of(rel), "extensions", None)
        try:
            cands = list(fn(rel, m) or ()) if callable(fn) else []
        except Exception:  # noqa: BLE001
            return "rival"
        chain = self.mro_class_keys(key) | {key}
        for item in cands:
            try:
                crel, cidx, tref = _norm_rel(item[0]), int(item[1]), item[2]
            except (TypeError, ValueError, IndexError):
                return "rival"
            sk = self.class_key.get((crel, cidx))
            tv = self.eval_type(tref, crel, -1, ctx=sk) if _ok_typeref(tref) else _UNK
            if tv.kind == "ext" or (tv.kind == "inst" and len(tv.keys) == 1 and next(iter(tv.keys)) not in chain):
                continue                           # an extension of a builtin / external type, or off the chain
            sci = self.classes.get(sk) if sk is not None else None
            if sci is None or sci.ambiguous:
                return "rival"
            for mf in (mf for mf in sci.facts.methods.get(m) or () if "static" in mf.flags):
                s = self.cs_member(sci, mf, m, "mparams")
                p = self._cs_sig_params(s)
                if p is None or not p:
                    return "rival"
                if not self._cs_sig_takes(s[0][1:], s[1], argc) or \
                        self.cs_same_sig(p, self.cs_sig_of(sci, mf, m, s, {}), mine, skip=1):
                    continue
                return "rival"
        return "none"

    def cs_args_fit(self, rel, si, idx, X, m, argc, on_class) -> bool:
        """Are the site's argument types KNOWN to convert to the declared parameter types of one of class X's
        definitions of m (its overloads are the one node the hit names)?"""
        t = self.cs_table(rel)
        try:
            args = t["args"][si][idx]
        except (TypeError, KeyError, IndexError):
            return False
        if not isinstance(args, tuple) or (argc >= 0 and len(args) != argc):
            return False
        descs = [self.cs_arg_type(rel, si, a) for a in args]
        for mf in self.member_defs(X, m, on_class):
            s = self.cs_member(X, mf, m, "mparams")
            if self._cs_sig_params(s) is None:
                continue
            params, _variadic = s
            if len(args) > len(params) or any(not p[2] for p in params[len(args):]):
                continue                           # a `params` array's extra arguments are not matched
            if all(p[1] not in ("ref", "out", "this")
                   and self.cs_fits(a, self.cs_param_type(X, self.rel_of(X, mf), p[0]))
                   for a, p in zip(descs, params)):
                return True
        return False

    def cs_overloads(self, rel, si, idx, site, key, look, on_class, allow_ext) -> str:
        """LanguageSpec.lookup_rules overloads (C#) for one site whose lookup on class key hit `look`: "ok" (no rival:
        the hit stands as before), "weak" (a rival exists and the argument types are known to fit the hit: typed,
        never EXTRACTED) or "unknown" (a rival, arguments not known to fit: the compiler may bind the rival)."""
        kind = self.cs_rival_kind(rel, key, site.member, look, site.argc, on_class, allow_ext)
        if kind != "rival":
            return "ok"
        spec = self.spec_of(rel)
        st = self.stats["by_lang"].setdefault(spec.name, _lang_stats()).setdefault(
            "overloads", {"sites": 0, "fit": 0, "unknown": 0})
        st["sites"] += 1
        if idx >= 0 and not self.cs_override_shadowed(key, site.member, look.owner, site.argc, on_class) \
                and self.cs_args_fit(rel, si, idx, look.owner, site.member, site.argc, on_class):
            st["fit"] += 1
            return "weak"
        st["unknown"] += 1
        return "unknown"

    def cs_override_shadowed(self, key, m, X, argc, on_class) -> bool:
        """May a call that member lookup on class key bound to class X's m bind ANOTHER class's definition although
        its arguments fit X's? C# leaves an `override` out of the candidates (the virtual it overrides stands in for
        it, declared higher up) and then drops every candidate of a less derived class than one with an applicable
        candidate: when every definition of m in X is an override, a definition of m of another signature in a
        class strictly between X and the class declaring that virtual, taking argc arguments, may be the one bound
        (Der7 : Mid7 : Base7, `override M7(int)` / `M7(double)` / `virtual M7(int)`: `_d.M7(5)` binds Mid7.M7)."""
        mine, own = [], self.cs_subst(key, X.key) or {}
        for mf in self.member_defs(X, m, on_class):
            mods = self.cs_member(X, mf, m, "mods")
            if not isinstance(mods, frozenset) or "override" not in mods:
                return False
            s = self.cs_member(X, mf, m, "mparams")
            p = self._cs_sig_params(s)
            if p is None:
                return True
            mine.append((p, self.cs_sig_of(X, mf, m, s, own)))
        if not mine:
            return False
        entries = [e for lv in (self.mro(key) or ()) for e in lv]
        if ("class", X.key) not in entries:
            return True
        for e in entries[entries.index(("class", X.key)) + 1:]:
            c = self.classes.get(e[1]) if e[0] == "class" else None
            if c is None or c.ambiguous:
                return True                        # an external / unread base: its overloads are not read
            if c.facts.kind == "interface":
                continue
            subst, declarer, rival = self.cs_subst(key, c.key) or {}, False, False
            for mf in self.member_defs(c, m, on_class):
                mods = self.cs_member(c, mf, m, "mods")
                s = self.cs_member(c, mf, m, "mparams")
                p = self._cs_sig_params(s)
                if not isinstance(mods, frozenset) or p is None:
                    return True
                if self.cs_same_sig(p, self.cs_sig_of(c, mf, m, s, subst), mine):
                    declarer = declarer or "override" not in mods
                elif self._cs_sig_takes(s[0], s[1], argc):
                    rival = True
            if declarer:
                return False                       # the slot's declarer: its own overloads are the rule as before
            if rival:
                return True                        # a more derived candidate than the slot's declarer
        return False

    # ---------------------------------------------------------------- expressions
    def eval_recv(self, rel, si, r, depth) -> _V:
        if r is None:
            return _UNK
        if depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        k = r.kind
        if k == "SELF":
            ff = self.facts.get(rel)
            if ff is None or not 0 <= si < len(ff.scopes):
                return _UNK
            key = self.scope_class_key(rel, si)
            sk = ff.scopes[si].self_kind
            if key is None or key not in self.classes or self.classes[key].ambiguous or sk not in ("inst", "class"):
                return _UNK
            # decl: `self` / `cls` may be (the class object of) any project subclass at run time
            return _V("inst" if sk == "inst" else "cls", {key}, ev={self.classes[key].rel}, src="self", decl=True)
        if k == "IDENT":
            return self.name_value(rel, si, r.name, depth + 1)
        if k == "FIELD":
            return self.member_value(self.eval_recv(rel, si, r.inner, depth + 1), r.name, depth + 1)
        if k == "CALL":
            return self.call_result(rel, si, r, depth + 1)
        if k == "NEW":
            v = self.eval_type(r.type, rel, si, depth=depth + 1)
            return v.but(src="ctor") if v.kind in ("inst", "ext") else _UNK
        if k == "CLASSREF":
            v = self.eval_type(r.type, rel, si, depth=depth + 1, opaque=False)
            if v.kind == "inst" and len(v.keys) == 1:
                return _V("cls", v.keys, ev=v.ev, src="class")
            return v if v.kind == "ext" else _UNK
        if k == "MODULE":
            ff = self.facts.get(rel)
            imps = (ff.imports.get(r.name) if ff is not None else None) or []
            vals = [self.import_value(rel, i, depth + 1) for i in imps]
            if not vals or any(not v.same(vals[0]) for v in vals) or vals[0].kind not in ("module", "ns", "ext") \
                    or (vals[0].kind == "ext" and vals[0].ref != "module"):
                return _UNK
            return vals[0]
        if k == "LITERAL":
            return _V("ext", ref="literal", src="literal")
        return _UNK                                    # SUPER outside a direct call, OTHER

    def call_result(self, rel, si, r, depth) -> _V:
        """The value of CALL r: a constructor's instance, or a return annotation (R4: weak + ret + decl)."""
        if depth > _EVAL_DEPTH:
            self.cut += 1
            return _UNK
        m = r.name
        if r.inner is None:
            v = self.name_value(rel, si, m, depth + 1)
            if v.kind == "cls":
                return _V("inst", v.keys, ev=v.ev, src="ctor")
            if v.kind == "func":
                frel, ff = v.ref
                if ff.returns is None:
                    return _UNK
                t = self.eval_type(ff.returns, frel, -1, depth=depth + 1)
                return t.but(ev=t.ev | v.ev, weak=True, ret=True, src="return", decl=True) \
                    if t.kind in ("inst", "ext") else _UNK
            if v.kind == "ext" and v.ref == "builtin":
                return _V("ext", ref="builtin", src="ctor")
            return _UNK
        base = self.eval_recv(rel, si, r.inner, depth + 1)
        if base.kind == "module":
            mv = self.module_member(base.ref, m, depth + 1).plus_ev(base.ev)
            if mv.kind == "cls":
                return _V("inst", mv.keys, ev=mv.ev, src="ctor")
            if mv.kind == "func":
                frel, ff = mv.ref
                if ff.returns is None:
                    return _UNK
                t = self.eval_type(ff.returns, frel, -1, depth=depth + 1)
                return t.but(ev=t.ev | mv.ev, weak=True, ret=True, src="return", decl=True) \
                    if t.kind in ("inst", "ext") else _UNK
            return _UNK
        if base.kind not in ("inst", "cls"):
            return _UNK
        vals = []
        for key in sorted(base.keys):
            look = self.lookup(key, m, on_class=base.kind == "cls")
            if look.kind != "hit" or len(look.targets) != 1:
                return _UNK
            if look.is_class:
                vals.append(_V("inst", look.targets, ev=look.ev, src="ctor"))
                continue
            mf = look.mfs[0]
            if mf is None or mf.returns is None:
                return _UNK
            if self.cs_rival_kind(rel, key, m, look, -1, base.kind == "cls", base.kind == "inst") == "rival":
                return _UNK                        # C#: which overload runs (so its return type) is not known here
            t = self.eval_type(mf.returns, self.rel_of(look.owner, mf), look.owner.facts.scope, self_key=key,
                               depth=depth + 1, extra_shadow=look.owner.facts.type_params, ctx=look.owner.key)
            if t.kind not in ("inst", "ext"):
                return _UNK
            vals.append(t.but(ev=t.ev | look.ev, weak=True, ret=True, src="return", decl=True))
        v = _join(vals)
        return v.plus_ev(base.ev) if v.kind in ("inst", "ext") else _UNK

    # ---------------------------------------------------------------- site verdicts
    def site_verdict(self, rel, si, idx, site) -> _Verdict:
        mk = ("sv", rel, si, idx)
        if mk not in self.memo:
            self.memo[mk] = self._site_verdict(rel, si, site, idx)
        return self.memo[mk]

    def _target_verdict(self, looks, v, rel, shape_hint, recv_kind, recv_cls="", line=0, dispatch="",
                        on_class=False) -> _Verdict:
        spec = self.spec_of(rel)
        if all(lk.kind == "hit" for lk in looks):
            targets = sorted({t for lk in looks for t in lk.targets})
            if len(targets) > MAX_TARGETS:
                return _Verdict("unknown", why="too many", recv_kind=recv_kind, recv_cls=recv_cls)
            shape = "C" if any(lk.is_class for lk in looks) else shape_hint
            ev = frozenset().union(v.ev, *(lk.ev for lk in looks))
            if any(lk.is_class for lk in looks):
                dispatch = ""                      # a nested class constructed: that class exactly
            return _Verdict("typed", tuple(targets), ev, v.weak, shape, recv_kind=recv_kind, recv_cls=recv_cls,
                            recv_keys=frozenset(v.keys) if dispatch else frozenset(), dispatch=dispatch,
                            on_class=bool(on_class))
        if all(lk.kind == "absent" for lk in looks) and spec is not None and spec.absent_ok:
            if self.unchecked(rel, line, spec):
                return _Verdict("unknown", why="unchecked", shape=shape_hint, recv_kind=recv_kind,
                                recv_cls=recv_cls)
            return _Verdict("absent", shape=shape_hint, recv_kind=recv_kind, recv_cls=recv_cls)
        why = next((lk.why for lk in looks if lk.kind == "unknown"), "absent")
        return _Verdict("unknown", why=why, shape=shape_hint, recv_kind=recv_kind, recv_cls=recv_cls)

    def _site_verdict(self, rel, si, site, idx=-1) -> _Verdict:
        spec = self.spec_of(rel)
        m = site.member
        if not m:
            return _V_UNKNOWN
        if spec is not None and spec.dunders and len(m) > 4 and m.startswith("__") and m.endswith("__"):
            return _Verdict("unknown", why="dunder", recv_kind=site.recv.kind if site.recv else "")
        if site.kind == "ctor":
            r = site.recv
            if r is None or r.type is None:
                return _V_UNKNOWN
            v = self.eval_type(r.type, rel, si, opaque=False)
            if v.kind == "inst" and len(v.keys) == 1:
                return _Verdict("typed", tuple(sorted(v.keys)), v.ev, v.weak, "C", recv_kind="CLASSREF")
            if v.kind == "ext":
                return _Verdict("external", shape="C", recv_kind="CLASSREF")
            return _Verdict("unknown", why="ctor", shape="C", recv_kind="CLASSREF")
        if site.kind == "bare":
            v = self.name_value(rel, si, m, 0)
            if v.kind == "cls" and spec is not None and "member_scope" in spec.lookup_rules:
                v = _UNK                               # C#: a bare `T()` is never a constructor (only `new T()` is)
            if v.kind == "cls" and len(v.keys) == 1:
                return _Verdict("typed", tuple(sorted(v.keys)), v.ev, v.weak, "C", recv_kind="")
            if v.kind == "ext" and v.ref in _EXT_BARE_OK:
                return _Verdict("external", shape="M", recv_kind="")
            ff = self.facts.get(rel)
            if (spec is not None and spec.implicit_this and ff is not None and 0 <= si < len(ff.scopes)
                    and v.kind == "unk" and self._unbound_everywhere(rel, si, m)):
                key = self.scope_class_key(rel, si)
                sk = ff.scopes[si].self_kind
                if key is not None and sk in ("inst", "class"):
                    look = self.lookup(key, m, argc=site.argc)
                    base = _V("inst", {key}, ev={self.classes[key].rel})
                    alt = self.static_import_verdict(rel, site, look)
                    if alt is not None:
                        return alt
                    ov = self.cs_overloads(rel, si, idx, site, key, look, sk == "class", False)
                    if ov == "unknown":
                        return _Verdict("unknown", why="overload", shape="S", recv_kind="SELF")
                    return self._target_verdict([look], base.but(weak=ov == "weak"), rel, "S", "SELF",
                                                line=site.line, dispatch="sub", on_class=sk == "class")
            return _Verdict("unknown", why="bare", shape="M", recv_kind="")
        r = site.recv
        if r is None:
            return _V_UNKNOWN
        if r.kind == "SUPER":
            key = self.scope_class_key(rel, si)
            if key is None or key not in self.classes:
                return _Verdict("unknown", why="super", shape="S", recv_kind="SUPER")
            spec_k = self.classes[key].spec
            ff_s = self.facts.get(rel)
            static_ctx = (spec_k is not None and spec_k.static_split and ff_s is not None
                          and 0 <= si < len(ff_s.scopes) and ff_s.scopes[si].self_kind == "class")
            look = self.lookup(key, m, skip=1, argc=site.argc, on_class=static_ctx)
            ov = self.cs_overloads(rel, si, idx, site, key, look, static_ctx, False)
            if ov == "unknown":
                return _Verdict("unknown", why="overload", shape="S", recv_kind="SUPER")
            return self._target_verdict([look], _V("inst", {key}, ev={self.classes[key].rel}, weak=ov == "weak"),
                                        rel, "S", "SUPER", line=site.line, dispatch="super", on_class=static_ctx)
        v = self.eval_recv(rel, si, r, 0)
        shape = self._shape(r, v)
        if v.kind == "inst":
            keys = sorted(v.keys)
            looks = [self.lookup(k, m, argc=site.argc) for k in keys]
            alt = self.extension_verdict(rel, site, v, looks, shape, r.kind)
            if alt is not None:
                return alt
            ovs = [self.cs_overloads(rel, si, idx, site, k, lk, False, True) for k, lk in zip(keys, looks)]
            if "unknown" in ovs:
                return _Verdict("unknown", why="overload", shape=shape, recv_kind=r.kind)
            return self._target_verdict(looks, v.but(weak=True) if "weak" in ovs else v, rel, shape, r.kind,
                                        line=site.line, dispatch="sub" if v.decl else "")
        if v.kind == "cls":
            key = next(iter(v.keys))
            look = self.lookup(key, m, argc=site.argc, on_class=True)
            ov = self.cs_overloads(rel, si, idx, site, key, look, True, False)
            if ov == "unknown":
                return _Verdict("unknown", why="overload", shape=shape, recv_kind=r.kind, recv_cls=key)
            return self._target_verdict([look], v.but(weak=True) if ov == "weak" else v, rel, shape, r.kind,
                                        recv_cls=key, line=site.line, dispatch="sub" if v.decl else "", on_class=True)
        if v.kind == "module":
            mv = self.module_member(v.ref, m, 0).plus_ev(v.ev)
            if mv.kind == "cls" and len(mv.keys) == 1:
                return _Verdict("typed", tuple(sorted(mv.keys)), mv.ev, mv.weak, "C", recv_kind=r.kind)
            if mv.kind == "func":
                frel, ff = mv.ref
                k = self.node_at(frel, (ff.line,) + tuple(ff.lines), ff.name, TARGET_LABELS)
                if k is None:
                    cands = [x for x in self.by_file_seg.get((frel, ff.name), ())
                             if self.label_of.get(x) == "Function"]
                    k = cands[0] if len(cands) == 1 else None
                if k is not None:
                    return _Verdict("typed", (k,), mv.ev | {frel}, mv.weak, "M", recv_kind=r.kind)
                return _Verdict("unknown", why="no node", shape="M", recv_kind=r.kind)
            if mv.kind == "ext" and mv.ref in _EXT_BARE_OK:
                return _Verdict("external", shape="M", recv_kind=r.kind)
            return _Verdict("unknown", why="module member", shape="M", recv_kind=r.kind)
        if v.kind == "ext" and v.ref in _EXT_MEMBER_OK and self.loose(rel, m):
            return _Verdict("unknown", why="patched", shape=shape, recv_kind=r.kind)   # PATCH_RULES loose
        if v.kind == "ext" and v.ref in _EXT_MEMBER_OK:
            if v.decl and v.ref in ("type", "builtin") and spec is not None and self.open_member(rel, m):
                # a DECLARED external type whose member m a project subclass of an external / unresolved base
                # defines: the value may be that subclass (Liskov; a checked TypeScript annotation admits a
                # subclass instance too) -> unknown (dynamic dispatch may reach the project's override)
                return _Verdict("unknown", why="subclass", shape=shape, recv_kind=r.kind)
            return _Verdict("external", shape=shape, recv_kind=r.kind)
        return _Verdict("unknown", why="receiver", shape=shape, recv_kind=r.kind)

    def _unbound_everywhere(self, rel, si, m) -> bool:
        ff = self.facts.get(rel)
        s = si
        while 0 <= s < len(ff.scopes):
            sc = ff.scopes[s]
            if m in sc.poisoned or m in sc.params or m in sc.locals:
                return False
            s = sc.parent
        return self.file_symbol(rel, m).kind == "unbound"

    def _lookup_stat(self, rel, name):
        spec = self.spec_of(rel)
        if spec is not None:
            st = self.stats["by_lang"].setdefault(spec.name, _lang_stats()).setdefault("lookup", {})
            st[name] = st.get(name, 0) + 1

    def static_import_verdict(self, rel, site, look):
        """C# (LanguageSpec.lookup_rules using_static): a bare call the enclosing class chain does not hold (a
        fully-project chain without it: lookup why "open") is a static method of a `using static` type. Exactly one
        project method across the file's static imports -> typed, weak (INFERRED; shape M); several, or a static
        import that is not a project class (it may hold the method) -> unknown. None: the rule does not apply."""
        spec = self.spec_of(rel)
        if spec is None or "using_static" not in spec.lookup_rules or look.kind != "unknown" or look.why != "open":
            return None
        fn = getattr(self.binder_of(rel), "static_imports", None)
        try:
            imports = list(fn(rel) or ()) if callable(fn) else []
        except Exception:  # noqa: BLE001
            imports = [RES_UNKNOWN]
        if not imports:
            return None
        targets, ev = set(), set()
        for res in imports:
            v = self.res_value(rel, res, 1)
            if v.kind != "cls" or len(v.keys) != 1:
                return _Verdict("unknown", why="using static", shape="M", recv_kind="")
            lk = self.lookup(next(iter(v.keys)), site.member, argc=site.argc, on_class=True)
            if lk.kind == "hit" and not lk.is_class:
                targets.update(lk.targets)
                ev |= set(lk.ev) | set(v.ev)
            elif lk.kind == "unknown" and lk.why != "open":
                return _Verdict("unknown", why="using static", shape="M", recv_kind="")
        if len(targets) != 1:
            return _Verdict("unknown", why="using static", shape="M", recv_kind="") if targets else None
        self._lookup_stat(rel, "using_static")
        return _Verdict("typed", tuple(sorted(targets)), frozenset(ev), True, "M", recv_kind="")

    def extension_verdict(self, rel, site, v, looks, shape, recv_kind):
        """C# (LanguageSpec.lookup_rules extensions): a member the receiver's fully-project chain does not hold (an
        instance method always wins: this runs only after a miss, lookup why "open") is an extension method whose
        `this` type is a class of that chain. Exactly one such project method -> typed, weak (INFERRED); several, or
        an extension of that name on a type the pass cannot place (a generic `this T`, `object`, an unknown type:
        it may apply) -> unknown. None: the rule does not apply."""
        spec = self.spec_of(rel)
        if spec is None or "extensions" not in spec.lookup_rules or len(v.keys) != 1 or len(looks) != 1 \
                or looks[0].kind != "unknown" or looks[0].why != "open":
            return None
        fn = getattr(self.binder_of(rel), "extensions", None)
        try:
            cands = list(fn(rel, site.member) or ()) if callable(fn) else []
        except Exception:  # noqa: BLE001
            return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
        if not cands:
            return None
        key = next(iter(v.keys))
        chain = self.mro_class_keys(key) | {key}
        targets, ev = set(), set(v.ev)
        for item in cands:
            try:
                crel, cidx, tref = _norm_rel(item[0]), int(item[1]), item[2]
            except (TypeError, ValueError, IndexError):
                return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
            tv = self.eval_type(tref, crel, -1, ctx=self.class_key.get((crel, cidx))) if _ok_typeref(tref) else _UNK
            if tv.kind == "ext":
                continue                              # an extension of an external type: not on a project chain
            if tv.kind != "inst" or len(tv.keys) != 1:
                return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
            if next(iter(tv.keys)) not in chain:
                continue
            sk = self.class_key.get((crel, cidx))
            sci = self.classes.get(sk) if sk is not None else None
            if sci is None or sci.ambiguous:
                return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
            mfs = [mf for mf in sci.facts.methods.get(site.member) or () if "static" in mf.flags]
            keys, _by = self.method_nodes(sci, site.member, mfs)
            if not keys:
                return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
            targets.update(keys)
            ev |= set(tv.ev) | {sci.rel} | {self.file_of[k] for k in keys}
        if not targets:
            return None
        if len(targets) != 1:
            return _Verdict("unknown", why="extension", shape=shape, recv_kind=recv_kind)
        self._lookup_stat(rel, "extensions")
        return _Verdict("typed", tuple(sorted(targets)), frozenset(ev), True, shape, recv_kind=recv_kind)

    @staticmethod
    def _shape(r, v: _V) -> str:
        if r.kind in ("SELF", "SUPER"):
            return "S"
        if v.kind == "cls":
            return "K"
        if v.kind == "module":
            return "M"
        if r.kind == "IDENT":
            return "R1" if v.src == "param" else ("R4" if v.ret else "R2")
        if r.kind == "FIELD":
            return "R3"
        if r.kind == "CALL":
            return "R4"
        if r.kind == "NEW":
            return "C"
        if r.kind in ("CLASSREF",):
            return "K"
        if r.kind == "MODULE":
            return "M"
        return "R2"

    def unchecked(self, rel, line, spec) -> bool:
        """Is the compiler's member check switched off at `line` of rel (LanguageSpec.unchecked: a whole-file
        marker anywhere in the file, or a next-line marker on the site's line or the 3 lines before it)? Read
        conservatively: a marker in a string counts too (it only turns an `absent` into unknown)."""
        whole, near = spec.unchecked if spec is not None else ((), ())
        if not whole and not near:
            return False
        ft = self.file_text(rel)
        if ft is None:
            return True
        if whole:
            mk = ("nocheck", rel)
            if mk not in self.memo:
                self.memo[mk] = any(w in ln for ln in ft.lines for w in whole)
            if self.memo[mk]:
                return True
        lo, hi = max(1, line - 3), min(len(ft.lines), line)
        return any(w in ft.lines[k - 1] for k in range(lo, hi + 1) for w in near)

    def open_member(self, rel, m) -> bool:
        """Does a project class of rel's language family whose chain is OPEN (an external, unresolved or
        unlinearizable base: it may subclass an external class) define member m?"""
        fam_of = _sibling("edge_resolve").lang_family
        if "open_members" not in self.memo:
            out = set()
            for key in sorted(self.classes):
                ci = self.classes[key]
                levels = self.mro(key)
                if levels is not None and all(e[0] == "class" for lv in levels for e in lv):
                    continue
                fam = fam_of(ci.rel)
                out.update((fam, mm) for mm in ci.facts.methods)
            self.memo["open_members"] = out
        return (fam_of(rel), m) in self.memo["open_members"]

    # ---------------------------------------------------------------- textual veto
    def file_text(self, rel):
        """THE FileText of rel (lines, masked text, minified lines, declaration offsets), built once; None when
        the file is unreadable."""
        if rel not in self.texts:
            self.texts[rel] = None
            try:
                lines = self.source.lines(rel)
            except Exception:  # noqa: BLE001
                lines = None
            spec = self.spec_of(rel)
            ff = self.facts.get(rel)
            if lines is not None and spec is not None:
                self.texts[rel] = FileText(lines, spec.lexer, ff.decls if ff is not None else frozenset())
        return self.texts[rel]

    def text_count(self, rel, sc, m):
        """The call-shaped `m(` count over C's span: from the FIRST line its node may start on (a decorator, the
        declarator line of an arrow) to end_line. One scan per span (memoised in its FileText). None when
        unreadable or unjudgeable (a minified line in the span)."""
        ft = self.file_text(rel)
        start = _span_start(sc)
        if ft is None or sc.end_line < start or sc.end_line > len(ft.lines):
            return None
        return ft.count(start, sc.end_line, m)

    # ---------------------------------------------------------------- the merge
    def run(self):
        st = self.stats
        self.index_nodes()
        readers = self.load_readers()
        self.read_facts(readers)
        self.index_classes()
        by_caller: dict = {}
        by_seg: dict = {}                          # caller -> {last name segment of the callee: [tuple indexes]}
        for i, c in enumerate(self.calls):
            if isinstance(c, (tuple, list)) and len(c) >= 2 and isinstance(c[0], str):
                by_caller.setdefault(c[0], []).append(i)
                seg = _callee_seg(c[1], self.name_of)
                if seg:
                    by_seg.setdefault(c[0], {}).setdefault(seg, []).append(i)
        removed: set = set()
        emitted: list = []                         # (caller, target, shape) in emission order
        typed_attr: dict = {}                      # caller -> {target: grade}
        weak_attr: dict = {}                       # caller -> set(target)
        member_attr: dict = {}                     # caller -> set(names to drop from call_member)
        callers_typed: set = set()
        callers_audit: set = set()
        audit_ctx: list = [None, None]             # [the push's name index, Method count per (family, name)]
        lang_family = _sibling("edge_resolve").lang_family
        for rel in sorted(self.facts):
            lang = self.lang_of[rel]
            do_typed, do_audit = lang in self.cfg.typed, lang in self.cfg.audit
            if not (do_typed or do_audit):
                continue
            ff = self.facts[rel]
            ls = st["by_lang"].setdefault(lang, _lang_stats())
            kids: dict = {}
            for si, sc in enumerate(ff.scopes):
                kids.setdefault(sc.parent, []).append(si)
            caller_of = {si: self.node_at(rel, (sc.line,) + tuple(sc.lines), sc.name, CALLER_LABELS)
                         for si, sc in enumerate(ff.scopes)}
            dup = Counter(k for k in caller_of.values() if k is not None)
            shared: dict = {}                      # node key -> the scopes the engine merged into it
            for si in caller_of:                   # two scopes on one node: neither is trusted
                if caller_of[si] is not None and dup[caller_of[si]] > 1:
                    shared.setdefault(caller_of[si], []).append(si)
                    caller_of[si] = None
            for si, sc in enumerate(ff.scopes):
                for idx, site in enumerate(sc.sites):
                    v = self.site_verdict(rel, si, idx, site)
                    st["sites"][v.kind] += 1
                    ls["sites"] += 1
                    ls[v.kind] += 1
                    if v.shape in st["by_shape"]:
                        st["by_shape"][v.shape]["sites"] += 1
                    if v.why == "too many":
                        st["too_many"] += 1
            fam_c = lang_family(rel)
            try:
                test_c = bool(self.is_test(rel)) if self.is_test is not None else True
            except Exception:  # noqa: BLE001 — an unreadable rule: corrections only
                test_c = True
            additions_ok = (not test_c or self.cfg.tests) and (not is_nonprod_path(rel) or self.cfg.nonprod)
            spec = self.spec_of(rel)
            header = self.header_sites(ff, spec)
            for si, sc in enumerate(ff.scopes):
                ck = caller_of.get(si)
                if ck is None:
                    continue
                if do_typed:
                    callers_typed.add(ck)
                if do_audit:
                    callers_audit.add(ck)
                sites_by = self.span_sites(rel, ff, si, kids, spec, header)
                mine = by_caller.get(ck, [])
                if do_typed:
                    self._merge_caller(rel, sc, ck, sites_by, by_seg.get(ck, {}), removed, emitted, typed_attr,
                                       member_attr,
                                       additions_ok, fam_c, lang_family)
                if do_audit:
                    self._audit_caller(rel, ck, sites_by, mine, removed, weak_attr, fam_c, audit_ctx, lang_family)
            if do_audit:                           # a node the engine merged from two scopes (a property getter /
                for ck in sorted(shared):          # setter pair, if / else redefinitions): never TYPED (one scope's
                    union: dict = {}               # REPLACE could remove the other's tuples), but the AUDIT — it
                    seen: set = set()              # only lowers grades — judges it over the UNION of their sites
                    for si in shared[ck]:
                        for m, group in self.span_sites(rel, ff, si, kids, spec, header).items():
                            for item in group:
                                if item[:2] not in seen:
                                    seen.add(item[:2])
                                    union.setdefault(m, []).append(item)
                    callers_audit.add(ck)
                    st["audit_shared"] += 1
                    self._audit_caller(rel, ck, union, by_caller.get(ck, []), removed, weak_attr, fam_c, audit_ctx,
                                       lang_family)
        return self._finish(removed, emitted, typed_attr, weak_attr, member_attr, callers_typed, callers_audit)

    def span_sites(self, rel, ff, si, kids, spec, header) -> dict:
        """{member: [(scope index, site index, Site, attribution)]} over the FULL span of caller scope si: its own
        sites and every nested scope's, each with its attribution relative to si (ATTRIBUTION: attributed |
        detached | uncertain)."""
        sc = ff.scopes[si]
        status, todo = {si: "own"}, [si]          # the attribution of every scope of C's span (ATTRIBUTION)
        while todo:
            s = todo.pop()
            for c in kids.get(s, ()):
                status[c] = self._nested(status[s], ff.scopes[c].kind, spec)
                todo.append(c)
        walked = self.symbol_walked(ff, sc, spec)
        sites_by: dict = {}
        for s in sorted(status):
            att = status[s]
            for idx, site in enumerate(ff.scopes[s].sites):
                a = "detached" if att in _ATTRIBUTED and (s, idx) in header else att
                if a == "detached" and walked and site.kind == "bare" and \
                        self.symbol_bound(rel, ff, site.member, spec):
                    a = "uncertain"                # graphify's symbol pass may give this call to C (ATTRIBUTION)
                sites_by.setdefault(site.member, []).append((s, idx, site, a))
        return sites_by

    def _nested(self, parent: str, kind, spec) -> str:
        """The attribution (relative to the caller) of a nested scope of `kind` whose parent scope's is
        `parent`: attributed | detached | uncertain (module docstring, ATTRIBUTION)."""
        if self.engine == "ast":
            return "attributed"                    # sync._Extractor: ast.walk over the whole def
        if parent == "detached" or (spec is not None and kind in spec.nested_detached):
            return "detached"                      # graphify's walk stopped at a boundary on the way down
        if parent == "uncertain" or spec is None or kind not in spec.nested_attributed:
            return "uncertain"
        return "attributed"

    def symbol_walked(self, ff, sc, spec) -> bool:
        """Does graphify's symbol-resolution post-pass walk caller sc's WHOLE body (LanguageSpec.symbol_walk: a
        top-level scope of such a kind; with symbol_plain, never one whose FunctionFacts lists decorator lines)?
        Read conservatively: a scope without a FunctionFacts entry on its line counts as walked."""
        if self.engine == "ast" or spec is None or sc.parent != -1 or sc.kind not in spec.symbol_walk:
            return False
        if spec.symbol_plain:
            defs = [f for f in ff.functions.get(sc.name, ()) if _int(getattr(f, "line", 0)) == sc.line]
            if defs and all(getattr(f, "lines", ()) for f in defs):
                return False                       # decorated: a decorated_definition, never walked
        return True

    def symbol_bound(self, rel, ff, m, spec) -> bool:
        """Can graphify's symbol pass resolve a bare call of m in rel (LanguageSpec.symbol_imports_only: m is bound
        by a named / default import whose resolution is not EXTERNAL — a project file, or unknown; otherwise any
        bare call: JS / TS also resolve lexical aliases of imports)?"""
        if not spec.symbol_imports_only:
            return True
        mk = ("symb", rel, m)
        if mk in self.memo:
            return self.memo[mk]
        c0 = self.cut
        out = any(getattr(imp, "kind", "") in ("named", "default") and self.import_value(rel, imp, 0).kind != "ext"
                  for imp in ff.imports.get(m) or ())
        if self.cut == c0:
            self.memo[mk] = out
        return out

    def header_sites(self, ff, spec) -> frozenset:
        """{(scope index, site index)}: the sites a reader recorded in a scope on the lines of one of its
        DETACHED child scopes — that child's header (a Python default argument), inside graphify's
        function_definition boundary, so attributed to no caller (LanguageSpec.header_detached)."""
        if self.engine == "ast" or spec is None or not spec.header_detached:
            return frozenset()
        ranges: dict = {}
        for c in ff.scopes:
            if c.parent >= 0 and c.kind in spec.nested_detached:
                ranges.setdefault(c.parent, []).append((c.line, c.end_line))
        out = set()
        for p, rs in ranges.items():
            for idx, site in enumerate(ff.scopes[p].sites):
                if any(a <= site.line <= b for a, b in rs):
                    out.add((p, idx))
        return frozenset(out)

    def _merge_caller(self, rel, sc, ck, sites_by, segs, removed, emitted, typed_attr, member_attr, additions_ok,
                      fam_c, lang_family):
        """Design 2.5 + 2.6 for one caller: REPLACE / ADD per bare name (judged over the FULL span), then the
        emission of the ATTRIBUTED sites' typed targets under the caller policy."""
        st = self.stats
        span = sc.end_line - _span_start(sc) + 1
        g5 = self.node_by_key[ck].get("call_member") or ()
        if not isinstance(g5, (list, tuple, set, frozenset)):
            g5 = ()
        spec_c = self.spec_of(rel)
        dispatch_ok = spec_c is not None and "iface_dispatch" in spec_c.lookup_rules
        virtual_ok = spec_c is not None and "virtual_dispatch" in spec_c.lookup_rules
        for m in sorted(sites_by):
            if not m:
                continue
            group = sites_by[m]
            iface_add: dict = {}                       # C# interface dispatch: target -> [grade, shape, line]
            virt_hold: list = []                       # C# virtual dispatch: a fan-out this group may not REPLACE
            verdicts = []
            for s, idx, site, att in group:
                vd = self.site_verdict(rel, s, idx, site)
                if vd.kind != "unknown" and vd.shape and vd.shape not in self.cfg.shapes:
                    vd = vd._replace(kind="unknown", why="shape off")
                if vd.kind == "typed" and fam_c is not None and any(
                        lang_family(self.file_of.get(t, "")) not in (None, fam_c) for t in vd.targets):
                    st["skipped_family"] += 1      # a target of another family is never emitted, so it may never
                    vd = vd._replace(kind="unknown", why="family")   # REPLACE the engine's tuple either
                if vd.kind == "typed" and any(self.interface_member(t) for t in vd.targets):
                    st["skipped_interface"] += 1   # a member of an interface never runs: no key, and it may never
                    vd = vd._replace(kind="unknown", why="interface")   # REPLACE the engine's tuples either
                if dispatch_ok and vd.why == "interface":
                    known = self.iface_dispatch(rel, m, site, att, self.site_verdict(rel, s, idx, site), iface_add,
                                                si=s)
                    if known is not None:          # C#: the whole dispatch set is known (the interface member + its
                        vd = vd._replace(kind="dispatch", targets=tuple(sorted(known)))   # implementers): no veto
                if dispatch_ok and vd.kind == "typed" and any(t in self.cs_static_iface for t in vd.targets):
                    self.cs_dispatch_stats(rel)["static_member"] += 1     # `IFactory.Make()`: an exact call
                if virtual_ok and vd.kind == "typed":
                    vd = self.virtual_dispatch(rel, m, site, att, vd, iface_add, virt_hold, si=s)
                verdicts.append((vd, att))
            if _SEG_SPLIT.search(m):               # never an identifier: the general rule, tuple by tuple
                prior = [i for ii in segs.values() for i in ii if i not in removed
                         and callee_matches(self.calls[i][1], m, self.name_of, _is_call_key)]
            else:                                   # == callee_matches for an identifier m (gate-locked)
                prior = [i for i in segs.get(m, ()) if i not in removed]
            if not prior and m not in g5:
                mode, why = "moot", ""             # nothing of m to replace (no tuple, no G5 flag): not judged —
            elif any(vd.kind == "unknown" for vd, _a in verdicts):   # REPLACE and ADD would emit the same
                mode, why = "add", "unknown_site"
            elif any(a == "uncertain" for _vd, a in verdicts):
                mode, why = "add", "nested"
            elif span > SPAN_MAX_LINES or span < 1:
                mode, why = "add", "span"
            elif sc.all_names is not None and sc.all_names.get(m, 0) != len(group):
                mode, why = "add", "census"
            elif self.text_count(rel, sc, m) != len(group):
                mode, why = "add", "text"
            elif not self.cfg.replace:
                mode, why = "add", "replace_off"
            else:
                mode, why = "replace", ""
            over = None
            if mode == "replace" and virt_hold:    # C# virtual dispatch whose overrides are unknowable or more than
                mode, why = "add", "dispatch"      # MAX_TARGETS: the engine's tuples stay (never a wider fan-out)
            if mode == "replace":                  # what dynamic dispatch reaches beyond the static targets
                over = self.override_reach(rel, m, verdicts, fam_c, lang_family)
                if over is None:
                    mode, why = "add", "dispatch"
            st["groups"][mode] += 1
            if why:
                st["vetoes"][why] += 1
            grades: dict = {}
            shapes: dict = {}
            singular: set = set()
            nested_only: set = set()
            for vd, att in verdicts:
                if vd.kind != "typed":
                    continue
                if att not in _ATTRIBUTED:         # the engine gives this call to no enclosing caller: C keeps
                    nested_only.update(vd.targets)     # it out of its emission (never a new attribution)
                    continue
                if len(vd.targets) == 1:
                    t = vd.targets[0]
                    same = not vd.weak and self.file_of.get(t) == rel and vd.ev <= {rel} \
                        and not self.via_interface(vd, t)
                    g = "EXTRACTED" if same else "INFERRED"
                    singular.add(t)
                else:
                    g = "AMBIGUOUS"
                    st["candidate_sets"] += 1
                for t in vd.targets:
                    if _GRADE_RANK[g] > _GRADE_RANK.get(grades.get(t), -1):
                        grades[t], shapes[t] = g, vd.shape
            st["skipped_nested"] += len(nested_only - set(grades))
            for t, (g, shp, _ln) in sorted(iface_add.items()):     # C# interface dispatch: never narrowed by the
                if _GRADE_RANK[g] > _GRADE_RANK.get(grades.get(t), -1):   # candidate rule (an implementer fan-out
                    grades[t], shapes[t] = g, shp      # of 2..MAX_TARGETS is this language's deliberate exception)
                singular.add(t)
            tline = {t: v[2] for t, v in iface_add.items()}       # the first call line of every typed target
            for (_s, _i, site_, att_), (vd_, _a) in zip(group, verdicts):
                if vd_.kind == "typed" and att_ in _ATTRIBUTED:
                    for t_ in vd_.targets:
                        tline[t_] = min(tline.get(t_, site_.line), site_.line)
            gline = min((site_.line for _s, _i, site_, att_ in group if att_ in _ATTRIBUTED), default=0)
            rekey: set = set()
            if mode == "replace":                  # (a C# interface site's targets: its whole known dispatch set)
                static = {t for vd, _a in verdicts if vd.kind in ("typed", "dispatch") for t in vd.targets}
                keep, rekey = self.override_split(ck, prior, over, static, m, fam_c, lang_family)
                removed.update(i for i in prior if i not in keep)
                st["override_kept"] += len(keep)
                names = self.node_by_key[ck].get("call_member") or ()
                if isinstance(names, (list, tuple, set, frozenset)) and m in names and not any(
                        not _is_call_key(self.calls[i][1]) for i in keep):   # a kept NAME still needs its G5 flag
                    member_attr.setdefault(ck, set()).add(m)
            had = bool(prior)
            reach = None                            # what the prior tuples can bind (a candidate never widens it)
            for t in sorted(grades):
                if t == ck:
                    st["skipped_self"] += 1
                    continue
                if t not in self.node_by_key:
                    continue
                fam_t = lang_family(self.file_of.get(t, ""))
                if fam_c is not None and fam_t is not None and fam_c != fam_t:
                    st["skipped_family"] += 1
                    continue
                if t not in singular and reach is None and had:
                    reach = self.prior_reach(ck, prior)
                if t not in singular and (not had or t not in reach):
                    st["candidate_skipped"] += 1
                    continue
                if not had and not additions_ok:
                    st["skipped_policy"] += 1
                    continue
                emitted.append((ck, t, shapes.get(t, "")))
                typed_attr.setdefault(ck, {})[t] = grades[t]
                self.note_line(ck, t, tline.get(t, gline))
            for t in sorted(rekey - set(grades)):   # an override a removed fan-out reached: kept as a key, at the
                if t == ck or t not in self.node_by_key:    # grade a fan-out has (AMBIGUOUS — never an upgrade)
                    continue
                emitted.append((ck, t, ""))
                typed_attr.setdefault(ck, {})[t] = "AMBIGUOUS"
                self.note_line(ck, t, gline)
                st["override_keyed"] += 1

    def interface_member(self, t) -> bool:
        """Is node t a member of an INTERFACE (ClassFacts.kind "interface": a TS interface, a Python typing.Protocol,
        a Python class written under `if TYPE_CHECKING:`)? Such a member is a declaration that never runs: the
        call runs whatever its run-time receiver implements — `orjson = cast(_OrjsonModule,
        import_module("orjson")); orjson.dumps()` runs the C extension, flask's `app_ctx: AppContextProxy =
        LocalProxy(...); app_ctx._get_current_object()` runs werkzeug's LocalProxy, never the ProxyMixin stub. A
        site typed to one is unknown for the merge (no key, no REPLACE: the engine's tuples stay as they are);
        its verdict, the site census and the audit are unchanged. Only when EVERY class that claims node t is an
        interface: a TS interface merged with a same-named class (`interface Order { extra(): void }` beside
        `class Order { extra() {} }`) claims the class's real `Order.extra` node by name — a real method. C# (with the
        reader's modifiers): a static interface member with a body is no such declaration (cs_static_iface)."""
        return self.iface_only.get(t, False)

    def via_interface(self, vd, t) -> bool:
        """Was typed target t reached through an INTERFACE (ClassFacts.kind "interface": a TS interface, a Python
        typing.Protocol, a typing-only class) — t is a member of one (never emitted: interface_member), or the
        receiver's class is one? Its run-time values need not be its subclasses (structural implementers; a
        typing-only proxy's real object), so the static target may not be what the call runs: never EXTRACTED."""
        keys = set(vd.recv_keys) | ({vd.recv_cls} if vd.recv_cls else set())
        if t in self.cs_static_iface:
            return False                           # C#: `IFactory.Make()`, a static member with a body, runs as written
        return self.interface_member(t) or any(k in self.classes and self.classes[k].facts.kind == "interface"
                                               for k in keys)

    def note_line(self, ck, t, line) -> None:
        """Record the first call line of emitted key t in caller ck (applied in _finish as call_lines["key"])."""
        if _ok_int(line) and line > 0:
            cur = self.line_attr.setdefault(ck, {})
            if t not in cur or line < cur[t]:
                cur[t] = line

    def cs_virtual_kind(self, t, m) -> str:
        """C# (virtual_dispatch): is method node t (member m) a VIRTUAL slot — "virtual" (every definition of m in its
        class is virtual / abstract / override and none is sealed), "plain" (none is), "mixed" (overloads disagree, or
        a modifier set is unknown), "noinfo" (the reader keeps no modifiers)."""
        ci = self.classes.get(self.owner_of.get(t))
        if ci is None or ci.facts.kind == "interface":
            return "plain"
        defs = [mf for mf in ci.facts.methods.get(m) or () if t in self.method_nodes(ci, m, [mf])[0]] or \
            list(ci.facts.methods.get(m) or ())
        kinds = set()
        for mf in defs:
            mods = self.cs_member(ci, mf, m, "mods")
            if mods is _NOINFO:
                return "noinfo"
            if not isinstance(mods, frozenset):
                kinds.add("?")
            elif "static" in mods or "sealed" in mods or not (mods & _CS_VIRTUAL):
                kinds.add("plain")
            else:
                kinds.add("virtual")
        return kinds.pop() if len(kinds) == 1 and "?" not in kinds else ("plain" if not kinds else "mixed")

    def virtual_dispatch(self, rel, m, site, att, vd, out, hold, si=-1) -> _Verdict:
        """LanguageSpec.lookup_rules virtual_dispatch (C#): a site typed to a VIRTUAL class member (virtual, abstract or
        override, never sealed) through a receiver whose run-time class may be a project subclass (a declared type,
        `this`, an implicit `this`; never `base.`, never a construction, never a struct or a sealed / static class)
        is dispatch, like an interface member: the
        declared member is linked at INFERRED, never EXTRACTED (DEEPMAP_TYPED_CALLS_IFACE static; a body-less
        abstract member included), plus the overrides with a body that project subclasses of the receiver's static
        type declare in an unbroken override chain (impl): one -> INFERRED, 2..MAX_TARGETS -> AMBIGUOUS each, more
        or an unknowable reach -> none, and the group then never REPLACEs (`hold`). A test-file override counts only
        for a test caller. A receiver of a closed generic class (`Converter<int> _c`) skips a subclass whose base list
        closes it with other concrete type arguments (`: Converter<string>`: never a Converter<int>; cs_args_exclude,
        invariant). Without the static mode the site no longer types its declared member (unknown: ADD only).
        Returns the verdict to merge; `out` = {target: [grade, shape, first line]} like iface_dispatch's; `si` = the
        site's scope (its receiver's declaration)."""
        if vd.dispatch != "sub" or vd.recv_kind == "SUPER" or not vd.recv_keys \
                or all(self.cs_final(k) for k in vd.recv_keys):
            return vd                              # a struct / sealed receiver: its run-time class is exactly it
        kinds = {self.cs_virtual_kind(t, m) for t in vd.targets}
        if kinds <= {"plain", "noinfo"}:
            if "noinfo" in kinds and any("abstract" in mf.flags for t in vd.targets
                                         for mf in self.target_defs(t, m)):
                return vd._replace(weak=True)      # a body-less member never runs: never EXTRACTED
            return vd
        lang = self.lang_of.get(rel) or getattr(self.spec_of(rel), "name", "")
        ds = self.stats["by_lang"].setdefault(lang, _lang_stats()).setdefault(
            "virtual", {"sites": 0, "static": 0, "overrides": 0, "fanout": 0, "too_many": 0, "unknown": 0,
                        "mixed": 0, "closed_args": 0})
        if kinds != {"virtual"}:
            ds["mixed"] += 1                       # an overload set the pass cannot split: never EXTRACTED, no fan-out
            return vd._replace(weak=True)
        ds["sites"] += 1
        if "impl" in self.cfg.iface:
            closed = self.cs_closed_args(rel, si, site.recv) if len(vd.recv_keys) == 1 else None
            got = self.virt_overrides(vd.recv_keys, m, set(vd.targets), self.test_file(rel), closed)
            ds["closed_args"] = ds.get("closed_args", 0) + int(self.memo.get(
                ("virtx", frozenset(vd.recv_keys), m, frozenset(vd.targets), bool(self.test_file(rel)), closed),
                0) or 0)
            if got is None:
                ds["unknown"] += 1
                hold.append("unknown")
            elif len(got) > MAX_TARGETS:
                ds["too_many"] += 1
                hold.append("too many")
            elif got and att in _ATTRIBUTED:
                grade = "INFERRED" if len(got) == 1 else "AMBIGUOUS"
                if len(got) > 1:
                    ds["fanout"] += 1
                for t in sorted(got):
                    cur = out.get(t)
                    if cur is None:
                        out[t] = [grade, vd.shape, site.line]
                    else:
                        if _GRADE_RANK[grade] > _GRADE_RANK[cur[0]]:
                            cur[0] = grade
                        cur[2] = min(cur[2], site.line)
                    ds["overrides"] += 1
        if "static" in self.cfg.iface:
            ds["static"] += 1
            return vd._replace(weak=True)
        return vd._replace(kind="unknown", why="virtual")

    def cs_final(self, key) -> bool:
        """C#: can no class derive from class key — a struct / enum, or a class declared sealed or static in any
        part (_cs["sealed"])? False when unknown."""
        ci = self.classes.get(key)
        if ci is None:
            return False
        if ci.facts.kind in ("struct", "enum"):
            return True
        for rel, idx in self.class_parts(key):
            t = self.cs_table(rel)
            if t is not None and isinstance(t.get("sealed"), list) and idx in t["sealed"]:
                return True
        return False

    def target_defs(self, t, m) -> list:
        """The MethodFacts of member m in method node t's class."""
        ci = self.classes.get(self.owner_of.get(t))
        return list(ci.facts.methods.get(m) or ()) if ci is not None else []

    def virt_overrides(self, keys, m, targets, caller_test, closed=None) -> "set | None":
        """The overrides a virtual call of m through a receiver of static type(s) `keys` may run (virtual_dispatch):
        every definition of m with a body marked `override` in a project subclass that overrides the slot of the
        call's target definition(s) (cs_slot_unbroken: its own signature, an unbroken override chain up to the
        receiver's class — a `new` / non-override definer of THAT signature between them starts another slot; a
        same-named definition of another signature neither breaks nor continues it). None when that cannot be
        known: a subclass is ambiguous, dynamic, unlinearizable or has no modifiers recorded, or a signature on the
        path cannot be told. `closed` (cs_closed_args, one receiver class): its concrete type arguments — a subclass
        whose every listing of the class has other concrete arguments is skipped (cs_args_exclude, invariant; the
        count kept in memo ("virtx", ...))."""
        mk = ("virt", frozenset(keys), m, frozenset(targets), bool(caller_test), closed)
        if mk in self.memo:
            return self.memo[mk]
        out: "set | None" = set()
        skipped = 0
        for key in sorted(keys):
            if key not in self.classes or self.classes[key].ambiguous:
                out = None
                break
            for s in self.descendants(key):
                sc = self.classes.get(s)
                if sc is None or sc.ambiguous or sc.facts.dynamic:
                    out = None
                    break
                if sc.facts.kind == "interface" or (not caller_test and self.test_file(sc.rel, unknown=False)):
                    continue
                if closed is not None:
                    lv = self.mro(s)
                    if lv is not None and self.cs_args_exclude(s, key, closed, [e for x in lv for e in x]):
                        skipped += 1
                        continue
                ov = []
                for mf in sc.facts.methods.get(m) or ():
                    mods = self.cs_member(sc, mf, m, "mods")
                    if not isinstance(mods, frozenset):
                        out = None
                        break
                    if "override" in mods and "abstract" not in mf.flags:
                        ov.append(mf)
                if out is None:
                    break
                if not ov:
                    continue
                slot, picked = self.cs_slot_sigs(s, key, m, targets), []
                for mf in ov:
                    ok = self.cs_slot_unbroken(s, key, m, mf, slot)
                    if ok is None:
                        out = None
                        break
                    if not ok:
                        continue
                    picked.append(mf)
                if out is None:
                    break
                if not picked:
                    continue
                nodes = self.method_nodes(sc, m, picked)[0]
                if not nodes:
                    out = None
                    break
                out.update(nodes)
            if out is None:
                break
        if out is not None:
            out -= set(targets)
        self.memo[mk] = out
        self.memo[("virtx",) + mk[1:]] = skipped
        return out

    def cs_slot_sigs(self, s, key, m, targets) -> "list | None":
        """The signatures, as subclass s sees them (cs_def_sig), of the definitions of m that the call's target nodes
        hold in classes on receiver class key's chain (key itself or a base: the class its member lookup hit), or
        None when one cannot be read."""
        mk = ("cslot", s, key, m, frozenset(targets))
        if mk in self.memo:
            return self.memo[mk]
        chain = self.mro_class_keys(key) | {key}
        out: "list | None" = []
        for t in sorted(targets):
            ci = self.classes.get(self.owner_of.get(t))
            if ci is None or ci.key not in chain:
                continue
            defs = [mf for mf in ci.facts.methods.get(m) or () if t in self.method_nodes(ci, m, [mf])[0]] or \
                list(ci.facts.methods.get(m) or ())
            for mf in defs:
                sig = self.cs_def_sig(ci, mf, m, s)
                if sig is None:
                    out = None
                    break
                out.append(sig)
            if out is None:
                break
        self.memo[mk] = out if out else None
        return self.memo[mk]

    def cs_slot_unbroken(self, s, key, m, mf, slot) -> "bool | None":
        """Does definition mf (an `override`) of m in subclass s override the call's slot — the target definitions'
        signatures `slot` (cs_slot_sigs)? Its signature must be one of the slot's, and every class strictly between s
        and class key on s's chain that defines m with THAT signature must define it as an `override` (C# hides only a
        base method of the same signature: a definition of another signature neither breaks nor continues the slot,
        and neither does a private one, which no subclass sees). None when unknowable (an unreadable entry or
        modifiers, a signature _cs_tcmp cannot tell, an `internal` / `private protected` one in another .csproj than
        s: another assembly does not see it)."""
        levels = self.mro(s)
        sc = self.classes.get(s)
        if levels is None or slot is None or sc is None:
            return None
        entries = [e for lv in levels for e in lv]
        if ("class", key) not in entries:
            return None
        mine = self.cs_def_sig(sc, mf, m, s)
        cmp = [self.cs_sig_cmp(mine, q) for q in slot]
        if True not in cmp:
            return None if None in cmp else False  # an override of another slot (another signature)
        for e in entries[1:entries.index(("class", key))]:
            if e[0] != "class":
                return None
            c = self.classes.get(e[1])
            if c is None or c.ambiguous:
                return None
            if c.facts.kind == "interface":
                continue
            for d in c.facts.methods.get(m) or ():
                mods = self.cs_member(c, d, m, "mods")
                if not isinstance(mods, frozenset):
                    return None
                same = self.cs_sig_cmp(self.cs_def_sig(c, d, m, s), mine)
                if same is None:
                    return None
                if same and "override" not in mods:
                    acc = mods & _CS_ACCESS
                    if acc <= {"private"}:
                        continue                   # private (as written, or by default): no subclass sees it
                    if acc in ({"internal"}, {"private", "protected"}) \
                            and self.cs_group_of(self.rel_of(c, d)) != self.cs_group_of(sc.rel):
                        return None                # another .csproj: maybe another assembly, which does not see it
                    return False
        return True

    def iface_dispatch(self, rel, m, site, att, orig, out, si=-1) -> "frozenset | None":
        """LanguageSpec.lookup_rules iface_dispatch (C#): an ATTRIBUTED site typed to an interface's own member through
        an interface receiver (`private readonly IShoppingCartHelper _h; _h.Create()`) links that member at INFERRED
        (the compiler's own binding; DEEPMAP_TYPED_CALLS_IFACE static) plus the project implementers it may run
        (impl): one -> INFERRED, 2..MAX_TARGETS -> AMBIGUOUS each, more or an unknowable reach -> none. A test-file
        implementer counts only for a test caller. A STATIC interface member with a body (C# 8+) never gets here:
        it is an exact call (cs_static_iface); a body-less static one has no implementers either.
        A receiver of a closed generic interface (`IRepo<Order> _r`) skips an implementer whose own base-list entry
        for it has other concrete type arguments (`: IRepo<User>`) unless the interface is variant (`in` / `out`).
        `out` = {target: [grade, shape, first line]} for the group; `si` = the site's scope (its receiver's
        declaration). Returns the site's WHOLE dispatch set (the interface member plus every implementer) when it is
        fully known: both modes on, the reader's tables read, every implementer reach known and at most
        MAX_TARGETS, no project class that may implement the interface unseen (cs_iface_unresolved). The group may
        then REPLACE the engine's tuples outside that set (stats dispatch known). Else None: ADD only."""
        if att not in _ATTRIBUTED or orig.kind != "typed":
            return
        ds = self.cs_dispatch_stats(rel)
        keys = set(orig.recv_keys) | ({orig.recv_cls} if orig.recv_cls else set())
        if not keys or not all(k in self.classes and self.classes[k].facts.kind == "interface" for k in keys) \
                or not all(self.interface_member(t) for t in orig.targets):
            ds["not_interface"] += 1                   # a member reached through a concrete receiver: as before
            return
        ds["sites"] += 1

        def add(t, g):
            cur = out.get(t)
            if cur is None:
                out[t] = [g, orig.shape, site.line]
            else:
                if _GRADE_RANK[g] > _GRADE_RANK[cur[0]]:
                    cur[0] = g
                cur[2] = min(cur[2], site.line)
        if "static" in self.cfg.iface:
            for t in orig.targets:
                add(t, "INFERRED")
            ds["static"] += 1
        if "impl" not in self.cfg.iface:
            return
        defs = [self.target_defs(t, m) for t in orig.targets]
        if all(d and all("static" in mf.flags for mf in d) for d in defs):
            ds["static_member"] += 1               # a static interface member has no implementers: nothing to run
            return
        caller_test = self.test_file(rel)
        closed = self.cs_closed_args(rel, si, site.recv) if len(keys) == 1 else None
        impls: set = set()
        for k in sorted(keys):
            got = self.impl_reach(k, m, caller_test, closed)
            if got is None:
                ds["unknown"] += 1
                return
            ds["closed_args"] += int(self.memo.get(("implx", k, m, bool(caller_test), closed), 0) or 0)
            impls |= got
        if len(impls) > MAX_TARGETS:
            ds["too_many"] += 1
            return
        grade = "INFERRED" if len(impls) == 1 else "AMBIGUOUS"
        if len(impls) > 1:
            ds["fanout"] += 1
        for t in sorted(impls):
            add(t, grade)
            ds["implementers"] += 1
        if "static" not in self.cfg.iface or self.cs_table(rel) is None \
                or any(self.cs_iface_unresolved(k, m) for k in keys):
            return
        ds["known"] += 1
        return frozenset(orig.targets) | frozenset(impls)

    def cs_dispatch_stats(self, rel) -> dict:
        """by_lang[<rel's language>]["dispatch"] (interface dispatch counters), created with every key."""
        lang = self.lang_of.get(rel) or getattr(self.spec_of(rel), "name", "")
        ds = self.stats["by_lang"].setdefault(lang, _lang_stats()).setdefault(
            "dispatch", {"sites": 0, "static": 0, "implementers": 0, "fanout": 0, "too_many": 0, "unknown": 0,
                         "not_interface": 0, "static_member": 0, "closed_args": 0, "known": 0})
        ds.setdefault("known", 0)
        return ds

    def cs_iface_unresolved(self, key, m) -> bool:
        """May a project class of interface key's language implement it unseen: one that lists a base of the
        interface's own name the pass cannot resolve (an ambiguous using, an alias it does not hold), or a base it
        cannot read at all (`global::Ns.IPlugin`) while defining m itself? Then the interface's dispatch set is not
        fully known."""
        mk = ("ifunres", key, m)
        if mk not in self.memo:
            if "unres_idx" not in self.memo:      # once per run: {base name: classes}, [classes with an unread base]
                by_name: dict = {}
                blank: list = []
                for k in sorted(self.classes):
                    for e in self.bases(k):
                        if e[0] == "unres":
                            (by_name.setdefault(e[1], []) if e[1] else blank).append(k)
                self.memo["unres_idx"] = (by_name, blank)
            by_name, blank = self.memo["unres_idx"]
            ci = self.classes.get(key)
            name = ci.facts.name if ci is not None else None
            fam = ci.spec.family if ci is not None and ci.spec is not None else None

            def same_family(k):
                c = self.classes[k]
                return k != key and c.spec is not None and c.spec.family == fam
            self.memo[mk] = name is None or any(same_family(k) for k in by_name.get(name, ())) or any(
                same_family(k) and m in self.classes[k].facts.methods for k in blank)
        return self.memo[mk]

    def impl_reach(self, key, m, caller_test, closed=None) -> "set | None":
        """The project methods a call of m through interface `key` may run (iface_dispatch): for every project class
        that implements it (transitively; never an interface), the definition of m that implements it — with the C#
        reader's modifiers (cs_impl_of): the member mapped by the most derived class that LISTS the interface (or an
        interface deriving from it) in its own base list, or the most derived `override` of that member in an
        unbroken override chain; a `new` / non-virtual same-named method of a subclass that does not re-list the
        interface is not one. Without modifiers: the first NON-abstract, non-static definition of m before `key` on
        that class's chain. An explicit implementation is included. None when that cannot be known: a descendant is
        ambiguous, dynamic or unlinearizable, an external or unresolved entry comes first (an external base may
        implement m), or a concrete class has no definition (an unread part, a generated one). An abstract class
        without a definition defers to its subclasses. A test-file class counts only for a test caller. `closed`
        (cs_closed_args): the receiver's concrete type arguments — an implementer whose every listing of `key` has
        other concrete arguments is skipped (cs_args_exclude). The count of those is kept in memo ("implx", ...)."""
        mk = ("impl", key, m, bool(caller_test), closed)
        if mk in self.memo:
            return self.memo[mk]
        out: "set | None" = set()
        skipped = 0
        ci = self.classes.get(key)
        if ci is None or ci.ambiguous:
            out = None
        for s in (self.descendants(key) if out is not None else ()):
            si = self.classes.get(s)
            if si is None or si.ambiguous or si.facts.dynamic:
                out = None
                break
            if si.facts.kind == "interface":
                continue
            if not caller_test and self.test_file(si.rel, unknown=False):
                continue
            levels = self.mro(s)
            entries = [e for lv in levels for e in lv] if levels is not None else []
            if ("class", key) not in entries:
                out = None
                break
            if closed is not None and self.cs_args_exclude(s, key, closed, entries):
                skipped += 1
                continue
            if self.cs_table(si.rel) is not None:
                found = self.cs_impl_of(si, key, m, entries)
            else:
                found = None
                for e in entries[:entries.index(("class", key))]:
                    if e[0] != "class":
                        break
                    x = self.classes.get(e[1])
                    if x is None or x.ambiguous or x.facts.dynamic:
                        break
                    mfs = [mf for mf in x.facts.methods.get(m) or () if not ({"abstract", "static"} & set(mf.flags))]
                    if mfs:
                        found = self.method_nodes(x, m, mfs)[0]
                        break
                else:
                    found = () if si.facts.kind == "abstract" else None
            if not found and found != ():
                out = None
                break
            out.update(found)
        self.memo[mk] = out
        self.memo[("implx",) + mk[1:]] = skipped
        return out

    def cs_lists(self, x, key) -> bool:
        """Does class x's OWN base list name interface key, or an interface deriving from it?"""
        for b in self.bases(x):
            if b[0] != "class" or b[1] not in self.classes or self.classes[b[1]].facts.kind != "interface":
                continue
            if b[1] == key or key in self.mro_class_keys(b[1]):
                return True
        return False

    def cs_impl_of(self, si, key, m, entries):
        """C# (iface_dispatch, with the reader's modifiers): the node(s) of the definition of m that class si runs for
        interface key's member m — () for an abstract class with none, None when unknowable (impl_reach)."""
        chain: list = []                           # si and its base classes before key, most derived first
        for e in entries[:entries.index(("class", key))]:
            if e[0] != "class":
                chain.append(None)                 # an external / unresolved base
                break
            x = self.classes.get(e[1])
            if x is None or x.ambiguous or x.facts.dynamic:
                return None
            if x.facts.kind != "interface":
                chain.append(x)
        defs = [[mf for mf in x.facts.methods.get(m) or () if "static" not in mf.flags] if x is not None else None
                for x in chain]
        j = next((i for i, x in enumerate(chain) if x is not None and self.cs_lists(x.key, key)), None)
        if j is None or None in chain[:j]:
            return None
        islot = self.cs_iface_sigs(si.key, key, m)
        if islot is None:
            return None
        nodes: list = []
        for sig in islot:                          # each signature the interface declares m with
            same: dict = {}                        # chain index -> [(definition, modifiers)] of THAT signature
            for i, (x, d) in enumerate(zip(chain, defs)):
                if x is None:
                    break
                for mf in d:
                    c = self.cs_sig_cmp(self.cs_def_sig(x, mf, m, si.key), sig)
                    if c is None:
                        return None                # a definition the pass cannot place: the implementer unknown
                    if c:
                        md = self.cs_member(x, mf, m, "mods")
                        if not isinstance(md, frozenset):
                            return None
                        same.setdefault(i, []).append((mf, md))
                if i >= j and i in same:
                    break                          # the implementing class: the bases above it are not read
            # the interface mapping: from the most derived class listing the interface up, the first definition of
            # the member's own signature (a same-named one of another signature implements nothing)
            impl = next((i for i in range(j, len(chain)) if chain[i] is None or i in same), None)
            if impl is None:
                if si.facts.kind == "abstract":
                    continue
                return None
            if chain[impl] is None:
                return None                        # an external base may implement m

            def overrides(i):
                return i in same and all("override" in md for _mf, md in same[i])

            pick = impl
            for i in range(impl):                  # the most derived override of the implementing member's slot
                if overrides(i) and all(k not in same or overrides(k) for k in range(i + 1, impl)):
                    pick = i
                    break
            body = [mf for mf, _md in same[pick] if "abstract" not in mf.flags]
            if not body:
                if si.facts.kind == "abstract":
                    continue
                return None
            got = self.method_nodes(chain[pick], m, body)[0]
            if not got:
                return None
            nodes.extend(k for k in got if k not in nodes)
        return nodes or (() if si.facts.kind == "abstract" else None)

    def cs_iface_sigs(self, frame, key, m) -> "list | None":
        """The signatures, as class `frame` sees them (cs_def_sig), of every definition of m that interface key and
        the interfaces it derives from declare (the members a call through key may bind), or None when one cannot be
        read or none is found."""
        mk = ("cisig", frame, key, m)
        if mk in self.memo:
            return self.memo[mk]
        out: "list | None" = []
        for k in [key] + sorted(self.mro_class_keys(key) - {key}):
            ci = self.classes.get(k)
            if ci is None or ci.facts.kind != "interface":
                continue
            for mf in ci.facts.methods.get(m) or ():
                if "static" in mf.flags:
                    continue
                sig = self.cs_def_sig(ci, mf, m, frame)
                if sig is None:
                    out = None
                    break
                out.append(sig)
            if out is None:
                break
        self.memo[mk] = out if out else None
        return self.memo[mk]

    def cs_type_desc(self, t, rel, si, ctx):
        """A written type argument as ("b", builtin) | ("c", class key) | None (a type parameter, a generic, an
        external or unresolved type)."""
        if t is None:
            return None
        if t.kind == "builtin":
            return ("b", t.name)
        if t.kind not in ("name", "qualified"):
            return None
        v = self.eval_type(t, rel, si, opaque=False, ctx=ctx)
        return ("c", next(iter(v.keys))) if v.kind == "inst" and len(v.keys) == 1 else None

    def cs_closed_args(self, rel, si, recv):
        """The receiver's declared generic interface type arguments (`IRepo<Order> _r` -> (("c", Order),)), each a
        cs_type_desc or None, or None when it has none known."""
        d = self.cs_decl_typeref(rel, si, recv)
        if d is None:
            return None
        t = d[0]
        if t.kind == "nullable" and t.args:
            t = t.args[0]
        if t.kind != "generic" or len(t.args) < 2:
            return None
        got = tuple(self.cs_type_desc(a, d[1], d[2], d[3]) for a in t.args[1:])
        return got if any(g is not None for g in got) else None

    def cs_type_args_apart(self, a, b, variance) -> bool:
        """Can two concrete type arguments a, b (cs_type_desc) never denote one constructed interface? Invariant: they
        differ. `out` / `in` (covariance / contravariance): also no reference conversion either way — two project
        classes with closed chains, neither on the other's; a predefined type other than object against another
        type (a value type never varies; nothing derives from string)."""
        if a is None or b is None or a == b:
            return False
        if not variance:
            return True
        if a[0] == "b" or b[0] == "b":
            return not any(x[0] == "b" and x[1] in ("object", "dynamic") for x in (a, b)) \
                and all(x[0] != "b" or x[1] in _CS_KNOWN for x in (a, b))
        sa, ca = self.cs_supertypes(a[1])
        sb, cb = self.cs_supertypes(b[1])
        return ca and cb and b[1] not in sa and a[1] not in sb

    def cs_args_exclude(self, s, key, closed, entries) -> bool:
        """Is implementer s never a run-time value of a receiver typed `key<closed>` — every base-list entry naming key
        along s's chain has, at some position, a concrete type argument the receiver's can never be
        (cs_type_args_apart; a variant type parameter only where no reference conversion exists either)?"""
        kc = self.classes.get(key)
        if kc is None:
            return False
        variance: tuple = ()
        for rel_k, idx in self.class_parts(key):
            t = self.cs_table(rel_k)
            if t is None or not isinstance(t.get("variant"), dict):
                return False
            variance = variance or tuple(t["variant"].get(idx, ()))
        listings = 0
        for e in entries:
            if e[0] != "class" or e[1] == key:
                continue
            c = self.classes.get(e[1])
            if c is None:
                return False
            for b in c.facts.bases:
                head = b.args[0] if b.kind == "generic" and b.args else b
                v = self.eval_type(head, self.rel_of(c, b), c.facts.scope, opaque=False,
                                   extra_shadow=c.facts.type_params, ctx=self.outer_key(c.key))
                if v.kind != "inst" or v.keys != frozenset({key}):
                    continue
                listings += 1
                if b.kind != "generic" or len(b.args) - 1 != len(closed):
                    return False
                mine = [self.cs_type_desc(a, self.rel_of(c, b), c.facts.scope, self.outer_key(c.key))
                        for a in b.args[1:]]
                if not any(self.cs_type_args_apart(x, y, variance[i] if i < len(variance) else "")
                           for i, (x, y) in enumerate(zip(mine, closed))):
                    return False                   # this listing may be the receiver's constructed interface
        return listings > 0

    def override_reach(self, rel, m, verdicts, fam_c, lang_family):
        """The node keys a REPLACE group's typed sites may reach by dynamic dispatch beyond their static targets
        (a project override of m in a subclass of a receiver whose run-time class may be that subclass; the next
        definer for a cooperative `super()`), or None when that cannot be known (then the group only ADDs). C#
        (virtual_dispatch, with the reader's modifiers): only the overrides virt_overrides finds (an `override` with
        a body in an unbroken chain from the receiver's class, test files included), so a REPLACE removes a tuple
        to a hiding `new` method or to an override of another slot."""
        out: set = set()
        for vd, _att in verdicts:
            if vd.kind != "typed" or not vd.dispatch:
                continue
            for key in sorted(vd.recv_keys):
                ci = self.classes.get(key)
                if vd.dispatch == "sub" and ci is not None and ci.spec is not None \
                        and "virtual_dispatch" in ci.spec.lookup_rules and self.cs_table(ci.rel) is not None:
                    # C# (virtual_dispatch): only an override in an unbroken chain from the receiver's class runs,
                    # never a `new` method, nor an override of another slot (one under a `new virtual`)
                    got = self.virt_overrides({key}, m, set(vd.targets), True)
                else:
                    got = self.dispatch_reach(key, m, vd.dispatch, vd.on_class)
                if got is None:
                    return None
                out |= got
        static = {t for vd, _a in verdicts if vd.kind == "typed" for t in vd.targets}
        return {t for t in out - static
                if fam_c is None or lang_family(self.file_of.get(t, "")) in (None, fam_c)}

    def override_split(self, ck, prior, over, static, m, fam_c, lang_family):
        """(prior tuple indexes kept WHOLE, override keys to re-emit) when a REPLACE fires for (C, m): a prior tuple
        that reaches an override (`over`) — or a same-family :Method m whose class this pass could not see (a file
        it could not read, a class without a node: it may be an override) — is kept whole, with its own grade,
        when everything else it reaches is a static target; otherwise it goes (its other targets are the
        non-override fan-out) and the overrides it reached are re-emitted as keys at AMBIGUOUS, the grade of a
        fan-out (never an upgrade). An override a kept tuple already reaches is not re-emitted."""
        idx = self.name_index()
        keep, rekey, kept_reach, unseen = set(), set(), set(), set()
        for i in prior:
            callee = self.calls[i][1]
            reach = {str(callee)} if _is_call_key(callee) else {k for k in idx.get(callee, ()) if k != ck}
            dark = {t for t in reach - over - static if self.unseen_member(t, m, fam_c, lang_family)}
            hit = (reach & over) | dark
            if not hit:
                continue
            unseen |= dark
            if reach <= hit | static:
                keep.add(i)
                kept_reach |= reach
            else:
                rekey |= hit
        self.stats["override_unseen"] += len(unseen)
        return keep, rekey - kept_reach

    def unseen_member(self, t, m, fam_c, lang_family) -> bool:
        """Is node t a :Method named m of the caller's language family whose class this pass cannot see — its file
        has no facts (unreadable, capped, failed), or no indexed class of that file carries its owner's name (a
        class the engine gave no node)? Such a method may override the typed target. A method of a class this pass
        DID index is judged by that class's facts (dispatch_reach), never here."""
        if self.label_of.get(t) != "Method" or name_seg(self.name_of.get(t)) != m or t in self.owner_of:
            return False
        rel = self.file_of.get(t, "")
        if fam_c is not None and lang_family(rel) != fam_c:
            return False
        if rel not in self.facts:
            return True
        name = str(self.name_of.get(t) or "")
        owner = name_seg(name[:len(name) - len(m)].rstrip(".:#")) if name.endswith(m) else ""
        return not any(k in self.classes for k in self.by_file_seg.get((rel, owner), ()))

    def name_index(self) -> dict:
        """THE push's name index (edge_resolve.build_name_index: full name + last `.` / `::` segment -> keys)."""
        if "name_idx" not in self.memo:
            self.memo["name_idx"] = _sibling("edge_resolve").build_name_index(
                [{"key": k, "name": self.name_of[k]} for k in self.node_by_key])
        return self.memo["name_idx"]

    def prior_reach(self, ck, prior) -> set:
        """Every node the caller's prior tuples of a name can bind at the push (a key: itself; a name: its fan-out
        through the push's name index) — a candidate set may only narrow this, never add to it (design 2.5)."""
        out = set()
        idx = self.name_index()
        for i in prior:
            callee = self.calls[i][1]
            if _is_call_key(callee):
                out.add(str(callee))
            else:
                out.update(k for k in idx.get(callee, ()) if k != ck)
        return out

    def _audit_caller(self, rel, ck, sites_by, mine, removed, weak_attr, fam_c, audit_ctx, lang_family):
        """Design 2.7 for one caller's surviving input tuples."""
        for i in mine:
            if i in removed:
                continue
            if audit_ctx[0] is None:
                audit_ctx[0] = self.name_index()
                count = Counter()
                for k in self.node_by_key:
                    if self.label_of[k] == "Method":
                        count[(lang_family(self.file_of[k]), name_seg(self.name_of[k]))] += 1
                audit_ctx[1] = count
            t = self._audit_target(ck, self.calls[i][1], audit_ctx[0])
            if t is None or self.label_of.get(t) != "Method" or self.file_of.get(t) != rel:
                continue
            m = name_seg(self.name_of.get(t))
            if audit_ctx[1][(fam_c, m)] < 2:
                continue
            if self._audit_demote(rel, sites_by.get(m, ()), t):
                weak_attr.setdefault(ck, set()).add(t)

    def _finish(self, removed, emitted, typed_attr, weak_attr, member_attr, callers_typed, callers_audit):
        """Build the output list, the stats and the node attributes (nothing is mutated before this point; the
        attributes are applied last, all-or-nothing)."""
        st = self.stats
        emit_set = {(ck, t) for ck, t, _s in emitted}
        moved = set()
        for i, c in enumerate(self.calls):
            if i in removed:
                continue
            if isinstance(c, (tuple, list)) and len(c) >= 2 and _is_call_key(c[1]) and (c[0], c[1]) in emit_set:
                moved.add(i)                       # an identical keyed tuple: re-appended last (its grade wins)
        for ck in list(weak_attr):
            weak_attr[ck] -= set(typed_attr.get(ck, {}))
            if not weak_attr[ck]:
                del weak_attr[ck]
        input_pairs = {(c[0], c[1]) for c in self.calls if isinstance(c, (tuple, list)) and len(c) >= 2}
        out_emitted = [(ck, _call_key(t)) for ck, t, _s in emitted]
        if removed or moved or out_emitted:
            out = [c for i, c in enumerate(self.calls) if i not in removed and i not in moved] + out_emitted
        else:
            out = self.calls
        st["removed"] = len(removed)
        st["emitted"] = len(out_emitted)
        st["new_pairs"] = sum(1 for ck, t, _s in emitted if (ck, t) not in input_pairs)
        for ck, t, shape in emitted:
            st["grades"][typed_attr[ck][t]] += 1
            if shape in st["by_shape"]:
                st["by_shape"][shape]["emitted"] += 1
            lang = self.lang_of.get(self.file_of.get(ck, ""))
            if lang in st["by_lang"]:
                st["by_lang"][lang]["emitted"] += 1
        for i in sorted(removed):
            lang = self.lang_of.get(self.file_of.get(self.calls[i][0], ""))
            if lang in st["by_lang"]:
                st["by_lang"][lang]["removed"] += 1
        for ck in sorted(weak_attr):
            st["audit_weak"] += len(weak_attr[ck])
            lang = self.lang_of.get(self.file_of.get(ck, ""))
            if lang in st["by_lang"]:
                st["by_lang"][lang]["weak"] += len(weak_attr[ck])
        changes = []
        for ck in sorted(callers_typed):
            node = self.node_by_key[ck]
            want = typed_attr.get(ck)
            if want:
                changes.append((node, "call_typed", {t: want[t] for t in sorted(want)}))
            elif "call_typed" in node:
                changes.append((node, "call_typed", _DELETE))
            drop = member_attr.get(ck)
            if drop:
                kept = sorted(str(x) for x in node.get("call_member") if str(x) not in drop)
                changes.append((node, "call_member", kept if kept else _DELETE))
                st["call_member_cleared"] += len(drop)
        for ck in sorted(callers_audit):
            node = self.node_by_key[ck]
            want = weak_attr.get(ck)
            if want:
                changes.append((node, "call_weak", sorted(want)))
            elif "call_weak" in node:
                changes.append((node, "call_weak", _DELETE))
        for ck in sorted(self.line_attr):          # call_lines["key"]: the first call line of every emitted key
            node = self.node_by_key.get(ck)
            keep = {t: ln for t, ln in self.line_attr[ck].items() if t in (typed_attr.get(ck) or {})}
            merged = _merged_lines(node.get("call_lines") if node is not None else None, keep)
            if node is not None and merged is not None:
                changes.append((node, "call_lines", merged))
        _apply(changes)
        return out

    def _audit_target(self, ck, callee, name_idx):
        """The one node an input callee binds (a CallKey; a qualified name binding exactly one node), or None."""
        if not isinstance(callee, str):
            return None
        if _is_call_key(callee):
            return callee if callee in self.node_by_key else None
        if "." not in callee and "::" not in callee:
            return None
        hits = [k for k in name_idx.get(callee, ()) if k != ck and k in self.node_by_key]
        return hits[0] if len(hits) == 1 else None

    def _audit_demote(self, rel, group, t) -> bool:
        """Design 2.7(3): no site of m in C's span is bare, SELF/SUPER, a class reference naming t's owner (or a
        subclass of it) or typed to t; at least one site ATTRIBUTED to C is an untyped receiver (the engine's
        tuple came from an attributed site)."""
        if not group:
            return False
        owner = self.owner_of.get(t)
        untyped = False
        for s, idx, site, att in group:
            if site.kind != "member" or site.recv is None:
                return False
            if site.recv.kind in ("SELF", "SUPER"):
                return False
            vd = self.site_verdict(rel, s, idx, site)
            if vd.kind == "typed" and t in vd.targets:
                return False
            if vd.recv_cls and owner is not None and (vd.recv_cls == owner
                                                      or owner in self.mro_class_keys(vd.recv_cls)):
                return False
            if att in _ATTRIBUTED:
                untyped = True
        return untyped


_DELETE = object()


def _merged_lines(old, add) -> "dict | None":
    """A NEW call_lines dict: `old`'s entries (its "name" sub-dict and any other key kept as they are) with every
    `add` {target key: line} merged into "key" as the minimum with a line already there; None when nothing changes.
    `old` is never mutated, so _apply's undo restores it exactly."""
    if not add:
        return None
    base = old if isinstance(old, dict) else {}
    cur = base.get("key")
    keys = dict(cur) if isinstance(cur, dict) else {}
    changed = not isinstance(cur, dict)
    for t, ln in add.items():
        have = keys.get(str(t))
        if not (_ok_int(have) and have > 0) or ln < have:
            keys[str(t)] = ln
            changed = True
    if not changed:
        return None
    out = {k: v for k, v in base.items() if k != "key"}
    out["key"] = keys
    return out


def _apply(changes) -> None:
    """Set / delete node attributes; on any failure every change made so far is undone and the error re-raised."""
    undo = []
    try:
        for node, attr, value in changes:
            undo.append((node, attr, node.get(attr, _DELETE)))
            if value is _DELETE:
                node.pop(attr, None)
            else:
                node[attr] = value
    except Exception:
        for node, attr, old in reversed(undo):
            try:
                if old is _DELETE:
                    node.pop(attr, None)
                else:
                    node[attr] = old
            except Exception:  # noqa: BLE001
                pass
        raise


# ================================================================================================
# The entry point
# ================================================================================================

def link(root, nodes, calls, *, languages=None, readers=None, source=None, is_test=None, env=None, engine=None,
         sources=None):
    """(calls, stats): the CALLS tuples with typed keys merged in (see the module docstring), and what happened.

    `engine` is the extractor that built `nodes` / `calls` ('graphify' | 'ast'; anything else reads as graphify):
    it decides which nested-scope sites the engine attributed to each caller (ATTRIBUTION). `sources` =
    {rel: the graphify_extract._Source the extraction read and parsed that file with} (S2 passes the
    extraction's own readers, so no file is read or parsed twice; stats sources_shared counts the files they
    answered); a file not in it, or no `sources` at all, is read through `source` (a single shared reader),
    else a fresh _Source(root) — same result, slower.
    `languages` (a {name: LanguageSpec} registry), `readers` ({lang: Reader}) and `env` (the flags) are injection
    points for gates and the offline measurement kit; `is_test` is the test caller rule (default
    sync._is_test_path — S2 passes it). Never raises: on any internal error the INPUT list is returned (the same
    object), every node attribute this call set is rolled back, every counter is the skeleton's again (nothing
    changed) and stats["error"] says why."""
    t0 = time.perf_counter()
    eng = engine if engine in ENGINES else None
    cfg = None
    stats = _stats_skeleton(None, eng)
    try:
        registry = dict(languages) if languages is not None else LANGUAGES
        cfg = _config(env, registry)
        stats = _stats_skeleton(cfg, eng)
        if not cfg.typed and not cfg.audit:
            return calls, stats
        if not isinstance(nodes, list) or not isinstance(calls, list):
            stats["error"] = "nodes / calls are not lists"
            return calls, stats
        shared = None
        if isinstance(sources, dict) and sources:
            single = source
            shared = _SharedSources(sources, (lambda: single) if single is not None
                                    else (lambda: _default_source(root)))
            source = shared
        if source is None:
            source = _default_source(root)
        if is_test is None:
            is_test = _default_is_test()
            if is_test is None:
                stats["notes"].append("sync._is_test_path unavailable: every caller gets corrections only")
        paused = gc.isenabled()           # the cyclic GC is paused for the run (restored after): every full
        if paused:                        # collection would traverse each AST the shared reader holds (millions of
            gc.disable()                  # nodes) while nothing here builds a cycle worth collecting
        try:
            out = _Linker(root, nodes, calls, cfg, registry, readers, source, is_test, stats, engine=eng).run()
        finally:
            if paused:
                gc.enable()
        if shared is not None:
            stats["sources_shared"] = len(shared.served)
        return out, stats
    except Exception as exc:  # noqa: BLE001 — a correction pass must never break a sync
        notes = list(stats.get("notes") or [])
        stats = _stats_skeleton(cfg, eng)         # no partial counts: the run changed nothing
        stats["notes"] = notes
        stats["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return calls, stats
    finally:
        stats["seconds"] = round(time.perf_counter() - t0, 3)


def describe(stats: dict) -> str:
    """One stderr line for the sync log (S2)."""
    try:
        if stats.get("error"):
            return f"typed call linking skipped ({stats['error']}) — the CALLS are unchanged"
        langs = ",".join(stats.get("typed_langs") or []) or "-"
        audit = ",".join(stats.get("audit_langs") or []) or "-"
        g = stats.get("grades") or {}
        kept = int(stats.get("override_kept") or 0) + int(stats.get("override_keyed") or 0)
        shared = int(stats.get("sources_shared") or 0)
        return (f"typed call linking [{langs}; audit {audit}]: {stats.get('emitted', 0)} typed key(s) "
                f"({g.get('EXTRACTED', 0)} EXTRACTED / {g.get('INFERRED', 0)} INFERRED / "
                f"{g.get('AMBIGUOUS', 0)} AMBIGUOUS), {stats.get('removed', 0)} replaced tuple(s)"
                + (f", {kept} subclass override(s) kept reachable" if kept else "")
                + f", {stats.get('audit_weak', 0)} audited down, {stats.get('files', 0)} file(s) read"
                + (f" ({shared} shared with the extraction)" if shared else "")
                + f" in {stats.get('seconds', 0)}s")
    except Exception:  # noqa: BLE001
        return "typed call linking: (no stats)"
