#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking, slice S4 — ENGINE PARITY between graphify 0.8.39 and 0.8.51 (design §8.4 E, §9 S4,
as amended before RC by benchmark/call_linking/PREREG.md amendment A1). No Neo4j, no network, no spend.

Every comparison is made on VERSION-STABLE identities (a node is `label:name@file:line`; node keys differ by engine)
and, per amendment A1, on the edges whose BOTH endpoints exist on both engines: the two engines do not always make the
same nodes (graphify 0.8.39 makes no node for a TypeScript `static create = <T>(...) => ...` member that 0.8.51 does),
and an edge touching such a node exists on one engine only whatever typed linking does. The RAW figures over all
edges are always reported beside the decisive ones, never decisive.

  S  the comparison itself, on synthetic engine outcomes: identical outcomes pass; a typed tuple or a VT edge on one
     engine only between common endpoints fails; one to a node only one engine makes is excluded (A1) and reported
     raw; shipped-policy drift of a test caller passes F1 / F2 while the same drift with TESTS=1 fails F2; a variant
     whose node set differs from its V0 base is VOID (fails); the pre-RC decisions, read from the REAL
     PRE_RC_DECISIONS.md: PD-1's two zod tuples on 0.8.51 only, INFERRED, pass F1 / F2 while E as registered is
     reported failing, and an unlisted differing tuple, a listed one on the other engine, with another grade or on
     another corpus, or any of them with no decision recorded, still fails.
  F  the FIXTURES — every captured tree of the typed-calls gates (tests/fixtures/typed_calls/<0839|0851>/{py,ts,
     precise}/ and negative/<0839|0851>/), replayed through the FULL push chain exactly as sync runs it (the shapes
     gate's pipeline: `_rebuild_code` with the engine subprocess handing back the capture, every push pass, the pure
     `_resolve_calls`), under V0 (every flag off), VT (TYPED python,typescript,javascript + the audit; the shipped
     test / non-production caller policy) and VTtn (VT + DEEPMAP_TYPED_CALLS_TESTS=1 + DEEPMAP_TYPED_CALLS_NONPROD=1):
       F1 the typed tuples (caller, target, grade) of PRODUCTION callers under VT are identical across the engines;
       F2 under VTtn the typed tuples of EVERY caller are identical (the shipped policy keys a test / non-production
          caller only where the engine already had a tuple, so it is engine-sensitive by design — reported, F3);
       F4 E: the final graphs' symmetric difference between the engines (VT) <= V0's (both on the common endpoints).
  C  the CORPORA (opt-in, `--corpora`; minutes per corpus and engine): the five Python benchmark corpora and zod at
     their pinned SHAs, copied from the read-only benchmark clones with `git clone --no-hardlinks`, extracted by the
     REAL graphify of each engine (a child of this file under each engine's Python: `--py39` / `--py51`), then the
     same F1/F2/F4 checks, every raw figure, the nodes on one engine only, and the engine-specific changes (G6:
     an edge the variant adds, removes or regrades on one engine only, between two common endpoints).

  A  (last) every pipeline module was loaded from the build/ under test (the sibling gates this file loads for their
     trees put this repository's build/ first on sys.path while they load, so the pipeline is imported first).

The E criterion is the measurement kit's own (benchmark/call_linking/engdiff.py `criterion`), so this gate and the
RC measurement (S6) apply ONE rule. A1 judges ENDPOINTS: a typed tuple between two common nodes whose evidence chain
passes through a node only one engine makes still counts (zod: `ZodTuple.create([]).rest()` types `.rest` only on
0.8.51, which alone makes a node for the generic static arrow member `create`). Every differing tuple is therefore
reported with whether its caller's file holds such a node — a diagnostic, never decisive.
F1 / F2 (and C1 / C2) apply the decisions recorded before RC (benchmark/call_linking/PRE_RC_DECISIONS.md) through the
kit's own `engdiff.typed_tuple_verdict`: they pass when no typed tuple differs on the common endpoints, or when every
differing one is listed there for that corpus, engine and grade (PD-1: exactly two zod tuples, on 0.8.51, INFERRED —
a list, never a rule). E bullet 1 as registered (`holds_as_registered`) is printed beside it with each accepted tuple
and its decision id, and `--json` writes both (`E_typed_production`, `E_typed_all_tn`) for S6.
Without the tree-sitter TS / JS grammars (an optional dependency graphify pulls in) TS / JS typing is off by design and
those trees compare empty typed sets; their parity is measured where graphify is installed.

usage: python tests/typed_calls_engine_parity_gates.py [--root <tree holding build/>]
           [--corpora [--py39 <python with graphify 0.8.39>] [--py51 <python with graphify 0.8.51>]
            [--only <corpus,...>] [--work <dir> [--keep]] [--jobs N] [--json <summary.json>]]
       (default --py39 / --py51: this interpreter when its graphify is that version; the two must differ)
internal: --child <corpus root> <out.json>   (one engine's dump of one corpus; run under that engine's Python)
"""
from __future__ import annotations

import concurrent.futures as cf
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FXROOT = REPO / "tests" / "fixtures" / "typed_calls"
BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
CLONES_DEFAULT = "C:/tmp/deepmap-bench-clones"
# design §8.2 / benchmark/call_linking/run_matrix.sh: the corpora and their pinned SHAs
CORPORA = {
    "psf__requests": "23953c0c875219a715f081cf3de7c149a7629ccf",
    "pallets__flask": "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81",
    "pallets__click": "679a7a0eccbdded7a6e85680bdaaf08003765e01",
    "pallets__werkzeug": "1b00618e787f40dfb21eba29caf8f8be7c8e1d93",
    "fastapi__fastapi": "702fea83bb3ef0b558104835d7936af49650fb2d",
    "colinhacks__zod": "912f0f51b0ced654d0069741e7160834dca742ee",
}


def _parse(argv):
    o = {"root": None, "corpora": False, "py39": None, "py51": None, "only": None, "work": None, "keep": False,
         "jobs": 2, "json": None, "child": None}
    it = iter(argv)
    for a in it:
        if a == "--corpora":
            o["corpora"] = True
        elif a == "--keep":
            o["keep"] = True
        elif a == "--child":
            o["child"] = (next(it, ""), next(it, ""))
        elif a.split("=", 1)[0] in ("--root", "--py39", "--py51", "--only", "--work", "--jobs", "--json"):
            k, v = (a.split("=", 1) if "=" in a else (a, next(it, "")))
            o[k[2:]] = v
        else:
            raise SystemExit(f"usage: see the module docstring (unknown argument {a!r})")
    if o["root"] is not None and not (Path(o["root"]) / "build").is_dir():
        raise SystemExit(f"--root needs a directory holding build/ (got {o['root']!r})")
    o["root"] = Path(o["root"]).resolve() if o["root"] else REPO
    o["jobs"] = max(1, int(o["jobs"] or 2))
    return o


OPT = _parse(sys.argv[1:]) if __name__ == "__main__" else _parse([])
BUILD = OPT["root"] / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402
import sync as S               # noqa: E402

# Every module of the pipeline imported NOW, from the build/ under test: the sibling gates loaded below put this
# repository's build/ first on sys.path while they run, so a module first imported then would come from there (a
# --root run would silently measure the wrong code). section A checks where each one came from.
PIPELINE_MODULES = ("graphify_extract", "sync", "edge_resolve", "typescript_edges", "language_recognizers",
                    "typed_calls", "receiver_facts_py", "receiver_facts_ts", "py_import_resolver")
_PRELOAD_ERRORS = {}
for _name in PIPELINE_MODULES:
    try:
        importlib.import_module(_name)
    except Exception as _exc:  # noqa: BLE001 — a tree without the feature (RED, never a crash)
        _PRELOAD_ERRORS[_name] = repr(_exc)

_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
WAVE1 = "python,typescript,javascript"
OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_PRECISE_FIXES": "1", "DEEPMAP_SELF_CALL_REBIND": "1", "DEEPMAP_INCREMENTAL_SYNC": "0",
       "DEEPMAP_TYPED_CALLS_REPLACE": "1", "DEEPMAP_TYPED_CALLS_TESTS": "0", "DEEPMAP_TYPED_CALLS_NONPROD": "0",
       "DEEPMAP_INCLUDE_VENDOR": "0", "DEEPMAP_MERGE_SUMMARIES": "0"}
VARIANTS = {"V0": dict(OFF),
            "VT": dict(OFF, DEEPMAP_TYPED_CALLS=WAVE1, DEEPMAP_CALLS_AUDIT=WAVE1),
            "VTtn": dict(OFF, DEEPMAP_TYPED_CALLS=WAVE1, DEEPMAP_CALLS_AUDIT=WAVE1, DEEPMAP_TYPED_CALLS_TESTS="1",
                         DEEPMAP_TYPED_CALLS_NONPROD="1")}

_TMP: list = []


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_s4p_")).resolve())
    return _TMP[0]


def _rmtree(path) -> None:
    """Remove a tree this gate made, read-only files included (a git clone's objects are read-only on Windows, and
    shutil.rmtree(ignore_errors=True) skips them silently, leaving the temp dir behind); never raises."""
    def _again(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_again)
        else:
            shutil.rmtree(path, onerror=_again)
    except OSError:
        pass


def cleanup() -> None:
    while _TMP:
        _rmtree(_TMP.pop())


@contextlib.contextmanager
def flags(env: dict, home: Path):
    """Every DEEPMAP_* variable removed, then `env` + the run settings (temporary DEEPMAP_HOME, the graphify engine);
    all restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update(env)
        os.environ.update({"DEEPMAP_HOME": str(home), "DEEPMAP_EXTRACTOR": "graphify", "DEEPMAP_AUTO_GITIGNORE": "0",
                           "DEEPMAP_GRAPHIFY_TIMEOUT": "3600"})
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


def _load(path: Path, name: str):
    """A sibling gate / kit module, loaded by path WITHOUT letting it re-point sys.path (the build/ modules are
    already imported from OPT["root"]: a later lazy import must come from there too) or keep its env edits."""
    saved_path, saved_env = list(sys.path), dict(os.environ)
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_env)


# the pipeline exactly as sync runs it, and the shape trees: the shapes gate (its section A locks the order to
# build/sync.py's AST and proves a real sync() pass pushes the same edges)
SG = _load(REPO / "tests" / "typed_calls_shapes_gates.py", "dm_s4_shapes")
ENG = _load(REPO / "benchmark" / "call_linking" / "engdiff.py", "dm_s4_engdiff")


class Rec:
    def __init__(self):
        self.passed, self.failed, self.fails, self.skipped = 0, 0, [], 0

    def check(self, label, cond, detail="") -> bool:
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.fails.append(label)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok or detail == "" else f" -- {str(detail)[:1500]}"))
        return ok

    def skip(self, label, why):
        self.skipped += 1
        print(f"  [SKIP] {label} -- {why}")


REC = Rec()
check = REC.check


# ================================================================================================
# One engine's outcome of one tree / corpus, on version-stable identities
# ================================================================================================

def stable_of(nodes) -> dict:
    return {n["key"]: SG.label_of(n) for n in nodes}


def is_production(source_file) -> bool:
    """§2.6: neither a test caller (sync._is_test_path) nor a non-production one (typed_calls.is_nonprod_path)."""
    try:
        import typed_calls as tc
        nonprod = bool(tc.is_nonprod_path(source_file))
    except Exception:  # noqa: BLE001
        nonprod = False
    return not S._is_test_path(str(source_file or "")) and not nonprod


def outcome(nodes, edges) -> dict:
    """{"nodes": [kit dump nodes], "edges": [[a, b, g]], "typed": [[caller, target, grade, production]]} with the
    caller / target as stable identities in `typed` (edges keep the keys: engdiff maps them itself)."""
    st = stable_of(nodes)
    typed = []
    for n in nodes:
        for t, g in sorted((n.get("call_typed") or {}).items()):
            typed.append([st[n["key"]], st.get(t, f"?{t}"), g, is_production(n.get("source_file"))])
    return {"nodes": [{k: n.get(k) for k in ("key", "label", "name", "source_file", "start_line", "end_line")}
                      for n in nodes],
            "edges": sorted([str(a), str(b), g] for (a, b), g in edges.items()),
            "typed": sorted(typed)}


def typed_parity(a39: dict, a51: dict, production_only: bool) -> dict:
    """The typed tuples of the two engines on the common endpoints (A1) and raw."""
    n39 = {SG.label_of(n) for n in a39["nodes"]}
    n51 = {SG.label_of(n) for n in a51["nodes"]}
    common = n39 & n51

    def tset(x, restrict):
        return {(c, t, g) for c, t, g, prod in x["typed"] if (prod or not production_only)
                and (not restrict or (c in common and t in common))}

    r39, r51 = tset(a39, False), tset(a51, False)
    c39, c51 = tset(a39, True), tset(a51, True)
    # diagnostic only (never decisive): a tuple on one engine whose CALLER'S FILE holds a node only one engine makes —
    # its evidence chain may pass through that node (zod: `ZodTuple.create(...).rest()`, where 0.8.39 makes no node
    # for the static arrow member `create`), which A1's endpoint rule does not see
    odd_files = {s.rsplit("@", 1)[-1].rsplit(":", 1)[0] for s in (n39 ^ n51)}
    diff = (c39 - c51) | (c51 - c39)
    via = sorted(x for x in diff if x[0].rsplit("@", 1)[-1].rsplit(":", 1)[0] in odd_files)
    return {"identical": c39 == c51, "n39": len(c39), "n51": len(c51), "only39": sorted(c39 - c51),
            "only51": sorted(c51 - c39), "raw_identical": r39 == r51, "raw_only39": len(r39 - r51),
            "raw_only51": len(r51 - r39), "nodes_only39": len(n39 - n51), "nodes_only51": len(n51 - n39),
            "diff_in_engine_only_files": len(via), "diff": len(diff)}


def criterion(v39, v51, b39, b51) -> dict:
    """The kit's A1 E criterion (benchmark/call_linking/engdiff.py) on these outcomes."""
    return ENG.criterion(v39, v51, b39, b51)


def tuple_verdict(label: str, p: dict, accepted: dict) -> dict:
    """§8.4 E bullet 1 for one tree / corpus and one view (p1 / p2): the kit's engdiff.typed_tuple_verdict on the
    tuples one engine has and the other lacks on the common endpoints — `holds_as_registered` (none differ) and
    `holds` (every differing tuple is one a pre-RC decision in PRE_RC_DECISIONS.md accepted for this corpus, on
    exactly that engine and grade; PD-1: two zod tuples). `label` is the corpus name, so a fixture tree (whose label
    is `<group>/<name>`) or another corpus is judged as registered."""
    return ENG.typed_tuple_verdict(label, p["only39"], p["only51"], accepted=accepted)


def _tv_text(tv: dict) -> str:
    acc = tv.get("accepted") or []
    return (f"as registered {'holds' if tv.get('holds_as_registered') else 'FAILS'}"
            + (f"; {len(acc)} differing tuple(s) accepted by {', '.join(tv.get('decisions') or [])}" if acc else "")
            + (f"; {len(tv.get('unaccepted') or [])} unaccepted" if tv.get("unaccepted") else ""))


def _tv_summary(tv: dict) -> dict:
    return {"holds": tv.get("holds"), "holds_as_registered": tv.get("holds_as_registered"),
            "decisions": tv.get("decisions") or [], "accepted": (tv.get("accepted") or [])[:50],
            "unaccepted": (tv.get("unaccepted") or [])[:50], "unaccepted_count": len(tv.get("unaccepted") or [])}


def judge(label: str, o: dict, *, prefix: str, rec=None, accepted=None) -> dict:
    """F1/F2/F3/F4 for one tree or corpus: `o` = {engine bucket: {variant: outcome}}; `rec` (default the gate's
    recorder) collects the verdicts. F1 / F2 apply the pre-RC decisions (`accepted`, default the kit's reading of
    PRE_RC_DECISIONS.md — the self-test injects its own) and report E as registered beside them."""
    rec = rec or REC
    acc = ENG.accepted_typed_tuples() if accepted is None else accepted
    a, b = o["0839"], o["0851"]
    p1 = typed_parity(a["VT"], b["VT"], production_only=True)
    p2 = typed_parity(a["VTtn"], b["VTtn"], production_only=False)
    p3 = typed_parity(a["VT"], b["VT"], production_only=False)
    tv1, tv2 = tuple_verdict(label, p1, acc), tuple_verdict(label, p2, acc)
    cr = criterion(a["VT"], b["VT"], a["V0"], b["V0"])
    rec.check(f"{prefix}1 {label}: production callers' typed tuples (VT, the shipped policy) identical across 0.8.39 / "
              f"0.8.51 on the common endpoints, or each differing one accepted by a pre-RC decision "
              f"({p1['n39']} / {p1['n51']}; {_tv_text(tv1)}; raw "
              f"{'identical' if p1['raw_identical'] else 'differs'}, nodes on one engine only "
              f"{p1['nodes_only39']} / {p1['nodes_only51']})",
              tv1["holds"], {"unaccepted": tv1["unaccepted"][:6], "accepted": tv1["accepted"][:6]})
    rec.check(f"{prefix}2 {label}: with DEEPMAP_TYPED_CALLS_TESTS=1 / NONPROD=1 every caller's typed tuples are "
              f"identical on the common endpoints, or each differing one accepted by a pre-RC decision "
              f"({p2['n39']} / {p2['n51']}; {_tv_text(tv2)})",
              tv2["holds"], {"unaccepted": tv2["unaccepted"][:6], "accepted": tv2["accepted"][:6]})
    for tag, tv in (("1", tv1), ("2", tv2)):
        if tv["accepted"] and rec is REC:
            print(f"     ({prefix}{tag} {label}: E bullet 1 as registered: "
                  f"{'HOLDS' if tv['holds_as_registered'] else 'FAILS'}; with the pre-RC decisions: "
                  f"{'HOLDS' if tv['holds'] else 'FAILS'}. Accepted differing tuples:)")
            for r in tv["accepted"]:
                print(f"       [{r['decision']}] {r['caller']} -> {r['target']} {r['grade']}, only on {r['engine']}")
    for tag, p in (("1", p1), ("2", p2)):
        if p["diff"] and rec is REC:
            print(f"     ({prefix}{tag} {label}: {p['diff_in_engine_only_files']} of the {p['diff']} differing tuple(s) have "
                  f"their caller in a file holding a node only one engine makes — an evidence chain through such a "
                  f"node is outside A1's endpoint rule; diagnostic, not decisive)")
    if not p3["identical"] and rec is REC:
        print(f"     ({prefix}3 {label}: under the SHIPPED policy, test / non-production callers' typed tuples differ "
              f"between the engines — {len(p3['only39'])} only on 0.8.39, {len(p3['only51'])} only on 0.8.51: "
              f"corrections-only keys where the engine already had a tuple; engine-sensitive by design)")
    void = cr.get("void")
    rec.check(f"{prefix}4 {label}: E (A1) — VT's symmetric difference between the engines on the common endpoints "
              f"{cr['variant_symdiff_common']} <= V0's {cr['v0_symdiff_common']} (raw, reported: "
              f"{cr['variant_symdiff_raw']} vs {cr['v0_symdiff_raw']}; nodes on one engine only "
              f"{len(cr['nodes_only_0839'])} / {len(cr['nodes_only_0851'])}; engine-specific changes "
              f"{len(cr['engine_specific_changes'])}, raw {cr['engine_specific_changes_raw']})",
              cr["holds"] and not void, {"void": void, "engine-specific": cr["engine_specific_changes"][:6]})
    return {"typed_production": {k: v for k, v in p1.items() if k not in ("only39", "only51")},
            "typed_all_tn": {k: v for k, v in p2.items() if k not in ("only39", "only51")},
            "E_typed_production": _tv_summary(tv1), "E_typed_all_tn": _tv_summary(tv2),
            "typed_all_shipped": {k: v for k, v in p3.items() if k not in ("only39", "only51")},
            "shipped_policy_only39": p3["only39"][:50], "shipped_policy_only51": p3["only51"][:50],
            "E": {k: (v if not isinstance(v, list) else (len(v) if k.startswith("nodes_only") else v[:50]))
                  for k, v in cr.items()}}


# ================================================================================================
# S: the comparison itself (synthetic engine outcomes: each verdict must come out as stated)
# ================================================================================================

class _Quiet(Rec):
    """A recorder that only collects (label prefix -> verdict) for the self-test."""

    def __init__(self):
        super().__init__()
        self.verdict = {}

    def check(self, label, cond, detail="") -> bool:
        self.verdict[label.split(" ", 1)[0]] = bool(cond)
        return bool(cond)


def _syn(keys, edges, typed):
    """A synthetic outcome: `keys` = [(key, label, name, file, line)], `edges` = [(a, b, g)], `typed` = [(caller key,
    target key, grade, production)] (typed stored on stable identities, like outcome())."""
    nodes = [{"key": k, "label": lb, "name": nm, "source_file": f, "start_line": ln, "end_line": ln}
             for k, lb, nm, f, ln in keys]
    st = {n["key"]: SG.label_of(n) for n in nodes}
    return {"nodes": nodes, "edges": sorted([a, b, g] for a, b, g in edges),
            "typed": sorted([st[c], st[t], g, p] for c, t, g, p in typed)}


def sec_loaded() -> None:
    print("\n-- A: the code under test --")
    wrong = {}
    for name in PIPELINE_MODULES:
        mod = sys.modules.get(name)
        f = Path(getattr(mod, "__file__", "") or "").resolve() if mod is not None else None
        if f is None or f.parent != BUILD.resolve():
            wrong[name] = str(f) if f else _PRELOAD_ERRORS.get(name, "not imported")
    check(f"A: every pipeline module is loaded from the build/ under test ({BUILD})", not wrong, wrong)


def sec_selftest() -> None:
    print("\n-- S: the comparison itself, on synthetic engine outcomes --")
    base = [("c", "Function", "caller", "a.py", 1), ("t", "Method", "Order.save", "a.py", 5),
            ("u", "Method", "Other.save", "a.py", 9), ("k", "Function", "test_x", "tests/test_a.py", 1)]
    only51 = base + [("z", "Method", "Box.create", "a.ts", 3)]

    def case(v39, v51, b39=None, b51=None, tn39=None, tn51=None):
        return {"0839": {"V0": b39 or v39, "VT": v39, "VTtn": tn39 or v39},
                "0851": {"V0": b51 or v51, "VT": v51, "VTtn": tn51 or v51}}

    same = _syn(base, [("c", "t", "INFERRED")], [("c", "t", "INFERRED", True)])
    v0 = _syn(base, [("c", "u", "AMBIGUOUS"), ("c", "t", "AMBIGUOUS")], [])
    rows = [
        ("identical outcomes", case(same, same, v0, v0), {"S1": True, "S2": True, "S4": True}),
        ("a production typed tuple on 0.8.51 only, between common endpoints -> F1 fails",
         case(same, _syn(base, [("c", "t", "INFERRED")], [("c", "t", "INFERRED", True), ("c", "u", "INFERRED", True)]),
              v0, v0), {"S1": False}),
        ("a typed tuple to a node only 0.8.51 makes -> excluded from F1 (A1), reported raw",
         case(_syn(only51[:4], [("c", "t", "INFERRED")], [("c", "t", "INFERRED", True)]),
              _syn(only51, [("c", "t", "INFERRED"), ("c", "z", "INFERRED")],
                   [("c", "t", "INFERRED", True), ("c", "z", "INFERRED", True)]),
              _syn(only51[:4], [], []), _syn(only51, [], [])), {"S1": True, "S4": True}),
        ("a test caller's key on one engine only under the shipped policy, identical with TESTS=1 -> F1/F2 hold",
         case(same, _syn(base, [("c", "t", "INFERRED"), ("k", "t", "INFERRED")],
                         [("c", "t", "INFERRED", True), ("k", "t", "INFERRED", False)]), v0, v0,
              _syn(base, [("c", "t", "INFERRED"), ("k", "t", "INFERRED")],
                   [("c", "t", "INFERRED", True), ("k", "t", "INFERRED", False)]),
              _syn(base, [("c", "t", "INFERRED"), ("k", "t", "INFERRED")],
                   [("c", "t", "INFERRED", True), ("k", "t", "INFERRED", False)])), {"S1": True, "S2": True}),
        ("a test caller's key on one engine only with TESTS=1 -> F2 fails",
         case(same, same, v0, v0, same,
              _syn(base, [("c", "t", "INFERRED"), ("k", "t", "INFERRED")],
                   [("c", "t", "INFERRED", True), ("k", "t", "INFERRED", False)])), {"S2": False}),
        ("a VT edge on 0.8.39 only between common endpoints (V0 identical) -> F4 fails",
         case(_syn(base, [("c", "t", "INFERRED"), ("c", "u", "AMBIGUOUS")], [("c", "t", "INFERRED", True)]),
              same, v0, v0), {"S4": False}),
        ("a VT edge to a node only 0.8.51 makes -> F4 holds on the common endpoints (raw worse, reported)",
         case(_syn(only51[:4], [("c", "t", "INFERRED")], [("c", "t", "INFERRED", True)]),
              _syn(only51, [("c", "t", "INFERRED"), ("c", "z", "INFERRED")], [("c", "t", "INFERRED", True)]),
              _syn(only51[:4], [], []), _syn(only51, [], [])), {"S4": True}),
        ("a variant whose node set differs from its V0 base -> VOID, F4 fails",
         case(same, same, v0, _syn(only51, [("c", "u", "AMBIGUOUS"), ("c", "t", "AMBIGUOUS")], [])), {"S4": False}),
    ]
    for why, o, want in rows:
        q = _Quiet()
        judge("synthetic", o, prefix="S", rec=q)
        got = {k: q.verdict.get(k) for k in want}
        check(f"S: {why}", got == want, {"got": got, "want": want})

    # §8.4 E bullet 1 with the pre-RC decisions (benchmark/call_linking/PRE_RC_DECISIONS.md, applied by the kit's
    # engdiff.typed_tuple_verdict): PD-1 accepts exactly two zod tuples, on 0.8.51 only, graded INFERRED — a list,
    # never a rule. These rows read the REAL record (the zod outcomes below carry PD-1's own stable keys).
    zf = "packages/zod/src/v3/types.ts"
    zk = [("fa", "Method", "ZodFunction.args", zf, 3912), ("fc", "Method", "ZodFunction.create", zf, 3952),
          ("tr", "Method", "ZodTuple.rest", zf, 3456), ("tp", "Method", "ZodType.pipe", zf, 522),
          ("pc", "Method", "ZodPipeline.create", zf, 4827)]
    zk51 = zk + [("tc", "Method", "ZodTuple.create", zf, 3463)]       # a node only 0.8.51 makes (A1's reason)
    zedges = [("fa", "tr", "INFERRED"), ("fc", "tr", "INFERRED"), ("tp", "pc", "INFERRED")]

    def zod(typed39, typed51, keys39=zk, keys51=zk51):
        """VT = VTtn per engine; V0 has the same nodes and edges (the pushed :CALLS agree; call_typed differs)."""
        v39, v51 = _syn(keys39, zedges, typed39), _syn(keys51, zedges, typed51)
        return case(v39, v51, _syn(keys39, zedges, []), _syn(keys51, zedges, []))

    pd1 = [("fa", "tr", "INFERRED", True), ("fc", "tr", "INFERRED", True)]
    common = [("tp", "pc", "EXTRACTED", True)]
    zrows = [
        ("PD-1 (the real record): zod's two listed tuples on 0.8.51 only, INFERRED -> S1 / S2 hold; E as "
         "registered does not (holds_as_registered False, both accepted with the decision id)",
         "colinhacks__zod", zod(common, common + pd1), None, {"S1": True, "S2": True, "S4": True},
         {"holds": True, "holds_as_registered": False, "decisions": ["PD-1"], "accepted": 2, "unaccepted": 0}),
        ("PD-1's tuples plus an UNLISTED differing tuple -> S1 / S2 fail (only the unlisted one is unaccepted)",
         "colinhacks__zod", zod(common, common + pd1 + [("tp", "tr", "INFERRED", True)]), None,
         {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": ["PD-1"], "accepted": 2, "unaccepted": 1}),
        ("a PD-1 tuple on the WRONG ENGINE (0.8.39 only) -> S1 / S2 fail",
         "colinhacks__zod", zod(common + pd1[:1], common), None, {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": [], "accepted": 0, "unaccepted": 1}),
        ("a PD-1 tuple with the WRONG GRADE (EXTRACTED / AMBIGUOUS on 0.8.51) -> S1 / S2 fail",
         "colinhacks__zod", zod(common, common + [("fa", "tr", "EXTRACTED", True), ("fc", "tr", "AMBIGUOUS", True)]),
         None, {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": [], "accepted": 0, "unaccepted": 2}),
        ("a PD-1 tuple graded INFERRED on 0.8.51 but differently on 0.8.39 -> the 0.8.39 side is unaccepted, "
         "S1 / S2 fail", "colinhacks__zod", zod(common + [("fa", "tr", "AMBIGUOUS", True)], common + pd1[:1]), None,
         {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": ["PD-1"], "accepted": 1, "unaccepted": 1}),
        ("PD-1's tuples on ANOTHER CORPUS -> S1 / S2 fail (a decision names its corpus)",
         "pallets__flask", zod(common, common + pd1), None, {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": [], "accepted": 0, "unaccepted": 2}),
        ("PD-1's tuples with NO decision recorded (an unreadable record accepts nothing) -> S1 / S2 fail",
         "colinhacks__zod", zod(common, common + pd1), {}, {"S1": False, "S2": False},
         {"holds": False, "holds_as_registered": False, "decisions": [], "accepted": 0, "unaccepted": 2}),
        ("PD-1's tuples accepted under VT, an unlisted test caller's tuple under VTtn -> S1 holds, S2 fails (each "
         "judged on its own differing tuples)",
         "colinhacks__zod", case(_syn(zk, zedges, common), _syn(zk51, zedges, common + pd1),
                                 _syn(zk, zedges, []), _syn(zk51, zedges, []),
                                 _syn(zk, zedges, common + [("tp", "tr", "INFERRED", False)]),
                                 _syn(zk51, zedges, common + pd1)), None, {"S1": True, "S2": False},
         {"holds": True, "holds_as_registered": False, "decisions": ["PD-1"], "accepted": 2, "unaccepted": 0}),
    ]
    for why, corpus, o, accepted, want, want_tv in zrows:
        q = _Quiet()
        try:
            res = judge(corpus, o, prefix="S", rec=q, **({} if accepted is None else {"accepted": accepted}))
            tv = (res or {}).get("E_typed_production") or {}
            got_tv = {k: (len(tv.get(k) or []) if k in ("accepted", "unaccepted") else tv.get(k)) for k in want_tv}
        except Exception as exc:  # noqa: BLE001 — RED, never a crash
            res, got_tv = None, f"{type(exc).__name__}: {exc}"
        got = {k: q.verdict.get(k) for k in want}
        check(f"S: {why}", got == want and got_tv == want_tv,
              {"got": got, "want": want, "verdict (production)": got_tv, "want verdict": want_tv})

    # the gate's own temp cleanup: a corpus copy is a git clone whose object files are READ-ONLY on Windows, which
    # shutil.rmtree(ignore_errors=True) skipped silently — the corpora mode left its temp work dir behind
    ro = tmp() / "ro_cleanup"
    obj = ro / "colinhacks__zod" / ".git" / "objects" / "pack"
    obj.mkdir(parents=True, exist_ok=True)
    pack = obj / "pack-0.idx"
    pack.write_bytes(b"\0")
    os.chmod(pack, stat.S_IREAD)
    try:
        _rmtree(ro)
        err = None
    except Exception as exc:  # noqa: BLE001 — RED, never a crash
        err = f"{type(exc).__name__}: {exc}"
    check("S: the gate's temp cleanup removes a tree holding read-only files (a git clone's objects) and never raises",
          err is None and not ro.exists(), err or f"{ro} still exists")


# ================================================================================================
# F: the fixtures
# ================================================================================================

def fixture_trees() -> list:
    """[(group, name, files, inject, cap39, cap51, problem)] for every captured tree of the typed-calls gates."""
    out = []
    groups = []
    for group, gate, attr in (("py", "typed_calls_py_gates.py", "TREES"), ("ts", "typed_calls_ts_gates.py", "TREES"),
                              ("precise", "precise_family_gates.py", "TREES")):
        try:
            mod = _load(REPO / "tests" / gate, f"dm_s4p_{group}")
            groups.append((group, getattr(mod, attr), {}))
        except Exception as exc:  # noqa: BLE001
            out.append((group, f"({gate})", None, None, None, None, f"cannot load {gate}: {exc!r}"))
    try:
        r11 = _load(REPO / "tests" / "calls_plus_scope_r11c_gates.py", "dm_s4p_r11c")
        groups.append(("precise", {r11.S1B_FIXTURE: r11.S1B_TREE}, {}))
    except Exception as exc:  # noqa: BLE001
        out.append(("precise", "(calls_plus_scope_r11c_gates.py)", None, None, None, None, f"cannot load: {exc!r}"))
    groups.append(("negative", SG.TREES, SG.TREE_INJECT))
    for group, trees, inject in groups:
        for name, files in sorted(trees.items()):
            sha = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
            caps, problem = {}, None
            for b in ("0839", "0851"):
                p = (FXROOT / "negative" / b / f"{name}.json") if group == "negative" else (FXROOT / b / group / f"{name}.json")
                doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
                if doc is None:
                    problem = f"{p.relative_to(REPO)} is missing"
                elif doc.get("tree_sha256") != sha:
                    problem = f"{p.relative_to(REPO)} was captured from another tree (stale)"
                caps[b] = doc
            out.append((group, name, files, inject.get(name), caps.get("0839"), caps.get("0851"), problem))
    return out


def replay(files: dict, graph: dict, inject, variant: str, root: Path) -> dict:
    with flags(VARIANTS[variant], tmp() / "home"):
        r = SG.chain(root, graph, inject=inject)
    if r.engine != "graphify":
        raise RuntimeError(f"the replay resolved to {r.engine!r}: {r.err[-400:]}")
    return outcome(r.nodes, r.edges)


def sec_fixtures(summary: dict) -> None:
    print("\n-- F: every captured fixture tree, both engine ends, through the full push chain (V0 / VT / VTtn) --")
    trees = fixture_trees()
    check(f"F0: the typed-calls gates' captured trees are found ({len(trees)})", len(trees) >= 30, len(trees))
    if not SG.ts_grammars():
        print("  (the tree-sitter TS / JS grammars are not importable here: TS / JS typing is off by design, so the "
              "TS / JS trees compare empty typed sets — their parity is measured where graphify is installed)")
    for group, name, files, inject, c39, c51, problem in trees:
        label = f"{group}/{name}"
        if problem:
            check(f"F {label}: both engines' captures exist and were made from the current tree", False, problem)
            continue
        o = {}
        try:
            for b, cap in (("0839", c39), ("0851", c51)):
                root = SG.write_tree(tmp() / "t" / b / group / name, files)
                o[b] = {v: replay(files, cap["graph"], inject, v, root) for v in VARIANTS}
        except Exception as exc:  # noqa: BLE001
            check(f"F {label}: replays", False, f"{type(exc).__name__}: {exc}")
            continue
        summary[label] = judge(label, o, prefix="F")


# ================================================================================================
# C: the corpora (opt-in)
# ================================================================================================

def child(corpus: str, out: str) -> int:
    """One engine's dump of one corpus: ONE real extraction (under VT, so the shared reader exists), then every
    variant's push chain on copies of it — with the extraction's engine and reader handed to the typed step."""
    root = Path(corpus).resolve()
    home = Path(tempfile.mkdtemp(prefix="dmp_")).resolve()    # short: the engine's cache paths run ~110 chars past it
    t0 = time.time()
    try:
        with flags(VARIANTS["VT"], home), SG.quiet() as err:
            extracted = S._rebuild_code(root)
            nodes0, calls0, _imports, engine = extracted
            info = dict(getattr(extracted, "info", None) or {})
            note = getattr(extracted, "typed_note", None)
            src = getattr(note, "source", None)
            S.enrich.apply_sidecar(root, nodes0)
            nodes0, _te = S._apply_recognizers(root, nodes0)
        t_extract = time.time() - t0
        res = {}
        for v, env in VARIANTS.items():
            nodes, calls = copy.deepcopy(nodes0), list(calls0)
            t1 = time.time()
            with flags(env, home), SG.quiet():
                calls, _n = S._unkey_receiver_calls(root, nodes, calls)
                calls, stats = S._typed_calls_link(root, nodes, calls, engine=engine, source=src)
                S._attach_bare_calls(root, nodes, calls)
                S._attach_local_defs(root, nodes, calls)
                S._attach_local_def_calls(root, nodes, calls)
                edges = S._resolve_calls(nodes, calls)
            res[v] = outcome(nodes, edges)
            res[v]["seconds"] = round(time.time() - t1, 1)
            res[v]["stats"] = {k: (stats or {}).get(k) for k in ("error", "files", "files_failed", "emitted",
                                                                   "removed", "audit_weak", "langs_disabled")}
        doc = {"engine": engine, "graphify_version": GX.graphify_version(), "python": sys.version.split()[0],
               "info": {k: info.get(k) for k in ("requested", "resolved", "fallback_reason", "timed_out")},
               "extract_seconds": round(t_extract, 1), "variants": res, "stderr_tail": err.getvalue()[-2000:]}
        Path(out).write_text(json.dumps(doc), encoding="utf-8")
        return 0 if engine == "graphify" else 3
    finally:
        _rmtree(home)


def _engine_of(py: str):
    try:
        p = subprocess.run([py, "-c", "from importlib.metadata import version; print(version('graphifyy'))"],
                           capture_output=True, text=True, timeout=120)
        return p.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def materialize(name: str, sha: str, work: Path, clones: Path) -> "Path | None":
    dest = work / name
    if (dest / ".git").is_dir():
        return dest
    src = clones / name
    if not (src / ".git").exists():
        return None
    part = work / f"{name}.partial"
    _rmtree(part)
    ok = subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", "--no-hardlinks", str(src), str(part)],
                        capture_output=True, text=True).returncode == 0
    ok = ok and subprocess.run(["git", "-C", str(part), "-c", "core.longpaths=true", "checkout", "-q", "--detach", sha],
                               capture_output=True, text=True).returncode == 0
    if not ok:
        _rmtree(part)
        return None
    part.rename(dest)
    return dest


def sec_corpora(summary: dict) -> None:
    print("\n-- C: the corpora (5 Python benchmark corpora + zod at their pinned SHAs), the REAL engine of each end --")
    if not OPT["corpora"]:
        REC.skip("C: the corpora", "opt-in: python tests/typed_calls_engine_parity_gates.py --corpora "
                 "--py51 <python with graphifyy==0.8.51> (minutes per corpus and engine)")
        return
    pys = {"0839": OPT["py39"], "0851": OPT["py51"]}
    here = BUCKETS.get(GX.graphify_version()) if _HAS_ENGINE else None
    if here and not pys[here]:
        pys[here] = sys.executable
    vers = {b: (_engine_of(p) if p else None) for b, p in pys.items()}
    if not check("C0: a Python with graphify 0.8.39 and one with 0.8.51 (--py39 / --py51)",
                 vers == {"0839": "0.8.39", "0851": "0.8.51"}, vers):
        return
    clones = Path(os.environ.get("CALL_LINKING_CLONES") or CLONES_DEFAULT)
    work = Path(OPT["work"]).resolve() if OPT["work"] else Path(tempfile.mkdtemp(prefix="dmpc_")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    names = [n for n in CORPORA if not OPT["only"] or n in OPT["only"].split(",")]
    roots = {}
    for n in names:
        roots[n] = materialize(n, CORPORA[n], work, clones)
        check(f"C0 {n}: a --no-hardlinks copy of {clones / n} at {CORPORA[n][:10]}", roots[n] is not None)
    jobs = [(n, b) for n in names if roots[n] is not None for b in ("0839", "0851")]

    def go(job):
        n, b = job
        out = work / f"{n}.{b}.json"
        if out.is_file() and OPT["keep"]:
            return job, 0, ""
        env = dict(os.environ, PYTHONUTF8="1", PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([pys[b], str(Path(__file__).resolve()), "--root", str(OPT["root"]), "--child",
                            str(roots[n]), str(out)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=4 * 3600)
        return job, p.returncode, (p.stdout + p.stderr)[-1500:]

    t0 = time.time()
    rcs = {}
    with cf.ThreadPoolExecutor(max_workers=OPT["jobs"]) as ex:
        for (n, b), rc, tail in ex.map(go, jobs):
            rcs[(n, b)] = rc
            print(f"  ({n} on {b}: exit {rc} after {time.time() - t0:.0f} s)" + (f" -- {tail}" if rc else ""),
                  flush=True)
    for n in names:
        if roots[n] is None:
            continue
        docs = {}
        for b in ("0839", "0851"):
            p = work / f"{n}.{b}.json"
            docs[b] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        okd = all(d is not None and d.get("engine") == "graphify" and d.get("graphify_version") == want
                  for d, want in ((docs["0839"], "0.8.39"), (docs["0851"], "0.8.51")))
        if not check(f"C {n}: both engines extracted it with graphify (no ast fallback: VOID otherwise)", okd,
                     {b: (d or {}).get("info") for b, d in docs.items()}):
            continue
        for b in ("0839", "0851"):
            d = docs[b]
            print(f"     ({n} {b}: {len(d['variants']['V0']['nodes'])} nodes, extract {d['extract_seconds']} s, "
                  f"typed pass VT {d['variants']['VT']['seconds']} s, VT stats {d['variants']['VT']['stats']})")
        summary[n] = judge(n, {b: docs[b]["variants"] for b in ("0839", "0851")}, prefix="C")
    if not OPT["work"] and not OPT["keep"]:
        _rmtree(work)
    else:
        print(f"  (work dir kept: {work})")


# ================================================================================================

def main() -> int:
    if OPT["child"]:
        return child(*OPT["child"])
    try:
        print(f"typed_calls_engine_parity_gates (S4): Python {sys.version.split()[0]}, graphify "
              f"{GX.graphify_version() if _HAS_ENGINE else '(not installed)'}, build {BUILD}")
        summary: dict = {}
        sec_selftest()
        sec_fixtures(summary)
        sec_loaded()
        sec_corpora(summary)
        if OPT["json"]:
            Path(OPT["json"]).parent.mkdir(parents=True, exist_ok=True)
            Path(OPT["json"]).write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n",
                                         encoding="utf-8")
            print(f"  (summary written: {OPT['json']})")
        print(f"\nRESULT: {REC.passed} passed, {REC.failed} failed, {REC.skipped} skipped")
        if REC.failed:
            print("FAILED:\n  " + "\n  ".join(REC.fails[:80]))
        print("GREEN" if not REC.failed else "RED")
        return 0 if not REC.failed else 1
    finally:
        cleanup()
        SG.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
