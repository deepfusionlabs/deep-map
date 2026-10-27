#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0 gate — the call-linking measurement kit (benchmark/call_linking/) and its pre-registration (PREREG.md).

The kit decides whether typed-receiver call linking, the untyped-receiver audit, family-scoped precise calls and
the cross-family filter ship (PREREG §8.5). A kit that measures something other than what `deepmap sync` pushes,
scores inexactly, or lets the protocol drift after data would decide wrongly, so this gate locks:

  A  the kit's files exist, parse, and import only the stdlib / their siblings at module level; nothing the
     product ships (build/, hooks/) imports the kit or a reference resolver (jedi, TypeScript)
  B  the offline pass order equals sync's, read from build/sync.py's AST: the pipeline function (`_sync_pass`,
     reached from `sync()`) runs exactly the CURRENT order or the FUTURE one with `_typed_calls_link` between
     `_unkey_receiver_calls` and `_attach_bare_calls` (and defines that helper iff it calls it); any other change
     to nodes/calls between extraction and the push fails — a rebind (a comprehension filter included), a write
     into them or into an alias / element of them, a mutating method call, any call that reaches them anywhere in
     its arguments (aliases and loop / comprehension elements tracked to a fixpoint); push_to_neo4j's first step
     is `_resolve_calls(nodes, calls)`; offline_calls.run_pipeline runs the future order through a guarded
     lookup. A self-test proves the checker accepts exactly those two orders and rejects every such shape.
  C  data flow: the REAL `sync.sync()` pass (fake driver; extraction frozen, the push recorded) and
     offline_calls.run_pipeline produce the identical :CALLS dict for the same extraction — ast engine always,
     graphify when installed, per flag mode; the passes are shown to matter (skipping one changes the edges);
     the CLI's two runs are byte-identical; a dump carries no wall-clock field (typed_calls' `seconds` stays out of
     `call_linking`: two typed runs, in process under two fake clocks and through the CLI, are byte-identical; the
     pass's own seconds go to <out>.timing.json; strip_wall_clock re-judges a pre-fix dump — 2026-10-24 ruling);
     its DEEPMAP_HOME / read-only-clone guards refuse; with graphify, VF
     against V0 end to end (the review's probe: a Python and a TypeScript `helper`) removes only cross-family
     targets and its delta is data, never an inert pair
  D  the offline counts reproduce the frozen flask graph (330 CALLS / 26 AMBIGUOUS on 0.8.39; 329 / 26 on
     0.8.51; exact edge hash) — when the benchmark clone and an in-range engine are present, else SKIPPED
  E  compare_calls, delta, indegree_drift (+ the void rule), audit_sample (seeded, blind, Wilson), engdiff,
     impact_offline, impact_spot, score_m (R1/R3), measure_self (R2), run_variant (round trip, matched state,
     safety) and variants are exact on synthetic inputs; the S0 review round: census reads the owner stamp sync
     writes and both live captures judge it with owner_problem; the inert-variant VOID rule (offline_calls
     variant_void on the review's V0/VT pair, delta's VOID pair rule) and the cross-file EXTRACTED invariant
     (exit 4); --src-prefix filters by the CALLER's file both ways; delta's EXTRACTED bucket keeps base-EXTRACTED
     edges; the audit's top-level losses and two-strata removal share; saturated spot symbols; `$` atoms and TS
     def spans in score_m; PREREG amendment A1 in engdiff (E compares the edges whose BOTH endpoints exist on both
     engines, the raw symmetric difference reported beside it; VOID on a non-edge-only variant or a VOID dump);
     compare_calls pool refuses a several-dump --json, a non-score, mixed variants / engines (exit 2) and a VOID
     score (exit 3) instead of crashing or flattening; the `effective` block reports, through the product, every
     switch a §8.3 variant sets (the cross-family filter, the typed-calls shapes / replace / tests / nonprod / max
     files): every variant differs from V0 and from every other variant, variant_void VOIDs a requested switch the
     product left off; engdiff.typed_tuple_verdict applies PRE_RC_DECISIONS.md's PD-1 (exactly its two zod tuples)
  F  tests/stage0_refreeze.py --env forwards only the DEEPMAP_* keys it is given; child_env is otherwise unchanged
  G  compliance: jedi and TypeScript appear in no shipped list
  H  PREREG.md: §8 verbatim (sha256-locked — thresholds never move after data), the dated "Amendments before RC"
     (A1 engine parity on common endpoints, A2 the `_mk_project` spot replaced by `read_records`), the RC identity,
     engine builds, corpus SHAs (== run_matrix.sh), the 5 TS + 5 Python spot symbols (== impact_spot.py), none
     saturated at V0, each on a pinned corpus (the STAGE-0 one defined once in the registered corpus), the §8.3
     variant table (== variants.py); §5 names the switches `effective` reports; A1 stays the only amendment of
     §8.4 E, and the zod tuples S4 found after it go the DECISION.md route (PRE_RC_DECISIONS.md PD-1: the two
     tuples, their cause, a list not a rule; §7 points to it); gt_self65.json holds the 65 recovered queries
  I  truth_jedi's call-site reader (forms, defstack, decorator lines, CHARACTER columns) without jedi; truth_jedi
     end to end on a non-ASCII / BOM + CRLF / latin-1 / syntax-error fixture when a jedi 0.20.1 python is given
     (CALL_LINKING_JEDI_PY); truth_ts.js when Node and a TypeScript 5.6.3 env are given (CALL_LINKING_TSENV);
     else SKIPPED

Hermetic except D/I (opt-in by presence): temp dirs + a temp DEEPMAP_HOME, no Neo4j, no network; never touches
~/.deepmap. Options (for the RED-first runs and mutation tests): --kit <dir> (load the kit from another dir),
--sync <file> (read another sync.py for B), --refreeze <file> (load another stage0_refreeze.py for F),
--no-real (skip C's graphify modes and D).
Usage: python tests/call_linking_harness_gates.py [--kit DIR] [--sync FILE] [--refreeze FILE] [--no-real]
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
BUILD = REPO / "build"
KIT_DEFAULT = REPO / "benchmark" / "call_linking"
PASSES: list = []
FAILS: list = []
SKIPS: list = []

KIT_FILES = ("offline_calls.py", "truth_jedi.py", "truth_ts.js", "compare_calls.py", "delta.py", "indegree_drift.py",
             "engdiff.py", "impact_offline.py", "impact_spot.py", "audit_sample.py", "run_variant.py",
             "measure_self.py", "score_m.py", "gt_self65.json", "run_matrix.sh", "PREREG.md", "README.md",
             "variants.py", "_common.py", "frozen/flask_V0.json", "PRE_RC_DECISIONS.md")
KIT_MODULES = ("_common", "variants", "offline_calls", "compare_calls", "delta", "indegree_drift", "engdiff",
               "impact_offline", "impact_spot", "audit_sample", "run_variant", "measure_self", "score_m",
               "truth_jedi")
ORDER_NOW = ["_rebuild_code", "enrich.apply_sidecar", "_apply_recognizers", "_unkey_receiver_calls",
             "_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls", "push_to_neo4j"]
ORDER_FUTURE = ORDER_NOW[:4] + ["_typed_calls_link"] + ORDER_NOW[4:]
STEP_NAMES = set(ORDER_FUTURE)
# Calls between extraction and the push that take nodes/calls but never change them (sync's prune PLANNING —
# _plan_prune and the auto-sync evidence-only _plan_prune_evidence read the live keys and the graph — the FTS diff,
# and builtins). A new call on nodes/calls fails B until it is classified here or mirrored in offline_calls.
READ_ONLY_OK = {"_plan_prune", "_plan_prune_evidence", "_fts_changed_docs", "print", "len", "bool", "list", "dict",
                "sorted", "set", "getattr", "isinstance", "hasattr", "tuple", "sum", "any", "all", "min", "max",
                "enumerate", "zip", "str", "int", "repr"}
# Methods that never change their receiver: a call `nodes.<m>(...)` / `n.<m>(...)` (n an element of nodes/calls)
# with any other m fails B.
READ_ONLY_METHODS = {"get", "items", "keys", "values", "count", "index", "copy"}
PREREG_S8_SHA256 = "7fcd02d98b48f7b3fe375d2b09be5843bf0b15890804546484683b3173e13d0c"
FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:900]}"))


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"  [SKIP] {name} — {why}")


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _graph_flags_cleared() -> dict:
    """Every DEEPMAP_* graph flag unset (the caller's shell must not leak into a mode)."""
    return {k: None for k in os.environ if k.startswith("DEEPMAP_") and k not in ("DEEPMAP_HOME", "DEEPMAP_PYTHON")}


def _quiet():
    return contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())


def _load_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    so, se = _quiet()
    with so, se:
        spec.loader.exec_module(mod)
    return mod


# =====================================================================================================
# A — inventory, imports, isolation from the product
# =====================================================================================================
def gate_a(kit: Path) -> dict:
    print("\n-- A: the kit's files, their imports, and the product never importing them --")
    mods = {}
    missing = [f for f in KIT_FILES if not (kit / f).is_file()]
    check(f"every S0 file exists under {kit.name}/ ({len(KIT_FILES)} files)", not missing, missing)
    std = set(getattr(sys, "stdlib_module_names", ()))
    local = set(KIT_MODULES)
    bad = {}
    for py in sorted(kit.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad[py.name] = f"syntax: {exc}"
            continue
        for node in tree.body:   # module level only: lazy imports inside functions are the point
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n != "__future__" and n not in std and n not in local:
                    bad.setdefault(py.name, []).append(n)
    check("every kit module imports only the stdlib and its siblings at module level (jedi, neo4j, yaml, the "
          "product's modules are imported lazily, inside functions)", not bad, bad)
    if str(kit) not in sys.path:
        sys.path.insert(0, str(kit))
    for m in KIT_MODULES:
        try:
            so, se = _quiet()
            with so, se:
                mods[m] = __import__(m)
        except Exception as exc:  # noqa: BLE001
            check(f"kit module {m} imports", False, repr(exc))
    check("every kit module imports in the product's Python (no jedi / TypeScript needed to load them)",
          len(mods) == len(KIT_MODULES), sorted(set(KIT_MODULES) - set(mods)))
    leaks = []
    for d in (BUILD, REPO / "hooks"):
        for p in sorted(d.glob("*.py")):
            t = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*(import|from)\s+(jedi|parso)\b", t, re.M) or re.search(r"benchmark[./\\]+call_linking", t):
                leaks.append(p.relative_to(REPO).as_posix())
    check("nothing under build/ or hooks/ imports jedi / parso or names the measurement kit", not leaks, leaks)
    return mods


# =====================================================================================================
# B — the pass order, read from the ASTs
# =====================================================================================================
def _call_name(call: ast.Call) -> str:
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _calls_in(fn) -> list:
    out = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    out.sort(key=lambda c: (c.lineno, c.col_offset))
    return out


def _arg_names(call: ast.Call) -> set:
    return {a.id for a in list(call.args) + [k.value for k in call.keywords] if isinstance(a, ast.Name)}


def _names_in(*exprs) -> set:
    return {n.id for e in exprs if e is not None for n in ast.walk(e) if isinstance(n, ast.Name)}


def _root_name(expr):
    """The Name at the base of an Attribute / Subscript / Starred chain (`nodes[0]['x']` -> nodes), else None."""
    while isinstance(expr, (ast.Attribute, ast.Subscript, ast.Starred)):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _target_roots(t) -> list:
    """[(name, direct)] of an assignment target: direct = the name itself is rebound; else written INTO."""
    if isinstance(t, (ast.Tuple, ast.List)):
        return [r for e in t.elts for r in _target_roots(e)]
    if isinstance(t, ast.Starred):
        return _target_roots(t.value)
    if isinstance(t, ast.Name):
        return [(t.id, True)]
    r = _root_name(t)
    return [(r, False)] if r else []


def _unmirrored(pipe, l0: int, l1: int) -> list:
    """Every statement / call between extraction (line l0) and the push (line l1) that could CHANGE the nodes or
    calls the offline mirror reproduces without being a mirrored step:
      (1) an assignment / del / walrus / loop target that rebinds `nodes` / `calls` (unless the value is a
          mirrored step call or the unpack of the `_rebuild_code` result), or that writes INTO one of them or into
          an alias / element of them (`calls[:] = ...`, `nodes[0]['name'] = ...`, `n['x'] = ...` in a loop);
      (2) a method call on nodes / calls / an alias / an element of them (`calls.append(...)`, `n.setdefault(...)`)
          other than READ_ONLY_METHODS;
      (3) any other call that reaches nodes / calls / an alias / an element ANYWHERE in its arguments
          (`_drop_calls(list(calls))`, `f(items=calls)`) unless it is a step or READ_ONLY_OK;
    where aliases and elements are tracked to a fixpoint: a name bound to nodes / calls (or a part of them) by an
    assignment, and the target of a `for` / comprehension over them."""
    def _line(n):   # an ast.comprehension has no position of its own: its iterable's
        return n.iter.lineno if isinstance(n, ast.comprehension) else getattr(n, "lineno", -1)
    body = [n for n in ast.walk(pipe) if l0 <= _line(n) <= l1]
    extract_alias = {t.id for n in body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                     and _call_name(n.value) == "_rebuild_code" for t in n.targets if isinstance(t, ast.Name)}
    base = {"nodes", "calls"}
    tracked = set(base) | extract_alias

    def may_alias(v) -> bool:
        if isinstance(v, ast.Name):
            return v.id in tracked and v.id not in extract_alias
        if isinstance(v, (ast.Attribute, ast.Subscript, ast.Starred)):
            return _root_name(v) in tracked
        if isinstance(v, ast.IfExp):
            return may_alias(v.body) or may_alias(v.orelse)
        if isinstance(v, ast.BoolOp):
            return any(may_alias(x) for x in v.values)
        if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
            return any(may_alias(x) for x in v.elts)
        if isinstance(v, ast.NamedExpr):
            return may_alias(v.value)
        if isinstance(v, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return any(_names_in(g.iter) & tracked for g in v.generators)
        return False

    while True:
        before = len(tracked)
        for n in body:
            if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)) and _names_in(n.iter) & tracked:
                tracked |= {x for x in _names_in(n.target)}
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and n.value is not None \
                    and may_alias(n.value):
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                tracked |= {name for t in tgts for name, direct in _target_roots(t) if direct}
        if len(tracked) == before:
            break

    def step_value(v) -> bool:
        return (isinstance(v, ast.Call) and _call_name(v) in STEP_NAMES) or (
            isinstance(v, ast.Name) and v.id in extract_alias)

    out = []
    for n in sorted(body, key=lambda x: (_line(x), getattr(x, "col_offset", 0))):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete, ast.NamedExpr, ast.For,
                          ast.AsyncFor)):
            tgts = (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target])
            for t in tgts:
                for name, direct in _target_roots(t):
                    if name in base and direct:
                        if not (isinstance(n, (ast.Assign, ast.AnnAssign)) and step_value(n.value)):
                            out.append(f"rebinds {name} (line {n.lineno})")
                    elif name in tracked and not direct:
                        out.append(f"writes into {name} (line {n.lineno})")
                    elif name in tracked and direct and isinstance(n, (ast.AugAssign, ast.Delete)):
                        out.append(f"changes {name} in place (line {n.lineno})")
        elif isinstance(n, ast.Call):
            cn = _call_name(n)
            if cn in STEP_NAMES:
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and _root_name(f.value) in tracked:
                if f.attr not in READ_ONLY_METHODS:
                    out.append(f"{cn or f.attr} (a method call on {_root_name(f.value)}, line {n.lineno})")
                continue
            if _names_in(*n.args, *[k.value for k in n.keywords]) & tracked \
                    and (cn or "").split(".")[-1] not in READ_ONLY_OK:
                out.append(f"{cn or '<call>'} (line {n.lineno})")
    return sorted(set(out), key=lambda s: int(re.search(r"line (\d+)", s).group(1)))


def sync_order(src: str) -> dict:
    """{function, steps, unknown, typed_helper, reached_from_sync, push_first_resolve, problems} of a sync.py."""
    tree = ast.parse(src)
    top = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    pipe = None
    for name, fn in top.items():
        names = {_call_name(c) for c in _calls_in(fn)}
        if "_rebuild_code" in names and "push_to_neo4j" in names:
            pipe = fn
            break
    res = {"function": pipe.name if pipe else None, "steps": [], "unknown": [], "problems": [],
           "typed_helper": "_typed_calls_link" in top}
    if pipe is None:
        res["problems"].append("no function calls both _rebuild_code and push_to_neo4j")
        return res
    calls = _calls_in(pipe)
    names = [_call_name(c) for c in calls]
    i0 = names.index("_rebuild_code")
    i1 = names.index("push_to_neo4j")
    for c, n in zip(calls[i0:i1 + 1], names[i0:i1 + 1]):
        if n in STEP_NAMES:
            res["steps"].append(n)
    res["unknown"] = _unmirrored(pipe, calls[i0].lineno, calls[i1].lineno)
    sync_fn = top.get("sync")
    res["reached_from_sync"] = pipe.name == "sync" or (
        sync_fn is not None and pipe.name in {_call_name(c) for c in _calls_in(sync_fn)})
    push = top.get("push_to_neo4j")
    first = None
    if push is not None:
        for c in _calls_in(push):
            if _arg_names(c) & {"calls"}:
                first = c
                break
    res["push_first_resolve"] = bool(first is not None and _call_name(first) == "_resolve_calls"
                                     and [getattr(a, "id", None) for a in first.args[:2]] == ["nodes", "calls"])
    if push is not None and first is not None:
        for n in ast.walk(push):
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and n.lineno < first.lineno:
                tg = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(isinstance(t, ast.Name) and t.id in ("nodes", "calls") for x in tg for t in ast.walk(x)):
                    res["problems"].append(f"push_to_neo4j rebinds nodes/calls before _resolve_calls (line {n.lineno})")
    return res


def order_verdict(res: dict) -> "str | None":
    """None when the sync order is one the offline tool mirrors, else why not."""
    if res["problems"]:
        return "; ".join(res["problems"])
    if res["steps"] not in (ORDER_NOW, ORDER_FUTURE):
        return f"step order {res['steps']} is neither the current nor the typed-calls order"
    if res["unknown"]:
        return f"unmirrored change(s) to nodes/calls between extraction and the push: {res['unknown']}"
    if (res["steps"] == ORDER_FUTURE) != res["typed_helper"]:
        return ("sync calls _typed_calls_link without defining it at module level" if res["steps"] == ORDER_FUTURE
                else "sync defines _typed_calls_link but its pipeline does not call it (the offline mirror would)")
    if not res["reached_from_sync"]:
        return f"{res['function']} is not called by sync()"
    if not res["push_first_resolve"]:
        return "push_to_neo4j does not start from _resolve_calls(nodes, calls)"
    return None


_SYNTH = '''
def sync(root):
    return _sync_pass(root)

def _sync_pass(root):
    extracted = _rebuild_code(root)
    nodes, calls, imports, engine = extracted
    enrich.apply_sidecar(root, nodes)
    nodes, typed_edges = _apply_recognizers(root, nodes)
    plan = _plan_prune(driver, root, nodes, engine, prior, force)
    calls, n_unkey = _unkey_receiver_calls(root, nodes, calls)
{typed}    n_bare = _attach_bare_calls(root, nodes, calls)
{after_bare}    _attach_local_defs(root, nodes, calls)
    n_local = _attach_local_def_calls(root, nodes, calls)
    fts_changed = _fts_changed_docs(driver, nodes) if nodes else 0
    n_pushed, n_edges = push_to_neo4j(driver, nodes, calls, imports, typed_edges)

def push_to_neo4j(driver, nodes, calls, imports, typed_edges=None):
    if not nodes:
        return 0, 0
    edges = _resolve_calls(nodes, calls)
{helper}'''
_TYPED_LINE = "    calls, call_stats = _typed_calls_link(root, nodes, calls)\n"
_HELPER = "\ndef _typed_calls_link(root, nodes, calls):\n    return calls, None\n"


def gate_b(mods: dict, sync_path: Path, kit: Path) -> None:
    print("\n-- B: the offline pass order equals sync()'s (read from build/sync.py's AST) --")
    cases = {
        "the current order": (dict(typed="", after_bare="", helper=""), True),
        "the typed-calls order (+ its helper)": (dict(typed=_TYPED_LINE, after_bare="", helper=_HELPER), True),
        "the typed step without its helper": (dict(typed=_TYPED_LINE, after_bare="", helper=""), False),
        "the helper defined but never called": (dict(typed="", after_bare="", helper=_HELPER), False),
        "the typed step AFTER _attach_bare_calls": (dict(typed="", after_bare=_TYPED_LINE, helper=_HELPER), False),
        "an unmirrored pass on calls": (dict(typed="", after_bare="    _attach_foo(root, nodes, calls)\n", helper=""),
                                        False),
    }
    # The shapes a concurrent edit of sync.py could add between extraction and the push without a direct
    # `f(nodes, calls)` call (each must be REJECTED), and read-only shapes that must stay ACCEPTED.
    mutations = {
        "a comprehension rebinding calls": ("    calls = [(a, b) for (a, b) in calls if not str(b).startswith('_')]\n",
                                            False),
        "calls.append(...)": ("    calls.append(('x', 'y'))\n", False),
        "a call hiding calls inside a nested call": ("    calls = _drop_calls(list(calls))\n", False),
        "a slice assignment into calls": ("    calls[:] = calls[:1]\n", False),
        "nodes filtered by a comprehension": ("    nodes = [n for n in nodes if n.get('label') != 'Method']\n", False),
        "a write into a node": ("    nodes[0]['name'] = 'x'\n", False),
        "calls passed as a keyword argument": ("    calls = _drop_calls(items=calls)\n", False),
        "calls passed inside a keyword argument's expression": ("    _check(items=sorted(calls)[:5])\n", False),
        "an augmented assignment to calls": ("    calls += [('x', 'y')]\n", False),
        "del calls[0]": ("    del calls[0]\n", False),
        "a loop writing into each node": ("    for n in nodes:\n        n['x'] = 1\n", False),
        "a loop calling a mutator on each node": ("    for i, n in enumerate(nodes):\n        n.setdefault('x', i)\n",
                                                  False),
        "a comprehension calling a mutator on each node": ("    _ = [n.update(x=1) for n in nodes]\n", False),
        "an alias of calls, then a mutation through it": ("    c2 = calls\n    c2.append(('x', 'y'))\n", False),
        "an element alias, then a write into it": ("    first = nodes[0]\n    first['name'] = 'x'\n", False),
        "a read-only loop over nodes": ("    for n in nodes:\n        print(n.get('label'), len(n))\n", True),
        "read-only builtins over calls": ("    print(len(calls), sorted({c for _a, c in calls})[:1])\n", True),
    }
    for label, (line, want) in mutations.items():
        cases[label] = (dict(typed="", after_bare=line, helper=""), want)
    for label, (fill, want) in cases.items():
        v = order_verdict(sync_order(_SYNTH.format(**fill)))
        check(f"self-test: the checker {'accepts' if want else 'rejects'} {label}", (v is None) == want, v)
    swapped = _SYNTH.format(typed="", after_bare="", helper="").replace(
        "    _attach_local_defs(root, nodes, calls)\n    n_local = _attach_local_def_calls(root, nodes, calls)\n",
        "    n_local = _attach_local_def_calls(root, nodes, calls)\n    _attach_local_defs(root, nodes, calls)\n")
    check("self-test: the checker rejects two passes swapped", order_verdict(sync_order(swapped)) is not None)
    try:
        res = sync_order(sync_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check(f"{sync_path.name} parses", False, repr(exc))
        return
    v = order_verdict(res)
    check(f"{sync_path.name}: {res['function']} runs exactly the current or the typed-calls order (found: "
          f"{'typed-calls' if res['steps'] == ORDER_FUTURE else 'current' if res['steps'] == ORDER_NOW else res['steps']})",
          v is None, v)
    oc_path = kit / "offline_calls.py"
    try:
        tree = ast.parse(oc_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("offline_calls.py parses", False, repr(exc))
        return
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    rp = fns.get("run_pipeline")
    mapping = {"typed_calls_step": "_typed_calls_link", "_resolve_calls": "push_to_neo4j"}
    steps = []
    if rp is not None:
        for c in _calls_in(rp):
            n = _call_name(c)
            n = n[2:] if n.startswith("S.") else n
            n = mapping.get(n, n)
            if n in STEP_NAMES:
                steps.append(n)
    check("offline_calls.run_pipeline runs the typed-calls order (the step is a no-op while sync lacks it)",
          steps == ORDER_FUTURE, steps)
    ts = fns.get("typed_calls_step")
    guarded = ts is not None and any(
        isinstance(n, ast.Call) and _call_name(n) == "getattr" and len(n.args) >= 3
        and isinstance(n.args[1], ast.Constant) and n.args[1].value == "_typed_calls_link"
        for n in ast.walk(ts))
    check("offline_calls reaches the typed step only through getattr(S, '_typed_calls_link', None) (guarded)",
          guarded)
    oc = mods.get("offline_calls")
    if oc is not None:
        want = tuple("_resolve_calls" if s == "push_to_neo4j" else s for s in ORDER_FUTURE)
        check("offline_calls.PIPELINE spells the same order", tuple(getattr(oc, "PIPELINE", ())) == want,
              getattr(oc, "PIPELINE", None))
        dummy = SimpleNamespace()
        sentinel = [("a", "b")]
        check("typed_calls_step: a sync without the helper returns the SAME list and no stats",
              oc.typed_calls_step(dummy, None, [], sentinel) == (sentinel, None)
              and oc.typed_calls_step(dummy, None, [], sentinel)[0] is sentinel)
        seen = []
        dummy2 = SimpleNamespace(_typed_calls_link=lambda r, n, c: (seen.append((r, len(n), len(c))) or (c + [("x", "y")], {"ok": 1})))
        out = oc.typed_calls_step(dummy2, "R", [1], sentinel)
        check("typed_calls_step: a sync with the helper calls it once with (root, nodes, calls) and returns its "
              "(calls, stats)", seen == [("R", 1, 1)] and out == (sentinel + [("x", "y")], {"ok": 1}), (seen, out))


# =====================================================================================================
# C — data flow: the real sync() pass vs offline_calls.run_pipeline on the same extraction
# =====================================================================================================
PROJECT = {
    "pkg/__init__.py": "",
    "pkg/models.py": (
        "class Order:\n"
        "    def save(self):\n"
        "        return self.validate()\n\n"
        "    def validate(self):\n"
        "        return True\n\n\n"
        "class Invoice:\n"
        "    def save(self):\n"
        "        return 1\n\n\n"
        "class Serializer:\n"
        "    def dumps(self, obj):\n"
        "        return str(obj)\n\n\n"
        "def helper():\n"
        "    return 2\n"),
    "pkg/service.py": (
        "from pkg.models import Order, helper\n\n\n"
        "def place():\n"
        "    o = Order()\n"
        "    o.save()\n"
        "    helper()\n"
        "    return save()\n\n\n"
        "def save():\n"
        "    return 3\n\n\n"
        "class Runner:\n"
        "    def run(self):\n"
        "        self.step()\n"
        "        return place()\n\n"
        "    def step(self):\n"
        "        return helper()\n\n\n"
        "def finish():\n"
        "    return save()\n"),
    "pkg/other.py": (
        "from json import dumps\n\n\n"
        "def helper():\n"
        "    return 4\n\n\n"
        "def use():\n"
        "    dumps({})\n"
        "    return helper()\n"),
    "tests/test_service.py": (
        "from pkg.service import place\n\n\n"
        "def test_place():\n"
        "    place()\n"),
}


def _write_project(root: Path) -> Path:
    for rel, text in PROJECT.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    return root


class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None

    def data(self):
        return []


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **k):
        return _Res()

    def begin_transaction(self):
        return self

    def commit(self):
        return None

    def close(self):
        return None


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _fresh_sync():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    return _load_file(BUILD / "sync.py", f"_cl_s0_sync_{time.time_ns()}")


def _clone_extraction(S, ex):
    nodes, calls, imports, engine = ex
    return S._Extraction([copy.deepcopy(n) for n in nodes], list(calls), list(imports or []), engine,
                         copy.deepcopy(getattr(ex, "info", {}) or {}))


def _sync_edges(root: Path, frozen) -> dict:
    """The REAL sync() pass on `root` with the extraction frozen and push_to_neo4j recorded: the :CALLS dict
    sync's push would write (its own _resolve_calls on exactly the nodes/calls sync handed the push)."""
    S = _fresh_sync()
    got = {}

    def _push(driver, nodes, calls, imports, typed_edges=None):
        got["edges"] = S._resolve_calls(nodes, calls)
        got["calls"] = list(calls)
        return len(nodes), len(got["edges"])
    S._rebuild_code = lambda r: _clone_extraction(S, frozen)
    S.push_to_neo4j = _push
    S._graph_has_nodes = lambda d: False
    S._graph_holds_nodes = lambda d: False
    S._graph_node_count = lambda d: 0
    S._prune_blast_radius = lambda d, live: (0, 0)
    S._cleanup_stale = lambda d, live: None
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 0
    S._maybe_auto_tune = lambda r: None
    so, se = _quiet()
    with so, se:
        ok = S.sync(root, driver=_Drv())
    got["ok"] = ok
    return got


def gate_c(mods: dict, base: Path, real: bool) -> None:
    print("\n-- C: the real sync() pass and offline_calls.run_pipeline push the identical :CALLS dict --")
    oc = mods.get("offline_calls")
    if oc is None:
        check("offline_calls is importable for the data-flow check", False)
        return
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import graphify_extract as GX  # noqa: E402
    engines =[("ast", {"DEEPMAP_EXTRACTOR": "ast"})]
    have_graphify = importlib.util.find_spec("graphify") is not None
    if real and have_graphify:
        engines.append(("graphify", {"DEEPMAP_EXTRACTOR": "graphify"}))
    elif not have_graphify:
        skip("C: the graphify-engine modes", "graphify is not installed in this Python")
    modes = {"default": {}, "DEEPMAP_PRECISE_CALLS=1": {"DEEPMAP_PRECISE_CALLS": "1"},
             "DEEPMAP_LOCAL_DEF_CALLS=1": {"DEEPMAP_LOCAL_DEF_CALLS": "1"}}
    S0 = _fresh_sync()
    if callable(getattr(S0, "_typed_calls_link", None)) and importlib.util.find_spec("typed_calls") is not None:
        modes["DEEPMAP_TYPED_CALLS=python"] = {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "1"}
    teeth = {}                                   # pass -> the (engine, mode) where skipping it changed the edges
    for eng, eng_env in engines:
        for mode, menv in modes.items():
            root = _write_project(base / f"proj_{eng}_{re.sub(r'[^A-Za-z0-9]+', '_', mode)}")
            home = base / f"home_{eng}"
            env = dict(_graph_flags_cleared(), **eng_env, **menv, NEO4J_URI="bolt://127.0.0.1:9",
                       DEEPMAP_HOME=str(home), DEEPMAP_AUTO_GITIGNORE="0")
            try:
                with _env(**env):
                    S = _fresh_sync()
                    so, se = _quiet()
                    with so, se:
                        frozen = S._rebuild_code(root)
                    synced = _sync_edges(root, frozen)
                    S2 = _fresh_sync()
                    with so, se:
                        _n, calls2, edges2, meta = oc.run_pipeline(S2, root, extraction=_clone_extraction(S2, frozen))
            except Exception as exc:  # noqa: BLE001
                check(f"C [{eng} | {mode}] runs", False, f"{exc!r}\n{traceback.format_exc()[-600:]}")
                continue
            e1 = synced.get("edges")
            check(f"C [{eng} | {mode}]: sync() pushed {len(e1 or {})} CALLS edges; offline_calls computes the identical "
                  f"dict, in the same order (engine {meta.get('engine')}, {len(frozen[0])} nodes)",
                  synced.get("ok") is True and e1 is not None and list(e1.items()) == list(edges2.items()),
                  {"ok": synced.get("ok"), "only_sync": sorted(set(e1 or {}) - set(edges2))[:5],
                   "only_offline": sorted(set(edges2) - set(e1 or {}))[:5]})
            check(f"C [{eng} | {mode}]: ... and the calls handed to the push are identical (keys stay marked)",
                  [(a, b, GX.is_call_key(b)) for a, b in synced.get("calls", [])]
                  == [(a, b, GX.is_call_key(b)) for a, b in calls2])
            for skipped_pass in ("_unkey_receiver_calls", "_attach_bare_calls", "_attach_local_defs",
                                 "_attach_local_def_calls"):
                with _env(**env):
                    S3 = _fresh_sync()
                    ex = _clone_extraction(S3, frozen)
                    so, se = _quiet()
                    with so, se:
                        nodes3, _typed3 = S3._apply_recognizers(root, ex[0])
                        calls3 = ex[1]
                        if skipped_pass != "_unkey_receiver_calls":
                            calls3, _u = S3._unkey_receiver_calls(root, nodes3, calls3)
                        for step in ("_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls"):
                            if step != skipped_pass:
                                getattr(S3, step)(root, nodes3, calls3)
                        without = S3._resolve_calls(nodes3, calls3)
                if without != e1:
                    teeth.setdefault(skipped_pass, f"{eng} | {mode}")
    check("C: not vacuous — skipping _attach_local_def_calls changes the pushed edges (DEEPMAP_LOCAL_DEF_CALLS=1)",
          "_attach_local_def_calls" in teeth, teeth)
    if any(e == "graphify" for e, _x in engines):
        check("C: not vacuous — skipping _attach_bare_calls changes the pushed edges (graphify: a bare-only `save()` "
              "keeps only the module function)", "_attach_bare_calls" in teeth, teeth)
    # The CLI: deterministic bytes, and its guards.
    root = _write_project(base / "cli_proj")
    home = base / "cli_home"
    oc_path = Path(oc.__file__)
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("DEEPMAP_") or k == "NEO4J_URI":
            env.pop(k)
    env.update(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR="ast", PYTHONUTF8="1", PYTHONHASHSEED="0",
               CALL_LINKING_READONLY_ROOTS=str(base / "ro_nothing"))
    outs = []
    for rep in ("a", "b"):
        o = base / f"cli_{rep}.json"
        p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(o), "--build", str(BUILD)],
                           env=env, capture_output=True, text=True, timeout=600)
        outs.append((p.returncode, o.read_bytes() if o.is_file() else b"", p.stderr[-400:]))
    check("offline_calls build: two runs on the same tree are byte-identical (no timings in the dump)",
          outs[0][0] == 0 and outs[1][0] == 0 and outs[0][1] == outs[1][1] and outs[0][1], [o[0] for o in outs] + [outs[0][2]])
    if outs[0][1]:
        d = json.loads(outs[0][1])
        check("... the dump records schema, code hash, engine, flags, the effective switches and sorted edges",
              d.get("schema") == "call-linking/offline-calls/1" and len((d.get("code") or {}).get("sha256", "")) == 64
              and d.get("engine") == "ast" and isinstance(d.get("effective"), dict)
              and d.get("edges") == sorted(d.get("edges") or []),
              {k: d.get(k) for k in ("schema", "engine", "effective")})
    pv = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "void.json"), "--build", str(BUILD),
                         "--expect-engine", "graphify"], env=env, capture_output=True, text=True, timeout=600)
    vd = json.loads((base / "void.json").read_text(encoding="utf-8")) if (base / "void.json").is_file() else {}
    pa = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "ok_ast.json"), "--build",
                         str(BUILD), "--expect-engine", "ast"], env=env, capture_output=True, text=True, timeout=600)
    check("offline_calls --expect-engine graphify: a dump that resolved to another engine is VOID (exit 3, `void` "
          "recorded); the engine it did resolve to passes",
          pv.returncode == 3 and "void" in vd and (vd.get("extraction") or {}).get("resolved") == "ast"
          and pa.returncode == 0, (pv.returncode, pa.returncode, pv.stderr[-300:]))
    pz = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "inert.json"), "--build",
                         str(BUILD)], env=dict(env, DEEPMAP_ZZ_NOT_A_FLAG="1"), capture_output=True, text=True,
                        timeout=600)
    zd = json.loads((base / "inert.json").read_text(encoding="utf-8")) if (base / "inert.json").is_file() else {}
    check("offline_calls build: a variant flag the code under test never names is VOID (exit 3, `void` recorded) — "
          "an inert variant is never scored as 'no effect'",
          pz.returncode == 3 and "DEEPMAP_ZZ_NOT_A_FLAG" in str(zd.get("void")), (pz.returncode, zd.get("void"),
                                                                               pz.stderr[-300:]))
    env_nohome = {k: v for k, v in env.items() if k != "DEEPMAP_HOME"}
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "x.json")], env=env_nohome,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses to run without DEEPMAP_HOME (exit 2, nothing written)",
          p.returncode == 2 and not (base / "x.json").exists(), p.stderr[-300:])
    fake_user = base / "user"
    (fake_user / ".deepmap").mkdir(parents=True)
    env_op = dict(env, DEEPMAP_HOME=str(fake_user / ".deepmap"), USERPROFILE=str(fake_user), HOME=str(fake_user))
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "y.json")], env=env_op,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses the operator's own ~/.deepmap as DEEPMAP_HOME (exit 2)",
          p.returncode == 2 and not (base / "y.json").exists(), p.stderr[-300:])
    env_ro = dict(env, CALL_LINKING_READONLY_ROOTS=str(base))
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "z.json")], env=env_ro,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses a corpus under a read-only clone root (copy it first; exit 2)",
          p.returncode == 2 and not (base / "z.json").exists(), p.stderr[-300:])
    gate_c_wallclock(mods, base, env)
    gate_c_vf(mods, base, env, real and have_graphify)


# A key that names a wall-clock value (`seconds`, `t_total_s`, `elapsed`, `duration_ms`, ...): a dump must hold none.
# Case-sensitive on purpose: typed_calls' shape names are single capitals (`by_shape.S` is shape S, not seconds).
_WALL_CLOCK_KEY = re.compile(r"(?:^|_)(?:seconds|secs|elapsed|duration|wall|timestamp|time)$|_(?:s|ms|ts)$")


def _wall_clock_keys(obj, path="") -> list:
    """Every dict key under `obj` (recursively) that names a wall-clock value, as a dotted path."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if _WALL_CLOCK_KEY.search(str(k)):
                out.append(p)
            out += _wall_clock_keys(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2000]):
            out += _wall_clock_keys(v, f"{path}[{i}]")
    return out


@contextlib.contextmanager
def _fake_clock(step: float):
    """Every clock of the `time` module (perf_counter / monotonic / time / process_time / thread_time and their
    _ns forms) advances by `step` seconds per read: two runs under two steps measure two different wall times."""
    names = [n for n in ("perf_counter", "monotonic", "time", "process_time", "thread_time") if hasattr(time, n)]
    saved = {n: getattr(time, n) for n in names}
    saved.update({f"{n}_ns": getattr(time, f"{n}_ns") for n in names if hasattr(time, f"{n}_ns")})
    state = {"t": 1000.0}

    def tick():
        state["t"] += step
        return state["t"]
    try:
        for n in names:
            setattr(time, n, tick)
            if f"{n}_ns" in saved:
                setattr(time, f"{n}_ns", lambda: int(tick() * 1e9))
        yield
    finally:
        for n, f in saved.items():
            setattr(time, n, f)


def gate_c_wallclock(mods: dict, base: Path, env: dict) -> None:
    """The determinism check (PREREG §8.4 G: each dump run twice is byte-identical) is about graph CONTENT; a timing
    is P-layer data. typed_calls.link's stats carry the pass's own wall time (`seconds`, build/typed_calls.py: "the
    only non-deterministic value"), and the kit used to copy them verbatim into the dump's `call_linking` block — so
    almost every typed-variant pair of the first RC matrix read "the two runs differ" (2026-10-24 coordinator ruling).
    Locked here: make_dump writes no wall-clock field (typed stats included) and leaves the caller's stats intact;
    strip_wall_clock turns a pre-fix dump into exactly that; two in-process runs of the typed pass under two
    different fake clocks give byte-identical dumps (any time-derived value reaching the dump fails this, whatever
    its name); the CLI's two typed runs are byte-identical and the pass's own seconds go to <out>.timing.json."""
    oc = mods.get("offline_calls")
    if oc is None or not callable(getattr(oc, "make_dump", None)):
        check("C wall clock: offline_calls.make_dump is importable", False)
        return
    xn = [_n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10)]
    stats = {"version": 4, "engine": "ast", "emitted": 3, "removed": 1, "grades": {"EXTRACTED": 1, "INFERRED": 2},
             "notes": [], "error": None, "seconds": 1.234}
    meta = {"info": {}, "call_stats": stats, "typed_step": "present", "n_unkey": 0, "n_bare": 0, "n_local": 0}
    try:
        d = oc.make_dump(base, xn, [("a.f", "a.g")], {("a.f", "a.g"): "INFERRED"}, meta, code=None)
    except Exception as exc:  # noqa: BLE001
        check("C wall clock: make_dump builds a dump from typed-call stats", False, repr(exc))
        return
    cl = d.get("call_linking")
    check("offline_calls.make_dump: a dump built from typed-call stats carries NO wall-clock field (call_linking "
          "without typed_calls' `seconds`, every other stat kept; no timing key anywhere in the dump) and the caller's "
          "stats keep `seconds` (it is P-layer data: the timing sidecar's)",
          isinstance(cl, dict) and "seconds" not in cl and cl.get("emitted") == 3 and cl.get("removed") == 1
          and cl.get("grades") == {"EXTRACTED": 1, "INFERRED": 2} and not _wall_clock_keys(d)
          and stats.get("seconds") == 1.234,
          {"call_linking": cl, "wall_clock_keys": _wall_clock_keys(d), "caller_stats_seconds": stats.get("seconds")})
    strip = getattr(oc, "strip_wall_clock", None)
    if callable(strip):
        old = copy.deepcopy(d)
        old["call_linking"] = dict(old.get("call_linking") or {}, seconds=9.876)
        none_cl = dict(copy.deepcopy(d), call_linking=None)
        dj = oc.C.dump_json
        check("offline_calls.strip_wall_clock: a dump of the pre-fix kit (call_linking.seconds present) reduces to "
              "exactly the bytes make_dump writes now — that field and nothing else — and leaves its input unchanged; "
              "a dump with no typed stats is unchanged (the determinism re-judge of pre-fix pairs uses it)",
              dj(strip(old)) == dj(d) and old["call_linking"].get("seconds") == 9.876
              and dj(strip(none_cl)) == dj(none_cl) and dj(strip(d)) == dj(d),
              {"stripped_cl": (strip(old) or {}).get("call_linking")})
    else:
        check("offline_calls.strip_wall_clock exists (removes exactly the wall-clock fields from a dump)", False)
    # In process, the real pass under two different fake clocks: any time-derived value reaching the dump shows.
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    S0 = _fresh_sync()
    if not (callable(getattr(S0, "_typed_calls_link", None)) and importlib.util.find_spec("typed_calls") is not None):
        skip("C wall clock: the typed pass under two fake clocks / the CLI's typed runs",
             "build/sync.py has no typed-call step or build/typed_calls.py is absent")
        return
    tenv = {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "1"}
    root = _write_project(base / "wallclock_proj")
    run_env = dict(_graph_flags_cleared(), DEEPMAP_EXTRACTOR="ast", NEO4J_URI="bolt://127.0.0.1:9",
                   DEEPMAP_HOME=str(base / "wallclock_home"), DEEPMAP_AUTO_GITIGNORE="0", **tenv)
    dumps, secs = [], []
    try:
        with _env(**run_env):
            S = _fresh_sync()
            so, se = _quiet()
            with so, se:
                frozen = S._rebuild_code(root)
            for step in (0.25, 7.0):
                S2 = _fresh_sync()
                so, se = _quiet()
                with so, se, _fake_clock(step):
                    nodes, calls, edges, m = oc.run_pipeline(S2, root, extraction=_clone_extraction(S2, frozen))
                m["effective"] = oc.effective_switches(S2)
                dumps.append(oc.C.dump_json(oc.make_dump(root, nodes, calls, edges, m, code=None)))
                secs.append((m.get("call_stats") or {}).get("seconds"))
    except Exception as exc:  # noqa: BLE001
        check("C wall clock: the typed pass runs in process under a fake clock", False,
              f"{exc!r}\n{traceback.format_exc()[-600:]}")
        return
    d0 = json.loads(dumps[0]) if dumps else {}
    cl0 = d0.get("call_linking")
    check("offline_calls: the real typed pass (DEEPMAP_TYPED_CALLS=python + DEEPMAP_CALLS_AUDIT=1, ast engine) run "
          "twice on one extraction under two different fake clocks gives byte-identical dumps — the pass's own "
          "`seconds` differed (the clocks bit) and reached no byte of the dump",
          len(dumps) == 2 and dumps[0] == dumps[1] and isinstance(cl0, dict) and not cl0.get("error")
          and len(secs) == 2 and None not in secs and secs[0] != secs[1] and not _wall_clock_keys(d0),
          {"seconds": secs, "call_linking_error": (cl0 or {}).get("error") if isinstance(cl0, dict) else cl0,
           "wall_clock_keys": _wall_clock_keys(d0)[:5],
           "first_diff": next((i for i, (x, y) in enumerate(zip(*dumps)) if x != y), None) if len(dumps) == 2 else None})
    # The CLI: two typed runs, byte-identical; the pass's own seconds in the sidecar.
    cenv = dict(env, **tenv)
    outs = []
    for rep in ("a", "b"):
        o = base / f"cli_typed_{rep}.json"
        p = subprocess.run([sys.executable, str(Path(oc.__file__)), "build", str(root), str(o), "--build", str(BUILD)],
                           env=cenv, capture_output=True, text=True, timeout=600)
        tf = Path(str(o) + ".timing.json")
        outs.append((p.returncode, o.read_bytes() if o.is_file() else b"",
                     json.loads(tf.read_text(encoding="utf-8")) if tf.is_file() else None, p.stderr[-400:]))
    da = json.loads(outs[0][1]) if outs[0][1] else {}
    cla = da.get("call_linking")
    timings = [o[2] for o in outs]
    check("offline_calls build with the typed pass on: two runs on the same tree are byte-identical (exit 0), the dump "
          "holds the typed stats without any wall-clock field",
          [o[0] for o in outs] == [0, 0] and outs[0][1] and outs[0][1] == outs[1][1] and isinstance(cla, dict)
          and not cla.get("error") and "seconds" not in cla and not _wall_clock_keys(da),
          {"rc": [o[0] for o in outs], "identical": outs[0][1] == outs[1][1], "wall_clock_keys": _wall_clock_keys(da)[:5],
           "stderr": outs[0][3]})
    check("... and each run's timing sidecar (<out>.timing.json) carries the P-layer timings: the typed pass's own "
          "seconds (call_linking_seconds, as typed_calls measured it) beside t_extract_s / t_typed_s / t_total_s",
          all(isinstance(t, dict) and isinstance(t.get("call_linking_seconds"), (int, float))
              and not isinstance(t.get("call_linking_seconds"), bool)
              and all(isinstance(t.get(k), (int, float)) for k in ("t_extract_s", "t_typed_s", "t_total_s"))
              for t in timings), timings)


# The review's probe: a Python and a TypeScript caller of `helper()`, with a `helper` in each language.
VF_PROJECT = {"a.py": "from c import helper\n\n\ndef main():\n    return helper()\n",
              "c.py": "def helper():\n    return 2\n",
              "b.ts": ("export function helper(): number {\n  return 1;\n}\n\n"
                       "export function run(): number {\n  return helper();\n}\n")}


def gate_c_vf(mods: dict, base: Path, env: dict, graphify: bool) -> None:
    """End to end, the real CLI on the real engine: VF (DEEPMAP_CALLS_FAMILY_FILTER=1) against V0 under the
    variants' clean environments. The filter removes the cross-family targets, and delta measures it — never VOID
    as an inert pair (the review found every VF delta VOID: `effective` did not report the filter)."""
    V, D, oc = mods.get("variants"), mods.get("delta"), mods.get("offline_calls")
    if not graphify:
        skip("C: VF vs V0 end to end (offline_calls build + delta)", "needs the graphify engine (TypeScript)")
        return
    if V is None or D is None or oc is None:
        check("C: variants / delta / offline_calls are importable for the VF end-to-end check", False)
        return
    root = base / "vf_proj"
    for rel, text in VF_PROJECT.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    rcs, dumps = {}, {}
    for vid in ("V0", "VF"):
        out = base / f"vf_{vid}.json"
        p = subprocess.run([sys.executable, str(Path(oc.__file__)), "build", str(root), str(out), "--build", str(BUILD),
                            "--expect-engine", "graphify"], env=V.child_env(vid, base=env), capture_output=True,
                           text=True, timeout=900)
        rcs[vid] = (p.returncode, p.stderr[-300:])
        dumps[vid] = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    rc_d, out_d, _e, ex_d = _cli(D.main, [str(base / "vf_V0.json"), str(base / "vf_VF.json")])
    d = D.delta(dumps.get("V0") or {}, dumps.get("VF") or {}) if dumps.get("V0") and dumps.get("VF") else {}

    def ext(f):
        return Path(str(f or "")).suffix.lower()
    removed = (d.get("items") or {}).get("removed") or []
    check("C: VF vs V0 end to end on the real engine — both dumps are data (exit 0), the filter removed only "
          "cross-family targets, and delta measures it (exit 0, not VOID)",
          rcs["V0"][0] == 0 and rcs["VF"][0] == 0 and (rc_d, ex_d) == (0, None) and d.get("void") == []
          and len(removed) == 2 and all(ext(i["caller_file"]) != ext(i["callee_file"]) for i in removed)
          and (dumps["VF"].get("effective") or {}).get("calls_family_filter") is True
          and (dumps["V0"].get("effective") or {}).get("calls_family_filter") is False,
          {"rc": rcs, "delta": (rc_d, ex_d, out_d[-400:]), "removed": [(i["caller"], i["callee"]) for i in removed]})


# =====================================================================================================
# D — the frozen flask graph
# =====================================================================================================
def gate_d(mods: dict, kit: Path, base: Path, real: bool) -> None:
    print("\n-- D: the offline counts reproduce the frozen flask graph (330 CALLS / 26 AMBIGUOUS on 0.8.39) --")
    try:
        frozen = json.loads((kit / "frozen" / "flask_V0.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("frozen/flask_V0.json loads", False, repr(exc))
        return
    e39 = (frozen.get("engines") or {}).get("0.8.39") or {}
    check("the frozen 0.8.39 flask graph is the registered one: 330 CALLS, 26 AMBIGUOUS",
          e39.get("n_edges") == 330 and (e39.get("conf") or {}).get("AMBIGUOUS") == 26, e39)
    if not real:
        skip("D: re-dump flask", "--no-real")
        return
    clones = Path(os.environ.get("CALL_LINKING_CLONES", "C:/tmp/deepmap-bench-clones"))
    src = clones / "pallets__flask"
    if not (src / ".git").exists():
        skip("D: re-dump flask", f"no benchmark clone at {src} (set CALL_LINKING_CLONES)")
        return
    try:
        import importlib.metadata as md
        ver = md.version("graphifyy")
    except Exception:  # noqa: BLE001
        ver = None
    want = (frozen.get("engines") or {}).get(ver or "")
    if not want:
        skip("D: re-dump flask", f"graphifyy {ver} has no frozen flask graph (frozen: {sorted(frozen.get('engines') or {})})")
        return
    dest = base / "flask"
    for argv in (["git", "clone", "-q", "--no-hardlinks", str(src), str(dest)],
                 ["git", "-C", str(dest), "checkout", "-q", "--detach", FLASK_SHA]):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            check("D: copy the flask clone at its pinned SHA", False, p.stderr[-300:])
            return
    oc = mods["offline_calls"]
    V = mods["variants"]
    env = V.child_env("V0")
    env.update(DEEPMAP_HOME=str(base / "flask_home"))
    out = base / "flask_V0.json"
    p = subprocess.run([sys.executable, oc.__file__, "build", str(dest), str(out), "--build", str(BUILD),
                        "--expect-engine", "graphify"], env=env, capture_output=True, text=True, timeout=1800)
    void = [ln for ln in (p.stderr or "").splitlines() if ln.startswith("[offline_calls] VOID")]
    check("D: offline_calls dumps flask engine-pinned (--expect-engine graphify: exit 0, no VOID — an ast fallback "
          "would be named here, not read as a count mismatch)", p.returncode == 0 and out.is_file() and not void,
          f"exit {p.returncode}: {void[:1] or p.stderr[-500:]}")
    if p.returncode != 0 or not out.is_file():
        return
    d = json.loads(out.read_text(encoding="utf-8"))
    C = mods["_common"]
    sk = {n["key"]: C.stable_key(n) for n in d["nodes"]}
    h = hashlib.sha256("\n".join(sorted(f"{sk[a]}\t{sk[b]}\t{g}" for a, b, g in d["edges"])).encode("utf-8")).hexdigest()
    got = {"n_nodes": d["counts"]["n_nodes"], "n_edges": d["counts"]["n_edges"], "conf": d["counts"]["conf"]}
    check(f"D: flask on graphifyy {ver}: {got['n_edges']} CALLS / {got['conf'].get('AMBIGUOUS', 0)} AMBIGUOUS, "
          f"{got['n_nodes']} nodes == frozen", {k: want[k] for k in got} == got, (got, want))
    check("D: ... and the exact frozen edge set (sha256 over the version-stable edge lines)",
          h == want.get("edges_sha256"), h)


# =====================================================================================================
# E — the scorers, exact on synthetic inputs
# =====================================================================================================
def _n(key, label, name, f, line):
    return {"key": key, "label": label, "name": name, "source_file": f, "start_line": line, "end_line": None}


NODES = [_n("a", "Module", "a", "a.py", 1), _n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10),
         _n("a.C", "Class", "C", "a.py", 20), _n("a.C.m", "Method", "C.m", "a.py", 21),
         _n("b.D.m", "Method", "D.m", "b.py", 5), _n("b.h", "Function", "h", "b.py", 1),
         _n("t.test_x", "Function", "test_x", "tests/t.py", 1)]


def _row(f, stack, form, name, defs):
    return {"file": f, "line": 0, "col": 0, "form": form, "name": name, "defstack": stack, "defs": defs}


def _d(f, line, typ, name, alt=()):
    return {"file": f, "line": line, "alt_lines": list(alt), "type": typ, "name": name}


TRUTH = {"schema": "call-linking/truth/1", "rows": [
    _row("a.py", [["f", 3, []]], "bare", "g", [_d("a.py", 10, "function", "g")]),
    _row("a.py", [["f", 3, []]], "name_attr", "m", [_d("a.py", 21, "function", "m")]),
    _row("a.py", [["g", 10, []]], "recv", "m", [_d("b.py", 5, "function", "m")]),
    _row("a.py", [["g", 10, []]], "bare", "C", [_d("a.py", 20, "class", "C")]),
    _row("a.py", [], "bare", "f", [_d("a.py", 3, "function", "f")]),
    _row("a.py", [["f", 3, []]], "bare", "print", []),
    _row("b.py", [["h", 2, [1]]], "bare", "g", [_d("a.py", 10, "function", "g")]),
    _row("tests/t.py", [["test_x", 1, []]], "name_attr", "h", [_d("b.py", 2, "function", "h", [1])]),
    _row("a.py", [["f", 3, []]], "self", "zz", [_d("a.py", 99, "function", "zz")]),
]}


def _dump(edges):
    return {"schema": "call-linking/offline-calls/1", "nodes": copy.deepcopy(NODES), "edges": [list(e) for e in edges],
            "flags": {}}


DUMP = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "b.D.m", "AMBIGUOUS"), ("a.f", "a.C.m", "AMBIGUOUS"),
              ("a.g", "b.D.m", "EXTRACTED"), ("a.g", "a.f", "INFERRED"), ("b.h", "a.g", "INFERRED"),
              ("a", "a.f", "INFERRED")])


def _eg(nodes, edges, **extra) -> dict:
    return dict({"nodes": [_n(*x) for x in nodes], "edges": [list(e) for e in edges]}, **extra)


def _cli(fn, argv) -> tuple:
    """(exit code or None, stdout, stderr, the exception's repr or None) of a kit main(): a crash is a RED result."""
    out, err = io.StringIO(), io.StringIO()
    rc, exc = None, None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(argv)
        except SystemExit as e:   # noqa: PERF203 — a SystemExit is an exit code, not a crash
            rc = e.code
        except Exception as e:  # noqa: BLE001
            exc = repr(e)
    return rc, out.getvalue(), err.getvalue(), exc


def gate_e_engdiff_common(EG, base: Path) -> None:
    """PREREG amendment A1 (before RC): engine parity E compares the edges whose BOTH endpoints exist on both
    engines (version-stable keys) — graphify 0.8.39 makes no node for TS `static create = () =>` members that
    0.8.51 does, so an edge touching such a node can exist on one engine only whatever the variant does; the raw
    symmetric difference over all edges is reported beside it, never decisive."""
    N39 = [("p", "Function", "f", "a.py", 1), ("q", "Function", "g", "a.py", 5)]
    N51 = [("P", "Function", "f", "a.py", 1), ("Q", "Function", "g", "a.py", 5), ("H", "Method", "K.h", "a.py", 9)]
    b39 = _eg(N39, [("p", "q", "INFERRED")])
    b51 = _eg(N51, [("P", "Q", "INFERRED"), ("H", "Q", "INFERRED")])                     # H: a node on 0.8.51 only
    t39 = _eg(N39, [("p", "q", "INFERRED")])
    t51 = _eg(N51, [("P", "Q", "INFERRED"), ("H", "Q", "INFERRED"), ("P", "H", "EXTRACTED"), ("Q", "H", "INFERRED")])
    x39 = _eg(N39, [("p", "q", "INFERRED"), ("q", "p", "EXTRACTED")])                     # g -> f on 0.8.39 only
    try:
        pr = EG.parity(b39, b51)
        cr = EG.criterion(t39, t51, b39, b51)
        cx = EG.criterion(x39, t51, b39, b51)
    except Exception as exc:  # noqa: BLE001 — the pre-amendment engdiff is a RED result, never a crash
        check("engdiff (amendment A1) runs on a corpus with a node on one engine only", False, repr(exc))
        return
    check("engdiff parity (A1): a node on one engine only is listed; an edge touching it counts in the RAW "
          "symmetric difference (1) and not in the one over edges whose both endpoints exist on both engines (0)",
          (pr.get("symdiff"), pr.get("symdiff_common"), pr.get("nodes_common"), pr.get("nodes_only_a"),
           pr.get("nodes_only_b")) == (1, 0, 2, [], ["Method:K.h@a.py:9"]),
          {k: pr.get(k) for k in ("symdiff", "symdiff_common", "nodes_common", "nodes_only_a", "nodes_only_b")})
    check("engdiff E (A1): typed edges to a node 0.8.39 lacks (raw symdiff 3 > V0's 1) HOLD on the common "
          "endpoints (0 <= 0); the raw comparison is reported (holds_raw False), the nodes on one engine are named, "
          "and no engine-specific change is left on common endpoints (2 raw)",
          cr.get("holds") is True and cr.get("holds_raw") is False
          and (cr.get("variant_symdiff_common"), cr.get("v0_symdiff_common")) == (0, 0)
          and (cr.get("variant_symdiff_raw"), cr.get("v0_symdiff_raw")) == (3, 1)
          and cr.get("nodes_only_0839") == [] and cr.get("nodes_only_0851") == ["Method:K.h@a.py:9"]
          and cr.get("nodes_common") == 2 and cr.get("engine_specific_changes") == []
          and cr.get("engine_specific_changes_raw") == 2 and not cr.get("void"), cr)
    check("engdiff E (A1): an edge between two nodes BOTH engines have, on one engine only, still FAILS E (1 > 0) "
          "and is listed as engine-specific",
          cx.get("holds") is False and (cx.get("variant_symdiff_common"), cx.get("v0_symdiff_common")) == (1, 0)
          and cx.get("engine_specific_changes") == [(("Function:g@a.py:5", "Function:f@a.py:1"), None, "EXTRACTED")],
          cx)
    d = base / "engdiff_a1"
    d.mkdir(exist_ok=True)
    files = {"b39": b39, "b51": b51, "t39": t39, "t51": t51, "x39": x39,
             "n51": _eg(N51 + [("Z", "Function", "z", "z.py", 1)], t51["edges"]),       # a node V0 does not have
             "v51": dict(copy.deepcopy(t51), void="engine_void: extraction resolved to ast")}
    for k, obj in files.items():
        (d / f"{k}.json").write_text(json.dumps(obj), encoding="utf-8")
    p = {k: str(d / f"{k}.json") for k in files}
    bases = [f"--base39={p['b39']}", f"--base51={p['b51']}"]
    rc_t, out_t, _e, ex_t = _cli(EG.main, [p["t39"], p["t51"], *bases, f"--json={d / 'out.json'}"])
    try:
        js = json.loads((d / "out.json").read_text(encoding="utf-8")).get("criterion") or {}
    except (OSError, ValueError):
        js = {}
    e_line = next((ln for ln in out_t.splitlines() if ln.startswith("E ")), "")
    check("engdiff CLI (A1): exit 0 when E holds on the common endpoints although the raw comparison is worse; "
          "the E line names both figures (0 <= 0 HOLDS; raw 3 vs 1) and the JSON records both",
          rc_t == 0 and ex_t is None and "both engines" in e_line and "HOLDS" in e_line
          and "symdiff 0 <= V0 symdiff 0" in e_line and "raw" in e_line and "3 vs 1" in e_line
          and js.get("holds") is True and js.get("holds_raw") is False and js.get("variant_symdiff_raw") == 3,
          (rc_t, ex_t, e_line, js))
    rc_x, _o, _e, ex_x = _cli(EG.main, [p["x39"], p["t51"], *bases])
    check("engdiff CLI (A1): exit 1 when E fails on the common endpoints", (rc_x, ex_x) == (1, None), (rc_x, ex_x))
    rc_n, out_n, _e, ex_n = _cli(EG.main, [p["t39"], p["n51"], *bases])
    rc_v, out_v, _e, ex_v = _cli(EG.main, [p["t39"], p["v51"], *bases])
    check("engdiff CLI: VOID (exit 3) when a variant's node set differs from its base's on one engine (every §8.3 "
          "variant is edge-only, so the common-endpoint domain would differ) or a dump carries `void`",
          (rc_n, rc_v, ex_n, ex_v) == (3, 3, None, None) and "VOID" in out_n and "VOID" in out_v,
          (rc_n, rc_v, ex_n, ex_v, out_n[-300:], out_v[-300:]))


# PD-1 (PRE_RC_DECISIONS.md, decided before RC; NOT an amendment of E): the two typed tuples S4's corpora run found
# on graphify 0.8.51 only — true (tsc), both endpoints on both engines, the final CALLS edge identical on both —
# whose evidence chain runs through `ZodTuple.create`, a node only 0.8.51 makes. Exactly these, on exactly that
# engine and grade; changing the list means changing this gate.
PD1_CORPUS = "colinhacks__zod"
PD1_TUPLES = (("Method:ZodFunction.args@packages/zod/src/v3/types.ts:3912",
               "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "INFERRED"),
              ("Method:ZodFunction.create@packages/zod/src/v3/types.ts:3952",
               "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "INFERRED"))
PD1_ENGINE = "0.8.51"


def gate_e_pd1(EG, base: Path) -> None:
    """engdiff.typed_tuple_verdict: §8.4 E bullet 1 (typed tuples identical across the engines on the common
    endpoints) as registered, plus the pre-RC decision PD-1 applied mechanically — exactly its listed tuples, on
    exactly the engine and grade listed, for its corpus; anything else still fails."""
    acc_fn, ver = getattr(EG, "accepted_typed_tuples", None), getattr(EG, "typed_tuple_verdict", None)
    if not callable(acc_fn) or not callable(ver):
        check("engdiff.accepted_typed_tuples / typed_tuple_verdict exist (PD-1 applied mechanically from "
              "PRE_RC_DECISIONS.md)", False)
        return
    try:
        acc = acc_fn()
        want = {(PD1_CORPUS, PD1_ENGINE, *t): "PD-1" for t in PD1_TUPLES}
        two = ver(PD1_CORPUS, [], [list(t) for t in PD1_TUPLES])
        extra_t = ("Method:ZodType.pipe@packages/zod/src/v3/types.ts:522",
                   "Method:ZodPipeline.create@packages/zod/src/v3/types.ts:4827", "EXTRACTED")
        extra = ver(PD1_CORPUS, [], list(PD1_TUPLES) + [extra_t])
        cases = {"the PD-1 tuple on 0.8.39 only": ver(PD1_CORPUS, [PD1_TUPLES[0]], []),
                 "another grade": ver(PD1_CORPUS, [], [PD1_TUPLES[0][:2] + ("AMBIGUOUS",)]),
                 "another corpus": ver("pallets__flask", [], [PD1_TUPLES[0]])}
        none = ver(PD1_CORPUS, [], [])
        bad_path = base / "pd_broken.md"
        bad_path.write_text("# no machine block here\n", encoding="utf-8")
        broken = acc_fn(bad_path)
    except Exception as exc:  # noqa: BLE001
        check("engdiff.typed_tuple_verdict runs", False, repr(exc))
        return
    check("engdiff.accepted_typed_tuples reads PRE_RC_DECISIONS.md: exactly PD-1's two zod tuples (ZodFunction.args / "
          "ZodFunction.create -> ZodTuple.rest, INFERRED, on 0.8.51 only), nothing else; an unreadable record "
          "accepts nothing", acc == want and broken == {}, {"got": acc, "broken": broken})
    check("engdiff.typed_tuple_verdict: PD-1's two tuples on 0.8.51 only FAIL E as registered (holds_as_registered "
          "False) and are accepted by PD-1 (holds True, both listed with the decision id); no difference holds both "
          "ways", two.get("holds_as_registered") is False and two.get("holds") is True
          and len(two.get("accepted") or []) == 2 and not two.get("unaccepted") and two.get("decisions") == ["PD-1"]
          and none.get("holds_as_registered") is True and none.get("holds") is True, (two, none))
    check("engdiff.typed_tuple_verdict: PD-1 is a list, not a rule — a third differing tuple, a listed tuple on the "
          "other engine, with another grade or on another corpus is unaccepted and fails E",
          extra.get("holds") is False and len(extra.get("unaccepted") or []) == 1
          and (extra.get("unaccepted") or [{}])[0].get("caller") == extra_t[0]
          and all(v.get("holds") is False and len(v.get("unaccepted") or []) == 1 for v in cases.values()),
          {"extra": extra, **cases})


def gate_e_pool_cli(CC, s: dict, base: Path) -> None:
    """compare_calls.py pool refuses — never crashes on, never silently flattens — inputs it cannot pool: a --json
    written for several dumps (a LIST), a file that is not a score, scores of different variants or engines (that
    would mix V0 and VT into one pooled recall); a VOID score is not data (exit 3)."""
    d = base / "pool_cli"
    d.mkdir(exist_ok=True)
    vt = dict(copy.deepcopy(s), flags={"DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1"})
    e51 = dict(copy.deepcopy(s), graphify_version="0.8.51")
    files = {"one": s, "multi": [s, vt], "notscore": _dump([]), "vt": vt, "e51": e51,
             "void": dict(copy.deepcopy(s), void="variant_void: inert")}
    for k, obj in files.items():
        (d / f"{k}.json").write_text(json.dumps(obj), encoding="utf-8")
    p = {k: str(d / f"{k}.json") for k in files}
    rc_ok, out_ok, _e, ex_ok = _cli(CC.main, ["pool", p["one"], p["one"], "--family=py"])
    try:
        got = json.loads(out_ok)
    except ValueError:
        got = {}
    check("compare_calls pool CLI: two scores of one variant and engine pool to 4/6 (exit 0)",
          rc_ok == 0 and ex_ok is None and (got.get("receiver_n"), got.get("receiver_hit")) == (6, 4),
          (rc_ok, ex_ok, out_ok[-300:]))
    rc_m, _o, err_m, ex_m = _cli(CC.main, ["pool", p["multi"], "--family=py"])
    check("compare_calls pool CLI: a --json written for several dumps (a list) is refused with a message (exit 2), "
          "never an AttributeError and never flattened", (rc_m, ex_m) == (2, None) and "several dumps" in err_m,
          (rc_m, ex_m, err_m[-300:]))
    rc_n, _o, err_n, ex_n = _cli(CC.main, ["pool", p["one"], p["notscore"]])
    check("compare_calls pool CLI: a file that is not a compare_calls score is refused (exit 2)",
          (rc_n, ex_n) == (2, None) and "not a compare_calls score" in err_n, (rc_n, ex_n, err_n[-300:]))
    rc_v, _o, err_v, ex_v = _cli(CC.main, ["pool", p["one"], p["vt"], "--family=py"])
    rc_e, _o, err_e, ex_e = _cli(CC.main, ["pool", p["one"], p["e51"], "--family=py"])
    check("compare_calls pool CLI: scores of different variants (flags) or engines are refused (exit 2): one pool "
          "is one variant on one engine", (rc_v, rc_e, ex_v, ex_e) == (2, 2, None, None)
          and "one variant" in err_v and "one variant" in err_e, (rc_v, rc_e, ex_v, ex_e, err_v[-200:], err_e[-200:]))
    rc_x, _o, err_x, ex_x = _cli(CC.main, ["pool", p["one"], p["void"]])
    check("compare_calls pool CLI: a score of a VOID dump is not data (exit 3)", (rc_x, ex_x) == (3, None)
          and "VOID" in err_x, (rc_x, ex_x, err_x[-300:]))


def gate_e(mods: dict, base: Path) -> None:
    print("\n-- E: compare_calls / delta / drift / audit / engdiff / impact / score_m / measure_self / run_variant / "
          "variants are exact on synthetic inputs --")
    need = ("_common", "compare_calls", "delta", "indegree_drift", "audit_sample", "engdiff", "impact_offline",
            "impact_spot", "score_m", "measure_self", "run_variant", "variants")
    if any(m not in mods for m in need):
        check("E: every scorer module is importable", False, sorted(set(need) - set(mods)))
        return
    C, CC, D, ID, AS = (mods[m] for m in ("_common", "compare_calls", "delta", "indegree_drift", "audit_sample"))
    # ---- compare_calls -------------------------------------------------------------------------------
    s = CC.score(TRUTH, DUMP)
    want = {"T": 7, "G": 7, "confirmed": 5, "refuted": 1, "unverified": 1, "recall": 0.7143,
            "precision_confirmed": 0.7143, "refuted_share": 0.1429, "ambiguous_share": 0.2857,
            "extracted_refuted_share": 0.0}
    check("compare_calls: |T| 7, |G| 7, confirmed 5 / refuted 1 / unverified 1, recall .7143, refuted .1429, "
          "AMBIGUOUS .2857, EXTRACTED refuted 0", {k: s.get(k) for k in want} == want, {k: s.get(k) for k in want})
    check("compare_calls: per form — bare 3/3, name_attr 1/2, recv 1/1, ctor 0/1 (a decorated caller and a "
          "decorated callee map through their decorator line)",
          s["by_form"] == {"bare": {"n": 3, "hit": 3}, "name_attr": {"n": 2, "hit": 1}, "recv": {"n": 1, "hit": 1},
                           "ctor": {"n": 1, "hit": 0}}, s["by_form"])
    check("compare_calls: truth stats — 9 sites, 1 unresolved, 1 callee unmapped, 0 callers unmapped",
          s["truth_stats"].get("sites") == 9 and s["truth_stats"].get("unresolved_or_external") == 1
          and s["truth_stats"].get("callee_unmapped") == 1 and not s["truth_stats"].get("caller_unmapped"),
          s["truth_stats"])
    check("compare_calls: per grade — AMBIGUOUS 2 (1 confirmed, 1 refuted), EXTRACTED 1 confirmed, INFERRED 4 "
          "(3 confirmed, 1 unverified)",
          s["by_grade"] == {"AMBIGUOUS": {"n": 2, "confirmed": 1, "refuted": 1, "unverified": 0},
                            "EXTRACTED": {"n": 1, "confirmed": 1, "refuted": 0, "unverified": 0},
                            "INFERRED": {"n": 4, "confirmed": 3, "refuted": 0, "unverified": 1}}, s["by_grade"])
    st = CC.score(TRUTH, DUMP, ["tests/"])
    check("compare_calls --src-prefix restricts BOTH sides to the prefix's callers (tests/: |T| 1, |G| 0)",
          (st["T"], st["G"], st["recall"]) == (1, 0, 0.0), (st["T"], st["G"], st["recall"]))
    p = CC.pool([s, s], "py")
    check("compare_calls pool: receiver forms of py = name_attr + recv, pooled 4/6 over two copies",
          (p["receiver_n"], p["receiver_hit"], p["receiver_recall"]) == (6, 4, 0.6667), p)
    gate_e_pool_cli(CC, s, base)
    # ---- delta ---------------------------------------------------------------------------------------
    b = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "b.D.m", "AMBIGUOUS"), ("a.f", "a.C.m", "AMBIGUOUS"),
               ("a.g", "a.f", "INFERRED")])
    v = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "a.C.m", "EXTRACTED"), ("a.g", "a.f", "AMBIGUOUS"),
               ("a.g", "b.D.m", "INFERRED"), ("t.test_x", "b.h", "INFERRED")])
    dd = D.delta(b, v, TRUTH)
    pairs = {k: [(i["caller"], i["callee"]) for i in dd["items"][k]] for k in dd["items"]}
    check("delta: added / removed / demoted / promoted / EXTRACTED buckets exact",
          pairs == {"added": [("a.g", "b.D.m"), ("t.test_x", "b.h")], "removed": [("a.f", "b.D.m")],
                    "demoted": [("a.g", "a.f")], "promoted": [("a.f", "a.C.m")], "EXTRACTED": [("a.f", "a.C.m")]},
          pairs)
    check("delta: caller class (tests/ -> test) and resolver verdict per item",
          [(i["caller_class"], i["verdict"]) for i in dd["items"]["added"]] == [("prod", "confirmed"), ("test", "confirmed")]
          and dd["items"]["removed"][0]["verdict"] == "refuted" and dd["node_set_identical"], dd["counts"])
    v2 = copy.deepcopy(v)
    v2["nodes"].append(_n("z", "Function", "z", "z.py", 1))
    check("delta: a differing node set is reported (not an edge-only pair)",
          D.delta(b, v2)["node_set_identical"] is False and D.delta(b, v2)["n_node_mismatch"] == 1)
    paths = {"a.py": "prod", "tests/t.py": "test", "docs/x.py": "nonprod", "src/examples/y.py": "nonprod",
             "examples.py": "prod", "src/docs_parser.py": "prod", "mydocs/z.py": "prod", "docs_src/q.py": "nonprod",
             "Demo/k.py": "nonprod"}
    got = {pth: C.caller_class(pth) for pth in paths}
    check("caller class: §2.6 component match — examples.py, src/docs_parser.py and mydocs/ are production",
          got == paths, got)
    try:
        if str(BUILD) not in sys.path:
            sys.path.insert(0, str(BUILD))
        import typed_calls as TC
        prod_rule = getattr(TC, "is_nonprod_path", None)
        prod_set = getattr(TC, "NONPROD_DIRS", None)
    except Exception:  # noqa: BLE001
        prod_rule = prod_set = None
    if prod_rule is None:
        skip("caller class == build/typed_calls.py's rule", "typed_calls.is_nonprod_path is not in this tree yet")
    else:
        agree = {pth: (bool(prod_rule(pth)), C.local_is_nonprod_path(pth)) for pth in paths}
        check("the kit's non-production rule agrees with build/typed_calls.py (the kit uses the product's when present)",
              all(x == y for x, y in agree.values()) and (prod_set is None or set(prod_set) == set(C.NONPROD_PARTS)),
              agree)
    # ---- indegree drift + void rule --------------------------------------------------------------------
    nb = [_n("T", "Function", "T", "t.py", 1), _n("U", "Function", "U", "u.py", 1)] + \
         [_n(f"c{i}", "Function", f"c{i}", "c.py", i + 1) for i in range(4)]
    base_d = {"nodes": nb, "edges": [["c0", "U", "INFERRED"], ["c1", "U", "INFERRED"]]}
    var_d = {"nodes": nb, "edges": [["c0", "U", "INFERRED"], ["c0", "T", "INFERRED"], ["c1", "T", "INFERRED"],
                                    ["c2", "T", "INFERRED"]]}
    dr = ID.drift(base_d, var_d)
    cands = [(c["key"], c["ratio"]) for c in dr["candidates"]]
    check("indegree_drift: T 0 -> 3 (x2.3863, a candidate), U 2 -> 1 (x0.8068); max ratio exact",
          cands == [("T", 2.3863)] and dr["n_changed"] == 2 and dr["ratio_max"] == 2.3863 and dr["ratio_min"] == 0.8068,
          (cands, dr["ratio_min"], dr["ratio_max"]))

    def cap(tops):
        return {"questions": [{"id": q, "search": [{"name": n, "file": f, "line": ln} for n, f, ln in t]}
                              for q, t in tops.items()]}
    tn = ("T", "t.py", 1)
    other = ("X", "x.py", 1)
    cb = cap({"q1": [other], "q2": [other], "q3": [other], "q4": [tn]})
    cv3 = cap({"q1": [tn], "q2": [other, tn], "q3": [tn], "q4": [tn]})
    cv2 = cap({"q1": [tn], "q2": [tn], "q3": [other], "q4": [tn]})
    r3, r2 = ID.void_rule(dr["candidates"], cb, cv3), ID.void_rule(dr["candidates"], cb, cv2)
    check("drift void rule: a 2x candidate newly in the fused top-3 of 3 questions VOIDS; of 2 does not "
          "(a question where it already was top-3 does not count)",
          r3["void"] and r3["newly_top3"]["T"] == ["q1", "q2", "q3"] and not r2["void"], (r3, r2))
    # ---- audit_sample ----------------------------------------------------------------------------------
    items = {"added": [{"caller": f"p{i}", "callee": f"q{i}", "verdict": "confirmed"} for i in range(10)],
             "removed": [{"caller": f"r{i}", "callee": f"s{i}", "verdict": "confirmed" if i < 3 else "refuted"}
                         for i in range(10)],
             "demoted": [], "EXTRACTED": [{"caller": f"e{i}", "callee": f"f{i}"} for i in range(3)]}
    d1 = AS.draw(items, 20261022, "flask", 4)
    d1b = AS.draw(copy.deepcopy(items), 20261022, "flask", 4)
    d2 = AS.draw(items, 7, "flask", 4)
    check("audit draw: seeded — the same seed and label give the same sample", d1 == d1b)
    check("audit draw: another seed gives another sample", d1["added"] != d2["added"] or d1["removed"] != d2["removed"])
    check("audit draw: n per bucket (4 of 10; all 3 of a 3-edge bucket; an empty bucket stays empty)",
          (len(d1["added"]), len(d1["EXTRACTED"]), len(d1["demoted"])) == (4, 3, 0))
    check("audit draw: EVERY resolver-confirmed removal is audited (exhaustive), and the sampled removals come "
          "from the rest", [i["caller"] for i in d1["removed_confirmed"]] == ["r0", "r1", "r2"]
          and all(i["verdict"] != "confirmed" for i in d1["removed"]) and len(d1["removed"]) == 4)
    corpus = base / "audit_corpus"
    (corpus / "tests").mkdir(parents=True)
    (corpus / "a.py").write_text("\n\ndef f():\n    g()\n    return C().m()\n\n\n\n\ndef g():\n    return D().m()\n"
                                 + "\n" * 8 + "class C:\n    def m(self):\n        return 1\n", encoding="utf-8")
    (corpus / "b.py").write_text("def h():\n    pass\n\n\nclass D:\n    def m(self):\n        return 2\n", encoding="utf-8")
    picked = {"added": [{"caller": "a.g", "callee": "b.D.m", "base": None, "variant": "INFERRED", "verdict": "confirmed",
                         "caller_class": "prod"}],
              "removed": [{"caller": "a.f", "callee": "b.D.m", "base": "AMBIGUOUS", "variant": None,
                           "verdict": "refuted", "caller_class": "prod"}],
              "demoted": [], "EXTRACTED": [], "removed_confirmed": []}
    rows, key = AS.build_rows(corpus, _dump([]), _dump([]), picked, 20261022, "synthetic")
    sheet = AS.render_sheet("synthetic", rows)
    leaked = [w for w in AS.BLIND_FORBIDDEN if w.lower() in sheet.lower()]
    check("audit sheet is blind: it names no bucket, grade, verdict, flag or variant", not leaked, leaked)
    check("audit sheet shows the caller's call sites with line numbers (g -> D().m() at a.py:11)",
          any("11" in str(ln) and "m()" in txt for r in rows for ln, txt in r["sites"]), rows)
    check("audit key keeps what the sheet hides (buckets, grades, verdict) per row id",
          {tuple(k["buckets"]) for k in key.values()} == {("added",), ("removed",)}
          and all(len(rid) == 12 for rid in key))
    keyd = {"label": "x", "rows": {
        "r1": {"buckets": ["added"], "verdict": "confirmed"}, "r2": {"buckets": ["added"], "verdict": "refuted"},
        "r3": {"buckets": ["added"], "verdict": None}, "r4": {"buckets": ["added", "EXTRACTED"], "verdict": "confirmed"},
        "r5": {"buckets": ["removed"], "verdict": "refuted"}, "r6": {"buckets": ["removed"], "verdict": "refuted"},
        "r7": {"buckets": ["removed_confirmed"], "verdict": "confirmed"}}}
    s1 = {"r1": "TRUE", "r2": "TRUE", "r3": "TRUE", "r4": "TRUE", "r5": "FALSE", "r6": "FALSE", "r7": "FALSE"}
    s2 = {"r1": "TRUE", "r2": "TRUE", "r3": "UNSURE", "r4": "TRUE", "r5": "FALSE", "r6": "FALSE", "r7": "FALSE"}
    sc = AS.score(keyd, [s1, s2])
    bk = sc["buckets"]
    check("audit score: added precision 3/4 (an UNSURE is not TRUE), Wilson 95% .3006..9544",
          (bk["added"]["true"], bk["added"]["n"], bk["added"]["share"], bk["added"]["wilson_lb"],
           bk["added"]["wilson_ub"]) == (3, 4, 0.75, 0.3006, 0.9544), bk["added"])
    check("audit score: removed = correct-removal share 2/2 (Wilson LB .3424); a confirmed removal audited FALSE "
          "is no loss", (bk["removed"]["false"], bk["removed"]["wilson_lb"], bk["removed_confirmed"]["true"])
          == (2, 0.3424, 0), (bk["removed"], bk["removed_confirmed"]))
    check("audit score: the resolver was wrong on 2 of 6 judged rows (r2 refuted but TRUE; r7 confirmed but FALSE) "
          "-> error rate .3333 voids the G deltas",
          sc["resolver"] == {"judged": 6, "wrong": 2, "error_rate": 0.3333, "voids_g_deltas": True}, sc["resolver"])
    # ---- engdiff -------------------------------------------------------------------------------------------
    EG = mods["engdiff"]
    a39 = {"nodes": [_n("k1", "Function", "f", "a.py", 1), _n("k2", "Function", "g", "a.py", 5)],
           "edges": [["k1", "k2", "INFERRED"]]}
    a51 = {"nodes": [_n("Q1", "Function", "f", "a.py", 1), _n("Q2", "Function", "g", "a.py", 5)],
           "edges": [["Q1", "Q2", "INFERRED"], ["Q2", "Q1", "AMBIGUOUS"]]}
    pr = EG.parity(a39, a51)
    check("engdiff: engine keys differ, version-stable identities match: symmetric difference 1", pr["symdiff"] == 1
          and pr["only_b"] == [("Function:g@a.py:5", "Function:f@a.py:1")], pr)
    b51 = copy.deepcopy(a51)
    b51["edges"] = [["Q1", "Q2", "INFERRED"]]                  # V0 agrees across the engines (symdiff 0)
    v39 = copy.deepcopy(a39)
    v39["edges"].append(["k2", "k1", "INFERRED"])              # the variant adds g -> f on 0.8.39 only
    cr = EG.criterion(v39, copy.deepcopy(b51), a39, b51)
    check("engdiff E criterion: a variant whose engine parity is worse than V0's FAILS (1 > 0); its one-engine-only "
          "change is listed", cr.get("holds") is False
          and (cr.get("variant_symdiff_common"), cr.get("v0_symdiff_common")) == (1, 0)
          and len(cr.get("engine_specific_changes") or ()) == 1, cr)
    same = EG.criterion(a39, b51, a39, b51)
    check("engdiff E criterion: V0 against itself holds, with no engine-specific change", same.get("holds")
          and not same.get("engine_specific_changes"), same)
    gate_e_engdiff_common(EG, base)
    gate_e_pd1(EG, base)
    # ---- impact_offline / impact_spot ---------------------------------------------------------------------------
    IO, IS = mods["impact_offline"], mods["impact_spot"]
    chain = {"nodes": [_n(x, "Function", x, "c.py", i) for i, x in enumerate("ABCD", 1)] +
                      [_n("E", "Method", "K.D", "k.py", 1)],
             "edges": [["A", "B", "INFERRED"], ["B", "C", "INFERRED"], ["C", "D", "AMBIGUOUS"], ["Z", "A", "INFERRED"]]}
    seeds, rows3 = IO.reach(chain, "D", 3)
    seeds2, rows2 = IO.reach(chain, "D", 2)
    check("impact_offline.reach: seeds by name or '.name' suffix; 3 hops reach C, B, A; 2 hops stop at B",
          seeds == ["D", "E"] and [(a, h) for a, h, _c in rows3] == [("C", 1), ("B", 2), ("A", 3)]
          and [a for a, _h, _c in rows2] == ["C", "B"], (seeds, rows3, rows2))
    spot_ref = {"nodes": NODES, "edges": [["a.f", "a.C.m", "TRUTH"], ["a.g", "a.C.m", "TRUTH"]]}
    spot_got = {"nodes": NODES, "edges": [["a.f", "a.C.m", "AMBIGUOUS"], ["b.h", "a.C.m", "INFERRED"],
                                          ["a.f", "b.D.m", "AMBIGUOUS"]]}
    sp = IS.spot(spot_ref, spot_got, "m@a.py")
    check("impact_spot: 'Name@file' targets only that file's node; hop-1 P 1/2, R 1/2",
          (sp["targets"], sp["ref"], sp["got"], sp["hit"], sp["precision"], sp["recall"]) == (1, 2, 2, 1, 0.5, 0.5), sp)
    check("impact_spot registers 5 Python and 5 TS/JS spot symbols",
          len(IS.PY_SPOTS) == 5 and len(IS.TS_SPOTS) == 5
          and sorted(s.split("@")[0] for k, v in IS.SPOTS.items() for s in v) == sorted(IS.PY_SPOTS + IS.TS_SPOTS))
    check("impact_spot: the saturated design-given spot `_mk_project` is replaced (PREREG amendment A2) by "
          "`read_records@retrieval_log.py` on the pinned STAGE-0 corpus; the moving `self` corpus holds no spot",
          "_mk_project" not in IS.PY_SPOTS and "read_records" in IS.PY_SPOTS
          and IS.SPOTS.get("stage0_build") == ["read_records@retrieval_log.py"] and "self" not in IS.SPOTS
          and not any("_mk_project" in s for v in IS.SPOTS.values() for s in v),
          {k: v for k, v in IS.SPOTS.items() if k in ("self", "stage0_build")})
    # ---- score_m R1 / R3 ---------------------------------------------------------------------------------------
    SM = mods["score_m"]

    def scq(qid, fam, atoms):
        return {"id": qid, "family": fam, "union_strict": sum(a["union_strict"] for a in atoms) / len(atoms),
                "impact_strict_hits": sum(a["impact_strict"] for a in atoms), "atoms": atoms}

    def at(name, us, sp_, imp):
        return {"atom": name, "union_strict": us, "union_spec": sp_, "impact_strict": imp}
    base_s = [scq("q1", "multi_hop", [at("x:1 - a", True, True, True), at("x:2 - b", False, True, False)]),
              scq("q2", "transitive", [at("y:1 - c", True, True, False)])]
    good = copy.deepcopy(base_s)
    good[0]["atoms"][1]["union_strict"] = True
    good[0]["union_strict"] = 1.0
    bad = copy.deepcopy(base_s)
    bad[1]["atoms"][0]["union_spec"] = False
    r_good, r_bad = SM.r1_compare(base_s, good), SM.r1_compare(base_s, bad)
    check("score_m R1: a gained atom holds; a lost SPEC atom fails (threshold 0) and is named",
          r_good["r1_holds"] and r_good["r3_holds"] and not r_bad["r1_holds"]
          and r_bad["atom_losses"] == [{"question": "q2", "atom": "y:1 - c", "measure": "union_spec"}], (r_good, r_bad))
    lost_imp = copy.deepcopy(base_s)
    lost_imp[0]["impact_strict_hits"] = 0
    check("score_m R3: fewer multi_hop impact STRICT atoms than V0 fails", not SM.r1_compare(base_s, lost_imp)["r3_holds"])
    # ---- measure_self R2 -----------------------------------------------------------------------------------------
    MS = mods["measure_self"]
    gt = {"queries": [{"id": "q1", "set": "A", "type": "concept", "expected": [["build/x.py", "f"]]},
                      {"id": "q2", "set": "B", "type": "code", "expected": [["hooks/y.py", "g"]]}]}

    def raw(r1, r2):
        def rows(r, f, nm):
            return [{"file": "z.py", "name": "nope"}] * (r - 1) + [{"file": f, "name": nm}] if r else []
        return {"queries": [{"id": "q1", "rows": rows(r1, "build/x.py", "Mod.f")},
                            {"id": "q2", "rows": rows(r2, "hooks/y.py", "g")}]}
    sb = MS.score(gt, raw(1, 4))
    check("measure_self: MRR (1 + 1/4) / 2 = .625, hit@1 .5, hit@5 1.0; a dotted name ending in .f is a hit",
          sb["metrics"]["ALL/ALL"] == {"n": 2, "hit@1": 0.5, "hit@5": 1.0, "hit@15": 1.0, "MRR": 0.625},
          sb["metrics"]["ALL/ALL"])
    check("measure_self R2: equal MRR holds; a lower MRR fails; D1-py's +0.017 gain is enforced when asked",
          MS.compare(sb, sb)["r2_holds"] and not MS.compare(sb, MS.score(gt, raw(2, 4)))["r2_holds"]
          and not MS.compare(sb, sb, min_gain=0.017)["r2_holds"])
    two_lost = MS.score(gt, raw(0, 0))
    check("measure_self R2: losing hit@15 on 2 queries fails (at most 1 allowed)",
          MS.compare(sb, two_lost)["hit15_net_drop"] == 2 and not MS.compare(sb, two_lost)["hit15_ok"])
    # ---- run_variant ---------------------------------------------------------------------------------------------
    RV = mods["run_variant"]
    q = {"id": "q", "search": [{"name": "f", "file": "a.py", "line": 1, "score": 0.5}],
         "impact": {"f": {"rows": [{"caller": "g", "hop": 1}]}}}
    q2 = copy.deepcopy(q)
    q2["search"][0]["score"] = 0.9
    q3 = copy.deepcopy(q)
    q3["impact"]["f"]["rows"][0]["hop"] = 2
    check("run_variant round trip: identical rows (a score change ignored) are EXACT; an impact row change is VOID",
          RV.roundtrip({"questions": [q]}, {"questions": [q2]})["exact"]
          and not RV.roundtrip({"questions": [q]}, {"questions": [q3]})["exact"])
    ok_log = "[deepmap sync] 120 nodes, 300 CALLS edges, 0 embedded this run (120 embedded total) from x"
    check("run_variant matched state: 0 re-embedded and no FTS re-create is matched; either one VOIDS",
          not RV.check_sync_log(ok_log)["void"]
          and RV.check_sync_log(ok_log.replace(" 0 embedded", " 3 embedded"))["void"]
          and RV.check_sync_log(ok_log + "\n[deepmap sync] full-text index re-created (x) in 1s")["void"]
          and RV.check_sync_log("no sync line")["void"])
    cen = {"nodes": 5, "embedded": 5, "summary_fp": "s", "node_keys_fp": "k"}
    check("run_variant matched state: a different node-text fingerprint is VOID",
          RV.matched(cen, dict(cen))["matched"] and not RV.matched(cen, dict(cen, summary_fp="t"))["matched"])
    home_ok = base / "h"
    route = SimpleNamespace(source="registry", bolt=7701, problem=None)
    envok = {"DEEPMAP_HOME": str(home_ok)}
    probs = {"ok": RV.safety_problems(base, route, environ=envok),
             "uri": RV.safety_problems(base, route, environ=dict(envok, NEO4J_URI="bolt://x")),
             "base": RV.safety_problems(base, SimpleNamespace(source="registry", bolt=7687, problem=None), environ=envok),
             "legacy": RV.safety_problems(base, SimpleNamespace(source="legacy-unverified", bolt=7687, problem="p"),
                                          environ=envok),
             "nohome": RV.safety_problems(base, route, environ={}),
             "operator": RV.safety_problems(base, route, environ={"DEEPMAP_HOME": str(Path.home() / ".deepmap")})}
    check("run_variant safety: a throwaway home + own instance passes; NEO4J_URI, bolt 7687, a legacy route, no "
          "home and the operator's ~/.deepmap are each refused",
          probs["ok"] == [] and all(probs[k] for k in ("uri", "base", "legacy", "nohome", "operator")), probs)

    class _Ret:
        def search(self, q, top_k):
            return [{"name": "f", "file": "a.py", "line": 1, "embedding": [1]}, {"name": "f", "file": "a.py", "line": 1},
                    {"name": "g"}, {"name": "h"}]

        def impact(self, name, limit):
            return [{"caller": name + "_caller", "limit": limit}]
    pas = RV.run_pass(_Ret(), [{"id": "t", "family": "transitive", "prompt": "p"},
                               {"id": "c", "family": "concept_to_impl", "prompt": "p"}])
    check("run_variant.run_pass: impact(limit=30) on the distinct names of the top-3 for graph families only; "
          "embeddings dropped", sorted(pas[0]["impact"]) == ["f", "g"] and pas[1]["impact"] == {}
          and "embedding" not in pas[0]["search"][0] and pas[0]["impact"]["f"]["rows"][0]["limit"] == 30, pas)
    # ---- variants ------------------------------------------------------------------------------------------------
    V = mods["variants"]
    env = V.child_env("VT", base={"PATH": "p", "NEO4J_URI": "bolt://x", "DEEPMAP_HOME": "h", "DEEPMAP_PYTHON": "py",
                                  "DEEPMAP_GRAPHIFY_TIMEOUT": "3600", "DEEPMAP_PRECISE_CALLS": "1",
                                  "DEEPMAP_EXCLUDE_TESTS": "1"})
    check("variants.child_env: every stray DEEPMAP_* and NEO4J_URI dropped, the run settings kept, the variant's "
          "flags set, PYTHONHASHSEED=0",
          env == {"PATH": "p", "DEEPMAP_HOME": "h", "DEEPMAP_PYTHON": "py", "DEEPMAP_GRAPHIFY_TIMEOUT": "3600",
                  "DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1",
                  "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"}, env)
    errs = []
    for bad_vrc in ((("VRC", None)), ("VT", ["DEEPMAP_HOME=x"]), ("VT", ["NEO4J_URI=x"]), ("V0", ["NOEQUALS"])):
        try:
            V.flags_of(bad_vrc[0], bad_vrc[1])
            errs.append(bad_vrc)
        except (ValueError, KeyError):
            pass
    check("variants: VRC needs --set; --set refuses run settings, non-DEEPMAP keys and malformed items", not errs, errs)
    slugs = [V.slug(k) for k in V.VARIANTS]
    check("variants: every slug is unique and file-name safe", len(set(slugs)) == len(slugs)
          and all(re.fullmatch(r"[A-Za-z0-9_\-]+", s) for s in slugs), slugs)
    # ---- the engine VOID rule + the CLI option forms ------------------------------------------------------------
    OC = mods.get("offline_calls")
    if OC is not None:
        ev = OC.engine_void
        cases = {"fallback to ast": ev({"engine": "ast", "extraction": {"fallback_reason": "0 nodes"}}, "graphify"),
                 "graphify after a timeout": ev({"engine": "graphify", "extraction": {"timed_out": True}}, "graphify"),
                 "clean graphify": ev({"engine": "graphify", "extraction": {"resolved": "graphify"}}, "graphify"),
                 "no expectation": ev({"engine": "ast", "extraction": {}}, None)}
        check("offline_calls.engine_void: a fallback or a timeout is VOID for an engine-pinned run; clean graphify and "
              "an unpinned run are not", bool(cases["fallback to ast"]) and bool(cases["graphify after a timeout"])
              and cases["clean graphify"] is None and cases["no expectation"] is None, cases)
    o1, p1 = C.parse_cli(["score", "--seed", "20261022", "--n=40", "--sheet", "a.csv", "--sheet=b.csv", "x.json",
                          "--flag"], ("--seed", "--n"), ("--sheet",))
    check("the kit's CLIs take both `--opt value` (the design's spelling) and `--opt=value`; repeatable options collect",
          o1 == {"--seed": "20261022", "--n": "40", "--sheet": ["a.csv", "b.csv"], "--flag": ""}
          and p1 == ["score", "x.json"], (o1, p1))
    gate_e_review(mods, base)
    gate_e_effective(mods)


# The switches offline_calls.effective_switches must report, read through the product: the cross-family filter
# (sync._calls_family_filter_enabled) and the typed-calls configuration (typed_calls._config) beside the language
# lists, so every §8.3 variant's block differs from V0's AND from every other variant's (VF vs V0; VT+t / VT+np /
# VT[shape] vs VT). Without them VF's delta against V0 is VOID as an inert pair although the filter worked.
_EFF_NEW = ("calls_family_filter", "typed_shapes", "typed_replace", "typed_tests", "typed_nonprod", "typed_max_files")


def _variant_effective(OC, S, V, vid: str) -> dict:
    """offline_calls.effective_switches(S) under the variant's clean flags (every other DEEPMAP_* graph flag unset)."""
    with _env(**dict(_graph_flags_cleared(), **V.flags_of(vid))):
        return OC.effective_switches(S)


def gate_e_effective(mods: dict) -> None:
    """The `effective` block covers every switch a §8.3 variant sets (review: VF was always VOID): each variant's
    block, under its own clean flags and this tree's product predicates, differs from V0's and from every other
    variant's; the block is read through the product (a predicate that ignores a flag reports it off); variant_void
    VOIDs a dump whose requested switch the product left off; delta's inert-pair rule no longer voids VF."""
    OC, V, D = mods.get("offline_calls"), mods.get("variants"), mods.get("delta")
    if OC is None or V is None or D is None or not callable(getattr(OC, "effective_switches", None)):
        check("offline_calls.effective_switches / variants / delta are importable for the effective-block checks",
              False)
        return
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sync as _SY  # noqa: E402 — the product's own predicates
    vids = [v for v, fl in V.VARIANTS.items() if fl is not None]
    try:
        eff = {v: _variant_effective(OC, _SY, V, v) for v in vids}
    except Exception as exc:  # noqa: BLE001 — a crash is a RED result
        check("offline_calls.effective_switches runs under every §8.3 variant's flags", False, repr(exc))
        return
    missing = sorted(k for k in _EFF_NEW if k not in eff["V0"])
    check("offline_calls.effective_switches reports the cross-family filter and the typed-calls configuration "
          f"({', '.join(_EFF_NEW)}), each read through the product", not missing, missing)
    same_as_v0 = sorted(v for v in vids if v != "V0" and eff[v] == eff["V0"])
    check("every §8.3 variant's effective block (its clean flags, this tree's product predicates) differs from "
          "V0's — VF included (calls_family_filter on): no variant is an inert pair against V0", not same_as_v0,
          {v: eff[v] for v in same_as_v0[:3]})
    seen, clashes = {}, []
    for v in vids:
        sig = json.dumps(eff[v], sort_keys=True, default=str)
        if sig in seen:
            clashes.append((seen[sig], v))
        seen.setdefault(sig, v)
    check("every two §8.3 variants with different flags have different effective blocks (an ablation compared "
          "against VT — VT+t, VT+np, VT[shape] — is never VOID as an inert pair)", not clashes, clashes)
    check("VF reports calls_family_filter True and V0 False; VT+t / VT+np report typed_tests / typed_nonprod True "
          "where VT reports False; VT[R1] reports typed_shapes ['R1'] where VT reports every shape",
          eff["VF"].get("calls_family_filter") is True and eff["V0"].get("calls_family_filter") is False
          and eff["VT+t"].get("typed_tests") is True and eff["VT"].get("typed_tests") is False
          and eff["VT+np"].get("typed_nonprod") is True and eff["VT"].get("typed_nonprod") is False
          and eff["VT[R1]"].get("typed_shapes") == ["R1"] and eff["VT"].get("typed_shapes") == sorted(V.SHAPES),
          {v: {k: eff[v].get(k) for k in _EFF_NEW} for v in ("V0", "VF", "VT", "VT+t", "VT+np", "VT[R1]")})
    # Read through the product: a sync whose filter predicate ignores the flag, a typed config that ignores TESTS.
    deaf = SimpleNamespace(graphify_extract=getattr(_SY, "graphify_extract", None),
                           _typed_calls_link=getattr(_SY, "_typed_calls_link", None),
                           _calls_family_filter_enabled=lambda: False)
    via = {}
    try:
        via["VF, a filter predicate that ignores the flag"] = _variant_effective(OC, deaf, V, "VF").get(
            "calls_family_filter")
        import typed_calls as _TC  # noqa: E402
        import dataclasses
        orig = _TC._config
        _TC._config = lambda env, registry: dataclasses.replace(orig(env, registry), tests=False)
        try:
            via["VT+t, a typed config that ignores TESTS"] = _variant_effective(OC, _SY, V, "VT+t").get("typed_tests")
        finally:
            _TC._config = orig
    except Exception as exc:  # noqa: BLE001
        via["error"] = repr(exc)
    check("effective_switches reads the switches through the product, never the flag text: a filter predicate that "
          "ignores DEEPMAP_CALLS_FAMILY_FILTER reports it off under VF, a typed config that ignores "
          "DEEPMAP_TYPED_CALLS_TESTS reports it off under VT+t", via == {
              "VF, a filter predicate that ignores the flag": False,
              "VT+t, a typed config that ignores TESTS": False}, via)
    # variant_void: a requested switch the product left off is VOID (the real blocks are not).
    vv = OC.variant_void
    cl = {"error": None, "emitted": 1}

    def dump_of(vid, **override):
        return {"flags": V.flags_of(vid), "effective": dict(eff[vid], **override), "typed_step": "present",
                "call_linking": cl}
    cases = {"VF, the filter left off": vv(dump_of("VF", calls_family_filter=False)),
             "VT+t, tests left off": vv(dump_of("VT+t", typed_tests=False)),
             "VT+np, non-production left off": vv(dump_of("VT+np", typed_nonprod=False)),
             "VT[R1], the shape not turned on": vv(dump_of("VT[R1]", typed_shapes=[])),
             "V9, CALLS_PLUS left off": vv(dump_of("V9", calls_plus=False)),
             "REPLACE=0, the product still replacing": vv(
                 {"flags": dict(V.flags_of("VT"), DEEPMAP_TYPED_CALLS_REPLACE="0"),
                  "effective": dict(eff["VT"], typed_replace=True), "typed_step": "present", "call_linking": cl}),
             "PRECISE=1, precise calls left off": vv({"flags": {"DEEPMAP_PRECISE_CALLS": "1"},
                                                      "effective": dict(eff["V0"]), "typed_step": "present"})}
    real = {v: vv(dump_of(v)) for v in vids}
    check("offline_calls.variant_void: a dump whose requested switch the product left off is VOID and names the flag "
          "(the cross-family filter, TESTS, NONPROD, a SHAPES arm, CALLS_PLUS, REPLACE=0, PRECISE=1); every §8.3 "
          "variant's real block on this tree is not",
          all(cases.values()) and not any(real.values())
          and "DEEPMAP_CALLS_FAMILY_FILTER" in str(cases["VF, the filter left off"])
          and "DEEPMAP_TYPED_CALLS_SHAPES" in str(cases["VT[R1], the shape not turned on"]),
          ({k: v for k, v in cases.items() if not v}, {k: v for k, v in real.items() if v}))
    # delta: the review's probe — VF against V0 with the real blocks is a measurement, not an inert pair.
    pair = [("a.f", "a.g", "AMBIGUOUS"), ("a.f", "b.h", "AMBIGUOUS")]
    d_vf = D.delta(dict(_dump(pair), flags={}, effective=eff["V0"]),
                   dict(_dump(pair[:1]), flags=V.flags_of("VF"), effective=eff["VF"]))
    d_ab = D.delta(dict(_dump(pair), flags=V.flags_of("VT"), effective=eff["VT"]),
                   dict(_dump(pair), flags=V.flags_of("VT+t"), effective=eff["VT+t"]))
    check("delta: VF against V0 (the real effective blocks) is not VOID — it measures the filter; an ablation "
          "against VT (VT+t) is not VOID either", d_vf["void"] == [] and d_ab["void"] == [],
          (d_vf["void"], d_ab["void"]))


# The V0 / VT dump pair of the S0 review (requests on 0.8.39, a tree with build/typed_calls.py but no
# sync._typed_calls_link): identical edges, VT's `effective` differs from V0's only in the language lists.
_EFF_V0 = {"audit_languages": [], "calls_plus": False, "keyed_calls": False, "precise_calls": False,
           "precise_families": [], "precise_fixes": True, "typed_languages": [], "typed_step": "absent"}
_EFF_VT = dict(_EFF_V0, audit_languages=["javascript", "python", "typescript"],
               typed_languages=["javascript", "python", "typescript"])
_FLAGS_VT = {"DEEPMAP_CALLS_AUDIT": "1", "DEEPMAP_TYPED_CALLS": "python,typescript,javascript"}


class _MetaDriver:
    """A recorder standing in for Neo4j under run_variant.census: a graph whose :_DeepMapMeta node carries ONLY
    the property sync writes (`owning_root`), plus one CALLS edge. A query for any other meta property reads null,
    exactly as Neo4j would."""

    def __init__(self, owning_root):
        self.meta = {"id": "singleton", "owning_root": owning_root}
        self.queries = []

    def session(self, *a, **k):
        drv = self

        class _S(_Sess):
            def run(self, q, *a, **k):
                drv.queries.append(q)
                m = re.search(r"_DeepMapMeta\)\s+RETURN\s+m\.(\w+)\s+AS\s+(\w+)", q)
                if m:
                    return _Res([{m.group(2): drv.meta.get(m.group(1))}])
                if "labels(n)[0]" in q:
                    return _Res([{"l": "Function", "c": 2}])
                if "n.embedding IS NOT NULL" in q:
                    return _Res([{"c": 2}])
                if "r.confidence AS k" in q:
                    return _Res([{"k": "INFERRED", "c": 1}])
                if "a.key AS a" in q:
                    return _Res([{"a": "f", "b": "g", "c": "INFERRED"}])
                if "n.key AS k" in q:
                    return _Res([{"k": "f", "s": "", "nm": "f"}, {"k": "g", "s": "", "nm": "g"}])
                return _Res([])
        return _S()


def gate_e_review(mods: dict, base: Path) -> None:
    """The S0 review round: the owner check of the live captures, the inert-variant VOID rule, the cross-file
    EXTRACTED invariant, compare_calls' prefix side, delta's EXTRACTED bucket, the audit's losses and stratified
    removal share, saturated spot symbols and the TS/JS atom spans."""
    C, CC, D, AS, IS, SM, RV = (mods[m] for m in ("_common", "compare_calls", "delta", "audit_sample", "impact_spot",
                                                  "score_m", "run_variant"))
    OC = mods.get("offline_calls")
    # ---- run_variant.census / owner_problem: the owner stamp sync writes -----------------------------------------
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sync as _SY  # noqa: E402 — the product's identity, as run_variant's check computes it
    clone = base / "owner_clone"
    clone.mkdir(exist_ok=True)
    stamp = str(_SY._project_identity(clone))
    drv = _MetaDriver(stamp)
    try:
        cen = RV.census(drv)
    except Exception as exc:  # noqa: BLE001
        cen = {"error": repr(exc)}
    check("run_variant.census reads the owner stamp sync writes (:_DeepMapMeta owning_root) — a graph stamped by "
          "sync for the clone reports that stamp, not null",
          cen.get("owner") == stamp and any("owning_root" in q for q in drv.queries),
          {"owner": cen.get("owner"), "queries": [q for q in drv.queries if "_DeepMapMeta" in q]})
    op = getattr(RV, "owner_problem", None)
    if not callable(op):
        check("run_variant.owner_problem exists (the one owner check both live captures use)", False)
    else:
        other = base / "owner_other"
        other.mkdir(exist_ok=True)
        verdicts = {"own stamp": op(clone, stamp), "another project's stamp": op(clone, str(_SY._project_identity(other))),
                    "no stamp": op(clone, None)}
        check("run_variant.owner_problem: a capture whose graph is stamped for the clone passes; another project's "
              "stamp and a missing stamp are refused", verdicts["own stamp"] is None
              and verdicts["another project's stamp"] and verdicts["no stamp"], verdicts)
    uses = {}
    for mod in ("run_variant", "measure_self"):
        tree = ast.parse(Path(mods[mod].__file__).read_text(encoding="utf-8"))
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_capture"), None)
        names = {_call_name(c) for c in _calls_in(fn)} if fn is not None else set()
        uses[mod] = bool(names & {"owner_problem", "RV.owner_problem"}) and "_same_project" not in \
            {n.split(".")[-1] for n in names}
    check("run_variant and measure_self capture through owner_problem (no private copy of the owner rule)",
          all(uses.values()), uses)
    # ---- offline_calls: the inert-variant VOID rule, the cross-file EXTRACTED invariant ---------------------------
    if OC is not None and callable(getattr(OC, "variant_void", None)):
        vv = OC.variant_void
        v0 = {"flags": {}, "effective": dict(_EFF_V0), "typed_step": "absent", "call_linking": None}
        vt = {"flags": dict(_FLAGS_VT), "effective": dict(_EFF_VT), "typed_step": "absent", "call_linking": None}
        vt_err = dict(vt, typed_step="present", effective=dict(_EFF_VT, typed_step="present"),
                      call_linking={"error": "RuntimeError: boom", "added": 0})
        vt_ok = dict(vt_err, call_linking={"error": None, "added": 3})
        vt_lang = dict(vt_ok, effective=dict(_EFF_VT, typed_step="present", typed_languages=["python"]))
        vp_csv = {"flags": {"DEEPMAP_PRECISE_CALLS": "python,js"}, "effective": dict(_EFF_V0), "typed_step": "absent"}
        vp_ok = dict(vp_csv, effective=dict(_EFF_V0, precise_families=["js", "python"], precise_calls=True))
        got = {"V0": vv(v0), "VT, no typed step (the review's pair)": vv(vt),
               "VT, the step erred": vv(vt_err), "VT, the step ran": vv(vt_ok),
               "VT, a requested language not turned on": vv(vt_lang),
               "PRECISE=python,js, no family turned on": vv(vp_csv), "PRECISE=python,js, families on": vv(vp_ok),
               "a flag no build/*.py names": vv({"flags": {"DEEPMAP_ZZ": "1"}}, "x = 'DEEPMAP_ZZZ'"),
               "a flag the code names": vv({"flags": {"DEEPMAP_ZZ": "1"}}, "x = 'DEEPMAP_ZZ'")}
        want_void = {"VT, no typed step (the review's pair)", "VT, the step erred",
                     "VT, a requested language not turned on", "PRECISE=python,js, no family turned on",
                     "a flag no build/*.py names"}
        absent_with_stats = vv(dict(vt, call_linking={"error": None, "added": 0}))
        check("offline_calls.variant_void: the review's V0/VT pair — VT on a sync without _typed_calls_link — is "
              "VOID (and says the step is missing, even beside stats); so are an erring typed step, a requested "
              "language left off, a PRECISE family list the code ignores and a flag no build/*.py file names; V0, a "
              "typed step that ran and PRECISE families on are not",
              {k for k, v in got.items() if v} == want_void
              and "_typed_calls_link" in str(got["VT, no typed step (the review's pair)"])
              and "_typed_calls_link" in str(absent_with_stats), (got, absent_with_stats))
        dv = D.delta(dict(_dump([("a.f", "a.g", "INFERRED")]), **v0), dict(_dump([("a.f", "a.g", "INFERRED")]), **vt))
        same_eff = D.delta(dict(_dump([]), **v0), dict(_dump([]), flags=dict(_FLAGS_VT), effective=dict(_EFF_V0)))
        flagged = D.delta(dict(_dump([]), **v0), dict(_dump([]), **dict(vt, void="the variant was inert")))
        check("delta: a pair whose variant flags differ but whose effective switches equal V0's is VOID, and so is a "
              "pair holding a VOID dump; the review's V0/VT pair (effective lists differ) is left to variant_void",
              bool(same_eff["void"]) and bool(flagged["void"]) and dv["void"] == [],
              (same_eff["void"], flagged["void"], dv["void"]))
    else:
        check("offline_calls.variant_void exists (the inert-variant VOID rule in code)", False)
    if OC is not None and callable(getattr(OC, "extracted_cross_file", None)) and callable(getattr(OC, "exit_code", None)):
        xn = [_n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10),
              _n("b.h", "Function", "h", "b.py", 1)]
        xe = {("a.f", "a.g"): "EXTRACTED", ("a.f", "b.h"): "EXTRACTED", ("a.g", "b.h"): "INFERRED"}
        md = OC.make_dump(base, xn, [("a.f", "a.g")], xe, {"info": {}}, code=None)
        clean = OC.make_dump(base, xn, [], {("a.f", "a.g"): "EXTRACTED"}, {"info": {}}, code=None)
        check("offline_calls: an EXTRACTED edge across files is counted (counts.extracted_cross_file) and named "
              "(invariant) — build exits 4 for it, 3 for a VOID dump, 0 for a clean one",
              md["counts"]["extracted_cross_file"] == 1 and md["invariant"] == {"extracted_cross_file": [["a.f", "b.h"]]}
              and clean["counts"]["extracted_cross_file"] == 0 and clean["invariant"] is None
              and (OC.exit_code(md), OC.exit_code(clean), OC.exit_code(dict(md, void="x"))) == (4, 0, 3),
              (md["counts"], md["invariant"]))
    else:
        check("offline_calls.extracted_cross_file / exit_code exist (the cross-file EXTRACTED invariant)", False)
    # ---- compare_calls: --src-prefix filters by the CALLER's file (both directions) -------------------------------
    pn = [_n("lib.f", "Function", "f", "lib/a.py", 1), _n("lib.g", "Function", "g", "lib/a.py", 10),
          _n("ext.bh", "Function", "h", "ext/b.py", 1), _n("ext.ch", "Function", "h", "ext/c.py", 1)]
    pt = {"schema": "call-linking/truth/1", "rows": [
        _row("lib/a.py", [["f", 1, []]], "bare", "h", [_d("ext/b.py", 1, "function", "h")]),
        _row("lib/a.py", [["f", 1, []]], "bare", "g", [_d("lib/a.py", 10, "function", "g")]),
        _row("ext/b.py", [["h", 1, []]], "bare", "g", [_d("lib/a.py", 10, "function", "g")])]}
    pd = {"nodes": pn, "edges": [["lib.f", "ext.bh", "INFERRED"], ["lib.f", "ext.ch", "AMBIGUOUS"],
                                 ["lib.f", "lib.g", "INFERRED"], ["ext.ch", "lib.g", "INFERRED"]]}
    keys = ("T", "G", "confirmed", "refuted", "unverified", "recall", "refuted_share", "ambiguous_share")
    s_lib, s_ext = CC.score(pt, pd, ["lib/"]), CC.score(pt, pd, ["ext/"])
    check("compare_calls --src-prefix keeps a pair by its CALLER's file: lib/ (calls INTO ext/ kept, ext/'s call "
          "into lib/ dropped) |T| 2, |G| 3, confirmed 2, refuted 1, refuted share .3333; ext/ |T| 1, |G| 1, 0 "
          "confirmed, 1 unverified",
          tuple(s_lib[k] for k in keys) == (2, 3, 2, 1, 0, 1.0, 0.3333, 0.3333)
          and tuple(s_ext[k] for k in keys) == (1, 1, 0, 0, 1, 0.0, 0.0, 0.0),
          ({k: s_lib[k] for k in keys}, {k: s_ext[k] for k in keys}))
    # ---- delta: the EXTRACTED bucket holds ALL the variant's EXTRACTED edges (kept ones too) -----------------------
    b3 = _dump([("a.f", "a.g", "EXTRACTED"), ("a.f", "a.C.m", "INFERRED")])
    v3 = _dump([("a.f", "a.g", "EXTRACTED"), ("a.f", "a.C.m", "EXTRACTED"), ("a.g", "b.D.m", "EXTRACTED")])
    d3 = D.delta(b3, v3)
    check("delta: the EXTRACTED bucket holds every EXTRACTED edge of the variant — one the base already had "
          "EXTRACTED, a promoted one and a new one; the cross-file one (a.py -> b.py) is counted",
          [(i["caller"], i["callee"]) for i in d3["items"]["EXTRACTED"]]
          == [("a.f", "a.C.m"), ("a.f", "a.g"), ("a.g", "b.D.m")] and d3.get("extracted_cross_file") == 1,
          (d3["items"]["EXTRACTED"], d3.get("extracted_cross_file")))
    sx = CC.score(TRUTH, v3)
    check("compare_calls reports the cross-file EXTRACTED edges of a dump (a.g -> b.D.m)",
          sx.get("extracted_cross_file") == 1 and sx.get("extracted_cross_file_pairs") == [["a.g", "b.D.m"]],
          sx.get("extracted_cross_file"))
    # ---- audit_sample: losses over both strata; the stratified correct-removal share ------------------------------
    keyd = {"label": "x", "strata": {"removed": {"population": 10, "sampled": 2},
                                     "removed_confirmed": {"population": 1, "sampled": 1}},
            "rows": {"r5": {"buckets": ["removed"], "verdict": "refuted", "caller": "c5", "callee": "d5"},
                     "r6": {"buckets": ["removed"], "verdict": "refuted", "caller": "c6", "callee": "d6"},
                     "r7": {"buckets": ["removed_confirmed"], "verdict": "confirmed", "caller": "c7", "callee": "d7"},
                     "r8": {"buckets": ["added"], "verdict": "confirmed", "caller": "c8", "callee": "d8"}}}
    votes = {"r5": "TRUE", "r6": "FALSE", "r7": "FALSE", "r8": "TRUE"}
    sc = AS.score(keyd, [dict(votes), dict(votes)])
    lo = sc.get("losses") or {}
    ra = sc.get("removed_all") or {}
    check("audit score: a SAMPLED removal audited TRUE by every reviewer is a LOSS (top-level losses, with its id), "
          "not only a lower correct-removal share; an added row audited TRUE is no loss",
          lo.get("n") == 1 and [x.get("id") for x in lo.get("rows", [])] == ["r5"], lo)
    check("audit score: the correct-removal share over BOTH strata, population-weighted: (10 x 1/2 + 1 x 1/1) / 11 "
          "= .5455, lower bound (10 x .0945 + 1) / 11 = .1768",
          (ra.get("population"), ra.get("share"), ra.get("lower_bound")) == (11, 0.5455, 0.1768), ra)
    st = AS.strata({"removed": [{"verdict": "confirmed"}, {"verdict": "refuted"}, {"verdict": None}]},
                   {"removed": [{"verdict": "refuted"}], "removed_confirmed": [{"verdict": "confirmed"}]}) \
        if callable(getattr(AS, "strata", None)) else None
    check("audit sample: key.json records both removal strata (population, sampled)",
          st == {"removed": {"population": 2, "sampled": 1}, "removed_confirmed": {"population": 1, "sampled": 1}}, st)
    # ---- impact_spot: a spot saturated at V0 can never improve ------------------------------------------------------
    tdir = base / "spot_sat"
    tdir.mkdir(exist_ok=True)
    full = _dump([("a.f", "a.C.m", "AMBIGUOUS")])
    empty = _dump([])
    for nm, obj in (("t.json", TRUTH), ("full.json", full), ("empty.json", empty)):
        (tdir / nm).write_text(json.dumps(obj), encoding="utf-8")
    so, se = _quiet()
    with so, se:
        rc_sat = IS.main([f"--truth={tdir / 't.json'}", "--symbols=m@a.py", str(tdir / "full.json"),
                          f"--base={tdir / 'full.json'}"])
        rc_ok = IS.main([f"--truth={tdir / 't.json'}", "--symbols=m@a.py", str(tdir / "full.json"),
                         f"--base={tdir / 'empty.json'}"])
    check("impact_spot --base: a spot whose base hop-1 recall is already 1 is SATURATED (exit 1: it can never count "
          "toward D3-G1); one the base misses is not (exit 0)", (rc_sat, rc_ok) == (1, 0), (rc_sat, rc_ok))
    # ---- score_m: `$` symbols and TS/JS def spans -----------------------------------------------------------------
    a1 = SM.parse_atom("packages/zod/src/v4/core/errors.ts:248 — $ZodError (export const $ZodError = $constructor)")
    check("score_m parses a `$` atom symbol ($ZodError)", bool(a1) and a1["sym"] == "$ZodError" and a1["last"] == "$ZodError",
          a1)
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
        have_ts = True
    except Exception:  # noqa: BLE001
        have_ts = False
    if not have_ts:
        skip("score_m: TS/JS def spans", "tree-sitter / tree-sitter-typescript are not importable in this Python")
        return
    ts_clone = base / "sm_ts"
    (ts_clone / "src").mkdir(parents=True, exist_ok=True)
    (ts_clone / "src" / "x.ts").write_text(
        "export const $ZodError = $constructor(\"$ZodError\", initializer);\n"
        "export function _parse(schema: any, value: unknown) {\n"
        "  const result = schema._zod.run({ value, issues: [] }, ctx);\n"
        "  if (result.issues.length) {\n"
        "    throw new Err(result.issues.map((iss: any) => finalizeIssue(iss)));\n"
        "  }\n"
        "  return result.value;\n"
        "}\n"
        "export class Repo {\n"
        "  @Log()\n"
        "  find(id: number) {\n"
        "    return id;\n"
        "  }\n"
        "}\n", encoding="utf-8")
    at_err = SM.parse_atom("src/x.ts:1 — $ZodError (export const $ZodError = $constructor)")
    at_throw = SM.parse_atom("src/x.ts:5 — throw new Err mapping result.issues through finalizeIssue")
    at_ret = SM.parse_atom("src/x.ts:12 — return id")
    n_err = {"name": "$ZodError", "type": "Class", "file": "src/x.ts", "line": 1}
    n_parse = {"name": "_parse", "type": "Function", "file": "src/x.ts", "line": 2}
    n_find = {"name": "Repo.find", "type": "Method", "file": "src/x.ts", "line": 11}
    h = {"$ZodError node, $ZodError atom": SM.hits(at_err, [n_err], ts_clone),
         "_parse node, throw atom inside it": SM.hits(at_throw, [n_parse], ts_clone),
         "Repo.find node, throw atom outside it": SM.hits(at_throw, [n_find], ts_clone),
         "Repo.find node, atom in its decorated span": SM.hits(at_ret, [n_find], ts_clone)}
    got = {k: (v["strict"], v["spec"]) for k, v in h.items()}
    check("score_m on TS: a `$ZodError` node at the atom line hits STRICT; a node whose TS span (tree-sitter) covers "
          "the atom line hits SPEC (and STRICT as the innermost def); a node elsewhere hits neither",
          got == {"$ZodError node, $ZodError atom": (True, True), "_parse node, throw atom inside it": (True, True),
                  "Repo.find node, throw atom outside it": (False, False),
                  "Repo.find node, atom in its decorated span": (True, True)}, got)


# =====================================================================================================
# F — stage0_refreeze --env
# =====================================================================================================
class _Abort(Exception):
    pass


def gate_f(refreeze_path: Path, base: Path) -> None:
    print("\n-- F: stage0_refreeze --env forwards only the DEEPMAP_* keys it is given; child_env otherwise unchanged --")
    try:
        sys.path.insert(0, str(TESTS))
        RF = _load_file(refreeze_path, f"_cl_s0_refreeze_{time.time_ns()}")
    except Exception as exc:  # noqa: BLE001
        check(f"{refreeze_path.name} loads", False, repr(exc))
        return
    basenv = {"NEO4J_URI": "bolt://localhost:7687", "DEEPMAP_EXTRACTOR": "ast", "DEEPMAP_HOME": "/h",
              "DEEPMAP_PYTHON": "/py", "PATH": "/bin", "NEO4J_PASSWORD": "pw"}
    plain = RF.child_env(dict(basenv))
    check("child_env without --env is exactly the stripped default environment",
          plain == {"DEEPMAP_HOME": "/h", "DEEPMAP_PYTHON": "/py", "PATH": "/bin", "NEO4J_PASSWORD": "pw",
                    "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", "HF_HUB_OFFLINE": "1"}, plain)
    try:
        with_extra = RF.child_env(dict(basenv), extra={"DEEPMAP_TYPED_CALLS": "1", "DEEPMAP_CALLS_AUDIT": "1"})
        none_extra = RF.child_env(dict(basenv), extra=None)
    except TypeError as exc:
        check("child_env takes the --env flags (extra=)", False, repr(exc))
        return
    check("child_env(extra=...) == the stripped default + exactly those keys", with_extra == dict(
        plain, DEEPMAP_TYPED_CALLS="1", DEEPMAP_CALLS_AUDIT="1") and none_extra == plain, with_extra)
    parse = getattr(RF, "parse_env_args", None)
    if not callable(parse):
        check("stage0_refreeze.parse_env_args exists", False)
        return
    ok, why = parse(["DEEPMAP_TYPED_CALLS=python,typescript", "DEEPMAP_CALLS_AUDIT=1", "DEEPMAP_X=a=b"])
    check("--env accepts DEEPMAP_* graph flags (a value may hold '=' and ',')",
          why is None and ok == {"DEEPMAP_TYPED_CALLS": "python,typescript", "DEEPMAP_CALLS_AUDIT": "1",
                                 "DEEPMAP_X": "a=b"}, (ok, why))
    refused = {item: parse([item])[1] for item in ("NEO4J_URI=bolt://x", "PATH=/x", "DEEPMAP_HOME=/x",
                                                   "DEEPMAP_PYTHON=/x", "deepmap_typed_calls=1", "DEEPMAP_=1",
                                                   "DEEPMAP_TYPED_CALLS")}
    check("--env refuses non-DEEPMAP keys, the home / interpreter, lower case, an empty name and a missing '='",
          all(refused.values()), refused)
    def run_main(args):
        so, se = _quiet()
        try:
            with so, se:
                return RF.main(args)
        except SystemExit as exc:          # argparse rejecting an unknown option (a tree without --env)
            return exc.code if isinstance(exc.code, int) else 2

    work = base / "rf_work"
    rc_bad = run_main(["--work", str(work), "--env", "NEO4J_URI=bolt://x"])
    rc_freeze = run_main(["--work", str(work), "--freeze", "--env", "DEEPMAP_TYPED_CALLS=1"])
    check("main: a refused --env exits 2 before anything is created", rc_bad == 2 and not work.exists(), rc_bad)
    check("main: --env with --freeze exits 2 (an oracle is frozen on the default graph only)",
          rc_freeze == 2 and not work.exists(), rc_freeze)
    saved = (RF.child_env, RF._registry, RF._load_build)
    for argv, want, label in (
            (["--check", "--env", "DEEPMAP_TYPED_CALLS=1"], {"DEEPMAP_TYPED_CALLS": "1"},
             "main --check --env DEEPMAP_TYPED_CALLS=1: every step's environment gets exactly that flag"),
            (["--check"], {}, "main --check without --env: no flag is added (the default pipeline, as before)")):
        seen = {}

        def _rec(base_=None, extra=None, _seen=seen):   # a RECORDER: the real child_env still runs
            _seen["extra"] = dict(extra or {})
            return saved[0](base_, extra=extra)

        def _abort(name):
            raise _Abort(name)
        RF.child_env = _rec
        RF._registry = lambda: {"oracles": {rid: {"corpus": {"sha": "a" * 40}} for rid in RF.ORDER}}
        RF._load_build = _abort            # the first step after the environment is built: stop there
        try:
            rc = run_main(["--work", str(base / f"rf_work_{len(argv)}")] + argv)
            check(label, False, f"main returned {rc} without reaching the build")
        except _Abort:
            check(label, seen.get("extra") == want, seen)
        finally:
            RF.child_env, RF._registry, RF._load_build = saved


# =====================================================================================================
# G — compliance
# =====================================================================================================
def gate_g() -> None:
    print("\n-- G: compliance — jedi and TypeScript appear in no shipped list --")
    hits = {}
    for rel in ("requirements.txt", "sbom.cdx.json", "NOTICE", "THIRD-PARTY-LICENSES.md", "install.sh", "install.ps1",
                ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        p = REPO / rel
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        found = [w for w in ("jedi", "parso") if re.search(rf"\b{w}\b", t, re.I)]
        if re.search(r"typescript@|npm\s+(i|install)\b|\"typescript\"|^\s*typescript\b", t, re.I | re.M):
            found.append("typescript (npm package)")
        if found:
            hits[rel] = found
    try:
        comps = [str(c.get("name", "")).lower() for c in json.loads((REPO / "sbom.cdx.json").read_text(
            encoding="utf-8")).get("components", [])]
    except Exception:  # noqa: BLE001
        comps = []
    bad_c = [c for c in comps if c in ("jedi", "parso", "typescript")]
    check("requirements / SBOM / NOTICE / THIRD-PARTY-LICENSES / installers / manifests name neither jedi (parso) nor "
          "the TypeScript package", not hits and not bad_c, (hits, bad_c))


# =====================================================================================================
# H — PREREG.md and gt_self65.json
# =====================================================================================================
def gate_h(mods: dict, kit: Path) -> None:
    print("\n-- H: PREREG.md (§8 verbatim, hash-locked) and gt_self65.json --")
    try:
        pre = (kit / "PREREG.md").read_text(encoding="utf-8")
    except OSError as exc:
        check("PREREG.md exists", False, repr(exc))
        return
    lines = pre.split("\n")
    try:
        b = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-SECTION-8 BEGIN"))
        e = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-SECTION-8 END"))
        block = "\n".join(lines[b + 1:e]) + "\n"
    except StopIteration:
        block = ""
    got = hashlib.sha256(block.encode("utf-8")).hexdigest()
    check("PREREG §8 is the design's §8 verbatim (sha256 locked: thresholds are never edited after data)",
          got == PREREG_S8_SHA256 and block.startswith("## 8. Measurement protocol\n"), got)
    check("PREREG records the RC code SHA as the S5 working tree (no commit) and a build/*.py content hash slot",
          "the working tree as of the S5 suite run (no commit: the operator commits)" in pre
          and "Content hash of build/\\*.py" in pre and "offline_calls.py codehash" in pre)
    check("PREREG records the engine builds (graphifyy 0.8.39 + 0.8.51) and the reference resolvers (jedi 0.20.1, "
          "TypeScript 5.6.3)", all(w in pre for w in ("graphifyy 0.8.39", "graphifyy 0.8.51", "jedi 0.20.1",
                                                      "TypeScript 5.6.3")))
    rm = (kit / "run_matrix.sh").read_text(encoding="utf-8") if (kit / "run_matrix.sh").is_file() else ""
    rm_shas = dict(re.findall(r"^\s*([\w\-]+__[\w.\-]+|stage0_build)\)\s+echo \"([0-9a-f]{40})\|", rm, re.M))
    pre_shas = set(re.findall(r"\b([0-9a-f]{40})\b", pre.split("<!-- PREREG-SECTION-8 BEGIN")[0]))
    check("every corpus SHA run_matrix.sh pins is in PREREG (and the TS app + JS repo are pinned there)",
          rm_shas and set(rm_shas.values()) <= pre_shas
          and "c1c2cc4e448b279ff083272df1ac50d20c3304fa" in pre_shas
          and "179ae84efec61b14206d0305d941daed6c6d07f9" in pre_shas, (rm_shas, sorted(pre_shas)))
    IS = mods.get("impact_spot")
    if IS is not None:
        check("PREREG names the 5 TS/JS spot symbols impact_spot.py registers, and the 5 Python ones",
              all(f"`{s}`" in pre for s in IS.TS_SPOTS) and all(f"`{s}`" in pre for s in IS.PY_SPOTS),
              [s for s in IS.TS_SPOTS + IS.PY_SPOTS if f"`{s}`" not in pre])
        head = pre.split("<!-- PREREG-SECTION-8 BEGIN")[0]
        sec4 = head.split("## 4.")[-1].split("## 5.")[0] if "## 4." in head else ""
        rows = {}   # symbol -> (the integer-only cells [reference callers, V0 hits 0.8.39, V0 hits 0.8.51], line)
        for ln in sec4.splitlines():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
            sym = next((c[1:-1] for c in cells[:2] if re.fullmatch(r"`[^`]+`", c)), None)
            if sym:   # cells[0] is the row number (TS table) or the symbol (Python table): never a count
                rows[sym] = ([int(c) for c in cells[1:] if re.fullmatch(r"\d+", c)][-3:], ln)

        def unsaturated(nums):
            return len(nums) == 3 and nums[0] > 0 and nums[1] < nums[0] and nums[2] < nums[0]
        ts_bad = {s: (rows[s][0] if s in rows else "missing") for s in IS.TS_SPOTS
                  if s not in rows or not unsaturated(rows[s][0])}
        check("PREREG §4 records every TS/JS spot's V0 hop-1 recall on both engines (reference callers, hits) and none "
              "is saturated (V0 recall < 1: the D3-G1 '3 of 5' counts five spots that CAN improve)", not ts_bad, ts_bad)
        py_bad = {s: (rows[s][0] if s in rows else "missing") for s in IS.PY_SPOTS
                  if s not in rows or not unsaturated(rows[s][0])}
        check("PREREG §4 records every Python spot's V0 hop-1 recall on both engines and none is saturated (the "
              "design-given `_mk_project` was replaced before RC: amendment A2)", not py_bad, py_bad)
        check("every spot symbol lives on a corpus run_matrix.sh pins to a SHA (its PREREG V0 numbers are "
              "reproducible at RC; the `self` working tree moves)", rm_shas and set(IS.SPOTS) <= set(rm_shas),
              sorted(set(IS.SPOTS) - set(rm_shas)))
    amend = pre.split("## Amendments before RC")[-1].split("<!-- PREREG-SECTION-8 BEGIN")[0] \
        if "## Amendments before RC" in pre else ""
    a1 = amend.split("### A1")[-1].split("### A2")[0] if "### A1" in amend else ""
    a2 = amend.split("### A2")[-1] if "### A2" in amend else ""
    check("PREREG has a dated 'Amendments before RC' section ahead of the verbatim §8 (which stays hash-locked)",
          bool(amend) and "2026-10-23" in amend and "before any RC data" in amend
          and pre.index("## Amendments before RC") < pre.index("<!-- PREREG-SECTION-8 BEGIN"))
    check("PREREG amendment A1 records the E change with its reason: parity on edges whose BOTH endpoints exist on "
          "both engines (version-stable keys), the raw symmetric difference reported beside it, the zod static "
          "arrow-field `create` members graphify 0.8.39 makes no node for",
          all(w in a1 for w in ("both endpoints", "raw symmetric difference", "label:name@file:line", "engdiff.py",
                                "static create", "0.8.39", "90", "74")), a1[:300])
    check("PREREG amendment A2 records the spot replacement with its reason: `_mk_project` saturated at V0, "
          "`read_records` chosen by V0 data only, before any typed-linking (VT) data on it",
          all(w in a2 for w in ("`_mk_project`", "`read_records`", "SATURATED", "V0 data only", "no typed-linking",
                                "impact_spot.py")), a2[:300])
    check("PREREG §5 states the inert-variant VOID rule is enforced in code (offline_calls variant_void, delta)",
          "variant_void" in pre and "delta.py" in pre.split("## 5.")[-1].split("## 6.")[0] if "## 5." in pre else False)
    sec5 = pre.split("## 5.")[-1].split("## 6.")[0] if "## 5." in pre else ""
    check("PREREG §5 names the switches the effective block reports through the product beyond the language lists "
          "(the cross-family filter, the typed-calls shapes / tests / non-production / replace) and the VOID rule for "
          "a requested switch the product left off",
          all(w in sec5 for w in ("DEEPMAP_CALLS_FAMILY_FILTER", "DEEPMAP_TYPED_CALLS_SHAPES",
                                  "DEEPMAP_TYPED_CALLS_TESTS", "DEEPMAP_TYPED_CALLS_NONPROD",
                                  "DEEPMAP_TYPED_CALLS_REPLACE", "left off")), sec5[-400:])
    # PD-1: the zod engine-specific typed tuples go the DECISION.md route, recorded before RC; E is not amended again.
    heads = re.split(r"^### ", amend, flags=re.M)[1:]           # one chunk per subsection, heading to heading
    e_amends = [h.split()[0] for h in heads if re.match(r"A\d+\b", h) and "§8.4 E" in h]
    check("PREREG: A1 stays the only amendment of §8.4 E (the zod tuples S4 found after A1 were not answered with a "
          "second amendment of E)", e_amends == ["A1"], e_amends)
    sec7 = pre.split("## 7.")[-1].split("## Amendments before RC")[0] if "## 7." in pre else ""
    check("PREREG §7 (PILOT) reports S4's near-RC engine-parity run and points to PD-1 in PRE_RC_DECISIONS.md",
          "PD-1" in sec7 and "PRE_RC_DECISIONS.md" in sec7 and "454" in sec7 and "456" in sec7, sec7[-500:])
    try:
        pd = (kit / "PRE_RC_DECISIONS.md").read_text(encoding="utf-8")
    except OSError as exc:
        pd = ""
        check("PRE_RC_DECISIONS.md exists", False, repr(exc))
    pd1 = " ".join((pd.split("## PD-1")[-1].split("\n## ")[0] if "## PD-1" in pd else "").split())
    pdn = " ".join(pd.split())                                      # whitespace-normalized: phrases span lines
    check("PRE_RC_DECISIONS.md: decisions recorded BEFORE the first RC run go the DECISION.md route (copied verbatim "
          "into DECISION.md), change no criterion or threshold, and are not decision data",
          all(w in pdn for w in ("before the first RC run", "verbatim into DECISION.md", "changes no criterion",
                                 "no threshold", "is not decision data")), pdn[:400])
    check("PD-1 records the two zod tuples with the general cause (an evidence chain through a node only one engine "
          "makes: `ZodTuple.create`, A1's generic static arrow member), the TypeScript 5.6.3 truth, the identical "
          "final CALLS edge, and that it is a list, not a rule",
          "2026-10-24" in pd1 and all(t[0].split("@")[0] in pd1 for t in PD1_TUPLES)
          and all(w in pd1 for w in ("evidence chain through a node only one engine makes", "ZodTuple.create",
                                     "TypeScript 5.6.3", "not a rule", "A1", "INFERRED", "0.8.51",
                                     "D3-js G6", "§8.4 E")), pd1[:600])
    check("run_matrix.sh materializes the STAGE-0 corpus as REGISTERED (offline_calls.py stage0-corpus, build/'s "
          "contents at the root, the whole corpus as its library code) and PREREG §3 says so",
          bool(re.search(r'stage0_build\)\s+echo "9c8d50bceab96288f83877c5306167b54b6d918a\|\|py\|\|\|stage0"', rm))
          and 'offline_calls.py" stage0-corpus' in rm and "stage0-corpus" in pre.split("## 3.")[-1].split("## 4.")[0])
    OC = mods.get("offline_calls")
    sha_ok = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", "9c8d50bceab96288f83877c5306167b54b6d918a^{commit}"],
                            capture_output=True).returncode == 0
    if OC is None or not callable(getattr(OC, "stage0_corpus", None)):
        check("offline_calls.stage0_corpus exists", False)
    elif not sha_ok:
        skip("H: materialize the STAGE-0 corpus", "commit 9c8d50bc is not in this clone (a shallow CI checkout)")
    else:
        dest = Path(tempfile.mkdtemp(prefix="dm_cl_s0c_"))
        IS = mods.get("impact_spot")
        s0_spots = list((IS.SPOTS.get("stage0_build") or []) if IS is not None else [])
        spot_src = {}
        try:
            res = OC.stage0_corpus(dest / "c")
            top = sorted(p.name for p in (dest / "c").iterdir())
            for sym in s0_spots:
                f = sym.partition("@")[2]
                if f and (dest / "c" / f).is_file():
                    spot_src[f] = (dest / "c" / f).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — a failure is a RED check, never a crash of the section
            res, top = {"error": repr(exc)}, []
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        check("offline_calls stage0-corpus: the registered self-graphify-build corpus — build/'s CONTENTS at the "
              "root (sync.py, no build/ dir), 34 files, no .git",
              res.get("corpus", {}).get("tree") == "build" and res.get("files") == 34 and "sync.py" in top
              and "build" not in top and ".git" not in top, (res, top[:8]))
        check("the STAGE-0 corpus holds a registered spot symbol (amendment A2)", bool(s0_spots), s0_spots)
        for sym in s0_spots:
            name, _, f = sym.partition("@")
            defs = re.findall(rf"^\s*def {re.escape(name.split('.')[-1])}\(", spot_src.get(f, ""), re.M)
            check(f"the STAGE-0 spot `{sym}` is defined exactly once in its file of the registered corpus",
                  bool(f) and len(defs) == 1, (f, len(defs)))
    V = mods.get("variants")
    if V is not None:
        table = re.findall(r"^\| (V[^|]*?) \| (.*?) \|$", block.split("### 8.3 Variants")[-1].split("### 8.4")[0], re.M)
        ids = [t[0].strip().replace("\u2212", "-") for t in table if t[0].strip() != "Id"]
        expanded = []
        for i in ids:
            expanded += [f"VT[{s}]" for s in V.SHAPES] if i == "VT[shape]" else \
                ["VT+t", "VT+np"] if i == "VT+t / VT+np" else [i]
        check("variants.py spells every §8.3 variant and no other", sorted(expanded) == sorted(V.VARIANTS),
              (sorted(expanded), sorted(V.VARIANTS)))
        want = {"V0": {}, "VF": {"DEEPMAP_CALLS_FAMILY_FILTER": "1"}, "VA": {"DEEPMAP_CALLS_AUDIT": "1"},
                "VT": {"DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1"},
                "VP": {"DEEPMAP_PRECISE_CALLS": "python,js"}}
        want["VT-a"] = dict(want["VT"], DEEPMAP_CALLS_AUDIT="0")
        want["VPA"] = dict(want["VP"], DEEPMAP_CALLS_AUDIT="1")
        want["VPT"] = dict(want["VP"], **want["VT"])
        want["V9"] = dict(want["VPT"], DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
        want["VT+t"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_TESTS="1")
        want["VT+np"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_NONPROD="1")
        for s_ in V.SHAPES:
            want[f"VT[{s_}]"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_SHAPES=s_)
        got_f = {k: v for k, v in V.VARIANTS.items() if v is not None}
        check("variants.py's flags are §8.3's (VT = TYPED wave 1 + AUDIT; VT-a pins AUDIT=0; VP = PRECISE "
              "python,js; V9 = VPT + CALLS_PLUS; one SHAPES arm per shape)", got_f == want and V.VARIANTS["VRC"] is None,
              {k: (got_f.get(k), want.get(k)) for k in set(got_f) | set(want) if got_f.get(k) != want.get(k)})
    check("PREREG labels the S0 numbers PILOT and not decision data", "PILOT" in pre and "NOT decision data" in pre)
    check("PREREG and run_matrix.sh pin the engine (an ast fallback is VOID: --expect-engine graphify)",
          "--expect-engine graphify" in pre and "--expect-engine graphify" in rm)
    try:
        gt = json.loads((kit / "gt_self65.json").read_text(encoding="utf-8"))
        qs = gt["queries"]
        ok = (len(qs) == 65 and len({q["id"] for q in qs}) == 65 and gt["_provenance"]["queries"] == 65
              and all(q.get("q") and q.get("expected") and q.get("set") in ("A", "B") for q in qs)
              and len(gt["_provenance"].get("original_sha256", "")) == 64)
    except Exception as exc:  # noqa: BLE001
        ok, qs = False, repr(exc)
    check("gt_self65.json: 65 unique queries (sets A/B) with expected symbols and a provenance block", ok,
          qs if not ok else "")


# =====================================================================================================
# I — the truth tools
# =====================================================================================================
TS_FIXTURE = {
    "repo.ts": "export class Repo {\n  find(id: number) { return id; }\n}\n",
    "service.ts": ("import { Repo } from './repo';\n\nexport class Service {\n  constructor(private repo: Repo) {}\n"
                   "  run() {\n    this.helper();\n    return this.repo.find(1);\n  }\n  helper() { return new Repo(); }\n}\n"),
}


def gate_i(mods: dict, kit: Path, base: Path) -> None:
    print("\n-- I: the truth tools --")
    TJ = mods.get("truth_jedi")
    if TJ is None:
        check("truth_jedi imports without jedi", False)
        return
    src = ("import os\n\n\nclass K(Base):\n    @staticmethod\n    @other\n    def m(self):\n        self.a()\n"
           "        super().b()\n        os.path.join()\n        x.c()\n        f()\n        x[0]()\n\n"
           "    @classmethod\n    def n(cls):\n        cls.d()\n\n\ndef top():\n    y = K()\n    return K().m()\n\nmain()\n")
    v = TJ.Sites()
    v.visit(ast.parse(src))
    forms = [(c["form"], c["name"], [e[0] for e in c["defstack"]]) for c in v.calls]
    check("truth_jedi call sites: forms self / super / recv / name_attr / bare / other / cls, with their defstack",
          forms == [("self", "a", ["K.m"]), ("super", "b", ["K.m"]), ("bare", "super", ["K.m"]),
                    ("recv", "join", ["K.m"]), ("name_attr", "c", ["K.m"]), ("bare", "f", ["K.m"]),
                    ("other", None, ["K.m"]), ("cls", "d", ["K.n"]), ("bare", "K", ["top"]),
                    ("recv", "m", ["top"]), ("bare", "K", ["top"]), ("bare", "main", [])], forms)
    m_entry = v.calls[0]["defstack"][0]
    check("truth_jedi defstack carries the def line AND the first decorator line (a graph node may sit on either)",
          m_entry[1] == 7 and m_entry[2] == [5], m_entry)
    line = "    print(\"éé —\", r.f(helper()))"
    u8 = "def use(r):\n" + line + "\n"
    try:
        vu = TJ.Sites(u8.split("\n"))
        vu.visit(ast.parse(u8))
        pos = {c["name"]: c["pos"] for c in vu.calls}
        want = {"print": (2, line.index("print")), "f": (2, line.index("f(helper")), "helper": (2, line.index("helper"))}
    except TypeError as exc:
        pos, want = repr(exc), "Sites(lines)"
    check("truth_jedi gives jedi CHARACTER columns: on a call line with non-ASCII text before the call, r.f() and "
          "helper() sit at their character positions (ast's UTF-8 byte offsets would point jedi at the wrong name)",
          pos == want, (pos, want))
    jedi_py = os.environ.get("CALL_LINKING_JEDI_PY", "")
    if not (jedi_py and Path(jedi_py).is_file()):
        skip("I: truth_jedi end to end (jedi 0.20.1)", "set CALL_LINKING_JEDI_PY to a jedi 0.20.1 venv's python")
    else:
        jc = base / "jedi_enc"
        jc.mkdir(parents=True, exist_ok=True)
        (jc / "mod.py").write_text("class Repo:\n    def f(self, x):\n        return x\n\n\ndef helper():\n    return 1\n\n\n"
                                   "def use(r: Repo):\n" + line + "\n", encoding="utf-8")
        (jc / "bom.py").write_bytes("﻿def g():\r\n    return 2\r\n\r\n\r\ndef call_g():\r\n    return g()\r\n"
                                    .encode("utf-8"))
        (jc / "lat.py").write_bytes("# -*- coding: latin-1 -*-\ndef h():\n    return 3\n\n\ndef call_h():\n"
                                    "    s = \"é\"; return h()\n".encode("latin-1"))
        (jc / "broken.py").write_text("def x(:\n", encoding="utf-8")
        out = base / "jedi_enc.json"
        p = subprocess.run([jedi_py, "-I", str(kit / "truth_jedi.py"), str(jc), str(out)], capture_output=True,
                           text=True, timeout=600)
        if p.returncode != 0 or not out.is_file():
            check("I: truth_jedi runs on the encoding fixture", False, p.stderr[-400:])
        else:
            tj = json.loads(out.read_text(encoding="utf-8"))
            res = sorted((r["file"], r["name"], tuple((d["file"], d["line"]) for d in r["defs"])) for r in tj["rows"])
            check("I: truth_jedi resolves a call after non-ASCII text, in a BOM + CRLF file and in a PEP 263 latin-1 "
                  "file; only the syntax-error file is a parse error, named",
                  res == sorted([("mod.py", "print", ()), ("mod.py", "f", (("mod.py", 2),)),
                                 ("mod.py", "helper", (("mod.py", 6),)), ("bom.py", "g", (("bom.py", 1),)),
                                 ("lat.py", "h", (("lat.py", 2),))])
                  and tj.get("parse_errors") == ["broken.py: SyntaxError"], (res, tj.get("parse_errors")))
    tsenv = os.environ.get("CALL_LINKING_TSENV", "")
    node = shutil.which("node")
    if not (tsenv and node):
        skip("I: truth_ts.js on a TS fixture", "set CALL_LINKING_TSENV to a dir holding node_modules/typescript@5.6.3")
        return
    proj = base / "tsproj"
    (proj / "src").mkdir(parents=True)
    for rel, text in TS_FIXTURE.items():
        (proj / "src" / rel).write_text(text, encoding="utf-8")
    out = base / "ts_truth.json"
    p = subprocess.run([node, str(kit / "truth_ts.js"), tsenv, str(proj), "src", str(out)], capture_output=True,
                       text=True, timeout=600)
    if p.returncode != 0:
        check("truth_ts.js runs on the fixture", False, p.stderr[-400:])
        return
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"]
    got = sorted((r["file"], r["form"], r["name"], tuple((d["file"], d["line"], d["type"]) for d in r["defs"]))
                 for r in rows)
    check("truth_ts.js: this.helper() / this.repo.find() (typed through the constructor parameter property) / "
          "new Repo() resolve to their declarations",
          got == sorted([("src/service.ts", "this", "helper", (("src/service.ts", 9, "function"),)),
                         ("src/service.ts", "recv", "find", (("src/repo.ts", 2, "function"),)),
                         ("src/service.ts", "ctor", "Repo", (("src/repo.ts", 1, "class"),))]), got)


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default):
        if name in argv:
            i = argv.index(name)
            return Path(argv[i + 1]).resolve()
        return default
    kit = opt("--kit", KIT_DEFAULT)
    sync_path = opt("--sync", BUILD / "sync.py")
    refreeze = opt("--refreeze", TESTS / "stage0_refreeze.py")
    real = "--no-real" not in argv
    print("=" * 72)
    print(f"DeepMap — S0 call-linking measurement kit + pre-registration (kit: {kit})")
    print("=" * 72)
    base = Path(tempfile.mkdtemp(prefix="dm_cl_s0_")).resolve()
    try:
        with _env(DEEPMAP_HOME=str(base / "home"), NEO4J_URI=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None):
            mods = {}
            for name, fn, args in (("A", gate_a, (kit,)), ("B", gate_b, None), ("C", gate_c, None), ("D", gate_d, None),
                                   ("E", gate_e, None), ("F", gate_f, (refreeze, base)), ("G", gate_g, ()),
                                   ("H", gate_h, None), ("I", gate_i, None)):
                if args is None:
                    args = {"B": (mods, sync_path, kit), "C": (mods, base, real), "D": (mods, kit, base, real),
                            "E": (mods, base), "H": (mods, kit), "I": (mods, kit, base)}[name]
                try:
                    out = fn(*args)
                    if name == "A":
                        mods = out or {}
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    check(f"section {name} ran without crashing", False, f"{exc!r}\n{traceback.format_exc()[-900:]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n" + "=" * 72)
    print(f"RESULT: {len(PASSES)} passed, {len(FAILS)} failed, {len(SKIPS)} skipped")
    for f in FAILS:
        print(f"  FAIL: {f}")
    print("=" * 72)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
