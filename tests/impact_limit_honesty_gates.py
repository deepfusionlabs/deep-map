#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the deepmap_impact correctness + honesty fixes (v0.28.0 live-validation, W4).

THE BUGS (2026-09-24 live validation, all 2-skeptic confirmed):
  S1  query_impact / query_callers applied `LIMIT $limit` in Cypher BEFORE the Python de-dup, so a name
      that resolves to N same-named nodes filled the LIMIT window with N byte-identical copies of the
      first caller: `_mk_project` returned 10 of 52 callers, `check` 1 of 643 — and no truncation note.
      (tests/impact_dedup_gates.py's fake ignores LIMIT, so it could not see this.)
  S3  '#_key' normalised to '._key' (lstrip ran BEFORE the '#'->'.' replace) and matched nothing; a
      whitespace-padded symbol matched nothing; Rust owners stored with generics / lifetimes / reference
      sigils (`Deserializer<R>.parse_integer`, `&'a mut Serializer<W, F>.serialize_str`) never matched the
      natural `Deserializer.parse_integer` / `Deserializer::parse_integer`.
  S5  a hub name exhausted Neo4j's transaction memory (a TransientError) and was re-raised as an outage,
      although a 1-hop walk answers; the relationship-type probe swallowed an instance-level timeout and
      then issued a second blocking query; no server-side query timeout bounded a slow walk.
  S6  an empty answer for a symbol that EXISTS read as "nothing depends on it"; `via` broke ties between
      edge types arbitrarily (a cascading dependent labelled `via REFERENCES`).

The fake driver here MODELS the Cypher semantics the bug depends on: it applies `RETURN DISTINCT` (when the
query says so) and then the query's own `LIMIT $limit [+ n]` — unlike the older impact fakes, which ignore
both. Pure + hermetic: no Neo4j, no network, no spend.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_{name}_w4", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(caller, callee="_mk_project", line=1, hop=1, via="CALLS", conf="AMBIGUOUS", file=None):
    return {"caller": caller, "caller_type": "Function", "file": file or f"tests/{caller}.py",
            "line": line, "confidence": conf, "callee": callee, "hop": hop, "via": via,
            "via_path": [via] * hop}


def _callers_row(caller, callee="_mk_project", line=1, conf="AMBIGUOUS"):
    return {"caller": caller, "caller_type": "Function", "file": f"tests/{caller}.py", "line": line,
            "confidence": conf, "callee": callee}


class _Result:
    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


def _apply_distinct_limit(cypher: str, params: dict, rows: list) -> list:
    """Model Cypher: `RETURN DISTINCT` collapses identical projected rows BEFORE `LIMIT` is applied."""
    out = rows
    if "RETURN DISTINCT" in cypher:
        seen, uniq = set(), []
        for r in rows:
            key = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in r.items()))
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        out = uniq
    m = re.search(r"LIMIT \$limit(?:\s*\+\s*(\d+))?", cypher)
    if m:
        out = out[: int(params["limit"]) + int(m.group(1) or 0)]
    return [dict(r) for r in out]


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        d = self.drv
        d.log.append((cypher, params))
        if "db.relationshipTypes" in cypher:
            if d.probe_error is not None:
                raise d.probe_error
            return _Result([{"types": list(d.present)}])
        if "MATCH path = (caller)" in cypher:
            hops = int(re.search(r"\*1\.\.(\d+)", cypher).group(1))
            err = d.walk_errors.get(hops)
            if err is not None:
                raise err
            return _Result(_apply_distinct_limit(cypher, params, d.walk_rows))
        if "MATCH (caller)-[r:CALLS]->(callee)" in cypher:
            return _Result(_apply_distinct_limit(cypher, params, d.callers_rows))
        if "AS matched" in cypher:                       # the symbol-existence probe (count only)
            return _Result([] if d.matched is None else [{"matched": d.matched}])
        return _Result([])


class _Driver:
    def __init__(self, walk_rows=(), callers_rows=(), present=("CALLS",), matched=None,
                 walk_errors=None, probe_error=None):
        self.walk_rows = list(walk_rows)
        self.callers_rows = list(callers_rows)
        self.present = present
        self.matched = matched
        self.walk_errors = walk_errors or {}
        self.probe_error = probe_error
        self.log = []

    def session(self, **kw):
        return _Session(self)

    def walks(self):
        return [c for c, _ in self.log if "MATCH path = (caller)" in c]

    def params_of(self, needle):
        return [p for c, p in self.log if needle in c]


def _distinct_callers(rows):
    return len({r["caller"] for r in rows})


# ---------------------------------------------------------------------------------------------------
def gate_limit_after_distinct(R) -> None:
    print("\n-- S1: DISTINCT is applied BEFORE LIMIT (a 3-way same-name fan-out no longer eats the window) --")
    # 52 distinct callers of `_mk_project`, each fanned out to 3 same-named callee nodes -> 3 identical
    # projected rows per caller, adjacent after ORDER BY (the live `_mk_project` shape).
    raw = [_row(f"caller_{i:02d}") for i in range(52) for _ in range(3)]
    drv = _Driver(walk_rows=raw, matched=3)
    rows = R.query_impact(drv, "_mk_project", limit=30)
    check("limit 30 returns 30 DISTINCT dependents (was 10: 30 raw rows / 3 copies)",
          len(rows) == 30 and _distinct_callers(rows) == 30,
          f"got {len(rows)} rows / {_distinct_callers(rows)} callers")
    walk = (drv.walks() or [""])[-1]
    check("the walk Cypher de-duplicates projected rows in Cypher (RETURN DISTINCT)",
          "RETURN DISTINCT" in walk, walk[-400:])
    check("the walk's LIMIT comes AFTER the DISTINCT projection",
          walk.find("RETURN DISTINCT") != -1 and walk.find("LIMIT") > walk.find("RETURN DISTINCT"))
    check("truncation is DETECTED (52 > 30) and exposed on the result",
          getattr(rows, "truncated", None) is True, repr(getattr(rows, "truncated", None)))
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("a truncation note tells the caller more dependents exist", "more" in notes.lower(), notes)

    rows = R.query_impact(_Driver(walk_rows=raw, matched=3), "_mk_project", limit=100)
    check("limit 100 returns all 52 distinct dependents (was 34)", len(rows) == 52, f"got {len(rows)}")
    check("no truncation flagged when everything fit", getattr(rows, "truncated", None) is False,
          repr(getattr(rows, "truncated", None)))
    check("no truncation note when everything fit",
          not any("more" in n.lower() for n in (getattr(rows, "notes", []) or [])),
          str(getattr(rows, "notes", None)))
    check("the result is still a plain list of row dicts (every consumer keeps working)",
          isinstance(rows, list) and all(isinstance(r, dict) for r in rows))

    print("\n-- S1: the one-hop query_callers applies DISTINCT before LIMIT too --")
    craw = [_callers_row(f"c{i:02d}") for i in range(20) for _ in range(4)]
    out = R.query_callers(_Driver(callers_rows=craw), "_mk_project", limit=10)
    check("query_callers limit 10 -> 10 distinct callers (was 3)",
          len(out) == 10 and _distinct_callers(out) == 10, f"got {len(out)} / {_distinct_callers(out)}")
    from neo4j.exceptions import ClientError
    drv = _Driver(callers_rows=craw, matched=4,
                  walk_errors={3: ClientError("variable-length not supported")})
    out = R.query_impact(drv, "_mk_project", limit=10)
    check("the degrade path (ClientError -> one-hop) also returns 10 distinct callers",
          len(out) == 10 and _distinct_callers(out) == 10, f"got {len(out)} / {_distinct_callers(out)}")
    check("the degrade path flags truncation (20 > 10)", getattr(out, "truncated", None) is True,
          repr(getattr(out, "truncated", None)))
    notes = " ".join(getattr(out, "notes", []) or [])
    check("the degrade path SAYS it fell back to direct callers (no silent 1-hop)",
          "direct" in notes.lower() or "one-hop" in notes.lower() or "1-hop" in notes.lower(), notes)


def gate_symbol_normalisation(R) -> None:
    print("\n-- S3: '#method', padding and '::' normalise; owner generics/sigils are tolerated --")

    def bound(symbol, fn=R.query_impact):
        drv = _Driver(matched=1)
        fn(drv, symbol)
        named = [p for _, p in drv.log if "name" in p]
        return named[-1] if named else {}

    for sym, want in (("#_key", "_key"), (" build_record ", "build_record"), ("build_record\n", "build_record"),
                      (" .build_record", "build_record"), ("User#save!", "User.save!"),
                      (".User#x", "User.x"), ("Svc.foo", "Svc.foo"), ("foo", "foo")):
        for fname, fn in (("query_impact", R.query_impact), ("query_callers", R.query_callers)):
            got = bound(sym, fn).get("name")
            check(f"{fname}({sym!r}) binds name={want!r}", got == want, repr(got))

    rx_of = getattr(R, "_impact_name_regex", None)
    check("an owner-tolerant name regex helper exists (_impact_name_regex)", callable(rx_of))
    if not callable(rx_of):
        return
    check("a bare name needs no regex (None -> the = / ENDS WITH match is unchanged)",
          rx_of("parse_integer") is None, repr(rx_of("parse_integer")))
    cases = [
        ("Deserializer.parse_integer", "Deserializer<R>.parse_integer", True),
        ("Deserializer::parse_integer", "Deserializer<R>.parse_integer", True),
        ("Serializer.serialize_str", "&'a mut Serializer<W, F>.serialize_str", True),
        ("Serializer::serialize_str", "&mut Serializer<W>.serialize_str", True),
        ("Deserializer.from_str", "Deserializer<read::StrRead<'a>>.from_str", True),
        ("Wrap::generic_target", "Wrap<T>.generic_target", True),
        ("Plain::plain_target", "Plain.plain_target", True),
        ("Admin::Override", "Admin::Override", True),
        ("Svc.foo", "pkg.Svc.foo", True),
        ("Wrap.generic_target", "OtherWrap.generic_target", False),
        ("Wrap.generic_target", "Wrap.generic_target_two", False),
        ("Deserializer.parse_integer", "Deserializer<R>.parse_integer_x", False),
    ]
    for query, stored, want in cases:
        rx = rx_of(query)
        got = bool(rx) and re.fullmatch(rx, stored) is not None
        check(f"regex for {query!r} {'matches' if want else 'rejects'} stored {stored!r}", got == want, rx or "")
    p = bound("Deserializer::parse_integer")
    check("a qualified symbol binds the owner-tolerant regex as $name_rx (a bound param, never inlined)",
          p.get("name_rx") and re.fullmatch(p["name_rx"], "Deserializer<R>.parse_integer") is not None, str(p))
    walk = (lambda d: (R.query_impact(d, "Deserializer::parse_integer"), d)[1])(_Driver(matched=1)).walks()
    check("the walk's callee match ORs in `callee.name =~ $name_rx`",
          bool(walk) and "=~ $name_rx" in walk[-1], (walk or [""])[-1][:300])
    check("the '::' symbol text is NOT inlined into the Cypher", bool(walk) and "parse_integer" not in walk[-1])


def gate_existence_and_empty_honesty(R) -> None:
    print("\n-- S6: an empty answer distinguishes 'not in the graph' from 'in the graph, no dependents found' --")
    drv = _Driver(walk_rows=[], matched=0)
    rows = R.query_impact(drv, "NoSuchThing")
    check("unknown symbol -> empty result", list(rows) == [], str(rows))
    check("unknown symbol -> the expensive walk is skipped", not drv.walks(), str(drv.walks()))
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("unknown symbol -> a note says it is NOT in the graph", "not in the graph" in notes.lower(), notes)
    check("unknown symbol -> matched == 0", getattr(rows, "matched", None) == 0,
          repr(getattr(rows, "matched", None)))

    drv = _Driver(walk_rows=[], matched=2)
    rows = R.query_impact(drv, "Context.invoke")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("existing symbol with 0 dependents -> empty result", list(rows) == [], str(rows))
    check("existing symbol -> the note says it IS in the graph (with the match count)",
          "in the graph" in notes and "2" in notes, notes)
    check("existing symbol -> the note warns empty is NOT proof nothing uses it + to verify by reading",
          "not" in notes.lower() and ("grep" in notes.lower() or "reading" in notes.lower()), notes)
    check("existing symbol -> it names the unresolved-call cause (receiver/method calls)",
          "receiver" in notes.lower() or "method" in notes.lower(), notes)

    rows = R.query_impact(_Driver(walk_rows=[_row("a")], matched=1), "x")
    check("a normal answer carries no honesty notes", not (getattr(rows, "notes", []) or []),
          str(getattr(rows, "notes", None)))


def gate_hub_memory_degrade(R) -> None:
    print("\n-- S5: a transaction-memory blow-up on a hub name degrades to a bounded 1-hop walk + a note --")
    from neo4j.exceptions import TransientError
    oom = TransientError("The allocation of an extra 2.0 MiB would use more than the limit 358.4 MiB. "
                         "Currently using 358.4 MiB. dbms.memory.transaction.total.max threshold reached")
    oom._neo4j_code = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"
    one_hop = [_row(f"c{i}") for i in range(5)]
    drv = _Driver(walk_rows=one_hop, matched=111, walk_errors={3: oom})
    raised = None
    try:
        rows = R.query_impact(drv, "check")
    except Exception as exc:  # noqa: BLE001
        raised, rows = exc, []
    check("a MemoryPoolOutOfMemoryError at 3 hops does NOT fail as an outage", raised is None, repr(raised))
    hops = [int(re.search(r"\*1\.\.(\d+)", c).group(1)) for c in drv.walks()]
    check("it retried the walk bounded to 1 hop", hops[:1] == [3] and 1 in hops, str(hops))
    check("the 1-hop answer is returned", len(rows) == 5, f"got {len(rows)}")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("a note says the walk was reduced to 1 hop because of Neo4j memory",
          "1-hop" in notes and "memory" in notes.lower(), notes)

    drv = _Driver(walk_rows=one_hop, matched=111, walk_errors={3: oom, 1: oom})
    raised = None
    try:
        R.query_impact(drv, "check")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    too = getattr(R, "ImpactTooExpensive", None)
    check("even-1-hop exhaustion raises the specific ImpactTooExpensive (not a fake empty, not 'unreachable')",
          too is not None and isinstance(raised, too), repr(raised))
    check("its message is honest + actionable (memory, a qualified Class.method name)",
          raised is not None and "memory" in str(raised).lower() and "Class.method" in str(raised), str(raised))

    from neo4j.exceptions import ClientError
    tmo = ClientError("The transaction has been terminated. Retry your operation in a new transaction")
    tmo._neo4j_code = "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"
    craw = [_callers_row(f"d{i}") for i in range(3)]
    drv = _Driver(walk_rows=one_hop, callers_rows=craw, matched=2, walk_errors={3: tmo})
    rows = R.query_impact(drv, "slow_symbol")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("a server-side query timeout (a ClientError, verified live) degrades to direct callers",
          len(rows) == 3, f"got {len(rows)}")
    check("...and the note says the walk TIMED OUT (not a vague failure)", "timed out" in notes, notes)

    from neo4j.exceptions import ServiceUnavailable
    drv = _Driver(walk_rows=one_hop, matched=1, walk_errors={3: ServiceUnavailable("unable to connect")})
    raised = None
    try:
        R.query_impact(drv, "x")
    except ServiceUnavailable as exc:
        raised = exc
    check("an instance-down error still fails LOUD (no retry masks an outage)", raised is not None)


def gate_probe_does_not_swallow_outage(R) -> None:
    print("\n-- S5: the relationship-type probe re-raises an instance-level failure (no 2nd blocking query) --")
    from neo4j.exceptions import SessionExpired
    drv = _Driver(walk_rows=[_row("a")], matched=1,
                  probe_error=SessionExpired("Failed to read from defunct connection (timed out)"))
    raised = None
    try:
        R.query_impact(drv, "x")
    except SessionExpired as exc:
        raised = exc
    check("a defunct/timed-out probe raises instead of issuing the walk", raised is not None, repr(raised))
    check("no walk query was issued after the dead probe", not drv.walks(), str(len(drv.walks())))
    drv = _Driver(walk_rows=[_row("a")], matched=1, probe_error=RuntimeError("no such procedure"))
    rows = R.query_impact(drv, "x")
    check("a benign probe failure still proceeds with the requested types", len(rows) == 1, str(rows))


def gate_via_priority(R) -> None:
    print("\n-- S6: `via` ties prefer CASCADES / RENDERS over the ORM association edges --")
    kinds = ("CALLS", "CASCADES", "RENDERS", "REFERENCES", "HAS_MANY")
    drv = _Driver(walk_rows=[_row("a")], matched=1, present=kinds)
    R.query_impact(drv, "Order", edge_types=list(kinds))
    walk = (drv.walks() or [""])[-1]
    pick = walk.split("collect(", 1)[0]          # the ORDER BY that decides which path is `best`
    m = re.search(r"CASE via (.*?) END", pick, re.S)
    body = m.group(1) if m else ""

    def rank(t):
        mm = re.search(rf"WHEN '{t}' THEN (\d+)", body)
        if mm:
            return int(mm.group(1))
        me = re.search(r"ELSE (\d+)", body)
        return int(me.group(1)) if me else None

    check("the path pick ranks edge types with a CASE over `via`", bool(body), pick[-300:])
    if body:
        check("CASCADES ranks before REFERENCES / HAS_MANY",
              rank("CASCADES") < rank("REFERENCES") and rank("CASCADES") < rank("HAS_MANY"),
              f"{rank('CASCADES')} {rank('REFERENCES')} {rank('HAS_MANY')}")
        check("RENDERS ranks before REFERENCES / HAS_MANY",
              rank("RENDERS") < rank("REFERENCES") and rank("RENDERS") < rank("HAS_MANY"),
              f"{rank('RENDERS')} {rank('REFERENCES')}")
        check("CALLS stays ahead of the association edges (a call is firmer wiring)",
              rank("CALLS") < rank("REFERENCES"), f"{rank('CALLS')} {rank('REFERENCES')}")
    order = re.search(r"ORDER BY hop ASC, worstOrd ASC,([^\n]*)", pick)
    check("the type rank breaks ties only AFTER hop + confidence (shortest, firmest path still wins)",
          order is not None and "viaRank" in order.group(1), pick[-300:])
    drv = _Driver(walk_rows=[_row("a")], matched=1, present=kinds)
    R.query_impact(drv, "Order")                        # the DEFAULT walk (no association opt-in)
    walk = (drv.walks() or [""])[-1]
    check("the default walk's Cypher still names no association type (benchmark-gate invariant)",
          "HAS_MANY" not in walk and "REFERENCES" not in walk, walk[:200])
    check("...while still ranking CASCADES first", "WHEN 'CASCADES' THEN 0" in walk)
    drv = _Driver(walk_rows=[_row("a")], matched=1, present=("CALLS",))
    R.query_impact(drv, "x")
    walk = (drv.walks() or [""])[-1]
    check("a CALLS-only graph gets a constant rank (no empty CASE)", "1 AS viaRank" in walk, walk[:300])


def gate_server_side_timeout(R) -> None:
    print("\n-- S5: a real driver session gets a server-side transaction timeout on every impact query --")
    from neo4j import Query, Session

    class _RealishSession(Session):
        def __init__(self, log):          # never touches a pool — a stand-in typed as a real Session
            self.log = log

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, query, parameters=None, **kw):
            self.log.append(query)
            text = query.text if isinstance(query, Query) else query
            if "AS matched" in text:
                return _Result([{"matched": 1}])
            return _Result([])

        def close(self):
            pass

        def __del__(self):
            pass

    class _RealishDriver:
        def __init__(self):
            self.log = []

        def session(self, **kw):
            return _RealishSession(self.log)

    drv = _RealishDriver()
    R.query_impact(drv, "x")
    walks = [q for q in drv.log if isinstance(q, Query) and "MATCH path = (caller)" in q.text]
    check("the impact walk is sent as a neo4j.Query with a timeout", bool(walks), str(drv.log)[:300])
    tmo = getattr(R, "IMPACT_QUERY_TIMEOUT_S", None)
    check("the timeout is the module's IMPACT_QUERY_TIMEOUT_S (bounded, > 0)",
          bool(walks) and tmo and walks[-1].timeout == tmo and tmo > 0, repr(tmo))
    check("every query issued on a real session is timed (count probe, type probe, walk)",
          drv.log and all(isinstance(q, Query) and q.timeout for q in drv.log),
          str([type(q).__name__ for q in drv.log]))


def gate_freeze(R) -> None:
    print("\n-- FREEZE: the ranking path is untouched; the impact path stays ranking-free --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("Weighted-RRF formula unchanged", "w * (1.0 / (k + rank))" in src)
    check("RRF_K is still 60", R.RRF_K == 60)
    check("DEFAULT_ARM_WEIGHTS still equal", R.DEFAULT_ARM_WEIGHTS == {"vector": 1.0, "bm25": 1.0,
                                                                       "graph_weighted": 1.0})
    qi = inspect.getsource(R.query_impact) + inspect.getsource(R.query_callers)
    check("the impact path references no ranking symbol",
          all(t not in qi for t in ("_arm_", "_weighted_rrf", "arm_weights")))


def gate_edge_types_docstring(R) -> None:
    """Round 2: the MCP deepmap_impact description must say `edge_types` REPLACES the default walk and name
    exactly the live default set — the old text ('pass ["CALLS","HAS_MANY","REFERENCES"] to fold model
    associations in') silently DROPPED routes/DI/events/jobs/hooks/inheritance/cascades/renders."""
    print("\n-- deepmap_impact's description: edge_types REPLACES the default walk (and names it) --")
    import ast
    src = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "deepmap_impact"), None)
    doc = " ".join((ast.get_docstring(fn) or "").split()) if fn else ""
    check("the description says edge_types REPLACES the default walk (not adds to it)",
          "REPLACES the default walk" in doc and "not added" in doc, doc[-600:])
    missing = [t for t in R.DEFAULT_IMPACT_EDGE_TYPES if t not in doc]
    check("it names every edge type of the LIVE default set (drift-locked to DEFAULT_IMPACT_EDGE_TYPES)",
          not missing, f"missing {missing}")
    check("it says what the association-only example DROPS", "DROPS" in doc, doc[-600:])
    check("it tells the caller how to ADD associations (the whole default list plus HAS_MANY/REFERENCES)",
          "whole default list" in doc and "HAS_MANY" in doc and "REFERENCES" in doc, doc[-600:])
    check("the ORM associations are still described as NOT in the default",
          all(t not in R.DEFAULT_IMPACT_EDGE_TYPES for t in R.ORM_ASSOCIATION_EDGE_TYPES)
          and "not in the default" in doc, doc[-600:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — impact LIMIT/DISTINCT + honesty gates (S1/S3/S5/S6; no Neo4j, no spend)")
    print("=" * 72)
    R = _load("retrieval")
    gate_limit_after_distinct(R)
    gate_symbol_normalisation(R)
    gate_existence_and_empty_honesty(R)
    gate_hub_memory_degrade(R)
    gate_probe_does_not_swallow_outage(R)
    gate_via_priority(R)
    gate_server_side_timeout(R)
    gate_freeze(R)
    gate_edge_types_docstring(R)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
