#!/usr/bin/env python3
"""R layer (design §8.4 R): capture ONE variant's live retrieval on a throwaway instance, in matched state.

capture   in-process DeepMapRetriever + embeddings.embed_query (the code the MCP tools run): search(top_k=15)
          for every benchmark question of --repo, plus impact(limit=30) on the top-3 hits of the transitive,
          multi_hop and runtime_edge questions. TWO passes: pass 1 is scored, pass 2 checks determinism.
          Writes raw_<slug>.json (+ census_<slug>.json: node / embedding / CALLS counts and fingerprints).
roundtrip raw_V0.json raw_V0_back.json — the capture after the edge-only round trip back to V0 must equal V0's
          EXACTLY (search rows and impact rows), else the run is VOID (exit 1).
matched   census_V0.json census_VX.json [--sync-log=<the variant's sync log>] — matched state: identical node
          set and node text (summary fingerprint) and embedded count; the variant's sync re-embedded 0 nodes and
          did not re-create the full-text index. Anything else is VOID (exit 1).

Safety (the operator's DeepMap is never touched): DEEPMAP_HOME must be set and must not be ~/.deepmap;
NEO4J_URI must be unset (the route is the clone's OWN registered instance, port_registry.resolve_neo4j_route);
the base bolt 7687 and the dev pairs (--forbid, default 7687,7689,7476) are refused; the graph's owner stamp
(:_DeepMapMeta owning_root, read with sync._read_owning_root) must name the clone by sync's own predicate
(owner_problem; measure_self.py uses the same check).

usage:
  python run_variant.py capture --variant=<id> --repo=<owner/name> --clone=<root> --out=<dir> [--questions=Q]
  python run_variant.py roundtrip <raw_a.json> <raw_b.json>
  python run_variant.py matched <census_V0.json> <census_VX.json> [--sync-log=<log>]
"""
from __future__ import annotations

import hashlib
import os
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

GRAPH_FAMILIES = ("transitive", "multi_hop", "runtime_edge")
SEARCH_TOP_K = 15
IMPACT_LIMIT = 30
DEFAULT_FORBID = (7687, 7689, 7476)
_SYNC_LINE = re.compile(r"(\d+) nodes, (\d+) CALLS edges, (\d+) embedded this run")
_FTS_LINE = re.compile(r"full-text index re-created")


def run_pass(ret, qs) -> list:
    """One pass over `qs` with a retriever exposing search(q, top_k) and impact(name, limit)."""
    out = []
    for q in qs:
        rec = {"id": q["id"], "family": q["family"], "prompt": q["prompt"]}
        t0 = time.perf_counter()
        try:
            rec["search"] = [dict(r) for r in ret.search(q["prompt"], top_k=SEARCH_TOP_K)]
            rec["search_error"] = None
        except Exception as e:  # noqa: BLE001
            rec["search"], rec["search_error"] = [], f"{type(e).__name__}: {e}"
        for r in rec["search"]:
            r.pop("embedding", None)
        rec["search_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        rec["impact"] = {}
        if q["family"] in GRAPH_FAMILIES:
            for hit in rec["search"][:3]:
                name = (hit.get("name") or "").strip()
                if not name or name in rec["impact"]:
                    continue
                t1 = time.perf_counter()
                try:
                    rows = ret.impact(name, limit=IMPACT_LIMIT)
                    notes = list(getattr(rows, "notes", None) or [])
                    rows, err = [dict(r) for r in rows], None
                except Exception as e:  # noqa: BLE001
                    rows, notes, err = [], [], f"{type(e).__name__}: {e}"
                rec["impact"][name] = {"rows": rows, "notes": notes, "error": err,
                                       "ms": round((time.perf_counter() - t1) * 1000, 1)}
        out.append(rec)
    return out


def _search_sig(rec):
    return [(r.get("name"), C.norm_path(r.get("file")), r.get("line"), r.get("type")) for r in rec.get("search") or []]


def _impact_sig(rec):
    return {k: [tuple(sorted((kk, str(vv)) for kk, vv in r.items())) for r in (v.get("rows") or [])]
            for k, v in sorted((rec.get("impact") or {}).items())}


def roundtrip(raw_a, raw_b) -> dict:
    """Exact equality of two captures' search and impact results (ids/scores/latencies ignored)."""
    a = {q["id"]: q for q in raw_a.get("questions") or []}
    b = {q["id"]: q for q in raw_b.get("questions") or []}
    diffs = []
    for qid in sorted(set(a) | set(b)):
        if qid not in a or qid not in b:
            diffs.append({"question": qid, "what": "missing"})
            continue
        if _search_sig(a[qid]) != _search_sig(b[qid]):
            diffs.append({"question": qid, "what": "search"})
        if _impact_sig(a[qid]) != _impact_sig(b[qid]):
            diffs.append({"question": qid, "what": "impact"})
    return {"exact": not diffs, "diffs": diffs}


def check_sync_log(text: str) -> dict:
    m = _SYNC_LINE.findall(text or "")
    re_embedded = int(m[-1][2]) if m else None
    fts = bool(_FTS_LINE.search(text or ""))
    return {"re_embedded": re_embedded, "fts_recreated": fts,
            "void": re_embedded is None or re_embedded != 0 or fts}


def matched(census_a, census_b, sync_log=None) -> dict:
    keys = ("nodes", "embedded", "summary_fp", "node_keys_fp")
    problems = [k for k in keys if census_a.get(k) != census_b.get(k)]
    out = {"problems": problems}
    if sync_log is not None:
        out["sync_log"] = check_sync_log(sync_log)
        if out["sync_log"]["void"]:
            problems.append("sync-log: nodes re-embedded or the full-text index re-created (or no sync line)")
    out["matched"] = not problems
    return out


def safety_problems(clone: Path, route, forbid=DEFAULT_FORBID, environ=None) -> list:
    e = os.environ if environ is None else environ
    probs = []
    home = (e.get("DEEPMAP_HOME") or "").strip()
    if not home:
        probs.append("DEEPMAP_HOME is unset — point it at a throwaway home (never ~/.deepmap)")
    elif Path(home).resolve() == (Path.home() / ".deepmap").resolve():
        probs.append("DEEPMAP_HOME is the operator's ~/.deepmap — use a throwaway home")
    elif len(home) > 140:
        probs.append(f"DEEPMAP_HOME is {len(home)} characters (design §8.4 R: at most 140)")
    if e.get("NEO4J_URI"):
        probs.append("NEO4J_URI is set — unset it: the capture must route through the clone's own instance")
    src = getattr(route, "source", None)
    if src not in ("registry", "instance-conf"):
        probs.append(f"the clone has no Neo4j instance of its own (route source {src!r}: {getattr(route, 'problem', None)})")
    bolt = getattr(route, "bolt", None)
    if bolt is not None and int(bolt) in set(forbid):
        probs.append(f"bolt {bolt} is forbidden (the base store / the dev pair)")
    return probs


def census(driver) -> dict:
    out = {}
    with driver.session() as s:
        out["nodes_by_label"] = {r["l"]: r["c"] for r in s.run(
            "MATCH (n) WHERE n.key IS NOT NULL RETURN labels(n)[0] AS l, count(*) AS c ORDER BY l")}
        out["nodes"] = sum(out["nodes_by_label"].values())
        out["embedded"] = s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()["c"]
        out["calls_by_conf"] = {str(r["k"]): r["c"] for r in s.run(
            "MATCH ()-[r:CALLS]->() RETURN r.confidence AS k, count(r) AS c ORDER BY k")}
        out["calls"] = sum(out["calls_by_conf"].values())
        h1, h2 = hashlib.sha1(), hashlib.sha1()
        for r in s.run("MATCH (a)-[r:CALLS]->(b) RETURN a.key AS a, b.key AS b, r.confidence AS c ORDER BY a, b, c"):
            h1.update(f"{r['a']}->{r['b']}\n".encode())
            h2.update(f"{r['a']}->{r['b']}:{r['c']}\n".encode())
        out["calls_fp"], out["calls_graded_fp"] = h1.hexdigest()[:16], h2.hexdigest()[:16]
        hk, hs = hashlib.sha1(), hashlib.sha1()
        for r in s.run("MATCH (n) WHERE n.key IS NOT NULL RETURN n.key AS k, coalesce(n.body_summary,'') AS s, "
                       "coalesce(n.name,'') AS nm ORDER BY k"):
            hk.update(f"{r['k']}\n".encode())
            hs.update(f"{r['k']}\t{r['nm']}\t{r['s']}\n".encode())
        out["node_keys_fp"], out["summary_fp"] = hk.hexdigest()[:16], hs.hexdigest()[:16]
    # The owner stamp through sync's own reader (`m.owning_root`, the property sync writes) — never a copy of
    # its query, which once read a property nothing writes and refused every capture.
    out["owner"] = _sync()._read_owning_root(driver)
    return out


def _sync():
    if str(C.BUILD) not in sys.path:
        sys.path.insert(0, str(C.BUILD))
    import sync  # noqa: WPS433 — the product's owner-stamp reader and owner predicate, never a copy
    return sync


def owner_problem(clone: Path, owner) -> "str | None":
    """None when the graph's owner stamp (census()["owner"]) names the clone's project — sync's own predicate
    (`_same_project` over `_project_identity`, the rule every consumer uses) — else why the capture is refused.
    A missing stamp is refused: it is no proof the graph is the clone's."""
    SY = _sync()
    if not owner:
        return f"the graph carries no owner stamp (:_DeepMapMeta owning_root) — it is not proven to be {clone}'s"
    if not SY._same_project(SY._project_identity(Path(clone)), str(owner)):
        return f"the graph's owner {owner!r} is not {clone}"
    return None


def cmd_capture(opts) -> int:  # pragma: no cover — live (S6)
    sys.path.insert(0, str(C.BUILD))
    import port_registry as PR
    import yaml
    from neo4j import GraphDatabase
    clone = Path(opts["--clone"]).resolve()
    route = PR.resolve_neo4j_route(clone, honor_env=False)
    forbid = tuple(int(x) for x in (opts.get("--forbid") or ",".join(map(str, DEFAULT_FORBID))).split(",") if x)
    probs = safety_problems(clone, route, forbid)
    if probs:
        print("[run_variant] REFUSED:\n  - " + "\n  - ".join(probs), file=sys.stderr)
        return 2
    import variants as V
    vid = V.resolve(opts["--variant"])
    qpath = opts.get("--questions") or str(C.REPO / "benchmark" / "questions.yaml")
    qs = [q for q in yaml.safe_load(open(qpath, encoding="utf-8"))["questions"] if q["repo"] == opts["--repo"]]
    drv = GraphDatabase.driver(route.uri, auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "deepmap")),
                               notifications_min_severity="OFF")
    try:
        drv.verify_connectivity()
        cen = census(drv)
        why = owner_problem(clone, cen.get("owner"))
        if why:
            print(f"[run_variant] REFUSED: {why}", file=sys.stderr)
            return 2
        import embeddings
        import retrieval
        embeddings.embed_query("warm-up")
        ret = retrieval.DeepMapRetriever(drv, embed_fn=embeddings.embed_query)
        ret.search("warm up the retriever", top_k=5)
        p1 = run_pass(ret, qs)
        p2 = run_pass(ret, qs)
    finally:
        drv.close()
    det = roundtrip({"questions": p1}, {"questions": p2})
    out = Path(opts["--out"])
    raw = {"schema": "call-linking/capture/1", "repo": opts["--repo"], "variant": vid, "clone": C.norm_path(clone),
           "flags": C.env_flags(), "questions": p1, "atoms": {q["id"]: q["answer_key"]["ground_truth_set"] for q in qs},
           "deterministic": det["exact"], "pass2_diffs": det["diffs"],
           "latency_ms": {"search_median_pass2": statistics.median([r["search_ms"] for r in p2]) if p2 else None}}
    C.write_json(out / f"raw_{V.slug(vid)}.json", raw)
    C.write_json(out / f"census_{V.slug(vid)}.json", dict(cen, flags=C.env_flags()))
    print(f"[run_variant] {vid}: {len(p1)} questions, deterministic={det['exact']}, CALLS={cen['calls']} "
          f"{cen['calls_by_conf']}, nodes={cen['nodes']} embedded={cen['embedded']}")
    return 0 if det["exact"] else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--variant', '--repo', '--clone', '--out', '--questions', '--forbid', '--sync-log'))
    if args[:1] == ["capture"] and all(opts.get(k) for k in ("--variant", "--repo", "--clone", "--out")):
        return cmd_capture(opts)
    if args[:1] == ["roundtrip"] and len(args) == 3:
        r = roundtrip(C.load_json(args[1]), C.load_json(args[2]))
        print(f"round trip {'EXACT' if r['exact'] else 'VOID'}: {len(r['diffs'])} difference(s) {r['diffs'][:10]}")
        return 0 if r["exact"] else 1
    if args[:1] == ["matched"] and len(args) == 3:
        log = Path(opts["--sync-log"]).read_text(encoding="utf-8", errors="replace") if opts.get("--sync-log") else None
        r = matched(C.load_json(args[1]), C.load_json(args[2]), log)
        print(f"matched state {'OK' if r['matched'] else 'VOID'}: {r}")
        return 0 if r["matched"] else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
