#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for opt-in precise CALLS resolution (P0-1, Rails field-review; DEEPMAP_PRECISE_CALLS).

The field bug: on the graphify path, `map_graph` DISCARDS graphify's already-resolved call target id
and keeps only the NAME; `push_to_neo4j` re-resolves that name against a GLOBAL index and fans the
call out to EVERY same-named node — 82.9% AMBIGUOUS, 3.25x edge inflation. The fix (opt-in, default
OFF) keeps the resolved target KEY so push makes ONE firm `:CALLS{confidence:'EXTRACTED'}` edge.

These gates lock, on a graph with ONE resolved call to a target whose NAME also matches a second node:
  * flag OFF: map_graph emits the NAME tuple (byte-identical to today) and push FANS OUT to BOTH
    same-named nodes as AMBIGUOUS — the pre-fix behaviour;
  * flag ON: map_graph emits the target KEY and push makes exactly ONE edge to the graphify-resolved
    node (no fan-out). F3b: its confidence is EXTRACTED only when caller+callee are in the SAME file
    (a literal same-file relationship — edge_resolve's cross-file-never-EXTRACTED principle); a
    graphify-resolved CROSS-FILE call is a firm binding but not a same-file literal -> INFERRED;
  * the ast path is unaffected (a bare-name callee never equals a node key, so the precise branch
    can't fire even with the flag on).
Also (F8b, default OFF): DEEPMAP_DROP_APP_TO_TEST_CALLS drops a CALLS edge from a NON-test caller to a
callee resolved under a test dir (the graphify app->test-fixture misbinding), keeping test nodes
searchable while stripping the polluting cross-boundary edge.
Pure + hermetic (map_graph is pure; a Cypher-capturing fake driver for push). No Neo4j, no spend.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

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


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# A caller `foo` calling `bar`, with TWO nodes named `bar` (the same-name collision that fans out).
# graphify RESOLVED the call to b1_bar (the link target).
GRAPH = {
    "nodes": [
        {"id": "a_foo", "label": "foo()", "file_type": "code", "source_file": "a.py", "source_location": "L1"},
        {"id": "b1_bar", "label": "bar()", "file_type": "code", "source_file": "b1.py", "source_location": "L2"},
        {"id": "b2_bar", "label": "bar()", "file_type": "code", "source_file": "b2.py", "source_location": "L3"},
    ],
    "links": [{"source": "a_foo", "target": "b1_bar", "relation": "calls"}],
}


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one entry per row
            self.drv.log.append((cypher, row))
        return []

    def begin_transaction(self):           # the edge rebuild runs inside a write transaction
        return self

    def commit(self):
        pass


class _Driver:
    def __init__(self):
        self.log = []

    def session(self):
        return _Session(self)


def _calls_merged(drv):
    """[(a, b, confidence)] for every CALLS MERGE the push emitted."""
    out = []
    for c, p in drv.log:
        if "MERGE (a)-[r:CALLS]->(b)" in c:
            out.append((p.get("a"), p.get("b"), p.get("c", "EXTRACTED")))
    return out


def _env(val):
    if val is None:
        os.environ.pop("DEEPMAP_PRECISE_CALLS", None)
    else:
        os.environ["DEEPMAP_PRECISE_CALLS"] = val


def gate_off(G, S) -> None:
    print("\n-- flag OFF: name tuple + fan-out AMBIGUOUS (byte-identical to today) --")
    saved = os.environ.get("DEEPMAP_PRECISE_CALLS")
    try:
        _env(None)
        nodes, calls, imports = G.map_graph(GRAPH)
        check("map_graph emits the NAME tuple (a_foo -> 'bar')", calls == [("a_foo", "bar")], str(calls))
        drv = _Driver()
        S.push_to_neo4j(drv, nodes, calls, imports)
        merged = _calls_merged(drv)
        dsts = {b for _a, b, _c in merged}
        check("push FANS the call out to BOTH same-named nodes", dsts == {"b1_bar", "b2_bar"}, str(merged))
        check("both fan-out edges are AMBIGUOUS (2 candidates)",
              all(c == "AMBIGUOUS" for _a, _b, c in merged), str(merged))
    finally:
        _env(saved)


def gate_on(G, S) -> None:
    print("\n-- flag ON: resolved target KEY -> ONE firm EXTRACTED edge, no fan-out --")
    saved = os.environ.get("DEEPMAP_PRECISE_CALLS")
    try:
        _env("1")
        nodes, calls, imports = G.map_graph(GRAPH)
        check("map_graph emits the resolved KEY tuple (a_foo -> 'b1_bar')",
              calls == [("a_foo", "b1_bar")], str(calls))
        drv = _Driver()
        S.push_to_neo4j(drv, nodes, calls, imports)
        merged = _calls_merged(drv)
        check("push makes EXACTLY ONE :CALLS edge (no fan-out)", len(merged) == 1, str(merged))
        check("the single edge targets the graphify-resolved node b1_bar (not b2_bar)",
              merged and merged[0][1] == "b1_bar", str(merged))
        # F3b: a.py foo -> b1.py bar is a CROSS-FILE resolved call -> INFERRED (a firm binding, but NOT
        # a literal same-file relationship; EXTRACTED is reserved for same-file, matching edge_resolve).
        check("the precise CROSS-FILE edge is confidence INFERRED (not the mis-stamped EXTRACTED)",
              merged and merged[0][2] == "INFERRED", str(merged))
    finally:
        _env(saved)


# A caller and its graphify-RESOLVED callee in the SAME file -> a literal same-file relationship.
GRAPH_SAME_FILE = {
    "nodes": [
        {"id": "a_foo", "label": "foo()", "file_type": "code", "source_file": "a.py", "source_location": "L1"},
        {"id": "a_helper", "label": "helper()", "file_type": "code", "source_file": "a.py", "source_location": "L2"},
    ],
    "links": [{"source": "a_foo", "target": "a_helper", "relation": "calls"}],
}


def gate_on_same_file(G, S) -> None:
    print("\n-- flag ON, SAME-FILE resolved call: ONE edge, confidence EXTRACTED (a same-file literal) --")
    saved = os.environ.get("DEEPMAP_PRECISE_CALLS")
    try:
        _env("1")
        nodes, calls, imports = G.map_graph(GRAPH_SAME_FILE)
        check("map_graph emits the resolved KEY tuple (a_foo -> 'a_helper')",
              calls == [("a_foo", "a_helper")], str(calls))
        drv = _Driver()
        S.push_to_neo4j(drv, nodes, calls, imports)
        merged = _calls_merged(drv)
        check("push makes EXACTLY ONE edge to a_helper", len(merged) == 1 and merged[0][1] == "a_helper",
              str(merged))
        check("F3b: a SAME-FILE resolved call IS EXTRACTED (the firmest tier, correctly)",
              merged and merged[0][2] == "EXTRACTED", str(merged))
    finally:
        _env(saved)


def gate_app_to_test_demote(S) -> None:
    print("\n-- F8b (default OFF): DEEPMAP_DROP_APP_TO_TEST_CALLS drops NON-test-caller -> test-callee edges --")
    saved = os.environ.get("DEEPMAP_DROP_APP_TO_TEST_CALLS")
    savedp = os.environ.get("DEEPMAP_PRECISE_CALLS")
    APP = {"key": "app/svc.py::process:1", "label": "Function", "name": "process",
           "source_file": "app/svc.py", "start_line": 1, "body_summary": "p"}
    TEST_HELPER = {"key": "test/svc_test.py::helper:2", "label": "Function", "name": "helper",
                   "source_file": "test/svc_test.py", "start_line": 2, "body_summary": "h"}
    TEST_CALLER = {"key": "test/svc_test.py::tcaller:3", "label": "Function", "name": "tcaller",
                   "source_file": "test/svc_test.py", "start_line": 3, "body_summary": "c"}
    try:
        _env(None)  # DEEPMAP_PRECISE_CALLS off (default name-resolution path is where the misbind lives)
        # (a) default OFF: the app->test edge is created (byte-identical to today).
        os.environ.pop("DEEPMAP_DROP_APP_TO_TEST_CALLS", None)
        drv = _Driver()
        S.push_to_neo4j(drv, [APP, TEST_HELPER], [("app/svc.py::process:1", "helper")], [])
        dsts = {b for _a, b, _c in _calls_merged(drv)}
        check("default OFF: app->test CALLS edge is created (no default-graph change)",
              "test/svc_test.py::helper:2" in dsts, str(dsts))
        # (b) flag ON: the app->test edge is DROPPED.
        os.environ["DEEPMAP_DROP_APP_TO_TEST_CALLS"] = "1"
        drv = _Driver()
        S.push_to_neo4j(drv, [APP, TEST_HELPER], [("app/svc.py::process:1", "helper")], [])
        dsts = {b for _a, b, _c in _calls_merged(drv)}
        check("flag ON: the NON-test-caller -> test-callee edge is DROPPED",
              "test/svc_test.py::helper:2" not in dsts, str(dsts))
        # (c) a test->test call is KEPT even with the flag on (only cross-boundary app->test drops).
        drv = _Driver()
        S.push_to_neo4j(drv, [TEST_CALLER, TEST_HELPER], [("test/svc_test.py::tcaller:3", "helper")], [])
        dsts = {b for _a, b, _c in _calls_merged(drv)}
        check("flag ON: a test->test CALLS edge is KEPT (only app->test is demoted)",
              "test/svc_test.py::helper:2" in dsts, str(dsts))
    finally:
        _env(savedp)
        if saved is None:
            os.environ.pop("DEEPMAP_DROP_APP_TO_TEST_CALLS", None)
        else:
            os.environ["DEEPMAP_DROP_APP_TO_TEST_CALLS"] = saved


def gate_ast_unaffected(S) -> None:
    print("\n-- ast path: a bare-name callee never equals a node key -> precise branch can't fire --")
    saved = os.environ.get("DEEPMAP_PRECISE_CALLS")
    try:
        _env("1")
        # ast-shaped nodes with relpath::name:line keys; a name-only call tuple.
        nodes = [{"key": "a.py::foo:1", "label": "Function", "name": "foo", "source_file": "a.py",
                  "start_line": 1, "body_summary": "foo"},
                 {"key": "b1.py::bar:2", "label": "Function", "name": "bar", "source_file": "b1.py",
                  "start_line": 2, "body_summary": "bar"},
                 {"key": "b2.py::bar:3", "label": "Function", "name": "bar", "source_file": "b2.py",
                  "start_line": 3, "body_summary": "bar"}]
        drv = _Driver()
        S.push_to_neo4j(drv, nodes, [("a.py::foo:1", "bar")], [])
        merged = _calls_merged(drv)
        dsts = {b for _a, b, _c in merged}
        check("even with the flag ON, a NAME callee still fans out (ast path unchanged)",
              dsts == {"b1.py::bar:2", "b2.py::bar:3"}
              and all(c == "AMBIGUOUS" for _a, _b, c in merged), str(merged))
    finally:
        _env(saved)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — precise CALLS resolution gates (P0-1, no Neo4j)")
    print("=" * 64)
    G = _load("graphify_extract", "graphify_extract.py")
    S = _load("sync", "sync.py")
    gate_off(G, S)
    gate_on(G, S)
    gate_on_same_file(G, S)
    gate_ast_unaffected(S)
    gate_app_to_test_demote(S)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
