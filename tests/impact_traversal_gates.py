#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for Track C — multi-hop impact traversal (build/retrieval.query_impact).

Behavioral (fake driver records the emitted Cypher + params, returns canned rows):
  * multi-hop across :CALLS + runtime edges, each row labeled hop/via/via_path;
  * degrade-to-CALLS when only :CALLS is present (a pre-Track-C graph) — never errors;
  * injection-safety (symbol bound as $name; rel-type list sanitized to [A-Z_]) + hop clamp;
  * never-error posture: a benign feature ClientError falls back to one-hop query_callers; an
    instance-down error fails loud.
FREEZE proof (source contract): query_callers is byte-intact; no ranking arm/_weighted_rrf change;
query_impact reaches into no ranking symbol; retrieval.py holds no learned-fusion coupling.
"""
from __future__ import annotations

import importlib.util
import inspect
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


# ---- fake driver: records Cypher, returns canned rows, can inject errors -----
class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.log.append((cypher, p))
        if "db.relationshipTypes" in cypher:
            if self.drv.probe_error is not None:
                raise self.drv.probe_error
            return [{"types": list(self.drv.present)}]
        if "MATCH path = (caller)" in cypher:            # the multi-hop impact query
            if self.drv.impact_error is not None:
                raise self.drv.impact_error
            return [dict(r) for r in self.drv.impact_rows]
        if "MATCH (caller)-[r:CALLS]->(callee)" in cypher:   # query_callers (one-hop fallback)
            return [dict(r) for r in self.drv.callers_rows]
        return []                                        # SEARCH-clause probe etc.


class _Driver:
    def __init__(self, present=("CALLS",), impact_rows=(), callers_rows=(),
                 impact_error=None, probe_error=None):
        self.present = present
        self.impact_rows = impact_rows
        self.callers_rows = callers_rows
        self.impact_error = impact_error
        self.probe_error = probe_error
        self.log = []

    def session(self):
        return _Session(self)


def _main_cypher(drv):
    for c, _ in drv.log:
        if "MATCH path = (caller)" in c:
            return c
    return ""


def gate_multihop_labeling(R) -> None:
    print("\n-- multi-hop traversal across CALLS + runtime edges, hop/via/via_path labeled --")
    rows = [
        {"caller": "app", "caller_type": "Module", "file": "a.py", "line": 1, "confidence": "EXTRACTED",
         "callee": "handler", "hop": 1, "via": "ROUTES_TO", "via_path": ["ROUTES_TO"]},
        {"caller": "svc", "caller_type": "Function", "file": "b.py", "line": 9, "confidence": "INFERRED",
         "callee": "handler", "hop": 2, "via": "CALLS", "via_path": ["CALLS", "ROUTES_TO"]},
    ]
    drv = _Driver(present=("CALLS", "ROUTES_TO", "DEPENDS_ON"), impact_rows=rows)
    out = R.query_impact(drv, "handler")
    cy = _main_cypher(drv)
    check("rel pattern filtered to present ∩ requested (CALLS|ROUTES_TO|DEPENDS_ON)",
          "rels:CALLS|ROUTES_TO|DEPENDS_ON*1..3" in cy, cy)
    check("returns rows with additive hop/via/via_path",
          out and all(k in out[0] for k in ("hop", "via", "via_path")), str(out[:1]))
    check("a 2-hop dependent is surfaced", any(r["hop"] == 2 for r in out), str(out))
    check("via records the incident edge type", out[0]["via"] == "ROUTES_TO", str(out[0]))


def gate_degrade_calls_only(R) -> None:
    print("\n-- degrade-to-CALLS on a pre-Track-C graph (only :CALLS present) --")
    drv = _Driver(present=("CALLS",), impact_rows=[
        {"caller": "x", "caller_type": "Function", "file": "a.py", "line": 1, "confidence": "INFERRED",
         "callee": "y", "hop": 1, "via": "CALLS", "via_path": ["CALLS"]}])
    out = R.query_impact(drv, "y")
    cy = _main_cypher(drv)
    check("rel pattern collapses to CALLS-only", "rels:CALLS*1..3" in cy, cy)
    check("no other edge type leaks into the pattern",
          "ROUTES_TO" not in cy and "INHERITS" not in cy, cy)
    check("still returns the one-hop CALLS result (never errors)", len(out) == 1, str(out))


def gate_probe_failure_uses_requested(R) -> None:
    print("\n-- db.relationshipTypes() probe failure -> proceed with the requested set --")
    drv = _Driver(present=(), impact_rows=[], probe_error=RuntimeError("no such procedure"))
    R.query_impact(drv, "z")
    cy = _main_cypher(drv)
    # SANCTIONED EXTENSIONS (review-locked): the default set may grow ONLY by genuine, benchmark-neutral
    # reachability edges — CASCADES (Rails/EF blast-radius) and RENDERS (Rails view->partial, P1-3; a NEW
    # edge absent from every benchmark graph, so it moves no measured number). The benchmark-comparability
    # holdouts HAS_MANY/REFERENCES/IMPLEMENTS/DISPATCHES/IMPORTS stay non-traversable by default (opt-in
    # via DEEPMAP_IMPACT_PROFILE=rails / DEEPMAP_IMPACT_EDGES).
    check("probe failure does not crash; pattern uses the default requested types",
          "rels:CALLS|ROUTES_TO|DEPENDS_ON|TRIGGERS|SCHEDULED_AS|HOOKS|INHERITS|CASCADES|RENDERS*1..3" in cy, cy)
    check("no benchmark-gated type slipped into the default set",
          all(t not in cy for t in ("HAS_MANY", "REFERENCES", "IMPLEMENTS", "DISPATCHES", "IMPORTS")), cy)


def gate_binding_and_injection(R) -> None:
    print("\n-- injection-safety: symbol bound as $name; rel-type list sanitized --")
    drv = _Driver(present=None if False else ("CALLS", "OK_TYPE"), impact_rows=[])
    # present includes a benign custom type; pass a hostile edge_types list.
    R.query_impact(drv, ".pay", edge_types=["CALLS", "OK_TYPE", "DROP TABLE", "bad-type", "x;y"])
    cy = _main_cypher(drv)
    params = next((p for c, p in drv.log if "MATCH path = (caller)" in c), {})
    check("symbol passed as bound $name (leading dot stripped)", params.get("name") == "pay", str(params))
    check("symbol value NOT embedded in the Cypher string", "pay" not in cy, cy)
    check("hostile rel tokens sanitized out (only CALLS|OK_TYPE survive)",
          "rels:CALLS|OK_TYPE*1..3" in cy and "DROP" not in cy and ";" not in cy, cy)
    # direct sanitizer + clamp contract
    check("_sanitize_rel_types keeps [A-Z_], dedups, CALLS-first",
          R._sanitize_rel_types(["ROUTES_TO", "calls", "bad-x", "OK_TYPE", "DROP TABLE"]) ==
          ["CALLS", "ROUTES_TO", "OK_TYPE"])
    check("max_hops clamps high->cap(5)", R._resolve_impact_params(None, 99)[1] == 5)
    check("max_hops clamps low->1", R._resolve_impact_params(None, 0)[1] == 1)


def gate_never_error(R) -> None:
    print("\n-- never-error posture: benign ClientError degrades, instance-down fails loud --")
    from neo4j.exceptions import ClientError, ServiceUnavailable
    callers = [{"caller": "c", "caller_type": "Function", "file": "a.py", "line": 3,
                "confidence": "INFERRED", "callee": "t"}]
    drv = _Driver(present=("CALLS", "ROUTES_TO"), callers_rows=callers,
                  impact_error=ClientError("SyntaxError: variable-length not supported"))
    out = R.query_impact(drv, "t")
    check("benign feature ClientError -> falls back to one-hop query_callers",
          out == callers, str(out))
    check("fallback actually issued the query_callers one-hop Cypher",
          any("MATCH (caller)-[r:CALLS]->(callee)" in c for c, _ in drv.log))

    drv2 = _Driver(present=("CALLS",), impact_error=ServiceUnavailable("unable to connect to server"))
    raised = False
    try:
        R.query_impact(drv2, "t")
    except ServiceUnavailable:
        raised = True
    check("instance-down (ServiceUnavailable) fails LOUD (never a fabricated empty)", raised)


def gate_impact_delegation(R) -> None:
    print("\n-- DeepMapRetriever.impact() delegates to the multi-hop query_impact --")
    rows = [{"caller": "app", "caller_type": "Module", "file": "a.py", "line": 1,
             "confidence": "EXTRACTED", "callee": "h", "hop": 1, "via": "ROUTES_TO",
             "via_path": ["ROUTES_TO"]}]
    drv = _Driver(present=("CALLS", "ROUTES_TO"), impact_rows=rows)
    out = R.DeepMapRetriever(drv, embed_fn=lambda q: [0.0] * 768).impact("h")
    check("impact() returns multi-hop rows (hop/via present)",
          out and "via" in out[0] and out[0]["via"] == "ROUTES_TO", str(out[:1]))
    check("impact() went through the multi-hop query_impact path", bool(_main_cypher(drv)))


def gate_freeze_and_ranking(R) -> None:
    print("\n-- FREEZE: query_callers byte-intact, no ranking change, no learned-fusion coupling --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    # query_callers one-hop CALLS Cypher unchanged (the pin for the no-regression gate)
    check("query_callers one-hop :CALLS MATCH intact",
          "MATCH (caller)-[r:CALLS]->(callee) " in src)
    check("query_callers confidence coalesce intact", "coalesce(r.confidence, 'INFERRED')" in src)
    check("query_callers trust-first order intact",
          "ORDER BY CASE confidence WHEN 'EXTRACTED' THEN 0 WHEN 'INFERRED' THEN 1 ELSE 2 END, "
          "\n            caller LIMIT $limit" in src.replace("\r\n", "\n") or
          "WHEN 'INFERRED' THEN 1 ELSE 2 END, " in src)
    # ranking fusion + arms untouched
    check("Weighted-RRF formula unchanged", "w * (1.0 / (k + rank))" in src)
    check("VECTOR_INDEXES still the 5 searched labels",
          all(f'"{lbl}"' in src for lbl in ("Function", "Method", "Class", "Module", "Route"))
          and src.count("_embeddings\")") >= 5)
    check("DEFAULT_ARM_WEIGHTS still equal (non-regressing default)",
          '"vector": 1.0' in src and '"bm25": 1.0' in src and '"graph_weighted": 1.0' in src)
    # query_impact must not reach into ranking
    qi = inspect.getsource(R.query_impact)
    check("query_impact references no ranking symbol (_arm_/_weighted_rrf/arm_weights)",
          "_arm_" not in qi and "_weighted_rrf" not in qi and "arm_weights" not in qi)
    # learned-fusion freeze (defense in depth; also locked by learned_fusion_wiring_gates)
    check("retrieval.py holds no learned-fusion coupling",
          "learned_fusion" not in src and "instance.json" not in src and "DEEPMAP_APPLY_LEARNED" not in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — impact traversal gates (Track C; no Neo4j, no spend)")
    print("=" * 64)

    R = _load("dm_retrieval_impact", "retrieval.py")

    gate_multihop_labeling(R)
    gate_degrade_calls_only(R)
    gate_probe_failure_uses_requested(R)
    gate_binding_and_injection(R)
    gate_never_error(R)
    gate_impact_delegation(R)
    gate_freeze_and_ranking(R)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
