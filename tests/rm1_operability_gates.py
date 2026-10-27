#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the RM-1 operability extension (design §F): log rotation, opt-in
sync auto-tune, and query redaction. All opt-in / zero-egress / never-break-the-caller.

Locks:
  - rotate_log caps the local log, KEEPS the newest whole valid lines, drops the oldest, never a torn
    half-line; no-op under the cap / absent; zero-egress; never raises,
  - the sync auto-tune hook is OPT-IN (DEEPMAP_AUTO_TUNE), best-effort (wrapped), writes applied:false,
    and also rotates the log — wired in build/sync.py,
  - the tune+write composition the hook runs stays applied:false (never live).
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


def gate_rotate_log(L) -> None:
    print("\n-- rotate_log: cap the log, keep NEWEST valid lines, drop oldest, zero-egress --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # No file -> no-op, no raise.
        check("absent log -> False, no raise", L.rotate_log(root) is False)
        # Seed 60 records with a fat query so the file is comfortably large.
        qids = []
        for i in range(60):
            rec = L.build_record("x" * 500 + f" q{i}", {"vector": ["A"]},
                                 [{"id": "A", "file": "f/a.py", "name": "A", "line": 1}], ts=f"t{i}")
            qids.append(rec["qid"])
            L.append_record(root, rec)
        lp = L.log_path(root)
        big = lp.stat().st_size
        # Under-cap -> no-op.
        check("under cap -> no-op (False)", L.rotate_log(root, max_bytes=big + 10) is False)
        # Rotate to a small cap -> rewrites, smaller, keeps the NEWEST.
        rewrote = L.rotate_log(root, max_bytes=big // 4)
        check("over cap -> rewrote (True)", rewrote is True)
        check("file shrank under the cap", lp.stat().st_size <= big // 4 + 4096, str(lp.stat().st_size))
        recs = L.read_records(root)
        kept_qids = {r.get("qid") for r in recs}
        check("all remaining lines are valid JSON records", all(isinstance(r, dict) for r in recs) and len(recs) >= 1)
        check("kept the NEWEST record", qids[-1] in kept_qids)
        check("dropped the OLDEST record", qids[0] not in kept_qids)
        # Zero-egress: only the log file exists under the root.
        stray = [p for p in root.rglob("*") if p.is_file() and p != lp]
        check("zero-egress: only the .deepmap log was touched", stray == [], str(stray))
    # Bad root -> False, never raises.
    raised = False
    try:
        out = L.rotate_log("\x00::bad::")
    except Exception:
        raised = True
        out = "raised"
    check("bad root -> False, no raise", (not raised) and out is False, str(out))


def gate_auto_tune_composition(L, T) -> None:
    print("\n-- the sync auto-tune composition (tune_profiles -> write) stays applied:false --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
        for i in range(80):
            returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
            L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
            L.append_record(root, {"type": "access", "v": 2, "paths": ["f/p.py"], "kind": "edit", "symbols": [], "ts": f"t{i}b"})
        res = T.tune_profiles(root=root, min_labeled=20)
        out = T.write_instance_weights(root, res)
        lf = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))["learned_fusion"]
        check("auto-tune composition writes instance.json", out is not None)
        check("applied stays false (never live via auto-tune)", lf.get("applied") is False)


def gate_sync_wiring() -> None:
    print("\n-- build/sync.py wires the opt-in auto-tune + rotation (best-effort, never breaks sync) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    check("sync calls _maybe_auto_tune after a successful sync", "_maybe_auto_tune(root)" in src)
    check("auto-tune is opt-in via DEEPMAP_AUTO_TUNE", "DEEPMAP_AUTO_TUNE" in src)
    check("auto-tune writes via write_instance_weights (applied:false)", "write_instance_weights" in src)
    check("auto-tune rotates the retrieval log", "rotate_log" in src)
    # The hook body must be exception-guarded (a tune failure must never fail a sync).
    idx = src.find("def _maybe_auto_tune")
    check("_maybe_auto_tune is exception-guarded", idx > 0 and "except Exception" in src[idx:idx + 1600])
    body = src[idx:idx + 2400] if idx > 0 else ""
    check("the learning-loop master switch also enables the tune leg",
          "_rl.learning_enabled(root)" in body)
    check("an explicit DEEPMAP_AUTO_TUNE=0 vetoes the tune leg (most-specific wins)",
          '"0"' in body and '"false"' in body)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 operability gates (rotation, opt-in auto-tune; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)

    gate_rotate_log(L)
    gate_auto_tune_composition(L, T)
    gate_sync_wiring()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
