#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking — the CORE linker gates (receiver-calls design slice S3a; no Neo4j, no engine).

build/typed_calls.py is driven through a FAKE reader (hand-built FileFacts over in-memory source) so every rule of
the whole-graph phase is pinned without a parser: the REPLACE / ADD truth table (all typed; any unknown; the
textual veto, strings / comments / declarations; the reader's own census; a span over 5000 lines;
DEEPMAP_TYPED_CALLS_REPLACE=0; external and absent sites), candidate sets that only narrow, the caller policy for
test and non-production callers (component match, with the paths that must NOT match), the audit predicate
(positive and negative), the grading table with its EXTRACTED evidence chain, never cross-family, never a key to a
node outside `nodes`, every emitted callee a CallKey, flag-off returns the SAME list, deterministic and idempotent
output, a canary failure / a reader error / a missing reader / MAX_FILES degrading one language only, a 300-graph
seeded fuzz, the frozen IR and the import-time contract, the phase-2 review's rules (section W: graphify's
symbol-resolution pass — a walked top-level def's nested bare calls of imported names are uncertain, locked against
the installed engine in section R —, a written star import after a binding, the interface grade cap, moot groups
that skip the veto scan, the GC pause, the validator's exact fast paths), the phase-2 verification's (an interface's
OWN member — a TS interface, a typing.Protocol, a typing-only class — is never typed and never REPLACEs, while a real
class's member reached through an interface receiver is INFERRED with an unknowable reach: sections S / V / W; a node
the engine merged from two scopes is never a TYPED caller but the audit judges it over the union of their sites:
section X) — and mutation teeth: each rule is re-run against a copy of the module with that rule broken, and the
matching section must go RED.

Usage: python tests/typed_calls_core_gates.py [--root <tree> | --root=<tree>]   (--root: load build/ from another
       checkout — the RED-first run; a missing value or a tree without build/ is a usage error)
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import random
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _parse_root(args: list) -> tuple:
    root, it = None, iter(args)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        else:
            return None, f"unknown argument {arg!r}"
        if not root or not (Path(root) / "build").is_dir():
            return None, f"--root needs a directory that holds build/ (got {root!r})"
    return (Path(root).resolve() if root else REPO), None


ROOT, _USAGE = _parse_root(sys.argv[1:]) if __name__ == "__main__" else (REPO, None)
if _USAGE:
    print(f"usage: python tests/typed_calls_core_gates.py [--root <tree>]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

try:
    import typed_calls as TC      # noqa: E402
    _IMPORT_ERROR = ""
except Exception as _exc:          # the pre-feature tree: RED, never a crash
    TC = None
    _IMPORT_ERROR = repr(_exc)


class Rec:
    """Check recorder. loud=True prints every check (the real run); loud=False only counts (mutation runs)."""

    def __init__(self, loud: bool = True):
        self.loud, self.passed, self.failed, self.fails = loud, 0, 0, []

    def check(self, desc: str, cond, detail="") -> bool:
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.fails.append(desc)
        if self.loud:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:700]}"))
        return ok


# ================================================================================================
# The FAKE reader kit
# ================================================================================================

class Src:
    """In-memory stand-in for graphify_extract._Source (only lines() is read by the core)."""

    def __init__(self, files: dict, big: dict | None = None):
        self.files, self.big, self.reads = dict(files), dict(big or {}), []

    def lines(self, rel):
        self.reads.append(rel)
        if rel in self.big:
            return ["x = 1"] * self.big[rel]
        t = self.files.get(rel)
        return None if t is None else t.split("\n")

    def raw(self, rel):
        t = self.files.get(rel)
        return None if t is None else t.encode("utf-8")

    def tree(self, rel):
        return None

    def ts_tree(self, rel):
        return None


class FBinder:
    def __init__(self, T, imports=None, subs=None, free=None, boom=False):
        self.T, self.imports, self.subs, self.free, self.boom = T, dict(imports or {}), dict(subs or {}), \
            dict(free or {}), boom

    def resolve_import(self, rel, imp):
        if self.boom:
            raise RuntimeError("binder boom")
        got = self.imports.get((rel, imp.local, imp.spec))
        return got if got is not None else self.imports.get((rel, imp.local), self.T.RES_UNKNOWN)

    def submodule(self, module, attr):
        return self.subs.get((module, attr))

    def resolve_free(self, rel, name):
        return self.free.get((rel, name))


class FReader:
    def __init__(self, T, facts: dict, binder=None, canary=True, boom=(), binder_boom=False):
        self.T, self.table, self.b, self.can, self.boom = T, facts, binder, canary, set(boom)
        self.version, self.binder_boom, self.calls = "fake-1", binder_boom, []

    def canary(self):
        if self.can == "raise":
            raise RuntimeError("canary boom")
        return self.can

    def facts(self, rel, source):
        self.calls.append(rel)
        if rel in self.boom:
            raise RuntimeError("reader boom")
        f = self.table.get(rel)
        return f() if callable(f) else f

    def binder(self, ctx):
        if self.binder_boom:
            raise RuntimeError("binder() boom")
        self.ctx = ctx
        return self.b if self.b is not None else FBinder(self.T)


def bcol(text: str, line: int, needle: str, nth: int = 0) -> int:
    """The UTF-8 byte column of the nth `needle` on 1-based `line` of `text`."""
    ln = text.split("\n")[line - 1]
    i = -1
    for _ in range(nth + 1):
        i = ln.index(needle, i + 1)
    return len(ln[:i].encode("utf-8"))


_DECL_RX = re.compile(r"\b(?:def|class|function)\s+([A-Za-z_$][\w$]*)")


def decls_of(text: str) -> frozenset:
    out = set()
    for n, ln in enumerate(text.split("\n"), 1):
        for mt in _DECL_RX.finditer(ln):
            out.add((n, len(ln[:mt.start(1)].encode("utf-8"))))
    return frozenset(out)


def nd(key, label, name, rel, line, **kw) -> dict:
    return dict(key=key, label=label, name=name, source_file=rel, start_line=line, **kw)


def link(T, files, facts, nodes, calls, env, *, binder=None, is_test=None, readers=None, languages=None,
         source=None):
    """Run T.link with a fake python reader (plus any extra readers) and a closed env."""
    if readers is None:
        readers = {"python": FReader(T, facts, binder)}
    langs = languages if languages is not None else {n: T.LANGUAGES[n] for n in readers if n in T.LANGUAGES}
    return T.link(".", nodes, calls, languages=langs, readers=readers, source=source or Src(files),
                  is_test=is_test or (lambda p: False), env=env)


def keyed(T, calls) -> list:
    """The calls with key-ness visible (a CallKey compares equal to its plain str)."""
    return [(c[0], c[1], T._is_call_key(c[1])) for c in calls]


ON = {"DEEPMAP_TYPED_CALLS": "python"}


# ---- fixture GO: two same-named methods; the truth-table caller ---------------------------------------------
GO_LINES = [
    "class A:",              # 1
    "    def go(self):",     # 2
    "        return 1",      # 3
    "",                      # 4
    "",                      # 5
    "class B:",              # 6
    "    def go(self):",     # 7
    "        return 2",      # 8
    "",                      # 9
    "",                      # 10
    "def caller(a: A, b):",  # 11
    "    a.go()",            # 12
    "    b.go()",            # 13
    "    return 0",          # 14
]


def go_world(T, *, rel="pkg/a.py", b_ann="none", line14="    return 0", all_names="auto", end=14,
             extra_sites=(), extra_lines=(), drop_decl=None, calls=None, call_member=("go", "x"), a_site=True):
    """(files, facts, nodes, calls) of fixture GO. b_ann: "none" (untyped b) | "B" | "union" | "str" | a TypeRef."""
    lines = list(GO_LINES)
    lines[13] = line14
    if not a_site:
        lines[11] = "    pass"
    lines += list(extra_lines)
    text = "\n".join(lines) + "\n"
    stem = rel.rsplit("/", 1)[-1].split(".")[0]
    k = lambda s: f"{stem}::{s}"    # noqa: E731
    if b_ann == "none":
        bt = None
    elif b_ann == "B":
        bt = T.t_name("B")
    elif b_ann == "union":
        bt = T.t_union(T.t_name("A"), T.t_name("B"))
    elif b_ann == "str":
        bt = T.t_name("str")
    else:
        bt = b_ann
    sites = ([T.Site(12, 4, "go", T.r_ident("a"), "member", 0)] if a_site else []) + \
        [T.Site(13, 4, "go", T.r_ident("b"), "member", 0)] + list(extra_sites)
    if all_names == "auto":
        an = {}
        for s in sites:
            an[s.member] = an.get(s.member, 0) + 1
    else:
        an = all_names
    decls = set(decls_of(text))
    if drop_decl is not None:
        decls.discard(drop_decl)
    facts = T.FileFacts(
        rel=rel, lang="python",
        classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]}),
                 T.ClassFacts("B", 6, 8, methods={"go": [T.MethodFacts(7)]})],
        functions={"caller": [T.FunctionFacts("caller", 11)]},
        scopes=[T.Scope("go", 2, 3, cls=0, self_kind="inst", params={"self": T.Binding("param", T.t_self())}),
                T.Scope("go", 7, 8, cls=1, self_kind="inst", params={"self": T.Binding("param", T.t_self())}),
                T.Scope("caller", 11, end,
                        params={"a": T.Binding("param", T.t_name("A")), "b": T.Binding("param", bt)},
                        sites=sites, all_names=an)],
        decls=frozenset(decls))
    nodes = [nd(stem, "Module", stem, rel, 1), nd(k("A"), "Class", "A", rel, 1),
             nd(k("A.go"), "Method", "A.go", rel, 2), nd(k("B"), "Class", "B", rel, 6),
             nd(k("B.go"), "Method", "B.go", rel, 7), nd(k("caller"), "Function", "caller", rel, 11)]
    if call_member is not None:
        nodes[-1]["call_member"] = list(call_member)
    if calls is None:
        calls = [(k("caller"), "go"), (k("caller"), "B.go"), (k("caller"), "other")]
    return {rel: text}, {rel: facts}, nodes, calls


def caller_node(nodes, name="caller"):
    return next(n for n in nodes if n["name"] == name)


# ================================================================================================
# A: the frozen IR, the registry and the import-time contract
# ================================================================================================

def sec_ir(T, rec: Rec) -> None:
    rec.check("A IR_VERSION is 1", T.IR_VERSION == 1, T.IR_VERSION)
    frozen = {
        "TypeRef": ("kind", "name", "parts", "args"),
        "Receiver": ("kind", "name", "inner", "type"),
        "Binding": ("kind", "type", "value", "line", "scope"),
        "Import": ("local", "kind", "spec", "name", "line"),
        "Site": ("line", "col", "member", "recv", "kind", "argc"),
        "MethodFacts": ("line", "flags", "returns", "arity", "lines"),
        "FunctionFacts": ("name", "line", "returns", "lines", "arity"),
        "Resolution": ("kind", "rel", "name", "stems"),
    }
    for cls, fields in frozen.items():
        rec.check(f"A IR {cls} fields frozen", getattr(T, cls)._fields == fields, getattr(T, cls)._fields)
    dcs = {
        "ClassFacts": ("name", "line", "end_line", "kind", "bases", "fields", "methods", "dynamic", "open", "lines",
                       "outer", "scope", "type_params"),
        "Scope": ("name", "line", "end_line", "lines", "parent", "cls", "self_kind", "params", "locals", "poisoned",
                  "sites", "all_names", "type_params", "kind"),
        "FileFacts": ("rel", "lang", "imports", "classes", "functions", "globals", "scopes", "decls", "patches",
                      "reader_version"),
        "BinderContext": ("root", "lang", "rels", "facts", "graph_files", "nodes"),
    }
    for cls, fields in dcs.items():
        got = tuple(getattr(T, cls).__dataclass_fields__)
        rec.check(f"A IR {cls} fields frozen", got == fields, got)
    rec.check("A IR vocabularies", T.TYPEREF_KINDS == {"name", "qualified", "union", "nullable", "generic", "self",
                                                       "classof", "builtin", "unknown"}
              and T.RECEIVER_KINDS == {"SELF", "SUPER", "IDENT", "FIELD", "CALL", "NEW", "CLASSREF", "MODULE",
                                       "LITERAL", "OTHER"}
              and T.BINDING_KINDS == {"ann", "ctor", "call", "field", "param", "none", "unknown"}
              and T.SITE_KINDS == {"bare", "member", "ctor"}, "")
    rec.check("A grades are exactly the three-grade vocabulary", T.GRADES == ("EXTRACTED", "INFERRED", "AMBIGUOUS"))
    rec.check("A shapes are R1 R2 R3 R4 S K M C", T.SHAPES == ("R1", "R2", "R3", "R4", "S", "K", "M", "C"))
    _f, facts, _n, _c = go_world(T)
    good = facts["pkg/a.py"]
    rec.check("A validate_facts accepts a well-formed FileFacts", T.validate_facts(good) == "", T.validate_facts(good))
    bad_cases = {
        "a dict instead of FileFacts": {"rel": "x"},
        "a scope whose parent is not before it": T.FileFacts("pkg/a.py", "python", scopes=[T.Scope("f", 1, 2,
                                                                                                    parent=0)]),
        "a site that is a plain tuple": T.FileFacts("pkg/a.py", "python", scopes=[T.Scope("f", 1, 2, sites=[(1, 2)])]),
        "an unknown receiver kind": T.FileFacts("pkg/a.py", "python", scopes=[T.Scope("f", 1, 2, sites=[
            T.Site(1, 0, "m", T.Receiver("WAT"), "member")])]),
        "a binding scope out of range": T.FileFacts("pkg/a.py", "python", globals={"x": [T.Binding("ann", scope=7)]}),
        "a class outer pointing forward": T.FileFacts("pkg/a.py", "python", classes=[T.ClassFacts("A", 1, outer=0)]),
        "decls that are not (line, col) pairs": T.FileFacts("pkg/a.py", "python", decls=frozenset({1})),
        "a TypeRef with a str arg": T.FileFacts("pkg/a.py", "python", functions={"f": [
            T.FunctionFacts("f", 1, T.TypeRef("union", args=("A",)))]}),
    }
    for why, f in bad_cases.items():
        if f is None:
            continue
        rec.check(f"A validate_facts rejects {why}", T.validate_facts(f) != "", "accepted")
    names = set(T.LANGUAGES)
    rec.check("A registry: the wave-1 languages python / typescript / javascript",
              {"python", "typescript", "javascript"} <= names and T.WAVE1_LANGS == ("python", "typescript",
                                                                                     "javascript"), sorted(names))
    rec.check("A registry: wave-1 readers are receiver_facts_py / receiver_facts_ts",
              T.LANGUAGES["python"].reader == "receiver_facts_py" and T.LANGUAGES["typescript"].reader ==
              "receiver_facts_ts" and T.LANGUAGES["javascript"].reader == "receiver_facts_ts")
    rec.check("A registry: absent only in compiler-checked languages (.ts/.tsx, Java, C#)",
              {n for n, s in T.LANGUAGES.items() if s.absent_ok} == {"typescript", "java", "csharp"},
              {n for n, s in T.LANGUAGES.items() if s.absent_ok})
    rec.check("A registry: Python uses C3, Go the depth rule, Java/C# an implicit this",
              T.LANGUAGES["python"].mro == "c3" and T.LANGUAGES["go"].mro == "depth"
              and T.LANGUAGES["java"].implicit_this and T.LANGUAGES["csharp"].implicit_this
              and not T.LANGUAGES["typescript"].implicit_this)
    try:
        import edge_resolve as ER
        drift = [(n, e, ER.lang_family("x" + e)) for n, s in T.LANGUAGES.items() for e in s.exts
                 if ER.lang_family("x" + e) != s.family]
    except Exception as exc:  # noqa: BLE001
        drift = [repr(exc)]
    rec.check("A registry: every language's extensions map to its family in edge_resolve.lang_family (drift lock)",
              not drift, drift)
    rec.check("A defaults: _TYPED_DEFAULT_LANGS and _AUDIT_DEFAULT_LANGS are empty (TYPED and the audit are OFF)",
              T._TYPED_DEFAULT_LANGS == frozenset() and T._AUDIT_DEFAULT_LANGS == frozenset())
    kinds = getattr(T, "SCOPE_KINDS", None)
    rec.check("A Scope.kind vocabulary: Python function / method / lambda / comprehension; JS / TS function_decl / "
              "generator_decl / function_expr / arrow / method_def", kinds == {
                  "function", "method", "lambda", "comprehension", "function_decl", "generator_decl",
                  "function_expr", "arrow", "method_def"}, kinds)
    att = {n: (getattr(s, "nested_attributed", None), getattr(s, "nested_detached", None),
               getattr(s, "header_detached", None)) for n, s in T.LANGUAGES.items()}
    rec.check("A attribution tables (graphify's walk): Python attributes lambda / comprehension, detaches function / "
              "method, a default argument is detached; JS / TS attribute function_expr / generator_decl, detach "
              "function_decl / arrow / method_def; wave 2 has none yet (every nested scope uncertain)",
              att.get("python") == ({"lambda", "comprehension"}, {"function", "method"}, True)
              and att.get("typescript") == att.get("javascript")
              == ({"function_expr", "generator_decl"}, {"function_decl", "arrow", "method_def"}, False)
              and all(att[n] == (frozenset(), frozenset(), False) for n in ("go", "java", "csharp", "rust", "ruby")),
              att)
    rec.check("A the legacy JS / TS kinds 'function' / 'method' are in NO table (they read as uncertain)",
              not ({"function", "method"} & (set(T.LANGUAGES["typescript"].nested_attributed)
                                             | set(T.LANGUAGES["typescript"].nested_detached))))
    rec.check("A only TypeScript carries the @ts-nocheck / @ts-ignore / @ts-expect-error markers",
              {n for n, s in T.LANGUAGES.items() if any(getattr(s, "unchecked", ((), ())))} == {"typescript"}
              and "@ts-nocheck" in T.LANGUAGES["typescript"].unchecked[0]
              and {"@ts-ignore", "@ts-expect-error"} <= set(T.LANGUAGES["typescript"].unchecked[1]))
    name_of = {"k1": "A.go", "k2": "Admin::Order.go", "k3": "go", "k4": "A.goo", "k5": "x::y"}
    callees = ["go", "A.go", "pkg.A.go", "A::go", "A#go", "ago", "go.x", "", "x.", ".go", "goo", "a.b::go",
               T._call_key("k1"), T._call_key("k2"), T._call_key("k3"), T._call_key("k4"), T._call_key("k5"),
               T._call_key("ghost"), 7, None]
    bad = [(c, m) for c in callees for m in ("go", "goo", "y", "A", "x", "")
           if bool(T.callee_matches(c, m, name_of, T._is_call_key)) != bool(m and T._callee_seg(c, name_of) == m)]
    rec.check("A the merge's per-caller segment index == callee_matches for every identifier (one rule)", not bad, bad)


def sec_imports(T, rec: Rec) -> None:
    src = (BUILD / "typed_calls.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    top = []
    for st in tree.body:
        if isinstance(st, ast.Import):
            top += [a.name.split(".")[0] for a in st.names]
        elif isinstance(st, ast.ImportFrom):
            top.append((st.module or "").split(".")[0])
    rec.check("A module-level imports are stdlib only", top and all(m in std for m in top),
              [m for m in top if m not in std])
    try:
        ast.parse(src, feature_version=(3, 10))
        ok310 = True
    except SyntaxError as exc:
        ok310 = repr(exc)
    rec.check("A the module is Python 3.10 syntax (the Linux container's oldest interpreter)", ok310 is True, ok310)
    makers = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for x in ast.walk(fn):
                if isinstance(x, ast.Call):
                    f = x.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    if nm in ("call_key", "CallKey"):
                        makers.append(fn.name)
    rec.check("A every CallKey is made in ONE place (_call_key -> graphify_extract.call_key)",
              sorted(set(makers)) == ["_call_key"], makers)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import typed_calls; "
            "print(sorted(m for m in ('graphify_extract', 'edge_resolve', 'sync', 'typescript_edges', "
            "'tree_sitter', 'receiver_facts_py', 'receiver_facts_ts') if m in sys.modules))")
    p = subprocess.run([sys.executable, "-I", "-c", code, str(BUILD)], capture_output=True, text=True, timeout=120)
    rec.check("A importing typed_calls loads no sibling / engine module (outside the lazy-import cycle)",
              p.returncode == 0 and p.stdout.strip() == "[]", (p.returncode, p.stdout, p.stderr[-400:]))
    try:
        sync_src = (BUILD / "sync.py").read_text(encoding="utf-8")
        val = next(st.value.value for st in ast.parse(sync_src).body if isinstance(st, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "_BARE_SCAN_MAX_LINES" for t in st.targets))
    except Exception as exc:  # noqa: BLE001
        val = repr(exc)
    rec.check("A SPAN_MAX_LINES mirrors sync._BARE_SCAN_MAX_LINES (read from sync.py's AST)",
              T.SPAN_MAX_LINES == val == 5000, (T.SPAN_MAX_LINES, val))
    try:
        gx_src = (BUILD / "graphify_extract.py").read_text(encoding="utf-8")
        mval = next(st.value.value for st in ast.parse(gx_src).body if isinstance(st, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_MINIFIED_LINE" for t in st.targets))
    except Exception as exc:  # noqa: BLE001
        mval = repr(exc)
    rec.check("A MINIFIED_LINE mirrors graphify_extract._MINIFIED_LINE (read from its AST; typescript_edges' rule)",
              getattr(T, "MINIFIED_LINE", None) == mval == 2000, (getattr(T, "MINIFIED_LINE", None), mval))
    rec.check("A link() takes engine= (the extractor that built the graph); ENGINES = graphify / ast",
              "engine" in __import__("inspect").signature(T.link).parameters
              and tuple(getattr(T, "ENGINES", ())) == ("graphify", "ast"))


# ================================================================================================
# B: flags
# ================================================================================================

def sec_flags(T, rec: Rec) -> None:
    L = T.LANGUAGES
    cases = [({}, frozenset()), ({"DEEPMAP_TYPED_CALLS": ""}, frozenset()),
             ({"DEEPMAP_TYPED_CALLS": "0"}, frozenset()), ({"DEEPMAP_TYPED_CALLS": "off"}, frozenset()),
             ({"DEEPMAP_TYPED_CALLS": "1"}, frozenset({"python", "typescript", "javascript"})),
             ({"DEEPMAP_TYPED_CALLS": "yes"}, frozenset({"python", "typescript", "javascript"})),
             ({"DEEPMAP_TYPED_CALLS": " Python , go "}, frozenset({"python", "go"})),
             ({"DEEPMAP_TYPED_CALLS": "python,klingon"}, frozenset({"python"}))]
    for env, want in cases:
        rec.check(f"B DEEPMAP_TYPED_CALLS={env.get('DEEPMAP_TYPED_CALLS', '<unset>')!r} -> {sorted(want)}",
                  T.typed_langs(env) == want, T.typed_langs(env))
    rec.check("B DEEPMAP_CALLS_AUDIT unset -> off; 1 -> wave 1; csv -> those",
              T.audit_langs({}) == frozenset() and T.audit_langs({"DEEPMAP_CALLS_AUDIT": "1"}) ==
              frozenset({"python", "typescript", "javascript"})
              and T.audit_langs({"DEEPMAP_CALLS_AUDIT": "javascript"}) == frozenset({"javascript"}))
    c = T._config({"DEEPMAP_TYPED_CALLS": "python,klingon", "DEEPMAP_TYPED_CALLS_SHAPES": "r1, S,zz"}, L)
    rec.check("B unknown language and shape names are ignored and reported",
              c.unknown_langs == ["klingon"] and c.shapes == {"R1", "S"} and c.unknown_shapes == ["ZZ"],
              (c.unknown_langs, c.shapes, c.unknown_shapes))
    c = T._config({}, L)
    rec.check("B defaults: every shape, REPLACE on, tests/nonprod additions off, MAX_FILES 20000",
              c.shapes == frozenset(T.SHAPES) and c.replace and not c.tests and not c.nonprod and c.max_files == 20000)
    c = T._config({"DEEPMAP_TYPED_CALLS_REPLACE": "0", "DEEPMAP_TYPED_CALLS_TESTS": "1",
                   "DEEPMAP_TYPED_CALLS_NONPROD": "on", "DEEPMAP_TYPED_CALLS_MAX_FILES": "7",
                   "DEEPMAP_TYPED_CALLS_SHAPES": "0"}, L)
    rec.check("B REPLACE=0 / TESTS=1 / NONPROD=on / MAX_FILES=7 / SHAPES=0 are read",
              not c.replace and c.tests and c.nonprod and c.max_files == 7 and c.shapes == frozenset())
    c = T._config({"DEEPMAP_TYPED_CALLS_MAX_FILES": "lots"}, L)
    rec.check("B an unreadable MAX_FILES keeps the default", c.max_files == 20000)
    rec.check("B enabled() is False with every flag unset", T.enabled({}) is False)


# ================================================================================================
# C: flag-off identity (frozen inputs)
# ================================================================================================

class _Poison:
    def __getattr__(self, name):
        raise AssertionError(f"flag-off link() touched {name}")


def sec_flag_off(T, rec: Rec) -> None:
    files, facts, nodes, calls = go_world(T, b_ann="B")
    frozen = json.dumps([nodes, [list(c) for c in calls]], sort_keys=True)
    for env in ({}, {"DEEPMAP_TYPED_CALLS": "0"}, {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "off"},
                {"DEEPMAP_TYPED_CALLS_REPLACE": "0", "DEEPMAP_TYPED_CALLS_TESTS": "1"}):
        rd = _Poison()
        out, st = T.link(".", nodes, calls, languages=T.LANGUAGES, readers={"python": rd}, source=_Poison(),
                         is_test=lambda p: False, env=env)
        rec.check(f"C flags off ({env or 'unset'}): the SAME list object, no reader / source touched",
                  out is calls and st["error"] is None, st["error"])
        rec.check(f"C flags off ({env or 'unset'}): nodes and calls byte-identical to the frozen input",
                  json.dumps([nodes, [list(c) for c in calls]], sort_keys=True) == frozen)
    files, facts, nodes, calls = go_world(T, b_ann="none", a_site=False)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("C flags on, nothing typed (every site unknown): still the SAME list object, no attribute",
              out is calls and st["error"] is None and "call_typed" not in caller_node(nodes), keyed(T, out))
    files, facts, nodes, calls = go_world(T, b_ann="B", calls=[])
    nodes[-1]["call_member"] = []
    st_calls = []
    out, st = link(T, files, facts, nodes, st_calls, {"DEEPMAP_CALLS_AUDIT": "python"})
    rec.check("C audit only, no input tuple: the SAME list object and no attribute", out is st_calls
              and all("call_weak" not in n and "call_typed" not in n for n in nodes))


# ================================================================================================
# D: the REPLACE / ADD truth table
# ================================================================================================

def sec_truth(T, rec: Rec) -> None:
    # D1: every site typed -> REPLACE
    files, facts, nodes, calls = go_world(T, b_ann="B")
    unrelated = ("a::caller", T._call_key("a::A"))
    calls = calls + [unrelated, ("a::caller", T._call_key("a::B.go"))]
    out, st = link(T, files, facts, nodes, calls, ON)
    want = [("a::caller", "other", False), ("a::caller", "a::A", True), ("a::caller", "a::A.go", True),
            ("a::caller", "a::B.go", True)]
    rec.check("D1 all typed -> REPLACE: every tuple of `go` (the name, `B.go`, a CallKey of a .go node) removed; "
              "others kept; the typed keys appended last", keyed(T, out) == want, keyed(T, out))
    c = caller_node(nodes)
    rec.check("D1 call_typed = both targets EXTRACTED (in-file chain); call_member loses `go`",
              c.get("call_typed") == {"a::A.go": "EXTRACTED", "a::B.go": "EXTRACTED"}
              and c.get("call_member") == ["x"], (c.get("call_typed"), c.get("call_member")))
    rec.check("D1 stats: 1 replace group, 3 removed, 2 emitted, 0 vetoes",
              st["groups"] == {"replace": 1, "add": 0, "moot": 0} and st["removed"] == 3 and st["emitted"] == 2
              and not any(st["vetoes"].values()), (st["groups"], st["removed"], st["emitted"], st["vetoes"]))
    files, facts, nodes, calls = go_world(T, b_ann="B", call_member=("go",))
    link(T, files, facts, nodes, calls, ON)
    rec.check("D1 a call_member that empties is deleted", "call_member" not in caller_node(nodes))
    # D2: any unknown site -> ADD
    files, facts, nodes, calls = go_world(T, b_ann="none")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D2 an unknown site -> ADD: every engine tuple stays, the typed key is appended last",
              keyed(T, out) == [(c0, c1, False) for c0, c1 in calls] + [("a::caller", "a::A.go", True)],
              keyed(T, out))
    rec.check("D2 call_member untouched; veto unknown_site counted", caller_node(nodes)["call_member"] == ["go", "x"]
              and st["vetoes"]["unknown_site"] == 1 and st["groups"]["add"] == 1, st["vetoes"])
    # D3: the textual veto
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    return a.go()")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3 a call of `go(` the reader did not record -> textual veto -> ADD (nothing removed)",
              st["vetoes"]["text"] == 1 and st["removed"] == 0 and ("a::caller", "go") in out, st["vetoes"])
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    return \"a.go()\"  # b.go(1)")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3 `go(` inside a string and a comment is not counted -> REPLACE still fires",
              st["groups"]["replace"] == 1 and st["removed"] == 2, (st["groups"], st["vetoes"]))
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    def go(z): return z")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3 a declaration the reader saw (`def go(` in decls) is not counted -> REPLACE",
              st["groups"]["replace"] == 1, st["vetoes"])
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    def go(z): return z",
                                          drop_decl=(14, bcol("\n".join(GO_LINES[:13]) + "\n    def go(z): return z",
                                                              14, "go")))
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3 the same declaration NOT in decls counts -> textual veto -> ADD", st["vetoes"]["text"] == 1
              and st["removed"] == 0, st["vetoes"])
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    return (a.go\n        ())", end=15)
    facts["pkg/a.py"].scopes[2].sites.append(T.Site(14, 12, "go", T.r_ident("a"), "member", 0))
    facts["pkg/a.py"].scopes[2].all_names["go"] = 3
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3 whitespace / a line break between the name and `(` still counts as call-shaped",
              st["groups"]["replace"] == 1, st["vetoes"])
    deco_lines = list(GO_LINES)
    deco_lines.insert(10, "@trace(a.go())")
    deco_text = "\n".join(deco_lines) + "\n"
    files, facts, nodes, calls = go_world(T, b_ann="B")
    f = facts["pkg/a.py"]
    for sc in f.scopes:
        if sc.name == "caller":
            sc.line, sc.end_line, sc.lines = 12, 15, (11,)
            sc.sites = [s._replace(line=s.line + 1) for s in sc.sites]
    f.functions["caller"] = [T.FunctionFacts("caller", 12, lines=(11,))]
    f.decls = decls_of(deco_text)
    nodes[-1]["start_line"] = 11
    out, st = link(T, {"pkg/a.py": deco_text}, facts, nodes, calls, ON)
    rec.check("D3 a decorator call above the def (`@trace(a.go())`, attributed to the caller by the engine) that "
              "the reader did not record -> textual veto -> ADD", st["vetoes"]["text"] == 1 and st["removed"] == 0,
              st["vetoes"])
    # D3b: the reader's own census
    files, facts, nodes, calls = go_world(T, b_ann="B", all_names={"go": 3})
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3b the reader saw 3 calls of `go` but emitted 2 sites -> census veto -> ADD",
              st["vetoes"]["census"] == 1 and st["removed"] == 0, st["vetoes"])
    files, facts, nodes, calls = go_world(T, b_ann="B", all_names=None)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D3b all_names=None (not provided) skips the census; the textual veto still guards",
              st["groups"]["replace"] == 1, st["vetoes"])
    # D4: span over 5000 lines
    files, facts, nodes, calls = go_world(T, b_ann="B", end=11 + 5000)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D4 a caller span over 5000 lines -> ADD (veto span)", st["vetoes"]["span"] == 1
              and st["removed"] == 0 and ("a::caller", "a::A.go") in [(c[0], c[1]) for c in out], st["vetoes"])
    files, facts, nodes, calls = go_world(T, b_ann="B", end=11 + 4999,
                                          extra_lines=["    pass"] * 4999)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D4 a span of exactly 5000 lines is judged (REPLACE)", st["groups"]["replace"] == 1, st["vetoes"])
    # D5: REPLACE=0
    files, facts, nodes, calls = go_world(T, b_ann="B")
    out, st = link(T, files, facts, nodes, calls, dict(ON, DEEPMAP_TYPED_CALLS_REPLACE="0"))
    rec.check("D5 DEEPMAP_TYPED_CALLS_REPLACE=0 -> add-only (nothing removed, keys appended)",
              st["removed"] == 0 and st["vetoes"]["replace_off"] == 1 and st["emitted"] == 2
              and caller_node(nodes)["call_member"] == ["go", "x"], st["vetoes"])
    # D6: external
    files, facts, nodes, calls = go_world(T, b_ann="str")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D6 typed + external (`b: str`) -> REPLACE: the name's tuples go, only A.go is added",
              keyed(T, out) == [("a::caller", "other", False), ("a::caller", "a::A.go", True)]
              and st["sites"]["external"] == 1, keyed(T, out))
    files, facts, nodes, calls = go_world(T, b_ann="str", a_site=False)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D6 every site external -> the tuples of `go` are removed and NOTHING is added",
              keyed(T, out) == [("a::caller", "other", False)] and "call_typed" not in caller_node(nodes),
              keyed(T, out))
    # D7: absent — only in absent_ok languages
    files, facts, nodes, calls = go_world(T, b_ann="B",
                                          extra_sites=[T.Site(14, 4, "nope", T.r_ident("a"), "member", 0)],
                                          line14="    a.nope()", calls=[("a::caller", "nope"), ("a::caller", "go")])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("D7 Python: a member no class defines is UNKNOWN (setattr / monkeypatching) -> its tuple stays",
              ("a::caller", "nope") in out and st["sites"]["absent"] == 0, keyed(T, out))
    ts_text = ("export class A {\n  go(): number { return 1; }\n}\n"
               "export function caller(a: A): number {\n  a.nope();\n  return a.go();\n}\n")
    for lang, rel, absent in (("typescript", "web/a.ts", True), ("javascript", "web/a.js", False)):
        f = T.FileFacts(rel, lang, classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]})],
                        functions={"caller": [T.FunctionFacts("caller", 4)]},
                        scopes=[T.Scope("go", 2, 2, cls=0, self_kind="inst"),
                                T.Scope("caller", 4, 7, params={"a": T.Binding("param", T.t_name("A"))},
                                        sites=[T.Site(5, 2, "nope", T.r_ident("a"), "member", 0),
                                               T.Site(6, 9, "go", T.r_ident("a"), "member", 0)],
                                        all_names={"nope": 1, "go": 1})],
                        decls=decls_of(ts_text) | {(2, 2)})
        nodes = [nd("w::A", "Class", "A", rel, 1), nd("w::A.go", "Method", "A.go", rel, 2),
                 nd("w::caller", "Function", "caller", rel, 4)]
        calls = [("w::caller", "nope"), ("w::caller", "go")]
        out, st = T.link(".", nodes, calls, languages={lang: T.LANGUAGES[lang]},
                         readers={lang: FReader(T, {rel: f})}, source=Src({rel: ts_text}),
                         is_test=lambda p: False, env={"DEEPMAP_TYPED_CALLS": lang})
        if absent:
            rec.check("D7 TypeScript: a closed chain without the member is ABSENT -> the name's tuple is removed, "
                      "nothing added", ("w::caller", "nope") not in out and st["sites"]["absent"] == 1
                      and keyed(T, out) == [("w::caller", "w::A.go", True)], keyed(T, out))
        else:
            rec.check("D7 JavaScript: never absent -> the tuple stays", ("w::caller", "nope") in out
                      and st["sites"]["absent"] == 0, keyed(T, out))
    f = T.FileFacts("web/a.ts", "typescript",
                    classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]}, open=True)],
                    functions={"caller": [T.FunctionFacts("caller", 4)]},
                    scopes=[T.Scope("caller", 4, 7, params={"a": T.Binding("param", T.t_name("A"))},
                                    sites=[T.Site(5, 2, "nope", T.r_ident("a"), "member", 0)])],
                    decls=decls_of(ts_text))
    nodes = [nd("w::A", "Class", "A", "web/a.ts", 1), nd("w::A.go", "Method", "A.go", "web/a.ts", 2),
             nd("w::caller", "Function", "caller", "web/a.ts", 4)]
    calls = [("w::caller", "nope")]
    out, st = T.link(".", nodes, calls, languages={"typescript": T.LANGUAGES["typescript"]},
                     readers={"typescript": FReader(T, {"web/a.ts": f})}, source=Src({"web/a.ts": ts_text}),
                     is_test=lambda p: False, env={"DEEPMAP_TYPED_CALLS": "typescript"})
    rec.check("D7 TypeScript: an OPEN class (declaration merging / index signature / declare) is never absent",
              out is calls and st["sites"]["absent"] == 0, keyed(T, out))
    ts_rel, js_rel = "web/a.ts", "web/b.js"
    ts_text2 = "export class A {\n  go(): number { return 1; }\n}\n"
    js_text = "import { A } from './a';\nfunction caller() {\n  new A().nope();\n}\n"
    fts = T.FileFacts(ts_rel, "typescript", classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]})],
                      decls=decls_of(ts_text2))
    fjs = T.FileFacts(js_rel, "javascript", imports={"A": [T.Import("A", "named", "./a", "A", 1)]},
                      functions={"caller": [T.FunctionFacts("caller", 2)]},
                      scopes=[T.Scope("caller", 2, 4, sites=[
                          T.Site(3, 2, "A", T.r_classref(T.t_name("A")), "ctor", 0),
                          T.Site(3, 2, "nope", T.r_new(T.t_name("A")), "member", 0)], all_names={"A": 1, "nope": 1})],
                      decls=decls_of(js_text))
    nodes = [nd("w::A", "Class", "A", ts_rel, 1), nd("w::A.go", "Method", "A.go", ts_rel, 2),
             nd("j::caller", "Function", "caller", js_rel, 2)]
    calls = [("j::caller", "nope")]
    readers = {"javascript": FReader(T, {js_rel: fjs}, FBinder(T, imports={(js_rel, "A"): T.Resolution(
        "symbol", ts_rel, "A")})), "typescript": FReader(T, {ts_rel: fts})}
    out, st = T.link(".", nodes, calls, languages={n: T.LANGUAGES[n] for n in ("typescript", "javascript")},
                     readers=readers, source=Src({ts_rel: ts_text2, js_rel: js_text}), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "javascript"})
    rec.check("D7 a JavaScript CALLER is never compiler-checked: a closed TypeScript class without the member is "
              "still UNKNOWN there (the tuple stays)", ("j::caller", "nope") in out and st["sites"]["absent"] == 0
              and caller_node(nodes).get("call_typed") == {"w::A": "INFERRED"}, (keyed(T, out), st["sites"]))


# ================================================================================================
# E: candidate sets only narrow; |T| > 4 is nothing
# ================================================================================================

def sec_candidates(T, rec: Rec) -> None:
    files, facts, nodes, calls = go_world(T, b_ann="union", a_site=False)
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("E a Union receiver narrowing an existing fan-out -> both targets AMBIGUOUS",
              caller_node(nodes).get("call_typed") == {"a::A.go": "AMBIGUOUS", "a::B.go": "AMBIGUOUS"},
              caller_node(nodes).get("call_typed"))
    files, facts, nodes, calls = go_world(T, b_ann="union", a_site=False, calls=[("a::caller", "other")])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("E the same candidate set with NO prior tuple of the name -> nothing emitted (never creates a fan-out)",
              out is calls and "call_typed" not in caller_node(nodes) and st["candidate_skipped"] == 2,
              (keyed(T, out), st["candidate_skipped"]))
    files, facts, nodes, calls = go_world(T, b_ann="union", a_site=False, calls=[("a::caller", "B.go")])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("E a candidate set over the engine's SINGLE pick (`B.go`) never widens it: B.go only, AMBIGUOUS",
              caller_node(nodes).get("call_typed") == {"a::B.go": "AMBIGUOUS"} and st["candidate_skipped"] == 1,
              (caller_node(nodes).get("call_typed"), st["candidate_skipped"]))
    files, facts, nodes, calls = go_world(T, b_ann="union", calls=[("a::caller", "other")])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("E a singular site of A.go beside the set, no prior: A.go added (EXTRACTED), B.go not",
              caller_node(nodes).get("call_typed") == {"a::A.go": "EXTRACTED"}, caller_node(nodes).get("call_typed"))
    files, facts, nodes, calls = go_world(T, b_ann="union")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("E with a prior tuple: A.go keeps the singular site's EXTRACTED, B.go AMBIGUOUS",
              caller_node(nodes).get("call_typed") == {"a::A.go": "EXTRACTED", "a::B.go": "AMBIGUOUS"},
              caller_node(nodes).get("call_typed"))
    # |T| > 4
    names = ["C1", "C2", "C3", "C4", "C5"]
    lines = []
    classes, nodes = [], []
    for i, nm in enumerate(names):
        lines += [f"class {nm}:", "    def go(self):", "        return 1"]
        classes.append(T.ClassFacts(nm, 3 * i + 1, 3 * i + 3, methods={"go": [T.MethodFacts(3 * i + 2)]}))
        nodes += [nd(f"m::{nm}", "Class", nm, "m.py", 3 * i + 1), nd(f"m::{nm}.go", "Method", f"{nm}.go", "m.py",
                                                                        3 * i + 2)]
    cl = len(lines) + 1
    lines += ["def caller(u):", "    u.go()"]
    text = "\n".join(lines) + "\n"
    ut = T.t_union(*[T.t_name(n) for n in names])
    f = T.FileFacts("m.py", "python", classes=classes, functions={"caller": [T.FunctionFacts("caller", cl)]},
                    scopes=[T.Scope("caller", cl, cl + 1, params={"u": T.Binding("param", ut)},
                                    sites=[T.Site(cl + 1, 4, "go", T.r_ident("u"), "member", 0)])],
                    decls=decls_of(text))
    nodes.append(nd("m::caller", "Function", "caller", "m.py", cl))
    calls = [("m::caller", "go")]
    out, st = link(T, {"m.py": text}, {"m.py": f}, nodes, calls, ON)
    rec.check("E |T| = 5 > 4 -> unknown: nothing emitted, the fan-out kept (ADD)", out is calls
              and st["too_many"] == 1 and st["vetoes"]["unknown_site"] == 1, (keyed(T, out), st["too_many"]))
    f.scopes[0].params["u"] = T.Binding("param", T.t_union(*[T.t_name(n) for n in names[:4]]))
    nodes = [dict(n) for n in nodes]
    out, st = link(T, {"m.py": text}, {"m.py": f}, nodes, calls, ON)
    rec.check("E |T| = 4 is a candidate set (narrows the fan-out, all AMBIGUOUS)",
              sorted((caller_node(nodes).get("call_typed") or {}).values()) == ["AMBIGUOUS"] * 4,
              caller_node(nodes).get("call_typed"))


# ================================================================================================
# F: the caller policy (tests and non-production callers: corrections only)
# ================================================================================================

def sec_policy(T, rec: Rec) -> None:
    def is_test(p):
        parts = p.split("/")
        return any(x in ("tests", "test") for x in parts[:-1]) or parts[-1].startswith("test_")

    for rel, prod in (("tests/test_a.py", False), ("examples/a.py", False), ("docs/a.py", False),
                      ("docs_src/a.py", False), ("pkg/Examples/a.py", False), ("demos/x/a.py", False),
                      ("pkg/examples.py", True), ("src/docs_parser.py", True), ("mydocs/a.py", True),
                      ("pkg/example_app.py", True), ("samplesx/a.py", True)):
        files, facts, nodes, calls = go_world(T, rel=rel, b_ann="B", calls=[])
        stem = rel.rsplit("/", 1)[-1].split(".")[0]
        calls = [(f"{stem}::caller", "other")]
        out, st = link(T, files, facts, nodes, calls, ON, is_test=is_test)
        got = caller_node(nodes).get("call_typed")
        if prod:
            rec.check(f"F {rel}: production -> pure additions allowed", got and len(got) == 2, got)
        else:
            rec.check(f"F {rel}: corrections only -> no pure addition (skipped_policy)", got is None and out is calls
                      and st["skipped_policy"] == 2, (got, st["skipped_policy"]))
    files, facts, nodes, calls = go_world(T, rel="tests/test_a.py", b_ann="B")
    out, st = link(T, files, facts, nodes, calls, ON, is_test=is_test)
    rec.check("F a test caller WITH a prior tuple of the name: the correction applies (REPLACE + keys)",
              st["removed"] == 2 and caller_node(nodes).get("call_typed") == {"test_a::A.go": "EXTRACTED",
                                                                              "test_a::B.go": "EXTRACTED"},
              (st["removed"], caller_node(nodes).get("call_typed")))
    files, facts, nodes, calls = go_world(T, rel="tests/test_a.py", b_ann="str", a_site=False)
    out, st = link(T, files, facts, nodes, calls, ON, is_test=is_test)
    rec.check("F removals still apply to a test caller", st["removed"] == 2, st["removed"])
    for env, rel in (({"DEEPMAP_TYPED_CALLS_TESTS": "1"}, "tests/test_a.py"),
                     ({"DEEPMAP_TYPED_CALLS_NONPROD": "1"}, "examples/a.py")):
        files, facts, nodes, calls = go_world(T, rel=rel, b_ann="B")
        stem = rel.rsplit("/", 1)[-1].split(".")[0]
        calls = [(f"{stem}::caller", "other")]
        out, st = link(T, files, facts, nodes, calls, dict(ON, **env), is_test=is_test)
        rec.check(f"F {env}: {rel} gets pure additions", len(caller_node(nodes).get("call_typed") or {}) == 2)
    files, facts, nodes, calls = go_world(T, rel="docs/tests/a.py", b_ann="B")
    calls = [("a::caller", "other")]
    out, st = link(T, files, facts, nodes, calls, dict(ON, DEEPMAP_TYPED_CALLS_TESTS="1"), is_test=is_test)
    rec.check("F a caller that is both test and non-production needs both opt-ins", out is calls)

    def boom(p):
        raise RuntimeError("no rule")

    files, facts, nodes, calls = go_world(T, b_ann="B")
    calls = [("a::caller", "other")]
    out, st = link(T, files, facts, nodes, calls, ON, is_test=boom)
    rec.check("F a test rule that raises -> corrections only, never an error", out is calls and st["error"] is None,
              st["error"])
    rec.check("F is_nonprod_path: directory components only, case-insensitive",
              T.is_nonprod_path("a/DOCS/b.py") and not T.is_nonprod_path("docs.py")
              and not T.is_nonprod_path("a/b/sample.py") and T.is_nonprod_path("sample/b.py"))
    try:
        dflt = T._default_is_test()
        import sync as S
        paths = ["tests/x.py", "src/contest.py", "a/test_b.py", "pkg/Latest.java", "pkg/UserTest.java",
                 "a/b.spec.ts", "a/b_test.go", "lib/latest.js", "x/__tests__/y.js"]
        same = dflt is not None and all(dflt(p) == S._is_test_path(p) for p in paths)
    except Exception as exc:  # noqa: BLE001
        same = repr(exc)
    rec.check("F the default test rule IS sync._is_test_path (one rule; KL-architecture:002)", same is True, same)


# ================================================================================================
# G: the audit predicate (design 2.7)
# ================================================================================================

AUD_LINES = [
    "class A:",                  # 1
    "    def notify(self):",     # 2
    "        return 1",          # 3
    "",                          # 4
    "    def helper(self, b):",  # 5
    "        self.notify()",     # 6
    "        b.notify()",        # 7
    "",                          # 8
    "",                          # 9
    "class B:",                  # 10
    "    def notify(self):",     # 11
    "        return 3",          # 12
    "",                          # 13
    "",                          # 14
    "class Sub(A):",             # 15
    "    pass",                  # 16
    "",                          # 17
    "",                          # 18
    "def caller(nt, a: A, b2: B):",  # 19
    "    STMT",                  # 20
    "    return 0",              # 21
]


def aud_world(T, stmt_sites, *, callee=None, extra_methods=True, rel="svc/n.py"):
    """stmt_sites: [(text, Site)] put on line 20 (joined by `; `)."""
    lines = list(AUD_LINES)
    lines[19] = "    " + "; ".join(t for t, _s in stmt_sites)
    text = "\n".join(lines) + "\n"
    sites = [s for _t, s in stmt_sites]
    an = {}
    for s in sites:
        an[s.member] = an.get(s.member, 0) + 1
    f = T.FileFacts(rel, "python",
                    classes=[T.ClassFacts("A", 1, 7, methods={"notify": [T.MethodFacts(2)],
                                                              "helper": [T.MethodFacts(5)]}),
                             T.ClassFacts("B", 10, 12, methods={"notify": [T.MethodFacts(11)]}),
                             T.ClassFacts("Sub", 15, 16, bases=(T.t_name("A"),))],
                    functions={"caller": [T.FunctionFacts("caller", 19)]},
                    scopes=[T.Scope("notify", 2, 3, cls=0, self_kind="inst"),
                            T.Scope("helper", 5, 7, cls=0, self_kind="inst", params={"b": T.Binding("param")},
                                    sites=[T.Site(6, 8, "notify", T.R_SELF, "member", 0),
                                           T.Site(7, 8, "notify", T.r_ident("b"), "member", 0)],
                                    all_names={"notify": 2}),
                            T.Scope("notify", 11, 12, cls=1, self_kind="inst"),
                            T.Scope("caller", 19, 21, params={"nt": T.Binding("param"),
                                                              "a": T.Binding("param", T.t_name("A")),
                                                              "b2": T.Binding("param", T.t_name("B"))},
                                    sites=sites, all_names=an)],
                    decls=decls_of(text))
    nodes = [nd("n::A", "Class", "A", rel, 1), nd("n::A.notify", "Method", "A.notify", rel, 2),
             nd("n::A.helper", "Method", "A.helper", rel, 5), nd("n::B", "Class", "B", rel, 10),
             nd("n::Sub", "Class", "Sub", rel, 15), nd("n::caller", "Function", "caller", rel, 19)]
    if extra_methods:
        nodes.insert(4, nd("n::B.notify", "Method", "B.notify", rel, 11))
    else:
        f.classes[1].methods.pop("notify")
    if callee is None:
        callee = T._call_key("n::A.notify")
    calls = [("n::caller", callee), ("n::A.helper", T._call_key("n::A.notify"))]
    return {rel: text}, {rel: f}, nodes, calls


def sec_audit(T, rec: Rec) -> None:
    AUD = {"DEEPMAP_CALLS_AUDIT": "python"}
    S = T.Site
    nt = ("nt.notify()", S(20, 4, "notify", T.r_ident("nt"), "member", 0))

    def weak(nodes, name="caller"):
        return next(n for n in nodes if n["name"] == name).get("call_weak")

    files, facts, nodes, calls = aud_world(T, [nt])
    out, st = link(T, files, facts, nodes, calls, AUD)
    rec.check("G+ an untyped `nt.notify()` with the engine's in-file pick A.notify and 2 notify methods -> "
              "call_weak [A.notify]", weak(nodes) == ["n::A.notify"] and st["audit_weak"] == 1, weak(nodes))
    rec.check("G+ audit-only (TYPED off): the SAME list object — grade-only, no edge added or removed",
              out is calls and all("call_typed" not in n for n in nodes))
    rec.check("G- a SELF site in the caller (`self.notify(); b.notify()`) keeps the pick's grade",
              weak(nodes, "A.helper") is None, weak(nodes, "A.helper"))
    files, facts, nodes, calls = aud_world(T, [nt], callee="A.notify")
    link(T, files, facts, nodes, calls, AUD)
    rec.check("G+ a qualified engine name `A.notify` (the default-mode pick) is audited the same way",
              weak(nodes) == ["n::A.notify"], weak(nodes))
    files, facts, nodes, calls = aud_world(T, [nt], callee="notify")
    link(T, files, facts, nodes, calls, AUD)
    rec.check("G- a bare name fan-out tuple is never audited (it is AMBIGUOUS already)", weak(nodes) is None)
    for desc, sites in (
            ("a class reference naming the owner (`A.notify(nt)`)", [("A.notify(nt)", S(20, 4, "notify",
                                                                                         T.r_ident("A"), "member",
                                                                                         1))]),
            ("a class reference naming a subclass (`Sub.notify(nt)`)", [("Sub.notify(nt)", S(20, 4, "notify",
                                                                                               T.r_ident("Sub"),
                                                                                               "member", 1))]),
            ("a bare `notify()` beside the untyped site", [nt, ("notify()", S(20, 17, "notify", None, "bare", 0))]),
            ("a site typed to the pick (`a: A; a.notify()`) beside the untyped one",
             [nt, ("a.notify()", S(20, 17, "notify", T.r_ident("a"), "member", 0))]),
            ("a `super().notify()` site", [nt, ("super().notify()", S(20, 17, "notify", T.R_SUPER, "member", 0))])):
        files, facts, nodes, calls = aud_world(T, sites)
        link(T, files, facts, nodes, calls, AUD)
        rec.check(f"G- {desc} -> not demoted", weak(nodes) is None, weak(nodes))
    files, facts, nodes, calls = aud_world(T, [("b2.notify()", S(20, 4, "notify", T.r_ident("b2"), "member", 0))])
    link(T, files, facts, nodes, calls, AUD)
    rec.check("G+ a site typed to ANOTHER class (`b2: B; b2.notify()`, the requests s.send() shape) -> demoted",
              weak(nodes) == ["n::A.notify"], weak(nodes))
    files, facts, nodes, calls = aud_world(T, [nt], extra_methods=False)
    link(T, files, facts, nodes, calls, AUD)
    rec.check("G- only ONE notify :Method in the family -> not demoted", weak(nodes) is None, weak(nodes))
    files, facts, nodes, calls = aud_world(T, [nt])
    nodes.append(nd("o::X.notify", "Method", "X.notify", "other/x.py", 3))
    calls[0] = ("n::caller", T._call_key("o::X.notify"))
    link(T, files, facts, nodes, calls, AUD)
    rec.check("G- the pick in ANOTHER file -> not demoted", weak(nodes) is None, weak(nodes))
    files, facts, nodes, calls = aud_world(T, [nt])
    link(T, files, facts, nodes, calls, {})
    rec.check("G- DEEPMAP_CALLS_AUDIT unset -> no call_weak", all("call_weak" not in n for n in nodes))
    files, facts, nodes, calls = aud_world(T, [nt, ("a.notify()", S(20, 17, "notify", T.r_ident("a"), "member", 0))])
    link(T, files, facts, nodes, calls, dict(AUD, DEEPMAP_TYPED_CALLS="python"))
    c = caller_node(nodes)
    rec.check("G TYPED + audit: call_weak never holds a key of the caller's call_typed",
              not set(c.get("call_weak") or ()) & set(c.get("call_typed") or {}), (c.get("call_weak"),
                                                                                   c.get("call_typed")))


# ================================================================================================
# H: the grading table + the evidence chain; shapes
# ================================================================================================

SVC_LINES = [
    "from app.models import Repo",          # 1
    "from app.base import Base",            # 2
    "",                                     # 3
    "",                                     # 4
    "class Local:",                         # 5
    "    def ping(self):",                  # 6
    "        return 1",                     # 7
    "",                                     # 8
    "",                                     # 9
    "def make() -> Local:",                 # 10
    "    return Local()",                   # 11
    "",                                     # 12
    "",                                     # 13
    "class Plain:",                         # 14
    "    def __init__(self, inj: Local):",  # 15
    "        self.inj = inj",               # 16
    "",                                     # 17
    "    @property",                        # 18
    "    def prop(self) -> Local:",         # 19
    "        return Local()",               # 20
    "",                                     # 21
    "    def helper(self):",                # 22
    "        return 0",                     # 23
    "",                                     # 24
    "    def run_r(self, r: Repo):",        # 25
    "        r.find()",                     # 26
    "",                                     # 27
    "    def run_l(self, l: Local):",       # 28
    "        l.ping()",                     # 29
    "",                                     # 30
    "    def run_inj(self):",               # 31
    "        self.inj.ping()",              # 32
    "",                                     # 33
    "    def run_prop(self):",              # 34
    "        self.prop.ping()",             # 35
    "",                                     # 36
    "    def run_ret(self):",               # 37
    "        x = make()",                   # 38
    "        x.ping()",                     # 39
    "",                                     # 40
    "    def run_chain(self):",             # 41
    "        make().ping()",                # 42
    "",                                     # 43
    "    def run_self(self):",              # 44
    "        self.helper()",                # 45
    "",                                     # 46
    "",                                     # 47
    "class Svc(Base):",                     # 48
    "    def run_base(self):",              # 49
    "        self.local.ping()",            # 50
    "",                                     # 51
    "",                                     # 52
    "class Sub(Plain):",                    # 53
    "    def helper(self):",                # 54
    "        return super().helper()",      # 55
]
BASE_LINES = ["from app.svc import Local", "", "", "class Base:", "    def __init__(self):",
              "        self.local = Local()"]
MODELS_LINES = ["class Repo:", "    def find(self):", "        return 1"]


def grade_world(T):
    svc, base, models = "app/svc.py", "app/base.py", "app/models.py"
    texts = {svc: "\n".join(SVC_LINES) + "\n", base: "\n".join(BASE_LINES) + "\n",
             models: "\n".join(MODELS_LINES) + "\n"}
    B, S, ri, sv = T.Binding, T.Site, T.r_ident, T.t_name

    def meth(name, line, end, cls, sites=(), params=None, locals_=None):
        return T.Scope(name, line, end, cls=cls, self_kind="inst", params=params or {}, locals=locals_ or {},
                       sites=list(sites), all_names={s.member: sum(1 for x in sites if x.member == s.member)
                                                     for s in sites})
    scopes = [
        T.Scope("make", 10, 11, sites=[S(11, 11, "Local", None, "bare", 0)], all_names={"Local": 1}),   # 0
        meth("__init__", 15, 16, 1, params={"inj": B("param", sv("Local"))}),                          # 1
        T.Scope("prop", 19, 20, lines=(18,), cls=1, self_kind="inst",
                sites=[S(20, 15, "Local", None, "bare", 0)], all_names={"Local": 1}),                      # 2
        meth("helper", 22, 23, 1),                                                                     # 3
        meth("run_r", 25, 26, 1, [S(26, 8, "find", ri("r"), "member", 0)], {"r": B("param", sv("Repo"))}),
        meth("run_l", 28, 29, 1, [S(29, 8, "ping", ri("l"), "member", 0)], {"l": B("param", sv("Local"))}),
        meth("run_inj", 31, 32, 1, [S(32, 8, "ping", T.r_field(T.R_SELF, "inj"), "member", 0)]),
        meth("run_prop", 34, 35, 1, [S(35, 8, "ping", T.r_field(T.R_SELF, "prop"), "member", 0)]),
        meth("run_ret", 37, 39, 1, [S(38, 12, "make", None, "bare", 0), S(39, 8, "ping", ri("x"), "member", 0)],
             locals_={"x": [B("call", value=T.r_call(None, "make"))]}),
        meth("run_chain", 41, 42, 1, [S(42, 8, "make", None, "bare", 0),
                                      S(42, 8, "ping", T.r_call(None, "make"), "member", 0)]),
        meth("run_self", 44, 45, 1, [S(45, 8, "helper", T.R_SELF, "member", 0)]),
        meth("run_base", 49, 50, 2, [S(50, 8, "ping", T.r_field(T.R_SELF, "local"), "member", 0)]),
        meth("helper", 54, 55, 4, [S(55, 15, "helper", T.R_SUPER, "member", 0)]),
        meth("ping", 6, 7, 0),
    ]
    svc_f = T.FileFacts(svc, "python",
                        imports={"Repo": [T.Import("Repo", "named", "app.models", "Repo", 1)],
                                 "Base": [T.Import("Base", "named", "app.base", "Base", 2)]},
                        classes=[T.ClassFacts("Local", 5, 7, methods={"ping": [T.MethodFacts(6)]}),
                                 T.ClassFacts("Plain", 14, 45, fields={"inj": [B("field", value=ri("inj"), scope=1)]},
                                              methods={"__init__": [T.MethodFacts(15)],
                                                       "prop": [T.MethodFacts(19, frozenset({"property"}),
                                                                              sv("Local"), lines=(18,))],
                                                       "helper": [T.MethodFacts(22)],
                                                       **{n: [T.MethodFacts(ln)] for n, ln in (
                                                           ("run_r", 25), ("run_l", 28), ("run_inj", 31),
                                                           ("run_prop", 34), ("run_ret", 37), ("run_chain", 41),
                                                           ("run_self", 44))}}),
                                 T.ClassFacts("Svc", 48, 50, bases=(sv("Base"),),
                                              methods={"run_base": [T.MethodFacts(49)]}),
                                 T.ClassFacts("Unused", 0, 0),
                                 T.ClassFacts("Sub", 53, 55, bases=(sv("Plain"),),
                                              methods={"helper": [T.MethodFacts(54)]})],
                        functions={"make": [T.FunctionFacts("make", 10, sv("Local"))]},
                        scopes=scopes, decls=decls_of(texts[svc]))
    base_f = T.FileFacts(base, "python", imports={"Local": [T.Import("Local", "named", "app.svc", "Local", 1)]},
                         classes=[T.ClassFacts("Base", 4, 6, methods={"__init__": [T.MethodFacts(5)]},
                                               fields={"local": [B("call", value=T.r_call(None, "Local"),
                                                                   scope=0)]})],
                         scopes=[T.Scope("__init__", 5, 6, cls=0, self_kind="inst",
                                         sites=[S(6, 21, "Local", None, "bare", 0)], all_names={"Local": 1})],
                         decls=decls_of(texts[base]))
    models_f = T.FileFacts(models, "python", classes=[T.ClassFacts("Repo", 1, 3, methods={"find": [T.MethodFacts(2)]})],
                           scopes=[T.Scope("find", 2, 3, cls=0, self_kind="inst")], decls=decls_of(texts[models]))
    binder = FBinder(T, imports={(svc, "Repo"): T.Resolution("symbol", models, "Repo"),
                                 (svc, "Base"): T.Resolution("symbol", base, "Base"),
                                 (base, "Local"): T.Resolution("symbol", svc, "Local")})
    nodes = [nd("s", "Module", "svc", svc, 1), nd("s::Local", "Class", "Local", svc, 5),
             nd("s::Local.ping", "Method", "Local.ping", svc, 6), nd("s::make", "Function", "make", svc, 10),
             nd("s::Plain", "Class", "Plain", svc, 14), nd("s::Plain.__init__", "Method", "Plain.__init__", svc, 15),
             nd("s::Plain.prop", "Method", "Plain.prop", svc, 18),
             nd("s::Plain.helper", "Method", "Plain.helper", svc, 22)]
    for name, ln in (("run_r", 25), ("run_l", 28), ("run_inj", 31), ("run_prop", 34), ("run_ret", 37),
                     ("run_chain", 41), ("run_self", 44)):
        nodes.append(nd(f"s::Plain.{name}", "Method", f"Plain.{name}", svc, ln))
    nodes += [nd("s::Svc", "Class", "Svc", svc, 48), nd("s::Svc.run_base", "Method", "Svc.run_base", svc, 49),
              nd("s::Sub", "Class", "Sub", svc, 53), nd("s::Sub.helper", "Method", "Sub.helper", svc, 54),
              nd("b::Base", "Class", "Base", base, 4), nd("b::Base.__init__", "Method", "Base.__init__", base, 5),
              nd("m::Repo", "Class", "Repo", models, 1), nd("m::Repo.find", "Method", "Repo.find", models, 2)]
    return texts, {svc: svc_f, base: base_f, models: models_f}, nodes, binder


def sec_grades(T, rec: Rec) -> None:
    texts, facts, nodes, binder = grade_world(T)
    out, st = link(T, texts, facts, nodes, [], ON, binder=binder)
    by = {n["key"]: n.get("call_typed") for n in nodes}
    expect = {
        "s::Plain.run_l": ({"s::Local.ping": "EXTRACTED"}, "R1 param, in-file chain"),
        "s::Plain.run_self": ({"s::Plain.helper": "EXTRACTED"}, "S self, in-file MRO"),
        "s::Sub.helper": ({"s::Plain.helper": "EXTRACTED"}, "S super() -> the next class of the MRO"),
        "s::Plain.run_r": ({"m::Repo.find": "INFERRED"}, "R1 param of an imported class (cross-file target)"),
        "s::Svc.run_base": ({"s::Local.ping": "INFERRED"}, "R3 field bound in a base class in another file "
                                                            "(cross-file EVIDENCE, in-file target)"),
        "s::Plain.run_inj": ({"s::Local.ping": "INFERRED"}, "R3 injected field `self.inj = inj`"),
        "s::Plain.run_prop": ({"s::Local.ping": "INFERRED"}, "R3 a property's return annotation"),
        "s::Plain.run_ret": ({"s::Local.ping": "INFERRED"}, "R4 `x = make(); x.ping()`"),
        "s::Plain.run_chain": ({"s::Local.ping": "INFERRED"}, "R4 `make().ping()`"),
        "s::make": ({"s::Local": "EXTRACTED"}, "C `Local()` -> the :Class node"),
        "s::Plain.prop": ({"s::Local": "EXTRACTED"}, "C in a property body"),
        "b::Base.__init__": ({"s::Local": "INFERRED"}, "C of an imported class"),
    }
    for key, (want, why) in expect.items():
        rec.check(f"H {why}: {want}", by.get(key) == want, by.get(key))
    rec.check("H no other caller got a typed key", {k for k, v in by.items() if v} == set(expect),
              sorted(k for k, v in by.items() if v))
    grades = {g for v in by.values() if v for g in v.values()}
    rec.check("H every grade is one of the three strings", grades <= set(T.GRADES), grades)
    rec.check("H run_ret: the bare `make()` (a function) is NOT typed (bare calls stay with the engine)",
              "s::make" not in (by.get("s::Plain.run_ret") or {}))
    sh = {s: v["emitted"] for s, v in st["by_shape"].items() if v["emitted"]}
    rec.check("H by_shape attributes each emitted key to its shape",
              sh == {"R1": 2, "R3": 3, "R4": 2, "S": 2, "C": 3}, sh)
    # ---- module receivers, the external test, proxies
    util, svc2 = "app/util.py", "app/svc2.py"
    lines = ["import app.util as U", "import json", "import re", "from flask_login import current_user",
             "from json import dumps", "", "", "def use(v):", "    U.run(v)", "    json.dumps(v)",
             "    re.search('a', v)", "    current_user.save()", "    dumps(v)", "    return 0"]
    text = "\n".join(lines) + "\n"
    S = T.Site
    imps = {"U": [T.Import("U", "module", "app.util")], "json": [T.Import("json", "module", "json")],
            "re": [T.Import("re", "module", "re")],
            "current_user": [T.Import("current_user", "named", "flask_login", "current_user")],
            "dumps": [T.Import("dumps", "named", "json", "dumps")]}

    def world(extra_nodes=()):
        f2 = T.FileFacts(svc2, "python", imports=imps, functions={"use": [T.FunctionFacts("use", 8)]},
                         scopes=[T.Scope("use", 8, 14, params={"v": T.Binding("param")}, sites=[
                             S(9, 4, "run", T.r_ident("U"), "member", 1),
                             S(10, 4, "dumps", T.r_module("json"), "member", 1),
                             S(11, 4, "search", T.r_ident("re"), "member", 2),
                             S(12, 4, "save", T.r_ident("current_user"), "member", 0),
                             S(13, 4, "dumps", None, "bare", 1)])],
                         decls=decls_of(text))
        f1 = T.FileFacts(util, "python", functions={"run": [T.FunctionFacts("run", 1)]},
                         scopes=[T.Scope("run", 1, 2)], decls=decls_of("def run(v):\n    return v\n"))
        b = FBinder(T, imports={(svc2, "U"): T.Resolution("module", util),
                                (svc2, "json"): T.Resolution("external", stems=("json",)),
                                (svc2, "re"): T.Resolution("external", stems=("re",)),
                                (svc2, "current_user"): T.Resolution("external", stems=("flask_login",)),
                                (svc2, "dumps"): T.Resolution("external", stems=("json",))})
        nodes = [nd("u::run", "Function", "run", util, 1), nd("v::use", "Function", "use", svc2, 8),
                 *extra_nodes]
        calls = [("v::use", "run"), ("v::use", "dumps"), ("v::use", "search"), ("v::use", "save")]
        return ({svc2: text, util: "def run(v):\n    return v\n"}, {svc2: f2, util: f1}, nodes, calls, b)

    files, facts2, nodes, calls, b = world()
    out, st = link(T, files, facts2, nodes, calls, ON, binder=b)
    use = caller_node(nodes, "use")
    rec.check("H M `U.run()` through `import app.util as U` -> the module function, INFERRED",
              use.get("call_typed") == {"u::run": "INFERRED"}, use.get("call_typed"))
    rec.check("H external: `json.dumps(v)` + bare `dumps(v)` and `re.search()` -> their name tuples are removed",
              ("v::use", "dumps") not in out and ("v::use", "search") not in out, keyed(T, out))
    rec.check("H a member call on an imported external OBJECT (`current_user.save()`, a proxy) stays UNKNOWN",
              ("v::use", "save") in out, keyed(T, out))
    files, facts2, nodes, calls, b = world([nd("j::x", "Module", "x", "app/json/x.py", 1)])
    out, st = link(T, files, facts2, nodes, calls, ON, binder=b)
    rec.check("H the conservative external test: `json` is a project directory -> UNKNOWN, the tuple stays "
              "(flask's json/provider.py shape)", ("v::use", "dumps") in out and ("v::use", "search") not in out,
              keyed(T, out))


# ================================================================================================
# S: design §5's must-not shapes as the core sees them (what a reader emits for each) — no typed edge AND no
#    removal — each beside a positive sibling
# ================================================================================================

def one(T, classes, scopes, *, rel="pkg/m.py", lang="python", imports=None, functions=None, globals_=None,
        patches=(), binder=None, env=None, calls="sites", extra_nodes=(), lines=40, more=None):
    """Run one file through the core: nodes made from the facts (Class / Method / Function); the caller is the
    scope named `caller`; calls = one name tuple per site member ("sites") or the given list."""
    stem = rel.rsplit("/", 1)[-1].split(".")[0]
    nodes = []
    for c in classes:
        nodes.append(nd(f"{stem}:{c.name}:{c.line}", "Class", c.name, rel, c.line))
        for m, mfs in c.methods.items():
            for mf in mfs:
                nodes.append(nd(f"{stem}:{c.name}.{m}:{mf.line}", "Method", f"{c.name}.{m}", rel, mf.line))
    for si, sc in enumerate(scopes):
        if sc.cls < 0:
            nodes.append(nd(f"{stem}:{sc.name}:{sc.line}", "Function", sc.name, rel, sc.line))
    nodes += list(extra_nodes)
    text_lines = ["pass"] * lines
    for sc in scopes:
        for s in sc.sites:
            text_lines[s.line - 1] = f"    x.{s.member}(1)" if s.kind == "member" else f"    {s.member}(1)"
    text = "\n".join(text_lines) + "\n"
    f = T.FileFacts(rel, lang, imports=imports or {}, classes=list(classes), functions=functions or {},
                    globals=globals_ or {}, scopes=list(scopes), patches=list(patches))
    caller = next(n for n in nodes if n["name"] == "caller")
    if calls == "sites":
        calls = [(caller["key"], s.member) for sc in scopes if sc.name == "caller" for s in sc.sites]
    out, st = T.link(".", nodes, calls, languages={lang: T.LANGUAGES[lang]},
                     readers={lang: FReader(T, {rel: f, **{r: v[0] for r, v in (more or {}).items()}},
                                            binder or FBinder(T))},
                     source=Src({rel: text, **{r: v[1] for r, v in (more or {}).items()}}),
                     is_test=lambda p: False, env=env or {"DEEPMAP_TYPED_CALLS": lang})
    return out, st, calls, caller


def sec_mustnot(T, rec: Rec) -> None:
    B, S, MF, CF, SC = T.Binding, T.Site, T.MethodFacts, T.ClassFacts, T.Scope
    A2 = [CF("A", 1, 3, methods={"m": [MF(2)]}), CF("B", 5, 7, methods={"m": [MF(6)]})]

    def caller(sites, params=None, locals_=None, poisoned=frozenset(), type_params=frozenset(), line=20):
        return SC("caller", line, line + 8, params=params or {}, locals=locals_ or {}, poisoned=poisoned,
                  sites=sites, type_params=type_params)

    def mustnot(desc, out, st, calls, c):
        rec.check(f"S- {desc}: no typed edge, no removal", out is calls and "call_typed" not in c
                  and st["files"] >= 1 and not st["files_failed"] and st["error"] is None and calls,
                  (keyed(T, out), c.get("call_typed"), st["files"], st["files_failed"], st["error"]))

    def typed(desc, c, want):
        rec.check(f"S+ {desc}", sorted(c.get("call_typed") or {}) == sorted(want), c.get("call_typed"))

    x_m = lambda line=21: S(line, 4, "m", T.r_ident("x"), "member", 0)   # noqa: E731
    # rebinding
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("call", value=T.r_call(None, "A")),
                                                                     B("call", value=T.r_call(None, "make"))]})])
    mustnot("`x = A(); x = make(); x.m()`", out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("call", value=T.r_call(None, "A"))]},
                                           poisoned=frozenset({"x"}))])
    mustnot("a poisoned local (for / with / except / del / walrus / global / tuple target)", out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("call", value=T.r_call(None, "A")),
                                                                     B("call", value=T.r_call(None, "B"))]})],
                            calls=[])
    rec.check("S- `if c: x = A() else: x = B()` with no prior tuple: nothing (a candidate set never creates)",
              out == [] and "call_typed" not in c, c.get("call_typed"))
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("call", value=T.r_call(None, "A")),
                                                                     B("call", value=T.r_call(None, "B"))]})])
    typed("`if c: x = A() else: x = B()` narrowing a fan-out -> a candidate set", c,
          ["m:A.m:2", "m:B.m:6"])
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("call", value=T.r_call(None, "A")),
                                                                     B("none")]})], calls=[])
    typed("`x = None` beside `x = A()` is ignored (nullable)", c, ["m:A.m:2"])
    # annotations
    for desc, ann in (("`x: Any`", T.t_name("Any")), ("`x: object`", T.t_name("object")),
                      ("`x: A | str`", T.t_union(T.t_name("A"), T.t_name("str"))),
                      ("`Union[A, Any]`", T.t_union(T.t_name("A"), T.t_name("Any"))),
                      ("`List[A]` (`xs.m` on a container)", T.t_generic(T.t_name("List"), T.t_name("A"))),
                      ("an unannotated parameter", None), ("an unreadable annotation", T.T_UNKNOWN)):
        imps = {"Any": [T.Import("Any", "named", "typing", "Any")], "List": [T.Import("List", "named", "typing",
                                                                                      "List")]}
        bd = FBinder(T, imports={("pkg/m.py", "Any"): T.Resolution("external", stems=("typing",)),
                                 ("pkg/m.py", "List"): T.Resolution("external", stems=("typing",))})
        out, st, calls, c = one(T, A2, [caller([x_m()], params={"x": B("param", ann)})], imports=imps, binder=bd)
        mustnot(desc, out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([x_m()], params={"x": B("param", T.t_name("A"))}, type_params=frozenset(
        {"A"}))])
    mustnot("a type parameter named like a class (`def f[A](x: A)`)", out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([x_m()], params={"x": B("param", T.t_generic(T.t_name("A"),
                                                                                           T.t_name("B")))})],
                            calls=[])
    typed("a project generic `A[B]` binds its head A", c, ["m:A.m:2"])
    # ambiguous resolution
    mod_text = "class A:\n    def m(self):\n        return 1\n"
    more = {r: (T.FileFacts(r, "python", classes=[CF("A", 1, 3, methods={"m": [MF(2)]})]), mod_text)
            for r in ("a.py", "b.py")}
    other = [nd("o:A", "Class", "A", "a.py", 1), nd("o:A.m", "Method", "A.m", "a.py", 2),
             nd("o2:A", "Class", "A", "b.py", 1), nd("o2:A.m", "Method", "A.m", "b.py", 2)]
    bd = FBinder(T, imports={("pkg/m.py", "A", "a"): T.Resolution("symbol", "a.py", "A"),
                             ("pkg/m.py", "A", "b"): T.Resolution("symbol", "b.py", "A")})
    xa = [caller([x_m()], params={"x": B("param", T.t_name("A"))})]
    imps = {"A": [T.Import("A", "named", "a", "A")]}
    out, st, calls, c = one(T, [], xa, imports=imps, binder=bd, extra_nodes=other, more=more, calls=[])
    typed("`from a import A; x: A` binds a.A.m (INFERRED)", c, ["o:A.m"])
    rec.check("S+ ... graded INFERRED (the class lives in another file)", (c.get("call_typed") or {}).get("o:A.m")
              == "INFERRED", c.get("call_typed"))
    out, st, calls, c = one(T, [], xa, imports=imps, binder=bd, extra_nodes=other, more=more,
                            globals_={"A": [B("field", value=T.r_ident("object"))]})
    mustnot("`if TYPE_CHECKING: from a import A` / `else: A = object` (bound two ways)", out, st, calls, c)
    imps = {"A": [T.Import("A", "named", "a", "A"), T.Import("A", "named", "a", "A")]}
    out, st, calls, c = one(T, [], xa, imports=imps, binder=bd, extra_nodes=other, more=more, calls=[])
    typed("the same import twice (both branches agree) still binds", c, ["o:A.m"])
    imps = {"A": [T.Import("A", "named", "a", "A"), T.Import("A", "named", "b", "A")]}
    out, st, calls, c = one(T, [], xa, imports=imps, binder=bd, extra_nodes=other, more=more)
    mustnot("try / except ImportError giving two DIFFERENT classes named A", out, st, calls, c)
    # module receivers
    out, st, calls, c = one(T, A2, [caller([S(21, 4, "main", T.r_ident("CG"), "member", 0)])],
                            globals_={"CG": [B("call", value=T.r_call(None, "_load"))]},
                            functions={"_load": [T.FunctionFacts("_load", 30)]})
    mustnot("a path-loaded module `CG = _load('x'); CG.main()`", out, st, calls, c)
    # fields and attributes
    A_cb = [CF("Base", 1, 3, methods={"cb": [MF(2)]}),
            CF("A", 5, 9, bases=(T.t_name("Base"),), fields={"cb": [B("call", value=T.r_call(None, "Other"))]},
               methods={"m": [MF(6)]}),
            CF("Other", 11, 13, methods={"x": [MF(12)]})]
    out, st, calls, c = one(T, A_cb[:1], [caller([S(21, 4, "cb", T.r_ident("a"), "member", 0)],
                                                 params={"a": B("param", T.t_name("Base"))})], calls=[])
    typed("`b.cb()` on Base binds the method Base.cb", c, ["m:Base.cb:2"])
    out, st, calls, c = one(T, A_cb, [caller([S(21, 4, "cb", T.r_ident("a"), "member", 0)],
                                             params={"a": B("param", T.t_name("A"))})])
    mustnot("`a.cb()` where A's instance FIELD cb shadows the inherited method Base.cb", out, st, calls, c)
    A_prop = [CF("A", 1, 9, methods={"p": [MF(2, frozenset({"property"}), T.t_name("B"))]}),
              CF("B", 11, 13, methods={"p": [MF(12)]})]
    out, st, calls, c = one(T, A_prop, [caller([S(21, 4, "p", T.r_ident("a"), "member", 0)],
                                               params={"a": B("param", T.t_name("A"))})])
    mustnot("a property called as `a.p()`", out, st, calls, c)
    two = [CF("Svc", 1, 9, fields={"repo": [B("call", value=T.r_call(None, "A")), B("call", value=T.r_call(None,
                                                                                                         "B"))]}),
           *[CF(n, ln, ln + 2, methods={"m": [MF(ln + 1)]}) for n, ln in (("A", 11), ("B", 15))]]
    out, st, calls, c = one(T, two, [caller([S(21, 4, "m", T.r_field(T.r_ident("s"), "repo"), "member", 0)],
                                            params={"s": B("param", T.t_name("Svc"))})])
    mustnot("`self.repo` assigned two different classes", out, st, calls, c)
    two[0].fields["repo"] = [B("call", value=T.r_call(None, "A")), B("call", value=T.r_call(None, "A"))]
    out, st, calls, c = one(T, two, [caller([S(21, 4, "m", T.r_field(T.r_ident("s"), "repo"), "member", 0)],
                                            params={"s": B("param", T.t_name("Svc"))})], calls=[])
    typed("`self.repo` assigned the same class twice binds it", c, ["m:A.m:12"])
    two[0].fields["repo"] = [B("field", value=T.r_ident("repo"), scope=-1)]
    out, st, calls, c = one(T, two, [caller([S(21, 4, "m", T.r_field(T.r_ident("s"), "repo"), "member", 0)],
                                            params={"s": B("param", T.t_name("Svc"))})])
    mustnot("`self.repo = repo` from an untyped parameter", out, st, calls, c)
    # open / unknown classes
    for desc, cls in (("a dynamic class (__getattr__ / metaclass / unknown decorator)",
                       CF("A", 1, 3, methods={"m": [MF(2)]}, dynamic=True)),):
        out, st, calls, c = one(T, [cls], [caller([x_m()], params={"x": B("param", T.t_name("A"))})])
        mustnot(desc, out, st, calls, c)
    out, st, calls, c = one(T, [CF("A", 1, 3, methods={"m": [MF(2)], "k": [MF(3)]})],
                            [caller([x_m(), S(22, 4, "k", T.r_ident("x"), "member", 0)],
                                    params={"x": B("param", T.t_name("A"))})],
                            patches=[(T.t_name("A"), "m")], calls=[])
    typed("a module-level `A.m = patched` makes A.m unknown — and only A.m", c, ["m:A.k:3"])
    out, st, calls, c = one(T, [CF("A", 1, 3, bases=(T.t_name("dict"),), methods={"k": [MF(2)]})],
                            [SC("caller", 2, 8, cls=0, self_kind="inst",
                                sites=[S(3, 4, "m", T.R_SUPER, "member", 0)])],
                            extra_nodes=[nd("m:caller", "Method", "caller", "pkg/m.py", 2)])
    mustnot("`super().m()` over an external base", out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([x_m()], locals_={"x": [B("field", value=T.r_ident("A"))]})])
    mustnot("`x = A; x.m()` (a class object alias)", out, st, calls, c)
    dund = [CF("A", 1, 3, methods={"__enter__": [MF(2)]})]
    out, st, calls, c = one(T, dund, [caller([S(21, 4, "__enter__", T.r_ident("x"), "member", 0)],
                                             params={"x": B("param", T.t_name("A"))})])
    mustnot("a dunder (`x.__enter__()`)", out, st, calls, c)
    out, st, calls, c = one(T, A2, [caller([S(21, 4, "call", T.r_ident("fn"), "member", 0)])],
                            functions={"fn": [T.FunctionFacts("fn", 30)]})
    mustnot("`fn.call(...)` on a function value", out, st, calls, c)
    # a WRITTEN star import (`from lots import *`) rebinds at run time every name bound BEFORE it (review: the core
    # kept `from app.models import Order` under a later `from app.other import *` and typed models.Order.save)
    A3 = [CF("A", 11, 13, methods={"m": [MF(12)]}), CF("B", 15, 17, methods={"m": [MF(16)]})]
    out, st, calls, c = one(T, A3, [caller([x_m()], params={"x": B("param", T.t_name("A"))})],
                            imports={"*": [T.Import("*", "star", "lots", "*", 1)]}, calls=[])
    typed("a star import BEFORE the class leaves it resolvable (the class statement binds A last)", c,
          ["m:A.m:12"])
    for desc, star in (("a star import AFTER the class (it may rebind A)", T.Import("*", "star", "lots", "*", 18)),
                       ("a star import whose line is unknown", T.Import("*", "star", "lots"))):
        out, st, calls, c = one(T, A3, [caller([x_m()], params={"x": B("param", T.t_name("A"))})],
                                imports={"*": [star]})
        mustnot(desc, out, st, calls, c)
    out, st, calls, c = one(T, A3, [caller([x_m()], params={"x": B("param", T.t_name("A"))})],
                            imports={"*": [T.Import("*", "star", "", "", 0)]}, calls=[])
    typed("a spec-less star (a reader's marker of an unread region, no binding) leaves bound names alone", c,
          ["m:A.m:12"])
    mrel, mtext = "pkg/models.py", "class A:\n    def m(self):\n        return 1\n"
    mfacts = T.FileFacts(mrel, "python", classes=[CF("A", 1, 3, methods={"m": [MF(2)]})], scopes=[
        SC("m", 2, 3, cls=0, self_kind="inst", kind="method")])
    mnode = [nd("models:A:1", "Class", "A", mrel, 1), nd("models:A.m:2", "Method", "A.m", mrel, 2)]
    mbind = FBinder(T, imports={("pkg/m.py", "A"): T.Resolution("symbol", rel=mrel, name="A")})
    for desc, imp_line, star_line, want in (
            ("`from pkg.models import A` then `from lots import *`", 1, 2, None),
            ("`from lots import *` then `from pkg.models import A`", 2, 1, ["models:A.m:2"])):
        out, st, calls, c = one(T, [], [caller([x_m()], params={"x": B("param", T.t_name("A"))})],
                                imports={"A": [T.Import("A", "named", "pkg.models", "A", imp_line)],
                                         "*": [T.Import("*", "star", "lots", "*", star_line)]},
                                binder=mbind, extra_nodes=mnode, more={mrel: (mfacts, mtext)},
                                calls="sites" if want is None else [])
        if want is None:
            mustnot(desc, out, st, calls, c)
        else:
            typed(desc + ": the named import binds A last", c, want)
    out, st, calls, c = one(T, A2, [caller([S(21, 4, "len", None, "bare", 1)])],
                            imports={"*": [T.Import("*", "star", "lots")]})
    mustnot("under a star import an unbound `len()` is not builtin", out, st, calls, c)
    loc = [*A2, CF("A", 26, 27, scope=0, methods={"m": [MF(27)]})]
    out, st, calls, c = one(T, loc, [caller([x_m()], params={"x": B("param", T.t_name("A"))})])
    mustnot("an annotation naming a class the caller defines locally (shadowing the top-level A)", out, st, calls, c)
    a_m = S(21, 4, "m", T.r_ident("A"), "member", 0)
    out, st, calls, c = one(T, A2, [caller([a_m])], calls=[])
    typed("K `A.m()` on the top-level class binds A.m", c, ["m:A.m:2"])
    out, st, calls, c = one(T, loc, [caller([a_m])])
    mustnot("`A.m()` where the caller defines its own class A", out, st, calls, c)
    # an interface's declared member never runs (the phase-2 verification: flask's ProxyMixin stub): no key to it,
    # and never a fan-out to an implementer either — the engine's tuples stay as they are
    ifc = [CF("Repo", 1, 3, kind="interface", methods={"find": [MF(2)]}),
           CF("SqlRepo", 5, 7, methods={"find": [MF(6)]}), CF("MemRepo", 9, 11, methods={"find": [MF(10)]})]
    out, st, calls, c = one(T, ifc, [caller([S(21, 4, "find", T.r_ident("r"), "member", 0)],
                                            params={"r": B("param", T.t_name("Repo"))})],
                            rel="web/m.ts", lang="typescript")
    mustnot("TS `r: Repo` (an interface): neither the declared Repo.find (it never runs) nor SqlRepo.find", out, st,
            calls, c)
    out, st, calls, c = one(T, ifc, [caller([S(21, 4, "find", T.r_ident("r"), "member", 0)],
                                            params={"r": B("param", T.t_union(T.t_name("SqlRepo"),
                                                                                 T.t_name("MemRepo")))})],
                            rel="web/m.ts", lang="typescript")
    mustnot("TS `r: SqlRepo | MemRepo` (only `T | null | undefined` binds in TS)", out, st, calls, c)
    out, st, calls, c = one(T, ifc, [caller([S(21, 4, "find", T.r_ident("r"), "member", 0)],
                                            params={"r": B("param", T.t_nullable(T.t_name("SqlRepo")))})],
                            calls=[], rel="web/m.ts", lang="typescript")
    typed("TS `r: SqlRepo | null` binds SqlRepo", c, ["m:SqlRepo.find:6"])
    out, st, calls, c = one(T, ifc, [caller([S(21, 4, "find", T.r_ident("r"), "member", 0)],
                                            params={"r": B("param", T.t_nullable(T.t_name("Repo")))})],
                            rel="web/m.ts", lang="typescript")
    mustnot("TS `r: Repo | null` (an interface): no key", out, st, calls, c)
    out, st, calls, c = one(T, ifc, [caller([S(21, 4, "find", T.r_ident("r"), "member", 0)],
                                            locals_={"r": [B("ctor", T.t_name("SqlRepo")),
                                                           B("ctor", T.t_name("MemRepo"))]})],
                            rel="web/m.ts", lang="typescript")
    mustnot("TS `let r = new SqlRepo(); r = new MemRepo()` (one binding only)", out, st, calls, c)


# ================================================================================================
# I: never cross-family, never a key outside `nodes`, every callee a CallKey
# ================================================================================================

def sec_family(T, rec: Rec) -> None:
    py, ts = "pkg/a.py", "web/order.ts"
    py_text = "from web.order import Order\n\n\ndef caller(o: Order):\n    o.save()\n    Order()\n"
    ts_text = "export class Order {\n  save(): void {}\n}\n"
    fpy = T.FileFacts(py, "python", imports={"Order": [T.Import("Order", "named", "web.order", "Order", 1)]},
                      functions={"caller": [T.FunctionFacts("caller", 4)]},
                      scopes=[T.Scope("caller", 4, 6, params={"o": T.Binding("param", T.t_name("Order"))},
                                      sites=[T.Site(5, 4, "save", T.r_ident("o"), "member", 0),
                                             T.Site(6, 4, "Order", None, "bare", 0)])],
                      decls=decls_of(py_text))
    fts = T.FileFacts(ts, "typescript", classes=[T.ClassFacts("Order", 1, 3, methods={"save": [T.MethodFacts(2)]})],
                      decls=decls_of(ts_text))
    nodes = [nd("p::caller", "Function", "caller", py, 4), nd("w::Order", "Class", "Order", ts, 1),
             nd("w::Order.save", "Method", "Order.save", ts, 2)]
    calls = [("p::caller", "save"), ("p::caller", "Order")]
    bad = FBinder(T, imports={(py, "Order"): T.Resolution("symbol", ts, "Order")})
    readers = {"python": FReader(T, {py: fpy}, bad), "typescript": FReader(T, {ts: fts})}
    out, st = T.link(".", nodes, calls, languages=T.LANGUAGES, readers=readers,
                     source=Src({py: py_text, ts: ts_text}), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "python,typescript"})
    rec.check("I a binder that resolves a Python name into a TypeScript class never yields a cross-family key "
              "(Python `Order()` never reaches a TS Order)",
              not any(T._is_call_key(c[1]) for c in out) and st["skipped_family"] == 2
              and "call_typed" not in nodes[0], (keyed(T, out), st["skipped_family"]))
    rec.check("I ... and a cross-family typed verdict counts as UNKNOWN: it never REPLACEs (nothing removed, the "
              "engine's `save` / `Order` name tuples — which may reach the true Python Order — survive)",
              st["removed"] == 0 and out is calls and ("p::caller", "save") in out and ("p::caller", "Order") in out
              and st["vetoes"]["unknown_site"] == 2, (keyed(T, out), st["removed"], st["vetoes"]))
    # a class without a node; a method def without a node; a same-named node elsewhere is never a target
    files, facts, nodes, calls = go_world(T, b_ann="B")
    nodes = [n for n in nodes if n["key"] != "a::B.go"] + [nd("z::B.go", "Method", "B.go", "zzz/b.py", 7)]
    out, st = link(T, files, facts, nodes, calls, ON)
    keys = [c[1] for c in out if T._is_call_key(c[1])]
    rec.check("I a method definition with no node is UNKNOWN — never retargeted to a same-named node elsewhere",
              keys == ["a::A.go"] and st["vetoes"]["unknown_site"] == 1, keyed(T, out))
    files, facts, nodes, calls = go_world(T, b_ann="B")
    nodes = [n for n in nodes if n["key"] not in ("a::B", "a::B.go")]
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("I a class with no node resolves nothing (no key to a node outside `nodes`)",
              all(c[1] in {n["key"] for n in nodes} for c in out if T._is_call_key(c[1])), keyed(T, out))
    files, facts, nodes, calls = go_world(T, b_ann="B")
    nodes[2]["start_line"] = 99
    nodes[2]["name"] = "A.go"
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("I a method whose node moved off the def line is still found by `<Owner>.m` in the class's file",
              ("a::caller", "a::A.go") in [(c[0], c[1]) for c in out if T._is_call_key(c[1])], keyed(T, out))
    files, facts, nodes, calls = go_world(T, b_ann=T.t_self())
    facts["pkg/a.py"].scopes[2].cls = -1
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("I `self` outside a class is unknown (no key)", st["sites"]["unknown"] == 1)


# ================================================================================================
# J: deterministic and idempotent
# ================================================================================================

def _snapshot(T, out, nodes):
    return json.dumps([keyed(T, out), nodes], sort_keys=True, default=str)


def sec_determinism(T, rec: Rec) -> None:
    texts, facts, nodes, binder = grade_world(T)
    calls = [("s::Plain.run_l", "ping"), ("s::Plain.run_l", "Local.ping"), ("s::Plain.run_r", "find"),
             ("s::Plain.run_inj", T._call_key("s::Local.ping")), ("s::Svc.run_base", "other")]
    runs = []
    for _ in range(2):
        n2, c2 = copy.deepcopy(nodes), list(calls)
        out, st = link(T, texts, copy.deepcopy(facts), n2, c2, dict(ON, DEEPMAP_CALLS_AUDIT="python"),
                       binder=binder)
        st = dict(st)
        st.pop("seconds")
        runs.append((_snapshot(T, out, n2), json.dumps(st, sort_keys=True)))
    rec.check("J two runs over the same input give byte-identical calls, node attributes and stats",
              runs[0] == runs[1], "differ")
    n2, c2 = copy.deepcopy(nodes), list(calls)
    out1, _ = link(T, texts, facts, n2, c2, dict(ON, DEEPMAP_CALLS_AUDIT="python"), binder=binder)
    snap1 = _snapshot(T, out1, n2)
    out2, st2 = link(T, texts, facts, n2, out1, dict(ON, DEEPMAP_CALLS_AUDIT="python"), binder=binder)
    rec.check("J idempotent: a second run over the first run's output changes nothing",
              _snapshot(T, out2, n2) == snap1 and st2["error"] is None, (keyed(T, out1), keyed(T, out2)))
    files, facts, nodes, calls = go_world(T, b_ann="none")
    out1, _ = link(T, files, facts, nodes, calls, ON)
    snap1 = _snapshot(T, out1, nodes)
    out2, _ = link(T, files, facts, nodes, out1, ON)
    rec.check("J idempotent in ADD mode (the typed key is moved, never duplicated)", _snapshot(T, out2, nodes) == snap1,
              (keyed(T, out1), keyed(T, out2)))


# ================================================================================================
# K: one language degrades alone
# ================================================================================================

def sec_robust(T, rec: Rec) -> None:
    files, facts, nodes, calls = go_world(T, b_ann="B")
    ts_rel = "web/a.ts"
    nodes = nodes + [nd("w::A", "Class", "A", ts_rel, 1), nd("w::A.go", "Method", "A.go", ts_rel, 2),
                     nd("w::caller", "Function", "caller", ts_rel, 4)]
    tsf = T.FileFacts(ts_rel, "typescript", classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]})],
                      scopes=[T.Scope("caller", 4, 6, params={"a": T.Binding("param", T.t_name("A"))},
                                      sites=[T.Site(5, 2, "go", T.r_ident("a"), "member", 0)])])
    ts_text = "export class A {\n  go() {}\n}\nfunction caller(a: A) {\n  a.go();\n}\n"
    env = {"DEEPMAP_TYPED_CALLS": "python,typescript"}
    readers = {"python": FReader(T, facts), "typescript": FReader(T, {ts_rel: tsf}, canary=False)}
    out, st = T.link(".", nodes, calls + [("w::caller", "go")], languages=T.LANGUAGES, readers=readers,
                     source=Src({**files, ts_rel: ts_text}), is_test=lambda p: False, env=env)
    rec.check("K a canary failure disables ONLY that language (TypeScript off, Python linked)",
              st["langs_disabled"] == {"typescript": "canary"} and ("w::caller", "go") in out
              and caller_node(nodes).get("call_typed"), (st["langs_disabled"], keyed(T, out)))
    for kind in ("raise",):
        files, facts, nodes, calls = go_world(T, b_ann="B")
        readers = {"python": FReader(T, facts, canary=kind)}
        out, st = link(T, files, facts, nodes, calls, ON, readers=readers)
        rec.check("K a canary that raises disables the language (no error, the SAME list)",
                  st["langs_disabled"] == {"python": "canary"} and out is calls and st["error"] is None)
    files, facts, nodes, calls = go_world(T, b_ann="B")
    facts["pkg/z.py"] = None
    files["pkg/z.py"] = "x = 1\n"
    nodes.append(nd("z", "Module", "z", "pkg/z.py", 1))
    files["pkg/y.py"] = "y = 1\n"
    nodes.append(nd("y", "Module", "y", "pkg/y.py", 1))
    readers = {"python": FReader(T, facts, boom=("pkg/y.py",))}
    out, st = link(T, files, facts, nodes, calls, ON, readers=readers)
    rec.check("K a reader that raises on one file / returns None costs those files only",
              st["files_failed"] == 2 and st["files"] == 1 and st["emitted"] == 2, (st["files_failed"], st["files"]))
    files, facts, nodes, calls = go_world(T, b_ann="B")
    facts["pkg/a.py"].scopes[2].sites.append(("not", "a", "site"))
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("K a malformed FileFacts is that file's failure (never an error, nothing linked from it)",
              st["files_failed"] == 1 and st["error"] is None and out is calls, (st["files_failed"], st["error"]))
    files, facts, nodes, calls = go_world(T, b_ann="B")
    out, st = link(T, files, facts, nodes, calls, ON, readers={"python": FReader(T, facts, binder_boom=True)})
    rec.check("K a binder() that raises disables the language", st["langs_disabled"] == {"python": "binder"}
              and out is calls)
    texts, gfacts, gnodes, _b = grade_world(T)
    out, st = link(T, texts, gfacts, gnodes, [], ON, binder=FBinder(T, boom=True))
    rec.check("K resolve_import raising = an UNKNOWN import (in-file typing still works, no error)",
              st["error"] is None and next(n for n in gnodes if n["key"] == "s::Plain.run_l").get("call_typed")
              == {"s::Local.ping": "EXTRACTED"} and not next(n for n in gnodes if n["key"] == "s::Plain.run_r")
              .get("call_typed"), st["error"])
    files, facts, nodes, calls = go_world(T, b_ann="B")
    ghost = T.LanguageSpec("ghost", (".gh",), "ghost", "no_such_reader_module_s3a")
    out, st = T.link(".", nodes, calls, languages={"python": T.LANGUAGES["python"], "ghost": ghost},
                     readers={"python": FReader(T, facts)}, source=Src(files), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "python,ghost"})
    rec.check("K a language whose reader module is missing is disabled ('no reader'); the others run",
              st["langs_disabled"] == {"ghost": "no reader"} and st["emitted"] == 2, st["langs_disabled"])
    files, facts, nodes, calls = go_world(T, b_ann="B")
    files["pkg/b.py"] = "x = 1\n"
    nodes.append(nd("b", "Module", "b", "pkg/b.py", 1))
    out, st = link(T, files, facts, nodes, calls, dict(ON, DEEPMAP_TYPED_CALLS_MAX_FILES="1"))
    rec.check("K MAX_FILES: a language over the cap is skipped whole, with a note",
              st["langs_disabled"] == {"python": "max_files"} and out is calls and st["notes"], st["notes"])
    files, facts, nodes, calls = go_world(T, b_ann="B")
    out, st = link(T, files, facts, nodes, calls, ON, source=Src(files, big={"pkg/a.py": T.FILE_MAX_LINES + 1}))
    rec.check("K a file over 200k lines is never handed to the reader (files_capped)",
              st["files_capped"] == 1 and out is calls)


# ================================================================================================
# L: never raises; returns its input; rolls node attributes back
# ================================================================================================

class _SetBoom(dict):
    def __setitem__(self, k, v):
        if k == "call_member":
            raise RuntimeError("setitem boom")
        super().__setitem__(k, v)


def sec_never_raises(T, rec: Rec) -> None:
    files, facts, nodes, calls = go_world(T, b_ann="B")
    out, st = T.link(".", "nodes", calls, languages=T.LANGUAGES, readers={"python": FReader(T, facts)},
                     source=Src(files), env=ON)
    rec.check("L nodes not a list -> the input back + error", out is calls and st["error"])
    orig = T._Linker.run

    def boom(self):
        raise ValueError("core boom")

    try:
        T._Linker.run = boom
        out, st = link(T, files, facts, nodes, calls, ON)
    finally:
        T._Linker.run = orig
    rec.check("L an internal error -> the SAME input list, stats['error'] says why",
              out is calls and "core boom" in str(st["error"]), st["error"])
    files, facts, nodes, calls = go_world(T, b_ann="B")
    nodes[-1] = _SetBoom(nodes[-1])
    before = dict(nodes[-1])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("L a failure while applying node attributes rolls EVERY one back and returns the input",
              out is calls and dict(nodes[-1]) == before and "setitem boom" in str(st["error"]),
              (dict(nodes[-1]), st["error"]))
    fresh = T._stats_skeleton(None)
    counters = ("removed", "emitted", "new_pairs", "grades", "groups", "vetoes", "sites", "files", "by_lang",
                "by_shape", "candidate_sets", "skipped_policy", "skipped_family", "skipped_nested", "audit_weak",
                "call_member_cleared", "langs_disabled")
    rec.check("L ... and after an internal error every counter is the skeleton's (no partial removed / emitted / "
              "grades next to `error`: the manifest must not record changes that never happened)",
              all(st.get(k) == fresh.get(k) for k in counters),
              {k: st.get(k) for k in counters if st.get(k) != fresh.get(k)})
    out, st = T.link(".", [], [], env=ON, is_test=lambda p: False)
    rec.check("L the default readers / source on an empty graph: no error", st["error"] is None and out == [],
              st["error"])
    desc = T.describe(st)
    rec.check("L describe() gives one line", isinstance(desc, str) and "\n" not in desc, desc)
    rec.check("L describe() of an error says the CALLS are unchanged",
              "unchanged" in T.describe({"error": "x"}))


# ================================================================================================
# M: the textual veto's code masks and counter
# ================================================================================================

def _cnt(T, text, lexer, m, decls=frozenset()):
    lines = text.split("\n")
    masked = T.mask_code(text, lexer).split("\n")
    try:
        return T.count_call_shaped(lines, masked, 1, len(lines), m, decls, lexer=lexer)
    except TypeError:                                   # a module without the lexer-aware shape (the RED tree)
        return T.count_call_shaped(lines, masked, 1, len(lines), m, decls)


def sec_lexers(T, rec: Rec) -> None:
    def cnt(text, lexer, m, decls=frozenset()):
        return _cnt(T, text, lexer, m, decls)

    py = ("f(1)\n# f(2)\ns = 'f(3)'\nt = \"\"\"\nf(4)\n\"\"\"\nu = f'{f(5)}'\nr = r'\\'f(6)'\n"
          "x.f (7)\nf\\\n(8)\ng(f(9))\nff(10)\n_f(11)\n")
    rec.check("M python: strings (triple-quoted, raw) and comments masked, an f-string's replacement field is CODE; "
              "`x.f (`, nested and line-continued calls count; longer names do not", cnt(py, "python", "f") == 5,
              cnt(py, "python", "f"))
    rec.check("M python: a call in an f-string replacement field counts (`f\"{a.f()}\"`) — the reader records it "
              "(ast parses f-string expressions on every version), and a hidden one could let REPLACE remove the "
              "engine's tuple for a call the reader missed", cnt("v = f\"{a.f()}\"\n", "python", "f") == 1)
    rec.check("M python: f-string literal text, `{{f()}}`, a conversion / format spec and a nested string are NOT "
              "code", cnt("v = f\"f() {{f()}} {x!r:>{w}} {d['f()']}\"\n", "python", "f") == 0)
    rec.check("M python: nested f-strings, a field inside a format spec and a triple-quoted f-string",
              cnt("v = f'{g(f\"{f(1)}\")}'\n", "python", "f") == 1 and cnt("v = f'{x:{f(2)}}'\n", "python", "f") == 1
              and cnt('v = f"""\nf() {f(3)}\n"""\nf(4)\n', "python", "f") == 2)
    rec.check("M python: a b / rb / u prefix is not an f-string; a name ending in f is not a prefix",
              cnt("v = b'f(1)' + rb\"f(2)\" + Rb'''f(3)'''\n", "python", "f") == 0
              and cnt("if\"f(1)\" in y: f(2)\n", "python", "f") == 1)
    js = ("f(1)\n// f(2)\n/* f(3)\n f(4) */\nconst s = 'f(5)', t = \"f(6)\";\n"
          "const u = `a ${f(7)} b`;\nconst r = /f(8)/g;\nconst d = a / f(9) / 2;\nobj.f(10); $f(11); f$(12);\n"
          "return /f(13)/.test(x);\n")
    rec.check("M js: comments, strings, template TEXT and regex literals masked, a `${...}` substitution is CODE; "
              "division is not a regex", cnt(js, "js", "f") == 4, cnt(js, "js", "f"))
    rec.check("M js: nested templates, an object literal in a substitution, a regex after `return` vs after a value",
              cnt("x = `a ${`b ${f(1)} f(2)`} f(3)`\n", "js", "f") == 1
              and cnt("x = `${ {a: f(1)}.a } f(2)`; f(3)\n", "js", "f") == 2
              and cnt("y = a /* c */ / f(1) / b; z = (/* c */ /f(2)/);\n", "js", "f") == 1)
    rec.check("M js: `m<T>(`, `m!(`, `m?.(` and a tagged template `` m`..` `` are call-shaped (over-counting only "
              "vetoes)", cnt("a.go<string>(); a.go?.(); a.go!(); go`x`; new Map<string, X>(); a.go2();\n", "js", "go")
              == 4, cnt("a.go<string>(); a.go?.(); a.go!(); go`x`; new Map<string, X>(); a.go2();\n", "js", "go"))
    rec.check("M js: type arguments across lines, with an object type, a function type or a nested generic "
              "(zod's `new ZodDiscriminatedUnion<\\n ...>(` / `new Map<string, { l?: true; r?: true }>()`)",
              cnt("x = new ZodDiscriminatedUnion<\n  D,\n  T\n>({});\ny = new Map<string, { l?: true; r?: true }>();\n"
                  "f<(a: X) => void>(cb); g<Map<K, V>>(m);\n", "js", "ZodDiscriminatedUnion") == 1
              and cnt("y = new Map<string, { l?: true; r?: true }>();\n", "js", "Map") == 1
              and cnt("f<(a: X) => void>(cb);\n", "js", "f") == 1 and cnt("g<Map<K, V>>(m);\n", "js", "g") == 1)
    c = 'f(1);\n// f(2)\n/* f(3) */\ns := "f(4)"\nr := `f(5)`\nc := \'(\'; f(6)\nv = @"f(7)""";\n'
    rec.check("M c-family: comments, strings, Go raw strings, char literals and C# verbatim strings masked",
              cnt(c, "c", "f") == 2, cnt(c, "c", "f"))
    rec.check("M c-family: a C# `$\"..{hole}..\"` hole is code (`{{` is not), verbatim `$@` too; Rust `m::<T>(`",
              cnt('var s = $"x {f(1)} f(2) {{f(3)}}";\n', "c", "f") == 1
              and cnt('var s = $@"x {f(1)} ""f(2)""";\nf(3);\n', "c", "f") == 2
              and cnt("let v = f::<u8>(x); g.f(1);\n", "c", "f") == 2)
    rb = "f(1)\n# f(2)\ns = 'f(3)'\n=begin\nf(4)\n=end\nf(5)\n"
    rec.check("M ruby: # comments, strings, =begin/=end masked", cnt(rb, "ruby", "f") == 2, cnt(rb, "ruby", "f"))
    rec.check("M ruby: a `\"#{...}\"` interpolation is code", cnt('s = "a #{f(1)} f(2)"\nf(3)\n', "ruby", "f") == 2)
    rec.check("M the masks keep every newline and column", all(
        len(T.mask_code(t, lx)) == len(t) and T.mask_code(t, lx).count("\n") == t.count("\n")
        for t, lx in ((py, "python"), (js, "js"), (c, "c"), (rb, "ruby"))))
    rec.check("M declarations at (line, UTF-8 byte col) are skipped",
              cnt("é = 1\ndef f(x):\n    f(1)\n", "python", "f", frozenset({(2, 4)})) == 1)
    rec.check("M a non-ASCII prefix: the byte column is what a parser reports",
              cnt("x = 'é'; f(1)\n", "python", "f", frozenset({(1, len("x = 'é'; ".encode('utf-8')))})) == 0)
    rec.check("M an unreadable span is None (the veto then holds: no REPLACE)",
              T.count_call_shaped(["a"], ["a"], 1, 5, "f") is None and T.count_call_shaped(["a"], ["a"], 2, 1, "f")
              is None)
    long_line = "x = " + "f(1) + " * 400
    rec.check("M a span holding a line of MINIFIED_LINE+ characters is unjudgeable (None); a shorter one counts",
              cnt(long_line + "\n", "python", "f") is None and cnt(long_line[:1900] + "\n", "python", "f") ==
              long_line[:1900].count("f("), cnt(long_line + "\n", "python", "f"))
    orig = T._MASKERS.get("python")
    try:
        T._MASKERS["python"] = lambda text: (_ for _ in ()).throw(RuntimeError("mask boom"))
        boom = T.mask_code("f('x')\n", "python")
    except Exception as exc:  # noqa: BLE001
        boom = repr(exc)
    finally:
        T._MASKERS["python"] = orig
    rec.check("M a mask that fails returns the text UNMASKED (all code: over-counting only vetoes), never raises",
              boom == "f('x')\n", boom)
    for lx in ("python", "js", "c", "ruby"):
        for junk in ("'unterminated\nf(1)\n", "`${`${'\n", "\"\"\"\n", "/* never closed f(1)", "a/b/c/d\n",
                     "@\"", "r#\"x", "\\", "=begin", "f'{", "f'{x:{", '$"{', '"#{', "`${", "f'''{'", "{{}}}}",
                     "f'" + "{f'" * 300 + "x" + "'}" * 300 + "'", "`${" * 300 + "f(1)" + "}`" * 300):
            try:
                got = T.mask_code(junk, lx)
                ok = True if len(got) == len(junk) and got.count("\n") == junk.count("\n") else "length changed"
            except Exception as exc:  # noqa: BLE001
                ok = repr(exc)
            if ok is not True:
                rec.check(f"M mask_code({lx}) never raises on {junk[:40]!r}", False, ok)
                break
        else:
            rec.check(f"M mask_code({lx}) never raises on unterminated / adversarial / 300-deep input (columns "
                      "kept)", True)


# ================================================================================================
# N: shapes (DEEPMAP_TYPED_CALLS_SHAPES, one arm per shape)
# ================================================================================================

def sec_shapes(T, rec: Rec) -> None:
    texts, facts, nodes, binder = grade_world(T)
    out, st = link(T, texts, facts, nodes, [], dict(ON, DEEPMAP_TYPED_CALLS_SHAPES="R1"), binder=binder)
    by = {n["key"]: n.get("call_typed") for n in nodes if n.get("call_typed")}
    rec.check("N SHAPES=R1: only the typed-parameter sites are keyed", set(by) == {"s::Plain.run_l",
                                                                                   "s::Plain.run_r"}, by)
    texts, facts, nodes, binder = grade_world(T)
    out, st = link(T, texts, facts, nodes, [], dict(ON, DEEPMAP_TYPED_CALLS_SHAPES="S,C"), binder=binder)
    by = {n["key"]: n.get("call_typed") for n in nodes if n.get("call_typed")}
    rec.check("N SHAPES=S,C: self/super and constructors only",
              set(by) == {"s::Plain.run_self", "s::Sub.helper", "s::make", "s::Plain.prop", "b::Base.__init__"}, by)
    files, facts, nodes, calls = go_world(T, b_ann="B")
    out, st = link(T, files, facts, nodes, calls, dict(ON, DEEPMAP_TYPED_CALLS_SHAPES="S"))
    rec.check("N a site whose shape is off counts as UNKNOWN (no key, no removal)", out is calls
              and st["vetoes"]["unknown_site"] == 1, keyed(T, out))


# ================================================================================================
# O: seeded fuzz over 300 synthetic graphs
# ================================================================================================

_POOL_C = ["A", "B", "Repo", "Order", "Base"]
_POOL_M = ["go", "find", "save", "run", "__init__", "Repo", "notify"]
_POOL_V = ["a", "b", "x", "self", "U", "json"]
_POOL_K = ["lambda", "comprehension", "function", "method", "arrow", "function_expr", "generator_decl",
           "function_decl", "method_def", "closure"]


def _rtype(T, rnd, depth=0):
    k = rnd.choice(["name", "name", "qualified", "union", "nullable", "generic", "self", "classof", "builtin",
                    "unknown"] if depth < 2 else ["name", "builtin", "unknown"])
    if k == "name":
        return T.t_name(rnd.choice(_POOL_C + ["str", "Any", "None"]))
    if k == "qualified":
        return T.t_qual(rnd.choice(["U", "json", "A"]), rnd.choice(_POOL_C))
    if k == "union":
        if rnd.random() < 0.6:                  # a union of project classes (a candidate set in Python)
            return T.t_union(*[T.t_name(c) for c in rnd.sample(_POOL_C, rnd.randint(2, 3))])
        return T.t_union(*[_rtype(T, rnd, depth + 1) for _ in range(rnd.randint(1, 3))])
    if k == "nullable":
        return T.t_nullable(_rtype(T, rnd, depth + 1))
    if k == "generic":
        return T.t_generic(_rtype(T, rnd, depth + 1), _rtype(T, rnd, depth + 1))
    if k == "self":
        return T.t_self()
    if k == "classof":
        return T.t_classof(_rtype(T, rnd, depth + 1))
    if k == "builtin":
        return T.t_builtin(rnd.choice(["str", "string", "None", "any"]))
    return T.T_UNKNOWN


def _rrecv(T, rnd, depth=0):
    k = rnd.choice(["SELF", "SUPER", "IDENT", "IDENT", "FIELD", "CALL", "NEW", "CLASSREF", "MODULE", "LITERAL",
                    "OTHER"] if depth < 2 else ["SELF", "IDENT", "LITERAL", "OTHER"])
    if k == "IDENT":
        return T.r_ident(rnd.choice(_POOL_V + _POOL_C))
    if k == "FIELD":
        return T.r_field(_rrecv(T, rnd, depth + 1), rnd.choice(["repo", "inj", "local"]))
    if k == "CALL":
        return T.r_call(_rrecv(T, rnd, depth + 1) if rnd.random() < 0.6 else None, rnd.choice(_POOL_M + _POOL_C))
    if k == "NEW":
        return T.r_new(_rtype(T, rnd, depth + 1))
    if k == "CLASSREF":
        return T.r_classref(_rtype(T, rnd, depth + 1))
    if k == "MODULE":
        return T.r_module(rnd.choice(["U", "json"]))
    return T.Receiver(k)


def _rbind(T, rnd, nscopes):
    k = rnd.choice(sorted(T.BINDING_KINDS))
    sc = rnd.randrange(-1, nscopes) if nscopes else -1
    if k in ("ann", "param", "ctor"):
        return T.Binding(k, _rtype(T, rnd) if rnd.random() < 0.9 else None, scope=sc)
    if k in ("call", "field"):
        return T.Binding(k, value=_rrecv(T, rnd), scope=sc)
    return T.Binding(k, scope=sc)


def _rparam(T, rnd, nscopes, here=()):
    """A parameter: a project class (40%), a union of two of the file's own classes (20%: Python candidate
    sets), anything (40%)."""
    r = rnd.random()
    if r < 0.4:
        return T.Binding("param", T.t_name(rnd.choice(_POOL_C)))
    if r < 0.6:
        pool = sorted(set(here)) if len(set(here)) >= 2 else _POOL_C
        return T.Binding("param", T.t_union(*[T.t_name(c) for c in rnd.sample(pool, 2)]))
    return _rbind(T, rnd, nscopes)._replace(kind="param")


def fuzz_case(T, rnd: random.Random):
    """(files, facts, nodes, calls, binders, env, is_test) — a random small graph, mostly well-formed."""
    files, facts, nodes = {}, {}, []
    site_calls: list = []                               # (caller key, member, rel) of every generated site
    rels = []
    for fi in range(rnd.randint(1, 4)):
        lang = rnd.choice(["python", "python", "typescript"])
        d = rnd.choice(["pkg", "tests", "examples", "src/app", "json", "web"])
        rels.append((f"{d}/f{fi}{'.py' if lang == 'python' else '.ts'}", lang))
    for rel, lang in rels:
        lines = [f"line {i}" for i in range(1, 101)]
        classes, scopes, functions, decls = [], [], {}, set()
        file_methods: list = []
        stem = rel.replace("/", "_")
        for ci in range(rnd.randint(0, 3)):
            line = 1 + ci * 10
            name = rnd.choice(_POOL_C)
            lines[line - 1] = f"class {name}:"
            decls.add((line, 6))
            methods, fields = {}, {}
            for mj in range(rnd.randint(0, 3)):
                mname = rnd.choice(_POOL_M)
                ml = line + 1 + mj * 2
                lines[ml - 1] = f"    def {mname}(self):"
                if rnd.random() < 0.9:
                    decls.add((ml, 8))
                flags = frozenset(rnd.sample(["static", "class", "property", "abstract"], rnd.randint(0, 1)))
                methods.setdefault(mname, []).append(T.MethodFacts(ml, flags, _rtype(T, rnd) if rnd.random() < 0.5
                                                                   else None, (0, rnd.choice([-1, 0, 1, 2]))))
                file_methods.append(mname)
                if rnd.random() < 0.85:
                    nodes.append(nd(f"{stem}::{name}.{mname}:{ml}", "Method", f"{name}.{mname}", rel, ml))
                if rnd.random() < 0.6:
                    scopes.append(T.Scope(mname, ml, ml + 1, cls=ci, self_kind=rnd.choice(["inst", "class", ""])))
            for _ in range(rnd.randint(0, 2)):
                fields.setdefault(rnd.choice(["repo", "inj", "local", "go"]), []).append(_rbind(T, rnd, 0))
            classes.append(T.ClassFacts(name, line, line + 8, bases=tuple(_rtype(T, rnd) for _ in range(
                rnd.randint(0, 2))), fields=fields, methods=methods, dynamic=rnd.random() < 0.1,
                open=rnd.random() < 0.15, outer=-1 if ci == 0 or rnd.random() < 0.8 else rnd.randrange(ci)))
            if rnd.random() < 0.9:
                nodes.append(nd(f"{stem}::{name}:{line}", "Class", name, rel, line))
        for fj in range(rnd.randint(1, 3)):
            fl = 41 + fj * 12
            fname = rnd.choice(["caller", "use", "run", "make"])
            lines[fl - 1] = f"def {fname}(a, b):"
            decls.add((fl, 4))
            parent = len(scopes) - 1 if (scopes and rnd.random() < 0.2) else -1
            functions.setdefault(fname, []).append(T.FunctionFacts(fname, fl, _rtype(T, rnd) if rnd.random() < 0.4
                                                                   else None))
            nsc = len(scopes) + 1
            sites, an = [], {}
            for sk in range(rnd.randint(0, 6)):
                sl = fl + 1 + sk
                kind = rnd.choice(["member", "member", "bare", "ctor"])
                m = rnd.choice(file_methods) if (file_methods and kind == "member" and rnd.random() < 0.6) \
                    else rnd.choice(_POOL_M + _POOL_C)
                recv = _rrecv(T, rnd) if kind == "member" else (T.r_classref(_rtype(T, rnd)) if kind == "ctor"
                                                               else None)
                sites.append(T.Site(sl, 4, m, recv, kind, rnd.choice([-1, 0, 1])))
                an[m] = an.get(m, 0) + 1
                txt = f"    x.{m}(1)" if kind == "member" else (f"    new {m}(1)" if kind == "ctor" else f"    {m}(1)")
                if rnd.random() < 0.15:
                    txt += f"  # {m}(2)"
                if rnd.random() < 0.08:
                    txt += f"; {m}(3)"                  # a call the reader did not record
                lines[sl - 1] = txt
            if rnd.random() < 0.1 and an:
                an[next(iter(an))] += 1
            kind = (rnd.choice(_POOL_K) if parent >= 0 else ("function" if lang == "python" else "function_decl"))
            scopes.append(T.Scope(fname, fl, fl + 7 if rnd.random() < 0.97 else fl + 6000, parent=parent, kind=kind,
                                  params={p: _rparam(T, rnd, nsc, [c.name for c in classes])
                                          for p in rnd.sample(_POOL_V, rnd.randint(0, 3))},
                                  locals={v: ([T.Binding("call", value=T.r_call(None, c)) for c in
                                               rnd.sample(_POOL_C, 2)] if rnd.random() < 0.3 else
                                              [_rbind(T, rnd, nsc) for _ in range(rnd.randint(1, 2))])
                                          for v in rnd.sample(_POOL_V, rnd.randint(0, 2))},
                                  poisoned=frozenset(rnd.sample(_POOL_V, rnd.randint(0, 1))),
                                  sites=sites, all_names=an if rnd.random() < 0.8 else None))
            if rnd.random() < 0.92:
                nodes.append(nd(f"{stem}::{fname}:{fl}", "Function" if parent < 0 else "Method", fname, rel, fl))
                site_calls.extend((f"{stem}::{fname}:{fl}", s.member, rel) for s in sites)
        # seeded shapes the random walk rarely builds (lines 85-99): a Python candidate set over two of the file's
        # classes, and a TypeScript member a closed class does not declare (absent)
        named: dict = {}
        for c in classes:
            named.setdefault(c.name, []).append(c)
        uniq = sorted(n for n, cs in named.items() if len(cs) == 1)
        if lang == "python" and len(uniq) >= 2 and rnd.random() < 0.4:
            c1, c2 = rnd.sample(uniq, 2)
            for c, ml in ((named[c1][0], 86), (named[c2][0], 87)):
                c.methods.setdefault("pick", []).append(T.MethodFacts(ml))
                nodes.append(nd(f"{stem}::{c.name}.pick:{ml}", "Method", f"{c.name}.pick", rel, ml))
            lines[89], lines[90] = "def cand(u):", "    u.pick()"
            decls.add((90, 4))
            functions["cand"] = [T.FunctionFacts("cand", 90)]
            scopes.append(T.Scope("cand", 90, 92, kind="function", all_names={"pick": 1},
                                  params={"u": T.Binding("param", T.t_union(T.t_name(c1), T.t_name(c2)))},
                                  sites=[T.Site(91, 4, "pick", T.r_ident("u"), "member", 0)]))
            nodes.append(nd(f"{stem}::cand:90", "Function", "cand", rel, 90))
            site_calls.append((f"{stem}::cand:90", "pick", rel))
        if lang == "typescript" and rnd.random() < 0.4:
            classes.append(T.ClassFacts("Closed", 94, 95, methods={"have": [T.MethodFacts(95)]}))
            nodes += [nd(f"{stem}::Closed:94", "Class", "Closed", rel, 94),
                      nd(f"{stem}::Closed.have:95", "Method", "Closed.have", rel, 95)]
            lines[96], lines[97] = "function absc(p) {", "  p.zzz();"
            decls.add((97, 9))
            functions["absc"] = [T.FunctionFacts("absc", 97)]
            scopes.append(T.Scope("absc", 97, 99, kind="function_decl", all_names={"zzz": 1},
                                  params={"p": T.Binding("param", T.t_name("Closed"))},
                                  sites=[T.Site(98, 2, "zzz", T.r_ident("p"), "member", 0)]))
            nodes.append(nd(f"{stem}::absc:97", "Function", "absc", rel, 97))
            site_calls.append((f"{stem}::absc:97", "zzz", rel))
        imports = {}
        for loc in rnd.sample(["U", "json", "A", "Repo", "*"], rnd.randint(0, 3)):
            imports[loc] = [T.Import(loc, "star" if loc == "*" else rnd.choice(["module", "named", "namespace",
                                                                                 "package"]), "spec." + loc)]
        f = T.FileFacts(rel, lang, imports=imports, classes=classes, functions=functions,
                        globals={"g": [_rbind(T, rnd, len(scopes))]} if rnd.random() < 0.3 else {},
                        scopes=scopes, decls=frozenset(decls),
                        patches=[(T.t_name(rnd.choice(_POOL_C)), rnd.choice(_POOL_M))] if rnd.random() < 0.1 else [])
        if rnd.random() < 0.05:                         # a malformed facts object: that file's failure only
            f = copy.copy(f)
            f.scopes = [("bad",)]
        files[rel] = "\n".join(lines) + "\n"
        facts[rel] = f
    keys = [n["key"] for n in nodes]
    binders = {}
    for lang in ("python", "typescript"):
        table = {}
        for rel, _l in rels:
            for loc in ("U", "json", "A", "Repo"):
                tgt = rnd.choice(rels)[0]
                table[(rel, loc)] = rnd.choice([
                    T.Resolution("module", tgt), T.Resolution("symbol", tgt, rnd.choice(_POOL_C)),
                    T.Resolution("external", stems=(rnd.choice(["json", "re", "zz"]),)), T.RES_UNKNOWN,
                    T.Resolution("namespace", name="ns"), "garbage", None])
        binders[lang] = FBinder(T, imports=table)
    calls = []
    for _ in range(rnd.randint(0, 14)):
        if not keys:
            break
        ck = rnd.choice(keys)
        pick = rnd.random()
        if pick < 0.4:
            callee = rnd.choice(_POOL_M + _POOL_C)
        elif pick < 0.6:
            callee = rnd.choice(_POOL_C) + "." + rnd.choice(_POOL_M)
        elif pick < 0.9:
            callee = T._call_key(rnd.choice(keys))
        else:
            callee = T._call_key("ghost::x")
        calls.append((ck, callee))
    for ck, m, rel in site_calls:                        # the engine's own tuples for those sites
        pick = rnd.random()
        if pick < 0.4:
            calls.append((ck, m))
        elif pick < 0.7:
            same = [n["key"] for n in nodes if n["label"] == "Method" and n["source_file"] == rel
                    and n["name"].rsplit(".", 1)[-1] == m]
            if same:
                calls.append((ck, T._call_key(rnd.choice(same))))
        elif pick < 0.8:
            calls.append((ck, rnd.choice(_POOL_C) + "." + m))
    if rnd.random() < 0.1:
        calls.append(("ghost::caller", "go"))
    env = {}
    if rnd.random() < 0.85:
        env["DEEPMAP_TYPED_CALLS"] = rnd.choice(["python", "python,typescript", "1", "typescript"])
    if rnd.random() < 0.5:
        env["DEEPMAP_CALLS_AUDIT"] = rnd.choice(["python", "1", "typescript"])
    for flag in ("DEEPMAP_TYPED_CALLS_REPLACE", "DEEPMAP_TYPED_CALLS_TESTS", "DEEPMAP_TYPED_CALLS_NONPROD"):
        if rnd.random() < 0.25:
            env[flag] = rnd.choice(["0", "1"])
    if rnd.random() < 0.2:
        env["DEEPMAP_TYPED_CALLS_SHAPES"] = ",".join(rnd.sample(list(T.SHAPES), rnd.randint(1, 4)))
    test_dirs = {"tests"} if rnd.random() < 0.8 else set()
    env["__engine__"] = rnd.choice([None, "graphify", "ast"])     # not a flag: the engine the graph came from
    return files, facts, nodes, calls, binders, env, (lambda p, t=test_dirs: p.split("/")[0] in t)


def _fuzz_run(T, case, facts_only=None):
    """One fuzz run. `facts_only` = the one rel whose facts the reader still hands out (the others fail)."""
    files, facts, nodes, calls, binders, env, is_test = case
    env = dict(env)
    engine = env.pop("__engine__", None)
    table = facts if facts_only is None else {r: f for r, f in facts.items() if r == facts_only}
    readers = {"python": FReader(T, table, binders["python"]), "typescript": FReader(T, table, binders["typescript"])}
    return T.link(".", nodes, calls, languages={n: T.LANGUAGES[n] for n in ("python", "typescript", "javascript")},
                  readers=readers, source=Src(files), is_test=is_test, env=env, engine=engine)


def sec_fuzz(T, rec: Rec, n: int = 300) -> None:
    import edge_resolve as ER
    rnd = random.Random(20261022)
    problems = {"raised": [], "error": [], "cross_extracted": [], "extracted_chain": [], "not_key": [], "family": [],
                "grade": [], "weak_typed": [], "ambiguous_widened": [], "flag_off": [], "determinism": [],
                "idempotent": []}
    exercised = {"replace": 0, "add": 0, "emitted": 0, "removed": 0, "weak": 0, "failed_files": 0,
                 "candidate_sets": 0, "external": 0, "absent": 0, "skipped_policy": 0, "veto_text": 0,
                 "veto_nested": 0, "skipped_nested": 0, "extracted": 0}
    for i in range(n):
        case = fuzz_case(T, rnd)
        files, facts, nodes, calls, binders, env, is_test = case
        snap_in = copy.deepcopy((nodes, calls))
        try:
            out, st = _fuzz_run(T, (files, facts, nodes, calls, binders, env, is_test))
        except Exception as exc:  # noqa: BLE001
            problems["raised"].append((i, repr(exc)))
            continue
        if st["error"]:
            problems["error"].append((i, st["error"]))
            continue
        for k in ("replace", "add"):
            exercised[k] += st["groups"][k]
        exercised["emitted"] += st["emitted"]
        exercised["removed"] += st["removed"]
        exercised["weak"] += st["audit_weak"]
        exercised["failed_files"] += st["files_failed"]
        exercised["candidate_sets"] += st["candidate_sets"]
        exercised["external"] += st["sites"]["external"]
        exercised["absent"] += st["sites"]["absent"]
        exercised["skipped_policy"] += st["skipped_policy"]
        exercised["veto_text"] += st["vetoes"]["text"]
        exercised["veto_nested"] += st["vetoes"].get("nested", 0)
        exercised["skipped_nested"] += st.get("skipped_nested", 0)
        keyset = {nn["key"] for nn in nodes}
        file_of = {nn["key"]: nn["source_file"] for nn in nodes}
        idx = ER.build_name_index([{"key": nn["key"], "name": nn["name"]} for nn in nodes])
        reach: dict = {}
        for ck, callee in snap_in[1]:
            if not isinstance(callee, str):
                continue
            reach.setdefault(ck, set()).update({str(callee)} if T._is_call_key(callee) else idx.get(callee, ()))
        for nn in nodes:
            for t, g in (nn.get("call_typed") or {}).items():
                if g not in T.GRADES:
                    problems["grade"].append((i, g))
                if g == "EXTRACTED" and file_of.get(t) != nn["source_file"]:
                    problems["cross_extracted"].append((i, nn["key"], t))
                fa, fb = ER.lang_family(nn["source_file"]), ER.lang_family(file_of.get(t, ""))
                if fa and fb and fa != fb:
                    problems["family"].append((i, nn["key"], t))
            if set(nn.get("call_weak") or ()) & set(nn.get("call_typed") or {}):
                problems["weak_typed"].append((i, nn["key"]))
            for t, g in (nn.get("call_typed") or {}).items():
                if g == "AMBIGUOUS" and t not in reach.get(nn["key"], ()):
                    problems["ambiguous_widened"].append((i, nn["key"], t))
        for c in out[len(out) - st["emitted"]:] if st["emitted"] else []:
            if not T._is_call_key(c[1]) or c[1] not in keyset:
                problems["not_key"].append((i, c))
        # the EXTRACTED evidence chain (design 4): an EXTRACTED pair may depend on its caller's file ONLY — it must
        # survive a run in which every OTHER file's facts are gone (an intermediate MRO class, a field's binding or
        # an import in another file would vanish with them)
        extracted = [(nn["key"], nn["source_file"], t) for nn in nodes
                     for t, g in sorted((nn.get("call_typed") or {}).items()) if g == "EXTRACTED"]
        exercised["extracted"] += len(extracted)
        for rel_c in sorted({r for _k, r, _t in extracted}):
            n5, c5 = copy.deepcopy(snap_in)
            _o5, st5 = _fuzz_run(T, (files, facts, n5, c5, binders, env, is_test), facts_only=rel_c)
            by5 = {nn["key"]: nn.get("call_typed") or {} for nn in n5}
            problems["extracted_chain"] += [(i, ck, t) for ck, r, t in extracted
                                            if r == rel_c and by5.get(ck, {}).get(t) != "EXTRACTED"]
        # determinism: the same case again from the frozen input
        n2, c2 = copy.deepcopy(snap_in)
        out2, st2 = _fuzz_run(T, (files, facts, n2, c2, binders, env, is_test))
        if _snapshot(T, out2, n2) != _snapshot(T, out, nodes):
            problems["determinism"].append(i)
        # idempotent
        out3, st3 = _fuzz_run(T, (files, facts, nodes, out, binders, env, is_test))
        if _snapshot(T, out3, nodes) != _snapshot(T, out, nodes):
            problems["idempotent"].append(i)
        # flag-off identity
        n4, c4 = copy.deepcopy(snap_in)
        frozen = json.dumps(_snapshot(T, c4, n4))
        out4, st4 = _fuzz_run(T, (files, facts, n4, c4, binders, {}, is_test))
        if out4 is not c4 or json.dumps(_snapshot(T, c4, n4)) != frozen:
            problems["flag_off"].append(i)
    for k, v in problems.items():
        rec.check(f"O fuzz ({n} graphs): {k} — none", not v, v[:5])
    rec.check("O fuzz exercised REPLACE, ADD, emission, removal, the audit and bad facts",
              all(v > 0 for v in exercised.values()), exercised)


# ================================================================================================
# P: attribution — a nested scope's typed site is emitted from the enclosing caller only when the ENGINE attributes
#    that call to it (graphify: lambdas / comprehensions / JS function expressions; never a nested def, a local
#    class's method, an arrow or a nested function declaration); the REPLACE judgement covers the full span
# ================================================================================================

PY_NEST = [
    "class A:",                             # 1
    "    def go(self):",                    # 2
    "        return 1",                     # 3
    "",                                     # 4
    "class B:",                             # 5
    "    def go(self):",                    # 6
    "        return 2",                     # 7
    "",                                     # 8
    "class D:",                             # 9
    "    def go(self):",                    # 10
    "        return 3",                     # 11
    "",                                     # 12
    "def outer(a: A, b: B, d: D):",         # 13
    "    a.go()",                           # 14
    "    f = lambda: b.go()",               # 15
    "    ys = [b.go() for _ in range(2)]",  # 16
    "    def inner(x=d.go()):",             # 17  (a default argument: inside graphify's def boundary)
    "        return d.go()",                # 18
    "    class Local:",                     # 19
    "        def run(self):",               # 20
    "            return d.go()",            # 21
    "    return f, ys, inner, Local",       # 22
]


def nest_world(T, *, lambda_kind="lambda", header_only=False, inner_node=False, default=True):
    """(files, facts, nodes, calls) of fixture PY_NEST. outer's own a.go() -> A.go; the lambda's and the
    comprehension's b.go() -> B.go (graphify attributes both to outer); the default argument, inner's and
    Local.run's d.go() -> D.go (graphify attributes none of them to anyone). default=False: no default argument
    (D.go then comes only from inner's and Local.run's bodies)."""
    rel = "pkg/n.py"
    lines = list(PY_NEST)
    if header_only:
        lines[17], lines[20] = "        return 0", "            return 0"
    if not default:
        lines[16] = "    def inner(x=0):"
    text = "\n".join(lines) + "\n"
    B, S, MF, CF, SC = T.Binding, T.Site, T.MethodFacts, T.ClassFacts, T.Scope

    def go(line, var):
        return S(line, bcol(text, line, f"{var}.go"), "go", T.r_ident(var), "member", 0)

    inner_sites = [] if header_only else [go(18, "d")]
    run_sites = [] if header_only else [go(21, "d")]
    scopes = [SC("go", 2, 3, cls=0, self_kind="inst", kind="method"),
              SC("go", 6, 7, cls=1, self_kind="inst", kind="method"),
              SC("go", 10, 11, cls=2, self_kind="inst", kind="method"),
              SC("outer", 13, 22, params={v: B("param", T.t_name(c)) for v, c in (("a", "A"), ("b", "B"), ("d", "D"))},
                 sites=[go(14, "a")] + ([go(17, "d")] if default else []), kind="function"),
              SC("<lambda>", 15, 15, parent=3, sites=[go(15, "b")], kind=lambda_kind),
              SC("<listcomp>", 16, 16, parent=3, sites=[go(16, "b")], kind="comprehension"),
              SC("inner", 17, 18, parent=3, params={"x": B("param")}, sites=inner_sites, kind="function"),
              SC("run", 20, 21, parent=3, cls=3, self_kind="inst", sites=run_sites, kind="method")]
    n_go = sum(len(s.sites) for s in scopes[3:])
    scopes[3].all_names = {"go": n_go}
    f = T.FileFacts(rel, "python",
                    classes=[CF("A", 1, 3, methods={"go": [MF(2)]}), CF("B", 5, 7, methods={"go": [MF(6)]}),
                             CF("D", 9, 11, methods={"go": [MF(10)]}),
                             CF("Local", 19, 21, scope=3, methods={"run": [MF(20)]})],
                    functions={"outer": [T.FunctionFacts("outer", 13)]}, scopes=scopes, decls=decls_of(text))
    nodes = [nd("n::A", "Class", "A", rel, 1), nd("n::A.go", "Method", "A.go", rel, 2),
             nd("n::B", "Class", "B", rel, 5), nd("n::B.go", "Method", "B.go", rel, 6),
             nd("n::D", "Class", "D", rel, 9), nd("n::D.go", "Method", "D.go", rel, 10),
             nd("n::outer", "Function", "outer", rel, 13)]
    if inner_node:                                      # the ast path: a nested def is a node too
        nodes.append(nd("n::inner", "Function", "inner", rel, 17))
    calls = [("n::outer", "go")] + ([("n::inner", "go")] if inner_node else [])
    return {rel: text}, {rel: f}, nodes, calls


TS_NEST = [
    "export class A {",                                     # 1
    "  go(): number { return 1; }",                         # 2
    "}",                                                    # 3
    "export class B {",                                     # 4
    "  go(): number { return 2; }",                         # 5
    "}",                                                    # 6
    "export class D {",                                     # 7
    "  go(): number { return 3; }",                         # 8
    "}",                                                    # 9
    "export function outer(a: A, b: B, d: D) {",            # 10
    "  a.go();",                                            # 11
    "  const e = function () { return b.go(); };",          # 12
    "  function* g() { yield b.go(); }",                    # 13
    "  const f = () => d.go();",                            # 14
    "  function inner() { return d.go(); }",                # 15
    "  const o = { m() { return d.go(); } };",              # 16
    "  return [e, g, f, inner, o];",                        # 17
    "}",                                                    # 18
]


def ts_nest_world(T, kinds=("function_expr", "generator_decl", "arrow", "function_decl", "method_def")):
    rel = "web/n.ts"
    text = "\n".join(TS_NEST) + "\n"
    B, S, MF, CF, SC = T.Binding, T.Site, T.MethodFacts, T.ClassFacts, T.Scope

    def go(line, var):
        return S(line, bcol(text, line, f"{var}.go"), "go", T.r_ident(var), "member", 0)

    ke, kg, kf, ki, km = kinds
    scopes = [SC("go", 2, 2, cls=0, self_kind="inst", kind="method_def"),
              SC("go", 5, 5, cls=1, self_kind="inst", kind="method_def"),
              SC("go", 8, 8, cls=2, self_kind="inst", kind="method_def"),
              SC("outer", 10, 18, params={v: B("param", T.t_name(c)) for v, c in (("a", "A"), ("b", "B"), ("d", "D"))},
                 sites=[go(11, "a")], all_names={"go": 6}, kind="function_decl"),
              SC("e", 12, 12, parent=3, sites=[go(12, "b")], kind=ke),
              SC("g", 13, 13, parent=3, sites=[go(13, "b")], kind=kg),
              SC("f", 14, 14, parent=3, sites=[go(14, "d")], kind=kf),
              SC("inner", 15, 15, parent=3, sites=[go(15, "d")], kind=ki),
              SC("m", 16, 16, parent=3, sites=[go(16, "d")], kind=km)]
    f = T.FileFacts(rel, "typescript",
                    classes=[CF("A", 1, 3, methods={"go": [MF(2)]}), CF("B", 4, 6, methods={"go": [MF(5)]}),
                             CF("D", 7, 9, methods={"go": [MF(8)]})],
                    functions={"outer": [T.FunctionFacts("outer", 10)]}, scopes=scopes,
                    decls=decls_of(text) | {(2, 2), (5, 2), (8, 2), (16, bcol(text, 16, "m("))})
    nodes = [nd("t::A", "Class", "A", rel, 1), nd("t::A.go", "Method", "A.go", rel, 2),
             nd("t::B", "Class", "B", rel, 4), nd("t::B.go", "Method", "B.go", rel, 5),
             nd("t::D", "Class", "D", rel, 7), nd("t::D.go", "Method", "D.go", rel, 8),
             nd("t::outer", "Function", "outer", rel, 10)]
    return {rel: text}, {rel: f}, nodes, [("t::outer", "go")]


def _ts_link(T, files, facts, nodes, calls, env=None, engine=None):
    kw = {} if engine is None else {"engine": engine}
    return T.link(".", nodes, calls, languages={"typescript": T.LANGUAGES["typescript"]},
                  readers={"typescript": FReader(T, facts)}, source=Src(files), is_test=lambda p: False,
                  env=env or {"DEEPMAP_TYPED_CALLS": "typescript"}, **kw)


def sec_attribution(T, rec: Rec) -> None:
    def typed(nodes, key):
        return next(n for n in nodes if n["key"] == key).get("call_typed")

    def run(*, engine=None, **kw):
        files, facts, nodes, calls = nest_world(T, **kw)
        kwe = {} if engine is None else {"engine": engine}
        out, st = T.link(".", nodes, calls, languages={"python": T.LANGUAGES["python"]},
                         readers={"python": FReader(T, facts)}, source=Src(files), is_test=lambda p: False,
                         env=ON, **kwe)
        return out, st, nodes

    out, st, nodes = run()
    rec.check("P graphify (the default): outer's typed keys come from its own a.go() and the lambda's / "
              "comprehension's b.go() only — never from the nested def, the local class's method or a nested def's "
              "default argument (graphify gives those calls to nobody)",
              typed(nodes, "n::outer") == {"n::A.go": "EXTRACTED", "n::B.go": "EXTRACTED"}, typed(nodes, "n::outer"))
    rec.check("P ... REPLACE is still judged over the FULL span (6 sites, all typed, the veto counts 6): the engine's "
              "`go` tuple is replaced; D.go is counted in skipped_nested, never emitted",
              st["removed"] == 1 and st["groups"]["replace"] >= 1 and st.get("skipped_nested") == 1
              and keyed(T, out) == [("n::outer", "n::A.go", True), ("n::outer", "n::B.go", True)],
              (keyed(T, out), st["removed"], st.get("skipped_nested"), st["vetoes"]))
    out2, st2, nodes2 = run(engine="graphify")
    rec.check("P engine='graphify' is the default's rule", typed(nodes2, "n::outer") == typed(nodes, "n::outer"))
    out, st, nodes = run(engine="ast")
    rec.check("P engine='ast' (sync._Extractor's ast.walk attributes every nested call to the enclosing def): "
              "outer gets D.go too", typed(nodes, "n::outer") == {"n::A.go": "EXTRACTED", "n::B.go": "EXTRACTED",
                                                                   "n::D.go": "EXTRACTED"}, typed(nodes, "n::outer"))
    for eng, want_d in (("ast", True), (None, False)):
        out, st, nodes = run(engine=eng, default=False)
        rec.check(f"P no default argument: D.go comes only from inner's / Local.run's bodies -> "
                  f"{'emitted from outer on the ast path' if want_d else 'never emitted from outer under graphify'}",
                  ("n::D.go" in (typed(nodes, "n::outer") or {})) == want_d, typed(nodes, "n::outer"))
    out, st, nodes = run(header_only=True)
    rec.check("P a nested def's DEFAULT argument recorded in outer (on the def's own line) is detached under "
              "graphify: no D.go from outer", typed(nodes, "n::outer") == {"n::A.go": "EXTRACTED",
                                                                           "n::B.go": "EXTRACTED"},
              typed(nodes, "n::outer"))
    out, st, nodes = run(header_only=True, engine="ast")
    rec.check("P ... and attributed on the ast path", "n::D.go" in (typed(nodes, "n::outer") or {}),
              typed(nodes, "n::outer"))
    out, st, nodes = run(lambda_kind="closure")
    rec.check("P a nested scope of an UNKNOWN kind is uncertain: nothing emitted from it and the name's REPLACE "
              "is vetoed (`nested`); B.go still comes from the comprehension",
              st["removed"] == 0 and st["vetoes"].get("nested") == 1
              and typed(nodes, "n::outer") == {"n::A.go": "EXTRACTED", "n::B.go": "EXTRACTED"},
              (st["removed"], st["vetoes"], typed(nodes, "n::outer")))
    out, st, nodes = run(inner_node=True)
    rec.check("P a nested def WITH its own node (the ast path) is a caller itself: inner gets D.go from its own "
              "body, outer still does not", typed(nodes, "n::inner") == {"n::D.go": "EXTRACTED"}
              and "n::D.go" not in (typed(nodes, "n::outer") or {}),
              (typed(nodes, "n::inner"), typed(nodes, "n::outer")))
    files, facts, nodes, calls = ts_nest_world(T)
    out, st = _ts_link(T, files, facts, nodes, calls)
    rec.check("P TS: a function expression's and a generator declaration's b.go() are attributed; an arrow's, a "
              "nested function declaration's and an object-literal method's d.go() are not",
              typed(nodes, "t::outer") == {"t::A.go": "EXTRACTED", "t::B.go": "EXTRACTED"}
              and st.get("skipped_nested") == 1 and st["removed"] == 1,
              (typed(nodes, "t::outer"), st.get("skipped_nested"), st["removed"], st["vetoes"]))
    files, facts, nodes, calls = ts_nest_world(T, kinds=("function", "function", "arrow", "function", "method"))
    out, st = _ts_link(T, files, facts, nodes, calls)
    rec.check("P TS: the legacy kinds 'function' / 'method' (a declaration OR an expression) are uncertain: no "
              "emission from them and no REPLACE of `go`", typed(nodes, "t::outer") == {"t::A.go": "EXTRACTED"}
              and st["removed"] == 0 and st["vetoes"].get("nested") == 1, (typed(nodes, "t::outer"), st["vetoes"]))
    # the audit: an untyped site the engine did not attribute to the caller is no witness
    rel = "svc/w.py"
    for where, want in (("inner", None), ("lambda", ["w::A.go"])):
        lines = ["class A:", "    def go(self):", "        return 1", "", "class B:", "    def go(self):",
                 "        return 2", "", "def outer(nt):", "    def inner():", "        return 0",
                 "    f = lambda: 0", "    return inner, f"]
        if where == "inner":
            lines[10] = "        return nt.go()"
        else:
            lines[11] = "    f = lambda: nt.go()"
        text = "\n".join(lines) + "\n"
        line = 11 if where == "inner" else 12
        site = T.Site(line, bcol(text, line, "nt.go"), "go", T.r_ident("nt"), "member", 0)
        f = T.FileFacts(rel, "python", classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]}),
                                                T.ClassFacts("B", 5, 7, methods={"go": [T.MethodFacts(6)]})],
                        functions={"outer": [T.FunctionFacts("outer", 9)]},
                        scopes=[T.Scope("outer", 9, 13, params={"nt": T.Binding("param")}, kind="function"),
                                T.Scope("inner", 10, 11, parent=0, sites=[site] if where == "inner" else [],
                                        kind="function"),
                                T.Scope("<lambda>", 12, 12, parent=0, sites=[site] if where == "lambda" else [],
                                        kind="lambda")], decls=decls_of(text))
        nodes = [nd("w::A", "Class", "A", rel, 1), nd("w::A.go", "Method", "A.go", rel, 2),
                 nd("w::B", "Class", "B", rel, 5), nd("w::B.go", "Method", "B.go", rel, 6),
                 nd("w::outer", "Function", "outer", rel, 9)]
        link(T, {rel: text}, {rel: f}, nodes, [("w::outer", T._call_key("w::A.go"))], {"DEEPMAP_CALLS_AUDIT": "python"})
        got = next(n for n in nodes if n["key"] == "w::outer").get("call_weak")
        rec.check(f"P audit: an untyped `nt.go()` inside a {'nested def (detached: no witness)' if want is None else 'lambda (attributed: a witness)'}"
                  f" -> {'not ' if want is None else ''}demoted", got == want, got)


# ================================================================================================
# R: the REAL engine (whichever graphify is installed: 0.8.39 on the dev box, 0.8.51 in CI) — its attribution of
#    every nested scope kind equals the LanguageSpec tables, and the core over its real nodes emits exactly the
#    attributed sites' targets (the drift lock between graphify's `function_boundary_types` and the core)
# ================================================================================================

R_PY_FN = """def h_lambda(): return 1
def h_comp(): return 1
def h_genexp(): return 1
def h_nested(): return 1
def h_method(): return 1
def h_classbody(): return 1
def h_deco(): return (lambda f: f)
def h_default(): return 1
def h_async(): return 1


def outer(xs):
    f = lambda: h_lambda()
    ys = [h_comp() for x in xs]
    g = sum(h_genexp() for x in xs)
    def inner():
        return h_nested()
    class Local:
        attr = h_classbody()
        def run(self):
            return h_method()
    @h_deco()
    def decorated():
        return 1
    def with_default(a=h_default()):
        return a
    async def anested():
        return h_async()
    return f, ys, g, inner, Local, decorated, with_default, anested
"""
R_PY_WHERE = {"h_lambda": "lambda", "h_comp": "comprehension", "h_genexp": "comprehension", "h_nested": "function",
              "h_method": "method", "h_classbody": "own", "h_deco": "own", "h_default": "header",
              "h_async": "function"}
R_TS_FN = """export function hArrow(): number { return 1; }
export function hFnDecl(): number { return 1; }
export function hFnExpr(): number { return 1; }
export function hGenDecl(): number { return 1; }
export function hGenExpr(): number { return 1; }
export function hMethod(): number { return 1; }
export function hObjMethod(): number { return 1; }
export function hObjFn(): number { return 1; }
export function hField(): number { return 1; }
export function hFieldFn(): number { return 1; }
export function hFieldArrow(): number { return 1; }
export function hGetter(): number { return 1; }
export function outer(xs: number[]) {
  const a = () => hArrow();
  function inner() { return hFnDecl(); }
  const e = function () { return hFnExpr(); };
  function* gen() { yield hGenDecl(); }
  const ge = function* () { yield hGenExpr(); };
  class Local {
    f = hField();
    ff = function () { return hFieldFn(); };
    fa = () => hFieldArrow();
    run() { return hMethod(); }
    get g() { return hGetter(); }
  }
  const o = { m() { return hObjMethod(); }, k: function () { return hObjFn(); } };
  return [a, inner, e, gen, ge, Local, o];
}
"""
R_TS_WHERE = {"hArrow": "arrow", "hFnDecl": "function_decl", "hFnExpr": "function_expr",
              "hGenDecl": "generator_decl", "hGenExpr": "function_expr", "hMethod": "method_def",
              "hObjMethod": "method_def", "hObjFn": "function_expr", "hField": "own", "hFieldFn": "function_expr",
              "hFieldArrow": "arrow", "hGetter": "method_def"}
R_PY_CLS = """class HLambda:
    pass


class HComp:
    pass


class HNested:
    pass


class HMethod:
    pass


class HClassBody:
    pass


class HDefault:
    pass


def outer(xs):
    f = lambda: HLambda()
    ys = [HComp() for x in xs]
    def inner():
        return HNested()
    class Local:
        attr = HClassBody()
        def run(self):
            return HMethod()
    def with_default(a=HDefault()):
        return a
    return f, ys, inner, Local, with_default
"""
R_TS_CLS = """export class HArrow {}
export class HFnDecl {}
export class HFnExpr {}
export class HGenDecl {}
export class HMethod {}
export class HField {}
export function outer() {
  const a = () => new HArrow();
  function inner() { return new HFnDecl(); }
  const e = function () { return new HFnExpr(); };
  function* gen() { yield new HGenDecl(); }
  class Local {
    f = new HField();
    run() { return new HMethod(); }
  }
  return [a, inner, e, gen, Local];
}
"""
# graphify's SYMBOL-RESOLUTION pass (the phase-2 review): helpers imported from a SECOND file, called bare inside a
# nested def, a nested def's default, a local class's method and a lambda in a nested def of an undecorated top-level
# def / async def — the engine gives each call to the top-level def; never inside a decorated def or a method
R_PY_GLIB = "".join(f"class {h}(Exception):\n    pass\n\n\n" for h in
                    ("GNested", "GAsync", "GDefault", "GLocal", "GLam", "GDeco", "GMeth"))
R_PY_GLUE = """from rp.glib import GNested, GAsync, GDefault, GLocal, GLam, GDeco, GMeth


def deco(f):
    return f


def gouter():
    def inner():
        raise GNested()
    def with_default(a=GDefault()):
        return a
    class Local:
        def run(self):
            raise GLocal()
    def lam_in_nested():
        return lambda: GLam()
    return inner, with_default, Local, lam_in_nested


async def gasync():
    def inner():
        raise GAsync()
    return inner


@deco
def gdeco():
    def inner():
        raise GDeco()
    return inner


class GK:
    def meth(self):
        def inner():
            raise GMeth()
        return inner
"""
R_PY_GLUE_WALKED = {"gouter": {"GNested", "GDefault", "GLocal", "GLam"}, "gasync": {"GAsync"}, "gdeco": set(),
                    "GK.meth": set()}
R_JS_GLIB = ("export function gArrow() { return 1; }\nexport function gDecl() { return 1; }\n"
             "export function gExp() { return 1; }\nexport function gMeth() { return 1; }\n")
R_JS_GLUE = """import { gArrow, gDecl, gExp, gMeth } from './jglib';

function jouter() {
  const a = () => gArrow();
  function i() { return gDecl(); }
  return [a, i];
}

const jarrow = () => {
  const a = () => gArrow();
  return a;
};

export function jexp() {
  const a = () => gExp();
  return a;
}

class JK {
  m() {
    const a = () => gMeth();
    return a;
  }
}

export { jouter, jarrow, JK };
"""
# the engine walks the root function declaration and the root const arrow, never an exported declaration or a method;
# the core reads every root function_decl / arrow as walked (an exported one too: conservative)
R_JS_GLUE_WALKED = {"jouter": {"gArrow", "gDecl"}, "jarrow": {"gArrow"}, "jexp": set(), "JK.m": set()}
R_JS_GLUE_KIND = {"jouter": "function_decl", "jarrow": "arrow", "jexp": "function_decl", "JK.m": "method_def"}
R_FILES = {"rp/m.py": R_PY_FN, "rp/w.ts": R_TS_FN, "rp/j.js": R_TS_FN.replace(": number[]", "").replace(": number", ""),
           "rp/c.py": R_PY_CLS, "rp/c.ts": R_TS_CLS, "rp/glib.py": R_PY_GLIB, "rp/glue.py": R_PY_GLUE,
           "rp/jglib.js": R_JS_GLIB, "rp/jglue.js": R_JS_GLUE}
_ENGINE_CACHE: dict = {}


def real_engine():
    """(nodes, calls, version) of the installed graphify over R_FILES (computed once per gate run; a temp
    DEEPMAP_HOME, CALLS_PLUS / PRECISE / supplemental calls pinned off), ("skip", why) or ("fail", why)."""
    if "r" in _ENGINE_CACHE:
        return _ENGINE_CACHE["r"]
    import contextlib
    import io
    import os
    import shutil
    import tempfile
    try:
        found = importlib.util.find_spec("graphify") is not None
    except Exception:  # noqa: BLE001
        found = False
    if not found:
        _ENGINE_CACHE["r"] = ("skip", "graphify is not installed in this interpreter")
        return _ENGINE_CACHE["r"]
    tmp = Path(tempfile.mkdtemp(prefix="tcg"))
    pins = {"DEEPMAP_HOME": str(tmp / "h"), "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_PRECISE_CALLS": "0",
            "DEEPMAP_SUPPLEMENTAL_TS_RUST": "0", "DEEPMAP_EXTRACTOR": "graphify"}
    saved = {k: os.environ.get(k) for k in pins}
    try:
        root = tmp / "t"
        for rel, text in R_FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        os.environ.update(pins)
        GX = importlib.import_module("graphify_extract")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(root)
        _ENGINE_CACHE["r"] = (("fail", (rep or {}).get("cause")) if not res
                              else (res[0], res[1], GX.graphify_version()))
    except Exception as exc:  # noqa: BLE001
        _ENGINE_CACHE["r"] = ("fail", repr(exc))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    return _ENGINE_CACHE["r"]


def _line_of(text: str, needle: str, nth: int = 0) -> int:
    hits = [i for i, ln in enumerate(text.split("\n"), 1) if needle in ln]
    return hits[nth]


def _r_py_facts(T, rel: str):
    """Fake-reader facts for R_PY_CLS exactly as the reader contract describes them."""
    text = R_PY_CLS
    S, SC, CF, MF = T.Site, T.Scope, T.ClassFacts, T.MethodFacts
    L = lambda needle: _line_of(text, needle)    # noqa: E731

    def bare(needle):
        ln = L(needle + "()")
        return S(ln, bcol(text, ln, needle + "()"), needle, None, "bare", 0)

    helpers = ["HLambda", "HComp", "HNested", "HMethod", "HClassBody", "HDefault"]
    classes = [CF(h, L(f"class {h}:"), L(f"class {h}:") + 1) for h in helpers]
    lo, hi = L("def outer"), L("return f, ys")
    classes.append(CF("Local", L("class Local:"), L("return HMethod()"), scope=0, methods={"run": [MF(L("def run"))]}))
    scopes = [SC("outer", lo, hi, params={"xs": T.Binding("param")}, sites=[bare("HClassBody"), bare("HDefault")],
                 kind="function"),
              SC("<lambda>", L("HLambda()"), L("HLambda()"), parent=0, sites=[bare("HLambda")], kind="lambda"),
              SC("<listcomp>", L("HComp()"), L("HComp()"), parent=0, sites=[bare("HComp")], kind="comprehension"),
              SC("inner", L("def inner"), L("return HNested()"), parent=0, sites=[bare("HNested")], kind="function"),
              SC("run", L("def run"), L("return HMethod()"), parent=0, cls=len(helpers), self_kind="inst",
                 sites=[bare("HMethod")], kind="method"),
              SC("with_default", L("def with_default"), L("return a"), parent=0, kind="function")]
    scopes[0].all_names = {h: 1 for h in helpers}
    return T.FileFacts(rel, "python", classes=classes, functions={"outer": [T.FunctionFacts("outer", lo)]},
                       scopes=scopes, decls=decls_of(text))


def _r_ts_facts(T, rel: str):
    text = R_TS_CLS
    S, SC, CF = T.Site, T.Scope, T.ClassFacts
    L = lambda needle: _line_of(text, needle)    # noqa: E731

    def new(name):
        ln = L(f"new {name}()")
        return S(ln, bcol(text, ln, f"new {name}"), name, T.r_classref(T.t_name(name)), "ctor", 0)

    helpers = ["HArrow", "HFnDecl", "HFnExpr", "HGenDecl", "HMethod", "HField"]
    classes = [CF(h, L(f"class {h} "), L(f"class {h} ")) for h in helpers]
    classes.append(CF("Local", L("class Local"), L("run()") + 1, scope=0,
                      methods={"run": [T.MethodFacts(L("run()"))]}))
    lo = L("export function outer")
    scopes = [SC("outer", lo, lo + 10, sites=[new("HField")], kind="function_decl"),
              SC("a", L("const a ="), L("const a ="), parent=0, sites=[new("HArrow")], kind="arrow"),
              SC("inner", L("function inner"), L("function inner"), parent=0, sites=[new("HFnDecl")],
                 kind="function_decl"),
              SC("e", L("const e ="), L("const e ="), parent=0, sites=[new("HFnExpr")], kind="function_expr"),
              SC("gen", L("function* gen"), L("function* gen"), parent=0, sites=[new("HGenDecl")],
                 kind="generator_decl"),
              SC("run", L("run()"), L("run()"), parent=0, cls=len(helpers), self_kind="inst", sites=[new("HMethod")],
                 kind="method_def")]
    scopes[0].all_names = {h: 1 for h in helpers}
    return T.FileFacts(rel, "typescript", classes=classes, functions={"outer": [T.FunctionFacts("outer", lo)]},
                       scopes=scopes, decls=decls_of(text))


def _r_glue_facts(T):
    """Fake-reader facts for R_PY_GLUE / R_PY_GLIB exactly as the reader contract describes them, and their binder."""
    text, rel, lrel = R_PY_GLUE, "rp/glue.py", "rp/glib.py"
    S, SC, CF, MF, B, FF = T.Site, T.Scope, T.ClassFacts, T.MethodFacts, T.Binding, T.FunctionFacts
    L = lambda needle, nth=0: _line_of(text, needle, nth)    # noqa: E731

    def bare(name):
        ln = L(name + "()")
        return S(ln, bcol(text, ln, name + "()"), name, None, "bare", 0)

    helpers = ["GNested", "GAsync", "GDefault", "GLocal", "GLam", "GDeco", "GMeth"]
    selfp = {"self": B("param", T.t_self())}
    scopes = [SC("deco", L("def deco"), L("def deco") + 1, params={"f": B("param")}, kind="function"),            # 0
              SC("gouter", L("def gouter"), L("return inner, with_default"), sites=[bare("GDefault")],
                 all_names={"GNested": 1, "GDefault": 1, "GLocal": 1, "GLam": 1}, kind="function"),             # 1
              SC("inner", L("def inner", 0), L("raise GNested"), parent=1, sites=[bare("GNested")],
                 kind="function"),                                                                               # 2
              SC("with_default", L("def with_default"), L("return a"), parent=1, params={"a": B("param")},
                 kind="function"),                                                                               # 3
              SC("run", L("def run"), L("raise GLocal"), parent=1, cls=0, self_kind="inst", params=selfp,
                 sites=[bare("GLocal")], kind="method"),                                                         # 4
              SC("lam_in_nested", L("def lam_in_nested"), L("return lambda"), parent=1, kind="function"),       # 5
              SC("<lambda>", L("return lambda"), L("return lambda"), parent=5, sites=[bare("GLam")],
                 kind="lambda"),                                                                                 # 6
              SC("gasync", L("async def gasync"), L("raise GAsync") + 1, all_names={"GAsync": 1},
                 kind="function"),                                                                               # 7
              SC("inner", L("def inner", 1), L("raise GAsync"), parent=7, sites=[bare("GAsync")],
                 kind="function"),                                                                               # 8
              SC("gdeco", L("def gdeco"), L("raise GDeco") + 1, lines=(L("@deco"),), all_names={"GDeco": 1},
                 kind="function"),                                                                               # 9
              SC("inner", L("def inner", 2), L("raise GDeco"), parent=9, sites=[bare("GDeco")],
                 kind="function"),                                                                               # 10
              SC("meth", L("def meth"), L("raise GMeth") + 1, cls=1, self_kind="inst", params=selfp,
                 all_names={"GMeth": 1}, kind="method"),                                                         # 11
              SC("inner", L("def inner", 3), L("raise GMeth"), parent=11, sites=[bare("GMeth")],
                 kind="function")]                                                                               # 12
    f = T.FileFacts(rel, "python", imports={h: [T.Import(h, "named", "rp.glib", h, 1)] for h in helpers},
                    classes=[CF("Local", L("class Local"), L("raise GLocal"), scope=1, methods={"run": [MF(L("def run"))]}),
                             CF("GK", L("class GK"), L("return inner", 3), methods={"meth": [MF(L("def meth"))]})],
                    functions={"deco": [FF("deco", L("def deco"))], "gouter": [FF("gouter", L("def gouter"))],
                               "gasync": [FF("gasync", L("async def gasync"))],
                               "gdeco": [FF("gdeco", L("def gdeco"), lines=(L("@deco"),))]},
                    scopes=scopes, decls=decls_of(text))
    lib = T.FileFacts(lrel, "python", decls=decls_of(R_PY_GLIB),
                      classes=[CF(h, _line_of(R_PY_GLIB, f"class {h}("), _line_of(R_PY_GLIB, f"class {h}(") + 1,
                                  bases=(T.t_name("Exception"),)) for h in helpers])
    binder = FBinder(T, imports={(rel, h): T.Resolution("symbol", rel=lrel, name=h) for h in helpers})
    return {rel: f, lrel: lib}, binder


def sec_real_engine(T, rec: Rec) -> None:
    got = real_engine()
    if got[0] == "skip":
        print(f"  [SKIP] {got[1]} — the real-engine section is not run")
        return
    if got[0] == "fail":
        rec.check("R the installed graphify extracts the R fixture tree", False, got[1])
        return
    nodes, calls, ver = got
    print(f"  [INFO] graphify {ver}")
    name_of = {n["key"]: n["name"] for n in nodes}
    for rel, where, lang in (("rp/m.py", R_PY_WHERE, "python"), ("rp/w.ts", R_TS_WHERE, "typescript"),
                             ("rp/j.js", R_TS_WHERE, "javascript")):
        spec = T.LANGUAGES[lang]
        outer = [n["key"] for n in nodes if n["name"] == "outer" and n["source_file"] == rel]
        attributed = {T.name_seg(name_of.get(str(c[1]), str(c[1]))) for c in calls if outer and c[0] == outer[0]}
        known = all(w in ("own", "header") or w in spec.nested_attributed or w in spec.nested_detached
                    for w in where.values())
        want = {h for h, w in where.items() if w == "own" or (w == "header" and not getattr(spec, "header_detached",
                                                                                            False))
                or w in getattr(spec, "nested_attributed", ())}
        rec.check(f"R graphify {ver} {rel}: the engine attributes to outer() exactly the helpers the {lang} tables "
                  f"predict ({sorted(want)})", len(outer) == 1 and known and attributed == want,
                  {"engine": sorted(attributed), "tables": sorted(want), "kinds covered": known})
    nested_nodes = sorted(n["name"] for n in nodes if n["name"] in ("inner", "run", "Local", "Local.run", "gen", "a"))
    rec.check(f"R graphify {ver}: no nested def / local class / arrow gets a node (their calls belong to nobody)",
              not nested_nodes, nested_nodes)
    # the core over the REAL nodes and calls
    for rel, lang, facts_of in (("rp/c.py", "python", _r_py_facts), ("rp/c.ts", "typescript", _r_ts_facts)):
        ns = copy.deepcopy([n for n in nodes if n["source_file"] == rel])
        cs = [c for c in calls if any(c[0] == n["key"] for n in ns)]
        outer = next((n for n in ns if n["name"] == "outer"), None)
        engine_names = {T.name_seg(name_of.get(str(c[1]), str(c[1]))) for c in cs if outer and c[0] == outer["key"]}
        out, st = T.link(".", ns, list(cs), languages={lang: T.LANGUAGES[lang]},
                         readers={lang: FReader(T, {rel: facts_of(T, rel)})}, source=Src({rel: R_FILES[rel]}),
                         is_test=lambda p: False, env={"DEEPMAP_TYPED_CALLS": lang})
        emitted = {name_of.get(t) for t in (outer or {}).get("call_typed") or {}}
        want = {"rp/c.py": {"HLambda", "HComp", "HClassBody"},
                "rp/c.ts": {"HFnExpr", "HGenDecl", "HField"}}[rel]
        same_as_engine = (emitted == engine_names) if lang == "python" else True
        rec.check(f"R graphify {ver} {rel}: the core over the real nodes keys exactly the attributed sites "
                  f"{sorted(want)} — never a nested def's / method's / arrow's call"
                  + (" — and they are exactly the engine's own tuples of outer()" if lang == "python" else ""),
                  st["error"] is None and emitted == want and same_as_engine,
                  {"emitted": sorted(x for x in emitted if x), "engine": sorted(engine_names), "error": st["error"],
                   "files": st["files"], "failed": st["files_failed"]})
    # graphify's symbol-resolution pass (the phase-2 review): which top-level callers it walks whole
    import types
    gfacts, gbinder = _r_glue_facts(T)
    probe = types.SimpleNamespace(engine=None)
    helpers_of = {"python": {"GNested", "GAsync", "GDefault", "GLocal", "GLam", "GDeco", "GMeth"},
                  "javascript": {"gArrow", "gDecl", "gExp", "gMeth"}}
    for rel, walked_want, lang in (("rp/glue.py", R_PY_GLUE_WALKED, "python"),
                                   ("rp/jglue.js", R_JS_GLUE_WALKED, "javascript")):
        spec = T.LANGUAGES[lang]
        engine_walked = {}
        for caller in walked_want:
            ck = [n["key"] for n in nodes if n["source_file"] == rel and n["name"] == caller]
            names = {T.name_seg(name_of.get(str(c[1]), str(c[1]))) for c in calls if ck and c[0] == ck[0]}
            engine_walked[caller] = (len(ck), names & helpers_of[lang])
        rec.check(f"R graphify {ver} {rel}: the symbol pass gives each top-level caller exactly the bare calls of "
                  f"imported names its nested scopes make — a walked def / async def / root function / root arrow — "
                  f"and nothing to a decorated def, a method or an exported declaration",
                  all(n == 1 and got_ == walked_want[c] for c, (n, got_) in engine_walked.items()), engine_walked)
        if lang == "python":
            ff = gfacts[rel]
            table = {sc.name if sc.cls < 0 else "GK.meth": T._Linker.symbol_walked(probe, ff, sc, spec)
                     for sc in ff.scopes if sc.parent == -1 and sc.name in ("gouter", "gasync", "gdeco", "meth")}
            rec.check(f"R graphify {ver} {rel}: the core's symbol_walked (LanguageSpec.symbol_walk / symbol_plain) "
                      f"marks walked exactly the callers the engine walks",
                      table == {c: bool(v) for c, v in walked_want.items()}, table)
        else:
            rec.check(f"R graphify {ver} {rel}: every top-level caller the engine walks has a LanguageSpec.symbol_walk "
                      f"kind (the core may read more as walked — an exported declaration — never fewer)",
                      all(R_JS_GLUE_KIND[c] in spec.symbol_walk for c, v in walked_want.items() if v)
                      and R_JS_GLUE_KIND["JK.m"] not in spec.symbol_walk, R_JS_GLUE_KIND)
    ns = copy.deepcopy([n for n in nodes if n["source_file"] in ("rp/glue.py", "rp/glib.py")])
    cs = [c for c in calls if any(c[0] == n["key"] for n in ns)]
    out, st = T.link(".", ns, list(cs), languages={"python": T.LANGUAGES["python"]},
                     readers={"python": FReader(T, gfacts, gbinder)},
                     source=Src({"rp/glue.py": R_PY_GLUE, "rp/glib.py": R_PY_GLIB}), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "python"})
    walked_keys = {n["key"] for n in ns if n["name"] in ("gouter", "gasync")}
    lost = [c for c in cs if c[0] in walked_keys and c not in out]
    keyed_ = {n["name"]: n.get("call_typed") for n in ns if n.get("call_typed")}
    rec.check(f"R graphify {ver} rp/glue.py: the core over the real nodes removes NONE of the engine's tuples the "
              f"symbol pass gave gouter / gasync (their nested sites are uncertain) and keys nothing from a nested "
              f"scope", st["error"] is None and st["files"] == 2 and not lost and not keyed_
              and sum(1 for c in cs if c[0] in walked_keys) == 5,
              {"lost": lost, "keyed": keyed_, "error": st["error"], "files": st["files"],
               "engine tuples": sum(1 for c in cs if c[0] in walked_keys)})


# ================================================================================================
# T: the budget — the textual veto scans each span ONCE (one Counter per span, never one scan per (caller, name)),
#    computes byte columns only for declarations, and a minified line is unjudgeable
# ================================================================================================

def bundle_world(T, n=1000):
    """One JS line: class A + n functions, each `a.go(); a.go(); new A(); helper();` (4n sites)."""
    rel = "web/app.js"
    parts = ["class A { go() { return 1; } }"] + [f"function f{i}(a) {{ a.go(); a.go(); new A(); helper(); }}"
                                                     for i in range(n)]
    text = " ".join(parts) + "\n"
    line = text.split("\n")[0]
    S, SC = T.Site, T.Scope
    scopes = [SC("go", 1, 1, cls=0, self_kind="inst", kind="method_def")]
    pos = 0
    for i in range(n):
        at = line.index(f"function f{i}(", pos)
        pos = at + 1
        b = lambda s, k=at: len(line[:line.index(s, k)].encode("utf-8"))   # noqa: E731
        g1 = line.index("a.go()", at)
        g2 = line.index("a.go()", g1 + 1)
        scopes.append(SC(f"f{i}", 1, 1, locals={"a": [T.Binding("ctor", T.t_name("A"))]}, kind="function_decl",
                         sites=[S(1, g1, "go", T.r_ident("a"), "member", 0), S(1, g2, "go", T.r_ident("a"), "member", 0),
                                S(1, b("new A"), "A", T.r_classref(T.t_name("A")), "ctor", 0),
                                S(1, b("helper("), "helper", None, "bare", 0)],
                         all_names={"go": 2, "A": 1, "helper": 1}))
    f = T.FileFacts(rel, "javascript", classes=[T.ClassFacts("A", 1, 1, methods={"go": [T.MethodFacts(1)]})],
                    functions={f"f{i}": [T.FunctionFacts(f"f{i}", 1)] for i in range(n)}, scopes=scopes,
                    decls=frozenset({(1, line.index("class A") + 6), (1, line.index("go()"))}
                                    | {(1, line.index(f"function f{i}(") + 9) for i in range(n)}))
    nodes = [nd("b::A", "Class", "A", rel, 1), nd("b::A.go", "Method", "A.go", rel, 1)] + \
        [nd(f"b::f{i}", "Function", f"f{i}", rel, 1) for i in range(n)]
    calls = [(f"b::f{i}", m) for i in range(n) for m in ("go", "A", "helper")]
    return {rel: text}, {rel: f}, nodes, calls


def corpus_world(T, files=60, fns=25):
    """A multi-line Python corpus: `files` files x `fns` functions, each with 2 typed `go` sites, a ctor and an
    unknown bare call, in a nested lambda too."""
    texts, facts, nodes, calls = {}, {}, [], []
    S, SC, B = T.Site, T.Scope, T.Binding
    for fi in range(files):
        rel = f"pkg/m{fi}.py"
        lines = ["class A:", "    def go(self):", "        return 1", ""]
        scopes = [SC("go", 2, 3, cls=0, self_kind="inst", kind="method")]
        nodes += [nd(f"{rel}::A", "Class", "A", rel, 1), nd(f"{rel}::A.go", "Method", "A.go", rel, 2)]
        for j in range(fns):
            lo = len(lines) + 1
            lines += [f"def f{j}(a: A):", "    a.go()", "    g = lambda: a.go()", "    x = A()", "    helper(x)",
                      "    return g", ""]
            scopes.append(SC(f"f{j}", lo, lo + 5, params={"a": B("param", T.t_name("A"))}, kind="function",
                             sites=[S(lo + 1, 4, "go", T.r_ident("a"), "member", 0), S(lo + 3, 8, "A", None, "bare", 0),
                                    S(lo + 4, 4, "helper", None, "bare", 1)],
                             all_names={"go": 2, "A": 1, "helper": 1}))
            scopes.append(SC("<lambda>", lo + 2, lo + 2, parent=len(scopes) - 1, kind="lambda",
                             sites=[S(lo + 2, 16, "go", T.r_ident("a"), "member", 0)]))
            nodes.append(nd(f"{rel}::f{j}", "Function", f"f{j}", rel, lo))
            calls += [(f"{rel}::f{j}", "go"), (f"{rel}::f{j}", "A"), (f"{rel}::f{j}", "helper")]
        text = "\n".join(lines) + "\n"
        texts[rel] = text
        facts[rel] = T.FileFacts(rel, "python", classes=[T.ClassFacts("A", 1, 3, methods={"go": [T.MethodFacts(2)]})],
                                 functions={f"f{j}": [T.FunctionFacts(f"f{j}", 5 + 7 * j)] for j in range(fns)},
                                 scopes=scopes, decls=decls_of(text))
    return texts, facts, nodes, calls


def sec_budget(T, rec: Rec) -> None:
    import time
    files, facts, nodes, calls = bundle_world(T)
    t0 = time.perf_counter()
    out, st = T.link(".", nodes, calls, languages={"javascript": T.LANGUAGES["javascript"]},
                     readers={"javascript": FReader(T, facts)}, source=Src(files), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "javascript"})
    dt = time.perf_counter() - t0
    print(f"  [INFO] one-line bundle (1,000 functions, 4,000 sites): link {dt:.2f}s")
    rec.check("T a one-line 1,000-function bundle links in under 10 s (the per-match prefix re-encode made it "
              "~60 s; the review's target is ~2 s on an idle box)", dt < 10 and st["error"] is None, (dt, st["error"]))
    rec.check("T ... its spans hold a MINIFIED_LINE+ line: unjudgeable -> no REPLACE (vetoes `text`), the typed "
              "keys still added", st["removed"] == 0 and st["vetoes"]["text"] == 2000 and st["emitted"] == 2000,
              (st["removed"], st["vetoes"], st["emitted"]))
    scans = {"n": 0}

    class CountingRx:                                   # every regex scan of the call-shape pattern is counted
        def __init__(self, rx):
            self.rx = rx

        def finditer(self, *a):
            scans["n"] += 1
            return self.rx.finditer(*a)

        def findall(self, *a):
            scans["n"] += 1
            return self.rx.findall(*a)

    texts, facts, nodes, calls = corpus_world(T)
    saved = dict(T._CALL_SHAPES)
    try:
        for k, rx in saved.items():
            T._CALL_SHAPES[k] = CountingRx(rx)
        t0 = time.perf_counter()
        out, st = link(T, texts, facts, nodes, calls, ON)
        dt = time.perf_counter() - t0
    finally:
        T._CALL_SHAPES.clear()
        T._CALL_SHAPES.update(saved)
    callers = sum(1 for n in nodes if n["label"] == "Function")
    print(f"  [INFO] synthetic corpus ({len(texts)} files, {callers} callers, {st['sites']}): link {dt:.2f}s, "
          f"{scans['n']} span scans")
    rec.check("T the textual veto scans each caller span once (scans <= callers), never once per (caller, name)",
              0 < scans["n"] <= callers and st["groups"]["replace"] >= callers, (scans["n"], callers, st["groups"]))
    rec.check("T a fixed synthetic corpus (60 files, 1,500 callers, 6,000 sites) links in under 20 s",
              dt < 20 and st["error"] is None, dt)
    # a NON-BLOCKING timing report of the REAL Python reader over build/ (design 8.5 P is S6's measurement of the
    # whole pass on this repository; this line only makes a reader regression visible in every run) — the one check
    # is that every product file lowers to a well-formed FileFacts
    import ast as _ast
    import gc as _gc
    try:
        import receiver_facts_py as RFP  # noqa: WPS433 — the sibling reader of this build/
    except Exception as exc:  # noqa: BLE001
        rec.check("T the Python reader imports", False, repr(exc))
        return
    files = sorted(BUILD.glob("*.py"))
    texts = [(p.name, p.read_text(encoding="utf-8")) for p in files]
    paused = _gc.isenabled()
    _gc.disable()
    try:
        t0 = time.perf_counter()
        trees = [(rel, _ast.parse(text), text.split("\n")) for rel, text in texts]
        t1 = time.perf_counter()
        bad = [rel for rel, tree, lines in trees if T.validate_facts(RFP.file_facts(rel, tree, lines, T))]
        t2 = time.perf_counter()
    finally:
        if paused:
            _gc.enable()
    mb = sum(len(t) for _r, t in texts) / 1e6
    print(f"  [INFO] the real Python reader over build/ ({len(files)} files, {mb:.1f} MB): ast.parse {t1 - t0:.2f}s, "
          f"facts + validation {t2 - t1:.2f}s ({mb / max(t2 - t1, 1e-6):.1f} MB/s) — not a budget; the P criterion "
          f"(the pass <= 15 s on this repository) is S6's measurement")
    rec.check("T every build/ module lowers to a well-formed FileFacts through the real reader", files and not bad,
              bad[:5])


# ================================================================================================
# U: the review's gate gaps — each fixture below turns RED under the mutant that broke its rule (section Q)
# ================================================================================================

def sec_review(T, rec: Rec) -> None:
    S, B, CF, MF, SC = T.Site, T.Binding, T.ClassFacts, T.MethodFacts, T.Scope
    # U1 an intermediate MRO class in ANOTHER file: the chain is not in-file, the key is INFERRED (design 4)
    xr, yr = "x.py", "y.py"
    xt = ("from y import Mid\n\n\nclass Base:\n    def m(self):\n        return 1\n\n\nclass Sub(Mid):\n    pass\n\n\n"
          "def caller(s: Sub):\n    s.m()\n")
    yt = "from x import Base\n\n\nclass Mid(Base):\n    pass\n"
    fx = T.FileFacts(xr, "python", imports={"Mid": [T.Import("Mid", "named", "y", "Mid", 1)]},
                     classes=[CF("Base", 4, 6, methods={"m": [MF(5)]}), CF("Sub", 9, 10, bases=(T.t_name("Mid"),))],
                     functions={"caller": [T.FunctionFacts("caller", 13)]},
                     scopes=[SC("m", 5, 6, cls=0, self_kind="inst", kind="method"),
                             SC("caller", 13, 14, params={"s": B("param", T.t_name("Sub"))}, kind="function",
                                sites=[S(14, 4, "m", T.r_ident("s"), "member", 0)], all_names={"m": 1})],
                     decls=decls_of(xt))
    fy = T.FileFacts(yr, "python", imports={"Base": [T.Import("Base", "named", "x", "Base", 1)]},
                     classes=[CF("Mid", 4, 5, bases=(T.t_name("Base"),))], decls=decls_of(yt))
    bd = FBinder(T, imports={(xr, "Mid"): T.Resolution("symbol", yr, "Mid"),
                             (yr, "Base"): T.Resolution("symbol", xr, "Base")})
    nodes = [nd("x::Base", "Class", "Base", xr, 4), nd("x::Base.m", "Method", "Base.m", xr, 5),
             nd("x::Sub", "Class", "Sub", xr, 9), nd("y::Mid", "Class", "Mid", yr, 4),
             nd("x::caller", "Function", "caller", xr, 13)]
    link(T, {xr: xt, yr: yt}, {xr: fx, yr: fy}, nodes, [("x::caller", "m")], ON, binder=bd)
    rec.check("U1 target and receiver in the caller's file, but the MRO walks Mid in y.py: INFERRED, not EXTRACTED",
              caller_node(nodes).get("call_typed") == {"x::Base.m": "INFERRED"}, caller_node(nodes).get("call_typed"))
    # U2 two scopes on ONE node (a TS declarator line holding two functions named h): neither is a caller
    rel = "w.ts"
    text = ("export class A {\n  go(): number { return 1; }\n}\n"
            "export const h = (a: A) => a.go(); export function h2(b) { return b.go(); }\n")
    f = T.FileFacts(rel, "typescript", classes=[CF("A", 1, 3, methods={"go": [MF(2)]})],
                    scopes=[SC("h", 4, 4, params={"a": B("param", T.t_name("A"))}, kind="arrow",
                               sites=[S(4, 27, "go", T.r_ident("a"), "member", 0)]),
                            SC("h", 4, 4, params={"b": B("param")}, kind="function_decl",
                               sites=[S(4, 64, "go", T.r_ident("b"), "member", 0)])],
                    decls=decls_of(text) | {(2, 2)})
    nodes = [nd("w::A", "Class", "A", rel, 1), nd("w::A.go", "Method", "A.go", rel, 2), nd("w::h", "Function", "h", rel, 4)]
    calls = [("w::h", "go")]
    out, st = _ts_link(T, {rel: text}, {rel: f}, nodes, calls)
    rec.check("U2 two scopes mapping to one node pool nothing: the node is no caller (no key, the tuple stays)",
              out is calls and "call_typed" not in nodes[-1], (keyed(T, out), nodes[-1].get("call_typed")))
    # U3 overloads narrowed by arity: the call passes 2 arguments, only the 2-argument `find` fits
    rel = "o.ts"
    text = ("export class R {\n  find(a: string): number { return 1; }\n  find(a: string, b: number): number { return 2; }"
            "\n}\nexport function caller(r: R) {\n  r.find('x', 1);\n}\n")
    f = T.FileFacts(rel, "typescript", classes=[CF("R", 1, 4, methods={"find": [MF(2, arity=(1, 1)),
                                                                               MF(3, arity=(2, 2))]})],
                    functions={"caller": [T.FunctionFacts("caller", 5)]},
                    scopes=[SC("caller", 5, 7, params={"r": B("param", T.t_name("R"))}, kind="function_decl",
                               sites=[S(6, 2, "find", T.r_ident("r"), "member", 2)])],
                    decls=decls_of(text) | {(2, 2), (3, 2)})
    nodes = [nd("o::R", "Class", "R", rel, 1), nd("o::R.find1", "Method", "R.find", rel, 2),
             nd("o::R.find2", "Method", "R.find", rel, 3), nd("o::caller", "Function", "caller", rel, 5)]
    _ts_link(T, {rel: text}, {rel: f}, nodes, [("o::caller", "find")])
    rec.check("U3 overloads are filtered by arity: one target, EXTRACTED",
              caller_node(nodes).get("call_typed") == {"o::R.find2": "EXTRACTED"}, caller_node(nodes).get("call_typed"))
    # U4 the audit's SUBCLASS exemption where it is the only guard: `Sub.notify(nt)` with a dynamic Sub
    files, facts, nodes, calls = aud_world(T, [("Sub.notify(nt)", S(20, 4, "notify", T.r_ident("Sub"), "member", 1))])
    facts["svc/n.py"].classes[2].dynamic = True
    link(T, files, facts, nodes, calls, {"DEEPMAP_CALLS_AUDIT": "python"})
    rec.check("U4 audit: a class reference to a SUBCLASS of the pick's owner (Sub is dynamic: the site is not typed) "
              "is not demoted", caller_node(nodes).get("call_weak") is None, caller_node(nodes).get("call_weak"))
    # U5 a reader that hands back facts for ANOTHER file
    files, facts, nodes, calls = go_world(T, b_ann="B")
    other = copy.copy(facts["pkg/a.py"])
    other.rel = "pkg/zz.py"
    out, st = link(T, files, {"pkg/a.py": other}, nodes, calls, ON)
    rec.check("U5 a FileFacts whose rel is another file's is that file's failure (nothing linked)",
              st["files_failed"] == 1 and st["files"] == 0 and out is calls, (st["files_failed"], st["files"]))
    # U6 two ClassFacts that map to ONE class node (a conditional redefinition): the class never resolves
    files, facts, nodes, calls = go_world(T, b_ann="B")
    ff = facts["pkg/a.py"]
    ff.classes.append(CF("B", 30, 31, scope=0, methods={"go": [MF(31)]}))   # a local B elsewhere (no node of
    out, st = link(T, files, facts, nodes, calls, ON)                        # its own: maps onto a::B by name)
    rec.check("U6 two class definitions on ONE class node (a local B elsewhere falls back onto a::B): B is "
              "ambiguous, `b: B; b.go()` is unknown (ADD)",
              ("a::caller", "go") in out and "a::B.go" not in (caller_node(nodes).get("call_typed") or {}),
              (keyed(T, out), caller_node(nodes).get("call_typed")))
    # U7 two nodes at one (file, line, name): node_at finds none, never one of them
    files, facts, nodes, calls = go_world(T, b_ann="B")
    nodes.append(nd("a::caller#2", "Function", "caller", "pkg/a.py", 11))
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("U7 two caller nodes at one (file, line, name): no caller is guessed (nothing keyed or removed)",
              out is calls and all("call_typed" not in n for n in nodes), keyed(T, out))
    # U8 a bare call of a builtin FUNCTION is external: with every other site settled, its tuple is replaced
    files, facts, nodes, calls = go_world(T, b_ann="B", line14="    return len(a)",
                                          extra_sites=[S(14, 11, "len", None, "bare", 1)],
                                          calls=[("a::caller", "go"), ("a::caller", "len")])
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("U8 a bare `len(a)` (an unbound builtin function) is external: the `len` name tuple is removed",
              ("a::caller", "len") not in out and st["sites"]["external"] == 1, (keyed(T, out), st["sites"]))
    # U9 the implicit-this bare call (Java / C#): resolves through the enclosing class; a local of that name blocks
    jrel = "src/Svc.java"
    jtext = ("class Svc {\n  void helper() {}\n  void run() {\n    helper();\n  }\n  void run2() {\n"
             "    Runnable helper = null;\n    helper();\n  }\n}\n")
    for local, want in ((False, {"j::Svc.helper": "EXTRACTED"}), (True, None)):
        sc_run = SC("run2" if local else "run", 6 if local else 3, 9 if local else 5, cls=0, self_kind="inst",
                    kind="method", locals={"helper": [B("unknown")]} if local else {},
                    sites=[S(8 if local else 4, 4, "helper", None, "bare", 0)])
        f = T.FileFacts(jrel, "java", classes=[CF("Svc", 1, 10, methods={"helper": [MF(2, arity=(0, 0))],
                                                                         "run": [MF(3)], "run2": [MF(6)]})],
                        scopes=[SC("helper", 2, 2, cls=0, self_kind="inst", kind="method"), sc_run],
                        decls=frozenset({(2, 7), (3, 7), (6, 7)}))
        caller = "j::Svc.run2" if local else "j::Svc.run"
        nodes = [nd("j::Svc", "Class", "Svc", jrel, 1), nd("j::Svc.helper", "Method", "Svc.helper", jrel, 2),
                 nd("j::Svc.run", "Method", "Svc.run", jrel, 3), nd("j::Svc.run2", "Method", "Svc.run2", jrel, 6)]
        T.link(".", nodes, [(caller, "helper")], languages={"java": T.LANGUAGES["java"]},
               readers={"java": FReader(T, {jrel: f})}, source=Src({jrel: jtext}), is_test=lambda p: False,
               env={"DEEPMAP_TYPED_CALLS": "java"})
        got = next(n for n in nodes if n["key"] == caller).get("call_typed")
        rec.check(f"U9 Java implicit this: a bare `helper()` {'shadowed by a local stays unknown' if local else 'binds Svc.helper'}",
                  got == want, got)
    # U10 the audit census counts :Method nodes only (a same-named :Function is not a second candidate)
    files, facts, nodes, calls = aud_world(T, [("nt.notify()", S(20, 4, "notify", T.r_ident("nt"), "member", 0))],
                                           extra_methods=False)
    nodes.append(nd("q::notify", "Function", "notify", "other/q.py", 1))
    link(T, files, facts, nodes, calls, {"DEEPMAP_CALLS_AUDIT": "python"})
    rec.check("U10 audit: one notify :Method plus a notify :Function -> still one candidate, not demoted",
              caller_node(nodes).get("call_weak") is None, caller_node(nodes).get("call_weak"))
    # U11 a long fluent chain never poisons its receiver's sibling site (a depth-cut value is not memoised): the
    #     chain's outermost site is evaluated first; whichever length makes the cut fire INSIDE `q`'s binding, the
    #     later shallow `q.run()` must still be typed
    rel = "web/q.ts"
    lost = []
    for chain_len in range(4, 21):
        text = ("export class Q {\n  a(): Q { return this; }\n  run(): number { return 1; }\n}\n"
                "export function caller(q: Q) {\n  q" + ".a()" * chain_len + ";\n  q.run();\n}\n")
        recv = T.r_ident("q")
        for _ in range(chain_len - 1):
            recv = T.r_call(recv, "a")
        f = T.FileFacts(rel, "typescript", classes=[CF("Q", 1, 4, methods={"a": [MF(2, returns=T.t_name("Q"))],
                                                                          "run": [MF(3)]})],
                        functions={"caller": [T.FunctionFacts("caller", 5)]},
                        scopes=[SC("caller", 5, 8, params={"q": B("param", T.t_name("Q"))}, kind="function_decl",
                                   sites=[S(6, 2, "a", recv, "member", 0),
                                          S(7, 2, "run", T.r_ident("q"), "member", 0)])],
                        decls=decls_of(text) | {(2, 2), (3, 2)})
        nodes = [nd("q::Q", "Class", "Q", rel, 1), nd("q::Q.a", "Method", "Q.a", rel, 2),
                 nd("q::Q.run", "Method", "Q.run", rel, 3), nd("q::caller", "Function", "caller", rel, 5)]
        _ts_link(T, {rel: text}, {rel: f}, nodes, [("q::caller", "run")])
        if (caller_node(nodes).get("call_typed") or {}).get("q::Q.run") != "EXTRACTED":
            lost.append(chain_len)
    rec.check("U11 a 4..20-call fluent chain evaluated first (the depth cap firing inside it) never poisons the "
              "sibling `q.run()` (a cut value is not memoised)", not lost, lost)
    # U12 an annotation naming an EXTERNAL class whose member a project subclass defines (Python: unchecked)
    rel, mrel = "app/views.py", "app/models.py"
    text = "from django.db.models import QuerySet\n\n\ndef published_titles(qs: QuerySet):\n    return qs.published()\n"
    mtext = ("from django.db.models import QuerySet\n\n\nclass ArticleQuerySet(QuerySet):\n"
             "    def published(self):\n        return self.filter(p=True)\n")

    def p3(member, sub_defines=True):
        f = T.FileFacts(rel, "python", imports={"QuerySet": [T.Import("QuerySet", "named", "django.db.models",
                                                                       "QuerySet", 1)]},
                        functions={"published_titles": [T.FunctionFacts("published_titles", 4)]},
                        scopes=[SC("published_titles", 4, 5, params={"qs": B("param", T.t_name("QuerySet"))},
                                   sites=[S(5, 11, member, T.r_ident("qs"), "member", 0)], all_names={member: 1},
                                   kind="function")], decls=decls_of(text))
        mf = T.FileFacts(mrel, "python", imports={"QuerySet": [T.Import("QuerySet", "named", "django.db.models",
                                                                         "QuerySet", 1)]},
                         classes=[CF("ArticleQuerySet", 4, 6, bases=(T.t_name("QuerySet"),),
                                     methods={"published": [MF(5)]} if sub_defines else {})],
                         scopes=[SC("published", 5, 6, cls=0, self_kind="inst", kind="method")], decls=decls_of(mtext))
        b = FBinder(T, imports={(rel, "QuerySet"): T.Resolution("external", stems=("django",)),
                                (mrel, "QuerySet"): T.Resolution("external", stems=("django",))})
        nodes = [nd("v::published_titles", "Function", "published_titles", rel, 4),
                 nd("m::ArticleQuerySet", "Class", "ArticleQuerySet", mrel, 4),
                 nd("m::ArticleQuerySet.published", "Method", "ArticleQuerySet.published", mrel, 5)]
        calls = [("v::published_titles", member)]
        out, st = link(T, {rel: text.replace("published()", member + "()"), mrel: mtext}, {rel: f, mrel: mf}, nodes,
                       calls, ON, binder=b)
        return out, st, calls

    out, st, calls = p3("published")
    rec.check("U12 `qs: QuerySet` (external) calling a member a project subclass of QuerySet defines: unknown — "
              "the engine's tuple (it may reach the true runtime target) is kept", out is calls
              and st["removed"] == 0 and st["sites"]["external"] == 0, (keyed(T, out), st["sites"]))
    out, st, calls = p3("published", sub_defines=False)
    rec.check("U12 ... with no project subclass defining it, the external verdict still removes the tuple",
              out != calls and st["removed"] == 1 and st["sites"]["external"] == 1, (keyed(T, out), st["sites"]))
    # U13 a module-level name bound in an unreadable way is in globals as unknown: never builtin / external
    for unknown_global in (True, False):
        files, facts, nodes, calls = go_world(T, b_ann="B", line14="    return input()",
                                              extra_sites=[S(14, 11, "input", None, "bare", 0)],
                                              calls=[("a::caller", "go"), ("a::caller", "input")])
        if unknown_global:
            facts["pkg/a.py"].globals["input"] = [B("unknown")]
        out, st = link(T, files, facts, nodes, calls, ON)
        if unknown_global:
            rec.check("U13 `for input in ...:` at module level (globals input = [unknown]): a bare `input()` is unknown, "
                      "its tuple stays", ("a::caller", "input") in out and st["sites"]["external"] == 0, keyed(T, out))
        else:
            rec.check("U13 ... unbound, the builtin `input()` is external (removed) — the reason the reader contract "
                      "asks for the unknown global", ("a::caller", "input") not in out, keyed(T, out))
    # U14 `super(Other, self).m()` is OTHER (reader contract): no typed edge, no removal
    files, facts, nodes, calls = aud_world(T, [("super(B, self).notify()", S(20, 4, "notify", T.R_OTHER, "member", 0))],
                                           callee="notify")
    out, st = link(T, files, facts, nodes, calls, ON)
    rec.check("U14 a two-argument super naming another class (OTHER) is unknown: nothing keyed or removed",
              ("n::caller", "notify") in out and "call_typed" not in caller_node(nodes)
              and not any(c[0] == "n::caller" and T._is_call_key(c[1]) for c in out), keyed(T, out))
    # U15 `this` in an arrow nested in a method: typed only when the arrow scope carries the method's class
    rel = "web/k.ts"
    text = ("export class K {\n  go(): number { return 1; }\n  run(): number {\n    this.go();\n"
            "    const f = () => this.go();\n    return f();\n  }\n}\n")
    for cls_on_arrow, want_removed in ((0, 1), (-1, 0)):
        sc = [SC("go", 2, 2, cls=0, self_kind="inst", kind="method_def"),
              SC("run", 3, 7, cls=0, self_kind="inst", kind="method_def",
                 sites=[S(4, 4, "go", T.R_SELF, "member", 0), S(6, 11, "f", None, "bare", 0)]),
              SC("f", 5, 5, parent=1, cls=cls_on_arrow, self_kind="inst" if cls_on_arrow == 0 else "", kind="arrow",
                 sites=[S(5, 20, "go", T.R_SELF, "member", 0)])]
        f = T.FileFacts(rel, "typescript", classes=[CF("K", 1, 8, methods={"go": [MF(2)], "run": [MF(3)]})],
                        scopes=sc, decls=decls_of(text) | {(2, 2), (3, 2)})
        nodes = [nd("k::K", "Class", "K", rel, 1), nd("k::K.go", "Method", "K.go", rel, 2),
                 nd("k::K.run", "Method", "K.run", rel, 3)]
        out, st = _ts_link(T, {rel: text}, {rel: f}, nodes, [("k::K.run", "go")])
        rec.check(f"U15 `this.go()` in an arrow nested in a method {'typed (the arrow carries cls): REPLACE' if cls_on_arrow == 0 else 'with cls -1 is unknown: ADD'}",
                  st["removed"] == want_removed
                  and (nodes[2].get("call_typed") or {}).get("k::K.go") == "EXTRACTED",
                  (keyed(T, out), st["removed"], nodes[2].get("call_typed")))
    # U16 `// @ts-ignore` / `// @ts-nocheck` switch the compiler's check off: never `absent`
    ts_base = ("export class A {\n  go(): number { return 1; }\n}\n"
               "export function caller(a: A): number {\n  MARK\n  a.nope();\n  return a.go();\n}\n")
    for mark, want_absent in (("// fine", 1), ("// @ts-ignore", 0), ("// @ts-expect-error", 0)):
        for head in ("", "// @ts-nocheck\n"):
            text = (head + ts_base.replace("MARK", mark))
            off = 1 if head else 0
            f = T.FileFacts("web/a.ts", "typescript", classes=[CF("A", 1 + off, 3 + off, methods={"go": [MF(2 + off)]})],
                            functions={"caller": [T.FunctionFacts("caller", 4 + off)]},
                            scopes=[SC("go", 2 + off, 2 + off, cls=0, self_kind="inst", kind="method_def"),
                                    SC("caller", 4 + off, 8 + off, params={"a": B("param", T.t_name("A"))},
                                       kind="function_decl",
                                       sites=[S(6 + off, 2, "nope", T.r_ident("a"), "member", 0),
                                              S(7 + off, 9, "go", T.r_ident("a"), "member", 0)],
                                       all_names={"nope": 1, "go": 1})],
                            decls=decls_of(text) | {(2 + off, 2)})
            nodes = [nd("w::A", "Class", "A", "web/a.ts", 1 + off), nd("w::A.go", "Method", "A.go", "web/a.ts", 2 + off),
                     nd("w::caller", "Function", "caller", "web/a.ts", 4 + off)]
            out, st = _ts_link(T, {"web/a.ts": text}, {"web/a.ts": f}, nodes, [("w::caller", "nope")])
            want = want_absent if not head else 0
            rec.check(f"U16 TS `a.nope()` after `{mark}`{' in a // @ts-nocheck file' if head else ''}: "
                      f"{'absent (removed)' if want else 'never absent (kept)'}",
                      st["sites"]["absent"] == want and ((("w::caller", "nope") in out) == (not want)),
                      (keyed(T, out), st["sites"]))


# ================================================================================================
# V: dynamic dispatch (overrides stay reachable), PATCH_RULES loose / star, LanguageSpec.static_split, and the
#    shared sources (`link(..., sources=)`) — the tc2 core round (phase-1 verification problems 1-5)
# ================================================================================================

def _nodes_of(T, rel, facts, *, functions=()):
    """Class / Method nodes from the facts' classes (keys `<stem>::<Cls>[.<m>]@<line>`), plus Function nodes."""
    stem = rel.rsplit("/", 1)[-1].split(".")[0]
    out = []
    for c in facts.classes:
        out.append(nd(f"{stem}::{c.name}@{c.line}", "Class", c.name, rel, c.line))
        for m, mfs in c.methods.items():
            for mf in mfs:
                out.append(nd(f"{stem}::{c.name}.{m}@{mf.line}", "Method", f"{c.name}.{m}", rel, mf.line))
    for name, line in functions:
        out.append(nd(f"{stem}::{name}@{line}", "Function", name, rel, line))
    return out


def _ck(nodes, name):
    hits = [n["key"] for n in nodes if n["name"] == name and n["label"] != "Class"]
    return hits[0] if len(hits) == 1 else None


def _typed(nodes, caller):
    c = next(n for n in nodes if n["key"] == caller)
    return dict(c.get("call_typed") or {})


DISPATCH_PY = [
    "class Base:",                 # 1
    "    def m(self):",            # 2
    "        return 1",            # 3
    "",                            # 4
    "    def run(self):",          # 5
    "        return self.m()",     # 6
    "",                            # 7
    "    @classmethod",            # 8
    "    def make(cls):",          # 9
    "        return cls.build()",  # 10
    "",                            # 11
    "    @classmethod",            # 12
    "    def build(cls):",         # 13
    "        return 0",            # 14
    "",                            # 15
    "",                            # 16
    "class Sub(Base):",            # 17
    "    def m(self):",            # 18
    "        return 2",            # 19
    "",                            # 20
    "    @classmethod",            # 21
    "    def build(cls):",         # 22
    "        return 1",            # 23
    "",                            # 24
    "",                            # 25
    "class Leaf:",                 # 26
    "    def m(self):",            # 27
    "        return 3",            # 28
    "",                            # 29
    "    def build(self):",        # 30
    "        return 4",            # 31
    "",                            # 32
    "",                            # 33
    "def use(r: Base):",           # 34
    "    return r.m()",            # 35
    "",                            # 36
    "",                            # 37
    "def made():",                 # 38
    "    b = Base()",              # 39
    "    return b.m()",            # 40
    "",                            # 41
    "",                            # 42
    "def named():",                # 43
    "    return Base.build()",     # 44
]


def dispatch_world(T, *, mro="c3", calls=None, base_kind="class", sub_bases=None, extra=None, call_member=None):
    """(files, facts, nodes, calls, languages) of DISPATCH_PY (one Python file, pkg/d.py)."""
    S, B, CF, MF, SC = T.Site, T.Binding, T.ClassFacts, T.MethodFacts, T.Scope
    rel = "pkg/d.py"
    text = "\n".join(DISPATCH_PY) + "\n"
    cm = frozenset({"class"})
    classes = [CF("Base", 1, 14, kind=base_kind, methods={"m": [MF(2)], "run": [MF(5)],
                                                         "make": [MF(9, cm, lines=(8,))],
                                                         "build": [MF(13, cm, lines=(12,))]}),
               CF("Sub", 17, 23, bases=sub_bases or (T.t_name("Base"),),
                  methods={"m": [MF(18)], "build": [MF(22, cm, lines=(21,))]}),
               CF("Leaf", 26, 31, methods={"m": [MF(27)], "build": [MF(30)]})]
    selfp = {"self": B("param", T.t_self())}
    scopes = [SC("m", 2, 3, cls=0, self_kind="inst", params=selfp, kind="method"),
              SC("run", 5, 6, cls=0, self_kind="inst", params=selfp, kind="method",
                 sites=[S(6, 20, "m", T.R_SELF, "member", 0)], all_names={"m": 1}),
              SC("make", 9, 10, lines=(8,), cls=0, self_kind="class", kind="method",
                 sites=[S(10, 19, "build", T.R_SELF, "member", 0)], all_names={"build": 1}),
              SC("build", 13, 14, lines=(12,), cls=0, self_kind="class", kind="method"),
              SC("m", 18, 19, cls=1, self_kind="inst", params=selfp, kind="method"),
              SC("build", 22, 23, lines=(21,), cls=1, self_kind="class", kind="method"),
              SC("m", 27, 28, cls=2, self_kind="inst", params=selfp, kind="method"),
              SC("build", 30, 31, cls=2, self_kind="inst", params=selfp, kind="method"),
              SC("use", 34, 35, params={"r": B("param", T.t_name("Base"))}, kind="function",
                 sites=[S(35, 13, "m", T.r_ident("r"), "member", 0)], all_names={"m": 1}),
              SC("made", 38, 40, locals={"b": [B("call", value=T.r_call(None, "Base"), line=39)]}, kind="function",
                 sites=[S(39, 8, "Base", None, "bare", 0), S(40, 13, "m", T.r_ident("b"), "member", 0)],
                 all_names={"Base": 1, "m": 1}),
              SC("named", 43, 44, kind="function",
                 sites=[S(44, 16, "build", T.r_classref(T.t_name("Base")), "member", 0)], all_names={"build": 1})]
    facts = T.FileFacts(rel, "python", classes=classes,
                        functions={n: [T.FunctionFacts(n, ln)] for n, ln in (("use", 34), ("made", 38),
                                                                              ("named", 43))},
                        scopes=scopes, decls=decls_of(text))
    nodes = _nodes_of(T, rel, facts, functions=(("use", 34), ("made", 38), ("named", 43)))
    if call_member is not None:
        for n in nodes:
            if n["name"] == "use":
                n["call_member"] = list(call_member)
    k = {n["name"] + "@" + str(n["start_line"]): n["key"] for n in nodes}
    if calls is None:
        calls = [(k["Base.run@5"], "m"), (k["Base.make@9"], "build"), (k["use@34"], "m"),
                 (k["made@38"], "m"), (k["made@38"], "Base"), (k["named@43"], "build")]
    files = {rel: text}
    fx = {rel: facts}
    if extra:
        for r, (f, t) in extra.items():
            files[r], fx[r] = t, f
    langs = {"python": T.LANGUAGES["python"] if mro == "c3" else
             __import__("dataclasses").replace(T.LANGUAGES["python"], mro=mro)}
    return files, fx, nodes, calls, langs, k


DIAMOND_PY = [
    "class Root:",                     # 1
    "    def save(self):",             # 2
    "        return 0",                # 3
    "",                                # 4
    "",                                # 5
    "class Mixin(Root):",              # 6
    "    def save(self):",             # 7
    "        return super().save()",   # 8
    "",                                # 9
    "",                                # 10
    "class Other(Root):",              # 11
    "    def save(self):",             # 12
    "        return 2",                # 13
    "",                                # 14
    "",                                # 15
    "class Model(Mixin, Other):",      # 16
    "    pass",                        # 17
    "",                                # 18
    "",                                # 19
    "class Unrel:",                    # 20
    "    def save(self):",             # 21
    "        return 9",                # 22
]

FIELD_PY = [
    "class Session:",                      # 1
    "    def get(self):",                  # 2
    "        return 1",                    # 3
    "",                                    # 4
    "",                                    # 5
    "class OtherSession:",                 # 6
    "    def get(self):",                  # 7
    "        return 2",                    # 8
    "",                                    # 9
    "",                                    # 10
    "class Holder:",                       # 11
    "    def __init__(self):",             # 12
    "        self.session = Session()",    # 13
    "",                                    # 14
    "    def run(self):",                  # 15
    "        return self.session.get()",   # 16
    "",                                    # 17
    "",                                    # 18
    "class SubHolder(Holder):",            # 19
    "    def __init__(self):",             # 20
    "        self.session = OtherSession()",   # 21
    "",                                    # 22
    "",                                    # 23
    "def outside():",                      # 24
    "    h = Holder()",                    # 25
    "    return h.session.get()",          # 26
]

FIELD2_PY = [
    "class Session:",                      # 1
    "    def get(self):",                  # 2
    "        return 1",                    # 3
    "",                                    # 4
    "",                                    # 5
    "class FastSession(Session):",         # 6
    "    def get(self):",                  # 7
    "        return 2",                    # 8
    "",                                    # 9
    "",                                    # 10
    "class Holder:",                       # 11
    "    def __init__(self):",             # 12
    "        self.session = Session()",    # 13
    "",                                    # 14
    "    def run(self):",                  # 15
    "        return self.session.get()",   # 16
    "",                                    # 17
    "",                                    # 18
    "class SubHolder(Holder):",            # 19
    "    def __init__(self):",             # 20
    "        self.session = FastSession()",    # 21
    "",                                    # 22
    "",                                    # 23
    "class Unrel:",                        # 24
    "    def get(self):",                  # 25
    "        return 3",                    # 26
]

STATIC_TS = [
    "export class Base {",            # 1
    "  static make(): number {",      # 2
    "    return this.build();",       # 3
    "  }",                            # 4
    "",                               # 5
    "  static build(): number {",     # 6
    "    return 0;",                  # 7
    "  }",                            # 8
    "",                               # 9
    "  build(): number {",            # 10
    "    return 1;",                  # 11
    "  }",                            # 12
    "}",                              # 13
    "",                               # 14
    "export class Sub extends Base {",   # 15
    "  static build(): number {",     # 16
    "    return 2;",                  # 17
    "  }",                            # 18
    "",                               # 19
    "  build(): number {",            # 20
    "    return 3;",                  # 21
    "  }",                            # 22
    "}",                              # 23
]


def sec_dispatch(T, rec: Rec) -> None:
    S, B, CF, MF, SC = T.Site, T.Binding, T.ClassFacts, T.MethodFacts, T.Scope
    # V1 the declared / self / cls / constructed / named receivers of DISPATCH_PY, name fan-outs
    files, fx, nodes, calls, langs, k = dispatch_world(T)
    out, st = link(T, files, fx, nodes, calls, ON, languages=langs)
    kt = keyed(T, out)
    rec.check("V1 no error, every group judged (no dispatch veto)", st["error"] is None
              and st["vetoes"].get("dispatch", -1) == 0, (st["error"], st["vetoes"]))
    rec.check("V1 `self.m()` in Base.run: Base.m typed EXTRACTED; Sub's override (self may be a Sub) re-emitted "
              "from the removed fan-out at AMBIGUOUS; the unrelated Leaf.m gone",
              _typed(nodes, k["Base.run@5"]) == {k["Base.m@2"]: "EXTRACTED", k["Sub.m@18"]: "AMBIGUOUS"}
              and (k["Base.run@5"], "m", False) not in kt, (_typed(nodes, k["Base.run@5"]), kt))
    rec.check("V1 `cls.build()` in a classmethod: the class object may be a subclass's — Sub.build (a classmethod "
              "override) kept as AMBIGUOUS, the instance-only Leaf.build gone",
              _typed(nodes, k["Base.make@9"]) == {k["Base.build@13"]: "EXTRACTED", k["Sub.build@22"]: "AMBIGUOUS"},
              _typed(nodes, k["Base.make@9"]))
    rec.check("V1 `r: Base; r.m()` (a declared receiver): Base.m + Sub.m (AMBIGUOUS); Leaf.m gone",
              _typed(nodes, k["use@34"]) == {k["Base.m@2"]: "EXTRACTED", k["Sub.m@18"]: "AMBIGUOUS"},
              _typed(nodes, k["use@34"]))
    rec.check("V1 `b = Base(); b.m()` (constructed: exactly Base): Base.m only — the subclass's override is removed",
              _typed(nodes, k["made@38"]) == {k["Base@1"]: "EXTRACTED", k["Base.m@2"]: "EXTRACTED"},
              _typed(nodes, k["made@38"]))
    rec.check("V1 `Base.build()` (a class named in the code): Base.build only — Sub.build is removed",
              _typed(nodes, k["named@43"]) == {k["Base.build@13"]: "EXTRACTED"}, _typed(nodes, k["named@43"]))
    rec.check("V1 stats: 3 overrides re-emitted (override_keyed), none kept whole", st.get("override_keyed") == 3
              and st.get("override_kept") == 0 and st["grades"]["AMBIGUOUS"] == 3, (st.get("override_keyed"),
                                                                                    st.get("override_kept"), st["grades"]))
    # V2 an engine tuple that reaches ONLY an override (a qualified name / a key) is kept whole, at its own grade
    use, made = k["use@34"], k["made@38"]
    calls = [(use, "Sub.m"), (use, "m"), (made, T._call_key(k["Sub.m@18"])), (made, "Base")]
    files, fx, nodes, _c, langs, k = dispatch_world(T, calls=calls)
    out, st = link(T, files, fx, nodes, calls, ON, languages=langs)
    kt = keyed(T, out)
    rec.check("V2 `use`'s `Sub.m` tuple (reaches only the override) is kept whole — its own grade, no typed grade for "
              "Sub.m — and the `m` fan-out it shares the override with is removed without a duplicate key",
              (use, "Sub.m", False) in kt and (use, "m", False) not in kt
              and _typed(nodes, use) == {k["Base.m@2"]: "EXTRACTED"} and st.get("override_kept") == 1
              and st.get("override_keyed") == 0, (kt, _typed(nodes, use), st.get("override_kept")))
    rec.check("V2 a key to the override behind a CONSTRUCTED receiver is removed (exactly Base)",
              (made, k["Sub.m@18"], True) not in kt and k["Sub.m@18"] not in _typed(nodes, made), kt)
    # V3 call_member: a kept NAME keeps its G5 flag; a removed fan-out clears it
    for keep in (True, False):
        calls = [(use, "Sub.m"), (use, "m")] if keep else [(use, "m")]
        files, fx, nodes, _c, langs, k = dispatch_world(T, calls=calls, call_member=("m", "Sub.m"))
        link(T, files, fx, nodes, calls, ON, languages=langs)
        cm = next(n for n in nodes if n["key"] == use).get("call_member")
        rec.check(f"V3 call_member {'keeps' if keep else 'drops'} `m` when a name tuple of it is "
                  f"{'kept whole' if keep else 'all removed'}", ("m" in (cm or ())) == keep, cm)
    # V4 the reach cannot be known -> the group only ADDs (veto dispatch): an unresolved base ahead of the class in a
    #    subclass; an interface / protocol RECEIVER whose target is a real class's member (V4b). A target that is
    #    the interface's OWN member is never typed at all (the phase-2 verification): nothing removed, no key
    files, fx, nodes, calls, langs, k = dispatch_world(T, calls=None, sub_bases=(T.t_name("Unknown"), T.t_name("Base")))
    calls = [(k["use@34"], "m"), (k["Base.run@5"], "m"), (k["Base.make@9"], "build")]
    out, st = link(T, files, fx, nodes, calls, ON, languages=langs)
    rec.check("V4 a subclass with an unresolved base ahead of Base: nothing removed (veto dispatch on each of the 3 "
              "dispatching groups), the typed key still added", out[:3] == calls and st["removed"] == 0
              and st["vetoes"]["dispatch"] == 3 and k["Base.m@2"] in _typed(nodes, k["use@34"]),
              (keyed(T, out), st["vetoes"], st["removed"]))
    files, fx, nodes, calls, langs, k = dispatch_world(T, calls=None, base_kind="interface")
    calls = [(k["use@34"], "m"), (k["Base.run@5"], "m"), (k["Base.make@9"], "build")]
    out, st = link(T, files, fx, nodes, calls, ON, languages=langs)
    rec.check("V4 Base an interface / Protocol: `r.m()`, `self.m()`, `cls.build()` reach ITS OWN members (they never "
              "run) -> unknown: nothing removed, no typed key, each group falls back on its unknown site "
              "(skipped_interface 5: those three and made's `b.m()` / named's `Base.build()`, moot groups; veto "
              "dispatch 0)", out[:3] == calls and st["removed"] == 0
              and st["vetoes"]["unknown_site"] == 3 and st["vetoes"]["dispatch"] == 0
              and st.get("skipped_interface") == 5
              and not any(_typed(nodes, k[x]) for x in ("use@34", "Base.run@5", "Base.make@9")),
              (keyed(T, out), st["vetoes"], st.get("skipped_interface")))
    files, facts, nodes, calls = iface_world(T)
    out, st = link(T, files, facts, nodes, list(calls), ON)
    g = {n["key"]: n.get("call_typed") for n in nodes}
    rec.check("V4b `i: I; i.m()` where the interface I extends the real class C (flask's typing-only "
              "`AppContextProxy(ProxyMixin[AppContext], AppContext)`): C.m keyed, but the run-time value need not be "
              "an I subclass, so the reach is unknowable -> the engine's `m` tuple stays (veto dispatch 1)",
              g["i::use_i"] == {"i::C.m": "INFERRED"} and ("i::use_i", "m") in out and st["vetoes"]["dispatch"] == 1,
              (g["i::use_i"], keyed(T, out), st["vetoes"]))
    # V5 a wave-2 `depth` chain (Go embedding) has no overriding: the static binding is the whole reach
    files, fx, nodes, calls, langs, k = dispatch_world(T, mro="depth")
    calls = [(k["use@34"], "m")]
    link(T, files, fx, nodes, calls, ON, languages=langs)
    rec.check("V5 mro 'depth': `r.m()` ends on Base.m only (no override kept)",
              _typed(nodes, k["use@34"]) == {k["Base.m@2"]: "EXTRACTED"}, _typed(nodes, k["use@34"]))
    # V6 a same-family :Method m whose class the pass cannot see (its file has no facts) may override: kept
    ghost_rel = "pkg/ghost.py"
    gtext = "from pkg.d import Base\n\n\nclass Ghost(Base):\n    def m(self):\n        return 5\n"
    files, fx, nodes, calls, langs, k = dispatch_world(T, extra={ghost_rel: (None, gtext)})
    nodes.append(nd("ghost::Ghost.m@5", "Method", "Ghost.m", ghost_rel, 5))
    calls = [(k["use@34"], "m")]
    out, st = link(T, files, fx, nodes, calls, ON, languages=langs)
    rec.check("V6 an unreadable file's `Ghost.m` (it may override Base.m): re-emitted as AMBIGUOUS, counted "
              "(override_unseen); Leaf.m (its class IS seen) gone",
              _typed(nodes, k["use@34"]).get("ghost::Ghost.m@5") == "AMBIGUOUS" and st.get("override_unseen") == 1
              and k["Leaf.m@27"] not in _typed(nodes, k["use@34"]) and st["files_failed"] == 1,
              (_typed(nodes, k["use@34"]), st.get("override_unseen"), st["files_failed"]))
    # V7 cooperative super(): the next definer after the caller's class in every subclass's chain (c3 diamond)
    rel = "pkg/dia.py"
    text = "\n".join(DIAMOND_PY) + "\n"
    selfp = {"self": B("param", T.t_self())}
    f = T.FileFacts(rel, "python", classes=[
        CF("Root", 1, 3, methods={"save": [MF(2)]}), CF("Mixin", 6, 8, bases=(T.t_name("Root"),),
                                                         methods={"save": [MF(7)]}),
        CF("Other", 11, 13, bases=(T.t_name("Root"),), methods={"save": [MF(12)]}),
        CF("Model", 16, 17, bases=(T.t_name("Mixin"), T.t_name("Other"))), CF("Unrel", 20, 22,
                                                                               methods={"save": [MF(21)]})],
        scopes=[SC("save", 7, 8, cls=1, self_kind="inst", params=selfp, kind="method",
                   sites=[S(8, 23, "save", T.R_SUPER, "member", 0)], all_names={"save": 1})], decls=decls_of(text))
    dn = _nodes_of(T, rel, f)
    mixin_save = _ck(dn, "Mixin.save")
    out, st = link(T, {rel: text}, {rel: f}, dn, [(mixin_save, "save")], ON)
    want = {_ck(dn, "Root.save"): "EXTRACTED", _ck(dn, "Other.save"): "AMBIGUOUS"}
    rec.check("V7 `super().save()` in Mixin: Root.save (static) + Other.save, which Model(Mixin, Other)'s MRO puts "
              "next (AMBIGUOUS); Unrel.save gone", _typed(dn, mixin_save) == want, _typed(dn, mixin_save))
    # V8 a field a SUBCLASS rebinds: `self.session.get()` (self may be the subclass) is unknown; through a
    #    constructed holder it is exactly Holder's binding
    rel = "pkg/f.py"
    text = "\n".join(FIELD_PY) + "\n"
    f = T.FileFacts(rel, "python", classes=[
        CF("Session", 1, 3, methods={"get": [MF(2)]}), CF("OtherSession", 6, 8, methods={"get": [MF(7)]}),
        CF("Holder", 11, 16, fields={"session": [B("call", value=T.r_call(None, "Session"), line=13, scope=1)]},
           methods={"__init__": [MF(12)], "run": [MF(15)]}),
        CF("SubHolder", 19, 21, bases=(T.t_name("Holder"),),
           fields={"session": [B("call", value=T.r_call(None, "OtherSession"), line=21, scope=3)]},
           methods={"__init__": [MF(20)]})],
        functions={"outside": [T.FunctionFacts("outside", 24)]},
        scopes=[SC("get", 2, 3, cls=0, self_kind="inst", params=selfp, kind="method"),
                SC("__init__", 12, 13, cls=2, self_kind="inst", params=selfp, kind="method",
                   sites=[S(13, 23, "Session", None, "bare", 0)], all_names={"Session": 1}),
                SC("run", 15, 16, cls=2, self_kind="inst", params=selfp, kind="method",
                   sites=[S(16, 28, "get", T.r_field(T.R_SELF, "session"), "member", 0)], all_names={"get": 1}),
                SC("__init__", 20, 21, cls=3, self_kind="inst", params=selfp, kind="method",
                   sites=[S(21, 23, "OtherSession", None, "bare", 0)], all_names={"OtherSession": 1}),
                SC("outside", 24, 26, locals={"h": [B("call", value=T.r_call(None, "Holder"), line=25)]},
                   kind="function", sites=[S(25, 8, "Holder", None, "bare", 0),
                                           S(26, 21, "get", T.r_field(T.r_ident("h"), "session"), "member", 0)],
                   all_names={"Holder": 1, "get": 1})], decls=decls_of(text))
    fn = _nodes_of(T, rel, f, functions=(("outside", 24),))
    run_k, out_k = _ck(fn, "Holder.run"), _ck(fn, "outside")
    calls = [(run_k, "get"), (out_k, "get")]
    out, st = link(T, {rel: text}, {rel: f}, fn, calls, ON)
    kt = keyed(T, out)
    rec.check("V8 `self.session.get()` in Holder.run while SubHolder rebinds `self.session = OtherSession()`: unknown "
              "(the engine's `get` fan-out stays, no typed key)", (run_k, "get", False) in kt
              and not _typed(fn, run_k), (kt, _typed(fn, run_k)))
    rec.check("V8 `h = Holder(); h.session.get()` (exactly a Holder): Session.get, the fan-out removed",
              _typed(fn, out_k).get(_ck(fn, "Session.get")) == "EXTRACTED" and (out_k, "get", False) not in kt,
              (_typed(fn, out_k), kt))
    # V8b ... a subclass rebinding it to a SUBCLASS of the bound class: the field is that class (the subtree's
    #      root), its run-time class any of them — typed, with the override kept and the unrelated method gone
    rel = "pkg/f2.py"
    text = "\n".join(FIELD2_PY) + "\n"
    f = T.FileFacts(rel, "python", classes=[
        CF("Session", 1, 3, methods={"get": [MF(2)]}),
        CF("FastSession", 6, 8, bases=(T.t_name("Session"),), methods={"get": [MF(7)]}),
        CF("Holder", 11, 16, fields={"session": [B("call", value=T.r_call(None, "Session"), line=13, scope=2)]},
           methods={"__init__": [MF(12)], "run": [MF(15)]}),
        CF("SubHolder", 19, 21, bases=(T.t_name("Holder"),),
           fields={"session": [B("call", value=T.r_call(None, "FastSession"), line=21, scope=4)]},
           methods={"__init__": [MF(20)]}),
        CF("Unrel", 24, 26, methods={"get": [MF(25)]})],
        scopes=[SC("get", 2, 3, cls=0, self_kind="inst", params=selfp, kind="method"),
                SC("get", 7, 8, cls=1, self_kind="inst", params=selfp, kind="method"),
                SC("__init__", 12, 13, cls=2, self_kind="inst", params=selfp, kind="method",
                   sites=[S(13, 23, "Session", None, "bare", 0)], all_names={"Session": 1}),
                SC("run", 15, 16, cls=2, self_kind="inst", params=selfp, kind="method",
                   sites=[S(16, 28, "get", T.r_field(T.R_SELF, "session"), "member", 0)], all_names={"get": 1}),
                SC("__init__", 20, 21, cls=3, self_kind="inst", params=selfp, kind="method",
                   sites=[S(21, 23, "FastSession", None, "bare", 0)], all_names={"FastSession": 1})],
        decls=decls_of(text))
    fn2 = _nodes_of(T, rel, f)
    run2 = _ck(fn2, "Holder.run")
    out, st = link(T, {rel: text}, {rel: f}, fn2, [(run2, "get")], ON)
    rec.check("V8b `self.session` = Session() here, FastSession() (a Session subclass) in SubHolder: typed Session.get, "
              "FastSession's override kept (AMBIGUOUS), Unrel.get gone",
              _typed(fn2, run2) == {_ck(fn2, "Session.get"): "EXTRACTED", _ck(fn2, "FastSession.get"): "AMBIGUOUS"}
              and (run2, "get") not in out, (_typed(fn2, run2), keyed(T, out)))
    # V9 static_split (TS / JS): the class object `this` of a static method sees the static members only; a
    #    subclass's STATIC override stays reachable, the instance `build`s go
    rel = "src/st.ts"
    text = "\n".join(STATIC_TS) + "\n"
    stc = frozenset({"static"})
    f = T.FileFacts(rel, "typescript", classes=[
        CF("Base", 1, 13, methods={"make": [MF(2, stc)], "build": [MF(6, stc), MF(10)]}),
        CF("Sub", 15, 23, bases=(T.t_name("Base"),), methods={"build": [MF(16, stc), MF(20)]})],
        scopes=[SC("make", 2, 4, cls=0, self_kind="class", kind="method_def",
                   sites=[S(3, 16, "build", T.R_SELF, "member", 0)], all_names={"build": 1})],
        decls=decls_of(text))
    tn = _nodes_of(T, rel, f)
    mk = _ck(tn, "Base.make")
    out, st = link(T, {rel: text}, {}, tn, [(mk, "build")], {"DEEPMAP_TYPED_CALLS": "typescript"},
                   readers={"typescript": FReader(T, {rel: f})}, languages={"typescript": T.LANGUAGES["typescript"]})
    k_sb = {n["start_line"]: n["key"] for n in tn if n["name"] in ("Base.build", "Sub.build")}
    rec.check("V9 TS `this.build()` in a static method: the static Base.build (EXTRACTED) + Sub's static override "
              "(AMBIGUOUS); the instance builds of both classes removed",
              _typed(tn, mk) == {k_sb[6]: "EXTRACTED", k_sb[16]: "AMBIGUOUS"}, (_typed(tn, mk), st["error"]))
    # V10 PATCH_RULES loose, per language family: a TS `String.prototype.cap = ...` makes `s.cap()` (s: string)
    #     unknown; a PYTHON patch of `cap` leaves the TS call external (removed)
    trel, prel = "src/p.ts", "pkg/q.py"
    ttext = "export function cap(): string {\n  return '';\n}\n\nexport function f(s: string): string {\n  return s.cap();\n}\n"
    ptext = "str_cap = None\n"
    for who in ("ts", "py"):
        tf = T.FileFacts(trel, "typescript", functions={"cap": [T.FunctionFacts("cap", 1)],
                                                        "f": [T.FunctionFacts("f", 5)]},
                         scopes=[SC("f", 5, 7, params={"s": B("param", T.t_builtin("string"))}, kind="function_decl",
                                    sites=[S(6, 11, "cap", T.r_ident("s"), "member", 0)], all_names={"cap": 1})],
                         patches=[(T.t_name("String"), "cap")] if who == "ts" else [], decls=decls_of(ttext))
        pf = T.FileFacts(prel, "python", patches=[(T.t_name("str"), "cap")] if who == "py" else [])
        ln = [nd("p::cap", "Function", "cap", trel, 1), nd("p::f", "Function", "f", trel, 5),
              nd("q::q", "Module", "q", prel, 1)]
        out, st = link(T, {trel: ttext, prel: ptext}, {}, ln, [("p::f", "cap")],
                       {"DEEPMAP_TYPED_CALLS": "typescript,python"},
                       readers={"typescript": FReader(T, {trel: tf}), "python": FReader(T, {prel: pf})},
                       languages={"typescript": T.LANGUAGES["typescript"], "python": T.LANGUAGES["python"]})
        if who == "ts":
            rec.check("V10 loose: `String.prototype.cap = ...` in TS -> `s.cap()` (s: string) is unknown, the engine's "
                      "tuple to the project's cap stays", out[:1] == [("p::f", "cap")] and st["sites"]["external"] == 0,
                      (keyed(T, out), st["sites"]))
        else:
            rec.check("V10 loose is per language family: a Python patch of `cap` leaves the TS `s.cap()` external "
                      "(its tuple removed)", ("p::f", "cap") not in out and st["sites"]["external"] == 1,
                      (keyed(T, out), st["sites"]))
    # V10b a loose patch a TEST file writes (zod's prototype-pollution test: `Object.assign(Object.prototype, ...)`,
    #      a "*" onto a builtin) counts for test callers only — production `JSON.parse()` stays external
    prel, trel, xrel = "src/app.ts", "src/x.test.ts", "src/parse.ts"
    ptext = "export function prod(): unknown {\n  return JSON.parse('1');\n}\n"
    ttext = "export function t(): unknown {\n  return JSON.parse('2');\n}\n"
    xtext = "export function parse(): number {\n  return 1;\n}\n"
    for where in ("test", "production"):
        def jf(rel, name, patches=()):
            return T.FileFacts(rel, "typescript", functions={name: [T.FunctionFacts(name, 1)]},
                               scopes=[SC(name, 1, 3, kind="function_decl",
                                          sites=[S(2, 14, "parse", T.r_ident("JSON"), "member", 1)],
                                          all_names={"parse": 1})],
                               patches=list(patches), decls=decls_of(ptext))
        star = [(T.t_name("Object"), "*")]
        facts = {prel: jf(prel, "prod", star if where == "production" else ()),
                 trel: jf(trel, "t", star if where == "test" else ()),
                 xrel: T.FileFacts(xrel, "typescript", functions={"parse": [T.FunctionFacts("parse", 1)]},
                                   decls=decls_of(xtext))}
        ln = [nd("a::prod", "Function", "prod", prel, 1), nd("x::t", "Function", "t", trel, 1),
              nd("p::parse", "Function", "parse", xrel, 1)]
        calls = [("a::prod", "parse"), ("x::t", "parse")]
        out, st = link(T, {prel: ptext, trel: ttext, xrel: xtext}, {}, ln, calls, {"DEEPMAP_TYPED_CALLS": "typescript"},
                       readers={"typescript": FReader(T, facts)}, languages={"typescript": T.LANGUAGES["typescript"]},
                       is_test=lambda r: r.endswith(".test.ts"))
        if where == "test":
            rec.check("V10b a TEST file's loose `*` (Object.prototype) leaves production `JSON.parse()` external (its "
                      "engine tuple removed) and makes the test caller's unknown (kept)",
                      ("a::prod", "parse") not in out and ("x::t", "parse") in out, keyed(T, out))
        else:
            rec.check("V10b ... a PRODUCTION file's loose `*` makes every caller's `JSON.parse()` unknown (both kept)",
                      ("a::prod", "parse") in out and ("x::t", "parse") in out, keyed(T, out))
    # V11 PATCH_RULES star: a '*' patch (a mixin no file reads) opens every member of the class
    rel = "pkg/k.py"
    text = "class K:\n    def find(self):\n        return 1\n\n\ndef use(k: K):\n    return k.find()\n"
    for star in (True, False):
        f = T.FileFacts(rel, "python", classes=[CF("K", 1, 3, methods={"find": [MF(2)]})],
                        functions={"use": [T.FunctionFacts("use", 6)]},
                        scopes=[SC("use", 6, 7, params={"k": B("param", T.t_name("K"))}, kind="function",
                                   sites=[S(7, 13, "find", T.r_ident("k"), "member", 0)], all_names={"find": 1})],
                        patches=[(T.t_name("K"), "*")] if star else [], decls=decls_of(text))
        kn = _nodes_of(T, rel, f, functions=(("use", 6),))
        out, st = link(T, {rel: text}, {rel: f}, kn, [(_ck(kn, "use"), "find")], ON)
        rec.check(f"V11 star: {'a `*` patch of K -> `k.find()` unknown, its tuple stays' if star else 'unpatched K -> typed, REPLACE'}",
                  ((_ck(kn, "use"), "find") in out and not _typed(kn, _ck(kn, "use"))) if star else
                  ((_ck(kn, "use"), "find") not in out and _typed(kn, _ck(kn, "use"))), (keyed(T, out), st["sites"]))
    # V13 TypeScript too: a receiver DECLARED as an external / builtin type (`e: Error`) may hold a project subclass
    #     (`class AppError extends Error { toString() }`), so its override stays reachable — unknown, not external
    rel = "src/err.ts"
    for sub in (True, False):
        text = ("export class AppError extends Error {\n  toString(): string {\n    return 'app';\n  }\n}\n\n"
                if sub else "\n\n\n\n\n\n") + "export function f(e: Error): string {\n  return e.toString();\n}\n"
        classes = [CF("AppError", 1, 5, bases=(T.t_name("Error"),), methods={"toString": [MF(2)]})] if sub else []
        f = T.FileFacts(rel, "typescript", classes=classes, functions={"f": [T.FunctionFacts("f", 7)]},
                        scopes=[SC("f", 7, 9, params={"e": B("param", T.t_name("Error"))}, kind="function_decl",
                                   sites=[S(8, 11, "toString", T.r_ident("e"), "member", 0)],
                                   all_names={"toString": 1})], decls=decls_of(text))
        en = _nodes_of(T, rel, f, functions=(("f", 7),))
        fk = _ck(en, "f")
        out, st = link(T, {rel: text}, {}, en, [(fk, "toString")], {"DEEPMAP_TYPED_CALLS": "typescript"},
                       readers={"typescript": FReader(T, {rel: f})},
                       languages={"typescript": T.LANGUAGES["typescript"]})
        if sub:
            rec.check("V13 TS `e: Error; e.toString()` beside a project `AppError extends Error` overriding toString: "
                      "unknown — the engine's tuple (it may reach AppError.toString) stays",
                      (fk, "toString") in out and st["sites"]["external"] == 0, (keyed(T, out), st["sites"]))
        else:
            rec.check("V13 ... with no project subclass overriding it: external, the tuple removed",
                      (fk, "toString") not in out and st["sites"]["external"] == 1, (keyed(T, out), st["sites"]))
    # V12 the shared sources: `link(..., sources={rel: _Source})` reads every file through the extraction's own reader
    files, facts, nodes, calls = go_world(T)
    ref_nodes = copy.deepcopy(nodes)
    ref, _st = link(T, files, facts, ref_nodes, list(calls), ON)
    shared, fallback = Src(files), Src({})
    rel0 = next(iter(files))
    import inspect
    has_sources = "sources" in inspect.signature(T.link).parameters
    rec.check("V12 link() takes the keyword `sources` (the S2 contract)", has_sources)
    if not has_sources:
        return
    out, st = T.link(".", nodes, list(calls), languages={"python": T.LANGUAGES["python"]},
                     readers={"python": FReader(T, facts)}, sources={rel0: shared}, source=fallback,
                     is_test=lambda p: False, env=ON)
    rec.check("V12 sources={rel: reader}: every read goes through the shared reader (the fallback is never read), "
              "stats sources_shared counts it, and the result is the one a fresh read gives",
              shared.reads and not fallback.reads and st["sources_shared"] == 1 and keyed(T, out) == keyed(T, ref)
              and st["error"] is None, (shared.reads, fallback.reads, st["sources_shared"], st["error"]))

    class Boom(Src):
        def lines(self, rel):
            raise OSError("gone")

    for desc, srcs in (("a shared reader that raises", {rel0: Boom(files)}),
                       ("a value that is no reader", {rel0: object()}),
                       ("a file the map does not hold", {"other/x.py": Src(files)})):
        fb = Src(files)
        files2, facts2, nodes2, calls2 = go_world(T)
        out, st = T.link(".", nodes2, list(calls2), languages={"python": T.LANGUAGES["python"]},
                         readers={"python": FReader(T, facts2)}, sources=srcs, source=fb, is_test=lambda p: False,
                         env=ON)
        rec.check(f"V12 {desc}: the file is read through the fallback instead — same result, no error",
                  fb.reads and keyed(T, out) == keyed(T, ref) and st["error"] is None,
                  (fb.reads, st["error"]))
    files3, facts3, nodes3, calls3 = go_world(T)
    out, st = T.link(".", nodes3, list(calls3), languages={"python": T.LANGUAGES["python"]},
                     readers={"python": FReader(T, facts3)}, sources={rel0.replace("/", "\\"): Src(files3)},
                     source=Src({}), is_test=lambda p: False, env=ON)
    rec.check("V12 a sources key spelled with backslashes is the same file", st["sources_shared"] == 1
              and keyed(T, out) == keyed(T, ref), (st["sources_shared"], st["error"]))
    probe = Src(files3)
    inp = list(calls3)
    out, st = T.link(".", copy.deepcopy(nodes3), inp, sources={rel0: probe}, env={})
    rec.check("V12 flags off with sources: the SAME list, nothing read", out is inp and not probe.reads
              and st["sources_shared"] == 0, (out is inp, probe.reads))


# ================================================================================================
# W: the phase-2 review — graphify's symbol-resolution pass (a top-level def's nested bare calls of imported
#    names), the interface grade cap, moot groups (no veto scan), the GC pause, the validator's exact fast paths
# ================================================================================================

GLUE_PY = [
    "from pkg.exc import Abort",            # 1
    "from os.path import join",             # 2
    "",                                     # 3
    "def deco(f):",                         # 4
    "    return f",                         # 5
    "",                                     # 6
    "def outer():",                         # 7
    "    def inner():",                     # 8
    "        raise Abort()",                # 9
    "    return inner",                     # 10
    "",                                     # 11
    "@deco",                                # 12
    "def douter():",                        # 13
    "    def inner():",                     # 14
    "        raise Abort()",                # 15
    "    return inner",                     # 16
    "",                                     # 17
    "def dflt():",                          # 18
    "    def inner(x=Abort()):",            # 19
    "        return x",                     # 20
    "    return inner",                     # 21
    "",                                     # 22
    "class Local:",                         # 23
    "    pass",                             # 24
    "",                                     # 25
    "def same_file():",                     # 26
    "    def inner():",                     # 27
    "        return Local()",               # 28
    "    return inner",                     # 29
    "",                                     # 30
    "def ext_call():",                      # 31
    "    def inner():",                     # 32
    "        return join('a')",             # 33
    "    return inner",                     # 34
    "",                                     # 35
    "class K:",                             # 36
    "    def meth(self):",                  # 37
    "        def inner():",                 # 38
    "            raise Abort()",            # 39
    "        return inner",                 # 40
]


def glue_world(T):
    """(files, facts, nodes, calls, binder) of GLUE_PY (pkg/g.py) + pkg/exc.py (class Abort). Each caller holds the
    engine's name tuple of the bare call its NESTED def makes: graphify's symbol pass gives outer / dflt (undecorated
    top-level defs) those of an imported project class; never the decorated douter, the method K.meth, or a call of
    a same-file class / an external import."""
    rel, xrel = "pkg/g.py", "pkg/exc.py"
    text = "\n".join(GLUE_PY) + "\n"
    xtext = "class Abort(Exception):\n    pass\n"
    S, SC, B, CF, MF, FF = T.Site, T.Scope, T.Binding, T.ClassFacts, T.MethodFacts, T.FunctionFacts

    def bare(line, name):
        return S(line, bcol(text, line, name + "("), name, None, "bare", 0 if name != "join" else 1)

    scopes = [SC("deco", 4, 5, params={"f": B("param")}, kind="function"),                          # 0
              SC("outer", 7, 10, all_names={"Abort": 1}, kind="function"),                          # 1
              SC("inner", 8, 9, parent=1, sites=[bare(9, "Abort")], kind="function"),               # 2
              SC("douter", 13, 16, lines=(12,), all_names={"Abort": 1}, kind="function"),           # 3
              SC("inner", 14, 15, parent=3, sites=[bare(15, "Abort")], kind="function"),            # 4
              SC("dflt", 18, 21, sites=[bare(19, "Abort")], all_names={"Abort": 1}, kind="function"),   # 5
              SC("inner", 19, 20, parent=5, params={"x": B("param")}, kind="function"),             # 6
              SC("same_file", 26, 29, all_names={"Local": 1}, kind="function"),                     # 7
              SC("inner", 27, 28, parent=7, sites=[bare(28, "Local")], kind="function"),            # 8
              SC("ext_call", 31, 34, all_names={"join": 1}, kind="function"),                       # 9
              SC("inner", 32, 33, parent=9, sites=[bare(33, "join")], kind="function"),             # 10
              SC("meth", 37, 40, cls=1, self_kind="inst", params={"self": B("param", T.t_self())},
                 all_names={"Abort": 1}, kind="method"),                                           # 11
              SC("inner", 38, 39, parent=11, sites=[bare(39, "Abort")], kind="function")]           # 12
    f = T.FileFacts(rel, "python",
                    imports={"Abort": [T.Import("Abort", "named", "pkg.exc", "Abort", 1)],
                             "join": [T.Import("join", "named", "os.path", "join", 2)]},
                    classes=[CF("Local", 23, 24), CF("K", 36, 40, methods={"meth": [MF(37)]})],
                    functions={"deco": [FF("deco", 4)], "outer": [FF("outer", 7)],
                               "douter": [FF("douter", 13, lines=(12,))], "dflt": [FF("dflt", 18)],
                               "same_file": [FF("same_file", 26)], "ext_call": [FF("ext_call", 31)]},
                    scopes=scopes, decls=decls_of(text))
    xf = T.FileFacts(xrel, "python", classes=[CF("Abort", 1, 2, bases=(T.t_name("Exception"),))],
                     decls=decls_of(xtext))
    nodes = [nd("g::deco", "Function", "deco", rel, 4), nd("g::outer", "Function", "outer", rel, 7),
             nd("g::douter", "Function", "douter", rel, 13), nd("g::dflt", "Function", "dflt", rel, 18),
             nd("g::Local", "Class", "Local", rel, 23), nd("g::same_file", "Function", "same_file", rel, 26),
             nd("g::ext_call", "Function", "ext_call", rel, 31), nd("g::K", "Class", "K", rel, 36),
             nd("g::K.meth", "Method", "K.meth", rel, 37), nd("x::Abort", "Class", "Abort", xrel, 1)]
    calls = [("g::outer", "Abort"), ("g::douter", "Abort"), ("g::dflt", "Abort"), ("g::same_file", "Local"),
             ("g::ext_call", "join"), ("g::K.meth", "Abort")]
    binder = FBinder(T, imports={(rel, "Abort"): T.Resolution("symbol", rel=xrel, name="Abort"),
                                 (rel, "join"): T.Resolution("external", stems=("os",))})
    return {rel: text, xrel: xtext}, {rel: f, xrel: xf}, nodes, calls, binder


JS_GLUE = [
    "import { Foo } from './foo';",          # 1
    "",                                     # 2
    "function jouter() {",                  # 3
    "  const a = () => Foo();",             # 4
    "  return a;",                          # 5
    "}",                                    # 6
    "",                                     # 7
    "class K {",                            # 8
    "  m() {",                              # 9
    "    const f = () => Foo();",           # 10
    "    return f;",                        # 11
    "  }",                                  # 12
    "}",                                    # 13
]


def js_glue_world(T):
    rel, xrel = "web/n.js", "web/foo.js"
    text = "\n".join(JS_GLUE) + "\n"
    xtext = "export class Foo {}\n"
    S, SC, CF, MF = T.Site, T.Scope, T.ClassFacts, T.MethodFacts

    def bare(line):
        return S(line, bcol(text, line, "Foo("), "Foo", None, "bare", 0)

    scopes = [SC("jouter", 3, 6, all_names={"Foo": 1}, kind="function_decl"),
              SC("a", 4, 4, parent=0, sites=[bare(4)], kind="arrow"),
              SC("m", 9, 12, cls=0, self_kind="inst", all_names={"Foo": 1}, kind="method_def"),
              SC("f", 10, 10, parent=2, sites=[bare(10)], kind="arrow")]
    f = T.FileFacts(rel, "javascript", imports={"Foo": [T.Import("Foo", "named", "./foo", "Foo", 1)]},
                    classes=[CF("K", 8, 13, methods={"m": [MF(9)]})], functions={"jouter": [T.FunctionFacts("jouter", 3)]},
                    scopes=scopes, decls=decls_of(text) | {(9, 2)})
    xf = T.FileFacts(xrel, "javascript", classes=[CF("Foo", 1, 1)], decls=decls_of(xtext))
    nodes = [nd("n::jouter", "Function", "jouter", rel, 3), nd("n::K", "Class", "K", rel, 8),
             nd("n::K.m", "Method", "K.m", rel, 9), nd("f::Foo", "Class", "Foo", xrel, 1)]
    calls = [("n::jouter", "Foo"), ("n::K.m", "Foo")]
    binder = FBinder(T, imports={(rel, "Foo"): T.Resolution("symbol", rel=xrel, name="Foo")})
    return {rel: text, xrel: xtext}, {rel: f, xrel: xf}, nodes, calls, binder


IFACE_PY = [
    "class P:",                       # 1   (a typing.Protocol: kind "interface")
    "    def m(self):",               # 2
    "        return 1",               # 3
    "    def run(self):",             # 4
    "        return self.m()",        # 5
    "",                               # 6
    "class C:",                       # 7
    "    def m(self):",               # 8
    "        return 2",               # 9
    "",                               # 10
    "def use(p: P):",                 # 11
    "    return p.m()",               # 12
    "",                               # 13
    "def use_c(c: C):",               # 14
    "    return c.m()",               # 15
    "",                               # 16
    "class I(C):",                    # 17  (an interface over a REAL class: flask's typing-only proxies)
    "    pass",                       # 18
    "",                               # 19
    "def use_i(i: I):",               # 20
    "    return i.m()",               # 21
]


def iface_world(T):
    """(files, facts, nodes, calls) of IFACE_PY (pkg/i.py): P an interface (a typing.Protocol), C a class, I an
    interface whose base is C; every caller has the engine's `m` name tuple."""
    rel = "pkg/i.py"
    text = "\n".join(IFACE_PY) + "\n"
    S, SC, B, CF, MF = T.Site, T.Scope, T.Binding, T.ClassFacts, T.MethodFacts
    selfp = {"self": B("param", T.t_self())}
    f = T.FileFacts(rel, "python", classes=[CF("P", 1, 5, kind="interface", methods={"m": [MF(2)], "run": [MF(4)]}),
                                            CF("C", 7, 9, methods={"m": [MF(8)]}),
                                            CF("I", 17, 18, kind="interface", bases=(T.t_name("C"),))],
                    functions={"use": [T.FunctionFacts("use", 11)], "use_c": [T.FunctionFacts("use_c", 14)],
                               "use_i": [T.FunctionFacts("use_i", 20)]},
                    scopes=[SC("m", 2, 3, cls=0, self_kind="inst", params=selfp, kind="method"),
                            SC("run", 4, 5, cls=0, self_kind="inst", params=selfp, kind="method",
                               sites=[S(5, bcol(text, 5, "m()"), "m", T.R_SELF, "member", 0)], all_names={"m": 1}),
                            SC("m", 8, 9, cls=1, self_kind="inst", params=selfp, kind="method"),
                            SC("use", 11, 12, params={"p": B("param", T.t_name("P"))}, kind="function",
                               sites=[S(12, bcol(text, 12, "m()"), "m", T.r_ident("p"), "member", 0)],
                               all_names={"m": 1}),
                            SC("use_c", 14, 15, params={"c": B("param", T.t_name("C"))}, kind="function",
                               sites=[S(15, bcol(text, 15, "m()"), "m", T.r_ident("c"), "member", 0)],
                               all_names={"m": 1}),
                            SC("use_i", 20, 21, params={"i": B("param", T.t_name("I"))}, kind="function",
                               sites=[S(21, bcol(text, 21, "m()"), "m", T.r_ident("i"), "member", 0)],
                               all_names={"m": 1})],
                    decls=decls_of(text))
    nodes = [nd("i::P", "Class", "P", rel, 1), nd("i::P.m", "Method", "P.m", rel, 2),
             nd("i::P.run", "Method", "P.run", rel, 4), nd("i::C", "Class", "C", rel, 7),
             nd("i::C.m", "Method", "C.m", rel, 8), nd("i::use", "Function", "use", rel, 11),
             nd("i::use_c", "Function", "use_c", rel, 14), nd("i::I", "Class", "I", rel, 17),
             nd("i::use_i", "Function", "use_i", rel, 20)]
    calls = [("i::use", "m"), ("i::use_c", "m"), ("i::P.run", "m"), ("i::use_i", "m")]
    return {rel: text}, {rel: f}, nodes, calls


def sec_review2(T, rec: Rec) -> None:
    import gc
    # W1 graphify's symbol pass (Python): a detached bare call of an imported project class inside an undecorated
    #    top-level def is UNCERTAIN — the engine's tuple stays, nothing is keyed from it
    files, facts, nodes, calls, binder = glue_world(T)
    out, st = link(T, files, facts, nodes, list(calls), ON, binder=binder)
    kt = keyed(T, out)
    left = {(c, t) for c, t, k in kt if not k}

    def typed_of(key):
        return next(n for n in nodes if n["key"] == key).get("call_typed")
    rec.check("W1 graphify (the default): outer's nested `raise Abort()` and dflt's nested-def DEFAULT `Abort()` "
              "(an imported project class: the symbol pass gave both calls to the top-level def) keep the engine's "
              "tuple; nothing keyed from them; veto `nested` x2",
              ("g::outer", "Abort") in left and ("g::dflt", "Abort") in left and typed_of("g::outer") is None
              and typed_of("g::dflt") is None and st["vetoes"]["nested"] == 2, (kt, st["vetoes"]))
    rec.check("W1 ... never the decorated def (a decorated_definition: not walked), a same-file class, an external "
              "import or a method's nested def: their detached typed / external sites REPLACE as before",
              ("g::douter", "Abort") not in left and ("g::same_file", "Local") not in left
              and ("g::ext_call", "join") not in left and ("g::K.meth", "Abort") not in left
              and st["removed"] == 4 and not any(k for _c, _t, k in kt), (kt, st["removed"]))
    files, facts, nodes, calls, binder = glue_world(T)
    out, st = T.link(".", nodes, list(calls), languages={"python": T.LANGUAGES["python"]},
                     readers={"python": FReader(T, facts, binder)}, source=Src(files), is_test=lambda p: False,
                     env=ON, engine="ast")
    rec.check("W1 engine='ast' (every nested call is attributed): outer's / dflt's `Abort` group REPLACEs and keys "
              "Abort from the def (INFERRED: another file)",
              typed_of("g::outer") == {"x::Abort": "INFERRED"} and typed_of("g::dflt") == {"x::Abort": "INFERRED"}
              and st["vetoes"]["nested"] == 0, (typed_of("g::outer"), typed_of("g::dflt"), st["vetoes"]))
    for lang in ("python", "typescript", "javascript"):
        spec = T.LANGUAGES[lang]
        want = {"python": ({"function"}, True, True), "typescript": ({"function_decl", "arrow"}, False, False),
                "javascript": ({"function_decl", "arrow"}, False, False)}[lang]
        rec.check(f"W1 LanguageSpec[{lang}] symbol_walk / symbol_imports_only / symbol_plain = {want}",
                  (set(spec.symbol_walk), spec.symbol_imports_only, spec.symbol_plain) == want,
                  (spec.symbol_walk, spec.symbol_imports_only, spec.symbol_plain))
    # W2 JavaScript: a root `function` declaration's nested arrow `Foo()` keeps the tuple; a class method's does not
    files, facts, nodes, calls, binder = js_glue_world(T)
    out, st = T.link(".", nodes, list(calls), languages={"javascript": T.LANGUAGES["javascript"]},
                     readers={"javascript": FReader(T, facts, binder)}, source=Src(files), is_test=lambda p: False,
                     env={"DEEPMAP_TYPED_CALLS": "javascript"})
    left = {(c, t) for c, t, k in keyed(T, out) if not k}
    rec.check("W2 JS: `function jouter() { const a = () => Foo(); }` keeps the engine's `Foo` tuple (the symbol pass "
              "walks a root function declaration whole); `K.m`'s nested arrow `Foo()` is detached: REPLACEd",
              ("n::jouter", "Foo") in left and ("n::K.m", "Foo") not in left and st["removed"] == 1,
              (keyed(T, out), st["removed"], st["vetoes"]))
    # W3 an INTERFACE (a typing.Protocol / TS interface / typing-only class): its own member is never typed (it never
    #    runs; the phase-2 verification); a real class's member reached through an interface receiver is never
    #    EXTRACTED, even in-file
    files, facts, nodes, calls = iface_world(T)
    out, st = link(T, files, facts, nodes, list(calls), ON)
    g = {n["key"]: n.get("call_typed") for n in nodes}
    rec.check("W3 `p: P; p.m()` (P an interface) and P.run's `self.m()`: no key to P.m (a declaration that never "
              "runs) and the engine's tuples stay; `c: C; c.m()` stays EXTRACTED; `i: I; i.m()` (I an interface "
              "over C) keys C.m at INFERRED only — the run-time value need not be an I subclass",
              g["i::use"] is None and g["i::P.run"] is None and g["i::use_c"] == {"i::C.m": "EXTRACTED"}
              and g["i::use_i"] == {"i::C.m": "INFERRED"} and ("i::use", "m") in out and ("i::P.run", "m") in out
              and st.get("skipped_interface") == 2, (g, keyed(T, out), st.get("skipped_interface")))
    # W4 moot groups: nothing of m to replace -> not judged (no veto scan); a G5-only group is judged
    scans = {"n": 0}

    class CountingRx:
        def __init__(self, rx):
            self.rx = rx

        def finditer(self, *a):
            scans["n"] += 1
            return self.rx.finditer(*a)

        def findall(self, *a):
            scans["n"] += 1
            return self.rx.findall(*a)

    saved = dict(T._CALL_SHAPES)
    try:
        for k, rx in saved.items():
            T._CALL_SHAPES[k] = CountingRx(rx)
        files, facts, nodes, calls = go_world(T, b_ann="B", calls=[], call_member=None)
        out, st = link(T, files, facts, nodes, [], ON)
        moot_scans = scans["n"]
        c = caller_node(nodes)
        rec.check("W4 no tuple and no G5 flag of `go`: the group is MOOT (REPLACE and ADD emit the same) — no veto "
                  "scan; the typed keys still come", st["groups"] == {"replace": 0, "add": 0, "moot": 1}
                  and moot_scans == 0 and set(c.get("call_typed") or {}) == {"a::A.go", "a::B.go"},
                  (st["groups"], moot_scans, c.get("call_typed")))
        scans["n"] = 0
        files, facts, nodes, calls = go_world(T, b_ann="B", calls=[], call_member=("go",))
        out, st = link(T, files, facts, nodes, [], ON)
        rec.check("W4 a G5 flag of `go` with no tuple: judged (the veto scans; REPLACE clears call_member)",
                  st["groups"]["replace"] == 1 and scans["n"] >= 1 and "call_member" not in caller_node(nodes),
                  (st["groups"], scans["n"], caller_node(nodes).get("call_member")))
    finally:
        T._CALL_SHAPES.clear()
        T._CALL_SHAPES.update(saved)
    # W5 the cyclic GC is paused for the run and restored (enabled -> enabled, disabled -> disabled, on an error too)
    was = gc.isenabled()
    try:
        for start in (True, False):
            (gc.enable if start else gc.disable)()
            files, facts, nodes, calls = go_world(T, b_ann="B")
            link(T, files, facts, nodes, calls, ON)
            ok = gc.isenabled() == start
            files, facts, nodes, calls = go_world(T, b_ann="B")
            rel = next(iter(facts))
            facts[rel].scopes[2].sites.append(T.Site(13, 9, "go", None, ["unhashable"], 0))  # the validator raises
            out, st = link(T, files, facts, nodes, calls, ON)
            ok_err = gc.isenabled() == start and out is calls and bool(st["error"])
            rec.check(f"W5 link() leaves the GC {'enabled' if start else 'disabled'} as it found it (a run, and a run "
                      f"that fails inside: the input list back, stats error)", ok and ok_err, (ok, ok_err, st["error"]))
    finally:
        (gc.enable if was else gc.disable)()
    # W6 the validator's fast paths keep its exact semantics: an int / str SUBCLASS is accepted, a bool never
    import enum

    class Line(enum.IntEnum):
        ONE = 1

    class Name(str):
        pass

    base = T.FileFacts("v.py", "python", scopes=[T.Scope("f", 1, 2, sites=[T.Site(1, 0, "m", None, "bare", 0)])])
    ok_sub = T.FileFacts("v.py", "python", scopes=[T.Scope("f", Line.ONE, 2,
                                                           sites=[T.Site(Line.ONE, 0, Name("m"), None, "bare", 0)])])
    bad_bool = T.FileFacts("v.py", "python", scopes=[T.Scope("f", 1, 2, sites=[T.Site(True, 0, "m", None, "bare", 0)])])
    rec.check("W6 validate_facts: an IntEnum line and a str-subclass member are well-formed, a bool line never",
              T.validate_facts(base) == "" and T.validate_facts(ok_sub) == ""
              and T.validate_facts(bad_bool) == "scope #0 sites",
              (T.validate_facts(base), T.validate_facts(ok_sub), T.validate_facts(bad_bool)))


# ================================================================================================
# X: the phase-2 verification — a node the engine merged from two scopes is audited over their union
# ================================================================================================

MERGED_PY = [
    "class Headers:",                         # 1
    "    def get(self, k):",                  # 2
    "        return 1",                       # 3
    "",                                       # 4
    "class Client:",                          # 5
    "    def get(self, url):",                # 6
    "        return 2",                       # 7
    "",                                       # 8
    "class Builder:",                         # 9
    "    def __init__(self):",                # 10
    "        self.headers = Headers()",       # 11
    "",                                       # 12
    "    @property",                          # 13
    "    def ct(self):",                      # 14
    "        return self.headers.get('x')",   # 15
    "",                                       # 16
    "    @ct.setter",                         # 17
    "    def ct(self, v):",                   # 18
    "        self.headers.pop('x')",          # 19
    "",                                       # 20
    "    @property",                          # 21
    "    def cl(self):",                      # 22
    "        return self.headers.get('y')",   # 23
    "",                                       # 24
    "    @cl.setter",                         # 25
    "    def cl(self, v):",                   # 26
    "        self.get('y')",                  # 27
    "",                                       # 28
    "    @property",                          # 29
    "    def solo(self):",                    # 30
    "        return self.headers.get('z')",   # 31
]


def merged_world(T, *, typed_field=True):
    """(files, facts, nodes, calls) of MERGED_PY (pkg/g.py) — werkzeug's EnvironBuilder.content_type shape: a property
    getter / setter pair is ONE engine node (the reader lists each def's lines in the other's Scope.lines), and the
    engine (DEEPMAP_PRECISE_CALLS) keyed `self.headers.get()` to the in-file Client.get. `cl`'s setter calls
    `self.get()` (a SELF site); `solo` is a getter alone (one scope, one node)."""
    rel = "pkg/g.py"
    text = "\n".join(MERGED_PY) + "\n"
    S, SC, B, CF, MF = T.Site, T.Scope, T.Binding, T.ClassFacts, T.MethodFacts
    selfp = {"self": B("param", T.t_self())}
    hdr = T.r_field(T.R_SELF, "headers")
    prop = frozenset({"property"})
    fields = {"headers": [B("call", value=T.r_call(None, "Headers"), line=11, scope=2)]} if typed_field else {}
    f = T.FileFacts(rel, "python",
                    classes=[CF("Headers", 1, 3, methods={"get": [MF(2)]}), CF("Client", 5, 7, methods={"get": [MF(6)]}),
                             CF("Builder", 9, 31, fields=fields,
                                methods={"__init__": [MF(10)], "ct": [MF(14, prop, lines=(13,)), MF(18, lines=(17,))],
                                         "cl": [MF(22, prop, lines=(21,)), MF(26, lines=(25,))],
                                         "solo": [MF(30, prop, lines=(29,))]})],
                    scopes=[SC("get", 2, 3, cls=0, self_kind="inst", params=selfp, kind="method"),
                            SC("get", 6, 7, cls=1, self_kind="inst", params=selfp, kind="method"),
                            SC("__init__", 10, 11, cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(11, bcol(text, 11, "Headers"), "Headers", None, "bare", 0)],
                               all_names={"Headers": 1}),
                            SC("ct", 14, 15, lines=(13, 17, 18), cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(15, bcol(text, 15, "get"), "get", hdr, "member", 1)], all_names={"get": 1}),
                            SC("ct", 18, 19, lines=(13, 14, 17), cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(19, bcol(text, 19, "pop"), "pop", hdr, "member", 1)], all_names={"pop": 1}),
                            SC("cl", 22, 23, lines=(21, 25, 26), cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(23, bcol(text, 23, "get"), "get", hdr, "member", 1)], all_names={"get": 1}),
                            SC("cl", 26, 27, lines=(21, 22, 25), cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(27, bcol(text, 27, "get"), "get", T.R_SELF, "member", 1)],
                               all_names={"get": 1}),
                            SC("solo", 30, 31, lines=(29,), cls=2, self_kind="inst", params=selfp, kind="method",
                               sites=[S(31, bcol(text, 31, "get"), "get", hdr, "member", 1)], all_names={"get": 1})],
                    decls=decls_of(text))
    nodes = [nd("g::Headers", "Class", "Headers", rel, 1), nd("g::Headers.get", "Method", "Headers.get", rel, 2),
             nd("g::Client", "Class", "Client", rel, 5), nd("g::Client.get", "Method", "Client.get", rel, 6),
             nd("g::Builder", "Class", "Builder", rel, 9), nd("g::Builder.__init__", "Method", "Builder.__init__", rel, 10),
             nd("g::Builder.ct", "Method", "Builder.ct", rel, 14), nd("g::Builder.cl", "Method", "Builder.cl", rel, 22),
             nd("g::Builder.solo", "Method", "Builder.solo", rel, 30)]
    ck = T._call_key("g::Client.get")
    calls = [("g::Builder.ct", ck), ("g::Builder.cl", ck), ("g::Builder.solo", ck)]
    return {rel: text}, {rel: f}, nodes, calls


def sec_verify2(T, rec: Rec) -> None:
    AUD = {"DEEPMAP_CALLS_AUDIT": "python"}
    BOTH = {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "python"}

    def node(nodes, key):
        return next(n for n in nodes if n["key"] == key)
    for typed_field in (True, False):
        tag = "a typed field (`self.headers = Headers()`)" if typed_field else "an untyped field"
        # X1 audit-only: the merged getter / setter node is judged over the union of its two scopes' sites
        files, facts, nodes, calls = merged_world(T, typed_field=typed_field)
        out, st = link(T, files, facts, nodes, calls, AUD)
        rec.check(f"X1 audit-only, {tag}: the merged `ct` getter / setter node (`self.headers.get()` in the getter, no "
                  f"`get` in the setter) is demoted — call_weak [Client.get] — like the single getter `solo`; "
                  f"nothing emitted or removed (the SAME list); audit_shared 2",
                  node(nodes, "g::Builder.ct").get("call_weak") == ["g::Client.get"]
                  and node(nodes, "g::Builder.solo").get("call_weak") == ["g::Client.get"]
                  and out is calls and st.get("audit_shared") == 2 and st["error"] is None,
                  ({k: node(nodes, k).get("call_weak") for k in ("g::Builder.ct", "g::Builder.cl", "g::Builder.solo")},
                   st.get("audit_shared"), st["error"]))
        rec.check(f"X1 audit-only, {tag}: `cl`'s SETTER calls `self.get()` (a SELF site): over the union the node "
                  f"is never demoted",
                  "call_weak" not in node(nodes, "g::Builder.cl"), node(nodes, "g::Builder.cl").get("call_weak"))
        # X2 TYPED + audit: the merged nodes are still never TYPED callers (no key, no removal of their tuples)
        files, facts, nodes, calls = merged_world(T, typed_field=typed_field)
        out, st = link(T, files, facts, nodes, list(calls), BOTH)
        kt = {(c, str(t)) for c, t in out}
        rec.check(f"X2 TYPED + audit, {tag}: the merged nodes are no TYPED callers (no call_typed; their engine "
                  f"tuples all stay), the audit still demotes `ct` and spares `cl`",
                  all("call_typed" not in node(nodes, k) for k in ("g::Builder.ct", "g::Builder.cl"))
                  and ("g::Builder.ct", "g::Client.get") in kt and ("g::Builder.cl", "g::Client.get") in kt
                  and node(nodes, "g::Builder.ct").get("call_weak") == ["g::Client.get"]
                  and "call_weak" not in node(nodes, "g::Builder.cl") and st.get("audit_shared") == 2,
                  ({k: (node(nodes, k).get("call_typed"), node(nodes, k).get("call_weak"))
                    for k in ("g::Builder.ct", "g::Builder.cl", "g::Builder.solo")}, sorted(kt)))
        if typed_field:
            rec.check("X2 ... while the single getter `solo` IS a TYPED caller: `self.headers.get()` typed to "
                      "Headers.get REPLACEs the engine's false Client.get key",
                      _typed(nodes, "g::Builder.solo") == {"g::Headers.get": "EXTRACTED"}
                      and ("g::Builder.solo", "g::Client.get") not in kt,
                      (_typed(nodes, "g::Builder.solo"), sorted(kt)))
    # X3 flags off: nothing read, no attribute (the merged-node audit is part of the audit flag only)
    files, facts, nodes, calls = merged_world(T)
    out, st = link(T, files, facts, nodes, calls, {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0"})
    rec.check("X3 TYPED and audit off: the SAME list, no call_weak / call_typed, audit_shared 0",
              out is calls and not any(n.get("call_weak") or n.get("call_typed") for n in nodes)
              and st.get("audit_shared") == 0, (st.get("audit_shared"), [n for n in nodes if n.get("call_weak")]))
    # X5 an interface whose member has no node of its own claims a same-named REAL class's method node by name (a TS
    #    interface merged with its class; a `TYPE_CHECKING` stub beside the runtime class): that node is a real
    #    method — still typed, and EXTRACTED in-file — whichever class key sorts first
    rel = "pkg/x.py"
    text = "\n".join(["class P:", "    def m(self): ...", "", "class P:", "    def m(self):", "        return 1",
                       "    def run(self):", "        return self.m()"]) + "\n"
    S, SC, B, CF, MF = T.Site, T.Scope, T.Binding, T.ClassFacts, T.MethodFacts
    selfp = {"self": B("param", T.t_self())}
    fx = T.FileFacts(rel, "python", classes=[CF("P", 1, 2, kind="interface", methods={"m": [MF(2)]}),
                                             CF("P", 4, 8, methods={"m": [MF(5)], "run": [MF(7)]})],
                     scopes=[SC("m", 2, 2, cls=0, self_kind="inst", params=selfp, kind="method"),
                             SC("m", 5, 6, cls=1, self_kind="inst", params=selfp, kind="method"),
                             SC("run", 7, 8, cls=1, self_kind="inst", params=selfp, kind="method",
                                sites=[S(8, bcol(text, 8, "m()"), "m", T.R_SELF, "member", 0)], all_names={"m": 1})],
                     decls=decls_of(text))
    nodes = [nd("x::P@1", "Class", "P", rel, 1), nd("x::P@4", "Class", "P", rel, 4),
             nd("x::P.m@5", "Method", "P.m", rel, 5), nd("x::P.run@7", "Method", "P.run", rel, 7)]
    calls = [("x::P.run@7", "m")]
    out, st = link(T, {rel: text}, {rel: fx}, nodes, list(calls), ON)
    rec.check("X5 the interface P (no node for its `m`; its key sorts first) claims the real P.m node by name: the "
              "real class's `self.m()` is still keyed to it, EXTRACTED (not an interface member: a real class "
              "claims it too), and its name tuple REPLACEd",
              _typed(nodes, "x::P.run@7") == {"x::P.m@5": "EXTRACTED"} and st.get("skipped_interface") == 0
              and ("x::P.run@7", "m") not in [(c, str(t)) for c, t in out if not T._is_call_key(t)],
              (_typed(nodes, "x::P.run@7"), st.get("skipped_interface"), keyed(T, out)))
    # X4 TYPED only (audit off): the merged node is untouched (no weak, no typed)
    files, facts, nodes, calls = merged_world(T)
    out, st = link(T, files, facts, nodes, list(calls), {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "0"})
    rec.check("X4 TYPED only: the merged `ct` node gets neither call_weak nor call_typed and keeps its tuple",
              not node(nodes, "g::Builder.ct").get("call_weak") and "call_typed" not in node(nodes, "g::Builder.ct")
              and ("g::Builder.ct", "g::Client.get") in {(c, str(t)) for c, t in out} and st.get("audit_shared") == 0,
              (node(nodes, "g::Builder.ct"), st.get("audit_shared")))


# ================================================================================================
# Q: mutation teeth (each rule broken in a copy of the module -> its section goes RED)
# ================================================================================================

MUTATIONS = [
    ("textual veto off", "            elif self.text_count(rel, sc, m) != len(group):",
     "            elif False:", "sec_truth"),
    ("census off", "            elif sc.all_names is not None and sc.all_names.get(m, 0) != len(group):",
     "            elif False:", "sec_truth"),
    ("span cap off", "            elif span > SPAN_MAX_LINES or span < 1:", "            elif False:",
     "sec_truth"),
    ("candidate sets create fan-outs", "                if t not in singular and (not had or t not in reach):",
     "                if False:", "sec_candidates"),
    ("candidate sets widen a pick", "                if t not in singular and (not had or t not in reach):",
     "                if t not in singular and not had:", "sec_candidates"),
    ("caller policy off", "                if not had and not additions_ok:", "                if False:", "sec_policy"),
    ("EXTRACTED across files", "same = not vd.weak and self.file_of.get(t) == rel and vd.ev <= {rel}",
     "same = not vd.weak", "sec_grades"),
    ("injected fields graded EXTRACTED", "                        fv = fv.but(weak=True)",
     "                        fv = fv", "sec_grades"),
    ("audit ignores SELF sites", '            if site.recv.kind in ("SELF", "SUPER"):\n                return False',
     '            if False:\n                return False', "sec_audit"),
    ("audit ignores typed-to-x", "            if vd.kind == \"typed\" and t in vd.targets:\n                return False",
     "            if False:\n                return False", "sec_audit"),
    ("flag-off returns a copy", "        if not cfg.typed and not cfg.audit:\n            return calls, stats",
     "        if not cfg.typed and not cfg.audit:\n            return list(calls), stats", "sec_flag_off"),
    ("family check off (a cross-family typed verdict REPLACEs)",
     "                if vd.kind == \"typed\" and fam_c is not None and any(",
     "                if False and vd.kind == \"typed\" and fam_c is not None and any(", "sec_family"),
    ("shape filter off", "                if vd.kind != \"unknown\" and vd.shape and vd.shape not in self.cfg.shapes:",
     "                if False:", "sec_shapes"),
    ("external test off", "            if not stems or any(s in self.stems for s in stems):",
     "            if not stems:", "sec_grades"),
    ("proxy rule off", '_EXT_MEMBER_OK = frozenset({"module", "module-attr", "type", "builtin", "literal"})',
     '_EXT_MEMBER_OK = frozenset({"module", "module-attr", "type", "builtin", "literal", "symbol"})', "sec_grades"),
    ("absent everywhere", "        if all(lk.kind == \"absent\" for lk in looks) and spec is not None and spec.absent_ok:",
     "        if all(lk.kind == \"absent\" for lk in looks):", "sec_truth"),
    ("no rollback", "    except Exception:\n        for node, attr, old in reversed(undo):",
     "    except Exception:\n        for node, attr, old in []:", "sec_never_raises"),
    ("the veto span starts at the def line", "    return min((sc.line,) + tuple(x for x in sc.lines if isinstance(x, int) and 0 < x <= sc.line))",
     "    return sc.line", "sec_truth"),
    ("fields need agreeing bindings", "if not vals or not _agree(vals):", "if not vals:", "sec_mustnot"),
    ("TS unions bind", "if len(vals) > 1 and spec is not None and not spec.unions:", "if False:", "sec_mustnot"),
    ("TS locals join", "v = _join(vals) if (spec is None or spec.unions or _agree(vals)) else _UNK",
     "v = _join(vals)", "sec_mustnot"),
    ("a name bound two ways resolves", "        if tables > 1:", "        if False:", "sec_mustnot"),
    ("disagreeing imports resolve", "or any(not v.same(vals[0]) for v in vals):", "or False:", "sec_mustnot"),
    ("opaque types are external", "opaque and spec is not None and nm in spec.opaque):", "False):", "sec_mustnot"),
    ("a property call binds", 'return _Look("unknown", why="property")', "pass", "sec_mustnot"),
    ("a field call binds", 'return _Look("unknown", why="field")', "pass", "sec_mustnot"),
    ("class / module aliases bind (both guards)",
     ['return v if v.kind == "inst" or (v.kind == "ext" and v.ref in _EXT_VALUE_OK) else _UNK',
      '            if v.kind in ("inst", "ext"):\n                v = v.but(src="param" if'],
     ["return v", '            if True:\n                v = v.but(src="param" if'], "sec_mustnot"),
    ("dunders bind", "if spec is not None and spec.dunders and len(m) > 4",
     "if False and spec is not None and spec.dunders and len(m) > 4", "sec_mustnot"),
    ("dynamic classes bind", "if ci is None or ci.ambiguous or ci.facts.dynamic:", "if ci is None or ci.ambiguous:",
     "sec_mustnot"),
    ("patched attributes bind", "if m in ci.patched:", "if False:", "sec_mustnot"),
    ("type names ignore shadowing", "if nm in extra_shadow or nm in self.shadow(rel, si):", "if False:",
     "sec_mustnot"),
    ("local classes ignored", "if x in self.local_cls.get(rel, {}).get(s, ()):", "if False:",
     "sec_mustnot"),
    ("star imports ignored", "        if self.has_star(ff):", "        if False:", "sec_mustnot"),
    ("poisoned names bind", "            if x in sc.poisoned:", "            if False:", "sec_mustnot"),
    ("python strings counted", "        parts.append(_blanked(text, k, e))", "        parts.append(text[k:e])",
     "sec_lexers"),
    # finding 1 — attribution
    ("nested sites emitted from the enclosing caller", "                if att not in _ATTRIBUTED:",
     "                if False:", "sec_attribution"),
    ("uncertain scopes never veto", "            elif any(a == \"uncertain\" for _vd, a in verdicts):",
     "            elif False:", "sec_attribution"),
    ("a nested def's default argument attributed",
     "        if self.engine == \"ast\" or spec is None or not spec.header_detached:\n            return frozenset()",
     "        if True:\n            return frozenset()", "sec_attribution"),
    ("the ast engine read as graphify", "        if self.engine == \"ast\":\n            return \"attributed\"",
     "        if False:\n            return \"attributed\"", "sec_attribution"),
    ("an unknown scope kind attributed",
     "        if parent == \"uncertain\" or spec is None or kind not in spec.nested_attributed:\n"
     "            return \"uncertain\"",
     "        if parent == \"uncertain\" or spec is None or kind not in spec.nested_attributed:\n"
     "            return \"attributed\"", "sec_attribution"),
    ("an untyped detached site is an audit witness", "            if att in _ATTRIBUTED:\n                untyped = True",
     "            if True:\n                untyped = True", "sec_attribution"),
    ("a detached scope kind in the attributed table", "_PY_ATTRIBUTED = frozenset({\"lambda\", \"comprehension\"})",
     "_PY_ATTRIBUTED = frozenset({\"lambda\", \"comprehension\", \"method\"})", "sec_real_engine"),
    # findings 2-4 — the textual veto
    ("minified spans judged", "            if not (k < len(self.long) and self.long[k] <= end):",
     "            if True:", "sec_lexers"),
    ("f-string fields masked", "        if \"f\" in pre or \"F\" in pre or \"t\" in pre or \"T\" in pre:",
     "        if False:", "sec_lexers"),
    ("template substitutions masked", "        j = _js_code(text, out, j + 2, nest)\n",
     "        j = text.find(\"}\", j + 2)\n        j = n if j < 0 else j\n        out.blank(j)\n", "sec_lexers"),
    ("m<T>( / m?.( / tagged templates not call-shaped", "(?:!\\s*)?(?:\\?\\.\\s*)?[(`]\"),", "\\(\"),",
     "sec_lexers"),
    ("one scan per (caller, name)", "        got = self.memo.get(key, _MISSING)", "        got = _MISSING",
     "sec_budget"),
    # findings 6-9
    ("a depth-cut value memoised (eval_binding)",
     "        if depth > _EVAL_DEPTH:\n            self.cut += 1\n            return _UNK\n        k = b.kind",
     "        if depth > _EVAL_DEPTH:\n            return _UNK\n        k = b.kind", "sec_review"),
    ("a declared external type ignores project subclasses",
     "            if v.decl and v.ref in (\"type\", \"builtin\")",
     "            if False and v.decl and v.ref in (\"type\", \"builtin\")", "sec_review"),
    ("@ts-ignore / @ts-nocheck ignored", "            if self.unchecked(rel, line, spec):", "            if False:",
     "sec_review"),
    ("partial stats kept after an error",
     "        stats = _stats_skeleton(cfg, eng)         # no partial counts: the run changed nothing",
     "        pass", "sec_never_raises"),
    # finding 5 — the review's surviving mutants (its M7 / M10 are equivalent mutants)
    ("M1 two scopes on one node pool their sites",
     "                if caller_of[si] is not None and dup[caller_of[si]] > 1:", "                if False:",
     "sec_review"),
    ("M2 an intermediate MRO class left out of the evidence",
     "                ev.add(ci.rel)\n                if m in ci.patched:",
     "                pass\n                if m in ci.patched:", "sec_review"),
    ("M3 facts for ANOTHER file accepted",
     "                if f is None or validate_facts(f) or _norm_rel(f.rel) != rel:",
     "                if f is None or validate_facts(f):", "sec_review"),
    ("M4 the audit's subclass exemption off",
     "(vd.recv_cls == owner\n                                                      or owner in "
     "self.mro_class_keys(vd.recv_cls)):", "(vd.recv_cls == owner):", "sec_review"),
    ("M5 two ClassFacts on one node not ambiguous",
     "                    self.classes[key].ambiguous = True       # two definitions, one node: never resolved",
     "                    pass", "sec_review"),
    ("M6 node_at picks one of several nodes", "        return next(iter(hits)) if len(hits) == 1 else None",
     "        return sorted(hits)[0] if hits else None", "sec_review"),
    ("M8 a bare builtin function not external", "_EXT_BARE_OK = frozenset({\"symbol\", \"builtin\", \"builtin_func\"})",
     "_EXT_BARE_OK = frozenset({\"symbol\", \"builtin\"})", "sec_review"),
    ("M9 implicit this ignores local bindings",
     "                    and v.kind == \"unk\" and self._unbound_everywhere(rel, si, m)):",
     "                    and v.kind == \"unk\"):", "sec_review"),
    ("M13 arity filter off", "            if argc >= 0:\n                fit = ",
     "            if False:\n                fit = ", "sec_review"),
    ("M14 the audit census counts every label",
     "                    if self.label_of[k] == \"Method\":\n                        count[",
     "                    if True:\n                        count[", "sec_review"),
    # the tc2 core round: dynamic dispatch, PATCH_RULES, static_split, shared sources
    ("overrides never kept", "            hit = (reach & over) | dark\n", "            hit = set()\n", "sec_dispatch"),
    ("a re-emitted override graded EXTRACTED",
     '                typed_attr.setdefault(ck, {})[t] = "AMBIGUOUS"\n',
     '                typed_attr.setdefault(ck, {})[t] = "EXTRACTED"\n', "sec_dispatch"),
    ("a kept tuple's override re-emitted too", "        return keep, rekey - kept_reach\n",
     "        return keep, rekey\n", "sec_dispatch"),
    ("a declared receiver is exact",
     '            return self._target_verdict(looks, v, rel, shape, r.kind, line=site.line,\n'
     '                                        dispatch="sub" if v.decl else "")',
     '            return self._target_verdict(looks, v, rel, shape, r.kind, line=site.line,\n'
     '                                        dispatch="")', "sec_dispatch"),
    ("a constructed receiver dispatches",
     '            return self._target_verdict(looks, v, rel, shape, r.kind, line=site.line,\n'
     '                                        dispatch="sub" if v.decl else "")',
     '            return self._target_verdict(looks, v, rel, shape, r.kind, line=site.line,\n'
     '                                        dispatch="sub")', "sec_dispatch"),
    ("`self` is exact", 'src="self", decl=True)', 'src="self")', "sec_dispatch"),
    ("`super()` reaches no cooperative next definer",
     "                        out.update(self.method_nodes(x, m, mfs)[0])\n                        break\n",
     "                        break\n", "sec_dispatch"),
    ("an interface receiver's reach read as known",
     '        if ci.facts.kind == "interface":\n            return None\n',
     '        if False:\n            return None\n', "sec_dispatch"),
    ("an unresolved subclass base ahead of the class ignored",
     '            for e in entries[:at]:\n                if e[0] != "class":\n                    if e[0] == "unres":\n'
     '                        return None\n',
     '            for e in entries[:at]:\n                if e[0] != "class":\n                    if False:\n'
     '                        return None\n', "sec_dispatch"),
    ("depth chains override", '        if ci.spec is None or ci.spec.mro not in ("c3", "linear"):\n            return set()\n',
     '        if ci.spec is None:\n            return set()\n', "sec_dispatch"),
    ("unseen members never kept",
     "            dark = {t for t in reach - over - static if self.unseen_member(t, m, fam_c, lang_family)}\n",
     "            dark = set()\n", "sec_dispatch"),
    ("a kept name drops its G5 flag",
     "isinstance(names, (list, tuple, set, frozenset)) and m in names and not any(",
     "isinstance(names, (list, tuple, set, frozenset)) and m in names and True or any(", "sec_dispatch"),
    ("a field bound within one class's subtree is never typed", "            bound = self.subtree_bound(vals)\n",
     "            bound = None\n", "sec_dispatch"),
    ("any disagreeing field bound to the first class", "        if len(roots) != 1:\n            return None\n",
     "        if not keys:\n            return None\n        roots = sorted(keys)\n", "sec_dispatch"),
    ("a checked (TS) declared external type ignores project subclasses",
     '            if v.decl and v.ref in ("type", "builtin") and spec is not None and self.open_member(rel, m):\n',
     '            if v.decl and v.ref in ("type", "builtin") and spec is not None and not spec.absent_ok '
     'and self.open_member(rel, m):\n', "sec_dispatch"),
    ("a subclass's field rebind ignored",
     "        if sub:                                      # the instance may be a subclass's: its bindings count too\n",
     "        if False:\n", "sec_dispatch"),
    ("loose off", '        if v.kind == "ext" and v.ref in _EXT_MEMBER_OK and self.loose(rel, m):\n',
     '        if False:\n', "sec_dispatch"),
    ("loose across language families",
     '        attrs = set(self.loose_patched.get((fam, False), ()))\n',
     "        attrs = set().union(*[v for (f, _t), v in self.loose_patched.items() if not _t])\n", "sec_dispatch"),
    ("a test file's loose patch counts for production callers", "        if self.test_file(rel):\n",
     "        if True:\n", "sec_dispatch"),
    ("star off", '                if "*" in ci.patched:                 # PATCH_RULES star: a mixin no file reads\n',
     '                if False:\n', "sec_dispatch"),
    ("static_split off", "        if ci.spec is not None and ci.spec.static_split:\n", "        if False:\n",
     "sec_dispatch"),
    ("sources ignored", "        if isinstance(sources, dict) and sources:\n", "        if False:\n", "sec_dispatch"),
    ("a failing shared reader breaks the pass",
     "            except Exception:  # noqa: BLE001 — a shared reader that fails costs only the re-read\n"
     "                pass\n",
     "            except Exception:  # noqa: BLE001\n                raise\n", "sec_dispatch"),
    # --- the phase-2 review
    ("graphify's symbol pass ignored (a walked def's nested bare call stays detached)",
     "        walked = self.symbol_walked(ff, sc, spec)\n", "        walked = False\n", "sec_review2"),
    ("graphify's symbol pass ignored, on the installed engine's real nodes",
     "        walked = self.symbol_walked(ff, sc, spec)\n", "        walked = False\n",
     "sec_real_engine"),
    ("a decorated def counts as walked",
     "        if spec.symbol_plain:\n", "        if False:\n", "sec_review2"),
    ("a method counts as walked",
     "        if self.engine == \"ast\" or spec is None or sc.parent != -1 or sc.kind not in spec.symbol_walk:\n",
     "        if self.engine == \"ast\" or spec is None or sc.parent != -1:\n", "sec_review2"),
    ("any bare call of a walked Python def is uncertain (a same-file class too)",
     "        if not spec.symbol_imports_only:\n            return True\n",
     "        if True:\n            return True\n", "sec_review2"),
    ("an external import counts as resolvable by the symbol pass",
     "self.import_value(rel, imp, 0).kind != \"ext\"", "True", "sec_review2"),
    ("a star import after a binding ignored",
     "        if tables and self.star_after(", "        if False and self.star_after(", "sec_mustnot"),
    ("a spec-less star marker rebinds bound names",
     "if getattr(i, \"kind\", \"\") == \"star\" and getattr(i, \"spec\", \"\")]",
     "if getattr(i, \"kind\", \"\") == \"star\"]", "sec_mustnot"),
    ("an interface target graded EXTRACTED", "                        and not self.via_interface(vd, t)\n",
     "                        and True\n", "sec_review2"),
    # the phase-2 verification
    ("an interface's OWN member typed (flask's ProxyMixin stub keyed)",
     "                if vd.kind == \"typed\" and any(self.interface_member(t) for t in vd.targets):\n",
     "                if False:\n", "sec_review2"),
    ("an interface's own member typed (the dispatch truth table)",
     "                if vd.kind == \"typed\" and any(self.interface_member(t) for t in vd.targets):\n",
     "                if False:\n", "sec_dispatch"),
    ("interface_member reads every claimed method as an interface's",
     "        return self.iface_only.get(t, False)\n", "        return t in self.owner_of\n", "sec_review2"),
    ("interface_member decided by the FIRST class claiming the node (an interface claiming a real method by name)",
     "                    self.iface_only[t] = self.iface_only.get(t, True) and iface\n",
     "                    self.iface_only.setdefault(t, iface)\n", "sec_verify2"),
    ("a merged (two-scope) node is never audited",
     "            if do_audit:                           # a node the engine merged from two scopes",
     "            if False:                              # a node the engine merged from two scopes",
     "sec_verify2"),
    ("a merged node audited over ONE scope's sites (not the union)",
     "                    for si in shared[ck]:\n", "                    for si in shared[ck][:1]:\n", "sec_verify2"),
    ("a merged node's weak attribute never applied (not counted as an audited caller)",
     "                    callers_audit.add(ck)\n                    st[\"audit_shared\"] += 1\n",
     "                    st[\"audit_shared\"] += 1\n", "sec_verify2"),
    ("moot groups judged (every group scans the veto)", "            if not prior and m not in g5:\n",
     "            if False:\n", "sec_review2"),
    ("a G5-only group counts as moot", "            if not prior and m not in g5:\n",
     "            if not prior:\n", "sec_review2"),
    ("the GC left paused after a run", "        finally:\n            if paused:\n                gc.enable()\n",
     "        finally:\n            if paused:\n                pass\n", "sec_review2"),
    ("the validator's int fast path accepts only exact ints",
     "    return type(x) is int or (isinstance(x, int) and not isinstance(x, bool))\n",
     "    return type(x) is int\n", "sec_review2"),
    ("the validator's site fast path accepts a bool line",
     "(type(x.line) is int or _ok_int(x.line))", "(type(x.line) is int or True)", "sec_review2"),
]


def _mutant(src: str, olds: list, news: list, tag: str):
    spec = importlib.util.spec_from_loader(f"typed_calls_mutant_{tag}", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(BUILD / "typed_calls.py")
    sys.modules[spec.name] = mod
    for old, new in zip(olds, news):
        src = src.replace(old, new, 1)
    exec(compile(src, str(BUILD / "typed_calls.py"), "exec"), mod.__dict__)
    return mod


def sec_mutations(T, rec: Rec) -> None:
    src = (BUILD / "typed_calls.py").read_text(encoding="utf-8")
    for i, (why, old, new, section) in enumerate(MUTATIONS):
        olds, news = ([old], [new]) if isinstance(old, str) else (list(old), list(new))
        if any(src.count(o) != 1 for o in olds):
            rec.check(f"Q mutation '{why}': each anchor is in the module exactly once", False,
                      [src.count(o) for o in olds])
            continue
        if section == "sec_real_engine" and real_engine()[0] == "skip":
            print(f"  [SKIP] mutation '{why}': graphify is not installed here (section R does not run)")
            continue
        try:
            mod = _mutant(src, olds, news, str(i))
            quiet = Rec(loud=False)
            globals()[section](mod, quiet)
            red = quiet.failed > 0
            detail = quiet.fails[:2]
        except Exception as exc:  # noqa: BLE001 — a crash is RED too
            red, detail = True, repr(exc)
        rec.check(f"Q mutation '{why}' turns {section} RED", red, detail)


# ================================================================================================

SECTIONS = [
    ("A: the frozen IR, the registry, the defaults", sec_ir),
    ("A: the import-time contract and the mirrors", sec_imports),
    ("B: flags", sec_flags),
    ("C: flag-off identity", sec_flag_off),
    ("D: the REPLACE / ADD truth table", sec_truth),
    ("E: candidate sets only narrow", sec_candidates),
    ("F: the caller policy", sec_policy),
    ("G: the audit predicate", sec_audit),
    ("H: grading, the evidence chain, module receivers, the external test", sec_grades),
    ("S: design §5 must-not shapes at the IR level", sec_mustnot),
    ("I: never cross-family; keys only to nodes", sec_family),
    ("J: deterministic and idempotent", sec_determinism),
    ("K: one language degrades alone", sec_robust),
    ("L: never raises", sec_never_raises),
    ("M: the textual veto's masks", sec_lexers),
    ("N: shapes", sec_shapes),
    ("P: attribution (nested scopes)", sec_attribution),
    ("R: the real engine's attribution", sec_real_engine),
    ("T: the budget", sec_budget),
    ("U: the review's gate gaps", sec_review),
    ("V: dynamic dispatch, PATCH_RULES, static_split, shared sources", sec_dispatch),
    ("W: the phase-2 review (graphify's symbol pass, the interface grade, moot groups, the GC pause, the "
     "validator's fast paths)", sec_review2),
    ("X: the phase-2 verification (a merged getter / setter node is audited over its scopes' union; an interface "
     "claiming a real method by name)", sec_verify2),
    ("O: seeded fuzz", sec_fuzz),
    ("Q: mutation teeth", sec_mutations),
]


def main() -> int:
    rec = Rec(loud=True)
    print(f"typed_calls core gates (S3a) — build/ at {BUILD}")
    if TC is None:
        rec.check("build/typed_calls.py imports", False, _IMPORT_ERROR)
    else:
        for title, fn in SECTIONS:
            print(f"\n-- {title} --")
            try:
                fn(TC, rec)
            except Exception:  # noqa: BLE001 — a crashing section is RED, never a traceback exit
                import traceback
                rec.check(f"{title}: section ran without crashing", False, traceback.format_exc()[-1500:])
    print(f"\nRESULT: {'GREEN' if rec.failed == 0 else 'RED'} — {rec.passed} passed, {rec.failed} failed")
    return 0 if rec.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
