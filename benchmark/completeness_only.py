#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — FREE, ZERO-SPEND completeness read of a captured run.

The improvement-loop signal. Given a captured run-id (or several), this reports the deterministic
`completeness_pct` (the HERO metric) per question and per family, with the ON-vs-OFF delta — WITHOUT
calling the paid LLM judge. It reuses the harness's OWN answer reader (`read_answer_from_scorecard`)
and the OWN deterministic matcher (`score_completeness`), so the number here equals the completeness
half of a full `--score` pass exactly; only the correctness judge is skipped.

Use it in the tight loop: capture (Opus n=1) -> `python benchmark/completeness_only.py <run-id>` ->
triage the ON-negative questions -> fix on the un-frozen surface -> re-capture -> re-read. The paid
`--score --score-workers N` runs ONCE at the end for the correctness half.

    python benchmark/completeness_only.py <run-id> [<run-id> ...]
    python benchmark/completeness_only.py <run-id> --family runtime_edge,multi_hop

No Neo4j, no network, no `claude`, no spend. Read-only. Exits 0.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_benchmark as RB          # noqa: E402  (import-safe: main() is __main__-guarded)
import score_completeness as C      # noqa: E402


def _questions_by_id() -> Dict[str, Dict[str, Any]]:
    _repos, questions = RB.load_config()
    return {q["id"]: q for q in questions if isinstance(q, dict) and q.get("id")}


def _ground_truth_set(q: Optional[Dict[str, Any]]) -> List[str]:
    ak = (q or {}).get("answer_key") or {}
    gts = ak.get("ground_truth_set")
    return list(gts) if isinstance(gts, list) else []


def read_run(run_id: str, qbi: Dict[str, Dict[str, Any]],
             families: Optional[set]) -> List[Dict[str, Any]]:
    """Return one row per captured scorecard: {qid, family, arm, completeness, empty_answer}."""
    run_dir = RB.RESULTS_ROOT / run_id
    rows: List[Dict[str, Any]] = []
    if not run_dir.is_dir():
        print(f"[completeness] run dir not found: {run_dir}", file=sys.stderr)
        return rows
    import json
    for rec_path in run_dir.rglob("record.json"):
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        qid = rec.get("question_id")
        fam = rec.get("family") or (qbi.get(qid, {}) or {}).get("family")
        if families and fam not in families:
            continue
        answer = RB.read_answer_from_scorecard(rec) or ""
        gts = _ground_truth_set(qbi.get(qid))
        comp = C.score_completeness(answer, gts)
        rows.append({
            "qid": qid, "family": fam, "arm": rec.get("arm"),
            "completeness": comp["completeness_pct"],
            "empty_answer": not answer.strip(),
            "empty_oracle": comp.get("empty_oracle", False),
        })
    return rows


def _median_by_qid_arm(rows: List[Dict[str, Any]]) -> Dict[tuple, float]:
    """Median completeness per (qid, arm) across the run(s) — the harness aggregates by median."""
    buckets: Dict[tuple, List[float]] = {}
    for r in rows:
        if r["empty_answer"]:          # a failed capture is UNSCORED, never a real 0 (harness rule)
            continue
        buckets.setdefault((r["qid"], r["arm"]), []).append(r["completeness"])
    return {k: round(statistics.median(v), 2) for k, v in buckets.items() if v}


def _arm_family(arm: Optional[str]) -> str:
    """'deepmap_on@claude-sonnet-5' -> 'deepmap_on'; keep on/off separation, drop the @model."""
    return (arm or "").split("@", 1)[0]


def report(run_ids: List[str], families: Optional[set]) -> None:
    qbi = _questions_by_id()
    rows: List[Dict[str, Any]] = []
    for rid in run_ids:
        rows.extend(read_run(rid, qbi, families))
    if not rows:
        print("[completeness] no scorecards found.")
        return

    med = _median_by_qid_arm(rows)
    qids = sorted({q for (q, _a) in med})
    arms = sorted({_arm_family(a) for (_q, a) in med})
    empties = [(r["qid"], r["arm"]) for r in rows if r["empty_answer"]]

    # Per-question table (ON vs OFF; delta = on - off when both present).
    print(f"\n=== completeness (HERO), zero-spend read of: {', '.join(run_ids)} ===")
    print("(a failed/empty capture is excluded, never scored as 0)\n")
    hdr = ["question", "family"] + arms + (["Δ(on-off)"] if {"deepmap_on", "deepmap_off"} <= set(arms) else [])
    print(" | ".join(hdr))
    print("-" * (len(" | ".join(hdr)) + 4))
    fam_deltas: Dict[str, List[float]] = {}
    on_wins = off_wins = ties = 0
    for qid in qids:
        fam = next((r["family"] for r in rows if r["qid"] == qid), "?")
        cells = [qid, str(fam)]
        for arm in arms:
            v = med.get((qid, arm))
            cells.append("—" if v is None else f"{v:.1f}")
        if {"deepmap_on", "deepmap_off"} <= set(arms):
            on = med.get((qid, "deepmap_on"))
            off = med.get((qid, "deepmap_off"))
            if on is not None and off is not None:
                d = round(on - off, 1)
                cells.append(f"{d:+.1f}")
                fam_deltas.setdefault(str(fam), []).append(d)
                if d > 0.5:
                    on_wins += 1
                elif d < -0.5:
                    off_wins += 1
                else:
                    ties += 1
            else:
                cells.append("—")
        print(" | ".join(cells))

    # Per-family + overall means of the per-question medians.
    if {"deepmap_on", "deepmap_off"} <= set(arms):
        print("\n=== per-family mean Δ (on - off), of per-question medians ===")
        for fam in sorted(fam_deltas):
            ds = fam_deltas[fam]
            on_mean = statistics.mean([med[(q, "deepmap_on")] for q in qids
                                       if (q, "deepmap_on") in med and next(r["family"] for r in rows if r["qid"] == q) == fam])
            off_mean = statistics.mean([med[(q, "deepmap_off")] for q in qids
                                        if (q, "deepmap_off") in med and next(r["family"] for r in rows if r["qid"] == q) == fam])
            print(f"  {fam:16s} ON {on_mean:5.1f}  vs OFF {off_mean:5.1f}  (Δ {statistics.mean(ds):+.1f}, n_q={len(ds)})")
        all_on = [med[(q, "deepmap_on")] for q in qids if (q, "deepmap_on") in med]
        all_off = [med[(q, "deepmap_off")] for q in qids if (q, "deepmap_off") in med]
        if all_on and all_off:
            print(f"\n  OVERALL          ON {statistics.mean(all_on):5.1f}  vs OFF {statistics.mean(all_off):5.1f} "
                  f"(Δ {statistics.mean(all_on) - statistics.mean(all_off):+.1f})   "
                  f"ON wins {on_wins}, OFF wins {off_wins}, ties {ties}")

    if empties:
        print(f"\n[note] {len(empties)} empty/failed capture(s) excluded (not scored as 0): "
              + ", ".join(f"{q}/{a}" for q, a in empties[:12]) + (" ..." if len(empties) > 12 else ""))
    print()


def main(argv: List[str]) -> int:
    RB._ensure_console_utf8()   # status glyphs (Δ/—) must never crash a legacy Windows console (PLAYBOOK #31)
    args = [a for a in argv if a]
    families: Optional[set] = None
    run_ids: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--family" and i + 1 < len(args):
            families = {f.strip() for f in args[i + 1].split(",") if f.strip()}
            i += 2
            continue
        run_ids.append(a)
        i += 1
    if not run_ids:
        print(__doc__)
        return 0
    report(run_ids, families)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
