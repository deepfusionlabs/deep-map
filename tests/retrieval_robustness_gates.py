#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for retrieval robustness (T10) — instance-down must fail LOUD, not fake-empty.

build/retrieval._is_fatal_db_error decides, per per-index error, whether to fail loud (the
instance is unreachable / auth / transient — re-raise) or to skip benignly (a missing/absent
index). This locks that classification so a future change can't reintroduce the silent
"0 results" that hides a down Neo4j. Pure: constructs neo4j exception objects, no live DB.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"


def _load_retrieval():
    spec = importlib.util.spec_from_file_location("dm_retrieval", BUILD / "retrieval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


class _FakeResult(list):
    """A neo4j-Result stand-in: an iterable of mapping-like rows."""


class _FakeSession:
    def __init__(self, captured, rows):
        self.captured, self.rows = captured, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.captured["cypher"] = cypher
        self.captured["params"] = params
        return _FakeResult(self.rows)


class _FakeDriver:
    def __init__(self, captured, rows):
        self.captured, self.rows = captured, rows

    def session(self):
        return _FakeSession(self.captured, self.rows)


def gate_query_callers(R) -> None:
    """Pure contract test for query_callers (the impact path): it must pass the symbol/limit
    through, map every row to a dict, and emit the bare-name OR suffix match + trust-first
    ordering + LIMIT in its Cypher. No Neo4j — a fake driver returns canned rows."""
    print("\n-- query_callers contract (impact path) --")
    rows = [
        {"caller": "a", "caller_type": "Function", "file": "x.py", "line": 1,
         "confidence": "INFERRED", "callee": "foo"},
        {"caller": "b", "caller_type": "Method", "file": "y.py", "line": 2,
         "confidence": "AMBIGUOUS", "callee": "foo"},
    ]
    captured: dict = {}
    drv = _FakeDriver(captured, rows)
    out = R.query_callers(drv, "foo", limit=7)
    check("passes the symbol as $name", captured["params"].get("name") == "foo", str(captured.get("params")))
    check("passes the limit as $limit", captured["params"].get("limit") == 7, str(captured.get("params")))
    check("returns one dict per row", isinstance(out, list) and len(out) == 2 and all(isinstance(r, dict) for r in out))
    check("preserves row fields", out[0]["caller"] == "a" and out[1]["confidence"] == "AMBIGUOUS", str(out))
    cy = captured["cypher"]
    check("matches a bare name OR a .suffix", "callee.name = $name" in cy and "ENDS WITH '.' + $name" in cy, cy)
    check("orders trust-first (EXTRACTED<INFERRED<AMBIGUOUS)",
          "WHEN 'EXTRACTED' THEN 0" in cy and "WHEN 'INFERRED' THEN 1" in cy, cy)
    check("applies LIMIT $limit", "LIMIT $limit" in cy, cy)
    # bug-hunt #18: the documented '.foo' suffix form must strip the leading dot so it binds the same
    # as the bare name 'foo' (previously '.foo' matched nothing and returned zero callers).
    cap_dot: dict = {}
    R.query_callers(_FakeDriver(cap_dot, []), ".foo", limit=5)
    check("documented '.foo' form strips the leading dot to bind $name='foo' (#18)",
          cap_dot.get("params", {}).get("name") == "foo", str(cap_dot.get("params")))
    # impact() must delegate to query_callers with the same args.
    cap2: dict = {}
    ret = R.DeepMapRetriever(_FakeDriver(cap2, []), embed_fn=lambda q: [0.0] * 768)
    ret.impact("bar", limit=3)
    check("DeepMapRetriever.impact delegates to query_callers",
          cap2.get("params", {}).get("name") == "bar" and cap2.get("params", {}).get("limit") == 3,
          str(cap2.get("params")))


def gate_weighted_rrf_keying(R) -> None:
    """Weights are keyed by the SURVIVING arm NAME, not position: with a SUBSET of arms (vector
    absent) in non-default order and NON-UNIFORM weights, each arm's contribution must use ITS
    name's weight (a positional bug would slip if only tested with all arms present). Also: an empty
    arm is identical to omitting it. Pure math on _weighted_rrf — no Neo4j."""
    print("\n-- weighted RRF: name-keyed weights + empty-arm isolation --")
    k = R.RRF_K
    fused = R._weighted_rrf([("bm25", {"A": 0.9}), ("graph_weighted", {"A": 0.8})],
                            {"vector": 0.0, "bm25": 1.0, "graph_weighted": 2.0})
    want = 1.0 * (1.0 / (k + 1)) + 2.0 * (1.0 / (k + 1))   # both rank-1 for doc A
    check("subset arms: weight applied by NAME not position", abs(fused["A"] - want) < 1e-9,
          f"got {fused.get('A')}, want {want}")
    f1 = R._weighted_rrf([("graph_weighted", {"X": 0.5})], {"graph_weighted": 1.0})
    f2 = R._weighted_rrf([("graph_weighted", {"X": 0.5})], {"graph_weighted": 2.0})
    check("a 2x arm weight doubles that arm's contribution", abs(f2["X"] - 2 * f1["X"]) < 1e-9,
          f"{f1} {f2}")
    with_empty = R._weighted_rrf([("vector", {}), ("bm25", {"A": 0.9})], {"vector": 1.0, "bm25": 1.0})
    without = R._weighted_rrf([("bm25", {"A": 0.9})], {"vector": 1.0, "bm25": 1.0})
    check("an empty arm is identical to omitting it", with_empty == without, f"{with_empty} vs {without}")


def gate_fts_query_safety(R) -> None:
    """A code-shaped query (parens, colons, carets, wildcards) must NOT crash search. The FTS builder
    Lucene-escapes each token; a CLEAN token is emitted byte-identically (so the no-regression oracle
    is preserved); and a query-PARSE error DEGRADES the arm (returns []) instead of failing loud
    (bug-hunt #1 / HARD-2). Pure — no Neo4j."""
    print("\n-- FTS query safety: Lucene escaping + parse-error degrade --")
    old = lambda q: " ".join(f"{t}~2" for t in q.split() if len(t) > 2)
    clean = "compute reciprocal rank fusion across arms"
    check("clean query byte-identical to the old builder (oracle preserved)",
          R._build_fts_query(clean) == old(clean), R._build_fts_query(clean))
    esc = R._build_fts_query("how does foo(bar) work")
    check("Lucene specials escaped (no raw parens)",
          "\\(" in esc and "(" not in esc.replace("\\(", "").replace("\\)", ""), esc)
    check("an all-punctuation token is dropped", R._build_fts_query("(){} :: ;;") == "",
          repr(R._build_fts_query("(){} :: ;;")))
    check("short (<=2) tokens still dropped", R._build_fts_query("ab abc") == "abc~2",
          R._build_fts_query("ab abc"))

    from neo4j.exceptions import ClientError
    pe = ClientError("Failed to invoke procedure: org.apache.lucene.queryparser.classic.ParseException")
    pe._neo4j_code = "Neo.ClientError.Procedure.ProcedureCallFailed"
    check("_is_fts_parse_error: a Lucene parse error is detected", R._is_fts_parse_error(pe))
    syn = ClientError("syntax"); syn._neo4j_code = "Neo.ClientError.Statement.SyntaxError"
    check("_is_fts_parse_error: a non-procedure ClientError is NOT a parse error",
          not R._is_fts_parse_error(syn))
    check("_is_fts_parse_error: a non-ClientError is not a parse error",
          not R._is_fts_parse_error(RuntimeError("x")))
    # NOTE: _is_fatal_db_error treats a MISSING-INDEX ProcedureCallFailed ("no such ... schema index")
    # as benign — the Neo4j-5.26 procedure-fallback path raises that (not an IndexNotFound code), and a
    # single missing/not-yet-ONLINE per-label index must not kill the whole arm (FD-1; bug-hunt #11/#12).
    # A Lucene PARSE ProcedureCallFailed is still handled by _arm_fulltext's _is_fts_parse_error FIRST
    # check; a CONNECTIVITY ProcedureCallFailed (no missing-index/parse signature) still fails loud (T10) —
    # all three verified in the classifier section of main().

    class _ParseSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **k):
            raise pe

    class _ParseDriver:
        def session(self):
            return _ParseSession()

    ret = R.DeepMapRetriever(_ParseDriver(), embed_fn=lambda q: [0.0] * 768)
    out = ret._arm_fulltext("foo(bar) ::config", 5)
    check("_arm_fulltext DEGRADES (returns []) on a parse error, never raises", out == [], str(out))


def gate_fail_loud_search(R) -> None:
    """End-to-end: a down instance must make search() RAISE, never return a fabricated []. The
    source grep can't prove the re-raise actually FIRES; this drives a real ServiceUnavailable
    through search() and asserts it propagates. No Neo4j (a fake driver raises)."""
    print("\n-- fail-loud end-to-end: search() raises on instance-down --")
    from neo4j.exceptions import ServiceUnavailable

    class _DownSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **k):
            raise ServiceUnavailable("instance down")

    class _DownDriver:
        def session(self):
            return _DownSession()

    ret = R.DeepMapRetriever(_DownDriver(), embed_fn=lambda q: [0.0] * 768)
    raised = False
    try:
        ret.search("anything", top_k=5)
    except Exception:
        raised = True   # any propagation is correct; the violation would be a silent []
    check("search() RAISES on a down instance (never a fabricated empty result)", raised,
          "search returned instead of raising — fail-loud (T10) VIOLATED")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — retrieval robustness gates (T10; no Neo4j)")
    print("=" * 64)

    R = _load_retrieval()
    fatal = R._is_fatal_db_error
    from neo4j.exceptions import (ServiceUnavailable, SessionExpired, TransientError,
                                  AuthError, ClientError, DatabaseError)

    print("\n-- FATAL (instance unusable -> fail loud) --")
    check("ServiceUnavailable is fatal", fatal(ServiceUnavailable("down")))
    check("SessionExpired is fatal", fatal(SessionExpired("expired")))
    check("TransientError is fatal", fatal(TransientError("transient")))
    check("AuthError is fatal", fatal(AuthError("bad creds")))
    check("DatabaseError (server-side OOM/execution failure) is fatal", fatal(DatabaseError("oom")))

    def _client_error(code: str) -> ClientError:
        e = ClientError("err")
        e._neo4j_code = code   # .code reads from _neo4j_code in this driver version
        return e

    check("unexpected ClientError (syntax) is fatal",
          fatal(_client_error("Neo.ClientError.Statement.SyntaxError")))

    check("connection-failure-looking generic error is fatal",
          fatal(RuntimeError("Unable to connect to localhost:7687")))
    check("a bare OSError (socket/timeout) is fatal", fatal(OSError("timed out")))
    check("message 'database is unavailable' is fatal (tightened seam)",
          fatal(RuntimeError("the database is unavailable")))

    print("\n-- BENIGN (skip that index, keep the arm — FD-1 isolation) --")
    for code in ("Neo.ClientError.Schema.IndexNotFound",
                 "Neo.ClientError.Procedure.ProcedureNotFound"):
        check(f"ClientError {code.split('.')[-1]} is benign",
              not fatal(_client_error(code)), code)
    check("a generic per-index error (no connectivity signal) is benign — preserves isolation",
          not fatal(RuntimeError("simulated index failure: deepmap_class_embeddings")))
    check("a plain non-connectivity error is benign", not fatal(ValueError("nothing to do here")))

    # bug-hunt #11/#12: the 5.26 procedure-fallback path raises a MISSING-INDEX ProcedureCallFailed
    # (NOT an IndexNotFound code). It must be BENIGN (skip the index, keep the arm); a connectivity
    # ProcedureCallFailed must still fail loud (the narrowness guard).
    def _proc_err(msg: str) -> ClientError:
        e = ClientError(msg)
        e._neo4j_code = "Neo.ClientError.Procedure.ProcedureCallFailed"
        return e

    check("missing VECTOR index (ProcedureCallFailed) is benign (procedure-fallback path)",
          not fatal(_proc_err("Failed to invoke procedure: There is no such vector schema index: deepmap_route_embeddings")))
    check("missing FTS index (ProcedureCallFailed) is benign (degrade to vector arms)",
          not fatal(_proc_err("Failed to invoke procedure: There is no such fulltext schema index: deepmap_code_fts")))
    check("a CONNECTIVITY ProcedureCallFailed (no missing-index signature) still FAILS LOUD (T10)",
          fatal(_proc_err("Failed to invoke procedure: connection to the database was lost")))

    print("\n-- contract: helper + arms wired --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("vector arm re-raises fatal", src.count("if _is_fatal_db_error(exc):\n                    raise") >= 1
          or src.count("_is_fatal_db_error(exc)") >= 3)
    check("all three arms guarded (>=3 call sites)", src.count("_is_fatal_db_error(exc)") >= 3,
          f"found {src.count('_is_fatal_db_error(exc)')}")

    gate_query_callers(R)
    gate_weighted_rrf_keying(R)
    gate_fts_query_safety(R)
    gate_fail_loud_search(R)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
