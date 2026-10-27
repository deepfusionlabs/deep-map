#!/usr/bin/env python3
"""DEEPMAP_CALLS_FAMILY_FILTER — the cross-family filter on the NAME branch of build/sync.py's _resolve_calls (decision
D0; receiver-calls design 6.1 / 7.3, slice S2). No Neo4j, no network; the real engine is never run (captured graphs).

graphify never resolves a call across languages, and the keyed branch already drops a key into another known language
family, but the name fan-out did not: a Python `Order()` reached a TypeScript `Order`, a Python `helper()` a Go
`helper`. Under the flag a name-resolved caller loses every target whose known family (edge_resolve.lang_family)
differs from its own, before the grade is counted.

  A  the reader (sync._calls_family_filter_enabled) and its classification in sync_status agree, and the default is
     OFF (until D0 / slice S8): unset and `0` give the identical graph on the 40-project synthetic corpus of
     tests/typed_calls_push_gates.py (whose section A locks the flag-off graph to the pre-S2 module byte for byte).
  B  the rule, by hand: a Python `Order()` never reaches a TS `Order` (and is INFERRED once alone); `.ts` -> `.js`,
     `.tsx` -> `.mjs` and `.erb` -> `.rb` are kept (one family); a caller or target with an unknown extension is never
     filtered; the ast path (receiver kinds) is filtered the same way; the keyed branch is unchanged by the flag;
     _attach_local_defs counts the kept targets (T3#6 then still grades a shadowed name AMBIGUOUS).
  C  D0's own assertions on the synthetic corpus (9 flag modes x 40 projects): the flag only REMOVES edges, every
     removed (caller, target) pair is cross-family, no same-family / unknown-family pair is ever removed.
  D  a real captured multi-language graph (S1's `mix` tree: Python, TS, JS and Go `helper`s), graphify 0.8.39 AND
     0.8.51, through every push pass: the Python / TS / Go callers keep only their own language's `helper`.
  E  mutation teeth: 5 mutants of build/sync.py, each must turn a check RED.

usage: python tests/calls_family_filter_gates.py [--root <tree holding build/>]
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import traceback
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _root(argv):
    root, it = None, iter(argv)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        else:
            raise SystemExit(f"usage: python tests/calls_family_filter_gates.py [--root <tree>] (unknown {arg!r})")
    if root is not None and not (Path(root) / "build").is_dir():
        raise SystemExit(f"--root needs a directory holding build/ (got {root!r})")
    return Path(root).resolve() if root else REPO


ROOT = _root(sys.argv[1:]) if __name__ == "__main__" else REPO
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))


def _load(path: Path, name: str, source=None):
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    code = compile(source if source is not None else path.read_text(encoding="utf-8"), str(path), "exec")
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)  # noqa: S102 — this repository's own module (or a mutant of it)
    finally:
        sys.modules.pop(name, None)
    return mod


import edge_resolve as ER      # noqa: E402  (imported BEFORE the helpers below, so --root's modules are the ones used)
import graphify_extract as GX  # noqa: E402
import sync as S               # noqa: E402
import sync_status as ST       # noqa: E402

# the shared helpers (flags pinned, temp dirs, the synthetic corpus, the push chain) of the S2 push gate
P = _load(REPO / "tests" / "typed_calls_push_gates.py", "dm_ff_push_helpers")
P.BUILD = BUILD

PASSES: list = []
FAILS: list = []
OFF = dict(P.OFF)
ON = dict(OFF, DEEPMAP_CALLS_FAMILY_FILTER="1")
node, flags, quiet, write_tree, tmp = P.node, P.flags, P.quiet, P.write_tree, P.tmp


class Rec:
    def __init__(self, loud=True):
        self.loud, self.failed, self.labels = loud, 0, []

    def check(self, name, ok, detail=""):
        if self.loud:
            (PASSES if ok else FAILS).append(name)
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" -- {str(detail)[:900]}"))
        if not ok:
            self.failed += 1
            self.labels.append(name)
        return bool(ok)


LOUD = Rec()


def resolve(Sm, nodes, calls, env):
    with flags(**env):
        return Sm._resolve_calls(copy.deepcopy(nodes), calls)


# =====================================================================================================
# A — the reader, its classification, the default
# =====================================================================================================
def a_reader(Sm, rec):
    fn = getattr(Sm, "_calls_family_filter_enabled", None)
    if not callable(fn):
        rec.check("A: sync defines _calls_family_filter_enabled", False)
        return
    table = {None: False, "": False, "  ": False, "1": True, "true": True, " YES ": True, "on": True, "0": False,
             "off": False, "no": False, "false": False, "maybe": False}
    got = {}
    for raw in table:
        with flags(**({} if raw is None else {"DEEPMAP_CALLS_FAMILY_FILTER": raw})):
            got[raw] = fn()
    rec.check("A: the reader — unset / blank -> the default (OFF), an ON-word -> on, anything else -> off",
              got == table and getattr(Sm, "_CALLS_FAMILY_FILTER_DEFAULT", None) is False, got)
    default_off = getattr(Sm, "_CALLS_FAMILY_FILTER_DEFAULT", None) is False
    rec.check("A: sync_status classifies the knob by that default (a default-off knob while "
              "_CALLS_FAMILY_FILTER_DEFAULT is False) — the auto-sync recipe records / replays it",
              ("DEEPMAP_CALLS_FAMILY_FILTER" in ST._DEFAULT_OFF_KNOBS) == default_off
              and ("DEEPMAP_CALLS_FAMILY_FILTER" in ST._DEFAULT_ON_KNOBS) == (not default_off)
              and "DEEPMAP_CALLS_FAMILY_FILTER" in ST.GRAPH_SHAPING_ENV)
    canon = {raw: ST.canonical_env_value("DEEPMAP_CALLS_FAMILY_FILTER", raw) == "on" for raw in table}
    rec.check("A: ... and its canonical value is 'on' exactly where the reader is on (same graph, same recipe)",
              canon == got, (canon, got))


def a_corpus_default(Sm, rec):
    bad = []
    for mode in ("off", "precise", "ast"):
        env, gen = P.MODES[mode]
        for seed in list(P.SEEDS)[:20]:
            files, nodes, calls = P.gen_project(seed, gen)
            root = write_tree(tmp() / f"ff_a_{id(Sm)}" / mode / f"s{seed}", files)
            outs = []
            for ff in (None, "0"):
                e = dict(env)
                e.pop("DEEPMAP_CALLS_FAMILY_FILTER", None)
                if ff is not None:
                    e["DEEPMAP_CALLS_FAMILY_FILTER"] = ff
                with flags(**e), quiet():
                    outs.append(P.canonical(*P.chain(Sm, root, nodes, calls)))
            if outs[0] != outs[1]:
                bad.append((mode, seed))
    rec.check("A: DEEPMAP_CALLS_FAMILY_FILTER unset == `0` (the default is off) on 60 synthetic projects",
              not bad, bad[:5])


# =====================================================================================================
# B — the rule, by hand
# =====================================================================================================
def b_nodes():
    return [node("svc_place", "Function", "place", "pkg/service.py", 3),
            node("models_order", "Class", "Order", "pkg/models.py", 1),
            node("web_order", "Class", "Order", "web/order.ts", 1),
            node("rb_order", "Class", "Order", "app/models/order.rb", 1),
            node("web_use", "Function", "use", "web/use.tsx", 2),
            node("lib_helper", "Function", "helper", "web/lib.mjs", 1),
            node("py_helper", "Function", "helper", "pkg/helpers.py", 1),
            node("view_show", "Function", "show", "app/views/show.html.erb", 1),
            node("rb_render", "Method", "Order.render", "app/models/order.rb", 3),
            node("x_proc", "Function", "proc", "misc/x.zzz", 1),
            node("y_helper", "Function", "helper", "misc/y.zzz", 1)]


def b_rules(Sm, rec):
    n = b_nodes()
    off = resolve(Sm, n, [("svc_place", "Order")], OFF)
    on = resolve(Sm, n, [("svc_place", "Order")], ON)
    rec.check("B: flag off — a Python `Order()` fans out to the Python, TS and Ruby `Order` (AMBIGUOUS: today's graph)",
              off == {("svc_place", "models_order"): "AMBIGUOUS", ("svc_place", "web_order"): "AMBIGUOUS",
                      ("svc_place", "rb_order"): "AMBIGUOUS"}, off)
    rec.check("B: flag on — the Python `Order()` reaches only the Python `Order`, and alone it is INFERRED",
              on == {("svc_place", "models_order"): "INFERRED"}, on)
    on = resolve(Sm, n, [("web_use", "helper")], ON)
    rec.check("B: `.tsx` -> `.mjs` (one js family) is kept; the Python helper goes; the unknown-extension helper "
              "stays (never filtered)",
              set(on) == {("web_use", "lib_helper"), ("web_use", "y_helper")}, on)
    on = resolve(Sm, n, [("view_show", "render")], ON)
    rec.check("B: `.erb` -> `.rb` (one ruby family) is kept", on == {("view_show", "rb_render"): "INFERRED"}, on)
    on = resolve(Sm, n, [("x_proc", "helper")], ON)
    rec.check("B: a caller with an unknown extension is never filtered (every helper stays)",
              set(on) == {("x_proc", "lib_helper"), ("x_proc", "py_helper"), ("x_proc", "y_helper")}, on)
    ts_js = [node("t_a", "Function", "a", "src/a.ts", 1), node("j_b", "Function", "b", "src/b.js", 1)]
    on = resolve(Sm, ts_js, [("t_a", "b")], ON)
    rec.check("B: `.ts` -> `.js` is kept", on == {("t_a", "j_b"): "INFERRED"}, on)
    ast_n = copy.deepcopy(n)
    ast_n[0]["call_recv"] = {"Order": ["bare"]}
    on = resolve(Sm, ast_n, [("svc_place", "Order")], ON)
    off = resolve(Sm, ast_n, [("svc_place", "Order")], OFF)
    rec.check("B: the ast path (receiver kinds) is filtered the same way",
              set(on) == {("svc_place", "models_order")} and len(off) == 3, (on, off))
    keyed = [("svc_place", GX.call_key("web_order")), ("svc_place", GX.call_key("models_order"))]
    on = resolve(Sm, n, keyed, dict(ON, DEEPMAP_PRECISE_CALLS="1"))
    off = resolve(Sm, n, keyed, dict(OFF, DEEPMAP_PRECISE_CALLS="1"))
    rec.check("B: the keyed branch is unchanged by the flag (it always dropped a cross-family key)",
              on == off == {("svc_place", "models_order"): "INFERRED"}, (on, off))


def b_local_defs(Sm, rec):
    root = write_tree(tmp() / f"ff_b_{id(Sm)}", {
        "pkg/service.py": "def place():\n    def Order():\n        return 0\n    return Order()\n",
        "pkg/models.py": "class Order:\n    pass\n", "web/order.ts": "export class Order {}\n"})
    n = [node("svc_place", "Function", "place", "pkg/service.py", 1),
         node("models_order", "Class", "Order", "pkg/models.py", 1),
         node("web_order", "Class", "Order", "web/order.ts", 1)]
    out = {}
    for label, env in (("off", OFF), ("on", ON)):
        ns = copy.deepcopy(n)
        with flags(**env), quiet():
            Sm._attach_local_defs(root, ns, [("svc_place", "Order")])
            out[label] = (ns[0].get("local_defs"), Sm._resolve_calls(ns, [("svc_place", "Order")]))
    rec.check("B: _attach_local_defs counts the targets the filter keeps: one cross-file Python `Order` -> the "
              "caller's scope (which defines its own `Order`) is attached, and T3#6 grades the edge AMBIGUOUS",
              out["on"][0] is not None and "Order" in out["on"][0]
              and out["on"][1] == {("svc_place", "models_order"): "AMBIGUOUS"}, out)
    rec.check("B: ... flag off: two candidates, no attribute needed (AMBIGUOUS both) — today's graph",
              out["off"][0] is None and len(out["off"][1]) == 2, out["off"])


# =====================================================================================================
# C — D0's assertions on the synthetic corpus
# =====================================================================================================
def c_corpus(Sm, rec):
    file_bad, added, same_removed, n_removed = [], [], [], 0
    for mode, (env, gen) in P.MODES.items():
        for seed in P.SEEDS:
            files, nodes, calls = P.gen_project(seed, gen)
            root = write_tree(tmp() / f"ff_c_{id(Sm)}" / mode / f"s{seed}", files)
            res = {}
            for ff in ("0", "1"):
                with flags(**dict(env, DEEPMAP_CALLS_FAMILY_FILTER=ff)), quiet():
                    ns, _cs, edges, _c = P.chain(Sm, root, nodes, calls)
                res[ff] = (edges, {x["key"]: x["source_file"] for x in ns})
            (e0, file_of), (e1, _f) = res["0"], res["1"]
            if set(e1) - set(e0):
                added.append((mode, seed, sorted(set(e1) - set(e0))[:2]))
            for a, b in set(e0) - set(e1):
                n_removed += 1
                fa, fb = ER.lang_family(file_of.get(a, "")), ER.lang_family(file_of.get(b, ""))
                if fa is None or fb is None:
                    same_removed.append((mode, seed, a, b, "unknown family"))
                elif fa == fb:
                    same_removed.append((mode, seed, a, b, "same family"))
            if not files:
                file_bad.append(seed)
    rec.check("C: the filter never ADDS an edge (9 modes x 40 projects)", not added, added[:3])
    rec.check(f"C: every removed (caller, target) pair is cross-family — none same-family, none of an unknown family "
              f"({n_removed} removed)", n_removed > 0 and not same_removed, same_removed[:3])


# =====================================================================================================
# D — a real captured multi-language graph (both engines)
# =====================================================================================================
def d_mix(Sm, rec):
    try:
        pfg = _load(REPO / "tests" / "precise_family_gates.py", "dm_ff_pfg")
        tree = pfg.TREES["mix"]
    except Exception as exc:  # noqa: BLE001
        rec.check("D: precise_family_gates.TREES['mix'] loads", False, repr(exc))
        return
    seen = []
    for bucket in ("0839", "0851"):
        p = REPO / "tests" / "fixtures" / "typed_calls" / bucket / "precise" / "mix.json"
        if not p.is_file():
            continue
        graph = json.loads(p.read_text(encoding="utf-8"))["graph"]
        root = write_tree(tmp() / f"ff_d_{id(Sm)}" / bucket, tree)
        out = {}
        for ff in ("0", "1"):
            with flags(**dict(OFF, DEEPMAP_CALLS_FAMILY_FILTER=ff)), quiet():
                nodes, calls, imports = GX.map_graph(copy.deepcopy(graph), root=root)
                nodes, _typed = Sm._apply_recognizers(root, nodes)
                ns, _cs, edges, _c = P.chain(Sm, root, nodes, calls)
            fo = {x["key"]: x["source_file"] for x in ns}
            out[ff] = sorted((fo.get(a), fo.get(b), c) for (a, b), c in edges.items())
        seen.append(bucket)
        helper_off = [e for e in out["0"] if e[1] and e[1].rsplit("/", 1)[-1] in ("b.py", "b.ts", "main.go")
                      and e[0] in ("py/a.py", "web/a.ts", "go/main.go")]
        helper_on = [e for e in out["1"] if e[1] and e[1].rsplit("/", 1)[-1] in ("b.py", "b.ts", "main.go")
                     and e[0] in ("py/a.py", "web/a.ts", "go/main.go")]
        cross_on = [e for e in out["1"] if ER.lang_family(e[0]) and ER.lang_family(e[1])
                    and ER.lang_family(e[0]) != ER.lang_family(e[1])]
        cross_off = [e for e in out["0"] if ER.lang_family(e[0]) and ER.lang_family(e[1])
                     and ER.lang_family(e[0]) != ER.lang_family(e[1])]
        rec.check(f"D [{bucket}]: flag off — today's graph fans the `helper` calls out across Python / TS / Go "
                  f"({len(cross_off)} cross-language edges)", len(cross_off) >= 2, helper_off)
        rec.check(f"D [{bucket}]: flag on — no cross-language CALLS edge is left; each `helper` caller keeps its own "
                  f"language's helper, now INFERRED",
                  not cross_on and ("py/a.py", "py/b.py", "INFERRED") in helper_on
                  and ("web/a.ts", "web/b.ts", "INFERRED") in helper_on, (helper_on, cross_on))
    rec.check("D: both engines' captures of the mix tree were replayed", seen == ["0839", "0851"], seen)


# =====================================================================================================
# E — mutation teeth
# =====================================================================================================
MUTANTS = [
    ("the filter never applied", "        if fam is not None and targets:\n            targets = _same_family(",
     "        if False and targets:\n            targets = _same_family(", "b"),
    ("an unknown-family target is dropped",
     "    return [t for t in targets if fam(file_of.get(t, \"\")) in (None, fa)]",
     "    return [t for t in targets if fam(file_of.get(t, \"\")) == fa]", "b"),
    ("the default turned ON", "_CALLS_FAMILY_FILTER_DEFAULT = False", "_CALLS_FAMILY_FILTER_DEFAULT = True", "a"),
    ("_attach_local_defs counts every family", "                targets = _same_family(rel, targets, file_of, fam)",
     "                targets = targets", "l"),
    ("the reader ignores ON-words", "    return raw in {\"1\", \"true\", \"yes\", \"on\"}\n\n\ndef _family_memo",
     "    return False\n\n\ndef _family_memo", "a"),
]


def e_mutants():
    print("\n-- E: mutation teeth (each mutant of build/sync.py must turn a check RED) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    for i, (label, old, new, which) in enumerate(MUTANTS):
        n = src.count(old)
        if n != 1:
            LOUD.check(f"E: mutant '{label}' — its anchor is found exactly once in sync.py", False, f"found {n}")
            continue
        try:
            Sm = _load(BUILD / "sync.py", f"dm_ff_mutant_{i}", src.replace(old, new))
        except Exception as exc:  # noqa: BLE001
            LOUD.check(f"E: mutant '{label}' loads", False, repr(exc))
            continue
        rec = Rec(loud=False)
        try:
            with quiet():
                if "a" in which:
                    a_reader(Sm, rec)
                    a_corpus_default(Sm, rec)
                if "b" in which:
                    b_rules(Sm, rec)
                if "l" in which:
                    b_local_defs(Sm, rec)
        except Exception as exc:  # noqa: BLE001
            rec.failed += 1
            rec.labels.append(f"crash: {exc!r}")
        LOUD.check(f"E: mutant '{label}' is caught ({rec.failed} check(s) RED)", rec.failed > 0, rec.labels[:2])


def main() -> int:
    print(f"calls_family_filter_gates — build: {BUILD}")
    try:
        for title, fn in (("A: the reader, its classification, the default (OFF)", lambda: (a_reader(S, LOUD),
                                                                                           a_corpus_default(S, LOUD))),
                          ("B: the rule, by hand", lambda: (b_rules(S, LOUD), b_local_defs(S, LOUD))),
                          ("C: D0's assertions on the synthetic corpus", lambda: c_corpus(S, LOUD)),
                          ("D: a captured multi-language graph, both engines", lambda: d_mix(S, LOUD))):
            print(f"\n-- {title} --")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — a crashing section is a RED section
                LOUD.check(f"{title.split(':')[0]} ran without crashing", False,
                           f"{exc!r}\n{traceback.format_exc()[-1500:]}")
        e_mutants()
    finally:
        while P._TMP:
            shutil.rmtree(str(P._TMP.pop()), ignore_errors=True)   # exactly the temp dir these checks created
    print(f"\ncalls_family_filter_gates: {len(PASSES)} passed, {len(FAILS)} failed")
    if FAILS:
        print("FAILED:\n  - " + "\n  - ".join(FAILS[:40]))
        print("calls_family_filter_gates: RED")
        return 1
    print("calls_family_filter_gates: GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
