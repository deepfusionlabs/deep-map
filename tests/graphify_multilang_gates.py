#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j MULTI-LANGUAGE gates for build/graphify_extract.map_graph.

The single-fixture graphify_map_gates.py proves the loose->typed inference on ONE Python-shaped
sample. This locks the *multi-language* promise: DeepMap's graphify default must type real graphs
from TypeScript, JavaScript (CommonJS + ES6 imports), Go, Rust, Java, Ruby, and C# — the reason
graphify exists over the Python-only ast path.

Every fixture in tests/fixtures/graphify_multilang/ is REAL, captured output of
`graphify update --no-cluster` on a tiny sample repo in that language (captured under a socket
tripwire — zero egress), trimmed to the fields map_graph reads. This gate feeds each through the
pure mapper (no graphify run, no Neo4j, no network) and asserts:

  * classes -> :Class, their members -> :Method (Class.method names), free functions -> :Function,
    files -> :Module — across all seven languages;
  * the intra-service call chain resolves (place_order -> compute, caller -> service);
  * NO junk node leaks in — specifically the CommonJS `const { x } = require(...)` destructuring,
    whose graphify label is `{ x }` and which the naive mapper typed as a bogus `Class { x }`
    (the Phase-2 hardening bug this gate regression-locks).

JavaScript method-typing is language-shape-dependent and locked HONESTLY by two fixtures (G0(a)
diagnostic, 2026-07-07): `js_esm_class` (a modern ESM `export default class`) proves graphify emits
`method` edges -> correct :Method typing; `js_commonjs` (prototype/`module.exports` style) proves
graphify emits NONE, so members type :Function and 0 :Method result — the express weakness is a
graphify CommonJS limitation, NOT a mapper bug. The `absent_labels` key regression-locks that 0-Method
documented limit. See docs/known-limitations.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
FIXDIR = ROOT / "tests" / "fixtures" / "graphify_multilang"
sys.path.insert(0, str(BUILD))
import graphify_extract as G  # noqa: E402

VALID_LABELS = {"Function", "Method", "Class", "Module", "Route"}

# Per-language expectations, human-verified against the real captured graphs. Each entry:
#   name_labels : (name, label) pairs that MUST appear in the typed nodes
#   calls       : (caller_name, callee_name) CALLS pairs that MUST resolve
#   forbidden   : substrings that must NOT appear in any node name (junk regression)
EXPECT = {
    "typescript": {
        "name_labels": [("api", "Module"), ("db", "Module"), ("orders", "Module"),
                        ("OrderService", "Class"),
                        ("OrderService.placeOrder", "Method"), ("OrderService.compute", "Method"),
                        ("checkout", "Function"), ("save", "Function"), ("handleRequest", "Function")],
        "calls": [("OrderService.placeOrder", "OrderService.compute"),
                  ("checkout", "OrderService.placeOrder"), ("handleRequest", "checkout")],
        "forbidden": ["{", "}", "checkout }"],
    },
    "js_ts_imports": {
        # The CommonJS `const { gamma } = require(...)` node (label `{ gamma }`) must be DROPPED,
        # not typed as a Class. ES6 `import {a,b}`, `import * as ns`, `import Default`, `import type`
        # produce no junk either. `Default` (a real `export default class`) survives as a Class.
        "name_labels": [("main", "Module"), ("util", "Module"),
                        ("run", "Function"), ("alpha", "Function"), ("beta", "Function"),
                        ("delta", "Function"), ("Default", "Class")],
        "calls": [],
        "forbidden": ["{", "}", "gamma"],
    },
    "js_esm_class": {
        # A modern ESM `export default class Queue { ... }` in a .js file (sindresorhus/yocto-queue).
        # graphify DOES emit `method` edges here -> members type :Method (Class.method) correctly.
        # This is the CAPABILITY half of the JS story (closes the prior coverage gap: the suite had
        # no JS-class-with-methods fixture). Contrast js_commonjs below.
        "name_labels": [("index", "Module"), ("Queue", "Class"),
                        ("Queue.constructor", "Method"), ("Queue.enqueue", "Method"),
                        ("Queue.dequeue", "Method"), ("Queue.clear", "Method")],
        "calls": [("Queue.constructor", "Queue.clear")],
        "forbidden": ["{", "}"],
    },
    "js_commonjs": {
        # CommonJS / prototype style (`function Router(){}; Router.prototype.handle=...;
        # module.exports=Router`) — the express shape. graphify emits NO `method` edges (prototype
        # methods aren't even captured), so class members flatten to module-level :Function and 0
        # :Method result. `absent_labels` regression-LOCKS that honest limitation (documented in
        # docs/known-limitations.md); the free functions still type + resolve correctly.
        "name_labels": [("router", "Module"), ("app", "Module"),
                        ("Router", "Function"), ("match", "Function"),
                        ("createApplication", "Function")],
        "calls": [],
        "forbidden": ["{", "}"],
        "absent_labels": ["Method", "Route"],
    },
    "go": {
        "name_labels": [("orders", "Module"), ("OrderService", "Class"),
                        ("OrderService.PlaceOrder", "Method"), ("OrderService.Compute", "Method"),
                        ("Save", "Function"), ("Checkout", "Function"), ("main", "Function")],
        "calls": [("OrderService.PlaceOrder", "OrderService.Compute"),
                  ("OrderService.PlaceOrder", "Save"), ("Checkout", "OrderService.PlaceOrder"),
                  ("main", "Checkout")],
        "forbidden": ["{", "}"],
    },
    "rust": {
        "name_labels": [("lib", "Module"), ("db", "Module"), ("OrderService", "Class"),
                        ("OrderService.place_order", "Method"), ("OrderService.compute", "Method"),
                        ("checkout", "Function"), ("save", "Function")],
        "calls": [("OrderService.place_order", "OrderService.compute"),
                  ("checkout", "OrderService.place_order")],
        "forbidden": ["{", "}"],
    },
    "java": {
        "name_labels": [("OrderService", "Class"), ("Db", "Class"),
                        ("OrderService.placeOrder", "Method"), ("OrderService.compute", "Method"),
                        ("Db.save", "Method")],
        "calls": [("OrderService.placeOrder", "OrderService.compute")],
        "forbidden": ["{", "}"],
    },
    "ruby": {
        "name_labels": [("db", "Module"), ("orders", "Module"), ("OrderService", "Class"),
                        ("OrderService.place_order", "Method"), ("OrderService.compute", "Method"),
                        ("checkout", "Function"), ("save", "Function")],
        "calls": [("OrderService.place_order", "OrderService.compute")],
        "forbidden": ["{", "}"],
    },
    "csharp": {
        "name_labels": [("OrderService", "Class"), ("Db", "Class"),
                        ("OrderService.PlaceOrder", "Method"), ("OrderService.Compute", "Method"),
                        ("Db.Save", "Method")],
        "calls": [("OrderService.PlaceOrder", "OrderService.Compute")],
        "forbidden": ["{", "}"],
    },
}

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _call_name_pairs(nodes, calls):
    """Translate map_graph's (caller_key, callee_name) into readable (caller_name, callee_name)."""
    key_to_name = {n["key"]: n["name"] for n in nodes}
    return {(key_to_name.get(ck, ck), callee) for ck, callee in calls}


def gate_language(lang: str, spec: dict) -> None:
    print(f"\n-- {lang} --")
    path = FIXDIR / f"{lang}.json"
    check(f"{lang}: fixture exists", path.exists(), str(path))
    if not path.exists():
        return
    graph = json.loads(path.read_text(encoding="utf-8"))
    nodes, calls, imports = G.map_graph(graph)
    names = {n["name"] for n in nodes}
    name_to_label = {}
    for n in nodes:                      # a file-Module + a class can share a bare name (Java Db) —
        name_to_label.setdefault(n["name"], set()).add(n["label"])  # keep the SET of labels per name

    check(f"{lang}: produced typed nodes", len(nodes) > 0, f"{len(nodes)} nodes")
    check(f"{lang}: every label is a valid DeepMap label",
          all(n["label"] in VALID_LABELS for n in nodes), str({n["label"] for n in nodes}))

    # No junk names (the core multi-language hardening invariant).
    junk = [n["name"] for n in nodes
            if any(c in n["name"] for c in spec["forbidden"]) or any(ch.isspace() for ch in n["name"])]
    check(f"{lang}: no junk / import-artifact nodes leaked", not junk, f"junk={junk}")

    # Every expected (name, label) is present.
    for name, want in spec["name_labels"]:
        check(f"{lang}: {name!r} -> {want}", want in name_to_label.get(name, set()),
              f"got {sorted(name_to_label.get(name, set()))}")

    # Labels that must be ABSENT (regression-lock a documented extraction limitation, e.g. CommonJS
    # JS yields 0 :Method — see js_commonjs). Present labels for context on failure.
    present_labels = {n["label"] for n in nodes}
    for lab in spec.get("absent_labels", []):
        check(f"{lang}: NO :{lab} nodes (documented limitation)", lab not in present_labels,
              f"present labels={sorted(present_labels)}")

    # Key CALLS edges resolve (by readable name).
    pairs = _call_name_pairs(nodes, calls)
    for caller, callee in spec["calls"]:
        check(f"{lang}: CALLS {caller} -> {callee}", (caller, callee) in pairs,
              f"resolved pairs: {sorted(pairs)}")

    # Line numbers parsed (no entity stuck at the default line 1 unless it truly is line 1).
    check(f"{lang}: >=1 entity has a start_line > 1 (source_location parsed)",
          any(n["start_line"] > 1 for n in nodes),
          str([(n["name"], n["start_line"]) for n in nodes]))


def gate_plausible_name_unit() -> None:
    """The junk filter is load-bearing: it must reject import/destructuring artifacts and keep real
    cross-language identifiers (dotted, underscored, $-prefixed, CamelCase)."""
    print("\n-- _plausible_name unit (junk filter is load-bearing) --")
    for junk in ("{ gamma }", "{ a, b }", "", "  ", "<lambda>", "[x]", "a=b", "x;y"):
        check(f"rejects {junk!r}", G._plausible_name(G._bare(junk)) is False)
    for ok in ("checkout", "place_order", "OrderService", "PlaceOrder", "_private", "$scope",
               "camelCase", "a1_b2"):
        check(f"keeps {ok!r}", G._plausible_name(G._bare(ok)) is True)
    # end-to-end: a graph with ONLY a destructuring-import node maps to nothing (no bogus Class)
    only_junk = {"nodes": [{"id": "n", "label": "{ x }", "file_type": "code",
                            "source_file": "m.ts", "source_location": "L1"}], "links": []}
    check("a lone `{ x }` destructuring node -> 0 typed nodes (not a bogus Class)",
          G.map_graph(only_junk)[0] == [])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — graphify MULTI-LANGUAGE typed-schema gates (no Neo4j; real captured graphs)")
    print("=" * 72)
    for lang, spec in EXPECT.items():
        gate_language(lang, spec)
    gate_plausible_name_unit()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
