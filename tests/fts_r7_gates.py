#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the BM25 query builder, round 7: I1 (byte-identity for every query Lucene ACCEPTS) + the
two gated FTS flags of I2 + a hash lock on the frozen ranking path.

THE DEFECT (round-6 review, reproduced live READ-ONLY on bolt 7689): the F4 builder emitted the old all-fuzzy
string only for queries of <= 10 terms and a budgeted string (tail terms EXACT) for every longer one — so it
changed the BM25 arm for every query of 11+ terms, including the ones Lucene accepted. Measured on this repo's
own index: ALL 49 benchmark prompts (12-207 terms) are ACCEPTED by Lucene as the pre-F4 string, and F4 changed
the top-15 BM25 hits of all 49. A tight a-priori bound is impossible (a fuzzy term's cost is the number of
indexed terms within edit distance 2 — up to 50 per populated field — which only the index knows; Lucene also
collapses repeated terms), so the protection moved into the one module-level helper the byte-frozen
`_arm_fulltext` already calls: `_build_fts_query` VALIDATES the original string against the index the arm is
about to query (a limit-1 read on the arm's own driver) and only a string Lucene actually rejects
(TooManyClauses / TooComplexToDeterminize / ParseException) gets the bounded variant.

THE FIX, locked here:
  A  accepted -> the arm sends the pre-F4 original, byte for byte (49 benchmark prompts, every STAGE-0 oracle
     query, a seeded corpus of 11-60-term queries); without a standalone reserved word that is HEAD's string.
  B  rejected (the live TooManyClauses / TooComplexToDeterminize texts) -> the original was tried first, then a
     bounded variant the index accepts; the arm answers.
  C  the validation is safe: none for a provably-safe query (<= T terms, none overlong); never for a string
     known to be dangerous (a > 2000-char pure-ASCII fuzzy term exhausted the heap live) or certain to fail
     (> 1024 terms); an instance-level error fails loud like the arm; a benign error / a timeout keeps the
     original; the probe is a timed neo4j.Query; a direct call (no arm) keeps the a-priori builder;
     DEEPMAP_FTS_PROBE=0 opts out.
  D  DEEPMAP_FTS_SPLIT_CODE_TOKENS=1 (I2, default OFF): pasted calls / punctuated tokens split into their
     identifier pieces (+ a qualified name kept whole); OFF is byte-identical.
  E  DEEPMAP_FTS_SHORT_NAME=1 (I2, default OFF): the clause-cost model covers the extra populated `short_name`
     field (index side: W6); OFF is byte-identical; a server whose flag disagrees with the index still answers.
  F  the frozen ranking functions are byte-identical to the v0.27.4 base (sha256 of each AST source segment).
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import json
import os
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
    spec = importlib.util.spec_from_file_location(f"dm_{name}_fts_r7", BUILD / f"{name}.py")
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


_FLAGS = ("DEEPMAP_FTS_SPLIT_CODE_TOKENS", "DEEPMAP_FTS_SHORT_NAME", "DEEPMAP_FTS_PROBE", "DEEPMAP_FTS_PROBE_TIMEOUT_S")
_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')
_KEYWORDS = {"AND", "OR", "NOT", "TO"}


def _head_builder(query: str) -> str:
    """HEAD's (v0.27.4) builder, verbatim — the string the frozen ranking was measured with."""
    terms = []
    for t in query.split():
        if len(t) <= 2 or not any(ch.isalnum() for ch in t):
            continue
        esc = "".join("\\" + c if c in _SPECIAL else c for c in t)
        terms.append(f"{esc}~2")
    return " ".join(terms)


def _orig_builder(query: str) -> str:
    """The pre-F4 original: HEAD's builder + the round-1 reserved-word fix (a standalone AND/OR/NOT/TO is a
    ParseException in HEAD's string, i.e. a string Lucene REJECTS — the only way it may differ from HEAD)."""
    terms = []
    for t in query.split():
        if len(t) <= 2 or not any(ch.isalnum() for ch in t):
            continue
        if t in _KEYWORDS:
            t = t.lower()
        terms.append("".join("\\" + c if c in _SPECIAL else c for c in t) + "~2")
    return " ".join(terms)


def _f4_reference(R, query: str) -> str:
    """The round-4 (F4) builder, verbatim (constants from the module) — what a DIRECT call (no arm, so no
    validation) must keep returning with both I2 flags OFF."""
    terms = []
    for t in query.split():
        if len(t) <= 2 or not any(ch.isalnum() for ch in t):
            continue
        if t in _KEYWORDS:
            t = t.lower()
        esc = "".join("\\" + c if c in _SPECIAL else c for c in t)
        terms.append((esc, t))
    if len(terms) <= 10 and all(R._fts_fuzzy_ok(raw) for _esc, raw in terms):
        return " ".join(f"{esc}~2" for esc, _raw in terms)
    uniq = list(dict.fromkeys(terms))[:4096]
    cost = [4 * R._fts_token_bound(raw) for _esc, raw in uniq]
    may_fuzz = [R._fts_fuzzy_ok(raw) for _esc, raw in uniq]
    best = None
    for n_fuzzy in range(min(10, sum(may_fuzz)), -1, -1):
        left = 1000 - n_fuzzy * 100
        if left < 0:
            continue
        out, fuzzy_left, skipped = [], n_fuzzy, None
        for (esc, raw), c, fz in zip(uniq, cost, may_fuzz):
            if fz and fuzzy_left:
                out.append(f"{esc}~2")
                fuzzy_left -= 1
            elif c <= left:
                out.append(esc)
                left -= c
            elif skipped is None:
                skipped = (len(out), raw)
        if best is None or len(out) > best[0]:
            best = (len(out), out, left, skipped)
        if len(out) == len(uniq):
            break
    if best is None:
        return ""
    _kept, out, left, skipped = best
    if skipped is not None and left >= 4:
        pos, raw = skipped
        prefix = R._fts_prefix_within(raw, left // 4)
        if prefix in _KEYWORDS:
            prefix = prefix.lower()
        if len(prefix) > 2 and any(ch.isalnum() for ch in prefix):
            out = out[:pos] + ["".join("\\" + c if c in _SPECIAL else c for c in prefix)] + out[pos:]
    return " ".join(out)


# ---- a fake Lucene index (the live failure texts, captured READ-ONLY on bolt 7689) ----------------------
_TOO_MANY = ("Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
             "org.apache.lucene.search.IndexSearcher$TooManyClauses: maxClauseCount is set to 1024")
_TOO_COMPLEX = ("Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
                "org.apache.lucene.util.automaton.TooComplexToDeterminizeException: Determinizing automaton with "
                "100140 states and 202118 transitions would require more than 10000 effort.")
_PARSE = ("Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
          "org.apache.lucene.queryparser.classic.ParseException: Encountered \" <AND> \"AND \"\" at line 1")
_NO_INDEX = ("Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
             "java.lang.IllegalArgumentException: There is no such fulltext schema index: deepmap_code_fts")


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


def _std_tokens(text: str) -> int:
    """Token count of the standard analyzer (a coarse, never-undercounting model is enough here)."""
    n = 0
    for ch in text:
        if ord(ch) >= 0x2E80 or unicodedata.category(ch) == "So":
            n += 1
    return n + len(re.findall(r"[^\W⺀-\U0010FFFF]+(?:[.:',][^\W⺀-\U0010FFFF]+)*", text))


def _worst_leaves(fts: str, fields: int, populated: int) -> int:
    leaves = 0
    for t in fts.split():
        if t.endswith("~2") and not t.endswith("\\~2"):
            leaves += populated * 50
        else:
            leaves += fields * max(1, _std_tokens(_unescape(t)))
    return leaves


def _reject(fts: str, mode: str, fields: int = 4, populated: int = 2) -> "str | None":
    """Lucene's verdict on `fts` in the given index model: None = accepted, else the failure text."""
    terms = fts.split()
    for t in terms:
        base = t[:-2] if t.endswith("~2") else t
        if base in _KEYWORDS:
            return _PARSE
        if t.endswith("~2") and not t.endswith("\\~2"):
            raw = _unescape(base)
            if not raw.isascii() and len(raw.encode("utf-8", "surrogatepass")) >= 810:
                return _TOO_COMPLEX
    if len(terms) > 1024:
        return _TOO_MANY
    if mode == "accept":
        return None
    if _worst_leaves(fts, fields, populated) > 1024:
        return _TOO_MANY
    return None


class _Result(list):
    def single(self):
        return self[0] if self else None


def _client_error(msg: str, code: str = "Neo.ClientError.Procedure.ProcedureCallFailed"):
    from neo4j.exceptions import ClientError
    exc = ClientError(msg)
    exc._neo4j_code = code
    exc._message = msg
    return exc


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, parameters=None, **params):
        text = getattr(cypher, "text", cypher)
        kind = "probe" if "count(node)" in text else "arm"
        q = params.get("q", "")
        self.drv.log.append((kind, q, cypher))
        forced = self.drv.probe_error if kind == "probe" else self.drv.arm_error
        if forced is not None:
            raise forced
        why = _reject(q, self.drv.mode, self.drv.fields, self.drv.populated)
        if why:
            raise _client_error(why)
        if kind == "probe":
            return _Result([{"n": 1}])
        return _Result([{"id": "n1", "name": "hit", "type": "Function", "file": "a.py", "line": 1,
                         "summary": "", "score": 1.0}])


class _Driver:
    def __init__(self, mode="accept", fields=4, populated=2, probe_error=None, arm_error=None):
        self.mode, self.fields, self.populated = mode, fields, populated
        self.probe_error, self.arm_error = probe_error, arm_error
        self.log = []

    def session(self, **kw):
        return _Session(self)

    def sent(self):
        """The strings the ARM (not the probe) sent to Lucene."""
        return [q for kind, q, _c in self.log if kind == "arm"]

    def probes(self):
        return [q for kind, q, _c in self.log if kind == "probe"]


def _arm(R, drv, query, k=45):
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)   # no SEARCH-clause probe: the BM25 arm is under test
    ret.driver = drv
    return ret._arm_fulltext(query, k)


def _prompts() -> list:
    try:
        import yaml
        d = yaml.safe_load((REPO / "benchmark" / "questions.yaml").read_text(encoding="utf-8"))
        return [q["prompt"] for q in d["questions"]]
    except Exception as exc:  # noqa: BLE001
        check("benchmark/questions.yaml loads (PyYAML)", False, str(exc))
        return []


def _oracle_queries() -> list:
    qs = []
    for f in [REPO / "tests" / "stage0_real_oracle.json", *sorted((REPO / "tests" / "oracles").glob("*.json"))]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and isinstance(d.get("queries"), dict):
            qs += list(d["queries"])
        elif isinstance(d, list) and all(isinstance(x, str) for x in d):
            qs += d
    return qs


# ---------------------------------------------------------------------------------------------------------
def gate_accepted_is_original(R) -> None:
    print("\n-- A: a string Lucene ACCEPTS reaches it byte for byte (the pre-F4 original) --")
    prompts = _prompts()
    check("the 49 benchmark prompts are loaded", len(prompts) == 49, str(len(prompts)))
    bad_orig, bad_head, no_rows, heads = [], [], [], 0
    for p in prompts:
        drv = _Driver("accept")
        rows = _arm(R, drv, p)
        sent = drv.sent()
        if sent != [_orig_builder(p)]:
            bad_orig.append(p[:50])
        if not any(w in _KEYWORDS for w in p.split()):
            heads += 1
            if sent != [_head_builder(p)]:
                bad_head.append(p[:50])
        if len(rows) != 1:
            no_rows.append(p[:50])
    check("all 49 benchmark prompts: the arm sends the pre-F4 original string (was: the budgeted F4 string "
          "for all 49, though Lucene accepts every one)", not bad_orig, f"{len(bad_orig)} differ: {bad_orig[:2]}")
    check(f"the {heads} prompts without a standalone reserved word: byte-identical to HEAD's builder",
          heads >= 40 and not bad_head, f"{len(bad_head)} differ: {bad_head[:2]}")
    check("…and the BM25 arm answers for every prompt", not no_rows, str(no_rows[:2]))
    oq = _oracle_queries() + ["compute reciprocal rank fusion across arms", "where is the sync lock acquired"]
    bad = [q for q in oq if _Driver("accept") and (lambda d: (_arm(R, d, q), d.sent())[1])(_Driver("accept"))
           != [_head_builder(q)]]
    check(f"every STAGE-0 oracle query ({len(oq)}) reaches Lucene byte-identical to HEAD",
          len(oq) >= 10 and not bad, str(bad[:3]))
    rng = random.Random(20260927)
    vocab = ("which function decides whether sync should refuse wipe graph when owner another project the and "
             "for handler route config retry upload token session cookie redirect auth digest adapter send "
             "request response parse value option default env var click group command werkzeug map rule "
             "converter endpoint dependency override scope security").split()
    mism = []
    for _ in range(300):
        q = " ".join(rng.choice(vocab) for _ in range(rng.randint(11, 60)))
        drv = _Driver("accept")
        _arm(R, drv, q)
        if drv.sent() != [_head_builder(q)]:
            mism.append(q[:60])
    check("300 seeded natural-language queries of 11-60 terms (accepted): byte-identical to HEAD",
          not mism, f"{len(mism)} differ: {mism[:2]}")


def gate_rejected_gets_bounded(R) -> None:
    print("\n-- B: only a string Lucene REJECTS gets the bounded variant (tried original first) --")
    three = ["the", "run", "and", "arm", "env", "mcp", "for", "not", "cli", "per", "git", "one", "get", "set",
             "add", "use", "new", "str", "int", "map", "key", "dir", "log", "row", "out", "err", "ids", "sum",
             "max", "min"]
    idents = [f"{a}_{b}" for a in ("load", "sync", "push", "read", "make", "check", "build", "parse", "write",
                                   "embed", "prune", "index")
              for b in ("graph", "nodes", "edges", "state", "query", "config", "lock", "cache", "rows", "arms")]
    for label, q in (("30 distinct 3-letter words (the live TooManyClauses shape)", " ".join(three)),
                     ("120 distinct identifiers", " ".join(idents[:120])),
                     ("2000 chars of distinct short words (the MCP cap)",
                      " ".join(f"w{i:03d}" for i in range(500))[:2000])):
        drv = _Driver("worst")
        rows = _arm(R, drv, q)
        orig = _orig_builder(q)
        check(f"{label}: the ORIGINAL was validated first", drv.probes()[:1] == [orig],
              str([p[:40] for p in drv.probes()][:2]))
        sent = drv.sent()
        check(f"{label}: the arm then sent a bounded variant the index accepts, and answers",
              len(sent) == 1 and sent[0] != orig and _reject(sent[0], "worst") is None and len(rows) == 1,
              f"sent={[s[:60] for s in sent]} rows={len(rows)}")
    q = " ".join(three[:8] + ["a1b2c3d4e5" * 50 + "é" + "f6g7h8i9j0" * 50])
    drv = _Driver("accept")
    rows = _arm(R, drv, q)
    check("a ~1005-char term around one é (the live TooComplexToDeterminize shape): validated, then bounded",
          drv.probes()[:1] == [_orig_builder(q)] and len(drv.sent()) == 1 and drv.sent()[0] != _orig_builder(q)
          and len(rows) == 1, str([s[:50] for s in drv.sent()]))
    check("…and the bounded variant keeps the short words FUZZY (only the overlong term is exact)",
          bool(drv.sent()) and all(f"{w}~2" in drv.sent()[0].split() for w in three[:8]), str(drv.sent()[:1])[:200])
    q = "config loader " + "".join(chr(random.Random(1).randrange(0x4E00, 0x9FA0)) for _ in range(200))
    drv = _Driver("accept")
    _arm(R, drv, q)
    check("a 600-byte CJK term Lucene ACCEPTS (over the a-priori 400-byte bound, under the ~810-byte failure) "
          "reaches it byte-identical — the a-priori bound no longer decides alone",
          drv.sent() == [_orig_builder(q)], str([s[-20:] for s in drv.sent()]))


def gate_probe_safety(R) -> None:
    print("\n-- C: the validation is safe, bounded and never changes what the frozen arm would do on errors --")
    from neo4j.exceptions import ServiceUnavailable
    drv = _Driver("accept")
    _arm(R, drv, "where is the sync lock acquired")
    check("a provably-safe query (<= T terms, none overlong): no validation — exactly one query",
          not drv.probes() and len(drv.log) == 1, str(drv.log))
    long_ascii = "".join(random.Random(3).choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(17000))
    drv = _Driver("accept")
    rows = _arm(R, drv, "sync lock " + long_ascii)
    check("a > 2000-char pure-ASCII term (heap exhaustion live at ~16.9k): never sent as a fuzzy validation",
          not any(long_ascii + "~2" in p for p in drv.probes()) and len(rows) == 1, str([p[:30] for p in drv.probes()]))
    check("…and never sent fuzzy by the arm either", bool(drv.sent()) and long_ascii + "~2" not in drv.sent()[0])
    drv = _Driver("accept")
    many = " ".join(f"t{i:05d}" for i in range(1100))
    rows = _arm(R, drv, many)
    check("> 1024 terms (certain parse-time TooManyClauses): the original is never sent, even to validate it",
          _orig_builder(many) not in drv.probes() and _orig_builder(many) not in drv.sent(),
          f"probes={len(drv.probes())}")
    check("…the arm sends a bounded string (<= 1024 terms) and answers",
          len(drv.sent()) == 1 and len(drv.sent()[0].split()) <= 1024 and len(rows) == 1, str(len(drv.sent())))
    long_q = " ".join(["which", "function", "decides", "whether", "sync", "should", "refuse", "wipe", "graph",
                       "when", "owner", "another", "project"])
    drv = _Driver("accept", probe_error=ServiceUnavailable("Couldn't connect to localhost:7699"))
    raised = None
    try:
        _arm(R, drv, long_q)
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("an instance-down error during validation FAILS LOUD (as the arm's own query would, T10)",
          isinstance(raised, ServiceUnavailable), repr(raised))
    check("…without a second query against the dead instance", len(drv.log) == 1, str(len(drv.log)))
    drv = _Driver("accept", probe_error=_client_error(_NO_INDEX), arm_error=_client_error(_NO_INDEX))
    rows = _arm(R, drv, long_q)
    check("a benign validation error (missing FTS index): the original is kept; the arm skips as before",
          drv.sent() == [_orig_builder(long_q)] and rows == [], f"{drv.sent()} {rows}")
    tmo = _client_error("The transaction has been terminated. ... timeout",
                        "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")
    drv = _Driver("accept", probe_error=tmo)
    rows = _arm(R, drv, long_q)
    check("a validation that times out keeps the original (unknown verdict never changes the string)",
          drv.sent() == [_orig_builder(long_q)] and len(rows) == 1, str(drv.sent()))
    # Timed on a real Session: the probe carries a server-side timeout; the frozen arm's own query is untouched.
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
            return _Result([{"n": 1}])

        def close(self):
            pass

        def __del__(self):
            pass

    class _RealishDriver:
        def __init__(self):
            self.log = []

        def session(self, **kw):
            return _RealishSession(self.log)

    rd = _RealishDriver()
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)
    ret.driver = rd
    ret._arm_fulltext(long_q, 45)
    probes = [q for q in rd.log if "count(node)" in getattr(q, "text", q)]
    check("the validation is sent as a neo4j.Query with a positive timeout on a real Session",
          len(probes) == 1 and isinstance(probes[0], Query) and (probes[0].timeout or 0) > 0,
          str([type(q).__name__ for q in rd.log]))
    with _env(DEEPMAP_FTS_PROBE_TIMEOUT_S="3.5"):
        rd = _RealishDriver()
        ret.driver = rd
        ret._arm_fulltext(long_q, 45)
        probes = [q for q in rd.log if "count(node)" in getattr(q, "text", q)]
    check("DEEPMAP_FTS_PROBE_TIMEOUT_S sets it", bool(probes) and getattr(probes[0], "timeout", None) == 3.5,
          str([getattr(q, "timeout", None) for q in probes]))
    # Outside the arm there is no driver: a DIRECT call keeps the a-priori (F4) builder exactly.
    rng = random.Random(5)
    words = ("the and for sync lock graph node edge retry route auth config parse value handler " * 3).split()
    diffs = [q for q in (" ".join(rng.choice(words) for _ in range(rng.randint(0, 80))) for _ in range(800))
             if R._build_fts_query(q) != _f4_reference(R, q)]
    check("a direct _build_fts_query call (no arm, no driver) is byte-identical to the F4 builder (800 queries)",
          not diffs, repr(diffs[:1])[:200])
    with _env(DEEPMAP_FTS_PROBE="0"):
        drv = _Driver("accept")
        _arm(R, drv, long_q)
    check("DEEPMAP_FTS_PROBE=0: no validation; the arm sends the a-priori (F4) string",
          not drv.probes() and drv.sent() == [_f4_reference(R, long_q)], str(drv.sent()))


def gate_split_code_tokens(R) -> None:
    print("\n-- D: DEEPMAP_FTS_SPLIT_CODE_TOKENS=1 (I2, default OFF): pasted calls split into identifier pieces --")
    cases = ["reset_pristine(repo_dir, pinned_sha)", "bump_session_count(root, STATE_FILENAME)",
             'emit("PreToolUse",', "run_benchmark.reset_pristine", "foo_bar(x, y)", "Class#method impact",
             "Owner::method", "multi-hop impact", "where is auth handled", "don't retry"]
    rng = random.Random(77)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.,;#$%@'+-&|!(){}[]^\"~*?:\\/é日"
    corpus = cases + [" ".join("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
                               for _ in range(rng.randint(0, 9))) for _ in range(3000)]
    off_default = [q for q in corpus if R._build_fts_query(q) != _f4_reference(R, q)]
    with _env(DEEPMAP_FTS_SPLIT_CODE_TOKENS="0"):
        off_zero = [q for q in corpus if R._build_fts_query(q) != _f4_reference(R, q)]
    check("flag unset: byte-identical to the F4 builder (3010 code-shaped queries)", not off_default,
          repr(off_default[:2]))
    check("flag =0: byte-identical too", not off_zero, repr(off_zero[:2]))
    with _env(DEEPMAP_FTS_SPLIT_CODE_TOKENS="1"):
        got = {q: R._build_fts_query(q).split() for q in cases}
        check("'reset_pristine(repo_dir, pinned_sha)' -> reset_pristine~2 repo_dir~2 pinned_sha~2",
              got[cases[0]] == ["reset_pristine~2", "repo_dir~2", "pinned_sha~2"], str(got[cases[0]]))
        check("'bump_session_count(root, STATE_FILENAME)' -> the three identifiers, no glued punctuation",
              got[cases[1]] == ["bump_session_count~2", "root~2", "STATE_FILENAME~2"], str(got[cases[1]]))
        check("'emit(\"PreToolUse\",' -> emit~2 PreToolUse~2", got[cases[2]] == ["emit~2", "PreToolUse~2"],
              str(got[cases[2]]))
        check("a dotted name keeps its whole qualified form (it is ONE analyzer token) AND its parts",
              got[cases[3]] == ["run_benchmark.reset_pristine~2", "run_benchmark~2", "reset_pristine~2"],
              str(got[cases[3]]))
        check("'foo_bar(x, y)' contains foo_bar~2 and no glued punctuation (the sweep's gate)",
              "foo_bar~2" in got[cases[4]] and not any(re.search(r"\\[(),]", t) for t in got[cases[4]]),
              str(got[cases[4]]))
        check("'Class#method' -> Class.method~2 (the stored form) + Class~2 method~2",
              got[cases[5]][:3] == ["Class.method~2", "Class~2", "method~2"], str(got[cases[5]]))
        check("'Owner::method' -> Owner.method~2 + the parts", got[cases[6]] == ["Owner.method~2", "Owner~2", "method~2"],
              str(got[cases[6]]))
        check("a hyphenated word splits where the analyzer splits it", got[cases[7]][:2] == ["multi~2", "hop~2"],
              str(got[cases[7]]))
        check("clean identifier words stay byte-identical with the flag ON",
              " ".join(got[cases[8]]) == _head_builder(cases[8]), str(got[cases[8]]))
        worst = None
        for q in corpus + [" ".join(f"a{i}.b{i}(c{i}, d{i})" for i in range(300))]:
            s = R._build_fts_query(q)
            why = _reject(s, "worst")
            if why or re.search(r"(?<!\\)[()\[\]{}\"]", s):
                worst = (q[:60], s[:80], why)
                break
        check("flag ON: every built string stays parseable within 1024 expanded clauses", worst is None, str(worst))
        drv = _Driver("accept")
        rows = _arm(R, drv, "reset_pristine(repo_dir, pinned_sha)")
        check("flag ON through the frozen arm: the split terms reach Lucene",
              drv.sent() == ["reset_pristine~2 repo_dir~2 pinned_sha~2"] and len(rows) == 1, str(drv.sent()))


def gate_short_name(R) -> None:
    print("\n-- E: DEEPMAP_FTS_SHORT_NAME=1 (I2, default OFF): the cost model covers the `short_name` field --")
    check("the index property the flag adds is named 'short_name' (the contract with W6's index side)",
          getattr(R, "FTS_SHORT_NAME_PROPERTY", None) == "short_name", str(getattr(R, "FTS_SHORT_NAME_PROPERTY", None)))
    model = getattr(R, "_fts_model", None)
    check("flag OFF: the cost model is the measured one (4 fields, 2 x 50 per fuzzy term, T=10)",
          model is not None and model() == (4, 100, 10), str(model() if model else None))
    with _env(DEEPMAP_FTS_SHORT_NAME="1"):
        check("flag ON: 5 fields, 3 populated (3 x 50 per fuzzy term), T=6 (6 x 150 <= 1000)",
              model is not None and model() == (5, 150, 6), str(model() if model else None))
        six = "load sync push read make check"
        check("flag ON: a 6-term query is still the original (direct call)", R._build_fts_query(six) == _head_builder(six),
              R._build_fts_query(six))
        rng = random.Random(8)
        vocab = [w for w in "the run and arm env mcp for not cli per git one get set add use".split()] + \
                [f"node{i}" for i in range(60)]
        worst = None
        for _ in range(1200):
            q = " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 300)))
            why = _reject(R._build_fts_query(q), "worst", fields=5, populated=3)
            if why:
                worst = (q[:60], why)
                break
        check("flag ON, direct calls: 1200 queries stay within 1024 under a 5-field / 3-populated worst case",
              worst is None, str(worst))
    q = " ".join(f"w{i:04d}" for i in range(240))
    off = R._build_fts_query(q)
    check("flag OFF: unchanged (240 exact terms at 4 clauses each)", off == _f4_reference(R, q))
    # The flag disagrees with the index (index built WITH short_name, server started without the flag): the
    # narrow bounded variant (4 fields) is rejected by a 5-field index — the wider one is tried and answers.
    drv = _Driver("worst", fields=5, populated=3)
    rows = _arm(R, drv, q)
    check("index has short_name but the server flag is OFF: the arm still answers (the wider model is tried)",
          len(rows) == 1 and bool(drv.sent()) and _reject(drv.sent()[-1], "worst", 5, 3) is None,
          f"rows={len(rows)} probes={len(drv.probes())}")


_FROZEN = {   # sha256[:16] of each symbol's AST source segment (CRLF-normalised) at the v0.27.4 base 9c8d50bce
    "DeepMapRetriever.__init__": "83778256c6d5dbb6",
    # docstring corrected 2026-09-29; behavior identical (AST equal with docstring stripped). Was 3b8b1a9f0e476f71;
    # the proof is tests/o3_retrieval_docstrings_r283_gates.py.
    "DeepMapRetriever.impact": "ee084e616dff3b53",
    "DeepMapRetriever._embed_or_none": "c9099cee93560d20", "DeepMapRetriever.search": "6b56bebdf189c474",
    "DeepMapRetriever._arm_vector": "ba935c518e31c19e", "DeepMapRetriever._arm_vector_search": "e97472fb11d9e197",
    "DeepMapRetriever._arm_vector_procedure": "1a36097f347db2b5",
    "DeepMapRetriever._arm_fulltext": "8a371f7f01c1031d", "DeepMapRetriever._arm_graph_weighted": "bde632e1e442e77a",
    "DeepMapRetriever._arm_graph_weighted_one": "7d2b037814eced42", "DeepMapRetriever._hydrate": "2e609792337998dd",
    "RRF_K": "da4cb420ff7ad1bd", "_candidate_floor": "171a91847a4687fd", "_is_fatal_db_error": "e4e5e5366619cfce",
    "_LUCENE_SPECIAL": "973253f4b6d36996", "_is_missing_index_error": "af1ea7989271f91a",
    "_is_fts_parse_error": "525b06f56ca9ffe0", "_weighted_rrf": "e8a914f677a25b69",
    "VECTOR_INDEXES": "35b6fc04a3df18aa", "DEFAULT_ARM_WEIGHTS": "05f8eca77cb90625",
}


def _segments(src: str) -> dict:
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in tg if isinstance(t, ast.Name)]
        for n in names:
            out[n] = ast.get_source_segment(src, node)
        if isinstance(node, ast.ClassDef) and node.name == "DeepMapRetriever":
            for m in node.body:
                if isinstance(m, ast.FunctionDef):
                    out["DeepMapRetriever." + m.name] = ast.get_source_segment(src, m)
    return out


def gate_frozen(R) -> None:
    print("\n-- F: the frozen ranking path is byte-identical to the v0.27.4 base (hash lock) --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    seg = _segments(src)
    drift = [n for n, h in _FROZEN.items()
             if hashlib.sha256((seg.get(n) or "").encode("utf-8")).hexdigest()[:16] != h]
    check(f"all {len(_FROZEN)} frozen symbols (_arm_*, search, _hydrate, _weighted_rrf, weights, RRF_K, the arm's "
          "error classifiers) hash-identical to the base (impact: its docstring-only re-lock)", not drift, str(drift))
    det = seg.get("DeepMapRetriever._detect_search_clause") or ""
    check("_detect_search_clause keeps its probe Cypher and its True/False decision (only the note changed)",
          "SEARCH n IN (VECTOR INDEX deepmap_fn_embeddings FOR $vec LIMIT 1)" in det
          and det.count("return True") == 1 and det.count("return False") == 1, det[-300:])
    check("the fulltext arm still calls the module-level builder (the one hook the protection may use)",
          "fts_q = _build_fts_query(query)" in (seg.get("DeepMapRetriever._arm_fulltext") or ""))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — BM25 builder round 7: I1 validate-then-bound + I2 FTS flags + frozen hash lock (no Neo4j)")
    print("=" * 72)
    with _env(**{k: None for k in _FLAGS}):
        R = _load("retrieval")
        gate_accepted_is_original(R)
        gate_rejected_gets_bounded(R)
        gate_probe_safety(R)
        gate_split_code_tokens(R)
        gate_short_name(R)
        gate_frozen(R)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
