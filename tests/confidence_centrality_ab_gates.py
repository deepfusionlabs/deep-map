#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/confidence_centrality_ab.py — the OFFLINE confidence-weighted
centrality EXPERIMENT (D3, Rails field-review P0-2). An offline variant of the graph arm's in-degree
that weights each :CALLS edge by its stored `confidence` (EXTRACTED/INFERRED/AMBIGUOUS), recomputed
against a live graph at RUN time through an INJECTED query fn and A/B'd by replaying the log's fusion.
retrieval.py is untouched: `VECTOR_INDEXES`/fusion are IMPORTED, the frozen `_arm_graph_weighted`
count(caller) Cypher is not edited, and nothing is wired live (experiments.confidence_centrality,
applied FORCED false).

Locks: the Cypher contract (gamma weights ALWAYS bound $gExt/$gInf/$gAmb, the `r IS NULL`->0 guard);
THE PARITY KEYSTONE gamma=(1,1,1) == count(caller); the replay substitutes ONLY the graph arm and
re-fuses with the real fusion (oracle-matched); verdicts; per-index isolation; persistence +
applied-false + live-resolver-blind; degrade-never-raise. Neo4j is needed at RUN time only — never here.
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
import tempfile
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
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _search(L, q, per_arm, ids, ts):
    returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ids]
    return L.build_record(q, per_arm, returned, ts=ts)


def _access(path, ts, kind="edit"):
    return {"type": "access", "ts": ts, "paths": [path], "symbols": [], "kind": kind}


def _fake_from_nodes(nodes):
    """A hermetic query_fn over [{id, raw, ext, inf, amb}] that mimics the two Cypher shapes: the
    count(caller) baseline (in-degree = ext+inf+amb) and the confidence-weighted variant (in-degree =
    gExt*ext + gInf*inf + gAmb*amb, weights read from the bound params)."""
    calls_log = []

    def fn(cypher, params):
        calls_log.append((cypher, dict(params)))
        weighted = "$gExt" in cypher or "r IS NULL" in cypher
        rows = []
        for n in nodes:
            if weighted:
                gE = float(params.get("gExt", 1.0)); gI = float(params.get("gInf", 1.0))
                gA = float(params.get("gAmb", 1.0))
                indeg = gE * n["ext"] + gI * n["inf"] + gA * n["amb"]
            else:
                indeg = n["ext"] + n["inf"] + n["amb"]
            rows.append({"id": n["id"], "score": n["raw"] * (1.0 + math.log(1.0 + indeg))})
        return rows

    fn.calls_log = calls_log
    return fn


def gate_cypher_contract(C) -> None:
    print("\n-- Cypher contract: baseline count; variant CASE with bound gamma + r-IS-NULL guard --")
    base = C.weighted_arm_cypher(None)
    check("gamma=None emits the frozen count(caller) baseline",
          "count(caller)" in base and "$gExt" not in base, base)
    var = C.weighted_arm_cypher((1.0, 0.5, 0.15))
    check("variant weights are BOUND params (never interpolated: no 0.15 literal)",
          "$gExt" in var and "$gInf" in var and "$gAmb" in var and "0.15" not in var, var)
    check("variant guards no-caller nodes to 0 (r IS NULL -> 0.0, so gamma=(1,1,1)==count)",
          "r IS NULL" in var and "0.0" in var, var)
    check("variant reads the stored confidence tiers (EXTRACTED/INFERRED explicit, AMBIGUOUS = ELSE)",
          "EXTRACTED" in var and "INFERRED" in var and "ELSE $gAmb" in var, var)
    src = (BUILD / "confidence_centrality_ab.py").read_text(encoding="utf-8")
    check("VECTOR_INDEXES is IMPORTED from retrieval (no local re-implementation)",
          "from retrieval import VECTOR_INDEXES" in src)
    check("neo4j is imported LAZILY only (module imports clean without a driver)",
          "import neo4j" not in src.split("def neo4j_query_fn")[0])


def gate_parity_keystone(C) -> None:
    print("\n-- PARITY KEYSTONE: gamma=(1,1,1) reduces exactly to count(caller) --")
    nodes = [{"id": "a", "raw": 0.9, "ext": 1, "inf": 0, "amb": 8},   # centrality mostly AMBIGUOUS
             {"id": "b", "raw": 0.85, "ext": 5, "inf": 0, "amb": 0},  # firm
             {"id": "c", "raw": 0.8, "ext": 0, "inf": 3, "amb": 0}]
    fn = _fake_from_nodes(nodes)
    base = [r["id"] for r in C.weighted_graph_arm(fn, [0.1], 10, gamma=None)]
    parity = [r["id"] for r in C.weighted_graph_arm(fn, [0.1], 10, gamma=(1.0, 1.0, 1.0))]
    check("gamma=(1,1,1) ordering == count(caller) ordering (the null experiment)",
          parity == base, f"{parity} vs {base}")
    down = [r["id"] for r in C.weighted_graph_arm(fn, [0.1], 10, gamma=(1.0, 0.5, 0.15))]
    check("down-weighting AMBIGUOUS CAN reorder (a's 8 ambiguous in-edges lose their inflation)",
          down != base, f"{down} vs {base}")
    check("a bad gamma normalizes to parity (never raises)",
          [r["id"] for r in C.weighted_graph_arm(fn, [0.1], 10, gamma="bogus")] == base)


def gate_replay_substitution(C, T, L) -> None:
    print("\n-- replay: substitutes ONLY the graph arm, re-fuses with the real fusion (oracle) --")
    per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
    recs = []
    for i in range(3):
        recs.append(_search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
        recs.append(_access("f/p.py", f"2026-07-10T0{i}:01:00Z"))

    def fn(cypher, params):
        return ([{"id": "p", "score": 5.0}]
                if params.get("index_name", "").endswith("fn_embeddings") else [])

    res1 = C.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
    res2 = C.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("deterministic (two runs identical)", {k: v for k, v in res1.items() if k != "generated_utc"}
          == {k: v for k, v in res2.items() if k != "generated_utc"})
    labeled = T.build_qrels(recs)
    q = labeled[0]
    W = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}
    expected = (T._ndcg_one(T._fuse(dict(q["per_arm"], graph_weighted=["p"]), W), q["graded"], 10)
                - T._ndcg_one(T._fuse(q["per_arm"], W), q["graded"], 10))
    check("mean delta matches the substitution-only oracle",
          abs(res1.get("mean_delta", 0) - expected) < 1e-9, f"{res1.get('mean_delta')} vs {expected}")
    check("all 3 queries replayed + beats_baseline_significant",
          res1.get("n_replayed") == 3 and res1.get("verdict") == "beats_baseline_significant", str(res1))
    check("gamma recorded as the three tiers", res1.get("gamma", {}).get("AMBIGUOUS") == 0.15, str(res1.get("gamma")))
    check("the conservative-bias note is persisted wording",
          "conservative" in str(res1.get("note", "")).lower())


def gate_degrade(C, L) -> None:
    print("\n-- honest verdicts: insufficient / backend_unavailable / degrade-never-raise --")
    calls = {"n": 0}

    def counting_fn(cypher, params):
        calls["n"] += 1
        return []

    r = C.replay([], query_fn=counting_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("empty log -> insufficient_data, query_fn NEVER invoked",
          r.get("verdict") == "insufficient_data" and calls["n"] == 0, str(r.get("verdict")))
    per_arm = {"vector": ["p"], "bm25": ["p"], "graph_weighted": ["p"]}
    recs = [_search(L, "find p", per_arm, ("p",), "2026-07-10T00:00:00Z"),
            _access("f/p.py", "2026-07-10T00:01:00Z")]
    r = C.replay(recs, query_fn=None, embed_fn=lambda q: [0.1], min_labeled=1)
    check("query_fn None -> backend_unavailable", r.get("verdict") == "backend_unavailable")

    def raising_fn(cypher, params):
        raise RuntimeError("boom")

    raised = False
    try:
        r = C.replay(recs, query_fn=raising_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    except Exception:
        raised = True
        r = {}
    check("an always-raising backend -> backend_unavailable, no raise",
          (not raised) and r.get("verdict") == "backend_unavailable", str(r.get("verdict")))
    # per-index isolation
    def flaky_fn(cypher, params):
        if params.get("index_name") == "deepmap_method_embeddings":
            raise RuntimeError("index gone")
        return ([{"id": "a", "score": 1.0}]
                if params.get("index_name") == "deepmap_fn_embeddings" else [])
    rows = C.weighted_graph_arm(flaky_fn, [0.1], 10, gamma=(1.0, 0.5, 0.15))
    check("a failing index is skipped; the rest still pool", [r2["id"] for r2 in rows] == ["a"], str(rows))


def gate_fuzz(C, L) -> None:
    print("\n-- seeded fuzz: malformed fake rows degrade, never raise --")
    rng = random.Random(20260714)
    per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
    recs = []
    for i in range(4):
        recs.append(_search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
        recs.append(_access("f/p.py", f"2026-07-10T0{i}:01:00Z"))
    garbage = [[{"id": None, "score": 1.0}], [{"score": 2.0}], [{"id": "x", "score": None}],
               [{"id": "x", "score": float("nan")}], ["not a dict"], [None],
               [{"id": "ok", "score": 1.0}], [], [{"id": "y", "score": "high"}]]
    raised = 0
    for _ in range(100):
        def fn(cypher, params, _rows=rng.choice(garbage)):
            return _rows
        try:
            C.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
        except Exception:
            raised += 1
    check("100 malformed-row backends -> zero raises", raised == 0, f"{raised} raised")


def gate_persistence_isolation(C, L, LF) -> None:
    print("\n-- persistence: experiments.confidence_centrality, applied FORCED false, resolver blind --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
        for i in range(3):
            L.append_record(root, _search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
            L.append_record(root, _access("f/p.py", f"2026-07-10T0{i}:01:00Z"))

        def fn(cypher, params):
            return ([{"id": "p", "score": 5.0}]
                    if params.get("index_name", "").endswith("fn_embeddings") else [])

        res = C.replay_and_record(root, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
        check("recorded", res.get("written") is True, str(res))
        data = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))
        exp = data.get("experiments", {}).get("confidence_centrality", {})
        check("experiments.confidence_centrality present with gamma + verdict",
              bool(exp.get("verdict")) and isinstance(exp.get("gamma"), dict), str(exp)[:150])
        check("applied FORCED false", exp.get("applied") is False)
        check("ties itself to the benchmark-gated decision (live arm count(caller))",
              "benchmark-gated" in str(exp.get("note", "")) and "count(caller)" in str(exp.get("note", "")))
        got = LF.resolve_arm_weights_for_live(root, env={"DEEPMAP_APPLY_LEARNED": "1"})
        check("live resolver blind to the experiment", got is None, str(got))


def gate_source_contracts() -> None:
    print("\n-- retrieval.py stays frozen; the module is import-only over it --")
    rsrc = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("retrieval.py never mentions confidence_centrality", "confidence_centrality" not in rsrc)
    check("the frozen graph arm still uses count(caller) (unchanged live centrality)",
          "count(caller) AS inDegree" in rsrc)
    csrc = (BUILD / "confidence_centrality_ab.py").read_text(encoding="utf-8")
    check("the module states the live arm stays count(caller) / benchmark-gated",
          "benchmark-gated" in csrc and "untouched" in csrc)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — confidence-weighted centrality gates (OFFLINE experiment; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)
    LF = _load("learned_fusion", BUILD)
    try:
        C = _load("confidence_centrality_ab", BUILD)
    except Exception as e:
        check("build/confidence_centrality_ab.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_cypher_contract(C)
    gate_parity_keystone(C)
    gate_replay_substitution(C, T, L)
    gate_degrade(C, L)
    gate_fuzz(C, L)
    gate_persistence_isolation(C, L, LF)
    gate_source_contracts()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
