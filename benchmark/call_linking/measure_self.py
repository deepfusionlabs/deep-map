#!/usr/bin/env python3
"""The self 65-query set (gt_self65.json; design §8.2 / §8.5 R2): this repository's own search quality.

capture  live, in-process (the code deepmap_search runs): search(q, top_k=15) for each of the 65 queries, three
         identical calls each (latency = median; the three must agree). Same safety rules as run_variant.py.
score    hit@1 / hit@5 / hit@15 / MRR per (set, type), per set and overall. A hit = a returned row whose file
         equals an expected file AND whose name equals the expected name or ends with "." + it.
compare  R2: variant MRR >= V0 MRR + --min-gain (D3: 0, tolerance 0; D1-py: 0.017) AND hit@15 drops by at most
         one query (exit 1 otherwise). Run it on BOTH replicated V0/variant pairs.
         `mrr_ok` / `r2_holds` compare the 4-decimal MRRs; the closed decisions D3 and D1-py read them, and they and
         every other field are kept byte for byte. AC29 (PREREG_CS: every threshold on the integer inputs): beside
         them, the same rule computed exactly from the integer per-query ranks — each MRR the rational
         sum(1 / rank) / n (`mrr_base_fraction` / `mrr_variant_fraction`, [numerator, denominator];
         `mrr_base_exact` / `mrr_variant_exact`, its full-precision float, for display), --min-gain read exactly from
         its decimal string (`min_gain_fraction`: 0.017 -> 17/1000), `mrr_ok_exact` and `r2_holds_exact`. D3-cs
         (PREREG_CS §8.7 R, AC23, AC29, §8.10 VRC-cs) reads the exact fields: two MRRs 1e-5 apart can both display
         0.2868. The CLI prints both readings, one line each; its exit code follows r2_holds, or r2_holds_exact with
         --exact.
check    which expected [file, name] pairs still exist in a tree (the set is re-baselined at the RC SHA: a query
         whose expected symbols are all gone is reported, never silently dropped).

usage:
  python measure_self.py capture --variant=<id> --clone=<root> --out=<dir> [--gt=gt_self65.json]
  python measure_self.py score <raw.json> [--gt=gt_self65.json] [--json=out.json]
  python measure_self.py compare <scored_V0.json> <scored_VX.json> [--min-gain=0] [--max-hit15-drop=1] [--exact]
  python measure_self.py check --tree=<root> [--gt=gt_self65.json]
"""
from __future__ import annotations

import ast
import os
import statistics
import sys
import time
from fractions import Fraction
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


def exact_decimal(x) -> Fraction:
    """x as the exact rational of the decimal it was written as: a str parsed as typed ("0.017" -> 17/1000), a float
    by its shortest round-trip decimal (repr(0.017) == "0.017" -> 17/1000, never the binary value
    0.017000000000000001221...), an int or a Fraction as it is."""
    if isinstance(x, (int, Fraction)):
        return Fraction(x)
    if isinstance(x, float):
        return Fraction(repr(x))
    return Fraction(str(x).strip())


def mrr_exact(per_query) -> "Fraction | None":
    """AC29: the MRR as an exact rational from the integer ranks — sum(1 / rank) over the queries with a hit, over n,
    the number of queries (a query without a hit adds 0): exactly the value metrics() rounds to 4 decimals."""
    ranks = [p.get("rank") for p in per_query or []]
    if not ranks:
        return None
    return sum((1 / Fraction(r) for r in ranks if r is not None), Fraction(0)) / len(ranks)


def _fraction(fr) -> "list | None":
    """A Fraction as the JSON pair [numerator, denominator] (None stays None)."""
    return None if fr is None else [fr.numerator, fr.denominator]


def compare(base, var, min_gain=0.0, max_hit15_drop=1, *, min_gain_decimal=None) -> dict:
    """R2. The first fields (mrr_base ... r2_holds) are the closed decisions' 4-decimal reading, unchanged; the
    `*_exact` / `*_fraction` fields after them are AC29's exact reading from the integer ranks, which D3-cs reads.
    min_gain_decimal: --min-gain's decimal string as typed (the exact gain); without it, min_gain read as a decimal."""
    mb, mv = base["metrics"]["ALL/ALL"], var["metrics"]["ALL/ALL"]
    rb = {p["id"]: p["rank"] for p in base["per_query"]}
    rv = {p["id"]: p["rank"] for p in var["per_query"]}
    lost15 = sorted(q for q, r in rb.items() if r is not None and r <= 15 and not (rv.get(q) is not None and rv[q] <= 15))
    gained15 = sorted(q for q, r in rv.items() if r is not None and r <= 15 and not (rb.get(q) is not None and rb[q] <= 15))
    net_drop = len(lost15) - len(gained15)
    mrr_ok = mv["MRR"] >= round(mb["MRR"] + min_gain, 4)
    out = {"mrr_base": mb["MRR"], "mrr_variant": mv["MRR"], "min_gain": min_gain, "mrr_ok": mrr_ok,
           "hit15_lost": lost15, "hit15_gained": gained15, "hit15_net_drop": net_drop,
           "hit15_ok": net_drop <= max_hit15_drop, "r2_holds": mrr_ok and net_drop <= max_hit15_drop}
    # AC29 (PREREG_CS; D3-cs reads these): the same rule on the integer ranks, exactly — no rounding anywhere
    eb, ev = mrr_exact(base["per_query"]), mrr_exact(var["per_query"])
    gain = exact_decimal(min_gain if min_gain_decimal is None else min_gain_decimal)
    ok_x = eb is not None and ev is not None and ev >= eb + gain
    out.update({"mrr_base_fraction": _fraction(eb), "mrr_variant_fraction": _fraction(ev),
                "mrr_base_exact": None if eb is None else float(eb),
                "mrr_variant_exact": None if ev is None else float(ev), "min_gain_fraction": _fraction(gain),
                "mrr_ok_exact": ok_x, "r2_holds_exact": ok_x and net_drop <= max_hit15_drop})
    return out


def _frac_text(pair) -> str:
    """[n, d] as `n/d` (`n` when d is 1); None as None."""
    if pair is None:
        return "None"
    return f"{pair[0]}" if pair[1] == 1 else f"{pair[0]}/{pair[1]}"


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
        gain = opts.get("--min-gain") or "0"
        r = compare(C.load_json(args[1]), C.load_json(args[2]), float(gain), int(opts.get("--max-hit15-drop") or 1),
                    min_gain_decimal=gain)
        print(f"R2 {'HOLDS' if r['r2_holds'] else 'FAILS'}: MRR {r['mrr_base']} -> {r['mrr_variant']} "
              f"(min gain {r['min_gain']}); hit@15 lost {r['hit15_lost']} gained {r['hit15_gained']}")
        print(f"R2 exact (AC29: from the integer ranks; D3-cs reads this line) "
              f"{'HOLDS' if r['r2_holds_exact'] else 'FAILS'}: MRR {_frac_text(r['mrr_base_fraction'])} "
              f"({r['mrr_base_exact']!r}) -> {_frac_text(r['mrr_variant_fraction'])} ({r['mrr_variant_exact']!r}) "
              f"(min gain {_frac_text(r['min_gain_fraction'])}); hit@15 net drop {r['hit15_net_drop']}")
        return 0 if (r["r2_holds_exact"] if "--exact" in opts else r["r2_holds"]) else 1
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
