#!/usr/bin/env python3
"""Arm-3 drift between two dumps (design §8.5 "Drift void rule"): retrieval.py's graph arm scores a node
`score × (1 + log(1 + inDegree))`, inDegree = its incoming :CALLS edges. A variant that changes CALLS moves
that multiplier; this tool reports how much and where, and — given the live captures of both arms — applies
the void rule.

Always reported (design: "Drift (count, max multiplier, top-20) is always reported"):
  n changed, up / down, the multiplier-ratio distribution, max ratio, the top-20 nodes by |log ratio|,
  and the CANDIDATES: nodes whose multiplier rose 2x or more.
--questions Q --repo owner/name   marks candidates that sit in a question's atom file (informational).
--capture-base / --capture-variant   (run_variant.py raw captures of the two arms): the VOID RULE — the flip is
  void if a candidate becomes newly fused-top-3 for 3 or more questions (unless the audit shows its new callers
  are true production callers: that judgement is the audit's, recorded in DECISION.md).

usage: python indegree_drift.py <base.json> <variant.json> [--questions=Q --repo=owner/name]
       [--capture-base=raw_V0.json --capture-variant=raw_VX.json] [--json=out.json]
"""
from __future__ import annotations

import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

VOID_RATIO = 2.0
VOID_QUESTIONS = 3


def in_degrees(dump) -> Counter:
    return Counter(b for (_a, b) in C.edges_of(dump))


def drift(base, variant, top=20) -> dict:
    nv = C.node_index(variant)
    nb = C.node_index(base)
    d0, d1 = in_degrees(base), in_degrees(variant)
    rows = []
    for k in sorted(set(d0) | set(d1)):
        if d0[k] == d1[k]:
            continue
        r = C.arm3_multiplier(d1[k]) / C.arm3_multiplier(d0[k])
        n = nv.get(k) or nb.get(k) or {}
        rows.append({"key": k, "name": n.get("name"), "file": n.get("source_file"), "line": n.get("start_line"),
                     "label": n.get("label"), "in_base": d0[k], "in_variant": d1[k], "ratio": round(r, 4)})
    ratios = sorted(x["ratio"] for x in rows)

    def q(p):
        return ratios[min(len(ratios) - 1, int(p * len(ratios)))] if ratios else None

    # AC29 record: a 4-decimal ratio (rounding only adds candidates: strict); closed D3 shares it; can't fire in D3-cs
    cands = [x for x in rows if x["ratio"] >= VOID_RATIO]
    return {"n_nodes": len(nv), "n_changed": len(rows), "up": sum(1 for x in rows if x["in_variant"] > x["in_base"]),
            "down": sum(1 for x in rows if x["in_variant"] < x["in_base"]),
            "ratio_p5": q(0.05), "ratio_p50": q(0.5), "ratio_p95": q(0.95),
            "ratio_min": ratios[0] if ratios else None, "ratio_max": ratios[-1] if ratios else None,
            "top": sorted(rows, key=lambda x: (-abs(math.log(x["ratio"])), x["key"]))[:top],
            "candidates": sorted(cands, key=lambda x: (-x["ratio"], x["key"]))}


def _top3(capture) -> dict:
    """{question id: [(name, file, line), ...top 3]} of a run_variant raw capture."""
    out = {}
    for q in capture.get("questions") or []:
        hits = [r for r in (q.get("search") or []) if isinstance(r, dict)][:3]
        out[q["id"]] = [(r.get("name"), C.norm_path(r.get("file")), r.get("line")) for r in hits]
    return out


def void_rule(candidates, cap_base, cap_variant) -> dict:
    """Per candidate: the questions where it is in the variant's fused top-3 but not in the base's."""
    t0, t1 = _top3(cap_base), _top3(cap_variant)
    per = {}
    for c in candidates:
        ident = (c["name"], C.norm_path(c["file"]), c["line"])
        newly = sorted(qid for qid, top in t1.items() if ident in top and ident not in t0.get(qid, []))
        if newly:
            per[c["key"]] = newly
    fired = sorted(k for k, qs in per.items() if len(qs) >= VOID_QUESTIONS)
    return {"newly_top3": per, "fired_for": fired, "void": bool(fired)}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--questions', '--repo', '--capture-base', '--capture-variant', '--json'))
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base, var = C.load_json(args[0]), C.load_json(args[1])
    d = drift(base, var)
    if opts.get("--questions"):
        import yaml  # measurement only (PyYAML is already a DeepMap requirement)
        qs = yaml.safe_load(open(opts["--questions"], encoding="utf-8"))["questions"]
        files = set()
        for q in qs:
            if opts.get("--repo") and q.get("repo") != opts["--repo"]:
                continue
            for a in (q.get("answer_key") or {}).get("ground_truth_set") or []:
                files.add(C.norm_path(str(a).split(":", 1)[0].strip()))
        for c in d["candidates"]:
            c["in_atom_file"] = C.norm_path(c["file"]) in files
    if opts.get("--capture-base") and opts.get("--capture-variant"):
        d["void_rule"] = void_rule(d["candidates"], C.load_json(opts["--capture-base"]),
                                   C.load_json(opts["--capture-variant"]))
    print(f"in-degree changed on {d['n_changed']}/{d['n_nodes']} nodes (up {d['up']}, down {d['down']}); "
          f"arm-3 multiplier ratio p5={d['ratio_p5']} p50={d['ratio_p50']} p95={d['ratio_p95']} "
          f"min={d['ratio_min']} max={d['ratio_max']}; candidates (ratio >= {VOID_RATIO}): {len(d['candidates'])}")
    for x in d["top"]:
        print(f"   {str(x['name'])[:60]:60s} {x['in_base']:5d} -> {x['in_variant']:5d}  x{x['ratio']:.3f}")
    if "void_rule" in d:
        vr = d["void_rule"]
        print(f"void rule: {'FIRED for ' + ', '.join(vr['fired_for']) if vr['void'] else 'not fired'}")
    if opts.get("--json"):
        C.write_json(opts["--json"], d)
    return 0


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
