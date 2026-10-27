#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap — read-only Cypher over the project graph: ONE implementation for the deepmap_cypher MCP tool and
`deepmap cypher` (the CLI), so the validator, the bounds and the result shape can never drift apart.

Three independent layers keep a query read-only:
  1. check_read_only(query) — a lexical validator that REJECTS writes, admin commands, IO and procedures before the
     query reaches Neo4j (CypherRejected, a ValueError: the caller gets an error, never an empty answer);
  2. run_read_only runs the query in a READ access-mode transaction, so Neo4j itself refuses a write
     (Neo.ClientError.Statement.AccessMode — verified live on Neo4j 5.26 for CREATE, MERGE, SET, DETACH DELETE and
     db.createLabel);
  3. that transaction is always ROLLED BACK, never committed.
Layer 1 is the only one that stops what a READ transaction allows: LOAD CSV (a READ transaction still fetches a
file:/// or an http:// URL — both verified live on 5.26), `USE <database>` (another database, e.g. system), TERMINATE /
SHOW TRANSACTIONS, and procedures or functions outside a short read-only allowlist (apoc.*, gds.*, dbms.*).

Why this validator shape (a leading-clause allowlist + allowlisted CALL / SHOW targets + a keyword denylist) rather
than an allowlist of every clause: telling a clause keyword from an identifier everywhere needs a full Cypher
parser, and a home-grown one that disagrees with Neo4j's is itself the bypass. So the parts that CAN be allowlisted
without parsing are: the statement must START with MATCH / OPTIONAL MATCH / WITH / UNWIND / RETURN / CALL / SHOW;
every CALL must name an allowlisted read-only procedure (or open a `CALL {...}` subquery); SHOW may only list
indexes, constraints, procedures or functions; a function call may only use a core namespace. Later clauses are
checked with a denylist of write / admin / IO keywords at keyword positions — a word after `.` (n.created), `$`, `:`
(labels, map values), before `:` (map keys) or after AS (aliases) is an identifier, and backtick-quoted names are
never keywords — and layers 2 and 3 backstop the writes that list could miss. The AS exemption holds only for the AS
keyword itself and only when the alias ENDS after the word (the next token is the end, `,` `)` `}` `]` `;` or a
clause / ORDER BY word — _ALIAS_END): after a variable, parameter, label or property spelled `as` (`WITH as SET
as.x = 1`) the next word is still checked.

The lexer drops string literals and comments before scanning (a keyword inside 'a string' or a comment does not
reject, and a write after a comment is still seen) and rejects an unterminated string, comment or quoted name. It must
agree with Neo4j on where each string and comment ends, or text one reads as a string the other runs as code:
  - Neo4j 5.26 DECODES \\u escapes before it lexes (verified live: `RETURN 'x\\u0027 AS y //'` closes the string at
    the escape). Its reader takes the 4 characters after a `\\u` and parses them with Java's Integer.parseInt(s, 16),
    which also accepts a sign and any Unicode digit (measured: \\u+041 and full-width or Arabic-Indic 0041 decode to
    'A'), and the JavaCC fallback parser accepts \\uu....: so a \\u not followed by exactly 4 ASCII hex digits is
    rejected, an escape that decodes to an ASCII character is rejected, and the decoded text is validated too (the
    only check that sees a keyword glued on by a non-ASCII whitespace escape: `x\\u3000LOAD` is one word raw);
  - Neo4j ends a // comment only at \\n or \\r (measured: VT, FF, FS, GS, RS, US, NEL, U+2028 and U+2029 do NOT end
    it; a reader that ends the comment there takes the next quote for a string Neo4j never opens, which hid a
    LOAD CSV that reached Neo4j), so those characters are rejected anywhere outside a string or a quoted name.
A word is matched against the denylist after Unicode case folding and NFKC (a long-s or full-width spelling of SET
still reads as SET), and an invisible format / control character outside a string is rejected.

Stdlib only and side-effect-free on import (the neo4j driver is imported lazily): safe for the MCP server at module
load, the CLI at parser-build time and the hermetic gates. Zero egress.
"""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections.abc import Mapping

LIMIT_DEFAULT = 100            # rows a call returns by default
LIMIT_MAX = 500                # hard cap on rows (a larger limit is clamped, with a note)
MAX_BYTES = 64_000             # cap on the rows' text as the MCP client receives it (UTF-8; see _size) — the answer
                               # lands in the agent's context
TIMEOUT_S = 20.0               # server-side transaction timeout (env DEEPMAP_CYPHER_TIMEOUT_S)
QUERY_MAX_CHARS = 20_000       # a longer query is rejected, never truncated (a cut query is a different query)
PARAMS_MAX_BYTES = 256_000     # serialized $parameters (a 768-number vector is ~16 KB)
STR_MAX = 1_000                # a longer string value is cut, with a note
VECTOR_MIN_LEN = 256           # a list of this many floats is an embedding vector: left out (a shorter slice is not)
LIST_PROP_MAX = 64             # a node / relationship list property longer than this is left out
_DEPTH_MAX = 8                 # nested lists / maps deeper than this come back as a placeholder

# The statement's FIRST word (an allowlist: an admin command, EXPLAIN / PROFILE / CYPHER options, USE and anything
# new can't lead).
LEADING = frozenset({"MATCH", "OPTIONAL", "WITH", "UNWIND", "RETURN", "CALL", "SHOW"})

# Write / admin / IO keywords rejected at ANY keyword position (the clauses after the first one).
DENIED = frozenset({
    "CREATE", "MERGE", "SET", "DELETE", "DETACH", "NODETACH", "REMOVE", "DROP", "FOREACH", "INSERT",   # writes / DDL
    "LOAD", "CSV", "PERIODIC",                                                         # LOAD CSV, USING PERIODIC COMMIT
    "TRANSACTION", "TRANSACTIONS", "CONCURRENT",               # CALL {...} IN TRANSACTIONS; SHOW/TERMINATE TRANSACTIONS
    "USE",                                                                         # another database (e.g. system)
    "ALTER", "RENAME", "GRANT", "DENY", "REVOKE", "TERMINATE", "ENABLE", "DEALLOCATE", "REALLOCATE", "DRYRUN",  # admin
})
# START / STOP are common path variables ((start)-[:CALLS*]->(end)): rejected only as START / STOP DATABASE|SERVER.
_ADMIN_PAIRS = {"START": {"DATABASE", "DATABASES", "SERVER", "SERVERS"},
                "STOP": {"DATABASE", "DATABASES", "SERVER", "SERVERS"}}

# CALL targets (lower-cased): read-only listings and the index queries the retriever itself uses.
PROCEDURES = frozenset({
    "db.labels", "db.relationshiptypes", "db.propertykeys",
    "db.schema.visualization", "db.schema.nodetypeproperties", "db.schema.reltypeproperties",
    "db.index.vector.querynodes", "db.index.vector.queryrelationships",
    "db.index.fulltext.querynodes", "db.index.fulltext.queryrelationships",
    "dbms.components",
})
# What SHOW may list: [modifiers] <object>. SHOW USERS / ROLES / PRIVILEGES / DATABASES / SETTINGS / TRANSACTIONS /
# SERVERS / ALIASES are refused.
_SHOW_MODIFIERS = frozenset({"ALL", "RANGE", "FULLTEXT", "TEXT", "POINT", "LOOKUP", "VECTOR", "BTREE", "UNIQUE",
                             "UNIQUENESS", "EXIST", "EXISTENCE", "EXISTS", "NODE", "REL", "RELATIONSHIP", "KEY",
                             "PROPERTY", "TYPE", "BUILT", "IN", "USER", "DEFINED"})
_SHOW_OBJECTS = frozenset({"INDEX", "INDEXES", "CONSTRAINT", "CONSTRAINTS", "PROCEDURE", "PROCEDURES", "FUNCTION",
                           "FUNCTIONS"})
# A namespaced function call (`x.y(...)`) may only use a core namespace; apoc / gds / dbms / genai (plugins, admin, IO
# and network calls) are refused anywhere they head a dotted name.
_FUNCTION_NAMESPACES = frozenset({"date", "datetime", "localdatetime", "localtime", "time", "duration", "point",
                                  "vector"})
_DENIED_NAMESPACES = ("apoc", "gds", "dbms", "genai")

# Where a // comment ends: exactly where Neo4j ends it, at \n or \r. The other characters a reader might take for a
# line break (Python's str.splitlines does; Neo4j 5.26 does not — measured) are refused anywhere outside a string or
# a quoted name, so no two readers can disagree on where a comment ends and the next string starts. US (\x1f) ends
# a line for no reader, but it is invisible whitespace no real query needs.
_LINE_ENDS = "\n\r"
_ODD_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x1f\x85\u2028\u2029"
# \uXXXX: Neo4j 5.26 decodes it ANYWHERE before it lexes (measured). \UXXXXXXXX: 5.26 does not (an error outside a
# string, literal text inside one), but a later Cypher may — it is handled the same way, which is only ever stricter.
_ESCAPE_RE = re.compile(r"\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})")
# A \u NOT followed by exactly 4 ASCII hex digits. Neo4j's reader parses the 4 characters after \u with Java's
# Integer.parseInt(s, 16), which also takes a sign and any Unicode digit (measured: \u+041 and full-width or
# Arabic-Indic 0041 decode to 'A'), and the JavaCC fallback takes \uu0041; the rest raise. Refused, never modelled.
# Python's [0-9A-Fa-f] is ASCII-only. Every \u counts, even one after an escaped backslash (Neo4j reads that as
# text): over-matching only ever refuses.
_ODD_ESCAPE_RE = re.compile(r"\\u(?![0-9A-Fa-f]{4})")


def _escape_code(m) -> int:
    return int(m.group(1) or m.group(2), 16)

_NOTE_HINT = "if it is a variable or alias name here, rename it or quote it in backticks"


class CypherRejected(ValueError):
    """The query is not one deepmap_cypher runs (a write, admin, IO, a procedure outside the allowlist, several
    statements, ...). Raised BEFORE the query reaches Neo4j; its message names the reason."""


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 and math.isfinite(value) else default


def timeout_s() -> float:
    """The server-side transaction timeout for a deepmap_cypher / `deepmap cypher` read."""
    return _env_seconds("DEEPMAP_CYPHER_TIMEOUT_S", TIMEOUT_S)


# ---- the lexer ----------------------------------------------------------------------------------------------------
# Tokens are (kind, text): "word" (a bare identifier or keyword), "number", "quoted" (a `backtick` name, unquoted),
# "string" (a literal's raw body) and "punct" (one character). Whitespace and comments are dropped.

def _odd_break(c: str) -> CypherRejected:
    return CypherRejected(f"a line-break-like character (U+{ord(c):04X}) outside a string — Neo4j ends a // comment "
                          "only at a newline, so text after it would be read differently; use a plain newline")


def _lex(text: str) -> list:
    toks: list = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in _ODD_BREAKS:
            raise _odd_break(c)
        if c.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] not in _LINE_ENDS:
                if text[i] in _ODD_BREAKS:
                    raise _odd_break(text[i])
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise CypherRejected("an unterminated /* comment")
            odd = next((ch for ch in text[i + 2:end] if ch in _ODD_BREAKS), None)
            if odd is not None:
                raise _odd_break(odd)
            i = end + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1     # a backslash takes the next character with it (Neo4j's rule)
            if j >= n:
                raise CypherRejected("an unterminated string literal")
            toks.append(("string", text[i + 1:j]))
            i = j + 1
            continue
        if c == "`":
            j, parts = i + 1, []
            while True:
                k = text.find("`", j)
                if k < 0:
                    raise CypherRejected("an unterminated `quoted` name")
                parts.append(text[j:k])
                if text.startswith("``", k):        # `` is an escaped backtick inside a quoted name
                    parts.append("`")
                    j = k + 2
                    continue
                break
            toks.append(("quoted", "".join(parts)))
            i = k + 1
            continue
        if c.isalnum() or c == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            toks.append(("number" if word[0].isdigit() else "word", word))
            i = j
            continue
        if unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs", "Cn"):
            raise CypherRejected(f"an invisible control or format character (U+{ord(c):04X}) outside a string")
        toks.append(("punct", c))
        i += 1
    return toks


def _forms(word: str) -> set:
    """Every spelling of `word` a case-insensitive reader could take for a keyword: plain upper case, Unicode case
    folding (long s -> S) and NFKC (full-width letters)."""
    out = {word.upper()}
    if not word.isascii():
        out.add(word.casefold().upper())
        out.add(unicodedata.normalize("NFKC", word).upper())
        out.add(unicodedata.normalize("NFKC", word).casefold().upper())
    return out


def _is(tok, keyword: str) -> bool:
    return tok is not None and tok[0] == "word" and keyword in _forms(tok[1])


def _ident(tok) -> bool:
    return tok is not None and tok[0] in ("word", "quoted")


def _member_dot(toks: list, i: int) -> bool:
    """toks[i] is a '.' that reads a member — of what precedes it (n.name, $p.x, (n).x) or in a map projection
    (n {.name, .key}) — not a number's dot."""
    if not (0 <= i < len(toks) and toks[i] == ("punct", ".")):
        return False
    prev = toks[i - 1] if i > 0 else None
    return prev is not None and (prev[0] in ("word", "quoted") or prev[0] == "punct" and prev[1] in ")]},{")


# What may follow an alias: the alias ENDS there. An AS-alias is exempt from the keyword check only when the next
# token is one of these (or the end, or , ) } ] ;) — so a variable named `as` (`WITH as SET as.x = 1`) can never pass
# for the AS keyword and hide the SET after it: there the next token is another word, so SET is still checked.
_ALIAS_END = frozenset({"RETURN", "WITH", "WHERE", "ORDER", "SKIP", "LIMIT", "OFFSET", "UNION", "MATCH", "OPTIONAL",
                        "UNWIND", "CALL", "YIELD", "FINISH", "ASC", "DESC", "ASCENDING", "DESCENDING"})
_ALIAS_END_PUNCT = frozenset(",)}];")


def _alias_ends(nxt) -> bool:
    """`nxt` (the token after a candidate alias) ends the alias: None, , ) } ] ; or a plain-ASCII _ALIAS_END word.
    Strict on purpose: anything else means the word is checked as a keyword (at worst a rename, never a bypass)."""
    if nxt is None:
        return True
    if nxt[0] == "punct":
        return nxt[1] in _ALIAS_END_PUNCT
    return nxt[0] == "word" and nxt[1].isascii() and nxt[1].upper() in _ALIAS_END


def _keyword_position(toks: list, i: int) -> bool:
    """toks[i] (a word) can be read as a keyword: not a member name, parameter, label / map value, map key or
    alias."""
    prev = toks[i - 1] if i > 0 else None
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if _member_dot(toks, i - 1):
        return False
    if prev in (("punct", "$"), ("punct", ":")) or nxt == ("punct", ":"):
        return False
    # An alias: only after the AS KEYWORD, and only when the alias ends right after the word (_alias_ends). A `prev`
    # spelled as that is itself a property (n.as), a parameter ($as), a label (n:AS) or an alias (x AS as) is a name;
    # a variable named as (`WITH as SET`) is not followed by an alias end — either way the word may still be a keyword.
    pp = toks[i - 2] if i >= 2 else None
    if (_is(prev, "AS") and not _member_dot(toks, i - 2) and pp not in (("punct", "$"), ("punct", ":"))
            and not _is(pp, "AS") and _alias_ends(nxt)):
        return False
    return True


def _dotted(toks: list, i: int) -> tuple:
    """(name, end): the dotted name starting at toks[i] (identifiers joined by '.'; a quoted part keeps its text) and
    the index just past it."""
    parts = [toks[i][1]]
    j = i + 1
    while j + 1 < len(toks) and toks[j] == ("punct", ".") and _ident(toks[j + 1]):
        parts.append(toks[j + 1][1])
        j += 2
    return ".".join(parts), j


def _denied_namespace(name: str) -> "str | None":
    low = name.lower()
    for ns in _DENIED_NAMESPACES:
        if low == ns or low.startswith(ns + "."):
            return ns
    return None


# Words that may follow a VARIABLE (so `call AS c`, `call IN list` read `call` as a name, not the CALL clause).
_AFTER_VARIABLE = frozenset({"AS", "IS", "IN", "AND", "OR", "XOR", "NOT", "STARTS", "ENDS", "CONTAINS", "WHERE",
                             "ORDER", "SKIP", "LIMIT", "OFFSET", "UNION", "RETURN", "WITH", "YIELD", "MATCH",
                             "OPTIONAL", "UNWIND", "CALL", "FINISH", "ASC", "DESC", "ASCENDING", "DESCENDING",
                             "WHEN", "THEN", "ELSE", "END"})
# What a SHOW command lists besides the allowed objects: a SHOW followed by one of these is always checked.
_SHOW_ADMIN = frozenset({"USER", "USERS", "ROLE", "ROLES", "PRIVILEGE", "PRIVILEGES", "DATABASE", "DATABASES",
                         "DEFAULT", "HOME", "SETTING", "SETTINGS", "SERVER", "SERVERS", "ALIAS", "ALIASES", "CURRENT",
                         "TRANSACTION", "TRANSACTIONS", "POPULATED", "SUPPORTED"})


def _call_kind(toks: list, i: int) -> "str | None":
    """What the CALL word at toks[i] opens: 'subquery' (CALL { ... } / CALL (x) { ... }), 'procedure', or None — a
    variable that happens to be named call (call.confidence, call AS c, a trailing `call`)."""
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if nxt in (("punct", "{"), ("punct", "(")):
        return "subquery"
    if not _ident(nxt):
        return None
    after = toks[i + 2] if i + 2 < len(toks) else None
    if nxt[0] == "word" and _forms(nxt[1]) & _AFTER_VARIABLE and after not in (("punct", "."), ("punct", "(")):
        return None
    return "procedure"


def _check_tokens(toks: list) -> None:
    if not toks:
        raise CypherRejected("the query is empty after comments are removed")
    first = toks[0]
    if not (first[0] == "word" and first[1].isascii() and first[1].upper() in LEADING):
        raise CypherRejected(f"a read query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, RETURN, CALL or "
                             f"SHOW (it starts with {first[1]!r})")
    call_targets: set = set()
    for i, tok in enumerate(toks):
        kind, text = tok
        if tok == ("punct", ";"):
            if i != len(toks) - 1:
                raise CypherRejected("several statements (a ';' followed by more text) — send one read query")
            continue
        if kind == "quoted":
            ns = _denied_namespace(text)
            if ns:
                raise CypherRejected(f"`{text}` uses the {ns}.* namespace, which deepmap_cypher does not allow")
        if kind not in ("word", "quoted"):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if kind == "word" and _keyword_position(toks, i):
            forms = _forms(text)
            hit = forms & DENIED
            if hit:
                raise CypherRejected(f"{sorted(hit)[0]} is a write, admin or IO keyword — deepmap_cypher only "
                                     f"reads ({_NOTE_HINT})")
            for verb, objects in _ADMIN_PAIRS.items():
                if verb in forms and nxt is not None and nxt[0] == "word" and _forms(nxt[1]) & objects:
                    raise CypherRejected(f"{verb} {nxt[1].upper()} is an admin command — deepmap_cypher only reads")
            if "CALL" in forms and _call_kind(toks, i) == "procedure":
                name, _end = _dotted(toks, i + 1)
                if not (name.isascii() and name.lower() in PROCEDURES):
                    raise CypherRejected(f"CALL {name} is not an allowed read-only procedure (allowed: "
                                         f"{', '.join(sorted(PROCEDURES))})")
                call_targets.add(i + 1)
            if "SHOW" in forms and (i == 0 or (nxt is not None and nxt[0] == "word"
                                               and _forms(nxt[1]) & (_SHOW_ADMIN | _SHOW_MODIFIERS | _SHOW_OBJECTS))):
                j = i + 1
                while j < len(toks) and toks[j][0] == "word" and _forms(toks[j][1]) & _SHOW_MODIFIERS:
                    j += 1
                obj = toks[j] if j < len(toks) else None
                if not (obj is not None and obj[0] == "word" and _forms(obj[1]) & _SHOW_OBJECTS):
                    shown = obj[1] if obj is not None else "nothing"
                    raise CypherRejected(f"SHOW may only list indexes, constraints, procedures or functions (not "
                                         f"{shown!r})")
        # A dotted name headed here (not a member of something before it): a denied namespace anywhere, and a
        # function call only in a core namespace.
        if i in call_targets or _member_dot(toks, i - 1):
            continue
        if nxt == ("punct", ".") or (kind == "quoted" and "." in text):
            name, end = _dotted(toks, i)
            ns = _denied_namespace(name)
            if ns:
                raise CypherRejected(f"{name} uses the {ns}.* namespace, which deepmap_cypher does not allow")
            if end < len(toks) and toks[end] == ("punct", "(") and "." in name:
                head = name.split(".", 1)[0].lower()
                if head not in _FUNCTION_NAMESPACES:
                    raise CypherRejected(f"{name}(...) is not a core Cypher function (allowed namespaces: "
                                         f"{', '.join(sorted(_FUNCTION_NAMESPACES))})")


def check_read_only(query) -> str:
    """The statement to send — `query` itself, never rewritten — or CypherRejected naming why it is not a single
    read-only query. Pure: no Neo4j, no I/O."""
    if not isinstance(query, str):
        raise CypherRejected("the query must be a string")
    if not query.strip():
        raise CypherRejected("empty query — nothing to run (e.g. MATCH (f:Function) RETURN f.name LIMIT 10)")
    if len(query) > QUERY_MAX_CHARS:
        raise CypherRejected(f"the query is {len(query):,} characters; the cap is {QUERY_MAX_CHARS:,} (a query is "
                             "never truncated) — pass long values as $parameters")
    odd = _ODD_ESCAPE_RE.search(query)
    if odd:
        raise CypherRejected(f"the escape {query[odd.start():odd.start() + 6]} is not \\u + four hex digits 0-9 A-F; "
                             "Neo4j also decodes signed and non-ASCII-digit forms before it parses, so one can hide "
                             "syntax — type the character itself, or pass the value as a $parameter")
    escapes = list(_ESCAPE_RE.finditer(query))
    for m in escapes:
        code = _escape_code(m)
        if code < 0x80:
            raise CypherRejected(f"the escape {m.group(0)} spells an ASCII character; Neo4j decodes \\u escapes "
                                 "before it parses, so one can hide syntax — type the character itself, or pass the "
                                 "value as a $parameter")
        if code > 0x10FFFF:
            raise CypherRejected(f"the escape {m.group(0)} is not a Unicode character")
    _check_tokens(_lex(query))
    if escapes:      # what Neo4j will actually parse: validated too
        _check_tokens(_lex(_ESCAPE_RE.sub(lambda m: chr(_escape_code(m)), query)))
    return query


def check_params(params) -> dict:
    """`params` as the $parameters to send: None -> {}; else a JSON object with string keys within
    PARAMS_MAX_BYTES. Raises CypherRejected otherwise."""
    if params is None:
        return {}
    if not isinstance(params, Mapping):
        raise CypherRejected("params must be a JSON object mapping parameter names to values, e.g. "
                             '{"name": "save"} for $name')
    bad = [k for k in params if not isinstance(k, str) or not k]
    if bad:
        raise CypherRejected(f"parameter names must be non-empty strings (got {bad[0]!r})")
    try:
        size = len(json.dumps(params, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CypherRejected(f"params must hold JSON values only ({exc})") from exc
    if size > PARAMS_MAX_BYTES:
        raise CypherRejected(f"params are {size:,} bytes; the cap is {PARAMS_MAX_BYTES:,}")
    return dict(params)


def clamp_limit(limit) -> tuple:
    """(rows, note): `limit` clamped into [1, LIMIT_MAX]; note None when it was already in range."""
    try:
        raw = int(limit)
    except (TypeError, ValueError):
        return LIMIT_DEFAULT, f"limit {limit!r} is not an integer; using {LIMIT_DEFAULT}."
    clamped = min(max(1, raw), LIMIT_MAX)
    if clamped != raw:
        return clamped, f"limit {raw} is out of range [1, {LIMIT_MAX}]; clamped to {clamped}."
    return clamped, None


# ---- result shaping -----------------------------------------------------------------------------------------------
# Whole graph objects come back as plain JSON: a node as {labels, key, name, source_file, start_line, confidence,
# body_summary, <other scalar properties>} (the property names a follow-up query uses), a relationship as {type,
# confidence, <attributes>, start, end} (the endpoint keys when the driver has them), a path as its nodes and
# relationships in order. Embedding properties and float lists of VECTOR_MIN_LEN+ numbers are left out everywhere,
# internal `_`-properties of whole nodes and relationships too (a map the query builds, n {.*}, keeps its `_` keys);
# a shorter slice of a vector (n.embedding[0..100]) is not left out: it is only a list of numbers. A column named
# exactly `note` is renamed (note_), so a {"note": ...} entry after the rows is always DeepMap's own remark.

_NODE_FIRST = ("key", "name", "source_file", "start_line", "confidence", "body_summary")


class _Shaper:
    def __init__(self):
        self.vectors = 0          # embedding-sized float lists left out of a returned VALUE
        self.cut_strings = 0

    def value(self, v, depth: int = 0):
        if v is None or isinstance(v, (bool, int)):
            return v
        if isinstance(v, float):
            return v if math.isfinite(v) else str(v)
        if isinstance(v, str):
            if len(v) > STR_MAX:
                self.cut_strings += 1
                return v[:STR_MAX] + f"… [{len(v) - STR_MAX:,} more characters]"
            return v
        if isinstance(v, (bytes, bytearray)):
            return f"<{len(v):,} bytes>"
        if depth >= _DEPTH_MAX:
            return f"<{type(v).__name__} nested too deeply>"
        if hasattr(v, "relationships") and hasattr(v, "nodes"):
            return self.path(v, depth)
        if hasattr(v, "type") and hasattr(v, "start_node"):
            return self.relationship(v, depth)
        if hasattr(v, "labels") and hasattr(v, "items"):
            return self.node(v, depth)
        if isinstance(v, Mapping):
            out = {}
            for k, x in v.items():
                k = str(k)
                if k.lower().startswith("embedding"):
                    self.vectors += 1
                    out[k] = "<embedding vector omitted>"
                else:
                    out[k] = self.value(x, depth + 1)
            return out
        if isinstance(v, (list, tuple)):
            if _is_vector(v):
                self.vectors += 1
                return f"<{len(v)}-number embedding vector omitted>"
            return [self.value(x, depth + 1) for x in v]
        return str(v)          # temporal, spatial, anything else: its text

    def _props(self, obj, first, depth: int) -> dict:
        items = dict(obj.items()) if hasattr(obj, "items") else {}
        out = {}
        for k in list(first) + sorted(k for k in items if k not in first):
            if k not in items:
                continue
            x = items[k]
            if k.startswith("_") or k.lower().startswith("embedding"):
                continue                                   # internal hashes, the vector itself
            if isinstance(x, (list, tuple)) and (len(x) > LIST_PROP_MAX or _is_vector(x)):
                continue
            out[k] = self.value(x, depth + 1)
        return out

    def node(self, n, depth: int = 0, compact: bool = False) -> dict:
        out = {"labels": sorted(str(x) for x in (getattr(n, "labels", None) or ()))}
        props = self._props(n, _NODE_FIRST, depth)
        if compact:
            props = {k: props[k] for k in ("key", "name", "source_file", "start_line") if k in props}
        out.update(props)
        return out

    def relationship(self, r, depth: int = 0, endpoints: bool = True) -> dict:
        out = {"type": str(getattr(r, "type", "") or "")}
        out.update(self._props(r, ("confidence",), depth))
        if endpoints:
            for side in ("start", "end"):
                ref = _endpoint(getattr(r, f"{side}_node", None))
                if ref is not None:
                    out[side] = ref
        return out

    def path(self, p, depth: int = 0) -> dict:
        nodes = list(getattr(p, "nodes", ()) or ())
        rels = list(getattr(p, "relationships", ()) or ())
        seq: list = []
        for idx, node in enumerate(nodes):
            seq.append(self.node(node, depth + 1, compact=True))
            if idx < len(rels):
                rel = self.relationship(rels[idx], depth + 1, endpoints=False)
                here = getattr(node, "element_id", None)
                start = getattr(getattr(rels[idx], "start_node", None), "element_id", None)
                if here is not None and start is not None:
                    rel["dir"] = "->" if start == here else "<-"
                seq.append(rel)
        return {"path": seq, "length": len(rels)}


def _is_vector(v) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) >= VECTOR_MIN_LEN
            and all(isinstance(x, float) for x in v))


def _endpoint(node):
    """A relationship endpoint's key (else its name) when the driver hydrated its properties; None otherwise."""
    try:
        if node is None or not hasattr(node, "get"):
            return None
        return node.get("key") or node.get("name")
    except Exception:  # noqa: BLE001
        return None


def _size(row) -> int:
    """UTF-8 bytes of `row` as the MCP client receives it. FastMCP sends EACH list item of a tool's answer as its own
    text block, serialised with pydantic_core.to_json(item, indent=2) — the same text as json.dumps(indent=2,
    ensure_ascii=False) — not compact JSON. Measuring compact JSON let an "about 64 KB" answer reach 79 KB of text for
    whole nodes and 109 KB for 3-hop paths (live, Neo4j 5.26, 2026-10-23: the deeper the nesting, the more the
    indentation adds). FastMCP also sends a structuredContent copy of the list (~compact size); the cap is on the
    text the agent reads."""
    return len(json.dumps(row, ensure_ascii=False, indent=2, default=str).encode("utf-8"))


def _columns(rec, renamed: list) -> list:
    """rec's (column, value) pairs in RETURN order, a column named exactly `note` renamed to the first of note_,
    note__, ... the record does not already use (recorded in `renamed`): the MCP tool appends its own remarks as
    {"note": ...} entries, so no row may look like one."""
    pairs = [(str(k), v) for k, v in rec.items()]
    names = {k for k, _ in pairs}
    if "note" not in names:
        return pairs
    alias = "note_"
    while alias in names:
        alias += "_"
    if not renamed:
        renamed.append(alias)
    return [(alias if k == "note" else k, v) for k, v in pairs]


def shape_rows(records, limit: int, max_bytes: int = MAX_BYTES) -> tuple:
    """(rows, notes) from an iterable of records (each with .items(): (column, value) pairs in RETURN order). Reads at
    most limit + 1 records (one more tells "more rows exist"), stops before the rows' text as the client receives it
    (_size) passes max_bytes, and says so in `notes`. A column named `note` comes back as `note_` (see _columns)."""
    shaper = _Shaper()
    renamed: list = []
    rows: list = []
    used = 0
    more = over = False
    first_too_big = None
    for rec in records:
        if len(rows) >= limit:
            more = True
            break
        row = {k: shaper.value(v) for k, v in _columns(rec, renamed)}
        size = _size(row)
        if used + size > max_bytes:
            over = True
            if not rows:
                first_too_big = size
            break
        rows.append(row)
        used += size
    notes: list = []
    kb = f"{max_bytes / 1000:g} KB"
    if first_too_big is not None:
        notes.append(f"The first row alone is {first_too_big:,} bytes, over the {kb} answer cap, so no rows are "
                     "shown — return fewer or smaller columns (a count(), a few properties instead of whole nodes "
                     "or collect() of nodes).")
    elif over:
        notes.append(f"Showing the first {len(rows)} rows: the answer reached the {kb} size cap. Return fewer "
                     "columns or properties (not whole nodes), or narrow the query.")
    elif more:
        notes.append(f"Showing the first {len(rows)} rows; the query returned more. Narrow it, aggregate (count(), "
                     f"collect()), or raise `limit` (max {LIMIT_MAX}).")
    if shaper.vectors:
        notes.append(f"Embedding vectors were left out (an embedding property, or a list of {VECTOR_MIN_LEN}+ "
                     "floats); a shorter slice of one is not.")
    if renamed and rows:
        notes.append(f"The column `note` is shown as `{renamed[0]}`: a {{\"note\": ...}} entry is always DeepMap's own "
                     "remark.")
    if shaper.cut_strings:
        notes.append(f"{shaper.cut_strings} long string value(s) were cut at {STR_MAX:,} characters.")
    if not rows and first_too_big is None:
        notes.append("No rows. If you expected some: labels, relationship types and property names are "
                     "case-sensitive, a method's name is Class.method, and a language or layer the graph does not "
                     "cover returns nothing (/deep-map:status shows coverage) — empty is not proof of absence.")
    return rows, notes


# ---- running a read ------------------------------------------------------------------------------------------------

def _read_access():
    try:
        from neo4j import READ_ACCESS
        return READ_ACCESS
    except Exception:  # noqa: BLE001 — a test double, or a driver too old to export it
        return "READ"


def run_read_only(driver, query, params=None, *, limit=LIMIT_DEFAULT, timeout=None,
                  max_bytes: int = MAX_BYTES) -> tuple:
    """(rows, notes) for ONE read-only query on `driver` (a neo4j Driver for THIS project's graph):
    check_read_only + check_params first (CypherRejected before Neo4j is touched), then a READ access-mode
    transaction with a server-side timeout that is ALWAYS rolled back. `limit` is clamped into [1, LIMIT_MAX] (a
    note says so); the rows are shaped and bounded by shape_rows. Neo4j errors propagate unchanged (a syntax error,
    the AccessMode refusal of a write, a timeout, an outage) for the caller to word."""
    statement = check_read_only(query)
    values = check_params(params)
    rows_max, clamp_note = clamp_limit(limit)
    budget = float(timeout) if timeout is not None else timeout_s()
    with driver.session(default_access_mode=_read_access(), fetch_size=rows_max + 1) as session:
        tx = session.begin_transaction(timeout=budget, metadata={"app": "deepmap-cypher"})
        try:
            rows, notes = shape_rows(tx.run(statement, values), rows_max, max_bytes)
        finally:
            try:
                tx.rollback()        # never commit: a read keeps nothing, and anything else is discarded
            except Exception:  # noqa: BLE001 — the read's own error (if any) is what the caller needs
                pass
    if clamp_note:
        notes.append(clamp_note)
    return rows, notes


_STATEMENT_CODES = ("Neo.ClientError.Statement.", "Neo.ClientError.Procedure.", "Neo.ClientError.Schema.")


def query_error_message(exc, timeout=None) -> "str | None":
    """A short, actionable message for a Neo4j error that is about THIS query (syntax, an unknown procedure or
    function, the READ-mode refusal of a write, the transaction timeout or memory limit); None for anything else
    (an outage, an auth rejection, a driver error), which the caller's own handling words. Never raises."""
    try:
        code = str(getattr(exc, "code", "") or "")
        msg = " ".join(str(getattr(exc, "message", None) or exc).split())[:400]
        if "Security." in code:
            return None
        if "TransactionTimedOut" in code or "Transaction.TransactionTimedOut" in code:
            took = f" after {float(timeout):g}s" if timeout else ""
            return (f"Neo4j stopped the query{took} (the read timeout). Narrow it: add LIMIT, bound variable-length "
                    "paths (e.g. [:CALLS*1..4]), or anchor the MATCH on a name or key.")
        if any(k in code for k in ("MemoryPool", "OutOfMemory", "MemoryLimit")):
            return ("The query exceeded Neo4j's transaction memory. Narrow it: aggregate instead of collecting whole "
                    "nodes, add LIMIT, or bound variable-length paths.")
        if "AccessMode" in code:
            return f"Neo4j refused a write in the READ transaction ({msg}) — deepmap_cypher only reads."
        if code.startswith(_STATEMENT_CODES):
            return f"Neo4j rejected the query ({code}): {msg}"
    except Exception:  # noqa: BLE001
        pass
    return None
