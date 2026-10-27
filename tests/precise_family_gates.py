#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 gates (typed-receiver call-linking design §6.1, §7.1, §9 S1): DEEPMAP_PRECISE_CALLS scoped by LANGUAGE
FAMILY. No Neo4j, no network, no spend; the real-engine section runs the installed graphify under a temporary
DEEPMAP_HOME, so nothing is written to ~/.deepmap.

DEEPMAP_PRECISE_CALLS used to be one switch for the whole graph. It is now a set of edge_resolve language
families (`graphify_extract.precise_calls_families`):
  unset (or blank)        -> `_PRECISE_DEFAULT_FAMILIES` — EMPTY until the measured per-family flip (S8)
  0 / false / no / off    -> none
  1 / true / yes / on     -> every file, whatever its extension (today's opt-in)
  a CSV of family names   -> those families (`python,js`); a name edge_resolve does not know selects nothing
and map_graph keys an engine call only when the CALLER's file is in a selected family
(`graphify_extract.precise_for`), at each place it used to read the single switch: the engine call branch, the
R10/P1 receiver guard (built whenever any family is on) and the supplemental TS/Rust calls.
`precise_calls_enabled()` is "some family is on"; `keyed_calls_enabled()` keeps its formula (sync's push then
honours the marked keys exactly as under `1`).

Sections:
  A  the switch: the value table, precise_for per path, precise_calls_enabled / keyed_calls_enabled, every
     edge_resolve family is accepted, _PRECISE_DEFAULT_FAMILIES is empty.
  B  byte identity against FROZEN pre-S1 output, on EVERY captured graph.json the repository holds (the
     S1 precise trees on graphify 0.8.39 AND 0.8.51, plus the r7 / r8 / r10 / multilang / typescript_frameworks
     fixtures): unset and 0 (every spelling) give the pre-S1 default tuples and nodes; `1` with
     DEEPMAP_PRECISE_FIXES=0 gives the pre-S1 opt-in tuples; a CSV of every family = `1` wherever every file has
     a family; no node dict gains a key in any mode, and the fixes never change a node.
  C  a CSV selects families: on the mixed Python / TypeScript / JavaScript / Go tree, a tuple is keyed iff its
     caller's family is selected, on both engines' captures.
  D  the supplemental TS/Rust calls (DEEPMAP_SUPPLEMENTAL_TS_RUST) are scoped by the recovered caller's family.
  E  the installed engine's real extraction of every S1 tree equals that engine's captured fixture.
  F  S1b, the shared-reader seam (design 2.1; sync hands its typed-call pass's reader to extract_with_report(...,
     source=...), then to typed_calls.link(..., source=...)): map_graph(source=<a _Source of the same root>) gives
     the SAME nodes / calls / imports / stats as map_graph() on every captured graph with sources, reads the same
     files and builds no other reader; without root, for another root or for a non-_Source the given reader is not
     used; on the real engine extract_with_report / extract map through the caller's reader (the result is the plain
     tuple, unchanged) and leave it a filled, faithful cache (bytes / engine lines / ast equal a fresh read).

Fixtures (tests/fixtures/typed_calls/<0839|0851>/precise/):
  <tree>.json   the REAL graph.json graphify 0.8.39 / 0.8.51 wrote for TREES[<tree>] (`--capture` re-writes the
                installed engine's bucket; run it under both engines after editing a tree).
  frozen.json   map_graph of the PRE-S1 graphify_extract.py on every captured graph of that bucket, in the four
                FROZEN_MODES. It was made once, before the change, with
                `python tests/precise_family_gates.py --freeze <dir holding the pre-S1 graphify_extract.py>`;
                a captured graph without a frozen record is a FAIL, never a skip.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
FX = REPO / "tests" / "fixtures" / "typed_calls"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail != '' else ''}")


def counts() -> tuple:
    return _PASS, _FAIL


@contextlib.contextmanager
def clean_env(**kv):
    """Every DEEPMAP_* variable except DEEPMAP_HOME removed, then `kv` applied (None = unset); restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in list(saved):
            if k != "DEEPMAP_HOME":
                os.environ.pop(k, None)
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


# ================================================================================================
# The S1 source trees (each captured on graphify 0.8.39 and 0.8.51)
# ================================================================================================
def _ts_types() -> str:
    """src/types.ts of rekey_ts: zod's shape — 11 schema classes whose _parse returns OK(...) (one as
    OK<any>(...)), one DIRTY(...) caller, a commented `OK(x)` above a real call, `new Box(1)`, a call of an
    interface and `new OK(1)`. With parseUtil.ts's mergeValues that is 13 OK + 1 DIRTY calls the engine keys
    to the TYPE alias, zod's 14 (parseUtil.ts OK/DIRTY)."""
    out = ['import { OK, DIRTY, INVALID } from "./parseUtil";', 'import { Shape } from "./parseUtil";',
           'import { Box } from "./box";', '']
    for i, c in enumerate(["ZodString", "ZodNumber", "ZodBigInt", "ZodBoolean", "ZodDate", "ZodSymbol",
                           "ZodUndefined", "ZodNull", "ZodAny", "ZodUnknown", "ZodVoid"]):
        out += [f"export class {c} {{", "  _parse(input: any) {", "    if (input === null) return INVALID;",
                "    return OK<any>(input);" if i == 1 else "    return OK(input);", "  }", "}", ""]
    out += ["export class ZodNever {", "  _parse(input: any) {", "    return DIRTY(input);", "  }", "}", "",
            "export function wrapOk(x: number) {", "  // OK(x) is returned below", "  return OK(x);", "}", "",
            "export function makeBox() {", "  return new Box(1);", "}", "",
            "export function makeShape() {", "  return Shape();", "}", "",
            "export function newOk() {", "  return new OK(1);", "}", ""]
    return "\n".join(out)


TREES = {
    # The receiver probe's Python tree (design §7.1: "the probe fixture gives 11 vs 9 tuples today" under
    # precise CALLS — graphify 0.8.39 also links `Order()` / `Repo()` to a placeholder node of the annotation's
    # name, which map_graph does not keep; P1b drops that duplicate name).
    "probe_py": {
        "helpers.py": """\
def compute_total(x):
    return x


def format_money(x):
    return str(x)
""",
        "models.py": """\
class Order:
    def save(self):
        return 1

    def archive_order(self):
        return 2


class Invoice:
    def save(self):
        return 3


class Repo:
    def find(self):
        return 4

    def find_by_id(self, i):
        return 5


class Cache:
    def find(self):
        return 6
""",
        "service.py": """\
import helpers
import helpers as hp
from helpers import format_money as fm
from models import Order, Repo, Cache


def s1_typed_param(order: Order) -> None:
    order.save()
    order.archive_order()


def s2_local_ctor():
    o = Order()
    o.save()
    o.archive_order()


class Service:
    def __init__(self, repo: Repo):
        self.repo = repo          # s4: constructor-parameter injection
        self.cache = Cache()      # s3: field assigned a constructor in __init__

    def s3_s4_fields(self):
        self.repo.find()
        self.repo.find_by_id(1)
        self.cache.find()
        self.own_helper()

    def own_helper(self):
        return 0

    def get_repo(self) -> Repo:
        return self.repo

    def s7_self_chain(self):
        self.get_repo().find_by_id(2)


def get_repo() -> Repo:
    return Repo()


def s7_return_chain():
    get_repo().find()
    get_repo().find_by_id(3)


def s8_module_alias():
    helpers.compute_total(1)
    hp.compute_total(2)
    fm(3)


helpers.compute_total(4)   # s8b: module-level call


class Holder:
    repo: Repo                    # s5: typed field declaration (class-body annotation)

    def s5_typed_field(self):
        self.repo.find_by_id(9)


class Local:
    def persist_local(self):
        return 7


def s9_same_file_ctor():
    loc = Local()
    loc.persist_local()
""",
    },
    # P1b's must and must-not shapes: the annotation in `typed` makes graphify 0.8.39 create the placeholders.
    "p1b_py": {
        "shop/__init__.py": "",
        "shop/models.py": """\
class Order:
    def save(self):
        return 1


class Repo:
    def find(self):
        return 2
""",
        "shop/svc.py": """\
from shop.models import Order, Repo
from shop.ext import Widget


def typed(o: Order, r: Repo, w: Widget) -> None:
    return None


def once():
    return Order()


def twice():
    a = Order()
    b = Order()
    return a, b


def widget():
    return Widget()


def commented():
    # Order() is built below
    return Order()


def both():
    r = Repo()
    o = Order()
    return r, o


class Builder:
    def build(self):
        return Order()

    def build_two(self):
        return [Order() for _ in range(2)], Order()
""",
    },
    # The TS value-over-type re-key (zod parseUtil's `export type OK<T>` + `export const OK`), its must-nots and a
    # recovered declaration with a body call (`DIRTY` -> mark) for the supplemental-call scoping.
    "rekey_ts": {
        "src/parseUtil.ts": """\
export type INVALID = { status: "aborted" };
export const INVALID: INVALID = Object.freeze({
  status: "aborted",
});

export type DIRTY<T> = { status: "dirty"; value: T };
export const DIRTY = <T>(value: T): DIRTY<T> => mark({ status: "dirty", value });

export type OK<T> = { status: "valid"; value: T };
export const OK = <T>(value: T): OK<T> => ({ status: "valid", value });

export function mergeValues(a: number, b: number) {
  if (a < 0) return INVALID;
  return OK(a + b);
}

function mark<T>(x: T): T {
  return x;
}

export interface Shape {
  area(): number;
}
""",
        "src/types.ts": _ts_types(),
        "src/box.ts": """\
export class Box {
  constructor(public n: number) {}
  open() {
    return this.n;
  }
}

export function useBox() {
  const b = new Box(2);
  return b.open();
}
""",
        "src/fmt_a.ts": "export type Fmt = (x: number) => string;\n",
        "src/fmt_b.ts": "export const Fmt = (x: number): string => String(x);\n",
        "src/fmt_use.ts": """\
import { Fmt } from "./fmt_a";

export function show(x: number) {
  return Fmt(x);
}
""",
    },
    # The re-key's scope rules (review of S1, finding 1): which `OK(` names the module-level `const OK`. A member call
    # on a parameter, a shadowing parameter / local / arrow parameter, an unbound or shadowed namespace and a bare call
    # the file never imports give the name; a bare call (same file, or imported) and `ns.OK(` through an imported
    # namespace bind the const — also when the engine's one link sits on a `new OK(` line (0.8.51 dedupes links).
    "rekey_scope_ts": {
        "src/ok.ts": """\
export type OK<T> = { v: T };
export const OK = <T>(v: T): OK<T> => ({ v });

export class Res {
  wrap(r: any) {
    return r.OK(1);
  }
}

export function shadow(OK: (x: number) => number) {
  return OK(2);
}

export function local() {
  const OK = (x: number) => x;
  return OK(3);
}

export function nested(xs: number[]) {
  xs.forEach((OK) => OK);
  return OK(4);
}

export function arrowOnly(xs: any[]) {
  return xs.map((OK: any) => OK(5));
}

export function firstNew() {
  const a = new OK(6);
  return [a, OK(7)];
}
""",
        "src/use.ts": """\
import * as ns from "./ok";
import { OK } from "./ok";

export function viaNs() {
  return ns.OK(1);
}

export function viaBare() {
  return OK(2);
}

export function viaObj(r: any) {
  return r.OK(3);
}

export function nsShadow(ns: any) {
  return ns.OK(4);
}
""",
        "src/noimp.ts": """\
export function noImport() {
  return OK(1);
}
""",
    },
    # One resolved call per family: Python, TypeScript, JavaScript (CommonJS) and Go.
    "mix": {
        "py/__init__.py": "",
        "py/a.py": """\
from py.b import helper, Thing


def run():
    helper()
    return Thing()
""",
        "py/b.py": """\
def helper():
    return 1


class Thing:
    pass
""",
        "web/a.ts": """\
import { helper } from "./b";

export function run() {
  return helper();
}
""",
        "web/b.ts": """\
export function helper() {
  return 1;
}
""",
        "web/c.js": """\
const { util } = require("./d");

function go() {
  return util();
}

module.exports = { go };
""",
        "web/d.js": """\
function util() {
  return 2;
}

module.exports = { util };
""",
        "go/main.go": """\
package main

func helper() int {
\treturn 1
}

func main() {
\thelper()
}
""",
    },
}

FROZEN_MODES = {
    "base": {},
    "plus": {"DEEPMAP_GRAPHIFY_CALLS_PLUS": "1", "DEEPMAP_SUPPLEMENTAL_TS_RUST": "1"},
    "p1": {"DEEPMAP_PRECISE_CALLS": "1"},
    "p1all": {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "1", "DEEPMAP_SUPPLEMENTAL_TS_RUST": "1"},
}

# the column order tests/fixtures/graphify_r10 stores its graphs in (extraction_calls_r10_gates._KEEP_N / _KEEP_L)
_R10_N = ("id", "label", "source_file", "source_location", "file_type")
_R10_L = ("relation", "source", "target", "source_location")

_TMP_HOLDER: list = []


def tmp() -> Path:
    if not _TMP_HOLDER:
        _TMP_HOLDER.append(Path(tempfile.mkdtemp(prefix="dm_s1p_")))
    return _TMP_HOLDER[0]


def cleanup() -> None:
    if _TMP_HOLDER:
        shutil.rmtree(_TMP_HOLDER.pop(), ignore_errors=True)


def tree_sha(name: str) -> str:
    return hashlib.sha256(json.dumps(TREES[name], sort_keys=True).encode("utf-8")).hexdigest()


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


_ROOTS: dict = {}


def root_for(bucket: str, eid: str, files) -> Path | None:
    """The tree of entry `eid` materialized once under the gate's temp dir (None for a graph without sources)."""
    if files is None:
        return None
    key = (bucket, eid)
    if key not in _ROOTS:
        _ROOTS[key] = write_tree(tmp() / "src" / bucket / eid.replace("/", "__"), files)
    return _ROOTS[key]


def fixture_path(bucket: str, name: str) -> Path:
    return FX / bucket / "precise" / f"{name}.json"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def precise_graph(bucket: str, name: str) -> dict:
    """A fresh copy of the captured graph.json of S1 tree `name` for engine bucket `bucket`."""
    return load_json(fixture_path(bucket, name))["graph"]


def entries() -> list:
    """[(bucket, entry id, graph, files or None)] — every captured graph.json the repository holds: the S1 trees
    (per engine), the r7 / r8 / r10 trees (with their sources, so the source-reading corrections run) and the
    multilang / typescript_frameworks graphs (pure: no sources)."""
    out: list = []
    for name in TREES:
        for b in BUCKETS.values():
            p = fixture_path(b, name)
            if p.is_file():
                out.append((b, f"precise/{name}", load_json(p)["graph"], TREES[name]))
    fx = REPO / "tests" / "fixtures"
    r7 = load_json(fx / "graphify_r7" / "extraction_r7.json")
    for t in sorted(k for k in r7 if not k.startswith("_")):
        out.append(("0839", f"r7/{t}", r7[t]["graph"], r7[t]["files"]))
    r8 = load_json(fx / "graphify_r8" / "extraction_r8.json")
    for ver in sorted(r8["graphs"]):
        for t in sorted(r8["graphs"][ver]):
            files = r8["trees"][t]["files"] if t in r8["trees"] else r7[t]["files"]
            out.append((BUCKETS[ver], f"r8/{t}", r8["graphs"][ver][t], files))
    r10 = load_json(fx / "graphify_r10" / "extraction_calls_r10.json")
    for t in sorted(r10["graphs"]):
        g = r10["graphs"][t]
        out.append(("0839", f"r10/{t}", {"nodes": [dict(zip(_R10_N, n)) for n in g["nodes"]],
                                          "links": [dict(zip(_R10_L, l)) for l in g["links"]]},
                    r10["trees"][t]))
    for p in sorted((fx / "graphify_multilang").glob("*.json")):
        g = load_json(p)
        out.append(("0839", f"multilang/{p.stem}", {"nodes": g.get("nodes", []), "links": g.get("links", [])}, None))
    g = load_json(fx / "typescript_frameworks" / "graph_0839.json")
    out.append(("0839", "tsfw/graph_0839", {"nodes": g.get("nodes", []), "links": g.get("links", [])}, None))
    return out


def outputs(gx, graph: dict, root, env: dict, stats: dict | None = None) -> dict:
    """map_graph of module `gx` on a copy of `graph` under exactly `env`, canonicalized: the nodes' digest and
    key set, every CALLS tuple with its key mark (in order) and every import."""
    with clean_env(**env):
        nodes, calls, imports = gx.map_graph(json.loads(json.dumps(graph)), root=root, stats=stats)
    return {"nodes_sha": hashlib.sha256(json.dumps(nodes, sort_keys=True).encode("utf-8")).hexdigest(),
            "node_keys": sorted({k for n in nodes for k in n}),
            "calls": [[str(s), str(c), bool(gx.is_call_key(c))] for s, c in calls],
            "imports": [[str(s), str(t)] for s, t in imports]}


def frozen(bucket: str) -> dict | None:
    p = FX / bucket / "precise" / "frozen.json"
    return load_json(p) if p.is_file() else None


def source_families(graph: dict) -> set:
    """The edge_resolve families of every code node's source file of `graph` (None for one without)."""
    import edge_resolve
    return {edge_resolve.lang_family(n.get("source_file")) for n in graph.get("nodes", [])
            if n.get("file_type") == "code" and str(n.get("source_file") or "")}


def all_families_csv() -> str:
    import edge_resolve
    return ",".join(sorted(edge_resolve._FAMILY_EXTS))


# ================================================================================================
# A — the switch
# ================================================================================================
def gate_a_switch() -> None:
    print("\n-- A: DEEPMAP_PRECISE_CALLS is a set of language families --")
    import edge_resolve
    fams_fn = getattr(GX, "precise_calls_families", None)
    for_fn = getattr(GX, "precise_for", None)
    check("graphify_extract.precise_calls_families and precise_for exist", callable(fams_fn) and callable(for_fn))
    if not (callable(fams_fn) and callable(for_fn)):
        return
    default = getattr(GX, "_PRECISE_DEFAULT_FAMILIES", None)
    check("_PRECISE_DEFAULT_FAMILIES is EMPTY until the measured per-family flip (S8)",
          isinstance(default, frozenset) and not default, default)
    for v in (None, "", "   "):
        with clean_env(DEEPMAP_PRECISE_CALLS=v):
            check(f"PRECISE={v!r} -> the defaults; precise_calls_enabled() False; keyed_calls_enabled() False",
                  fams_fn() == default and GX.precise_calls_enabled() is False and GX.keyed_calls_enabled() is False,
                  (fams_fn(), GX.precise_calls_enabled()))
    for v in ("0", "false", "no", "off", "OFF", " 0 ", "False"):
        with clean_env(DEEPMAP_PRECISE_CALLS=v):
            check(f"PRECISE={v!r} -> no family; nothing is precise",
                  fams_fn() == frozenset() and not GX.precise_calls_enabled()
                  and not any(for_fn(p) for p in ("a.py", "a.ts", "a.go", "a.weird", "", None)), fams_fn())
    for v in ("1", "true", "yes", "on", "ON", " 1 "):
        with clean_env(DEEPMAP_PRECISE_CALLS=v):
            check(f"PRECISE={v!r} -> every file, any extension or none (today's opt-in)",
                  GX.precise_calls_enabled() is True and GX.keyed_calls_enabled() is True
                  and all(for_fn(p) for p in ("a.py", "x/b.TS", "c.go", "d.pas", "e.weird", "noext", "", None)),
                  fams_fn())
    with clean_env(DEEPMAP_PRECISE_CALLS="python"):
        got = {p: for_fn(p) for p in ("a.py", "pkg/A.PY", "s.pyi", "a.ts", "a.js", "a.go", "a.weird", "", None)}
        check("PRECISE=python: .py / .PY / .pyi only",
              got == {"a.py": True, "pkg/A.PY": True, "s.pyi": True, "a.ts": False, "a.js": False, "a.go": False,
                      "a.weird": False, "": False, None: False}, got)
        check("PRECISE=python: precise_calls_enabled() and keyed_calls_enabled() are True",
              GX.precise_calls_enabled() is True and GX.keyed_calls_enabled() is True)
    with clean_env(DEEPMAP_PRECISE_CALLS="js"):
        got = {p: for_fn(p) for p in ("a.ts", "a.tsx", "a.js", "a.jsx", "a.mjs", "a.cjs", "a.py", "a.rb")}
        check("PRECISE=js: every TS / JS extension, nothing else",
              got == {"a.ts": True, "a.tsx": True, "a.js": True, "a.jsx": True, "a.mjs": True, "a.cjs": True,
                      "a.py": False, "a.rb": False}, got)
    for v, want in ((" Python , JS ", {"python", "js"}), ("python,,js", {"python", "js"}),
                    ("python,pyhton", {"python"}), ("GO", {"go"})):
        with clean_env(DEEPMAP_PRECISE_CALLS=v):
            check(f"PRECISE={v!r} -> {sorted(want)} (case and blanks ignored, an unknown name dropped)",
                  fams_fn() == frozenset(want), fams_fn())
    with clean_env(DEEPMAP_PRECISE_CALLS="pyhton,typescript"):
        check("PRECISE of unknown names only -> no family: precise and keyed calls stay OFF (no graph-wide keyed "
              "mode from a typo)", fams_fn() == frozenset() and not GX.precise_calls_enabled()
              and not GX.keyed_calls_enabled(), fams_fn())
    with clean_env(DEEPMAP_PRECISE_CALLS="0", DEEPMAP_GRAPHIFY_CALLS_PLUS="1"):
        check("keyed_calls_enabled() keeps its formula: CALLS_PLUS alone still keys its additions",
              GX.keyed_calls_enabled() is True and GX.precise_calls_enabled() is False)
    names = sorted(edge_resolve._FAMILY_EXTS)
    every_ext = sorted({e for exts in edge_resolve._FAMILY_EXTS.values() for e in exts})
    bad = []
    for fam in names:
        with clean_env(DEEPMAP_PRECISE_CALLS=fam):
            own = set(edge_resolve._FAMILY_EXTS[fam])
            if fams_fn() != frozenset({fam}) or for_fn("x.unknownext") \
                    or any(for_fn(f"d/x{e.upper()}") != (e in own) for e in every_ext):
                bad.append(fam)
    check(f"every edge_resolve family ({len(names)}) is accepted and selects exactly its own extensions, in any "
          f"case (.ts and .js are one family, .erb is ruby's)", not bad, bad)
    check("precise_for(path, families) takes an explicit family set",
          for_fn("a.py", frozenset({"python"})) is True and for_fn("a.ts", frozenset({"python"})) is False
          and for_fn("a.ts", frozenset()) is False)
    # an explicit value is read exactly like the environment's (a recorded build recipe can be compared by what it
    # selects: `python,pyhton` and `python` build the same CALLS) — the environment is not consulted then
    unknown_fn = getattr(GX, "precise_calls_unknown", None)
    check("graphify_extract.precise_calls_unknown exists", callable(unknown_fn))
    if callable(unknown_fn):
        with clean_env(DEEPMAP_PRECISE_CALLS="1"):
            got = {v: (fams_fn(v), unknown_fn(v)) for v in ("python,pyhton", " GO ", "0", "on", "", "all",
                                                            "js,typescript,ts")}
            env_now = (fams_fn(), unknown_fn())
        want = {"python,pyhton": (frozenset({"python"}), ["pyhton"]), " GO ": (frozenset({"go"}), []),
                "0": (frozenset(), []), "on": (frozenset({GX._PRECISE_ALL}), []), "": (default, []),
                "all": (frozenset(), ["all"]), "js,typescript,ts": (frozenset({"js"}), ["ts", "typescript"])}
        check("precise_calls_families(value) / precise_calls_unknown(value) read an explicit value (the environment's "
              "`1` ignored), unknown names sorted", got == want, got)
        check("...and with no value they read the environment", env_now == (frozenset({GX._PRECISE_ALL}), []), env_now)
        for v in (None, "", "0", "off", "1", "true", "python", "python,js", "go,rust,jvm,dotnet,ruby"):
            with clean_env(DEEPMAP_PRECISE_CALLS=v):
                if unknown_fn():
                    check(f"PRECISE={v!r}: no unknown family name", False, unknown_fn())


# ================================================================================================
# B — byte identity against the frozen pre-S1 output
# ================================================================================================
def _same(a: dict, b: dict, *, nodes=True) -> bool:
    keys = ("nodes_sha", "node_keys", "calls", "imports") if nodes else ("calls", "imports")
    return all(a.get(k) == b.get(k) for k in keys)


def gate_b_identity() -> None:
    print("\n-- B: unset / 0 / `1`+FIXES=0 are byte-identical to the frozen pre-S1 output on every captured graph --")
    fz = {b: frozen(b) for b in BUCKETS.values()}
    check("frozen pre-S1 outputs exist for the 0.8.39 and the 0.8.51 bucket", all(fz.values()), sorted(fz))
    if not all(fz.values()):
        return
    check("both frozen files were made from ONE pre-S1 module, in the four FROZEN_MODES",
          len({f.get("module_sha256") for f in fz.values()}) == 1
          and all(f.get("modes") == FROZEN_MODES for f in fz.values()))
    if not callable(getattr(GX, "precise_calls_families", None)):
        check("the S1 switch exists (precise_calls_families)", False)
    allcsv = all_families_csv()
    n_ent = n_csv = 0
    for b, eid, graph, files in entries():
        rec = fz[b]["entries"].get(eid)
        if rec is None:
            check(f"{b}/{eid}: a frozen pre-S1 record exists (re-freeze after adding a captured graph)", False)
            continue
        n_ent += 1
        root = root_for(b, eid, files)
        thorough = eid.startswith("precise/")
        offs = (None, "0", "false", "no", "off", "OFF", " 0 ", "", "  ") if thorough else (None, "0", "off", "")
        bad = [v for v in offs if not _same(outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": v}), rec["base"])]
        check(f"{b}/{eid}: PRECISE unset / 0 / off / blank -> the pre-S1 default (tuples, keys, nodes)", not bad, bad)
        bad = [v for v in (None, "0")
               if not _same(outputs(GX, graph, root, {**FROZEN_MODES["plus"], "DEEPMAP_PRECISE_CALLS": v}),
                            rec["plus"])]
        check(f"{b}/{eid}: CALLS_PLUS + SUPPLEMENTAL with PRECISE unset / 0 -> the pre-S1 output", not bad, bad)
        ons = ("1", "true", "yes", "on", "ON") if thorough else ("1",)
        bad = [v for v in ons if not _same(outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": v,
                                                                    "DEEPMAP_PRECISE_FIXES": "0"}), rec["p1"])]
        check(f"{b}/{eid}: PRECISE=1 + DEEPMAP_PRECISE_FIXES=0 -> today's opt-in output byte for byte", not bad, bad)
        got = outputs(GX, graph, root, {**FROZEN_MODES["p1all"], "DEEPMAP_PRECISE_FIXES": "0"})
        check(f"{b}/{eid}: PRECISE=1 + CALLS_PLUS + SUPPLEMENTAL + FIXES=0 -> today's output", _same(got, rec["p1all"]))
        on = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": "1"})
        check(f"{b}/{eid}: the fixes never touch a node (PRECISE=1, fixes ON: same nodes as before)",
              on["nodes_sha"] == rec["p1"]["nodes_sha"] and on["node_keys"] == rec["p1"]["node_keys"])
        if None not in source_families(graph):
            n_csv += 1
            got = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": allcsv, "DEEPMAP_PRECISE_FIXES": "0"})
            check(f"{b}/{eid}: a CSV of every family + FIXES=0 == `1` (every file here has a family)",
                  _same(got, rec["p1"]))
        allowed = set(rec["base"]["node_keys"]) | set(rec["p1"]["node_keys"])
        widen = []
        for csv in ("python", "js", "python,js", "go,rust,jvm,dotnet,ruby"):
            got = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": csv})
            if not set(got["node_keys"]) <= allowed:
                widen.append((csv, sorted(set(got["node_keys"]) - allowed)))
        check(f"{b}/{eid}: no node dict gains a key under any family CSV", not widen, widen)
    check(f"every captured graph was compared ({n_ent} graphs, {n_csv} also against the all-family CSV)",
          n_ent >= 40 and n_csv >= 10, (n_ent, n_csv))


# ================================================================================================
# C — a CSV selects families (the mixed tree)
# ================================================================================================
def gate_c_family_selection() -> None:
    print("\n-- C: a CSV keys exactly the callers of the selected families (Python / TS / JS / Go tree) --")
    import edge_resolve
    for ver, b in BUCKETS.items():
        if not fixture_path(b, "mix").is_file():
            check(f"{ver}: the mix tree is captured", False)
            continue
        graph = precise_graph(b, "mix")
        root = root_for(b, "precise/mix", TREES["mix"])
        fam_of = {n["id"]: edge_resolve.lang_family(n.get("source_file")) for n in graph["nodes"]}
        base = outputs(GX, graph, root, {})["calls"]
        full = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": "1"})["calls"]
        check(f"{ver}: `1` keys one call per family (python 2, js 2, go 1) and the default keys none",
              sorted(fam_of.get(s) for s, _c, k in full if k) == ["go", "js", "js", "python", "python"]
              and not any(k for _s, _c, k in base) and len(base) == len(full) == 5, (base, full))
        for csv in ("python", "js", "go", "python,js", "js,go", "rust"):
            want = set(csv.split(","))
            got = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": csv})["calls"]
            exp = [f if fam_of.get(f[0]) in want else c for c, f in zip(base, full)]
            check(f"{ver}: PRECISE={csv}: a tuple is keyed iff its caller's family is selected, else today's name",
                  got == exp, (got, exp))
        got = outputs(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": "python,js,go"})["calls"]
        check(f"{ver}: PRECISE=python,js,go == PRECISE=1 on this tree (every caller has one of them)", got == full)


# ================================================================================================
# D — the supplemental TS/Rust calls follow the recovered caller's family
# ================================================================================================
def gate_d_supplemental() -> None:
    print("\n-- D: DEEPMAP_SUPPLEMENTAL_TS_RUST calls are keyed only for a precise family --")
    for ver, b in BUCKETS.items():
        if not fixture_path(b, "rekey_ts").is_file():
            check(f"{ver}: the rekey_ts tree is captured", False)
            continue
        graph = precise_graph(b, "rekey_ts")
        root = root_for(b, "precise/rekey_ts", TREES["rekey_ts"])
        sup = {"DEEPMAP_SUPPLEMENTAL_TS_RUST": "1"}

        def marks(precise):
            calls = outputs(GX, graph, root, {**sup, "DEEPMAP_PRECISE_CALLS": precise})["calls"]
            return sorted((s, c, k) for s, c, k in calls if "@DIRTY:" in s)

        js, py, one, off = marks("js"), marks("python"), marks("1"), marks(None)
        check(f"{ver}: the recovered `const DIRTY` calls mark(): a KEY under PRECISE=js and PRECISE=1",
              js == one and len(js) == 1 and js[0][1] == "src_parseutil_mark" and js[0][2] is True, (js, one))
        check(f"{ver}: ...and the plain name under PRECISE=python (a TS caller is not precise there) == the default",
              py == off and len(py) == 1 and py[0][1:] == ("mark", False), (py, off))


# ================================================================================================
# E — the installed engine's real extraction equals its captured fixture
# ================================================================================================
def real_graphs() -> dict:
    """{tree: graph.json} the INSTALLED engine writes for every S1 tree (temporary DEEPMAP_HOME)."""
    out: dict = {}
    home = tmp() / "home_real"
    with clean_env(DEEPMAP_HOME=str(home)):
        for name in TREES:
            root = write_tree(tmp() / "real" / name, TREES[name])
            res, rep = GX.extract_with_report(root)
            gp = GX.graphify_out_dir(root) / "graph.json"
            out[name] = load_json(gp) if (res is not None and gp.is_file()) else None
            if out[name] is None:
                print(f"  (engine failed on {name}: {rep.get('cause')})")
    return out


def _sorted_calls(o: dict) -> list:
    return sorted(tuple(c) for c in o["calls"])


def gate_e_real_engine() -> None:
    print("\n-- E: the installed engine's extraction of every S1 tree == its captured fixture --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    print(f"  (graphify {ver})")
    if b is None:
        print(f"  (no S1 capture for graphify {ver}: only 0.8.39 and 0.8.51 are captured; comparison skipped)")
        return
    real = real_graphs()
    for name in TREES:
        if real.get(name) is None or not fixture_path(b, name).is_file():
            check(f"REAL {name}: the engine produced a graph and the fixture exists", False)
            continue
        fx = load_json(fixture_path(b, name))
        check(f"REAL {name}: the fixture was captured from THIS tree (re-capture after editing it)",
              fx.get("tree_sha256") == tree_sha(name))
        root = root_for(b, f"precise/{name}", TREES[name])
        bad = []
        for mode in ("base", "p1"):
            a = outputs(GX, real[name], root, FROZEN_MODES[mode])
            f = outputs(GX, fx["graph"], root, FROZEN_MODES[mode])
            if _sorted_calls(a) != _sorted_calls(f) or a["nodes_sha"] != f["nodes_sha"] \
                    or sorted(map(tuple, a["imports"])) != sorted(map(tuple, f["imports"])):
                bad.append(mode)
        check(f"REAL {name}: same nodes / calls / imports as the {ver} fixture (default and PRECISE=1)", not bad, bad)


# ================================================================================================
# F — S1b: the shared-reader seam
# ================================================================================================
def canon(gx, nodes, calls, imports) -> dict:
    """outputs()' canonical form of an already-mapped result."""
    return {"nodes_sha": hashlib.sha256(json.dumps(nodes, sort_keys=True).encode("utf-8")).hexdigest(),
            "node_keys": sorted({k for n in nodes for k in n}),
            "calls": [[str(s), str(c), bool(gx.is_call_key(c))] for s, c in calls],
            "imports": [[str(s), str(t)] for s, t in imports]}


@contextlib.contextmanager
def recorded_sources():
    """Every GX._Source built inside the block, recorded (a recorder seam: nothing raises, so nothing is hidden)."""
    made: list = []
    orig = GX._Source.__init__

    def init(self, root):
        made.append(self)
        orig(self, root)

    GX._Source.__init__ = init
    try:
        yield made
    finally:
        GX._Source.__init__ = orig


F_MODES = ("base", "p1all")


def gate_f_source_seam() -> None:
    print("\n-- F: S1b — a caller's _Source is read through (map_graph / extract_with_report source=); nothing changes --")
    import inspect
    sig_ok = all("source" in inspect.signature(getattr(GX, fn)).parameters
                 for fn in ("map_graph", "extract_with_report", "extract"))
    check("F0 map_graph, extract_with_report and extract take `source=` (sync's typed-call pass probes "
          "extract_with_report's signature for it)", sig_ok)
    if not sig_ok:
        return
    # F1 — every captured graph with sources: same output, same stats, same files read, no second reader
    bad_out, bad_reads, bad_extra, n_read = [], [], [], 0
    for b, eid, graph, files in entries():
        if files is None:
            continue
        root = root_for(b, eid, files)
        for mode in F_MODES:
            env = FROZEN_MODES[mode]
            st0, st1 = {}, {}
            with recorded_sources() as made0, clean_env(**env):
                ref = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), root=root, stats=st0))
            shared = GX._Source(root)
            with recorded_sources() as made1, clean_env(**env):
                got = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), root=root, stats=st1, source=shared))
            if got != ref or st0 != st1:
                bad_out.append(f"{b}/{eid}/{mode}")
            if made1:
                bad_extra.append(f"{b}/{eid}/{mode}: {len(made1)}")
            read0 = set(made0[0]._bytes) if len(made0) == 1 else None
            if read0 is None or set(shared._bytes) != read0 or set(shared._ast) != set(made0[0]._ast) \
                    or set(shared._ts) != set(made0[0]._ts):
                bad_reads.append(f"{b}/{eid}/{mode}")
            n_read += bool(shared._bytes)
    check("F1 map_graph(source=<a _Source of the same root>) == map_graph() on every captured graph with sources "
          "(nodes, marked calls, imports and the stats dict; flags off and PRECISE + CALLS_PLUS + SUPPLEMENTAL)",
          not bad_out, bad_out[:8])
    check("F1 ... and it reads exactly the files (bytes, ast, tree-sitter trees) a fresh reader would",
          not bad_reads, bad_reads[:8])
    check("F1 ... through the GIVEN reader only: no other _Source is built", not bad_extra, bad_extra[:8])
    check("F1 the given reader was really read (it holds file bytes on most entries)", n_read >= 10, n_read)

    # F2 — without root the mapping stays pure (the reader is ignored); a reader of ANOTHER root is not used
    b, eid, graph, files = next(e for e in entries() if e[1] == "precise/probe_py")
    root = root_for(b, eid, files)
    shared = GX._Source(root)
    with clean_env(DEEPMAP_GRAPHIFY_CALLS_PLUS="1"):
        pure = canon(GX, *GX.map_graph(json.loads(json.dumps(graph))))
        with_src = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), source=shared))
    check("F2 no root: a given reader is ignored (pure output, nothing read)",
          with_src == pure and not shared._bytes, (with_src == pure, len(shared._bytes)))
    other = GX._Source(tmp() / "elsewhere")
    with recorded_sources() as made, clean_env(DEEPMAP_GRAPHIFY_CALLS_PLUS="1"):
        ref = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), root=root))
        n_ref = len(made)
        got = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), root=root, source=other))
    check("F2 a reader of another root is not used: a fresh one is built and the output is unchanged",
          got == ref and len(made) - n_ref == 1 and not other._bytes, (got == ref, len(made) - n_ref, len(other._bytes)))
    with recorded_sources() as made, clean_env(DEEPMAP_GRAPHIFY_CALLS_PLUS="1"):
        got = canon(GX, *GX.map_graph(json.loads(json.dumps(graph)), root=root, source=object()))
    check("F2 something that is not a _Source is not used either", got == ref and len(made) == 1, len(made))

    # F3 — the real engine: extract_with_report / extract map through the caller's reader, which is then a faithful,
    # already-filled cache; the result is exactly the one without it
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed — F3 (the real extraction through a caller's reader) is not run")
        return
    import ast as _ast
    files = dict(TREES["probe_py"])
    root = write_tree(tmp() / "seam" / "probe_py", files).resolve()
    py = sorted(r for r in files if r.endswith(".py"))
    shared = GX._Source(root)
    elsewhere = GX._Source(tmp() / "elsewhere")
    with clean_env(DEEPMAP_HOME=str(tmp() / "home_seam"), DEEPMAP_GRAPHIFY_CALLS_PLUS="1"), \
            contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        res, rep = GX.extract_with_report(root, source=shared)
        already = [r for r in py if r in shared._bytes and shared._ast.get(r) is not None]   # before any other read
        res0, _rep0 = GX.extract_with_report(root)
        res1 = GX.extract(root, source=elsewhere)
        shared2 = GX._Source(root)
        res2 = GX.extract(root, source=shared2)
    if res is None or res0 is None:
        check("F3 the engine produced a graph of probe_py", False, rep.get("cause"))
        return
    check("F3 extract_with_report(source=...) returns the plain (nodes, calls, imports) tuple, equal to the run "
          "without a reader", type(res) is tuple and canon(GX, *res) == canon(GX, *res0), type(res).__name__)
    check("F3 the caller's reader is the one map_graph read with: it already holds every Python file's bytes and "
          "ast (CALLS_PLUS parses each), so a later pass parses nothing again", already == py, (already, py))
    ref = GX._Source(root)
    bad = [r for r in py if shared.raw(r) != (root / r).read_bytes() or shared.lines(r) != ref.lines(r)
           or _ast.dump(shared.tree(r)) != _ast.dump(ref.tree(r))]
    check("F3 ... and a faithful cache: every Python file's bytes, engine lines and ast equal a fresh read", not bad, bad)
    check("F3 extract(source=<a reader of another root>) ignores it (unread) and returns the same graph",
          res1 is not None and canon(GX, *res1) == canon(GX, *res0) and not elsewhere._bytes, len(elsewhere._bytes))
    check("F3 extract(source=...) maps through it too", res2 is not None and canon(GX, *res2) == canon(GX, *res0)
          and all(r in shared2._bytes for r in py))


# ================================================================================================
# --capture / --freeze
# ================================================================================================
def _relative(graph: dict, root: Path) -> dict:
    """`graph` with every string field that holds an absolute path under `root` (0.8.51's stub `origin_file`)
    made root-relative with forward slashes, so a capture holds no machine path."""
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def do_capture() -> int:
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    real = real_graphs()
    for name, g in real.items():
        if g is None:
            print(f"--capture: the engine failed on {name}")
            return 1
        p = fixture_path(b, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                          f"tests/precise_family_gates.TREES[{name!r}], written by extract_with_report (absolute "
                          "paths made root-relative; map_graph reads none of them); re-capture with "
                          "`python tests/precise_family_gates.py --capture` under this engine."),
               "engine": ver, "tree_sha256": tree_sha(name), "graph": _relative(g, tmp() / "real" / name)}
        p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(f"--capture: wrote {p.relative_to(REPO)} ({len(g.get('nodes', []))} nodes, "
              f"{len(g.get('links', []))} links)")
    return 0


def do_freeze(pre_dir: str) -> int:
    path = Path(pre_dir) / "graphify_extract.py"
    spec = importlib.util.spec_from_file_location("gx_pre_s1", str(path))
    gx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gx)
    if hasattr(gx, "precise_calls_families"):
        print("--freeze: that module already has precise_calls_families — not the pre-S1 module")
        return 1
    sha = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()   # git's (LF) bytes
    data: dict = {}
    for b, eid, graph, files in entries():
        root = root_for(b, eid, files)
        data.setdefault(b, {})[eid] = {m: outputs(gx, graph, root, env) for m, env in FROZEN_MODES.items()}
    for b, ents in data.items():
        p = FX / b / "precise" / "frozen.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_about": ("map_graph of the PRE-S1 build/graphify_extract.py (sha256 below) on every captured "
                          "graph.json of this engine bucket (tests/precise_family_gates.entries), in FROZEN_MODES; "
                          "calls are [caller, callee, is_call_key]. Made once, before the change: "
                          "`python tests/precise_family_gates.py --freeze <dir holding the pre-S1 module>`."),
               "module_sha256": sha, "modes": FROZEN_MODES, "entries": ents}
        p.write_bytes((json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        print(f"--freeze: wrote {p.relative_to(REPO)} ({len(ents)} graphs)")
    return 0


def main(argv) -> int:
    try:
        if "--capture" in argv:
            return do_capture()
        if "--freeze" in argv:
            return do_freeze(argv[argv.index("--freeze") + 1])
        print("precise_family_gates — S1: DEEPMAP_PRECISE_CALLS scoped by language family")
        for g in (gate_a_switch, gate_b_identity, gate_c_family_selection, gate_d_supplemental, gate_e_real_engine,
                  gate_f_source_seam):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        cleanup()
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
