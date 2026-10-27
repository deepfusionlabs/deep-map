#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for deepmap_impact honesty, round 7 (W4: T8d-X2, T10d-W, T5#0 residual).

THE DEFECTS (bug-by-bug sweep, reproduced hermetically):
  T8d-X2 / T10d-W  an unknown DEEPMAP_IMPACT_PROFILE ('railz') silently fell back to the default edge set, and an
                   unknown / malformed edge type — from the DEEPMAP_IMPACT_EDGES CSV or the MCP `edge_types`
                   argument (['CASCADE','renders','BOGUS-1']) — was silently kept (matches nothing) or dropped,
                   with no word to the caller; a requested type the graph does not have walked nothing, silently.
  T5#0 residual    a NON-empty but partial answer carried no caveat (Context.Next returned 2 of its 4 callers):
                   callers of a METHOD are the ones the extractor resolves least (receiver/interface calls).
  MCP              an out-of-range `limit` was clamped silently.
THE FIX (impact path + the MCP boundary only; the ranking path is untouched): the notes ride on ImpactRows.notes,
which the CLI prints and the MCP tool appends as trailing {"note": ...} entries.
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import os
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
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail else ''}")


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_ENV_KEYS = ("DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_MAX_HOPS", "DEEPMAP_PROJECT_ROOT",
             "DEEPMAP_PROJECT_KEY", "DEEPMAP_PROJECT_GUARD")


class _Result:
    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


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
            return _Result([{"types": list(d.present)}])
        if "MATCH path = (caller)" in cypher:
            m = re.search(r"LIMIT \$limit(?:\s*\+\s*(\d+))?", cypher)
            n = int(params["limit"]) + int(m.group(1) or 0) if m else len(d.walk_rows)
            return _Result([dict(r) for r in d.walk_rows[:n]])
        if "MATCH (caller)-[r:CALLS]->(callee)" in cypher:
            return _Result([dict(r) for r in d.walk_rows[: int(params.get("limit", 30))]])
        if "AS matched" in cypher:
            return _Result([] if d.matched is None else [{"matched": d.matched}])
        return _Result([])


class _Driver:
    def __init__(self, walk_rows=(), present=("CALLS",), matched=1):
        self.walk_rows, self.present, self.matched, self.log = list(walk_rows), present, matched, []

    def session(self, **kw):
        return _Session(self)

    def walks(self):
        return [c for c, _ in self.log if "MATCH path = (caller)" in c]


def _row(caller, callee="save", hop=1, via="CALLS", conf="INFERRED"):
    return {"caller": caller, "caller_type": "Function", "file": f"src/{caller}.py", "line": 3,
            "confidence": conf, "callee": callee, "hop": hop, "via": via, "via_path": [via] * hop}


def _notes(rows) -> str:
    return " | ".join(getattr(rows, "notes", None) or [])


# ---------------------------------------------------------------------------------------------------------
def gate_unknown_profile(R) -> None:
    print("\n-- T8d-X2: an unknown DEEPMAP_IMPACT_PROFILE is named, not silently replaced by the default --")
    with _env(DEEPMAP_IMPACT_PROFILE="railz"):
        drv = _Driver([_row("a")])
        rows = R.query_impact(drv, "save")
    n = _notes(rows)
    check("'railz' -> a note names the unknown profile", "railz" in n and "profile" in n.lower(), n)
    check("…and lists the known profiles", all(p in n for p in ("rails", "orm", "associations")), n)
    check("…and the walk is still the default edge set",
          bool(drv.walks()) and "HAS_MANY" not in drv.walks()[-1] and "CALLS" in drv.walks()[-1],
          (drv.walks() or [""])[-1][:200])
    for prof in ("rails", "RAILS", "orm", ""):
        with _env(DEEPMAP_IMPACT_PROFILE=prof):
            rows = R.query_impact(_Driver([_row("a")], present=("CALLS", "HAS_MANY", "REFERENCES")), "save")
        check(f"profile {prof!r}: no profile note", "profile" not in _notes(rows).lower(), _notes(rows))


def gate_unknown_edge_types(R) -> None:
    print("\n-- T8d-X2 / T10d-W: unknown or malformed edge types (env CSV / MCP arg) are named --")
    with _env(DEEPMAP_IMPACT_EDGES="CALLS,CASCADE,BOGUS-1"):
        rows = R.query_impact(_Driver([_row("a")], present=("CALLS", "CASCADES")), "save")
    n = _notes(rows)
    check("env CSV: the unknown type 'CASCADE' is named (it matches nothing)", "CASCADE" in n and "unknown" in n.lower(), n)
    check("env CSV: the malformed token 'BOGUS-1' is named", "BOGUS-1" in n, n)
    check("env CSV: the note says where they came from", "DEEPMAP_IMPACT_EDGES" in n, n)
    rows = R.query_impact(_Driver([_row("a")], present=("CALLS", "RENDERS")), "save",
                          edge_types=["CASCADE", "renders", "BOGUS-1"])
    n = _notes(rows)
    check("edge_types arg: 'CASCADE' (a typo of CASCADES) is named as unknown", "'CASCADE'" in n, n)
    check("edge_types arg: 'BOGUS-1' is named as malformed", "BOGUS-1" in n and "malformed" in n.lower(), n)
    check("edge_types arg: a valid lower-case 'renders' is NOT flagged", "'renders'" not in n, n)
    check("…and the note lists the known types (so the caller can fix it)", "CASCADES" in n and "ROUTES_TO" in n, n)
    try:
        import framework_edges as FE
        real = {str(t).upper() for t in FE.ALLOWED_EDGE_TYPES}
    except Exception as exc:  # noqa: BLE001
        real = {f"<framework_edges unloadable: {exc}>"}
    check("the whitelist mirror in retrieval equals framework_edges.ALLOWED_EDGE_TYPES (drift lock)",
          set(getattr(R, "_ALLOWED_EDGE_TYPES_MIRROR", ())) == real,
          str(set(getattr(R, "_ALLOWED_EDGE_TYPES_MIRROR", ())) ^ real))
    rows = R.query_impact(_Driver([_row("a")], present=("CALLS", "HAS_MANY", "REFERENCES")), "save",
                          edge_types=["CALLS", "HAS_MANY", "REFERENCES"])
    check("valid, present edge types: no edge-type note", not _notes(rows), _notes(rows))
    rows = R.query_impact(_Driver([_row("a")]), "save", edge_types=[])
    n = _notes(rows)
    check("edge_types=[]: a note says only CALLS was walked (omit it for the default walk)",
          "CALLS" in n and ("only" in n.lower() or "omit" in n.lower()), n)
    rows = R.query_impact(_Driver([_row("a")], matched=0), "nope", edge_types=["BOGUS-1"])
    check("the edge-type note also rides on the 'not in the graph' answer", "BOGUS-1" in _notes(rows)
          and "not in the graph" in _notes(rows).lower(), _notes(rows))


def gate_absent_requested_types(R) -> None:
    print("\n-- a REQUESTED edge type the graph does not have is named (it walked nothing) --")
    rows = R.query_impact(_Driver([_row("a")], present=("CALLS",)), "save", edge_types=["CALLS", "HAS_MANY"])
    n = _notes(rows)
    check("explicit HAS_MANY on a graph without it -> a note says it is not in this graph", "HAS_MANY" in n
          and "not" in n.lower(), n)
    with _env(DEEPMAP_IMPACT_PROFILE="rails"):
        rows = R.query_impact(_Driver([_row("a")], present=("CALLS",)), "save")
    check("profile rails on a graph without ORM edges -> the missing association types are named",
          "HAS_MANY" in _notes(rows) and "REFERENCES" in _notes(rows), _notes(rows))
    rows = R.query_impact(_Driver([_row("a")], present=("CALLS",)), "save")
    check("the DEFAULT walk on a graph without RENDERS/CASCADES stays silent (nothing was requested)",
          not _notes(rows), _notes(rows))


def gate_method_caveat(R) -> None:
    print("\n-- T5#0: a non-empty answer for a METHOD carries a completeness caveat --")
    rows = R.query_impact(_Driver([_row("a", callee="Context.Next"), _row("b", callee="Context.Next")]),
                          "Context.Next")
    n = _notes(rows)
    check("Context.Next with 2 dependents -> a caveat that the list may be incomplete", bool(n)
          and ("incomplete" in n.lower() or "lower bound" in n.lower()), n)
    check("…naming the unresolved kinds (receiver / interface) and the remedy (Grep)",
          "receiver" in n.lower() and "interface" in n.lower() and "grep" in n.lower(), n)
    rows = R.query_impact(_Driver([_row("a", callee="Context.Next")]), "Next")
    check("a bare name that resolves to a METHOD (dotted callee) gets the caveat too",
          "lower bound" in _notes(rows).lower() or "incomplete" in _notes(rows).lower(), _notes(rows))
    rows = R.query_impact(_Driver([_row("a", callee="_mk_project")]), "_mk_project")
    check("a plain function answer stays note-free (the existing 'normal answer' lock)", not _notes(rows),
          _notes(rows))
    rows = R.query_impact(_Driver([], matched=2), "Context.invoke")
    check("an EMPTY method answer keeps its own 'no dependents found' note (one note, no duplicate caveat)",
          len(getattr(rows, "notes", [])) == 1, _notes(rows))


def gate_mcp_relay(R) -> None:
    print("\n-- MCP: the notes reach the caller; an out-of-range limit is said, not silent --")
    with _env(**{k: None for k in _ENV_KEYS}):
        S = _load("deepmap_search_server", "dm_search_server_impact_r7")
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)
    ret.driver = _Driver([_row("a", callee="Context.Next")], present=("CALLS",))
    S._get_retriever = lambda: ret
    out = S.deepmap_impact("Context.Next", edge_types=["CASCADE"])
    notes = [r["note"] for r in out if isinstance(r, dict) and set(r) == {"note"}]
    check("deepmap_impact(edge_types=['CASCADE']) ends with a note naming it", any("CASCADE" in x for x in notes),
          str(notes))
    check("…and with the method caveat", any("lower bound" in x.lower() or "incomplete" in x.lower() for x in notes),
          str(notes))
    check("…the dependent row itself is unchanged and comes first", out and out[0].get("caller") == "a", str(out[:1]))
    out = S.deepmap_impact("Context.Next", limit=500)
    notes = [r["note"] for r in out if isinstance(r, dict) and set(r) == {"note"}]
    check("limit=500 -> a trailing note says it was clamped to the maximum (100)",
          any("500" in x and "100" in x for x in notes), str(notes))
    ret.driver = _Driver([_row("a", callee="save")], present=("CALLS",))
    out = S.deepmap_impact("save", limit=30)
    check("an in-range limit on a plain function answer: no note appended (unchanged contract)",
          out == [_row("a", callee="save")], str(out))


def gate_off_ranking(R) -> None:
    print("\n-- the change stays on the impact path (no ranking symbol, no new untimed query) --")
    src = inspect.getsource(R.query_impact) + inspect.getsource(R._impact_rows)
    check("the impact path references no ranking symbol",
          all(t not in src for t in ("_arm_", "_weighted_rrf", "arm_weights")))
    from neo4j import Query, Session

    class _RealishSession(Session):
        def __init__(self, log):
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
    with _env(DEEPMAP_IMPACT_PROFILE="railz"):
        R.query_impact(drv, "Svc.save", edge_types=["CASCADE"])
    check("every query on a real session is still timed (the notes cost no extra round trip)",
          drv.log and all(isinstance(q, Query) and q.timeout for q in drv.log) and len(drv.log) == 3,
          str([type(q).__name__ for q in drv.log]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — impact honesty round 7: unknown profile/edge notes, method caveat, MCP relay (no Neo4j)")
    print("=" * 72)
    sys.path.insert(0, str(BUILD))
    with _env(**{k: None for k in _ENV_KEYS}):
        R = _load("retrieval", "dm_retrieval_impact_r7")
        gate_unknown_profile(R)
        gate_unknown_edge_types(R)
        gate_absent_requested_types(R)
        gate_method_caveat(R)
        gate_mcp_relay(R)
        gate_off_ranking(R)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
