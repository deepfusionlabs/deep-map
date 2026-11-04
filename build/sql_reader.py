#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap's own reader of .sql files: what a script declares, read the same way on every engine end.

The extraction engine's optional SQL grammar finds no objects in SQL Server scripts (bracketed names, GO batches,
CREATE PROCEDURE) and parses them in superlinear time, so DeepMap reads `.sql` itself (EXTS), after the engine and from
the source alone (design .ai/design/2026-11/sql/design.md section 4). Pure: the stdlib and summarize.humanize, no engine
import, no I/O outside read_file and Cache; the output order is deterministic.

  decode(data)       UTF-8 (BOM dropped), UTF-16 by its BOM or its NUL pattern, else strict UTF-8, else cp1252.
  parse(text)        SqlFacts {"dialect", "decls", "alters", "writes", "dynamic_unread"}, JSON-serialisable (what the
                     cache stores); raises Slow only, past its deadline.
  file_nodes(rel, facts, size=, why=)  the node dicts: one :Module per file, a :Class per table / view / type /
                     package, a :Function per procedure / function / trigger / Oracle package member. Every one carries
                     the private `call_by_name: False` (no bare name in any language binds a SQL node, and no typed edge
                     resolves to one) and a key that holds no line: "sql:<rel>", "sql:<rel>#<kind>:<qualified lower>".
  read_file(path, rel, cache=, budget=)  stat() first (a file over MAX_BYTES is never opened), the per-sync byte
                     Budget in the caller's sorted order, the per-file Cache by content, the per-file DEADLINE_S safety
                     net (a Slow file is a :Module only and never cached); raises OSError only.

How a script is read:
  1. Dialect: tsql when a line holds only GO (sqlcmd's batch separator; no other dialect writes one); else mysql when
     the code (a MySQL-mode mask: comments and strings blanked, made only when a cheap raw test can match) holds a
     backtick identifier, a DELIMITER line whose delimiter is punctuation ($$ // ;;), `) ENGINE=` after a column list,
     or a line-leading `# ` comment; else tsql when the code (an ANSI mask) holds a bracketed [a].[b] name; else
     oracle (a line holding only `/` in the code mask); else ansi. Text in a comment or a string never decides it.
  2. One masking pass, as long as the text, newlines kept: comments and string literals blanked, quoted identifiers'
     insides turned to `x`, PostgreSQL dollar bodies blanked (and re-masked as code for the facts), Oracle q'[...]'
     quotes blanked, MySQL's `/*!NNNNN` wrappers kept as code, SQL*Plus REM / REMARK / PROMPT lines blanked (never
     inside a parenthesised list), the rows of a COPY ... FROM stdin up to its `\\.` line blanked (data, never code),
     T-SQL strings and block comments ended at the next GO line (sqlcmd and SSMS split a script there). Every scan is
     bounded so the pass is linear on any input: a quoted identifier ends at its line and the line end is found once per
     line (a quote that does not close on its line makes every later opener of it there a plain character), a nested
     comment's closer is found once per level, a dollar tag's closer, a q-quote's closer, a COPY's `\\.` line and the
     `*/` of `/*!` come from one search each (a `/*!` inside an open one reuses its `*/`). Names are always read from
     the original text at the same offsets.
  3. Declarations: CREATE [OR REPLACE | OR ALTER] or ALTER of TABLE, VIEW, PROC[EDURE], FUNCTION, TRIGGER, TYPE [BODY],
     PACKAGE [BODY] in the code mask, minus temporary objects, system schemas, SSMS template names <name, type,
     default>, pseudo and stop-word names (only a bare one-part name: [Statement], "values" and dbo.Row are names),
     Oracle's MATERIALIZED VIEW LOG ON t, GRANT / REVOKE / DENY privilege lists and ALTERs that redefine nothing (AS not
     first). A schema written as a migration-tool placeholder (${schema}, $(Schema), &&schema..) is kept as written.
     Spans are bounded and disjoint. One node per (label, kind, qualified name) per file at its first occurrence, except
     a stub, which gives way to a later definition of the same object: a T-SQL stub (`AS`, `AS RETURN 0`, `AS SELECT
     1`, `AS BEGIN END`) to a later ALTER / CREATE OR ALTER (the SSMS and IF OBJECT_ID(...) IS NULL EXEC('CREATE PROC x
     AS RETURN 0') idiom), a MySQL view whose body is only `SELECT 1 AS a, 1 AS b` to any later one (mysqldump's
     placeholder; its 5.7 form, a /*!50001 CREATE TABLE of the view's name, is dropped).
  4. Nested batches: a '...' literal run by EXEC(...), [sys.]sp_executesql [@statement | @stmt =], EXECUTE IMMEDIATE,
     or assigned to a variable the same batch executes (an index built once per file), is parsed as its own document
     (depth 1, lines offset; leading comments skipped); EF Core idempotent scripts and SSMS "check for object existence"
     scripts define objects this way. So is a PostgreSQL DO block's body (it runs when the script runs; Drizzle and
     Supabase create enums that way). A name that ends where the literal ends while the literal is concatenated (`'...
     dbo.Archive_' + @y`) is no name: it counts in dynamic_unread, like any other literal that starts with a declaring
     CREATE / ALTER and is not read (a literal inside a nested batch, text built by concatenation; never a PRINT /
     RAISERROR / SELECT / THROW message, a VALUES row or an @value = argument, never ALTER TABLE).
  5. Oracle package members (the spec's PROCEDURE / FUNCTION headers, else the body's at block depth 0) are :Function
     nodes `pkg.member` with the facts of their body.
  6. Facts, summary input only and never edges: columns and REFERENCES (also from ALTER TABLE), parameters, a trigger's
     table, reads (FROM / JOIN), writes (INSERT / UPDATE ... SET / DELETE / MERGE / TRUNCATE, aliases resolved), runs
     (EXEC / CALL / PERFORM); per file, ALTER TABLE targets and the DML outside every declaration.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import time
from pathlib import PurePosixPath

import summarize  # noqa: E402  (humanize: the same name words as every other DeepMap summary)

READER_REV = 1                          # bump on any change to parse() / file_nodes() output: invalidates the cache
EXTS = (".sql",)                        # what DeepMap reads itself; locked against SOURCE_CODE_EXTS and the census
MAX_BYTES = 8 * 1024 * 1024             # a larger file: its :Module only, "over the 8 MB limit" (stat() before read)
TOTAL_BYTES = 256 * 1024 * 1024         # per sync, in the caller's sorted order; past it: :Module only
DEADLINE_S = 60.0                       # per file; a safety net linear code never meets (a Slow file is never cached)


class Slow(Exception):
    """parse() ran past its deadline."""


class _Clock:
    """The per-file deadline: ticked once per token, declaration, gap and regex match, the time read every 1,024
    ticks; read_file checks it once more after parse (file_nodes after it is linear in the declarations)."""
    __slots__ = ("deadline", "n")

    def __init__(self, seconds):
        self.deadline = None if seconds is None else time.monotonic() + seconds
        self.n = 0

    def tick(self):
        self.n += 1
        if not self.n & 1023 and self.deadline is not None and time.monotonic() > self.deadline:
            raise Slow()


# ---------------------------------------------------------------------------------------------------- decoding
def decode(data: bytes) -> tuple[str, str]:
    """(text, encoding): a BOM decides; else UTF-16 when > 30 % of the odd (even) bytes of the first 4 KB are NUL and
    < 5 % of the others; else strict UTF-8; else cp1252 with replacement. Lines are counted by "\\n" afterwards."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace"), "utf-16-be"
    head = data[:4096]
    if len(head) >= 8:
        odd, even = head[1::2], head[0::2]
        odd_nul, even_nul = odd.count(0) / len(odd), even.count(0) / len(even)
        if odd_nul > 0.3 and even_nul < 0.05:
            return data.decode("utf-16-le", "replace"), "utf-16-le"
        if even_nul > 0.3 and odd_nul < 0.05:
            return data.decode("utf-16-be", "replace"), "utf-16-be"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), "cp1252"


# ---------------------------------------------------------------------------------------------------- masking
_GO_LINE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*(?:--[^\n]*)?\r?$")
_SLASH_LINE = re.compile(r"(?m)^[ \t]*/[ \t]*\r?$")
_DELIM_LINE = re.compile(r"(?im)^[ \t]*DELIMITER[ \t]+[^\w\s()\[\]'\"`,.]{1,16}[ \t]*\r?$")   # $$ // ;; |, never a word
_BACKTICK = re.compile(r"`[^`\n]{1,256}`")
_ENGINE_HINT = re.compile(r"(?i)\)\s*(?:(?:default\s+)?[a-z_]{1,32}\b\s*=\s*\w{1,64}\b\s*,?\s*){0,4}engine\s*=")
_HASH_LINE = re.compile(r"(?m)^[ \t]*#(?:[ \t!]|\r?$)")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")   # keeps every offset
_BRACKET_NAME = re.compile(r"\[[A-Za-z_][\w ]{0,127}\]\s*\.\s*\[")
_COPY = r"|(?im:^[ \t]*\\?copy(?=[ \t]))"              # COPY ... FROM stdin: its rows follow (pg_dump, psql)
_TOK_MYSQL = re.compile(r"--|/\*!?|['\"\[`#]" + _COPY)
_TOK_TSQL = re.compile(r"--|/\*|['\"\[`]" + _COPY)
_TOK_ANSI = re.compile(r"--|/\*|['\"\[`]|\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$" + _COPY
                       + r"|(?im:^[ \t]*(?:REMARK|REM|PROMPT)(?=[ \t\r\n]|\Z))")
_COPY_STDIN = re.compile(r"(?i)\bfrom[ \t]+stdin\b")
_COPY_END = re.compile(r"(?m)^\\\.[ \t]*\r?$")
_Q_CLOSE = {"[": "]", "{": "}", "(": ")", "<": ">"}
_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")
_SQ_STOP = re.compile(r"['\\]")
_DQ_STOP = re.compile(r'["\\]')
_IDCH = re.compile(r"[\w$#@]")
_NL = re.compile("\n")
_WS_RUN = re.compile(r"[ \t\r\n]*")
_HWS_RUN = re.compile(r"[ \t]*")


def _mysqlish(text: str, clock):
    """(is MySQL, the MySQL-mode mask when one was made): a backtick identifier, a DELIMITER line, `) ENGINE=` or a
    line-leading `# ` comment in the CODE. The mask is made only when the raw text can match (a substring test, then
    the regex), so most files never pay for it; on it, comments and strings are blanked, so a backtick in a COMMENT ON
    string, `engine = 'v8'` or a `delimiter` column never decides the dialect."""
    if not ("`" in text and _BACKTICK.search(text)):
        low = text.translate(_ASCII_LOWER)
        if not (("delimiter" in low and _DELIM_LINE.search(text)) or ("engine" in low and _ENGINE_HINT.search(text))
                or ("#" in text and _HASH_LINE.search(text))):
            return False, None
    hashes: list = []
    pre = _mask(text, "mysql", [], clock, hashes)
    mk = pre[0]
    if _BACKTICK.search(mk) or _DELIM_LINE.search(mk) or _ENGINE_HINT.search(mk):
        return True, pre
    for s in hashes:                                    # a `#` comment the masker found in code
        if text[s + 1:s + 2] in ("", " ", "\t", "!", "\r", "\n") and not text[text.rfind("\n", 0, s) + 1:s].strip():
            return True, pre
    return False, pre


def _blank(s: str) -> str:
    """s with every character but a newline turned to a space."""
    if "\n" not in s:
        return " " * len(s)
    return "\n".join(" " * len(line) for line in s.split("\n"))


def _kw(word: str) -> str:
    """A keyword that does not continue an identifier or a dotted name: the boundary is checked by a lookbehind AFTER
    the word (`WORD(?<![\\w$#@.].{len})`), so the regex engine can skip ahead by the word's first character (a
    lookbehind in front costs a try at every position: about twice the scan time)."""
    return f"{word}(?<![\\w$#@.]{'.' * len(word)})"


def _after(starts: list, s: int, default: int) -> int:
    """The first of the sorted `starts` after position s, else `default`."""
    j = bisect.bisect_right(starts, s)
    return starts[j] if j < len(starts) else default


def _q_quote(text: str, s: int) -> bool:
    """The ' at s opens an Oracle q-quote: q'<d> (or nq'<d>), the q standalone, <d> not whitespace."""
    if s < 1 or text[s - 1] not in "qQ" or s + 1 >= len(text) or text[s + 1] in " \t\r\n":
        return False
    j = s - 1
    if j > 0 and text[j - 1] in "nN":
        j -= 1
    return j == 0 or not _IDCH.match(text, j - 1)


def _mask(text: str, mode: str, go_starts: list, clock: _Clock, hashes: list | None = None):
    """(code mask, dollar bodies [(tag start, body start, body end)], literals [(start, end, closed)] of every '...'
    string, text[start + 1:end - 1] its content; for a q-quote start / end are its delimiters). The mask is as long as
    the text with its newlines kept. `mode`: "mysql", "tsql" or "ansi" (Oracle and PostgreSQL alike). `hashes`, when
    given, collects where each MySQL `#` comment starts."""
    tok_re = _TOK_MYSQL if mode == "mysql" else _TOK_TSQL if mode == "tsql" else _TOK_ANSI
    mysql = mode == "mysql"
    ansi = track = mode == "ansi"       # the SQL*Plus line rule needs the last code character before a line
    n = len(text)
    out: list = []
    literals: list = []
    dollars: list = []
    no_close: dict = {}                 # closer -> end of the line on which an opener found none
    cur_eol = -1                        # end of the line holding the last quote opener (openers only move forward)
    exec_end = None                     # MySQL /*! ... */: where its */ is, found once when it opens
    no_exec_close = n + 1               # no */ at all after this position (an unclosed /*! searched once)
    copy = None                         # COPY ... FROM stdin: (rows start, rows end), blanked as data
    last_sig = ""
    tags = None                         # dollar tag -> its sorted positions, indexed once
    i = 0

    def bound(s):                       # T-SQL: a string or block comment ends at the next GO line
        return _after(go_starts, s, n) if go_starts else n

    while i < n:
        clock.tick()
        if copy is not None and i > copy[0]:
            copy = None                 # a token ran on into the rows (malformed): they are scanned as text
        lim = n if copy is None else copy[0]
        m = tok_re.search(text, i, lim)
        if exec_end is not None:
            if exec_end < i:
                exec_end = None
            elif exec_end < lim and (m is None or exec_end < m.start()):
                out.append(text[i:exec_end])
                out.append("  ")
                i = exec_end + 2
                exec_end = None
                continue
        if m is None:
            if copy is None:
                out.append(text[i:])
                break
            a, b = copy
            out.append(text[i:a])
            out.append(_blank(text[a:b]))
            i, copy = b, None
            continue
        s = m.start()
        tok = m.group(0)
        if s > i:
            chunk = text[i:s]
            out.append(chunk)
            if track:
                t = chunk.rstrip()
                if t:
                    last_sig = t[-1]
        c = tok[0]
        if tok[-4:].lower() == "copy":                  # COPY ... FROM stdin;: the rows up to a \. line are data
            e = text.find("\n", s)
            e = n if e < 0 else e
            line = text[s:e]
            if e < n and _COPY_STDIN.search(line) and ("\\" in tok or line.rstrip().endswith(";")):
                cm = _COPY_END.search(text, e + 1)
                copy = (e + 1, cm.end() if cm else n)
            out.append(tok)
            last_sig = tok[-1]
            i = m.end()
        elif tok == "--" or (c == "#" and not _IDCH.match(text, s + 1)):
            e = text.find("\n", s)
            e = n if e < 0 else e
            out.append(" " * (e - s))
            if c == "#" and hashes is not None:
                hashes.append(s)
            i = e
        elif c == "#":                                  # MySQL #temp is an identifier, never a comment
            out.append(c)
            i = s + 1
        elif tok == "/*!":                              # mysqldump's executable comment: code, markers blanked
            k = s + 3
            while k < n and text[k].isdigit():
                k += 1
            out.append(" " * (k - s))
            if exec_end is None or exec_end < k:        # MySQL comments do not nest: an inner /*! keeps the */
                e = text.find("*/", k) if k < no_exec_close else -1
                if e < 0:
                    no_exec_close = min(no_exec_close, k)
                exec_end = e if e >= 0 else None
            i = k
        elif tok == "/*":
            lim = bound(s)
            depth, k = 1, s + 2
            close = text.find("*/", k, lim)
            while True:
                clock.tick()
                if close < 0:
                    k = lim
                    break
                a = text.find("/*", k, close) if not mysql else -1
                if a >= 0:                              # nested (never in MySQL): its closer is the next one
                    depth += 1
                    k = a + 2
                    continue
                depth -= 1
                k = close + 2
                if not depth:
                    break
                close = text.find("*/", k, lim)
            out.append(_blank(text[s:k]))
            i = k
        elif ansi and c == "'" and _q_quote(text, s):  # Oracle q'[...]': no escapes, ends at the delimiter + '
            p = text.find(_Q_CLOSE.get(text[s + 1], text[s + 1]) + "'", s + 2)
            end = n if p < 0 else p + 2
            out.append(_blank(text[s:end]))
            literals.append((s + 1, end - 1, p >= 0))
            last_sig = c
            i = end
        elif c == "'" or (c == '"' and mysql):
            lim = bound(s)
            bs = mysql or (s > 0 and text[s - 1] in "eE" and (s < 2 or not _IDCH.match(text[s - 2])))
            stop = (_SQ_STOP if c == "'" else _DQ_STOP) if bs else None
            k, end, closed = s + 1, lim, False
            while k < lim:
                clock.tick()
                if stop is not None:
                    sm = stop.search(text, k, lim)
                    p = sm.start() if sm else -1
                else:
                    p = text.find(c, k, lim)
                if p < 0:
                    break
                if text[p] == "\\":
                    k = p + 2
                    continue
                if p + 1 < lim and text[p + 1] == c:
                    k = p + 2
                    continue
                end, closed = p + 1, True
                break
            out.append(_blank(text[s:end]))
            if c == "'":
                literals.append((s, end, closed))
            last_sig = c
            i = end
        elif c in '"[`':                                # a quoted identifier: it ends on its own line
            close = "]" if c == "[" else c
            if no_close.get(close, -1) > s:
                out.append(c)
                last_sig = c
                i = s + 1
                continue
            if s >= cur_eol:
                cur_eol = text.find("\n", s)
                cur_eol = n if cur_eol < 0 else cur_eol
            k = text.find(close, s + 1, cur_eol)
            while k >= 0 and k + 1 < cur_eol and text[k + 1] == close:
                clock.tick()
                k = text.find(close, k + 2, cur_eol)
            if k < 0:
                no_close[close] = cur_eol
                out.append(c)
                last_sig = c
                i = s + 1
                continue
            out.append(c + "x" * (k - s - 1) + close)
            last_sig = close
            i = k + 1
        elif c == "$":                                  # a PostgreSQL dollar quote $tag$ ... $tag$
            if s > 0 and _IDCH.match(text, s - 1):
                out.append(tok)
                last_sig = "$"
                i = m.end()
                continue
            if tags is None:
                tags = {}
                for dm in _DOLLAR.finditer(text):
                    tags.setdefault(dm.group(0), []).append(dm.start())
            pos = tags.get(tok, ())
            j = bisect.bisect_left(pos, m.end())
            if j >= len(pos):
                out.append(tok)
                last_sig = "$"
                i = m.end()
                continue
            e = pos[j]
            dollars.append((s, m.end(), e))
            out.append(_blank(text[s:e + len(tok)]))
            last_sig = "$"
            i = e + len(tok)
        else:                                           # a SQL*Plus REM / REMARK / PROMPT line
            if last_sig and last_sig in "(,":           # ...unless it is a column in a list (`prompt TEXT,`)
                out.append(tok)
                last_sig = tok[-1]
                i = m.end()
                continue
            e = text.find("\n", s)
            e = n if e < 0 else e
            out.append(" " * (e - s))
            i = e
    res = "".join(out)
    if len(res) != n:
        raise AssertionError(f"sql_reader mask length {len(res)} != {n}")
    return res, dollars, literals


class _Doc:
    """One text and its masks (and their ASCII-lowercased copies), with a newline index built once (line numbers and
    line ends by bisect)."""
    __slots__ = ("text", "masked", "body", "lmask", "lbody", "nl", "mysql")

    def __init__(self, text, masked, body, mysql):
        self.text, self.masked, self.body, self.mysql = text, masked, body, mysql
        self.lmask = masked.translate(_ASCII_LOWER)    # keyword scans run on these (same offsets): a lowercase
        self.lbody = self.lmask if body is masked else body.translate(_ASCII_LOWER)   # literal is found faster
        self.nl = [m.start() for m in _NL.finditer(text)]

    def line(self, i: int) -> int:
        return bisect.bisect_left(self.nl, i) + 1

    def eol(self, i: int) -> int:
        j = bisect.bisect_left(self.nl, i)
        return self.nl[j] if j < len(self.nl) else len(self.text)


# ---------------------------------------------------------------------------------------------------- names
_BARE = re.compile(r"[^\W\d][\w$#@]*|[#@][\w$#@]*")
_SIMPLE_NAME = re.compile(r"[ \t\r\n]*([^\W\d][\w$#@]*|[#@][\w$#@]*)(?![\w$#@]|[ \t]*\.)")
# a schema written as a migration-tool placeholder: Flyway / Liquibase ${x}, SQLCMD $(x), SQL*Plus &x. / &&x. (whose `.`
# ends the variable, so &&schema..obj is schema.obj and &x.obj is one concatenated name, no name we can read)
_PLACEHOLDER = re.compile(r"\$\{[^}\n]{1,128}\}|\$\([^)\n]{1,128}\)|&{1,2}[A-Za-z_]\w{0,127}\.")
_PLACEHOLDER_HEADS = ("${", "$(", "&")


class _Parts(list):
    """A name's parts; `quoted`: its last part was written [..], ".." or `..` (then it is a name, whatever it says)."""
    __slots__ = ("quoted",)


def _name(doc: _Doc, i: int, limit, mk: str):
    """(parts, end) of a 1-4 part name at or after i: [..], ".." (not MySQL) and `..` parts end on their own line;
    whitespace and comments are skipped in the mask `mk`, the parts read from the original text; `db..obj` keeps an
    empty part; a migration-tool placeholder (${schema}, $(Schema), &&schema.) is a part as written. A dot not
    followed by a part (`[dbo].[unclosed`) is no name at all: []."""
    text = doc.text
    n = len(text) if limit is None else min(limit, len(text))
    m = _SIMPLE_NAME.match(mk, i, n) if i < n else None
    if m:                                               # the common case: one bare identifier, no dot after it
        return [m.group(1)], m.end(1)
    parts = _Parts()
    parts.quoted = False
    for _ in range(8):
        i = _WS_RUN.match(mk, i, n).end() if i < n else i
        c = mk[i] if i < n else ""
        if c and c in '["`' and not (c == '"' and doc.mysql):
            close = "]" if c == "[" else c
            k = mk.find(close, i + 1, min(doc.eol(i), n))
            if k < 0:
                return ([] if parts else parts), i
            parts.append(text[i + 1:k].replace(close + close, close))
            parts.quoted = True
            i = k + 1
        elif c == "." and parts:
            parts.append("")
            parts.quoted = False
        else:
            m = (_PLACEHOLDER.match(text, i, min(doc.eol(i), n)) if c in "$&" else None) if c else None
            m = m or (_BARE.match(text, i, n) if c else None)
            if not m:
                return ([] if parts else parts), i
            parts.append(m.group(0))
            parts.quoted = False
            i = m.end()
        j = _HWS_RUN.match(mk, i, n).end() if i < n else i
        if j < n and mk[j] == "." and len(parts) < 4:
            i = j + 1
            continue
        break
    return parts, i


def _qual(parts) -> str:
    return ".".join(p for p in parts if p)


def _obj(parts) -> str:
    for p in reversed(parts):
        if p:
            return p
    return ""


_SYS_SCHEMAS = {"sys", "information_schema", "pg_catalog", "mysql", "performance_schema"}
_PSEUDO = {"inserted", "deleted", "new", "old", "dual"}
_STOP = {"on", "set", "into", "select", "values", "or", "as", "where", "from", "join", "with", "table", "when",
         "then", "begin", "end", "if", "exists", "not", "default", "output", "top", "openjson", "openquery",
         "unnest", "lateral", "only", "each", "row", "statement", "instead", "of", "to", "for", "and", "in", "is",
         "by"}


def _usable(parts) -> bool:
    """A name that can be a SQL object: not a temp table or variable, a migration-tool placeholder, an SSMS template
    placeholder (<name, type, default>) or an object of a system schema; a bare one-part name also not a pseudo table
    or a stop word (`FROM ONLY t`, `GRANT CREATE VIEW TO u`), while [Statement], "values" and dbo.Row are names."""
    o = _obj(parts)
    if not o or o[0] in "#@" or o.startswith(_PLACEHOLDER_HEADS):
        return False
    if any("<" in p or ">" in p for p in parts):
        return False
    named = [p for p in parts if p]
    if len(named) == 1 and not getattr(parts, "quoted", False) and (o.lower() in _PSEUDO or o.lower() in _STOP):
        return False
    return not (len(named) > 1 and named[-2].lower() in _SYS_SCHEMAS)


def _uniq(items) -> list:
    seen, out = set(), []
    for x in items:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ---------------------------------------------------------------------------------------------------- declarations
_DECL = re.compile(                                    # on lowercased text (Doc.lmask), like every keyword scan
    r"(?P<verb>" + _kw("create") + r"(?:\s+or\s+(?:replace|alter))?|" + _kw("alter") + r")\s+"
    r"(?P<mods>(?:(?:definer\s*=\s*\S{1,256}|algorithm\s*=\s*\w+|sql\s+security\s+\w+|editionable|noneditionable|"
    r"no\s+force|force|unlogged|recursive|secure|global|local|temp|temporary|materialized|constraint|virtual|foreign|"
    r"external)\s+)*)"
    r"(?P<kind>table|view|procedure|proc|function|trigger|type\s+body|type|package\s+body|package)\b"
    r"(?:\s+if\s+not\s+exists\b)?")
_KIND = {"TABLE": ("Class", "table"), "VIEW": ("Class", "view"), "TYPE": ("Class", "type"),
         "TYPE BODY": ("Class", "type"), "PACKAGE": ("Class", "package"), "PACKAGE BODY": ("Class", "package"),
         "PROCEDURE": ("Function", "procedure"), "PROC": ("Function", "procedure"),
         "FUNCTION": ("Function", "function"), "TRIGGER": ("Function", "trigger")}
_ROUTINES = {"view", "procedure", "function", "trigger", "package"}
_ALTER_DEFINES = {"PROCEDURE", "PROC", "FUNCTION", "VIEW", "TRIGGER", "PACKAGE", "PACKAGE BODY"}
_ALTER_STOP = re.compile(_kw("as") + r"\b|;|" + _kw("owner") + r"\s+to\b|" + _kw("rename") + r"\b|" + _kw("set")
                         + r"\s+schema\b|" + _kw("depends") + r"\s+on\b|" + _kw("compile") + r"\b|" + _kw("comment")
                         + r"\b")                     # on Doc.lmask, within the ALTER's own statement
_MV_LOG = re.compile(r"\s+on\b")                       # Oracle CREATE MATERIALIZED VIEW LOG ON t: no view
_MYSQL_STUB = re.compile(r"(?i)AS\s+SELECT\s+1\s+AS\s+(?:`[^`\n]*`|\w+)(?:\s*,\s*1\s+AS\s+(?:`[^`\n]*`|\w+))*\s*\Z")
_PH_BEFORE = re.compile(r"/\*!50001[ \t]*\Z")          # mysqldump 5.7 wraps a view's placeholder table in /*!50001
_PRIV_HEAD = re.compile(r"(?i)\s*(?:GRANT|REVOKE|DENY)\b")
_PRIV_LIST = re.compile(r"(?i)\s*(?:GRANT|REVOKE|DENY)\b(?:(?!\b(?:ON|TO|FROM)\b)[\w\s,])*\Z")
_AS_TABLE = re.compile(r"(?i)AS\s+TABLE\b")
_AS_WORD = re.compile(r"(?i)\bAS\b")
_STUB_BODY = re.compile(r"(?i)AS\s*(?:RETURN\s*0?|SELECT\s+1|BEGIN\s*(?:RETURN\s*0?\s*;?\s*)?END)?\s*;?\s*\Z")
_END_BLOCK = _kw("end") + r"(?:\s+case)?(?!\s+(?:if|loop|while|repeat)\b)"     # END IF / LOOP / ...: no block
_SPAN_TOK = re.compile(r"(?:" + _kw("begin") + r"(?!\s+(?:tran|transaction|distributed|dialog|conversation)\b)|"
                       + _kw("case") + "|" + _END_BLOCK + r")\b|[();]")
_MEMBER_TOK = re.compile(r"(?:" + _kw("begin") + "|" + _kw("case") + "|" + _END_BLOCK + "|" + _kw("procedure")
                         + "|" + _kw("function") + r")\b")
_ALTER_TABLE = re.compile(_kw("alter") + r"\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?")
_ALTER_TABLE_END = re.compile(r";|(?:" + _kw("alter") + "|" + _kw("create") + r")\s")
_PAREN = re.compile(r"[()]")
_PAREN_COMMA = re.compile(r"[(),]")
_ON = re.compile(r"(?i)" + _kw("ON") + r"\s+(?=[\[\"`\w])")


def _norm(word: str) -> str:
    return " ".join(word.upper().split())


def _privilege(masked: str, s: int, go_starts: list) -> bool:
    """The statement holding position s (from the last ';' or GO line within 2,048 characters) is a GRANT / REVOKE /
    DENY whose text up to s is a privilege list (words and commas, no ON / TO / FROM yet): `GRANT CREATE SESSION,
    CREATE VIEW, CREATE TABLE TO sh`. A GRANT ... ON ... TO line followed by a CREATE on the next line (T-SQL needs no
    semicolon) is not one. Bounded look-back."""
    lo = max(0, s - 2048)
    st = masked.rfind(";", lo, s)
    if go_starts:
        j = bisect.bisect_right(go_starts, s) - 1
        if j >= 0 and go_starts[j] > st:
            e = masked.find("\n", go_starts[j], s)
            st = e if e >= 0 else st
    start = st + 1 if st >= 0 else lo
    return bool(_PRIV_HEAD.match(masked, start, s)) and bool(_PRIV_LIST.match(masked, start, s))


def _head(text: str, a: int, b: int) -> int:
    """The first position in [a, b) that is not whitespace or a -- / /* */ comment (SSMS writes a module's header
    comment before its CREATE); each closer is found once, so the cost is bounded by the literal."""
    for _ in range(256):
        a = _WS_RUN.match(text, a, b).end() if a < b else a
        if text.startswith("--", a, b):
            e = text.find("\n", a, b)
            a = b if e < 0 else e + 1
        elif text.startswith("/*", a, b):
            e = text.find("*/", a + 2, b)
            a = b if e < 0 else e + 2
        else:
            break
    return a


def _declaring(text: str, a: int, b: int) -> bool:
    """text[a:b] starts (after comments) with a statement that declares an object (the dynamic-SQL count): CREATE [OR
    ...] of a kind, not temporary, of a name that is no #temp / @variable and no bare stop word ('Create table for the
    customers' is a message), or ALTER of a routine, view or trigger (never ALTER TABLE). A literal that ends right
    after the kind (`'CREATE TABLE ' + @name`) declares one."""
    k = _head(text, a, b)
    m = _DECL.match(text[k:min(b, k + 2048)].translate(_ASCII_LOWER))       # the head only: never a whole literal
    if not m:
        return False
    if _norm(m.group("verb")) == "ALTER" and _norm(m.group("kind")) not in _ALTER_DEFINES:
        return False
    mods = m.group("mods").upper().split()
    if "TEMP" in mods or "TEMPORARY" in mods:
        return False
    k = _WS_RUN.match(text, k + m.end(), b).end() if k + m.end() < b else k + m.end()
    if k >= b:
        return True
    if text[k] in "#@":
        return False
    w = _BARE.match(text, k, b)
    if w is None or text.startswith(".", _HWS_RUN.match(text, w.end(), b).end()):
        return True
    return w.group(0).lower() not in _STOP and w.group(0).lower() not in _PSEUDO


def _matching_paren(mk: str, i: int, limit: int, clock: _Clock) -> int:
    depth = 0
    for m in _PAREN.finditer(mk, i, limit):
        clock.tick()
        if m.group(0) == "(":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.start()
    return -1


def _split_top(mk: str, a: int, b: int, clock: _Clock) -> list:
    out, depth, start = [], 0, a
    for m in _PAREN_COMMA.finditer(mk, a, b):
        clock.tick()
        c = m.group(0)
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            out.append((start, m.start()))
            start = m.start() + 1
    out.append((start, b))
    return out


def _semicolon_end(lk: str, a: int, b: int, clock: _Clock) -> int:
    """The first ';' in [a, b) at parenthesis depth 0 and BEGIN / CASE ... END depth 0 (END IF / LOOP / WHILE /
    REPEAT close no block, BEGIN TRAN opens none), else b."""
    paren = block = 0
    for m in _SPAN_TOK.finditer(lk, a, b):
        clock.tick()
        t = m.group(0)
        if t == "(":
            paren += 1
        elif t == ")":
            paren -= 1
        elif t == ";":
            if paren <= 0 and block <= 0:
                return m.start()
        elif t[:3].upper() == "END":
            block -= 1
        else:
            block += 1
    return b


_CONSTRAINT_HEAD = {"constraint", "primary", "foreign", "unique", "check", "index", "key", "period", "fulltext",
                    "spatial", "exclude", "like"}
_REF = re.compile(r"(?i)" + _kw("REFERENCES") + r"\s+")


def _columns(doc: _Doc, a: int, b: int, clock: _Clock):
    """(columns, references) of a table's parenthesised list [a, b): the first name of each top-level element that is
    not a constraint (a quoted first name is always a column), and every REFERENCES target in it."""
    mk = doc.masked
    cols, refs = [], []
    for ea, eb in _split_top(mk, a, b, clock):
        k = _WS_RUN.match(mk, ea, eb).end() if ea < eb else ea
        if k >= eb:
            continue
        quoted = mk[k] in "[`" or (mk[k] == '"' and not doc.mysql)
        parts, _e = _name(doc, k, eb, mk)
        if parts and parts[0] and (quoted or parts[0].lower() not in _CONSTRAINT_HEAD):
            cols.append(parts[0])
        for r in _REF.finditer(mk, ea, eb):
            clock.tick()
            rp, _e = _name(doc, r.end(), eb, mk)
            if _usable(rp):
                refs.append(_obj(rp))
    return cols, refs


_PTOK = re.compile(r"[\[\"`][^\]\"`\n]*[\]\"`]|[\w@$#]+")
_PARAM_MODES = {"IN", "OUT", "INOUT", "VARIADIC"}
_HDR_END = re.compile(r"(?i)\b(?:AS|IS|RETURNS|RETURN|BEGIN|LANGUAGE)\b")
_TSQL_PARAM = re.compile(r"@\w+")


def _params(doc: _Doc, nend: int, end: int, tsql: bool, clock: _Clock) -> list:
    """Parameters: the first name of each top-level element (of 2+ tokens, after IN / OUT / INOUT / VARIADIC) of the
    parenthesised list after the name; with no list, T-SQL's distinct @names before AS / RETURNS (4,000 characters at
    most)."""
    mk = doc.masked
    stop = min(end, nend + 4000)
    k = _WS_RUN.match(mk, nend, stop).end() if nend < stop else nend
    if k < stop and mk[k] == "(":
        close = _matching_paren(mk, k, end, clock)
        if close < 0:
            return []
        out = []
        for ea, eb in _split_top(mk, k + 1, close, clock):
            toks = [t for t in _PTOK.finditer(mk, ea, eb) if t.group(0).upper() not in _PARAM_MODES]
            if len(toks) >= 2:
                parts, _e = _name(doc, toks[0].start(), eb, mk)
                if parts and parts[0]:
                    out.append(parts[0])
        return _uniq(out)
    if tsql or (k < stop and mk[k] == "@"):
        hm = _HDR_END.search(mk, nend, stop)
        return _uniq(_TSQL_PARAM.findall(mk, nend, hm.start() if hm else stop))
    return []


# ---------------------------------------------------------------------------------------------------- facts
_READ = re.compile(r"(?:" + _kw("from") + "|" + _kw("join") + r")\s+(?=[\[\"`\w#@$&])")
_WRITE = re.compile(r"(?:(?P<ins>" + _kw("insert") + r")(?:\s+(?:ignore|low_priority|delayed|high_priority))*"
                    r"(?:\s+into)?|(?P<upd>" + _kw("update") + r")(?:\s+(?:ignore|low_priority))*|"
                    r"(?P<del>" + _kw("delete") + r")(?:\s+from)?|(?P<mrg>" + _kw("merge") + r")(?:\s+into)?|"
                    r"(?P<trn>" + _kw("truncate") + r"\s+table))\s+(?=[\[\"`\w#@$&])")
_RUN = re.compile(r"(?:(?:" + _kw("execute") + "|" + _kw("exec") + r")\s+(?:(?:function|procedure)\s+)?"
                  r"(?:@\w+\s*=\s*)?|" + _kw("call") + r"\s+|" + _kw("perform") + r"\s+)(?=[\[\"`\w])")
_CTE = re.compile(r"(?:" + _kw("with") + r"\s+(?:recursive\s+)?|,\s*)(\w+)\s*(?:\([^()]{0,512}\)\s*)?as\s*\(")
_ALIAS_AFTER = re.compile(r"(?i)\s+(?:AS\s+)?([A-Za-z_]\w*)")
_UPD_SET = re.compile(r"(?is)\s*(?:WITH\s*\([^)]{0,200}\)\s*)?SET\b|\s+\w+\s+SET\b")
_JOIN_WORDS = {"inner", "left", "right", "full", "cross", "outer", "group", "order", "where", "union", "having",
               "limit", "with", "for", "option", "pivot", "unpivot", "offset", "fetch", "window", "using", "natural",
               "straight_join", "returning", "except", "intersect", "minus", "connect", "start", "into"}
_NOT_A_READ = {"delete", "distinct", "year", "month", "day", "hour", "minute", "second", "epoch", "week", "quarter",
               "dow", "doy", "isodow", "isoyear", "century", "decade", "millennium", "milliseconds", "microseconds",
               "timezone", "both", "leading", "trailing"}
_NOT_A_WRITE = {"on", "after", "before", "for", "of", "or", "instead"}
_NOT_A_RUN = {"sp_executesql", "immediate"}


def _prev_word(s: str, i: int, lo: int) -> str:
    """The word ending before position i (whitespace skipped), looked for no further back than lo; lowercased."""
    j = i
    while j > lo and s[j - 1] in " \t\r\n":
        j -= 1
    k = j
    while k > lo and (s[k - 1].isalnum() or s[k - 1] == "_"):
        k -= 1
    return s[k:j].lower()


def _facts(doc: _Doc, a: int, b: int, clock: _Clock, runs: bool = True) -> dict:
    """reads / writes / runs of the span [a, b) of the body mask; a name read and written is listed once, under writes;
    CTE names are left out; a one-part write target that is a FROM / JOIN alias resolves to its table. `runs=False`
    skips the EXEC / CALL scan (a file's top-level writes need none)."""
    mk, lk = doc.body, doc.lbody
    ctes = {m.group(1) for m in _CTE.finditer(lk, a, b)}
    aliases: dict = {}
    reads, writes, ran = [], [], []
    for m in _READ.finditer(lk, a, b):
        clock.tick()
        if _prev_word(lk, m.start(), max(a, m.start() - 64)) in _NOT_A_READ:
            continue
        parts, end = _name(doc, m.end(), b, mk)
        am = _ALIAS_AFTER.match(mk, end, b) if end < b else None
        if am and parts and am.group(1).lower() not in _STOP and am.group(1).lower() not in _JOIN_WORDS:
            aliases[am.group(1).lower()] = parts
        if _usable(parts) and _obj(parts).lower() not in ctes:
            reads.append(_obj(parts))
    for m in _WRITE.finditer(lk, a, b):
        clock.tick()
        if _prev_word(lk, m.start(), max(a, m.start() - 64)) in _NOT_A_WRITE:
            continue
        parts, end = _name(doc, m.end(), b, mk)
        if m.group("upd") and not _UPD_SET.match(mk, end, min(b, end + 200)):
            continue
        if len([p for p in parts if p]) == 1 and parts[-1].lower() in aliases:
            parts = aliases[parts[-1].lower()]
        if _usable(parts) and _obj(parts).lower() not in ctes:
            writes.append(_obj(parts))
    for m in _RUN.finditer(lk, a, b) if runs else ():
        clock.tick()
        parts, _end = _name(doc, m.end(), b, mk)
        if _usable(parts) and _obj(parts).lower() not in _NOT_A_RUN:
            ran.append(_obj(parts))
    writes = _uniq(writes)
    written = {w.lower() for w in writes}
    return {"reads": [r for r in _uniq(reads) if r.lower() not in written], "writes": writes, "runs": _uniq(ran)}


def _members(doc: _Doc, a: int, b: int, clock: _Clock) -> list:
    """Oracle package members in [a, b): [(kind, name, header start, name end, next header or b)], the PROCEDURE /
    FUNCTION headers at BEGIN / CASE ... END depth 0 (END IF / LOOP / WHILE / REPEAT close no block)."""
    mk = doc.masked
    heads, depth = [], 0
    for m in _MEMBER_TOK.finditer(doc.lmask, a, b):
        clock.tick()
        w = m.group(0)[:3].upper()
        if w in ("BEG", "CAS"):
            depth += 1
        elif w == "END":
            depth = max(0, depth - 1)
        elif depth == 0:
            parts, nend = _name(doc, m.end(), b, mk)
            if _usable(parts) and len([p for p in parts if p]) == 1:
                heads.append(("procedure" if w == "PRO" else "function", _obj(parts), m.start(), nend))
    return [(k, nm, hs, he, heads[i + 1][2] if i + 1 < len(heads) else b) for i, (k, nm, hs, he) in enumerate(heads)]


# ---------------------------------------------------------------------------------------------------- nested batches
_CREATE_HEAD = re.compile(r"(?i)(?:CREATE|ALTER)\b")   # at the literal's head (_head: after comments)
_EXEC_BEFORE = re.compile(r"(?i)(?:\bEXEC(?:UTE)?\s*\(\s*|\bsp_executesql\]?\s+(?:@stmt\s*=\s*|@statement\s*=\s*)?|"
                          r"\bEXECUTE\s+IMMEDIATE\s+)\Z")   # [sys].[sp_executesql]: read on _unbracket's text
_ASSIGN_BEFORE = re.compile(r"(?i)(?:\bSET\s+(@\w+)\s*\+?=\s*|\bSELECT\s+(@\w+)\s*=\s*|"
                            r"(?:\bDECLARE|,)\s*(@\w+)\s+(?:AS\s+)?N?(?:VAR)?CHAR\s*\([^)]{0,16}\)\s*=\s*|"
                            r"(?<![\w$#@])([A-Za-z_]\w*)\s*:=\s*)\Z")
_EXECUTED_VAR = re.compile(r"(?i)(?:\bEXEC(?:UTE)?\s*\(\s*|\bsp_executesql\]?\s+(?:@stmt\s*=\s*|@statement\s*=\s*)?)"
                           r"(@\w+)|\bEXECUTE\s+IMMEDIATE\s+([A-Za-z_]\w*)")   # built only when a literal needs it
_MSG_BEFORE = re.compile(r"(?i)(?:\bPRINT|\bRAISERROR\s*\(|\bTHROW\s+[^,;]{1,40},|\bSELECT|\bRAISE\s+\w+|"
                         r"\bPUT_LINE\s*\(|\bIS|\bCOMMENT(?:\s*=)?|@value\s*=)\s*\Z")   # messages, never executed
_CONCAT_AFTER = re.compile(r"\s*(?:\+|\|\|)")          # the literal is one piece of a concatenation
_DO_BEFORE = re.compile(r"(?i)\bDO\s+(?:LANGUAGE\s+\w+\s+)?\Z")   # a PostgreSQL DO block: its body runs now
_VALUES = re.compile(_kw("values") + r"\s*\(")
_BRACKET_X = re.compile(r"\[x+\]")


def _quote_start(text: str, s: int) -> int:
    """The start of the literal at s with its standalone N prefix (N'...'); for a q-quote (s at its delimiter), the
    q (or nq)."""
    if text[s] != "'":
        s -= 2
        return s - 1 if s > 0 and text[s - 1] in "nN" else s
    if s > 0 and text[s - 1] in "Nn" and (s < 2 or not _IDCH.match(text, s - 2)):
        return s - 1
    return s


def _unbracket(doc: _Doc, a: int, b: int) -> str:
    """masked[a:b] with each [bracketed] name's text put back (EXEC [dbo].[sp_executesql] is an executor)."""
    seg = doc.masked[a:b]
    if "[" not in seg:
        return seg
    return _BRACKET_X.sub(lambda m: doc.text[a + m.start():a + m.end()], seg)


def _values_spans(lk: str, clock: _Clock) -> tuple:
    """(starts, ends) of every VALUES (...)[, (...)]* row list in the lowercased mask (its literals are data); each
    list is walked once, a match inside one already walked is skipped."""
    starts, ends, pos, n = [], [], 0, len(lk)
    for m in _VALUES.finditer(lk):
        clock.tick()
        if m.start() < pos:
            continue
        k, end = m.end() - 1, n
        while True:
            close = _matching_paren(lk, k, n, clock)
            if close < 0:
                break
            j = _WS_RUN.match(lk, close + 1).end()
            if j < n and lk[j] == ",":
                j = _WS_RUN.match(lk, j + 1).end()
                if j < n and lk[j] == "(":
                    k = j
                    continue
            end = close + 1
            break
        starts.append(m.start())
        ends.append(end)
        pos = end
    return starts, ends


# ---------------------------------------------------------------------------------------------------- parse
_DECL_KEYS = ("label", "kind", "name", "qualified", "line", "columns", "references", "params", "on", "reads",
              "writes", "runs", "package")
_FACT_LISTS = ("columns", "references", "params", "reads", "writes", "runs")


def _new_decl(label, kind, parts, line, pos, verb) -> dict:
    return {"label": label, "kind": kind, "name": _obj(parts), "qualified": _qual(parts), "line": line,
            "columns": [], "references": [], "params": [], "on": None, "reads": [], "writes": [], "runs": [],
            "package": None, "_pos": pos, "_verb": verb, "_stub": False, "_nend": None}


def _dialect(text: str, clock: _Clock, force):
    """(mysql, tsql, GO line starts, (mode, mask) when a hint check made a mask the reading can reuse). GO lines decide
    T-SQL first (no other dialect writes one); the MySQL hints and the [a].[b] hint are read on a mask (code only)."""
    if force:
        tsql = force == "tsql"
        return force == "mysql", tsql, ([m.start() for m in _GO_LINE.finditer(text)] if tsql else []), None
    go_lines = [m.start() for m in _GO_LINE.finditer(text)]
    if go_lines:
        return False, True, go_lines, None
    mysql, pre = _mysqlish(text, clock)
    if mysql:
        return True, False, [], ("mysql", pre)
    if _BRACKET_NAME.search(text):
        pre = _mask(text, "ansi", [], clock)
        return False, bool(_BRACKET_NAME.search(pre[0])), [], ("ansi", pre)
    return False, False, [], None


def _parse(text: str, clock: _Clock, depth: int, force) -> dict:
    """parse() on one document (depth 0: a file; depth 1: a nested batch, whose literals are counted, never read;
    `force`: the dialect a nested batch inherits). Declarations keep their private _pos / _verb / _stub / _nend for the
    merge."""
    n = len(text)
    mysql, tsql, go_lines, pre = _dialect(text, clock, force)
    go_starts = go_lines if tsql else []
    mode = "mysql" if mysql else "tsql" if tsql else "ansi"
    masked, dollars, literals = pre[1] if pre and pre[0] == mode else _mask(text, mode, go_starts, clock)
    delims = []                                         # MySQL DELIMITER lines in code: END$$ ... DELIMITER ; ends one
    if mysql:                                           # ...and the lines are blanked (kept until the hint check)
        delims = [m.start() for m in _DELIM_LINE.finditer(masked)]
        masked = _DELIM_LINE.sub(lambda mm: " " * len(mm.group(0)), masked)
    slash = [] if mysql or tsql else [m.start() for m in _SLASH_LINE.finditer(masked)]
    oracle = force == "oracle" if force else bool(slash)
    dialect = "mysql" if mysql else "tsql" if tsql else "oracle" if oracle else "ansi"
    body, body_literals = masked, []
    if dollars:                                         # dollar bodies re-masked as code: their tables are seen
        pieces, last = [], 0
        for _t, s, e in dollars:
            inner, _d, lits = _mask(text[s:e], "ansi", [], clock)
            pieces.append(masked[last:s])
            pieces.append(inner)
            last = e
            body_literals.extend((a + s, b + s, c) for a, b, c in lits)
        pieces.append(masked[last:])
        body = "".join(pieces)
    doc = _Doc(text, masked, body, mysql)

    accepted = []
    found = list(_DECL.finditer(doc.lmask))
    for fi, m in enumerate(found):
        clock.tick()
        verb, kind = _norm(m.group("verb")), _norm(m.group("kind"))
        if verb == "ALTER" and kind not in _ALTER_DEFINES:
            continue                                    # ALTER TABLE / TYPE ...: never a declaration
        mods = m.group("mods").upper().split()
        if "TEMP" in mods or "TEMPORARY" in mods:
            continue
        parts, nend = _name(doc, m.end(), None, masked)
        if not _usable(parts) or _privilege(masked, m.start(), go_starts):
            continue
        if kind == "VIEW" and "MATERIALIZED" in mods and _obj(parts).lower() == "log" and len(parts) == 1 \
                and _MV_LOG.match(doc.lmask, nend):
            continue                                    # Oracle CREATE MATERIALIZED VIEW LOG ON t
        if verb == "ALTER":                             # the window ends at the next declaration or GO line
            lim = min(n, nend + 4000, found[fi + 1].start() if fi + 1 < len(found) else n, _after(go_starts, nend, n))
            st = _ALTER_STOP.search(doc.lmask, nend, lim)
            if not st or st.group(0) != "as":
                continue                                # pg_dump ALTER FUNCTION f() OWNER TO x, ALTER ... COMPILE
        accepted.append((m.start(), verb, kind, parts, nend, mods))

    starts = [a[0] for a in accepted]
    routine_starts = [a[0] for a in accepted if _KIND[a[2]][1] in _ROUTINES]
    cands, spans = [], []
    for idx, (s, verb, kind, parts, nend, mods) in enumerate(accepted):
        clock.tick()
        label, what = _KIND[kind]
        if what == "view" and "MATERIALIZED" in mods:
            what = "materialized view"
        nxt_decl = starts[idx + 1] if idx + 1 < len(starts) else n
        info = _new_decl(label, what, parts, doc.line(s), s, verb)
        info["_nend"] = nend
        if mysql and what == "table" and _PH_BEFORE.search(text, max(0, s - 24), s):
            info["_ph"] = True                          # mysqldump 5.7: dropped when a view of its name follows
        if what in ("table", "type"):
            k = _WS_RUN.match(masked, nend, n).end() if nend < n else nend
            if kind == "TYPE":
                am = _AS_TABLE.match(masked, k, min(n, k + 64))
                if am:
                    info["kind"] = what = "table type"
                    k = _WS_RUN.match(masked, am.end(), n).end()
            tend = nend
            if what != "type" and k < n and masked[k] == "(":
                close = _matching_paren(masked, k, nxt_decl, clock)
                if close > 0:
                    tend = close + 1
                    info["columns"], info["references"] = _columns(doc, k + 1, close, clock)
            spans.append((s, tend))
            cands.append(info)
            continue
        if tsql:
            end = min(_after(go_starts, s, n), _after(routine_starts, s, n))
        elif what == "package" or (oracle and what in ("procedure", "function", "trigger")):
            end = min(_after(slash, s, n), nxt_decl)
        else:
            end = _semicolon_end(doc.lmask, nend, min(nxt_decl, _after(delims, s, n)), clock)
        spans.append((s, end))
        if what in ("procedure", "function"):
            info["params"] = _params(doc, nend, end, tsql, clock)
        if what == "trigger":
            om = _ON.search(masked, nend, end)
            if om:
                tp, _e = _name(doc, om.end(), end, masked)
                info["on"] = _obj(tp) or None
        if what == "package":
            mem = _members(doc, nend, end, clock)
            is_body = kind == "PACKAGE BODY"
            info["_spec_m"] = None if is_body else mem
            info["_body_m"] = mem if is_body else None
        else:
            info.update(_facts(doc, nend, end, clock))
        if tsql:
            am = _AS_WORD.search(masked, nend, min(end, nend + 4000))
            info["_stub"] = bool(am and _STUB_BODY.match(masked, am.start(), end))
        elif mysql and what == "view":                  # mysqldump's placeholder: AS SELECT 1 AS a, 1 AS b
            am = _AS_WORD.search(masked, nend, min(end, nend + 4000))
            if am and _MYSQL_STUB.match(masked, am.start(), end):
                info["_stub"] = "any"
        cands.append(info)

    dynamic, nested_alters, nested_writes = 0, [], []
    if depth == 0:                                      # a DO block runs its body now: a nested batch
        for ts, a, b in dollars:
            if _DO_BEFORE.search(masked, max(0, ts - 64), ts):
                sub = _parse(text[a:b], clock, 1, "ansi")
                off = doc.line(a) - 1
                for d in sub["decls"]:                  # its literals: counted below with every dollar body's
                    d["line"] += off
                    d["_pos"] += a
                    cands.append(d)
                nested_alters.extend(sub["alters"])
                nested_writes.extend(sub["writes"])
    vspans = None                                       # VALUES row lists, built once when a literal needs them

    def data(at: int, before: str) -> bool:             # a message, a VALUES row or an @value = argument
        nonlocal vspans
        if _MSG_BEFORE.search(before):
            return True
        if vspans is None:
            vspans = _values_spans(doc.lbody, clock)
        j = bisect.bisect_right(vspans[0], at) - 1
        return j >= 0 and at < vspans[1][j]

    if not mysql:
        executed = None                                 # variable -> sorted positions where it is executed (once)
        sub_dialect = dialect if dialect in ("tsql", "oracle") else None
        for s, e, closed in literals:
            clock.tick()
            if not closed or e - s < 10 or not _CREATE_HEAD.match(text, _head(text, s + 1, e - 1), e - 1):
                continue
            q = _quote_start(text, s)
            before = _unbracket(doc, max(0, q - 96), q)
            run = depth == 0 and _EXEC_BEFORE.search(before) is not None
            if depth == 0 and not run:
                am = _ASSIGN_BEFORE.search(before)
                var = next((g for g in am.groups() if g), None) if am else None
                if var:
                    if executed is None:
                        executed = {}
                        for xm in _EXECUTED_VAR.finditer(_unbracket(doc, 0, n)):
                            executed.setdefault((xm.group(1) or xm.group(2)).lower(), []).append(xm.start())
                    pos = executed.get(var.lower(), ())
                    j = bisect.bisect_right(pos, e)
                    run = j < len(pos) and pos[j] < _after(go_starts, s, n)
            if not run:                                 # not executed, or inside a nested batch: counted, not read
                if not data(s, before) and _declaring(text, s + 1, e - 1):
                    dynamic += 1
                continue
            content = text[s + 1:e - 1]
            if text[s] == "'":
                content = content.replace("''", "'")
            sub = _parse(content, clock, 1, sub_dialect)
            kept = sub["decls"]
            if _CONCAT_AFTER.match(masked, e):          # '... dbo.Archive_' + @y: a name cut at the literal's end
                kept = [d for d in kept if d["_nend"] is None or content[d["_nend"]:].strip()]
                dynamic += len(sub["decls"]) - len(kept)
            if not sub["decls"] and _declaring(text, s + 1, e - 1):
                dynamic += 1                            # run, but built by concatenation: read up to the first one
            dynamic += sub["dynamic_unread"]
            off = doc.line(s + 1) - 1
            for d in kept:
                d["line"] += off
                d["_pos"] += s + 1
                cands.append(d)
            nested_alters.extend(sub["alters"])
            nested_writes.extend(sub["writes"])
        for s, e, closed in body_literals if depth == 0 else ():   # EXECUTE '...' in a plpgsql body: counted
            clock.tick()
            if closed and e - s >= 10 and _declaring(text, s + 1, e - 1):
                q = _quote_start(text, s)
                if not data(s, body[max(0, q - 96):q]):
                    dynamic += 1

    seen: dict = {}
    decls = []
    for c in sorted(cands, key=lambda d: d["_pos"]):
        clock.tick()
        key = (c["label"], c["kind"], c["qualified"].lower())
        prev = seen.get(key)
        if prev is None:
            seen[key] = c
            decls.append(c)
        elif c["kind"] == "package":                    # spec and body: one node, members from either
            for k in ("_spec_m", "_body_m"):
                if prev.get(k) is None and c.get(k) is not None:
                    prev[k] = c[k]
        elif prev["_stub"] and not c["_stub"] and (prev["_stub"] == "any" or c["_verb"] != "CREATE"):
            prev.update({k: v for k, v in c.items() if k != "label"})
    if mysql:                                           # mysqldump 5.7's placeholder table of a view
        views = {d["qualified"].lower() for d in decls if d["kind"] == "view"}
        decls = [d for d in decls if not (d.get("_ph") and d["qualified"].lower() in views)]

    members = []
    for p in [d for d in decls if d["kind"] == "package"]:
        spec, bodym = p.pop("_spec_m", None), p.pop("_body_m", None)
        at = {}
        for i, h in enumerate(bodym or ()):
            at.setdefault((h[0], h[1].lower()), i)
        for mk, mname, hs, he, nxt in spec or bodym or ():
            clock.tick()
            info = _new_decl("Function", mk, [p["qualified"], mname], doc.line(hs), hs, "CREATE")
            info["package"] = p["name"]
            key = ("Function", mk, info["qualified"].lower())
            if key in seen:
                continue
            bi = at.get((mk, mname.lower()))
            if bi is not None:
                _k, _n, _bs, bhe, bnxt = bodym[bi]
                info["params"] = _params(doc, bhe, bnxt, False, clock)
                info.update(_facts(doc, bhe, bnxt, clock))
            else:
                info["params"] = _params(doc, he, nxt, False, clock)
            seen[key] = info
            members.append(info)
    decls.extend(members)

    tables, by_name = {}, {}
    for d in decls:
        if d["kind"] == "table":
            tables.setdefault(d["qualified"].lower(), d)
            by_name.setdefault(d["name"].lower(), []).append(d)
    alters = []
    for m in _ALTER_TABLE.finditer(doc.lmask):
        clock.tick()
        parts, nend = _name(doc, m.end(), None, masked)
        if not _usable(parts):
            continue
        alters.append(_obj(parts))
        st = _ALTER_TABLE_END.search(doc.lmask, nend)
        end = min(st.start() if st else n, _after(go_starts, nend, n))
        tgt = tables.get(_qual(parts).lower())
        if tgt is None:
            same = by_name.get(_obj(parts).lower(), ())
            tgt = same[0] if len(same) == 1 else None
        if tgt is not None:
            for r in _REF.finditer(masked, nend, end):
                rp, _e = _name(doc, r.end(), end, masked)
                if _usable(rp):
                    tgt["references"].append(_obj(rp))

    top_writes, last = [], 0                            # module writes: DML in the gaps between declarations
    for s, e in sorted(spans) + [(n, n)]:
        clock.tick()                                    # per gap too: a gap without DML has no match to tick on
        if s > last:
            top_writes.extend(_facts(doc, last, s, clock, runs=False)["writes"])
        last = max(last, e)

    for d in decls:
        clock.tick()
        for k in _FACT_LISTS:
            d[k] = _uniq(d[k])
    decls.sort(key=lambda d: (d["line"], d["_pos"]))
    return {"dialect": dialect, "decls": decls, "alters": _uniq(alters + nested_alters),
            "writes": _uniq(top_writes + nested_writes), "dynamic_unread": dynamic}


def parse(text: str, *, deadline_s: float | None = None) -> dict:
    """SqlFacts of one script: {"dialect", "decls": [{"label", "kind", "name", "qualified", "line", "columns",
    "references", "params", "on", "reads", "writes", "runs", "package"}], "alters", "writes", "dynamic_unread"}, in line
    order. Raises Slow only (past `deadline_s`)."""
    r = _parse(text, _Clock(deadline_s), 0, None)
    return {"dialect": r["dialect"], "decls": [{k: d[k] for k in _DECL_KEYS} for d in r["decls"]],
            "alters": r["alters"], "writes": r["writes"], "dynamic_unread": r["dynamic_unread"]}


# ---------------------------------------------------------------------------------------------------- summaries
_KEY_KIND = {"materialized view": "materializedview", "table type": "tabletype"}
_PLAIN_SCHEMAS = {"dbo", "public", "main"}


def _h(name: str) -> str:
    return summarize.humanize(name) or name


def _phrase(items, cap: int) -> str:
    seen, out = set(), []
    for x in items:
        h = _h(x)
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
            if len(out) >= cap:
                break
    return ", ".join(out)


def _join(head: str, clauses, tail: str = "") -> str:
    text = "; ".join([head] + [f"{word} {what}" for word, what in clauses if what] + ([tail] if tail else []))
    return text[:summarize.SUMMARY_MAX].rstrip()


def _schema(d: dict) -> str:
    q, nm = d["qualified"], d["name"]
    prefix = q[:-len(nm)].rstrip(".") if nm and q.endswith(nm) else ""
    return prefix.rsplit(".", 1)[-1] if prefix else ""


def _decl_summary(d: dict, members: list) -> str:
    h, kind = _h(d["name"]), d["kind"]
    if kind == "table":
        sch = _schema(d)
        head = f"{h}: table" + (f" in {sch}" if sch and sch.lower() not in _PLAIN_SCHEMAS else "")
        return _join(head, [("columns", _phrase(d["columns"], 12)), ("references", _phrase(d["references"], 6))])
    if kind in ("view", "materialized view"):
        return _join(f"{h}: {kind}", [("reads", _phrase(d["reads"], 6))])
    if kind == "table type":
        return _join(f"{h}: table type", [("columns", _phrase(d["columns"], 12))])
    if kind == "package":
        return _join(f"{h}: package", [("members", _phrase(members, 12))])
    if kind == "type":
        return _join(f"{h}: type", [])
    if kind == "trigger":
        head = f"{h}: trigger" + (f" on {_h(d['on'])}" if d.get("on") else "")
    else:
        params = _phrase([p.lstrip("@") for p in d["params"]], 6)
        head = f"{h}: {kind}" + (f" in package {_h(d['package'])}" if d.get("package") else "") \
            + (f" ({params})" if params else "")
    return _join(head, [("reads", _phrase(d["reads"], 6)), ("writes", _phrase(d["writes"], 6)),
                        ("runs", _phrase(d["runs"], 6))])


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}".rstrip("0").rstrip(".") + " MB"
    if n >= 1024:
        return f"{n / 1024:.1f}".rstrip("0").rstrip(".") + " KB"
    return f"{n} bytes"


def _why_text(why, size: int) -> str:
    if why == "too_large":
        return f"{_size(size)}, over the {_size(MAX_BYTES)} limit"
    if why == "budget":
        return f"the per-sync SQL read budget ({_size(TOTAL_BYTES)}) was used up"
    if why == "slow":
        return f"reading it took over {DEADLINE_S:g} s"
    return str(why) if why else ""


def file_nodes(rel, facts: dict | None, *, size: int, why: str | None = None) -> list:
    """The node dicts of one file: its :Module first, then one per declaration in line order. `facts` None: the
    :Module only, "not read: ..." (`why`: "too_large" | "budget" | "slow", or a text). `rel` is root-relative."""
    rel = str(rel).replace("\\", "/")
    stem = PurePosixPath(rel).stem or rel

    def node(key, label, name, line, summary):
        return {"key": key, "label": label, "name": name, "source_file": rel, "start_line": line, "documented": False,
                "body_summary": summary, "call_by_name": False}

    if facts is None:
        text = _why_text(why, size)
        summary = f"{_h(stem)}: SQL script" + (f" (not read: {text})" if text else "")
        return [node(f"sql:{rel}", "Module", stem, 1, summary[:summarize.SUMMARY_MAX].rstrip())]
    decls = facts["decls"]
    members: dict = {}
    for d in decls:
        if d.get("package"):
            members.setdefault(d["qualified"].rsplit(".", 1)[0].lower(), []).append(d["name"])
    clauses = [("creates", _phrase([d["name"] for d in decls if not d.get("package")], 6)),
               ("alters", _phrase(facts["alters"], 6)), ("writes", _phrase(facts["writes"], 6))]
    n = facts["dynamic_unread"]
    tail = f"{n} object(s) created by dynamic SQL not read" if n else ""
    out = [node(f"sql:{rel}", "Module", stem, 1, _join(f"{_h(stem)}: SQL script", clauses, tail))]
    keys = {out[0]["key"]}
    for d in decls:
        key = f"sql:{rel}#{_KEY_KIND.get(d['kind'], d['kind'])}:{d['qualified'].lower()}"
        if key in keys:
            continue
        keys.add(key)
        out.append(node(key, d["label"], d["name"], d["line"],
                        _decl_summary(d, members.get(d["qualified"].lower(), []))))
    return out


# ---------------------------------------------------------------------------------------------------- files
class Budget:
    """The per-sync read budget in bytes (deterministic: which files are read never depends on machine load). The
    caller offers every file in sorted rel order; the first that does not fit, and every later one, is refused."""

    def __init__(self, total: int | None = None):
        self.total = TOTAL_BYTES if total is None else int(total)
        self.used = 0
        self.spent = False

    def take(self, size: int) -> bool:
        if self.spent or self.used + size > self.total:
            self.spent = True
            return False
        self.used += size
        return True


_CACHE_NAME = re.compile(r"[0-9a-f]{32}-r\d+\.json\Z")


def _valid_entry(entry) -> bool:
    """A cache entry has exactly SqlFacts' shape (anything else: a fresh parse)."""
    try:
        f = entry["facts"]
        if not isinstance(entry["encoding"], str) or set(f) != {"dialect", "decls", "alters", "writes",
                                                                "dynamic_unread"}:
            return False
        if not isinstance(f["dialect"], str) or type(f["dynamic_unread"]) is not int:
            return False
        if not all(isinstance(x, str) for x in f["alters"] + f["writes"]):
            return False
        for d in f["decls"]:
            if set(d) != set(_DECL_KEYS) or d["label"] not in ("Class", "Function") or type(d["line"]) is not int:
                return False
            if not all(isinstance(d[k], str) for k in ("kind", "name", "qualified")):
                return False
            if not all(isinstance(d[k], list) and all(isinstance(x, str) for x in d[k]) for k in _FACT_LISTS):
                return False
            if not all(d[k] is None or isinstance(d[k], str) for k in ("on", "package")):
                return False
        return True
    except Exception:
        return False


class Cache:
    """The per-file cache: `<directory>/<sha256(bytes)[:32]>-r<READER_REV>.json` holds the encoding and the SqlFacts
    of one content (identical files at two paths share an entry). A read, write or JSON failure is a fresh parse, never
    an error; only read results are written. prune() deletes, one exact path at a time, the entries this run did not
    use (never a wildcard; a file that is not an entry is never touched)."""

    def __init__(self, directory):
        self.dir = os.fspath(directory)
        self.used: set = set()
        self.hits = 0

    def _name(self, digest: str) -> str:
        return f"{digest}-r{READER_REV}.json"

    def get(self, digest: str):
        name = self._name(digest)
        self.used.add(name)
        try:
            with open(_os_path(os.path.join(self.dir, name)), encoding="utf-8") as fh:
                entry = json.load(fh)
        except Exception:                               # OSError, ValueError, RecursionError (deep nesting): a miss
            return None
        if not _valid_entry(entry):
            return None
        self.hits += 1
        return entry["encoding"], entry["facts"]

    def put(self, digest: str, encoding: str, facts: dict) -> None:
        name = self._name(digest)
        self.used.add(name)
        path = _os_path(os.path.join(self.dir, name))
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            os.makedirs(_os_path(self.dir), exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"encoding": encoding, "facts": facts}, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def prune(self) -> int:
        try:
            names = os.listdir(_os_path(self.dir))
        except OSError:
            return 0
        removed = 0
        for name in sorted(names):
            if _CACHE_NAME.match(name) and name not in self.used:
                try:
                    os.remove(_os_path(os.path.join(self.dir, name)))
                    removed += 1
                except OSError:
                    pass
        return removed


def _os_path(path) -> str:
    """On Windows a path of 248+ characters is opened through its extended-length form (\\\\?\\C:\\... or
    \\\\?\\UNC\\server\\...), which the Win32 API reads at any length; every other path is returned as it is."""
    p = os.fspath(path)
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if len(p) < 248 or p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


def read_file(path, rel, *, cache: Cache | None = None, budget: Budget | None = None):
    """(nodes, info) of one .sql file. info: {"state": "read" | "too_large" | "budget" | "slow", "declarations",
    "encoding", "dialect", "dynamic_unread", "cache_hit"}. Raises OSError only (the caller counts it)."""
    p = _os_path(path)
    size = os.stat(p).st_size
    info = {"state": "read", "declarations": 0, "encoding": None, "dialect": None, "dynamic_unread": 0,
            "cache_hit": False}
    if size > MAX_BYTES:
        return file_nodes(rel, None, size=size, why="too_large"), dict(info, state="too_large")
    if budget is not None and not budget.take(size):
        return file_nodes(rel, None, size=size, why="budget"), dict(info, state="budget")
    with open(p, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:                           # it grew after the stat
        return file_nodes(rel, None, size=len(data), why="too_large"), dict(info, state="too_large")
    digest = hashlib.sha256(data).hexdigest()[:32]
    got = cache.get(digest) if cache is not None else None
    if got is not None:
        encoding, facts = got
    else:
        text, encoding = decode(data)
        t0 = time.monotonic()
        try:
            facts = parse(text, deadline_s=DEADLINE_S)
            if time.monotonic() - t0 > DEADLINE_S:      # past it between two clock reads: slow all the same
                raise Slow()
        except Slow:
            return file_nodes(rel, None, size=size, why="slow"), dict(info, state="slow", encoding=encoding)
        if cache is not None:
            cache.put(digest, encoding, facts)
    info.update(declarations=len(facts["decls"]), encoding=encoding, dialect=facts["dialect"],
                dynamic_unread=facts["dynamic_unread"], cache_hit=got is not None)
    return file_nodes(rel, facts, size=size), info
