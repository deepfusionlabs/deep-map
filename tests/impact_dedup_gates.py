#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the deepmap_impact visually-identical-row de-duplication in
`build/retrieval.py:query_impact`.

When a callee NAME resolves to several distinct nodes, query_impact projects BYTE-IDENTICAL rows
(same caller/file/line/confidence/callee/hop/via/via_path) — a display artifact, not distinct
information. The fix collapses rows whose ENTIRE projected dict is equal (order-preserving,
keep-first), on BOTH the multi-hop path and the one-hop degrade path — so no genuinely-distinct
dependent (a different callee/hop/route) is ever dropped from the CLI output or the MCP payload.

These gates lock:
  - exact-duplicate rows collapse (multi-hop path),
  - rows differing in ANY field (via_path / hop) are KEPT (proves it is not the lossy 4-key),
  - the degrade path (benign ClientError -> query_callers) is deduped too,
  - the freeze invariant: query_impact's own source still contains none of the ranking tokens.

Pure + hermetic: a fake driver/session returns scripted rows; no Neo4j.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

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


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(caller, callee, line, via, hop, via_path, confidence="INFERRED",
         file="a.py", caller_type="Function"):
    return {"caller": caller, "caller_type": caller_type, "file": file, "line": line,
            "confidence": confidence, "callee": callee, "hop": hop, "via": via,
            "via_path": list(via_path)}


class _Result:
    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class _Session:
    def __init__(self, main_rows, raise_multihop=False, exc=None):
        self.main_rows = main_rows
        self.raise_multihop = raise_multihop
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        if "db.relationshipTypes" in cypher:
            return _Result([{"types": ["CALLS"]}])
        if "rels:" in cypher or "*1.." in cypher:          # the multi-hop query
            if self.raise_multihop:
                raise self.exc
            return _Result(self.main_rows)
        return _Result(self.main_rows)                     # the one-hop query_callers query


class _Driver:
    def __init__(self, main_rows, raise_multihop=False, exc=None):
        self.main_rows = main_rows
        self.raise_multihop = raise_multihop
        self.exc = exc

    def session(self):
        return _Session(self.main_rows, self.raise_multihop, self.exc)


def gate_multihop_dedup(R) -> None:
    print("\n-- multi-hop: exact-duplicate rows collapse; distinct rows survive --")
    dup = _row("X", "foo", 10, "CALLS", 1, ["CALLS"])
    dup2 = _row("X", "foo", 10, "CALLS", 1, ["CALLS"])   # a distinct callee-node, identical projection
    distinct = _row("Y", "foo", 22, "CALLS", 1, ["CALLS"])
    rows = R.query_impact(_Driver([dup, dup2, distinct]), "foo", limit=30)
    check("byte-identical rows collapse (3 in -> 2 out)", len(rows) == 2, f"got {len(rows)}")
    check("keep-first order preserved (X before Y)",
          len(rows) == 2 and rows[0]["caller"] == "X" and rows[1]["caller"] == "Y",
          str([r.get("caller") for r in rows]))


def gate_no_over_collapse(R) -> None:
    print("\n-- rows differing in ANY field are KEPT (not the lossy 4-key) --")
    a = _row("X", "foo", 10, "CALLS", 1, ["CALLS"])
    b = _row("X", "foo", 10, "CALLS", 2, ["CALLS", "CALLS"])       # differs in hop + via_path
    c = _row("X", "foo", 10, "CALLS", 2, ["CALLS", "ROUTES_TO"])   # same hop/via, differs in via_path
    rows = R.query_impact(_Driver([a, b, c]), "foo", limit=30)
    check("same (caller,file,line,via) but different hop/via_path all kept (3 -> 3)",
          len(rows) == 3, f"got {len(rows)}")


def gate_degrade_path_dedup(R) -> None:
    print("\n-- degrade path (benign ClientError -> query_callers) is deduped too --")
    from neo4j.exceptions import ClientError
    dup = _row("X", "foo", 10, "CALLS", 1, ["CALLS"])
    dup2 = _row("X", "foo", 10, "CALLS", 1, ["CALLS"])
    distinct = _row("Z", "foo", 5, "CALLS", 1, ["CALLS"])
    drv = _Driver([dup, dup2, distinct], raise_multihop=True, exc=ClientError("benign feature error"))
    rows = R.query_impact(drv, "foo", limit=30)
    check("degrade path collapses byte-identical rows (3 -> 2)", len(rows) == 2, f"got {len(rows)}")


def gate_freeze_invariant(R) -> None:
    print("\n-- freeze: query_impact source still contains no ranking tokens --")
    src = inspect.getsource(R.query_impact)
    for tok in ("_arm_", "_weighted_rrf", "arm_weights"):
        check(f"query_impact source has no {tok!r}", tok not in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — deepmap_impact row de-duplication gates (no Neo4j)")
    print("=" * 64)
    R = _load("retrieval", BUILD)
    gate_multihop_dedup(R)
    gate_no_over_collapse(R)
    gate_degrade_path_dedup(R)
    gate_freeze_invariant(R)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
