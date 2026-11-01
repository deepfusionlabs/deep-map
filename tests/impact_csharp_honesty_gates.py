#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for C#-aware deepmap_impact notes (2026-10 C# field report).

THE DEFECT (a real C#/.NET + Angular CQRS app): `deepmap_impact CreateShoppingCartAsync` answered "no dependents"
for a method three handlers call. graphify marks every dotted C# call (`_helper.M()`, `Type.M()`, `this.M()`) as a
member call and its cross-file pass skips every member call, so a call from another file through a field, a parameter,
a local or a type name never reaches the graph. The only note said such calls are "often not resolved" and named
Python's `self.m()`; for C# they are NEVER resolved, and calls inside constructors and property getters are not read.
A false "nobody calls this" makes live code look dead.

THE FIX (build/retrieval.py, the impact path only; DeepMapRetriever.impact and every ranking function untouched):
the symbol-count query also returns the matched symbols' source-file extensions (`AS exts`, the SAME round trip);
they map to language families through edge_resolve.lang_family. When a match is C#, the notes say what this graph
cannot see for C#: the one zero-dependents note for a C#-only answer, a lower-bound note on every C# answer with
rows (even a non-method one), and a C# clause beside the generic text when the matches mix languages. When a C#
target matched, one extra timed read of :_DeepMapMeta.typed_call_langs tells whether the opt-in C# typed linking
ran for this graph (DEEPMAP_TYPED_CALLS=csharp); the wording then names what typed linking still misses. A graph
or fake that returns no extensions is answered exactly as before.

THE REVIEW ROUND (2026-10-28): the typed notes named too few gaps (review CR-1, CR-4, F2 and the virtual-dispatch
finding). They now say what the C# reader still leaves out, as measured on graphify 0.8.39 and 0.8.51: an awaited
value, a cast, an untyped lambda parameter, a static field inside a static method, dynamic, reflection, delegates,
a mediator, Razor files, test and example / sample / demo / docs callers (and the flags that link them), overloaded
and #if callers, a static constructor beside an instance one and second or later instance constructors, calls whose
overload depends on the argument types, more than 4 interface or virtual implementations, and property reads
(gate_typed_claims). The same timed meta read also returns typed_call_narrowed (review F4, sync writes it); when it
is exactly true the typed note adds one sentence (gate_typed_narrowed).

THE LIVE CHECK (2026-10-28): the property clause said "reading a property (x.{m}) is never a link" with {m} = the
queried name, so a method answer read as if the method were a property ("reading a property
(x.CreateShoppingCartAsync) is never a link"). Every C# note, untyped and typed, empty, with rows and mixed, now
states the fact without the queried name: "reading a property is never a link" (gate_property_clause).

A PROPERTY TARGET (2026-10-30): `deepmap_impact ShoppingCartDto.ItemsCalculatedPrice` (a computed property, the
:Method node graphify_extract's C# member step adds with summary "...: property") got the call-shaped note
(`_service.ItemsCalculatedPrice()`, `Helper.ItemsCalculatedPrice()`). The same count query now also counts the
matches that are PROVEN computed properties (`AS props`: a .cs :Method keyed `<file id>@<name>:L<line>` whose summary
ends ": property"); when that is every match of a C#-only answer, the note says that reading a property is never a
link, so its readers are not listed (untyped and typed alike, no narrowed tail). An unknown or partial count keeps
the method wording (gate_property_target); the predicate's conjuncts pick exactly the property nodes map_graph makes
from the captured C# trees, on both engines' captures (gate_property_producer).

Hermetic: scripted fake drivers (no Neo4j, no network, no spend), a temp DEEPMAP_HOME for the CLI relay.
Usage: python tests/impact_csharp_honesty_gates.py [--root <tree holding build/>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"          # main() points it at --root's build/ (the tree under test)

_PASS = 0
_FAIL = 0
_MISSING = object()


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
             "DEEPMAP_PROJECT_KEY", "DEEPMAP_PROJECT_GUARD", "DEEPMAP_IMPACT_TIMEOUT_S")

# ---- the note texts, written out here independently of build/retrieval.py (the contract) -----------------------
SYM = "CreateShoppingCartAsync"


def _found(symbol, matched):
    if matched:
        return (f"{symbol!r} is in the graph ({matched} matching symbol{'' if matched == 1 else 's'}) but no "
                "dependents were found.")
    return f"No dependents of {symbol!r} were found."


GENERIC_TAIL = (" That is NOT proof nothing uses it: calls through a receiver or method (obj.m(), self.m()), a "
                "trait/interface, a callback or a namespace are often not resolved by the extractor — verify with "
                "Grep or by reading the call sites before treating it as unused.")
CS_EMPTY = (" That is NOT proof nothing uses it: for C#, this graph links only calls inside the same file and bare "
            "calls to a name no other symbol shares. A call from another file through a receiver (a field, parameter "
            "or local, as in _service.{m}()) or a type name (Helper.{m}()) is never resolved, calls made inside "
            "constructors and property getters are not read, a handler reached through a mediator (MediatR Send) "
            "has no callers here, and reading a property is never a link. Grep for {m} or read the call sites "
            "before treating it as unused.")
# What the opt-in C# reader (DEEPMAP_TYPED_CALLS=csharp) still leaves unlinked, as measured on graphify 0.8.39 and
# 0.8.51 (review CR-1, CR-4, F2, cs-typed-note-virtual-dispatch): the same words in all four typed notes.
TYPED_CORE = ("calls through a receiver or a type name are linked in this graph when the source states the receiver's "
              "type, but some can still be missing, including calls on an awaited value, a cast, an untyped lambda "
              "parameter or a static field inside a static method; calls through dynamic, reflection, delegates or a "
              "mediator (MediatR Send); calls from Razor files (.razor, .cshtml) or to code in them; calls from test "
              "files, unless the graph was built with DEEPMAP_TYPED_CALLS_TESTS=1, and from example, sample, demo or "
              "docs folders, unless it was built with DEEPMAP_TYPED_CALLS_NONPROD=1; calls from an overloaded method, "
              "a method holding #if, a static constructor beside an instance one or a second or later instance "
              "constructor; and calls whose overload depends on the argument types. A call through an interface or a "
              "virtual member with more than 4 implementations reaches only the declared member, and reading a "
              "property is never a link.")
CS_EMPTY_TYPED = (" That is NOT proof nothing uses it: C# " + TYPED_CORE +
                  " Grep for {m} or read the call sites before treating it as unused.")
CS_ROWS = ("For C#, these dependents are a lower bound: only calls inside the same file and bare calls to a name no "
           "other symbol shares are linked, so callers that reach it from another file through a receiver (a field, "
           "parameter or local) or a type name, through an interface or a mediator, or from a constructor or "
           "property getter are missing, and reading a property is never a link. Grep for {m} to find the rest.")
CS_ROWS_TYPED = "For C#, these dependents are a lower bound: " + TYPED_CORE + " Grep for {m} to find the rest."
MIXED_EMPTY = (" For the C# matches, calls from another file through a receiver or a type name are never resolved, "
               "constructor and getter bodies are not read, and reading a property is never a link.")
MIXED_EMPTY_TYPED = " For the C# matches, " + TYPED_CORE
MIXED_ROWS = ("Some matches are C# symbols: callers that reach them from another file through a receiver or a type "
              "name, or from constructors and getters, are not linked, and reading a property is never a link. Treat "
              "their dependents as a lower bound and grep for {m}.")
MIXED_ROWS_TYPED = ("Some matches are C# symbols, and their dependents are a lower bound: " + TYPED_CORE +
                    " Grep for {m} to find the rest.")
# Review F4: sync stamps :_DeepMapMeta.typed_call_narrowed beside typed_call_langs; when it is exactly true the typed
# note says one sentence more (absent, null, false or a non-bool value: nothing more).
NARROWED = (" This graph was built with DEEPMAP_TYPED_CALLS_SHAPES or DEEPMAP_TYPED_CALLS_IFACE narrowed, so more C# "
            "calls are unlinked than listed here.")
TYPED_TEXTS = (CS_EMPTY_TYPED, CS_ROWS_TYPED, MIXED_EMPTY_TYPED, MIXED_ROWS_TYPED)
NEW_TEXTS = (CS_EMPTY, CS_EMPTY_TYPED, CS_ROWS, CS_ROWS_TYPED, MIXED_EMPTY, MIXED_EMPTY_TYPED, MIXED_ROWS,
             MIXED_ROWS_TYPED, NARROWED)
# Every phrase each typed note must carry (the claims the review found missing or false), checked text by text.
TYPED_MUST = ("receiver", "awaited value", "a cast", "untyped lambda parameter", "static field inside a static method",
              "dynamic", "reflection", "delegates", "MediatR Send", "Razor files (.razor, .cshtml)",
              "test files", "DEEPMAP_TYPED_CALLS_TESTS=1", "example, sample, demo or docs folders",
              "DEEPMAP_TYPED_CALLS_NONPROD=1", "overloaded method", "#if", "static constructor beside an instance one",
              "second or later instance constructor", "overload depends on the argument types",
              "interface or a virtual member with more than 4 implementations reaches only the declared member",
              "reading a property is never a link")
# The property fact, in the same words in every C# note: never with the queried name, which may be a method.
PROPERTY_CLAUSE = "reading a property is never a link"
CS_NOTE_TEXTS = (CS_EMPTY, CS_ROWS, MIXED_EMPTY, MIXED_ROWS) + TYPED_TEXTS
# A target whose every match is a proven C# computed property: what is true of a property (nobody's read of it is
# a link, by default or with the opt-in reader), with a READ of it as the example, never a call (`{m}()`).
PROP_EMPTY = (" That is NOT proof nothing uses it: {m} is a C# property, and reading a property is never a link in "
              "this graph (by default or with the opt-in C# reader), so the code that reads it, as in _service.{m} "
              "or order.{m}, is not listed here. Grep for {m} or read the code before treating it as unused.")
PROP_ROWS = ("For C#, these dependents are a lower bound: {m} is a C# property, and reading a property is never a "
             "link in this graph, so the code that reads it, as in _service.{m}, is not listed. Grep for {m} to "
             "find the rest.")
PROP_TEXTS = (PROP_EMPTY, PROP_ROWS)
PROP_SYM, PROP_M = "ShoppingCartDto.ItemsCalculatedPrice", "ItemsCalculatedPrice"


def _f(text, m=SYM):
    return text.replace("{m}", m)


# ---- a scripted fake Neo4j ---------------------------------------------------------------------------------------
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
            if d.meta_error is not None:
                raise d.meta_error
            if d.meta is _MISSING:
                return _Result([])
            rec = {"l": d.meta}
            if d.narrowed is not _MISSING:      # a record without the column: an older double, read as absent
                rec["n"] = d.narrowed
            return _Result([rec])
        if "db.relationshipTypes" in text:
            return _Result([{"types": list(d.present)}])
        if "MATCH path = (caller)" in text:
            hops = int(re.search(r"\*1\.\.(\d+)", text).group(1))
            err = d.walk_errors.get(hops)
            if err is not None:
                raise err
            m = re.search(r"LIMIT \$limit(?:\s*\+\s*(\d+))?", text)
            n = int(params["limit"]) + int(m.group(1) or 0) if m else len(d.walk_rows)
            return _Result([dict(r) for r in d.walk_rows[:n]])
        if "MATCH (caller)-[r:CALLS]->(callee)" in text:
            return _Result([dict(r) for r in d.callers_rows[: int(params.get("limit", 30))]])
        if "AS matched" in text and "$file_rx" in text:          # a module-qualified reading's count
            if d.module is None:
                return _Result([{"matched": 0, "files": []}])
            return _Result([{"matched": len(d.module), "files": list(d.module)}])
        if "AS matched" in text:                                  # the direct symbol count
            if d.matched is None:
                return _Result([])
            rec = {"matched": d.matched}
            if d.exts is not _MISSING:
                rec["exts"] = d.exts
            if d.props is not _MISSING:          # the proven-property count (absent: an older double)
                rec["props"] = d.props
            return _Result([rec])
        return _Result([])


class _Driver:
    def __init__(self, walk_rows=(), callers_rows=(), present=("CALLS",), matched=2, exts=_MISSING, meta=_MISSING,
                 meta_error=None, walk_errors=None, module=None, narrowed=_MISSING, props=_MISSING):
        self.walk_rows, self.callers_rows, self.present = list(walk_rows), list(callers_rows), present
        self.matched, self.exts, self.meta, self.meta_error = matched, exts, meta, meta_error
        self.narrowed, self.props = narrowed, props
        self.walk_errors, self.module, self.log = dict(walk_errors or {}), module, []

    def session(self, **kw):
        return _Session(self)

    def verify_connectivity(self):
        pass

    def close(self):
        pass

    def queries(self, needle):
        return [c for c, _ in self.log if needle in c]


def _row(caller, callee=f"ShoppingCartHelper.{SYM}", file=None, line=12, hop=1, via="CALLS", conf="INFERRED"):
    return {"caller": caller, "caller_type": "Method", "file": file or f"src/Handlers/{caller.split('.')[0]}.cs",
            "line": line, "confidence": conf, "callee": callee, "hop": hop, "via": via, "via_path": [via] * hop}


def _notes(rows) -> list:
    return list(getattr(rows, "notes", None) or [])


def _impact(R, drv, symbol=SYM, **kw):
    with _env(**{k: None for k in _ENV_KEYS}):
        return R.query_impact(drv, symbol, **kw)


# ---------------------------------------------------------------------------------------------------------------
def gate_count_query(R) -> None:
    print("\n-- the count query returns the matched symbols' extensions in the SAME round trip --")
    drv = _Driver(exts=["cs"])
    _impact(R, drv)
    counts = drv.queries("AS matched")
    check("exactly one count query for a direct match (no extra round trip for the extensions)", len(counts) == 1,
          str(len(counts)))
    q = " ".join((counts or [""])[0].split())
    check("the count query keeps 'RETURN count(callee) AS matched' (every fake driver keys on it)",
          "RETURN count(callee) AS matched" in q, q[:300])
    check("...and returns the extensions in that same query ('AS exts')", "AS exts" in q, q[-300:])
    check("...starting with the unchanged _CALLEE_MATCH", q.startswith(" ".join(R._CALLEE_MATCH.split())), q[:120])
    check("_count_symbol_matches is kept (a wrapper returning the count alone)",
          R._count_symbol_matches(_Driver(matched=3, exts=["cs"]).session(), SYM, None) == 3)
    langs = getattr(R, "_count_symbol_matches_langs", None)
    check("_count_symbol_matches_langs exists beside it", callable(langs))


def gate_csharp_only(R) -> None:
    print("\n-- a C#-only answer: the zero-dependents note and the lower-bound note say what C# misses --")
    rows = _impact(R, _Driver(exts=["cs"]))
    want = _found(SYM, 2) + _f(CS_EMPTY)
    check("C# only, zero rows -> exactly ONE note (the one-note rule for an empty answer)", len(_notes(rows)) == 1,
          _notes(rows))
    check("...and it is the C# text (found sentence + what this graph cannot see for C#)",
          _notes(rows) == [want], _notes(rows))
    n = " ".join(_notes(rows))
    check("...it keeps the locked words: 'in the graph', the count, 'NOT', Grep, receiver",
          "in the graph" in n and "2" in n and "NOT" in n and "grep" in n.lower() and "receiver" in n, n)
    check("...it names the receiver and type-name forms with the queried method (_service.M(), Helper.M())",
          f"_service.{SYM}()" in n and f"Helper.{SYM}()" in n, n)
    check("...constructors, property getters, MediatR and property reads", all(
        w in n for w in ("constructors", "property getters", "MediatR Send", PROPERTY_CLAUSE)), n)
    check("...and never names the queried METHOD as a property (no 'x.<method>')",
          f"x.{SYM}" not in n and "property (x." not in n, n)
    check("...no Python wording (self.m()) in the C# text", "self." not in n, n)
    check("...no em dash in the C# text", "—" not in n, n)

    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"]))
    check("C# only, rows found (method-shaped) -> the C# lower-bound note REPLACES the method caveat",
          _notes(rows) == [_f(CS_ROWS)], _notes(rows))
    check("...and the generic _METHOD_CAVEAT is not added beside it", R._METHOD_CAVEAT not in _notes(rows))
    plain = _row("CartService.Build", callee="BuildCartItems")
    rows = _impact(R, _Driver(walk_rows=[plain], exts=["cs"]), "BuildCartItems")
    check("C# only, rows found for a NON-method-shaped name -> the C# note still fires",
          _notes(rows) == [_f(CS_ROWS, "BuildCartItems")], _notes(rows))
    many = [_row(f"H{i}.Handle") for i in range(4)]
    rows = _impact(R, _Driver(walk_rows=many, exts=["cs"]), limit=3)
    n = _notes(rows)
    check("C# truncated answer -> the truncation note, then the C# lower-bound note",
          len(n) == 2 and "more exist" in n[0] and n[1] == _f(CS_ROWS), n)
    rows = _impact(R, _Driver(exts=["CS"]))
    check("an upper-case extension ('CS') is read as C#", _notes(rows) == [want], _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "", "x/makefile"]))
    check("an empty extension (no source_file) or a path without one is ignored (still C# only)",
          _notes(rows) == [want], _notes(rows))
    rows = _impact(R, _Driver(exts=["razor"]))
    check("a Razor match is C# too (edge_resolve's dotnet family)", _notes(rows) == [want], _notes(rows))
    rows = _impact(R, _Driver(matched=1, exts=["cs"]))
    check("one matching symbol -> '(1 matching symbol)'",
          _notes(rows) == [_found(SYM, 1) + _f(CS_EMPTY)], _notes(rows))


def gate_symbol_segment(R) -> None:
    print("\n-- {m} is the last name segment of the queried symbol (after . : # ::) --")
    for sym, m in ((f"ShoppingCartHelper.{SYM}", SYM), ("Shop::Cart::Find", "Find"), ("Cart#Find", "Find"),
                   (" .Find ", "Find"), ("Find", "Find")):
        rows = _impact(R, _Driver(exts=["cs"]), sym)
        n = " ".join(_notes(rows))
        check(f"{sym!r} -> grep for {m!r}", f"Grep for {m} or read" in n and f"_service.{m}()" in n, n[-260:])


def gate_typed_meta(R) -> None:
    print("\n-- the :_DeepMapMeta typed_call_langs read: only for a C# target, and it switches the wording --")
    drv = _Driver(exts=["cs"], meta=["csharp"])
    rows = _impact(R, drv)
    check("C# target -> exactly one :_DeepMapMeta read", len(drv.queries("_DeepMapMeta")) == 1,
          len(drv.queries("_DeepMapMeta")))
    q = (drv.queries("_DeepMapMeta") or [""])[0]
    check("...the ONE bounded read named by the contract, which also reads typed_call_narrowed (no extra round trip)",
          " ".join(q.split()) ==
          "MATCH (m:_DeepMapMeta) RETURN m.typed_call_langs AS l, m.typed_call_narrowed AS n LIMIT 1", q)
    check("typed linking ran for csharp, zero rows -> the typed wording (one note)",
          _notes(rows) == [_found(SYM, 2) + _f(CS_EMPTY_TYPED)], _notes(rows))
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"],
                              meta=["python", "csharp"]))
    check("typed linking ran for csharp, rows -> the typed lower-bound wording",
          _notes(rows) == [_f(CS_ROWS_TYPED)], _notes(rows))
    untyped = [_found(SYM, 2) + _f(CS_EMPTY)]
    for label, kw in (("typed_call_langs without csharp (['python'])", {"meta": ["python"]}),
                      ("typed_call_langs = [] (no typed language ran)", {"meta": []}),
                      ("typed_call_langs null (an older graph: the property is absent)", {"meta": None}),
                      ("no :_DeepMapMeta node", {}),
                      ("a non-list value", {"meta": "csharp"}),
                      ("a benign error on the read", {"meta_error": RuntimeError("no such label (fake)")})):
        rows = _impact(R, _Driver(exts=["cs"], **kw))
        check(f"{label} -> not linked: the untyped C# wording", _notes(rows) == untyped, _notes(rows))
    from neo4j.exceptions import ServiceUnavailable
    raised = None
    try:
        _impact(R, _Driver(exts=["cs"], meta_error=ServiceUnavailable("unable to connect")))
    except ServiceUnavailable as exc:
        raised = exc
    check("an instance-level failure on the read still fails LOUD (never a fabricated answer)", raised is not None)
    for label, kw in (("no extensions returned (an older fake / graph)", {}), ("a Python match", {"exts": ["py"]}),
                      ("no symbol matched", {"matched": 0, "exts": []})):
        drv = _Driver(meta=["csharp"], **kw)
        _impact(R, drv)
        check(f"{label} -> no :_DeepMapMeta read at all", not drv.queries("_DeepMapMeta"),
              len(drv.queries("_DeepMapMeta")))


def gate_typed_claims(R) -> None:
    print("\n-- the typed C# notes name what the reader still misses (review CR-1, CR-4, F2, virtual dispatch) --")
    empty = _notes(_impact(R, _Driver(exts=["cs"], meta=["csharp"])))
    rows = _notes(_impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"], meta=["csharp"])))
    m_empty = _notes(_impact(R, _Driver(exts=["cs", "ts"], meta=["csharp"])))
    m_rows = _notes(_impact(R, _Driver(walk_rows=[_row("main", callee="BuildCartItems", file="src/main.ts")],
                                       exts=["ts", "cs"], meta=["csharp"]), "BuildCartItems"))
    # the mixed zero-dependents note is the generic text (which names Python's self.m()) + the C# clause: judge the
    # clause alone
    head = _found(SYM, 2) + GENERIC_TAIL
    m_clause = " ".join(m_empty)
    check("typed mixed empty note = the unchanged generic text, then the C# clause",
          len(m_empty) == 1 and m_clause.startswith(head), m_clause[:200])
    got = {"empty": " ".join(empty), "rows": " ".join(rows), "mixed empty": m_clause[len(head):],
           "mixed rows": " ".join(m_rows)}
    sym = {"empty": SYM, "rows": SYM, "mixed empty": SYM, "mixed rows": "BuildCartItems"}
    for kind, text in got.items():
        missing = [p.replace("{m}", sym[kind]) for p in TYPED_MUST if p.replace("{m}", sym[kind]) not in text]
        check(f"typed {kind} note names every gap the reader leaves (awaited value, cast, lambda, static field, "
              f"dynamic, reflection, delegates, MediatR, Razor, test / nonprod callers and their flags, overloads, "
              f"#if, constructors, argument-dependent overloads, more than 4 implementations, property reads)",
              not missing, missing)
        check(f"typed {kind} note: no em dash, no Python self.m(), no claim that every such call is linked",
              "—" not in text and "self." not in text and "some can still be missing" in text, text[:200])
    check("typed empty note keeps the locked words: NOT proof, receiver, 'Grep for {m}'",
          "NOT proof" in got["empty"] and "receiver" in got["empty"] and f"Grep for {SYM} or read" in got["empty"],
          got["empty"][-200:])
    check("typed rows notes end with 'Grep for {m} to find the rest.'",
          got["rows"].endswith(f"Grep for {SYM} to find the rest.")
          and got["mixed rows"].endswith("Grep for BuildCartItems to find the rest."), (got["rows"][-80:],
                                                                                         got["mixed rows"][-80:]))
    check("the typed notes no longer say the interface member alone is reached (a virtual member is too)",
          all("reaches only the interface member" not in t for t in got.values()))
    for text in TYPED_TEXTS:
        check(f"the contract text {text[:40]!r}... carries every required phrase",
              all(p in text for p in TYPED_MUST), [p for p in TYPED_MUST if p not in text])


def gate_typed_narrowed(R) -> None:
    print("\n-- :_DeepMapMeta.typed_call_narrowed (review F4): read in the SAME query; true adds one sentence --")
    typed_empty = _found(SYM, 2) + _f(CS_EMPTY_TYPED)
    for label, value, want in (("absent (a record without the column: an older double)", _MISSING, typed_empty),
                               ("null (sync could not tell)", None, typed_empty),
                               ("false", False, typed_empty),
                               ("true", True, typed_empty + NARROWED),
                               ("the string 'true' (not a bool)", "true", typed_empty),
                               ("1 (an int, not a bool)", 1, typed_empty),
                               ("['x'] (a list)", ["x"], typed_empty)):
        drv = _Driver(exts=["cs"], meta=["csharp"], narrowed=value)
        rows = _impact(R, drv)
        what = "the typed note + the narrowed sentence" if value is True else "the typed note alone"
        check(f"narrowed {label}, zero rows -> exactly one note: {what}", _notes(rows) == [want], _notes(rows))
        check(f"...narrowed {label}: still exactly one :_DeepMapMeta read", len(drv.queries("_DeepMapMeta")) == 1,
              len(drv.queries("_DeepMapMeta")))
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"], meta=["csharp"],
                              narrowed=True))
    check("narrowed true, rows -> the typed lower-bound note + the narrowed sentence (one note)",
          _notes(rows) == [_f(CS_ROWS_TYPED) + NARROWED], _notes(rows))
    generic = _found(SYM, 2) + GENERIC_TAIL
    rows = _impact(R, _Driver(exts=["cs", "ts"], meta=["csharp"], narrowed=True))
    check("narrowed true, mixed, zero rows -> ONE note: generic + typed C# clause + the narrowed sentence",
          _notes(rows) == [generic + _f(MIXED_EMPTY_TYPED) + NARROWED], _notes(rows))
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs", "ts"], meta=["csharp"],
                              narrowed=True))
    check("narrowed true, mixed, rows -> the method caveat, then the typed C# sentence + the narrowed sentence",
          _notes(rows) == [R._METHOD_CAVEAT, _f(MIXED_ROWS_TYPED) + NARROWED], _notes(rows))
    untyped = [_found(SYM, 2) + _f(CS_EMPTY)]
    for label, kw in (("typed_call_langs ['python'] (C# was not linked)", {"meta": ["python"]}),
                      ("typed_call_langs []", {"meta": []}),
                      ("typed_call_langs null", {"meta": None})):
        rows = _impact(R, _Driver(exts=["cs"], narrowed=True, **kw))
        check(f"narrowed true but {label} -> the untyped C# wording, no narrowed sentence", _notes(rows) == untyped,
              _notes(rows))
    rows = _impact(R, _Driver(exts=["py"], meta=["csharp"], narrowed=True))
    check("narrowed true on a Python answer -> no C# text, no narrowed sentence, no meta read",
          _notes(rows) == [generic], _notes(rows))
    meta = getattr(R, "_typed_call_meta", None)
    check("_typed_call_meta exists (langs + narrowed from one read)", callable(meta))
    if callable(meta):
        drv = _Driver(meta=["CSharp"], narrowed=True)
        got = meta(drv.session())
        check("_typed_call_meta -> (lower-cased langs, True), one query",
              got == (frozenset({"csharp"}), True) and len(drv.log) == 1, (got, len(drv.log)))
        drv = _Driver(meta="csharp", narrowed=True)
        check("...a non-list typed_call_langs -> (None, False): unknown reads as not linked and not narrowed",
              meta(drv.session()) == (None, False), meta(drv.session()))
        check("...no meta node -> (None, False)", meta(_Driver().session()) == (None, False))
    check("_typed_call_langs is kept (the languages alone)",
          R._typed_call_langs(_Driver(meta=["csharp"], narrowed=True).session()) == frozenset({"csharp"}))


def _cs_notes(R, symbol, mode, typed, narrowed=False):
    """The C# note(s) of one answer for `symbol`: mode 'empty' / 'rows' (C# only), 'mixed empty' / 'mixed rows'
    (C# + TypeScript), or 'fallback' (the 1-hop query_callers answer, with rows)."""
    from neo4j.exceptions import ClientError
    kw = {"meta": ["csharp"]} if typed else {}
    if narrowed:
        kw["narrowed"] = True
    exts = ["cs", "ts"] if mode.startswith("mixed") else ["cs"]
    rows = [_row("CreateOrderHandler.Handle", callee=symbol)] if mode.endswith("rows") else []
    if mode == "fallback":
        crow = {"caller": "CreateOrderHandler.Handle", "caller_type": "Method", "file": "src/H.cs", "line": 9,
                "confidence": "INFERRED", "callee": symbol}
        kw.update(callers_rows=[crow], walk_errors={3: ClientError("variable-length not supported (fake)")})
    return [n for n in _notes(_impact(R, _Driver(walk_rows=rows, exts=exts, **kw), symbol)) if "C#" in n]


def gate_property_clause(R) -> None:
    print("\n-- the property fact reads right for every target: 'reading a property is never a link', never x.{m} --")
    method, prop = f"ShoppingCartHelper.{SYM}", "CartItem.ItemsCalculatedPrice"
    modes = ("empty", "rows", "mixed empty", "mixed rows", "fallback")
    seen = {}
    for typed in (False, True):
        for narrowed in ((False, True) if typed else (False,)):
            for mode in modes:
                kind = f"{'typed' if typed else 'untyped'}{' narrowed' if narrowed else ''} {mode}"
                for target in (method, prop):
                    m = target.rsplit(".", 1)[-1]
                    text = " ".join(_cs_notes(R, target, mode, typed, narrowed))
                    seen[(kind, target)] = text.replace(target, "{s}").replace(m, "{m}")
                    if target == method:
                        check(f"{kind} note on a method: the property fact once, without the method's name",
                              text.count(PROPERTY_CLAUSE) == 1 and f"x.{m}" not in text
                              and "property (x." not in text, text[-260:] or "(no C# note)")
                    else:
                        check(f"{kind} note on a property: the same property fact once",
                              text.count(PROPERTY_CLAUSE) == 1 and "property (x." not in text,
                              text[-260:] or "(no C# note)")
                check(f"{kind} note: the same words for a method and a property (only the grep name differs)",
                      seen[(kind, method)] == seen[(kind, prop)], (seen[(kind, method)][-200:],
                                                                   seen[(kind, prop)][-200:]))
    for text in CS_NOTE_TEXTS:
        check(f"the contract text {text[:40]!r}... carries the property fact once, with no {{m}} in it",
              text.count(PROPERTY_CLAUSE) == 1 and "(x.{m})" not in text and "x.{m}" not in text)
    real = [getattr(R, n, "") for n in ("_CS_EMPTY", "_CS_ROWS", "_CS_MIXED_EMPTY", "_CS_MIXED_ROWS",
                                        "_CS_EMPTY_TYPED", "_CS_ROWS_TYPED", "_CS_MIXED_EMPTY_TYPED",
                                        "_CS_MIXED_ROWS_TYPED")]
    check("every C# note template in build/retrieval.py states the property fact once and never as x.{m}",
          all(t.count(PROPERTY_CLAUSE) == 1 and "x.{m}" not in t for t in real),
          [t[:60] for t in real if t.count(PROPERTY_CLAUSE) != 1 or "x.{m}" in t])


def gate_mixed(R) -> None:
    print("\n-- mixed languages: the generic text plus a C# clause; no C#: byte-identical to today --")
    generic = _found(SYM, 2) + GENERIC_TAIL
    rows = _impact(R, _Driver(exts=["cs", "ts"]))
    check("C# + TypeScript, zero rows -> ONE note: the generic text + the C# clause",
          _notes(rows) == [generic + MIXED_EMPTY], _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "ts"], meta=["csharp"]))
    check("...typed linking ran for csharp -> the typed C# clause", _notes(rows) == [generic + _f(MIXED_EMPTY_TYPED)],
          _notes(rows))
    rows = _impact(R, _Driver(exts=["cs", "xyz"]))
    check("an extension no family knows counts as another language (mixed wording, never C#-only)",
          _notes(rows) == [generic + MIXED_EMPTY], _notes(rows))
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs", "ts"]))
    check("mixed, rows found, method-shaped -> the method caveat, then the C# sentence",
          _notes(rows) == [R._METHOD_CAVEAT, _f(MIXED_ROWS)], _notes(rows))
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs", "ts"], meta=["csharp"]))
    check("...typed linking ran for csharp -> the typed C# sentence",
          _notes(rows) == [R._METHOD_CAVEAT, _f(MIXED_ROWS_TYPED)], _notes(rows))
    plain = _row("main", callee="BuildCartItems", file="src/main.ts")
    rows = _impact(R, _Driver(walk_rows=[plain], exts=["ts", "cs"]), "BuildCartItems")
    check("mixed, rows found, NOT method-shaped -> the C# sentence alone",
          _notes(rows) == [_f(MIXED_ROWS, "BuildCartItems")], _notes(rows))

    base = _impact(R, _Driver())
    for exts in (["py"], ["ts", "py"], []):
        rows = _impact(R, _Driver(exts=exts))
        check(f"exts {exts} -> notes byte-identical to an answer with no extensions",
              _notes(rows) == _notes(base) == [generic], _notes(rows))
    py_rows = [_row("main", callee="save", file="app/main.py")]
    rows = _impact(R, _Driver(walk_rows=py_rows, exts=["py"]), "save")
    check("a Python function answer stays note-free (the normal-answer lock)", _notes(rows) == [], _notes(rows))
    meth = [_row("main", callee="Repo.save", file="app/main.py")]
    rows = _impact(R, _Driver(walk_rows=meth, exts=["py"]), "Repo.save")
    check("a Python method answer keeps the generic method caveat alone", _notes(rows) == [R._METHOD_CAVEAT],
          _notes(rows))
    for text in NEW_TEXTS:
        check(f"no em dash and no self.m() in the new text {text[:46]!r}…", "—" not in text and "self." not in text)


def gate_degrade_paths(R) -> None:
    print("\n-- the 1-hop fallback, the memory-reduced walk and the module-qualified reading carry the C# notes --")
    from neo4j.exceptions import ClientError, TransientError
    err = ClientError("variable-length pattern not supported (fake)")
    rows = _impact(R, _Driver(exts=["cs"], walk_errors={3: err}))
    n = _notes(rows)
    check("fallback (unsupported multi-hop), zero rows -> the 1-hop note, then the C# zero-dependents note",
          len(n) == 2 and "1 hop" in n[0] and n[1] == _found(SYM, 2) + _f(CS_EMPTY), n)
    callers = [{"caller": "CreateOrderHandler.Handle", "caller_type": "Method", "file": "src/H.cs", "line": 9,
                "confidence": "INFERRED", "callee": f"ShoppingCartHelper.{SYM}"}]
    rows = _impact(R, _Driver(exts=["cs"], walk_errors={3: err}, callers_rows=callers, meta=["csharp"]))
    n = _notes(rows)
    check("fallback with rows (typed linking ran) -> the 1-hop note, then the typed C# lower-bound note",
          len(n) == 2 and "1 hop" in n[0] and n[1] == _f(CS_ROWS_TYPED), n)
    rows = _impact(R, _Driver(exts=["cs"], walk_errors={3: err}, callers_rows=callers, meta=["csharp"],
                              narrowed=True))
    n = _notes(rows)
    check("...the same fallback on a narrowed graph -> the typed note carries the narrowed sentence",
          len(n) == 2 and "1 hop" in n[0] and n[1] == _f(CS_ROWS_TYPED) + NARROWED, n)
    oom = TransientError("dbms.memory.transaction.total.max threshold reached (fake)")
    oom._neo4j_code = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"], walk_errors={3: oom}))
    n = _notes(rows)
    check("memory-reduced 1-hop walk -> the reduced-depth note, then the C# lower-bound note",
          len(n) == 2 and "transaction memory" in n[0] and n[1] == _f(CS_ROWS), n)
    rows = _impact(R, _Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"], walk_errors={3: oom},
                              meta=["csharp"], narrowed=True))
    n = _notes(rows)
    check("...the memory-reduced walk on a narrowed typed graph -> the typed note + the narrowed sentence",
          len(n) == 2 and "transaction memory" in n[0] and n[1] == _f(CS_ROWS_TYPED) + NARROWED, n)
    drv = _Driver(matched=0, exts=[], module=["src/Cart/Cart.cs"])
    rows = _impact(R, drv, f"Cart.{SYM}")
    n = _notes(rows)
    check("module-qualified reading of a C# file, zero rows -> the reading note, then the C# note",
          len(n) == 2 and "read as" in n[0] and n[1] == _found(f"Cart.{SYM}", 1) + _f(CS_EMPTY), n)
    check("...and the typed read ran for that C# target", len(drv.queries("_DeepMapMeta")) == 1)
    rows = _impact(R, _Driver(matched=0, exts=[], module=["build/provision.py"]), "provision.is_bolt_up")
    n = _notes(rows)
    check("a module-qualified Python reading keeps today's notes (no C# text)",
          len(n) == 2 and "read as" in n[0] and n[1] == _found("provision.is_bolt_up", 1) + GENERIC_TAIL, n)


def gate_timed(R) -> None:
    print("\n-- every query on a real driver session is timed; a C# target adds exactly one query --")
    from neo4j import Query, Session

    class _RealishSession(Session):
        def __init__(self, log, exts):
            self.log, self.exts = log, exts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, query, parameters=None, **kw):
            self.log.append(query)
            text = query.text if isinstance(query, Query) else query
            if "AS matched" in text:
                return _Result([{"matched": 1} if self.exts is None else {"matched": 1, "exts": self.exts}])
            return _Result([])

        def close(self):
            pass

        def __del__(self):
            pass

    class _RealishDriver:
        def __init__(self, exts):
            self.log, self.exts = [], exts

        def session(self, **kw):
            return _RealishSession(self.log, self.exts)

    for exts, want in ((None, 3), (["py"], 3), (["cs"], 4)):
        drv = _RealishDriver(exts)
        _impact(R, drv, "Svc.save")
        check(f"exts {exts}: {want} queries (count, [meta,] types, walk), every one a timed neo4j.Query",
              len(drv.log) == want and all(isinstance(q, Query) and q.timeout for q in drv.log),
              str([getattr(q, "text", q)[:40] for q in drv.log]))
        metas = [q.text for q in drv.log if isinstance(q, Query) and "_DeepMapMeta" in q.text]
        what = "one meta query reading typed_call_langs AND typed_call_narrowed" if want == 4 else "no meta query"
        check(f"exts {exts}: {what}",
              (len(metas) == 1 and "typed_call_langs" in metas[0] and "typed_call_narrowed" in metas[0])
              if want == 4 else not metas, metas)


def gate_family_source(R) -> None:
    print("\n-- the language families come from edge_resolve.lang_family; the fallback mirror is drift-locked --")
    import edge_resolve as ER
    real = {e.lstrip(".").lower() for e in ER._FAMILY_EXTS["dotnet"]}
    mirror = getattr(R, "_DOTNET_EXTS_MIRROR", None)
    check("the dotnet mirror in retrieval equals edge_resolve._FAMILY_EXTS['dotnet'] (drift lock)",
          mirror is not None and set(mirror) == real, str(set(mirror or ()) ^ real))
    saved = sys.modules.get("edge_resolve")
    sys.modules["edge_resolve"] = None          # `import edge_resolve` now raises ImportError
    try:
        rows = _impact(R, _Driver(exts=["cs"]))
        mixed = _impact(R, _Driver(exts=["cs", "ts"]))
    finally:
        if saved is None:
            sys.modules.pop("edge_resolve", None)
        else:
            sys.modules["edge_resolve"] = saved
    check("edge_resolve unimportable -> a .cs match is still C# (the mirror)",
          _notes(rows) == [_found(SYM, 2) + _f(CS_EMPTY)], _notes(rows))
    check("...and another extension still reads as another language",
          _notes(mixed) == [_found(SYM, 2) + GENERIC_TAIL + MIXED_EMPTY], _notes(mixed))


def gate_mcp_relay(R) -> None:
    print("\n-- MCP: deepmap_impact relays the C# notes as trailing {note} entries --")
    with _env(**{k: None for k in _ENV_KEYS}):
        S = _load("deepmap_search_server", f"dm_search_server_cs_honesty_{time.monotonic_ns()}")
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)
    S._get_retriever = lambda: ret
    S._touch_last_used = lambda: None
    ret.driver = _Driver(exts=["cs"])
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact(SYM)
    check("C# only, zero rows -> exactly [{note: the C# text}]", out == [{"note": _found(SYM, 2) + _f(CS_EMPTY)}],
          str(out))
    row = _row("CreateOrderHandler.Handle")
    ret.driver = _Driver(walk_rows=[row], exts=["cs"])
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact(SYM)
    check("C# rows -> the row first, then {note: the C# lower-bound text}",
          out == [row, {"note": _f(CS_ROWS)}], str(out))
    ret.driver = _Driver(walk_rows=[row], exts=["cs", "ts"], meta=["csharp"])
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact(SYM)
    check("mixed + typed -> the method caveat and the typed C# sentence, in order",
          out == [row, {"note": R._METHOD_CAVEAT}, {"note": _f(MIXED_ROWS_TYPED)}], str(out))
    ret.driver = _Driver(exts=["cs"], meta=["csharp"], narrowed=True)
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact(SYM)
    check("C# only + typed + narrowed, zero rows -> exactly [{note: the typed text + the narrowed sentence}]",
          out == [{"note": _found(SYM, 2) + _f(CS_EMPTY_TYPED) + NARROWED}], str(out))


def gate_cli_relay(R) -> None:
    print("\n-- CLI: `deepmap impact` prints the C# notes --")
    with tempfile.TemporaryDirectory(prefix="dm_cs_honesty_cli_") as td:
        home = Path(td) / "home"
        home.mkdir()
        proj = Path(td) / "proj"
        proj.mkdir()
        scrub = {k: None for k in _ENV_KEYS + ("NEO4J_URI", "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_LEARNING")}
        with _env(DEEPMAP_HOME=str(home), **scrub):
            C = _load("deepmap_cli", f"dm_cli_cs_honesty_{time.monotonic_ns()}")

            def run(drv, *argv):
                saved_argv, saved_drv, saved_load = sys.argv[:], C._driver, C._load
                orig_load = C._load

                def _load_stub(name):
                    if name == "retrieval":
                        return R
                    if name == "port_registry":
                        import types
                        return types.SimpleNamespace(touch_last_used=lambda *a, **k: None)
                    return orig_load(name)
                out, err = io.StringIO(), io.StringIO()
                prev = os.getcwd()
                try:
                    C._driver, C._load = (lambda: drv), _load_stub
                    sys.argv = ["deepmap", "impact", *argv]
                    os.chdir(str(proj))
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        rc = C.main()
                finally:
                    os.chdir(prev)
                    sys.argv, C._driver, C._load = saved_argv, saved_drv, saved_load
                return rc, out.getvalue(), err.getvalue()

            rc, out, err = run(_Driver(exts=["cs"]), SYM)
            check("C# only, zero rows -> exit 0 and the C# note printed on its own line",
                  rc == 0 and (_found(SYM, 2) + _f(CS_EMPTY)) in out.splitlines(), (out + err)[-500:])
            rc, out, err = run(_Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"]), SYM)
            check("C# rows -> the row, then '  Note: <the C# lower-bound text>'",
                  rc == 0 and "CreateOrderHandler.Handle" in out and f"  Note: {_f(CS_ROWS)}" in out.splitlines(),
                  (out + err)[-500:])
            rc, out, err = run(_Driver(walk_rows=[_row("CreateOrderHandler.Handle")], exts=["cs"], meta=["csharp"],
                                       narrowed=True), SYM)
            check("typed + narrowed C# rows -> '  Note: <the typed lower-bound text + the narrowed sentence>'",
                  rc == 0 and f"  Note: {_f(CS_ROWS_TYPED) + NARROWED}" in out.splitlines(), (out + err)[-700:])
            rc, out, err = run(_Driver(walk_rows=[_row("main", callee="save", file="app/main.py")], exts=["py"]),
                               "save")
            check("a Python function answer prints no note (unchanged)", rc == 0 and "Note:" not in out, out[-300:])


def gate_property_target(R) -> None:
    print("\n-- a C# computed-property target: the note says a read is never a link, never names a call --")
    from neo4j.exceptions import ClientError
    drv = _Driver(exts=["cs"], props=2)
    _impact(R, drv, PROP_SYM)
    counts = drv.queries("AS matched")
    q = " ".join((counts or [""])[0].split())
    check("the property count rides in the ONE count query ('AS props', beside 'AS matched' and 'AS exts')",
          len(counts) == 1 and "AS props" in q and "RETURN count(callee) AS matched" in q and "AS exts" in q,
          q[-400:])
    conj = ("callee:Method", "callee.body_summary ENDS WITH ': property'",
            "toLower(coalesce(callee.source_file, '')) ENDS WITH '.cs'",
            "callee.key ENDS WITH ('@' + callee.name + ':L' + toString(callee.start_line))")
    check("...counting only :Method nodes of a .cs file whose summary ends ': property' and whose key is "
          "<file id>@<their name>:L<their start line> (the C# member step's _supp_key)",
          all(c in q for c in conj), [c for c in conj if c not in q])

    want = _found(PROP_SYM, 2) + _f(PROP_EMPTY, PROP_M)
    rows = _impact(R, _Driver(exts=["cs"], props=2), PROP_SYM)
    pos = _notes(rows) == [want]
    n = " ".join(_notes(rows))
    check("every match a proven C# property, zero rows -> exactly ONE note: the property note", pos, _notes(rows))
    check("...it names no call: no '{m}()', no 'Helper.', no 'call sites'; a READ is its example (_service.{m})",
          bool(n) and f"{PROP_M}(" not in n and "Helper." not in n and "call sites" not in n
          and f"as in _service.{PROP_M} or order.{PROP_M}," in n and n.count(PROPERTY_CLAUSE) == 1, n)
    real = (getattr(R, "_CS_PROP_EMPTY", None), getattr(R, "_CS_PROP_ROWS", None))
    check("build/retrieval.py's property templates are the contract texts, each with the property fact once, no "
          "call ('{m}('), no em dash, no self.m()",
          real == PROP_TEXTS and all(t.count(PROPERTY_CLAUSE) == 1 and "{m}(" not in t and "—" not in t
                                     and "self." not in t for t in PROP_TEXTS), [str(t)[:60] for t in real])
    for kw, what in (({"meta": ["csharp"]}, "typed linking ran"),
                     ({"meta": ["csharp"], "narrowed": True}, "typed linking ran narrowed")):
        rows = _impact(R, _Driver(exts=["cs"], props=2, **kw), PROP_SYM)
        check(f"{what}: the same property note (the reader never links a read), no typed call list, no narrowed "
              f"sentence", _notes(rows) == [want], _notes(rows))
    prow = _row("CartView.Render", callee=PROP_SYM, file="src/Views/CartView.cs")
    rows = _impact(R, _Driver(walk_rows=[prow], exts=["cs"], matched=1, props=1), PROP_SYM)
    check("a proven property WITH rows -> the property lower-bound note alone (no method caveat, no call list)",
          _notes(rows) == [_f(PROP_ROWS, PROP_M)], _notes(rows))
    crow = {"caller": "CartView.Render", "caller_type": "Method", "file": "src/Views/CartView.cs", "line": 9,
            "confidence": "INFERRED", "callee": PROP_SYM}
    rows = _impact(R, _Driver(callers_rows=[crow], exts=["cs"], props=2,
                              walk_errors={3: ClientError("variable-length not supported (fake)")}), PROP_SYM)
    n = _notes(rows)
    check("the 1-hop fallback keeps the property note after its own", len(n) == 2 and "1 hop" in n[0]
          and n[1] == _f(PROP_ROWS, PROP_M), n)

    # Not proven every match a property: today's wording, beside the proven count that switches it (pos).
    old = [_found(PROP_SYM, 2) + _f(CS_EMPTY, PROP_M)]
    for props, what in ((1, "1 of the 2 matches (a property in one type, a method in another)"), (0, "0"),
                        (3, "3, more than matched"), (None, "null"), (True, "a bool"), ("2", "a string"),
                        (-2, "negative"), (_MISSING, "absent (an older double)")):
        rows = _impact(R, _Driver(exts=["cs"], props=props), PROP_SYM)
        check(f"props {what} -> today's C# note (only a count equal to the matches switches it)",
              pos and _notes(rows) == old, _notes(rows))
    generic = _found(PROP_SYM, 2) + GENERIC_TAIL
    rows = _impact(R, _Driver(exts=["cs", "ts"], props=2), PROP_SYM)
    check("C# + TypeScript with every match counted -> the mixed wording (the property notes are for C# alone)",
          pos and _notes(rows) == [generic + MIXED_EMPTY], _notes(rows))
    rows = _impact(R, _Driver(exts=["py"], props=2), PROP_SYM)
    check("a Python answer with a count -> the generic note, unchanged", pos and _notes(rows) == [generic],
          _notes(rows))
    rows = _impact(R, _Driver(exts=["cs"], props=0, matched=0, module=["src/Dto/ShoppingCartDto.cs"]), PROP_SYM)
    n = _notes(rows)
    check("a module-qualified reading (its count holds no property count) -> today's C# note, never the property one",
          pos and any(x.endswith(_f(CS_EMPTY, PROP_M)) for x in n) and not any("is a C# property" in x for x in n), n)

    with _env(**{k: None for k in _ENV_KEYS}):
        S = _load("deepmap_search_server", f"dm_search_server_cs_prop_{time.monotonic_ns()}")
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)
    S._get_retriever = lambda: ret
    S._touch_last_used = lambda: None
    ret.driver = _Driver(exts=["cs"], props=2)
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact(PROP_SYM)
    check("MCP: deepmap_impact on the property relays exactly [{note: the property note}]",
          out == [{"note": want}], str(out))


def _props_pick(q: str):
    """retrieval._COUNT_PROPS's conjuncts evaluated on a mapped node, or None when the query text does not hold
    all four (so the mirror never judges a predicate the product does not run)."""
    q = " ".join(str(q or "").split())
    if not all(c in q for c in ("callee:Method", "callee.body_summary ENDS WITH ': property'",
                                "toLower(coalesce(callee.source_file, '')) ENDS WITH '.cs'",
                                "callee.key ENDS WITH ('@' + callee.name + ':L' + toString(callee.start_line))")):
        return None

    def pick(n) -> bool:
        return (n.get("label") == "Method" and str(n.get("body_summary") or "").endswith(": property")
                and str(n.get("source_file") or "").lower().endswith(".cs")
                and str(n.get("key") or "").endswith(f"@{n.get('name')}:L{n.get('start_line')}"))
    return pick


def gate_property_producer(R) -> None:
    print("\n-- _COUNT_PROPS picks exactly the C# member step's property nodes (map_graph on both engines' captures) --")
    pick = _props_pick(getattr(R, "_COUNT_PROPS", ""))
    saved_path = list(sys.path)
    try:
        sys.path.insert(0, str(REPO / "tests"))
        import csharp_members_gates as CM      # its captured C# trees and its own map_graph helper (read-only)
    finally:
        sys.path[:] = saved_path
    try:
        root = CM.src_root()
        want = {(name, sf, line) for _lbl, name, sf, line, _s in CM._members()
                if len(name.split(".")) == 2 and name.split(".")[0] != name.split(".")[1]}
        ctors = {(name, sf, line) for _lbl, name, sf, line, _s in CM._members()} - want
        for ver, b in CM.BUCKETS.items():
            graph = CM.graph_of(b)
            if graph is None:
                check(f"{ver}: the captured C# tree {CM.fixture_path(b).name} exists", False)
                continue
            nodes = CM.mapped(CM.GX, graph, root, {})[0]
            got = {(n["name"], n["source_file"], n["start_line"]) for n in nodes if pick and pick(n)}
            check(f"{ver}: the count's predicate picks exactly the {len(want)} computed properties (none of the "
                  f"{len(ctors)} constructors, no engine node)", pick is not None and got == want and len(want) >= 10,
                  {"missing": sorted(want - got), "extra": sorted(got - want)})
            eng = [n for n in nodes if n.get("label") == "Method" and "@" not in str(n.get("key"))
                   and str(n.get("source_file")).lower().endswith(".cs")]
            forged = [dict(n, body_summary=f"{n['body_summary']}: property") for n in eng]
            check(f"{ver}: an ENGINE C# method whose doc summary ended ': property' is not picked (the key test)",
                  pick is not None and bool(eng) and not any(pick(n) for n in forged), len(eng))
            props = [n for n in nodes if pick and pick(n)]
            rewritten = [dict(n, body_summary="Sums the line totals of the cart.") for n in props]
            check(f"{ver}: a property whose summary enrich rewrote is not proven one (today's wording, never the "
                  f"reverse)", pick is not None and bool(props) and not any(pick(n) for n in rewritten), len(props))
    finally:
        CM.cleanup()


def main() -> int:
    global BUILD
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = sys.argv[1:]
    if "--root" in argv:
        i = argv.index("--root")
        tree = argv[i + 1] if i + 1 < len(argv) else ""
        if not tree or not (Path(tree) / "build").is_dir():
            print(f"usage: python tests/impact_csharp_honesty_gates.py [--root <tree holding build/>] (got {tree!r})",
                  file=sys.stderr)
            return 2
        BUILD = Path(tree).resolve() / "build"
    print("=" * 72)
    print("DeepMap — C#-aware impact notes (exts in the count query, typed_call_langs, MCP + CLI relay; no Neo4j)"
          f" (build: {BUILD})")
    print("=" * 72)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    with _env(**{k: None for k in _ENV_KEYS}):
        R = _load("retrieval", f"dm_retrieval_cs_honesty_{time.monotonic_ns()}")
    for gate in (gate_count_query, gate_csharp_only, gate_symbol_segment, gate_typed_meta, gate_typed_claims,
                 gate_typed_narrowed, gate_property_clause, gate_mixed, gate_degrade_paths, gate_timed,
                 gate_family_source, gate_mcp_relay, gate_cli_relay, gate_property_target,
                 gate_property_producer):
        try:
            gate(R)
        except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crashed suite
            import traceback
            traceback.print_exc()
            check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
