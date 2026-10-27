#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking — TypeScript / JavaScript reader hardening (receiver-calls design slice S3c, §9 S3c
"Fuzz: never raises; no edge from text that is not a call"). No Neo4j, no engine, no network.

  P  300 seeded random TS / JS programs (classes with fields, constructors, parameter properties, static and
     decorated methods, `extends` chains, free functions and arrows, ESM and CommonJS imports between files, and
     every receiver shape: params, locals, fields, `this` / `super`, class and module references, `new`) laced
     with traps (call-shaped text in strings, comments, templates and regexes; `this` in `function` expressions
     and object literals; casts, subscripts, `.call`; rebinding, for-of, destructuring; JSX; parse errors; field
     writes through a `this` alias, a `.bind(this)` callback or a parameter; window / String.prototype patches,
     Object.assign prototype mixins, a local `type` shadowing a class, `@ts-nocheck`, `export *` barrels,
     `declare global`, computed members) run
     through build/receiver_facts_ts.py + typed_calls.link over synthetic engine nodes and random engine-like name
     tuples. Every program must: never raise / record no error / fail no file; give FileFacts that validate and
     whose census never undercounts a site; emit only CallKeys to nodes of `nodes`, never to the caller, with a
     grade, EXTRACTED only in-file; type a call ONLY where the caller really calls that name (the generator's
     ledger — decoy text is no call); remove a tuple only for a name the caller really calls, and never from a
     caller whose span holds a parse error; give the same output twice; return the same list with the flags off.
  M  300 seeded byte mutations of the S3c fixture trees' sources: the reader never raises and every FileFacts
     validates; a scope that holds a parse error has a census above its sites for every name it calls.

Usage: python tests/typed_calls_ts_fuzz_gates.py [--seed N] [--n N]
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

SEED = 20261022
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
    import receiver_facts_ts as R                 # noqa: E402
    import graphify_extract as GX                 # noqa: E402
    _ERR = ""
except Exception as _exc:  # noqa: BLE001 — the pre-feature tree: RED, never a crash
    TC = R = GX = None
    _ERR = repr(_exc)

ON = {"DEEPMAP_TYPED_CALLS": "typescript,javascript"}


class Src:
    """An in-memory graphify_extract._Source (lines / raw; no tree: the reader parses raw bytes itself)."""

    def __init__(self, files: dict):
        self.files = files

    def raw(self, rel):
        t = self.files.get(rel)
        return None if t is None else t.encode("utf-8")

    def lines(self, rel):
        t = self.files.get(rel)
        return None if t is None else t.split("\n")

    def ts_tree(self, rel):
        return None

    def tree(self, rel):
        return None


# ================================================================================================
# P: the program generator (it keeps the ledger of every REAL call)
# ================================================================================================

METHODS = ["save", "find", "load", "run", "close", "apply", "emit", "check"]
STATICS = ["make", "create", "build"]
FIELDS = ["repo", "cache", "store", "peer"]


class Program:
    def __init__(self, rng: random.Random, idx: int):
        self.rng, self.idx = rng, idx
        self.files: dict = {}
        self.nodes: list = []
        self.ledger: dict = {}            # caller key -> Counter(name) of REAL calls in its full span
        self.errored: set = set()         # caller keys whose span holds a parse error
        self.n_files = rng.randint(1, 3)
        self.exts = [rng.choice([".ts", ".ts", ".tsx", ".js", ".mjs"]) for _ in range(self.n_files)]
        self.classes: dict = {}           # file index -> [(class name, [methods], [statics], base)]

    # ------------------------------------------------------------------ planning
    def plan(self):
        rng = self.rng
        for fi in range(self.n_files):
            out = []
            for ci in range(rng.randint(1, 3)):
                name = f"C{fi}{ci}"
                ms = rng.sample(METHODS, rng.randint(1, 4))
                ss = rng.sample(STATICS, rng.randint(0, 2))
                base = None
                if out and rng.random() < 0.4:
                    base = rng.choice(out)[0]
                out.append((name, ms, ss, base))
            self.classes[fi] = out

    def ts(self, fi) -> bool:
        return self.exts[fi] in (".ts", ".tsx")

    def rel(self, fi) -> str:
        return f"src/f{fi}{self.exts[fi]}"

    # ------------------------------------------------------------------ statements
    def stmt(self, fi, ctx, out: list, real: Counter, errs: list):
        """One statement for a body; real calls go to `real`."""
        rng = self.rng
        cls = ctx.get("cls")
        own = [c for c in self.classes[fi]]
        other = [(f, c) for f in range(self.n_files) for c in self.classes[f]]
        tgt_f, tgt = rng.choice(other)
        m = rng.choice(tgt[1])
        k = rng.randint(0, 31)
        ts = self.ts(fi)
        name_ref = tgt[0] if tgt_f == fi else f"m{tgt_f}.{tgt[0]}" if ctx.get("ns") and tgt_f in ctx["ns"] else None
        if k == 0 and ctx.get("param"):
            out.append(f"{ctx['param']}.{m}();")
            real[m] += 1
        elif k == 1 and cls is not None:
            mm = rng.choice(cls[1])
            out.append(f"this.{mm}();")
            real[mm] += 1
        elif k == 2 and cls is not None and ctx.get("fields"):
            f, fcls = rng.choice(ctx["fields"])
            mm = rng.choice(fcls[1])
            out.append(f"this.{f}.{mm}();" if rng.random() < 0.8 else f"this.{f}?.{mm}();")
            real[mm] += 1
        elif k == 3 and name_ref:
            v = f"v{rng.randint(0, 9)}"
            out.append(f"const {v} = new {name_ref}();")
            out.append(f"{v}.{m}();")
            real[tgt[0]] += 1
            real[m] += 1
        elif k == 4 and name_ref and tgt[2]:
            s = rng.choice(tgt[2])
            out.append(f"{name_ref}.{s}();")
            real[s] += 1
        elif k == 5 and name_ref:
            out.append(f"new {name_ref}();")
            real[tgt[0]] += 1
        elif k == 6:
            out.append(f'const s{rng.randint(0, 9)} = "x.{m}()"; // y.{m}()')
        elif k == 7:
            out.append(f"/* {m}() and this.{m}() */")
        elif k == 8:
            out.append(f"const t{rng.randint(0, 9)} = `a.{m}() ${{1}}`;")
        elif k == 9:
            out.append(f"const r{rng.randint(0, 9)} = /\\.{m}\\(/;")
        elif k == 10 and cls is not None:
            mm = rng.choice(cls[1])
            out.append(f"[1].forEach(() => this.{mm}());")
            real[mm] += 1
            real["forEach"] += 1
        elif k == 11:
            out.append(f"[1].forEach(function () {{ this.{m}(); }});")
            real[m] += 1
            real["forEach"] += 1
        elif k == 12 and ctx.get("param") and ts and name_ref:
            out.append(f"({ctx['param']} as {name_ref}).{m}();")
            real[m] += 1
        elif k == 13 and name_ref:
            v = f"w{rng.randint(0, 9)}"
            out.append(f"let {v} = new {name_ref}();")
            out.append(f"{v} = {v};")
            out.append(f"{v}.{m}();")
            real[tgt[0]] += 1
            real[m] += 1
        elif k == 14 and ctx.get("param"):
            out.append(f"for (const it of [{ctx['param']}]) {{ it.{m}(); }}")
            real[m] += 1
        elif k == 15 and cls is not None and cls[3] is not None:
            base = next((c for c in own if c[0] == cls[3]), None)
            if base is not None:
                mm = rng.choice(base[1])
                out.append(f"super.{mm}();")
                real[mm] += 1
        elif k == 16:
            out.append(f"const o{rng.randint(0, 9)} = {{ {m}() {{ return this; }}, go() {{ this.{m}(); }} }};")
            real[m] += 1
        elif k == 17 and ctx.get("param"):
            out.append(f"{ctx['param']}[\"{m}\"]();")
        elif k == 18 and ctx.get("param"):
            out.append(f"{ctx['param']}.{m}.call(null);")
            real["call"] += 1
            real[m] += 1                              # an indirect call of m (census only, never REPLACEd)
        elif k == 19 and ctx.get("param") and ts:
            out.append(f"{ctx['param']}!.{m}();")
            real[m] += 1
        elif k == 20 and ctx.get("param"):
            out.append(f"{ctx['param']}.{m}(;")
            real[m] += 1
            errs.append(True)
        elif k == 21 and ctx.get("modvar"):
            mv, fname = ctx["modvar"]
            out.append(f"{mv}.{fname}();")
            real[fname] += 1
        elif k == 22 and self.exts[fi] == ".tsx" and name_ref:
            out.append(f"const j{rng.randint(0, 9)} = <{tgt[0]} />;")
        elif k == 23:
            out.append(f"const {{ {m} }} = {{ {m}: null }};")
        # --- the S3c review round: writes and declarations that change what a receiver may be
        elif k == 25 and name_ref:
            al = f"self{rng.randint(0, 9)}"
            out.append(f"const {al} = this;")
            out.append(f"{al}.{rng.choice(FIELDS)} = new {name_ref}();")
            real[tgt[0]] += 1
        elif k == 26:
            out.append(f"window.{m} = () => 1;")
        elif k == 27 and name_ref:
            out.append(f"Object.assign({name_ref}.prototype, {{ {m}() {{ return 1; }} }});")
            real["assign"] += 1
        elif k == 28 and ts and name_ref and "." not in name_ref:
            out.append(f"type {name_ref} = number;")
        elif k == 29:
            out.append(f"[1].forEach(function () {{ this.{rng.choice(FIELDS)} = 1; }}.bind(this));")
            real["forEach"] += 1
            real["bind"] += 1
        elif k == 30:
            out.append(f"String.prototype.{m} = function () {{ return 1; }};")
        elif k == 31 and ctx.get("param"):
            out.append(f"{ctx['param']}.{rng.choice(FIELDS)} = 1;")
        else:
            out.append(f"// {m}()")

    # ------------------------------------------------------------------ files
    def build(self):
        rng = self.rng
        self.plan()
        for fi in range(self.n_files):
            lines: list = []
            ts = self.ts(fi)
            rel = self.rel(fi)
            ns = set()
            modvar = None
            for fj in range(self.n_files):
                if fj == fi:
                    continue
                spec = f"./f{fj}" + (".mjs" if self.exts[fj] == ".mjs" else "")
                if not ts and rng.random() < 0.4:
                    lines.append(f"const m{fj} = require('{spec}');")
                else:
                    lines.append(f'import * as m{fj} from "{spec}";')
                ns.add(fj)
                if self.functions_of(fj):
                    modvar = (f"m{fj}", self.functions_of(fj)[0])
                if rng.random() < 0.15:
                    lines.append(f'export * from "{spec}";')
            if ts and rng.random() < 0.1:
                lines.insert(0, "// @ts-nocheck")
            if ts and rng.random() < 0.15:
                lines.append(f"declare global {{ interface Window {{ {rng.choice(METHODS)}(): void; }} }}")
            lines.append("")
            self.module_node(fi, rel)
            for cname, ms, ss, base in self.classes[fi]:
                cls = (cname, ms, ss, base)
                lines.append(f"export class {cname}" + (f" extends {base}" if base else "") + " {")
                self.node("Class", cname, rel, len(lines))
                fields = []
                for f in rng.sample(FIELDS, rng.randint(0, 2)):
                    fcls = rng.choice(self.classes[fi])
                    if ts and rng.random() < 0.5:
                        lines.append(f"  {f}: {fcls[0]} = new {fcls[0]}();")
                    else:
                        lines.append(f"  {f} = new {fcls[0]}();")
                    fields.append((f, fcls))
                if rng.random() < 0.1:                    # a computed member: the class opens
                    lines.append("  [Symbol.iterator](): any {" if ts else "  [Symbol.iterator]() {")
                    lines.append("    return null;")
                    lines.append("  }")
                free = [x for x in METHODS if x not in ms]
                if free and rng.random() < 0.1:           # a string-literal computed name: a method with no node
                    lines.append(f"  ['{rng.choice(free)}']() {{}}")
                ctx = {"cls": cls, "fields": fields, "ns": ns, "modvar": modvar}
                for mname in ms + ss:
                    static = mname in ss
                    if ts and rng.random() < 0.2:
                        lines.append("  @dec()")
                    param = None
                    sig = "()"
                    if rng.random() < 0.6:
                        pc = rng.choice([c for f in range(self.n_files) for c in self.classes[f]])
                        param = "p"
                        ref = pc[0] if pc in self.classes[fi] else (f"m{self.classes_file(pc)}.{pc[0]}" if ts else None)
                        sig = f"(p: {ref})" if (ts and ref) else "(p)"
                    lines.append(f"  {'static ' if static else ''}{mname}{sig} {{")
                    key = self.node("Method", f"{cname}.{mname}", rel, len(lines))
                    real, errs = Counter(), []
                    body: list = []
                    for _ in range(rng.randint(0, 5)):
                        self.stmt(fi, dict(ctx, param=param, cls=None if static else cls), body, real, errs)
                    lines += ["    " + b for b in body]
                    lines.append("  }")
                    self.ledger[key] = real
                    if errs:
                        self.errored.add(key)
                lines.append("}")
                lines.append("")
            for fname in self.functions_of(fi):
                arrow = rng.random() < 0.4
                pc = rng.choice(self.classes[fi])
                sig = f"(p: {pc[0]})" if ts else "(p)"
                lines.append(f"export const {fname} = {sig} => {{" if arrow else f"export function {fname}{sig} {{")
                key = self.node("Function", fname, rel, len(lines))
                real, errs = Counter(), []
                body = []
                for _ in range(rng.randint(0, 5)):
                    self.stmt(fi, {"cls": None, "fields": [], "ns": ns, "param": "p", "modvar": modvar}, body, real,
                              errs)
                lines += ["  " + b for b in body]
                lines.append("};" if arrow else "}")
                lines.append("")
                self.ledger[key] = real
                if errs:
                    self.errored.add(key)
            self.files[rel] = "\n".join(lines) + "\n"
        return self

    def functions_of(self, fi) -> list:
        return [f"fn{fi}a", f"fn{fi}b"][: 1 + (fi % 2)]

    def classes_file(self, c) -> int:
        return next(f for f in range(self.n_files) if c in self.classes[f])

    def module_node(self, fi, rel):
        self.node("Module", f"f{fi}", rel, 1)

    def node(self, label, name, rel, line) -> str:
        key = f"p{self.idx}_{rel.replace('/', '_').replace('.', '_')}_{name.replace('.', '_')}_{line}"
        self.nodes.append({"key": key, "label": label, "name": name, "source_file": rel, "start_line": line})
        return key

    def engine_calls(self) -> list:
        """Random engine-like name tuples (real names and others)."""
        rng = self.rng
        names = METHODS + STATICS + [c[0] for f in self.classes for c in self.classes[f]] + ["forEach", "call"]
        out = []
        for n in self.nodes:
            if n["label"] in ("Function", "Method"):
                for nm in rng.sample(names, rng.randint(0, 3)):
                    out.append((n["key"], nm))
        return out


_EMPTY: list = []


def empty_root() -> str:
    """An empty directory: the sources are in memory, so module resolution must see no tree on disk (a root of
    "." would walk the repository)."""
    if not _EMPTY:
        import tempfile
        _EMPTY.append(tempfile.mkdtemp(prefix="dm_s3c_fuzz_"))
    return _EMPTY[0]


def run_program(prog: Program, calls: list, env=ON):
    nodes = [dict(n) for n in prog.nodes]
    return nodes, TC.link(empty_root(), nodes, calls, env=env, source=Src(prog.files), is_test=lambda p: False)


def seg(name) -> str:
    return TC.name_seg(name)


def sec_programs():
    print(f"\n-- P: {N} seeded random TS / JS programs (seed {SEED}) --")
    rng = random.Random(SEED)
    viol: dict = {k: [] for k in ("raised", "error", "files_failed", "invalid facts", "census undercount",
                                  "not a node / self", "bad grade", "EXTRACTED across files", "edge without a call",
                                  "removal without a call", "removal in an errored caller", "nondeterministic",
                                  "flag-off copy")}
    seen = Counter()
    for i in range(N):
        prog = Program(rng, i).build()
        calls = prog.engine_calls()
        # the reader alone: every file's facts validate and the census never undercounts
        for rel, text in prog.files.items():
            try:
                lang = "javascript" if rel.endswith((".js", ".mjs")) else "typescript"
                ff = R.make_reader(TC.LANGUAGES[lang]).facts(rel, Src(prog.files))
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
            if prog.ledger.get(c[0], Counter())[seg(t["name"])] <= 0:
                viol["edge without a call"].append((i, caller["name"], t["name"]))
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
        if prog.errored:
            seen["errored programs"] += 1
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
    check("P the fuzz exercised typed edges, removals and parse errors",
          seen["typed"] > 200 and seen["removed"] > 20 and seen["errored programs"] > 10, dict(seen))


# ================================================================================================
# M: byte mutations of the fixture trees
# ================================================================================================

def sec_mutations():
    print(f"\n-- M: {N} seeded byte mutations of the S3c fixture sources --")
    try:
        import typed_calls_ts_gates as G   # noqa: E402 — the fixture trees
        trees = G.TREES
    except Exception as exc:  # noqa: BLE001
        check("M the S3c fixture trees import", False, repr(exc))
        return
    sources = [(rel, text) for t in trees.values() for rel, text in t.items()
               if rel.endswith((".ts", ".tsx", ".js", ".mjs"))]
    rng = random.Random(SEED + 1)
    viol = {"raised": [], "invalid": [], "error scope without a census margin": []}
    errored = 0
    junk = ["(", ")", "{", "}", "`", "'", '"', "/", "*", ";", ".", "=>", "<", ">", "@", "\n", " "]
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
        lang = "javascript" if rel.endswith((".js", ".mjs")) else "typescript"
        try:
            ff = R.make_reader(TC.LANGUAGES[lang]).facts(rel, Src({rel: mutated}))
        except Exception as exc:  # noqa: BLE001
            viol["raised"].append((i, rel, repr(exc)))
            continue
        if ff is None or TC.validate_facts(ff):
            viol["invalid"].append((i, rel, ff and TC.validate_facts(ff)))
            continue
        root = R.parse_bytes(mutated.encode("utf-8"), R.dialect_of(rel))
        errs = []
        stack = [root]
        while stack:
            x = stack.pop()
            if x.type == "ERROR" or x.is_missing:
                errs.append((x.start_byte, max(x.end_byte, x.start_byte + 1)))
            stack.extend(x.children)
        if not errs:
            continue
        errored += 1
        reader = R._FileReader(rel, lang, mutated.encode("utf-8"), root, TC)
        reader.run()
        for si, s in enumerate(reader.scopes):
            a, b = reader.ranges[si]
            if any(es < b and ee > a for es, ee in errs):
                c = Counter(x.member for x in s.sites)
                if any((s.all_names or {}).get(m, 0) <= n for m, n in c.items()):
                    viol["error scope without a census margin"].append((i, rel, s.name))
    for k, v in viol.items():
        check(f"M {k}: none", not v, v[:4])
    check("M the mutations produced parse errors to judge", errored > N // 6, errored)


def main() -> int:
    print("typed_calls TS/JS reader fuzz gates (S3c)")
    if TC is None or R is None:
        check("build/typed_calls.py and build/receiver_facts_ts.py import", False, _ERR)
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
