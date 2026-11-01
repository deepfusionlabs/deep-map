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
     with an argument of the type parameter's type argument or through the derived class's own `T` (the base's member
     or nothing, never the derived overload); a same-named definition of another signature in the middle of an
     override chain or beside the override, through the base class or an interface (the override that runs is
     always reached: its key, or the engine's tuple kept); a constructed local rebound by a deconstruction (plain,
     nested, `var`), a ref local or a user-defined `++` (the subclass's override reached, its member never
     EXTRACTED); two external types
     of one simple name (never typed to the derived overload of the other type); and round 2's: a generic class's
     `T?` (unconstrained: an annotation; `where T : struct`: Nullable<T>) closed by int / a struct / an enum (the
     closer's override reached, the subclass closing it with another type argument never keyed), one type of two
     spellings in an override or an interface implementer (nint / System.IntPtr, int? / System.Nullable<int>, a
     `global using` alias of CancellationToken or of a project class: the other spelling's definition reached, the
     base class's definition of the interface member's spelling never keyed), a private (or access-less) or
     `new virtual` same-signature middle method (the subclass's override reached, or never keyed); and round 3's,
     some in .csproj groups of their own (a project file, a Directory.Build.props / .targets, written to a
     per-program tree under the fuzz's temp directory, the root the pass links with): (a) `levels` — an interface
     member and its implementer importing System.Threading at other levels (the compilation unit, inside the
     namespace, MSBuild's implicit usings from the project or Directory.Build.props, turned off again by a
     Directory.Build.targets) or spelling it qualified (the implementer reached), and the Timer pair: an implementer
     whose `Timer` is System.Threading's through its project's implicit usings, beside an interface whose `Timer` is
     System.Timers' in a project without System.Threading (no implicit usings, a `<Using Remove>`, an ImplicitUsings
     under a condition the pass cannot read) — an overload, never keyed, while the public base member that implements
     the interface is reached; (b) `alias` — a compilation unit's alias beaten by a namespace block's using of a
     project namespace, a block's alias beating the compilation unit's using, a block's alias seen from a sibling
     block (never keyed to the alias target where C# binds elsewhere; the binding reached); (d) `explicit` — an
     explicit implementation runs for the subclass overriding its public twin and an explicit implementation of
     ANOTHER interface implements nothing (the explicit implementation's class node reached, the override / the
     other interface's implementer never keyed); (e) `private` — a non-public same-signature method implements no
     interface member (never keyed; the public base member reached), a `public new` one does; (f) `nullable` — a
     closed `int?` / struct `?` type argument is Nullable (the subclass closing with `int` / the struct never keyed,
     the one closing with the nullable reached), a class's `?` and an unconstrained `T?` are annotations (every such
     closer reached); (g) `subst` — overload resolution on the receiver's constructed type: the derived class's
     substituted overload binds when it applies (a keyword widening, a user-defined implicit conversion), the base's
     when it does not (another class, a keyword without a conversion, an explicit conversion): the binding or
     nothing ("reach_or_nothing": a key to it, or no key of that name and the engine's tuple kept), never the
     other overload; beside it, from a generator of its own, `access` — a base overload the call site may not see
     (private, protected, private protected, no modifier) is skipped by C# and never keyed (the root's Ax(object)
     or nothing), an accessible one (public, internal, protected internal) is bound. And N // 2 programs of the
     soundness round's wrong-True classes alone (Program.build_sound, a generator of their own, every shape in .csproj
     groups of its own, each proof beside the same shape proven): `projns` a project type in a capable namespace (its
     external member beats a using and a compilation unit's alias), `pkg` a namespace a package shares (the id's first
     segment, from a PackageReference, a <Reference>, a packages.config, a Directory.Packages.props, another group),
     `xalias` an x tuple (one alias name of two targets, one generic target text in two blocks, two projects'
     unreadable <Using Alias>, two blocks' classes of one name), `mlines` white space around ImplicitUsings, `wildrm`
     a wildcard <Using Remove>, `linked` a file another project compiles (no project's directory, another project's,
     a .projitems, two files of no project): the type's real implementer reached, the class that only overloads it
     never keyed. And N // 2 programs of the last round's wrong-True classes (Program.build_final, a generator of their
     own, every shape in .csproj groups whose AssemblyName owns the program's namespace root): `arity` a method's own
     generic arity and a `params` array in an interface mapping and a virtual slot, `ustatic` a block's `using static`
     whose type nests the name (a project's, a package's; field types too), `ifuse` a using, alias or `global using`
     inside an #if / #elif / #else branch, `cremove` a <Compile Remove> (also through a path property) and default
     items off taking a `global using` out, `xblock` another namespace declaration's class in the same file, `filetype`
     a `file` type, `blkuse` another declaration's using (a sibling block, an enclosing namespace declared apart),
     `garity` a type name's generic arity against a package's type, `pkgpre` a package id whose first segment starts
     with or is a prefix of the root, and a root no project owns: the C# binding reached, the other member never keyed,
     each with its proven sibling. And N // 2 programs of the closing round's wrong-True classes (Program.build_close, a
     generator of their own, the same owned .csproj groups): `unres` an UNRESOLVED base (an alias of a root no project
     owns naming the interface itself, a project base behind a namespace declaration's non-transparent using, a generic
     `TiBase<int>`, a qualifier of an unowned root) under interface and virtual dispatch (the implementer / override
     that runs reached, never removed) and in the overload rival walk (the derived overload of another parameter type
     never keyed, the base's member reached), and `relusing` a using or alias target written inside a namespace
     declaration (relative to it, after a file-scoped namespace, in the same file, `global::`, the compilation unit's
     absolute, two blocks' `using Kj;` meaning two namespaces or one): the C# binding reached, the other never keyed,
     each with its proven sibling. Each kind must be planted and judged often enough. Since that round (R32) a root is
     transparent only when a project OWNS it: every .csproj of the older families that names no AssemblyName gets one
     under the program's root (Program.own_roots; an AssemblyName changes no binding), so their proofs stay
     reachable; a program with no .csproj at all owns no root, and its usings stay unknown. The pass reads a file only
     through its nodes here (the sources stay in memory): a planted file that must be read (a removed file's `global
     using`, a `using static` holder) carries a type with the engine's node.
  M  300 seeded byte mutations of the C# fixture trees' sources (tests/typed_calls_cs_gates TREES: CQRS, REVIEW,
     RECHECK, SIGS, RND2 and every tree added there since):
     the reader never raises and every FileFacts validates; a scope whose span holds a parse error keeps no site.

Usage: python tests/typed_calls_cs_fuzz_gates.py [--seed N] [--n N] [--root <tree>]
       (--root: load build/ from another checkout, the RED-first run)
"""
from __future__ import annotations

import os
import random
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
    os.environ.pop(_k, None)

SEED = 20261028
N = 300
ROOT = REPO                       # the checkout whose build/ is fuzzed (--root: the unfixed tree, the RED-first run)
_args = sys.argv[1:] if __name__ == "__main__" else []
for _i, _a in enumerate(_args):
    if _a == "--seed" and _i + 1 < len(_args):
        SEED = int(_args[_i + 1])
    if _a == "--n" and _i + 1 < len(_args):
        N = int(_args[_i + 1])
    if _a == "--root" or _a.startswith("--root="):
        _r = _a[len("--root="):] if _a.startswith("--root=") else (_args[_i + 1] if _i + 1 < len(_args) else "")
        if not _r or not (Path(_r) / "build").is_dir():
            print(f"usage: python tests/typed_calls_cs_fuzz_gates.py [--seed N] [--n N] [--root <tree>]  "
                  f"(--root needs a directory that holds build/, got {_r!r})", file=sys.stderr)
            raise SystemExit(2)
        ROOT = Path(_r).resolve()
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(REPO / "tests"))

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
# the planted kinds: the fix round's, round 2's, round 3's (the reader residuals (a) levels, (b) alias, (d) explicit,
# (e) private, (f) nullable, (g) subst)
KINDS_R1 = ("open", "slot", "rebind", "extname")
KINDS_R2 = ("annot", "spell", "priv")
KINDS_R3 = ("levels", "alias", "explicit", "private", "nullable", "subst")
KINDS = KINDS_R1 + KINDS_R2 + KINDS_R3
# the planting draw: levels and alias twice (their shapes are several variants over several files / .csproj groups),
# extname twice (one oracle per shape)
DRAW = KINDS + ("levels", "alias", "extname")
# the soundness round's wrong-True classes: a family of programs of their own (Program.build_sound, drawn from their own
# generator, so the main programs above are the same programs): projns (a project type in a capable namespace), pkg (a
# namespace a package shares), xalias (x tuples), mlines (a multi-line ImplicitUsings), wildrm (a wildcard <Using
# Remove>), linked (a file another project compiles)
KINDS_R4 = ("projns", "pkg", "xalias", "mlines", "wildrm", "linked")
# the last soundness round's wrong-True classes (W4: R22-R32): a family of programs of their own (Program.build_final,
# its own generator): arity (a method's own generic arity, a `params` array), ustatic (`using static` in a block),
# ifuse (a directive in an #if branch), cremove (<Compile Remove> / default items off), xblock (another namespace
# declaration's class in the same file), filetype (a `file` type), blkuse (another declaration's using), garity (a
# type name's generic arity), pkgpre (a package id sharing the namespace root by prefix, a root no project owns)
KINDS_R5 = ("arity", "ustatic", "ifuse", "cremove", "xblock", "filetype", "blkuse", "garity", "pkgpre")
# the closing round's wrong-True classes (W5: R42 / R44): a family of programs of its own (Program.build_close, its own
# generator): unres (an unresolved base under interface and virtual dispatch, and in the overload rival walk),
# relusing (a using or alias target written inside a namespace declaration)
KINDS_R6 = ("unres", "relusing")


def _msb(props=(), items=(), sdk="") -> str:
    """An MSBuild file: a Directory.Build.props / .targets, or with `sdk` a project."""
    out = [f'<Project Sdk="{sdk}">' if sdk else "<Project>"]
    if props:
        out += ["  <PropertyGroup>"] + [f"    {p}" for p in props] + ["  </PropertyGroup>"]
    if items:
        out += ["  <ItemGroup>"] + [f"    {i}" for i in items] + ["  </ItemGroup>"]
    return "\n".join(out + ["</Project>"]) + "\n"


def _csproj(props=(), items=()) -> str:
    return _msb(["<TargetFramework>net8.0</TargetFramework>"] + list(props), items, sdk="Microsoft.NET.Sdk")


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
        self.pfiles: dict = {}            # round 3: planted source files of their own, rel -> lines
        self.disk: dict = {}              # round 3: MSBuild files (rel -> text) written to the program's tree on disk
        self.member_lines: dict = {}      # (rel, line) of every planted member definition -> its node key
        self.root = None                  # the program's tree on disk (write_tree), None: the empty root
        self.vend: dict = {}              # the soundness round's unread assemblies, rel -> text: never given to the
                                          # pass, only to the Roslyn validation (a stand-in for a NuGet package)

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
        for u in range(rng.randint(2, 7)):
            kind = rng.choice(DRAW)
            getattr(self, f"plant_{kind}")(lines, rel, u)
        self.files[rel] = "\n".join(lines) + "\n"
        if self.globs:
            self.files["src/PlantGlob.cs"] = "\n".join(self.globs) + "\n"
        for r, ls in self.pfiles.items():
            self.files[r] = "\n".join(ls) + "\n"

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
            if mname is not None:
                self.member_lines[(rel, first)] = keys[mname]
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
        """D1: GS<T>.Put(T) beside GD<T>.Put(PlC): an argument of GD<PlA>'s type argument binds the base — the base's
        member or nothing, never the derived overload (no key to GD.Put; GS.Put keyed, or no key of Put and the
        engine's tuple kept); inside GD<T>, `Put(x)` with x : T binds the base too (the same oracle); an argument of
        PlC fits the derived overload, which C# then binds (the base's candidates dropped): GD.Put or nothing, beside a
        rival (never EXTRACTED). A virtual Keep(T) overridden as Keep(T) is the same slot, sometimes."""
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
        self.planted.append(("open", "reach_or_nothing", callers[f"Go{u}"], put, keys["base"][put]))
        self.planted.append(("open", "reach_or_nothing", sk, put, keys["base"][put]))
        self.planted.append(("open", "reach_or_nothing", callers[f"GoC{u}"], put, keys["der"][put]))
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

    def _user(self, lines, rel, name, field, call, member, params=""):
        """`public class name { private readonly field; public void Go(params) { call } }`: a planted caller of
        member."""
        lines.append(f"public class {name}")
        self.node("Class", name, rel, len(lines))
        lines.append("{")
        lines.append(f"    private readonly {field};")
        lines.append(f"    public void Go({params})")
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

    # ------------------------------------------------------------------ round 3 (the reader residuals (a)-(g))
    def _src(self, rel) -> list:
        """The lines of a planted source file of its own (joined into self.files at the end of plant())."""
        return self.pfiles.setdefault(rel, [])

    @staticmethod
    def _ns_open(lines, ns, inner, style):
        """A namespace declaration, file-scoped ("fs") or a block ("blk"), with `inner` usings inside it."""
        lines.extend([f"namespace {ns}", "{"] if style == "blk" else [f"namespace {ns};"])
        lines.extend(f"using {x};" for x in inner)
        lines.append("")

    @staticmethod
    def _ns_close(lines, style):
        if style == "blk":
            lines.append("}")

    def plant_levels(self, lines, rel, u):
        """Round 3, (a): MSBuild implicit usings and usings at other levels, each shape in .csproj groups of its own
        (self.disk). PROOF: ILv.Lp(CancellationToken) and its one implementer LvImpl, each file importing
        System.Threading at another level (the compilation unit; inside the namespace, file-scoped or a block; none,
        through MSBuild's implicit usings, which the project or its Directory.Build.props turns on and a
        Directory.Build.targets may turn off again — last assignment wins — so that each file then writes its own) or
        spelling it qualified: an object of LvImpl runs LvImpl.Lp, which is reached. TIMER: ITk.Tk(Timer) under `using
        System.Timers;` in project A, where System.Threading is no using (no implicit usings, a `<Using Remove>` in the
        project or its Directory.Build.targets, an ImplicitUsings under a condition the pass cannot read but MSBuild
        evaluates false: an undefined property, Exists() of a missing file), TkBase.Tk(System.Timers.Timer) public
        beside it; in project B (which references A) TkImpl : TkBase, ITk declares Tk(Timer) with Timer
        System.Threading's (implicit usings — from the project, its Directory.Build.props, or under a condition MSBuild
        evaluates true but the pass cannot read, Exists() of the project file — or a written using): an overload, so
        ITk.Tk maps to the public TkBase.Tk —
        TkImpl.Tk never keyed, TkBase.Tk reached (the engine's tuple kept when not keyed: never removed). When TkImpl
        spells System.Timers.Timer (`public new`), it is the implementation: reached, TkBase.Tk never keyed."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        if rng.random() < 0.4:
            g, ns, lp = f"src/Lv{tag}", f"Fz{self.idx}.Lv{u}", f"Lp{u}"
            how = rng.choice(["csproj", "props", "targets_off", "off"])
            props = ["<ImplicitUsings>enable</ImplicitUsings>"] if how == "csproj" else (
                ["<ImplicitUsings>disable</ImplicitUsings>"] if how == "off" and rng.random() < 0.5 else [])
            self.disk[f"{g}/Lv{tag}.csproj"] = _csproj(props)
            if how in ("props", "targets_off"):
                self.disk[f"{g}/Directory.Build.props"] = _msb(["<ImplicitUsings>enable</ImplicitUsings>"])
            if how == "targets_off":
                self.disk[f"{g}/Directory.Build.targets"] = _msb(["<ImplicitUsings>disable</ImplicitUsings>"])
            places = (["none"] if how in ("csproj", "props") else []) + ["top", "inner"]
            ip, pp = rng.choice(places), rng.choice(places + ["qual"])
            r1 = f"{g}/Contracts/ILv{u}.cs"
            ls = self._src(r1)
            if ip == "top":
                ls.append("using System.Threading;")
            st = rng.choice(["fs", "blk"])
            self._ns_open(ls, f"{ns}.Contracts", ["System.Threading"] if ip == "inner" else [], st)
            self._type(ls, r1, f"public interface ILv{u}", [(lp, f"void {lp}(CancellationToken ct)", [])], iface=True)
            self._ns_close(ls, st)
            r2 = f"{g}/Impl/LvImpl{u}.cs"
            ls = self._src(r2)
            cu = ["System.Threading"] if pp == "top" else []
            inner = ["System.Threading"] if pp == "inner" else []
            (cu if rng.random() < 0.5 else inner).append(f"{ns}.Contracts")
            rng.shuffle(cu)
            rng.shuffle(inner)
            ls.extend(f"using {x};" for x in cu)
            st = rng.choice(["fs", "blk"])
            self._ns_open(ls, f"{ns}.Impl", inner, st)
            tok = "System.Threading.CancellationToken" if pp == "qual" else "CancellationToken"
            im = self._type(ls, r2, f"public class LvImpl{u} : ILv{u}", [(lp, f"public void {lp}({tok} ct)", [])])
            self._ns_close(ls, st)
            r3 = f"{g}/Use/LvUser{u}.cs"
            ls = self._src(r3)
            ls.append(f"using {ns}.Contracts;")
            self._ns_open(ls, f"{ns}.Use", [], "fs")
            k = self._user(ls, r3, f"LvUser{u}", f"ILv{u} _p", f"_p.{lp}(default);", lp)
            self.planted.append(("levels", "reach", k, lp, im[lp]))
            return
        ga, gb = f"src/LtA{tag}", f"src/LtB{tag}"
        nsa, nsb, tk = f"Fz{self.idx}.LtA{u}", f"Fz{self.idx}.LtB{u}", f"Tk{u}"
        a_how = rng.choice(["plain", "remove", "remove_targets", "cond", "exists"])
        props, items = [], []
        if a_how == "plain" and rng.random() < 0.5:
            props.append("<ImplicitUsings>disable</ImplicitUsings>")
        if a_how.startswith("remove"):
            props.append("<ImplicitUsings>enable</ImplicitUsings>")
            if a_how == "remove":
                items.append('<Using Remove="System.Threading" />')
            else:
                self.disk[f"{ga}/Directory.Build.targets"] = _msb([], ['<Using Remove="System.Threading" />'])
        if a_how == "cond":
            props.append("<ImplicitUsings Condition=\"'$(DeepMapFuzzOn)' == 'true'\">enable</ImplicitUsings>")
        if a_how == "exists":
            props.append("<ImplicitUsings Condition=\"Exists('dm-fuzz-absent.txt')\">enable</ImplicitUsings>")
        self.disk[f"{ga}/LtA{tag}.csproj"] = _csproj(props, items)
        b_src = rng.choice(["csproj", "props", "csproj", "props", "exists", "using_top", "using_inner"])
        if b_src == "props":
            self.disk[f"{gb}/Directory.Build.props"] = _msb(["<ImplicitUsings>enable</ImplicitUsings>"])
        bprops = {"csproj": ["<ImplicitUsings>enable</ImplicitUsings>"],    # Exists() of the project file itself:
                  "exists": [f"<ImplicitUsings Condition=\"Exists('LtB{tag}.csproj')\">enable</ImplicitUsings>"]}
        self.disk[f"{gb}/LtB{tag}.csproj"] = _csproj(       # true for MSBuild (relative to the project's directory)
            bprops.get(b_src, []), [f'<ProjectReference Include="..\\LtA{tag}\\LtA{tag}.csproj" />'])
        same = rng.random() < 0.25
        ra = f"{ga}/Tick{u}.cs"
        ls = self._src(ra)
        a_top = rng.random() < 0.7
        ls.extend(["using System.Timers;"] if a_top else [])
        self._ns_open(ls, nsa, [] if a_top else ["System.Timers"], "fs")
        self._type(ls, ra, f"public interface ITk{u}", [(tk, f"void {tk}(Timer t)", [])], iface=True)
        tb = self._type(ls, ra, f"public class TkBase{u}",
                        [(tk, f"public void {tk}({rng.choice(['Timer', 'System.Timers.Timer'])} t)", [])])
        k = self._user(ls, ra, f"TkUser{u}", f"ITk{u} _t", f"_t.{tk}(x);", tk, params="System.Timers.Timer x")
        rb = f"{gb}/Ticker{u}.cs"
        ls = self._src(rb)
        ref = rng.choice(["inner", "inner", "top", "qual", "qual"])
        cu = (["System.Threading"] if b_src == "using_top" else []) + ([nsa] if ref == "top" else [])
        inner = (["System.Threading"] if b_src == "using_inner" else []) + ([nsa] if ref == "inner" else [])
        ls.extend(f"using {x};" for x in cu)
        st = rng.choice(["fs", "blk"])
        self._ns_open(ls, nsb, inner, st)
        q = f"{nsa}." if ref == "qual" else ""
        sig = f"public new void {tk}(System.Timers.Timer t)" if same else f"public void {tk}(Timer t)"
        ti = self._type(ls, rb, f"public class TkImpl{u} : {q}TkBase{u}, {q}ITk{u}", [(tk, sig, [])])
        self._ns_close(ls, st)
        self.planted.append(("levels", "reach", k, tk, (ti if same else tb)[tk]))
        self.planted.append(("levels", "no_key", k, tk, (tb if same else ti)[tk]))

    def plant_alias(self, lines, rel, u):
        """Round 3, (b): C# looks a simple name up per namespace declaration, innermost out (that declaration's
        members, then its aliases, then its using-namespaces), the compilation unit last. NEAR: a compilation-unit
        alias `using Ord = ...Legacy.Ord;` loses to a namespace's `using ...Models;` holding an Ord (Models' Ord.Sh
        binds: no key to Legacy's); PLAIN: without that using the alias binds (no key to Models'); INNER: a
        namespace's alias beats the compilation unit's using of Models (no key to Models'); GLOB: a `global using`
        alias of the project (src/PlantGlob.cs) sits at every file's compilation-unit level, so a namespace's using
        of Models beats it too (no key to Legacy's); SIB: a block's alias applies only inside its block — the
        sibling block's Rp is its own namespace's class (no key to the alias target from there, nor to that class
        from the aliasing block). Each binding is reached (a key, or the engine's tuple kept)."""
        rng = self.rng
        g, ns = f"src/Al{self.idx}x{u}", f"Fz{self.idx}.Al{u}"
        shape = rng.choice(["near", "plain", "inner", "glob", "sib"])
        if shape != "sib":
            od, sh = (f"Og{self.idx}x{u}" if shape == "glob" else f"Ord{u}"), f"Sh{u}"   # a global alias: unique
            got = {}
            for part in ("Models", "Legacy"):
                r = f"{g}/{part}/{od}.cs"
                ls = self._src(r)
                self._ns_open(ls, f"{ns}.{part}", [], "fs")
                got[part] = self._type(ls, r, f"public class {od}", [(sh, f"public void {sh}()", [])])
            alias, models = f"{od} = {ns}.Legacy.{od}", f"{ns}.Models"
            if shape == "glob":
                self.globs.append(f"global using {alias};")
            cu, inner = {"near": ([alias], [models]), "plain": ([alias], []), "inner": ([models], [alias]),
                         "glob": ([], [models])}[shape]
            r = f"{g}/Use/{od}User.cs"
            ls = self._src(r)
            ls.extend(f"using {x};" for x in cu)
            st = rng.choice(["fs", "blk"])
            self._ns_open(ls, f"{ns}.Use", inner, st)
            k = self._user(ls, r, f"{od}User{u}", f"{od} _o", f"_o.{sh}();", sh)
            self._ns_close(ls, st)
            bound, other = ("Models", "Legacy") if shape in ("near", "glob") else ("Legacy", "Models")
            self.planted.append(("alias", "reach", k, sh, got[bound][sh]))
            self.planted.append(("alias", "no_key", k, sh, got[other][sh]))
            return
        rp, sa = f"Rp{u}", f"Sa{u}"
        r = f"{g}/Data/SqlRp{u}.cs"
        ls = self._src(r)
        self._ns_open(ls, f"{ns}.Data", [], "fs")
        sql = self._type(ls, r, f"public class SqlRp{u}", [(sa, f"public void {sa}()", [])])
        rb = f"{g}/Blocks{u}.cs"
        own_file = rng.random() < 0.5                 # B's Rp in a file of its own, else in block B
        blocks = ["A", "B"]
        rng.shuffle(blocks)
        ls = self._src(rb)
        keys = {}
        for blk in blocks:
            self._ns_open(ls, f"{ns}.{blk}", [f"{rp} = {ns}.Data.SqlRp{u}"] if blk == "A" else [], "blk")
            if blk == "B" and not own_file:
                keys["rp"] = self._type(ls, rb, f"public class {rp}", [(sa, f"public void {sa}()", [])])
            keys[blk] = self._user(ls, rb, f"U{blk}{u}", f"{rp} _r", f"_r.{sa}();", sa)
            self._ns_close(ls, "blk")
        if own_file:
            r = f"{g}/B/{rp}.cs"
            ls = self._src(r)
            self._ns_open(ls, f"{ns}.B", [], "fs")
            keys["rp"] = self._type(ls, r, f"public class {rp}", [(sa, f"public void {sa}()", [])])
        self.planted.append(("alias", "reach", keys["A"], sa, sql[sa]))
        self.planted.append(("alias", "no_key", keys["A"], sa, keys["rp"][sa]))
        self.planted.append(("alias", "reach", keys["B"], sa, keys["rp"][sa]))
        self.planted.append(("alias", "no_key", keys["B"], sa, sql[sa]))

    def plant_explicit(self, lines, rel, u):
        """Round 3, (d): C# maps an interface member to the implementing class's EXPLICIT implementation first (C#
        spec 18.6.5), which nothing can override, and an explicit implementation of ANOTHER interface implements
        nothing for this one. D1: RdA : IRd { void IRd.Rn(); public virtual void Rn(); } (either order) and RdB : RdA
        overriding the public Rn (or hiding a non-virtual one with `new`): an object of either class runs RdA's
        explicit implementation (RdA.Rn reached, RdB.Rn never keyed). D3: RdC : RbB, IRd, IOt declaring only `void
        IOt.Rn()`: IRd.Rn is RbB's public Rn (reached; RdC.Rn never keyed). OWN: RdE : IRd { void IRd.Rn(); } is
        reached; its subclass RdF's public Rn is never keyed, unless RdF lists IRd again (a re-implementation: then
        RdF.Rn is reached too). The specifier is written plainly or qualified. Dispatch: never EXTRACTED."""
        rng = self.rng
        rn, ird = f"Rn{u}", f"IRd{u}"
        spec = rng.choice([ird, f"Fz{self.idx}.Pl.{ird}"])
        self._type(lines, rel, f"public interface {ird}", [(rn, f"void {rn}()", [])], iface=True)
        shape = rng.choice(["d1", "d3", "own"])
        if shape == "d1":
            virt = rng.random() < 0.6
            ex, pb = (rn, f"void {spec}.{rn}()", []), (rn, f"public {'virtual ' if virt else ''}void {rn}()", [])
            a = self._type(lines, rel, f"public class RdA{u} : {ird}", [ex, pb] if rng.random() < 0.5 else [pb, ex])
            b = self._type(lines, rel, f"public class RdB{u} : RdA{u}",
                           [(rn, f"public {'override' if virt else 'new'} void {rn}()", [])])
            k = self._user(lines, rel, f"RdU{u}", f"{ird} _r", f"_r.{rn}();", rn)
            self.planted += [("explicit", "reach", k, rn, a[rn]), ("explicit", "no_key", k, rn, b[rn]),
                             ("explicit", "never_E", k, rn, None)]
        elif shape == "d3":
            iot = f"IOt{u}"
            self._type(lines, rel, f"public interface {iot}", [(rn, f"void {rn}()", [])], iface=True)
            bb = self._type(lines, rel, f"public class RbB{u}", [(rn, f"public void {rn}()", [])])
            ot = rng.choice([iot, f"Fz{self.idx}.Pl.{iot}"])
            c = self._type(lines, rel, f"public class RdC{u} : RbB{u}, {ird}, {iot}", [(rn, f"void {ot}.{rn}()", [])])
            k = self._user(lines, rel, f"RdU{u}", f"{ird} _r", f"_r.{rn}();", rn)
            self.planted += [("explicit", "reach", k, rn, bb[rn]), ("explicit", "no_key", k, rn, c[rn])]
        else:
            relist = rng.random() < 0.4
            e = self._type(lines, rel, f"public class RdE{u} : {ird}", [(rn, f"void {spec}.{rn}()", [])])
            f = self._type(lines, rel, f"public class RdF{u} : RdE{u}" + (f", {ird}" if relist else ""),
                           [(rn, f"public void {rn}()", [])])
            k = self._user(lines, rel, f"RdU{u}", f"{ird} _r", f"_r.{rn}();", rn)
            self.planted += [("explicit", "reach", k, rn, e[rn]),
                             ("explicit", "reach" if relist else "no_key", k, rn, f[rn])]

    def plant_private(self, lines, rel, u):
        """Round 3, (e): C# maps an interface member implicitly only to a PUBLIC member (C# spec 18.6.5). E1: PrI : PbB,
        IPr redefines Pr() private / internal / protected / without an access modifier (all `new`): IPr.Pr is PbB's
        public Pr (reached), PrI.Pr never keyed; E2: a `public new` Pr is the implementation (reached; PbB.Pr, whose
        class implements nothing, never keyed); E3: beside a default interface member `void Pr() { }`, PrO's private
        (or access-less) Pr implements nothing: the interface's own body runs (PrO.Pr never keyed)."""
        rng = self.rng
        pr, ipr = f"Pr{u}", f"IPr{u}"
        shape = rng.choice(["e1", "e1", "e2", "e3"])
        if shape == "e3":
            self._type(lines, rel, f"public interface {ipr}", [(pr, f"void {pr}()", [])])
            o = self._type(lines, rel, f"public class PrO{u} : {ipr}",
                           [(pr, f"{rng.choice(['private ', ''])}void {pr}()", [])])
            k = self._user(lines, rel, f"PrU{u}", f"{ipr} _p", f"_p.{pr}();", pr)
            self.planted.append(("private", "no_key", k, pr, o[pr]))
            return
        self._type(lines, rel, f"public interface {ipr}", [(pr, f"void {pr}()", [])], iface=True)
        b = self._type(lines, rel, f"public class PbB{u}", [(pr, f"public void {pr}()", [])])
        mod = "public new" if shape == "e2" else rng.choice(["private new", "new", "internal new", "protected new"])
        i = self._type(lines, rel, f"public class PrI{u} : PbB{u}, {ipr}", [(pr, f"{mod} void {pr}()", [])])
        k = self._user(lines, rel, f"PrU{u}", f"{ipr} _p", f"_p.{pr}();", pr)
        bound, other = (i, b) if shape == "e2" else (b, i)
        self.planted.append(("private", "reach", k, pr, bound[pr]))
        self.planted.append(("private", "no_key", k, pr, other[pr]))

    def plant_nullable(self, lines, rel, u):
        """Round 3, (f): a nullable closed type argument. `Cv<int?>` is Cv<System.Nullable<int>>, never a `: Cv<int>`
        (no key to CvI's override) while `: Cv<int?>` runs (reached); through an interface IRp<int?> the same (IntRp
        never keyed, NulRp reached); a class's `?` is an annotation (`Cv<NlK?>`: `: Cv<NlK>` reached, `: Cv<int>` never
        keyed); a struct's / an enum's is Nullable (`Cv<NlS?>`: `: Cv<NlS>` never keyed, `: Cv<NlS?>` reached); an
        unconstrained `T?` inside a generic class is T (every closer reached). Dispatch: never EXTRACTED."""
        rng = self.rng
        mv = f"Mv{u}"
        shape = rng.choice(["virt", "iface", "cls", "struct", "open"])
        kw = rng.choice(["int", "long", "bool", "double", "char"])
        if shape == "iface":
            gt, irp = f"Gt{u}", f"IRp{u}"
            self._type(lines, rel, f"public interface {irp}<T>", [(gt, f"T {gt}()", [])], iface=True)
            a = self._type(lines, rel, f"public class IntRp{u} : {irp}<{kw}>",
                           [(gt, f"public {kw} {gt}()", ["return default;"])])
            n = self._type(lines, rel, f"public class NulRp{u} : {irp}<{kw}?>",
                           [(gt, f"public {kw}? {gt}()", ["return null;"])])
            k = self._user(lines, rel, f"NlU{u}", f"{irp}<{kw}?> _r", f"var v = _r.{gt}();", gt)
            self.planted += [("nullable", "reach", k, gt, n[gt]), ("nullable", "no_key", k, gt, a[gt]),
                             ("nullable", "never_E", k, gt, None)]
            return
        if shape in ("virt", "open"):
            plain, nul = kw, f"{kw}?"
        elif shape == "cls":
            self._type(lines, rel, f"public class NlK{u}", [])
            plain, nul = f"NlK{u}", f"NlK{u}?"
        else:
            self._type(lines, rel, rng.choice([f"public struct NlS{u}", f"public enum NlS{u}"]), [])
            plain, nul = f"NlS{u}", f"NlS{u}?"
        self._type(lines, rel, f"public class Cv{u}<T>", [(mv, f"public virtual void {mv}()", [])])
        p = self._type(lines, rel, f"public class CvP{u} : Cv{u}<{plain}>", [(mv, f"public override void {mv}()", [])])
        n = self._type(lines, rel, f"public class CvN{u} : Cv{u}<{nul}>", [(mv, f"public override void {mv}()", [])])
        if shape == "open":
            lines.append(f"public class Hd{u}<T>")
            self.node("Class", f"Hd{u}", rel, len(lines))
            lines += ["{", f"    private readonly Cv{u}<T?> _c;", "    public void Go()"]
            k = self.node("Method", f"Hd{u}.Go", rel, len(lines))
            lines += ["    {", f"        _c.{mv}();", "    }"]
            self.spans[k] = (len(lines) - 3, len(lines))
            lines += ["}", ""]
            self._caller(k, mv)
            self.planted += [("nullable", "reach", k, mv, p[mv]), ("nullable", "reach", k, mv, n[mv])]
            return
        o = self._type(lines, rel, f"public class CvO{u} : Cv{u}<{'string' if shape == 'cls' else 'decimal'}>",
                       [(mv, f"public override void {mv}()", [])]) if rng.random() < 0.5 else None
        k = self._user(lines, rel, f"NlU{u}", f"Cv{u}<{nul}> _c", f"_c.{mv}();", mv)
        self.planted += [("nullable", "reach", k, mv, n[mv]), ("nullable", "never_E", k, mv, None),
                         ("nullable", "reach" if shape == "cls" else "no_key", k, mv, p[mv])]
        if o is not None:
            self.planted.append(("nullable", "no_key", k, mv, o[mv]))

    def plant_subst(self, lines, rel, u):
        """Round 3, (g): overload resolution on the receiver's constructed type — C# drops a base class's candidates
        once a more derived class's applies, whatever the base's fit. FIT: SbD<X>.Sv(T) applies to the argument (X its
        class, its keyword, or a keyword it widens to: int -> long) and binds (SbB.Sv never keyed); NOFIT: SbD<Y>.Sv(T)
        cannot take it (another class, a keyword without an implicit conversion: short, string, bool) and SbB.Sv binds
        (SbD.Sv never keyed); GEN: StD<T>.Sv(PlCt) beside StB<T>.Sv(T), called with the type argument's class, binds
        StB.Sv; CONV: a user-defined IMPLICIT conversion to PlCt (declared by either class) makes StD's applicable (it
        binds; StB.Sv never keyed), an EXPLICIT one does not (StB.Sv binds; StD.Sv never keyed). The binding: a key to
        it, or no key of that name and the engine's tuple kept ("reach_or_nothing")."""
        rng = self.rng
        sv = f"Sv{u}"
        shape = rng.choice(["fit", "nofit", "gen", "conv"])
        if shape in ("fit", "nofit"):
            if rng.random() < 0.4:
                bt = "int"
                xt = rng.choice(["int", "long"]) if shape == "fit" else rng.choice(["short", "string", "bool"])
            else:
                bt, ct = f"PlOr{u}", f"PlCt{u}"
                self._type(lines, rel, f"public class {bt}", [])
                self._type(lines, rel, f"public class {ct}", [])
                xt = bt if shape == "fit" else ct
            b = self._type(lines, rel, f"public class SbB{u}", [(sv, f"public void {sv}({bt} o)", [])])
            d = self._type(lines, rel, f"public class SbD{u}<T> : SbB{u}", [(sv, f"public void {sv}(T item)", [])])
            k = self._user(lines, rel, f"SbU{u}", f"SbD{u}<{xt}> _r", f"_r.{sv}(o);", sv, params=f"{bt} o")
            bound, other = (d, b) if shape == "fit" else (b, d)
        else:
            ct, ot = f"PlCt{u}", (f"PlOr{u}" if shape == "gen" else f"PlOc{u}")
            implicit = rng.random() < 0.6
            op = [(None, f"public static {'implicit' if implicit else 'explicit'} operator {ct}({ot} o)",
                   [f"return new {ct}();"])] if shape == "conv" else []
            in_target = rng.random() < 0.5
            self._type(lines, rel, f"public class {ct}", op if in_target else [])
            self._type(lines, rel, f"public class {ot}", [] if in_target else op)
            b = self._type(lines, rel, f"public class StB{u}<T>", [(sv, f"public void {sv}(T item)", [])])
            d = self._type(lines, rel, f"public class StD{u}<T> : StB{u}<T>", [(sv, f"public void {sv}({ct} c)", [])])
            k = self._user(lines, rel, f"StU{u}", f"StD{u}<{ot}> _s", f"_s.{sv}(o);", sv, params=f"{ot} o")
            bound, other = (d, b) if shape == "conv" and implicit else (b, d)
        self.planted.append(("subst", "reach_or_nothing", k, sv, bound[sv]))
        self.planted.append(("subst", "no_key", k, sv, other[sv]))
        # the integration round's access shape, drawn from its OWN generator (seeded by the run's seed, the program and
        # u), so every other planted shape of the run, and each kind's calibrated share, stays as it was
        arng = random.Random(f"access-{SEED}-{self.idx}-{u}")
        if arng.random() < 0.5:
            self.plant_access(lines, rel, u, arng)

    def plant_access(self, lines, rel, u, arng):
        """The integration round, (g2) and accessibility: C#'s member lookup keeps ACCESSIBLE members only (spec
        12.5). AxD{u}.Ax(PlCx) cannot take a PlOx argument; AxH{u}, between it and AxR{u}, declares Ax(PlOx) with an
        access modifier drawn at random: a public / internal / protected internal one binds (a fuzz program is one
        assembly), a private / protected / private protected / default one is skipped by C# and AxR{u}.Ax(object)
        binds. Oracles (kind "access", its own count check): the binding or nothing ("reach_or_nothing"), the other
        two never keyed."""
        ax, ot, ct = f"Ax{u}", f"PlOx{u}", f"PlCx{u}"
        acc = arng.choice(["public", "internal", "protected internal", "private", "protected", "private protected", ""])
        self._type(lines, rel, f"public class {ot}", [])
        self._type(lines, rel, f"public class {ct}", [])
        r = self._type(lines, rel, f"public class AxR{u}", [(ax, f"public void {ax}(object o)", [])])
        h = self._type(lines, rel, f"public class AxH{u} : AxR{u}", [(ax, f"{acc} void {ax}({ot} o)".strip(), [])])
        d = self._type(lines, rel, f"public class AxD{u} : AxH{u}", [(ax, f"public void {ax}({ct} c)", [])])
        k = self._user(lines, rel, f"AxU{u}", f"AxD{u} _a", f"_a.{ax}(o);", ax, params=f"{ot} o")
        bound, other = (h, r) if acc in ("public", "internal", "protected internal") else (r, h)
        self.planted += [("access", "reach_or_nothing", k, ax, bound[ax]), ("access", "no_key", k, ax, other[ax]),
                         ("access", "no_key", k, ax, d[ax])]

    def node(self, label, name, rel, line) -> str:
        key = f"p{self.idx}_{rel.replace('/', '_').replace('.', '_')}_{name.replace('.', '_')}_{line}"
        self.nodes.append({"key": key, "label": label, "name": name, "source_file": rel, "start_line": line})
        return key

    # ------------------------------------------------------------------ the soundness round (wrong Trues)
    def build_sound(self):
        """A program of the soundness round's planted shapes alone, each in .csproj groups of its own: every shape's
        answer comes from the C# and MSBuild rules, a sample validated on Roslyn (w3's fuzz_roslyn)."""
        self.n_files, self.types = 0, {}
        for u in range(self.rng.randint(2, 5)):
            getattr(self, f"plant_{self.rng.choice(KINDS_R4)}")(u)
        for r, ls in self.pfiles.items():
            self.files[r] = "\n".join(ls) + "\n"
        return self

    def _pair(self, kind, u, ga, nsa, gb, nsb, *, top=(), inner=(), param="Timer", base="System.Threading.Timer",
              cu=(), blk=(), impl="Timer", proven=False):
        """`ga` / `nsa`: ITi.Ti(param), the public TiBase.Ti(base) beside it, the interface call; `gb` / `nsb`: TiImpl :
        TiBase, ITi declaring Ti(impl). proven: impl is param's type, so TiImpl.Ti implements the member: reached, and
        TiBase.Ti, whose class implements nothing, never keyed. Else impl is another type (an overload): C# maps ITi.Ti
        to the public TiBase.Ti: reached (the engine's tuple kept when not keyed: never removed), TiImpl.Ti never
        keyed."""
        rng = self.rng
        m = f"Ti{u}"
        ra = f"{ga}/Ti{u}.cs"
        ls = self._src(ra)
        ls.extend(f"using {x};" for x in top)
        st = rng.choice(["fs", "blk"])
        self._ns_open(ls, nsa, list(inner), st)
        self._type(ls, ra, f"public interface ITi{u}", [(m, f"void {m}({param} t)", [])], iface=True)
        tb = self._type(ls, ra, f"public class TiBase{u}", [(m, f"public void {m}({base} t)", [])])
        k = self._user(ls, ra, f"TiUser{u}", f"ITi{u} _t", f"_t.{m}(x);", m, params=f"{base} x")
        self._ns_close(ls, st)
        rb = f"{gb}/TiImpl{u}.cs"
        ls = self._src(rb)
        ls.extend(f"using {x};" for x in cu)
        st = rng.choice(["fs", "blk"])
        self._ns_open(ls, nsb, list(blk), st)
        ti = self._type(ls, rb, f"public class TiImpl{u} : {nsa}.TiBase{u}, {nsa}.ITi{u}",
                        [(m, f"public void {m}({impl} t)", [])])
        self._ns_close(ls, st)
        bound, other = (ti, tb) if proven else (tb, ti)
        self.planted.append((kind, "reach", k, m, bound[m]))
        self.planted.append((kind, "no_key", k, m, other[m]))

    def plant_projns(self, u):
        """R1: a project type does not close a namespace: N.T, a member of an enclosing namespace N the project also
        declares a type in, beats that declaration's using of O, and from a child namespace of N the compilation unit's
        using (and from both, the compilation unit's alias of T), so TiImpl's Ti(T) is no implementation of ITi.Ti(O.T).
        The class spelling O.T qualified is one, and so is a child's own using of O, which comes before its parent's
        members (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Pn{tag}", f"Fz{self.idx}.Pn{u}"
        self.disk[f"{g}/Pn{tag}.csproj"] = _csproj()
        n, o, t = rng.choice([("System.Timers", "System.Threading", "Timer"), ("System.Threading", "System.Timers", "Timer"),
                              ("Microsoft.AspNetCore.Hosting", "Microsoft.Extensions.Hosting", "IHostingEnvironment"),
                              ("Microsoft.Extensions.Hosting", "Microsoft.AspNetCore.Hosting", "IHostingEnvironment")])
        nsb = rng.choice([n, f"{n}.J{tag}"])          # the namespace itself, or a child: the member of the parent
        child = nsb != n
        shape = rng.choice(["using", "using", "qual", "alias", "inner"])
        if shape == "alias":
            r = f"{g}/PjT{u}.cs"
            ls = self._src(r)
            self._ns_open(ls, ns, [], "fs")
            self._type(ls, r, f"public class PjT{u}", [])
            self._pair("projns", u, g, ns, g, nsb, param=f"PjT{u}", base=f"PjT{u}", cu=[f"{t} = {ns}.PjT{u}"], impl=t)
        elif shape == "qual":
            self._pair("projns", u, g, ns, g, nsb, top=[o], param=t, base=f"{o}.{t}", impl=f"{o}.{t}", proven=True)
        elif shape == "inner":                        # a child's own using comes before its parent's members
            self._pair("projns", u, g, ns, g, f"{n}.J{tag}", top=[o], param=t, base=f"{o}.{t}", blk=[o], impl=t,
                       proven=True)
        else:                                         # a declaration's using: its own members first, then that using
            kw = {"cu": [o]} if child else {"blk": [o]}
            self._pair("projns", u, g, ns, g, nsb, top=[o], param=t, base=f"{o}.{t}", impl=t, **kw)

    def plant_pkg(self, u):
        """R2: a package may fill the project's own namespaces. Its id's FIRST SEGMENT decides (read as text from a
        PackageReference, a <Reference> up to its comma, a packages.config, a Directory.Packages.props, another group's
        project): `using root.X;` nearer than the compilation unit's using of System.Threading may supply Timer (the
        package's: self.vend), and so may the enclosing root.Sub, so TiImpl's Ti(Timer) is no implementation. Under a
        root the project OWNS (its AssemblyName: R32) and no package id shares, the using supplies project types only:
        proven."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, root = f"src/Pk{tag}", f"Fzp{tag}"
        shape = rng.choice(["using", "using", "root", "encl", "proven"])
        if shape == "proven":
            root = f"Fzq{tag}"
        pid = f"{root}.Core"
        how = rng.choice(["ref", "ref", "reference", "config", "central", "other"]) if shape != "proven" else "none"
        pref = f'<PackageReference Include="{pid}" Version="1.0.0" />'
        items = {"ref": [pref], "reference": [f'<Reference Include="{pid}, Version=1.0.0.0, Culture=neutral" />']}
        if how == "config":
            self.disk[f"{g}/packages.config"] = f'<packages>\n  <package id="{pid}" version="1.0.0" />\n</packages>\n'
        if how == "central":
            self.disk[f"{g}/Directory.Packages.props"] = _msb([], [f'<PackageVersion Include="{pid}" Version="1.0.0" />'])
        if how == "other":
            self.disk[f"src/PkO{tag}/PkO{tag}.csproj"] = _csproj([], [pref])
        own = [f"<AssemblyName>{root}.Web</AssemblyName>"] if shape == "proven" else []
        self.disk[f"{g}/Pk{tag}.csproj"] = _csproj(own, items.get(how, []))
        sub = {"using": "Core", "proven": "Core", "root": "Extra", "encl": "Sub"}[shape]
        r = f"{g}/Hold{u}.cs"
        ls = self._src(r)
        self._ns_open(ls, f"{root}.{sub}", [], "fs")
        self._type(ls, r, f"public class Hold{u}", [])
        if shape != "proven":
            self.vend[f"{g}/vendor/Pk{u}.cs"] = f"namespace {root}.{sub}\n{{\n    public class Timer\n    {{\n    }}\n}}\n"
        kw = dict(top=["System.Threading"], param="Timer", base="System.Threading.Timer", proven=shape == "proven")
        if shape == "encl":
            self._pair("pkg", u, g, f"{root}.Web", g, f"{root}.Sub", blk=["System.Threading"], **kw)
        else:
            self._pair("pkg", u, g, f"{root}.Web", g, f"{root}.Web.Jobs", cu=["System.Threading"],
                       blk=[f"{root}.{sub}"], **kw)

    def plant_xalias(self, u):
        """R3: an x tuple is unknown, never "the same". x2: one alias name of two targets, each behind a nearer external
        using; gen: one generic target text in two namespace blocks (each relative to its block); msb: two projects'
        <Using Alias> of one name, two targets, under a condition the pass cannot read; blocks: two namespace blocks of
        one file each declare a class of one name. One alias of one target at both compilation units is one type
        (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Xa{tag}", f"Fz{self.idx}.Xa{u}"
        self.disk[f"{g}/Xa{tag}.csproj"] = _csproj()
        tok, tim = "System.Threading.CancellationToken", "System.Timers.Timer"
        shape = rng.choice(["x2", "gen", "msb", "blocks", "proven"])
        a = f"Tk{self.idx}x{u}"
        if shape == "x2":
            ext = f"FzX{tag}"
            self.vend[f"{g}/vendor/X{u}.cs"] = (f"namespace {ext}.One\n{{\n    public class M1\n    {{\n    }}\n}}\n"
                                                f"namespace {ext}.Two\n{{\n    public class M2\n    {{\n    }}\n}}\n")
            self._pair("xalias", u, g, ns, g, ns, top=[f"{a} = {tok}"], inner=[f"{ext}.One"], param=a, base=tok,
                       cu=[f"{a} = {tim}"], blk=[f"{ext}.Two"], impl=a)
        elif shape == "gen":
            na, nb = f"{ns}.A", f"{ns}.B"
            self.vend[f"{g}/vendor/X{u}.cs"] = "".join(
                f"namespace {x}.Core\n{{\n    public class Box<T>\n    {{\n    }}\n}}\n" for x in (na, nb))
            self._pair("xalias", u, g, na, g, nb, inner=[f"{a} = Core.Box<int>"], param=a, base="Core.Box<int>",
                       blk=[f"{a} = Core.Box<int>"], impl=a)
        elif shape == "msb":
            gb = f"src/Xb{tag}"
            cond = "Condition=\"'$(DmFuzzCi)' != 'true'\""
            self.disk[f"{g}/Xa{tag}.csproj"] = _csproj([], [f'<Using Include="{tok}" Alias="{a}" {cond} />'])
            self.disk[f"{gb}/Xb{tag}.csproj"] = _csproj([], [
                f'<Using Include="{tim}" Alias="{a}" {cond} />',
                f'<ProjectReference Include="..\\Xa{tag}\\Xa{tag}.csproj" />'])
            self._pair("xalias", u, g, ns, gb, f"Fz{self.idx}.Xb{u}", param=a, base=tok, impl=a)
        elif shape == "blocks":
            m, o = f"Ti{u}", f"Ord{u}"
            r = f"{g}/Blocks{u}.cs"
            ls = self._src(r)
            self._ns_open(ls, f"{ns}.A", [], "blk")
            self._type(ls, r, f"public class {o}", [])
            self._type(ls, r, f"public interface ITi{u}", [(m, f"void {m}({o} t)", [])], iface=True)
            tb = self._type(ls, r, f"public class TiBase{u}", [(m, f"public void {m}({o} t)", [])])
            k = self._user(ls, r, f"TiUser{u}", f"ITi{u} _t", f"_t.{m}(x);", m, params=f"{o} x")
            self._ns_close(ls, "blk")
            self._ns_open(ls, f"{ns}.B", [], "blk")
            self._type(ls, r, f"public class {o}", [])
            ti = self._type(ls, r, f"public class TiImpl{u} : {ns}.A.TiBase{u}, {ns}.A.ITi{u}",
                            [(m, f"public void {m}({o} t)", [])])
            self._ns_close(ls, "blk")
            self.planted.append(("xalias", "reach", k, m, tb[m]))
            self.planted.append(("xalias", "no_key", k, m, ti[m]))
        else:
            self._pair("xalias", u, g, ns, g, ns, top=[f"{a} = {tok}"], param=a, base=tok, cu=[f"{a} = {tok}"], impl=a,
                       proven=True)

    def plant_mlines(self, u):
        """R4: MSBuild keeps a property's white space and the SDK's condition compares it verbatim, so an ImplicitUsings
        with white space around `enable` adds nothing: project A's Timer is its own `using System.Timers;`, project B's
        System.Threading's: an overload. `ENABLE` in both: the same implicit Timer (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        ga, gb = f"src/Ma{tag}", f"src/Mb{tag}"
        nsa, nsb = f"Fz{self.idx}.Ma{u}", f"Fz{self.idx}.Mb{u}"
        pref = [f'<ProjectReference Include="..\\Ma{tag}\\Ma{tag}.csproj" />']
        if rng.random() < 0.3:
            on = "<ImplicitUsings>ENABLE</ImplicitUsings>"
            self.disk[f"{ga}/Ma{tag}.csproj"] = _csproj([on])
            self.disk[f"{gb}/Mb{tag}.csproj"] = _csproj([on], pref)
            self._pair("mlines", u, ga, nsa, gb, nsb, proven=True)
            return
        val = rng.choice(["\n      enable\n    ", " enable", "enable ", " true ", "\ttrue"])
        self.disk[f"{ga}/Ma{tag}.csproj"] = _csproj([f"<ImplicitUsings>{val}</ImplicitUsings>"])
        self.disk[f"{gb}/Mb{tag}.csproj"] = _csproj([], pref)
        self._pair("mlines", u, ga, nsa, gb, nsb, top=["System.Timers"], base="System.Timers.Timer",
                   blk=["System.Threading"])

    def plant_wildrm(self, u):
        """R5: a `*` / `?` in a <Using Remove> is a glob on item identities: project A's Timer is its own System.Timers',
        project B's System.Threading's: an overload. Inside project A both spellings are that group's: a using at the
        top against one in a block (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        ga, gb = f"src/Wa{tag}", f"src/Wb{tag}"
        nsa, nsb = f"Fz{self.idx}.Wa{u}", f"Fz{self.idx}.Wb{u}"
        pat = rng.choice(["System.Thread*", "System.Threadin?", "System.Threading*", "*.Threading", "System.Thr*ing", "*"])
        iu = "<ImplicitUsings>enable</ImplicitUsings>"
        self.disk[f"{ga}/Wa{tag}.csproj"] = _csproj([iu], [f'<Using Remove="{pat}" />'])
        if rng.random() < 0.3:
            self._pair("wildrm", u, ga, nsa, f"{ga}/Sub", f"{nsa}.Sub", top=["System.Timers"],
                       base="System.Timers.Timer", blk=["System.Timers"], proven=True)
            return
        self.disk[f"{gb}/Wb{tag}.csproj"] = _csproj([], [f'<ProjectReference Include="..\\Wa{tag}\\Wa{tag}.csproj" />'])
        self._pair("wildrm", u, ga, nsa, gb, nsb, top=["System.Timers"], base="System.Timers.Timer",
                   blk=["System.Threading"])

    def plant_linked(self, u):
        """R6: a file another project compiles sees that project's implicit usings, whatever directory it sits in: App's
        <Compile Include> reaches a directory holding no project (noproj), one of another project that removes it from
        its own compilation (other), a shared project's .projitems (shared); two files in no project's directory
        linked into two projects are no one group (two). Its Timer is System.Threading's through App; Lib's `using
        System.Timers;` gives an overload. Both files saying `using System.Threading;` read alike (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        s = f"Fz{self.idx}.Li{u}"
        iu = "<ImplicitUsings>enable</ImplicitUsings>"
        app, lib, sh = f"src/LiA{tag}", f"src/LiL{tag}", f"src/LiS{tag}"
        pref = [f'<ProjectReference Include="../LiA{tag}/LiA{tag}.csproj" />']
        shape = rng.choice(["noproj", "other", "shared", "two", "proven"])
        kw = dict(top=["System.Text"], cu=["System.Text", "System.Timers"])   # a using in both: U3 decides, not R2
        if shape == "noproj":
            self.disk[f"{app}/LiA{tag}.csproj"] = _csproj([iu], [f'<Compile Include="../LiS{tag}/**/*.cs" />'])
        elif shape == "other":
            self.disk[f"{sh}/LiS{tag}.csproj"] = _csproj([], ['<Compile Remove="Linked/**" />'])
            self.disk[f"{app}/LiA{tag}.csproj"] = _csproj([iu], [f'<Compile Include="../LiS{tag}/Linked/*.cs" />'])
            sh = f"{sh}/Linked"
        elif shape == "shared":
            self.disk[f"{sh}/LiS{tag}.projitems"] = _msb([], [
                f'<Compile Include="$(MSBuildThisFileDirectory)Ti{u}.cs" />'])
            self.disk[f"{app}/LiA{tag}.csproj"] = _csproj([iu]).replace(
                "</Project>", f'  <Import Project="..\\LiS{tag}\\LiS{tag}.projitems" Label="Shared" />\n</Project>')
        elif shape == "two":
            self.disk[f"{app}/LiA{tag}.csproj"] = _csproj([iu], [f'<Compile Include="../LiS{tag}/*.cs" />'])
            lib = f"src/LiT{tag}"
            self.disk[f"src/LiL{tag}/LiL{tag}.csproj"] = _csproj([], [f'<Compile Include="../LiT{tag}/*.cs" />'] + pref)
            self._pair("linked", u, sh, f"{s}.S", lib, f"{s}.L", **kw)
            return
        else:
            self.disk[f"{app}/LiA{tag}.csproj"] = _csproj([iu], [f'<Compile Include="../LiS{tag}/**/*.cs" />'])
            self._pair("linked", u, sh, f"{s}.S", f"{sh}/Sub", f"{s}.L", top=["System.Threading"],
                       cu=["System.Threading"], proven=True)
            return
        self.disk[f"{lib}/LiL{tag}.csproj"] = _csproj([], pref)
        self._pair("linked", u, sh, f"{s}.S", lib, f"{s}.L", **kw)

    # ------------------------------------------------------------------ the last soundness round (wrong Trues, W4)
    def build_final(self):
        """A program of the last round's planted shapes alone (R22-R32), each in .csproj groups of its own whose
        AssemblyName owns the program's namespace root (R32): every shape's answer comes from the C# and MSBuild
        rules, a sample validated on Roslyn."""
        self.n_files, self.types = 0, {}
        for u in range(self.rng.randint(2, 5)):
            getattr(self, f"plant_{self.rng.choice(KINDS_R5)}")(u)
        for r, ls in self.pfiles.items():
            self.files[r] = "\n".join(ls) + "\n"
        return self

    def own_roots(self):
        """R32 (the last round): a namespace root is transparent only when a project OWNS it (its AssemblyName,
        RootNamespace or project file name starts with it). The older families' .csproj groups (round 3, the soundness
        round) name none, so every .csproj that names no AssemblyName gets one under the program's root Fz<idx>: their
        usings stay provable, as in a repository whose assemblies are named after its namespaces. An AssemblyName
        changes no C# binding and no MSBuild using (no InternalsVisibleTo here), so every oracle stays the C# rule's."""
        for k, rel in enumerate(sorted(self.disk)):
            text = self.disk[rel]
            if rel.endswith(".csproj") and "<AssemblyName>" not in text and "    <TargetFramework>" in text:
                own = f"    <AssemblyName>Fz{self.idx}.P{k}</AssemblyName>\n"
                self.disk[rel] = text.replace("    <TargetFramework>", own + "    <TargetFramework>", 1)
        return self

    def _own(self, tag) -> str:
        """An AssemblyName whose first segment is the program's namespace root Fz<idx>: the project owns it (R32)."""
        return f"<AssemblyName>Fz{self.idx}.A{tag}</AssemblyName>"

    def _fpair(self, kind, u, ra, nsa, rb, nsb, *, top=("System.Threading",), param="Timer",
               base="System.Threading.Timer", pre_b=(), cu=(), blk=(), impl="Timer", head="public class",
               bsig=None, isig=None, call=None, uparams=None, proven=False, style=None, extra_impl=(), lead_b=(),
               blk_a=()):
        """The interface pair of the round: in ra (namespace nsa) ITi.Ti(param), the public TiBase.Ti(base) beside it
        and the interface call; in rb (nsb) `head TiImpl : TiBase, ITi` declaring Ti(impl) (or `isig`), with raw lines
        pre_b at the top of rb, lead_b right after rb's namespace line, extra_impl more TiImpl members. proven: TiImpl.Ti implements the member (reached, TiBase.Ti never keyed); else C# maps
        ITi.Ti to the public TiBase.Ti (reached: keyed or the engine's tuple kept), TiImpl.Ti never keyed."""
        rng = self.rng
        m = f"Ti{u}"
        ls = self._src(ra)
        ls.extend(f"using {x};" for x in top)
        st = style or rng.choice(["fs", "blk"])
        self._ns_open(ls, nsa, list(blk_a), st)
        self._type(ls, ra, f"public interface ITi{u}", [(m, f"void {m}({param} t)" if bsig is None else bsig, [])],
                   iface=True)
        tb = self._type(ls, ra, f"public class TiBase{u}",
                        [(m, f"public void {m}({base} t)" if bsig is None else f"public {bsig}", [])])
        k = self._user(ls, ra, f"TiUser{u}", f"ITi{u} _t", call or f"_t.{m}(x);", m,
                       params=uparams or f"{base} x")
        self._ns_close(ls, st)
        ls = self._src(rb)
        ls.extend(pre_b)
        ls.extend(f"using {x};" for x in cu)
        st = style or rng.choice(["fs", "blk"])
        self._ns_open(ls, nsb, list(blk), st)
        ls.extend(lead_b)
        q = "" if nsa == nsb else f"{nsa}."
        ti = self._type(ls, rb, f"{head} TiImpl{u} : {q}TiBase{u}, {q}ITi{u}",
                        [(m, f"public void {m}({impl} t)" if isig is None else isig, [])] + list(extra_impl))
        self._ns_close(ls, st)
        bound, other = (ti, tb) if proven else (tb, ti)
        self.planted.append((kind, "reach", k, m, bound[m]))
        self.planted.append((kind, "no_key", k, m, other[m]))

    def _ouser(self, lines, rel, name, fields, gos, head="public class"):
        """`head name { fields; Go<i>() { call } ... }`: gos = [(go name, call, member)]; returns {go name: key}."""
        lines.append(f"{head} {name}")
        self.node("Class", name, rel, len(lines))
        lines.append("{")
        lines.extend(f"    private readonly {f};" for f in fields)
        keys = {}
        for go, call, member in gos:
            lines.append(f"    public void {go}()")
            k = self.node("Method", f"{name}.{go}", rel, len(lines))
            lines += ["    {", f"        {call}", "    }"]
            self.spans[k] = (len(lines) - 3, len(lines))
            self._caller(k, member)
            keys[go] = k
        lines += ["}", ""]
        return keys

    def _save_cls(self, lines, rel, name, sv, head="public class", indent=""):
        """`head name { public void sv() {} }` at indent: {sv: node key} (graphify's name for a nested class's member
        is the class's own name: name.sv)."""
        lines.append(f"{indent}{head} {name}")
        name = name.split("<")[0]
        self.node("Class", name, rel, len(lines))
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    public void {sv}()")
        k = self.node("Method", f"{name}.{sv}", rel, len(lines))
        self.member_lines[(rel, len(lines))] = k
        lines += [f"{indent}    {{", f"{indent}    }}", f"{indent}}}"]
        self.spans[k] = (len(lines) - 3, len(lines) - 1)
        return {sv: k}

    def plant_arity(self, u):
        """R22: a method's own generic arity and a `params` array are part of its signature. TiImpl's Ti<T>(o) or
        Ti(o, params int[]) is another signature than ITi.Ti(o): the public TiBase.Ti implements it (gen, params); a
        Ti(o, int[]) is ITi.Ti(o, params int[])'s signature (parr), and beside a Ti<T>(o) overload the non-generic Ti(o)
        implements (both): proven. A virtual VBase.Ti(o) whose middle class declares another signature of the name
        (Ti<T>(o), a `new` Ti(o, params int[])): the leaf's override is the slot's, reached (never EXTRACTED)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns, m, o = f"src/Ar{tag}", f"Fz{self.idx}.Ar{u}", f"Ti{u}", f"ArO{u}"
        self.disk[f"{g}/Ar{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["gen", "params", "parr", "both", "slotgen", "slotpar"])
        r0 = f"{g}/ArO{u}.cs"
        ls = self._src(r0)
        self._ns_open(ls, ns, [], "fs")
        self._type(ls, r0, f"public class {o}", [])
        if shape.startswith("slot"):
            r1 = f"{g}/ArV{u}.cs"
            ls = self._src(r1)
            self._ns_open(ls, ns, [], "fs")
            self._type(ls, r1, f"public class VBase{u}", [(m, f"public virtual void {m}({o} o)", [])])
            mid = f"public void {m}<T>({o} o)" if shape == "slotgen" else f"public new void {m}({o} o, params int[] xs)"
            self._type(ls, r1, f"public class VMid{u} : VBase{u}", [(m, mid, [])])
            lf = self._type(ls, r1, f"public class VLeaf{u} : VMid{u}", [(m, f"public override void {m}({o} o)", [])])
            k = self._user(ls, r1, f"VUser{u}", f"VBase{u} _b", f"_b.{m}(o);", m, params=f"{o} o")
            self.planted += [("arity", "reach", k, m, lf[m]), ("arity", "never_E", k, m, None)]
            return
        sig = f"void {m}({o} o, params int[] xs)" if shape == "parr" else f"void {m}({o} o)"
        isig = {"gen": f"public void {m}<T>({o} o)", "params": f"public void {m}({o} o, params int[] xs)",
                "parr": f"public void {m}({o} o, int[] xs)", "both": f"public void {m}({o} o)"}[shape]
        call = f"_t.{m}(o, xs);" if shape == "parr" else f"_t.{m}(o);"
        up = f"{o} o, int[] xs" if shape == "parr" else f"{o} o"
        ra, rb = f"{g}/Ti{u}.cs", f"{g}/TiImpl{u}.cs"
        self._fpair("arity", u, ra, ns, rb, ns, top=(), bsig=sig, isig=isig, call=call, uparams=up,
                    proven=shape in ("parr", "both"),     # both: a generic overload after the implementing member
                    extra_impl=[(m, f"public void {m}<T>({o} o)", [])] if shape == "both" else ())

    def plant_ustatic(self, u):
        """R23: `using static T` in a namespace block supplies T's nested types before the compilation unit's usings.
        proj / ext: a project (or a package's: vendor/) static class nests a Timer, so TiImpl's Ti(Timer) is no
        implementation of ITi.Ti(System.Threading.Timer); a project static class with no nested Timer, imported by the
        same `using static` in both blocks, passes the name on (proven: the same lookup order on both sides; a static
        level on one side only stays unknown, R23). field: OUser's field type Ord is the static holder's nested Ord,
        never Models.Ord (a nested type has no Resolution form: reached through the engine's tuple or not at all)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Us{tag}", f"Fz{self.idx}.Us{u}"
        self.disk[f"{g}/Us{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["proj", "ext", "field", "proven"])
        if shape == "field":
            sv, o = f"Save{u}", f"Ord{u}"
            r0 = f"{g}/Models/Ord{u}.cs"
            ls = self._src(r0)
            self._ns_open(ls, f"{ns}.Models", [], "fs")
            mo = self._save_cls(ls, r0, o, sv)
            r1 = f"{g}/Lib/Hold{u}.cs"
            ls = self._src(r1)
            ls += [f"namespace {ns}.Lib", "{", f"    public static class Hold{u}", "    {"]
            ho = self._save_cls(ls, r1, o, sv, indent="        ")
            ls += ["    }", "}"]
            r2 = f"{g}/Jobs/OUser{u}.cs"
            ls = self._src(r2)
            ls.append(f"using {ns}.Models;")
            self._ns_open(ls, f"{ns}.Jobs", [f"static {ns}.Lib.Hold{u}"], "blk")
            ks = self._ouser(ls, r2, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
            self._ns_close(ls, "blk")
            self.planted += [("ustatic", "reach", ks["Go"], sv, ho[sv]), ("ustatic", "no_key", ks["Go"], sv, mo[sv])]
            return
        if shape == "ext":
            holder = f"FzU{tag}.Lib.Hold{u}"
            self.vend[f"{g}/vendor/Hold{u}.cs"] = (f"namespace FzU{tag}.Lib\n{{\n    public static class Hold{u}\n    {{\n"
                                                   "        public class Timer\n        {\n        }\n    }\n}\n")
        else:
            holder = f"{ns}.Lib.Hold{u}"
            nested = ["        public class Timer", "        {", "        }"] if shape == "proj" else [
                "        public static int Twice(int x)", "        {", "            return x * 2;", "        }"]
            r1 = f"{g}/Lib/Hold{u}.cs"
            ls = self._src(r1)
            ls += [f"namespace {ns}.Lib", "{", f"    public static class Hold{u}"]
            self.node("Class", f"Hold{u}", r1, len(ls))      # the engine's node of it: the pass reads the file (an
            ls += ["    {"] + nested + ["    }", "}"]         # unread holder makes its level unknown, R23)
        self._fpair("ustatic", u, f"{g}/Api/Ti{u}.cs", f"{ns}.Api", f"{g}/Jobs/TiImpl{u}.cs", f"{ns}.Jobs",
                    cu=["System.Threading"], blk=[f"static {holder}"], proven=shape == "proven", style="blk",
                    blk_a=[f"static {holder}"] if shape == "proven" else ())

    def plant_ifuse(self, u):
        """R24: a using, alias or `global using` inside an #if / #elif / #else branch is possible, never certain. On
        net8.0 TiImpl's Timer is System.Timers' (the #else / #elif branch, or no branch at all): an overload. An #if
        region holding no directive costs nothing (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        ga, gb = f"src/IfA{tag}", f"src/IfB{tag}"
        nsa, nsb = f"Fz{self.idx}.IfA{u}", f"Fz{self.idx}.IfB{u}"
        self.disk[f"{ga}/IfA{tag}.csproj"] = _csproj([self._own(tag)])
        self.disk[f"{gb}/IfB{tag}.csproj"] = _csproj(
            [self._own(f"{tag}b")], [f'<ProjectReference Include="..\\IfA{tag}\\IfA{tag}.csproj" />'])
        shape = rng.choice(["using", "elif", "alias", "global", "proven"])
        pre = {"using": ["#if NETFRAMEWORK", "using System.Threading;", "#else", "using System.Timers;", "#endif"],
               "elif": ["#if NET48", "using System.Threading;", "#elif NET8_0_OR_GREATER", "using System.Timers;",
                        "#endif"],
               "alias": ["#if NETFRAMEWORK", "using Timer = System.Threading.Timer;", "#else",
                         "using Timer = System.Timers.Timer;", "#endif"],
               "global": ["using System.Timers;"],
               "proven": ["#if DEBUG", "#endif", "using System.Threading;"]}[shape]
        if shape == "global":
            self.pfiles[f"{gb}/Glob{u}.cs"] = ["#if NETFRAMEWORK", "global using System.Threading;", "#endif", "",
                                               f"namespace FzG{tag};", "", f"public class Glob{u}", "{", "}"]
            self.node("Class", f"Glob{u}", f"{gb}/Glob{u}.cs", 7)    # the engine's node: the pass reads the file
        self._fpair("ifuse", u, f"{ga}/Ti{u}.cs", nsa, f"{gb}/TiImpl{u}.cs", nsb, pre_b=pre, proven=shape == "proven")

    def plant_cremove(self, u):
        """R25: <Compile Remove> takes a file out of the group: a removed file's `global using System.Threading;` counts
        for nothing, so TiImpl's Timer is its own `using System.Timers;` (remove; a Remove through a path property;
        EnableDefaultCompileItems=false with the file not listed). The removed file's `global using Timer = ...` alias
        is no alias of the group: TiImpl's Timer is System.Threading's (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        ga, gb = f"src/CrA{tag}", f"src/CrB{tag}"
        nsa, nsb = f"Fz{self.idx}.CrA{u}", f"Fz{self.idx}.CrB{u}"
        self.disk[f"{ga}/CrA{tag}.csproj"] = _csproj([self._own(tag)])
        pref = f'<ProjectReference Include="..\\CrA{tag}\\CrA{tag}.csproj" />'
        shape = rng.choice(["remove", "prop", "nodefault", "proven"])
        props, items, junk = [self._own(f"{tag}b")], [pref], f"{gb}/Junk/G{u}.cs"
        if shape in ("remove", "proven"):
            items.append('<Compile Remove="Junk/**" />')
        elif shape == "prop":
            items.append('<Compile Remove="$(MSBuildProjectDirectory)/Junk/**" />')
        else:
            props.append("<EnableDefaultCompileItems>false</EnableDefaultCompileItems>")
            items.append(f'<Compile Include="TiImpl{u}.cs" />')
            junk = f"{gb}/G{u}.cs"
        self.disk[f"{gb}/CrB{tag}.csproj"] = _csproj(props, items)
        if shape == "proven":
            self.files[junk] = (f"global using Timer = FzJ{tag}.Clock;\n\nnamespace FzJ{tag};\n\n"
                                "public class Clock\n{\n}\n")
            self.node("Class", "Clock", junk, 5)       # the engine's node: the pass reads the removed file
            self._fpair("cremove", u, f"{ga}/Ti{u}.cs", nsa, f"{gb}/TiImpl{u}.cs", nsb, top=(),
                        param="System.Threading.Timer", cu=["System.Threading"], proven=True)
            return
        self.files[junk] = (f"global using System.Threading;\n\nnamespace FzJ{tag};\n\n"
                            f"public class Junk{u}\n{{\n}}\n")
        self.node("Class", f"Junk{u}", junk, 5)        # the engine's node: the pass reads the removed file
        self._fpair("cremove", u, f"{ga}/Ti{u}.cs", nsa, f"{gb}/TiImpl{u}.cs", nsb, cu=["System.Timers"])

    def plant_xblock(self, u):
        """R26: the same-file rule binds only a class of the same or an enclosing namespace DECLARATION. impl: TiImpl,
        in a second block of the interface's file, takes its namespace's Ord from another file, so its Ti(Ord) is no
        implementation; field: OUser's Ord is its own namespace's (another file), never the first block's. The same
        declaration's class is bound (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Xb{tag}", f"Fz{self.idx}.Xb{u}"
        self.disk[f"{g}/Xb{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["impl", "field", "proven"])
        o, sv, m = f"Ord{u}", f"Save{u}", f"Ti{u}"
        r2 = f"{g}/B/Ord{u}.cs"
        ls2 = self._src(r2)
        self._ns_open(ls2, f"{ns}.{'D' if shape == 'proven' else 'B'}", [], "blk")
        other = self._save_cls(ls2, r2, o, sv)
        self._ns_close(ls2, "blk")
        r = f"{g}/Blk{u}.cs"
        ls = self._src(r)
        if shape == "impl":
            self._ns_open(ls, f"{ns}.A", [], "blk")
            self._type(ls, r, f"public class {o}", [])
            self._type(ls, r, f"public interface ITi{u}", [(m, f"void {m}({o} t)", [])], iface=True)
            tb = self._type(ls, r, f"public class TiBase{u}", [(m, f"public void {m}({o} t)", [])])
            k = self._user(ls, r, f"TiUser{u}", f"ITi{u} _t", f"_t.{m}(x);", m, params=f"{o} x")
            self._ns_close(ls, "blk")
            self._ns_open(ls, f"{ns}.B", [], "blk")
            ti = self._type(ls, r, f"public class TiImpl{u} : {ns}.A.TiBase{u}, {ns}.A.ITi{u}",
                            [(m, f"public void {m}({o} t)", [])])
            self._ns_close(ls, "blk")
            self.planted += [("xblock", "reach", k, m, tb[m]), ("xblock", "no_key", k, m, ti[m])]
            return
        self._ns_open(ls, f"{ns}.{'C' if shape == 'proven' else 'A'}", [], "blk")
        mine = self._save_cls(ls, r, o, sv)
        ls.append("")
        if shape == "proven":
            ks = self._ouser(ls, r, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
            self._ns_close(ls, "blk")
            self.planted += [("xblock", "reach", ks["Go"], sv, mine[sv]), ("xblock", "no_key", ks["Go"], sv, other[sv])]
            return
        self._ns_close(ls, "blk")
        self._ns_open(ls, f"{ns}.B", [], "blk")
        ks = self._ouser(ls, r, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
        self._ns_close(ls, "blk")
        self.planted += [("xblock", "reach", ks["Go"], sv, other[sv]), ("xblock", "no_key", ks["Go"], sv, mine[sv])]

    def plant_filetype(self, u):
        """R28: a `file` type is visible only in its own file. impl: the file-local Ord of TiImpl's file is its Ti's
        parameter, the interface's Ord is M's (another file cannot see the local one): no implementation; field:
        OUser's Ord is M's, never the file-local one in another file of its namespace. From its own file the file-local
        type is the one (proven)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Fl{tag}", f"Fz{self.idx}.Fl{u}"
        self.disk[f"{g}/Fl{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["impl", "field", "proven"])
        o, sv = f"Ord{u}", f"Save{u}"
        rm = f"{g}/M/Ord{u}.cs"
        ls = self._src(rm)
        self._ns_open(ls, f"{ns}.M", [], "fs")
        if shape == "impl":
            self._type(ls, rm, f"public class {o}", [])
            self._fpair("filetype", u, f"{g}/Ti{u}.cs", f"{ns}.N", f"{g}/TiImpl{u}.cs", f"{ns}.N", top=[f"{ns}.M"],
                        param=o, base=f"{ns}.M.{o}", head="file class", style="fs",
                        lead_b=[f"file class {o}", "{", "}", ""])
            return
        mo = self._save_cls(ls, rm, o, sv)
        rl = f"{g}/Local{u}.cs"
        ls = self._src(rl)
        self._ns_open(ls, f"{ns}.N", [], "fs")
        lo = self._save_cls(ls, rl, o, sv, head="file class")
        ls.append("")
        if shape == "proven":
            ks = self._ouser(ls, rl, f"LUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)],
                             head="file class")
            self.planted += [("filetype", "reach", ks["Go"], sv, lo[sv]), ("filetype", "no_key", ks["Go"], sv, mo[sv])]
            return
        ru = f"{g}/OUser{u}.cs"
        ls = self._src(ru)
        ls.append(f"using {ns}.M;")
        self._ns_open(ls, f"{ns}.N", [], "fs")
        ks = self._ouser(ls, ru, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
        self.planted += [("filetype", "reach", ks["Go"], sv, mo[sv]), ("filetype", "no_key", ks["Go"], sv, lo[sv])]

    def plant_blkuse(self, u):
        """R29: a namespace block's usings apply only inside it and the blocks nested in it: from another block of the
        same namespace (sibling) or of a child namespace declared apart (outer), OUser's Ord is the compilation unit's
        using's (Y), never the block's (X); inside the block (proven) or a block nested in it (nested) it is X's."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Bu{tag}", f"Fz{self.idx}.Bu{u}"
        self.disk[f"{g}/Bu{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["sibling", "outer", "proven", "nested"])
        o, sv = f"Ord{u}", f"Save{u}"
        keys = {}
        for d in ("X", "Y"):
            r = f"{g}/{d}/Ord{u}.cs"
            ls = self._src(r)
            self._ns_open(ls, f"{ns}.{d}", [], "blk")
            keys[d] = self._save_cls(ls, r, o, sv)
            self._ns_close(ls, "blk")
        r = f"{g}/OUser{u}.cs"
        ls = self._src(r)
        ls.append(f"using {ns}.Y;")
        ls.append("")
        if shape in ("sibling", "outer"):
            self._ns_open(ls, f"{ns}.A" if shape == "sibling" else ns, [f"{ns}.X"], "blk")
            self._type(ls, r, f"public class Oth{u}", [])
            self._ns_close(ls, "blk")
            ls.append("")
            self._ns_open(ls, f"{ns}.A", [], "blk")
            ks = self._ouser(ls, r, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
            self._ns_close(ls, "blk")
            win, lose = "Y", "X"
        elif shape == "proven":
            self._ns_open(ls, f"{ns}.B", [f"{ns}.X"], "blk")
            ks = self._ouser(ls, r, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
            self._ns_close(ls, "blk")
            win, lose = "X", "Y"
        else:
            self._ns_open(ls, ns, [f"{ns}.X"], "blk")
            self._ns_open(ls, "C", [], "blk")
            ks = self._ouser(ls, r, f"OUser{u}", [f"{o} _o"], [("Go", f"_o.{sv}();", sv)])
            self._ns_close(ls, "blk")
            self._ns_close(ls, "blk")
            win, lose = "X", "Y"
        self.planted += [("blkuse", "reach", ks["Go"], sv, keys[win][sv]),
                         ("blkuse", "no_key", ks["Go"], sv, keys[lose][sv])]

    def plant_garity(self, u):
        """R30: the type index carries generic arity: `Res` is the package's Res (vendor/), `Res<int>` the project's
        Res<T>; `Rep<int>` the package's Rep<T>, `Rep` the project's Rep."""
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Ga{tag}", f"Fz{self.idx}.Ga{u}"
        self.disk[f"{g}/Ga{tag}.csproj"] = _csproj([self._own(tag)])
        sv, ext = f"Save{u}", f"FzG{tag}"
        self.vend[f"{g}/vendor/Ext{u}.cs"] = (
            f"namespace {ext}\n{{\n    public class Res{u}\n    {{\n        public void {sv}()\n        {{\n        }}\n"
            f"    }}\n\n    public class Rep{u}<T>\n    {{\n        public void {sv}()\n        {{\n        }}\n    }}\n}}\n")
        r = f"{g}/Common/Res{u}.cs"
        ls = self._src(r)
        self._ns_open(ls, f"{ns}.Common", [], "blk")
        res = self._save_cls(ls, r, f"Res{u}<T>", sv)
        ls.append("")
        rep = self._save_cls(ls, r, f"Rep{u}", sv)
        self._ns_close(ls, "blk")
        ru = f"{g}/App/GUser{u}.cs"
        ls = self._src(ru)
        ls += [f"using {ext};", f"using {ns}.Common;"]
        self._ns_open(ls, f"{ns}.App", [], "fs")
        ks = self._ouser(ls, ru, f"GUser{u}", [f"Res{u} _r", f"Res{u}<int> _g", f"Rep{u}<int> _p", f"Rep{u} _q"],
                         [("GoR", f"_r.{sv}();", sv), ("GoG", f"_g.{sv}();", sv), ("GoP", f"_p.{sv}();", sv),
                          ("GoQ", f"_q.{sv}();", sv)])
        self.planted += [("garity", "no_key", ks["GoR"], sv, res[sv]), ("garity", "reach", ks["GoG"], sv, res[sv]),
                         ("garity", "no_key", ks["GoP"], sv, rep[sv]), ("garity", "reach", ks["GoQ"], sv, rep[sv])]

    def plant_pkgpre(self, u):
        """R32: a using is transparent only under a root a project file OWNS and no package id SHARES. A package id
        whose first segment starts with the root (`<root>Corp.Core`) or is a prefix of it (`<root[:3]>.Tools`), or a
        root no project file names (unowned): `using <root>.Core;` may supply the package's Timer (vendor/), so
        TiImpl's Ti(Timer) is no implementation. Owned, shared by no id (an unrelated package beside it): proven."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, root = f"src/Pp{tag}", f"Fzr{tag}"
        shape = rng.choice(["starts", "prefix", "unowned", "proven"])
        pid = {"starts": f"{root}Corp.Core", "prefix": f"{root[:3]}.Tools", "proven": f"Zz{tag}.Tools"}.get(shape)
        items = [f'<PackageReference Include="{pid}" Version="1.0.0" />'] if pid else []
        self.disk[f"{g}/{'Pp' + tag if shape == 'unowned' else root + '.Web'}.csproj"] = _csproj([], items)
        r = f"{g}/Core/Hold{u}.cs"
        ls = self._src(r)
        self._ns_open(ls, f"{root}.Core", [], "fs")
        self._type(ls, r, f"public class Hold{u}", [])
        if shape != "proven":
            self.vend[f"{g}/vendor/Pk{u}.cs"] = f"namespace {root}.Core\n{{\n    public class Timer\n    {{\n    }}\n}}\n"
        self._fpair("pkgpre", u, f"{g}/Web/Ti{u}.cs", f"{root}.Web", f"{g}/Web/TiImpl{u}.cs", f"{root}.Web.Jobs",
                    cu=["System.Threading"], blk=[f"{root}.Core"], proven=shape == "proven", style="blk")

    # ------------------------------------------------------------------ the closing round (W5: R42-R44)
    def build_close(self):
        """A program of the closing round's planted shapes alone (R42 an unresolved base, R44 a using inside a
        namespace declaration), each in .csproj groups of its own whose AssemblyName owns the program's namespace root
        Fz<idx> (R32), every oracle the C# rule's (a sample validated on Roslyn)."""
        self.n_files, self.types = 0, {}
        for u in range(self.rng.randint(2, 5)):
            getattr(self, f"plant_{self.rng.choice(KINDS_R6)}")(u)
        for r, ls in self.pfiles.items():
            self.files[r] = "\n".join(ls) + "\n"
        return self

    def plant_unres(self, u):
        """R42: an UNRESOLVED base may be any project class or interface, the one in question included: an alias
        target under a root no project owns (ialias: the alias of the interface itself), a project type behind a
        namespace declaration's non-transparent using (the shadow rule: ishadow through an interface, vshadow through
        a virtual member, vgeneric a generic base `TiBase<int>`), a qualifier of a root no project owns (vqual). The
        dispatch set is then open: the implementer / override that runs is reached (its key, or the engine's tuple
        kept), never removed. rival: the overload walk stops unknown at it, so the derived overload of another
        parameter type is never keyed and the base's member C# binds is reached. Each beside its proven sibling (the
        base resolved: palias, pshadow, pvirt, prival with no rival in the base)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g = f"src/Ur{tag}"
        self.disk[f"{g}/Ur{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["ialias", "ishadow", "vshadow", "vgeneric", "vqual", "rival",
                            "palias", "pshadow", "pvirt", "prival"])
        m, own, ext = f"Ti{u}", f"Fz{self.idx}.Ur{u}", f"Fzu{tag}"
        use = rng.choice(["System", "System.Timers", "System.Collections.Generic", "System.Threading.Tasks"])
        r = f"{g}/All{u}.cs"
        ls = self._src(r)
        if shape in ("ialias", "palias"):
            ns = ext if shape == "ialias" else own
            ls.append(f"using Al{u} = {ns}.ITi{u};")
            self._ns_open(ls, ns, [], "fs")
            self._type(ls, r, f"public interface ITi{u}", [(m, f"void {m}()", [])], iface=True)
            ti = self._type(ls, r, f"public class TiImpl{u} : Al{u}", [(m, f"public void {m}()", [])])
            k = self._user(ls, r, f"TiUser{u}", f"ITi{u} _t", f"_t.{m}();", m)
            self.planted.append(("unres", "reach", k, m, ti[m]))
            return
        if shape in ("rival", "prival"):
            riv = shape == "rival"
            bns = ext if riv else own
            self._ns_open(ls, f"{bns}.A", ["System.Threading"], "blk")
            tb = self._type(ls, r, f"public class TBase{u}", [(m, f"public void {m}(Timer t)", [])] if riv
                            else [(f"Other{u}", f"public void Other{u}()", [])])
            self._ns_close(ls, "blk")
            self._ns_open(ls, f"{bns}.B", [], "blk")
            k = self._user(ls, r, f"TUser{u}", f"TDer{u} _d", f"_d.{m}(t);", m, params="System.Threading.Timer t")
            self._ns_close(ls, "blk")
            rd = f"{g}/Der{u}.cs"
            ld = self._src(rd)
            self._ns_open(ld, f"{bns}.B", ["System.Timers"] if riv else [], "blk")
            ptype = "Timer" if riv else "System.Threading.Timer"
            td = self._type(ld, rd, f"public class TDer{u} : {bns}.A.TBase{u}",
                            [(m, f"public void {m}({ptype} t)", [])])
            self._ns_close(ld, "blk")
            self.planted += ([("unres", "no_key", k, m, td[m]), ("unres", "reach", k, m, tb[m])] if riv
                             else [("unres", "reach", k, m, td[m])])
            return
        iface = shape in ("ishadow", "pshadow")
        gen = shape == "vgeneric"
        bns = ext + ".Q" if shape == "vqual" else own
        self._ns_open(ls, bns, [], "blk")
        if iface:
            self._type(ls, r, f"public interface ITi{u}", [(m, f"void {m}()", [])], iface=True)
        tp, lst, prm = ("<T>" if gen else ""), (f" : ITi{u}" if iface else ""), ("T x" if gen else "")
        tb = self._type(ls, r, f"public class TiBase{u}{tp}{lst}", [(m, f"public virtual void {m}({prm})", [])])
        recv = f"ITi{u}" if iface else f"TiBase{u}<int>" if gen else f"TiBase{u}"
        k = self._user(ls, r, f"TiUser{u}", f"{recv} _t", f"_t.{m}({'1' if gen else ''});", m)
        self._ns_close(ls, "blk")
        if shape == "vqual":
            self._ns_open(ls, f"{ext}.R", [], "blk")
            head = f"public class TiImpl{u} : {ext}.Q.TiBase{u}"
        else:
            self._ns_open(ls, f"{own}.Jobs", [] if shape in ("pshadow", "pvirt") else [use], "blk")
            head = f"public class TiImpl{u} : TiBase{u}{'<int>' if gen else ''}"
        ti = self._type(ls, r, head, [(m, f"public override void {m}({'int x' if gen else ''})", [])])
        self._ns_close(ls, "blk")
        self.planted += [("unres", "reach", k, m, ti[m]), ("unres", "reach", k, m, tb[m])]

    def plant_relusing(self, u):
        """R44: a using or alias target written inside a namespace declaration (a block, or after a file-scoped
        namespace: StyleCop SA1200) means what C# resolves from that declaration's namespace outward (`using Kr;` in
        namespace N is N.Kr when it exists, else an enclosing namespace's Kr, else the global Kr); `global::` and a
        directive of the compilation unit are absolute. field / fsfield / same: OUser's Ord is the project's N.Kr.Ord
        (another file, after a file-scoped namespace, the same file), never the global Kr.Ord; glob (`global::Kr.Ord`)
        and cu (`using Kr;` above the namespace): the global one, never N.Kr's; map: `using Kj;` in N.A and in N.B
        means N.A.Kj and N.B.Kj, two Tm types, so TiImpl.Ti(Tm) implements nothing (TiBase.Ti reached); pmap: in N.A
        and N.A.Sub it means N.A.Kj both times (TiImpl.Ti reached, TiBase.Ti never keyed)."""
        rng = self.rng
        tag = f"{self.idx}x{u}"
        g, ns = f"src/Ru{tag}", f"Fz{self.idx}.Ru{u}"
        self.disk[f"{g}/Ru{tag}.csproj"] = _csproj([self._own(tag)])
        shape = rng.choice(["field", "fsfield", "same", "glob", "cu", "map", "pmap"])
        o, sv, kk = f"Ord{u}", f"Save{u}", f"Kr{tag}"
        if shape in ("map", "pmap"):
            m, tm, kj = f"Ti{u}", f"Tm{u}", f"Kj{u}"
            for d in ("A", "B"):
                rt = f"{g}/{d}/{kj}/{tm}.cs"
                lt = self._src(rt)
                self._ns_open(lt, f"{ns}.{d}.{kj}", [], "blk")
                self._type(lt, rt, f"public class {tm}", [])
                self._ns_close(lt, "blk")
            ra = f"{g}/A/ITi{u}.cs"
            la = self._src(ra)
            st = rng.choice(["fs", "blk"])
            self._ns_open(la, f"{ns}.A", [kj], st)
            self._type(la, ra, f"public interface ITi{u}", [(m, f"void {m}({tm} t)", [])], iface=True)
            tb = self._type(la, ra, f"public class TiBase{u}", [(m, f"public void {m}({tm} t)", [])])
            k = self._user(la, ra, f"TiUser{u}", f"ITi{u} _t", f"_t.{m}(x);", m, params=f"{tm} x")
            self._ns_close(la, st)
            rb = f"{g}/{'A/Sub' if shape == 'pmap' else 'B'}/TiImpl{u}.cs"
            lb = self._src(rb)
            st = rng.choice(["fs", "blk"])
            self._ns_open(lb, f"{ns}.A.Sub" if shape == "pmap" else f"{ns}.B", [kj], st)
            ti = self._type(lb, rb, f"public class TiImpl{u} : {ns}.A.TiBase{u}, {ns}.A.ITi{u}",
                            [(m, f"public void {m}({tm} t)", [])])
            self._ns_close(lb, st)
            bound, other = (ti, tb) if shape == "pmap" else (tb, ti)
            self.planted += [("relusing", "reach", k, m, bound[m]), ("relusing", "no_key", k, m, other[m])]
            return
        rg = f"{g}/Shared/{o}.cs"
        lg = self._src(rg)
        self._ns_open(lg, kk, [], "blk")
        glob = self._save_cls(lg, rg, o, sv)
        self._ns_close(lg, "blk")
        ru = f"{g}/Api/OUser{u}.cs"
        lu = self._src(ru)
        if shape == "same":
            self._ns_open(lu, f"{ns}.{kk}", [], "blk")
            proj = self._save_cls(lu, ru, o, sv)
            self._ns_close(lu, "blk")
            lu.append("")
        else:
            rp = f"{g}/{kk}/{o}.cs"
            lp = self._src(rp)
            self._ns_open(lp, f"{ns}.{kk}", [], "blk")
            proj = self._save_cls(lp, rp, o, sv)
            self._ns_close(lp, "blk")
        typ = o
        if shape == "cu":
            lu += [f"using {kk};", ""]
            self._ns_open(lu, ns, [], "blk")
        elif shape == "glob":
            typ = f"A{u}"
            self._ns_open(lu, ns, [f"{typ} = global::{kk}.{o}"], "blk")
        elif shape == "fsfield":
            self._ns_open(lu, f"{ns}.Api", [kk], "fs")
        else:
            self._ns_open(lu, ns, [kk], "blk")
        ks = self._ouser(lu, ru, f"OUser{u}", [f"{typ} _o"], [("Go", f"_o.{sv}();", sv)])
        self._ns_close(lu, "fs" if shape == "fsfield" else "blk")
        win, lose = (glob, proj) if shape in ("glob", "cu") else (proj, glob)
        self.planted += [("relusing", "reach", ks["Go"], sv, win[sv]), ("relusing", "no_key", ks["Go"], sv, lose[sv])]

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


_TREES: list = []


def write_tree(prog: Program):
    """Round 3: the program's MSBuild files (prog.disk) in a directory of its own under the fuzz's temp directory —
    the root the pass links with, so the binder sees the .csproj groups (the sources stay in memory) — or None."""
    if not prog.disk:
        return None
    if not _TREES:
        import tempfile
        _TREES.append(tempfile.mkdtemp(prefix="dm_tccs_fuzz_trees_"))
    d = os.path.join(_TREES[0], f"p{prog.idx}")
    for rel, text in prog.disk.items():
        p = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return d


def drop_tree(prog: Program) -> None:
    """Remove the program's tree (exactly the directory write_tree made)."""
    if prog.root:
        import shutil
        shutil.rmtree(prog.root, ignore_errors=True)
        prog.root = None


def run_program(prog: Program, calls: list, env=ON):
    nodes = [dict(n) for n in prog.nodes]
    return nodes, TC.link(prog.root or empty_root(), nodes, calls, env=env, source=Src(prog.files),
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
                                  "planted: a dispatch call EXTRACTED",
                                  "planted: neither the bound member nor nothing")}
    seen = Counter()
    for i in range(N):
        prog = Program(rng, i).build().own_roots()
        calls = prog.engine_calls()
        prog.root = write_tree(prog)
        try:
            judge_program(i, prog, calls, viol, seen)
        finally:
            drop_tree(prog)
    srng = random.Random(f"sound-{SEED}")      # the soundness round's family: its own generator, the programs above
    for i in range(N, N + N // 2):             # stay the same programs
        prog = Program(srng, i).build_sound().own_roots()
        calls = prog.engine_calls()
        prog.root = write_tree(prog)
        try:
            judge_program(i, prog, calls, viol, seen)
        finally:
            drop_tree(prog)
    frng = random.Random(f"final-{SEED}")      # the last round's family: its own generator too
    for i in range(N + N // 2, N + 2 * (N // 2)):
        prog = Program(frng, i).build_final()
        calls = prog.engine_calls()
        prog.root = write_tree(prog)
        try:
            judge_program(i, prog, calls, viol, seen)
        finally:
            drop_tree(prog)
    crng = random.Random(f"close-{SEED}")      # the closing round's family: its own generator too
    for i in range(N + 2 * (N // 2), N + 3 * (N // 2)):
        prog = Program(crng, i).build_close()
        calls = prog.engine_calls()
        prog.root = write_tree(prog)
        try:
            judge_program(i, prog, calls, viol, seen)
        finally:
            drop_tree(prog)
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
          all(seen[f"planted {k}"] > 60 for k in KINDS_R1)
          and seen["planted slot keyed"] > 20 and seen["planted rebind keyed"] > 20,
          {k: v for k, v in seen.items() if k.startswith("planted")})
    check("P ... and round 2's: an unconstrained / struct-constrained `T?` closed by a value type, one type of two "
          "spellings (nint / IntPtr, int? / Nullable<int>, `global using` aliases), a private or `new virtual` middle "
          "method, each planted and judged often, their reach oracles met on typed keys",
          all(seen[f"planted {k}"] > 60 for k in KINDS_R2)
          and seen["planted annot keyed"] > 20 and seen["planted spell keyed"] > 20 and seen["planted priv keyed"] > 10,
          {k: v for k, v in seen.items() if k.startswith("planted")})
    check("P ... and round 3's: usings at other levels and MSBuild implicit usings in .csproj groups (with the Timer "
          "pair across two projects), alias precedence, explicit and non-public implementations, nullable closed type "
          "arguments, overload resolution on the receiver's constructed type: each planted and judged often, every "
          "group tree on disk, and their bindings met on typed keys (the implementer that runs, the alias walk's "
          "class, the explicit implementation's class, the public base member, the nullable closer, the bound "
          "overload, the open kind's base member bound beside the derived overload)",
          # keyed reach oracles per planted oracle of the kind, over seeds 20261028, 1, 2, 3, 7, 11 (re-measured with
          # the integration round's access shape in the programs): the unfixed reader's maxima -> round 3's minima
          # levels .23 -> .30, alias .32 -> .45, explicit .43 -> .46, private .14 -> .41, nullable .50 -> .43 (the same
          # on both trees: its teeth are its no_key oracles), subst 0 -> .34, open .17 -> .33 (the Timer
          # pair's base member, an implicit conversion's derived overload, a type parameter's argument and a sibling
          # block's same-file class stay ADD-only: untyped, their engine tuple kept). Re-measured on the last round's
          # build (its .csproj groups own the program's root, Program.own_roots): levels .30 - .39, alias .16 - .23,
          # explicit .38 - .44, private .41 - .45, nullable .43 - .50, subst .34 - .41, open .33; alias and explicit
          # moved down by R32 + R27 (a decision, recall only: a program with no .csproj owns no root, so its usings
          # stay unknown), no oracle violated on any seed. On the closing round's build (W5: R42 / R44 / R45): levels
          # .236 - .367 (seed 3 lowest: recall only, no oracle violated on any seed), the others within their floors
          all(seen[f"planted {k}"] > 60 for k in KINDS_R3) and seen["msbuild groups"] > 60
          and all(keyed_share(seen, k) > floor for k, floor in (("levels", .2), ("alias", .14), ("explicit", .35),
                                                                 ("private", .31), ("nullable", .35), ("subst", .2),
                                                                 ("open", .25))),
          {**{k: v for k, v in seen.items() if k.startswith("planted") or k == "msbuild groups"},
           **{f"{k} keyed share": round(keyed_share(seen, k), 3) for k in KINDS_R3 + ("open",)}})
    check("P ... and the integration round's access shape (planted beside subst): a base overload the call site may "
          "not see (private, protected, private protected, no modifier) is never keyed (the root's Ax(object) or "
          "nothing), an accessible one (public, internal, protected internal) is bound: planted and judged often, the "
          "accessible bindings met on typed keys",
          # one reach oracle of three per shape, bound when the drawn modifier is accessible (3 of 7): .13 - .17 keyed
          # with the rival binding over the six seeds above, 0 without it
          seen["planted access"] > 60 and keyed_share(seen, "access") > .07,
          {"planted access": seen["planted access"], "access keyed share": round(keyed_share(seen, "access"), 3)})
    check("P ... and the soundness round's wrong-True classes (a family of its own): a project type in a capable "
          "namespace, a namespace a package shares, x tuples, white space around ImplicitUsings, a wildcard <Using "
          "Remove>, a file another project compiles: each planted and judged often, the proofs that stand met on typed "
          "keys",
          # keyed reach oracles per planted oracle of the kind: only the shape's proven sibling is keyed (the rest stay
          # ADD-only, their teeth the no_key and reach oracles). Minima -> maxima over seeds 20261028, 1, 2, 3, 7, 11:
          # projns .079 - .125, pkg .062 - .118, xalias .065 - .124, mlines .106 - .195, wildrm .155 - .172, linked .062
          # - .097; the main family's shares did not move in this round. A sample of 80 programs on seed 20261028 was
          # checked on Roslyn: every reach and no_key oracle agreed (552 of 552). On the last round's build (owned
          # roots): pkg .062 - .118 (its proven shape's root owned by its AssemblyName), xalias .167 - .210, mlines
          # .106 - .195, wildrm .155 - .172, linked .062 - .097, and projns 0 on every seed: its proven shapes (`O.T`
          # qualified, a child's own using of O) sit inside a capable namespace, where R27 leaves every name the
          # project does not declare there unknown (a decision, recall only): its oracles keep their teeth, no floor
          all(seen[f"planted {k}"] > 60 for k in KINDS_R4)
          and all(keyed_share(seen, k) > floor for k, floor in (("pkg", .05), ("xalias", .05),
                                                                 ("mlines", .09), ("wildrm", .13), ("linked", .05))),
          {**{f"planted {k}": seen[f"planted {k}"] for k in KINDS_R4},
           **{f"{k} keyed share": round(keyed_share(seen, k), 3) for k in KINDS_R4}})
    check("P ... and the last round's wrong-True classes (a family of its own): a method's own generic arity and a "
          "`params` array, `using static` in a block, a directive in an #if branch, <Compile Remove> and default items "
          "off, another declaration's class in the same file, a `file` type, another declaration's using, a type name's "
          "generic arity, a package id sharing the root by prefix and a root no project owns: each planted and judged "
          "often, the proofs that stand met on typed keys",
          # FINAL_FLOORS: keyed reach oracles per planted oracle of the kind, minima over the six seeds (calibrated)
          all(seen[f"planted {k}"] > 60 for k in KINDS_R5)
          and all(keyed_share(seen, k) > floor for k, floor in FINAL_FLOORS.items()),
          {**{f"planted {k}": seen[f"planted {k}"] for k in KINDS_R5},
           **{f"{k} keyed share": round(keyed_share(seen, k), 3) for k in KINDS_R5}})
    check("P ... and the closing round's wrong-True classes (a family of its own): an unresolved base (an alias of a "
          "root no project owns, a project type behind a namespace declaration's non-transparent using, a generic or a "
          "qualified spelling) under interface and virtual dispatch and in the overload rival walk, and a using or "
          "alias target written inside a namespace declaration (relative, `global::`, the compilation unit's): each "
          "planted and judged often, the proofs that stand met on typed keys",
          all(seen[f"planted {k}"] > 60 for k in KINDS_R6)
          and all(keyed_share(seen, k) > floor for k, floor in CLOSE_FLOORS.items()),
          {**{f"planted {k}": seen[f"planted {k}"] for k in KINDS_R6},
           **{f"{k} keyed share": round(keyed_share(seen, k), 3) for k in KINDS_R6}})


# keyed reach oracles per planted oracle of the kind over seeds 20261028, 1, 2, 3, 7, 11 on the closing round's build:
# CLOSE_FLOORS, below the six seeds' minima (20261028, 1, 2, 3, 7, 11) on the closing round's final build (R42 as
# narrowed by L1, R43-R45, L2): unres .559 - .615, relusing .500 on every seed (each relusing shape's binding keyed: its
# reach oracle is half of its oracles), every other kind's share unchanged from the build before L1 / L2, no oracle
# violated on any seed. The build before the round (seed 20261028): RED through both kinds' oracle violations. A sample
# of 210 programs of the family (60 on seed 20261028, 150 on seed 1) was checked on Roslyn: every reach and no_key
# oracle agreed (1,373)
CLOSE_FLOORS: dict = {"unres": .5, "relusing": .45}

# keyed reach oracles per planted oracle of the kind over seeds 20261028, 1, 2, 3, 7, 11 on the last round's build
# (gates scratch logs/calib_final1): arity, xblock, blkuse, garity .50 on every seed (each shape's binding keyed or
# its must-not), filetype .29 - .41, ustatic .09 - .20, cremove .09 - .17, pkgpre .08 - .13, ifuse .06 - .13. The
# build before the round (seed 20261028): arity .09, ustatic .11, ifuse .09, cremove 0, xblock .18, filetype .15,
# blkuse .26, garity .50 (its keys wrong Trues), pkgpre .10, every kind RED there through its oracle violations.
# A sample of 200 programs of the family (seed 1) was checked on Roslyn: every reach and no_key oracle agreed (1,636)
FINAL_FLOORS: dict = {"arity": .45, "ustatic": .07, "ifuse": .04, "cremove": .08, "xblock": .45, "filetype": .25,
                      "blkuse": .45, "garity": .45, "pkgpre": .06}


def keyed_share(seen: Counter, kind: str) -> float:
    """The planted reach oracles of kind met on a typed key, per planted oracle of that kind."""
    return seen[f"planted {kind} keyed"] / max(1, seen[f"planted {kind}"])


def judge_program(i: int, prog: Program, calls: list, viol: dict, seen: Counter) -> None:
    """Every rule of P (the module docstring) on program i: its violations to `viol`, what it exercised to `seen`."""
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
        return
    if st.get("error"):
        viol["error"].append((i, st["error"]))
        return
    if st.get("files_failed"):
        viol["files_failed"].append((i, st["files_failed"]))
    seen["msbuild groups"] += sum(1 for r in prog.disk if r.lower().endswith(".csproj"))
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
            viol["planted: a key the C# rules forbid"].append((i, kind, by[ck]["name"], by[tk]["name"],
                                                               by[tk]["source_file"]))
        elif rule == "reach" and tk not in mine and names_left[(ck, member)] <= 0:
            viol["planted: the override that runs lost"].append((i, kind, by[ck]["name"], by[tk]["name"],
                                                                 by[tk]["source_file"]))
        elif rule == "reach_or_nothing" and tk not in mine:
            others = sorted(by[t]["name"] for t in mine if seg(by[t]["name"]) == member)
            if others or names_left[(ck, member)] <= 0:      # another member typed, or the engine's tuple removed
                viol["planted: neither the bound member nor nothing"].append(
                    (i, kind, by[ck]["name"], by[tk]["name"], others or "tuple removed"))
        elif rule == "never_E":
            gr = by[ck].get("call_typed") or {}
            bad = [by[t]["name"] for t in mine if seg(by[t]["name"]) == member and gr.get(t) == "EXTRACTED"]
            if bad:                            # dispatch (slot, rebind), or a hit beside a rival (open)
                viol["planted: a dispatch call EXTRACTED"].append((i, kind, by[ck]["name"], bad))
        if rule in ("reach", "reach_or_nothing"):
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


# ================================================================================================
# M: byte mutations of the fixture tree
# ================================================================================================

def sec_mutations():
    print(f"\n-- M: {N} seeded byte mutations of the C# fixture sources --")
    try:
        import typed_calls_cs_gates as G   # noqa: E402 — the fixture trees
        trees = list(getattr(G, "TREES", {}).values()) or (
            [G.CQRS] + [getattr(G, t) for t in ("REVIEW", "RECHECK", "SIGS", "RND2") if hasattr(G, t)])
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
    import shutil
    for made in (_EMPTY, _TREES):          # exactly the directories this run made
        if made:
            shutil.rmtree(made.pop(), ignore_errors=True)
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
