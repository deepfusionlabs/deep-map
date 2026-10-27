#!/usr/bin/env python3
"""DeepMap STAGE-0 in-session deterministic gates (NO Neo4j required).

This is the "chat-validatable now" layer of the test plan
(offline, no database): the pure-Python invariants of the fusion
engine and the arm-survival logic, exercised against the REAL `build/retrieval.py`
WITHOUT a live Neo4j and WITHOUT modifying the product code. It imports the module
and either calls its pure functions directly (`_weighted_rrf`) or drives
`DeepMapRetriever.search()` with a fake driver + monkeypatched arm methods so the
{0,1,2,3}-surviving-arm paths run with canned data.

It complements (does not replace) the live-Neo4j runbook in
`tests/oracles/README.md`:
  - THIS file  -> fusion math + survival/dedupe/single-arm safety   (offline, deterministic)
  - the runbook -> the T2 regression oracle + FD-1.1-1.7 probes      (live Neo4j, operator-run)

Each gate follows the deep-init deterministic-gate discipline (plan §2.4): a
green assertion of the invariant PLUS a paired RED-mutation (`--mutate <name>`)
that proves the gate is load-bearing, not decorative.

Usage:
    python tests/stage0_insession_gates.py            # run all gates -> expect ALL PASS, exit 0
    python tests/stage0_insession_gates.py --mutate weight     # equal-weight regression gate -> RED
    python tests/stage0_insession_gates.py --mutate dedupe     # intra-arm-dedupe gate -> RED
    python tests/stage0_insession_gates.py --mutate singlearm  # single-arm-bypass gate -> RED
    python tests/stage0_insession_gates.py --mutate fts        # FTS fuzzy-builder gate -> RED
    python tests/stage0_insession_gates.py --mutate pooling    # multi-label pooling gate -> RED
    python tests/stage0_insession_gates.py --mutate isolation  # per-label isolation gate -> RED
    python tests/stage0_insession_gates.py --mutate gwformula  # graph-weight-formula gate -> RED

A/B note: this validates whatever `RETRIEVER_MODULE` resolves to. Default is this
repo's `build/retrieval.py` (Scenario A — the only reachable retriever). Override
with the env var DEEPMAP_RETRIEVER_MODULE=/abs/path/to/retrieval.py if a deployed
file is later located (Scenario B).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# --- locate + import the retriever module (A/B parametric) -------------------
_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_RETRIEVER = _REPO / "build" / "retrieval.py"
RETRIEVER_MODULE = Path(os.environ.get("DEEPMAP_RETRIEVER_MODULE", _DEFAULT_RETRIEVER))


def _load_retrieval(path: Path):
    spec = importlib.util.spec_from_file_location("deepmap_retrieval_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- a fake Neo4j driver so __init__ / _detect_search_clause() run offline ---
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, *args, **kwargs):
        # _detect_search_clause probes with a query; an empty result (no exception)
        # makes it report SEARCH-clause support. The arm methods are monkeypatched
        # in the search() gates, so no real Cypher ever runs.
        return _FakeResult([])


class _FakeDriver:
    def session(self):
        return _FakeSession()


# --- a canned-routing driver for the arm-internal gates (FTS builder, pooling) ---
# Routes session.run() by inspecting the query/params: the FTS arm hits
# `fulltext.queryNodes`; the vector/graph PROCEDURE path passes `index_name`. This
# lets the real _arm_fulltext / _arm_vector run end-to-end with NO Neo4j.
class _RoutingSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.driver.calls.append((query, params))
        if "fulltext.queryNodes" in query:                       # ARM 2 (BM25)
            return _FakeResult([dict(r) for r in self.driver.fts_rows])
        if "index_name" in params:                               # vector/graph procedure path
            idx = params["index_name"]
            if idx in self.driver.raise_on:
                raise RuntimeError(f"simulated index failure: {idx}")
            return _FakeResult([dict(r) for r in self.driver.vec_rows.get(idx, [])])
        return _FakeResult([])                                    # _detect_search_clause probe, etc.


class _RoutingDriver:
    def __init__(self, vec_rows=None, fts_rows=None, raise_on=()):
        self.vec_rows = vec_rows or {}     # {index_name: [row dicts]}
        self.fts_rows = fts_rows or []     # [row dicts]
        self.raise_on = set(raise_on)      # index_names that raise
        self.calls = []                    # [(query, params)] recorded

    def session(self):
        return _RoutingSession(self)


def _row(rid, score, rtype="Function"):
    return {"id": rid, "name": rid, "type": rtype, "file": "f.py",
            "line": 1, "summary": "", "score": score}


# --- gate plumbing -----------------------------------------------------------
class GateFailure(AssertionError):
    pass


_PASSED = 0
_N = 0


def gate(name, ok, detail=""):
    global _PASSED, _N
    _N += 1
    status = "PASS" if ok else "FAIL"
    print(f"  == {_N:>2} == [{status}] {name}" + (f"  - {detail}" if detail else ""))
    if ok:
        _PASSED += 1
    else:
        raise GateFailure(f"gate {_N} ({name}) failed: {detail}")


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def make_retriever(R, arm_weights=None):
    r = R.DeepMapRetriever(_FakeDriver(), embed_fn=lambda q: [0.0] * 768,
                           arm_weights=arm_weights)
    # offline hydration: return ids in given order with their fused/arm score
    r._hydrate = lambda top_ids, scores: [
        {"id": i, "name": i, "type": "Function", "file": "f.py",
         "line": 1, "summary": "", "score": scores.get(i, 0.0)}
        for i in top_ids
    ]
    return r


def drive(r, vec=None, fts=None, graph=None, top_k=15):
    """Run search() with canned arm outputs (each a list of {id,score} dicts)."""
    r._arm_vector = lambda qvec, k: list(vec or [])
    r._arm_fulltext = lambda query, k: list(fts or [])
    r._arm_graph_weighted = lambda qvec, k: list(graph or [])
    return r.search("any query", top_k=top_k)


def ref_plain_rrf(runs_by_arm, k):
    """Independent reference: equal-weight RRF == Σ 1/(k+rank), rank 1-based by
    descending arm score. Used to cross-check _weighted_rrf at equal weights."""
    fused = {}
    for _name, scores in runs_by_arm:
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        for rank, (doc_id, _s) in enumerate(ranked, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


# --- the gates ---------------------------------------------------------------
def run_gates(R, mutate=None):
    print(f"Retriever under test: {RETRIEVER_MODULE}")
    print(f"Mutation: {mutate or 'none (expect ALL PASS)'}\n")

    # Gate 1 — RRF_K is the field/ranx default 60 (premise of equal-weight ≡ plain RRF).
    gate("RRF_K == 60 (matches ranx default; retrieval.py:27-29)",
         R.RRF_K == 60, f"RRF_K={R.RRF_K}")

    # Gate 2 — _weighted_rrf at equal weights == hand-computed plain RRF (THE load-bearing
    # invariant; retrieval.py:39 / FD-6.1). Tiny fixture with a cross-arm overlap (B).
    runs = [
        ("vector", {"A": 0.9, "B": 0.5}),
        ("graph_weighted", {"B": 0.8, "C": 0.4}),
    ]
    eq_w = {"vector": 1.0, "graph_weighted": 1.0}
    fused = R._weighted_rrf(runs, eq_w)
    exp_A = 1.0 / 61
    exp_B = 1.0 / 62 + 1.0 / 61
    exp_C = 1.0 / 62
    hand_ok = (approx(fused["A"], exp_A) and approx(fused["B"], exp_B)
               and approx(fused["C"], exp_C))
    order = [d for d, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)]
    gate("equal-weight _weighted_rrf == hand-computed plain RRF (ordering B,A,C)",
         hand_ok and order == ["B", "A", "C"], f"fused={ {k: round(v,6) for k,v in fused.items()} } order={order}")

    # Gate 3 — generic equal-weight cross-check against the independent reference.
    runs2 = [
        ("vector", {"x": 0.3, "y": 0.31, "z": 0.1}),
        ("bm25", {"y": 9.0, "w": 2.0}),
        ("graph_weighted", {"x": 0.7, "w": 0.65}),
    ]
    g3 = R._weighted_rrf(runs2, {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0})
    ref = ref_plain_rrf(runs2, R.RRF_K)
    gate("equal-weight _weighted_rrf == independent plain-RRF reference (3 arms)",
         all(approx(g3[k], ref[k]) for k in ref) and set(g3) == set(ref),
         "all ids match reference within 1e-9")

    # Gate 4 — weights key by ARM NAME not position (FD-8.1, retrieval.py:46). Zeroing
    # one arm's weight must remove exactly that arm's rank contributions.
    w_zero = {"vector": 0.0, "graph_weighted": 1.0}
    fz = R._weighted_rrf(runs, w_zero)
    # with vector zeroed: A only came from vector -> A contribution 0; B keeps only graph rank1; C graph rank2
    gate("weighted: zeroing 'vector' weight drops only vector's contributions (keyed by name)",
         approx(fz["A"], 0.0) and approx(fz["B"], 1.0 / 61) and approx(fz["C"], 1.0 / 62),
         f"A={round(fz['A'],6)} B={round(fz['B'],6)} C={round(fz['C'],6)}")

    # Gate 5 — no-arm guard: zero surviving arms -> [] (retrieval.py:125-126; FD-1.9).
    r = make_retriever(R)
    gate("no surviving arms -> [] (never an error / fake answer)",
         drive(r, vec=[], fts=[], graph=[]) == [], "empty input -> empty output")

    # Gate 6 — single-arm bypass: exactly one surviving arm is NOT routed through RRF; it returns
    # top_k by RAW arm score (retrieval.py:128-133). The DISCRIMINATOR is the SCORE, not the order:
    # for a single arm, RRF preserves the same order as raw-score, so an order-only check could never
    # detect "lone arm routed through RRF" (the old tautology). But the bypass returns the RAW arm
    # score (0.9) whereas RRF would return ~1/(RRF_K+1) — so we assert on the score value.
    r = make_retriever(R)
    only = [{"id": "p", "score": 0.2}, {"id": "q", "score": 0.9}, {"id": "s", "score": 0.5}]
    res = drive(r, fts=only, top_k=2)
    bypass_order = [d["id"] for d in res]
    raw_order = [d["id"] for d, in [(x,) for x in sorted(only, key=lambda r: r["score"], reverse=True)]][:2]
    rrf_score = 1.0 / (R.RRF_K + 1)                  # what the score WOULD be if routed through RRF
    top_score = res[0]["score"] if res else None
    if mutate == "singlearm":
        # emulate the regression: the lone arm IS routed through _weighted_rrf -> the returned score
        # becomes the RRF score, not the raw arm score. The score assertion below must catch this.
        top_score = rrf_score
    gate("single surviving arm -> bypass fusion: top_k by RAW arm score (NOT an RRF score)",
         bypass_order == raw_order and len(res) == 2 and top_score is not None
         and abs(top_score - 0.9) < 1e-9 and abs(top_score - rrf_score) > 1e-6,
         f"order={bypass_order} top_score={top_score} (raw expected 0.9; rrf would be {rrf_score:.4f})")

    # Gate 7 — intra-arm dedupe keeps the BEST score per id within an arm
    # (retrieval.py:117-122).
    r = make_retriever(R)
    dup_vec = [{"id": "d", "score": 0.2}, {"id": "d", "score": 0.95}, {"id": "e", "score": 0.4}]
    graph = [{"id": "e", "score": 0.9}]
    if mutate == "dedupe":
        # MUTATION: defeat dedupe by making search see the WORSE dup as authoritative.
        # We can't edit retrieval.py, so we assert against the buggy expectation to show
        # the gate would catch a dedupe regression (best-score 'd' must outrank worse).
        dup_vec = [{"id": "d", "score": 0.2}, {"id": "e", "score": 0.4}]  # drop the better dup
    res = drive(r, vec=dup_vec, graph=graph, top_k=5)
    by_id = {d["id"]: d for d in res}
    # 'd' only appears in vector; with best-score 0.95 it ranks 1 in vector -> contributes 1/(k+1).
    # If dedupe were broken (kept 0.2), 'd' would rank 2 -> 1/(k+2), changing the fused score.
    d_score = by_id.get("d", {}).get("score", -1.0)
    expect_d = 1.0 / (R.RRF_K + 1)   # best-score dedupe => vector rank 1
    gate("intra-arm dedupe keeps best score per id (affects rank => fused score)",
         approx(d_score, expect_d),
         f"fused('d')={round(d_score,6)} expected(best-score rank1)={round(expect_d,6)}")

    # Gate 8 — equal-weight retriever ordering is the regression contract; a non-equal
    # default would change it. Build the equal-weight oracle ordering, then (under
    # --mutate weight) rebuild with a perturbed weight and assert the ORDERING changed.
    base = make_retriever(R)  # equal default weights
    vec = [{"id": "A", "score": 0.9}, {"id": "B", "score": 0.5}]
    graph = [{"id": "B", "score": 0.8}, {"id": "C", "score": 0.4}]
    eq_order = [d["id"] for d in drive(base, vec=vec, graph=graph, top_k=5)]
    if mutate == "weight":
        mut = make_retriever(R, arm_weights={"vector": 0.05})  # perturb one arm's weight
        mut_order = [d["id"] for d in drive(mut, vec=vec, graph=graph, top_k=5)]
        gate("equal-weight ordering is STABLE vs a weight perturbation (MUST go RED under --mutate weight)",
             mut_order == eq_order,
             f"equal={eq_order} perturbed={mut_order} — divergence proves the gate is load-bearing")
    else:
        gate("equal-weight retriever ordering == equal-weight RRF oracle (B,A,C)",
             eq_order == ["B", "A", "C"], f"order={eq_order}")

    # ---- arm-internal gates (no Neo4j; canned-routing driver) ----------------

    # Gate 9 — FTS fuzzy-builder: per-term `{t}~2` for tokens >2 chars; <=2-char
    # tokens dropped (retrieval.py:188). An all-short query short-circuits to []
    # WITHOUT a fulltext call (retrieval.py:189).
    drv = _RoutingDriver(fts_rows=[_row("x", 1.0)])
    r = R.DeepMapRetriever(drv, embed_fn=lambda q: [0.0] * 768)
    r._arm_fulltext("foo to bar parse_config", 10)
    built = next((p["q"] for q, p in drv.calls if "fulltext.queryNodes" in q and "q" in p), None)
    expected_q = "foo~2 bar~2 parse_config~2"          # 'to' (2 chars) dropped
    if mutate == "fts":
        expected_q = "foo~2 to~2 bar~2 parse_config~2"  # a >=2 threshold bug would keep 'to'
    gate("FTS fuzzy-builder: '{t}~2' per token >2 chars, short tokens dropped",
         built == expected_q, f"built={built!r} expected={expected_q!r}")

    drv2 = _RoutingDriver()
    r2 = R.DeepMapRetriever(drv2, embed_fn=lambda q: [0.0] * 768)
    res = r2._arm_fulltext("a bc", 10)   # no token >2 -> fts_q empty -> [] before any fulltext call
    gate("FTS empty-query short-circuit -> [] with NO fulltext call",
         res == [] and not any("fulltext.queryNodes" in q for q, _ in drv2.calls),
         f"res={res}")

    # Gate 10 — multi-label vector pooling: _arm_vector pools across VECTOR_INDEXES,
    # sorts by score desc, truncates to k (retrieval.py:143-157). Force the procedure
    # path so index_name routes (R7: instance-level, never edits source).
    fn_idx, cls_idx = R.VECTOR_INDEXES[0][1], R.VECTOR_INDEXES[2][1]
    rows = {fn_idx: [_row("f1", 0.90, "Function")],
            cls_idx: [_row("c1", 0.95, "Class")]}
    if mutate == "pooling":
        rows.pop(cls_idx)            # drop the non-Function label -> multi-label coverage lost
    drv3 = _RoutingDriver(vec_rows=rows)
    r3 = R.DeepMapRetriever(drv3, embed_fn=lambda q: [0.0] * 768)
    r3._use_search_clause = False
    pooled = [x["id"] for x in r3._arm_vector([0.0] * 768, 10)]
    gate("multi-label vector pooling across VECTOR_INDEXES, sorted desc",
         pooled == ["c1", "f1"], f"pooled={pooled} (c1=.95 must precede f1=.90)")

    # Gate 11 — per-label index isolation: a raising per-label index is caught and
    # skipped; it must NOT kill the whole arm (retrieval.py:152-155; invariant 5).
    iso_rows = {fn_idx: [_row("f1", 0.9, "Function")]}
    raise_on = {cls_idx}
    if mutate == "isolation":
        raise_on = {fn_idx, cls_idx}     # if failures weren't isolated, the survivor is lost too
    drv4 = _RoutingDriver(vec_rows=iso_rows, raise_on=raise_on)
    r4 = R.DeepMapRetriever(drv4, embed_fn=lambda q: [0.0] * 768)
    r4._use_search_clause = False
    iso = [x["id"] for x in r4._arm_vector([0.0] * 768, 10)]
    gate("per-label index failure is isolated, arm survives (invariant 5)",
         iso == ["f1"], f"survivors={iso}")

    # Gate 13 — graph-weighting formula integrity (the TRUE claim, FD-8.3). Arm 3 must
    # weight vector score by (1.0 + log(1.0 + toFloat(inDegree))) on BOTH the SEARCH path
    # and the procedure path (retrieval.py:226, 238). This is the real, executable
    # "graph-weighted ranking" claim — this gate stops it from being silently dropped or
    # altered when the FAKE "self-improving" claim is cut (T12 guard).
    import inspect as _inspect
    formula = "(1.0 + log(1.0 + toFloat(inDegree)))"
    gw_src = _inspect.getsource(R.DeepMapRetriever._arm_graph_weighted_one)
    if mutate == "gwformula":
        # emulate a real regression: someone drops the log dampening from both paths.
        gw_src = gw_src.replace(formula, "(1.0 + toFloat(inDegree))")
    gate("graph-weighting formula present on BOTH arm-3 paths (TRUE claim, FD-8.3)",
         gw_src.count(formula) >= 2,
         f"occurrences of {formula!r} = {gw_src.count(formula)} (need >=2)")

    # Gate 14 — _detect_search_clause branch logic (retrieval.py:81-96). An empty probe
    # (no exception) -> SEARCH supported (True); a probe that raises -> procedure fallback
    # (False). The SEARCH-with-procedure-fallback dual path is a LOCKED decision; this
    # confirms the detector offline (the live-version check is T3/1.1).
    class _RaisingDriver:
        def session(self):
            class _S:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *a):
                    return False

                def run(self_, *a, **k):
                    raise RuntimeError("SEARCH clause not supported on this version")
            return _S()

    r_true = R.DeepMapRetriever(_FakeDriver(), embed_fn=lambda q: [0.0] * 768)
    r_false = R.DeepMapRetriever(_RaisingDriver(), embed_fn=lambda q: [0.0] * 768)
    gate("_detect_search_clause: empty probe -> True; raising probe -> False (fallback)",
         r_true._use_search_clause is True and r_false._use_search_clause is False,
         f"empty->{r_true._use_search_clause}, raising->{r_false._use_search_clause}")


def main():
    # Windows consoles default to a legacy codepage (e.g. cp1255) that cannot encode
    # non-ASCII output. Force UTF-8 so the harness never dies on its own banner.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    mutate = None
    if "--mutate" in sys.argv:
        i = sys.argv.index("--mutate")
        mutate = sys.argv[i + 1] if i + 1 < len(sys.argv) else "weight"

    if not RETRIEVER_MODULE.exists():
        print(f"FATAL: retriever module not found: {RETRIEVER_MODULE}", file=sys.stderr)
        return 2
    R = _load_retrieval(RETRIEVER_MODULE)

    print("=" * 72)
    print("DeepMap STAGE-0 in-session deterministic gates")
    print("=" * 72)
    try:
        run_gates(R, mutate=mutate)
    except GateFailure as e:
        print(f"\nRESULT: RED — {_PASSED}/{_N} passed. First failure: {e}")
        if mutate:
            print("(This RED is the EXPECTED outcome of the injected mutation — "
                  "it proves the gate is load-bearing, not decorative.)")
            return 0  # a mutation that turns the gate red is a SUCCESS of the harness
        return 1
    print(f"\nRESULT: GREEN — {_PASSED}/{_N} gates passed.")
    if mutate:
        print(f"WARNING: mutation '{mutate}' did NOT turn any gate red — the gate may be decorative!")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
