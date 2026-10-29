#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for `call_line` on impact rows (2026-10 C# field report, item 3): the retrieval / MCP / CLI side.

THE DEFECT: every impact row pointed at the FIRST line of the calling method (`caller.start_line`), never at the
call. graphify records the first call-site line per (caller, callee) pair, and sync now writes it onto the :CALLS
relationship as `r.line` (display only: the MERGE pattern, the grades and the in-degree are unchanged).

THE FIX (build/retrieval.py's impact path; DeepMapRetriever.impact and every ranking function untouched):
  - `_impact_walk` takes `rels[0].line` only when rels[0] (the dependent's own edge on the representative path)
    is :CALLS, keeps it inside the path pick (`best`), and aggregates it with min() grouped by EXACTLY the columns
    RETURN DISTINCT projected before, so the row set (and the 52x3 fan-out de-dup) is unchanged;
  - `query_callers` (the 1-hop fallback) does the same over `r.line`;
  - a row gets `call_line` only when it is a known positive int on a CALLS hop; None is dropped, so an older graph
    answers byte-identically; `line` stays the caller's start line (benchmark/call_linking/score_m.py matches it);
  - the deepmap_impact MCP description says what `line` and `call_line` mean, within the 2,048-char client budget;
    the deepmap_cypher description says CALLS carry `line`;
  - the CLI prints `<file>:<call_line> (call site; caller starts at line <line>)` on a direct (hop 1) row, else
    exactly the old row.
THE REVIEW ROUND (2026-10-28 live check): from hop 2 on, `call_line` is the dependent's call into the NEXT symbol of
the path, not a call to the queried symbol, so "call site" right after `X -> <queried symbol>` invited the wrong
reading. A hop 2+ row now prints `<file>:<call_line> (first call on the path; caller starts at line <line>)`; a hop 1
row (and a 1-hop fallback row, which carries no hop) keeps `(call site; ...)` byte for byte (gate_cli).
Mutation teeth (section M): rels[-1] for rels[0], a kept None, a dropped min() each turn this gate RED, and so does a
CLI that labels every call line one way (hop ignored).

Hermetic: fake drivers that model RETURN DISTINCT + LIMIT; no Neo4j, no network, no spend.
Usage: python tests/impact_call_line_gates.py
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path, PurePath

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


def _load_path(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load(name: str, alias: str):
    return _load_path(BUILD / f"{name}.py", alias)


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
_OLD_DISTINCT = {"caller.name", "labels(caller)[0]", "caller.source_file", "caller.start_line", "callee.name",
                 "best.hop", "best.via", "best.via_path",
                 "CASE best.worstOrd WHEN 0 THEN 'EXTRACTED' WHEN 1 THEN 'INFERRED' ELSE 'AMBIGUOUS' END"}
_OLD_CALLERS_DISTINCT = {"caller.name", "labels(caller)[0]", "caller.source_file", "caller.start_line", "confidence",
                         "callee.name"}


# ---- a fake Neo4j that models RETURN DISTINCT + LIMIT (the impact_limit_honesty model) -----------------------
class _Result:
    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


def _apply_distinct_limit(cypher: str, params: dict, rows: list) -> list:
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
        text = getattr(cypher, "text", cypher)
        d.log.append((text, params))
        if "db.relationshipTypes" in text:
            return _Result([{"types": list(d.present)}])
        if "MATCH path = (caller)" in text:
            if d.walk_error is not None:
                raise d.walk_error
            return _Result(_apply_distinct_limit(text, params, d.walk_rows))
        if "MATCH (caller)-[r:CALLS]->(callee)" in text:
            return _Result(_apply_distinct_limit(text, params, d.callers_rows))
        if "AS matched" in text:
            return _Result([{"matched": d.matched}])
        return _Result([])


class _Driver:
    def __init__(self, walk_rows=(), callers_rows=(), present=("CALLS",), matched=1, walk_error=None):
        self.walk_rows, self.callers_rows, self.present = list(walk_rows), list(callers_rows), present
        self.matched, self.walk_error, self.log = matched, walk_error, []

    def session(self, **kw):
        return _Session(self)

    def verify_connectivity(self):
        pass

    def close(self):
        pass

    def texts(self, needle):
        return [c for c, _ in self.log if needle in c]


def _row(caller, callee="formatTotalPrice", line=3, hop=1, via="CALLS", conf="INFERRED", file=None, **extra):
    r = {"caller": caller, "caller_type": "Method", "file": file or f"src/{caller}.ts", "line": line,
         "confidence": conf, "callee": callee, "hop": hop, "via": via, "via_path": [via] * hop}
    r.update(extra)
    return r


def _crow(caller, callee="formatTotalPrice", line=3, conf="INFERRED", **extra):
    r = {"caller": caller, "caller_type": "Method", "file": f"src/{caller}.ts", "line": line, "confidence": conf,
         "callee": callee}
    r.update(extra)
    return r


def _flat(s: str) -> str:
    return " ".join((s or "").split())


def _split_top(s: str) -> list:
    """Split a projection list at top-level commas (not inside (), [], {} or quotes)."""
    out, depth, cur, quote = [], 0, [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _clauses(cypher: str) -> list:
    """The WITH / RETURN clauses of a flattened query, as (keyword, body) pairs."""
    flat = _flat(cypher)
    parts = re.split(r"\b(WITH|RETURN DISTINCT|RETURN|ORDER BY|MATCH|WHERE|LIMIT)\b", flat)
    return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def _grouping(cypher: str, agg_marker: str):
    """(grouping expressions, aggregate items) of the WITH clause that holds `agg_marker`; None when absent."""
    for kw, body in _clauses(cypher):
        if kw == "WITH" and agg_marker in body:
            items = _split_top(body)
            exprs = [re.sub(r"\s+AS\s+\w+$", "", it).strip() for it in items]
            aggs = [e for e in exprs if e.startswith(("min(", "max(", "collect(", "count("))]
            return {e for e in exprs if e not in aggs}, aggs
    return None


# ---- the structure checks (run on the real module, and on every mutant in section M) ---------------------------
def _walk_text(R) -> str:
    drv = _Driver(walk_rows=[_row("a")])
    with _env(**{k: None for k in _ENV_KEYS}):
        R.query_impact(drv, "formatTotalPrice")
    return _flat((drv.texts("MATCH path = (caller)") or [""])[-1])


def _callers_text(R) -> str:
    drv = _Driver(callers_rows=[_crow("a")])
    R.query_callers(drv, "formatTotalPrice")
    return _flat((drv.texts("MATCH (caller)-[r:CALLS]->(callee)") or [""])[-1])


def walk_checks(R) -> list:
    """[(label, ok, detail)] for the walk's call_line Cypher."""
    w = _walk_text(R)
    res = []
    res.append(("the walk takes rels[0].line only when rels[0] is :CALLS",
                "CASE WHEN type(rels[0]) = 'CALLS' THEN rels[0].line END AS cl" in w, w[:600]))
    res.append(("...never the LAST edge of the path (rels[-1] / last(rels))",
                "rels[-1]" not in w and "last(rels)" not in w, w[:600]))
    pick = w.split("collect(", 1)[0]
    res.append(("the path pick keeps the ORDER BY prefix (hop, worstOrd, viaRank, via) and then cl",
                "ORDER BY hop ASC, worstOrd ASC, viaRank ASC, via ASC, cl ASC" in pick, pick[-300:]))
    res.append(("the call line rides inside the picked path (`best`), so it belongs to the same path as `via`",
                re.search(r"collect\(\{[^}]*\bcl:\s*cl\b[^}]*\}\)\[0\] AS best", w) is not None, w[:900]))
    g = _grouping(w, "min(best.cl)")
    res.append(("the call line is aggregated with min(best.cl)", g is not None and g[1] == ["min(best.cl)"],
                str(g)))
    res.append(("...grouped by EXACTLY the columns RETURN DISTINCT projected before (row set unchanged)",
                g is not None and g[0] == _OLD_DISTINCT, str(g and (g[0] ^ _OLD_DISTINCT))))
    i_d, i_l = w.find("RETURN DISTINCT"), w.rfind("LIMIT $limit + 1")
    res.append(("RETURN DISTINCT still comes before LIMIT $limit + 1", 0 <= i_d < i_l, w[-400:]))
    ret = next((b for k, b in _clauses(w) if k == "RETURN DISTINCT"), "")
    res.append(("RETURN DISTINCT projects call_line beside the old columns",
                all(re.search(rf"\bAS {c}\b|^{c}$|, {c}\b", ret) or f" {c}," in f" {ret},"
                    for c in ("caller", "caller_type", "file", "line", "confidence", "callee", "hop", "via",
                              "via_path", "call_line")), ret))
    res.append(("`line` is still the caller's start line", "caller.start_line" in w and "AS line" in w, w[-500:]))
    return res


def callers_checks(R) -> list:
    c = _callers_text(R)
    res = []
    res.append(("query_callers keeps 'MATCH (caller)-[r:CALLS]->(callee)' and coalesce(r.confidence, 'INFERRED')",
                "MATCH (caller)-[r:CALLS]->(callee)" in c and "coalesce(r.confidence, 'INFERRED')" in c, c))
    g = None
    for kw, body in _clauses(c):
        if kw == "WITH" and "min(" in body:
            items = [re.sub(r"\s+AS\s+\w+$", "", it).strip() for it in _split_top(body)]
            g = ({e for e in items if not e.startswith("min(")}, [e for e in items if e.startswith("min(")])
    res.append(("query_callers aggregates r.line with min()", g is not None and len(g[1]) == 1, str(g)))
    res.append(("...grouped by exactly its old DISTINCT columns", g is not None and g[0] == _OLD_CALLERS_DISTINCT,
                str(g and (g[0] ^ _OLD_CALLERS_DISTINCT))))
    res.append(("...reading r.line", "r.line" in c, c))
    res.append(("...keeping RETURN DISTINCT before LIMIT $limit",
                0 <= c.find("RETURN DISTINCT") < c.rfind("LIMIT $limit"), c[-300:]))
    return res


def behavior_checks(R) -> list:
    """[(label, ok, detail)] for what the rows carry (fake driver)."""
    res = []
    with _env(**{k: None for k in _ENV_KEYS}):
        rows = R.query_impact(_Driver(walk_rows=[_row("Cart.total", call_line=18, line=11)]), "formatTotalPrice")
        res.append(("a CALLS row keeps an int call_line; line stays the caller's start line",
                    list(rows) == [_row("Cart.total", call_line=18, line=11)], str(list(rows))))
        rows = R.query_impact(_Driver(walk_rows=[_row("Cart.total", call_line=None)]), "formatTotalPrice")
        res.append(("call_line None -> the key is dropped: the row is byte-identical to an older graph's",
                    list(rows) == [_row("Cart.total")] and "call_line" not in rows[0], str(list(rows))))
        rows = R.query_callers(_Driver(callers_rows=[_crow("Cart.total", call_line=None)]), "formatTotalPrice")
        res.append(("query_callers drops a None call_line too", rows == [_crow("Cart.total")], str(rows)))
    return res


# ---------------------------------------------------------------------------------------------------------------
def gate_structure(R) -> None:
    print("\n-- the walk and query_callers Cypher carry the first call line without changing the row set --")
    for label, ok, detail in walk_checks(R) + callers_checks(R):
        check(label, ok, detail)


def gate_rows(R) -> None:
    print("\n-- rows: call_line only when known, on a CALLS hop; everything else exactly as before --")
    for label, ok, detail in behavior_checks(R):
        check(label, ok, detail)
    with _env(**{k: None for k in _ENV_KEYS}):
        rows = R.query_impact(_Driver(walk_rows=[_row("Router.get", via="ROUTES_TO", call_line=7)],
                                      present=("CALLS", "ROUTES_TO")), "formatTotalPrice")
        check("a non-CALLS hop never carries call_line (even if a row brought one)",
              list(rows) == [_row("Router.get", via="ROUTES_TO")], str(list(rows)))
        for bad in ("12", True, 0, -3, 1.5):
            rows = R.query_impact(_Driver(walk_rows=[_row("a", call_line=bad)]), "formatTotalPrice")
            check(f"call_line {bad!r} (not a positive int) is dropped", list(rows) == [_row("a")], str(list(rows)))
        two = _row("Cart.total", call_line=30, hop=2, via_path=["CALLS", "CALLS"])
        rows = R.query_impact(_Driver(walk_rows=[two]), "formatTotalPrice")
        check("a 2-hop CALLS dependent keeps its call line (its call into the next link of the path)",
              list(rows) == [two], str(list(rows)))
        raw = [_row(f"caller_{i:02d}", call_line=40 + i) for i in range(52) for _ in range(3)]
        rows = R.query_impact(_Driver(walk_rows=raw, matched=3), "formatTotalPrice", limit=30)
        check("the 52x3 same-name fan-out still returns 30 DISTINCT dependents at limit 30, truncated",
              len(rows) == 30 and len({r["caller"] for r in rows}) == 30 and rows.truncated is True,
              f"{len(rows)} rows")
        check("...each with its own call_line", all(r.get("call_line") == 40 + int(r["caller"][-2:]) for r in rows),
              str([r.get("call_line") for r in rows][:5]))
        rows = R.query_impact(_Driver(walk_rows=raw, matched=3), "formatTotalPrice", limit=100)
        check("...and all 52 at limit 100, not truncated", len(rows) == 52 and rows.truncated is False, str(len(rows)))
        from neo4j.exceptions import ClientError
        callers = [_crow("Cart.total", call_line=18), _crow("Cart.view")]
        drv = _Driver(callers_rows=callers, walk_error=ClientError("variable-length not supported (fake)"))
        rows = R.query_impact(drv, "formatTotalPrice")
        check("the 1-hop fallback (query_callers) returns call_line where known and drops it where not",
              list(rows) == [_crow("Cart.total", call_line=18), _crow("Cart.view")], str(list(rows)))
        check("...through the query_callers Cypher", bool(drv.texts("MATCH (caller)-[r:CALLS]->(callee)")))
    dedup = R._dedup_impact_rows([_row("a", call_line=5), _row("a", call_line=5), _row("a", call_line=6)])
    check("_dedup_impact_rows is unaffected (identical rows collapse, a different call_line is a different row)",
          dedup == [_row("a", call_line=5), _row("a", call_line=6)], str(dedup))
    src = inspect.getsource(R.query_impact) + inspect.getsource(R._impact_rows) + inspect.getsource(R._impact_walk)
    check("the impact path still references no ranking symbol",
          all(t not in src for t in ("_arm_", "_weighted_rrf", "arm_weights")))


def _impact_doc():
    src = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for name in ("deepmap_impact", "deepmap_cypher"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        raw = ast.get_docstring(fn, clean=False) or ""
        lead = ""
        for dec in fn.decorator_list:
            for kw in getattr(dec, "keywords", []):
                if kw.arg == "describe" and isinstance(kw.value, ast.Call) and kw.value.args:
                    lead = kw.value.args[0].value
        out[name] = (raw, inspect.cleandoc(raw), lead)
    return out


def gate_mcp(R) -> None:
    print("\n-- MCP: deepmap_impact describes line / call_line within 2,048 chars and relays call_line --")
    import mcp_bounds as B
    docs = _impact_doc()
    raw, clean, lead = docs["deepmap_impact"]
    flat = _flat(clean)
    check("the description lists call_line among the returned fields", "call_line" in flat, flat[:500])
    check("...and says `line` is where the dependent starts and `call_line` its first call on the path",
          re.search(r"`line` = where the dependent starts", flat) is not None
          and re.search(r"`call_line` = its first call on the path", flat) is not None, flat[:700])
    locked = ("REPLACES the default walk", "not added", "DROPS", "whole default list", "not in the default",
              "multi-hop :CALLS walk", "reduced in depth", "HAS_MANY", "REFERENCES") + tuple(R.DEFAULT_IMPACT_EDGE_TYPES)
    missing = [p for p in locked if p not in flat]
    check("every phrase other gates lock is kept", not missing, f"missing {missing}")
    clause = re.search(r"`line` = [^`]*`call_line` = [^,]*", flat)
    check("no em dash in the new line / call_line clause", clause is not None and "—" not in clause.group(0),
          clause.group(0) if clause else flat[:700])
    name = "payments-reconciliation-service"
    roots = [("Linux", "/home/alexandra.johnson/src/github.com/acme-corp/" + name)]
    if os.name == "nt":
        roots.append(("Windows", "\\".join(["C:", "Users", "alexandra.johnson", "source", "repos", "AcmeCorp", name])))
    for os_name, root in roots:
        d = B.project_tool_describer(lead, env={"DEEPMAP_PROJECT_ROOT": root})(raw)
        head = d.split("\n\n", 1)[0]
        check(f"{os_name} {len(root)}-char path: {len(d)} <= {B.CLIENT_DESCRIPTION_MAX} chars, the compact remedy "
              "kept, the docstring whole", len(d) <= B.CLIENT_DESCRIPTION_MAX and "different project" in head
              and "/deep-map:setup" in head and d.endswith(clean), f"{len(d)} / {head[:200]!r}")
    craw, cclean, _clead = docs["deepmap_cypher"]
    cflat = _flat(cclean)
    check("deepmap_cypher's description says CALLS also carry line (first call site; may be null)",
          "CALLS also carry line (first call site; may be null)" in cflat, cflat[:900])
    check(f"...and stays <= 1,600 chars ({len(cclean)})", len(cclean) <= 1600)
    check("...with no new line starting like an example query (the cypher gate counts those)",
          sum(1 for ln in cclean.splitlines() if ln.strip().startswith(("MATCH ", "CALL ", "OPTIONAL "))) == 3)

    with _env(**{k: None for k in _ENV_KEYS}):
        S = _load("deepmap_search_server", f"dm_search_call_line_{time.monotonic_ns()}")
    ret = R.DeepMapRetriever.__new__(R.DeepMapRetriever)
    S._get_retriever = lambda: ret
    S._touch_last_used = lambda: None
    ret.driver = _Driver(walk_rows=[_row("Cart.total", call_line=18, line=11), _row("Cart.view", call_line=None)])
    with _env(**{k: None for k in _ENV_KEYS}):
        out = S.deepmap_impact("formatTotalPrice")
    check("MCP relays call_line where known and leaves it out where not",
          out == [_row("Cart.total", call_line=18, line=11), _row("Cart.view")], str(out))


def _old_cli_line(r) -> str:
    """The CLI row exactly as it was printed before call_line (the byte-identity reference)."""
    conf, caller = str(r.get("confidence") or ""), str(r.get("caller") or "")
    callee = str(r.get("callee") or "")
    loc = f"{r.get('file') or '?'}:{r.get('line') if r.get('line') is not None else '?'}"
    hop = r.get("hop")
    hoptag = f"{hop}h" if hop is not None else "1h"
    via = str(r.get("via") or "CALLS")
    return f"  [{conf:9s}] {hoptag:>3s} via {via:12s} {caller:32s} -> {callee:22s} {loc}"


@contextlib.contextmanager
def _cli_sandbox():
    """A temp DEEPMAP_HOME and an empty project dir as the cwd, with the impact env scrubbed, so the CLI never sees
    the real ~/.deepmap or this checkout. Yields the temp dir (mutant copies of the CLI go there)."""
    with tempfile.TemporaryDirectory(prefix="dm_call_line_cli_") as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        home.mkdir()
        proj.mkdir()
        scrub = {k: None for k in _ENV_KEYS + ("NEO4J_URI", "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_LEARNING")}
        prev = os.getcwd()
        with _env(DEEPMAP_HOME=str(home), **scrub):
            try:
                os.chdir(str(proj))
                yield Path(td)
            finally:
                os.chdir(prev)          # leave the temp dir before it is removed (Windows refuses otherwise)


def _cli_lines(C, R, drv, symbol="formatTotalPrice"):
    """(exit code, stdout lines) of `deepmap impact <symbol>` run through C.main() against the fake driver."""
    saved_argv, saved_drv, saved_load = sys.argv[:], C._driver, C._load
    orig_load = C._load

    def _load_stub(name):
        if name == "retrieval":
            return R
        if name == "port_registry":
            return types.SimpleNamespace(touch_last_used=lambda *a, **k: None)
        return orig_load(name)
    out, err = io.StringIO(), io.StringIO()
    try:
        C._driver, C._load = (lambda: drv), _load_stub
        sys.argv = ["deepmap", "impact", symbol]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main()
    finally:
        sys.argv, C._driver, C._load = saved_argv, saved_drv, saved_load
    return rc, out.getvalue().splitlines()


_HOP1 = "call site"                    # a direct caller: call_line IS a call to the queried symbol
_PATH = "first call on the path"       # hop 2+: call_line is the dependent's call into the NEXT symbol of the path


def _want(r, what) -> str:
    """The old CLI row of `r` with its location replaced by '<file>:<call_line> (<what>; caller starts at line N)'."""
    old, tail = _old_cli_line(r), f"{r['file']}:{r['line']}"
    return old[: -len(tail)] + f"{r['file']}:{r['call_line']} ({what}; caller starts at line {r['line']})"


def cli_checks(C, R) -> list:
    """[(label, ok, detail)] for the CLI's impact rows (run on the real CLI, and on every CLI mutant in section M)."""
    from neo4j.exceptions import ClientError
    res = []
    one = _row("Cart.total", call_line=18, line=11, file="src/cart.ts")
    without = _row("Cart.view", line=40, file="src/view.ts")
    two = _row("Checkout.place", call_line=30, line=25, hop=2, file="src/checkout.ts")
    three = _row("Shop.run", call_line=9, line=4, hop=3, file="src/shop.ts")
    two_without = _row("Summary.render", line=7, hop=2, file="src/summary.ts")
    rc, lines = _cli_lines(C, R, _Driver(walk_rows=[one, without, two, three, two_without]))
    show = "\n".join(lines[-8:])
    res.append(("a hop 1 row with call_line prints EXACTLY '<file>:<call_line> (call site; caller starts at line N)'",
                rc == 0 and _want(one, _HOP1) in lines, show))
    res.append(("a hop 2 row with call_line prints '<file>:<call_line> (first call on the path; caller starts at "
                "line N)' (its call goes into the next symbol, not into the queried one)",
                rc == 0 and _want(two, _PATH) in lines, show))
    res.append(("...and so does a hop 3 row", rc == 0 and _want(three, _PATH) in lines, show))
    res.append(("...no row from hop 2 on is labelled a call site, and the hop 1 row is not labelled a path call",
                _want(two, _HOP1) not in lines and _want(three, _HOP1) not in lines and _want(one, _PATH) not in lines,
                show))
    res.append(("a hop 1 row without call_line prints EXACTLY the old row (byte-identical)",
                _old_cli_line(without) in lines, show))
    res.append(("...and so does a hop 2 row without call_line", _old_cli_line(two_without) in lines, show))
    crow = _crow("Cart.total", call_line=18, line=11)
    drv = _Driver(callers_rows=[crow, _crow("Cart.view", line=40)],
                  walk_error=ClientError("variable-length not supported (fake)"))
    rc, lines = _cli_lines(C, R, drv)
    show = "\n".join(lines[-8:])
    res.append(("a 1-hop fallback row (query_callers: no hop, a direct caller) prints '(call site; ...)'",
                rc == 0 and _want(crow, _HOP1) in lines, show))
    res.append(("...and a fallback row without call_line prints EXACTLY the old row",
                _old_cli_line(_crow("Cart.view", line=40)) in lines, show))
    return res


def gate_cli(R) -> None:
    print("\n-- CLI: hop 1 '(call site; ...)', hop 2+ '(first call on the path; ...)', else the old row byte for byte --")
    with _cli_sandbox():
        C = _load("deepmap_cli", f"dm_cli_call_line_{time.monotonic_ns()}")
        results = cli_checks(C, R)
    for label, ok, detail in results:
        check(label, ok, detail)


# ---- M: mutation teeth -------------------------------------------------------------------------------------------
_MUTANTS = (
    ("rels[-1] for rels[0] (the LAST edge's line)", "THEN rels[0].line END", "THEN rels[-1].line END", walk_checks),
    ("a None call_line kept", 'row.pop("call_line", None)', "None", behavior_checks),
    ("min() dropped from the walk", "min(best.cl) AS cl", "best.cl AS cl", walk_checks),
    ("min() dropped from query_callers", "min(rl) AS cl", "rl AS cl", callers_checks),
)
# build/deepmap_cli.py's label choice (one expression); each mutant labels every call line one way.
_CLI_LABEL = '"call site" if hop is None or (type(hop) is int and hop == 1) else "first call on the path"'
_CLI_MUTANTS = (
    ("every call line labelled 'call site' (hop ignored)", _CLI_LABEL, '"call site"'),
    ("every call line labelled 'first call on the path' (hop ignored)", _CLI_LABEL, '"first call on the path"'),
    ("a hop of 2 read as a direct caller", "hop == 1) else", "hop <= 2) else"),
)


def gate_mutants(R) -> None:
    print("\n-- M: each mutant of build/retrieval.py and build/deepmap_cli.py turns the checks above RED --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="dm_call_line_mut_") as td:
        for i, (label, old, new, checks) in enumerate(_MUTANTS):
            hits = src.count(old)
            check(f"mutant anchor present exactly once: {old!r}", hits == 1, str(hits))
            if hits != 1:
                continue
            path = Path(td) / f"retrieval_mut{i}.py"
            path.write_bytes(src.replace(old, new).encode("utf-8"))
            with _env(**{k: None for k in _ENV_KEYS}):
                M = _load_path(path, f"dm_retrieval_call_line_mut{i}_{time.monotonic_ns()}")
            try:
                failed = [lbl for lbl, ok, _d in checks(M) if not ok]
            except Exception as exc:  # noqa: BLE001 — a mutant that crashes the checks is caught too
                failed = [f"crashed: {type(exc).__name__}"]
            check(f"mutant '{label}' is caught", bool(failed), "every check still passed")
    csrc = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    with _cli_sandbox() as td:
        for i, (label, old, new) in enumerate(_CLI_MUTANTS):
            hits = csrc.count(old)
            check(f"CLI mutant anchor present exactly once: {old!r}", hits == 1, str(hits))
            if hits != 1:
                continue
            path = td / f"deepmap_cli_mut{i}.py"
            path.write_bytes(csrc.replace(old, new).encode("utf-8"))
            saved_path = sys.path[:]
            try:
                M = _load_path(path, f"dm_cli_call_line_mut{i}_{time.monotonic_ns()}")
            finally:
                sys.path[:] = saved_path        # the copy put its temp dir on sys.path
            M.HERE = BUILD                      # its _load() reads sibling modules from build/, like the real CLI
            try:
                failed = [lbl for lbl, ok, _d in cli_checks(M, R) if not ok]
            except Exception as exc:  # noqa: BLE001 — a mutant that crashes the checks is caught too
                failed = [f"crashed: {type(exc).__name__}"]
            check(f"CLI mutant '{label}' is caught", bool(failed), "every check still passed")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — impact call_line: walk / query_callers Cypher, rows, MCP, CLI, mutants (no Neo4j)")
    print("=" * 72)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    with _env(**{k: None for k in _ENV_KEYS}):
        R = _load("retrieval", f"dm_retrieval_call_line_{time.monotonic_ns()}")
    for gate in (gate_structure, gate_rows, gate_mcp, gate_cli, gate_mutants):
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
