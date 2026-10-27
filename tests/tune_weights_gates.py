#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/tune_weights.py — the RM-1 OFFLINE weight tuner.

The tuner reads the local retrieval log (opt-in), treats accessed_ids as soft positive
qrels, and replays the REAL rank-based `_weighted_rrf` under a grid of per-arm weights to
find weights that lift accessed results — with the cold-start safety that makes it not
naive. It NEVER touches live ranking: the result is written to .deepmap/instance.json with
`applied: false` (G4-gated), and equal weights stay the default.

Locks (design §5–§7):
  - it replays the REAL retrieval._weighted_rrf / RRF_K (identity, not a re-impl),
  - THRESHOLD: below N labeled queries -> equal weights, reason 'below_threshold' (cold-start),
  - LEARNING WORKS: when one arm consistently surfaces the accessed result, the tuner learns
    to up-weight it and BEATS equal on a held-out slice,
  - AUTO-REVERT: when learned does not beat equal on holdout -> revert to equal,
  - REGULARIZE toward equal: no weight deviates past the cap,
  - EXPLORATION floor: no arm is starved below the floor,
  - DETERMINISTIC: same log -> same result,
  - applied is ALWAYS False (G4 not open); instance.json writer merges + is zero-egress + never raises.
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


def _labeled(n, per_arm, positive, L, base_ts=0):
    """n search records, all with the same per_arm shape + one accessed positive id."""
    recs = []
    for i in range(n):
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1}
                    for rid in dict.fromkeys(sum(per_arm.values(), []))]
        r = L.build_record(f"q{i}", per_arm, returned, ts=f"t{base_ts + i}")
        r["accessed_ids"] = [positive]
        recs.append(r)
    return recs


def gate_uses_real_fusion(T, R) -> None:
    print("\n-- the tuner replays the REAL retrieval._weighted_rrf / RRF_K (no re-impl) --")
    check("tune_weights._weighted_rrf IS retrieval._weighted_rrf",
          getattr(T, "_weighted_rrf", None) is R._weighted_rrf)
    check("tune_weights.RRF_K == retrieval.RRF_K", getattr(T, "RRF_K", None) == R.RRF_K)


def gate_threshold_coldstart(T, L) -> None:
    print("\n-- cold-start: below the labeled-query threshold -> equal weights --")
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _labeled(3, per_arm, "p", L)
    res = T.tune(records=recs, min_labeled=10)
    check("below threshold -> equal weights", res["arm_weights"] == {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}, str(res["arm_weights"]))
    check("below threshold -> beats_equal False", res["beats_equal"] is False)
    check("below threshold -> reason 'below_threshold'", res["reason"] == "below_threshold", res["reason"])
    check("cold-start still reports n_labeled_queries", res["n_labeled_queries"] == 3, str(res["n_labeled_queries"]))


def gate_learning_beats_equal(T, L) -> None:
    print("\n-- learning: one arm (bm25) consistently surfaces the positive -> learn + BEAT equal --")
    # vector+graph rank the decoy 'x' first; bm25 ranks the positive 'p' first. Learned optimum
    # down-weights vector/graph and up-weights bm25 (all within the deviation cap) so 'p' wins.
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _labeled(8, per_arm, "p", L)
    res = T.tune(records=recs, min_labeled=4)
    check("learned beats_equal True", res["beats_equal"] is True, str(res))
    check("reason 'learned_beats_equal'", res["reason"] == "learned_beats_equal", res["reason"])
    check("bm25 weight was raised above vector/graph",
          res["arm_weights"]["bm25"] > res["arm_weights"]["vector"]
          and res["arm_weights"]["bm25"] > res["arm_weights"]["graph_weighted"], str(res["arm_weights"]))
    check("holdout MRR(learned) > holdout MRR(equal)",
          res["holdout_mrr_learned"] > res["holdout_mrr_equal"],
          f'{res["holdout_mrr_learned"]} vs {res["holdout_mrr_equal"]}')
    # applied stays False — live ranking is untouched (G4 gate).
    check("applied is False (G4 not open — never auto-applied to live ranking)", res["applied"] is False)


def gate_autorevert_when_not_better(T, L) -> None:
    print("\n-- auto-revert: no arm helps (positive already rank-1 everywhere) -> revert to equal --")
    per_arm = {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": ["p", "x"]}
    recs = _labeled(8, per_arm, "p", L)
    res = T.tune(records=recs, min_labeled=4)
    check("learned does not beat equal -> equal weights",
          res["arm_weights"] == {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}, str(res["arm_weights"]))
    check("beats_equal False", res["beats_equal"] is False)
    check("reason 'reverted_not_better'", res["reason"] == "reverted_not_better", res["reason"])


def gate_regularize_and_floor(T, L) -> None:
    print("\n-- regularize toward equal (deviation cap) + exploration floor --")
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _labeled(8, per_arm, "p", L)
    res = T.tune(records=recs, min_labeled=4, max_deviation=0.5, exploration_floor=0.25)
    w = res["arm_weights"]
    check("no weight deviates more than the cap from equal (<=0.5)",
          all(abs(v - 1.0) <= 0.5 + 1e-9 for v in w.values()), str(w))
    check("no arm starved below the exploration floor (>=0.25)",
          all(v >= 0.25 - 1e-9 for v in w.values()), str(w))


def gate_determinism(T, L) -> None:
    print("\n-- deterministic: identical log -> identical result --")
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    recs = _labeled(8, per_arm, "p", L)
    a = T.tune(records=[dict(r) for r in recs], min_labeled=4)
    b = T.tune(records=[dict(r) for r in recs], min_labeled=4)
    check("two runs -> identical arm_weights", a["arm_weights"] == b["arm_weights"], f'{a["arm_weights"]} vs {b["arm_weights"]}')
    check("two runs -> identical beats_equal/reason", (a["beats_equal"], a["reason"]) == (b["beats_equal"], b["reason"]))


def gate_reads_from_root_and_folds(T, L) -> None:
    print("\n-- tune(root=...) reads the JSONL log AND folds access events itself --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Write raw search records + access events; the tuner must fold them into qrels.
        per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
        for i in range(8):
            returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1} for rid in ("x", "p")]
            L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
            L.append_record(root, {"type": "access", "ts": f"t{i}b", "paths": ["f/p.py"], "symbols": []})
        res = T.tune(root=root, min_labeled=4)
        check("root-mode found 8 labeled queries after folding", res["n_labeled_queries"] == 8, str(res["n_labeled_queries"]))
        check("root-mode learned + beats equal", res["beats_equal"] is True, str(res))


def gate_instance_writer(T, L) -> None:
    print("\n-- write_instance_weights: merges, applied:false, zero-egress, never raises --")
    fn = getattr(T, "write_instance_weights", None)
    check("write_instance_weights exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Pre-existing unrelated instance content must be preserved.
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text(json.dumps({"project": "deepmap", "other": 1}), encoding="utf-8")
        res = T.tune(records=_labeled(8, {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}, "p", L), min_labeled=4)
        out = fn(root, res)
        check("returns the instance.json path", out is not None and Path(out) == inst, str(out))
        data = json.loads(inst.read_text(encoding="utf-8"))
        check("preserved the pre-existing keys", data.get("project") == "deepmap" and data.get("other") == 1, str(data))
        lf = data.get("learned_fusion", {})
        check("wrote learned_fusion.arm_weights", isinstance(lf.get("arm_weights"), dict))
        check("learned_fusion.applied is False (G4 gate — never live)", lf.get("applied") is False, str(lf.get("applied")))
        check("records beats_equal + metric + provenance",
              "beats_equal" in lf and lf.get("metric") == "mrr" and "generated_utc" in lf, str(lf))
        # Never raises on a hopeless root.
        raised = False
        try:
            bad = fn("\x00::bad::", res)
        except Exception:
            raised = True
            bad = "raised"
        check("never raises on a bad root (returns None)", (not raised) and bad is None, str(bad))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — tune_weights gates (RM-1 offline tuner; no Neo4j, no spend)")
    print("=" * 68)
    # Import retrieval the SAME way tune_weights does (plain module name) so the identity
    # assertion below reflects real in-process usage, not an importlib dual-load artifact.
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import retrieval as R  # noqa: E402
    L = _load("retrieval_log", BUILD)
    T = _load("tune_weights", BUILD)

    gate_uses_real_fusion(T, R)
    gate_threshold_coldstart(T, L)
    gate_learning_beats_equal(T, L)
    gate_autorevert_when_not_better(T, L)
    gate_regularize_and_floor(T, L)
    gate_determinism(T, L)
    gate_reads_from_root_and_folds(T, L)
    gate_instance_writer(T, L)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
