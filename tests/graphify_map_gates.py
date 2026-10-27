#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for build/graphify_extract.map_graph (Phase 3b).

Feeds a CAPTURED real graphify `graph.json` (from `graphify update --no-cluster` on a 2-file
sample) through the pure mapper and locks the loose->typed inference: Module/Class/Method/Function
from label+edge shape, rationale nodes folded into body_summary, and CALLS/IMPORTS edges. Pure:
no graphify run, no Neo4j, no network — safe in CI even where graphify isn't installed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))
import graphify_extract as G  # noqa: E402

# --- real graphify graph.json (captured 2026-06-28; orders.py + api.py sample) ---------------
FIXTURE = {
    "nodes": [
        {"id": "api", "label": "api.py", "file_type": "code", "source_file": "api.py", "source_location": "L1", "_origin": "ast"},
        {"id": "api_handle_checkout", "label": "handle_checkout()", "file_type": "code", "source_file": "api.py", "source_location": "L3", "_origin": "ast"},
        {"id": "orders", "label": "orders.py", "file_type": "code", "source_file": "orders.py", "source_location": "L1", "_origin": "ast"},
        {"id": "orders_orderservice", "label": "OrderService", "file_type": "code", "source_file": "orders.py", "source_location": "L3", "_origin": "ast"},
        {"id": "orders_orderservice_place_order", "label": ".place_order()", "file_type": "code", "source_file": "orders.py", "source_location": "L4", "_origin": "ast"},
        {"id": "orders_orderservice_compute_total", "label": "._compute_total()", "file_type": "code", "source_file": "orders.py", "source_location": "L8", "_origin": "ast"},
        {"id": "orders_charge", "label": "charge()", "file_type": "code", "source_file": "orders.py", "source_location": "L11", "_origin": "ast"},
        {"id": "orders_rationale_1", "label": "Order processing module.", "file_type": "rationale", "source_file": "orders.py", "source_location": "L1", "_origin": "ast"},
        # graphify tags JSON as file_type "code"; a JSON key must NOT become a Class node:
        {"id": "cfg_title", "label": "title", "file_type": "code", "source_file": "config.json", "source_location": "L2", "_origin": "ast"},
    ],
    "links": [
        {"source": "api", "target": "orders", "relation": "imports_from"},
        {"source": "api", "target": "api_handle_checkout", "relation": "contains"},
        {"source": "orders", "target": "orders_orderservice", "relation": "contains"},
        {"source": "orders_orderservice", "target": "orders_orderservice_place_order", "relation": "method"},
        {"source": "orders_orderservice", "target": "orders_orderservice_compute_total", "relation": "method"},
        {"source": "orders", "target": "orders_charge", "relation": "contains"},
        {"source": "orders_orderservice_place_order", "target": "orders_orderservice_compute_total", "relation": "calls"},
        {"source": "orders_orderservice_place_order", "target": "orders_charge", "relation": "calls"},
        {"source": "orders_rationale_1", "target": "orders", "relation": "rationale_for"},
        {"source": "api", "target": "orders_orderservice", "relation": "imports"},
        {"source": "api_handle_checkout", "target": "orders_orderservice", "relation": "calls"},
    ],
}

VALID_LABELS = {"Function", "Method", "Class", "Module", "Route"}

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


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — graphify graph.json -> typed schema gates (no Neo4j)")
    print("=" * 64)

    os.environ.pop("DEEPMAP_RESOLVED_IMPORTS", None)   # the default contract (hermetic vs the shell)
    nodes, calls, imports = G.map_graph(FIXTURE)
    by_key = {n["key"]: n for n in nodes}

    print("\n-- node typing --")
    check("rationale + JSON-key nodes dropped (9 raw -> 7 typed)", len(nodes) == 7, f"got {len(nodes)}")
    check("no rationale leaked as a node",
          all("Order processing" not in n["name"] for n in nodes))
    check("JSON-key node (file_type code, .json source) filtered out", "cfg_title" not in by_key)
    check("every label is a valid DeepMap label",
          all(n["label"] in VALID_LABELS for n in nodes),
          str({n["label"] for n in nodes}))
    expect_kind = {
        "api": "Module", "orders": "Module",
        "orders_orderservice": "Class",
        "orders_orderservice_place_order": "Method",
        "orders_orderservice_compute_total": "Method",
        "api_handle_checkout": "Function", "orders_charge": "Function",
    }
    for k, want in expect_kind.items():
        check(f"{k} -> {want}", by_key.get(k, {}).get("label") == want,
              f"got {by_key.get(k, {}).get('label')}")

    print("\n-- name composition --")
    check("module name is the stem ('orders', not 'orders.py')",
          by_key["orders"]["name"] == "orders", by_key["orders"]["name"])
    check("method name is Class.method", by_key["orders_orderservice_place_order"]["name"] == "OrderService.place_order",
          by_key["orders_orderservice_place_order"]["name"])
    check("function name strips ()", by_key["orders_charge"]["name"] == "charge")
    check("start_line parsed from 'L4'", by_key["orders_orderservice_place_order"]["start_line"] == 4)

    print("\n-- summaries --")
    check("rationale folds into module body_summary",
          by_key["orders"]["body_summary"] == "Order processing module.",
          by_key["orders"]["body_summary"])
    hc = by_key["api_handle_checkout"]["body_summary"]
    check("undocumented fn summary humanizes name", "handle checkout" in hc, hc)
    check("undocumented fn summary lists a graph callee", "order service" in hc, hc)

    print("\n-- edges --")
    check("CALLS: place_order -> charge", ("orders_orderservice_place_order", "charge") in calls,
          str(calls))
    check("CALLS: handle_checkout -> OrderService",
          ("api_handle_checkout", "OrderService") in calls, str(calls))
    check("CALLS count == 3", len(calls) == 3, str(calls))
    check("IMPORTS: api -> orders", ("api", "orders") in imports, str(imports))
    # Resolved-import-key contract (round 3, default ON): an import graphify resolved to a KEPT node
    # carries that node's key, so a consumer binds exactly it (not every same-named node).
    check("IMPORTS: api -> OrderService, as the engine-resolved node KEY",
          ("api", "orders_orderservice") in imports, str(imports))
    _saved = os.environ.get("DEEPMAP_RESOLVED_IMPORTS")
    os.environ["DEEPMAP_RESOLVED_IMPORTS"] = "0"
    try:
        _legacy_imports = G.map_graph(FIXTURE)[2]
    finally:
        if _saved is None:
            os.environ.pop("DEEPMAP_RESOLVED_IMPORTS", None)
        else:
            os.environ["DEEPMAP_RESOLVED_IMPORTS"] = _saved
    check("IMPORTS opt-out (DEEPMAP_RESOLVED_IMPORTS=0): api -> OrderService by NAME, as before",
          _legacy_imports == [("api", "orders"), ("api", "OrderService")], str(_legacy_imports))

    print("\n-- robustness --")
    check("empty graph -> ([],[],[])", G.map_graph({}) == ([], [], []))
    # a node with no file_type/source_file is not provably source code -> dropped, never crash
    check("node lacking file_type dropped (no crash)",
          G.map_graph({"nodes": [{"id": "x", "label": "x()"}], "links": []}) == ([], [], []))
    # a proper .py code node still maps
    check("valid .py code node maps to Function",
          G.map_graph({"nodes": [{"id": "y", "label": "y()", "file_type": "code", "source_file": "m.py", "source_location": "L1"}], "links": []})[0][0]["label"] == "Function")

    print("\n-- _line_of: L<n> marker, not a lowercase path word (bug-hunt #12) --")
    check("explicit L<n> marker is read", G._line_of({"source_location": "L42"}) == 42)
    check("L<n> after a path separator is read", G._line_of({"source_location": "src/api.py:L7"}) == 7)
    check("a lowercase 'l10n' path word is NOT mistaken for the line marker",
          G._line_of({"source_location": "src/l10n/api.py:5"}) == 5,
          str(G._line_of({"source_location": "src/l10n/api.py:5"})))
    check("no digits -> defaults to line 1", G._line_of({"source_location": "no-numbers-here"}) == 1)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
