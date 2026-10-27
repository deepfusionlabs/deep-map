#!/usr/bin/env python3
"""Engine parity (design §8.4 E, as amended before RC by PREREG amendment A1): the same corpus, code and flags
dumped under graphify 0.8.39 and 0.8.51, compared on VERSION-STABLE identities — a node is `label:name@file:line`
(node keys differ by engine).

The comparison is made on the edges whose BOTH endpoints exist on both engines (the common node set: for the E
criterion, the stable keys present in all four dumps). The two engines do not always make the same nodes —
graphify 0.8.39 makes no node for a TS `static create = (...) => ...` member that 0.8.51 does (zod) — and an edge
touching such a node can exist on one engine only whatever the variant does, so it says nothing about the
variant's engine parity. The RAW symmetric difference over all edges is reported beside it, never decisive, with
the nodes that exist on one engine only.

  parity      the symmetric difference of the two final edge sets on the common endpoints (+ grade
              disagreements on common pairs), and the raw one over all edges
  E criterion with --base39/--base51 (the same corpus's V0 dumps): the variant's symmetric difference on the common
              endpoints must be <= V0's; the variant's own changes (vs its base) must agree across the engines — an
              edge the variant adds, removes or regrades on one engine only, between two common endpoints, is an
              ENGINE-SPECIFIC change (G6; the raw count is reported too)
  projection  the label-set projection: per caller, the set of callee LAST-NAME labels (diagnostic only —
              e.g. flask's (source, label) pairs agree across the pin range; never product logic)
  typed tuples  typed_tuple_verdict (a function; the dumps carry no typed tuples — the engine-parity gate
              tests/typed_calls_engine_parity_gates.py computes them): §8.4 E bullet 1 as registered
              (holds_as_registered) and with the tuples a decision recorded BEFORE RC accepted (PRE_RC_DECISIONS.md:
              PD-1 lists two zod tuples; exactly those, never a rule)

usage: python engdiff.py <variant_0839.json> <variant_0851.json> [--base39=V0_0839.json --base51=V0_0851.json]
       [--list=N] [--json=out.json]
Exit 0 when the E criterion holds (or no bases given), 1 when the variant's parity on the common endpoints is worse
than V0's, 3 VOID (not data): a dump carries `void` (offline_calls.py), or a variant's node set differs from its
base's on one engine (every §8.3 variant is edge-only; the common-endpoint domain would differ).
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402


def stable_nodes(dump) -> set:
    """The version-stable identities of a dump's nodes."""
    return {C.stable_key(n) for n in dump.get("nodes") or []}


def collisions(dump) -> int:
    """Nodes whose stable identity another node of the same dump shares (they collapse into one identity)."""
    nodes = dump.get("nodes") or []
    return len(nodes) - len(stable_nodes(dump))


def stable_edges(dump) -> dict:
    """{(stable caller, stable callee): grade}. Two nodes sharing a stable key collapse (`collisions`)."""
    sk = {n["key"]: C.stable_key(n) for n in dump.get("nodes") or []}
    out = {}
    for (a, b), g in C.edges_of(dump).items():
        out[(sk.get(a, f"?{a}"), sk.get(b, f"?{b}"))] = g
    return out


def on_nodes(edges: dict, keep: set) -> dict:
    """The edges whose BOTH endpoints are in `keep` (an endpoint that is no node, `?key`, never is)."""
    return {p: g for p, g in edges.items() if p[0] in keep and p[1] in keep}


def projection(dump) -> set:
    """{(stable caller, callee last-name label)} — the version-invariant label-set projection."""
    return {(a, C.last_segment(b.split(":", 1)[1].rsplit("@", 1)[0])) for (a, b) in stable_edges(dump)}


def parity(a, b, common=None) -> dict:
    """a vs b on the common endpoints (`common`, default: the stable nodes both dumps have) and over all edges."""
    ea, eb = stable_edges(a), stable_edges(b)
    na, nb = stable_nodes(a), stable_nodes(b)
    common = (na & nb) if common is None else set(common)
    ca, cb = on_nodes(ea, common), on_nodes(eb, common)
    pa, pb = projection(a), projection(b)
    return {"edges_a": len(ea), "edges_b": len(eb), "symdiff": len(set(ea) ^ set(eb)),
            "only_a": sorted(set(ea) - set(eb)), "only_b": sorted(set(eb) - set(ea)),
            "nodes_a": len(na), "nodes_b": len(nb), "nodes_common": len(common),
            "nodes_only_a": sorted(na - nb), "nodes_only_b": sorted(nb - na),
            "collisions_a": collisions(a), "collisions_b": collisions(b),
            "edges_common_a": len(ca), "edges_common_b": len(cb), "symdiff_common": len(set(ca) ^ set(cb)),
            "only_a_common": sorted(set(ca) - set(cb)), "only_b_common": sorted(set(cb) - set(ca)),
            "grade_diff": sorted((p, ca[p], cb[p]) for p in set(ca) & set(cb) if ca[p] != cb[p]),
            "projection_a": len(pa), "projection_b": len(pb), "projection_symdiff": len(pa ^ pb)}


def changes(base, variant, common=None) -> set:
    """The variant's own edge changes vs its base, on stable identities: {(pair, base grade, variant grade)};
    with `common`, only the pairs whose both endpoints are in it."""
    e0, e1 = stable_edges(base), stable_edges(variant)
    if common is not None:
        e0, e1 = on_nodes(e0, common), on_nodes(e1, common)
    return {(p, e0.get(p), e1.get(p)) for p in set(e0) | set(e1) if e0.get(p) != e1.get(p)}


def void_reasons(*named) -> list:
    """The reasons a set of (label, dump) pairs is not data: a dump carrying `void` (offline_calls.py)."""
    return [f"{label} is VOID: {d.get('void')}" for label, d in named if d.get("void")]


def criterion(v39, v51, b39, b51) -> dict:
    """E (amendment A1): the variant's symmetric difference between the engines, on the edges whose both endpoints
    exist in all four dumps, <= V0's on the same edges; the raw comparison over all edges is reported."""
    n = {"v39": stable_nodes(v39), "v51": stable_nodes(v51), "b39": stable_nodes(b39), "b51": stable_nodes(b51)}
    common = n["v39"] & n["v51"] & n["b39"] & n["b51"]
    pv, pb = parity(v39, v51, common), parity(b39, b51, common)
    c39, c51 = changes(b39, v39, common), changes(b51, v51, common)
    r39, r51 = changes(b39, v39), changes(b51, v51)
    void = void_reasons(("the 0.8.39 variant dump", v39), ("the 0.8.51 variant dump", v51),
                        ("the 0.8.39 V0 dump", b39), ("the 0.8.51 V0 dump", b51))
    for eng, v, b in (("0.8.39", "v39", "b39"), ("0.8.51", "v51", "b51")):
        if n[v] != n[b]:
            void.append(f"the variant's node set differs from its V0 base's on {eng} ({len(n[v] - n[b])} only in the "
                        f"variant, {len(n[b] - n[v])} only in V0): every §8.3 variant is edge-only")
    return {"holds": pv["symdiff_common"] <= pb["symdiff_common"],
            "variant_symdiff_common": pv["symdiff_common"], "v0_symdiff_common": pb["symdiff_common"],
            "holds_raw": pv["symdiff"] <= pb["symdiff"],
            "variant_symdiff_raw": pv["symdiff"], "v0_symdiff_raw": pb["symdiff"],
            "variant_grade_diff": len(pv["grade_diff"]), "v0_grade_diff": len(pb["grade_diff"]),
            "nodes_common": len(common),
            "nodes_only_0839": sorted((n["v39"] | n["b39"]) - common),
            "nodes_only_0851": sorted((n["v51"] | n["b51"]) - common),
            "changes_0839": len(c39), "changes_0851": len(c51),
            "engine_specific_changes": sorted(c39 ^ c51, key=repr),
            "engine_specific_changes_raw": len(r39 ^ r51),
            "void": void or None}


PRE_RC_DECISIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PRE_RC_DECISIONS.md")
_JSON_BLOCK = re.compile(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", re.S | re.M)


def accepted_typed_tuples(path=None) -> dict:
    """{(corpus, engine, caller, target, grade): decision id}: the engine-specific typed tuples a decision recorded
    before RC accepted (PRE_RC_DECISIONS.md, its one ```json block; stable keys as engdiff writes them). Exactly the
    listed tuples — a list, never a rule. An unreadable record accepts nothing."""
    try:
        with open(path or PRE_RC_DECISIONS, encoding="utf-8") as fh:
            m = _JSON_BLOCK.search(fh.read())
        doc = json.loads(m.group(1)) if m else {}
    except (OSError, ValueError):
        return {}
    out = {}
    decisions = doc.get("decisions") if isinstance(doc, dict) else None
    for d in decisions if isinstance(decisions, list) else []:
        tuples = d.get("tuples") if isinstance(d, dict) else None
        for t in tuples if isinstance(tuples, list) else []:
            if isinstance(t, dict) and d.get("id"):
                out[(d.get("corpus"), t.get("only_on"), t.get("caller"), t.get("target"), t.get("grade"))] = d["id"]
    return out


def typed_tuple_verdict(corpus, only39, only51, accepted=None) -> dict:
    """§8.4 E bullet 1 for one corpus: the typed tuples (caller, target, grade — version-stable keys, on the common
    endpoints of amendment A1) one engine has and the other lacks. `holds_as_registered`: none differ (E as
    registered). `holds`: every differing tuple is one a pre-RC decision accepted for this corpus, on exactly that
    engine and with exactly that grade (accepted_typed_tuples); any other differing tuple fails E. Both are
    reported; the accepted ones carry their decision id."""
    acc = accepted_typed_tuples() if accepted is None else accepted
    ok, bad = [], []
    for eng, rows in (("0.8.39", only39), ("0.8.51", only51)):
        for t in rows or []:
            c, tg, g = (list(t) + [None, None, None])[:3]
            row = {"engine": eng, "caller": c, "target": tg, "grade": g}
            did = acc.get((corpus, eng, c, tg, g))
            (ok if did else bad).append(dict(row, decision=did) if did else row)
    return {"holds_as_registered": not ok and not bad, "holds": not bad, "accepted": ok, "unaccepted": bad,
            "decisions": sorted({r["decision"] for r in ok})}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--base39', '--base51', '--list', '--json'))
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    a, b = C.load_json(args[0]), C.load_json(args[1])
    p = parity(a, b)
    lists = ("only_a", "only_b", "only_a_common", "only_b_common", "grade_diff", "nodes_only_a", "nodes_only_b")
    out = {"parity": {k: v for k, v in p.items() if k not in lists}, **{k: p[k] for k in lists}}
    print(f"parity on the edges whose both endpoints exist on both engines ({p['nodes_common']} common nodes; "
          f"nodes only in the first {len(p['nodes_only_a'])}, only in the second {len(p['nodes_only_b'])}): edges "
          f"{p['edges_common_a']} vs {p['edges_common_b']}, symmetric difference {p['symdiff_common']} (only first "
          f"{len(p['only_a_common'])}, only second {len(p['only_b_common'])}), grade disagreements "
          f"{len(p['grade_diff'])}")
    print(f"raw, all edges (reported, not the criterion): edges {p['edges_a']} vs {p['edges_b']}, symmetric "
          f"difference {p['symdiff']} (only first {len(p['only_a'])}, only second {len(p['only_b'])}); "
          f"label-set projection {p['projection_a']} vs {p['projection_b']} (symdiff {p['projection_symdiff']})"
          + (f"; stable-key collisions {p['collisions_a']} / {p['collisions_b']}"
             if p["collisions_a"] or p["collisions_b"] else ""))
    nl = int(opts.get("--list") or 0)
    for x in p["only_a_common"][:nl]:
        print(f"   only first : {x}")
    for x in p["only_b_common"][:nl]:
        print(f"   only second: {x}")
    for x in p["nodes_only_a"][:nl]:
        print(f"   node only in the first : {x}")
    for x in p["nodes_only_b"][:nl]:
        print(f"   node only in the second: {x}")
    void = void_reasons(("the first dump", a), ("the second dump", b))
    rc = 0
    if opts.get("--base39") and opts.get("--base51"):
        cr = criterion(a, b, C.load_json(opts["--base39"]), C.load_json(opts["--base51"]))
        out["criterion"] = cr
        void = cr["void"] or []
        print(f"E (on the edges whose both endpoints exist on both engines, {cr['nodes_common']} nodes): variant "
              f"symdiff {cr['variant_symdiff_common']} <= V0 symdiff {cr['v0_symdiff_common']}: "
              f"{'HOLDS' if cr['holds'] else 'FAILS'}; raw, all edges (reported, not the criterion): "
              f"{cr['variant_symdiff_raw']} vs {cr['v0_symdiff_raw']}; nodes on one engine only: 0.8.39 "
              f"{len(cr['nodes_only_0839'])} / 0.8.51 {len(cr['nodes_only_0851'])}; changes 0.8.39 "
              f"{cr['changes_0839']} / 0.8.51 {cr['changes_0851']}; engine-specific changes "
              f"{len(cr['engine_specific_changes'])} (raw {cr['engine_specific_changes_raw']})")
        for x in cr["engine_specific_changes"][:nl]:
            print(f"   engine-specific: {x}")
        rc = 0 if cr["holds"] else 1
    if void:
        out["void"] = void
        for v in void:
            print(f"VOID (not data): {v}")
        rc = 3
    if opts.get("--json"):
        C.write_json(opts["--json"], out)
    return rc


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
