#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend DETERMINISTIC FUZZ of the RM-1 offline pipeline on MALFORMED logs.

A real `.deepmap/retrieval_log.jsonl` is untrusted input: it can be hand-edited, half-written
(a torn last line), or corrupted. The whole offline chain — read_records → fold_access_events →
build_qrels → tune — must DEGRADE (skip garbage, return well-formed output), never raise. This
gate throws hundreds of seeded malformed logs at the chain and asserts:

  * read_records NEVER raises and returns a list[dict],
  * fold_access_events NEVER raises and returns search dicts whose accessed_ids is a list,
  * build_qrels NEVER raises and returns a list of well-formed qrel dicts,
  * tune NEVER raises and returns a valid result (3 arm weights, applied:false, a known reason).

Seeded PRNG (no wall-clock / entropy) so it is reproducible. Pairs with PLAYBOOK #35 (fuzz the
code that parses untrusted output) and the scorer fuzz.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0
ITERATIONS = 300
ARMS = ("vector", "bm25", "graph_weighted")


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
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


_GARBAGE = [
    None, 123, "a string", [], {}, {"type": "search"}, {"type": "access"},
    {"type": "search", "per_arm_candidate_ids": "not a dict", "returned": "nope", "accessed_ids": "x"},
    {"type": "search", "per_arm_candidate_ids": {"vector": "x"}, "returned": [123, None], "accessed_ids": None},
    {"type": "access", "paths": "not-a-list", "symbols": 5},
    {"type": "access", "paths": [None, 7, "a.py"], "symbols": [None]},
    {"type": "search", "returned": [{"id": None}], "accessed_ids": ["ghost"]},
    {"type": "weird", "stuff": True},
    # v2 + wrong-typed extension fields (kind/session_id/features/graded/negatives).
    {"type": "access", "v": 2, "paths": ["a.py"], "symbols": [], "kind": 123, "session_id": []},
    {"type": "access", "v": 2, "paths": ["a.py"], "kind": {"x": 1}, "session_id": 5},
    {"type": "search", "v": 2, "session_id": [], "features": "not a dict",
     "per_arm_candidate_ids": {"vector": ["id1"]},
     "returned": [{"id": "id1", "file": "f/id1.py", "name": "id1", "line": 1}], "accessed_ids": ["id1"]},
    {"type": "search", "v": 2, "features": ["nope"], "graded": "x", "negatives": 7,
     "per_arm_candidate_ids": {"vector": 3}, "returned": [{"id": "id2"}], "accessed_ids": ["id2"]},
]


def _rand_record(rng):
    """A random record: sometimes valid, sometimes malformed, sometimes garbage."""
    roll = rng.random()
    if roll < 0.35:  # a plausibly-valid search
        ids = [f"id{rng.randint(0, 5)}" for _ in range(rng.randint(0, 4))]
        per_arm = {a: [f"id{rng.randint(0, 5)}" for _ in range(rng.randint(0, 3))] for a in ARMS}
        returned = [{"id": i, "file": f"f/{i}.py", "name": i, "line": rng.randint(1, 99)} for i in ids]
        rec = {"type": "search", "v": 1, "qid": f"q{rng.randint(0, 9999)}",
               "query": "x" * rng.randint(0, 5), "per_arm_candidate_ids": per_arm,
               "returned": returned, "accessed_ids": (ids[:1] if ids and rng.random() < 0.6 else [])}
        return rec
    if roll < 0.6:  # a plausibly-valid access (sometimes v2 with a graded kind + session_id)
        ev = {"type": "access", "ts": str(rng.randint(0, 99)),
              "paths": [f"f/id{rng.randint(0, 5)}.py"] if rng.random() < 0.7 else [],
              "symbols": [f"id{rng.randint(0, 5)}"] if rng.random() < 0.3 else []}
        if rng.random() < 0.5:
            ev["v"] = 2
            ev["kind"] = rng.choice(["read", "edit", "impact", "write", ""])
            ev["session_id"] = rng.choice([None, "s1", "s2"])
        return ev
    return rng.choice(_GARBAGE)  # garbage


def _write_log(L, root, records, rng):
    """Write records as JSONL — with some raw garbage/torn lines interleaved."""
    lp = L.log_path(root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in records:
        try:
            lines.append(json.dumps(r))
        except (TypeError, ValueError):
            lines.append("{unserializable}")
        if rng.random() < 0.15:
            lines.append(rng.choice(["", "   ", "{not json", "]["]))  # blank / torn / garbage
    lp.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _valid_weights(w) -> bool:
    return (isinstance(w, dict) and set(w) == set(ARMS)
            and all(isinstance(w[a], (int, float)) and w[a] >= 0 for a in ARMS))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print(f"DeepMap — RM-1 offline-pipeline fuzz ({ITERATIONS} seeded malformed logs; no Neo4j)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    T = _load("tune_weights", BUILD)

    read_crashes = fold_crashes = qrel_crashes = tune_crashes = 0
    graded_crashes = 0
    shape_violations = 0
    rng = random.Random(20260701)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for _ in range(ITERATIONS):
            n = rng.randint(0, 25)
            records = [_rand_record(rng) for _ in range(n)]
            _write_log(L, root, records, rng)

            try:
                recs = L.read_records(root)
                if not isinstance(recs, list) or not all(isinstance(r, dict) for r in recs):
                    shape_violations += 1
            except Exception:
                read_crashes += 1
                recs = []

            try:
                folded = L.fold_access_events(recs)
                if not (isinstance(folded, list)
                        and all(isinstance(r.get("accessed_ids"), list) for r in folded)):
                    shape_violations += 1
            except Exception:
                fold_crashes += 1

            # The GRADED fold (RM-1 extension) must also degrade, never raise, and emit graded qrels
            # ({id:weight}) + a negatives list on every search copy.
            try:
                gfolded = L.fold_graded(records)
                if not (isinstance(gfolded, list)
                        and all(isinstance(r.get("accessed_ids"), list)
                                and isinstance(r.get("graded"), dict)
                                and all(isinstance(v, (int, float)) for v in r.get("graded", {}).values())
                                and isinstance(r.get("negatives"), list) for r in gfolded)):
                    shape_violations += 1
            except Exception:
                graded_crashes += 1

            # Also fuzz the pure functions directly with in-memory garbage (not just file-round-tripped).
            try:
                q = T.build_qrels(records)
                if not (isinstance(q, list) and all(isinstance(x.get("positives"), set) for x in q)):
                    shape_violations += 1
            except Exception:
                qrel_crashes += 1

            try:
                res = T.tune(records=records, min_labeled=rng.randint(0, 5))
                if not (_valid_weights(res.get("arm_weights")) and res.get("applied") is False
                        and isinstance(res.get("reason"), str)):
                    shape_violations += 1
            except Exception:
                tune_crashes += 1

    check(f"read_records never raised across {ITERATIONS} malformed logs", read_crashes == 0, f"{read_crashes} crashes")
    check(f"fold_access_events never raised", fold_crashes == 0, f"{fold_crashes} crashes")
    check(f"fold_graded never raised (graded qrels + negatives always well-formed)", graded_crashes == 0, f"{graded_crashes} crashes")
    check(f"build_qrels never raised", qrel_crashes == 0, f"{qrel_crashes} crashes")
    check(f"tune never raised (always equal/valid weights, applied:false)", tune_crashes == 0, f"{tune_crashes} crashes")
    check("every output was well-formed (no shape violations)", shape_violations == 0, f"{shape_violations} violations")

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
