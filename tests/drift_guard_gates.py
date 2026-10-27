#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the RM-1 DRIFT GUARD (newest-slice re-validation + auto-revert).

On every learning-loop re-tune (and every manual `deepmap tune`), the STORED learned weights are
re-validated against equal on the NEWEST slice of labeled queries. A statistically significant
degradation (bootstrap CI entirely below zero — the mirror of the tuner's win rule) AUTO-REVERTS
to equal and says so: printed, and recorded under learned_fusion.drift with the full pre-revert
state in drift.previous. A stale profile is never silently kept; the only `applied` transition
the guard ever makes is true -> false.

Locks:
  - verdict ladder: no_learned_weights | not_learned | insufficient_data | ok | degraded,
  - the slice is the NEWEST tail in log STREAM order (never wall-clock parsing),
  - the evaluation mirrors the live profile->global ladder (a degraded PROFILE with an equal
    global still reverts),
  - degraded: equal restored, profiles cleared, applied true->false, reason=drift_reverted,
    drift.previous carries the old state; ok: weights untouched, drift block still written,
  - the drift block survives a subsequent write_instance_weights merge,
  - teeth: the STATISTIC drives the revert (favorable newest data -> ok), and the slice is the
    newest TAIL (old degradation with a healthy tail -> ok),
  - never raises; wired in sync's _maybe_auto_tune BEFORE tune_profiles and in cmd_tune.
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


BM25_HEAVY = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 0.5}
EQUAL = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}

# per_arm patterns: PRO = the bm25-heavy stored weights rank the accessed 'p' first and equal does
# not (bm25 says p first, the other two say x first); ANTI = the mirror (stored loses, equal wins).
PRO_STORED = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
ANTI_STORED = {"vector": ["p", "x"], "bm25": ["x", "p"], "graph_weighted": ["p", "x"]}


def _write_instance(root: Path, lf: dict) -> None:
    inst = root / ".deepmap" / "instance.json"
    inst.parent.mkdir(parents=True, exist_ok=True)
    inst.write_text(json.dumps({"learned_fusion": lf}), encoding="utf-8")


def _read_lf(root: Path) -> dict:
    return json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8")).get("learned_fusion", {})


def _seed(root: Path, L, phases) -> None:
    """phases = [(n, per_arm), ...] appended in order — stream order IS the age order."""
    i = 0
    for n, per_arm in phases:
        for _ in range(n):
            returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
            L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i:04d}"))
            L.append_record(root, {"type": "access", "ts": f"t{i:04d}b", "paths": ["f/p.py"],
                                   "symbols": [], "kind": "edit"})
            i += 1


LF_STORED = {"arm_weights": dict(BM25_HEAVY), "applied": False, "beats_equal": True,
             "significant": True, "reason": "learned_beats_equal", "objective": "ndcg",
             "ndcg_k": 10, "rrf_k": 60, "profiles": {}, "logging_enabled": True}


def gate_verdict_ladder(T, L) -> None:
    print("\n-- verdict ladder: no_learned_weights / not_learned / insufficient_data --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = T.check_drift(root)
        check("no instance -> no_learned_weights", d.get("verdict") == "no_learned_weights", str(d))
        _write_instance(root, {"arm_weights": "garbage"})
        check("invalid stored weights -> no_learned_weights",
              T.check_drift(root).get("verdict") == "no_learned_weights")
        _write_instance(root, {"arm_weights": dict(EQUAL), "profiles": {}})
        check("stored == equal, no profiles -> not_learned",
              T.check_drift(root).get("verdict") == "not_learned")
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(10, PRO_STORED)])
        d = T.check_drift(root)   # default min_n=20 > 10 labeled
        check("below min_n -> insufficient_data", d.get("verdict") == "insufficient_data", str(d))
        check("insufficient_data reports the counts", d.get("n_labeled") == 10 and d.get("min_n") == 20, str(d))


def gate_ok_verdict(T, L) -> None:
    print("\n-- healthy newest slice -> ok; drift block written; weights untouched --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(40, PRO_STORED), (25, PRO_STORED)])
        d = T.apply_drift_guard(root)
        check("verdict ok", d.get("verdict") == "ok", str(d))
        check("not reverted", d.get("reverted") is False)
        lf = _read_lf(root)
        check("weights untouched", lf.get("arm_weights") == BM25_HEAVY, str(lf.get("arm_weights")))
        check("drift block recorded (self-checking is visible)",
              lf.get("drift", {}).get("verdict") == "ok" and bool(lf.get("drift", {}).get("checked_utc")))
        check("mean/CI reported", isinstance(d.get("mean_delta"), float)
              and isinstance(d.get("ci_lo"), float) and isinstance(d.get("ci_hi"), float))
        check("slice math: n_slice = max(min_n, ceil(frac*n))", d.get("n_slice") == 20, str(d.get("n_slice")))


def gate_degradation_reverts(T, L) -> None:
    print("\n-- significant degradation on the NEWEST slice -> auto-revert to equal, honestly recorded --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(40, PRO_STORED), (25, ANTI_STORED)])
        d = T.apply_drift_guard(root)
        check("verdict degraded", d.get("verdict") == "degraded", str(d))
        check("reverted True + written", d.get("reverted") is True and d.get("written") is True)
        check("CI entirely below zero (the mirror of the win rule)",
              isinstance(d.get("ci_hi"), float) and d["ci_hi"] < 0.0, str(d.get("ci_hi")))
        lf = _read_lf(root)
        check("equal weights restored", lf.get("arm_weights") == EQUAL, str(lf.get("arm_weights")))
        check("reason = drift_reverted", lf.get("reason") == "drift_reverted")
        check("beats_equal/significant cleared",
              lf.get("beats_equal") is False and lf.get("significant") is False)
        check("profiles cleared", lf.get("profiles") == {})
        check("drift.previous carries the pre-revert vector",
              lf.get("drift", {}).get("previous", {}).get("arm_weights") == BM25_HEAVY,
              str(lf.get("drift", {}).get("previous")))
        check("unrelated lf sub-key survives the revert (logging_enabled)",
              lf.get("logging_enabled") is True)


def gate_applied_true_flip(T, L) -> None:
    print("\n-- an APPLIED (live) profile that drifts is flipped applied:true -> false --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(LF_STORED, applied=True, applied_utc="2026-07-01T00:00:00Z"))
        _seed(root, L, [(40, PRO_STORED), (25, ANTI_STORED)])
        d = T.apply_drift_guard(root)
        lf = _read_lf(root)
        check("applied flipped to false", lf.get("applied") is False)
        check("drift.previous.applied preserves the history", lf.get("drift", {}).get("previous", {}).get("applied") is True)
        check("was_applied surfaced in the check result", d.get("was_applied") is True)
        check("the historic applied_utc is preserved", lf.get("applied_utc") == "2026-07-01T00:00:00Z")


def gate_profile_ladder_mirror(T, L) -> None:
    print("\n-- the evaluation mirrors the live profile->global ladder --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Global EQUAL (would never degrade on its own) but a degraded PROFILE for the queries'
        # bucket ('default'). If the guard ignored profiles, every delta would be 0 -> ok.
        _write_instance(root, dict(LF_STORED, arm_weights=dict(EQUAL),
                                   profiles={"default": {"arm_weights": dict(BM25_HEAVY), "rrf_k": 60}}))
        _seed(root, L, [(40, PRO_STORED), (25, ANTI_STORED)])
        d = T.apply_drift_guard(root)
        check("a degraded PROFILE alone triggers the revert (ladder mirrored)",
              d.get("verdict") == "degraded", str(d.get("verdict")))
        check("profiles cleared on the profile-driven revert", _read_lf(root).get("profiles") == {})


def gate_survives_retune(T, L) -> None:
    print("\n-- the drift block survives a subsequent write_instance_weights merge --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(40, PRO_STORED), (25, PRO_STORED)])
        T.apply_drift_guard(root)
        res = T.tune_profiles(root=root, min_labeled=10)
        T.write_instance_weights(root, res)
        lf = _read_lf(root)
        check("drift block still present after a re-tune", lf.get("drift", {}).get("verdict") == "ok",
              str(lf.get("drift")))


def gate_teeth(T, L) -> None:
    print("\n-- TEETH: the statistic drives the revert, and the slice is the newest TAIL --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Same phase-2 VOLUME, but the newest data still favors the stored weights -> ok.
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(40, ANTI_STORED), (25, PRO_STORED)])
        d = T.apply_drift_guard(root)
        check("old degradation + healthy newest tail -> ok, NOT reverted (newest-tail selection)",
              d.get("verdict") == "ok" and d.get("reverted") is False, str(d.get("verdict")))
        check("weights kept", _read_lf(root).get("arm_weights") == BM25_HEAVY)


def gate_never_raises(T) -> None:
    print("\n-- never raises --")
    for label, root in (("bad root", "\x00::bad::"), ("None root", None)):
        raised = False
        try:
            d = T.apply_drift_guard(root)
        except Exception:
            raised = True
            d = None
        check(f"{label} -> a dict, no raise", (not raised) and isinstance(d, dict))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text("{garbage", encoding="utf-8")
        (root / ".deepmap" / "retrieval_log.jsonl").write_text('{"type": "search"}\nnot json\n', encoding="utf-8")
        raised = False
        try:
            d = T.apply_drift_guard(root)
        except Exception:
            raised = True
            d = None
        check("garbage instance + malformed log -> a dict, no raise", (not raised) and isinstance(d, dict))


def gate_wiring(T) -> None:
    print("\n-- wiring: sync runs the guard BEFORE tune_profiles; cmd_tune runs it too --")
    sync_src = (BUILD / "sync.py").read_text(encoding="utf-8")
    idx = sync_src.find("def _maybe_auto_tune")
    body = sync_src[idx:] if idx > 0 else ""
    i_guard, i_tune = body.find("apply_drift_guard"), body.find("tune_profiles")
    check("sync: apply_drift_guard wired", i_guard > 0)
    check("sync: guard runs BEFORE tune_profiles", 0 < i_guard < i_tune, f"{i_guard} vs {i_tune}")
    check("sync: a revert is PRINTED (never silent)", "drift guard" in body and "REVERTED" in body)
    cli_src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("cmd_tune runs the guard on the full-run path", "apply_drift_guard" in cli_src)
    check("cmd_tune SKIPS the guard on --dry-run (a dry run writes nothing)",
          "if not args.dry_run" in cli_src)
    # Determinism (behavioral): identical input -> identical verdict + CI.
    L = _load("retrieval_log", BUILD)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(LF_STORED))
        _seed(root, L, [(40, PRO_STORED), (25, ANTI_STORED)])
        d1 = T.check_drift(root)
        d2 = T.check_drift(root)
        d1.pop("checked_utc", None), d2.pop("checked_utc", None)
        check("check_drift is deterministic (same input -> same stats)", d1 == d2)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — drift-guard gates (newest-slice re-validation; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)
    if not hasattr(T, "check_drift") or not hasattr(T, "apply_drift_guard"):
        check("tune_weights exposes check_drift + apply_drift_guard", False, "missing")
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_verdict_ladder(T, L)
    gate_ok_verdict(T, L)
    gate_degradation_reverts(T, L)
    gate_applied_true_flip(T, L)
    gate_profile_ladder_mirror(T, L)
    gate_survives_retune(T, L)
    gate_teeth(T, L)
    gate_never_raises(T)
    gate_wiring(T)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
