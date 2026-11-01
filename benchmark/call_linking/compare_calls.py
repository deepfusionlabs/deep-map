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

A DISPATCH truth (truth_roslyn.py, D3-cs; rows carry `dispatch`, PREREG_CS §8.5) is scored by §8.5's rules, and its
score says so (`truth_kind` "dispatch"; a jedi / tsc truth's score is exactly as before, without that key):
  T / T_s     the static pairs (each row's `defs`); `recall` and `by_form` are over T_s — G1's recall
  T_d         the dispatch pairs (each row's `dispatch`) minus T_s; `dispatch_recall` and `by_form_dispatch` over it
  every graph edge is confirmed (in T_s) | dispatch_confirmed (in T_d) | refuted (the caller has resolved sites,
              static or dispatch, of that callee NAME, and the pair is in neither set) | unverified; `by_grade`,
              `refuted_share`, `ambiguous_share` and `extracted_refuted_share` use this classification (G2b, G2c, G4a)
  static_only the classification with dispatch ignored (today's rule over T_s) — reported beside it, never decisive
  g4c         EXTRACTED edges in T_d (`extracted_dispatch`), and those whose producing site binds statically to an
              interface member (`extracted_dispatch_iface`, read through PREREG_CS AC5's fields: the producing row's
              `dispatch_rule` — every T_d pair comes from a row with dispatch targets; `exact` False when a producing
              row breaks AC5 (no `interface` / `override` rule) and the count could be higher). This is the decisive
              count (AC24: "a's site of that name" is a site whose dispatch targets include b). Beside it, never
              decisive (`any_site_decisive` False): `extracted_dispatch_iface_any_site`, the EXTRACTED T_d pairs (a, b)
              where ANY site of a named b's last name segment binds statically to an interface member

usage:
  python compare_calls.py <truth.json> <dump.json> [<dump.json> ...] [--src-prefix=src/flask/,...] [--json=out.json]
  python compare_calls.py pool <score.json> [<score.json> ...] [--family=py|js|cs]
      pooled per-form recall over several --json outputs (D3-G1: receiver-form recall, pooled per family), each
      the score of ONE dump, all of one variant on one engine; a --json written for several dumps (a list), a
      file that is not a score, or scores of different variants / engines are refused (exit 2) — pooling them
      would mix, e.g., V0 and VT into one recall; a score of a VOID dump is not data (exit 3). --family=cs pools
      only scores of a dispatch truth, all whole-graph or all --src-prefix (exit 2 otherwise), and adds the pooled
      dispatch recall over T_d beside the receiver-form recall over T_s
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402


def score(truth, dump, prefixes=None) -> dict:
    """The full scoring of one dump (pure; exact on synthetic inputs — the harness gate locks it). A dispatch truth
    (C.has_dispatch) is scored by score_cs; any other exactly as before."""
    if C.has_dispatch(truth):
        return score_cs(truth, dump, prefixes)
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


def _share(num, den):
    return round(num / den, 4) if den else None


def score_cs(truth, dump, prefixes=None) -> dict:
    """PREREG_CS §8.5 / §8.6 G for a dispatch truth: the §8.5 classification (confirmed / dispatch_confirmed / refuted
    / unverified), recall over T_s overall and per form, dispatch recall over T_d, the AMBIGUOUS and EXTRACTED-refuted
    shares, G4c's count, and the static-only classification beside it (never decisive). Pure."""
    nodes = dump.get("nodes") or []
    name_of = {n["key"]: n.get("name") for n in nodes}
    file_of = {n["key"]: n.get("source_file") for n in nodes}
    P = C.truth_pairs_cs(truth, nodes, prefixes)
    Ts, Td, by_cn, by_s, td_iface = P["Ts"], P["Td"], P["by_cn"], P["by_cn_static"], P["td_iface"]
    G = {p: g for p, g in C.edges_of(dump).items() if C.in_prefixes(file_of.get(p[0]), prefixes)}
    verdicts, static_v = Counter(), Counter()
    by_grade, static_grade = {}, {}
    for pair, grade in sorted(G.items()):
        v = C.classify_pair_cs(pair, Ts, Td, by_cn, name_of)
        vs = C.classify_pair(pair, Ts, by_s, name_of)
        verdicts[v] += 1
        static_v[vs] += 1
        g = by_grade.setdefault(grade, {"n": 0, "confirmed": 0, "dispatch_confirmed": 0, "refuted": 0,
                                        "unverified": 0})
        g["n"] += 1
        g[v.replace("-", "_")] += 1
        h = static_grade.setdefault(grade, {"n": 0, "confirmed": 0, "refuted": 0, "unverified": 0})
        h["n"] += 1
        h[vs] += 1

    def forms(T):
        out = {}
        for pair, fs in T.items():
            e = out.setdefault(C.best_form(fs), {"n": 0, "hit": 0})
            e["n"] += 1
            e["hit"] += int(pair in G)
        return {f: out[f] for f in C.FORM_ORDER if f in out}
    nG = len(G)
    hit, dhit = verdicts["confirmed"], verdicts[C.DISPATCH_CONFIRMED]
    ext, sext = by_grade.get("EXTRACTED", {}), static_grade.get("EXTRACTED", {})
    xfile = sorted(p for p, g in G.items() if g == "EXTRACTED" and C.norm_path(file_of.get(p[0]))
                   != C.norm_path(file_of.get(p[1])))
    xd = sorted(p for p, g in G.items() if g == "EXTRACTED" and p in Td)
    xd_iface = [p for p in xd if td_iface.get(p) is True]
    xd_unknown = [p for p in xd if td_iface.get(p) is None]
    # AC24: the count over ANY same-named site of the caller, reported beside the producing-site count, never decisive
    xd_any = [p for p in xd if (p[0], C.last_segment(name_of.get(p[1]))) in P["iface_sites"]]
    return {
        "dump": dump.get("_path"), "flags": dump.get("flags"), "engine": dump.get("engine"),
        "graphify_version": dump.get("graphify_version"), "prefixes": prefixes, "truth_kind": "dispatch",
        "truth_stats": dict(sorted(P["stats"].items())),
        "T": len(Ts), "T_s": len(Ts), "T_d": len(Td), "G": nG,
        "confirmed": hit, "dispatch_confirmed": dhit, "refuted": verdicts["refuted"],
        "unverified": verdicts["unverified"],
        "recall": _share(hit, len(Ts)), "dispatch_recall": _share(dhit, len(Td)),
        "precision_confirmed": _share(hit, nG), "precision_confirmed_or_dispatch": _share(hit + dhit, nG),
        "refuted_share": _share(verdicts["refuted"], nG),
        "ambiguous_share": _share(by_grade.get("AMBIGUOUS", {}).get("n", 0), nG),
        "extracted_refuted_share": (round(ext["refuted"] / ext["n"], 4) if ext.get("n") else None),
        "extracted_cross_file": len(xfile), "extracted_cross_file_pairs": [list(p) for p in xfile[:20]],
        "g4c": {"extracted_dispatch": len(xd), "extracted_dispatch_iface": len(xd_iface),
                "exact": not xd_unknown, "iface_unknown": len(xd_unknown),
                "pairs": [list(p) for p in (xd_iface + xd_unknown)[:20]],
                "extracted_dispatch_iface_any_site": len(xd_any), "any_site_decisive": False},
        "void": dump.get("void"),
        "by_grade": dict(sorted(by_grade.items())),
        "by_form": forms(Ts),
        "by_form_dispatch": forms(Td),
        "static_only": {"confirmed": static_v["confirmed"], "refuted": static_v["refuted"],
                        "unverified": static_v["unverified"], "refuted_share": _share(static_v["refuted"], nG),
                        "extracted_refuted_share": (round(sext["refuted"] / sext["n"], 4) if sext.get("n") else None),
                        "by_grade": dict(sorted(static_grade.items()))},
    }


def format_score(s) -> str:
    lines = [f"=== {s.get('dump')}  engine={s.get('engine')} graphify={s.get('graphify_version')} flags={s.get('flags')}"
             + (f"  prefixes={s['prefixes']}" if s.get("prefixes") else "")]
    if s.get("truth_kind") == "dispatch":
        return "\n".join(lines + _format_cs(s))
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


def _format_cs(s) -> list:
    ts = s["truth_stats"]
    so = s["static_only"]
    g4 = s["g4c"]
    out = ["truth (dispatch) sites={sites} resolved_in_repo={r} unresolved/external={u} callee_unmapped={cu} "
           "caller_unmapped={ca} dispatch_sites={ds} dispatch_unmapped={du}".format(
               sites=ts.get("sites", 0), r=ts.get("resolved_in_repo", 0), u=ts.get("unresolved_or_external", 0),
               cu=ts.get("callee_unmapped", 0), ca=ts.get("caller_unmapped", 0), ds=ts.get("dispatch_sites", 0),
               du=ts.get("dispatch_unmapped", 0)),
           f"|T_s|={s['T_s']} |T_d|={s['T_d']} |G|={s['G']} confirmed={s['confirmed']} recall={s['recall']} "
           f"dispatch-confirmed={s['dispatch_confirmed']} dispatch recall={s['dispatch_recall']} "
           f"refuted={s['refuted']} ({s['refuted_share']}) unverified={s['unverified']} "
           f"AMBIGUOUS share={s['ambiguous_share']} EXTRACTED refuted share={s['extracted_refuted_share']} "
           f"EXTRACTED cross-file={s['extracted_cross_file']}",
           f"G4c: EXTRACTED dispatch-only edges={g4['extracted_dispatch']} of them through an interface binding="
           f"{g4['extracted_dispatch_iface']}" + ("" if g4["exact"] else
                                                  f" (NOT EXACT: {g4['iface_unknown']} whose producing row names "
                                                  f"no dispatch_rule interface / override, PREREG_CS AC5)")
           + f"; through an interface binding at any same-named site (AC24, never decisive)="
             f"{g4['extracted_dispatch_iface_any_site']}",
           f"static-only (dispatch ignored; never decisive): confirmed={so['confirmed']} refuted={so['refuted']} "
           f"({so['refuted_share']}) unverified={so['unverified']} EXTRACTED refuted share="
           f"{so['extracted_refuted_share']}"]
    if s.get("void"):
        out.append(f"VOID (not data): {s['void']}")
    for g, d in s["by_grade"].items():
        out.append(f"   {g:10s} n={d['n']:6d} confirmed={d['confirmed']:6d} dispatch-confirmed="
                   f"{d['dispatch_confirmed']:6d} refuted={d['refuted']:6d} unverified={d['unverified']:6d}")
    out.append("   recall over T_s by call form: " + "  ".join(f"{f}={d['hit']}/{d['n']}"
                                                            for f, d in s["by_form"].items()))
    out.append("   recall over T_d by call form: " + "  ".join(f"{f}={d['hit']}/{d['n']}"
                                                            for f, d in s["by_form_dispatch"].items()))
    return out


def pool_problem(named, family=None) -> "tuple[int, str] | None":
    """Why a list of (path, loaded JSON) cannot be pooled, as (exit code, message), or None. One pool is the scores
    of single dumps of ONE variant on ONE engine: a --json written for several dumps holds a LIST of scores, and
    flattening it would mix variants (V0 and VT) into one recall. family "cs" (PREREG_CS G1a) pools only scores of
    a dispatch truth, all of them whole-graph or all of them --src-prefix; another family never takes one."""
    if not named:
        return 2, "no score files given"
    for path, s in named:
        if isinstance(s, list):
            return 2, (f"{path} holds the scores of several dumps ({len(s)}): write one dump per --json and pool "
                       f"one variant at a time")
        if not isinstance(s, dict) or not isinstance(s.get("by_form"), dict):
            return 2, f"{path} is not a compare_calls score (no by_form)"
    if family is not None:
        if family not in C.RECEIVER_FORMS:
            return 2, f"unknown family {family!r} (known: {', '.join(sorted(C.RECEIVER_FORMS))})"
        for path, s in named:
            disp = s.get("truth_kind") == "dispatch"
            if (family == "cs") != disp:
                return 2, (f"{path} is the score of {'a dispatch' if disp else 'a jedi / tsc'} truth: --family={family} "
                           f"pools {'only' if family == 'cs' else 'no'} scores of a dispatch (Roslyn) truth")
        if family == "cs" and len({bool(s.get("prefixes")) for _p, s in named}) > 1:
            return 2, ("--family=cs: some scores are whole-graph and some --src-prefix (library code): pool one kind "
                       "at a time")
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
    if any(isinstance(s.get("by_form_dispatch"), dict) for s in scores):
        # a dispatch truth's T_d (PREREG_CS §8.6 G: dispatch recall over T_d), pooled the same way, beside T_s
        dt, dh = Counter(), Counter()
        for s in scores:
            for f, d in (s.get("by_form_dispatch") or {}).items():
                dt[f] += d["n"]
                dh[f] += d["hit"]
        out["dispatch_by_form"] = {f: {"n": dt[f], "hit": dh[f], "recall": round(dh[f] / dt[f], 4) if dt[f] else None}
                                   for f in C.FORM_ORDER if dt[f]}
        n_d, h_d = sum(dt.values()), sum(dh.values())
        out["dispatch_n"], out["dispatch_hit"] = n_d, h_d
        out["dispatch_recall"] = round(h_d / n_d, 4) if n_d else None
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--src-prefix', '--json', '--family'))
    if args and args[0] == "pool":
        named = [(p, C.load_json(p)) for p in args[1:]]
        problem = pool_problem(named, opts.get("--family") or None)
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
    truth = C.load_truth(args[0])
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
