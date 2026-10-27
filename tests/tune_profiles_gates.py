#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for tune_profiles() — the COMPREHENSIVE RM-1 tuner (design §1, §5-§6).

Beyond the foundation's single global MRR grid, this learns a graded objective (default nDCG),
PER-INTENT weight profiles, and validates with k-fold CV + a bootstrap significance gate. It never
touches live ranking (applied:false). Locks:
  - default objective is graded nDCG; a strong single-arm signal -> beats_equal AND statistically
    significant, with that arm up-weighted, applied:false,
  - AUTO-REVERT: no arm helps -> equal, not significant, reason 'no_improvement',
  - SIGNIFICANCE gate: significant requires the bootstrap CI lower bound > 0,
  - PER-INTENT profiles: a bucket whose own optimum beats the GLOBAL vector earns a profile row;
    otherwise it falls back to global (layered cold-start ladder),
  - coord and grid optimizers both find a beats-equal solution + are deterministic,
  - RRF_K stays 60 unless tune_rrf_k is set; the searched k is only RECORDED (never applied),
  - persistence writes profiles/objective/significant/bootstrap/per-slice, applied:false, and MERGES
    (a pre-existing logging_enabled flag survives a re-tune),
  - tune() (the legacy single-split path) is unchanged: metric 'mrr', applied:false.
"""
from __future__ import annotations

import importlib.util
import json
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


def _log(L, specs, kind="edit"):
    """specs: list of (query, per_arm, positive_id). Returns a records list (search + access)."""
    recs = []
    for i, (q, per_arm, pos) in enumerate(specs):
        ids = list(dict.fromkeys(sum(per_arm.values(), [])))
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1} for rid in ids]
        recs.append(L.build_record(q, per_arm, returned, ts=f"t{i}"))
        recs.append({"type": "access", "v": 2, "paths": [f"f/{pos}.py"], "symbols": [], "kind": kind, "ts": f"t{i}b"})
    return recs


def gate_coldstart(T, L) -> None:
    print("\n-- cold-start: below the labeled threshold -> equal, below_threshold, applied:false --")
    bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _log(L, [("q", bm25, "p")] * 5)
    res = T.tune_profiles(records=recs, min_labeled=50)
    check("below threshold -> equal weights", res["arm_weights"] == T.EQUAL_WEIGHTS, str(res["arm_weights"]))
    check("reason 'below_threshold'", res["reason"] == "below_threshold", res["reason"])
    check("applied is False", res["applied"] is False)
    check("no profiles below threshold", res["profiles"] == {})


def gate_strong_signal(T, L) -> None:
    print("\n-- strong signal: nDCG default, beats_equal + SIGNIFICANT, arm up-weighted, applied:false --")
    bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _log(L, [("q", bm25, "p")] * 200)
    res = T.tune_profiles(records=recs, min_labeled=50)
    check("objective defaults to nDCG", res["objective"] == "ndcg" and res["ndcg_k"] == 10, str((res["objective"], res["ndcg_k"])))
    check("beats_equal True", res["beats_equal"] is True, str(res["reason"]))
    check("statistically SIGNIFICANT (CI lo > 0)", res["significant"] is True and res["bootstrap"]["ci_lo"] > 0, str(res["bootstrap"]))
    check("reason 'learned_beats_equal'", res["reason"] == "learned_beats_equal", res["reason"])
    check("bm25 up-weighted over vector/graph",
          res["arm_weights"]["bm25"] > res["arm_weights"]["vector"]
          and res["arm_weights"]["bm25"] > res["arm_weights"]["graph_weighted"], str(res["arm_weights"]))
    check("applied is False (G4 gate)", res["applied"] is False)
    check("records n_folds + per_slice_delta", res["n_folds"] >= 2 and isinstance(res["per_slice_delta"], dict))


def gate_autorevert(T, L) -> None:
    print("\n-- auto-revert: no arm helps (positive rank-1 everywhere) -> equal, not significant --")
    eq = {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": ["p", "x"]}
    recs = _log(L, [("q", eq, "p")] * 200)
    res = T.tune_profiles(records=recs, min_labeled=50)
    check("equal weights", res["arm_weights"] == T.EQUAL_WEIGHTS, str(res["arm_weights"]))
    check("not significant", res["significant"] is False)
    check("reason 'no_improvement'", res["reason"] == "no_improvement", res["reason"])
    check("beats_equal False", res["beats_equal"] is False)


def gate_per_intent_profiles(T, L) -> None:
    print("\n-- per-intent profiles: an impact bucket that wants graph earns its own row --")
    graph_first = {"vector": ["x", "p"], "bm25": ["x", "p"], "graph_weighted": ["p", "x"]}
    bm25_first = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    specs = [("who calls parse_config", graph_first, "p")] * 120 \
        + [("handle the user login flow properly today", bm25_first, "p")] * 120
    res = T.tune_profiles(records=_log(L, specs), min_labeled=50, min_labeled_bucket=40)
    check("global vector learned (bm25-favoring)", res["arm_weights"]["bm25"] >= res["arm_weights"]["graph_weighted"], str(res["arm_weights"]))
    prof = res.get("profiles", {})
    check("the impact bucket earned a profile row", "impact" in prof, str(list(prof)))
    if "impact" in prof:
        w = prof["impact"]["arm_weights"]
        check("impact profile up-weights graph over bm25", w["graph_weighted"] > w["bm25"], str(w))
        check("impact profile beats_global recorded", prof["impact"].get("beats_global") is True)


def gate_optimizers(T, L) -> None:
    print("\n-- coord and grid both beat equal + are deterministic --")
    bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _log(L, [("q", bm25, "p")] * 200)
    g = T.tune_profiles(records=recs, min_labeled=50, optimizer="grid")
    c = T.tune_profiles(records=recs, min_labeled=50, optimizer="coord")
    check("grid beats equal", g["beats_equal"] is True)
    check("coord beats equal", c["beats_equal"] is True, str(c["reason"]))
    check("coord score within tolerance of grid", c["holdout_mrr_learned"] >= g["holdout_mrr_learned"] - 1e-9,
          f'{c["holdout_mrr_learned"]} vs {g["holdout_mrr_learned"]}')
    g2 = T.tune_profiles(records=[dict(r) for r in recs], min_labeled=50, optimizer="grid")
    check("grid deterministic", g2["arm_weights"] == g["arm_weights"] and g2["significant"] == g["significant"])
    c2 = T.tune_profiles(records=[dict(r) for r in recs], min_labeled=50, optimizer="coord")
    check("coord deterministic", c2["arm_weights"] == c["arm_weights"])


def gate_rrf_k(T, L) -> None:
    print("\n-- RRF_K stays 60 unless tune_rrf_k; searched k only RECORDED --")
    bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _log(L, [("q", bm25, "p")] * 200)
    off = T.tune_profiles(records=recs, min_labeled=50, tune_rrf_k=False)
    on = T.tune_profiles(records=recs, min_labeled=50, tune_rrf_k=True, optimizer="coord")
    check("tune_rrf_k off -> rrf_k == 60", off["rrf_k"] == 60, str(off["rrf_k"]))
    check("tune_rrf_k on -> rrf_k in the searched grid {30,60,90}", on["rrf_k"] in (30, 60, 90), str(on["rrf_k"]))
    check("applied still False with rrf_k search", on["applied"] is False)


def gate_persistence(T, L) -> None:
    print("\n-- write_instance_weights: profiles/objective/significant persisted; applied:false; MERGES --")
    fn = getattr(T, "write_instance_weights", None)
    check("write_instance_weights exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        # Pre-existing opt-in flag under learned_fusion MUST survive a re-tune.
        inst.write_text(json.dumps({"project": "deepmap",
                                    "learned_fusion": {"logging_enabled": True}}), encoding="utf-8")
        bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
        res = T.tune_profiles(records=_log(L, [("q", bm25, "p")] * 200), min_labeled=50)
        fn(root, res)
        lf = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion", {})
        check("applied:false (G4 gate)", lf.get("applied") is False)
        check("persists objective + significant + rrf_k", lf.get("objective") == "ndcg"
              and "significant" in lf and lf.get("rrf_k") == 60, str({k: lf.get(k) for k in ("objective", "significant", "rrf_k")}))
        check("persists a bootstrap block + per_slice_delta", isinstance(lf.get("bootstrap"), dict) and "per_slice_delta" in lf)
        check("persists a profiles table", isinstance(lf.get("profiles"), dict))
        check("MERGE preserved the pre-existing logging_enabled flag", lf.get("logging_enabled") is True, str(lf.get("logging_enabled")))


def gate_tune_backcompat(T, L) -> None:
    print("\n-- tune() (legacy single-split path) unchanged: metric 'mrr', applied:false --")
    bm25 = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _log(L, [("q", bm25, "p")] * 8)
    res = T.tune(records=recs, min_labeled=4)
    check("tune() metric == 'mrr'", res["metric"] == "mrr", res.get("metric"))
    check("tune() applied is False", res["applied"] is False)
    check("tune() still learns on the fixture", res["beats_equal"] is True, str(res["reason"]))


def gate_never_raises(T) -> None:
    print("\n-- tune_profiles never raises on garbage / empty --")
    for bad in (None, [], [123, "x", {}], [{"type": "search"}], [{"type": "access", "kind": 5}]):
        try:
            r = T.tune_profiles(records=bad, min_labeled=1)
            ok = (r["arm_weights"] == T.EQUAL_WEIGHTS and r["applied"] is False and isinstance(r["reason"], str))
        except Exception as exc:
            ok = False
            r = f"raised {exc}"
        check(f"tune_profiles({bad!r}) -> equal/applied:false, no raise", ok, str(r))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — tune_profiles gates (comprehensive RM-1 tuner; no Neo4j, no spend)")
    print("=" * 68)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    L = _load("retrieval_log", BUILD)
    T = _load("tune_weights", BUILD)

    gate_coldstart(T, L)
    gate_strong_signal(T, L)
    gate_autorevert(T, L)
    gate_per_intent_profiles(T, L)
    gate_optimizers(T, L)
    gate_rrf_k(T, L)
    gate_persistence(T, L)
    gate_tune_backcompat(T, L)
    gate_never_raises(T)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
