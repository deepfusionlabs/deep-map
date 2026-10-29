#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking: C# reader hardening (DEEPMAP_TYPED_CALLS=csharp; "never raises; no edge from text
that is not a call"). No Neo4j, no engine, no network.

  P  300 seeded random C# programs (namespaces and usings across files; interfaces with 1..6 implementers, base
     classes, static classes with extension methods; fields typed by a class or an interface, primary constructors,
     properties, overloads, attribute lines; every receiver shape: fields, parameters, `var` / typed / target-typed
     `new()` locals, `this` / `base`, static `Type.M()`, bare and generic `M<int>()` calls, `x?.M()`, casts,
     lambdas, anonymous methods, local functions, interpolated strings; virtual / override / sealed / `new`
     modifiers, a block-scoped local shadowing a field, literal / named arguments against extension rivals of a
     method's name, nested types named like top-level ones; a generic base `G<T>` whose member subclasses override
     through `: G<Arg>`, static interface members called through the interface, `T x = new T()` locals) laced
     with traps (call-shaped text in
     strings, verbatim strings and comments, `nameof(M)`, a method group `Action a = M;`, `#if` regions, parse
     errors) run through build/receiver_facts_csharp.py + typed_calls.link over synthetic engine nodes and random
     engine-like name tuples. Every program must: never raise / record no error / fail no file; give FileFacts that
     validate and whose census never undercounts a site; emit only CallKeys to nodes of `nodes`, never to the
     caller, with a grade, EXTRACTED only in-file; type a call ONLY where the caller really calls that name (the
     generator's ledger — decoy text is no call); never fan one name out to more than MAX_TARGETS AMBIGUOUS targets;
     remove a tuple only for a name the caller really calls, and never from a caller whose span holds a parse error
     or an #if region; give each typed key a call_lines line inside its caller's span; give the same output twice;
     return the same list with the flags off. PLANTED shapes (a file of their own, each with its oracle from the C#
     rules Roslyn applies): a generic base's open-T member beside a concrete overload of the derived class, called
     with an argument of the type parameter's type argument or through the derived class's own `T` (never typed to
     the derived overload); a same-named definition of another signature in the middle of an override chain or
     beside the override, through the base class or an interface (the override that runs is always reached: its
     key, or the engine's tuple kept); a constructed local rebound by a deconstruction (plain, nested, `var`), a ref
     local or a user-defined `++` (the subclass's override reached, its member never EXTRACTED); two external types
     of one simple name (never typed to the derived overload of the other type); and round 2's: a generic class's
     `T?` (unconstrained: an annotation; `where T : struct`: Nullable<T>) closed by int / a struct / an enum (the
     closer's override reached, the subclass closing it with another type argument never keyed), one type of two
     spellings in an override or an interface implementer (nint / System.IntPtr, int? / System.Nullable<int>, a
     `global using` alias of CancellationToken or of a project class: the other spelling's definition reached, the
     base class's definition of the interface member's spelling never keyed), a private (or access-less) or
     `new virtual` same-signature middle method (the subclass's override reached, or never keyed). Each kind must
     be planted and judged often enough.
  M  300 seeded byte mutations of the C# fixture trees' sources (tests/typed_calls_cs_gates CQRS, REVIEW, RECHECK,
     SIGS, RND2):
     the reader never raises and every FileFacts validates; a scope whose span holds a parse error keeps no site.

Usage: python tests/typed_calls_cs_fuzz_gates.py [--seed N] [--n N]
"""
from __future__ import annotations

import os
import random
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))
sys.path.insert(0, str(REPO / "tests"))
for _k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
    os.environ.pop(_k, None)

SEED = 20261028
N = 300
_args = sys.argv[1:]
for _i, _a in enumerate(_args):
    if _a == "--seed" and _i + 1 < len(_args):
        SEED = int(_args[_i + 1])
    if _a == "--n" and _i + 1 < len(_args):
        N = int(_args[_i + 1])

_PASS = _FAIL = 0


def check(label: str, cond, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' -- ' + str(detail)[:900]) if detail != '' else ''}")


try:
    import typed_calls as TC                      # noqa: E402
    import receiver_facts_csharp as R             # noqa: E402
    import graphify_extract as GX                 # noqa: E402
    _ERR = ""
except Exception as _exc:  # noqa: BLE001 — the pre-feature tree: RED, never a crash
    TC = R = GX = None
    _ERR = repr(_exc)

ON = {"DEEPMAP_TYPED_CALLS": "csharp"}


class Src:
    """An in-memory graphify_extract._Source (raw / lines; the reader parses raw bytes itself)."""

    def __init__(self, files: dict):
        self.files = files

    def raw(self, rel):
        t = self.files.get(rel)
        return None if t is None else t.encode("utf-8")

    def lines(self, rel):
        t = self.files.get(rel)
        return None if t is None else GX._engine_lines(t.encode("utf-8"))

    def ts_tree(self, rel):
        return None

    def tree(self, rel):
        return None


# ================================================================================================
# P: the program generator (it keeps the ledger of every REAL call)
# ================================================================================================

METHODS = ["Save", "Find", "Load", "Run", "Close", "Apply", "Emit", "Check"]
STATICS = ["Make", "Create", "Build"]
FIELDS = ["_repo", "_cache", "_store", "_peer"]


class Program:
    def __init__(self, rng: random.Random, idx: int):
        self.rng, self.idx = rng, idx
        self.files: dict = {}
        self.nodes: list = []
        self.ledger: dict = {}            # caller key -> Counter(name) of REAL calls over its full span
        self.errored: set = set()         # caller keys whose span holds a parse error or an #if region
        self.spans: dict = {}             # caller key -> (first line, last line)
        self.n_files = rng.randint(1, 3)
        self.types: dict = {}             # file index -> [(name, kind, methods, statics, bases)]
        self.n = 0
        self.gen = None                   # a generic base class G<T> with one virtual member m(T item): (name, m)
        self.gen_over: dict = {}          # class name -> the type argument its `: G<Arg>` gives (it overrides m)
        self.planted: list = []           # (kind, rule, caller key, member, target key): the fix round's shapes
        self.extra_calls: list = []       # the engine's name tuples of the planted callers
        self.globs: list = []             # `global using` lines the planted shapes need (src/PlantGlob.cs)

    def ns(self, fi) -> str:
        return f"Fz{self.idx}.N{fi}"

    # ------------------------------------------------------------------ planning
    def plan(self):
        rng = self.rng
        ifaces = []
        if rng.random() < 0.5:
            self.gen = (f"G{self.idx}", rng.choice(METHODS))
        for fi in range(self.n_files):
            out = []
            if rng.random() < 0.6:
                name = f"I{fi}"
                # a static interface member with a body (an exact call) beside the instance members, sometimes
                out.append((name, "interface", rng.sample(METHODS, rng.randint(1, 3)),
                            rng.sample(STATICS, 1) if rng.random() < 0.4 else [], []))
                ifaces.append((fi, name, out[-1][2]))
            for ci in range(rng.randint(1, 4)):
                name = f"C{fi}{ci}"
                ms = rng.sample(METHODS, rng.randint(1, 4))
                ss = rng.sample(STATICS, rng.randint(0, 2))
                bases = []
                classes = [t for f in range(fi + 1) for t in self.types.get(f, out) if t[1] == "class"]
                if classes and rng.random() < 0.3:
                    bases.append(rng.choice(classes)[0])
                elif self.gen and rng.random() < 0.4:  # `: G<Arg>` overriding G's m(T) as m(Arg)
                    arg = rng.choice([c[0] for c in classes] + ["int", "string"])
                    bases.append(f"{self.gen[0]}<{arg}>")
                    self.gen_over[name] = arg
                    ms = list(dict.fromkeys(ms + [self.gen[1]]))
                if ifaces and rng.random() < 0.7:
                    _f, iname, ims = rng.choice(ifaces)
                    bases.append(iname)
                    ms = list(dict.fromkeys(ms + ims))
                out.append((name, "class", ms, ss, bases))
            self.types[fi] = out

    def all_types(self):
        return [t for f in range(self.n_files) for t in self.types[f]]

    def classes(self):
        return [t for t in self.all_types() if t[1] == "class"]

    # ------------------------------------------------------------------ statements
    def fresh(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def recv_type(self):
        return self.rng.choice(self.all_types())

    def stmt(self, ctx, out: list, real: Counter, errs: list):
        """One statement for a body; real calls go to `real`."""
        rng = self.rng
        cls, fields, param = ctx["cls"], ctx["fields"], ctx.get("param")
        k = rng.randrange(26)
        m = rng.choice(METHODS)
        if k == 0 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.append(f"{f}.{m}();")
            real[m] += 1
        elif k == 1 and param is not None:
            m = rng.choice(param[1][2] or METHODS)
            out.append(f"{param[0]}.{m}();")
            real[m] += 1
        elif k == 2:
            c = rng.choice(self.classes())
            v = self.fresh("v")
            m = rng.choice(c[2])
            out.append(f"var {v} = new {c[0]}();")
            out.append(f"{v}.{m}();")
            real[c[0]] += 1
            real[m] += 1
        elif k == 3:
            c = rng.choice(self.classes())
            v = self.fresh("t")
            m = rng.choice(c[2])
            out.append(f"{c[0]} {v} = new();")
            out.append(f"{v}.{m}();")
            real[c[0]] += 1
            real[m] += 1
        elif k == 4 and cls is not None and not ctx.get("static"):
            m = rng.choice(cls[2])
            out.append(f"this.{m}();")
            real[m] += 1
        elif k == 5 and cls is not None and cls[4] and not ctx.get("static"):
            out.append(f"base.{m}();")
            real[m] += 1
        elif k == 6:
            c = rng.choice(self.classes())
            if c[3]:
                s = rng.choice(c[3])
                out.append(f"{c[0]}.{s}();")
                real[s] += 1
        elif k == 7 and cls is not None:
            m = rng.choice(cls[2])
            out.append(f"{m}();")
            real[m] += 1
        elif k == 8 and cls is not None:
            m = rng.choice(cls[2])
            out.append(f"{m}<int>();")
            real[m] += 1
        elif k == 9 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.append(f"{f}?.{m}();")
            real[m] += 1
        elif k == 10 and fields:
            f, ft = rng.choice(fields)
            c = rng.choice(self.classes())
            out.append(f"(({c[0]}){f}).{m}();")
            real[m] += 1
        elif k == 11 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.append(f"System.Action {self.fresh('l')} = () => {f}.{m}();")
            real[m] += 1
        elif k == 12 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.append(f"System.Action {self.fresh('d')} = delegate () {{ {f}.{m}(); }};")
            real[m] += 1
        elif k == 13 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            lf = self.fresh("Lf")
            out.append(f"void {lf}() {{ {f}.{m}(); }}")
            out.append(f"{lf}();")
            real[m] += 1
            real[lf] += 1
        elif k == 14 and fields:
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.append(f'var {self.fresh("i")} = $"{{{f}.{m}()}} and more";')
            real[m] += 1
        elif k == 15:                                  # decoys: no call
            f = rng.choice(FIELDS)
            out.append(rng.choice([f'var {self.fresh("s")} = "{f}.{m}()";',
                                   f'var {self.fresh("s")} = @"{f}.{m}() ""q""";',
                                   f"// {f}.{m}();", f"/* {m}() */",
                                   f"var {self.fresh('q')} = nameof({m});",
                                   f"System.Action {self.fresh('g')} = {m};"]))
        elif k == 16 and fields:                       # an #if region: the caller is unjudgeable
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            out.extend(["#if DEBUG", f"{f}.{m}();", "#else", f"{f}.{m}();", "#endif"])
            real[m] += 2
            errs.append("pp")
        elif k == 17 and rng.random() < 0.3:           # a parse error
            f = rng.choice(FIELDS)
            out.append(f"{f}.{m}(;")
            real[m] += 1
            errs.append("syntax")
        elif k == 18 and ctx.get("async"):
            out.append(f"var {self.fresh('a')} = await System.Threading.Tasks.Task.FromResult(1);")
            real["FromResult"] += 1
        elif k == 19 and ctx.get("exts"):
            c, ext = rng.choice(ctx["exts"])
            v = self.fresh("x")
            out.append(f"var {v} = new {c}();")
            out.append(f"{v}.{ext}();")
            real[c] += 1
            real[ext] += 1
        elif k == 20:
            out.append(f"var {self.fresh('n')} = {rng.randint(0, 9)};")
        elif k == 21 and fields:                       # a block-scoped local shadowing a field, then the field
            f, ft = rng.choice(fields)
            c = rng.choice(self.classes())
            m = rng.choice(c[2])
            m2 = rng.choice(ft[2] or METHODS)
            head = rng.choice(["if (true)", "for (int q = 0; q < 1; q++)", "lock (this)",
                               "using (var u = new System.IO.MemoryStream())"])
            out.append(f"{head} {{ {c[0]} {f} = new {c[0]}(); {f}.{m}(); }}")
            out.append(f"{f}.{m2}();")
            real[c[0]] += 1
            real[m] += 1
            real[m2] += 1
            if "MemoryStream" in head:
                real["MemoryStream"] += 1
        elif k == 22 and fields:                       # arguments: literals of several types, a parameter
            f, ft = rng.choice(fields)
            m = rng.choice(ft[2] or METHODS)
            args = rng.choice(['1', '"s"', '2L', '1.5f', '3m', "'c'", 'true', 'null', 'p' if param else '0', 'x: 1'])
            out.append(f"{f}.{m}({args});")
            real[m] += 1
        elif k == 23 and ctx.get("nested"):            # a nested type named like a top-level one
            nn, nm = ctx["nested"]
            v = self.fresh("w")
            out.append(f"{nn} {v} = new {nn}();")
            out.append(f"{v}.{nm}();")
            real[nn] += 1
            real[nm] += 1
        elif k == 24 and any(t[1] == "interface" and t[3] for t in self.all_types()):
            it = rng.choice([t for t in self.all_types() if t[1] == "interface" and t[3]])
            s = rng.choice(it[3])                      # a static interface member: an exact call
            out.append(f"var {self.fresh('f')} = {it[0]}.{s}();")
            real[s] += 1
        elif k == 25:                                  # a local constructing exactly its declared type
            c = rng.choice(self.classes())
            v = self.fresh("e")
            m = rng.choice(c[2])
            out.append(f"{c[0]} {v} = new {c[0]}();")
            out.append(f"{v}.{m}({'p' if param and rng.random() < 0.5 else ''});")
            real[c[0]] += 1
            real[m] += 1
        else:
            out.append(f"{m}Unknown();")
            real[f"{m}Unknown"] += 1

    # ------------------------------------------------------------------ files
    def build(self):
        rng = self.rng
        self.plan()
        exts_all = []
        for fi in range(self.n_files):
            if rng.random() < 0.4:
                c = rng.choice(self.classes())
                # an extension named like a class method (a rival of an instance hit of another signature) or not
                exts_all.append((fi, c[0], rng.choice(METHODS) if rng.random() < 0.5 else f"{rng.choice(METHODS)}Ext"))
        for fi in range(self.n_files):
            rel = f"src/F{fi}.cs"
            lines = [f"using {self.ns(o)};" for o in range(self.n_files) if o != fi]
            if rng.random() < 0.3:
                lines.insert(0, "﻿using System;")
            lines.append(f"namespace {self.ns(fi)};")
            lines.append("")
            self.node("Module", f"F{fi}", rel, 1)
            if fi == 0 and self.gen:                   # the generic base: `G<T> { virtual void m(T item) }`
                lines.append(f"public class {self.gen[0]}<T>")
                self.node("Class", self.gen[0], rel, len(lines))
                lines.append("{")
                lines.append(f"    public virtual void {self.gen[1]}(T item)")
                self.node("Method", f"{self.gen[0]}.{self.gen[1]}", rel, len(lines))
                lines += ["    {", "    }", "}", ""]
            for name, kind, ms, ss, bases in self.types[fi]:
                t = (name, kind, ms, ss, bases)
                if kind == "interface":
                    lines.append(f"public interface {name}")
                    self.node("Class", name, rel, len(lines))
                    lines.append("{")
                    for m in ms:
                        lines.append(f"    void {m}();")
                        self.node("Method", f"{name}.{m}", rel, len(lines))
                    for s in ss:
                        lines.append(f"    static {name} {s}()")
                        self.node("Method", f"{name}.{s}", rel, len(lines))
                        lines += ["    {", "        return null;", "    }"]
                    lines.append("}")
                    lines.append("")
                    continue
                primary = rng.random() < 0.2
                fields = []
                ptypes = []
                if primary:
                    pt = self.recv_type()
                    ptypes.append(("pc", pt))
                head = f"public class {name}" + (f"({ptypes[0][1][0]} pc)" if primary else "")
                head += f" : {', '.join(bases)}" if bases else ""
                lines.append(head)
                self.node("Class", name, rel, len(lines))
                lines.append("{")
                if primary:
                    fields.append(ptypes[0])
                for f in rng.sample(FIELDS, rng.randint(0, 2)):
                    ft = self.recv_type()
                    lines.append(f"    private readonly {ft[0]} {f};")
                    fields.append((f, ft))
                if rng.random() < 0.2:
                    lines.append("    public int Prop { get; set; }")
                nested = None
                others = [c[0] for c in self.classes() if c[0] != name]
                if others and rng.random() < 0.25:     # a nested type named like a top-level one (it shadows it)
                    other, nm = rng.choice(others), rng.choice(METHODS)
                    lines.append(f"    public class {other}")
                    self.node("Class", other, rel, len(lines))
                    lines.append("    {")
                    lines.append(f"        public void {nm}() {{ }}")
                    self.node("Method", f"{other}.{nm}", rel, len(lines))
                    lines.append("    }")
                    nested = (other, nm)
                ctx = {"cls": t, "fields": fields, "exts": [(c, e) for _f, c, e in exts_all], "nested": nested}
                overload = rng.choice(ms) if rng.random() < 0.15 else None
                for mname in ms + ss + ([overload] if overload else []):
                    static = mname in ss
                    is_async = rng.random() < 0.2 and not static
                    first = len(lines) + 1             # graphify starts a C# member's node at its first attribute line
                    if rng.random() < 0.25:
                        lines.append("    [System.Obsolete]")
                    param = None
                    sig = "()"
                    if rng.random() < 0.6:
                        pt = self.recv_type()
                        param = ("p", pt)
                        sig = f"({pt[0]} p)"
                    ret ="async System.Threading.Tasks.Task" if is_async else "void"
                    mod = "static " if static else rng.choice(["", "", "", "virtual ", "override ", "new ",
                                                               "sealed override ", "new virtual "])
                    if not static and self.gen and mname == self.gen[1] and name in self.gen_over \
                            and rng.random() < 0.8:        # G<Arg>'s m(T item) overridden as m(Arg item)
                        mod, param, sig = "override ", None, f"({self.gen_over[name]} item)"
                    lines.append(f"    public {mod}{ret} {mname}{sig}")
                    lines.append("    {")
                    full = f"{name}.{mname}"
                    key = next((n["key"] for n in self.nodes if n["name"] == full and n["source_file"] == rel),
                               None)
                    if key is None:
                        key = self.node("Method", full, rel, first)
                    real, errs = self.ledger.setdefault(key, Counter()), []
                    body: list = []
                    for _ in range(rng.randint(0, 6)):
                        self.stmt(dict(ctx, param=param, static=static, **{"async": is_async}), body, real, errs)
                    lines += [("        " + b) if not b.startswith("#") else b for b in body]
                    lines.append("    }")
                    span = self.spans.get(key)
                    self.spans[key] = (first if span is None else span[0], len(lines))
                    if errs:
                        self.errored.add(key)
                lines.append("}")
                lines.append("")
            mine = [(c, e) for f, c, e in exts_all if f == fi]
            if mine:
                lines.append(f"public static class X{fi}")
                self.node("Class", f"X{fi}", rel, len(lines))
                lines.append("{")
                for c, e in mine:
                    extra = "" if e.endswith("Ext") else ", int n"
                    lines.append(f"    public static void {e}(this {c} c{extra}) {{ }}")
                    self.node("Method", f"X{fi}.{e}", rel, len(lines))
                lines.append("}")
            self.files[rel] = "\n".join(lines) + "\n"
        if rng.random() < 0.8:
            self.plant()
        return self

    # ------------------------------------------------------------------ planted shapes (the fix round)
    def plant(self):
        """A file of planted shapes, each with its oracle (self.planted): rule "no_key" = the caller never gets a
        CallKey to target; "reach" = target is reached (a CallKey to it, or the engine's name tuple of the member
        kept); "never_E" = no typed key of the member from the caller is EXTRACTED."""
        rng = self.rng
        rel = "src/Plant.cs"
        lines = [f"namespace Fz{self.idx}.Pl;", ""]
        for u in range(rng.randint(1, 4)):
            kind = rng.choice(["open", "slot", "rebind", "extname", "annot", "spell", "priv"])
            getattr(self, f"plant_{kind}")(lines, rel, u)
        self.files[rel] = "\n".join(lines) + "\n"
        if self.globs:
            self.files["src/PlantGlob.cs"] = "\n".join(self.globs) + "\n"

    def _type(self, lines, rel, head, members, iface=False):
        """`head { members }`: members = [(name or None, signature, body lines)]; one node per member name at its first
        definition's line (graphify merges overloads; an operator gets none), its span recorded, plus the type's
        node. An interface's members are body-less. Returns {member: node key}."""
        name = head.split()[2].split("<")[0]
        lines.append(head)
        self.node("Class", name, rel, len(lines))
        lines.append("{")
        keys = {}
        for mname, sig, body in members:
            lines.append(f"    {sig}" + (";" if iface else ""))
            first = len(lines)
            if mname is not None and mname not in keys:
                keys[mname] = self.node("Method", f"{name}.{mname}", rel, first)
            if not iface:
                lines.append("    {")
                lines.extend(f"        {b}" for b in body)
                lines.append("    }")
            if mname is not None:
                lo, _hi = self.spans.get(keys[mname], (first, first))
                self.spans[keys[mname]] = (lo, len(lines))
        lines.append("}")
        lines.append("")
        return keys

    def _caller(self, key, member):
        """A planted caller calls member once: the ledger, and the engine's name tuple of it."""
        self.ledger.setdefault(key, Counter())[member] += 1
        self.extra_calls.append((key, member))

    def plant_open(self, lines, rel, u):
        """D1: GS<T>.Put(T) beside GD<T>.Put(PlC): an argument of GD<PlA>'s type argument binds the base (no key to
        GD.Put); inside GD<T>, `Put(x)` with x : T binds the base too; an argument of PlC fits the derived overload
        beside a rival (never EXTRACTED). A virtual Keep(T) overridden as Keep(T) is the same slot, sometimes."""
        rng = self.rng
        put, keep, cls_a, cls_c = f"Put{u}", f"Keep{u}", f"PlA{u}", f"PlC{u}"
        with_keep = rng.random() < 0.5
        base = [(put, f"public void {put}(T item)", [])] + (
            [(keep, f"public virtual void {keep}(T item)", [])] if with_keep else [])
        der = [(put, f"public void {put}({cls_c} c)", [])] + (
            [(keep, f"public override void {keep}(T item)", [])] if with_keep else []) + [
            (f"Self{u}", f"public void Self{u}(T x)", [f"{put}(x);"])]
        blocks = [("base", f"public class GS{u}<T>", base), ("der", f"public class GD{u}<T> : GS{u}<T>", der)]
        if rng.random() < 0.5:
            blocks.reverse()                       # the base last: the engine's same-file tuple is the true one
        keys = {tag: self._type(lines, rel, head, members) for tag, head, members in blocks}
        self._type(lines, rel, f"public class {cls_a}", [])
        self._type(lines, rel, f"public class {cls_c}", [])
        lines.append(f"public class PU{u}")
        self.node("Class", f"PU{u}", rel, len(lines))
        lines.append("{")
        lines.append(f"    private readonly GD{u}<{cls_a}> _g;")
        callers = {}
        for nm, arg, ty in ((f"Go{u}", "a", cls_a), (f"GoC{u}", "c", cls_c)):
            lines.append(f"    public void {nm}({ty} {arg})")
            k = self.node("Method", f"PU{u}.{nm}", rel, len(lines))
            lines += ["    {", f"        _g.{put}({arg});", "    }"]
            self.spans[k] = (len(lines) - 3, len(lines))
            self._caller(k, put)
            callers[nm] = k
        lines += ["}", ""]
        sk = keys["der"][f"Self{u}"]
        self._caller(sk, put)
        self.planted.append(("open", "no_key", callers[f"Go{u}"], put, keys["der"][put]))
        self.planted.append(("open", "no_key", sk, put, keys["der"][put]))
        self.planted.append(("open", "never_E", callers[f"GoC{u}"], put, None))

    def plant_slot(self, lines, rel, u):
        """D2: HB.Hop() virtual; another signature `Hop(int)` in the middle class or beside HS's override; HS's
        override of Hop() runs for an HS: it is always reached (its key, or the engine's tuple kept), through the base
        class or an interface, and the call is dispatch (never EXTRACTED)."""
        rng = self.rng
        hop = f"Hop{u}"
        via_iface, middle = rng.random() < 0.5, rng.random() < 0.5
        if via_iface:
            self._type(lines, rel, f"public interface IH{u}", [(hop, f"void {hop}()", [])], iface=True)
        self._type(lines, rel, f"public class HB{u}" + (f" : IH{u}" if via_iface else ""),
                   [(hop, f"public virtual void {hop}()", [])])
        self._type(lines, rel, f"public class HM{u} : HB{u}", [(hop, f"public void {hop}(int x)", [])] if middle else [])
        sm = [(hop, f"public override void {hop}()", [])]
        if not middle:
            other = [(hop, f"public void {hop}(int x)", [])]
            sm = other + sm if rng.random() < 0.5 else sm + other
        hs = self._type(lines, rel, f"public class HS{u} : HM{u}", sm)
        lines.append(f"public class HU{u}")
        self.node("Class", f"HU{u}", rel, len(lines))
        lines.append("{")
        lines.append(f"    private readonly {'IH' if via_iface else 'HB'}{u} _h;")
        lines.append(f"    public void Go{u}()")
        k = self.node("Method", f"HU{u}.Go{u}", rel, len(lines))
        lines += ["    {", f"        _h.{hop}();", "    }"]
        self.spans[k] = (len(lines) - 3, len(lines))
        lines += ["}", ""]
        self._caller(k, hop)
        self.planted.append(("slot", "reach", k, hop, hs[hop]))
        self.planted.append(("slot", "never_E", k, hop, None))

    def plant_rebind(self, lines, rel, u):
        """D3: a constructed local rebound before its call (a deconstruction, nested, a ref local, a user-defined
        ++): the subclass's override is reached and the call is never EXTRACTED."""
        rng = self.rng
        pk = f"Pk{u}"
        self._type(lines, rel, f"public class RB{u}", [(pk, f"public virtual void {pk}()", []),
                                                       (None, f"public static RB{u} operator ++(RB{u} r)",
                                                        ["return r;"])])
        rc = self._type(lines, rel, f"public class RC{u} : RB{u}", [(pk, f"public override void {pk}()", [])])
        decl = rng.choice([f"RB{u} b = new RB{u}();", f"var b = new RB{u}();", f"RB{u} b = new();"])
        how = rng.choice(["tuple", "nested", "ref", "inc"])
        body = [decl, "int y;", "int z;"] + {"tuple": [f"(b, y) = (new RC{u}(), 1);"],
                                              "nested": [f"((b, y), z) = ((new RC{u}(), 1), 2);"],
                                              "ref": [f"ref RB{u} r = ref b;", f"r = new RC{u}();"],
                                              "inc": ["b++;"]}[how] + [f"b.{pk}();"]
        lines.append(f"public class RU{u}")
        self.node("Class", f"RU{u}", rel, len(lines))
        lines.append("{")
        lines.append(f"    public void Go{u}()")
        k = self.node("Method", f"RU{u}.Go{u}", rel, len(lines))
        lines.append("    {")
        lines.extend(f"        {b}" for b in body)
        lines.append("    }")
        self.spans[k] = (len(lines) - 2 - len(body), len(lines))
        lines += ["}", ""]
        self._caller(k, pk)
        self.ledger[k][f"RB{u}"] += 1
        if how != "inc":
            self.ledger[k][f"RC{u}"] += 1
        self.planted.append(("rebind", "reach", k, pk, rc[pk]))
        self.planted.append(("rebind", "never_E", k, pk, None))

    def plant_extname(self, lines, rel, u):
        """D4: Dw(A.X) in the base, Dw(B.X) in the derived class (one simple name, two namespaces): an A.X argument
        binds the base, never the derived overload."""
        dw = f"Dw{u}"
        a, b = self.rng.choice([("System.Drawing.Point", "Windows.Foundation.Point"),
                                ("System.Timers.Timer", "System.Threading.Timer"),
                                ("Microsoft.Extensions.Logging.ILogger", "Serilog.ILogger")])
        self._type(lines, rel, f"public class DwB{u}", [(dw, f"public void {dw}({a} p)", [])])
        dd = self._type(lines, rel, f"public class DwD{u} : DwB{u}", [(dw, f"public void {dw}({b} p)", [])])
        lines.append(f"public class DwU{u}")
        self.node("Class", f"DwU{u}", rel, len(lines))
        lines.append("{")
        lines.append(f"    private readonly DwD{u} _d;")
        lines.append(f"    public void Go{u}({a} p)")
        k = self.node("Method", f"DwU{u}.Go{u}", rel, len(lines))
        lines += ["    {", f"        _d.{dw}(p);", "    }"]
        self.spans[k] = (len(lines) - 3, len(lines))
        lines += ["}", ""]
        self._caller(k, dw)
        self.planted.append(("extname", "no_key", k, dw, dd[dw]))

    def _user(self, lines, rel, name, field, call, member):
        """`public class name { private readonly field; public void Go() { call } }`: a planted caller of member."""
        lines.append(f"public class {name}")
        self.node("Class", name, rel, len(lines))
        lines.append("{")
        lines.append(f"    private readonly {field};")
        lines.append("    public void Go()")
        k = self.node("Method", f"{name}.Go", rel, len(lines))
        lines += ["    {", f"        {call}", "    }"]
        self.spans[k] = (len(lines) - 3, len(lines))
        lines += ["}", ""]
        self._caller(k, member)
        return k

    def plant_annot(self, lines, rel, u):
        """Round 2, N1 + R2: AnB<T>.An(T?) — an annotation of an unconstrained T (C# 9+), Nullable<T> under `where T :
        struct` — closed by int / a struct / an enum: the closer's override `An(int)` (`An(int?)` under struct) is the
        slot's and is reached; the subclass closing AnB with another type argument (string, long) is never the
        receiver's run-time class: no key to it; the call is dispatch (never EXTRACTED)."""
        rng = self.rng
        an = f"An{u}"
        struct = rng.random() < 0.4
        arg = rng.choice(["int", f"PlS{u}", f"PlE{u}"])
        if arg == f"PlS{u}":
            self._type(lines, rel, f"public struct PlS{u}", [])
        elif arg == f"PlE{u}":
            self._type(lines, rel, f"public enum PlE{u}", [])
        q = "?" if struct else ""
        other = "long" if struct else "string"
        self._type(lines, rel, f"public class AnB{u}<T>" + (" where T : struct" if struct else ""),
                   [(an, f"public virtual void {an}(T? v)", [])])
        mine = self._type(lines, rel, f"public class AnI{u} : AnB{u}<{arg}>",
                          [(an, f"public override void {an}({arg}{q} v)", [])])
        oth = self._type(lines, rel, f"public class AnO{u} : AnB{u}<{other}>",
                         [(an, f"public override void {an}({other}? v)", [])])
        k = self._user(lines, rel, f"AnU{u}", f"AnB{u}<{arg}> _a", f"_a.{an}(default);", an)
        self.planted.append(("annot", "reach", k, an, mine[an]))
        self.planted.append(("annot", "no_key", k, an, oth[an]))
        self.planted.append(("annot", "never_E", k, an, None))

    def plant_spell(self, lines, rel, u):
        """Round 2, N2: one type spelled two ways — nint / System.IntPtr, int? / System.Nullable<int>, a `global using`
        alias of CancellationToken or of a project class: the override of the other spelling is the slot's (reached,
        never EXTRACTED); through an interface, the implementer's own `new` definition of the other spelling
        implements the member (reached) and the base class's definition of the first does not (no key)."""
        rng = self.rng
        sp = f"Sp{u}"
        kind = rng.choice(["ptr", "nul", "tok", "cls"])
        if kind == "ptr":
            a, b = "nint", "System.IntPtr"
        elif kind == "nul":
            a, b = "int?", "System.Nullable<int>"
        elif kind == "tok":
            a, b = "System.Threading.CancellationToken", f"Tk{self.idx}x{u}"
            self.globs.append(f"global using {b} = {a};")
        else:
            self._type(lines, rel, f"public class PlO{u}", [])
            a, b = f"PlO{u}", f"Od{self.idx}x{u}"
            self.globs.append(f"global using {b} = Fz{self.idx}.Pl.{a};")
        if rng.random() < 0.5:
            a, b = b, a
        if rng.random() < 0.5:
            self._type(lines, rel, f"public class SpB{u}", [(sp, f"public virtual void {sp}({a} v)", [])])
            d = self._type(lines, rel, f"public class SpD{u} : SpB{u}", [(sp, f"public override void {sp}({b} v)", [])])
            k = self._user(lines, rel, f"SpU{u}", f"SpB{u} _s", f"_s.{sp}(default);", sp)
            self.planted.append(("spell", "reach", k, sp, d[sp]))
            self.planted.append(("spell", "never_E", k, sp, None))
        else:
            self._type(lines, rel, f"public interface ISp{u}", [(sp, f"void {sp}({a} v)", [])], iface=True)
            bb = self._type(lines, rel, f"public class SpB{u}", [(sp, f"public void {sp}({a} v)", [])])
            ii = self._type(lines, rel, f"public class SpI{u} : SpB{u}, ISp{u}", [(sp, f"public new void {sp}({b} v)", [])])
            k = self._user(lines, rel, f"SpU{u}", f"ISp{u} _s", f"_s.{sp}(default);", sp)
            self.planted.append(("spell", "reach", k, sp, ii[sp]))
            self.planted.append(("spell", "no_key", k, sp, bb[sp]))

    def plant_priv(self, lines, rel, u):
        """Round 2, R4: PvA.Pv() virtual; PvB redefines Pv() private (written, or no access modifier: private by
        default) — no subclass sees it, so PvC's override is the slot's (reached) — or as a `new virtual` (public, or
        internal in the same project) — another slot: no key to PvC's override. Never EXTRACTED."""
        rng = self.rng
        pv = f"Pv{u}"
        mod = rng.choice(["private new", "new", "public new virtual", "internal new virtual"])
        self._type(lines, rel, f"public class PvA{u}", [(pv, f"public virtual void {pv}()", [])])
        self._type(lines, rel, f"public class PvB{u} : PvA{u}", [(pv, f"{mod} void {pv}()", [])])
        acc = "internal" if mod.startswith("internal") else "public"      # an override keeps its virtual's access
        c = self._type(lines, rel, f"public class PvC{u} : PvB{u}", [(pv, f"{acc} override void {pv}()", [])])
        k = self._user(lines, rel, f"PvU{u}", f"PvA{u} _p", f"_p.{pv}();", pv)
        self.planted.append(("priv", "reach" if "virtual" not in mod else "no_key", k, pv, c[pv]))
        self.planted.append(("priv", "never_E", k, pv, None))

    def node(self, label, name, rel, line) -> str:
        key = f"p{self.idx}_{rel.replace('/', '_').replace('.', '_')}_{name.replace('.', '_')}_{line}"
        self.nodes.append({"key": key, "label": label, "name": name, "source_file": rel, "start_line": line})
        return key

    def engine_calls(self) -> list:
        """Random engine-like name tuples (real names and others)."""
        rng = self.rng
        names = METHODS + STATICS + [t[0] for t in self.all_types()] + ["ToString", "FromResult"]
        out = []
        for n in self.nodes:
            if n["label"] in ("Function", "Method"):
                for nm in rng.sample(names, rng.randint(0, 3)):
                    out.append((n["key"], nm))
        return out + list(self.extra_calls)


_EMPTY: list = []


def empty_root() -> str:
    """An empty directory: the sources are in memory, so the binder's .csproj lookup must see no tree on disk (a
    root of "." would walk the repository)."""
    if not _EMPTY:
        import tempfile
        _EMPTY.append(tempfile.mkdtemp(prefix="dm_tccs_fuzz_"))
    return _EMPTY[0]


def run_program(prog: Program, calls: list, env=ON):
    nodes = [dict(n) for n in prog.nodes]
    return nodes, TC.link(empty_root(), nodes, calls, env=env, source=Src(prog.files),
                          is_test=lambda p: False)


def seg(name) -> str:
    return TC.name_seg(name)


def sec_programs():
    print(f"\n-- P: {N} seeded random C# programs (seed {SEED}) --")
    rng = random.Random(SEED)
    viol: dict = {k: [] for k in ("raised", "error", "files_failed", "invalid facts", "census undercount",
                                  "not a node / self", "bad grade", "EXTRACTED across files", "edge without a call",
                                  "fan-out above MAX_TARGETS", "removal without a call", "removal in an errored caller",
                                  "call line outside the caller", "nondeterministic", "flag-off copy",
                                  "planted: a key the C# rules forbid", "planted: the override that runs lost",
                                  "planted: a dispatch call EXTRACTED")}
    seen = Counter()
    for i in range(N):
        prog = Program(rng, i).build()
        calls = prog.engine_calls()
        rd = R.make_reader(TC.LANGUAGES["csharp"])
        for rel in prog.files:
            try:
                ff = rd.facts(rel, Src(prog.files))
            except Exception as exc:  # noqa: BLE001
                viol["raised"].append((i, rel, repr(exc)))
                continue
            if ff is None or TC.validate_facts(ff):
                viol["invalid facts"].append((i, rel, ff and TC.validate_facts(ff)))
                continue
            for s in ff.scopes:
                c = Counter(x.member for x in s.sites)
                if any((s.all_names or {}).get(m, 0) < n for m, n in c.items()):
                    viol["census undercount"].append((i, rel, s.name))
        try:
            nodes, (out, st) = run_program(prog, list(calls))
        except Exception as exc:  # noqa: BLE001
            viol["raised"].append((i, "link", repr(exc)))
            continue
        if st.get("error"):
            viol["error"].append((i, st["error"]))
            continue
        if st.get("files_failed"):
            viol["files_failed"].append((i, st["files_failed"]))
        by = {n["key"]: n for n in nodes}
        amb = Counter()
        for c in out:
            if not GX.is_call_key(c[1]):
                continue
            seen["typed"] += 1
            t, caller = by.get(c[1]), by.get(c[0])
            if t is None or caller is None or c[0] == c[1]:
                viol["not a node / self"].append((i, c))
                continue
            g = (caller.get("call_typed") or {}).get(c[1])
            if g not in TC.GRADES:
                viol["bad grade"].append((i, c, g))
            if g == "EXTRACTED" and t["source_file"] != caller["source_file"]:
                viol["EXTRACTED across files"].append((i, c))
            if g == "AMBIGUOUS":
                amb[(c[0], seg(t["name"]))] += 1
                seen["ambiguous"] += 1
            if prog.ledger.get(c[0], Counter())[seg(t["name"])] <= 0:
                viol["edge without a call"].append((i, caller["name"], t["name"]))
            line = ((caller.get("call_lines") or {}).get("key") or {}).get(c[1])
            lo, hi = prog.spans.get(c[0], (0, -1))
            if not (isinstance(line, int) and lo <= line <= hi):
                viol["call line outside the caller"].append((i, caller["name"], t["name"], line, (lo, hi)))
        viol["fan-out above MAX_TARGETS"] += [(i, k) for k, v in amb.items() if v > TC.MAX_TARGETS]
        after = Counter((a, str(b), bool(GX.is_call_key(b))) for a, b in out)
        for a, b in calls:
            key = (a, str(b), False)
            if after[key] > 0:
                after[key] -= 1
                continue
            seen["removed"] += 1
            if prog.ledger.get(a, Counter())[seg(b)] <= 0:
                viol["removal without a call"].append((i, by[a]["name"], b))
            if a in prog.errored:
                viol["removal in an errored caller"].append((i, by[a]["name"], b))
        typed_by = {}
        for a, b in out:
            if GX.is_call_key(b):
                typed_by.setdefault(a, set()).add(str(b))
        names_left = Counter((a, str(b)) for a, b in out if not GX.is_call_key(b))
        for kind, rule, ck, member, tk in prog.planted:
            seen[f"planted {kind}"] += 1
            mine = typed_by.get(ck, set())
            if rule == "no_key" and tk in mine:
                viol["planted: a key the C# rules forbid"].append((i, kind, by[ck]["name"], by[tk]["name"]))
            elif rule == "reach" and tk not in mine and names_left[(ck, member)] <= 0:
                viol["planted: the override that runs lost"].append((i, kind, by[ck]["name"], by[tk]["name"]))
            elif rule == "never_E":
                gr = by[ck].get("call_typed") or {}
                bad = [by[t]["name"] for t in mine if seg(by[t]["name"]) == member and gr.get(t) == "EXTRACTED"]
                if bad:                            # dispatch (slot, rebind), or a hit beside a rival (open)
                    viol["planted: a dispatch call EXTRACTED"].append((i, kind, by[ck]["name"], bad))
            if rule == "reach":
                seen[f"planted {kind} keyed"] += int(tk in mine)
        if prog.errored:
            seen["errored programs"] += 1
        ds = (st.get("by_lang") or {}).get("csharp", {}).get("dispatch", {})
        seen["dispatch sites"] += int(ds.get("sites", 0) or 0)
        cst = (st.get("by_lang") or {}).get("csharp", {})
        seen["virtual sites"] += int((cst.get("virtual") or {}).get("sites", 0) or 0)
        seen["rival sites"] += int((cst.get("overloads") or {}).get("sites", 0) or 0)
        seen["nested types"] += int((cst.get("lookup") or {}).get("nested_types", 0) or 0)
        seen["known dispatch sets"] += int(ds.get("known", 0) or 0)
        seen["static interface members"] += int(ds.get("static_member", 0) or 0)
        seen["generic bases"] += int(bool(prog.gen_over))
        nodes2, (out2, _st2) = run_program(prog, list(calls))
        g1 = sorted((a, str(b), (by[a].get("call_typed") or {}).get(b)) for a, b in out)
        by2 = {n["key"]: n for n in nodes2}
        g2 = sorted((a, str(b), (by2[a].get("call_typed") or {}).get(b)) for a, b in out2)
        if g1 != g2:
            viol["nondeterministic"].append(i)
        inp = list(calls)
        _n3, (out3, _s3) = run_program(prog, inp, env={})
        if out3 is not inp:
            viol["flag-off copy"].append(i)
        seen["programs"] += 1
    for k, v in viol.items():
        check(f"P {k}: none", not v, v[:4])
    check("P the fuzz exercised typed edges, implementer fan-outs, interface dispatch, removals and errored callers",
          seen["typed"] > 200 and seen["ambiguous"] > 10 and seen["dispatch sites"] > 20 and seen["removed"] > 20
          and seen["errored programs"] > 10, dict(seen))
    check("P ... and the review round's rules: virtual dispatch, argument rivals, nested type names",
          seen["virtual sites"] > 20 and seen["rival sites"] > 10 and seen["nested types"] > 10, dict(seen))
    check("P ... and the re-check round's: interface groups whose dispatch set is known (they may REPLACE), static "
          "interface members, overrides of a generic base's member",
          seen["known dispatch sets"] > 10 and seen["static interface members"] > 5 and seen["generic bases"] > 20,
          dict(seen))
    check("P ... and the fix round's planted shapes, each kind planted and judged often (an open-T base member beside "
          "a concrete overload, another signature in an override chain, a rebound constructed local, two external "
          "types of one simple name), the slot and rebind oracles on typed keys",
          all(seen[f"planted {k}"] > 60 for k in ("open", "slot", "rebind", "extname"))
          and seen["planted slot keyed"] > 20 and seen["planted rebind keyed"] > 20,
          {k: v for k, v in seen.items() if k.startswith("planted")})
    check("P ... and round 2's: an unconstrained / struct-constrained `T?` closed by a value type, one type of two "
          "spellings (nint / IntPtr, int? / Nullable<int>, `global using` aliases), a private or `new virtual` middle "
          "method, each planted and judged often, their reach oracles met on typed keys",
          all(seen[f"planted {k}"] > 60 for k in ("annot", "spell", "priv"))
          and seen["planted annot keyed"] > 20 and seen["planted spell keyed"] > 20 and seen["planted priv keyed"] > 10,
          {k: v for k, v in seen.items() if k.startswith("planted")})


# ================================================================================================
# M: byte mutations of the fixture tree
# ================================================================================================

def sec_mutations():
    print(f"\n-- M: {N} seeded byte mutations of the C# fixture sources --")
    try:
        import typed_calls_cs_gates as G   # noqa: E402 — the fixture trees
        trees = [G.CQRS] + [getattr(G, t) for t in ("REVIEW", "RECHECK", "SIGS", "RND2") if hasattr(G, t)]
    except Exception as exc:  # noqa: BLE001
        check("M the C# fixture tree reads (typed_calls_cs_gates.CQRS)", False, repr(exc))
        return
    sources = [(rel, text) for tree in trees for rel, text in tree.items() if rel.endswith(".cs")]
    rng = random.Random(SEED + 1)
    viol = {"raised": [], "invalid": [], "an error scope with a site": []}
    errored = 0
    junk = ["(", ")", "{", "}", "'", '"', "/", "*", ";", ".", "=>", "<", ">", "@", "$", "#if X\n", "\n", " ", "?"]
    rd = R.make_reader(TC.LANGUAGES["csharp"])
    for i in range(N):
        rel, text = rng.choice(sources)
        chars = list(text)
        for _ in range(rng.randint(1, 4)):
            pos = rng.randrange(len(chars) + 1)
            if rng.random() < 0.5 and chars:
                del chars[min(pos, len(chars) - 1)]
            else:
                chars.insert(pos, rng.choice(junk))
        mutated = "".join(chars)
        try:
            ff = rd.facts(rel, Src({rel: mutated}))
        except Exception as exc:  # noqa: BLE001
            viol["raised"].append((i, rel, repr(exc)))
            continue
        if ff is None or TC.validate_facts(ff):
            viol["invalid"].append((i, rel, ff and TC.validate_facts(ff)))
            continue
        data = mutated.encode("utf-8")
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        root = R.parse_bytes(data)
        err_lines = []
        stack = [root]
        while stack:
            x = stack.pop()
            if x.type == "ERROR" or x.is_missing:
                err_lines.append(x.start_point[0] + 1)
            stack.extend(x.children)
        if not err_lines:
            continue
        errored += 1
        for s in ff.scopes:
            if any(s.line < ln < s.end_line for ln in err_lines) and s.sites:
                viol["an error scope with a site"].append((i, rel, s.name, s.line))
    for k, v in viol.items():
        check(f"M {k}: none", not v, v[:4])
    check("M the mutations produced parse errors to judge", errored > N // 6, errored)


def main() -> int:
    print("typed_calls C# reader fuzz gates")
    if TC is None or R is None:
        check("build/typed_calls.py and build/receiver_facts_csharp.py import", False, _ERR)
    else:
        for fn in (sec_programs, sec_mutations):
            try:
                fn()
            except Exception:  # noqa: BLE001
                import traceback
                check(f"{fn.__name__} ran without crashing", False, traceback.format_exc()[-1500:])
    if _EMPTY:
        import shutil
        shutil.rmtree(_EMPTY.pop(), ignore_errors=True)
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
