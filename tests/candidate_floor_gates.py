#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the opt-in candidate-pool floor (P1-7, Rails field-review; DEEPMAP_CANDIDATE_FLOOR).

`fetch_k = top_k * 3` scales the per-arm candidate pool with top_k and has no floor, so a top-3 is not
a stable prefix of a top-8 (classic RRF prefix instability). The fix makes it
`fetch_k = max(top_k*3, _candidate_floor())`, gated on DEEPMAP_CANDIDATE_FLOOR (default 0 -> byte-
identical `top_k*3` ranking; the default flip is benchmark-gated).

These gates lock: (1) `_candidate_floor()` parses/clamps the env (default 0; negative/garbage -> 0);
(2) the arms receive fetch_k == top_k*3 when the floor is unset (byte-identical), == the floor when it
raises the pool, and top_k*3 when it doesn't. Behavioural (arms stubbed to record their k). No Neo4j.
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


def _set(val):
    if val is None:
        os.environ.pop("DEEPMAP_CANDIDATE_FLOOR", None)
    else:
        os.environ["DEEPMAP_CANDIDATE_FLOOR"] = val


def _recorded_k(R, top_k):
    """The fetch_k the arms actually receive for a given top_k (arms + hydrate stubbed)."""
    rec = {}
    ret = R.DeepMapRetriever(object(), embed_fn=lambda q: [0.1])
    ret._arm_vector = lambda qvec, k: (rec.__setitem__("vec", k), [])[1]
    ret._arm_fulltext = lambda query, k: (rec.__setitem__("fts", k), [])[1]
    ret._arm_graph_weighted = lambda qvec, k: (rec.__setitem__("graph", k), [])[1]
    ret._hydrate = lambda *a, **k: []
    ret.search("q", top_k=top_k)
    return rec


def gate_helper(R) -> None:
    print("\n-- _candidate_floor(): default 0, clamps negatives/garbage, never raises --")
    saved = os.environ.get("DEEPMAP_CANDIDATE_FLOOR")
    try:
        _set(None)
        check("unset -> 0 (byte-identical default)", R._candidate_floor() == 0)
        _set("100")
        check("'100' -> 100", R._candidate_floor() == 100)
        _set("-5")
        check("'-5' -> 0 (negative clamped)", R._candidate_floor() == 0)
        _set("abc")
        check("'abc' -> 0 (garbage, never raises)", R._candidate_floor() == 0)
    finally:
        _set(saved)


def gate_behavioural(R) -> None:
    print("\n-- fetch_k = max(top_k*3, floor): default byte-identical; floor lifts a small pool --")
    saved = os.environ.get("DEEPMAP_CANDIDATE_FLOOR")
    try:
        _set(None)
        rec = _recorded_k(R, 3)
        check("floor unset, top_k=3 -> arms get fetch_k==9 (top_k*3, byte-identical to today)",
              rec.get("vec") == 9 and rec.get("fts") == 9 and rec.get("graph") == 9, str(rec))
        _set("100")
        rec = _recorded_k(R, 3)
        check("floor=100, top_k=3 -> arms get fetch_k==100 (the pool is lifted; stable prefix)",
              rec.get("vec") == 100 and rec.get("fts") == 100 and rec.get("graph") == 100, str(rec))
        _set("5")
        rec = _recorded_k(R, 3)
        check("floor=5, top_k=3 -> fetch_k==9 (floor below top_k*3 -> no change)",
              rec.get("fts") == 9, str(rec))
        _set("100")
        rec = _recorded_k(R, 50)
        check("floor=100, top_k=50 -> fetch_k==150 (top_k*3 already above the floor)",
              rec.get("fts") == 150, str(rec))
    finally:
        _set(saved)


def gate_source() -> None:
    print("\n-- search() uses max(top_k*3, _candidate_floor()) --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("search computes fetch_k = max(top_k * 3, _candidate_floor())",
          "fetch_k = max(top_k * 3, _candidate_floor())" in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — candidate-pool floor gates (P1-7, no Neo4j)")
    print("=" * 64)
    R = _load("retrieval", "retrieval.py")
    gate_helper(R)
    gate_behavioural(R)
    gate_source()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
