#!/usr/bin/env python3
"""The self 65-query set (gt_self65.json; design §8.2 / §8.5 R2): this repository's own search quality.

capture  live, in-process (the code deepmap_search runs): search(q, top_k=15) for each of the 65 queries, three
         identical calls each (latency = median; the three must agree). Same safety rules as run_variant.py.
score    hit@1 / hit@5 / hit@15 / MRR per (set, type), per set and overall. A hit = a returned row whose file
         equals an expected file AND whose name equals the expected name or ends with "." + it.
compare  R2: variant MRR >= V0 MRR + --min-gain (D3: 0, tolerance 0; D1-py: 0.017) AND hit@15 drops by at most
         one query (exit 1 otherwise). Run it on BOTH replicated V0/variant pairs.
check    which expected [file, name] pairs still exist in a tree (the set is re-baselined at the RC SHA: a query
         whose expected symbols are all gone is reported, never silently dropped).

usage:
  python measure_self.py capture --variant=<id> --clone=<root> --out=<dir> [--gt=gt_self65.json]
  python measure_self.py score <raw.json> [--gt=gt_self65.json] [--json=out.json]
  python measure_self.py compare <scored_V0.json> <scored_VX.json> [--min-gain=0] [--max-hit15-drop=1]
  python measure_self.py check --tree=<root> [--gt=gt_self65.json]
"""
from __future__ import annotations

import ast
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

GT = C.KIT / "gt_self65.json"
SEARCH_TOP_K = 15


def is_hit(row, expected) -> bool:
    f = C.norm_path(row.get("file")).lstrip("./")
    n = str(row.get("name") or "")
    return any(f == ef and (n == en or n.endswith("." + en)) for ef, en in expected)


def rank_of(rows, expected):
    for i, r in enumerate(rows, start=1):
        if is_hit(r, expected):
            return i
    return None


def metrics(ranks) -> dict:
    n = len(ranks)
    if not n:
        return {}

    def h(k):
        return sum(1 for r in ranks if r is not None and r <= k) / n
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    return {"n": n, "hit@1": round(h(1), 4), "hit@5": round(h(5), 4), "hit@15": round(h(15), 4), "MRR": round(mrr, 4)}


def score(gt, raw) -> dict:
    rows_of = {q["id"]: q.get("rows") or [] for q in raw.get("queries") or []}
    groups, per = {}, []
    for q in gt["queries"]:
        rk = rank_of(rows_of.get(q["id"], []), q["expected"])
        per.append({"id": q["id"], "rank": rk})
        for g in ((q["set"], q["type"]), (q["set"], "ALL"), ("ALL", "ALL")):
            groups.setdefault(g, []).append(rk)
    return {"metrics": {f"{a}/{b}": metrics(v) for (a, b), v in sorted(groups.items())}, "per_query": per,
            "missing": sorted(q["id"] for q in gt["queries"] if q["id"] not in rows_of)}


def compare(base, var, min_gain=0.0, max_hit15_drop=1) -> dict:
    mb, mv = base["metrics"]["ALL/ALL"], var["metrics"]["ALL/ALL"]
    rb = {p["id"]: p["rank"] for p in base["per_query"]}
    rv = {p["id"]: p["rank"] for p in var["per_query"]}
    lost15 = sorted(q for q, r in rb.items() if r is not None and r <= 15 and not (rv.get(q) is not None and rv[q] <= 15))
    gained15 = sorted(q for q, r in rv.items() if r is not None and r <= 15 and not (rb.get(q) is not None and rb[q] <= 15))
    net_drop = len(lost15) - len(gained15)
    mrr_ok = mv["MRR"] >= round(mb["MRR"] + min_gain, 4)
    return {"mrr_base": mb["MRR"], "mrr_variant": mv["MRR"], "min_gain": min_gain, "mrr_ok": mrr_ok,
            "hit15_lost": lost15, "hit15_gained": gained15, "hit15_net_drop": net_drop,
            "hit15_ok": net_drop <= max_hit15_drop, "r2_holds": mrr_ok and net_drop <= max_hit15_drop}


def _defined(tree_root: Path, rel: str, name: str) -> bool:
    p = tree_root / rel
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8", errors="replace")
    if name == p.stem:                      # the file's :Module node
        return True
    if p.suffix != ".py":                   # shell / PowerShell / ...: the name must still be written there
        return name in text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    want = name.split(".")
    found = set()

    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + [ch.name]
                found.add(".".join(q))
                found.add(ch.name)
                walk(ch, q)
            else:
                walk(ch, prefix)
    walk(tree, [])
    return ".".join(want) in found or (len(want) == 1 and want[0] in found)


def check(gt, tree_root: Path) -> dict:
    out = {"missing_pairs": [], "queries_all_missing": []}
    for q in gt["queries"]:
        miss = [[f, n] for f, n in q["expected"] if not _defined(tree_root, f, n)]
        out["missing_pairs"] += [[q["id"]] + m for m in miss]
        if len(miss) == len(q["expected"]):
            out["queries_all_missing"].append(q["id"])
    return out


def cmd_capture(opts) -> int:  # pragma: no cover — live (S6)
    import run_variant as RV
    sys.path.insert(0, str(C.BUILD))
    import port_registry as PR
    from neo4j import GraphDatabase
    import variants as V
    clone = Path(opts["--clone"]).resolve()
    route = PR.resolve_neo4j_route(clone, honor_env=False)
    probs = RV.safety_problems(clone, route)
    if probs:
        print("[measure_self] REFUSED:\n  - " + "\n  - ".join(probs), file=sys.stderr)
        return 2
    vid = V.resolve(opts["--variant"])
    gt = C.load_json(opts.get("--gt") or GT)
    drv = GraphDatabase.driver(route.uri, auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "deepmap")),
                               notifications_min_severity="OFF")
    out = {"schema": "call-linking/self-capture/1", "variant": vid, "clone": C.norm_path(clone), "flags": C.env_flags(),
           "queries": []}
    try:
        drv.verify_connectivity()
        cen = RV.census(drv)
        why = RV.owner_problem(clone, cen.get("owner"))
        if why:
            print(f"[measure_self] REFUSED: {why}", file=sys.stderr)
            return 2
        import embeddings
        import retrieval
        embeddings.embed_query("warm-up")
        ret = retrieval.DeepMapRetriever(drv, embed_fn=embeddings.embed_query)
        ret.search("warm up the retriever", top_k=5)
        for q in gt["queries"]:
            reps, rows, same = [], None, True
            for _ in range(3):
                t0 = time.perf_counter()
                got = [dict(r) for r in ret.search(q["q"], top_k=SEARCH_TOP_K)]
                reps.append(round((time.perf_counter() - t0) * 1000, 1))
                for r in got:
                    r.pop("embedding", None)
                sig = [(r.get("name"), r.get("file"), r.get("line")) for r in got]
                if rows is None:
                    rows, first = got, sig
                else:
                    same = same and sig == first
            out["queries"].append({"id": q["id"], "rows": rows, "ms": statistics.median(reps), "deterministic": same})
        out["census"] = cen
    finally:
        drv.close()
    C.write_json(Path(opts["--out"]) / f"self_raw_{V.slug(vid)}.json", out)
    det = all(q["deterministic"] for q in out["queries"])
    print(f"[measure_self] {vid}: {len(out['queries'])} queries, deterministic={det}")
    return 0 if det else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--variant', '--clone', '--out', '--gt', '--json', '--min-gain', '--max-hit15-drop', '--tree'))
    cmd = args[0] if args else ""
    if cmd == "capture" and all(opts.get(k) for k in ("--variant", "--clone", "--out")):
        return cmd_capture(opts)
    gt = C.load_json(opts.get("--gt") or GT)
    if cmd == "score" and len(args) == 2:
        s = score(gt, C.load_json(args[1]))
        for g, m in s["metrics"].items():
            print(f"{g:14s} {m}")
        if opts.get("--json"):
            C.write_json(opts["--json"], s)
        return 0
    if cmd == "compare" and len(args) == 3:
        r = compare(C.load_json(args[1]), C.load_json(args[2]), float(opts.get("--min-gain") or 0),
                    int(opts.get("--max-hit15-drop") or 1))
        print(f"R2 {'HOLDS' if r['r2_holds'] else 'FAILS'}: MRR {r['mrr_base']} -> {r['mrr_variant']} "
              f"(min gain {r['min_gain']}); hit@15 lost {r['hit15_lost']} gained {r['hit15_gained']}")
        return 0 if r["r2_holds"] else 1
    if cmd == "check" and opts.get("--tree"):
        r = check(gt, Path(opts["--tree"]))
        print(f"{len(r['missing_pairs'])} expected pair(s) missing; queries with every expected symbol gone: "
              f"{r['queries_all_missing']}")
        for m in r["missing_pairs"]:
            print(f"   {m}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
