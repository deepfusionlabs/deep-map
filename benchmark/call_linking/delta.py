#!/usr/bin/env python3
"""The edge delta between two dumps of the SAME corpus, engine and code (design §8.4 G): a base (usually V0) and
a variant. Every variant in §8.3 is edge-only, so the node sets must be identical — a differing node set is
reported (and makes the delta VOID for decisions, exit 3). The delta is also VOID (exit 3, `void` lists why) when
either dump carries offline_calls.py's own `void`, or when the variant's flags differ from the base's while the
`effective` switches the product reported are identical (an inert variant equals V0 — PREREG §5).

Buckets (the audit_sample.py buckets come from here):
  added     pair in the variant only            removed   pair in the base only
  demoted   grade lowered (EXTRACTED/INFERRED -> AMBIGUOUS, EXTRACTED -> INFERRED)
  promoted  grade raised                         EXTRACTED the variant's EXTRACTED edges (ALL of them: new ones
                                                           and those already EXTRACTED in the base)
each split by caller class (prod / test / nonprod, design §2.6) and, with --truth, by verdict
(confirmed / refuted / unverified — compare_calls' rule).

With a DISPATCH truth (truth_roslyn.py, D3-cs; PREREG_CS §8.5) the verdict is §8.5's: confirmed (T_s) |
dispatch-confirmed (T_d) | refuted | unverified, and each item also carries `verdict_static` (dispatch ignored; never
decisive) and `callee_class`. The delta then has a `cs` block: G2d — the ADDED edges whose caller is not test code
and whose callee is declared in a test file (the product's rule, _common.caller_class / sync._is_test_path), zero
allowed. A jedi / tsc truth's delta is exactly as before.

usage: python delta.py <base.json> <variant.json> [--truth=truth.json] [--src-prefix=a/,b/] [--json=out.json]
       [--list=N]   (print the first N items of each bucket)
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

RANK = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}
BUCKETS = ("added", "removed", "demoted", "promoted", "EXTRACTED")


def delta(base, variant, truth=None, prefixes=None) -> dict:
    """Pure: the bucketed delta (sorted item lists + counts)."""
    nb, nv = C.node_index(base), C.node_index(variant)
    node_mismatch = sorted(set(nb) ^ set(nv))
    nodes = variant.get("nodes") or []
    name_of = {k: n.get("name") for k, n in nv.items()}
    file_of = {k: n.get("source_file") for k, n in nv.items()}
    for k, n in nb.items():
        name_of.setdefault(k, n.get("name"))
        file_of.setdefault(k, n.get("source_file"))
    E0, E1 = C.edges_of(base), C.edges_of(variant)
    T = by_cn = P = None
    cs = truth is not None and C.has_dispatch(truth)
    if cs:
        P = C.truth_pairs_cs(truth, nodes, None)
        T, by_cn = P["Ts"], P["by_cn_static"]
    elif truth is not None:
        T, by_cn, _st = C.truth_pairs(truth, nodes, None)

    def item(pair, g0, g1):
        a, b = pair
        it = {"caller": a, "callee": b, "caller_name": name_of.get(a), "callee_name": name_of.get(b),
              "caller_file": file_of.get(a), "callee_file": file_of.get(b), "base": g0, "variant": g1,
              "caller_class": C.caller_class(file_of.get(a))}
        if cs:
            it["verdict"] = C.classify_pair_cs(pair, P["Ts"], P["Td"], P["by_cn"], name_of)
            it["verdict_static"] = C.classify_pair(pair, T, by_cn, name_of)
            it["callee_class"] = C.caller_class(file_of.get(b))
        elif T is not None:
            it["verdict"] = C.classify_pair(pair, T, by_cn, name_of)
        return it

    out = {b: [] for b in BUCKETS}
    for pair in sorted(set(E0) | set(E1)):
        if not C.in_prefixes(file_of.get(pair[0]), prefixes):
            continue
        g0, g1 = E0.get(pair), E1.get(pair)
        if g0 is None:
            out["added"].append(item(pair, g0, g1))
        elif g1 is None:
            out["removed"].append(item(pair, g0, g1))
        elif g0 != g1:
            out["demoted" if RANK.get(g1, 1) < RANK.get(g0, 1) else "promoted"].append(item(pair, g0, g1))
        if g1 == "EXTRACTED":
            out["EXTRACTED"].append(item(pair, g0, g1))
    counts = {}
    for b in BUCKETS:
        c = Counter()
        for it in out[b]:
            c[f"class:{it['caller_class']}"] += 1
            if "verdict" in it:
                c[f"verdict:{it['verdict']}"] += 1
                c[f"{it['caller_class']}:{it['verdict']}"] += 1
            if "verdict_static" in it:
                c[f"static:{it['verdict_static']}"] += 1
        counts[b] = {"n": len(out[b]), **dict(sorted(c.items()))}
    void = []
    for side, d in (("base", base), ("variant", variant)):
        if d.get("void"):
            void.append(f"the {side} dump is VOID ({d['void']})")
    inert = ((base.get("flags") or {}) != (variant.get("flags") or {}) and base.get("effective") is not None
             and base.get("effective") == variant.get("effective"))
    if inert:
        void.append("the variant's flags differ from the base's but the product's effective switches are identical "
                    "(an inert variant)")
    xfile = sorted([it["caller"], it["callee"]] for it in out["EXTRACTED"]
                   if C.norm_path(it["caller_file"]) != C.norm_path(it["callee_file"]))
    res = {"base": base.get("_path"), "variant": variant.get("_path"), "base_flags": base.get("flags"),
           "variant_flags": variant.get("flags"), "prefixes": prefixes, "void": void,
           "extracted_cross_file": len(xfile), "extracted_cross_file_pairs": xfile[:20],
           "node_set_identical": not node_mismatch, "node_mismatch": node_mismatch[:50],
           "n_node_mismatch": len(node_mismatch), "edges_base": len(E0), "edges_variant": len(E1),
           "counts": counts, "items": out}
    if cs:
        g2d = sorted([it["caller"], it["callee"]] for it in out["added"]
                     if it["caller_class"] != "test" and it.get("callee_class") == "test")
        res["cs"] = {"truth_kind": "dispatch", "g2d_added_nontest_to_test": len(g2d), "g2d_pairs": g2d[:50]}
    return res


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--truth', '--src-prefix', '--json', '--list'))
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base, var = C.load_json(args[0]), C.load_json(args[1])
    base["_path"], var["_path"] = os.path.basename(args[0]), os.path.basename(args[1])
    truth = C.load_truth(opts["--truth"]) if opts.get("--truth") else None
    d = delta(base, var, truth, C.parse_prefixes(opts.get("--src-prefix")))
    print(f"delta {d['base']} -> {d['variant']}: edges {d['edges_base']} -> {d['edges_variant']}"
          f"  node sets identical: {d['node_set_identical']} ({d['n_node_mismatch']} differ)"
          f"  variant EXTRACTED cross-file: {d['extracted_cross_file']}")
    for why in d["void"]:
        print(f"  VOID: {why}")
    for b in BUCKETS:
        print(f"  {b:9s} " + " ".join(f"{k}={v}" for k, v in d["counts"][b].items()))
    if d.get("cs"):
        print(f"  G2d (added, non-test caller -> a node in a test file): {d['cs']['g2d_added_nontest_to_test']}"
              + (f" e.g. {d['cs']['g2d_pairs'][:3]}" if d["cs"]["g2d_pairs"] else ""))
    n = int(opts.get("--list") or 0)
    for b in BUCKETS:
        for it in d["items"][b][:n]:
            print(f"    [{b}] {it['base']}->{it['variant']} {it['caller_name']} @{it['caller_file']} -> "
                  f"{it['callee_name']} @{it['callee_file']} ({it['caller_class']}, {it.get('verdict', '-')})")
    if opts.get("--json"):
        C.write_json(opts["--json"], d)
    return 0 if d["node_set_identical"] and not d["void"] else 3


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
