#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for deepmap_impact's notes on SQL matches (the 2026-11 SQL design, section 5.4; supply critique
MAJOR 1; design gate M).

THE TWO DEFECTS THIS LOCKS (build/retrieval.py, the impact path only; every ranking function untouched, see
tests/fts_r7_gates.py):
  1. A same-named SQL table changed the C# notes. The count query maps the matched symbols' file extensions to
     language families, and `.sql` is a family of its own (edge_resolve._FAMILY_EXTS["sql"]). A DB-first .NET repo
     whose table `Customer` shares its name with the C# class `Customer` read as "C# mixed with another language", so
     `deepmap_impact Customer` lost the C#-only notes (_CS_EMPTY, _CS_ROWS, the property notes) and gave the generic
     mixed text. Now the C# mode is chosen over the NON-SQL families, and the property rule compares the proven
     property count with the non-SQL matches (`props == matched - sqlm`).
  2. A SQL match got no honest note. SQL nodes are declarations only: nothing in the graph links who reads, writes
     or executes a table, view or procedure, so an empty answer says nothing about its users. The count query now
     also counts the matches in .sql files (`AS sqlm`, the SAME round trip): a SQL-only empty answer gets ONE note
     saying so (declarations only, SQL in .txt scripts and code strings is not in the graph, Grep for it); a mixed
     answer gets one more sentence (appended to the ONE empty-answer note, or its own note beside rows).

  A. the count query: still ONE query per direct match, holding `AS sqlm` beside `AS matched` / `AS exts` /
     `AS props`; the SQL suffix it tests is edge_resolve's "sql" family; _count_symbol_matches_info returns the SQL
     count as a 4th value (None when the record lacks it or it is not a count); _count_symbol_matches_langs keeps its
     2-tuple and _count_symbol_matches its int.
  B. SQL only, no rows -> exactly one note: the found sentence + _SQL_EMPTY; no :_DeepMapMeta read. Its head
     agrees with the count: "{m} comes from a .sql file" for one match, "all {k} matches come from .sql files" (and
     "their users") for more, as the mixed sentence words it.
  C. C# + a same-named SQL table: the C#-only notes, byte for byte, then the SQL sentence (empty, rows, truncated,
     typed, narrowed, the property notes with props == matched - sqlm, the 1-hop fallback).
  D. other mixes: Python + SQL, C# + Python + SQL (C# mixed clause kept), SQL-only with rows (its own note).
  E. unchanged: no SQL in the answer, an unknown language (no extensions), a record without `sqlm` or with a
     malformed one (a bool, a negative, text, a float, more than the matches) -> the notes are exactly what the same
     answer gives without the SQL column (today's behaviour).
  F. the module-qualified reading: a .sql file's symbol -> the SQL note; a C# + SQL file list, or a list cut at 4
     files (unknown) -> today's notes.
  G. off ranking: the impact path names no ranking symbol.

Hermetic: a scripted fake driver (no Neo4j, no network, no spend). The C# texts come from
tests/impact_csharp_honesty_gates.py (their contract); the SQL texts are written out here.
Usage: python tests/sql_impact_notes_gates.py
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0
_MISSING = object()


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' -- ' + str(detail)) if detail != '' else ''}")


def _load(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# The C# contract texts and helpers (one source for them: the C# honesty gate).
CS = _load(REPO / "tests" / "impact_csharp_honesty_gates.py", f"dm_sqlnotes_cs_{time.monotonic_ns()}")
_found, _row = CS._found, CS._row
SYM = "Customer"


def _f(text: str, m: str = SYM) -> str:
    return text.replace("{m}", m)


# ---- the SQL texts, written out here independently of build/retrieval.py (the contract) -------------------------
SQL_EMPTY = (" That is NOT proof nothing uses it: {m} comes from a .sql file, and this graph holds SQL files, tables, "
             "views, procedures, functions and triggers as declarations only: it does not link who reads, writes or "
             "executes them (other SQL, C# or other data-access code, ORM mappings), and SQL kept outside .sql files "
             "(scripts saved as .txt, SQL strings in code) is not in the graph. Grep for {m} to find its users.")
# The same note when every one of k > 1 matches lives in a .sql file: the head and "their" agree with the count.
SQL_EMPTY_MANY = (" That is NOT proof nothing uses it: all {k} matches come from .sql files, and this graph holds SQL "
                  "files, tables, views, procedures, functions and triggers as declarations only: it does not link "
                  "who reads, writes or executes them (other SQL, C# or other data-access code, ORM mappings), and "
                  "SQL kept outside .sql files (scripts saved as .txt, SQL strings in code) is not in the graph. Grep "
                  "for {m} to find their users.")


def sql_empty(k: int, m: str = SYM) -> str:
    """The SQL-only empty answer's note after the found sentence, for k matches that all live in .sql files."""
    return _f(SQL_EMPTY, m) if k == 1 else _f(SQL_EMPTY_MANY.replace("{k}", str(k)), m)


def sql_more(k: int, matched: int, m: str = SYM) -> str:
    """The SQL sentence of a mixed answer (or of a SQL-only answer with rows), without its leading space."""
    tail = "nothing in this graph links who reads, writes or executes {it}; Grep for {m} to find {its} users."
    if k == matched == 1:
        head, it = f"{m} comes from a .sql file", True
    elif k == matched:
        head, it = f"All {k} matches come from .sql files", False
    elif k == 1:
        head, it = "1 of the matches comes from a .sql file", True
    else:
        head, it = f"{k} of the matches come from .sql files", False
    return f"{head}: " + tail.format(it="it" if it else "them", m=m, its="its" if it else "their")


# ---- a scripted fake Neo4j (the C# gate's, plus the SQL count) --------------------------------------------------
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
        text = getattr(cypher, "text", cypher)
        d.log.append((text, params))
        if "_DeepMapMeta" in text:
            if d.meta is _MISSING:
                return _Result([])
            rec = {"l": d.meta}
            if d.narrowed is not _MISSING:
                rec["n"] = d.narrowed
            return _Result([rec])
        if "db.relationshipTypes" in text:
            return _Result([{"types": ["CALLS"]}])
        if "MATCH path = (caller)" in text:
            hops = int(re.search(r"\*1\.\.(\d+)", text).group(1))
            if d.walk_errors.get(hops) is not None:
                raise d.walk_errors[hops]
            m = re.search(r"LIMIT \$limit(?:\s*\+\s*(\d+))?", text)
            n = int(params["limit"]) + int(m.group(1) or 0) if m else len(d.walk_rows)
            return _Result([dict(r) for r in d.walk_rows[:n]])
        if "MATCH (caller)-[r:CALLS]->(callee)" in text:
            return _Result([dict(r) for r in d.callers_rows[: int(params.get("limit", 30))]])
        if "AS matched" in text and "$file_rx" in text:          # a module-qualified reading's count
            if d.module is None:
                return _Result([{"matched": 0, "files": []}])
            return _Result([{"matched": d.module_matched or len(d.module), "files": list(d.module)}])
        if "AS matched" in text:                                  # the direct symbol count
            rec = {"matched": d.matched, "exts": d.exts}
            if d.props is not _MISSING:
                rec["props"] = d.props
            if d.sqlm is not _MISSING:
                rec["sqlm"] = d.sqlm
            return _Result([rec])
        return _Result([])


class _Driver:
    def __init__(self, exts, matched=2, sqlm=_MISSING, props=_MISSING, walk_rows=(), callers_rows=(), meta=_MISSING,
                 narrowed=_MISSING, walk_errors=None, module=None, module_matched=None):
        self.exts, self.matched, self.sqlm, self.props = exts, matched, sqlm, props
        self.walk_rows, self.callers_rows = list(walk_rows), list(callers_rows)
        self.meta, self.narrowed, self.walk_errors = meta, narrowed, dict(walk_errors or {})
        self.module, self.module_matched, self.log = module, module_matched, []

    def session(self, **kw):
        return _Session(self)

    def queries(self, needle):
        return [c for c, _ in self.log if needle in c]


def _notes(rows) -> list:
    return list(getattr(rows, "notes", None) or [])


def _impact(R, drv, symbol=SYM, **kw):
    with CS._env(**{k: None for k in CS._ENV_KEYS}):
        return R.query_impact(drv, symbol, **kw)


# =================================================================================================================
def gate_count_query(R) -> None:
    print("\n-- A. the SQL count rides in the ONE count query --")
    drv = _Driver(exts=["cs", "sql"], sqlm=1, props=0)
    _impact(R, drv)
    counts = drv.queries("AS matched")
    q = " ".join((counts or [""])[0].split())
    check("exactly one count query for a direct match", len(counts) == 1, len(counts))
    check("...holding 'AS sqlm' beside 'AS matched', 'AS exts' and 'AS props'",
          all(w in q for w in ("RETURN count(callee) AS matched", "AS exts", "AS props", "AS sqlm")), q[-400:])
    ER = _load(BUILD / "edge_resolve.py", f"dm_sqlnotes_er_{time.monotonic_ns()}")
    fam = tuple(ER._FAMILY_EXTS.get("sql") or ())
    tested = re.findall(r"ENDS WITH '(\.[a-z0-9]+)' THEN 1 END\) AS sqlm", q)
    check("...and it counts exactly edge_resolve's 'sql' family suffixes (lower-cased source_file)",
          fam == (".sql",) and tested == list(fam) and "toLower(coalesce(callee.source_file, ''))" in q,
          (fam, tested))
    sess = _Driver(exts=["cs", "sql"], sqlm=1, props=0).session()
    info = R._count_symbol_matches_info(sess, SYM, None)
    check("_count_symbol_matches_info -> (matched, families, props, sqlm)",
          isinstance(info, tuple) and len(info) == 4 and info[0] == 2 and info[1] == {"dotnet", "sql"}
          and info[2] == 0 and info[3] == 1, info)
    two = R._count_symbol_matches_langs(_Driver(exts=["cs", "sql"], sqlm=1).session(), SYM, None)
    check("_count_symbol_matches_langs keeps its 2-tuple", two == (2, {"dotnet", "sql"}), two)
    check("_count_symbol_matches keeps its int", R._count_symbol_matches(_Driver(exts=["sql"]).session(), SYM, None)
          == 2)
    for label, v in (("absent", _MISSING), ("null", None), ("a bool", True), ("negative", -1), ("text", "1"),
                     ("a float", 1.0)):
        got = R._count_symbol_matches_info(_Driver(exts=["sql"], sqlm=v).session(), SYM, None)
        check(f"sqlm {label} -> None (unknown)", got[3] is None, got)


def gate_sql_only(R) -> None:
    print("\n-- B. a SQL-only answer, no rows: ONE note, the found sentence + what the graph holds for SQL --")
    drv = _Driver(exts=["sql"], sqlm=2, meta=["csharp"])
    rows = _impact(R, drv)
    want = _found(SYM, 2) + sql_empty(2)
    check("SQL only, zero rows -> exactly [found + _SQL_EMPTY]", _notes(rows) == [want], _notes(rows))
    n0 = (_notes(rows) or [""])[0]
    check("...two SQL matches: the head agrees with the count ('all 2 matches come from .sql files', 'their users')",
          "all 2 matches come from .sql files" in n0 and "their users" in n0
          and f"{SYM} comes from a .sql file" not in n0 and "its users" not in n0, n0)
    rows = _impact(R, _Driver(exts=["sql"], matched=3, sqlm=3))
    check("three SQL matches -> found (3 matching symbols) + 'all 3 matches come from .sql files'",
          _notes(rows) == [_found(SYM, 3) + sql_empty(3)], _notes(rows))
    n = " ".join(_notes(rows))
    check("...it says declarations only, names .txt scripts and code strings, and Grep",
          "declarations only" in n and ".txt" in n and "SQL strings in code" in n and f"Grep for {SYM}" in n, n)
    check("...no Python / C# call wording (self.m(), receiver), no em / en dash",
          "self." not in n and "receiver" not in n and chr(0x2014) not in n and chr(0x2013) not in n, n)
    check("...and no :_DeepMapMeta read (no C# target)", not drv.queries("_DeepMapMeta"))
    rows = _impact(R, _Driver(exts=["sql"], matched=1, sqlm=1), "dbo.Customer")
    check("one SQL match, a schema-qualified symbol -> '(1 matching symbol)', Grep for the last segment",
          _notes(rows) == [_found("dbo.Customer", 1) + _f(SQL_EMPTY)], _notes(rows))
    check("...one match keeps the singular note byte for byte ('{m} comes from a .sql file', 'its users')",
          sql_empty(1, "Customer") == _f(SQL_EMPTY, "Customer") and "Customer comes from a .sql file" in _notes(rows)[0]
          and "its users" in _notes(rows)[0], _notes(rows))
    rows = _impact(R, _Driver(exts=["SQL"], sqlm=2))
    check("an upper-case extension ('SQL') is the SQL family too", _notes(rows) == [want], _notes(rows))
    rows = _impact(R, _Driver(exts=["sql", ""], matched=3, sqlm=2))
    check("SQL plus a node without a source file (sqlm < matched) -> the generic note + the SQL sentence, not "
          "'SQL only'", _notes(rows) == [_found(SYM, 3) + CS.GENERIC_TAIL + " " + sql_more(2, 3)], _notes(rows))


def gate_csharp_and_sql(R) -> None:
    print("\n-- C. C# + a same-named SQL table: the C#-only notes byte for byte, then the SQL sentence --")
    base = _notes(_impact(R, _Driver(exts=["cs"], matched=1, props=0, sqlm=0)))
    check("(the C#-only answer for one C# match is [found + _CS_EMPTY])",
          base == [_found(SYM, 1) + _f(CS.CS_EMPTY)], base)
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1))
    want = [_found(SYM, 2) + _f(CS.CS_EMPTY) + " " + sql_more(1, 2)]
    check("C# + SQL, zero rows -> ONE note: found + _CS_EMPTY (not the mixed C# clause) + the SQL sentence",
          _notes(rows) == want, _notes(rows))
    check("...the generic Python text and the mixed C# clause are not in it",
          CS.GENERIC_TAIL not in _notes(rows)[0] and CS.MIXED_EMPTY not in _notes(rows)[0], _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=3, props=0, sqlm=2))
    check("C# + two SQL matches -> '2 of the matches come from .sql files'",
          _notes(rows) == [_found(SYM, 3) + _f(CS.CS_EMPTY) + " " + sql_more(2, 3)], _notes(rows))
    walk = [_row("CustomerService.Load", callee="Customer")]
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1, walk_rows=walk))
    check("C# + SQL with rows -> [_CS_ROWS, the SQL sentence as its own note]",
          _notes(rows) == [_f(CS.CS_ROWS), sql_more(1, 2)], _notes(rows))
    many = [_row(f"H{i}.Handle", callee="Customer") for i in range(4)]
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1, walk_rows=many), limit=3)
    n = _notes(rows)
    check("C# + SQL truncated -> the truncation note, _CS_ROWS, the SQL sentence",
          len(n) == 3 and "more exist" in n[0] and n[1:] == [_f(CS.CS_ROWS), sql_more(1, 2)], n)
    drv = _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1, meta=["csharp"])
    rows = _impact(R, drv)
    check("typed C# linking ran -> found + _CS_EMPTY_TYPED + the SQL sentence (one meta read)",
          _notes(rows) == [_found(SYM, 2) + _f(CS.CS_EMPTY_TYPED) + " " + sql_more(1, 2)]
          and len(drv.queries("_DeepMapMeta")) == 1, _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1, meta=["csharp"], narrowed=True))
    check("...narrowed -> the narrowed sentence, then the SQL sentence",
          _notes(rows) == [_found(SYM, 2) + _f(CS.CS_EMPTY_TYPED) + CS.NARROWED + " " + sql_more(1, 2)],
          _notes(rows))
    m = "ItemsCalculatedPrice"
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=1, sqlm=1), m)
    check("an all-property C# match + a same-named SQL object (props == matched - sqlm) -> _CS_PROP_EMPTY + SQL",
          _notes(rows) == [_found(m, 2) + _f(CS.PROP_EMPTY, m) + " " + sql_more(1, 2, m)], _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=1, sqlm=1,
                              walk_rows=[_row("CartView.Render", callee=m)]), m)
    check("...with rows -> [_CS_PROP_ROWS, the SQL sentence]", _notes(rows) == [_f(CS.PROP_ROWS, m), sql_more(1, 2, m)],
          _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=3, props=1, sqlm=1), m)
    check("a partial property count (1 of 2 C# matches) keeps the method wording",
          _notes(rows) == [_found(m, 3) + _f(CS.CS_EMPTY, m) + " " + sql_more(1, 3, m)], _notes(rows))
    from neo4j.exceptions import ClientError
    err = ClientError("variable-length pattern not supported (fake)")
    rows = _impact(R, _Driver(exts=["cs", "sql"], matched=2, props=0, sqlm=1, walk_errors={3: err}))
    n = _notes(rows)
    check("the 1-hop fallback carries it too: the 1-hop note, then found + _CS_EMPTY + the SQL sentence",
          len(n) == 2 and "1 hop" in n[0] and n[1] == _found(SYM, 2) + _f(CS.CS_EMPTY) + " " + sql_more(1, 2), n)
    rows = _impact(R, _Driver(exts=["sql"], sqlm=2, walk_errors={3: err}))
    n = _notes(rows)
    check("...and a SQL-only fallback answer: the 1-hop note, then found + _SQL_EMPTY",
          len(n) == 2 and "1 hop" in n[0] and n[1] == _found(SYM, 2) + sql_empty(2), n)


def gate_other_mixes(R) -> None:
    print("\n-- D. other mixes keep their notes and gain the SQL sentence --")
    rows = _impact(R, _Driver(exts=["py", "sql"], matched=3, sqlm=2))
    check("Python + SQL, zero rows -> found + the generic text + the SQL sentence",
          _notes(rows) == [_found(SYM, 3) + CS.GENERIC_TAIL + " " + sql_more(2, 3)], _notes(rows))
    rows = _impact(R, _Driver(exts=["py", "sql"], matched=2, sqlm=1,
                              walk_rows=[_row("billing.run", callee="Customer.save")]), "Customer.save")
    check("Python + SQL with method-shaped rows -> [_METHOD_CAVEAT, the SQL sentence]",
          _notes(rows) == [R._METHOD_CAVEAT, sql_more(1, 2, "save")], _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "py", "sql"], matched=3, props=0, sqlm=1))
    check("C# + Python + SQL -> the generic text, the C# mixed clause, then the SQL sentence",
          _notes(rows) == [_found(SYM, 3) + CS.GENERIC_TAIL + CS.MIXED_EMPTY + " " + sql_more(1, 3)], _notes(rows))
    rows = _impact(R, _Driver(exts=["sql"], matched=1, sqlm=1, walk_rows=[_row("report.build", callee="Customer")]))
    check("SQL only WITH rows (an engine SQL node a call reached by name) -> the SQL sentence as its own note",
          _notes(rows) == [sql_more(1, 1)], _notes(rows))
    rows = _impact(R, _Driver(exts=["sql"], matched=2, sqlm=2, walk_rows=[_row("report.build", callee="Customer")]))
    check("...two SQL matches with rows -> 'All 2 matches come from .sql files'", _notes(rows) == [sql_more(2, 2)],
          _notes(rows))


def gate_unchanged(R) -> None:
    print("\n-- E. no SQL in the answer, or the SQL count unknown -> the notes the answer always got --")
    for label, exts in (("C# only", ["cs"]), ("Python only", ["py"]), ("C# + TypeScript", ["cs", "ts"]),
                        ("no extensions (unknown language)", None)):
        for walk in ((), [_row("A.b", callee="Customer")]):
            old = _notes(_impact(R, _Driver(exts=exts, props=0, walk_rows=walk)))
            new = _notes(_impact(R, _Driver(exts=exts, props=0, sqlm=0, walk_rows=walk)))
            check(f"{label}{' with rows' if walk else ''}: sqlm 0 gives the notes of a record without it",
                  new == old and not any(".sql" in x for x in new), (old, new))
    expect = [_found(SYM, 2) + CS.GENERIC_TAIL + CS.MIXED_EMPTY]
    for label, v in (("absent", _MISSING), ("null", None), ("a bool", True), ("negative", -1), ("text", "1"),
                     ("a float", 1.0), ("more than the matches", 3), ("0 while .sql is among the extensions", 0)):
        rows = _impact(R, _Driver(exts=["cs", "sql"], props=0, sqlm=v))
        check(f"C# + SQL extensions, sqlm {label} -> today's mixed notes (unknown: never a SQL claim)",
              _notes(rows) == expect, _notes(rows))
    rows = _impact(R, _Driver(exts=None, sqlm=2))
    check("a SQL count without extensions (unknown language) -> today's generic note",
          _notes(rows) == [_found(SYM, 2) + CS.GENERIC_TAIL], _notes(rows))


def gate_module_reading(R) -> None:
    print("\n-- F. the module-qualified reading (no direct match) --")
    rows = _impact(R, _Driver(exts=[], matched=0, module=["db/schema.sql"]), f"schema.{SYM}")
    n = _notes(rows)
    check("a symbol of one .sql file -> the reading note, then found + _SQL_EMPTY",
          len(n) == 2 and "read as" in n[0] and n[1] == _found(f"schema.{SYM}", 1) + _f(SQL_EMPTY), n)
    rows = _impact(R, _Driver(exts=[], matched=0, module=["db/schema.sql", "db/v2/schema.sql"]), f"schema.{SYM}")
    n = _notes(rows)
    check("two .sql files of that module -> SQL only too (all 2 matches)",
          len(n) == 2 and n[1] == _found(f"schema.{SYM}", 2) + sql_empty(2), n)
    rows = _impact(R, _Driver(exts=[], matched=0, module=["src/Schema.cs", "db/Schema.sql"]), f"Schema.{SYM}")
    n = _notes(rows)
    check("a C# + SQL file list (how many are SQL is unknown) -> today's mixed notes",
          len(n) == 2 and n[1] == _found(f"Schema.{SYM}", 2) + CS.GENERIC_TAIL + CS.MIXED_EMPTY, n)
    four = [f"db/{i}/schema.sql" for i in range(4)]
    rows = _impact(R, _Driver(exts=[], matched=0, module=four, module_matched=9), f"schema.{SYM}")
    n = _notes(rows)
    check("a file list cut at 4 (more files may exist) -> unknown: today's generic note",
          len(n) == 2 and n[1] == _found(f"schema.{SYM}", 9) + CS.GENERIC_TAIL, n)


def gate_off_ranking(R) -> None:
    print("\n-- G. the change stays on the impact path --")
    src = "".join(inspect.getsource(getattr(R, n)) for n in ("query_impact", "_impact_rows", "_all_properties",
                                                              "_count_symbol_matches_info")
                  if callable(getattr(R, n, None)))
    check("the impact path references no ranking symbol",
          bool(src) and all(t not in src for t in ("_arm_", "_weighted_rrf", "arm_weights")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap: deepmap_impact notes on SQL matches (C# mode over the non-SQL families; no Neo4j)")
    print("=" * 72)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    with CS._env(**{k: None for k in CS._ENV_KEYS}):
        R = _load(BUILD / "retrieval.py", f"dm_retrieval_sqlnotes_{time.monotonic_ns()}")
    for gate in (gate_count_query, gate_sql_only, gate_csharp_and_sql, gate_other_mixes, gate_unchanged,
                 gate_module_reading, gate_off_ranking):
        try:
            gate(R)
        except Exception as exc:  # noqa: BLE001 - a crashing gate is RED, not a crashed suite
            import traceback
            traceback.print_exc()
            check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'}: {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
