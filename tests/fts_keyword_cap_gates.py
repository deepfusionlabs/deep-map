#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the BM25 query builder: Lucene keywords + the EXPANDED-clause bound (v0.28.0 S2 + F4).

BUG 1 (3 independent live reports, all 2-skeptic confirmed): `_build_fts_query` escaped Lucene's special
CHARACTERS but not the classic-QueryParser's reserved WORDS. A standalone uppercase AND / NOT ("read AND
write lock", "which converters are NOT used by default") became `AND~2` / `NOT~2`, Lucene threw a
ParseException, and the whole BM25 arm was silently dropped (5/49 benchmark prompts lost it). Fix: a
standalone reserved word is emitted lower-case (the index analyzer lower-cases anyway).

BUG 2 (round-3 review, reproduced live and READ-ONLY on this repo's own Neo4j 5.26, bolt 7689): the round-1
"cap at 1024 terms" never protected anything. Lucene 9 counts the REWRITTEN leaf clauses, not the top-level
terms, and Neo4j's fulltext query is a MultiFieldQueryParser over the index's 4 properties (name,
body_summary, docstring, rationale):
  * a fuzzy `term~2` becomes one FuzzyQuery per field, each rewritten to <= 50 expansions (maxExpansions).
    DeepMap only ever writes `name` + `body_summary` (docstring / rationale stay empty; an empty field's
    fuzzy query rewrites to nothing: 1024 distinct nonsense fuzzy terms still parse), so one fuzzy term
    costs <= 2 x 50 = 100 leaves. Measured: the/and/get/run/for/set cost 44-56 leaves each; gate/node
    24-32; long words ~0-4.
  * an exact term costs 4 leaves (a TermQuery per field, populated or not) per analyzer token (measured:
    256 distinct exact terms parse, 257 fail).
  * first TooManyClauses, distinct words, current builder: 3-letter words at 23 terms, 4-letter at 45,
    3-5 letters at 40, 5-7 at 173 — every size far below the 2000-char MCP/CLI query cap.
When it fires, `_is_fts_parse_error` swallows it and the arm returns 0 rows with no note.

THE FIX (F4 — the builder only; `_arm_fulltext` and the ranking path stay byte-frozen): a query of up to
T = 10 terms is emitted byte-identically (10 x 100 = 1000 <= 1024, and >= 2x under the worst class measured
on this graph). Above T the query is de-duplicated and the TAIL becomes EXACT terms; as many leading terms
stay fuzzy as the worst-case bound allows, and whatever does not fit the budget is dropped — so the
expanded clause count can never exceed 1024 and the BM25 arm is never silently lost.

The fake Lucene below counts expanded leaves under the WORST case (every populated field saturates at 50
expansions for every fuzzy term) with its own model of the standard analyzer.
"""
from __future__ import annotations

import importlib.util
import json
import random
import re
import sys
import unicodedata
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
    spec = importlib.util.spec_from_file_location(f"dm_{name}_fts", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _old_builder(query: str) -> str:
    """The pre-fix builder, frozen here verbatim as the byte-identity reference."""
    terms = []
    for t in query.split():
        if len(t) <= 2 or not any(ch.isalnum() for ch in t):
            continue
        esc = "".join("\\" + c if c in _SPECIAL else c for c in t)
        terms.append(f"{esc}~2")
    return " ".join(terms)


_KEYWORDS = {"AND", "OR", "NOT", "TO"}
_MAX_CLAUSES = 1024          # Lucene IndexSearcher.maxClauseCount (the live error: "maxClauseCount is set to 1024")
_FIELDS = 4                  # deepmap_code_fts: name, body_summary, docstring, rationale
_POPULATED_FIELDS = 2        # DeepMap writes name + body_summary only
_MAX_EXPANSIONS = 50         # FuzzyQuery.defaultMaxExpansions
T = 10                       # the byte-identity threshold F4 fixes


def _unescape(term: str) -> str:
    out, i = [], 0
    while i < len(term):
        if term[i] == "\\" and i + 1 < len(term):
            out.append(term[i + 1])
            i += 2
        else:
            out.append(term[i])
            i += 1
    return "".join(out)


# Line_Break=SA ("complex context") blocks below U+2E80: Thai, Lao, Myanmar, Khmer, Tai Le / New Tai Lue / Tai Tham.
_SA_BLOCKS = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x1950, 0x19FF), (0x1A20, 0x1AAF))
_MAX_TOKEN_LEN = 255         # StandardAnalyzer.DEFAULT_MAX_TOKEN_LENGTH: a longer token is split


def _std_tokens(text: str) -> list:
    """An independent model of the standard analyzer's tokenizer (UAX#29 + Lucene's StandardTokenizer):
    ideographic / kana / hangul characters are one token each, symbols (emoji) are one token each, a run of
    complex-context (SA) letters is a token of its own (UAX#29 never makes them ALetter), a No-category
    character (superscript / circled digit — Word_Break=Other) is a break, word-character runs joined by
    `_ . : ' ,` form one token, and a token longer than 255 characters is split."""
    out, rest = [], []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch.isalnum() and ord(ch) >= 0x2E80:
            out.append(ch)
            rest.append(" ")
        elif cat == "So":
            out.append(ch)
            rest.append(" ")
        elif cat == "No":
            rest.append(" ")
        elif cat[0] == "L" and any(lo <= ord(ch) <= hi for lo, hi in _SA_BLOCKS):
            rest.append("\x01")
        else:
            rest.append(ch)
    s = "".join(rest)
    for word in re.findall("\x01+", s) + re.findall(r"\w+(?:[.:',]\w+)*", s):
        out += [word[i:i + _MAX_TOKEN_LEN] for i in range(0, len(word), _MAX_TOKEN_LEN)]
    return out


def _nonascii_utf8(term: str) -> int:
    return sum(len(ch.encode("utf-8", "surrogatepass")) for ch in term if ord(ch) > 0x7F)


def _unescaped_special(term: str) -> bool:
    i = 0
    while i < len(term):
        c = term[i]
        if c == "\\":
            i += 2
            continue
        if c in _SPECIAL and not (c == "~" and term[i:] == "~2"):
            return True
        i += 1
    return False


def _lucene_leaves(fts: str) -> int:
    """Expanded leaf clauses of the builder's string under the WORST case."""
    leaves = 0
    for t in fts.split():
        if t.endswith("~2") and not t.endswith("\\~2"):
            leaves += _POPULATED_FIELDS * _MAX_EXPANSIONS
        else:
            leaves += _FIELDS * max(1, len(_std_tokens(_unescape(t))))
    return leaves


# One fuzzy term that sinks the query on its own (measured READ-ONLY on bolt 7689, round 4): with ANY non-ASCII
# character, TooComplexToDeterminizeException at ~810-1010 UTF-8 bytes of the whole term (worst: 225 random
# mixed 2/3/4-byte characters ~811 bytes; 271 random 3-byte = 813; 213 4-byte = 852; one CJK/é/emoji among
# ~1005 ASCII = 1008); pure ASCII: OutOfMemoryError at 16859 random characters (6000 parse).
_TOO_COMPLEX_MIN_BYTES = 810
_ASCII_FUZZY_OOM_MIN = 16859


def _fuzzy_sinks(raw: str) -> "str | None":
    if raw.isascii():
        return f"OutOfMemoryError: a {len(raw)}-char fuzzy term" if len(raw) >= _ASCII_FUZZY_OOM_MIN else None
    n = len(raw.encode("utf-8", "surrogatepass"))
    return f"TooComplexToDeterminizeException: a {n}-byte fuzzy term" if n >= _TOO_COMPLEX_MIN_BYTES else None


def _lucene_would_reject(fts: str) -> "str | None":
    """The classic-parser failures the live runs hit, modelled: a reserved word as a term (`AND~2` ->
    ParseException; a bare `AND` is an operator), an unescaped special character, one overlong fuzzy term
    (TooComplexToDeterminize / heap), more than maxClauseCount top-level clauses, and more than maxClauseCount
    EXPANDED leaf clauses (TooManyClauses)."""
    terms = fts.split()
    for t in terms:
        base = t[:-2] if t.endswith("~2") else t
        if base in _KEYWORDS:
            return f"ParseException: Encountered <{base}> as a term"
        if _unescaped_special(t):
            return f"ParseException: unescaped special character in {t!r}"
        if t.endswith("~2") and not t.endswith("\\~2"):
            sunk = _fuzzy_sinks(_unescape(base))
            if sunk:
                return sunk
    if len(terms) > _MAX_CLAUSES:
        return f"TooManyClauses: maxClauseCount is set to {_MAX_CLAUSES} (top level)"
    n = _lucene_leaves(fts)
    if n > _MAX_CLAUSES:
        return f"TooManyClauses: maxClauseCount is set to {_MAX_CLAUSES} ({n} expanded clauses)"
    return None


def _terms(fts: str):
    """[(raw_term, fuzzy?)] of a builder output."""
    out = []
    for t in fts.split():
        fz = t.endswith("~2") and not t.endswith("\\~2")
        out.append((_unescape(t[:-2] if fz else t), fz))
    return out


# --------------------------------------------------------------------------------------------------------

def gate_keywords(R) -> None:
    print("\n-- reserved words become plain (lower-case) terms --")
    for q, want in (("read AND write lock", "read~2 and~2 write~2 lock~2"),
                    ("files NOT found", "files~2 not~2 found~2"),
                    ("AND", "and~2"),
                    ("NOT", "not~2"),
                    ("which converters are NOT used by default",
                     "which~2 converters~2 are~2 not~2 used~2 default~2"),
                    ("AND OR NOT AND OR NOT", "and~2 not~2 and~2 not~2")):
        got = R._build_fts_query(q)
        check(f"{q!r} -> {want!r}", got == want, repr(got))
        check(f"{q!r} is parseable (no reserved word as a term)", _lucene_would_reject(got) is None,
              str(_lucene_would_reject(got)))
    print("\n-- words that only CONTAIN a reserved word are untouched (they already parsed) --")
    for q in ("Op.AND", "NOT_FOUND", "AND,", "ANDROID NOTE TODO", "and or not", "Not And"):
        check(f"{q!r} byte-identical to the old builder", R._build_fts_query(q) == _old_builder(q),
              f"{R._build_fts_query(q)!r} vs {_old_builder(q)!r}")
    long_kw = " ".join(["alpha", "AND", "beta", "NOT", "gamma"] + [f"w{i:03d}" for i in range(20)])
    got = R._build_fts_query(long_kw)
    check("above T a reserved word is still lower-cased (a bare AND would be an operator)",
          _lucene_would_reject(got) is None and " AND" not in f" {got}" and " NOT" not in f" {got}", got[:120])


def gate_byte_identity(R) -> None:
    print(f"\n-- any query of <= T={T} terms and no reserved word is byte-identical (oracle preserved) --")
    rng = random.Random(20260924)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.,;#$%@'+-&|!(){}[]^\"~*?:\\/é日"
    mismatches, tried = [], 0
    for _ in range(6000):
        words = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
                 for _ in range(rng.randint(0, 25))]
        q = rng.choice([" ", "  ", "\t", "\n"]).join(words)
        if any(w in _KEYWORDS for w in q.split()) or len(_old_builder(q).split()) > T:
            continue
        tried += 1
        if R._build_fts_query(q) != _old_builder(q):
            mismatches.append(q)
    check(f"{tried} seeded random / code-shaped queries of <= {T} terms: new == old", not mismatches and tried > 1500,
          repr(mismatches[:3]))
    q = " ".join(["order"] * T)
    check(f"exactly T={T} terms (repeats included) is byte-identical — no dedupe at or below T",
          R._build_fts_query(q) == _old_builder(q), R._build_fts_query(q))
    q = " ".join(f"word{i}" for i in range(T))
    check(f"exactly T={T} distinct terms is byte-identical (all fuzzy)", R._build_fts_query(q) == _old_builder(q))
    oracle_qs = []
    for f in [REPO / "tests" / "stage0_real_oracle.json", *sorted((REPO / "tests" / "oracles").glob("*.json"))]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("queries"), dict):
            oracle_qs += list(d["queries"])
        elif isinstance(d, list) and all(isinstance(x, str) for x in d):
            oracle_qs += d
    oracle_qs += ["compute reciprocal rank fusion across arms", "parse_config retry_backoff",
                  "where is the sync lock acquired", "how does foo(bar) work", "Class#method impact"]
    check(f"every committed STAGE-0 oracle query ({len(oracle_qs)}) has <= T terms and is byte-identical",
          len(oracle_qs) >= 10 and all(len(_old_builder(q).split()) <= T and R._build_fts_query(q) == _old_builder(q)
                                        for q in oracle_qs),
          repr([q for q in oracle_qs if R._build_fts_query(q) != _old_builder(q)][:3]))


def gate_expanded_bound(R) -> None:
    print("\n-- the EXPANDED clause count never exceeds maxClauseCount (the arm is never silently dropped) --")
    # The live repro shapes (measured READ-ONLY on bolt 7689 with the round-1 builder).
    three = ["the", "run", "and", "arm", "env", "mcp", "for", "not", "cli", "per", "git", "one", "get", "set",
             "add", "use", "new", "str", "int", "map", "key", "dir", "log", "row", "out", "err", "ids", "sum",
             "max", "min"]
    idents = [f"{a}_{b}" for a in ("load", "sync", "push", "read", "make", "check", "build", "parse", "write",
                                   "embed", "prune", "index")
              for b in ("graph", "nodes", "edges", "state", "query", "config", "lock", "cache", "rows", "arms")]
    shapes = {
        "23 distinct 3-letter words (first live failure)": " ".join(three[:23]),
        "30 distinct 3-letter words": " ".join(three),
        "120 distinct identifiers (the review's repro size)": " ".join(idents[:120]),
        "11 short words": " ".join(three[:11]),
        "2000 chars of distinct words (the MCP/CLI cap)": " ".join(f"w{i:03d}" for i in range(500))[:2000],
        "5000 distinct words (uncapped direct API)": " ".join(f"term{i}" for i in range(5000)),
        "300 dotted paths": " ".join(f"pkg.mod{i}.Class{i}.method{i}" for i in range(300)),
        "a pasted stack trace": " ".join(f'File "/srv/app/mod{i}.py", line {i * 7}, in handler_{i}'
                                         for i in range(60)),
        "CJK blobs": " ".join("日本語のテキスト" * 3 + str(i) for i in range(80)),
        "emoji-separated words": " ".join(f"a{i}😀b{i}😀c{i}" for i in range(120)),
        "one 3000-char CJK term among short words": " ".join(three[:12] + ["漢" * 3000]),
        "keywords + specials": " ".join(["AND", "NOT", "foo(bar)", "x:y", "a+b", "c-d", "TO", "OR"] * 60),
    }
    for label, q in shapes.items():
        old = _lucene_would_reject(_old_builder(q))
        got = R._build_fts_query(q)
        why = _lucene_would_reject(got)
        check(f"{label}: parseable within {_MAX_CLAUSES} expanded clauses", why is None and bool(got),
              f"{why} (old builder: {old})")
    rng = random.Random(4)
    vocab = [w for w in three] + [f"{w}{i}" for w in ("node", "edge", "sync", "lock") for i in range(40)]
    worst = None
    for _ in range(1500):
        q = " ".join(rng.choice(vocab) if rng.random() < 0.8 else "".join(
            rng.choice("abcxyz_.:(日😀") for _ in range(rng.randint(3, 14))) for _ in range(rng.randint(0, 400)))
        why = _lucene_would_reject(R._build_fts_query(q))
        if why:
            worst = (q[:80], why)
            break
    check("1500 seeded random queries (0-400 terms, repeats, CJK/emoji/specials): none exceeds the bound",
          worst is None, str(worst))


def gate_above_t(R) -> None:
    print(f"\n-- above T={T}: de-duplicated, EXACT tail, as many leading fuzzy terms as the bound allows --")
    q = " ".join(f"word{i}" for i in range(T + 1))
    got = _terms(R._build_fts_query(q))
    check(f"{T + 1} distinct single-token terms: every term kept, in order",
          [t for t, _ in got] == [f"word{i}" for i in range(T + 1)], str(got))
    nf = sum(1 for _t, fz in got if fz)
    check("the leading terms stay fuzzy, the tail is exact (no fuzzy term after an exact one)",
          0 < nf < T + 1 and all(fz for _t, fz in got[:nf]) and not any(fz for _t, fz in got[nf:]), str(got))
    check(f"as many fuzzy terms as the worst case allows: 9 fuzzy + 2 exact for {T + 1} terms",
          nf == 9, str(nf))
    q = " ".join(["alpha", "beta"] * 40 + ["gamma"])
    got = _terms(R._build_fts_query(q))
    check("repeats are de-duplicated above T (first-seen order): alpha, beta, gamma, all fuzzy",
          got == [("alpha", True), ("beta", True), ("gamma", True)], str(got))
    q = " ".join(f"w{i:04d}" for i in range(240))
    got = _terms(R._build_fts_query(q))
    check("240 distinct single-token terms: ALL kept (as exact terms) — nothing silently lost",
          len(got) == 240 and [t for t, _ in got] == [f"w{i:04d}" for i in range(240)], f"{len(got)} kept")
    idents = [f"{a}_{b}_{c}" for a in ("load", "sync", "push", "read", "make", "check") for b in
              ("graph", "nodes", "edges", "state", "query") for c in ("fast", "safe", "once", "all")]
    got = _terms(R._build_fts_query(" ".join(idents)))
    check(f"{len(idents)} distinct snake_case identifiers: ALL kept, some fuzzy (an underscore is not a token break)",
          [t for t, _ in got] == idents and any(fz for _t, fz in got), f"{len(got)} kept")
    rng = random.Random(9)
    pool = "abcXYZ019_.:'-(日本ア한😀é́,ไทยລາວ"
    bound = getattr(R, "_fts_token_bound", None)
    under = ["<retrieval._fts_token_bound is missing>"] if bound is None else [
        t for t in ("".join(rng.choice(pool) for _ in range(rng.randint(1, 24))) for _ in range(20000))
        if bound(t) < len(_std_tokens(t))]
    check("the builder's per-term token bound is >= an independent analyzer model (20000 random terms)",
          not under, repr(under[:3]))
    words = [f"word{i}" for i in range(12)] + ["漢" * 300] + [f"tail{i}" for i in range(5)]
    got = [t for t, _ in _terms(R._build_fts_query(" ".join(words)))]
    check("one term too costly to keep as an exact term is skipped — the cheaper terms AFTER it are still kept",
          [w for w in words if not w.startswith("漢")] == [t for t in got if not t.startswith("漢")]
          and _lucene_would_reject(R._build_fts_query(" ".join(words))) is None, str(got))
    q = " ".join(f"w{i:04d}" for i in range(2000))
    got = _terms(R._build_fts_query(q))
    check("2000 distinct terms: capped (the first ones kept, in order) under the bound",
          0 < len(got) <= _MAX_CLAUSES // _FIELDS and [t for t, _ in got] == [f"w{i:04d}" for i in range(len(got))],
          f"{len(got)} kept")
    q = " ".join(["read", "AND", "write", "NOT", "lock"] + [f"x{i}" for i in range(15)])
    got = R._build_fts_query(q)
    check("above T, specials stay escaped and reserved words lower-case",
          _lucene_would_reject(got) is None and ("and" in got.split() or "and~2" in got.split())
          and ("not" in got.split() or "not~2" in got.split()), got[:120])


def _mcp_query_cap() -> int:
    try:
        return int(_load("mcp_bounds").QUERY_MAX_CHARS)
    except Exception:  # noqa: BLE001
        return 10 ** 9   # unknown -> the >= check fails loudly


def _rand_cjk(n: int, seed: int) -> str:
    rng = random.Random(seed)
    return "".join(chr(rng.randrange(0x4E00, 0x9FA0)) for _ in range(n))


def gate_overlong_fuzzy(R) -> None:
    print("\n-- one overlong fuzzy term (TooComplexToDeterminize / heap) never sinks the arm --")
    cjk400 = _rand_cjk(400, 1)
    shapes = {
        "a 3-term query holding a 400-char CJK sentence (one whitespace-free term)": f"config loader {cjk400}",
        "a lone 300-char CJK sentence": _rand_cjk(300, 2),
        "a 700-char accented word + words": "config loader " + ("éüñ" * 240)[:700],
        "~1005 ASCII chars around ONE é (a single term)": "sync " + "a1b2c3d4e5" * 50 + "é" + "f6g7h8i9j0" * 50,
        "a 17000-char pure-ASCII term (direct API only)": "sync lock " + "".join(
            random.Random(3).choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(17000)),
        "a mixed 2/3/4-byte 230-char term": "".join(random.Random(4).choice("éñжщ漢字語𠀀𠀁") for _ in range(230)),
        "a lone 480-char CJK term full of ( ) : (its kept prefix must stay escaped)": "(漢字:語)" * 96,
    }
    for label, q in shapes.items():
        old = _lucene_would_reject(_old_builder(q))
        got = R._build_fts_query(q)
        why = _lucene_would_reject(got)
        check(f"{label}: parseable, the arm still answers", why is None and bool(got), f"{why} (old builder: {old})")
    got = _terms(R._build_fts_query(f"config loader {cjk400}"))
    check("…and the short words beside it stay FUZZY, in order (only the overlong term changes)",
          got[:2] == [("config", True), ("loader", True)] and all(not fz for t, fz in got[2:]), str(got)[:160])
    got = _terms(R._build_fts_query("config loader " + _rand_cjk(150, 5)))
    check("a 150-char CJK run (450 bytes, over the fuzzy bound) is kept as an EXACT term, never fuzzy",
          [fz for _t, fz in got] == [True, True, False] and len(got[2][0]) == 150, str([fz for _t, fz in got]))
    print(f"\n-- a term within the fuzzy bounds keeps the query byte-identical (<= T terms) --")
    for label, q in (("a 133-char CJK run (399 UTF-8 bytes)", "read the " + _rand_cjk(133, 6) + " lock"),
                     ("a 200-char 2-byte word (400 bytes)", "parse " + ("жщ" * 100) + " config"),
                     ("a 2000-char pure-ASCII term", "sync " + "abc123xyz_" * 200),
                     ("short accented / CJK words", "café naïve 設定 ロック résumé")):
        check(f"{label}: new == old", R._build_fts_query(q) == _old_builder(q), R._build_fts_query(q)[:80])
    rng = random.Random(11)
    alphabets = ("abcxyz019_", "éñжщ", "漢字語", "𠀀𠀁😀", "abcé漢")
    # the builder's own bounds (the 2x-margin halves of the measured limits when a builder predates them)
    max_ascii = getattr(R, "_FTS_FUZZY_MAX_ASCII", _ASCII_FUZZY_OOM_MIN // 2)
    max_bytes = getattr(R, "_FTS_FUZZY_MAX_BYTES", _TOO_COMPLEX_MIN_BYTES // 2)
    worst = None
    for _ in range(600):
        words = []
        for _w in range(rng.randint(1, 14)):
            a = rng.choice(alphabets)
            words.append("".join(rng.choice(a) for _c in range(rng.choice((3, 8, 40, 150, 260, 400, 900)))))
        got = R._build_fts_query(" ".join(words))
        why = _lucene_would_reject(got)
        overlong = [t for t, fz in _terms(got) if fz and (len(t) > max_ascii if t.isascii()
                                                           else len(t.encode("utf-8")) > max_bytes)]
        if why or overlong or not got:
            worst = (words[:3], why, overlong[:1])
            break
    check("600 seeded queries of long ASCII / 2-, 3-, 4-byte / mixed terms: no fuzzy term over the bounds, "
          "always parseable and non-empty", worst is None, str(worst)[:200])


def gate_token_bound_splits(R) -> None:
    print("\n-- the exact-term token bound never undercounts (the analyzer splits more than isalnum() runs) --")
    bound = getattr(R, "_fts_token_bound", None)
    if bound is None:
        check("retrieval._fts_token_bound exists", False)
        return
    for label, term in (("Latin / Thai alternating (SA letters are never ALetter)", "q1" + "ไa" * 8),
                        ("Latin / Lao alternating", "q2" + "ລb" * 8),
                        ("superscripts inside a word (Word_Break=Other)", "q3" + "x²" * 8),
                        ("circled digits inside a word", "q4" + "k①" * 8),
                        ("a 700-char word (split every 255)", "a" * 700),
                        ("a 256-char word", "b" * 256),
                        ("Thai digits join Latin (Numeric), Thai letters split", "ab๑๒ไทยcd"),
                        ("Thai digits (Numeric, not SA) split two Thai-letter runs", "ไทย๑๒ไทย" * 3)):
        check(f"{label}: bound {bound(term)} >= analyzer model {len(_std_tokens(term))}",
              bound(term) >= len(_std_tokens(term)))
    for label, n in (("240 Latin/Thai-alternating terms (live: TooManyClauses before the fix)", 240),
                     ("240 superscript-joined terms", 240)):
        make = (lambda i: f"q{i}" + "ไa" * 8) if "Thai" in label else (lambda i: f"q{i}" + "x²" * 8)
        got = R._build_fts_query(" ".join(make(i) for i in range(n)))
        why = _lucene_would_reject(got)
        check(f"{label}: within {_MAX_CLAUSES} expanded clauses", why is None and bool(got), str(why))


def gate_prefix_keyword(R) -> None:
    print("\n-- the kept-prefix path obeys the main loop's rules (no bare AND/OR/NOT/TO, no <=2-char token) --")
    # Round-4 review: when a long term is cut to the prefix that fits the budget, the cut could land on a bare
    # reserved word (Lucene reads 'AND' as an operator -> ParseException -> the BM25 arm is silently dropped).
    saved = getattr(R, "_fts_prefix_within", None)
    if saved is None:
        check("retrieval._fts_prefix_within exists", False)
        return
    query = "alpha bravo charlie " + "漢" * 2000   # an overlong CJK term forces the budgeted path + a skip
    try:
        for stub, bad, label in (("AND", "AND", "a cut landing on AND is lower-cased"),
                                 ("NOT", "NOT", "a cut landing on NOT is lower-cased"),
                                 ("AB", "AB", "a 2-char cut is dropped like the main loop drops it")):
            R._fts_prefix_within = lambda raw, n, _s=stub: _s
            got = R._build_fts_query(query)
            toks = got.split()
            check(f"{label}: no bare {bad!r} token (got {got[:80]!r})", bad not in toks and bool(got))
            if stub in ("AND", "NOT"):
                check(f"{label}: emitted as {stub.lower()!r}", stub.lower() in toks, got[:80])
    finally:
        R._fts_prefix_within = saved


def gate_constants(R) -> None:
    print("\n-- the bound's assumptions are locked to the code they depend on --")
    check("the fuzzy byte bound for a non-ASCII term is >= 2x under the measured TooComplexToDeterminize minimum",
          0 < getattr(R, "_FTS_FUZZY_MAX_BYTES", 10 ** 9) * 2 <= _TOO_COMPLEX_MIN_BYTES,
          str(getattr(R, "_FTS_FUZZY_MAX_BYTES", None)))
    check("the pure-ASCII fuzzy length bound is >= 2x under the measured heap exhaustion, and >= the MCP/CLI "
          "query cap (so no capped query changes)",
          getattr(R, "_FTS_FUZZY_MAX_ASCII", 10 ** 9) * 2 <= _ASCII_FUZZY_OOM_MIN
          and getattr(R, "_FTS_FUZZY_MAX_ASCII", 0) >= _mcp_query_cap(), str(getattr(R, "_FTS_FUZZY_MAX_ASCII", None)))
    check("the builder splits tokens at the analyzer's 255-character maximum",
          getattr(R, "_FTS_MAX_TOKEN_UNITS", None) == _MAX_TOKEN_LEN, str(getattr(R, "_FTS_MAX_TOKEN_UNITS", None)))
    check(f"the byte-identity threshold is T={T}", getattr(R, "_FTS_FUZZY_MAX_TERMS", None) == T,
          str(getattr(R, "_FTS_FUZZY_MAX_TERMS", None)))
    check("T fuzzy terms fit the worst case (T x populated fields x 50 <= 1024) — the <=T path is bounded too",
          T * _POPULATED_FIELDS * _MAX_EXPANSIONS <= _MAX_CLAUSES)
    try:
        I = _load("indexes")
        props = next(p for n, _l, p in I.FTS_INDEXES if n == "deepmap_code_fts")
    except Exception as exc:  # noqa: BLE001
        props = None
        check("indexes.FTS_INDEXES is loadable", False, str(exc))
    check("deepmap_code_fts indexes exactly the 4 properties the bound models",
          props == ["name", "body_summary", "docstring", "rationale"], str(props))
    check("the builder's field count matches the index", getattr(R, "_FTS_FIELDS", None) == len(props or []),
          str(getattr(R, "_FTS_FIELDS", None)))
    writers = []
    for f in sorted(BUILD.glob("*.py")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\.(docstring|rationale)\s*=\s*(row|\$|n\.|x\.)", txt) or \
                re.search(r"SET[^\n\"']*\.(docstring|rationale)\b", txt):
            writers.append(f.name)
    check("no build/ module writes node.docstring / node.rationale (only name + body_summary are populated, "
          "which the fuzzy bound of 2 x 50 relies on)", not writers, str(writers))


def gate_arm_keeps_bm25(R) -> None:
    print("\n-- end-to-end: the BM25 arm now ANSWERS for the queries that used to drop it --")
    from neo4j.exceptions import ClientError

    class _LuceneSession:
        def __init__(self, log):
            self.log = log

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            q = params.get("q", "")
            self.log.append(q)
            why = _lucene_would_reject(q)
            if why:
                exc = ClientError(f"Failed to invoke procedure `db.index.fulltext.queryNodes`: "
                                  f"org.apache.lucene.search.IndexSearcher$TooManyClauses / {why}")
                exc._neo4j_code = "Neo.ClientError.Procedure.ProcedureCallFailed"
                raise exc
            return [{"id": "n1", "name": "lock", "type": "Function", "file": "a.py", "line": 1,
                     "summary": "", "score": 1.0}]

    class _Drv:
        def __init__(self):
            self.log = []

        def session(self, **kw):
            return _LuceneSession(self.log)

    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)   # no probe query; the arm is what we test
    ret.driver = _Drv()
    for q in ("read AND write lock", "files NOT found", "order NOT cancel",
              "the run and arm env mcp for not cli per git one get set add use new str int map key dir log",
              " ".join(f"w{i:04d}" for i in range(1500)),
              "config loader " + _rand_cjk(400, 1),
              " ".join(f"q{i}" + "ไa" * 8 for i in range(240))):
        rows = ret._arm_fulltext(q, 45)
        check(f"_arm_fulltext({q[:30]!r}...) returns BM25 rows (not a silently skipped arm)", len(rows) == 1,
              str(ret.driver.log[-1:])[:160])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — BM25 query builder: Lucene keywords + expanded-clause bound (S2 + F4; no Neo4j)")
    print("=" * 72)
    R = _load("retrieval")
    gate_keywords(R)
    gate_byte_identity(R)
    gate_expanded_bound(R)
    gate_above_t(R)
    gate_overlong_fuzzy(R)
    gate_token_bound_splits(R)
    gate_prefix_keyword(R)
    gate_constants(R)
    gate_arm_keeps_bm25(R)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
