#!/usr/bin/env python3
"""DeepMap retrieval — three-arm Weighted-RRF fusion for hybrid search.

Arms (each run across ALL embeddable labels, not just Function):
  1. Vector search (semantic similarity via SEARCH clause or procedure fallback)
  2. BM25 fulltext (keyword match with fuzzy) — already multi-label via deepmap_code_fts
  3. Graph-weighted vector (vector score × (1 + log(1 + inDegree)) for structural importance)

Fusion: Weighted Reciprocal Rank Fusion (RRF), implemented directly.

UPGRADE NOTES (v2.1):
  - FD-5 multi-label coverage: arms 1 and 3 now query every per-label vector index
    (Function/Method/Class/Module/Route), not only deepmap_fn_embeddings. Results
    from all labels are pooled per arm before fusion.
  - FD-6 Weighted RRF: fusion uses per-arm weights (default equal == prior plain RRF,
    so behavior cannot regress until weights are calibrated by the benchmark).
    Implemented directly (ranx's `rrf` takes no weights arg; its weighted methods are
    score-based, and we want weighted *rank* fusion to keep RRF scale-independence).
  - FD-8.1: the empty-arm skip and single-arm bypass safety is preserved; weights are
    keyed by SURVIVING arm name, never by fixed position, so a dropped empty arm cannot
    misalign the weight vector.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

# Standard RRF smoothing constant (Cormack et al. 2009). k=60 is the field default
# and matches ranx's default, so equal-weight output equals plain RRF.
RRF_K: int = 60


def _candidate_floor() -> int:
    """P1-7 (opt-in): a per-arm candidate-pool floor. `fetch_k = max(top_k*3, _candidate_floor())` makes
    a small top_k still fetch a deep pool so a top-3 is a stable prefix of a top-8 (RRF prefix
    instability). Defaults to 0 (DEEPMAP_CANDIDATE_FLOOR unset) -> byte-identical `top_k*3` ranking; the
    default flip is benchmark-gated. A non-int / negative value is treated as 0 (never raises)."""
    try:
        return max(0, int(os.environ.get("DEEPMAP_CANDIDATE_FLOOR", "0")))
    except (TypeError, ValueError):
        return 0


def _is_fatal_db_error(exc: BaseException) -> bool:
    """Should this per-index error fail LOUD (True) or be skipped benignly (False)?

    Posture (T10): an INSTANCE-level failure must fail loud (so an outage never becomes a
    fabricated "no results"), while a single-index problem stays isolated (skip that index, let
    the other arms answer — the FD-1 isolation invariant). So:
      * FATAL — connectivity/auth/transient driver errors (ServiceUnavailable / SessionExpired /
        TransientError / AuthError), any OSError (socket/timeout), an unexpected ClientError
        (e.g. a syntax error), and any other exception whose MESSAGE looks like a connection
        failure (an outage surfacing as a bare error must still fail loud).
      * BENIGN — a missing/absent index or procedure (ClientError *NotFound), and any other
        per-index error with no connectivity signal (one index failing must not kill the arm).
    An empty graph that is genuinely UP still returns [] via the normal no-rows path (true negative).
    """
    try:
        from neo4j.exceptions import (ServiceUnavailable, SessionExpired, TransientError,
                                       AuthError, ClientError, DatabaseError)
    except Exception:
        return True   # driver not importable — don't mask anything
    # DatabaseError = a server-side execution failure (OOM / ExecutionFailed) — a REAL DB problem,
    # not a missing index. Fail loud (T10) so it never masquerades as an empty/partial result.
    if isinstance(exc, (ServiceUnavailable, SessionExpired, TransientError, AuthError, OSError,
                        DatabaseError)):
        return True
    if isinstance(exc, ClientError):
        code = (getattr(exc, "code", "") or "")
        if any(token in code for token in ("IndexNotFound", "NoSuchIndex", "ProcedureNotFound")):
            return False   # explicit missing-index/procedure code (the Cypher-25 SEARCH-clause path)
        if _is_missing_index_error(exc):
            return False   # procedure-fallback path (Neo4j 5.26): a missing per-label index surfaces
                           # as ProcedureCallFailed "no such ... schema index" — benign, skip & isolate
        return True        # any other ClientError (a real syntax/connectivity error) — fail loud
    # Other exception types: fatal only if the message smells like a connection failure; else
    # benign so a one-off per-index error stays isolated.
    msg = str(exc).lower()
    return any(s in msg for s in ("unable to connect", "connection refused", "connection reset",
                                  "connection lost", "defunct", "unavailable", "econnrefused",
                                  "cannot resolve", "broken pipe", "timed out", "no connection"))


# Lucene classic-query reserved characters. Code-search words are full of these — ( ) [ ] : . * ^ —
# and left raw they make db.index.fulltext.queryNodes raise a query-PARSE error that would crash the
# whole search (HARD-2). We escape them per token so any user query yields a valid Lucene string.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')
# ...and reserved WORDS: the classic QueryParser reads a standalone upper-case AND / OR / NOT (and TO, in a
# range) as an operator, so `AND~2` is a ParseException that silently dropped the whole BM25 arm (3 live
# reports). Lower-cased they are plain terms — the index analyzer lower-cases anyway, so they match the
# same words. (OR / TO are <=2 chars and already dropped; listed for completeness.)
_LUCENE_KEYWORDS = frozenset({"AND", "OR", "NOT", "TO"})

# The EXPANDED-clause bound (F4; measured READ-ONLY on this repo's own Neo4j 5.26 index, bolt 7689). Lucene 9
# counts the REWRITTEN leaf clauses of a query against maxClauseCount (1024) — not its top-level terms — and
# Neo4j parses a fulltext query with a MultiFieldQueryParser over every property of the index, so one user
# term costs:
#   * fuzzy `t~2`: one FuzzyQuery per field, each rewritten to <= 50 expansions (FuzzyQuery.defaultMaxExpansions).
#     Only `name` + `body_summary` are ever written (no DeepMap writer, past or present, sets docstring /
#     rationale; an empty field's fuzzy query rewrites to nothing), so <= 2 x 50 = 100 leaves. Measured:
#     the/and/get/run/for/set cost 44-56 leaves each, gate/node 24-32, long words 0-4.
#   * exact `t`: at most one leaf per field per analyzer token -> <= 4 x tokens. Measured: a one-token term costs
#     4 (256 distinct exact terms parse, 257 raise TooManyClauses); terms of 3 / 5 / 30 tokens cost 4 / 5 / 30.
# With every term fuzzy, the first TooManyClauses came at 23 distinct 3-letter words (45 four-letter, 173 of
# 5-7 letters), far under the 2000-char MCP/CLI cap, and `_arm_fulltext` then returns [] (arm lost, no note).
_FTS_MAX_CLAUSES = 1024           # Lucene IndexSearcher.maxClauseCount ("maxClauseCount is set to 1024")
_FTS_CLAUSE_BUDGET = 1000         # what the builder spends, a margin under the limit
_FTS_FIELDS = 4                   # deepmap_code_fts: name, body_summary, docstring, rationale (build/indexes.py)
_FTS_FUZZY_CLAUSES = 2 * 50       # populated fields x maxExpansions: the worst case for one fuzzy term
# T: a query of up to this many terms (none overlong) CANNOT exceed the limits — 10 x 100 = 1000 fits the worst
# case above — so it is emitted byte-identically with no questions asked. Every STAGE-0 oracle query (<= 8 terms)
# is below it. Above T the original string is still emitted unchanged when Lucene accepts it (I1, round 7: the
# builder validates it on the arm's own index — see _build_fts_query); T only decides when to ask.
_FTS_FUZZY_MAX_TERMS = 10
# Distinct terms a longer query is scanned for (the MCP/CLI 2000-char cap allows ~660; only a direct API call
# can pass more, and at most ~260 of them could be kept anyway).
_FTS_SCAN_TERMS = 4096
# One fuzzy term can also sink the query ON ITS OWN (the same silent loss of the arm). Measured on bolt 7689:
#   * a term holding ANY non-ASCII character: TooComplexToDeterminizeException (Lucene cannot determinize its
#     Levenshtein automaton over UTF-8) once the whole term reaches ~810-1010 UTF-8 bytes — 225-286 random CJK /
#     other 3- or 4-byte characters, 463-506 2-byte ones, or ~1005 ASCII characters around a single `é` / CJK
#     character / emoji. Inside the 2000-char MCP/CLI cap: one whitespace-free CJK sentence is ONE term.
#   * a pure-ASCII term: 6000 random characters parse; ~16900 exhaust the (512 MB) heap — OutOfMemoryError. Only
#     a direct API call gets past the 2000-char cap; bounded anyway.
# A term over these lengths (>= 2x under the worst measured) is never emitted fuzzy — it is kept EXACT when that
# fits the budget (exact terms have no automaton: 40000 characters parse). A long fuzzy CJK run matched nothing
# anyway: CJK is indexed one character per token.
_FTS_FUZZY_MAX_BYTES = 400        # UTF-8 bytes, for a term with any non-ASCII character
_FTS_FUZZY_MAX_ASCII = 2000       # characters, for a pure-ASCII term
# The standard analyzer splits a token longer than 255 UTF-16 units (StandardAnalyzer.DEFAULT_MAX_TOKEN_LENGTH).
_FTS_MAX_TOKEN_UNITS = 255
# Scripts UAX#29 leaves to "complex context" (Line_Break=SA: Thai, Lao, Myanmar, Khmer, Tai Le / New Tai Lue /
# Tai Tham) — their letters are never ALetter; a run of them is a token of its own. (The SA blocks above U+2E80
# are counted one token per character anyway.)
_FTS_SA_RANGES = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x1950, 0x19FF), (0x1A20, 0x1AAF))


def _fts_token_bound(term: str) -> int:
    """An UPPER bound on the tokens the index's standard analyzer (UAX#29 word breaks) makes of one exact
    (non-fuzzy) term. A token is a run of letters, decimal digits and `_` (ExtendNumLet) — split again every
    _FTS_MAX_TOKEN_UNITS characters and wherever the run switches between a complex-context (SA) script's
    letters and any other word character. Every ideographic / kana / hangul / fullwidth character counts as
    one token (Han and Hiragana are tokenized one by one; the others only join, which lowers the real count),
    and so does every other non-ASCII character: an emoji is a token, and a superscript / circled digit (a
    word BREAK, never part of a word — Python's isalnum() would have joined `x²y`) splits the run. The joins
    UAX#29 also makes across `. : '` and combining marks only lower the real count."""
    n, run, units = 0, None, 0
    for ch in term:
        o = ord(ch)
        cat = unicodedata.category(ch)
        if cat[0] == "L" or cat in ("Nd", "Nl") or ch == "_":
            if o >= 0x2E80:
                n, run = n + 1, None
                continue
            cls = "sa" if cat[0] == "L" and any(lo <= o <= hi for lo, hi in _FTS_SA_RANGES) else "w"
            if cls != run or units >= _FTS_MAX_TOKEN_UNITS:
                n, run, units = n + 1, cls, 0
            units += 1
        else:
            run = None
            if o > 0x7F:
                n += 1
    return max(n, 1)


def _fts_fuzzy_ok(raw: str) -> bool:
    """False when `raw~2` could sink the query on its own (see _FTS_FUZZY_MAX_BYTES / _FTS_FUZZY_MAX_ASCII):
    such a term is only ever emitted exact."""
    if raw.isascii():
        return len(raw) <= _FTS_FUZZY_MAX_ASCII
    return len(raw.encode("utf-8", "surrogatepass")) <= _FTS_FUZZY_MAX_BYTES


# ---- I1 (round 7): byte-identical for every query Lucene ACCEPTS --------------------------------------------
# F4 (round 4) budgeted EVERY query above T terms a priori, and so changed the BM25 arm for queries Lucene took
# without complaint: measured READ-ONLY on bolt 7689, all 49 benchmark prompts (12-207 terms) are ACCEPTED as the
# original all-fuzzy string, and the budgeted one changed the top-15 hits of all 49. No tight a-priori bound
# exists — a fuzzy term's cost is the number of indexed terms within edit distance 2 (<= 50 per populated
# field), which only the index knows, and Lucene collapses repeated terms — so the builder now ASKS the index the
# arm is about to query: it validates the original string with a limit-1 read on the arm's own driver and emits
# it unchanged unless Lucene rejects it (TooManyClauses / TooComplexToDeterminize / ParseException). Only a
# rejected string gets the bounded variant. The arm (`_arm_fulltext`) is byte-frozen, so the builder finds the
# arm's driver from its caller's frame — the one module-level hook the arm already calls — and a call from
# anywhere else (a test, a script) has no driver and keeps the a-priori builder. DEEPMAP_FTS_PROBE=0 opts out.
_FTS_PROBE_TIMEOUT_S = 10.0       # server-side timeout of the validation read (env DEEPMAP_FTS_PROBE_TIMEOUT_S)
_FTS_PROBE_CYPHER = ("CALL db.index.fulltext.queryNodes('deepmap_code_fts', $q, {limit: 1}) "
                     "YIELD node RETURN count(node) AS n")
# I2 (gated, default OFF): DEEPMAP_FTS_SHORT_NAME=1 adds this property (a Class.method node's bare `method`) to
# deepmap_code_fts — the index side lives in indexes.py / sync.py — so the cost model counts a 5th field, 3 of
# them populated.
FTS_SHORT_NAME_PROPERTY = "short_name"
# I2 (gated, default OFF): DEEPMAP_FTS_SPLIT_CODE_TOKENS=1 splits a token holding non-identifier characters (a
# pasted call `reset_pristine(repo_dir,`, `a.b`, `Class#m`, `multi-hop`) into its identifier pieces: a fuzzy term
# is NOT analyzed, so the glued `reset_pristine\(repo_dir,~2` was within 2 edits of no indexed token (0 hits). A
# qualified name is also kept whole ('.'-joined) — the analyzer indexes `Class.method` as ONE token.
_FTS_NON_WORD = re.compile(r"\W+")
_FTS_QUALIFIED = re.compile(r"\w+(?:(?:\.|::|#)\w+)+")


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _fts_model() -> "tuple[int, int, int]":
    """(index fields, worst-case expanded leaves of one fuzzy term, T = the terms that always fit): the measured
    (4, 2 x 50, 10) — or, under DEEPMAP_FTS_SHORT_NAME=1, (5, 3 x 50, 6) for the extra populated field."""
    if _env_on("DEEPMAP_FTS_SHORT_NAME"):
        fuzzy = _FTS_FUZZY_CLAUSES + 50
        return _FTS_FIELDS + 1, fuzzy, _FTS_CLAUSE_BUDGET // fuzzy
    return _FTS_FIELDS, _FTS_FUZZY_CLAUSES, _FTS_FUZZY_MAX_TERMS


def _fts_escape(t: str) -> str:
    return "".join("\\" + c if c in _LUCENE_SPECIAL else c for c in t)


def _fts_code_pieces(token: str) -> list:
    """DEEPMAP_FTS_SPLIT_CODE_TOKENS: a token's qualified names ('::' / '#' written as '.', the stored form),
    then its identifier pieces — `run_benchmark.reset_pristine` -> [run_benchmark.reset_pristine, run_benchmark,
    reset_pristine]; `reset_pristine(repo_dir,` -> [reset_pristine, repo_dir]."""
    quals = [m.group(0).replace("::", ".").replace("#", ".") for m in _FTS_QUALIFIED.finditer(token)]
    return quals + [p for p in _FTS_NON_WORD.split(token) if p]


def _fts_terms(query: str) -> list:
    """[(escaped, raw)] — raw is what the analyzer sees: words > 2 chars with alphanumeric content, a standalone
    reserved word lower-cased (`AND~2` was a ParseException), Lucene specials escaped. With the split flag a
    punctuated token contributes its pieces (a piece already emitted is not repeated); the user's own clean
    words are kept exactly as before, repeats included."""
    split = _env_on("DEEPMAP_FTS_SPLIT_CODE_TOKENS")
    terms, emitted = [], set()
    for tok in query.split():
        pieces = _fts_code_pieces(tok) if split and _FTS_NON_WORD.search(tok) else None
        for t in (pieces if pieces is not None else [tok]):
            if len(t) <= 2 or not any(ch.isalnum() for ch in t):
                continue
            if t in _LUCENE_KEYWORDS:
                t = t.lower()
            if pieces is not None and t in emitted:
                continue
            emitted.add(t)
            terms.append((_fts_escape(t), t))
    return terms


def _fts_bounded(terms: list, fields: int, fuzzy_clauses: int, max_terms: int) -> str:
    """The bounded variant (F4): de-duplicated (first-seen order); as many terms stay fuzzy (the first ones that
    may) as the worst case allows, the rest become EXACT terms (<= `fields` clauses per analyzer token instead of
    up to `fuzzy_clauses` per fuzzy term), and every term that fits _FTS_CLAUSE_BUDGET is kept — so the expanded
    clause count can never exceed maxClauseCount and no emitted fuzzy term is overlong."""
    uniq = list(dict.fromkeys(terms))[:_FTS_SCAN_TERMS]   # bounded work on a pathological direct-API query
    cost = [fields * _fts_token_bound(raw) for _esc, raw in uniq]
    may_fuzz = [_fts_fuzzy_ok(raw) for _esc, raw in uniq]
    best = None   # (whole terms kept, emitted pieces, budget left, (position, raw) of the first skipped term)
    for n_fuzzy in range(min(max_terms, sum(may_fuzz)), -1, -1):
        left = _FTS_CLAUSE_BUDGET - n_fuzzy * fuzzy_clauses
        if left < 0:
            continue
        out, fuzzy_left, skipped = [], n_fuzzy, None
        for (esc, raw), c, fz in zip(uniq, cost, may_fuzz):
            if fz and fuzzy_left:
                out.append(f"{esc}~2")
                fuzzy_left -= 1
            elif c <= left:   # exact; a term too costly for what is left is skipped — later, cheaper ones may fit
                out.append(esc)
                left -= c
            elif skipped is None:
                skipped = (len(out), raw)
        if best is None or len(out) > best[0]:
            best = (len(out), out, left, skipped)
        if len(out) == len(uniq):
            break   # every term fits with this many fuzzy — the most fuzzy such split
    if best is None:
        return ""
    _kept, out, left, skipped = best
    if skipped is not None and left >= fields:
        # Budget is left over but a term did not fit whole (a long CJK sentence is ONE term of one token per
        # character): keep its longest prefix that fits rather than lose it — alone, it would leave the arm empty.
        pos, raw = skipped
        prefix = _fts_prefix_within(raw, left // fields)
        if prefix in _LUCENE_KEYWORDS:   # a cut can land on a bare AND/OR/NOT/TO — same rule as the main loop
            prefix = prefix.lower()
        if len(prefix) > 2 and any(ch.isalnum() for ch in prefix):   # the main loop's <=2-char drop
            out = out[:pos] + [_fts_escape(prefix)] + out[pos:]
    return " ".join(out)


def _fts_probe_timeout_s() -> float:
    try:
        value = float(os.environ.get("DEEPMAP_FTS_PROBE_TIMEOUT_S", _FTS_PROBE_TIMEOUT_S))
    except (TypeError, ValueError):
        return _FTS_PROBE_TIMEOUT_S
    return value if value > 0 else _FTS_PROBE_TIMEOUT_S


def _fts_arm_driver(frame):
    """The Neo4j driver of the DeepMapRetriever whose byte-frozen `_arm_fulltext` is the builder's caller (`frame`
    is that caller's frame) — the index the arm is about to query. None for any other caller: only the arm's own
    call is validated, against the arm's own driver."""
    try:
        if frame is None or frame.f_code is not DeepMapRetriever._arm_fulltext.__code__:
            return None
        return getattr(frame.f_locals.get("self"), "driver", None)
    except Exception:  # noqa: BLE001 — no frame introspection: the a-priori builder
        return None


def _fts_validate(driver, fts: str) -> "bool | None":
    """Does Lucene accept `fts` on the arm's index? True: accepted. False: rejected — TooManyClauses,
    TooComplexToDeterminize, a ParseException (all ProcedureCallFailed, which _arm_fulltext would skip) or a
    memory refusal. None: unknown (the validation timed out, or a benign error such as a missing index — the arm
    then runs the string and handles that error exactly as before). An INSTANCE-level error re-raises: the arm's
    own query would fail loud on it (T10), and a second query against a dead instance only doubles the wait."""
    try:
        with driver.session() as s:
            cypher = _FTS_PROBE_CYPHER
            try:
                from neo4j import Query, Session
                if isinstance(s, Session):
                    cypher = Query(_FTS_PROBE_CYPHER, timeout=_fts_probe_timeout_s())
            except Exception:  # noqa: BLE001 — a driver too old to import Query from: an untimed read
                pass
            _first(s.run(cypher, q=fts))
        return True
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "") or "")
        if "TimedOut" in code or "Timeout" in code:
            return None
        if _is_fts_parse_error(exc) or _is_memory_error(exc):
            return False
        if _is_fatal_db_error(exc):
            raise
        return None


def _fts_validation_safe(raw: str) -> bool:
    """False for a term that must never be sent to Lucene as a fuzzy term, even to test it: a pure-ASCII term
    over _FTS_FUZZY_MAX_ASCII (a ~16.9k-character fuzzy term exhausted the heap live — only a direct API call can
    pass one; the MCP/CLI cap is 2000 characters). A long non-ASCII term fails fast (TooComplexToDeterminize)."""
    return not raw.isascii() or len(raw) <= _FTS_FUZZY_MAX_ASCII


def _build_fts_query(query: str) -> str:
    """Fuzzy BM25 query from the user's words, Lucene-escaped (_fts_terms: words > 2 chars, fuzzy '~2', a
    standalone AND/OR/NOT/TO lower-cased, a token with no alphanumeric content dropped).

    I1 (round 7) — the ORIGINAL all-fuzzy string, byte for byte, whenever Lucene ACCEPTS it:
      * provably safe (<= T terms, none overlong: _fts_model / _fts_fuzzy_ok) -> the original, no round trip;
      * otherwise, when the caller is the frozen `_arm_fulltext`, the original is validated on the arm's own
        driver (_fts_validate) and emitted unchanged unless Lucene rejects it;
      * only a rejected string — or one certain to fail (> maxClauseCount terms) or too dangerous to send
        (_fts_validation_safe) — gets the bounded variant (_fts_bounded), itself validated so a server whose
        DEEPMAP_FTS_SHORT_NAME disagrees with the index still answers.
    Without the arm's driver (a direct call, or DEEPMAP_FTS_PROBE=0) a query that is not provably safe gets the
    bounded variant a priori, as in F4. Every rejection this avoids used to drop the BM25 arm silently
    (`_arm_fulltext` swallows it as a parse error). A crash-path exception like the HARD-2 escaping:
    `_arm_fulltext` and every ranking function stay byte-frozen, and a string Lucene accepts is never changed."""
    terms = _fts_terms(query)
    fields, fuzzy_clauses, max_terms = _fts_model()
    original = " ".join(f"{esc}~2" for esc, _raw in terms)
    if len(terms) <= max_terms and all(_fts_fuzzy_ok(raw) for _esc, raw in terms):
        return original          # within every measured limit: byte-identical, nothing to ask
    driver = None
    if os.environ.get("DEEPMAP_FTS_PROBE", "1").strip() != "0":
        getframe = getattr(sys, "_getframe", None)
        driver = _fts_arm_driver(getframe(1)) if getframe else None
    bounded = _fts_bounded(terms, fields, fuzzy_clauses, max_terms)
    if driver is None:
        return bounded           # no index to ask: the a-priori bound (F4)
    if len(terms) <= _FTS_MAX_CLAUSES and all(_fts_validation_safe(raw) for _esc, raw in terms):
        if _fts_validate(driver, original) is not False:
            return original      # Lucene accepts it (or the verdict is unknown): never change it
    wide = (_FTS_FIELDS + 1, _FTS_FUZZY_CLAUSES + 50)
    if (fields, fuzzy_clauses) == wide:
        return bounded
    # The index may carry more fields than this process models (built with DEEPMAP_FTS_SHORT_NAME=1, served
    # without it): if even the bounded string is rejected, fall back to the wider model's.
    if _fts_validate(driver, bounded) is not False:
        return bounded
    return _fts_bounded(terms, wide[0], wide[1], _FTS_CLAUSE_BUDGET // wide[1])


def _fts_prefix_within(raw: str, max_tokens: int) -> str:
    """The longest prefix of `raw` whose _fts_token_bound is <= max_tokens ('' when not even one token fits).
    The bound never decreases as a prefix grows, so a binary search finds it."""
    if max_tokens < 1:
        return ""
    lo, hi = 0, len(raw)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _fts_token_bound(raw[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return raw[:lo]


def _is_missing_index_error(exc: BaseException) -> bool:
    """True for a 'no such (vector|fulltext) schema index' failure from the db.index.*.queryNodes
    PROCEDURE path (Neo4j 5.26 / DD-7), which surfaces as ProcedureCallFailed — NOT an IndexNotFound
    code. Benign (skip-and-isolate): a single missing / not-yet-ONLINE per-label index must not kill
    the whole arm (the FD-1 isolation invariant). Kept narrow (a missing-index message on a procedure
    error) so a genuine connectivity ProcedureCallFailed still fails loud (T10)."""
    try:
        from neo4j.exceptions import ClientError
    except Exception:
        return False
    if not isinstance(exc, ClientError):
        return False
    code = (getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    return "Procedure" in code and any(s in msg for s in (
        "no such vector schema index", "no such fulltext schema index", "no such index"))


def _is_fts_parse_error(exc: BaseException) -> bool:
    """True for a Lucene/query-PARSE failure (a malformed query string), NOT a DB outage. Benign at
    the arm level — the query, not the instance, is wrong, so degrade to the other arms instead of
    failing loud. Kept narrow (a parse signature on a procedure-call error) so a genuine connectivity
    ClientError still fails loud (T10)."""
    try:
        from neo4j.exceptions import ClientError
    except Exception:
        return False
    if not isinstance(exc, ClientError):
        return False
    code = (getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    return "Procedure" in code and any(s in msg for s in ("parse", "lucene", "queryparser", "maxclausecount"))


def _weighted_rrf(runs_by_arm: list[tuple[str, dict[str, float]]],
                  weights: dict[str, float],
                  k: int = RRF_K) -> dict[str, float]:
    """Weighted Reciprocal Rank Fusion.

    score(d) = sum over arms of  w_arm * 1 / (k + rank_arm(d))

    Rank is 1-based, computed per arm by descending arm score. Equal weights reduce
    exactly to plain RRF. Implemented directly because ranx's `rrf` takes no weights
    argument and its weighted methods (`wsum`) are score-based, not rank-based — we
    want weighted *rank* fusion to keep RRF's scale-independence (FD-6/DD-4).
    """
    fused: dict[str, float] = {}
    for arm_name, scores in runs_by_arm:
        w = weights.get(arm_name, 1.0)
        # rank by descending score within this arm
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        for rank, (doc_id, _s) in enumerate(ranked, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + w * (1.0 / (k + rank))
    return fused

# ---- impact: symbol matching, bounded queries, honesty metadata -------------------------------------
# The callee match shared by the existence probe, the one-hop query and the multi-hop walk: a bare name,
# a `.name` suffix of a dotted `Class.method`, or (for a QUALIFIED symbol) the owner-tolerant regex below.
# `callee.name =~ null` is null, so a bare symbol (name_rx None) matches exactly as before.
_CALLEE_MATCH = ("MATCH (callee) WHERE callee.name = $name OR callee.name ENDS WITH '.' + $name "
                 "OR callee.name =~ $name_rx ")
_QUALIFIER_SEP = re.compile(r"\.|::")
# The same match restricted to ONE source file ($file_rx, a full-match regex): used only for a MODULE-qualified
# name (round 8, J6 — see _resolve_module_name). The parentheses keep the file condition on every alternative.
_CALLEE_MATCH_IN_FILE = ("MATCH (callee) WHERE (callee.name = $name OR callee.name ENDS WITH '.' + $name "
                         "OR callee.name =~ $name_rx) AND callee.source_file =~ $file_rx ")


def _callee_match(file_rx) -> str:
    """The callee MATCH for a walk / count: the unchanged _CALLEE_MATCH, or its file-restricted form."""
    return _CALLEE_MATCH if file_rx is None else _CALLEE_MATCH_IN_FILE


def _match_params(name: str, name_rx, file_rx) -> dict:
    """The callee match's parameters; $file_rx only when the match is file-restricted (so a direct answer
    sends exactly the parameters it always did)."""
    params = {"name": name, "name_rx": name_rx}
    if file_rx is not None:
        params["file_rx"] = file_rx
    return params

# Server-side transaction timeout on every impact query sent over a REAL driver session, so a pathological
# walk on a live-but-slow instance ends in bounded time (Neo4j then raises a ClientError, which degrades to
# the one-hop answer with a note). DEEPMAP_IMPACT_TIMEOUT_S overrides it for a very large graph.
IMPACT_QUERY_TIMEOUT_S: float = 25.0


def _impact_timeout_s() -> float:
    try:
        value = float(os.environ.get("DEEPMAP_IMPACT_TIMEOUT_S", IMPACT_QUERY_TIMEOUT_S))
    except (TypeError, ValueError):
        return IMPACT_QUERY_TIMEOUT_S
    return value if value > 0 else IMPACT_QUERY_TIMEOUT_S


def _timed(session, cypher: str):
    """`cypher` wrapped in a neo4j.Query carrying the impact timeout when `session` is a real driver
    session; the plain string otherwise (a test double, or a driver too old to import Query from)."""
    try:
        from neo4j import Query, Session
    except Exception:
        return cypher
    return Query(cypher, timeout=_impact_timeout_s()) if isinstance(session, Session) else cypher


def _first(result):
    """The first record of a driver result (or a test double's iterable); None when there is none."""
    return result.single() if hasattr(result, "single") else next(iter(result), None)


def _impact_name_regex(name: str) -> "str | None":
    """Best-effort owner-tolerant match for a QUALIFIED symbol, bound as $name_rx (a Java regex; Cypher's
    `=~` is a full match). Stored owners can carry generics, lifetimes and reference sigils (Rust
    `Deserializer<R>.parse_integer`, `&'a mut Serializer<W, F>.serialize_str`) and a namespace can be
    spelled `::` or `.`, so `Deserializer::parse_integer` must still find them. Each owner segment
    tolerates a `<...>` suffix, the first a leading `&` / `&'a ` / `&mut ` / `*const ` sigil, and any
    module prefix is allowed (the ENDS WITH semantics). None for a bare name: = / ENDS WITH cover it."""
    parts = [p for p in _QUALIFIER_SEP.split(name) if p]
    if len(parts) < 2:
        return None
    owner = r"(?:<.*>)?(?:\.|::)".join(re.escape(p) for p in parts[:-1])
    return (r"(?:.*(?:\.|::))?(?:&(?:'\w+\s*)?(?:mut\s+)?|\*(?:const|mut)\s+)?" + owner
            + r"(?:<.*>)?(?:\.|::)" + re.escape(parts[-1]))


def query_callers(driver, symbol: str, limit: int = 30, *, in_file: "str | None" = None) -> list[dict]:
    """Reverse :CALLS — who calls `symbol` — with each edge's confidence (the audit wedge).

    Matches a bare name (`foo`) or a Class.method suffix (`.foo`). Each row carries the edge
    `confidence` — name-resolved CALLS edges are INFERRED (one candidate) or AMBIGUOUS (the name
    maps to several symbols); the firmest edges are ordered FIRST so the audit reads trust-first.
    Shared by the deepmap_impact MCP tool and the `deepmap_cli.py impact` command. `in_file` (a full-match
    source_file regex; query_impact's module-qualified reading) restricts the callee to that file.
    """
    # Normalise the query INPUT (never stored data) to how names are stored: trim padding (' foo ' matched
    # nothing), translate Ruby's `Class#method` to the dotted `Class.method` (F10d), THEN drop a leading
    # '.' / ':' — the documented '.foo' suffix form, '#foo', '::foo'. The '#' is replaced BEFORE the strip:
    # the other order turned '#_key' into '._key', which no stored name can match.
    name = symbol.strip().replace("#", ".").lstrip(".:")
    params = _match_params(name, _impact_name_regex(name), in_file)
    params["limit"] = limit
    with driver.session() as s:
        # RETURN DISTINCT before LIMIT: a name that resolves to N same-named nodes projects N identical
        # rows per caller, which used to fill the LIMIT window with copies of the first caller.
        rows = s.run(_timed(s,
            _callee_match(in_file) +
            "MATCH (caller)-[r:CALLS]->(callee) "
            "WITH caller, callee, coalesce(r.confidence, 'INFERRED') AS confidence "
            "RETURN DISTINCT caller.name AS caller, labels(caller)[0] AS caller_type, "
            "       caller.source_file AS file, caller.start_line AS line, "
            "       confidence, callee.name AS callee "
            "ORDER BY CASE confidence WHEN 'EXTRACTED' THEN 0 WHEN 'INFERRED' THEN 1 ELSE 2 END, "
            "         caller, file, line, callee LIMIT $limit"), **params)
        return [dict(r) for r in rows]


# ---- Track C: multi-hop reverse-reachability over runtime edges -------------
# Blast-radius across :CALLS PLUS the framework/runtime edges (routes, DI, events, jobs, inheritance),
# so "what reaches this?" includes runtime ENTRY POINTS grep + a call graph can't see. This is ADDITIVE
# to ranking: it touches no _arm_*/_weighted_rrf/_hydrate; the graph-weighted ranking arm still keys on
# :CALLS in-degree only (new edges are NOT folded into ranking — that is a benchmark-gated change).
IMPACT_MAX_HOPS: int = 3          # default reverse-reachability depth (bounded)
_IMPACT_MAX_HOPS_CAP: int = 5     # hard clamp so an env override can't runaway
# The persisted edge types traversed by default == the entry-point/blast-radius set. Overridable three
# ways (precedence high->low): the query-time `edge_types` arg (CLI --edges / the deepmap_impact MCP
# param), the DEEPMAP_IMPACT_EDGES env CSV, or the DEEPMAP_IMPACT_PROFILE env (rails/orm -> adds the ORM
# associations below). CALLS is ALWAYS included, so a graph built before Track C degrades to CALLS-only.
DEFAULT_IMPACT_EDGE_TYPES: list[str] = [
    "CALLS", "ROUTES_TO", "DEPENDS_ON", "TRIGGERS", "SCHEDULED_AS", "HOOKS", "INHERITS", "CASCADES",
    "RENDERS",   # Rails view -> partial render (P1-3): a genuine blast-radius edge, benchmark-neutral
]                #            (absent from every existing benchmark graph, so it moves no measured number)
# ORM association edges (Rails has_many/belongs_to, EF/JPA/GORM navs) are NON-traversable by DEFAULT —
# a review-locked, benchmark-gated invariant (flipping the LIVE default would move the multi_hop
# benchmark). But they ARE the blast radius on an ORM app ("what breaks if I change this model" IS the
# association graph — Rails field-review P1-1), so they're opt-in ergonomically: set
# DEEPMAP_IMPACT_PROFILE=rails (or pass edge_types) and they join the reverse walk. The live default set
# stays frozen until the benchmark gate clears the flip.
ORM_ASSOCIATION_EDGE_TYPES: list[str] = ["HAS_MANY", "REFERENCES"]
_IMPACT_PROFILES: dict[str, list[str]] = {
    name: DEFAULT_IMPACT_EDGE_TYPES + ORM_ASSOCIATION_EDGE_TYPES
    for name in ("rails", "orm", "associations")
}


def _sanitize_rel_types(types) -> list[str]:
    """Keep only well-formed relationship-type tokens ([A-Z_]+), CALLS always first. The type list is
    interpolated into Cypher LABEL position (it can't be a bound parameter), so this is the injection
    guard — the symbol itself is ALWAYS a bound $name parameter."""
    seen, out = set(), []
    for t in ["CALLS"] + list(types or []):
        t = str(t).strip().upper()
        if t and t not in seen and all(c.isalpha() or c == "_" for c in t):
            seen.add(t)
            out.append(t)
    return out


def _resolve_impact_params(edge_types, max_hops):
    if edge_types is None:
        env_edges = os.environ.get("DEEPMAP_IMPACT_EDGES", "").strip()
        if env_edges:                                   # explicit CSV wins over the profile
            edge_types = [t for t in env_edges.split(",") if t.strip()]
        else:                                           # else an optional profile, else the frozen set
            profile = os.environ.get("DEEPMAP_IMPACT_PROFILE", "").strip().lower()
            edge_types = _IMPACT_PROFILES.get(profile) or DEFAULT_IMPACT_EDGE_TYPES
    if max_hops is None:
        try:
            max_hops = int(os.environ.get("DEEPMAP_IMPACT_MAX_HOPS", IMPACT_MAX_HOPS))
        except (TypeError, ValueError):
            max_hops = IMPACT_MAX_HOPS
    max_hops = max(1, min(int(max_hops), _IMPACT_MAX_HOPS_CAP))
    return _sanitize_rel_types(edge_types), max_hops


# The persisted typed-edge vocabulary (framework_edges.ALLOWED_EDGE_TYPES is the one whitelist); this mirror is
# used only when that module can't be imported, and tests/impact_r7_gates.py locks it to the real set.
_ALLOWED_EDGE_TYPES_MIRROR = frozenset({
    "INHERITS", "IMPLEMENTS", "ROUTES_TO", "REFERENCES", "HAS_MANY", "DEPENDS_ON", "HOOKS", "SCHEDULED_AS",
    "TRIGGERS", "DISPATCHES", "IMPORTS", "CASCADES", "RENDERS"})


def _known_impact_edge_types() -> frozenset:
    """CALLS + every typed edge type the graph can hold. Imported lazily: only an explicit request needs it."""
    try:
        import framework_edges as _fe
        allowed = _fe.ALLOWED_EDGE_TYPES
    except Exception:  # noqa: BLE001 — build/ not importable here: the mirror
        allowed = _ALLOWED_EDGE_TYPES_MIRROR
    return frozenset({"CALLS"} | {str(t).upper() for t in allowed})


def _impact_request(edge_types) -> "tuple[list, list]":
    """What the caller EXPLICITLY asked the walk to follow, and the notes that keep that request honest
    (T8d-X2 / T10d-W): (requested edge types — [] for the default walk —, notes). Resolves the same precedence
    as _resolve_impact_params (arg > DEEPMAP_IMPACT_EDGES > DEEPMAP_IMPACT_PROFILE) but changes nothing about
    the walk: an unknown profile used to fall back to the default set silently, and an unknown or malformed
    edge type was silently kept (it matches nothing) or dropped. Never raises; no database access."""
    try:
        if edge_types is None:
            env_edges = os.environ.get("DEEPMAP_IMPACT_EDGES", "").strip()
            if env_edges:
                raw, source = env_edges.split(","), "DEEPMAP_IMPACT_EDGES"
            else:
                profile = os.environ.get("DEEPMAP_IMPACT_PROFILE", "").strip()
                if not profile:
                    return [], []
                if profile.lower() in _IMPACT_PROFILES:
                    return list(ORM_ASSOCIATION_EDGE_TYPES), []   # what the profile ADDS to the default walk
                return [], [f"DEEPMAP_IMPACT_PROFILE={profile!r} is not a known profile "
                            f"({', '.join(sorted(_IMPACT_PROFILES))}), so it was ignored and the default edge set "
                            "was walked."]
        else:
            raw, source = list(edge_types), "edge_types"
        tokens = [str(t).strip() for t in raw if str(t).strip()]
        if not tokens:
            return [], [f"{source} was empty, so only CALLS was walked — omit it for the default walk "
                        f"({', '.join(DEFAULT_IMPACT_EDGE_TYPES)})."]
        known = _known_impact_edge_types()
        requested, unknown, malformed = [], [], []
        for t in tokens:
            u = t.upper()
            if not all(c.isalpha() or c == "_" for c in u):
                malformed.append(t)
            elif u not in known:
                unknown.append(t)
            elif u not in requested:
                requested.append(u)
        notes = []
        if unknown or malformed:
            parts = ([f"unknown edge type(s) {', '.join(repr(t) for t in unknown)} match nothing"] if unknown else []) \
                + ([f"malformed edge type(s) {', '.join(repr(t) for t in malformed)} were dropped"] if malformed else [])
            notes.append(f"{source}: {'; '.join(parts)}. Known types: {', '.join(sorted(known))} "
                         "(CALLS is always walked).")
        return requested, notes
    except Exception:  # noqa: BLE001 — a note is never worth failing the answer for
        return [], []


def _impact_should_raise(exc: BaseException) -> bool:
    """Impact has a working one-hop fallback (query_callers), so — unlike the search arms — a
    feature/syntax ClientError DEGRADES rather than fails loud. Only an INSTANCE-level failure raises
    (never fabricate an empty blast radius from an outage): connectivity/auth/transient/OSError/
    DatabaseError, or a message that smells like a dropped connection."""
    try:
        from neo4j.exceptions import (ServiceUnavailable, SessionExpired, TransientError,
                                       AuthError, DatabaseError, ClientError)
    except Exception:
        return True
    if isinstance(exc, (ServiceUnavailable, SessionExpired, TransientError, AuthError, OSError,
                        DatabaseError)):
        return True
    if isinstance(exc, ClientError):
        # A Security error (the login lock after repeated failed logins — a plain ClientError, not an
        # AuthError — expired credentials, a forbidden action) is the INSTANCE refusing us, not an unsupported
        # query feature: the one-hop fallback would only log in once more and feed Neo4j's login lock.
        if ".Security." in str(getattr(exc, "code", "") or ""):
            return True
        return False   # unsupported var-length feature / syntax on older Neo4j -> degrade to one-hop
    msg = str(exc).lower()
    return any(s in msg for s in ("unable to connect", "connection refused", "connection reset",
                                  "connection lost", "defunct", "unavailable", "econnrefused",
                                  "cannot resolve", "broken pipe", "timed out", "no connection"))


def _is_memory_error(exc: BaseException) -> bool:
    """Neo4j refused the query for MEMORY (the transaction pool / per-transaction limit, or the heap):
    the query was too big for the instance — not an outage, so a smaller walk can still answer."""
    code = str(getattr(exc, "code", "") or "")
    if any(t in code for t in ("OutOfMemory", "MemoryLimit", "MemoryPool")):
        return True
    msg = str(exc).lower()
    return "memory" in msg and any(t in msg for t in ("dbms.memory.transaction", "db.memory.transaction",
                                                      "outofmemory", "memorypool"))


def _present_rel_types(session) -> "set | None":
    """The relationship types actually present in the graph (db.relationshipTypes()); None if the
    probe itself fails (older Neo4j) — callers then proceed with the requested set (a var-length
    pattern naming an absent type simply matches nothing, so this is only an optimisation). An
    INSTANCE-level failure re-raises: after a read timeout on a hung JVM, swallowing it only queued a
    second blocking query behind the first."""
    try:
        rows = session.run(_timed(session, "CALL db.relationshipTypes() YIELD relationshipType "
                                            "RETURN collect(relationshipType) AS types"))
        rec = _first(rows)
        if rec is not None:
            return {str(t).upper() for t in (rec["types"] or [])}
    except Exception as exc:  # noqa: BLE001
        if _impact_should_raise(exc):
            raise
        return None
    return None


def _count_symbol_matches(session, name: str, name_rx) -> "int | None":
    """How many graph nodes the symbol resolves to (the walk's own callee match): 0 means "not in the
    graph", so an empty answer can say which kind of empty it is. None when the answer is unreadable."""
    rec = _first(session.run(_timed(session, _CALLEE_MATCH + "RETURN count(callee) AS matched"),
                             name=name, name_rx=name_rx))
    try:
        return int(rec["matched"])
    except Exception:  # noqa: BLE001 — no record / a test double's row: unknown, not zero
        return None


# ---- module-qualified names (round 8, J6) ----------------------------------------------------------------
# `provision.is_bolt_up` / `retrieval._weighted_rrf` is how a Python, Go or Rust developer spells a function, but
# stored names carry no module (a function is `is_bolt_up`, a method `AuthBackoff.check`), so it matched nothing
# and impact answered "not in the graph" for a symbol with 37 dependents. When a QUALIFIED name matches no symbol
# DIRECTLY, it is read as <module>.<symbol>: the module is the defining FILE's stem (a dotted prefix names its
# directories: `build.provision.x` -> build/provision.<ext>), else a package DIRECTORY holding the file (Go
# `render.WriteJSON` -> render/json.go; a Python package's __init__; Rust `m::f` -> m/mod.rs). Stem readings are
# tried before directory readings, the longest module prefix first; segment names match case-SENSITIVELY
# (`Config.load` is not config.py). A direct match always wins, so an answer that worked before is unchanged.
_MODULE_READINGS_MAX = 6          # readings tried per rule (a long dotted name costs at most 2 x 6 count queries)
_SUGGEST_MAX = 3                  # did-you-mean candidates named for a qualified name that still matches nothing
_SUGGEST_CYPHER = ("MATCH (n) WHERE n.name = $last OR n.name ENDS WITH '.' + $last "
                   "RETURN DISTINCT n.name AS suggestion_name, n.source_file AS suggestion_file "
                   "ORDER BY suggestion_file, suggestion_name LIMIT $cap")


def _module_file_regex(module_parts: list, package_dir: bool) -> str:
    """A full-match (Java / Python) regex for a source_file the module path names: `a.b` -> '.../a/b.<ext>'
    (package_dir False) or '.../a/b/<file>' (True). Either path separator; segments match whole path parts."""
    path = r"[/\\]".join(re.escape(p) for p in module_parts)
    tail = r"[/\\][^/\\]+" if package_dir else r"\.[^/\\.]+"
    return r"(?:.*[/\\])?" + path + tail


def _module_readings(name: str) -> list:
    """[(module parts, symbol)] — `name` split at each qualifier separator, the LONGEST module prefix first
    ('build.provision.x' -> (['build','provision'], 'x'), then (['build'], 'provision.x')). Capped."""
    out = []
    for m in reversed(list(_QUALIFIER_SEP.finditer(name))):
        parts = [p.strip() for p in _QUALIFIER_SEP.split(name[:m.start()]) if p.strip()]
        rest = name[m.end():].strip().lstrip(".:")
        if parts and rest:
            out.append((parts, rest))
        if len(out) >= _MODULE_READINGS_MAX:
            break
    return out


def _count_module_matches(session, name: str, name_rx, file_rx: str) -> "tuple[int, list]":
    """(symbols `name` resolves to inside the files `file_rx` matches, up to 4 of those files)."""
    rec = _first(session.run(_timed(session, _CALLEE_MATCH_IN_FILE +
                                    "RETURN count(callee) AS matched, "
                                    "collect(DISTINCT callee.source_file)[0..4] AS files"),
                             name=name, name_rx=name_rx, file_rx=file_rx))
    try:
        n = int(rec["matched"])
    except Exception:  # noqa: BLE001 — unreadable: treat as no match
        return 0, []
    try:
        files = [str(f) for f in (rec["files"] or []) if f]
    except Exception:  # noqa: BLE001 — a record without the file list: the count alone
        files = []
    return n, files


def _resolve_module_name(session, name: str) -> "dict | None":
    """The module-qualified reading of a QUALIFIED `name` that matched no symbol directly (see above): {name,
    name_rx, file_rx, matched, files, module, package_dir} for the first reading that finds a symbol, else None.
    Best-effort: a query error that is not an instance failure (which re-raises) counts as no reading."""
    if not _QUALIFIER_SEP.search(name):
        return None
    readings = _module_readings(name)
    try:
        for package_dir in (False, True):
            for parts, rest in readings:
                rx = _impact_name_regex(rest)
                file_rx = _module_file_regex(parts, package_dir)
                n, files = _count_module_matches(session, rest, rx, file_rx)
                if n > 0:
                    return {"name": rest, "name_rx": rx, "file_rx": file_rx, "matched": n, "files": files,
                            "module": parts, "package_dir": package_dir}
    except Exception as exc:  # noqa: BLE001
        if _impact_should_raise(exc):
            raise
    return None


def _module_reading_note(shown: str, res: dict) -> str:
    files = res.get("files") or []
    where = ", ".join(files[:3]) + (", …" if len(files) > 3 else "")
    if res.get("package_dir"):
        what = f"in package directory {'/'.join(res['module'])!r}"
    else:
        what = f"in module {'.'.join(res['module'])!r}"
    return (f"{shown!r} names no symbol directly, so it was read as {res['name']!r} {what}"
            + (f" ({where})" if where else "") + " — the dependents are that symbol's.")


def _suggestions(session, name: str) -> str:
    """' Did you mean: 'x' (file), …?' — symbols named like the LAST segment of a qualified `name` that matched
    nothing, so "not in the graph" still points at what exists. '' when there are none or the probe fails."""
    last = [p for p in _QUALIFIER_SEP.split(name) if p.strip()]
    if len(last) < 2:
        return ""
    last = last[-1].strip()
    try:
        rows = list(session.run(_timed(session, _SUGGEST_CYPHER), last=last, cap=_SUGGEST_MAX + 1))
        seen = [(str(r["suggestion_name"]), str(r["suggestion_file"] or "?")) for r in rows]
    except Exception as exc:  # noqa: BLE001
        if _impact_should_raise(exc):
            raise
        return ""
    if not seen:
        return ""
    shown = ", ".join(f"{n!r} ({f})" for n, f in seen[:_SUGGEST_MAX])
    return f" Did you mean: {shown}{', …' if len(seen) > _SUGGEST_MAX else ''}?"


class ImpactTooExpensive(RuntimeError):
    """Even a 1-hop impact walk exhausted Neo4j's transaction memory (a hub name that matches very many
    symbols). Raised instead of the raw TransientError so it is never reported as an outage."""


class ImpactRows(list):
    """query_impact's answer: a plain list of dependent-row dicts (every existing consumer keeps working)
    plus the honesty metadata the CLI and the deepmap_impact MCP tool surface — `matched` (graph nodes
    the symbol resolved to; None when unknown), `truncated` (more dependents exist beyond `limit`),
    `max_hops` (the depth actually walked) and human-readable `notes`."""

    def __init__(self, rows=(), matched=None, truncated=False, max_hops=None, notes=()):
        super().__init__(rows)
        self.matched = matched
        self.truncated = truncated
        self.max_hops = max_hops
        self.notes = list(notes)


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


_METHOD_CAVEAT = ("Callers of a method are often only partly resolved: calls through a receiver or an instance "
                  "(obj.m(), self.m()), an interface/trait or a callback may be missing — treat these dependents as "
                  "a lower bound and confirm with Grep before relying on the list being complete.")


def _method_shaped(symbol: str, rows) -> bool:
    """The answer is about a METHOD — a qualified symbol (Class.method / Owner::m / Class#m), or a callee stored
    dotted (`Context.Next`) — whose callers the extractor resolves least completely (T5#0)."""
    name = str(symbol).strip().replace("#", ".").lstrip(".:")
    if _QUALIFIER_SEP.search(name):
        return True
    return any("." in str(r.get("callee") or "").lstrip(".") for r in rows if isinstance(r, dict))


def _impact_rows(rows, symbol: str, limit: int, matched, max_hops, notes=(), shape=None) -> ImpactRows:
    """Dedupe (the Cypher already DISTINCTs; this is the safety net), trim the one look-ahead row to
    `limit`, and attach the notes that keep a short, empty or partial answer honest. `shape` is the name the
    method caveat judges (the resolved symbol of a module-qualified name; default `symbol`)."""
    rows = _dedup_impact_rows(rows)
    notes = list(notes)
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
        notes.append(f"Showing the first {limit} dependents of {symbol!r}; more exist — raise the limit, "
                     "or narrow the symbol to a qualified Class.method name.")
    elif not rows:
        found = (f"{symbol!r} is in the graph ({_plural(matched, 'matching symbol')}) but no dependents "
                 "were found." if matched else f"No dependents of {symbol!r} were found.")
        notes.append(found + " That is NOT proof nothing uses it: calls through a receiver or method "
                     "(obj.m(), self.m()), a trait/interface, a callback or a namespace are often not "
                     "resolved by the extractor — verify with Grep or by reading the call sites before "
                     "treating it as unused.")
    if rows and _method_shaped(symbol if shape is None else shape, rows):
        notes.append(_METHOD_CAVEAT)   # a NON-empty answer can be partial too (Context.Next: 2 of its 4 callers)
    return ImpactRows(rows, matched=matched, truncated=truncated, max_hops=max_hops, notes=notes)


# When several edge types attach the same dependent at the same hop + confidence, `via` names the most
# severe wiring: a cascading delete / template render beats a call (default 1), which beats a bare ORM
# association. Only the traversed types are ranked, so the default walk's Cypher never names an
# association type (the benchmark-gate invariant is checked on the query text).
_VIA_SEVERITY = {"CASCADES": 0, "RENDERS": 0, "HAS_MANY": 2, "REFERENCES": 2}


def _impact_walk(session, rel_pattern: str, max_hops: int, name: str, name_rx, limit: int,
                 file_rx=None) -> list[dict]:
    """One reverse-reachability walk, returning at most `limit + 1` distinct rows (the extra row only
    tells the caller the answer was truncated). `rel_pattern` holds sanitized [A-Z_] type tokens only.
    `file_rx` restricts the callee to one source file (a module-qualified name); None = the unchanged match."""
    ranks = " ".join(f"WHEN '{t}' THEN {r}" for t, r in _VIA_SEVERITY.items() if t in rel_pattern.split("|"))
    via_rank = f"CASE via {ranks} ELSE 1 END" if ranks else "1"
    params = _match_params(name, name_rx, file_rx)
    params["limit"] = limit
    rows = session.run(_timed(session,
        _callee_match(file_rx) +
        f"MATCH path = (caller)-[rels:{rel_pattern}*1..{max_hops}]->(callee) "
        "WHERE caller <> callee "
        "WITH caller, callee, length(path) AS hop, type(rels[0]) AS via, "
        "     [r IN rels | type(r)] AS via_path, "
        "     reduce(w = 0, r IN rels | CASE coalesce(r.confidence, 'INFERRED') "
        "         WHEN 'AMBIGUOUS' THEN 2 "
        "         WHEN 'INFERRED' THEN (CASE WHEN w < 1 THEN 1 ELSE w END) ELSE w END) AS worstOrd "
        # Equal-hop, equal-confidence paths tie-break on wiring severity, so a dependent that both cascades
        # and references its owner reads `via CASCADES`, not whichever edge Neo4j happened to expand first.
        f"WITH caller, callee, hop, via, via_path, worstOrd, {via_rank} AS viaRank "
        "ORDER BY hop ASC, worstOrd ASC, viaRank ASC, via ASC "
        "WITH caller, callee, "
        "     collect({hop:hop, via:via, via_path:via_path, worstOrd:worstOrd})[0] AS best "
        # DISTINCT before LIMIT: a name resolving to N same-named nodes projects N identical rows per
        # caller, which used to fill the LIMIT window with copies of the first caller (10 of 52 shown).
        "RETURN DISTINCT caller.name AS caller, labels(caller)[0] AS caller_type, "
        "       caller.source_file AS file, caller.start_line AS line, "
        "       CASE best.worstOrd WHEN 0 THEN 'EXTRACTED' WHEN 1 THEN 'INFERRED' "
        "            ELSE 'AMBIGUOUS' END AS confidence, "
        "       callee.name AS callee, best.hop AS hop, best.via AS via, best.via_path AS via_path "
        "ORDER BY CASE confidence WHEN 'EXTRACTED' THEN 0 WHEN 'INFERRED' THEN 1 ELSE 2 END, "
        "         hop, caller, file, line, callee LIMIT $limit + 1"), **params)
    return [dict(r) for r in rows]


def _dedup_impact_rows(rows: list[dict]) -> list[dict]:
    """Collapse rows whose ENTIRE projected dict is identical, order-preserving / keep-first.

    A callee NAME that resolves to several distinct nodes projects a BYTE-IDENTICAL impact row
    (same caller/file/line/confidence/callee/hop/via/via_path) — a display artifact, not distinct
    information. This removes ONLY those genuinely-redundant rows: any row differing in ANY field
    (a different callee, hop, route/via_path, confidence) is kept, so no distinct dependent is ever
    dropped from the CLI output or the deepmap_impact MCP payload. Purely a de-duplication of output
    rows — it touches no arm/fusion/ranking (deliberately named clear of those symbols)."""
    seen = set()
    out: list[dict] = []
    for r in rows:
        try:
            if isinstance(r, dict):
                key = tuple((k, tuple(v) if isinstance(v, list) else v)
                            for k, v in sorted(r.items()))
            else:
                key = ("\x00nondict", repr(r))
            hash(key)
        except TypeError:
            key = ("\x00repr", repr(r))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def query_impact(driver, symbol: str, limit: int = 30, edge_types=None, max_hops=None) -> list[dict]:
    """Reverse reachability — what REACHES `symbol` — across :CALLS + framework/runtime edges, up to
    `max_hops` deep, each dependent labeled with the wiring that attaches it. A superset of
    query_callers: rows keep {caller, caller_type, file, line, confidence, callee} and ADD `hop`
    (shortest depth), `via` (the edge type incident to the dependent), `via_path` (the edge types along
    the representative path). `confidence` is the WEAKEST edge on the path (a chain is only as firm as
    its least-certain hop). Ordered trust-first, then hop, then a stable caller/file/line tie-break.

    Degrade-safe (T10): pre-filters the type list to what the graph actually has (CALLS always kept, so
    a pre-Track-C graph collapses to CALLS-only multi-hop); on an unsupported-feature ClientError (or a
    server-side timeout) falls back to one-hop query_callers; a walk that exhausts Neo4j's transaction
    memory (a hub name) is retried at 1 hop; only an instance-down error fails loud.

    Returns an ImpactRows (a list) whose `notes` keep a short or empty answer honest: not in the graph
    vs. in the graph with no resolved dependents, truncated at `limit`, or walked at reduced depth."""
    types, max_hops = _resolve_impact_params(edge_types, max_hops)
    # What was explicitly asked for (an unknown profile / edge type is named, never silently ignored).
    requested, notes = _impact_request(edge_types)
    # F10d + normalisation (query INPUT only): trim padding, Ruby 'Class#method' -> stored 'Class.method',
    # THEN drop a leading '.' / ':' ('.foo', '#foo', '::foo'). The one-hop query_callers fallback below
    # applies the same normalization independently.
    shown = symbol.strip()
    name = shown.replace("#", ".").lstrip(".:")
    name_rx = _impact_name_regex(name)
    file_rx = None          # set only for a module-qualified reading (round 8, J6): the callee's source file
    matched, reduced_from = None, None
    try:
        with driver.session() as s:
            matched = _count_symbol_matches(s, name, name_rx)
            if matched == 0:
                # A QUALIFIED name with no direct match may be module-qualified (`provision.is_bolt_up`): read
                # it as <symbol> in that module's file before calling it absent. A bare name skips this.
                reading = _resolve_module_name(s, name)
                if reading is not None:
                    name, name_rx, file_rx = reading["name"], reading["name_rx"], reading["file_rx"]
                    matched = reading["matched"]
                    notes.append(_module_reading_note(shown, reading))
            if matched == 0:   # not in the graph: say so, and skip the (expensive) walk entirely
                return ImpactRows([], matched=0, max_hops=max_hops, notes=notes + [
                    f"{shown!r} is not in the graph (no indexed symbol has that name) — check the spelling "
                    "or try the bare method name; if its language or directory is not indexed, search "
                    "the source with Grep instead." + _suggestions(s, name)])
            present = _present_rel_types(s)
            if present is not None:
                filtered = [t for t in types if t in present] or ["CALLS"]
                absent = [t for t in requested if t != "CALLS" and t not in present]
                if absent:   # a requested type the graph does not hold walked nothing: say so
                    notes.append(f"Requested edge type(s) {', '.join(absent)} are not in this graph, so no "
                                 "dependent was reached through them.")
            else:
                filtered = types
            rel_pattern = "|".join(filtered)
            try:
                rows = _impact_walk(s, rel_pattern, max_hops, name, name_rx, limit, file_rx)
            except Exception as exc:  # noqa: BLE001
                if not _is_memory_error(exc):
                    raise
                if max_hops == 1:
                    raise ImpactTooExpensive(_too_expensive(shown, matched)) from exc
                reduced_from = max_hops
        if reduced_from is not None:
            # A hub name blew Neo4j's transaction memory on the deep walk: answer with the bounded 1-hop
            # walk in a fresh session and say so — never report it as an outage.
            with driver.session() as s:
                try:
                    rows = _impact_walk(s, rel_pattern, 1, name, name_rx, limit, file_rx)
                except Exception as exc:  # noqa: BLE001
                    if _is_memory_error(exc):
                        raise ImpactTooExpensive(_too_expensive(shown, matched)) from exc
                    raise
            max_hops = 1
    except ImpactTooExpensive:
        raise
    except Exception as exc:  # noqa: BLE001
        if _impact_should_raise(exc):
            raise
        # unsupported multi-hop feature / timeout / benign query error -> one-hop CALLS answer (never crash
        # impact), fetched one row long so a truncated fallback is detected too
        code = str(getattr(exc, "code", "") or "")
        why = (f"timed out after {_impact_timeout_s():g}s" if "TimedOut" in code or "Timeout" in code
               else type(exc).__name__)
        # A module-qualified reading keeps its symbol + file restriction in the one-hop answer too.
        one_hop = (query_callers(driver, symbol, limit + 1) if file_rx is None
                   else query_callers(driver, name, limit + 1, in_file=file_rx))
        return _impact_rows(one_hop, shown, limit, matched, 1, notes + [
            f"The multi-hop walk was unavailable ({why}); showing direct :CALLS callers (1 hop) only."],
            shape=None if file_rx is None else name)
    if reduced_from is not None:
        notes.append(f"The {reduced_from}-hop walk exhausted Neo4j's transaction memory{_shared(matched)}, "
                     "so only direct (1-hop) dependents are shown — query a qualified Class.method name "
                     "for a deeper walk.")
    return _impact_rows(rows, shown, limit, matched, max_hops, notes, shape=None if file_rx is None else name)


def _shared(matched) -> str:
    """' (N symbols share this name)' — why a name is a hub — or '' when it isn't one / is unknown."""
    return f" ({matched} symbols share this name)" if matched and matched > 1 else ""


def _too_expensive(symbol: str, matched) -> str:
    return (f"impact on {symbol!r} is too expensive for this Neo4j instance: even a 1-hop walk exhausted "
            f"its transaction memory{_shared(matched)}. Query a qualified Class.method name instead (e.g. "
            f"'MyClass.{symbol.strip().replace('#', '.').lstrip('.:').rsplit('.', 1)[-1] or 'method'}'); a "
            "larger Neo4j heap (DEEPMAP_NEO4J_HEAP_MAX, applied when the instance restarts) also raises "
            "the limit.")


# Per-label vector indexes (FD-5). (label, index_name). FTS arm spans labels already.
VECTOR_INDEXES: list[tuple[str, str]] = [
    ("Function", "deepmap_fn_embeddings"),
    ("Method", "deepmap_method_embeddings"),
    ("Class", "deepmap_class_embeddings"),
    ("Module", "deepmap_module_embeddings"),
    ("Route", "deepmap_route_embeddings"),
    # Community is intentionally excluded from code-entity search (different question class).
]

_SEARCH_CLAUSE_NOTED = False


def _note_search_clause_fallback(exc: BaseException) -> None:
    """_detect_search_clause chose the db.index.vector.queryNodes() procedure path. On Neo4j 5.x the probe's
    Cypher syntax error is the EXPECTED answer — silent (the note used to print on every retriever construction,
    i.e. on every CLI search); any other probe failure is reported once per process. DEEPMAP_DEBUG=1 reports the
    expected case too (once). Never raises; the path choice itself is _detect_search_clause's, unchanged."""
    global _SEARCH_CLAUSE_NOTED
    try:
        if _SEARCH_CLAUSE_NOTED:
            return
        expected = "SyntaxError" in str(getattr(exc, "code", "") or "") or type(exc).__name__ == "CypherSyntaxError"
        if expected and not _env_on("DEEPMAP_DEBUG"):
            return
        _SEARCH_CLAUSE_NOTED = True
        detail = " ".join(str(exc).split())[:200]
        print(f"[deepmap retrieval] SEARCH clause not supported ({type(exc).__name__}: {detail}) — "
              "using db.index.vector.queryNodes() fallback", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


# Default per-arm weights for Weighted RRF (FD-6). Equal weights == prior plain RRF.
# Calibrate via the benchmark (do not guess); equal keeps the default non-regressing.
DEFAULT_ARM_WEIGHTS: dict[str, float] = {
    "vector": 1.0,
    "bm25": 1.0,
    "graph_weighted": 1.0,
}


class DeepMapRetriever:
    def __init__(self, driver, embed_fn, arm_weights: dict[str, float] | None = None):
        self.driver = driver
        self.embed_fn = embed_fn
        self.arm_weights = dict(DEFAULT_ARM_WEIGHTS)
        if arm_weights:
            self.arm_weights.update(arm_weights)
        self._use_search_clause = self._detect_search_clause()

    def _detect_search_clause(self) -> bool:
        """Check if Cypher 25 SEARCH clause is supported (probe with LIMIT 0)."""
        try:
            with self.driver.session() as s:
                s.run("""
                    MATCH (n:Function)
                    SEARCH n IN (VECTOR INDEX deepmap_fn_embeddings FOR $vec LIMIT 1)
                    SCORE AS score
                    RETURN n.name LIMIT 0
                """, vec=[0.0] * 768)
            return True
        except Exception as exc:
            _note_search_clause_fallback(exc)   # silent for the expected 5.x syntax error; else once per process
            return False

    def impact(self, symbol: str, limit: int = 30, edge_types=None) -> list[dict]:
        """What REACHES `symbol` — multi-hop reverse reachability across :CALLS + framework/runtime
        edges (routes/DI/events/jobs/inheritance), each hop confidence-graded. See query_impact;
        a pre-Track-C graph collapses to CALLS-only multi-hop; only an error path degrades to one hop.

        `edge_types` (optional) overrides the traversed edge set for this call — e.g. add the ORM
        associations HAS_MANY/REFERENCES for a Rails/ORM blast radius. Default None resolves from
        DEEPMAP_IMPACT_EDGES / DEEPMAP_IMPACT_PROFILE / the frozen default set."""
        return query_impact(self.driver, symbol, limit, edge_types=edge_types)

    def _embed_or_none(self, query: str):
        """Embed the query, or None if the model is unavailable — so search degrades to FTS-only
        instead of failing entirely (the spec's --no-embeddings graceful-degradation path). An
        embedding outage is NOT a graph/DB outage: BM25 fulltext still answers without a vector."""
        try:
            return self.embed_fn(query)
        except Exception as exc:   # noqa: BLE001 — model load/encode failure -> degrade, don't die
            print(f"[deepmap retrieval] embedding unavailable ({exc}); FTS-only degraded search",
                  file=sys.stderr)
            return None

    def search(self, query: str, top_k: int = 15) -> list[dict]:
        """Run three-arm retrieval (multi-label) with Weighted-RRF fusion (implemented directly in _weighted_rrf; not ranx — see module docstring).

        If the embedding model is unavailable, the two vector arms are skipped and search runs
        BM25-only (degraded but functional) rather than failing."""
        qvec = self._embed_or_none(query)
        # P1-7 (opt-in, default OFF): a per-arm candidate FLOOR so a small top_k still fetches a deep
        # enough pool that top-3 is a stable prefix of top-8 (RRF prefix instability). DEEPMAP_CANDIDATE_FLOOR
        # defaults to 0 -> max(top_k*3, 0) == top_k*3, byte-identical ranking behaviour to today; the
        # default flip is benchmark-gated / STAGE-0 oracle re-freeze.
        fetch_k = max(top_k * 3, _candidate_floor())

        # Vector + graph-weighted arms need the query vector; skip them under embedding outage.
        vec = self._arm_vector(qvec, fetch_k) if qvec is not None else []
        fts = self._arm_fulltext(query, fetch_k)
        graph = self._arm_graph_weighted(qvec, fetch_k) if qvec is not None else []

        # Build per-arm score maps, skipping empty arms. Track arm NAMES of surviving
        # arms so weights align by arm, not position (FD-8.1).
        arms = [
            ("vector", vec),
            ("bm25", fts),
            ("graph_weighted", graph),
        ]
        runs_by_arm: list[tuple[str, dict[str, float]]] = []
        for name, results in arms:
            if results:
                # Best score per id within an arm (guard against intra-arm dupes).
                scores: dict[str, float] = {}
                for r in results:
                    rid = r["id"]
                    if rid not in scores or r["score"] > scores[rid]:
                        scores[rid] = r["score"]
                runs_by_arm.append((name, scores))

        if not runs_by_arm:
            return []

        if len(runs_by_arm) == 1:
            # Single arm — no fusion needed, just take top_k by arm score.
            _name, scores = runs_by_arm[0]
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            top_ids = [doc_id for doc_id, _ in ranked[:top_k]]
            return self._hydrate(top_ids, scores)

        # Weighted RRF over surviving arms (equal weights == plain RRF; FD-6).
        fused = _weighted_rrf(runs_by_arm, self.arm_weights)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        top_ids = [doc_id for doc_id, _ in ranked[:top_k]]
        return self._hydrate(top_ids, fused)

    # -- ARM 1: Vector search (multi-label) ----------------------------------

    def _arm_vector(self, qvec, k):
        """Pool vector hits across every per-label index, then return top fetch_k."""
        pooled: list[dict] = []
        for label, index_name in VECTOR_INDEXES:
            try:
                if self._use_search_clause:
                    pooled += self._arm_vector_search(qvec, k, label, index_name)
                else:
                    pooled += self._arm_vector_procedure(qvec, k, index_name)
            except Exception as exc:
                if _is_fatal_db_error(exc):
                    raise   # instance-down/auth: fail loud, never a fabricated empty result (T10)
                # A missing/empty per-label index must not kill the whole arm.
                print(f"[deepmap retrieval] vector arm: index {index_name} skipped ({exc})",
                      file=sys.stderr)
        pooled.sort(key=lambda r: r["score"], reverse=True)
        return pooled[:k]

    def _arm_vector_search(self, qvec, k, label, index_name):
        """SEARCH clause (Cypher 25)."""
        with self.driver.session() as s:
            r = s.run(f"""
                MATCH (node:{label})
                SEARCH node IN (VECTOR INDEX {index_name} FOR $vec LIMIT $k)
                SCORE AS score
                RETURN elementId(node) AS id, node.name AS name,
                       labels(node)[0] AS type, node.source_file AS file,
                       node.start_line AS line, node.body_summary AS summary, score
            """, vec=qvec, k=k)
            return [dict(rec) for rec in r]

    def _arm_vector_procedure(self, qvec, k, index_name):
        """db.index.vector.queryNodes fallback (label is implied by the index)."""
        with self.driver.session() as s:
            r = s.run("""
                CALL db.index.vector.queryNodes($index_name, $k, $vec)
                YIELD node, score
                RETURN elementId(node) AS id, node.name AS name,
                       labels(node)[0] AS type, node.source_file AS file,
                       node.start_line AS line, node.body_summary AS summary, score
            """, index_name=index_name, vec=qvec, k=k)
            return [dict(rec) for rec in r]

    # -- ARM 2: BM25 fulltext (already multi-label via deepmap_code_fts) ------

    def _arm_fulltext(self, query, k):
        """BM25 fulltext (not deprecated — stable API). Spans all code labels."""
        fts_q = _build_fts_query(query)   # Lucene-escaped — a code-shaped query can't crash the parse
        if not fts_q:
            return []
        try:
            with self.driver.session() as s:
                r = s.run("""
                    CALL db.index.fulltext.queryNodes('deepmap_code_fts', $q, {limit: $k})
                    YIELD node, score
                    RETURN elementId(node) AS id, node.name AS name,
                           labels(node)[0] AS type, node.source_file AS file,
                           node.start_line AS line, node.body_summary AS summary, score
                """, q=fts_q, k=k)
                return [dict(rec) for rec in r]
        except Exception as exc:
            # A malformed-query PARSE error is benign at the arm level (the query, not the instance,
            # is the problem) — degrade to the other arms, never crash the whole search (HARD-2 / the
            # never-crash-on-bad-input rule). Checked BEFORE _is_fatal_db_error so it isn't re-raised.
            if _is_fts_parse_error(exc):
                print(f"[deepmap retrieval] fulltext arm: query parse error, skipped ({exc})",
                      file=sys.stderr)
                return []
            if _is_fatal_db_error(exc):
                raise   # instance-down/auth: fail loud (T10)
            # A missing FTS index is benign — let the vector/graph arms still answer.
            print(f"[deepmap retrieval] fulltext arm skipped ({exc})", file=sys.stderr)
            return []

    # -- ARM 3: Graph-weighted vector (multi-label) --------------------------

    def _arm_graph_weighted(self, qvec, k):
        """Vector score weighted by call-graph centrality, pooled across labels."""
        pooled: list[dict] = []
        for label, index_name in VECTOR_INDEXES:
            try:
                pooled += self._arm_graph_weighted_one(qvec, k, label, index_name)
            except Exception as exc:
                if _is_fatal_db_error(exc):
                    raise   # instance-down/auth: fail loud (T10)
                print(f"[deepmap retrieval] graph arm: index {index_name} skipped ({exc})",
                      file=sys.stderr)
        pooled.sort(key=lambda r: r["score"], reverse=True)
        return pooled[:k]

    def _arm_graph_weighted_one(self, qvec, k, label, index_name):
        if self._use_search_clause:
            cypher = f"""
                MATCH (node:{label})
                SEARCH node IN (VECTOR INDEX {index_name} FOR $vec LIMIT $k)
                SCORE AS score
                OPTIONAL MATCH (node)<-[:CALLS]-(caller)
                WITH node, score, count(caller) AS inDegree
                RETURN elementId(node) AS id, node.name AS name,
                       labels(node)[0] AS type, node.source_file AS file,
                       node.start_line AS line, node.body_summary AS summary,
                       score * (1.0 + log(1.0 + toFloat(inDegree))) AS score
                ORDER BY score DESC LIMIT $k
            """
        else:
            cypher = """
                CALL db.index.vector.queryNodes($index_name, $k, $vec)
                YIELD node, score AS rawScore
                OPTIONAL MATCH (node)<-[:CALLS]-(caller)
                WITH node, rawScore, count(caller) AS inDegree
                RETURN elementId(node) AS id, node.name AS name,
                       labels(node)[0] AS type, node.source_file AS file,
                       node.start_line AS line, node.body_summary AS summary,
                       rawScore * (1.0 + log(1.0 + toFloat(inDegree))) AS score
                ORDER BY score DESC LIMIT $k
            """
        with self.driver.session() as s:
            r = s.run(cypher, vec=qvec, k=k, index_name=index_name)
            return [dict(rec) for rec in r]

    # -- Hydration (label-agnostic; unchanged) -------------------------------

    def _hydrate(self, top_ids: list[str], scores: dict) -> list[dict]:
        """Fetch full node data for fused results (matches any label)."""
        if not top_ids:
            return []
        with self.driver.session() as s:
            r = s.run("""
                UNWIND $ids AS nid
                MATCH (n) WHERE elementId(n) = nid
                RETURN elementId(n) AS id, n.name AS name,
                       labels(n)[0] AS type, n.source_file AS file,
                       n.start_line AS line, n.body_summary AS summary,
                       coalesce(n.confidence, 'EXTRACTED') AS confidence
            """, ids=top_ids)
            results = []
            for rec in r:
                d = dict(rec)
                d["score"] = scores.get(d["id"], 0.0)
                results.append(d)
        return sorted(results, key=lambda x: x["score"], reverse=True)
