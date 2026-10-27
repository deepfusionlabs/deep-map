#!/usr/bin/env python3
"""Score offline :CALLS dumps (offline_calls.py) against a reference resolver's call sites (truth_jedi.py /
truth_ts.js) — design §8.4 G.

A truth pair is (caller node, callee node): the call site's innermost enclosing def that HAS a graph node
(else the file's :Module), and each in-corpus definition the resolver bound, mapped to the graph node on the
definition's line (or its decorator / name line). Every graph edge is then one of:
  confirmed   the pair is a truth pair;
  refuted     the caller has resolved sites of that callee NAME, all bound to OTHER nodes;
  unverified  neither (no resolved site of that name in the caller: unresolved, external, or a shape the
              resolver does not see).
Recall = |G ∩ T| / |T|, per call form too (a pair counts under its first form in FORM_ORDER; a :Class target
is "ctor"). --src-prefix restricts BOTH sides to callers under the given path prefix(es) (library code): a pair
belongs to the prefix when its CALLER is under it, whatever file the callee is in.
`extracted_cross_file` counts the EXTRACTED edges whose caller and callee are in different files — design §4
forbids them (EXTRACTED needs the whole evidence chain in the caller's file), so any is a failed variant.
A dump carrying `void` (offline_calls.py) is not data: it is scored for diagnosis only and the exit code is 3.

usage:
  python compare_calls.py <truth.json> <dump.json> [<dump.json> ...] [--src-prefix=src/flask/,...] [--json=out.json]
  python compare_calls.py pool <score.json> [<score.json> ...] [--family=py|js]
      pooled per-form recall over several --json outputs (D3-G1: receiver-form recall, pooled per family), each
      the score of ONE dump, all of one variant on one engine; a --json written for several dumps (a list), a
      file that is not a score, or scores of different variants / engines are refused (exit 2) — pooling them
      would mix, e.g., V0 and VT into one recall; a score of a VOID dump is not data (exit 3)
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402


def score(truth, dump, prefixes=None) -> dict:
    """The full scoring of one dump (pure; exact on synthetic inputs — the harness gate locks it)."""
    nodes = dump.get("nodes") or []
    name_of = {n["key"]: n.get("name") for n in nodes}
    file_of = {n["key"]: n.get("source_file") for n in nodes}
    T, by_cn, st = C.truth_pairs(truth, nodes, prefixes)
    G = {p: g for p, g in C.edges_of(dump).items() if C.in_prefixes(file_of.get(p[0]), prefixes)}
    by_grade = {}
    verdicts = Counter()
    for pair, grade in sorted(G.items()):
        v = C.classify_pair(pair, T, by_cn, name_of)
        verdicts[v] += 1
        g = by_grade.setdefault(grade, {"n": 0, "confirmed": 0, "refuted": 0, "unverified": 0})
        g["n"] += 1
        g[v] += 1
    by_form = {}
    for pair, forms in T.items():
        f = C.best_form(forms)
        e = by_form.setdefault(f, {"n": 0, "hit": 0})
        e["n"] += 1
        e["hit"] += int(pair in G)
    hit = verdicts["confirmed"]
    nG = len(G)
    ext = by_grade.get("EXTRACTED", {})
    xfile = sorted(p for p, g in G.items() if g == "EXTRACTED" and C.norm_path(file_of.get(p[0]))
                   != C.norm_path(file_of.get(p[1])))
    return {
        "dump": dump.get("_path"), "flags": dump.get("flags"), "engine": dump.get("engine"),
        "graphify_version": dump.get("graphify_version"), "prefixes": prefixes,
        "truth_stats": dict(sorted(st.items())),
        "T": len(T), "G": nG, "confirmed": hit, "refuted": verdicts["refuted"], "unverified": verdicts["unverified"],
        "recall": round(hit / len(T), 4) if T else None,
        "precision_confirmed": round(hit / nG, 4) if nG else None,
        "refuted_share": round(verdicts["refuted"] / nG, 4) if nG else None,
        "ambiguous_share": round(by_grade.get("AMBIGUOUS", {}).get("n", 0) / nG, 4) if nG else None,
        "extracted_refuted_share": (round(ext["refuted"] / ext["n"], 4) if ext.get("n") else None),
        "extracted_cross_file": len(xfile), "extracted_cross_file_pairs": [list(p) for p in xfile[:20]],
        "void": dump.get("void"),
        "by_grade": dict(sorted(by_grade.items())),
        "by_form": {f: by_form[f] for f in C.FORM_ORDER if f in by_form},
    }


def format_score(s) -> str:
    lines = [f"=== {s.get('dump')}  engine={s.get('engine')} graphify={s.get('graphify_version')} flags={s.get('flags')}"
             + (f"  prefixes={s['prefixes']}" if s.get("prefixes") else "")]
    ts = s["truth_stats"]
    lines.append("truth sites={sites} resolved_in_repo={r} unresolved/external={u} callee_unmapped={cu} "
                 "caller_unmapped={ca}".format(sites=ts.get("sites", 0), r=ts.get("resolved_in_repo", 0),
                                               u=ts.get("unresolved_or_external", 0), cu=ts.get("callee_unmapped", 0),
                                               ca=ts.get("caller_unmapped", 0)))
    lines.append(f"|T|={s['T']} |G|={s['G']} confirmed={s['confirmed']} recall={s['recall']} "
                 f"precision(confirmed)={s['precision_confirmed']} refuted={s['refuted']} ({s['refuted_share']}) "
                 f"unverified={s['unverified']} AMBIGUOUS share={s['ambiguous_share']} "
                 f"EXTRACTED refuted share={s['extracted_refuted_share']} "
                 f"EXTRACTED cross-file={s['extracted_cross_file']}")
    if s.get("void"):
        lines.append(f"VOID (not data): {s['void']}")
    for g, d in s["by_grade"].items():
        lines.append(f"   {g:10s} n={d['n']:6d} confirmed={d['confirmed']:6d} refuted={d['refuted']:6d} "
                     f"unverified={d['unverified']:6d}")
    lines.append("   recall by call form: " + "  ".join(f"{f}={d['hit']}/{d['n']}" for f, d in s["by_form"].items()))
    return "\n".join(lines)


def pool_problem(named) -> "tuple[int, str] | None":
    """Why a list of (path, loaded JSON) cannot be pooled, as (exit code, message), or None. One pool is the scores
    of single dumps of ONE variant on ONE engine: a --json written for several dumps holds a LIST of scores, and
    flattening it would mix variants (V0 and VT) into one recall."""
    if not named:
        return 2, "no score files given"
    for path, s in named:
        if isinstance(s, list):
            return 2, (f"{path} holds the scores of several dumps ({len(s)}): write one dump per --json and pool "
                       f"one variant at a time")
        if not isinstance(s, dict) or not isinstance(s.get("by_form"), dict):
            return 2, f"{path} is not a compare_calls score (no by_form)"
    for path, s in named:
        if s.get("void"):
            return 3, f"{path} scores a VOID dump (not data): {s['void']}"
    ident = {}
    for path, s in named:
        key = (C.dump_json(s.get("flags") or {}).strip(), s.get("engine"), s.get("graphify_version"))
        ident.setdefault(key, []).append(path)
    if len(ident) > 1:
        return 2, ("the scores come from different variants or engines (flags / engine / graphify version): pool one "
                   "variant on one engine at a time — " + "; ".join(f"{k[0]} {k[1]} {k[2]}: {len(v)} file(s)"
                                                                    for k, v in sorted(ident.items(), key=repr)))
    return None


def pool(scores, family=None) -> dict:
    """Sum per-form hits/totals over several score dicts; with `family`, also the pooled receiver-form recall."""
    tot = Counter()
    hit = Counter()
    for s in scores:
        if not isinstance(s, dict):
            raise TypeError(f"pool: a score is a dict, got {type(s).__name__} (a --json written for several dumps "
                            f"holds a list: pool one dump's score per file)")
        for f, d in (s.get("by_form") or {}).items():
            tot[f] += d["n"]
            hit[f] += d["hit"]
    out = {"by_form": {f: {"n": tot[f], "hit": hit[f], "recall": round(hit[f] / tot[f], 4) if tot[f] else None}
                       for f in C.FORM_ORDER if tot[f]}}
    if family:
        forms = C.RECEIVER_FORMS[family]
        n = sum(tot[f] for f in forms)
        h = sum(hit[f] for f in forms)
        out["receiver_forms"] = list(forms)
        out["receiver_recall"] = round(h / n, 4) if n else None
        out["receiver_n"], out["receiver_hit"] = n, h
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--src-prefix', '--json', '--family'))
    if args and args[0] == "pool":
        named = [(p, C.load_json(p)) for p in args[1:]]
        problem = pool_problem(named)
        if problem:
            print(f"pool: {problem[1]}", file=sys.stderr)
            return problem[0]
        res = pool([s for _p, s in named], opts.get("--family") or None)
        print(C.dump_json(res), end="")
        return 0
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    prefixes = C.parse_prefixes(opts.get("--src-prefix"))
    truth = C.load_json(args[0])
    results = []
    for p in args[1:]:
        d = C.load_json(p)
        d["_path"] = os.path.basename(p)
        s = score(truth, d, prefixes)
        results.append(s)
        print(format_score(s))
    if opts.get("--json"):
        C.write_json(opts["--json"], results[0] if len(results) == 1 else results)
    return 3 if any(r.get("void") for r in results) else 0


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
