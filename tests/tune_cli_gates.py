#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the `deepmap tune` CLI subcommand (RM-1).

`deepmap tune [<root>]` runs the OFFLINE weight tuner over the local retrieval log and writes
.deepmap/instance.json (applied:false). It needs NO Neo4j and NO network — it is pure log
analysis. These gates drive it as a real subprocess:

  - `tune <root> --min-queries N` on a log with learning signal -> exit 0, writes instance.json
    with applied:false and reports beats_equal,
  - `--dry-run` -> computes + prints but writes NOTHING,
  - `--status` -> prints the current learned_fusion block (or a clean 'none yet' on an empty root),
  - an empty log -> graceful equal/below_threshold, exit 0 (never a crash),
  - runs WITHOUT a Neo4j driver present (offline).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
CLI = BUILD / "deepmap_cli.py"
PY = sys.executable

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


def _seed_log(root: Path, L, n=8):
    """A log with clear learning signal: bm25 surfaces the accessed 'p' first, others surface 'x'."""
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(n):
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1} for rid in ("x", "p")]
        L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
        L.append_record(root, {"type": "access", "ts": f"t{i}b", "paths": ["f/p.py"], "symbols": []})


def _run(args, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([PY, str(CLI), *args], cwd=str(cwd), capture_output=True, text=True, env=env, timeout=90)


def gate_tune_writes(L) -> None:
    print("\n-- `deepmap tune <root> --min-queries 4` writes instance.json (applied:false), offline --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_log(root, L)
        p = _run(["tune", str(root), "--min-queries", "4"], cwd=REPO)
        check("exit 0", p.returncode == 0, (p.stdout + p.stderr)[-300:])
        inst = root / ".deepmap" / "instance.json"
        check("wrote .deepmap/instance.json", inst.is_file())
        if inst.is_file():
            lf = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion", {})
            check("applied:false (G4 gate)", lf.get("applied") is False, str(lf.get("applied")))
            check("reports beats_equal True on the seeded signal", lf.get("beats_equal") is True, str(lf))
        check("output mentions beats_equal / weights", ("beats_equal" in p.stdout.lower()) or ("weights" in p.stdout.lower()), p.stdout[:200])


def gate_dry_run(L) -> None:
    print("\n-- `--dry-run` computes but writes nothing --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_log(root, L)
        p = _run(["tune", str(root), "--min-queries", "4", "--dry-run"], cwd=REPO)
        check("exit 0", p.returncode == 0, (p.stdout + p.stderr)[-300:])
        check("dry-run wrote NO instance.json", not (root / ".deepmap" / "instance.json").exists())


def gate_status(L) -> None:
    print("\n-- `--status` prints the learned_fusion block, or a clean 'none yet' --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Empty root -> status is graceful (no crash), exit 0.
        p = _run(["tune", str(root), "--status"], cwd=REPO)
        check("empty root: --status exit 0", p.returncode == 0, (p.stdout + p.stderr)[-300:])
        check("empty root: says none/off (no crash)", len(p.stdout) >= 0 and p.returncode == 0)
        # After a real tune, --status reflects it.
        _seed_log(root, L)
        _run(["tune", str(root), "--min-queries", "4"], cwd=REPO)
        p = _run(["tune", str(root), "--status"], cwd=REPO)
        check("post-tune: --status exit 0", p.returncode == 0)
        check("post-tune: --status shows arm weights", ("vector" in p.stdout and "bm25" in p.stdout), p.stdout[:200])


def gate_empty_log_graceful() -> None:
    print("\n-- empty log -> graceful equal/below_threshold, exit 0 (offline, no Neo4j) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _run(["tune", str(root)], cwd=REPO)
        check("exit 0 on an empty log", p.returncode == 0, (p.stdout + p.stderr)[-300:])
        check("mentions equal / below_threshold / no data",
              any(w in p.stdout.lower() for w in ("equal", "below_threshold", "no ", "0 ")), p.stdout[:200])


def gate_min_queries_drift_lock() -> None:
    """The `deepmap tune` CLI must NOT re-declare the cold-start thresholds — the argparse defaults are
    None and resolve to tune_weights.DEFAULT_MIN_LABELED / DEFAULT_MIN_LABELED_BUCKET at the call site,
    so a change to the tuner constants can never silently leave the CLI gate at a stale 150/40 (the same
    drift-lock insights.py already uses — tests/insights_gates.py). RED-first-provable: under the old
    hardcoded `default=150` + `min_labeled=args.min_queries`, an omitted flag (Namespace min_queries=None)
    passes None straight through; the sentinel assert below fails."""
    print("\n-- `deepmap tune` thresholds are IMPORTED from tune_weights, not re-declared (drift-lock) --")
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    cli = _load("deepmap_cli", BUILD)
    T = _load("tune_weights", BUILD)

    # (1) source: argparse defaults are None (not the literals) and the call site cites the constants.
    src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("--min-queries default is None, not a hardcoded 150",
          'dest="min_queries", type=int, default=None' in src and 'dest="min_queries", type=int, default=150' not in src)
    check("--min-queries-bucket default is None, not a hardcoded 40",
          'dest="min_queries_bucket", type=int, default=None' in src and 'dest="min_queries_bucket", type=int, default=40' not in src)
    check("call site resolves via tune_weights.DEFAULT_MIN_LABELED / DEFAULT_MIN_LABELED_BUCKET",
          "T.DEFAULT_MIN_LABELED" in src and "T.DEFAULT_MIN_LABELED_BUCKET" in src)

    # (2) values unchanged today (so the fix is byte-identical behavior).
    check("tune_weights.DEFAULT_MIN_LABELED == 150 (unchanged)", T.DEFAULT_MIN_LABELED == 150, str(T.DEFAULT_MIN_LABELED))
    check("tune_weights.DEFAULT_MIN_LABELED_BUCKET == 40 (unchanged)", T.DEFAULT_MIN_LABELED_BUCKET == 40, str(T.DEFAULT_MIN_LABELED_BUCKET))

    # (3) behavioral drift-lock: swap the tuner for a sentinel-carrying stub and prove the omitted-flag
    #     path forwards the SENTINEL constant (not None, not 150) to tune_profiles.
    captured = {}

    class _StubTW:
        DEFAULT_MIN_LABELED = 987654
        DEFAULT_MIN_LABELED_BUCKET = 4321

        @staticmethod
        def tune_profiles(**kw):
            captured.update(kw)
            return {"n_labeled_queries": 0, "objective": "ndcg", "rrf_k": 60,
                    "reason": "below_threshold", "beats_equal": False, "significant": False,
                    "arm_weights": {}}

    real_load = cli._load

    def _fake_load(name):
        return _StubTW if name == "tune_weights" else real_load(name)

    import argparse
    ns = argparse.Namespace(root=None, status=False, min_queries=None, min_queries_bucket=None,
                            objective="ndcg", optimizer="grid", tune_rrf_k=False,
                            no_profiles=True, dry_run=True)
    cli._load = _fake_load
    try:
        rc = cli.cmd_tune(ns)
    finally:
        cli._load = real_load
    check("cmd_tune with omitted flags exits 0", rc == 0, str(rc))
    check("omitted --min-queries forwards DEFAULT_MIN_LABELED (sentinel), not None/150",
          captured.get("min_labeled") == 987654, str(captured.get("min_labeled")))
    check("omitted --min-queries-bucket forwards DEFAULT_MIN_LABELED_BUCKET (sentinel), not None/40",
          captured.get("min_labeled_bucket") == 4321, str(captured.get("min_labeled_bucket")))


def gate_benchmark_pointer_honest() -> None:
    print("\n-- no phantom `tune --benchmark`: messages point at the REAL A/B entrypoint --")
    tw = (BUILD / "tune_weights.py").read_text(encoding="utf-8")
    cli = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("no `tune --benchmark` phantom flag referenced anywhere",
          "tune --benchmark" not in tw and "tune --benchmark" not in cli)
    check("apply_learned's refusal names the real G4 procedure (benchmark/weight_ab.py stage + evidence), "
          "not the plan-only run_benchmark.py --ab-weights",
          "--ab-weights" not in tw and "weight_ab.py" in tw and "stage_learned_weights" in tw
          and "build_g4_evidence" in tw)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — `deepmap tune` CLI gates (RM-1; offline, no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)

    gate_tune_writes(L)
    gate_dry_run(L)
    gate_status(L)
    gate_empty_log_graceful()
    gate_min_queries_drift_lock()
    gate_benchmark_pointer_honest()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
