#!/usr/bin/env python3
"""The Python reader of typed-receiver call linking (receiver-calls design 3.1; slice S3b).

typed_calls.link (the whole-graph core, slice S3a) loads this module lazily through
LanguageSpec("python").reader and calls `make_reader(spec)`. The reader lowers ONE file's stdlib `ast` (the
sync's shared graphify_extract._Source.tree) to the frozen IR (typed_calls.FileFacts): Phase A, a pure function
of (rel, the file's bytes, READER_VERSION, the running Python's `ast`). Its binder resolves imports by IMPORT
ROOT (build/py_import_resolver.py), never by suffix. Every typing decision is the core's; this module only says
what the source WRITES — and refuses to say more than it can read:

  imports   module-level imports, including those under module-level `try` / `if` / `with` and
            `if TYPE_CHECKING:` (a name imported twice keeps both entries; the core sees whether they agree).
            `from __future__` is skipped; `from x import *` is the "*" entry. An import inside a function
            POISONS the name there (design 3.1 R2).
  classes   every ClassDef (nested ones carry `outer` / `scope`). `bases` = the written bases minus the
            NEUTRAL ones (builtin `object`, typing / typing_extensions `Generic` / `Protocol`, abc `ABC`, by
            their import, never by spelling); a base that is a call (`namedtuple(...)`, `with_metaclass(...)`)
            is an unknown TypeRef (the core then never types past it). `kind` is "interface" for a typing
            `Protocol` (structural: its implementers need not inherit it) and for a TYPING-ONLY class — one
            written in the body of an `if TYPE_CHECKING:` (by spelling; nested ones too), which never exists at
            run time — else "class"; the core never keys an interface's own member (it never runs) and never
            knows what a call on an interface-typed receiver dispatches to. `fields` = every binding of `self.f`
            (`cls.f` in a classmethod) across the class's methods — closures included — plus every class-body
            binding, each with the scope its expression's names use (a class-body VALUE is unknown unless it is
            `None` or a literal: `f = Descriptor()` may be a descriptor, and only class-body annotations are R3
            sources). `methods` = the defs of the class body
            (also under class-body `if` / `try`), `@overload` stubs dropped when an implementation exists;
            flags static / class / property (property, cached_property, any `*property*` decorator,
            `@x.setter`) / abstract; `returns` only through decorators that keep a function's return value
            (staticmethod, classmethod, properties, abstractmethod, overload, override, final, lru_cache,
            cache) and never for an `async def` (its call returns a coroutine). `dynamic` (design 3.1 S): a
            `__getattr__` / `__getattribute__`, a metaclass other than abc.ABCMeta, a class decorator other
            than dataclass / total_ordering / the typing ones / attrs' class makers, or a `setattr` /
            `delattr` / `__setattr__` whose target is not the instance (`setattr(cls, name, fn)`, at
            class-body level, or a module-level `setattr(C, <not a literal>, ...)`). `setattr(self, ...)` sets
            instance DATA (`__setstate__`, a kwargs constructor): `setattr(self, "x", v)` is an unknown binding
            of field x, a non-literal name changes nothing — requests' Session restores its pickled fields that
            way and stays typed.
  functions module-level defs (a conditional redefinition gives several entries: never resolved).
  globals   module-level bindings: `x = T()` / `x: T` / `x = y` / `x = None`; any other binding form (for,
            with, augassign, tuple target, del, walrus, `global x` in a function, match capture) is unknown.
  scopes    one per def / lambda / comprehension, pre-order. A method's first parameter is SELF (`inst`;
            `class` under @classmethod, `__new__`, `__init_subclass__`, `__class_getitem__`; none for
            @staticmethod) while no binding of the scope rebinds it; a lambda, comprehension or nested def that
            does not rebind that name inherits it (its `cls` / `self_kind` say so), so `self.m()` inside a
            closure is SELF too. Locals: `x = T(...)` / `x = mod.T(...)` / `x = f(...)` (call), `x: T = ...`
            (ann), `x = cast(T, e)` (ann, typing.cast only), `x = y.z` / `x = y` (field), `x = None`
            (none), literals (field -> LITERAL); everything else unknown. POISONED: for / async for, with,
            except, walrus (in the nearest def, also from inside a comprehension), augassign, tuple / list /
            starred targets, del, global, nonlocal (also in every enclosing def), a local import, a nested def or
            class name, a match capture, a PEP 695 `type` alias. A comprehension is its own scope whose targets
            are poisoned (they shadow outer names); its FIRST iterable runs in the enclosing scope.
            Sites: every call whose callee is a name (`m(...)`, bare) or an attribute (`r.m(...)`, member) is a
            Site of the scope it EXECUTES in: a decorator, default value or annotation of a def or lambda runs
            in the enclosing scope (recorded there; at module level nowhere); a class body nested in a def runs
            in that def. `super().m()` / `super(C, self).m()` (C = the enclosing class) is SUPER only in a
            method whose SELF is valid; anywhere else super() is an ordinary call (OTHER receiver). argc = the
            positional arguments as written, -1 with a `*` argument. all_names[m] = every call of m in the
            scope's FULL textual span — decorators, defaults, annotations and nested scopes included — so a
            call the core cannot see as a site always blocks a REPLACE of that name. A bare call through an
            import alias (`from .x import m as a; a()`) also counts for the IMPORTED name m: the engine reports
            that call as a tuple named m, so a REPLACE of m (`b.m()` typed) must not remove it. So does a member
            reached without naming it in a call — the literal of `getattr(x, "m"[, d])`, `methodcaller("m")`,
            `attrgetter("m")`: requests' `getattr(jar, "copy", None)()` runs RequestsCookieJar.copy, so the engine's
            `copy` tuple (from `copy.copy(jar)`, an external call) must survive the REPLACE of `copy`.
            A def whose name is defined again in the same container (a property's getter / setter, an if / else
            pair, @overload stubs) lists the other definitions' lines in its `lines`, so every definition maps
            to the engine's ONE merged node and the core trusts none of them as a caller.
  decls     (line, UTF-8 byte column) of every def / class NAME (the textual veto skips them).
  patches   `C.attr = ...`, `del C.attr`, `setattr(C, "attr", ...)` anywhere in the file whose base name is not
            bound by the scope it is in (the core resolves C at file level; anything but a class is ignored).
            An attribute assigned through any other VARIABLE (`o = Order(); o.save = fake`, `setattr(p, "x", v)`,
            a module-level `shared.save = fake`) is never read back typed through that same variable: a receiver
            `o.save` / `o.save()` there is OTHER (`o.total()` stays typed). An assignment through another variable
            of the same class (`configure(c: Car): c.engine = FakeEngine()` while Car.go calls `self.engine.start()`)
            is NOT followed: like a subclass override, it is dispatch the static binding cannot see.

Annotations -> TypeRef (design 3.1): Name / Attribute as written; string annotations parsed in eval mode
(unparseable -> unknown; memoised per text, a plain dotted name never parsed); typing's Optional[X] -> nullable X;
Union[...] / `X | Y` -> union; Annotated[X, ...] / ClassVar[X] / Final[X] / Required / NotRequired / ReadOnly -> X;
Type[X] / type[X] -> classof X; Self -> self; any other typing form, a builtin container (list[X]), Literal,
Callable, a starred or computed annotation -> unknown. A project generic Foo[T] is a generic whose head the core
reads. Typing names are recognised by their IMPORT (typing / typing_extensions), never by spelling. A PEP 695
parameter is in the scope's type_params — a class's parameters in its methods' and nested classes' too
(`class Box[Order]: def put(self, x: Order)`) — and lowers to unknown. An annotation evaluated in a CLASS NAMESPACE
(a method's parameters and return, a class-body annotation, a nested class's base) never reads a name that class
body binds (a nested class, an attribute, a def, an import) as a module-level class: `class Tree: class Node: ...;
def walk(self, n: Node)` is unknown, never the imported Node.

The binder (PyBinder) answers module / package / named imports with the project file py_import_resolver finds
from the importer's roots (a relative import by its package path; a namespace package as an opaque token whose
submodules resolve by path), `external` (stems = the top-level name; the core's conservative stem test still
applies) when no root holds it, and unknown otherwise. `from . import sub` inside a package's own __init__ is the
SUBMODULE (flask's `from . import json as json`; `from flask import json; json.dumps()` then reaches flask.json).
One more conservative rule: a class that a PROJECT class extends (`class RequestsCookieJar(cookielib.CookieJar)`,
`class LookupDict(dict)`) is never a closed external type — its import, its module import and (for a builtin) the
free name resolve to unknown — because a value declared as that class may be a project subclass that overrides
the called method: requests' `merge_cookies(cookiejar: CookieJar)` -> `cookiejar.set_cookie()` must keep its link
to RequestsCookieJar.set_cookie (an external verdict would let a REPLACE remove it). The base is followed through
project RE-EXPORTS to the external class it names (`from .compat import CookieJar`, `from . import compat;
class R(compat.ODict)`, star imports, `X = mod.Y`); a base that cannot be followed opens every external class of
its NAME. Likewise an external module or class whose attributes project code ASSIGNS (`sys.stdout =
ThreadedStream()`, `setattr(os, "sep", s)`, `monkeypatch.setattr("json.dumps", f)`) is open: werkzeug's debug
console calls `sys.stdout._write(...)` on its own stream object. Literals stay external.

Imports only the stdlib at import time (typed_calls and py_import_resolver are siblings loaded lazily). Never
raises out of `facts()` for a parse failure (None: the core counts files_failed). Linear in the file: the cyclic
GC is paused while one file is lowered (restored after).
"""
from __future__ import annotations

import ast
import gc
import keyword
import re
from collections import Counter

READER_VERSION = "py-2"

_MAX_RECV_DEPTH = 20          # deeper receiver chains are OTHER (the IR validator rejects nesting > 32)
_MAX_TYPE_DEPTH = 12          # deeper annotations are unknown
_TYPING = frozenset({"typing", "typing_extensions"})
_TYPING_STRIP = frozenset({"Annotated", "ClassVar", "Final", "Required", "NotRequired", "ReadOnly"})
_NEUTRAL_BASES = frozenset({("builtins", "object"), ("typing", "Generic"), ("typing", "Protocol"),
                            ("typing_extensions", "Generic"), ("typing_extensions", "Protocol"),
                            ("abc", "ABC")})
_PROTOCOL_BASES = frozenset({("typing", "Protocol"), ("typing_extensions", "Protocol")})
_OK_METACLASSES = frozenset({("abc", "ABCMeta")})
_OK_CLASS_DECORATORS = frozenset({
    ("dataclasses", "dataclass"), ("functools", "total_ordering"),
    ("typing", "final"), ("typing", "runtime_checkable"), ("typing", "dataclass_transform"),
    ("typing", "type_check_only"), ("typing_extensions", "final"), ("typing_extensions", "runtime_checkable"),
    ("typing_extensions", "dataclass_transform"), ("typing_extensions", "type_check_only"),
    ("typing_extensions", "deprecated"), ("warnings", "deprecated"),
    ("attr", "s"), ("attr", "attrs"), ("attr", "define"), ("attr", "frozen"), ("attr", "mutable"),
    ("attr", "dataclass"), ("attrs", "define"), ("attrs", "frozen"), ("attrs", "mutable")})
# decorators that keep a function's return value (and so its return annotation) as written
_TRANSPARENT_DECORATORS = frozenset({
    ("builtins", "staticmethod"), ("builtins", "classmethod"), ("builtins", "property"),
    ("functools", "cached_property"), ("functools", "lru_cache"), ("functools", "cache"),
    ("abc", "abstractmethod"), ("abc", "abstractproperty"), ("typing", "overload"), ("typing", "override"),
    ("typing", "final"), ("typing_extensions", "overload"), ("typing_extensions", "override"),
    ("typing_extensions", "final")})
_IMPLICIT_CLASSMETHODS = frozenset({"__new__", "__init_subclass__", "__class_getitem__"})
_LITERALS = ((str, "str"), (bytes, "bytes"), (bool, "bool"), (int, "int"), (float, "float"), (complex, "complex"))
_DEF_NAME = re.compile(rb"(?:async\s+)?(?:def|class)\s+")
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_TRY_STAR = getattr(ast, "TryStar", None)
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)
_MATCH = getattr(ast, "Match", None)
_LEAVES = (ast.Name, ast.Constant)                       # expressions that hold no call
_NO_CALLS = (ast.expr_context, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)
# fields that never hold a node with a call (identifiers, flags, operators, contexts): _kids skips them
_NO_KID_FIELDS = frozenset({"ctx", "op", "ops", "id", "attr", "arg", "kind", "conversion", "is_async", "type_comment",
                            "level", "module"})
_KID_FIELDS: dict = {}
_AST = ast.AST
_NAME, _CONST, _CALL, _LAMBDA, _NAMED = ast.Name, ast.Constant, ast.Call, ast.Lambda, ast.NamedExpr
_COMP_TYPES = frozenset(_COMPS)
_SPECIAL = (ast.Call, ast.Lambda, ast.NamedExpr) + _COMPS + _LEAVES
_ONE_VALUE = frozenset({ast.Attribute, ast.keyword, ast.Starred, ast.Await})    # node types whose only child is .value


def _kids(x) -> list:
    """`[c for c in ast.iter_child_nodes(x) if not isinstance(c, _NO_CALLS)]` — the same nodes in the same order
    (the walk's scope order and emission order depend on it), without the generator: the walk's hot path."""
    tp = type(x)
    fields = _KID_FIELDS.get(tp)
    if fields is None:
        fields = _KID_FIELDS[tp] = tuple(f for f in getattr(tp, "_fields", ()) if f not in _NO_KID_FIELDS)
    out = []
    for f in fields:
        v = getattr(x, f, None)
        if v is None:
            continue
        if type(v) is list:
            for it in v:
                if isinstance(it, _AST) and not isinstance(it, _NO_CALLS):
                    out.append(it)
        elif isinstance(v, _AST) and not isinstance(v, _NO_CALLS):
            out.append(v)
    return out


def _ir():
    """typed_calls — the frozen IR (a sibling, imported lazily)."""
    import typed_calls  # noqa: WPS433
    return typed_calls


# ================================================================================================
# Phase A: one file -> FileFacts
# ================================================================================================

class _S:
    """One scope while the file is walked (pass 1), lowered to typed_calls.Scope in pass 2."""
    __slots__ = ("idx", "node", "kind", "name", "parent", "cls", "method", "self_name", "params", "binds",
                 "poisoned", "calls", "census", "line", "end_line", "lines", "type_params", "is_method",
                 "local_imports")

    def __init__(self, idx, node, kind, name, parent):
        self.idx, self.node, self.kind, self.name, self.parent = idx, node, kind, name, parent
        self.cls = -1                 # the class whose method this is (methods only)
        self.method = ""              # "inst" | "class" | "" (static / not a method)
        self.self_name = None         # the first parameter's name for a method with SELF
        self.params = {}              # name -> ("ann", annotation ast | None) | ("self",) ...
        self.binds = {}               # name -> [("value", ast, line) | ("ann", ast, line)]
        self.poisoned = set()
        self.calls = []               # ast.Call nodes executed in this scope
        self.census = Counter()       # call name -> count over the full span
        self.local_imports = {}       # local name -> the names a function-level import binds it to
        self.line = getattr(node, "lineno", 0) or 0
        self.end_line = getattr(node, "end_lineno", 0) or self.line
        self.lines = ()
        self.type_params = frozenset()
        self.is_method = False


class _C:
    """One class while the file is walked."""
    __slots__ = ("idx", "node", "outer", "scope", "fields", "methods", "dynamic", "body_names", "type_params")

    def __init__(self, idx, node, outer, scope):
        self.idx, self.node, self.outer, self.scope = idx, node, outer, scope
        self.fields = {}              # name -> [("value"|"ann"|"unknown", ast, line, scope)]
        self.methods = {}             # name -> [(def node, flags, keeps-its-return, is an @overload stub)]
        self.dynamic = False
        self.body_names = set()       # every name the class BODY binds (fields, defs, nested classes, imports ...)
        self.type_params = frozenset()    # PEP 695 parameters visible in the body: its own + every enclosing class's

    def hide(self) -> frozenset:
        """Names an annotation evaluated in this class's NAMESPACE (a method's signature, a class-body annotation,
        a nested class's base) cannot read as a module-level class: the body's own bindings (`class Node:` nested
        in the class shadows an imported Node) and the visible type parameters."""
        return frozenset(self.body_names) | self.type_params


class _Ctx(tuple):
    """(exec scope index or -1, class-body index or -1, span: tuple of scope indexes whose census counts here,
    record: calls here are sites)."""
    __slots__ = ()

    def __new__(cls, scope, klass, span, record=True):
        return super().__new__(cls, (scope, klass, span, record))

    scope = property(lambda s: s[0])
    klass = property(lambda s: s[1])
    span = property(lambda s: s[2])
    record = property(lambda s: s[3])


def _is_type_checking(e) -> bool:
    """Is an `if` test TYPE_CHECKING by its spelling (a name, any `x.TYPE_CHECKING`, or one operand of an `and`)?"""
    if isinstance(e, ast.Name):
        return e.id == "TYPE_CHECKING"
    if isinstance(e, ast.Attribute):
        return e.attr == "TYPE_CHECKING"
    if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.And):
        return any(_is_type_checking(v) for v in e.values)
    return False


def _call_name(call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


# A member reached WITHOUT naming it in a call (`m = getattr(jar, "copy", None); m()`, `methodcaller("save")`,
# `attrgetter("save")`): the literal name counts once in the census (no Site), so no tuple of that name is ever
# REPLACEd in that span — an engine tuple of it (requests' `_copy_cookie_jar` -> RequestsCookieJar.copy) may be the
# run-time call. Any callee spelled so counts (an over-count only vetoes a REPLACE).
_INDIRECT = {"getattr": (1,), "methodcaller": (0,), "attrgetter": None}     # name -> literal positions (None: all)


def _indirect_names(call, nm) -> list:
    pos = _INDIRECT[nm]
    args = call.args
    idx = range(len(args)) if pos is None else [p for p in pos if p < len(args)]
    out = []
    for i in idx:
        a = args[i]
        if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value:
            out.append(a.value.rsplit(".", 1)[-1])      # attrgetter("a.b") reaches b
    return out


def _target_names(t) -> list:
    """Every Name a binding target binds (tuple / list / starred unpacked)."""
    out = []
    stack = [t]
    while stack:
        x = stack.pop()
        if isinstance(x, ast.Name):
            out.append(x.id)
        elif isinstance(x, (ast.Tuple, ast.List)):
            stack.extend(x.elts)
        elif isinstance(x, ast.Starred):
            stack.append(x.value)
    return out


def _pattern_names(p) -> list:
    out = []
    for x in ast.walk(p):
        nm = getattr(x, "name", None)
        if isinstance(nm, str) and type(x).__name__ in ("MatchAs", "MatchStar"):
            out.append(nm)
        rest = getattr(x, "rest", None)
        if isinstance(rest, str) and type(x).__name__ == "MatchMapping":
            out.append(rest)
    return out


def _type_param_names(node) -> frozenset:
    return frozenset(str(getattr(tp, "name", "")) for tp in (getattr(node, "type_params", None) or ())
                     if getattr(tp, "name", None))


class _File:
    """Pass 0 (module-level names), pass 1 (structure), pass 2 (lowering) for one file."""

    def __init__(self, rel, tree, lines, T):
        self.rel, self.tree, self.lines, self.T = rel, tree, list(lines or []), T
        self.scopes: list = []
        self.classes: list = []
        self.imports: dict = {}           # local -> [Import]
        self.functions: dict = {}         # name -> [(def node, decorator flags)]
        self.globals: dict = {}           # name -> [("value"|"ann"|"unknown", ast, line)]
        self.decls: set = set()
        self.patch_raw: list = []         # (scope, base ast, attr)
        self.setattr_raw: list = []       # (scope, class-body index, call)
        self.from_origin: dict = {}       # local -> (module, name) for absolute from-imports
        self.mod_origin: dict = {}        # local -> module for `import x` / `import x.y as z`
        self.other_bound: set = set()     # module-level names bound by anything but an import
        self._str_ann: dict = {}          # string annotation text -> its parsed expression (None: unparseable)
        self._top_defs = None             # names of the top-level classes and functions (built once, lazily)
        self._lpatch: dict = {}           # (owner scope, attribute path) -> {attr}: `x.attr = ...` on a non-SELF base
        self._lpatch_attrs: frozenset = frozenset()

    # ---------------------------------------------------------------- pass 0: module-level origins
    def _origins(self):
        def visit(body):
            for st in body:
                if isinstance(st, ast.Import):
                    for a in st.names:
                        if a.asname:
                            self.mod_origin.setdefault(a.asname, set()).add(a.name)
                        else:
                            top = a.name.split(".", 1)[0]
                            self.mod_origin.setdefault(top, set()).add(top)
                elif isinstance(st, ast.ImportFrom):
                    if st.level or not st.module or st.module == "__future__":
                        for a in st.names:
                            if a.name != "*" and st.module != "__future__":
                                self.other_bound.add(a.asname or a.name)
                        continue
                    for a in st.names:
                        if a.name != "*":
                            self.from_origin.setdefault(a.asname or a.name, set()).add((st.module, a.name))
                elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.other_bound.add(st.name)
                elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                        self.other_bound.update(_target_names(t))
                if isinstance(st, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)) \
                        or (_TRY_STAR is not None and isinstance(st, _TRY_STAR)):
                    visit(getattr(st, "body", []))
                    visit(getattr(st, "orelse", []))
                    visit(getattr(st, "finalbody", []))
                    for h in getattr(st, "handlers", []) or []:
                        visit(h.body)
        visit(self.tree.body)

    def origin(self, e):
        """(module, name) a module-level Name / dotted Attribute refers to by its IMPORT (or ("builtins", n) for
        an unbound builtin name), else None. Ambiguous or locally re-bound names have no origin."""
        if isinstance(e, ast.Name):
            n = e.id
            if n in self.other_bound:
                return None
            fo, mo = self.from_origin.get(n), self.mod_origin.get(n)
            if fo and not mo and len(fo) == 1:
                return next(iter(fo))
            if not fo and not mo:
                return ("builtins", n)
            return None
        if isinstance(e, ast.Attribute):
            chain = [e.attr]
            cur = e.value
            while isinstance(cur, ast.Attribute):
                chain.insert(0, cur.attr)
                cur = cur.value
            if not isinstance(cur, ast.Name) or cur.id in self.other_bound or cur.id in self.from_origin:
                return None
            mo = self.mod_origin.get(cur.id)
            if not mo or len(mo) != 1:
                return None
            return (".".join([next(iter(mo))] + chain[:-1]), chain[-1])     # `t.Optional`, `a.b.C`
        if isinstance(e, ast.Call):
            return self.origin(e.func)
        return None

    def _is_typing(self, e, *names) -> bool:
        o = self.origin(e)
        return o is not None and o[0] in _TYPING and (not names or o[1] in names)

    # ---------------------------------------------------------------- pass 1: structure
    def _new_scope(self, node, kind, name, parent) -> _S:
        s = _S(len(self.scopes), node, kind, name, parent)
        self.scopes.append(s)
        return s

    def _census(self, ctx, call):
        nm = _call_name(call)
        if nm:
            for si in ctx.span:
                self.scopes[si].census[nm] += 1
            if nm in _INDIRECT and ctx.span:
                for lit in _indirect_names(call, nm):
                    for si in ctx.span:
                        self.scopes[si].census[lit] += 1

    def _decl(self, node):
        ln = getattr(node, "lineno", 0)
        if not 1 <= ln <= len(self.lines):
            return
        b = self.lines[ln - 1].encode("utf-8", "replace")
        m = _DEF_NAME.match(b, getattr(node, "col_offset", 0) or 0)
        if m is None:
            return
        name = node.name.encode("utf-8", "replace")
        if b[m.end():m.end() + len(name)] == name:
            self.decls.add((ln, m.end()))

    def _poison(self, ctx, names, *, nearest_def=False):
        if not names:
            return
        if ctx.klass >= 0 and not nearest_def:
            c = self.classes[ctx.klass]
            c.body_names.update(names)
            for n in names:
                c.fields.setdefault(n, []).append(("unknown", None, 0, ctx.scope))
            return
        si = ctx.scope
        if nearest_def:
            while si >= 0 and self.scopes[si].kind == "comprehension":
                si = self.scopes[si].parent
        if si < 0:
            for n in names:
                self.globals.setdefault(n, []).append(("unknown", None, 0))
            return
        self.scopes[si].poisoned.update(names)

    def _bind(self, ctx, target, value, ann, line):
        """One assignment target (`value` / `ann` as written; both None = an unreadable binding)."""
        if isinstance(target, ast.Name):
            kind = "ann" if ann is not None else ("value" if value is not None else "unknown")
            src = ann if ann is not None else value
            if ctx.klass >= 0:
                if kind == "value":
                    kind = "classvalue"      # design 3.1 R3 reads class-body ANNOTATIONS, not values (descriptors)
                elif kind == "ann":
                    kind = "classann"        # evaluated in the class namespace (its own names shadow)
                self.classes[ctx.klass].body_names.add(target.id)
                self.classes[ctx.klass].fields.setdefault(target.id, []).append((kind, src, line, ctx.scope))
            elif ctx.scope < 0:
                self.globals.setdefault(target.id, []).append((kind, src, line))
            elif kind == "unknown":
                self.scopes[ctx.scope].poisoned.add(target.id)
            else:
                self.scopes[ctx.scope].binds.setdefault(target.id, []).append((kind, src, line))
            return
        if isinstance(target, ast.Attribute):
            base = target.value
            if isinstance(base, ast.Name):
                kind = "ann" if ann is not None else ("value" if value is not None else "unknown")
                src = ann if ann is not None else value
                if ctx.scope >= 0:
                    self.scope_self_targets(ctx.scope, base.id, target.attr, kind, src, line)
                self.patch_raw.append((ctx.scope, base, target.attr))
            elif isinstance(base, ast.Attribute):
                self.patch_raw.append((ctx.scope, base, target.attr))
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                self._bind(ctx, e, None, None, line)
            return
        if isinstance(target, ast.Starred):
            self._bind(ctx, target.value, None, None, line)

    def scope_self_targets(self, si, base, attr, kind, src, line):
        """A `<name>.attr = ...` in scope si: kept until pass 2 knows whether <name> is a method's SELF."""
        self._self_raw.append((si, base, attr, kind, src, line))

    def walk(self):
        self._self_raw: list = []
        self._bare_raw: list = []         # (exec scope, span, called name) of every bare call
        self._str_patches: list = []      # ((module parts...), attr) of `monkeypatch.setattr("pkg.mod.attr", v)`
        self._origins()
        self._body(self.tree.body, _Ctx(-1, -1, ()))

    def _body(self, stmts, ctx):
        for st in stmts:
            self._stmt(st, ctx)

    def _stmt(self, st, ctx):
        line = getattr(st, "lineno", 0) or 0
        if isinstance(st, _DEFS):
            self._def(st, ctx)
            return
        if isinstance(st, ast.ClassDef):
            self._class(st, ctx)
            return
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            self._import(st, ctx)
            return
        if isinstance(st, ast.Assign):
            self._expr(st.value, ctx)
            for t in st.targets:
                self._target_exprs(t, ctx)
                self._bind(ctx, t, st.value, None, line)
            return
        if isinstance(st, ast.AnnAssign):
            if st.value is not None:
                self._expr(st.value, ctx)
            self._expr(st.annotation, _Ctx(ctx.scope, ctx.klass, ctx.span, False))
            self._target_exprs(st.target, ctx)
            self._bind(ctx, st.target, st.value, st.annotation, line)
            return
        if isinstance(st, ast.AugAssign):
            self._expr(st.value, ctx)
            self._target_exprs(st.target, ctx)
            self._bind(ctx, st.target, None, None, line)
            return
        if isinstance(st, (ast.For, ast.AsyncFor)):
            self._expr(st.iter, ctx)
            self._target_exprs(st.target, ctx)
            self._bind(ctx, st.target, None, None, line)
            self._body(st.body, ctx)
            self._body(st.orelse, ctx)
            return
        if isinstance(st, (ast.With, ast.AsyncWith)):
            for it in st.items:
                self._expr(it.context_expr, ctx)
                if it.optional_vars is not None:
                    self._target_exprs(it.optional_vars, ctx)
                    self._bind(ctx, it.optional_vars, None, None, line)
            self._body(st.body, ctx)
            return
        if isinstance(st, ast.Try) or (_TRY_STAR is not None and isinstance(st, _TRY_STAR)):
            self._body(st.body, ctx)
            for h in st.handlers:
                if h.type is not None:
                    self._expr(h.type, ctx)
                if h.name:
                    self._poison(ctx, [h.name])
                self._body(h.body, ctx)
            self._body(st.orelse, ctx)
            self._body(st.finalbody, ctx)
            return
        if isinstance(st, ast.Delete):
            for t in st.targets:
                self._target_exprs(t, ctx)
                if isinstance(t, ast.Attribute):
                    self._bind(ctx, t, None, None, line)
                else:
                    self._poison(ctx, _target_names(t))
            return
        if isinstance(st, ast.Global):
            for n in st.names:
                self.globals.setdefault(n, []).append(("unknown", None, line))
            if ctx.scope >= 0:
                self.scopes[ctx.scope].poisoned.update(st.names)
            return
        if isinstance(st, ast.Nonlocal):
            si = ctx.scope
            while si >= 0:
                self.scopes[si].poisoned.update(st.names)
                si = self.scopes[si].parent
            return
        if _MATCH is not None and isinstance(st, _MATCH):
            self._expr(st.subject, ctx)
            for case in st.cases:
                self._poison(ctx, _pattern_names(case.pattern))     # (patterns hold no calls)
                if case.guard is not None:
                    self._expr(case.guard, ctx)
                self._body(case.body, ctx)
            return
        if _TYPE_ALIAS is not None and isinstance(st, _TYPE_ALIAS):
            self._poison(ctx, [getattr(st.name, "id", "")])
            return
        for child in _kids(st):
            if isinstance(child, ast.stmt):
                self._stmt(child, ctx)
            elif isinstance(child, ast.expr):
                self._expr(child, ctx)

    def _target_exprs(self, t, ctx):
        """Calls inside a target (`a[f()] = x`, `g().attr = v`) run too."""
        if isinstance(t, ast.Name):
            return
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._target_exprs(e, ctx)
            return
        if isinstance(t, ast.Starred):
            self._target_exprs(t.value, ctx)
            return
        if isinstance(t, ast.Attribute):
            self._expr(t.value, ctx)
            return
        self._expr(t, ctx)

    def _import(self, st, ctx):
        line = st.lineno
        T = self.T
        if isinstance(st, ast.Import):
            entries = []
            for a in st.names:
                if a.asname:
                    entries.append(T.Import(a.asname, "module", a.name, "", line))
                elif "." in a.name:
                    entries.append(T.Import(a.name.split(".", 1)[0], "package", a.name, "", line))
                else:
                    entries.append(T.Import(a.name, "module", a.name, "", line))
        else:
            if st.module == "__future__":
                return
            spec = "." * (st.level or 0) + (st.module or "")
            entries = []
            for a in st.names:
                if a.name == "*":
                    entries.append(T.Import("*", "star", spec, "*", line))
                else:
                    entries.append(T.Import(a.asname or a.name, "named", spec, a.name, line))
        if ctx.scope < 0 and ctx.klass < 0:
            for imp in entries:
                self.imports.setdefault(imp.local, []).append(imp)
            return
        if ctx.scope >= 0 and ctx.klass < 0:
            for imp in entries:
                if imp.kind == "named":
                    self.scopes[ctx.scope].local_imports.setdefault(imp.local, set()).add(imp.name)
        self._poison(ctx, [imp.local for imp in entries if imp.local != "*"])

    # ---- defs, lambdas, comprehensions, classes
    def _decorators_ok(self, node) -> tuple:
        """(flags, transparent): the method flags its decorators give, and whether they keep its return."""
        flags, transparent, overload = set(), True, False
        for d in node.decorator_list:
            o = self.origin(d)
            head = d.func if isinstance(d, ast.Call) else d
            nm = o[1] if o else (head.attr if isinstance(head, ast.Attribute) else
                                 (head.id if isinstance(head, ast.Name) else ""))
            if isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter"):
                flags.add("property")          # `@x.setter`: the same attribute (its return is irrelevant)
                continue
            is_prop = "property" in nm.lower()
            if o == ("builtins", "staticmethod"):
                flags.add("static")
            elif o == ("builtins", "classmethod"):
                flags.add("class")
            if is_prop:
                flags.add("property")          # its return annotation IS the attribute's type (R3)
            if o is not None and o[0] == "abc" and nm in ("abstractmethod", "abstractproperty"):
                flags.add("abstract")
            if o is not None and o[0] in _TYPING and o[1] == "overload":
                overload = True
            if not is_prop and o not in _TRANSPARENT_DECORATORS:
                transparent = False            # an unknown decorator may change what a call returns
        return frozenset(flags), transparent, overload

    def _header(self, node, ctx, s_idx):
        """A def's / lambda's decorators, defaults and annotations: run in the ENCLOSING scope (sites there),
        counted in the def's own census too (its span starts at the first decorator)."""
        hctx = _Ctx(ctx.scope, ctx.klass, ctx.span + (s_idx,), ctx.record)
        actx = _Ctx(ctx.scope, ctx.klass, ctx.span + (s_idx,), False)      # annotations: census only
        for d in getattr(node, "decorator_list", []) or []:
            self._expr(d, hctx)
        a = node.args
        for dflt in list(a.defaults) + [x for x in a.kw_defaults if x is not None]:
            self._expr(dflt, hctx)
        if not isinstance(node, ast.Lambda):
            for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs) + [a.vararg, a.kwarg]:
                if p is not None and p.annotation is not None:
                    self._expr(p.annotation, actx)
            if node.returns is not None:
                self._expr(node.returns, actx)

    def _def(self, node, ctx):
        in_class = ctx.klass >= 0
        s = self._new_scope(node, "method" if in_class else "function", node.name, ctx.scope)
        s.lines = tuple(sorted({d.lineno for d in node.decorator_list}))
        # a method also sees its class's (and every enclosing class's) PEP 695 parameters: `class Box[Order]:
        # def put(self, x: Order)` — x is the type parameter, never the module's class Order
        s.type_params = _type_param_names(node) | (self.classes[ctx.klass].type_params if in_class else frozenset())
        self._decl(node)
        self._header(node, ctx, s.idx)
        flags, transparent, overload = self._decorators_ok(node)
        a = node.args
        pos = list(a.posonlyargs) + list(a.args)
        if in_class:
            c = self.classes[ctx.klass]
            c.body_names.add(node.name)
            s.is_method = True
            s.cls = ctx.klass
            if "static" in flags:
                s.method = ""
            elif "class" in flags or node.name in _IMPLICIT_CLASSMETHODS:
                s.method = "class"
            else:
                s.method = "inst"
            if s.method and pos:
                s.self_name = pos[0].arg
            else:
                s.method = ""
            c.methods.setdefault(node.name, []).append((node, flags, transparent, overload))
            if node.name in ("__getattr__", "__getattribute__"):
                c.dynamic = True
        else:
            if ctx.scope < 0:
                self.functions.setdefault(node.name, []).append((node, flags, transparent, overload))
            elif ctx.scope >= 0:
                self.scopes[ctx.scope].poisoned.add(node.name)
        for i, p in enumerate(pos + list(a.kwonlyargs)):
            if i == 0 and s.self_name is not None and p.arg == s.self_name:
                s.params[p.arg] = ("self",)
            else:
                s.params[p.arg] = ("ann", p.annotation)
        for p in (a.vararg, a.kwarg):
            if p is not None:
                s.params[p.arg] = ("ann", None)
        bctx = _Ctx(s.idx, -1, ctx.span + (s.idx,))
        self._body(node.body, bctx)

    def _lambda(self, node, ctx):
        s = self._new_scope(node, "lambda", "<lambda>", ctx.scope)
        self._header(node, ctx, s.idx)
        a = node.args
        for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs) + [a.vararg, a.kwarg]:
            if p is not None:
                s.params[p.arg] = ("ann", None)
        self._expr(node.body, _Ctx(s.idx, -1, ctx.span + (s.idx,)))

    def _comp(self, node, ctx):
        s = self._new_scope(node, "comprehension", "<" + type(node).__name__.lower() + ">", ctx.scope)
        inner = _Ctx(s.idx, -1, ctx.span + (s.idx,))
        for i, g in enumerate(node.generators):
            # the FIRST iterable is evaluated in the enclosing scope
            self._expr(g.iter, _Ctx(ctx.scope, ctx.klass, ctx.span + (s.idx,), ctx.record) if i == 0 else inner)
            s.poisoned.update(_target_names(g.target))
            self._target_exprs(g.target, inner)
            for cond in g.ifs:
                self._expr(cond, inner)
        if isinstance(node, ast.DictComp):
            self._expr(node.key, inner)
            self._expr(node.value, inner)
        else:
            self._expr(node.elt, inner)

    def _class(self, node, ctx):
        c = _C(len(self.classes), node, ctx.klass, ctx.scope)
        c.type_params = _type_param_names(node) | (self.classes[ctx.klass].type_params if ctx.klass >= 0
                                                   else frozenset())
        self.classes.append(c)
        self._decl(node)
        if ctx.klass < 0 and ctx.scope >= 0:
            self.scopes[ctx.scope].poisoned.add(node.name)   # (a nested class of a class is its `outer`'s)
        if ctx.klass >= 0:
            self.classes[ctx.klass].body_names.add(node.name)
        hctx = _Ctx(ctx.scope, -1, ctx.span, ctx.record)
        for d in node.decorator_list:
            self._expr(d, hctx)
            o = self.origin(d)
            if o not in _OK_CLASS_DECORATORS:
                c.dynamic = True
        for b in node.bases:
            self._expr(b, hctx)
        for kw in node.keywords:
            self._expr(kw.value, hctx)
            if kw.arg == "metaclass" and self.origin(kw.value) not in _OK_METACLASSES:
                c.dynamic = True
        self._body(node.body, _Ctx(ctx.scope, c.idx, ctx.span, ctx.record))

    # ---- expressions
    def _expr(self, e, ctx):
        """Every call / lambda / comprehension / walrus of expression e, pre-order, children in field order (a
        parsed tree's node types are exact: the walk's hot path tests them by identity)."""
        if e is None:
            return
        stack = [e]
        pop, push = stack.pop, stack.append
        while stack:
            x = pop()
            tp = type(x)
            if tp is _NAME or tp is _CONST:
                continue
            # the commonest node types push their children directly (reversed: the first is walked first); a Name /
            # Constant child is never pushed (it would only be popped and skipped)
            if tp is _CALL:
                self._on_call(x, ctx)
                for k in reversed(x.keywords):
                    push(k)
                for a in reversed(x.args):
                    ta = type(a)
                    if ta is not _NAME and ta is not _CONST:
                        push(a)
                f = x.func
                tf = type(f)
                if tf is not _NAME and tf is not _CONST:
                    push(f)
                continue
            if tp in _ONE_VALUE:                       # Attribute / keyword / Starred / Await: `.value` only
                v = x.value
                tv = type(v)
                if v is not None and tv is not _NAME and tv is not _CONST:
                    push(v)
                continue
            if tp is _LAMBDA:
                self._lambda(x, ctx)
                continue
            elif tp in _COMP_TYPES:
                self._comp(x, ctx)
                continue
            elif tp is _NAMED:
                self._poison(ctx, [x.target.id] if isinstance(x.target, ast.Name) else [], nearest_def=True)
                stack.append(x.value)
                continue
            elif isinstance(x, _SPECIAL):              # a subclass of one of those (never a parsed node)
                if isinstance(x, ast.Lambda):
                    self._lambda(x, ctx)
                    continue
                if isinstance(x, _COMPS):
                    self._comp(x, ctx)
                    continue
                if isinstance(x, ast.NamedExpr):
                    self._poison(ctx, [x.target.id] if isinstance(x.target, ast.Name) else [], nearest_def=True)
                    stack.append(x.value)
                    continue
                if isinstance(x, ast.Call):
                    self._on_call(x, ctx)
                else:
                    continue                           # a leaf
            for k in reversed(_kids(x)):
                tk = type(k)
                if tk is not _NAME and tk is not _CONST:
                    push(k)

    def _on_call(self, x, ctx):
        self._census(ctx, x)
        if isinstance(x.func, ast.Name):
            self._bare_raw.append((ctx.scope, ctx.span, x.func.id))
        if ctx.record and ctx.scope >= 0:
            self.scopes[ctx.scope].calls.append(x)
        fn = _call_name(x)
        if fn in ("setattr", "delattr", "__setattr__", "__delattr__"):
            self.setattr_raw.append((ctx.scope, ctx.klass, x))

    # ---------------------------------------------------------------- pass 2: lowering
    def _self_of(self, si, name):
        """(class index, kind) when `name` in scope si is a method's SELF, else None (the nearest binding wins)."""
        s = si
        while s >= 0:
            sc = self.scopes[s]
            if sc.is_method and sc.self_name == name and self._self_valid(sc):
                return sc.cls, sc.method
            if name in sc.params or name in sc.binds or name in sc.poisoned:
                return None
            s = sc.parent
        return None

    @staticmethod
    def _self_valid(sc) -> bool:
        n = sc.self_name
        return bool(sc.method) and n is not None and n not in sc.binds and n not in sc.poisoned

    def _scope_self(self, sc):
        """(cls, self_kind) of a scope in the IR: a method's own, or (for a lambda / comprehension / nested def
        that does not rebind it) the nearest enclosing method's."""
        if sc.is_method:
            return (sc.cls, sc.method) if self._self_valid(sc) else (sc.cls, "")
        p = sc.parent
        while p >= 0:
            ps = self.scopes[p]
            if ps.is_method:
                n = ps.self_name
                if n is not None and self._self_valid(ps) and self._self_of(sc.idx, n) == (ps.cls, ps.method):
                    return ps.cls, ps.method
                return -1, ""
            p = ps.parent
        return -1, ""

    def _bound_in_chain(self, si, name) -> bool:
        s = si
        while s >= 0:
            sc = self.scopes[s]
            if name in sc.params or name in sc.binds or name in sc.poisoned:
                return True
            s = sc.parent
        return False

    def _module_name(self, si, name) -> bool:
        """`name` here is a module-level `import x` / `import x.y as z` binding and nothing else."""
        if self._bound_in_chain(si, name) or name in self.other_bound or name in self.from_origin:
            return False
        imps = self.imports.get(name) or []
        if self._top_defs is None:          # once per file (it was a scan of every class per receiver)
            self._top_defs = frozenset(c.node.name for c in self.classes if c.outer < 0 and c.scope < 0) \
                | frozenset(self.functions)
        return bool(imps) and all(i.kind in ("module", "package") for i in imps) and name not in self.globals \
            and name not in self._top_defs

    @staticmethod
    def _path(e):
        """(name, attr, ...) of a Name / dotted Attribute chain, else None."""
        parts = []
        while isinstance(e, ast.Attribute):
            parts.append(e.attr)
            e = e.value
        if not isinstance(e, ast.Name):
            return None
        parts.append(e.id)
        return tuple(reversed(parts))

    def _attr_owner(self, si, name) -> int:
        """The index of the scope whose binding `name` denotes in scope si (-1: the module)."""
        s = si
        while s >= 0:
            sc = self.scopes[s]
            if name in sc.params or name in sc.binds or name in sc.poisoned:
                return s
            s = sc.parent
        return -1

    def _local_patches(self):
        """`x.attr = ...` / `del x.attr` / `setattr(x, "attr", ...)` on anything but the method's own SELF
        (`self.attr = ...` is an R3 field binding): {(the scope whose x it is, x's attribute path): {attr}}. Reading
        that attribute back through the same variable is never typed (`o = Order(); o.save = fake; o.save()`)."""
        out: dict = {}
        for si, base, attr in self.patch_raw:
            path = self._path(base)
            if path is None:
                continue
            if len(path) == 1 and si >= 0 and self._self_of(si, path[0]) is not None:
                continue
            owner = self._attr_owner(si, path[0]) if si >= 0 else -1
            out.setdefault((owner, path), set()).add(attr)
        self._lpatch = out
        self._lpatch_attrs = frozenset(a for v in out.values() for a in v)

    def _patched(self, si, value, attr) -> bool:
        """`value.attr` reads an attribute this file assigns through the same variable (see _local_patches)."""
        if attr not in self._lpatch_attrs:
            return False
        path = self._path(value)
        if path is None:
            return False
        owner = self._attr_owner(si, path[0]) if si >= 0 else -1
        return attr in self._lpatch.get((owner, path), ())

    def recv(self, e, si, depth=0):
        T = self.T
        if depth > _MAX_RECV_DEPTH:
            return T.R_OTHER
        if isinstance(e, ast.Name):
            ir_cls, ir_kind = self._ir_self.get(si, (-1, ""))
            got = self._self_of(si, e.id) if si >= 0 else None
            if got is not None and got == (ir_cls, ir_kind) and ir_kind:
                return T.R_SELF
            if self._module_name(si, e.id):
                return T.r_module(e.id)
            return T.r_ident(e.id)
        if isinstance(e, ast.Attribute):
            if self._patched(si, e.value, e.attr):
                return T.R_OTHER
            return T.r_field(self.recv(e.value, si, depth + 1), e.attr)
        if isinstance(e, ast.Call):
            if self._is_super(e, si):
                return T.R_SUPER
            r = self.call_recv(e, si, depth + 1)
            return r if r is not None else T.R_OTHER
        if isinstance(e, ast.Constant):
            for typ, nm in _LITERALS:
                if type(e.value) is typ:
                    return T.r_literal(nm)
            return T.R_OTHER
        if isinstance(e, ast.JoinedStr):
            return T.r_literal("str")
        if isinstance(e, (ast.List, ast.ListComp)):
            return T.r_literal("list")
        if isinstance(e, (ast.Dict, ast.DictComp)):
            return T.r_literal("dict")
        if isinstance(e, (ast.Set, ast.SetComp)):
            return T.r_literal("set")
        if isinstance(e, ast.Tuple):
            return T.r_literal("tuple")
        return T.R_OTHER

    def call_recv(self, call, si, depth=0):
        """CALL(...) for a call whose callee is a name or an attribute, else None."""
        T = self.T
        f = call.func
        if isinstance(f, ast.Name):
            return T.r_call(None, f.id)
        if isinstance(f, ast.Attribute):
            if self._patched(si, f.value, f.attr):
                return T.R_OTHER
            return T.r_call(self.recv(f.value, si, depth + 1), f.attr)
        return None

    def _is_super(self, call, si) -> bool:
        """`super()` / `super(C, self)` inside a method whose SELF is valid (C = that method's class)."""
        if not (isinstance(call.func, ast.Name) and call.func.id == "super") or si < 0:
            return False
        if self._bound_in_chain(si, "super") or "super" in self.other_bound or "super" in self.imports:
            return False
        sc = self.scopes[si]
        if not sc.is_method or not self._self_valid(sc) or call.keywords:
            return False
        if not call.args:
            return True
        if len(call.args) == 2 and isinstance(call.args[0], ast.Name) and isinstance(call.args[1], ast.Name):
            return call.args[0].id == self.classes[sc.cls].node.name and call.args[1].id == sc.self_name
        return False

    def _parse_str_ann(self, text):
        """A string annotation's expression (memoised per text: a big file repeats the same few strings), or None
        when it does not parse."""
        if text not in self._str_ann:
            t = text.strip()
            parts = t.split(".")
            if t.isascii() and all(p.isidentifier() and not keyword.iskeyword(p) for p in parts):
                node = ast.Name(id=parts[0], ctx=ast.Load())        # `"Order"` / `"app.models.Order"`: no parse
                for p in parts[1:]:
                    node = ast.Attribute(value=node, attr=p, ctx=ast.Load())
                self._str_ann[text] = node
            else:
                try:
                    self._str_ann[text] = ast.parse(t, mode="eval").body
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    self._str_ann[text] = None
        return self._str_ann[text]

    def ann(self, e, depth=0, hide=frozenset()):
        """An annotation expression -> TypeRef (see the module docstring). `hide`: names the annotation's own
        namespace binds (the class body it is evaluated in, the visible type parameters) — such a name is never a
        module-level class: unknown."""
        T = self.T
        if e is None:
            return None
        if depth > _MAX_TYPE_DEPTH:
            return T.T_UNKNOWN
        if isinstance(e, ast.Constant):
            if e.value is None:
                return T.t_name("None")
            if isinstance(e.value, str):
                body = self._parse_str_ann(e.value)
                return T.T_UNKNOWN if body is None else self.ann(body, depth + 1, hide)
            return T.T_UNKNOWN
        if hide:
            head = e.value if isinstance(e, ast.Subscript) else e
            while isinstance(head, ast.Attribute):
                head = head.value
            if isinstance(head, ast.Name) and head.id in hide:
                return T.T_UNKNOWN
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
            members = []
            for side in (e.left, e.right):
                t = self.ann(side, depth + 1, hide)
                members.extend(t.args if t is not None and t.kind == "union" else [t])
            return T.t_union(*[m if m is not None else T.T_UNKNOWN for m in members])
        if isinstance(e, (ast.Name, ast.Attribute)):
            o = self.origin(e)
            if o is not None and o[0] in _TYPING:
                return T.t_self() if o[1] == "Self" else T.T_UNKNOWN
            if isinstance(e, ast.Name):
                return T.t_name(e.id)
            parts = []
            cur = e
            while isinstance(cur, ast.Attribute):
                parts.insert(0, cur.attr)
                cur = cur.value
            if not isinstance(cur, ast.Name):
                return T.T_UNKNOWN
            return T.t_qual(cur.id, *parts)
        if isinstance(e, ast.Subscript):
            head = e.value
            sl = e.slice
            if type(sl).__name__ == "Index":                  # Python < 3.9 (never in range; harmless)
                sl = getattr(sl, "value", sl)
            elts = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
            o = self.origin(head)
            if o is not None and o[0] in _TYPING:
                nm = o[1]
                if nm == "Optional":
                    return T.t_nullable(self.ann(elts[0], depth + 1, hide) or T.T_UNKNOWN)
                if nm == "Union":
                    return T.t_union(*[self.ann(x, depth + 1, hide) or T.T_UNKNOWN for x in elts])
                if nm in _TYPING_STRIP:
                    return self.ann(elts[0], depth + 1, hide) if elts else T.T_UNKNOWN
                if nm == "Type":
                    return T.t_classof(self.ann(elts[0], depth + 1, hide) or T.T_UNKNOWN)
                return T.T_UNKNOWN                            # List / Dict / Callable / Literal / Iterator ...
            if o == ("builtins", "type"):
                return T.t_classof(self.ann(elts[0], depth + 1, hide) or T.T_UNKNOWN)
            if o is not None and o[0] == "builtins":
                return T.T_UNKNOWN                            # list[X] / dict[...] / tuple[...]: containers
            h = self.ann(head, depth + 1, hide)
            if h is None or h.kind not in ("name", "qualified"):
                return T.T_UNKNOWN
            return T.t_generic(h, *[self.ann(x, depth + 1, hide) or T.T_UNKNOWN for x in elts])
        return T.T_UNKNOWN

    def binding(self, kind, src, line, scope, hide=frozenset()):
        """One written binding -> typed_calls.Binding (`hide`: a class-body annotation's namespace, see ann)."""
        T = self.T
        if kind == "unknown":
            return T.Binding("unknown", line=line, scope=scope)
        if kind in ("ann", "classann"):
            return T.Binding("ann", self.ann(src, hide=hide if kind == "classann" else frozenset()) or T.T_UNKNOWN,
                             line=line, scope=scope)
        v = src
        if isinstance(v, ast.Constant) and v.value is None:
            return T.Binding("none", line=line, scope=scope)
        if kind == "classvalue":
            # a class-body value: `f = None` (nullable, above) and a literal keep their meaning; anything else
            # (`f = Descriptor()`, `f = other`, `f = property(g)`) may be a descriptor whose instance access returns
            # something else — unknown, never an R3 source
            if isinstance(v, (ast.Constant, ast.JoinedStr)):
                r = self.recv(v, scope)
                if r.kind == "LITERAL":
                    return T.Binding("field", value=r, line=line, scope=scope)
            return T.Binding("unknown", line=line, scope=scope)
        if isinstance(v, ast.Call):
            if self._is_typing(v.func, "cast") and v.args:
                return T.Binding("ann", self.ann(v.args[0]) or T.T_UNKNOWN, line=line, scope=scope)
            r = self.call_recv(v, scope)
            if r is None:
                return T.Binding("unknown", line=line, scope=scope)
            return T.Binding("call", value=r, line=line, scope=scope)
        if isinstance(v, (ast.Name, ast.Attribute, ast.Constant, ast.JoinedStr, ast.List, ast.ListComp, ast.Dict,
                          ast.DictComp, ast.Set, ast.SetComp, ast.Tuple)):
            r = self.recv(v, scope)
            if r.kind == "OTHER":
                return T.Binding("unknown", line=line, scope=scope)
            return T.Binding("field", value=r, line=line, scope=scope)
        return T.Binding("unknown", line=line, scope=scope)

    def _setattrs(self):
        """setattr / delattr / __setattr__ / __delattr__: on the INSTANCE (`self`) they set data — a literal name
        is an unknown binding of that field, any other name changes nothing; on a class (`cls` in a classmethod,
        `type(self)`, `self.__class__`, a top-level class of this file by name with a non-literal attribute, or
        anything at class-body level) the class is DYNAMIC; `setattr(C, "attr", ...)` on a top-level class is a
        patch of that attribute only."""
        top_classes = {c.node.name: c for c in self.classes if c.outer < 0 and c.scope < 0}
        for si, ck, call in self.setattr_raw:
            fn = _call_name(call)
            args = list(call.args)
            if isinstance(call.func, ast.Attribute) and fn in ("__setattr__", "__delattr__"):
                base = call.func.value
                unbound = isinstance(base, ast.Name) and base.id in ("object", "type") \
                    or (isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "super")
                if not unbound:
                    args = [base] + args                 # `x.__setattr__(name, v)`: the target is x
            elif fn not in ("setattr", "delattr"):
                continue
            elif isinstance(call.func, ast.Attribute):
                # `monkeypatch.setattr(target, "attr", v)` / `monkeypatch.setattr("pkg.mod.attr", v)`: a patch of
                # that attribute (the binder treats a patched EXTERNAL module / class as open)
                tgt = args[0] if args else None
                if isinstance(tgt, ast.Constant) and isinstance(tgt.value, str) and "." in tgt.value:
                    parts = tgt.value.split(".")
                    if all(p.isidentifier() for p in parts):
                        self._str_patches.append((tuple(parts[:-1]), parts[-1]))
                    continue
            tgt = args[0] if args else None
            lit = args[1] if len(args) > 1 else None
            lit_name = lit.value if isinstance(lit, ast.Constant) and isinstance(lit.value, str) else None
            owner = None
            if isinstance(tgt, ast.Name) and si >= 0 and self._self_of(si, tgt.id) is not None:
                cls_idx, kind = self._self_of(si, tgt.id)
                if kind == "inst":
                    if lit_name is not None:
                        self.classes[cls_idx].fields.setdefault(lit_name, []).append(("unknown", None,
                                                                                      call.lineno, si))
                    continue
                owner = self.classes[cls_idx]            # `setattr(cls, ...)` in a classmethod
            elif isinstance(tgt, (ast.Call, ast.Attribute)) and si >= 0:
                inner = tgt.args[0] if isinstance(tgt, ast.Call) and tgt.args and isinstance(tgt.func, ast.Name) \
                    and tgt.func.id == "type" else (tgt.value if isinstance(tgt, ast.Attribute)
                                                    and tgt.attr == "__class__" else None)
                if isinstance(inner, ast.Name) and self._self_of(si, inner.id) is not None:
                    owner = self.classes[self._self_of(si, inner.id)[0]]      # type(self) / self.__class__
            elif isinstance(tgt, ast.Name) and tgt.id in top_classes and not self._bound_in_chain(si, tgt.id):
                if lit_name is not None:
                    self.patch_raw.append((si, tgt, lit_name))
                    continue
                owner = top_classes[tgt.id]
            elif isinstance(tgt, (ast.Name, ast.Attribute)) and lit_name is not None:
                self.patch_raw.append((si, tgt, lit_name))   # `setattr(sys, "stdout", s)`: a file-level name's attr
                continue
            if owner is None and ck >= 0:
                owner = self.classes[ck]                 # at class-body level: the class being built
            if owner is not None:
                owner.dynamic = True

    def _arity(self, node, drop_first, instance, transparent):
        a = node.args
        if not transparent or a.vararg is not None:
            return (0, -1)
        n = len(a.posonlyargs) + len(a.args) - (1 if drop_first and (a.posonlyargs or a.args) else 0)
        return (0, n + (1 if instance else 0))       # `Base.m(self, ...)` passes the instance explicitly

    def _same_name_lines(self, defs) -> dict:
        """{def node: the lines of the OTHER defs of the same name in the same container}."""
        out = {}
        if len(defs) > 1:
            for d in defs:
                out[id(d)] = tuple(sorted({x.lineno for x in defs if x is not d}
                                          | {dl.lineno for x in defs if x is not d for dl in x.decorator_list}))
        return out

    def _alias_census(self):
        """A bare call through an import alias (`from .x import m as a; a()`) reaches the engine under the
        IMPORTED name m (graphify resolves the alias: `a()` becomes a tuple named `m`), so it counts in the census
        of m too — a REPLACE of m then never removes the tuple that call produced."""
        for si, span, name in self._bare_raw:
            originals = set()
            s = si
            while s >= 0:
                sc = self.scopes[s]
                if name in sc.local_imports:
                    originals |= sc.local_imports[name]
                    break
                if name in sc.params or name in sc.binds or name in sc.poisoned:
                    break
                s = sc.parent
            else:
                originals = {imp.name for imp in self.imports.get(name, ()) if imp.kind == "named"}
            for orig in sorted(originals - {name}):
                for sc_idx in span:
                    self.scopes[sc_idx].census[orig] += 1

    def _sig_hide(self, s) -> frozenset:
        """The names a def's signature cannot read as module-level classes: its own PEP 695 parameters (and, for a
        method, the class body's names and the class's visible parameters — a signature runs in the class
        namespace)."""
        return s.type_params | (self.classes[s.cls].hide() if s.is_method and s.cls >= 0 else frozenset())

    def lower(self):
        T = self.T
        self._alias_census()
        # same-named defs in one container share their lines (the engine merges them into ONE node)
        extra: dict = {}
        containers: dict = {}
        for s in self.scopes:
            if s.kind in ("function", "method"):
                key = ("c", s.cls) if s.is_method else ("s", s.parent)
                containers.setdefault((key, s.name), []).append(s.node)
        for defs in containers.values():
            extra.update(self._same_name_lines(defs))
        self._ir_self = {s.idx: self._scope_self(s) for s in self.scopes}
        self._setattrs()                     # (adds patch_raw entries the receivers below consult)
        self._local_patches()
        scopes = []
        for s in self.scopes:
            ir_cls, ir_kind = self._ir_self[s.idx]
            params = {}
            hide = self._sig_hide(s)
            for name, p in s.params.items():
                if p[0] == "self":
                    t = None
                    if self._self_valid(s):
                        t = T.t_self() if s.method == "inst" else T.t_classof(T.t_self())
                    params[name] = T.Binding("param", t, line=s.line, scope=s.idx)
                else:
                    params[name] = T.Binding("param", self.ann(p[1], hide=hide) if p[1] is not None else None,
                                             line=s.line, scope=s.idx)
            locals_ = {}
            for name, bl in s.binds.items():
                if name in s.poisoned:
                    continue
                locals_[name] = [self.binding(k, src, ln, s.idx) for k, src, ln in bl]
            sites = []
            for call in s.calls:
                site = self.site(call, s.idx)
                if site is not None:
                    sites.append(site)
            sites.sort(key=lambda x: (x.line, x.col))
            lines = tuple(sorted(set(s.lines) | set(extra.get(id(s.node), ()))))
            scopes.append(T.Scope(
                name=s.name, line=s.line, end_line=s.end_line, lines=lines, parent=s.parent,
                cls=ir_cls, self_kind=ir_kind, params=params, locals=locals_, poisoned=frozenset(s.poisoned),
                sites=sites, all_names=dict(sorted(s.census.items())), type_params=s.type_params,
                kind=s.kind))
        # fields: `self.f` bindings once SELF is known
        for (si, base, attr, kind, src, line) in self._self_raw:
            got = self._self_of(si, base)
            if got is None:
                continue
            self.classes[got[0]].fields.setdefault(attr, []).append((kind, src, line, si))
        classes = []
        typing_only = self._typing_only()
        for c in self.classes:
            n = c.node
            fields = {}
            chide = c.hide()
            for name, bl in c.fields.items():
                fields[name] = [self.binding(k, src, ln, sc, chide) for k, src, ln, sc in bl]
            methods = {}
            for name, defs in c.methods.items():
                impl = [d for d in defs if not d[3]]
                use = impl if impl else defs             # @overload stubs: only when there is no implementation
                mfs = []
                for node, flags, transparent, _ov in use:
                    is_async = isinstance(node, ast.AsyncFunctionDef)
                    static = "static" in flags
                    klass = "class" in flags or node.name in _IMPLICIT_CLASSMETHODS
                    ret = None if (is_async or not transparent or node.returns is None) \
                        else self.ann(node.returns, hide=chide | _type_param_names(node))
                    mfs.append(T.MethodFacts(
                        line=node.lineno, flags=flags, returns=ret,
                        arity=self._arity(node, not static, not static and not klass, transparent),
                        lines=tuple(sorted({d.lineno for d in node.decorator_list}))))
                methods[name] = mfs
            bases = []
            bhide = self.classes[c.outer].hide() if c.outer >= 0 else frozenset()   # bases run in the outer body
            for b in n.bases:
                t = self._base(b, bhide)
                if t is not None:
                    bases.append(t)
            # a typing Protocol is STRUCTURAL: a class that matches it need not inherit from it, so the core can
            # never know what a call on a Protocol-typed receiver dispatches to (kind "interface", like TS); a class
            # written under `if TYPE_CHECKING:` is TYPING-ONLY (it never exists at run time: its own members never
            # run, and a value annotated with it is something else — flask's `app_ctx: AppContextProxy =
            # LocalProxy(...)`), an interface too
            protocol = any(self._is_protocol(b, bhide) for b in n.bases)
            classes.append(T.ClassFacts(
                name=n.name, line=n.lineno, end_line=getattr(n, "end_lineno", n.lineno) or n.lineno,
                kind="interface" if protocol or id(n) in typing_only else "class", bases=tuple(bases), fields=fields,
                methods=methods,
                dynamic=bool(c.dynamic),
                open=False, lines=tuple(sorted({d.lineno for d in n.decorator_list})), outer=c.outer,
                scope=c.scope, type_params=c.type_params))
        functions = {}
        for name, defs in self.functions.items():
            impl = [d for d in defs if not d[3]]
            use = impl if impl else defs
            for node, flags, transparent, _ov in use:
                is_async = isinstance(node, ast.AsyncFunctionDef)
                ret = None if (is_async or not transparent or node.returns is None) \
                    else self.ann(node.returns, hide=_type_param_names(node))
                functions.setdefault(name, []).append(T.FunctionFacts(
                    name=name, line=node.lineno, returns=ret,
                    lines=tuple(sorted({d.lineno for d in node.decorator_list})),
                    arity=self._arity(node, False, False, transparent)))
        globals_ = {}
        for name, bl in self.globals.items():
            globals_[name] = [self.binding(k, src, ln, -1) for k, src, ln in bl]
        patches = set()
        for si, base, attr in self.patch_raw:
            if isinstance(base, ast.Name):
                if si >= 0 and (self._bound_in_chain(si, base.id) or self._self_of(si, base.id) is not None):
                    continue
                patches.add((T.t_name(base.id), attr))
            else:
                parts = []
                cur = base
                while isinstance(cur, ast.Attribute):
                    parts.insert(0, cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name) and not (si >= 0 and self._bound_in_chain(si, cur.id)):
                    patches.add((T.t_qual(cur.id, *parts), attr))
        for parts, attr in self._str_patches:
            patches.add((T.t_qual(*parts) if len(parts) > 1 else T.t_name(parts[0]), attr))
        return T.FileFacts(
            rel=self.rel, lang="python", imports=self.imports, classes=classes, functions=functions,
            globals=globals_, scopes=scopes, decls=frozenset(self.decls),
            patches=sorted(patches, key=lambda p: (p[0].kind, p[0].name, p[0].parts, p[1])),
            reader_version=READER_VERSION)

    def _typing_only(self) -> frozenset:
        """id() of every ClassDef written in the body of an `if TYPE_CHECKING:` (anywhere: module level, a try, a
        def or a class body; classes nested in it too) — a class only type checkers see. The test is read by its
        SPELLING (`TYPE_CHECKING`, `typing.TYPE_CHECKING`, `t.TYPE_CHECKING`, a project's own `TYPE_CHECKING =
        False`, or one operand of an `and`): the body never runs. Its else branch, `if not TYPE_CHECKING:` and
        `TYPE_CHECKING or ...` do run. A statement-level walk, skipped when the text never names TYPE_CHECKING."""
        if self.lines and not any("TYPE_CHECKING" in ln for ln in self.lines):
            return frozenset()
        out = set()
        stack = [(self.tree.body, False)]
        while stack:
            body, inside = stack.pop()
            for st in body:
                if isinstance(st, ast.ClassDef):
                    if inside:
                        out.add(id(st))
                    stack.append((st.body, inside))
                elif isinstance(st, ast.If):
                    stack.append((st.body, inside or _is_type_checking(st.test)))
                    stack.append((st.orelse, inside))
                else:
                    for f in ("body", "orelse", "finalbody"):
                        b = getattr(st, f, None)
                        if isinstance(b, list):
                            stack.append((b, inside))
                    for h in getattr(st, "handlers", None) or ():
                        stack.append((h.body, inside))
                    for case in getattr(st, "cases", None) or ():
                        stack.append((case.body, inside))
        return frozenset(out)

    def _is_protocol(self, b, hide=frozenset()) -> bool:
        """Is the written base typing / typing_extensions `Protocol` (or `Protocol[T]`)?"""
        head = b.value if isinstance(b, ast.Subscript) else b
        path = self._path(head)
        if path is not None and path[0] in hide:
            return False
        o = self.origin(head) if isinstance(head, (ast.Name, ast.Attribute)) else None
        return o in _PROTOCOL_BASES

    def _base(self, b, hide=frozenset()):
        """A written base -> TypeRef, or None for a neutral base (`hide`: the enclosing class body's names, for a
        nested class)."""
        T = self.T
        head = b.value if isinstance(b, ast.Subscript) else b
        path = self._path(head)
        if path is not None and path[0] in hide:
            return T.T_UNKNOWN
        o = self.origin(head) if isinstance(head, (ast.Name, ast.Attribute)) else None
        if o in _NEUTRAL_BASES:
            return None
        if isinstance(b, (ast.Name, ast.Attribute, ast.Subscript)):
            t = self.ann(b, hide=hide)
            return t if t is not None and t.kind in ("name", "qualified", "generic") else T.T_UNKNOWN
        return T.T_UNKNOWN

    def site(self, call, si):
        """One call -> typed_calls.Site (None when its callee is neither a name nor an attribute)."""
        T = self.T
        f = call.func
        argc = -1 if any(isinstance(a, ast.Starred) for a in call.args) else len(call.args)
        if isinstance(f, ast.Name):
            return T.Site(call.lineno, f.col_offset, f.id, None, "bare", argc)
        if isinstance(f, ast.Attribute):
            line = getattr(f, "end_lineno", call.lineno) or call.lineno
            col = (getattr(f, "end_col_offset", 0) or 0) - len(f.attr.encode("utf-8"))
            if isinstance(f.value, ast.Call) and self._is_super(f.value, si):
                r = T.R_SUPER
            elif self._patched(si, f.value, f.attr):
                r = T.R_OTHER                    # `o.save = fake; o.save()`: the assigned attribute, never typed
            else:
                r = self.recv(f.value, si)
            return T.Site(line, max(col, 0), f.attr, r, "member", argc)
        return None


def file_facts(rel, tree, lines, T=None):
    """Phase A for one Python file: (rel, its ast.Module, its engine-numbered lines) -> typed_calls.FileFacts.
    The cyclic garbage collector is paused meanwhile (restored after; nothing here builds a reference cycle): each
    allocation-heavy step (a string annotation's parse) would otherwise re-scan the file's whole live AST, which made
    one 18k-line file cost seconds."""
    T = T or _ir()
    paused = gc.isenabled()
    if paused:
        gc.disable()
    try:
        f = _File(rel, tree, lines, T)
        f.walk()
        return f.lower()
    finally:
        if paused:
            gc.enable()


# ================================================================================================
# The binder: imports by IMPORT ROOT (py_import_resolver), never by suffix
# ================================================================================================

_NS = "\x00py-ns\x00"            # a namespace-package token (never a path: NUL is in no file name)


def _norm_rel(p) -> str:
    """A root-relative POSIX path as py_import_resolver writes it."""
    s = str(p or "").replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.strip("/")


def _ns_token(dirs) -> str:
    return _NS + "|".join(dirs)


def _ns_dirs(token):
    return tuple(token[len(_NS):].split("|")) if isinstance(token, str) and token.startswith(_NS) else None


_REEXPORT_DEPTH = 6              # == typed_calls.REEXPORT_DEPTH: files followed through re-exports


def _file_binds(ff, name) -> bool:
    """File `ff` binds `name` at module level (a class, a function, a global or an import; a star import is not
    counted)."""
    return (any(c.name == name and c.outer < 0 and c.scope < 0 for c in ff.classes) or name in ff.functions
            or name in ff.globals or name in ff.imports)


def _recv_path(r):
    """(name, attr, ...) of an IDENT / MODULE / FIELD-chain receiver, else None."""
    parts = []
    while r is not None and r.kind == "FIELD":
        parts.append(r.name)
        r = r.inner
    if r is None or r.kind not in ("IDENT", "MODULE"):
        return None
    parts.append(r.name)
    return tuple(reversed(parts))


class _NoFacts:
    classes, functions, globals, imports = (), {}, {}, {}


_EMPTY = _NoFacts()


class _BaseOrigins:
    """Where a project class's written base really comes from: the EXTERNAL (module, name) pairs it may denote,
    followed through project re-exports — a relative or absolute named import, a module attribute (`compat.ODict`
    after `from . import compat`), a package's submodule, a star import, a module-level alias `X = mod.Y` — up to
    _REEXPORT_DEPTH files, cycle-safe. `ok` is False when some path could not be followed (an ambiguous or
    off-graph module, a computed value): the caller then opens the base's NAME (conservative)."""

    def __init__(self, facts, roots):
        self.facts, self.roots, self.memo = facts, roots, {}

    def name(self, rel, parts, depth=0, seen=frozenset()) -> tuple:
        """(pairs, ok) for the dotted name `parts` as file `rel` reads it at module level."""
        key = (rel, parts)
        if key in self.memo:
            return self.memo[key]
        if key in seen or depth > _REEXPORT_DEPTH:
            return frozenset(), False
        seen = seen | {key}
        ff = self.facts.get(rel)
        if ff is None or getattr(ff, "lang", "") != "python" or not parts:
            return frozenset(), False
        head, rest = parts[0], parts[1:]
        out, ok, found = set(), True, False
        if any(c.name == head and c.outer < 0 and c.scope < 0 for c in ff.classes) or head in ff.functions:
            found = True                                  # a project class (or a nested one): nothing external
        for imp in ff.imports.get(head, ()):
            found = True
            e, k = self._import(rel, imp, rest, depth, seen)
            out |= e
            ok = ok and k
        for b in ff.globals.get(head, ()):
            found = True
            p = _recv_path(b.value) if b.kind == "field" else None
            if p is not None:
                e, k = self.name(rel, p + rest, depth + 1, seen)
                out |= e
                ok = ok and k
            elif b.kind != "none":
                ok = False                                # `Base = declarative_base()`: not followed
        if not found:
            stars = ff.imports.get("*", ())
            for imp in stars:
                e, k = self._star(rel, imp, parts, depth, seen)
                out |= e
                ok = ok and k
            if not rest:
                out.add(("builtins", head))               # an unbound name: a builtin (or what a star import gives)
            elif not stars:
                ok = False
        got = (frozenset(out), ok)
        self.memo[key] = got
        return got

    def _import(self, rel, imp, rest, depth, seen) -> tuple:
        spec = str(imp.spec or "")
        level = len(spec) - len(spec.lstrip("."))
        dotted = spec[level:]
        if imp.kind in ("module", "package"):
            if level or not dotted:
                return frozenset(), False
            mod = dotted if imp.kind == "module" else dotted.split(".", 1)[0]
            return self.module(rel, mod, rest, depth, seen)
        if imp.kind != "named":
            return frozenset(), False
        tgt = self.roots.module(dotted, rel, level)
        if tgt.kind in ("file", "namespace"):
            return self._attr(tgt, (imp.name,) + rest, depth, seen, importer=rel)
        if tgt.kind == "none" and not level:
            return frozenset({(".".join((dotted, imp.name) + rest[:-1]), rest[-1]) if rest
                              else (dotted, imp.name)}), True
        return frozenset(), False

    def module(self, rel, mod, rest, depth, seen) -> tuple:
        """The absolute module `mod` (as file rel imports it), then the attributes `rest`."""
        if not rest:
            return frozenset(), True                      # a module itself is no class
        tgt = self.roots.module(mod, rel)
        if tgt.kind in ("file", "namespace"):
            return self._attr(tgt, rest, depth, seen)
        if tgt.kind == "none":
            return frozenset({(".".join((mod,) + rest[:-1]), rest[-1])}), True
        return frozenset(), False

    def _attr(self, tgt, parts, depth, seen, importer=None) -> tuple:
        """Attribute path `parts` of the project module `tgt`: a module-level binding of its file, else a
        submodule (a package's `from . import sub` inside its own __init__ binds the submodule)."""
        if not parts:
            return frozenset(), True
        if tgt.kind == "file" and tgt.rel != importer and _file_binds(self.facts.get(tgt.rel) or _EMPTY, parts[0]):
            return self.name(tgt.rel, parts, depth + 1, seen)
        sub = self.roots.submodule(tgt, parts[0])
        if sub.kind in ("file", "namespace"):
            return self._attr(sub, parts[1:], depth + 1, seen)
        if tgt.kind == "file" and tgt.rel != importer:
            return self.name(tgt.rel, parts, depth + 1, seen)    # a star import / builtin of that file
        return frozenset(), False

    def _star(self, rel, imp, parts, depth, seen) -> tuple:
        spec = str(imp.spec or "")
        level = len(spec) - len(spec.lstrip("."))
        dotted = spec[level:]
        tgt = self.roots.module(dotted, rel, level)
        if tgt.kind == "file" and tgt.rel != rel:
            return self.name(tgt.rel, parts, depth + 1, seen)
        if tgt.kind == "none" and not level:
            return frozenset({(".".join((dotted,) + parts[:-1]), parts[-1])}), True
        return frozenset(), False


def extended_externals(facts, roots=None) -> tuple:
    """({(module, name)}, {name}): every NON-project class some project Python class extends — (`http.cookiejar`,
    `CookieJar`) for `class Jar(cookielib.CookieJar)`, ("builtins", "dict") for `class D(dict)` — followed through
    project re-exports (`from .compat import CookieJar`, `from . import compat; class R(compat.ODict)`, star
    imports, `X = mod.Y`), plus the NAMES of bases that could not be followed. An instance declared as one of these
    may be a project subclass that overrides the called method (requests' `merge_cookies(cookiejar: CookieJar)` ->
    `cookiejar.set_cookie()` reaches RequestsCookieJar.set_cookie), so the binder never calls such a class external:
    a REPLACE never removes the engine's link to the override. `roots`: the binder's ImportRoots (built from the
    facts' own files when omitted)."""
    facts = facts or {}
    if roots is None:
        import py_import_resolver as R  # noqa: WPS433 — a sibling, lazily
        roots = R.ImportRoots([r for r, ff in facts.items() if getattr(ff, "lang", "") == "python"])
    origins = _BaseOrigins(facts, roots)
    pairs, names = set(), set()
    for rel in sorted(facts):
        ff = facts[rel]
        if getattr(ff, "lang", "") != "python":
            continue
        for c in ff.classes:
            for b in c.bases:
                head = b
                while head.kind == "generic" and head.args:
                    head = head.args[0]
                if head.kind == "name":
                    parts = (head.name,)
                elif head.kind == "qualified" and head.parts:
                    parts = tuple(head.parts)
                else:
                    continue
                got, ok = origins.name(rel, parts)
                pairs |= got
                if not ok:
                    names.add(parts[-1])
    return frozenset(pairs), frozenset(names)


def patched_externals(facts) -> tuple:
    """(pairs, modules) whose attributes project code ASSIGNS — `sys.stdout = ThreadedStream()`,
    `setattr(builtins, "open", f)`, `monkeypatch.setattr("os.getcwd", f)`, `Lib.method = patched`: {(module,
    attr)} and the {module} itself (resolved through each file's imports; a name the file does not bind is read as
    an absolute module path). A patched external module is never closed: werkzeug's debug console calls
    `sys.stdout._write(...)` after replacing sys.stdout with its own stream, so that call reaches the project's
    HTMLStringO._write and an external verdict would let a REPLACE remove the link. Project targets are unaffected
    (they resolve to files before any external test)."""
    pairs, mods = set(), set()
    for ff in (facts or {}).values():
        if getattr(ff, "lang", "") != "python":
            continue
        defined = {c.name for c in ff.classes if c.outer < 0 and c.scope < 0} | set(ff.functions) | set(ff.globals)
        for tref, attr in ff.patches:
            if tref.kind == "name":
                head, rest = tref.name, ()
            elif tref.kind == "qualified" and tref.parts:
                head, rest = tref.parts[0], tuple(tref.parts[1:])
            else:
                continue
            imps = ff.imports.get(head)
            found = []
            if imps:
                for i in imps:
                    if i.kind == "module":
                        found.append(".".join((i.spec,) + rest))
                    elif i.kind == "package":
                        found.append(".".join((head,) + rest))
                    elif i.kind == "named" and not i.spec.startswith("."):
                        found.append(".".join((i.spec, i.name) + rest))
                        if not rest:
                            pairs.add((i.spec, i.name))      # `from lib import Cls; Cls.attr = f`
            elif head not in defined and "*" not in ff.imports:
                found.append(".".join((head,) + rest))
            for m in found:
                mods.add(m)
                pairs.add((m, attr))
    return frozenset(pairs), frozenset(mods)


class PyBinder:
    """typed_calls' Binder for Python: module / named / package imports through py_import_resolver."""

    def __init__(self, ctx, T):
        import py_import_resolver as R  # noqa: WPS433 — a sibling, lazily
        self.R, self.T = R, T
        files = [f for f in getattr(ctx, "graph_files", ()) or () if str(f).lower().endswith(R.PY_EXTS)]
        self.roots = R.ImportRoots(files, root=getattr(ctx, "root", None))
        facts = getattr(ctx, "facts", None)
        self.extended, self.open_names = extended_externals(facts, self.roots)
        p_pairs, self.patched_mods = patched_externals(facts)
        self.open_pairs = self.extended | p_pairs
        self.open_mods = frozenset(m for m, _n in self.extended) | self.patched_mods

    def _open_module(self, dotted) -> bool:
        """External module `dotted` (or one of its submodules) holds a class a project class extends, or has
        attributes project code assigns: never a closed external."""
        return any(m == dotted or m.startswith(dotted + ".") for m in self.open_mods)

    def _res(self, target, stems):
        T = self.T
        if target.kind == "file":
            return T.Resolution("module", rel=target.rel)
        if target.kind == "namespace":
            return T.Resolution("namespace", name=_ns_token(target.dirs))
        if target.kind == "none" and stems:
            return T.Resolution("external", stems=tuple(stems))
        return T.RES_UNKNOWN

    def resolve_import(self, rel, imp):
        T = self.T
        spec = str(imp.spec or "")
        level = len(spec) - len(spec.lstrip("."))
        dotted = spec[level:]
        if imp.kind in ("module", "package"):
            if level or not dotted:
                return T.RES_UNKNOWN
            name = dotted if imp.kind == "module" else dotted.split(".", 1)[0]
            res = self._res(self.roots.module(name, rel), (self.R.top_name(dotted),))
            if res.kind == "external" and self._open_module(name):
                return T.RES_UNKNOWN            # extended / patched by project code: never a closed external
            return res
        if imp.kind == "named":
            tgt = self.roots.module(dotted, rel, level)
            if tgt.kind == "file" and tgt.rel == _norm_rel(rel):
                # `from . import sub` inside the package's own __init__: the name is not bound yet, so the import
                # system loads the SUBMODULE (a symbol of the importer itself would only read this very import)
                sub = self.roots.submodule(tgt, imp.name)
                if sub.kind in ("file", "namespace"):
                    return self._res(sub, ())
            if tgt.kind == "file":
                return T.Resolution("symbol", rel=tgt.rel, name=imp.name)
            if tgt.kind == "namespace":
                return self._res(self.roots.submodule(tgt, imp.name), ())
            if tgt.kind == "none" and not level:
                if (dotted, imp.name) in self.open_pairs or dotted in self.patched_mods \
                        or self._open_module(dotted + "." + imp.name) or imp.name in self.open_names:
                    return T.RES_UNKNOWN        # extended / patched by project code: never a closed external
                return T.Resolution("external", stems=(self.R.top_name(dotted),))
            return T.RES_UNKNOWN
        return T.RES_UNKNOWN

    def submodule(self, module, attr):
        dirs = _ns_dirs(module)
        if dirs is not None:
            tgt = self.R.Target("namespace", dirs=dirs)
        else:
            tgt = self.R.Target("file", rel=str(module))
        got = self.roots.submodule(tgt, attr)
        return self._res(got, ()) if got.kind in ("file", "namespace") else None

    def resolve_free(self, rel, name):
        """A builtin a project class extends (`class D(dict)`) or project code assigns (`builtins.open = f`) is
        not a closed builtin: unknown."""
        return self.T.RES_UNKNOWN if ("builtins", name) in self.open_pairs else None


# ================================================================================================
# The reader (typed_calls' reader contract)
# ================================================================================================

_CANARY = (
    "import os\n"
    "class A:\n"
    "    def m(self, x: 'A') -> 'A':\n"
    "        return x.m(self)\n"
    "def f(a: A):\n"
    "    a.m(a)\n")


class PyReader:
    """make_reader(spec) -> this. facts(rel, source) reads source.tree(rel) / source.lines(rel) only."""
    version = READER_VERSION

    def __init__(self, spec=None):
        self.spec = spec
        self.T = _ir()

    def canary(self) -> bool:
        T = self.T
        lines = _CANARY.split("\n")
        tree = ast.parse(_CANARY)
        f = file_facts("canary.py", tree, lines, T)
        if T.validate_facts(f):
            return False
        try:
            a = f.classes[0]
            m_scope, f_scope = f.scopes[0], f.scopes[1]
            site = f_scope.sites[0]
            return (a.name == "A" and list(a.methods) == ["m"] and a.methods["m"][0].returns == T.t_name("A")
                    and m_scope.self_kind == "inst" and m_scope.cls == 0
                    and m_scope.sites[0].recv == T.r_ident("x")
                    and f_scope.params["a"].type == T.t_name("A")
                    and site == T.Site(6, 6, "m", T.r_ident("a"), "member", 1)
                    and f.imports["os"][0].kind == "module" and len(f.decls) == 3
                    and f_scope.all_names == {"m": 1})
        except (IndexError, KeyError, AttributeError):
            return False

    def facts(self, rel, source):
        tree = source.tree(rel)
        lines = source.lines(rel)
        if tree is None or lines is None:
            return None
        return file_facts(rel, tree, lines, self.T)

    def binder(self, ctx):
        return PyBinder(ctx, self.T)


def make_reader(spec=None) -> PyReader:
    return PyReader(spec)
