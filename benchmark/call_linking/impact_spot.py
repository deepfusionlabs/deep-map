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

The C# spots of D3-cs (PREREG_CS §8.3, a table apart from the one above: CS_SPOTS, then CS_RESERVES in substitution
order) — jellyfin__jellyfin J1-J5 (reserves J-r1..J-r5), dotnet__eShop E1-E5 (reserves E-r1..E-r3). Their reference
is T_s ∪ T_d of the Roslyn dispatch truth (_common.truth_as_dump), and a C# spot must map to EXACTLY ONE graph node.

usage: python impact_spot.py --truth=<truth.json> (--symbols=a,b@f | --corpus=<id> | --final=<substitute.json>)
       <dump.json> [<dump.json> ...] [--base=<V0 dump.json>] [--one-node] [--json=out.json]
       --base: also report on how many symbols the hop-1 recall improved over the base (the D3-G1 count).
       --final: the spot list a `substitute` run settled (its `final`), for the C# corpus it names. PREREG_CS AC12: a
                C# corpus is measured on that FINAL list only — `--corpus=<C# id>` without --final is refused (exit
                2), and a --final whose corpus differs from --corpus, that `substitute` cannot have written, or given
                with --symbols is refused too; the list is measured exactly as settled (fewer than five spots when
                the reserves ran out; with --base the G1c count is printed).
       --one-node: the exactly-one-node check for an ad-hoc --symbols list (always on for a C# corpus).
   python impact_spot.py substitute --truth=<truth.json> --corpus=<C# id> --base39=<V0 g39> --base51=<V0 g51>
       [--json=out.json]
       PREREG_CS §8.3's substitution rule (step 3 of §8.1: V0 dumps and the truth only). A spot is replaced when, on
       either engine, it does not map to exactly one node, the reference gives it no hop-1 caller, or its V0 hop-1
       recall is already 1 (SATURATED); its replacement is the first reserve of the corpus, in order, that passes
       the same three checks on both engines. Writes the final list and every check with its V0 numbers; a corpus
       that runs out of reserves keeps fewer spots (reported, exit 0).

A spot whose base (V0) hop-1 recall is already 1, or that has no reference caller, can never "improve": it is
SATURATED. PREREG §4 requires every registered spot's V0 recall to be below 1 (the D3-G1 "3 of 5" must be a
count over five spots that CAN improve); with --base each saturated spot is flagged and the run exits 1, so a
saturated spot is never silently counted as "not improved". With --base a C# spot that does not map to exactly one
node (on the base or the dump) also exits 1: it must be substituted.
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

# D3-cs (PREREG_CS §8.3), apart from SPOTS (harness gate H locks SPOTS to PREREG.md and to run_matrix.sh's PREREG
# corpus table): the five spots per C# corpus, in the registered order, then the reserves in substitution order.
CS_SPOTS = {
    "jellyfin__jellyfin": [
        "ActivityManager.CreateAsync@Jellyfin.Server.Implementations/Activity/ActivityManager.cs",
        "TmdbClientManager.GetProfileUrl@MediaBrowser.Providers/Plugins/Tmdb/TmdbClientManager.cs",
        "RequestHelpers.GetUserId@Jellyfin.Api/Helpers/RequestHelpers.cs",
        "ItemResolveArgs.GetCollectionType@MediaBrowser.Controller/Library/ItemResolveArgs.cs",
        "MediaSourceInfo.InferTotalBitrate@MediaBrowser.Model/Dto/MediaSourceInfo.cs"],
    "dotnet__eShop": [
        "OrderRepository.GetAsync@src/Ordering.Infrastructure/Repositories/OrderRepository.cs",
        "OrderStatusNotificationService.NotifyOrderStatusChangedAsync@src/WebApp/Services/OrderStatus/"
        "OrderStatusNotificationService.cs",
        "UriHelper.CombineUri@src/ClientApp/Helpers/UriHelper.cs",
        "CustomerBasket.AddItemToBasket@src/ClientApp/Models/Basket/CustomerBasket.cs",
        "Order.AddOrderItem@src/Ordering.Domain/AggregatesModel/OrderAggregate/Order.cs"],
}
CS_RESERVES = {
    "jellyfin__jellyfin": [
        "VideoResolver.IsVideoFile@Emby.Naming/Video/VideoResolver.cs",
        "DirectoryService.GetFile@MediaBrowser.Controller/Providers/DirectoryService.cs",
        "MetadataResult.AddPerson@MediaBrowser.Controller/Providers/MetadataResult.cs",
        "ProviderManager.QueueRefresh@MediaBrowser.Providers/Manager/ProviderManager.cs",
        "EventManager.PublishAsync@Jellyfin.Server.Implementations/Events/EventManager.cs"],
    "dotnet__eShop": [
        "OrderingIntegrationEventService.AddAndSaveEventAsync@src/Ordering.API/Application/IntegrationEvents/"
        "OrderingIntegrationEventService.cs",
        "Order.GetTotal@src/Ordering.Domain/AggregatesModel/OrderAggregate/Order.cs",
        "IdentityService.GetAuthTokenAsync@src/ClientApp/Services/Identity/IdentityService.cs"],
}
_CS_ID_LETTER = {"jellyfin__jellyfin": "J", "dotnet__eShop": "E"}
# PREREG_CS's ids: J1..J5 / E1..E5 for the spots, J-r1.. / E-r1.. for the reserves, by position.
CS_IDS = {**{s: f"{_CS_ID_LETTER[c]}{i}" for c, v in CS_SPOTS.items() for i, s in enumerate(v, 1)},
          **{s: f"{_CS_ID_LETTER[c]}-r{i}" for c, v in CS_RESERVES.items() for i, s in enumerate(v, 1)}}
CS_SPOT_NAMES = tuple(s.split("@")[0] for c in CS_SPOTS for s in CS_SPOTS[c])
CS_RESERVE_NAMES = tuple(s.split("@")[0] for c in CS_RESERVES for s in CS_RESERVES[c])


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


def spot_problems(truth, base, symbol) -> tuple:
    """(the spot's row on `base`, its problems): PREREG_CS §8.3's three substitution checks on one V0 dump —
    `Name@file` maps to exactly one graph node; the reference gives it a hop-1 caller; its V0 hop-1 recall is below 1
    (else SATURATED). An empty list passes."""
    r = spot(C.truth_as_dump(truth, base), base, symbol)
    why = []
    if r["targets"] != 1:
        why.append(f"maps to {r['targets']} graph nodes, not exactly one")
    if not r["ref"]:
        why.append("the reference gives it no hop-1 caller")
    elif r["recall"] is not None and r["recall"] >= 1.0:
        why.append("SATURATED: its V0 hop-1 recall is already 1")
    return r, why


def substitute(truth, bases: dict, corpus: str) -> dict:
    """PREREG_CS §8.3's substitution rule for one C# corpus. `bases` = {engine label: V0 dump} (both engines). Each
    registered spot, in order, is kept when it passes the three checks on every engine; else it is replaced by the
    first unused reserve, in order, that passes them on every engine. A reserve that fails is never used. Pure."""
    if corpus not in CS_SPOTS:
        raise KeyError(f"{corpus!r} is not a C# corpus of PREREG_CS (known: {', '.join(CS_SPOTS)})")
    checks = {}

    def problems(sym):
        if sym not in checks:
            checks[sym] = {}
            for eng, b in sorted(bases.items()):
                r, why = spot_problems(truth, b, sym)
                checks[sym][eng] = {"targets": r["targets"], "ref": r["ref"], "got": r["got"], "hit": r["hit"],
                                    "recall": r["recall"], "precision": r["precision"], "conf": r["conf"],
                                    "problems": why}
        return {eng: c["problems"] for eng, c in checks[sym].items() if c["problems"]}
    reserves = list(CS_RESERVES.get(corpus, []))
    final, subs = [], []
    for sym in CS_SPOTS[corpus]:
        bad = problems(sym)
        if not bad:
            final.append(sym)
            continue
        rep = None
        while reserves:
            cand = reserves.pop(0)
            if not problems(cand):
                rep = cand
                break
        subs.append({"spot": sym, "id": CS_IDS.get(sym), "problems": bad, "replacement": rep,
                     "replacement_id": CS_IDS.get(rep) if rep else None})
        if rep is not None:
            final.append(rep)
    return {"corpus": corpus, "engines": sorted(bases), "registered": list(CS_SPOTS[corpus]),
            "reserves": list(CS_RESERVES.get(corpus, [])), "final": final,
            "final_ids": [CS_IDS.get(s) for s in final], "n_final": len(final), "substitutions": subs,
            "reserves_unused": reserves, "checks": checks}


def _cmd_substitute(opts) -> int:
    if not (opts.get("--truth") and opts.get("--corpus") and opts.get("--base39") and opts.get("--base51")):
        print(__doc__, file=sys.stderr)
        return 2
    truth = C.load_truth(opts["--truth"])
    bases = {"g39": C.load_json(opts["--base39"]), "g51": C.load_json(opts["--base51"])}
    void = [f"{e}: {d['void']}" for e, d in sorted(bases.items()) if d.get("void")]
    if void:
        print(f"[impact_spot] REFUSED: a V0 dump is VOID (not data): {'; '.join(void)}", file=sys.stderr)
        return 3
    try:
        res = substitute(truth, bases, opts["--corpus"])
    except KeyError as exc:
        print(f"[impact_spot] {exc}", file=sys.stderr)
        return 2
    res["base_dumps"] = {"g39": os.path.basename(opts["--base39"]), "g51": os.path.basename(opts["--base51"])}
    for sym in res["registered"] + [s["replacement"] for s in res["substitutions"] if s["replacement"]]:
        for eng, c in res["checks"].get(sym, {}).items():
            print(f"   {CS_IDS.get(sym, '?'):5s} {eng} {sym:90s} nodes={c['targets']} ref={c['ref']} hit={c['hit']} "
                  f"R={c['recall']}" + (f"  -> {'; '.join(c['problems'])}" if c["problems"] else ""))
    for s in res["substitutions"]:
        print(f"   SUBSTITUTED {s['id']} {s['spot']} -> {s['replacement_id'] or 'none left'} {s['replacement'] or ''}")
    print(f"[impact_spot] {res['corpus']}: {res['n_final']} final spot(s): {', '.join(res['final_ids'])}")
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 0


def final_problem(final, corpus=None) -> "str | None":
    """Why `final` (a loaded `substitute` output) is not a C# corpus's settled spot list for `corpus` (the --corpus
    value, when given), or None (PREREG_CS AC12): it names a C# corpus of CS_SPOTS — the --corpus one, if given — its
    `registered` list is that corpus's CS_SPOTS, and every final symbol is one of that corpus's registered spots or
    reserves, each once (a list `substitute` cannot have written is refused, never measured)."""
    if not isinstance(final, dict) or not isinstance(final.get("final"), list):
        return "it is not an `impact_spot.py substitute` output (no `final` list)"
    fc = final.get("corpus")
    if fc not in CS_SPOTS:
        return f"it names corpus {fc!r}, not a C# corpus of PREREG_CS ({', '.join(CS_SPOTS)})"
    if corpus and corpus != fc:
        return f"it is {fc}'s final list, not {corpus}'s (--corpus={corpus})"
    if list(final.get("registered") or []) != list(CS_SPOTS[fc]):
        return f"its `registered` list is not {fc}'s registered spots (PREREG_CS §8.3)"
    if sorted(final.get("engines") or []) != ["g39", "g51"]:
        return f"it was not settled on both engines' V0 dumps (engines {final.get('engines')!r}; §8.3: either engine)"
    allowed = set(CS_SPOTS[fc]) | set(CS_RESERVES.get(fc, []))
    bad = [s for s in final["final"] if s not in allowed]
    if bad or len(set(final["final"])) != len(final["final"]):
        return f"its final list holds {bad or 'a repeated symbol'}: only {fc}'s registered spots and reserves, once each"
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--truth', '--symbols', '--corpus', '--base', '--json', '--final', '--base39',
                                    '--base51'))
    if args[:1] == ["substitute"]:
        return _cmd_substitute(opts)
    corpus = opts.get("--corpus") or ""
    final = C.load_json(opts["--final"]) if opts.get("--final") else None
    if final is not None:
        # PREREG_CS AC12: a C# corpus is measured on its FINAL spot list from step 3 (the `substitute` output),
        # exactly that list, never topped up from the registered one nor replaced by --symbols
        why = final_problem(final, corpus or None)
        if why is None and opts.get("--symbols"):
            why = "--symbols and --final name two spot lists: the final list is the one measured"
        if why:
            print(f"[impact_spot] REFUSED --final={opts['--final']}: {why}", file=sys.stderr)
            return 2
        corpus = final["corpus"]
    elif corpus in CS_SPOTS and not opts.get("--symbols"):
        print(f"[impact_spot] REFUSED: {corpus} is a C# corpus: PREREG_CS AC12 measures its FINAL spot list from step "
              f"3 — give --final=<{corpus}>/spots_final.json (the output of `impact_spot.py substitute`), never the "
              f"registered list", file=sys.stderr)
        return 2
    syms = [s for s in (opts.get("--symbols") or "").split(",") if s]
    if not syms:   # a settled list is used as it is (never topped up from the registered one)
        syms = list(final.get("final") or []) if final is not None else list(SPOTS.get(corpus) or [])
    one_node = corpus in CS_SPOTS or "--one-node" in opts
    if not opts.get("--truth") or not args or not (syms or final is not None):
        print(__doc__, file=sys.stderr)
        return 2
    truth = C.load_truth(opts["--truth"])
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
                if one_node:
                    r["base_targets"] = b["targets"]
            if one_node:
                r["one_node"] = r["targets"] == 1 and r.get("base_targets", 1) == 1
                if s in CS_IDS:
                    r["id"] = CS_IDS[s]
            rows.append(r)
            print(f"   {s:60s} ref={r['ref']:4d} got={r['got']:5d} hit={r['hit']:4d} P={r['precision']} "
                  f"R={r['recall']} conf={r['conf']}" + (f" base R={r['base_recall']}" if base is not None else "")
                  + ("" if r.get("one_node", True) else f" NOT ONE NODE ({r['targets']} nodes)"))
        if base is not None:
            sat = [r["symbol"] for r in rows if r["saturated"]]
            print(f"   hop-1 recall improved on {sum(1 for r in rows if r['improved'])} of {len(rows)} symbols"
                  + (f"; SATURATED at base (can never improve — PREREG §4 requires V0 recall < 1): {sat}" if sat else ""))
            multi = [r["symbol"] for r in rows if r.get("one_node") is False]
            if multi:
                print(f"   NOT EXACTLY ONE GRAPH NODE (PREREG_CS §8.3: substitute it): {multi}")
            if final is not None:
                k = sum(1 for r in rows if r["improved"])
                print(f"   G1c ({corpus}, the final list of {len(rows)} spot(s) {final.get('final_ids')}): improved on "
                      f"{k}; G1c needs at least 3" + (" (fewer than 3 final spots: G1c fails)" if len(rows) < 3 else ""))
        results.append({"dump": os.path.basename(p), "spots": rows})
    if opts.get("--json"):
        C.write_json(opts["--json"], results)
    bad = any(r.get("saturated") or (base is not None and r.get("one_node") is False)
              for res in results for r in res["spots"])
    return 1 if bad else 0


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
