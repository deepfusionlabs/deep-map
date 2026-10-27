#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/graph_signal_ab.py — the OFFLINE framework-edge-aware
graph-signal EXPERIMENT (D2). An offline variant of the graph arm's in-degree that also counts
typed framework/runtime edges (ROUTES_TO/DEPENDS_ON/...), recomputed against a live graph at RUN
time through an INJECTED query function and A/B'd by replaying the log's fusion. retrieval.py is
untouched: the frozen `_sanitize_rel_types` / `VECTOR_INDEXES` / fusion are IMPORTED, never
re-implemented, and the live ranking arm still keys on :CALLS only (retrieval.py:174-178 — the
benchmark-gated change this experiment produces the measured case FOR).

Locks:
  - the Cypher contract: sanitized rel pattern (injection attempt dropped, CALLS first), lambda
    ALWAYS a bound $lam parameter (never interpolated), the two-count typed-discount shape,
  - THE PARITY KEYSTONE: lambda=0 reduces exactly to CALLS-only,
  - the replay substitutes ONLY the graph arm and re-fuses with the real fusion (oracle-matched),
  - honesty statistics: replay_fidelity (a rebuilt graph shows up, never a silently clean
    number) and qrel_coverage (the log-bias against the variant, stated),
  - verdicts: insufficient_data (query_fn never invoked) / backend_unavailable (degrade, never
    raise) / no_improvement / not_significant / beats_baseline_significant,
  - per-index error isolation (the FD-1 mirror), redacted queries skipped + counted,
  - persistence: experiments.graph_signal, applied FORCED false, live resolver blind,
  - seeded fuzz on malformed fake rows: degrade, never raise. Neo4j is needed at RUN time only —
    never here.
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
    """A hermetic query_fn over [{id, raw, calls, typed}] that mimics the three Cypher shapes:
    CALLS-only, all-equal extended (pattern with '|'), and the $lam two-count variant."""
    calls_log = []

    def fn(cypher, params):
        calls_log.append((cypher, dict(params)))
        rows = []
        for n in nodes:
            if "$lam" in cypher:
                lam = float(params.get("lam", 0.0))
                score = n["raw"] * (1.0 + math.log(1.0 + n["calls"] + lam * n["typed"]))
            elif "|" in cypher:
                score = n["raw"] * (1.0 + math.log(1.0 + n["calls"] + n["typed"]))
            else:
                score = n["raw"] * (1.0 + math.log(1.0 + n["calls"]))
            rows.append({"id": n["id"], "score": score})
        return rows

    fn.calls_log = calls_log
    return fn


def gate_cypher_contract(G) -> None:
    print("\n-- Cypher contract: sanitized pattern, CALLS first, $lam bound, injection dropped --")
    hostile = ["ROUTES_TO", "X) DETACH DELETE (n", "bad-type", "routes_to"]
    cy = G.extended_arm_cypher(hostile)
    check("injection attempt dropped", "DETACH" not in cy and ")" not in cy.split("<-[:")[1].split("]-")[0], cy)
    check("malformed/lowercase tokens normalized + deduped", "<-[:CALLS|ROUTES_TO]-" in cy, cy)
    cy_lam = G.extended_arm_cypher(hostile, lam=0.5)
    check("lambda variant: $lam is a bound param, never interpolated",
          "$lam" in cy_lam and "0.5" not in cy_lam, cy_lam)
    check("lambda variant has the two-count shape (callsIn + typedIn)",
          "callsIn" in cy_lam and "typedIn" in cy_lam)
    check("CALLS-only when the type list is just CALLS", "<-[:CALLS]-" in G.extended_arm_cypher(["CALLS"]))
    src = (BUILD / "graph_signal_ab.py").read_text(encoding="utf-8")
    check("the sanitizer is IMPORTED from retrieval (mutation guard — no local re-implementation)",
          "_sanitize_rel_types" in src and "def _sanitize_rel_types" not in src)
    check("neo4j is imported LAZILY only (module imports clean without the driver)",
          "import neo4j" not in src.split("def neo4j_query_fn")[0])


def gate_lambda_zero_parity(G) -> None:
    print("\n-- PARITY KEYSTONE: lambda=0 reduces exactly to CALLS-only --")
    nodes = [{"id": "a", "raw": 0.9, "calls": 5, "typed": 40},
             {"id": "b", "raw": 0.8, "calls": 9, "typed": 0},
             {"id": "c", "raw": 0.7, "calls": 0, "typed": 90}]
    fn = _fake_from_nodes(nodes)
    lam0 = G.extended_graph_arm(fn, [0.1], 10, lam=0.0)
    calls_only = G.extended_graph_arm(fn, [0.1], 10, edge_types=["CALLS"])
    check("lambda=0 ordering == CALLS-only ordering",
          [r["id"] for r in lam0] == [r["id"] for r in calls_only],
          f"{[r['id'] for r in lam0]} vs {[r['id'] for r in calls_only]}")
    lam1 = G.extended_graph_arm(fn, [0.1], 10, lam=1.0)
    check("lambda=1 CAN reorder (typed edges count)",
          [r["id"] for r in lam1] != [r["id"] for r in calls_only], str([r["id"] for r in lam1]))


def gate_replay_substitution(G, T, L) -> None:
    print("\n-- replay: substitutes ONLY the graph arm, re-fuses with the real fusion (oracle) --")
    per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
    recs = []
    for i in range(3):
        recs.append(_search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
        recs.append(_access("f/p.py", f"2026-07-10T0{i}:01:00Z"))
    # The variant arm surfaces ONLY p — the fake returns one row for the first index, [] after.
    seen = {"n": 0}

    def fn(cypher, params):
        seen["n"] += 1
        return [{"id": "p", "score": 5.0}] if params.get("index_name", "").endswith("fn_embeddings") else []

    res1 = G.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
    res2 = G.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("deterministic (two runs identical)", {k: v for k, v in res1.items() if k != "generated_utc"}
          == {k: v for k, v in res2.items() if k != "generated_utc"})
    labeled = T.build_qrels(recs)
    q = labeled[0]
    expected = (T._ndcg_one(T._fuse(dict(q["per_arm"], graph_weighted=["p"]),
                                    {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}), q["graded"], 10)
                - T._ndcg_one(T._fuse(q["per_arm"],
                                      {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}), q["graded"], 10))
    check("mean delta matches the substitution-only oracle",
          abs(res1.get("mean_delta", 0) - expected) < 1e-9, f"{res1.get('mean_delta')} vs {expected}")
    check("all 3 queries replayed", res1.get("n_replayed") == 3, str(res1.get("n_replayed")))
    check("a positive, uniform delta -> beats_baseline_significant",
          res1.get("verdict") == "beats_baseline_significant", str(res1.get("verdict")))
    check("qrel_coverage reported in [0,1]", isinstance(res1.get("qrel_coverage"), float)
          and 0.0 <= res1["qrel_coverage"] <= 1.0, str(res1.get("qrel_coverage")))
    check("the conservative-bias note is persisted wording",
          "conservative" in str(res1.get("note", "")).lower(), str(res1.get("note"))[:150])


def gate_replay_fidelity(G, L) -> None:
    print("\n-- replay_fidelity: a rebuilt/drifted graph SHOWS UP, never a silently clean number --")
    per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
    recs = [_search(L, "find p", per_arm, ("a", "p"), "2026-07-10T00:00:00Z"),
            _access("f/p.py", "2026-07-10T00:01:00Z")]

    def exact_fn(cypher, params):   # reproduces the LOGGED graph ordering exactly
        return ([{"id": "a", "score": 2.0}, {"id": "p", "score": 1.0}]
                if params.get("index_name", "").endswith("fn_embeddings") else [])

    def drifted_fn(cypher, params):   # a rebuilt graph: different candidates
        return ([{"id": "zz", "score": 9.0}]
                if params.get("index_name", "").endswith("fn_embeddings") else [])

    r = G.replay(recs, query_fn=exact_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("exact reproduction -> fidelity 1.0, no drift flag",
          r.get("replay_fidelity") == 1.0 and r.get("graph_drift") is False, str(r.get("replay_fidelity")))
    r = G.replay(recs, query_fn=drifted_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("a drifted graph -> fidelity < 1.0 and the drift flag SURFACED",
          (r.get("replay_fidelity") or 0) < 1.0 and r.get("graph_drift") is True,
          str(r.get("replay_fidelity")))


def gate_redacted_skip(G, L) -> None:
    print("\n-- redacted queries are skipped + counted; embed_fn never sees them --")
    per_arm = {"vector": ["p"], "bm25": ["p"], "graph_weighted": ["p"]}
    rec = _search(L, "real query", per_arm, ("p",), "2026-07-10T00:00:00Z")
    rec["query"] = "redacted:deadbeefdeadbeef"
    recs = [rec, _access("f/p.py", "2026-07-10T00:01:00Z")]
    embedded = []
    r = G.replay(recs, query_fn=lambda c, p: [], embed_fn=lambda q: embedded.append(q) or [0.1],
                 min_labeled=1)
    check("redacted query skipped + counted", r.get("n_skipped_redacted") == 1, str(r))
    check("embed_fn never called on a redacted query", embedded == [], str(embedded))
    check("nothing usable -> insufficient_data (not a fabricated number)",
          r.get("verdict") == "insufficient_data", str(r.get("verdict")))


def gate_insufficient_and_unavailable(G, L) -> None:
    print("\n-- insufficient_data / backend_unavailable: honest, degrade, never raise --")
    calls = {"n": 0}

    def counting_fn(cypher, params):
        calls["n"] += 1
        return []

    r = G.replay([], query_fn=counting_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    check("empty log -> insufficient_data", r.get("verdict") == "insufficient_data", str(r.get("verdict")))
    check("query_fn NEVER invoked on insufficient data", calls["n"] == 0, str(calls))
    per_arm = {"vector": ["p"], "bm25": ["p"], "graph_weighted": ["p"]}
    recs = [_search(L, "find p", per_arm, ("p",), "2026-07-10T00:00:00Z"),
            _access("f/p.py", "2026-07-10T00:01:00Z")]

    def raising_fn(cypher, params):
        raise RuntimeError("boom")

    raised = False
    try:
        r = G.replay(recs, query_fn=raising_fn, embed_fn=lambda q: [0.1], min_labeled=1)
    except Exception:
        raised = True
        r = {}
    check("an always-raising backend -> backend_unavailable, no raise",
          (not raised) and r.get("verdict") == "backend_unavailable", str(r.get("verdict")))
    r = G.replay(recs, query_fn=None, embed_fn=lambda q: [0.1], min_labeled=1)
    check("query_fn None -> backend_unavailable", r.get("verdict") == "backend_unavailable")


def gate_pooling_mirror(G) -> None:
    print("\n-- per-index error isolation (the FD-1 mirror) --")
    def flaky_fn(cypher, params):
        if params.get("index_name") == "deepmap_method_embeddings":
            raise RuntimeError("index gone")
        return ([{"id": "a", "score": 1.0}]
                if params.get("index_name") == "deepmap_fn_embeddings" else [])

    rows = G.extended_graph_arm(flaky_fn, [0.1], 10)
    check("a failing index is skipped; the rest still pool", [r["id"] for r in rows] == ["a"], str(rows))


def gate_persistence_isolation(G, L, LF) -> None:
    print("\n-- persistence: experiments.graph_signal, applied FORCED false, resolver blind --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
        for i in range(3):
            L.append_record(root, _search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
            L.append_record(root, _access("f/p.py", f"2026-07-10T0{i}:01:00Z"))
        def fn(cypher, params):
            return ([{"id": "p", "score": 5.0}]
                    if params.get("index_name", "").endswith("fn_embeddings") else [])
        res = G.replay_and_record(root, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
        check("recorded", res.get("written") is True, str(res))
        data = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))
        exp = data.get("experiments", {}).get("graph_signal", {})
        check("experiments.graph_signal present with edge_types + verdict",
              bool(exp.get("verdict")) and isinstance(exp.get("edge_types"), list), str(exp)[:150])
        check("applied FORCED false", exp.get("applied") is False)
        check("ties itself to the benchmark-gated ranking-upgrade decision",
              "benchmark-gated" in str(exp.get("note", "")),
              str(exp.get("note")))
        got = LF.resolve_arm_weights_for_live(root, env={"DEEPMAP_APPLY_LEARNED": "1"})
        check("live resolver blind to the experiment", got is None, str(got))


def gate_fuzz(G, L) -> None:
    print("\n-- seeded fuzz: malformed fake rows degrade, never raise --")
    rng = random.Random(20260714)
    per_arm = {"vector": ["a", "p"], "bm25": ["a", "p"], "graph_weighted": ["a", "p"]}
    recs = []
    for i in range(4):
        recs.append(_search(L, f"find p {i}", per_arm, ("a", "p"), f"2026-07-10T0{i}:00:00Z"))
        recs.append(_access("f/p.py", f"2026-07-10T0{i}:01:00Z"))
    garbage_rows = [
        [{"id": None, "score": 1.0}], [{"score": 2.0}], [{"id": "x", "score": None}],
        [{"id": "x", "score": float("nan")}], ["not a dict"], [None], [{"id": "ok", "score": 1.0}],
        [], [{"id": "y", "score": "high"}],
    ]
    raised = 0
    for _ in range(120):
        def fn(cypher, params, _rows=rng.choice(garbage_rows)):
            return _rows
        try:
            G.replay(recs, query_fn=fn, embed_fn=lambda q: [0.1], min_labeled=1)
        except Exception:
            raised += 1
    check("120 malformed-row backends -> zero raises", raised == 0, f"{raised} raised")


def gate_source_contracts() -> None:
    print("\n-- retrieval.py stays frozen; the module is import-only over it --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("retrieval.py never mentions graph_signal", "graph_signal" not in src)
    gsrc = (BUILD / "graph_signal_ab.py").read_text(encoding="utf-8")
    check("the module states the live arm stays CALLS-only (benchmark-gated)",
          "benchmark-gated" in gsrc)
    rb = (REPO / "benchmark" / "run_benchmark.py").read_text(encoding="utf-8")
    check("run_benchmark exposes the --ab-graph-signal plan printer (visibility seam)",
          "--ab-graph-signal" in rb and "_print_ab_graph_signal_plan" in rb)
    idx = rb.find("def _print_ab_graph_signal_plan")
    check("the printer is try/except-wrapped (visibility never breaks the run)",
          idx > 0 and "except Exception" in rb[idx:idx + 1600])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — framework-edge graph-signal gates (OFFLINE experiment; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)
    LF = _load("learned_fusion", BUILD)
    try:
        G = _load("graph_signal_ab", BUILD)
    except Exception as e:
        check("build/graph_signal_ab.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_cypher_contract(G)
    gate_lambda_zero_parity(G)
    gate_replay_substitution(G, T, L)
    gate_replay_fidelity(G, L)
    gate_redacted_skip(G, L)
    gate_insufficient_and_unavailable(G, L)
    gate_pooling_mirror(G)
    gate_persistence_isolation(G, L, LF)
    gate_fuzz(G, L)
    gate_source_contracts()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
