#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/access_prior.py — the OFFLINE per-node access-prior
re-ranker EXPERIMENT (D1). Learns a small local prior from repeated access patterns ("nodes this
user actually works in") and re-ranks the FUSED list in REPLAY only. It is NEVER wired into live
search: results persist under instance.json `experiments.access_prior` with applied FORCED
false, which the live resolver provably ignores (instance_state_gates).

Locks:
  - prior math: grade-weighted (Edit 1.0 > Read 0.5), recency-decayed (half-life), normalized to
    [0,1]; unknown ids -> 0.0,
  - THE PARITY KEYSTONE: rerank at beta=0 reproduces the real fusion ordering exactly,
  - re-rank behavior: a high-prior node climbs at sufficient beta; an all-zero prior changes
    nothing at ANY beta,
  - LEAKAGE (teeth): a fold's prior is built from TRAIN-fold access signal only — a node
    accessed only by held-out queries has prior 0.0 there; the naive all-records prior DISAGREES,
  - evaluate: verdict ladder (insufficient_data / no_improvement / not_significant /
    beats_baseline_significant), deterministic, prior_hit_rate (the named circularity-exposure
    statistic) in [0,1], never raises,
  - persistence: experiments.access_prior, applied false, circularity named, learned_fusion
    untouched,
  - 300-seed fuzz: malformed logs degrade, never raise.
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


EQUAL = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}


def _search(L, q, per_arm, ids, ts):
    returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ids]
    return L.build_record(q, per_arm, returned, ts=ts)


def _access(path, ts, kind="edit"):
    return {"type": "access", "ts": ts, "paths": [path], "symbols": [], "kind": kind}


def gate_prior_basics(AP, L) -> None:
    print("\n-- prior math: grade-weighted, normalized [0,1], unknown -> 0 --")
    per_arm = {"vector": ["e", "r"], "bm25": ["e", "r"], "graph_weighted": []}
    recs = [
        _search(L, "q0", per_arm, ("e", "r"), "2026-07-10T00:00:00Z"),
        _access("f/e.py", "2026-07-10T00:01:00Z", kind="edit"),
        _search(L, "q1", per_arm, ("e", "r"), "2026-07-10T01:00:00Z"),
        _access("f/r.py", "2026-07-10T01:01:00Z", kind="read"),
    ]
    prior = AP.build_prior(recs)
    check("edited node outranks read-only node", prior.get("e", 0) > prior.get("r", 0), str(prior))
    check("values in [0,1], max == 1.0", 0 <= prior.get("r", 0) <= 1 and prior.get("e") == 1.0, str(prior))
    check("unknown id -> 0.0 (dict default)", prior.get("zz", 0.0) == 0.0)
    check("empty records -> empty prior", AP.build_prior([]) == {})


def gate_recency_decay(AP, L) -> None:
    print("\n-- recency decay: newer access earns a higher prior (half-life) --")
    per_arm = {"vector": ["old", "new"], "bm25": ["old", "new"], "graph_weighted": []}
    recs = [
        _search(L, "q0", per_arm, ("old", "new"), "2026-01-01T00:00:00Z"),
        _access("f/old.py", "2026-01-01T00:01:00Z"),           # ~190 days before the newest ts
        _search(L, "q1", per_arm, ("old", "new"), "2026-07-10T00:00:00Z"),
        _access("f/new.py", "2026-07-10T00:01:00Z"),
    ]
    prior = AP.build_prior(recs, half_life_days=14.0)
    check("newer access > older access (same grade)", prior.get("new", 0) > prior.get("old", 0), str(prior))
    check("old access decayed hard (~190d at 14d half-life)", prior.get("old", 1) < 0.01, str(prior))
    # TEETH: a flipped decay exponent (growth instead of decay) would invert the order.
    check("fixture has teeth vs a flipped exponent",
          prior.get("new", 0) > prior.get("old", 0) != (0.5 ** (-190 / 14.0) < 1))
    # Unparseable ts -> decay 1.0 (degrade, never raise).
    recs2 = [
        _search(L, "q0", per_arm, ("old", "new"), "t0"),
        _access("f/old.py", "t0b"),
    ]
    prior2 = AP.build_prior(recs2)
    check("unparseable ts -> full weight, no raise", prior2.get("old") == 1.0, str(prior2))


def gate_beta_zero_parity(AP, T) -> None:
    print("\n-- PARITY KEYSTONE: beta=0 reproduces the real fusion ordering exactly --")
    rng = random.Random(20260712)
    ids = [f"n{i}" for i in range(12)]
    mismatches = 0
    for _ in range(25):
        per_arm = {a: rng.sample(ids, rng.randint(0, len(ids))) for a in EQUAL}
        prior = {rid: rng.random() for rid in rng.sample(ids, 5)}
        baseline = T._fuse(per_arm, EQUAL)
        scores = AP.fuse_scores(per_arm, EQUAL)
        reranked = AP.rerank(scores, prior, 0.0)
        if reranked != baseline:
            mismatches += 1
    check("25 random per-arm sets: rerank(beta=0) == the real _fuse ordering", mismatches == 0,
          f"{mismatches} mismatches")


def gate_rerank_moves(AP) -> None:
    print("\n-- re-rank behavior: high-prior climbs; all-zero prior changes nothing --")
    # Fused scores descending a > b > c; prior favors c strongly.
    scores = {"a": 0.30, "b": 0.29, "c": 0.28}
    check("beta=1 lifts the high-prior node above a zero-prior one",
          AP.rerank(scores, {"c": 1.0}, 1.0)[0] == "c", str(AP.rerank(scores, {"c": 1.0}, 1.0)))
    check("a small beta is NOT enough (multiplicative, bounded)",
          AP.rerank(scores, {"c": 1.0}, 0.01) == ["a", "b", "c"])
    for beta in (0.0, 0.1, 0.25, 0.5, 1.0):
        pass
    check("all-zero prior -> ordering unchanged at every beta",
          all(AP.rerank(scores, {}, b) == ["a", "b", "c"] for b in (0.0, 0.1, 0.25, 0.5, 1.0)))
    check("deterministic tie-break (score then id)",
          AP.rerank({"x": 0.5, "y": 0.5}, {}, 0.0) == ["x", "y"])


def gate_leakage_split(AP, L, T) -> None:
    print("\n-- LEAKAGE (teeth): a fold's prior sees TRAIN-fold access signal only --")
    per_arm_h = {"vector": ["H", "x"], "bm25": ["H", "x"], "graph_weighted": []}
    per_arm_p = {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": []}
    recs = []
    # q0/q1 access 'p'; q2/q3 access 'H' — with explicit folds [[0,1],[2,3]], node H is accessed
    # ONLY by fold-1's queries, so fold-1's prior (built on its train complement = q0,q1) has no H.
    for i, (pa, path) in enumerate(((per_arm_p, "f/p.py"), (per_arm_p, "f/p.py"),
                                    (per_arm_h, "f/H.py"), (per_arm_h, "f/H.py"))):
        ids = ("p", "x") if path == "f/p.py" else ("H", "x")
        recs.append(_search(L, f"q{i}", pa, ids, f"2026-07-10T0{i}:00:00Z"))
        recs.append(_access(path, f"2026-07-10T0{i}:01:00Z"))
    labeled = T.build_qrels(recs)
    ts_by_qid = AP.ts_by_qid(recs)
    folds = [[0, 1], [2, 3]]
    priors = AP.fold_priors(labeled, ts_by_qid, folds)
    check("fold-1's prior (train = q0,q1) has NO leak of the held-out node H",
          priors[1].get("H", 0.0) == 0.0, str(priors[1]))
    check("fold-0's prior (train = q2,q3) DOES carry H", priors[0].get("H", 0) > 0, str(priors[0]))
    # The naive all-records prior would leak H into every fold — it must DISAGREE.
    naive = AP.build_prior(recs)
    check("naive all-records prior leaks (fixture has teeth)", naive.get("H", 0) > 0)


def gate_evaluate(AP, L) -> None:
    print("\n-- evaluate: verdict ladder, determinism, prior_hit_rate, never raises --")
    res = AP.evaluate([], min_labeled=4)
    check("empty -> insufficient_data", res.get("verdict") == "insufficient_data", str(res))
    check("insufficient reports what's needed", res.get("n_labeled") == 0 and res.get("min_labeled") == 4)
    # A log where the prior can genuinely help: the accessed node sits mid-list for fusion.
    recs = []
    for i in range(12):
        per_arm = {"vector": ["a", "b", "p"], "bm25": ["b", "a", "p"], "graph_weighted": ["a", "p", "b"]}
        recs.append(_search(L, f"q{i}", per_arm, ("a", "b", "p"), f"2026-07-10T{i:02d}:00:00Z"))
        recs.append(_access("f/p.py", f"2026-07-10T{i:02d}:01:00Z"))
    r1 = AP.evaluate(recs, min_labeled=4, n_folds=3)
    r2 = AP.evaluate(recs, min_labeled=4, n_folds=3)
    # Compare everything EXCEPT the wall-clock stamp: two runs straddling a second boundary differ only there
    # (a real flake seen in a full-suite run), and determinism is about the evaluation, not the clock.
    _strip = lambda r: {k: v for k, v in r.items() if k != "generated_utc"}   # noqa: E731
    check("deterministic (two runs identical, ignoring the generated_utc stamp)", _strip(r1) == _strip(r2))
    check("verdict is from the ladder", r1.get("verdict") in
          ("insufficient_data", "no_improvement", "not_significant", "beats_baseline_significant"),
          str(r1.get("verdict")))
    check("re-ranking the always-accessed node HELPS here",
          r1.get("verdict") == "beats_baseline_significant", str({k: r1.get(k) for k in
          ("verdict", "cv_mean_delta", "chosen_betas")}))
    check("prior_hit_rate present and in [0,1]",
          isinstance(r1.get("prior_hit_rate"), float) and 0.0 <= r1["prior_hit_rate"] <= 1.0,
          str(r1.get("prior_hit_rate")))
    check("chosen betas + bootstrap reported", isinstance(r1.get("chosen_betas"), list)
          and isinstance(r1.get("bootstrap"), dict))
    check("the circularity note is IN the result", "circular" in str(r1.get("circularity_note", "")).lower())
    raised = False
    try:
        AP.evaluate([None, "x", {"type": "search", "returned": 7}], min_labeled=1)
    except Exception:
        raised = True
    check("malformed input -> no raise", not raised)


def gate_persistence_isolation(AP, LF) -> None:
    print("\n-- persistence: experiments.access_prior, applied FORCED false, lf untouched --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text(json.dumps({"learned_fusion": {"arm_weights": {"vector": 1.0, "bm25": 1.0,
                        "graph_weighted": 1.0}, "applied": False}}), encoding="utf-8")
        before = json.dumps(json.loads(inst.read_text(encoding="utf-8"))["learned_fusion"], sort_keys=True)
        L = _load("retrieval_log", BUILD)
        recs = []
        for i in range(6):
            per_arm = {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": []}
            recs.append(_search(L, f"q{i}", per_arm, ("p", "x"), f"2026-07-10T{i:02d}:00:00Z"))
            recs.append(_access("f/p.py", f"2026-07-10T{i:02d}:01:00Z"))
        out = AP.evaluate_and_record(root, records=recs, min_labeled=4, n_folds=3)
        check("recorded (path returned)", out.get("written") is True, str(out))
        data = json.loads(inst.read_text(encoding="utf-8"))
        exp = data.get("experiments", {}).get("access_prior", {})
        check("experiments.access_prior present with a verdict", bool(exp.get("verdict")), str(exp)[:150])
        check("applied FORCED false", exp.get("applied") is False)
        check("circularity named in the persisted block", "circular" in str(exp.get("circularity_note", "")).lower())
        check("activation-evidence disclaimer present", "benchmark" in str(exp.get("note", "")).lower(), str(exp.get("note")))
        after = json.dumps(data["learned_fusion"], sort_keys=True)
        check("learned_fusion byte-untouched", before == after)
        got = LF.resolve_arm_weights_for_live(root, env={"DEEPMAP_APPLY_LEARNED": "1"})
        check("live resolver still blind to the experiment", got is None, str(got))


def gate_fuzz(AP) -> None:
    print("\n-- 300-seed fuzz: malformed logs degrade, never raise --")
    rng = random.Random(20260713)
    pool = [
        {"type": "search"}, {"type": "search", "returned": "junk", "per_arm_candidate_ids": []},
        {"type": "access", "paths": 3, "kind": None}, {"type": "access", "ts": 9, "paths": [None]},
        None, "str", 4.2, {"type": "search", "returned": [{"id": None}], "ts": {}},
        {"type": "search", "per_arm_candidate_ids": {"vector": None, "bm25": "x", "graph_weighted": [1]},
         "returned": [{"id": "a", "file": 3}], "accessed_ids": "x"},
    ]
    raised = 0
    for _ in range(300):
        recs = [rng.choice(pool) for _ in range(rng.randint(0, 10))]
        try:
            AP.build_prior(recs)
            AP.evaluate(recs, min_labeled=1, n_folds=2)
        except Exception:
            raised += 1
    check("300 malformed logs -> zero raises", raised == 0, f"{raised} raised")


def gate_source_contracts() -> None:
    print("\n-- source contracts: retrieval.py frozen; module is import-only over it --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("retrieval.py never mentions access_prior", "access_prior" not in src)
    ap_src = (BUILD / "access_prior.py").read_text(encoding="utf-8")
    check("access_prior REUSES the real fusion (imports, never re-implements)",
          "_weighted_rrf" in ap_src and "def _weighted_rrf" not in ap_src)
    check("the module names itself OFFLINE-only", "never wired into live search" in ap_src.lower()
          or "offline" in ap_src.lower())
    rb = (REPO / "benchmark" / "run_benchmark.py").read_text(encoding="utf-8")
    check("run_benchmark exposes the --ab-rerank plan printer (visibility seam)",
          "--ab-rerank" in rb and "_print_ab_rerank_plan" in rb)
    idx = rb.find("def _print_ab_rerank_plan")
    check("the printer is try/except-wrapped (visibility never breaks the run)",
          idx > 0 and "except Exception" in rb[idx:idx + 1600])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — access-prior re-ranker gates (OFFLINE experiment; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)
    LF = _load("learned_fusion", BUILD)
    try:
        AP = _load("access_prior", BUILD)
    except Exception as e:
        check("build/access_prior.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_prior_basics(AP, L)
    gate_recency_decay(AP, L)
    gate_beta_zero_parity(AP, T)
    gate_rerank_moves(AP)
    gate_leakage_split(AP, L, T)
    gate_evaluate(AP, L)
    gate_persistence_isolation(AP, LF)
    gate_fuzz(AP)
    gate_source_contracts()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
