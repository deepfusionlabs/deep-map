#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/retrieval_log.py — the RM-1 retrieval-logging
foundation (opt-in, zero-egress). LIVE RANKING IS NOT TOUCHED: retrieval.py stays frozen;
this module only *observes*.

Locks:
  - opt-in: logging is OFF by default; log_retrieval() is a no-op unless enabled,
  - zero-egress confinement: the log lives ONLY under <root>/.deepmap/, never elsewhere,
  - robustness: append/read NEVER raise into the caller (a logging failure must never
    break a search),
  - schema: build_record produces the documented record shape (per_arm_candidate_ids,
    returned entries with id/file/name/line, empty accessed_ids),
  - read_records tolerates malformed / blank lines (skips them),
  - THE KEYSTONE: capture_search reproduces retriever.search()'s returned ids EXACTLY,
    and the logged per_arm_candidate_ids, re-fused with the REAL rank-based _weighted_rrf
    at equal weights, reproduce that same top-k order (so the log is a faithful,
    fusion-lossless record the offline tuner can replay).

Pure + hermetic: a temp dir stands in for a project root; a FakeRetriever (canned arms,
no driver) exercises the real DeepMapRetriever.search / _weighted_rrf. No Neo4j, no spend.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ---- a canned, driverless retriever exercising the REAL search()/_weighted_rrf ----
def _fake_retriever(R):
    class FakeRetriever(R.DeepMapRetriever):
        # Canned per-arm results (id, score). Overlap across arms so fusion is non-trivial.
        _VEC = [("A", 0.9), ("B", 0.8), ("C", 0.7)]
        _FTS = [("B", 0.9), ("D", 0.85), ("A", 0.5)]
        _GRAPH = [("A", 0.95), ("C", 0.6)]

        def __init__(self):
            self.arm_weights = dict(R.DEFAULT_ARM_WEIGHTS)
            self.embed_fn = lambda q: [0.0] * 768
            self._use_search_clause = True

        def _embed_or_none(self, query):
            return [0.0] * 768

        @staticmethod
        def _mk(rows):
            return [{"id": i, "name": i, "type": "Function",
                     "file": f"build/{i.lower()}.py", "line": 10, "score": s} for i, s in rows]

        def _arm_vector(self, qvec, k):
            return self._mk(self._VEC)[:k]

        def _arm_fulltext(self, query, k):
            return self._mk(self._FTS)[:k]

        def _arm_graph_weighted(self, qvec, k):
            return self._mk(self._GRAPH)[:k]

        def _hydrate(self, top_ids, scores):
            return [{"id": i, "name": i, "type": "Function",
                     "file": f"build/{i.lower()}.py", "line": 10,
                     "score": scores.get(i, 0.0)} for i in top_ids]

    return FakeRetriever()


def gate_optin_default_off(L) -> None:
    print("\n-- opt-in: logging OFF by default; enabled only via env / instance flag --")
    # Scrub any ambient enable.
    saved = os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            check("logging_enabled() False with no env / no config", L.logging_enabled(root) is False)
            # log_retrieval is a NO-OP (returns None, writes nothing) when disabled.
            out = L.log_retrieval(root, "q", {"vector": ["A"]}, [{"id": "A", "file": "x.py", "name": "A", "line": 1}])
            check("log_retrieval() disabled -> None returned", out is None)
            check("log_retrieval() disabled -> no log file written",
                  not L.log_path(root).exists())
            # Env opt-in flips it on.
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = "1"
            check("logging_enabled() True with DEEPMAP_RETRIEVAL_LOG=1", L.logging_enabled(root) is True)
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = "0"
            check("logging_enabled() False with DEEPMAP_RETRIEVAL_LOG=0", L.logging_enabled(root) is False)
    finally:
        os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
        if saved is not None:
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = saved


def gate_zero_egress_and_write(L) -> None:
    print("\n-- zero-egress: the log lives ONLY under <root>/.deepmap/, append never raises --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = L.log_path(root)
        check("log_path is <root>/.deepmap/retrieval_log.jsonl",
              lp == root / ".deepmap" / "retrieval_log.jsonl", str(lp))
        check("log_path resolves UNDER <root>/.deepmap (no escape)",
              str(lp.resolve()).startswith(str((root / ".deepmap").resolve())))
        rec = L.build_record("find the auth check", {"vector": ["A", "B"], "bm25": ["B"], "graph_weighted": ["A"]},
                             [{"id": "A", "file": "build/a.py", "name": "A", "line": 10}],
                             project="deepmap", top_k=15, ts="2026-07-01T00:00:00Z")
        p = L.append_record(root, rec)
        check("append_record returns the log path", p is not None and Path(p) == lp)
        check("append_record wrote under .deepmap", lp.is_file())
        # A second append -> two JSONL lines (append-only).
        L.append_record(root, L.build_record("q2", {"vector": ["C"]}, [{"id": "C", "file": "c.py", "name": "C", "line": 1}], ts="2026-07-01T00:00:01Z"))
        lines = [ln for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("append-only: two records after two appends", len(lines) == 2, str(len(lines)))
        # append_record NEVER raises even on a hopeless root (returns None).
        raised = False
        try:
            bad = L.append_record("\x00::not a path::", rec)
        except Exception:
            raised = True
            bad = "raised"
        check("append_record never raises on a bad root (returns None)", (not raised) and bad is None, str(bad))


def gate_record_schema(L) -> None:
    print("\n-- build_record schema (per_arm_candidate_ids, returned entries, empty accessed_ids) --")
    rec = L.build_record("q", {"vector": ["A", "B"], "bm25": ["B", "D"], "graph_weighted": ["A"]},
                         [{"id": "A", "file": "build/a.py", "name": "alpha", "line": 12},
                          {"id": "B", "file": "build/b.py", "name": "beta", "line": 3}],
                         project="deepmap", top_k=15, ts="2026-07-01T00:00:00Z")
    check("type == 'search'", rec.get("type") == "search")
    check("carries a stable qid", isinstance(rec.get("qid"), str) and len(rec["qid"]) >= 8)
    check("per_arm_candidate_ids keyed by the 3 arm names",
          set(rec.get("per_arm_candidate_ids", {})) == {"vector", "bm25", "graph_weighted"})
    check("returned entries carry id/file/name/line",
          all({"id", "file", "name", "line"} <= set(e) for e in rec.get("returned", [])))
    check("accessed_ids starts empty", rec.get("accessed_ids") == [])
    check("records project + top_k + ts", rec.get("project") == "deepmap" and rec.get("top_k") == 15
          and rec.get("ts") == "2026-07-01T00:00:00Z")
    # qid is deterministic for identical inputs, distinct for a different query.
    rec2 = L.build_record("q", {"vector": ["A", "B"], "bm25": ["B", "D"], "graph_weighted": ["A"]},
                          [{"id": "A", "file": "build/a.py", "name": "alpha", "line": 12},
                           {"id": "B", "file": "build/b.py", "name": "beta", "line": 3}],
                          project="deepmap", top_k=15, ts="2026-07-01T00:00:00Z")
    rec3 = L.build_record("other", {"vector": ["A"]}, [{"id": "A", "file": "a", "name": "a", "line": 1}], ts="2026-07-01T00:00:00Z")
    check("qid deterministic for identical inputs", rec["qid"] == rec2["qid"])
    check("qid differs for a different query", rec["qid"] != rec3["qid"])


def gate_read_records_robust(L) -> None:
    print("\n-- read_records tolerates malformed / blank lines --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("read_records([]) when absent", L.read_records(root) == [])
        lp = L.log_path(root)
        lp.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps(L.build_record("q", {"vector": ["A"]}, [{"id": "A", "file": "a", "name": "A", "line": 1}], ts="t"))
        lp.write_text(good + "\n\n" + "{not json}\n" + good + "\n", encoding="utf-8")
        recs = L.read_records(root)
        check("skips blank + malformed, keeps the 2 good records", len(recs) == 2, str(len(recs)))
    # read_records(None) must not raise (log_path(None) guarded).
    raised = False
    try:
        out = L.read_records(None)
    except Exception:
        raised = True
        out = "raised"
    check("read_records(None) -> [] (never raises)", (not raised) and out == [], str(out))


def gate_capture_search_keystone(L, R) -> None:
    print("\n-- KEYSTONE: capture_search == search(); per_arm re-fused (equal wts) reproduces top-k --")
    r = _fake_retriever(R)
    results, per_arm = L.capture_search(r, "q", top_k=15)
    search_ids = [x["id"] for x in r.search("q", top_k=15)]
    cap_ids = [x["id"] for x in results]
    check("capture_search returned ids EXACTLY equal search() ids",
          cap_ids == search_ids, f"{cap_ids} vs {search_ids}")
    check("per_arm keyed by the 3 arms", set(per_arm) == {"vector", "bm25", "graph_weighted"})
    check("per_arm 'vector' ranked by score desc", per_arm["vector"] == ["A", "B", "C"], str(per_arm["vector"]))
    check("per_arm 'bm25' ranked by score desc", per_arm["bm25"] == ["B", "D", "A"], str(per_arm["bm25"]))
    # Re-fuse the LOGGED per_arm ordered ids (rank-only) with the REAL _weighted_rrf, equal weights.
    runs = [(arm, {rid: float(-i) for i, rid in enumerate(ids)}) for arm, ids in per_arm.items() if ids]
    fused = R._weighted_rrf(runs, R.DEFAULT_ARM_WEIGHTS)
    refused_top = [i for i, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)][:15]
    check("logged per_arm re-fused (equal wts) reproduces search() top-k order EXACTLY",
          refused_top == search_ids, f"{refused_top} vs {search_ids}")


def gate_fold_access_events(L) -> None:
    print("\n-- fold_access_events: correlate a Read/access to the most-recent search that surfaced it --")
    if not hasattr(L, "fold_access_events"):
        check("fold_access_events exists", False, "missing")
        return
    s1 = L.build_record("auth", {"vector": ["A"]},
                        [{"id": "A", "file": "build/auth.py", "name": "check_auth", "line": 5},
                         {"id": "B", "file": "build/session.py", "name": "Session", "line": 9}], ts="1")
    acc = {"type": "access", "ts": "2", "paths": ["build/auth.py"], "symbols": []}
    s2 = L.build_record("later", {"vector": ["C"]},
                        [{"id": "C", "file": "build/other.py", "name": "other", "line": 1}], ts="3")
    folded = L.fold_access_events([s1, acc, s2])
    by_qid = {r["qid"]: r for r in folded}
    check("2 search records survive folding", len(folded) == 2, str(len(folded)))
    check("the accessed file's id ('A') is credited to search #1",
          "A" in by_qid[s1["qid"]]["accessed_ids"], str(by_qid[s1["qid"]]["accessed_ids"]))
    check("the un-accessed id ('B') is NOT credited",
          "B" not in by_qid[s1["qid"]]["accessed_ids"])
    check("search #2 (no access) keeps empty accessed_ids",
          by_qid[s2["qid"]]["accessed_ids"] == [])
    # A symbol-based access also correlates.
    acc2 = {"type": "access", "ts": "4", "paths": [], "symbols": ["other"]}
    folded2 = L.fold_access_events([s2, acc2])
    check("symbol access credits the matching returned id ('C')",
          "C" in {r["qid"]: r for r in folded2}[s2["qid"]]["accessed_ids"])
    # BOUNDARY: an access of 'a.py' must NOT falsely credit a returned 'src/xa.py' (bare-suffix bug).
    s3 = L.build_record("q3", {"vector": ["Z"]},
                        [{"id": "Z", "file": "src/xa.py", "name": "xa", "line": 1}], ts="5")
    acc3 = {"type": "access", "ts": "6", "paths": ["a.py"], "symbols": []}
    folded3 = L.fold_access_events([s3, acc3])
    check("no false path match: 'a.py' does NOT credit returned 'src/xa.py'",
          folded3[0]["accessed_ids"] == [], str(folded3[0]["accessed_ids"]))
    # But a real path-boundary suffix DOES match: access 'src/xa.py' (or '/repo/src/xa.py') credits Z.
    acc3b = {"type": "access", "ts": "7", "paths": ["/repo/src/xa.py"], "symbols": []}
    folded3b = L.fold_access_events([L.build_record("q3", {"vector": ["Z"]},
                        [{"id": "Z", "file": "src/xa.py", "name": "xa", "line": 1}], ts="5"), acc3b])
    check("boundary suffix match: '/repo/src/xa.py' credits returned 'src/xa.py'",
          "Z" in folded3b[0]["accessed_ids"], str(folded3b[0]["accessed_ids"]))


def gate_maybe_log_search(L, R) -> None:
    print("\n-- maybe_log_search: opt-in live call site (no-op when disabled; logs when enabled) --")
    r = _fake_retriever(R)
    results = r.search("q", top_k=15)
    saved = os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Disabled (default) -> no-op, no log.
            out = L.maybe_log_search(r, "q", results, root=root, top_k=15)
            check("disabled: returns None, writes nothing", out is None and not L.log_path(root).exists())
            # Enabled -> logs one search record with returned ids == results + per_arm populated.
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = "1"
            out = L.maybe_log_search(r, "q", results, root=root, top_k=15)
            check("enabled: returns the log path", out is not None and Path(out) == L.log_path(root))
            recs = [x for x in L.read_records(root) if x.get("type") == "search"]
            check("enabled: exactly one search record", len(recs) == 1, str(len(recs)))
            if recs:
                rec = recs[0]
                check("record's returned ids == the live results ids",
                      [e["id"] for e in rec["returned"]] == [x["id"] for x in results],
                      str(rec["returned"]))
                check("record's per_arm is populated (all 3 arms)",
                      all(rec["per_arm_candidate_ids"].get(a) for a in ("vector", "bm25", "graph_weighted")),
                      str(rec["per_arm_candidate_ids"]))
    finally:
        os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
        if saved is not None:
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = saved
    # default_root honors DEEPMAP_PROJECT_ROOT.
    with tempfile.TemporaryDirectory() as td:
        os.environ["DEEPMAP_PROJECT_ROOT"] = td
        try:
            check("default_root honors DEEPMAP_PROJECT_ROOT", str(L.default_root()) == td)
        finally:
            os.environ.pop("DEEPMAP_PROJECT_ROOT", None)
    # never raises even if an arm blows up under logging.
    class Boom(type(r)):
        def _arm_fulltext(self, q, k):
            raise RuntimeError("boom")
    b = Boom()
    os.environ["DEEPMAP_RETRIEVAL_LOG"] = "1"
    try:
        with tempfile.TemporaryDirectory() as td:
            raised = False
            try:
                L.maybe_log_search(b, "q", results, root=Path(td), top_k=15)
            except Exception:
                raised = True
            check("never raises even if an arm throws under logging", not raised)
    finally:
        os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)


def gate_call_site_wired() -> None:
    print("\n-- the live search path is WIRED to the opt-in logger (CLI + MCP server) --")
    cli = (REPO / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
    srv = (REPO / "build" / "deepmap_search_server.py").read_text(encoding="utf-8")
    check("deepmap_cli.cmd_search references maybe_log_search", "maybe_log_search" in cli, "not wired")
    check("deepmap_search_server references maybe_log_search", "maybe_log_search" in srv, "not wired")


def gate_arm_names_match(L, R) -> None:
    # ISS-build:006 — retrieval_log.ARM_NAMES is coupled to retrieval.DEFAULT_ARM_WEIGHTS BY COMMENT
    # only; nothing enforced it. retrieval.DEFAULT_ARM_WEIGHTS is the source of truth for arm names,
    # so if an arm is ever renamed/added there, the log's per-arm capture would silently mis-key. THIS
    # gate is that enforcement: it fails RED the instant the two arm-name sets diverge. (The guard
    # lives here in CI, NOT as a module-load assert in retrieval_log.py: a raising import would violate
    # that module's "NEVER raises into the caller" pure-sink invariant — the PostToolUse access hook
    # imports it on every tool call — and coupling a pure sink to the retriever at load time is exactly
    # the rot we are avoiding.)
    print("\n-- guard: retrieval_log.ARM_NAMES == retrieval.DEFAULT_ARM_WEIGHTS keys (drift-lock) --")
    log_arms = set(L.ARM_NAMES)
    weight_arms = set(R.DEFAULT_ARM_WEIGHTS)
    check("both arm-name sets are non-empty (the compare is real, not vacuously True)",
          bool(log_arms) and bool(weight_arms), f"{sorted(log_arms)} / {sorted(weight_arms)}")
    check("set(retrieval_log.ARM_NAMES) == set(retrieval.DEFAULT_ARM_WEIGHTS)",
          log_arms == weight_arms, f"{sorted(log_arms)} vs {sorted(weight_arms)}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — retrieval_log gates (RM-1 logging foundation; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    R = _load("retrieval", BUILD)

    gate_optin_default_off(L)
    gate_zero_egress_and_write(L)
    gate_record_schema(L)
    gate_read_records_robust(L)
    gate_capture_search_keystone(L, R)
    gate_maybe_log_search(L, R)
    gate_call_site_wired()
    gate_fold_access_events(L)
    gate_arm_names_match(L, R)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
