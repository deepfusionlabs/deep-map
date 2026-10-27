#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for deepmap_cypher — read-only Cypher over the project graph (MCP tool + `deepmap cypher`).

THE FEATURE: the MCP search server exposed deepmap_search, deepmap_embed and deepmap_impact only, so an agent could not
ask the graph an ad-hoc STRUCTURE question (list / count / group / path). build/cypher_readonly.py adds ONE read-only
Cypher implementation that the new deepmap_cypher MCP tool and the new `deepmap cypher` CLI verb both call.

What is locked here (hermetic: fake drivers, no Neo4j, no network, no ~/.deepmap writes):
  A  the validator's truth table — reads that must PASS (keywords inside strings, comments, backtick names, member
     names like n.created / n.settings, aliases, map keys, start/end path variables, allowlisted CALL / SHOW) and
     writes / admin / IO / procedures / several statements that must be REJECTED, including the shapes Neo4j 5.26
     itself makes dangerous: it DECODES \\uXXXX escapes before it lexes (measured live: `'x\\u0027 AS y //'` closes
     the string at the escape), so an ASCII-decoding escape is refused outright, and a non-ASCII one is validated in
     its decoded form too; it reads the 4 characters after \\u with Java's Integer.parseInt (measured: \\u+041,
     full-width and Arabic-Indic digits decode), so a \\u not followed by 4 ASCII hex digits is refused; Unicode
     case-folding / full-width spellings of SET; Neo4j ends a // comment only at \\n or \\r (measured: VT, FF, FS-US,
     NEL, U+2028 and U+2029 do not end it), so those characters are refused outside a string — the validator and
     Neo4j can't disagree on where a later string starts; a variable, parameter or label named `as` does not exempt
     the word after it (an alias is exempt only when it ends right after the word: the end, , ) } ] ; or a clause /
     ORDER BY word); a keyword glued on by a non-ASCII whitespace escape (\\u3000, \\u00a0) is caught in the
     DECODED text;
  B  params / limit validation;
  C  result shaping: nodes {labels, key, name, source_file, start_line, ...} with NO embedding / _hash properties,
     relationships {type, confidence, attrs, start, end}, paths in order with directions, float lists of 256+
     numbers left out (and the note says a slice is not), long strings cut, a column named `note` renamed so a
     {"note": ...} entry is always DeepMap's own;
  D  the caps: at most limit+1 records READ from the result, the byte cap — measured on the text the client
     receives (FastMCP sends each row as JSON with indent=2: measured live, compact-JSON budgeting let an "about
     64 KB" answer reach 79 KB of whole nodes, 109 KB of paths), also end to end through the registered tool — the
     first-row-too-big case, notes;
  E  the transaction: a READ access-mode session, a server-side timeout, ALWAYS rolled back and never committed
     (even when the run raises), no autocommit / execute_write path, nothing opened for a rejected query;
  F  the MCP tool: registered through mcp_bounds.bounded_tool (async, project guard, auth back-off, project label —
     AST + behaviour), validates BEFORE it asks for the driver (a recorder seam), answers rows + trailing notes,
     stamps last_used, keeps Neo4j's timeout inside the tool deadline, a slow query fails at the deadline without
     blocking the loop, a rejected login backs off, a statement error reads as a sentence; its description names
     every relationship type in framework_edges.ALLOWED_EDGE_TYPES (+ CALLS), the confidence grades, labels and
     properties, its example queries pass the validator, and it fits the client's 2,048-char budget with a
     realistic project path WITH the "different project" remedy;
  G  the CLI: `deepmap cypher` parses, rejects (exit 2) without calling _driver(), runs through _driver() +
     _read_retry("cypher"), prints rows + notes (and --json), clamps --limit with a note, never opens a raw
     GraphDatabase driver;
  H  SKILL.md teaches when to use deepmap_cypher and that AMBIGUOUS links are unverified; the HTTP smoke test can
     call the tool.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
SKILL = REPO / "skills" / "deep-map" / "SKILL.md"
SMOKE = REPO / "tests" / "mcp_http_smoketest.py"

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


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    so, se = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = so, se


_ENV_KEYS = ("DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY", "DEEPMAP_PROJECT_GUARD", "NEO4J_URI",
             "DEEPMAP_MCP_WARMUP_TIMEOUT_S", "DEEPMAP_MCP_TOOL_TIMEOUT_S", "DEEPMAP_CYPHER_TIMEOUT_S",
             "DEEPMAP_MCP_AUTH_BACKOFF_S", "DEEPMAP_MCP_AUTH_BACKOFF_MAX_S", "DEEPMAP_RETRIEVAL_LOG",
             "DEEPMAP_APPLY_LEARNED", "DEEPMAP_LEARNING")


# ---- fake graph objects shaped like the neo4j driver's (Node / Relationship / Path) ----------------------------------
class FNode(dict):
    def __init__(self, eid, labels, props):
        super().__init__(props)
        self.element_id = eid
        self.labels = frozenset(labels)


class FRel(dict):
    def __init__(self, eid, type_, start, end, props):
        super().__init__(props)
        self.element_id = eid
        self.type = type_
        self.start_node = start
        self.end_node = end


class FPath:
    def __init__(self, nodes, rels):
        self.nodes = tuple(nodes)
        self.relationships = tuple(rels)
        self.start_node = self.nodes[0]
        self.end_node = self.nodes[-1]


VEC = [0.01 * i for i in range(768)]


def _wire_bytes(rows) -> int:
    """UTF-8 bytes of the text FastMCP sends for `rows` (one text block per row): measured with FastMCP's own converter
    (mcp.server.fastmcp.utilities.func_metadata._convert_to_content -> pydantic_core.to_json(row, indent=2)) when the
    mcp SDK has it, else json.dumps(row, ensure_ascii=False, indent=2) — the same text."""
    try:
        from mcp.server.fastmcp.utilities.func_metadata import _convert_to_content
        return sum(len(c.text.encode("utf-8")) for c in _convert_to_content(list(rows)))
    except Exception:  # noqa: BLE001 — no SDK (or a later one without the helper): the documented equivalent
        import json
        return sum(len(json.dumps(r, ensure_ascii=False, indent=2, default=str).encode("utf-8")) for r in rows)


try:
    from mcp.server.fastmcp.utilities.func_metadata import _convert_to_content as _probe  # noqa: F401
    _WIRE_HOW = "FastMCP's own converter"
except Exception:  # noqa: BLE001
    _WIRE_HOW = "JSON with indent=2"


def _fn(eid="4:a:1", name="save", key="app/order.py::save:10", **extra):
    props = {"key": key, "name": name, "source_file": "app/order.py", "start_line": 10, "confidence": "EXTRACTED",
             "body_summary": "persist the order", "embedding": VEC, "_embed_hash": "abc", "_content_hash": "abc"}
    props.update(extra)
    return FNode(eid, ["Function"], props)


# ---- a fake driver that records what the code asked of it ------------------------------------------------------------
class _Counting:
    def __init__(self, records, owner):
        self.records, self.owner = records, owner

    def __iter__(self):
        for r in self.records:
            self.owner.consumed += 1
            yield r


class FakeDriver:
    def __init__(self, records=(), raise_on_run=None, delay=0.0):
        self.records, self.raise_on_run, self.delay = list(records), raise_on_run, delay
        self.sessions, self.tx_kw, self.ran = [], [], []
        self.rolled_back = self.committed = self.autocommit = self.writes = self.consumed = 0
        self.closed = self.verified = 0

    def session(self, **kw):
        self.sessions.append(dict(kw))
        return _FakeSession(self)

    def verify_connectivity(self):
        self.verified += 1

    def close(self):
        self.closed += 1


class _FakeSession:
    def __init__(self, owner):
        self.o = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin_transaction(self, timeout=None, metadata=None, **kw):
        self.o.tx_kw.append({"timeout": timeout, "metadata": metadata, **kw})
        return _FakeTx(self.o)

    def run(self, *a, **k):                    # autocommit: must never be used
        self.o.autocommit += 1
        return []

    def execute_write(self, *a, **k):
        self.o.writes += 1

    def execute_read(self, *a, **k):          # a managed read COMMITS — the tool must not use it either
        self.o.autocommit += 1


class _FakeTx:
    def __init__(self, owner):
        self.o = owner

    def run(self, q, params=None):
        self.o.ran.append((q, dict(params or {})))
        if self.o.delay:
            time.sleep(self.o.delay)
        if self.o.raise_on_run is not None:
            raise self.o.raise_on_run
        return _Counting(self.o.records, self.o)

    def rollback(self):
        self.o.rolled_back += 1

    def commit(self):
        self.o.committed += 1

    def close(self):
        pass


# =====================================================================================================================
ACCEPT = [
    ("a plain listing", "MATCH (n:Function) RETURN n.name LIMIT 5"),
    ("member names that contain keywords (n.created, n.settings, n.deleted_at, n.loaded, n.use_count)",
     "MATCH (n) WHERE n.created > 1 AND n.settings IS NOT NULL RETURN n.deleted_at, n.loaded, n.use_count"),
    ("a keyword inside a single-quoted string", "MATCH (n) WHERE n.name = 'DELETE all; DROP INDEX x' RETURN n"),
    ("a keyword inside a double-quoted string", 'MATCH (n) WHERE n.body_summary CONTAINS "CREATE (x)" RETURN n'),
    ("an escaped quote inside a string", "MATCH (n) WHERE n.name = 'it\\'s; DELETE' RETURN n"),
    ("a keyword inside a // comment", "MATCH (n) RETURN n // then DETACH DELETE n"),
    ("a keyword inside a /* */ comment", "MATCH (n) /* SET n.x = 1 */ RETURN n"),
    ("backtick-quoted names spelled like keywords", "MATCH (`delete`:Function) RETURN `delete`.name AS `create`"),
    ("an alias spelled like a keyword (after AS)", "MATCH (n) RETURN n.name AS delete"),
    ("a map key spelled like a keyword", "MATCH (n) RETURN n {.name, create: 1, set: 2} AS m"),
    ("a projected property spelled like a keyword", "MATCH (n) RETURN n {.set, .delete} AS m"),
    ("labels / types spelled like keywords", "MATCH (n:Create)-[:DELETE]->(m:Merge) RETURN n"),
    ("a $parameter spelled like a keyword", "MATCH (n) WHERE n.name = $set RETURN n"),
    ("start / end path variables", "MATCH p=(start:Function)-[:CALLS*1..3]->(end) RETURN start.name, end.name, p"),
    ("a variable named stop", "MATCH (stop:Function) RETURN stop.name"),
    ("a relationship variable named call", "MATCH (a)-[call:CALLS]->(b) RETURN call.confidence, call AS c"),
    ("a variable named show", "MATCH (show:Class) RETURN show.name, show"),
    ("identifiers that merely start with a keyword", "MATCH (setting:Class), (creator) RETURN setting.name"),
    ("STARTS WITH", "MATCH (n) WHERE n.name STARTS WITH 'set' RETURN n"),
    ("a regex mentioning load csv", "MATCH (n:Function) WHERE n.name =~ '(?i).*load csv.*' RETURN n"),
    ("CALL db.index.vector.queryNodes",
     "CALL db.index.vector.queryNodes('deepmap_fn_embeddings', 5, $v) YIELD node, score RETURN node.name, score"),
    ("CALL db.index.vector.queryNodes from a node's own vector",
     "MATCH (m:Function {name: 'save'}) CALL db.index.vector.queryNodes('deepmap_fn_embeddings', 10, m.embedding) "
     "YIELD node, score RETURN node.name, score"),
    ("CALL db.index.fulltext.queryNodes",
     "CALL db.index.fulltext.queryNodes('deepmap_code_fts', 'retry') YIELD node RETURN node.name"),
    ("CALL db.labels()", "CALL db.labels()"),
    ("CALL db.labels (no parentheses)", "CALL db.labels"),
    ("CALL db.relationshipTypes", "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"),
    ("CALL db.schema.visualization", "CALL db.schema.visualization()"),
    ("CALL dbms.components (the one dbms.* listing)", "CALL dbms.components() YIELD versions RETURN versions"),
    ("CALL `db`.`labels` (backtick parts)", "CALL `db`.`labels`() YIELD label RETURN label"),
    ("a CALL { } read subquery", "CALL { MATCH (n:Function) RETURN n LIMIT 1 } RETURN n.name"),
    ("a scoped CALL (n) { } subquery", "MATCH (n:Function) CALL (n) { RETURN n.name AS nm } RETURN nm"),
    ("a COUNT { } subquery", "MATCH (n) RETURN COUNT { (n)-[:CALLS]->() } AS out"),
    ("SHOW INDEXES", "SHOW INDEXES"),
    ("SHOW VECTOR INDEXES YIELD", "SHOW VECTOR INDEXES YIELD name, state RETURN name, state"),
    ("SHOW CONSTRAINTS", "SHOW CONSTRAINTS"),
    ("SHOW PROCEDURES", "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'db.' RETURN name"),
    ("SHOW FUNCTIONS", "SHOW FUNCTIONS"),
    ("OPTIONAL MATCH", "OPTIONAL MATCH (n:Route) RETURN count(n)"),
    ("UNWIND a parameter", "UNWIND $names AS nm MATCH (f:Function {name: nm}) RETURN f.key"),
    ("WITH ... RETURN", "WITH 1 AS x RETURN x"),
    ("a trailing semicolon", "RETURN 1 AS x;"),
    ("a trailing semicolon then a comment", "MATCH (n) RETURN n; // done"),
    ("a ';' inside a string", "MATCH (n) WHERE n.name = 'a;b' RETURN n"),
    ("ORDER BY / SKIP / LIMIT", "MATCH (n:Function) RETURN n.name ORDER BY n.name SKIP 5 LIMIT 5"),
    ("a confidence filter", "MATCH (a)-[r:CALLS]->(b) WHERE r.confidence <> 'AMBIGUOUS' RETURN a.name, b.name"),
    ("a USING INDEX planner hint", "MATCH (f:Function) USING INDEX f:Function(key) WHERE f.key = $k RETURN f"),
    ("a core namespaced function", "RETURN vector.similarity.cosine($a, $b) AS s, date.truncate('day', date()) AS d"),
    ("UNION of two reads", "MATCH (n:Class) RETURN n.name AS x UNION MATCH (m:Module) RETURN m.name AS x"),
    ("a non-ASCII \\u escape (decodes to e-acute)", "MATCH (n) RETURN n.name AS \\u00e9t\\u00e9"),
    ("lower-case keywords", "match (n:Function) return n.name limit 3"),
    ("a variable named db (db.name is a property)", "MATCH (db:Module) RETURN db.name, db.created"),
    ("an apostrophe in a // comment, then a read on the next line", "MATCH (n) // it's the caller\nRETURN n.name"),
    ("a // comment ended by CRLF", "MATCH (n) // c\r\nRETURN n"),
    ("a // comment ended by a lone CR", "MATCH (n) // c\rRETURN n"),
    ("a tab between clauses", "MATCH (n)\tRETURN n"),
    ("a line separator INSIDE a string", "MATCH (n) WHERE n.name = 'a b' RETURN n"),
    ("a variable named as", "WITH 1 AS as RETURN as"),
    ("a real alias spelled like a keyword after a variable", "UNWIND $xs AS x RETURN x AS set"),
    ("a column named note (shape_rows renames it)", "MATCH (f:Function) RETURN f.body_summary AS note"),
    # The AS exemption holds when the alias ENDS after the word (the end, , ) } ] ; or a clause / ORDER BY word).
    ("a variable named as carried by WITH, then RETURN", "MATCH (as) WITH as RETURN as.name"),
    ("an alias spelled like a keyword before ORDER BY", "MATCH (n) RETURN n.name AS delete ORDER BY n.name"),
    ("aliases spelled like keywords before ',' and LIMIT", "MATCH (n) RETURN n.name AS set, n.key AS remove LIMIT 3"),
    ("an alias spelled like a keyword before '}' and ';'",
     "CALL { MATCH (n) RETURN n.name AS create } RETURN 1 AS merge;"),
    ("an alias spelled like a keyword before UNION", "RETURN 1 AS drop UNION RETURN 2 AS drop"),
    ("an alias spelled like a keyword before WHERE", "MATCH (n) WITH n.name AS detach WHERE true RETURN 1"),
]

# Characters Python (str.splitlines) reads as a line break but Neo4j 5.26 does NOT end a // comment at (measured live:
# `RETURN 1 AS a // c<X>'\n, 2 AS b //'` returns columns a AND b), plus US, which Neo4j treats as whitespace.
_ODD_BREAKS = (("VT", "\x0b"), ("FF", "\x0c"), ("FS", "\x1c"), ("GS", "\x1d"), ("RS", "\x1e"), ("US", "\x1f"),
               ("NEL", "\x85"), ("U+2028", " "), ("U+2029", " "))
_BS = "\\"
_FW = "".join(chr(0xFF10 + int(d)) for d in "0027")          # full-width digits 0027
_AR = "".join(chr(0x0660 + int(d)) for d in "0027")          # Arabic-Indic digits 0027

REJECT = [
    ("CREATE", "CREATE (n:X)", "CREATE"),
    ("MERGE", "MERGE (n:X {k: 1}) RETURN n", "MERGE"),
    ("SET after MATCH", "MATCH (n) SET n.x = 1", "SET"),
    ("DELETE", "MATCH (n) DELETE n", "DELETE"),
    ("DETACH DELETE", "MATCH (n) DETACH DELETE n", "DETACH"),
    ("REMOVE", "MATCH (n) REMOVE n.x", "REMOVE"),
    ("DROP INDEX", "DROP INDEX deepmap_code_fts", "start with"),
    ("CREATE INDEX (DDL)", "CREATE INDEX foo FOR (n:Function) ON (n.name)", "start with"),
    ("CREATE CONSTRAINT (DDL)", "CREATE CONSTRAINT c FOR (n:Function) REQUIRE n.key IS UNIQUE", "start with"),
    ("FOREACH", "MATCH (n) FOREACH (x IN [1] | SET n.y = x)", "FOREACH"),
    ("INSERT (GQL)", "MATCH (n) WITH n LIMIT 1 INSERT (m:X)", "INSERT"),
    ("LOAD CSV from a file", "LOAD CSV FROM 'file:///x.csv' AS l RETURN l", "start with"),
    ("LOAD CSV after a read clause (READ mode would let it run)",
     "WITH 1 AS x LOAD CSV WITH HEADERS FROM 'https://example.com/x.csv' AS l RETURN l", "LOAD"),
    ("LOAD CSV with LOAD hidden as an alias (no alias ends at LOAD: CSV follows)",
     "UNWIND [1] AS LOAD CSV FROM 'https://e.x/a' AS l RETURN l", "LOAD"),
    ("CALL { } IN TRANSACTIONS (a write)", "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS", "DETACH"),
    ("CALL { } IN TRANSACTIONS (even a read)",
     "CALL { MATCH (n) RETURN n } IN TRANSACTIONS OF 10 ROWS RETURN n", "TRANSACTIONS"),
    ("USING PERIODIC COMMIT", "USING PERIODIC COMMIT LOAD CSV FROM 'x' AS l RETURN l", "start with"),
    ("CREATE USER", "CREATE USER bob SET PASSWORD 'secret123'", "start with"),
    ("DROP USER", "DROP USER bob", "start with"),
    ("ALTER USER", "ALTER USER neo4j SET PASSWORD 'x'", "start with"),
    ("GRANT", "GRANT ROLE admin TO bob", "start with"),
    ("DENY", "DENY TRAVERSE ON GRAPH * TO bob", "start with"),
    ("REVOKE", "REVOKE ROLE admin FROM bob", "start with"),
    ("SHOW USERS", "SHOW USERS", "SHOW may only"),
    ("SHOW CURRENT USER", "SHOW CURRENT USER", "SHOW may only"),
    ("SHOW DATABASES", "SHOW DATABASES", "SHOW may only"),
    ("SHOW SETTINGS", "SHOW SETTINGS YIELD name", "SHOW may only"),
    ("SHOW TRANSACTIONS", "SHOW TRANSACTIONS", "SHOW may only"),
    ("SHOW PRIVILEGES", "SHOW USER PRIVILEGES", "SHOW may only"),
    ("TERMINATE TRANSACTIONS", "TERMINATE TRANSACTIONS 'neo4j-transaction-1'", "start with"),
    ("SHOW ... TERMINATE (composite)", "SHOW INDEXES YIELD name TERMINATE TRANSACTIONS 'x'", "TERMINATE"),
    ("START DATABASE", "START DATABASE neo4j", "start with"),
    ("STOP DATABASE mid-query", "MATCH (n) STOP DATABASE neo4j", "STOP DATABASE"),
    ("USE system", "USE system SHOW USERS", "start with"),
    ("USE after UNION", "RETURN 1 AS x UNION USE system RETURN 2 AS x", "USE"),
    ("USE inside a subquery", "CALL { USE system RETURN 1 AS x } RETURN x", "USE"),
    ("EXPLAIN prefix", "EXPLAIN MATCH (n) RETURN n", "start with"),
    ("PROFILE prefix", "PROFILE MATCH (n) RETURN n", "start with"),
    ("CYPHER options prefix", "CYPHER 5 MATCH (n) RETURN n", "start with"),
    ("CALL apoc.*", "CALL apoc.create.node(['X'], {})", "apoc"),
    ("an apoc function", "RETURN apoc.text.join(['a'], ',') AS s", "apoc"),
    ("CALL `apoc`.`create`.`node` (backtick parts)", "CALL `apoc`.`create`.`node`(['X'], {})", "apoc"),
    ("an apoc function as one backtick name", "RETURN `apoc.text.join`(['a'], ',') AS s", "apoc"),
    ("an apoc procedure after YIELD", "CALL db.labels() YIELD label CALL apoc.do.when(true, 'x', 'y') YIELD value "
     "RETURN value", "apoc"),
    ("CALL gds.*", "CALL gds.graph.project('g', '*', '*')", "gds"),
    ("a genai.* function (egress)", "RETURN genai.vector.encode('x', 'OpenAI', {}) AS v", "genai"),
    ("CALL db.createLabel", "CALL db.createLabel('X')", "db.createLabel"),
    ("CALL db.create.setNodeVectorProperty", "MATCH (n) CALL db.create.setNodeVectorProperty(n, 'e', [1.0])",
     "db.create"),
    ("CALL dbms.setConfigValue", "CALL dbms.setConfigValue('db.logs.query.enabled', 'OFF')", "dbms"),
    ("CALL dbms.security.createUser", "CALL dbms.security.createUser('a', 'b')", "dbms"),
    ("a dbms.* function", "RETURN dbms.something() AS x", "dbms"),
    ("CALL db.clearQueryCaches (not on the allowlist)", "CALL db.clearQueryCaches()", "db.clearQueryCaches"),
    ("CALL an unqualified procedure", "CALL something()", "something"),
    ("an unknown namespaced function", "RETURN custom.plugin.fn(1) AS x", "core Cypher function"),
    ("a second statement after ';'", "MATCH (n) RETURN n; MATCH (m) DETACH DELETE m", "several statements"),
    ("two read statements", "RETURN 1; RETURN 2", "several statements"),
    ("';;'", "MATCH (n) RETURN n;;", "several statements"),
    ("a write after a // comment line", "MATCH (n) RETURN n.name AS x // ok\nDETACH DELETE n", "DETACH"),
    ("a write after a /* */ comment", "MATCH (n) /* look */ DETACH DELETE n", "DETACH"),
    ("a decoded \\u2028 inside a // comment (the decoded text is checked too)",
     "MATCH (n) RETURN n // c \\u2028 DETACH DELETE n", "line-break"),
    ("U+2028 between clauses (no comment)", "MATCH (n)\u2028RETURN n", "line-break"),
    ("VT inside a /* */ comment", "MATCH (n) /* a\x0bb */ RETURN n", "line-break"),
    ("a quote spelled \\u0027 (Neo4j closes the string there)",
     "MATCH (n) WHERE n.name = 'x\\u0027 DETACH DELETE n //' RETURN n", "\\u0027"),
    ("a keyword spelled with \\u escapes", "MATCH (n) \\u0053ET n.x = 1", "\\u0053"),
    ("a whole clause spelled with \\u escapes", "\\u0043REATE (n)", "\\u0043"),
    ("a comment opener spelled \\u002f\\u002f", "MATCH (n) RETURN n \\u002f\\u002f x", "\\u002f"),
    ("a backtick spelled \\u0060", "MATCH (n) RETURN 1 AS `a\\u0060 DETACH DELETE n //`", "\\u0060"),
    ("a newline spelled \\u000a", "MATCH (n) RETURN n // x \\u000a DETACH DELETE n", "\\u000a"),
    ("a quote spelled \\U00000027 (a later Cypher may decode 8-digit escapes)",
     "MATCH (n) WHERE n.name = 'x\\U00000027 DETACH DELETE n //' RETURN n", "\\U00000027"),
    ("an escape that is no Unicode character", "MATCH (n) RETURN n.name AS \\UFFFFFFFF", "not a Unicode character"),
    ("a long-s spelling of SET (case folding)", "MATCH (n) \u017fET n.x = 1", "SET"),
    ("a full-width spelling of SET (NFKC)", "MATCH (n) \uff33\uff25\uff34 n.x = 1", "SET"),
    ("an invisible format character outside a string", "MATCH (n) RETURN n\u200b", "invisible"),
    ("an unterminated string", "MATCH (n) WHERE n.name = 'abc RETURN n", "unterminated string"),
    ("an unterminated /* comment", "MATCH (n) /* RETURN n", "unterminated /*"),
    ("an unterminated `quoted` name", "MATCH (`n) RETURN 1", "unterminated `quoted`"),
    ("a backslash-escaped closing quote keeps the string open", "MATCH (n) WHERE n.name = 'a\\' RETURN n",
     "unterminated string"),
    ("an empty query", "   ", "empty query"),
    ("only a comment", "// nothing here", "empty"),
    ("a leading FOREACH", "FOREACH (x IN [1] | CREATE (n))", "start with"),
    ("a leading ALTER", "ALTER DATABASE neo4j SET ACCESS READ ONLY", "start with"),
    ("ENABLE SERVER", "ENABLE SERVER 'x'", "start with"),
    ("a leading FINISH (not a read listing)", "FINISH", "start with"),
    ("a leading BOM", "\ufeffMATCH (n) RETURN n", "invisible"),
    # Neo4j 5.26 reads the 4 characters after \u with Integer.parseInt(s, 16): a sign and any Unicode digit decode
    # (measured: 'A\u+041', full-width and Arabic-Indic 0041 all read 'AA'); the JavaCC fallback also takes \uu.
    ("a sign-prefixed escape \\u+027 (Neo4j decodes it)",
     "MATCH (n) WHERE n.name = 'x" + _BS + "u+027 DETACH DELETE n //' RETURN n", "four hex digits"),
    ("a sign-prefixed newline escape \\u+00a ends a comment for Neo4j",
     "RETURN 1 AS a // note " + _BS + "u+00a , 2 AS b", "four hex digits"),
    ("\\u + full-width digits", "MATCH (n) WHERE n.name = 'x" + _BS + "u" + _FW + " DETACH DELETE n //' RETURN n",
     "four hex digits"),
    ("\\u + Arabic-Indic digits", "MATCH (n) WHERE n.name = 'x" + _BS + "u" + _AR + " DETACH DELETE n //' RETURN n",
     "four hex digits"),
    ("\\u + a full-width hex letter", "RETURN 'A" + _BS + "u004" + chr(0xFF21) + "' AS s", "four hex digits"),
    ("\\uu0027 (the JavaCC form)", "MATCH (n) WHERE n.name = 'x" + _BS + "uu0027 DETACH DELETE n //' RETURN n",
     "four hex digits"),
    ("a truncated escape", "RETURN 'a" + _BS + "u12' AS s", "four hex digits"),
    # The AS exemption holds only for the AS KEYWORD: after an alias / parameter / label / property spelled `as`, the
    # next word is still checked (Neo4j: `WITH 1 AS as RETURN as` is legal).
    ("SET after an alias named as", "MATCH (n) WITH n, 1 AS as SET n.x = 1", "SET"),
    ("CREATE after an alias named as", "MATCH (n) WITH n AS as CREATE (m:X)", "CREATE"),
    ("SET after a label named AS", "MATCH (n) WHERE n:AS SET n.x = 1 RETURN n", "SET"),
    ("SET after a parameter named as", "MATCH (n) WHERE $as SET n.x = 1 RETURN n", "SET"),
    ("SET after a property named as", "MATCH (n) WHERE n.as SET n.x = 1 RETURN n", "SET"),
    # ...and after a VARIABLE named `as` that is not the AS keyword: `WITH as SET` read `as` as the AS keyword and
    # exempted SET (only READ mode stopped it). An alias is exempt only when it ends right after the word.
    ("SET after a variable named as (WITH as SET)", "MATCH (as) WITH as SET as.x = 1 RETURN as", "SET"),
    ("SET after a variable named as in a WITH list", "MATCH (n), (as) WITH n, as SET n.x = 1 RETURN n", "SET"),
    ("CREATE after a variable named as", "UNWIND [1] AS as WITH as CREATE (m:X)", "CREATE"),
    # A keyword glued to the text before it by a NON-ASCII whitespace escape lexes as one word in the raw text;
    # only the DECODED text (what Neo4j parses) shows the keyword — these rows lock that second check.
    ("LOAD CSV glued by \\u3000 (ideographic space) escapes: only the decoded text shows it",
     "WITH 1 AS x" + _BS + "u3000LOAD" + _BS + "u3000CSV FROM 'file:///x.csv' AS l RETURN l", "LOAD"),
    ("SET glued by a \\u00a0 (no-break space) escape: only the decoded text shows it",
     "UNWIND [1] AS a WITH a" + _BS + "u00a0SET a.x = 1", "SET"),
] + [
    (f"{name} after a // comment (Neo4j keeps it IN the comment; the next quote would open a string only for the "
     f"validator)", f"WITH 1 AS a // c{ch}'\nLOAD CSV FROM 'file:///x.csv' AS l RETURN l //'", "line-break")
    for name, ch in _ODD_BREAKS
]


def gate_validator(CR) -> None:
    print("\n-- A: the validator truth table (reads pass, writes / admin / IO / procedures are rejected) --")
    for label, q in ACCEPT:
        try:
            got = CR.check_read_only(q)
            check(f"accepts {label}", got == q, f"returned {got!r}")
        except Exception as exc:  # noqa: BLE001
            check(f"accepts {label}", False, f"{type(exc).__name__}: {exc}")
    for label, q, why in REJECT:
        try:
            CR.check_read_only(q)
            check(f"rejects {label}", False, "accepted")
        except CR.CypherRejected as exc:
            check(f"rejects {label} (reason names {why!r})", why.lower() in str(exc).lower(), str(exc))
        except Exception as exc:  # noqa: BLE001
            check(f"rejects {label}", False, f"raised {type(exc).__name__}, not CypherRejected: {exc}")
    check("CypherRejected is a ValueError (FastMCP turns it into an isError, never an empty answer)",
          issubclass(CR.CypherRejected, ValueError))
    for bad in (None, 42, b"MATCH (n) RETURN n"):
        try:
            CR.check_read_only(bad)
            check(f"rejects a non-string query {bad!r}", False, "accepted")
        except CR.CypherRejected:
            check(f"rejects a non-string query {bad!r}", True)
    long_q = "MATCH (n) WHERE n.name IN [" + ", ".join(f"'n{i}'" for i in range(4000)) + "] RETURN n"
    try:
        CR.check_read_only(long_q)
        check(f"rejects a {len(long_q):,}-char query (never truncates it)", False, "accepted")
    except CR.CypherRejected as exc:
        check(f"rejects a {len(long_q):,}-char query (never truncates it)", "never truncated" in str(exc), str(exc))
    check("the denylist and allowlists are what the docstring promises",
          {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH", "LOAD", "USE"} <= CR.DENIED
          and "db.index.vector.querynodes" in CR.PROCEDURES and "dbms.components" in CR.PROCEDURES
          and not any(p.startswith(("apoc.", "gds.")) or p.startswith("db.create") for p in CR.PROCEDURES)
          and CR.LEADING == {"MATCH", "OPTIONAL", "WITH", "UNWIND", "RETURN", "CALL", "SHOW"})


def gate_params_and_limit(CR) -> None:
    print("\n-- B: params and limit --")
    check("params None -> {}", CR.check_params(None) == {})
    check("params dict passes through", CR.check_params({"name": "save", "v": [0.1, 0.2]}) ==
          {"name": "save", "v": [0.1, 0.2]})
    for label, bad, why in (("a list", [1, 2], "JSON object"), ("a string", "x", "JSON object"),
                            ("a non-string key", {1: "x"}, "non-empty strings"),
                            ("an empty key", {"": 1}, "non-empty strings"),
                            ("a non-JSON value", {"x": object()}, "JSON values"),
                            ("an oversized value", {"x": "y" * (CR.PARAMS_MAX_BYTES + 10)}, "cap")):
        try:
            CR.check_params(bad)
            check(f"params: rejects {label}", False, "accepted")
        except CR.CypherRejected as exc:
            check(f"params: rejects {label}", why in str(exc), str(exc))
    check(f"limit default {CR.LIMIT_DEFAULT} and max {CR.LIMIT_MAX} (about 100 / 500)",
          CR.LIMIT_DEFAULT == 100 and CR.LIMIT_MAX == 500)
    check("an in-range limit is silent", CR.clamp_limit(25) == (25, None))
    got, note = CR.clamp_limit(9999)
    check("limit 9999 -> 500 with a note", got == 500 and "clamped" in (note or ""), (got, note))
    got, note = CR.clamp_limit(0)
    check("limit 0 -> 1 with a note", got == 1 and "clamped" in (note or ""), (got, note))
    got, note = CR.clamp_limit("abc")
    check("a non-integer limit -> the default with a note", got == CR.LIMIT_DEFAULT and note, (got, note))
    with _env(DEEPMAP_CYPHER_TIMEOUT_S="7.5"):
        check("timeout_s honours DEEPMAP_CYPHER_TIMEOUT_S", CR.timeout_s() == 7.5)
    with _env(DEEPMAP_CYPHER_TIMEOUT_S="nope"):
        check("a malformed DEEPMAP_CYPHER_TIMEOUT_S falls back to the default", CR.timeout_s() == CR.TIMEOUT_S)
    with _env(DEEPMAP_CYPHER_TIMEOUT_S="-1"):
        check("a non-positive DEEPMAP_CYPHER_TIMEOUT_S falls back to the default", CR.timeout_s() == CR.TIMEOUT_S)


def gate_shaping(CR) -> None:
    print("\n-- C: result shaping (nodes / relationships / paths, no vectors) --")
    a = _fn()
    b = _fn(eid="4:a:2", name="Order.notify", key="app/order.py::Order.notify:30")
    b.labels = frozenset(["Method"])
    r = FRel("5:a:1", "CALLS", a, b, {"confidence": "AMBIGUOUS"})
    rows, notes = CR.shape_rows([{"n": a}], 10)
    n = rows[0]["n"] if rows else {}
    check("a node -> labels + key/name/source_file/start_line/confidence/body_summary",
          n.get("labels") == ["Function"] and n.get("key") == a["key"] and n.get("name") == "save"
          and n.get("source_file") == "app/order.py" and n.get("start_line") == 10
          and n.get("confidence") == "EXTRACTED" and n.get("body_summary") == "persist the order", str(n))
    check("a node never carries its embedding or the _embed_hash / _content_hash internals",
          "embedding" not in n and "_embed_hash" not in n and "_content_hash" not in n, str(sorted(n)))
    check("node property order: labels, key, name, source_file, start_line first",
          list(n)[:5] == ["labels", "key", "name", "source_file", "start_line"], str(list(n)))
    big = _fn(tags=list(range(500)), aliases=["x", "y"])
    n2 = CR.shape_rows([{"n": big}], 10)[0][0]["n"]
    check("a long list property is left out; a short one is kept", "tags" not in n2 and n2.get("aliases") == ["x", "y"],
          str(sorted(n2)))
    small = _fn(embedding=[0.5] * 32, embedding_model="nomic")   # below the size rules: only the NAME rule drops it
    n3 = CR.shape_rows([{"n": small}], 10)[0][0]["n"]
    check("an embedding* property is left out by NAME, even a short vector (any model / dimension)",
          not any(k.lower().startswith("embedding") for k in n3) and n3.get("name") == "save", str(sorted(n3)))
    rows, _ = CR.shape_rows([{"r": r}], 10)
    rr = rows[0]["r"]
    check("a relationship -> type, confidence and its endpoint keys",
          rr.get("type") == "CALLS" and rr.get("confidence") == "AMBIGUOUS" and rr.get("start") == a["key"]
          and rr.get("end") == b["key"], str(rr))
    bare = FRel("5:a:9", "ROUTES_TO", FNode("4:x:1", [], {}), FNode("4:x:2", [], {}),
                {"confidence": "INFERRED", "method": "POST", "path": "/orders"})
    rb = CR.shape_rows([{"r": bare}], 10)[0][0]["r"]
    check("a relationship keeps its attrs (method, path) and omits endpoints the driver did not hydrate",
          rb == {"type": "ROUTES_TO", "confidence": "INFERRED", "method": "POST", "path": "/orders"}, str(rb))
    c = _fn(eid="4:a:3", name="checkout", key="app/shop.py::checkout:5")
    r2 = FRel("5:a:2", "CALLS", c, a, {"confidence": "INFERRED"})
    p = FPath([a, c, b], [r2, r])          # r2 is c->a, so read from a the first hop runs backwards ('<-')
    pr = CR.shape_rows([{"p": p}], 10)[0][0]["p"]
    seq = pr.get("path") or []
    check("a path -> {path: [node, rel, node, ...], length}", pr.get("length") == 2 and len(seq) == 5, str(pr))
    check("path nodes are compact (key, name, source_file, start_line; no summary, no vector)",
          seq and seq[0] == {"labels": ["Function"], "key": a["key"], "name": "save", "source_file": "app/order.py",
                             "start_line": 10}, str(seq[:1]))
    check("path relationships carry type, confidence and the direction along the path",
          len(seq) == 5 and seq[1] == {"type": "CALLS", "confidence": "INFERRED", "dir": "<-"}
          and seq[3].get("dir") == "<-" and seq[3].get("confidence") == "AMBIGUOUS", str(seq[1::2]))
    fwd = FPath([c, a], [r2])
    sf = CR.shape_rows([{"p": fwd}], 10)[0][0]["p"]["path"]
    check("a forward hop reads '->'", sf[1].get("dir") == "->", str(sf))
    rows, notes = CR.shape_rows([{"name": "save", "v": VEC}], 10)
    check("a returned 768-float list (n.embedding) is never returned",
          isinstance(rows[0]["v"], str) and "omitted" in rows[0]["v"] and rows[0]["name"] == "save", str(rows)[:200])
    check("...and a note says what is left out (embedding properties, lists of 256+ floats) and that a slice is not",
          any("256" in x and "slice" in x for x in notes) and not any("never returned" in x for x in notes),
          str(notes))
    rows, notes = CR.shape_rows([{"p1": VEC[:255], "p3": VEC[510:]}], 10)
    check("a 255-float slice of a vector comes back (the honest limit the note and docs state)",
          rows[0]["p1"] == VEC[:255] and isinstance(rows[0]["p3"], str), str(rows)[:200])
    rows, notes = CR.shape_rows([{"m": {"name": "save", "embedding": [0.1, 0.2]}}], 10)
    check("a map projection's `embedding` key is omitted (n {.*})", rows[0]["m"]["embedding"].startswith("<")
          and rows[0]["m"]["name"] == "save", str(rows))
    rows, _ = CR.shape_rows([{"lines": list(range(400)), "scores": [0.5] * 100}], 10)
    check("an int list (collect(start_line)) and a short float list are NOT mistaken for vectors",
          rows[0]["lines"] == list(range(400)) and rows[0]["scores"] == [0.5] * 100)
    rows, notes = CR.shape_rows([{"s": "x" * 5000}], 10)
    check(f"a long string is cut at {CR.STR_MAX} characters and a note says so",
          rows[0]["s"].startswith("x" * CR.STR_MAX) and len(rows[0]["s"]) < 1100 and any("cut" in x for x in notes))
    rows, _ = CR.shape_rows([{"b": b"\x00\x01", "f": float("nan"), "t": types.SimpleNamespace(), "z": None,
                              "ok": True, "i": 3}], 10)
    check("bytes / NaN / unknown objects become text; None / bool / int stay",
          rows[0]["b"] == "<2 bytes>" and rows[0]["f"] == "nan" and isinstance(rows[0]["t"], str)
          and rows[0]["z"] is None and rows[0]["ok"] is True and rows[0]["i"] == 3, str(rows))
    nested = [[[[[[[[[["deep"]]]]]]]]]]
    rows, _ = CR.shape_rows([{"d": nested}], 10)
    check("pathological nesting is bounded", "nested too deeply" in str(rows), str(rows)[:200])
    rows, _ = CR.shape_rows([{"b": 1, "a": 2}], 10)
    check("columns keep the RETURN order", list(rows[0]) == ["b", "a"], str(rows))
    rows, notes = CR.shape_rows([{"note": "Showing the first 3 rows", "name": "save"}], 10)
    check("a column named `note` comes back as `note_` (a {'note': ...} entry is always DeepMap's own), in place",
          rows == [{"note_": "Showing the first 3 rows", "name": "save"}], str(rows))
    check("...and a note says it was renamed", any("`note_`" in x for x in notes), str(notes))
    rows, _ = CR.shape_rows([{"note": 1, "note_": 2}, {"note": 3, "note_": 4}], 10)
    check("...never onto a column the query already returns (note -> note__)",
          rows == [{"note__": 1, "note_": 2}, {"note__": 3, "note_": 4}], str(rows))
    rows, notes = CR.shape_rows([{"Note": 1, "notes": 2}], 10)
    check("...only the exact name `note` is renamed", rows == [{"Note": 1, "notes": 2}] and not notes, str(rows))


def gate_caps(CR) -> None:
    print("\n-- D: the row and byte caps --")
    owner = types.SimpleNamespace(consumed=0)
    recs = _Counting([{"i": i} for i in range(150)], owner)
    rows, notes = CR.shape_rows(recs, 100)
    check("150 records, limit 100 -> 100 rows", len(rows) == 100 and rows[-1] == {"i": 99}, len(rows))
    check("...reading at most limit + 1 records from the result", owner.consumed == 101, owner.consumed)
    check("...and a note says more rows exist", any("returned more" in x for x in notes), str(notes))
    rows, notes = CR.shape_rows([{"i": i} for i in range(100)], 100)
    check("exactly limit records -> no 'more rows' note", len(rows) == 100 and not notes, str(notes))
    blob = "y" * 900
    rows, notes = CR.shape_rows([{"s": blob, "i": i} for i in range(300)], 500, max_bytes=CR.MAX_BYTES)
    size = _wire_bytes(rows)
    check(f"the byte cap stops the rows before {CR.MAX_BYTES:,} bytes of the text the client receives",
          0 < len(rows) < 300 and size <= CR.MAX_BYTES, (len(rows), size))
    check("...with a note naming the size cap", any("size cap" in x for x in notes), str(notes))
    # FastMCP sends EACH row as its own text block, JSON with indent=2 (pydantic_core.to_json(row, indent=2)), not
    # compact JSON: ~24% more text on whole nodes, ~70% on paths (measured live). The cap must hold for THAT text, or
    # "about 64 KB" is untrue of what lands in the agent's context.
    nodes = [{"n": _fn(eid=f"4:a:{i}", name=f"fn_{i}", key=f"app/order.py::fn_{i}:{i}")} for i in range(500)]
    rows, notes = CR.shape_rows(nodes, 500)
    wire = _wire_bytes(rows)
    one = _wire_bytes(rows[:1])
    check(f"whole-node rows: the text FastMCP sends ({wire:,} bytes as {_WIRE_HOW}) stays within the "
          f"{CR.MAX_BYTES:,}-byte cap", 0 < len(rows) < 500 and wire <= CR.MAX_BYTES, (len(rows), wire))
    check("...and the cap is used, not wasted (it stopped because the next row would not fit)",
          CR.MAX_BYTES - wire < one, (wire, one))
    zrow = {"s": "z" * 400, "k": {"a": 1}}
    at = _wire_bytes([zrow])
    under, _ = CR.shape_rows([zrow] * 3, 10, max_bytes=at - 1)
    exact, notes = CR.shape_rows([zrow] * 3, 10, max_bytes=at)
    check(f"a row is measured as sent ({at} bytes): a cap 1 byte under that holds no row, a cap of exactly that "
          "holds one", under == [] and len(exact) == 1 and any("size cap" in x for x in notes), (len(under), len(exact)))
    sample = [{"n": CR._Shaper().value(_fn())}, {"name": "café — 東京", "nested": {"a": [1, 2, {"b": None}]}},
              {"t": True, "e": [], "m": {}}, {"q": 'say "hi"\\n\ttab'}]
    try:
        import pydantic_core
        exact = [(CR._size(r), len(pydantic_core.to_json(r, fallback=str, indent=2))) for r in sample]
        check("_size(row) == the byte length of pydantic_core.to_json(row, indent=2), FastMCP's own serialiser "
              "(nodes, non-ASCII, nesting, empty containers, escapes)", all(a == b for a, b in exact), str(exact))
    except ImportError:
        check("(pydantic_core absent: the FastMCP serialiser parity check is skipped)", True)
    rows, notes = CR.shape_rows([{"s": "z" * 900}] * 3, 10, max_bytes=500)
    check("a first row bigger than the cap -> no rows and a note saying why",
          rows == [] and any("first row alone" in x for x in notes), str(notes))
    check("...and not the misleading 'No rows' note", not any(x.startswith("No rows") for x in notes), str(notes))
    rows, notes = CR.shape_rows([], 100)
    check("an empty answer -> [] + a note that empty is not proof of absence",
          rows == [] and any("not proof of absence" in x for x in notes), str(notes))


def gate_run_read_only(CR) -> None:
    print("\n-- E: the transaction — READ mode, a server-side timeout, always rolled back, never committed --")
    drv = FakeDriver(records=[{"name": "save"}])
    rows, notes = CR.run_read_only(drv, "MATCH (f:Function) RETURN f.name AS name", {"x": 1}, limit=5, timeout=7)
    check("rows come back", rows == [{"name": "save"}] and notes == [], (rows, notes))
    kw = drv.sessions[0] if drv.sessions else {}
    check("the session is opened in READ access mode", str(kw.get("default_access_mode")).upper() == "READ", str(kw))
    check("...fetching at most limit + 1 records per batch", kw.get("fetch_size") == 6, str(kw))
    tx = drv.tx_kw[0] if drv.tx_kw else {}
    check("an explicit transaction carries the server-side timeout", tx.get("timeout") == 7.0, str(tx))
    check("...and DeepMap's metadata tag", (tx.get("metadata") or {}).get("app") == "deepmap-cypher", str(tx))
    check("the query and its $params are sent verbatim",
          drv.ran == [("MATCH (f:Function) RETURN f.name AS name", {"x": 1})], str(drv.ran))
    check("the transaction is ROLLED BACK and never committed", drv.rolled_back == 1 and drv.committed == 0,
          (drv.rolled_back, drv.committed))
    check("no autocommit / execute_read / execute_write path is used", drv.autocommit == 0 and drv.writes == 0)
    with _env(DEEPMAP_CYPHER_TIMEOUT_S="9"):
        drv2 = FakeDriver()
        CR.run_read_only(drv2, "RETURN 1 AS x")
    check("without an explicit timeout the DEEPMAP_CYPHER_TIMEOUT_S budget is used",
          drv2.tx_kw and drv2.tx_kw[0]["timeout"] == 9.0, str(drv2.tx_kw))

    class _Boom(Exception):
        code = "Neo.ClientError.Statement.SyntaxError"
    drv3 = FakeDriver(raise_on_run=_Boom("Invalid input"))
    try:
        CR.run_read_only(drv3, "MATCH (n) RETURN n")
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("a failing run propagates the Neo4j error unchanged", isinstance(raised, _Boom), repr(raised))
    check("...and still rolls back (never commits)", drv3.rolled_back == 1 and drv3.committed == 0)
    drv4 = FakeDriver()
    for q in ("MATCH (n) DETACH DELETE n", "LOAD CSV FROM 'https://e.x/a' AS l RETURN l"):
        try:
            CR.run_read_only(drv4, q)
        except CR.CypherRejected:
            pass
    check("a rejected query never opens a session (validated before Neo4j is touched)",
          drv4.sessions == [] and drv4.ran == [], (drv4.sessions, drv4.ran))
    try:
        CR.run_read_only(drv4, "RETURN 1 AS x", params=[1])
    except CR.CypherRejected:
        pass
    check("bad params never open a session either", drv4.sessions == [])
    drv5 = FakeDriver(records=[{"i": i} for i in range(10)])
    rows, notes = CR.run_read_only(drv5, "UNWIND range(1, 10) AS i RETURN i", limit=9999)
    check("an out-of-range limit is clamped with a note", any("clamped to 500" in x for x in notes), str(notes))
    print("\n-- E2: Neo4j errors about THIS query read as a sentence; outages / auth stay for the caller --")
    E = types.SimpleNamespace
    m = CR.query_error_message(E(code="Neo.ClientError.Statement.SyntaxError", message="Invalid input 'x'"))
    check("a syntax error -> 'Neo4j rejected the query (code): message'",
          m is not None and "rejected the query" in m and "SyntaxError" in m, m)
    m = CR.query_error_message(E(code="Neo.ClientError.Statement.AccessMode",
                                 message="Writing in read access mode not allowed."), 20)
    check("the READ-mode refusal of a write is named as such", m is not None and "refused a write" in m, m)
    m = CR.query_error_message(E(code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
                                 message="timed out"), 15)
    check("a transaction timeout says how to narrow the query", m is not None and "15s" in m and "LIMIT" in m, m)
    m = CR.query_error_message(E(code="Neo.TransientError.General.MemoryPoolOutOfMemoryError", message="oom"))
    check("a memory-pool error says how to narrow the query", m is not None and "memory" in m, m)
    m = CR.query_error_message(E(code="Neo.ClientError.Procedure.ProcedureNotFound", message="no procedure"))
    check("an unknown procedure is a query error", m is not None and "ProcedureNotFound" in m, m)
    for code in ("Neo.ClientError.Security.Unauthorized", ""):
        check(f"{code or 'a code-less error (an outage)'} is left to the caller (None)",
              CR.query_error_message(E(code=code, message="x")) is None)


# ---- F: the MCP tool --------------------------------------------------------------------------------------------------
def _call(srv, name, args):
    import anyio

    async def go():
        return await srv.mcp._tool_manager.call_tool(name, args, convert_result=False)
    try:
        return False, anyio.run(go)
    except Exception as exc:  # noqa: BLE001 — FastMCP raises ToolError for an isError result
        return True, str(exc)


def _call_sent(srv, name, args):
    """(isError, what FastMCP sends): the converted result — (content blocks, structuredContent) — of a tool call."""
    import anyio

    async def go():
        return await srv.mcp._tool_manager.call_tool(name, args, convert_result=True)
    try:
        return False, anyio.run(go)
    except Exception as exc:  # noqa: BLE001
        return True, str(exc)


def gate_mcp_registration(S, CR) -> None:
    print("\n-- F1: deepmap_cypher is registered on the search server like the other tools --")
    tool = S.mcp._tool_manager.get_tool("deepmap_cypher")
    check("the search server registers deepmap_cypher", tool is not None)
    if tool is None:
        return
    check("it is an ASYNC tool (bounded_tool: the body runs off the event loop)", tool.is_async)
    props = set((tool.parameters or {}).get("properties", {}))
    check("it advertises exactly (query, params, limit)", props == {"query", "params", "limit"}, str(props))
    check("only `query` is required", sorted((tool.parameters or {}).get("required") or []) == ["query"],
          str((tool.parameters or {}).get("required")))
    check(f"limit defaults to {CR.LIMIT_DEFAULT}",
          ((tool.parameters or {}).get("properties", {}).get("limit") or {}).get("default") == CR.LIMIT_DEFAULT)
    check("the directly-callable sync contract stays a plain function", callable(S.deepmap_cypher)
          and not __import__("inspect").iscoroutinefunction(S.deepmap_cypher))
    names = [t.name for t in S.mcp._tool_manager.list_tools()]
    check("deepmap_search and deepmap_impact are still registered", {"deepmap_search", "deepmap_impact"} <= set(names),
          str(names))


def gate_mcp_wiring_ast() -> None:
    print("\n-- F2: the decorator wires the project guard, the auth back-off and the project label --")
    tree = ast.parse((BUILD / "deepmap_search_server.py").read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "deepmap_cypher"), None)
    check("deepmap_cypher is defined in build/deepmap_search_server.py", fn is not None)
    if fn is None:
        return
    deco = next((d for d in fn.decorator_list if isinstance(d, ast.Call)
                 and ast.unparse(d.func) == "mcp_bounds.bounded_tool"), None)
    check("it is decorated with mcp_bounds.bounded_tool(mcp, ...)", deco is not None
          and deco.args and ast.unparse(deco.args[0]) == "mcp")
    kw = {k.arg: ast.unparse(k.value) for k in (deco.keywords if deco else [])}
    check("guard=_project_guard (the D3 project guard)", kw.get("guard") == "_project_guard", str(kw))
    check("backoff=_AUTH (no new login inside the back-off window)", kw.get("backoff") == "_AUTH", str(kw))
    check("describe=project_tool_describer(...) (the project label)",
          "project_tool_describer" in kw.get("describe", ""), str(kw))
    body = ast.unparse(fn)
    check("the body validates with cypher_readonly before it asks for the driver",
          "check_read_only" in body and body.index("check_read_only") < body.index("_get_retriever"), body[:300])
    check("the body never opens its own GraphDatabase driver", "GraphDatabase" not in body)


class _AuthError(Exception):
    code = "Neo.ClientError.Security.Unauthorized"


_AuthError.__name__ = "AuthError"


def gate_mcp_behaviour(S, CR) -> None:
    print("\n-- F3: the tool validates first, answers rows + notes, stamps last_used, stays bounded --")
    got = {"driver_asked": 0, "touched": 0}
    drv = FakeDriver(records=[{"n": _fn()}, {"n": _fn(eid="4:a:2", name="load")}])

    def _retriever():
        got["driver_asked"] += 1
        return types.SimpleNamespace(driver=drv)

    def _touch():
        got["touched"] += 1
    S._get_retriever = _retriever
    S._touch_last_used = _touch
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n) DETACH DELETE n"})
    check("a write is an isError naming the reason", err and "DETACH" in str(out), str(out)[:200])
    check("...decided BEFORE the driver was asked for (recorder seam: never called)",
          got["driver_asked"] == 0 and drv.sessions == [], (got, drv.sessions))
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n) RETURN n", "params": "not-a-dict"})
    check("bad params are refused (pydantic or the validator) before the driver", err and got["driver_asked"] == 0,
          str(out)[:200])
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n:Function) RETURN n LIMIT 2", "limit": 1})
    rows = out if isinstance(out, list) else []
    check("a read answers its rows", not err and rows[:1] and rows[0].get("n", {}).get("name") == "save", str(out)[:300])
    check("...then a trailing {'note': ...} when the limit cut it",
          len(rows) == 2 and "note" in rows[-1] and "returned more" in rows[-1]["note"], str(rows)[-300:])
    check("...shaped (no embedding in the answer)", "embedding" not in str(rows[0]), str(rows[0])[:200])
    check("...through a READ session, rolled back, never committed",
          str((drv.sessions or [{}])[-1].get("default_access_mode")).upper() == "READ" and drv.rolled_back >= 1
          and drv.committed == 0, (drv.sessions, drv.rolled_back, drv.committed))
    check("an answered call stamps last_used (the idle-hibernation signal)", got["touched"] == 1, got)
    err, out = _call(S, "deepmap_cypher", {"query": "RETURN 1 AS x", "limit": 9999})
    check("an out-of-range limit is clamped and SAID (trailing note)",
          not err and any("clamped to 500" in str(r.get("note", "")) for r in out), str(out))
    with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="12", DEEPMAP_CYPHER_TIMEOUT_S=None):
        _call(S, "deepmap_cypher", {"query": "RETURN 1 AS x"})
    check("Neo4j's read timeout stays 5 s inside the tool deadline (12 s -> 7 s)",
          drv.tx_kw and drv.tx_kw[-1]["timeout"] == 7.0, str(drv.tx_kw[-1:]))
    with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S=None, DEEPMAP_CYPHER_TIMEOUT_S=None):
        _call(S, "deepmap_cypher", {"query": "RETURN 1 AS x"})
    check(f"with the default 60 s deadline Neo4j gets the {CR.TIMEOUT_S:g} s read timeout",
          drv.tx_kw and drv.tx_kw[-1]["timeout"] == CR.TIMEOUT_S, str(drv.tx_kw[-1:]))

    many = [{"n": _fn(eid=f"4:a:{i}", name=f"fn_{i}", key=f"app/order.py::fn_{i}:{i}")} for i in range(500)]
    S._get_retriever = lambda: types.SimpleNamespace(driver=FakeDriver(records=many))
    err, sent = _call_sent(S, "deepmap_cypher", {"query": "MATCH (n:Function) RETURN n", "limit": 500})
    texts = [] if err else [getattr(c, "text", "") for c in (sent[0] if isinstance(sent, tuple) else sent)]
    row_texts = [t for t in texts if not t.lstrip().startswith('{\n  "note"')]
    sent_bytes = sum(len(t.encode("utf-8")) for t in row_texts)
    check(f"END TO END: the row text blocks FastMCP builds for 500 whole nodes total <= {CR.MAX_BYTES:,} bytes "
          f"({sent_bytes:,} in {len(row_texts)} blocks)", not err and 0 < len(row_texts) < 500
          and sent_bytes <= CR.MAX_BYTES, (err, str(sent)[:200]))
    check("...and the answer says the size cap cut it", any("size cap" in t for t in texts), texts[-2:])
    S._get_retriever = lambda: types.SimpleNamespace(driver=FakeDriver(records=[{"note": "persist the order"}]))
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (f:Function) RETURN f.body_summary AS note"})
    check("a column named `note` never reads as one of DeepMap's trailing {'note': ...} entries",
          not err and out and out[0] == {"note_": "persist the order"} and len(out) == 2
          and set(out[1]) == {"note"} and "`note_`" in out[1]["note"], str(out)[:300])
    syntax = type("CypherSyntaxError", (Exception,), {"code": "Neo.ClientError.Statement.SyntaxError",
                                                       "message": "Invalid input 'RETRUN'"})
    S._get_retriever = lambda: types.SimpleNamespace(driver=FakeDriver(raise_on_run=syntax("x")))
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n) RETURN n"})
    check("a Neo4j syntax error is an isError that reads as a sentence",
          err and "rejected the query" in str(out) and "RETRUN" in str(out), str(out)[:300])
    access = type("ClientError", (Exception,), {"code": "Neo.ClientError.Statement.AccessMode",
                                                 "message": "Writing in read access mode not allowed."})
    S._get_retriever = lambda: types.SimpleNamespace(driver=FakeDriver(raise_on_run=access("x")))
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n) RETURN n"})
    check("the READ-mode refusal (layer 2) is named as such", err and "refused a write" in str(out), str(out)[:300])
    down = type("ServiceUnavailable", (Exception,), {})

    def _stopped():
        raise down("Couldn't connect to 127.0.0.1:7689")
    S._get_retriever = _stopped
    err, out = _call(S, "deepmap_cypher", {"query": "MATCH (n) RETURN n"})
    check("a stopped Neo4j is an outage with the next step (bounded_tool's wording), not a query error",
          err and "is not reachable" in str(out) and "/deep-map:setup" in str(out)
          and "rejected the query" not in str(out), str(out)[:300])

    print("\n-- F4: a slow query fails at the deadline and does not block the loop; a rejected login backs off --")
    import anyio
    slow = FakeDriver(records=[{"x": 1}], delay=2.5)
    S._get_retriever = lambda: types.SimpleNamespace(driver=slow)
    done: dict = {}

    async def both():
        t0 = time.monotonic()

        async def a():
            try:
                await S.mcp._tool_manager.call_tool("deepmap_cypher", {"query": "RETURN 1 AS x"})
                done["cypher"] = ("ok", time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001
                done["cypher"] = (str(exc), time.monotonic() - t0)

        async def b():
            await anyio.sleep(0.2)
            done["other"] = time.monotonic() - t0
        async with anyio.create_task_group() as tg:
            tg.start_soon(a)
            tg.start_soon(b)
    with _env(DEEPMAP_MCP_TOOL_TIMEOUT_S="1"):
        anyio.run(both)
    msg, took = done.get("cypher", ("", 99))
    check("past DEEPMAP_MCP_TOOL_TIMEOUT_S the call fails with the deadline message (not an empty answer)",
          "did not answer within" in msg and took < 2.4, (msg[:160], took))
    check("the event loop kept serving while the query ran", done.get("other", 99) < 0.9, done)

    calls = {"n": 0}

    def _rejecting():
        calls["n"] += 1
        return types.SimpleNamespace(driver=FakeDriver(raise_on_run=_AuthError("The client is unauthorized")))
    S._get_retriever = _rejecting
    with _env(DEEPMAP_MCP_AUTH_BACKOFF_S="30"):
        err1, out1 = _call(S, "deepmap_cypher", {"query": "RETURN 1 AS x"})
        err2, out2 = _call(S, "deepmap_cypher", {"query": "RETURN 1 AS x"})
    check("a rejected login is answered with the back-off diagnosis", err1 and "credentials" in str(out1),
          str(out1)[:200])
    check("...and the next call does NOT log in again (the driver is not asked)", err2 and calls["n"] == 1,
          (calls, str(out2)[:120]))
    S._AUTH.success()


def gate_mcp_description(CR) -> None:
    print("\n-- F5: the description states the schema briefly, and fits the client's budget --")
    import inspect
    sys.path.insert(0, str(BUILD))
    FE = _load("framework_edges", "dm_fe_cypher_gates")
    B = _load("mcp_bounds", "dm_bounds_cypher_gates")
    with _env(**{k: None for k in _ENV_KEYS}):
        bare = _load("deepmap_search_server", "dm_search_cypher_bare")
    tool = bare.mcp._tool_manager.get_tool("deepmap_cypher")
    doc = inspect.cleandoc(bare.deepmap_cypher.__doc__ or "")   # the same text on Python 3.10-3.12 and 3.13+
    check("without the project env the description is the docstring (FastMCP keeps the raw __doc__)",
          tool is not None and tool.description.strip() == (bare.deepmap_cypher.__doc__ or "").strip())
    words = set(__import__("re").findall(r"[A-Z_]+", doc))
    types_ = {"CALLS"} | {str(t) for t in FE.ALLOWED_EDGE_TYPES}
    missing = sorted(types_ - words)
    check(f"it names all {len(types_)} relationship types (CALLS + framework_edges.ALLOWED_EDGE_TYPES)", not missing,
          f"missing {missing}")
    check("it names the confidence grades and says AMBIGUOUS is unverified",
          all(g in doc for g in ("EXTRACTED", "INFERRED", "AMBIGUOUS")) and "unverified" in doc.lower())
    check("it names the code labels", all(f":{lbl}" in doc for lbl in ("Function", "Method", "Class", "Module")))
    check("it names the node properties", all(p in doc for p in ("key", "name", "source_file", "start_line",
                                                                  "body_summary", "confidence")))
    examples = [ln.strip() for ln in doc.splitlines() if ln.strip().startswith(("MATCH ", "CALL ", "OPTIONAL "))]
    check("it carries 2-3 example queries", 2 <= len(examples) <= 3, str(examples))
    for ex in examples:
        try:
            CR.check_read_only(ex)
            check(f"example passes the validator: {ex[:70]}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"example passes the validator: {ex[:70]}", False, str(exc))
    flat = " ".join(doc.split())
    check("it says read-only, the limit, and which vectors are left out without overclaiming (no 'never embedding')",
          "READ transaction" in doc and str(CR.LIMIT_MAX) in doc and "embedding" in doc.lower()
          and str(CR.VECTOR_MIN_LEN) in flat and "never embedding" not in flat.lower(), flat[-400:])
    check(f"the docstring is short (<= 1,600 chars; it costs context every session): {len(doc)}", len(doc) <= 1600)
    name = "payments-reconciliation-service"
    roots = [("Linux", "/home/alexandra.johnson/src/github.com/acme-corp/" + name)]
    if os.name == "nt":
        roots.append(("Windows", "C:\\Users\\alexandra.johnson\\source\\repos\\AcmeCorp\\" + name))
    for os_name, root in roots:
        with _env(DEEPMAP_PROJECT_ROOT=root, DEEPMAP_PROJECT_KEY="k"):
            srv = _load("deepmap_search_server", f"dm_search_cypher_{os_name}")
        d = srv.mcp._tool_manager.get_tool("deepmap_cypher").description
        head = d.split("\n\n", 1)[0]
        check(f"{os_name} {len(root)}-char path: 'Queries the DeepMap graph of project {name} (...)' + the remedy, "
              f"within {B.CLIENT_DESCRIPTION_MAX} chars ({len(d)})",
              head.startswith(f"Queries the DeepMap graph of project {name} ({root}) only.")
              and "different project" in head and "/deep-map:setup" in head
              and len(d) <= B.CLIENT_DESCRIPTION_MAX and d.endswith(doc), repr(head[:240]))


# ---- G: the CLI --------------------------------------------------------------------------------------------------------
def gate_cli(CR, home: Path) -> None:
    print("\n-- G: `deepmap cypher` — same validator and caps, through _driver() and _read_retry --")
    import argparse
    with _env(DEEPMAP_HOME=str(home)):
        C = _load("deepmap_cli", "dm_cli_cypher_gates")
    p = C.build_parser()
    try:
        a = p.parse_args(["cypher", "MATCH (n) RETURN n"])
    except SystemExit:
        a = None
    check("`cypher <query>` parses", a is not None)
    if a is None:
        return
    check("cypher -> cmd_cypher", getattr(a, "fn", None) is getattr(C, "cmd_cypher", object()))
    check(f"--limit defaults to {CR.LIMIT_DEFAULT}; --params and --json default off",
          a.limit == CR.LIMIT_DEFAULT and a.params is None and a.json is False, vars(a))
    sub = next(x for x in p._actions if isinstance(x, argparse._SubParsersAction))
    check("`deepmap -h` lists cypher", "cypher" in sub.choices)
    src = ast.parse((BUILD / "deepmap_cli.py").read_text(encoding="utf-8"))
    fn = next((n for n in src.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_cypher"), None)
    body = ast.unparse(fn) if fn is not None else ""
    check("cmd_cypher never opens a raw GraphDatabase driver (only _driver())",
          fn is not None and "GraphDatabase" not in body and "_driver()" in body)

    drivers: list = []
    retried: list = []
    touched: list = []
    real_retry, real_load = C._read_retry, C._load

    def _fake_driver():
        drv = FakeDriver(records=[{"name": "save", "n": _fn()}, {"name": "load", "n": _fn(name="load")}])
        drivers.append(drv)
        return drv

    def _retry(cmd, op):
        retried.append(cmd)
        return real_retry(cmd, op)

    def _load_stub(name):
        if name == "port_registry":      # never the machine's registry: a recorder
            return types.SimpleNamespace(touch_last_used=lambda root: touched.append(str(root)))
        return real_load(name)
    C._driver, C._read_retry, C._load = _fake_driver, _retry, _load_stub

    def run(argv):
        args = p.parse_args(argv)
        with _captured() as (out, err):
            rc = args.fn(args)
        return rc, out.getvalue(), err.getvalue()

    rc, out, err = run(["cypher", "MATCH (n) DETACH DELETE n"])
    check("a write exits 2 with the reason on stderr", rc == 2 and "DETACH" in err, (rc, err[:200]))
    check("...without ever calling _driver() (recorder seam)", drivers == [], len(drivers))
    rc, out, err = run(["cypher", "MATCH (n) RETURN n", "--params", "{not json"])
    check("invalid --params JSON exits 2 without a driver", rc == 2 and drivers == [] and "params" in err.lower(),
          (rc, err[:200]))
    rc, out, err = run(["cypher", "MATCH (n) RETURN n", "--params", "[1, 2]"])
    check("--params that is not an object exits 2", rc == 2 and drivers == [], (rc, err[:200]))
    rc, out, err = run(["cypher", "MATCH (n:Function) RETURN n.name AS name, n", "--limit", "1",
                        "--params", '{"k": "v"}'])
    drv = drivers[-1] if drivers else FakeDriver()
    check("a read exits 0 and goes through _driver() + _read_retry('cypher')",
          rc == 0 and len(drivers) == 1 and retried == ["cypher"], (rc, len(drivers), retried, err[:200]))
    check("...READ session, rolled back, never committed, driver closed",
          str((drv.sessions or [{}])[0].get("default_access_mode")).upper() == "READ" and drv.rolled_back == 1
          and drv.committed == 0 and drv.closed == 1, (drv.sessions, drv.rolled_back, drv.committed, drv.closed))
    check("...the $params are sent", drv.ran and drv.ran[0][1] == {"k": "v"}, str(drv.ran))
    check("...the DEEPMAP_CYPHER_TIMEOUT_S budget is the server-side timeout",
          drv.tx_kw and drv.tx_kw[0]["timeout"] == CR.timeout_s(), str(drv.tx_kw))
    check("...prints the rows (JSON per row, no embedding) and the 'more rows' note",
          '"name": "save"' in out and "embedding" not in out and "Note:" in out and "returned more" in out,
          out[:400])
    check("...and stamps this project's last_used (via a stub, never the machine's registry)", len(touched) == 1,
          touched)
    rc, out, err = run(["cypher", "RETURN 1 AS x", "--json"])
    try:
        payload = __import__("json").loads(out)
    except Exception:  # noqa: BLE001
        payload = None
    check("--json prints {rows, notes}", rc == 0 and isinstance(payload, dict) and "rows" in payload
          and "notes" in payload, out[:200])
    rc, out, err = run(["cypher", "RETURN 1 AS x", "--limit", "9999"])
    check("--limit 9999 is clamped to 500 with a note on stderr", rc == 0 and "clamped to 500" in err, err[:200])
    C._read_retry, C._load = real_retry, real_load

    print("\n-- G2: a Neo4j error from the query is classified by main() (exit 3), never a traceback --")
    try:
        from neo4j import exceptions as NE
        syntax = NE.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError",
                                              message="Invalid input 'RETRUN'")
    except Exception:  # noqa: BLE001
        syntax = None
    if syntax is None:
        check("(neo4j driver present to build a real error) — skipped", True)
        return
    C._driver = lambda: FakeDriver(raise_on_run=syntax)
    C._load = _load_stub
    saved = sys.argv
    sys.argv = ["deepmap", "cypher", "MATCH (n) RETURN n"]
    try:
        with _captured() as (out, err):
            rc = C.main()
    finally:
        sys.argv = saved
        C._load = real_load
    check("exit 3 with 'the query failed — ... SyntaxError ...' on stderr",
          rc == 3 and "query failed" in err.getvalue() and "SyntaxError" in err.getvalue(), (rc, err.getvalue()[:300]))


# ---- H: the skill + the smoke test -----------------------------------------------------------------------------------
def gate_skill_and_smoke() -> None:
    print("\n-- H: SKILL.md teaches deepmap_cypher; the HTTP smoke test can call it --")
    text = SKILL.read_text(encoding="utf-8")
    check("SKILL.md documents deepmap_cypher(...)", "deepmap_cypher(" in text)
    sec = text[text.find("deepmap_cypher("):] if "deepmap_cypher(" in text else ""
    low = " ".join(sec.lower().split())
    check("...when to use it: ad-hoc structure — listing, counting, paths",
          all(w in low for w in ("list", "count", "path")))
    check("...versus deepmap_search and deepmap_impact", "deepmap_search" in sec and "deepmap_impact" in sec)
    check("...AMBIGUOUS links are unverified", "AMBIGUOUS" in sec and "unverified" in low)
    check("...it is read-only", "read-only" in low)
    check("...and states the vector limit honestly (lists of 256+ floats; no 'never embedding vectors')",
          "256" in low and "never embedding" not in low)
    smoke = SMOKE.read_text(encoding="utf-8")
    check("tests/mcp_http_smoketest.py builds deepmap_cypher's arguments (query + limit)",
          'tool == "deepmap_cypher"' in smoke)


# =====================================================================================================================
def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — deepmap_cypher: read-only Cypher (validator, READ tx, caps, MCP tool, CLI) — no Neo4j")
    print("=" * 72)
    sys.path.insert(0, str(BUILD))
    try:
        CR = _load("cypher_readonly", "dm_cypher_readonly_gates")
    except Exception as exc:  # noqa: BLE001
        check("build/cypher_readonly.py loads", False, f"{type(exc).__name__}: {exc}")
        CR = None
    if CR is not None:
        gate_validator(CR)
        gate_params_and_limit(CR)
        gate_shaping(CR)
        gate_caps(CR)
        gate_run_read_only(CR)
    with tempfile.TemporaryDirectory(prefix="dm_cypher_gates_") as td:
        with _env(**{k: None for k in _ENV_KEYS}, DEEPMAP_HOME=td):
            S = _load("deepmap_search_server", "dm_search_server_cypher_gates")
            if CR is not None:
                gate_mcp_registration(S, CR)
            gate_mcp_wiring_ast()
            if CR is not None and S.mcp._tool_manager.get_tool("deepmap_cypher") is not None:
                gate_mcp_behaviour(S, CR)
                gate_mcp_description(CR)
            elif CR is not None:
                check("deepmap_cypher behaviour (the tool is not registered)", False)
            if CR is not None:
                gate_cli(CR, Path(td))
        gate_skill_and_smoke()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
