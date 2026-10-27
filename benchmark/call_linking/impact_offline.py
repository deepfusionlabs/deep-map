#!/usr/bin/env python3
"""Oracle-seeded, search-free impact recall (design §8.4 G): reverse :CALLS reachability (<= --hops, default 3)
from each multi_hop question's named target, scored with score_m.py's STRICT / SPEC atom rules on the reached
nodes. Pooled over the cases given (the four Python multi_hop questions: 49 atoms; pilot baseline 25/49 STRICT,
reference-resolver ceiling 28/49). A case scored on the resolver's own pairs (--case-truth) is the ceiling.

usage:
  python impact_offline.py --questions benchmark/questions.yaml
         --case <owner/name> <corpus root> <dump.json> [--case ...]
         [--case-truth <owner/name> <corpus root> <dump.json> <truth.json>] [--hops 3] [--json out.json]
(each case is separate arguments, never one comma-joined string: Git Bash rewrites a string that looks like a
path list before a Windows Python sees it)
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import score_m as S  # noqa: E402

# The multi_hop question -> the symbol whose blast radius it asks for (the question's own seed).
SEEDS = {"werkzeug-blast-1": "MapAdapter.match", "flask-blast-1": "Flask.ensure_sync",
         "click-blast-1": "Context.invoke", "fastapi-blast-1": "solve_dependencies"}


def reach(dump, seed: str, hops: int):
    """(seed keys, [(caller key, hop, grade)]) — breadth-first over reversed :CALLS edges."""
    by_key = C.node_index(dump)
    rev = defaultdict(list)
    for (a, b), c in sorted(C.edges_of(dump).items()):
        rev[b].append((a, c))
    seeds = sorted(k for k, n in by_key.items() if n.get("name") == seed or str(n.get("name")).endswith("." + seed))
    seen, frontier, rows = set(seeds), deque((s, 0) for s in seeds), []
    while frontier:
        k, d = frontier.popleft()
        if d >= hops:
            continue
        for a, c in rev.get(k, ()):
            if a in seen:
                continue
            seen.add(a)
            rows.append((a, d + 1, c))
            frontier.append((a, d + 1))
    return seeds, rows


def score_case(question, root, dump, hops=3) -> dict:
    by_key = C.node_index(dump)
    seed = SEEDS[question["id"]]
    seeds, rows = reach(dump, seed, hops)

    def as_row(k):
        n = by_key[k]
        return {"name": n.get("name"), "type": n.get("label"), "file": n.get("source_file"), "line": n.get("start_line")}

    nodes = [as_row(s) for s in seeds] + [as_row(a) for a, _d, _c in rows]
    atoms = [a for a in (S.parse_atom(x) for x in question["answer_key"]["ground_truth_set"]) if a]
    strict = sum(bool(S.hits(a, nodes, Path(root))["strict"]) for a in atoms)
    spec = sum(bool(S.hits(a, nodes, Path(root))["spec"]) for a in atoms)
    return {"id": question["id"], "seed": seed, "n_seeds": len(seeds), "reached": len(rows),
            "conf": dict(sorted(Counter(c for _a, _d, c in rows).items())), "atoms": len(atoms),
            "strict": strict, "spec": spec}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Oracle-seeded offline impact recall.")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--case", nargs=3, action="append", default=[], metavar=("REPO", "ROOT", "DUMP"))
    ap.add_argument("--case-truth", nargs=4, action="append", default=[], metavar=("REPO", "ROOT", "DUMP", "TRUTH"))
    ap.add_argument("--hops", type=int, default=3)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    if not (a.case or a.case_truth):
        print(__doc__, file=sys.stderr)
        return 2
    import yaml  # measurement only (PyYAML is already a DeepMap requirement)
    qs = yaml.safe_load(open(a.questions, encoding="utf-8"))["questions"]
    hops = a.hops
    opts = {"--json": a.json}
    results = []
    for parts, is_truth in [(c, False) for c in a.case] + [(c, True) for c in a.case_truth]:
        repo, root, dump_path = parts[0], parts[1], parts[2]
        dump = C.load_json(dump_path)
        if is_truth:
            dump = C.truth_as_dump(C.load_json(parts[3]), dump)
        for q in qs:
            if q.get("repo") == repo and q.get("family") == "multi_hop" and q["id"] in SEEDS:
                r = score_case(q, root, dump, hops)
                r.update(repo=repo, dump=os.path.basename(dump_path), truth=is_truth)
                results.append(r)
                print(f"{r['dump']:40s} {r['id']:18s} seed={r['seed']:22s} seeds={r['n_seeds']} "
                      f"reached={r['reached']:5d} conf={r['conf']} STRICT={r['strict']}/{r['atoms']} "
                      f"SPEC={r['spec']}/{r['atoms']}{' (truth ceiling)' if is_truth else ''}")
    def pooled(rs):
        return {"strict": sum(r["strict"] for r in rs), "spec": sum(r["spec"] for r in rs),
                "atoms": sum(r["atoms"] for r in rs), "questions": len(rs)}

    tot = pooled([r for r in results if not r["truth"]])
    ceil = pooled([r for r in results if r["truth"]])
    if tot["questions"]:
        print(f"POOLED STRICT={tot['strict']}/{tot['atoms']} SPEC={tot['spec']}/{tot['atoms']}")
    if ceil["questions"]:
        print(f"CEILING (reference resolver's pairs) STRICT={ceil['strict']}/{ceil['atoms']} "
              f"SPEC={ceil['spec']}/{ceil['atoms']}")
    if opts.get("--json"):
        C.write_json(opts["--json"], {"cases": results, "pooled": tot, "ceiling": ceil, "hops": hops})
    return 0


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
