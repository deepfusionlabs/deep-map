#!/usr/bin/env python3
"""Hop-1 impact spot checks (design §8.4 G, D3-G1 "hop-1 recall improves on at least 3 of 5 spot symbols"):
for each registered symbol, its direct callers in a dump vs the reference resolver's direct callers (the truth
pairs mapped onto the same dump's nodes), with precision, recall and the grade mix.

A symbol is `Name` (every node whose name is Name or ends with `.Name`) or `Name@path/to/file` (only nodes in
that file — Depends is both a function and a class in fastapi).

The registered spots (PREREG.md lists the same; tests/call_linking_harness_gates.py locks the two together):
  Python  MapAdapter.match, Map.bind, run_wsgi_app (werkzeug); Depends (fastapi); read_records (the STAGE-0
          build/ corpus — PREREG amendment A2: it replaced the design-given `_mk_project` of this repository,
          saturated at V0 (108/108), chosen before RC by V0 data only)
  TS/JS   ZodString._addCheck, ParseStatus.mergeObjectSync (zod); ArticleService.findAll,
          UserService.generateJWT (nestjs-realworld); getUserById (node-express-boilerplate)
Every spot lives on a corpus run_matrix.sh pins to a SHA, so the V0 numbers PREREG §4 records are reproducible at RC.

usage: python impact_spot.py --truth=<truth.json> (--symbols=a,b@f | --corpus=<id>) <dump.json> [<dump.json> ...]
       [--base=<V0 dump.json>] [--json=out.json]
       --base: also report on how many symbols the hop-1 recall improved over the base (the D3-G1 count).

A spot whose base (V0) hop-1 recall is already 1, or that has no reference caller, can never "improve": it is
SATURATED. PREREG §4 requires every registered spot's V0 recall to be below 1 (the D3-G1 "3 of 5" must be a
count over five spots that CAN improve); with --base each saturated spot is flagged and the run exits 1, so a
saturated spot is never silently counted as "not improved".
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

SPOTS = {
    "pallets__werkzeug": ["MapAdapter.match@src/werkzeug/routing/map.py", "Map.bind@src/werkzeug/routing/map.py",
                          "run_wsgi_app@src/werkzeug/test.py"],
    "fastapi__fastapi": ["Depends@fastapi/param_functions.py"],
    "stage0_build": ["read_records@retrieval_log.py"],
    "colinhacks__zod": ["ZodString._addCheck@packages/zod/src/v3/types.ts",
                        "ParseStatus.mergeObjectSync@packages/zod/src/v3/helpers/parseUtil.ts"],
    "lujakob__nestjs-realworld-example-app": ["ArticleService.findAll@src/article/article.service.ts",
                                              "UserService.generateJWT@src/user/user.service.ts"],
    "hagopj13__node-express-boilerplate": ["getUserById@src/services/user.service.js"],
}
PY_SPOTS = ("MapAdapter.match", "Map.bind", "Depends", "run_wsgi_app", "read_records")
TS_SPOTS = ("ZodString._addCheck", "ParseStatus.mergeObjectSync", "ArticleService.findAll",
            "UserService.generateJWT", "getUserById")


def targets(dump, symbol: str) -> set:
    name, _, file = symbol.partition("@")
    out = set()
    for n in dump.get("nodes") or []:
        nm = str(n.get("name") or "")
        if (nm == name or nm.endswith("." + name)) and (not file or C.norm_path(n.get("source_file")) == file):
            out.add(n["key"])
    return out


def callers(dump, keys: set) -> dict:
    return {a: c for (a, b), c in C.edges_of(dump).items() if b in keys and a not in keys}


def spot(ref, dump, symbol) -> dict:
    tg = targets(dump, symbol)
    R, G = callers(ref, tg), callers(dump, tg)
    hit = set(R) & set(G)
    return {"symbol": symbol, "targets": len(tg), "ref": len(R), "got": len(G), "hit": len(hit),
            "precision": round(len(hit) / len(G), 4) if G else None,
            "recall": round(len(hit) / len(R), 4) if R else None,
            "conf": dict(sorted(Counter(G.values()).items())), "missed": sorted(set(R) - set(G)),
            "extra": sorted(set(G) - set(R))}


def saturated(base_row) -> bool:
    """A spot that cannot improve over its base: no reference caller, or base hop-1 recall already 1."""
    return not base_row.get("ref") or (base_row.get("recall") is not None and base_row["recall"] >= 1.0)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--truth', '--symbols', '--corpus', '--base', '--json'))
    syms = [s for s in (opts.get("--symbols") or "").split(",") if s] or SPOTS.get(opts.get("--corpus") or "", [])
    if not opts.get("--truth") or not syms or not args:
        print(__doc__, file=sys.stderr)
        return 2
    truth = C.load_json(opts["--truth"])
    base = C.load_json(opts["--base"]) if opts.get("--base") else None
    results = []
    for p in args:
        d = C.load_json(p)
        ref = C.truth_as_dump(truth, d)
        print(f"== {os.path.basename(p)}")
        rows = []
        for s in syms:
            r = spot(ref, d, s)
            if base is not None:
                b = spot(C.truth_as_dump(truth, base), base, s)
                r["base_recall"] = b["recall"]
                r["base_hit"], r["base_ref"] = b["hit"], b["ref"]
                r["saturated"] = saturated(b)
                r["improved"] = (r["recall"] or 0) > (b["recall"] or 0)
            rows.append(r)
            print(f"   {s:60s} ref={r['ref']:4d} got={r['got']:5d} hit={r['hit']:4d} P={r['precision']} "
                  f"R={r['recall']} conf={r['conf']}" + (f" base R={r['base_recall']}" if base is not None else ""))
        if base is not None:
            sat = [r["symbol"] for r in rows if r["saturated"]]
            print(f"   hop-1 recall improved on {sum(1 for r in rows if r['improved'])} of {len(rows)} symbols"
                  + (f"; SATURATED at base (can never improve — PREREG §4 requires V0 recall < 1): {sat}" if sat else ""))
        results.append({"dump": os.path.basename(p), "spots": rows})
    if opts.get("--json"):
        C.write_json(opts["--json"], results)
    return 1 if any(r.get("saturated") for res in results for r in res["spots"]) else 0


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
