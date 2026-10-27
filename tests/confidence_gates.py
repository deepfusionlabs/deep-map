#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the confidence model (the audit wedge, FD-8.5).

Locks: name-resolved CALLS edges are graded INFERRED (one candidate) vs AMBIGUOUS (the callee
name maps to several symbols — uncertain target); nodes are stored with a confidence; and the
retriever returns confidence on every result. Pure: classifier unit-test + source contract grep.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

VALID = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}

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


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_ast_extractor(S) -> None:
    """Pure contract test for the DEFAULT stdlib-`ast` extractor (build/sync.py `_Extractor`) — the
    engine the no-regression oracle was captured on, and (until now) untested offline. Locks node
    typing (Function/Method/Class/Route), Class.method qualification, the rel::name:line key, and
    call/import collection. ast in -> nodes/calls/imports out; no Neo4j, no model."""
    import ast
    print("\n-- ast extractor (forced Python-only path; default is now graphify via auto) --")
    src = (
        "import os\n"
        "from pkg.sub import thing\n"
        "def top():\n"
        "    helper()\n"
        "class Svc:\n"
        "    def run(self):\n"
        "        self.go()\n"
        "    @app.route('/x')\n"
        "    def handler(self):\n"
        "        pass\n"
    )
    ex = S._Extractor("svc.py")
    ex.visit(ast.parse(src))
    labels = {n["name"]: n["label"] for n in ex.nodes}
    check("top-level def -> Function", labels.get("top") == "Function", str(labels))
    check("class -> Class", labels.get("Svc") == "Class", str(labels))
    check("method -> Method, qualified Class.method", labels.get("Svc.run") == "Method", str(labels))
    check("decorated route handler -> Route", labels.get("Svc.handler") == "Route", str(labels))
    check("key is rel::name:line", any(k.startswith("svc.py::top:") for k in (n["key"] for n in ex.nodes)),
          str([n["key"] for n in ex.nodes]))
    callees = {c[1] for c in ex.calls}
    check("collects a plain callee (helper)", "helper" in callees, str(ex.calls))
    check("collects an attribute callee (go)", "go" in callees, str(ex.calls))
    imps = {i[1] for i in ex.imports}
    check("collects 'import os'", "os" in imps, str(ex.imports))
    check("collects 'from pkg.sub import' module", "pkg.sub" in imps, str(ex.imports))
    check("every node carries a body_summary", all(n.get("body_summary") for n in ex.nodes))


def gate_push_resolution(S) -> None:
    """End-to-end name-resolution + edge-confidence in push_to_neo4j (previously untested): a callee
    name resolving to ONE node -> INFERRED; to SEVERAL -> AMBIGUOUS (one edge each); self-edges
    excluded; nodes stored with EXTRACTED. A fake driver captures the Cypher; no Neo4j."""
    print("\n-- push_to_neo4j resolution + confidence --")
    nodes = [
        {"key": "a.py::caller:1", "label": "Function", "name": "caller", "source_file": "a.py", "start_line": 1, "body_summary": "c"},
        {"key": "a.py::foo:5", "label": "Function", "name": "foo", "source_file": "a.py", "start_line": 5, "body_summary": "f"},
        {"key": "b.py::foo:9", "label": "Function", "name": "foo", "source_file": "b.py", "start_line": 9, "body_summary": "g"},
        {"key": "a.py::bar:20", "label": "Function", "name": "bar", "source_file": "a.py", "start_line": 20, "body_summary": "b"},
    ]
    calls = [("a.py::caller:1", "foo"), ("a.py::caller:1", "bar")]  # foo -> 2 targets, bar -> 1
    captured = []

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **p):
            for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one capture per row
                captured.append((cypher, row))
            return []

        def begin_transaction(self):           # the edge rebuild runs inside a write transaction
            return self

        def commit(self):
            pass

    class _Drv:
        def session(self):
            return _Sess()

    n_nodes, n_edges = S.push_to_neo4j(_Drv(), nodes, calls, imports=[("a.py", "os")])
    node_calls = [c for c in captured if "MERGE (x:" in c[0]]
    edge_calls = [c for c in captured if "MERGE (a)-[r:CALLS]->(b)" in c[0]]
    confs = [c[1].get("c") for c in edge_calls]
    check("one MERGE per node (4)", len(node_calls) == 4, str(len(node_calls)))
    check("nodes stored EXTRACTED by default", all(c[1].get("conf") == "EXTRACTED" for c in node_calls), str([c[1].get("conf") for c in node_calls]))
    check("multi-target callee 'foo' -> 2 AMBIGUOUS edges", confs.count("AMBIGUOUS") == 2, str(confs))
    check("single-target callee 'bar' -> 1 INFERRED edge", confs.count("INFERRED") == 1, str(confs))
    check("returned edge count == 3", n_edges == 3, str(n_edges))
    check("returned node count == 4", n_nodes == 4, str(n_nodes))


def gate_push_edge_cases(S) -> None:
    """push_to_neo4j edge-resolution corners: (a) a recursive self-call must NOT create a self-edge
    (the `ck != caller_key` filter); (b) a method called by its SHORT name must resolve to its
    Class.method node (the short-name entry in name_to_keys). Fake driver; no Neo4j."""
    print("\n-- push_to_neo4j: self-edge exclusion + method short-name resolution --")
    nodes = [
        {"key": "a.py::rec:1", "label": "Function", "name": "rec", "source_file": "a.py", "start_line": 1, "body_summary": "r"},
        {"key": "a.py::Svc.run:5", "label": "Method", "name": "Svc.run", "source_file": "a.py", "start_line": 5, "body_summary": "m"},
        {"key": "a.py::caller:9", "label": "Function", "name": "caller", "source_file": "a.py", "start_line": 9, "body_summary": "c"},
    ]
    calls = [("a.py::rec:1", "rec"),       # recursion -> self-edge must be excluded
             ("a.py::caller:9", "run")]    # short name -> must resolve to Svc.run

    captured = []

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **p):
            for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one capture per row
                captured.append((cypher, row))
            return []

        def begin_transaction(self):           # the edge rebuild runs inside a write transaction
            return self

        def commit(self):
            pass

    class _Drv:
        def session(self):
            return _Sess()

    _n, n_edges = S.push_to_neo4j(_Drv(), nodes, calls, imports=[])
    pairs = {(c[1].get("a"), c[1].get("b")) for c in captured if "MERGE (a)-[r:CALLS]->(b)" in c[0]}
    check("recursive self-call creates NO self-edge", ("a.py::rec:1", "a.py::rec:1") not in pairs, str(pairs))
    check("method called by SHORT name resolves to Class.method",
          ("a.py::caller:9", "a.py::Svc.run:5") in pairs, str(pairs))
    check("edge count == 1 (self-edge excluded, short-name resolved)", n_edges == 1, str(n_edges))


def gate_parse_skip(S) -> None:
    """A file that fails to ast.parse must be SKIPPED but LOGGED to stderr (the 'never silent'
    principle, FD-1.9) — not silently dropped — and the good files must still extract. No Neo4j."""
    import io
    import contextlib
    import tempfile
    print("\n-- unparseable file is skipped AND logged (not silent) --")
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "good.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        (dp / "bad.py").write_text("def oops(:\n", encoding="utf-8")   # deliberate syntax error
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nodes, _calls, _imports = S._rebuild_code_ast(dp)
        names = {n["name"] for n in nodes}
        log = err.getvalue()
        check("good file still extracted (alpha)", "alpha" in names, str(names))
        check("unparseable file dropped (no 'oops')", "oops" not in names, str(names))
        check("skip is LOGGED to stderr (not silent)", "skipped" in log and "bad.py" in log, repr(log))


def gate_post_parse_isolation(S) -> None:
    """A POST-PARSE failure on one file (a visitor/summarizer error — e.g. RecursionError on
    pathological nesting that survived ast.parse) must skip ONLY that file, not abort extraction of
    the WHOLE workspace. The per-file try originally wrapped only ast.parse, so a post-parse error
    propagated out of the loop and lost every remaining file (bug-hunt #5). No Neo4j."""
    import io
    import contextlib
    import tempfile
    print("\n-- a POST-PARSE error skips one file, doesn't abort the whole sync --")
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "a_first.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        (dp / "b_second.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        # Inject a post-parse failure on the FIRST file only: summarize() is called per-file AFTER
        # ast.parse (the Module node), so make it raise once, then behave.
        orig = S.summarize.summarize
        state = {"n": 0}

        def boom(*a, **k):
            state["n"] += 1
            if state["n"] == 1:
                raise RecursionError("pathological AST nesting (post-parse)")
            return orig(*a, **k)

        S.summarize.summarize = boom
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                nodes, _c, _i = S._rebuild_code_ast(dp)
            raised = False
        except Exception as exc:  # noqa: BLE001 — current (buggy) code propagates and aborts everything
            raised = True
            nodes = []
        finally:
            S.summarize.summarize = orig
        names = {n["name"] for n in nodes}
        check("a post-parse error does NOT abort the whole extraction (no propagation)", not raised,
              "extraction aborted on a single file's post-parse error")
        check("the OTHER file still extracted (beta survives)", "beta" in names, str(names))
        check("the failed file is skipped atomically (no partial 'alpha')", "alpha" not in names, str(names))
        check("the skip is LOGGED to stderr (not silent)",
              "skipped" in err.getvalue().lower() and "a_first" in err.getvalue(), repr(err.getvalue()))


def gate_extraction_bughunt_fixes(S) -> None:
    """Bug-hunt extraction fixes (2026-06-29), each RED against the prior code:
      #8 Route is classified by a decorator's exact NAME, not a substring of ast.dump
         (`@x.deleter`/`@target_getter` were wrongly flipped to :Route);
      #7 a UTF-8 BOM-prefixed (valid, runnable) file is parsed, not dropped;
      #9 the __pycache__/.venv exclusion matches path COMPONENTS, not a whole-path substring;
      #19 the returned :CALLS count is DISTINCT (idempotent MERGE), not inflated per call-site."""
    import ast
    import contextlib
    import io
    import tempfile
    print("\n-- bug-hunt extraction fixes (#7 BOM / #8 Route-name / #9 path-parts / #19 edge-count) --")

    # #8 — Route by exact decorator NAME, not a substring of the dumped decorator.
    src = (
        "class C:\n"
        "    @property\n"
        "    def x(self): return 1\n"
        "    @x.deleter\n"                 # 'deleter' must NOT be read as 'delete'
        "    def x(self): pass\n"
        "    @app.get('/h')\n"             # a real route (attribute-call decorator)
        "    def handler(self): pass\n"
        "def target_getter(): pass\n"      # 'getter' must NOT be read as 'get'
        "@router.post('/p')\n"             # a real route
        "def create(): pass\n"
    )
    ex = S._Extractor("r.py")
    ex.visit(ast.parse(src))
    cx_labels = [n["label"] for n in ex.nodes if n["name"] == "C.x"]
    lab = {n["name"]: n["label"] for n in ex.nodes}
    check("@property/@x.deleter NOT misclassified Route (was the substring bug)",
          cx_labels and all(l == "Method" for l in cx_labels), str(cx_labels))
    check("@target_getter NOT Route ('getter' contains 'get')", lab.get("target_getter") == "Function", str(lab))
    check("@app.get(...) IS Route", lab.get("C.handler") == "Route", str(lab))
    check("@router.post(...) IS Route", lab.get("create") == "Route", str(lab))

    # #7 — a UTF-8 BOM-prefixed file must parse (CPython runs it fine), not be dropped.
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "bom.py").write_bytes(b"\xef\xbb\xbfdef bommed():\n    return 1\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nodes, _c, _i = S._rebuild_code_ast(dp)
        names = {n["name"] for n in nodes}
        check("BOM-prefixed file parses (function extracted)", "bommed" in names, str(names))
        check("BOM file is NOT skipped as a parse error", "bom.py" not in err.getvalue(), repr(err.getvalue()))

    # #9 — exclusion by path COMPONENT, not substring.
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "my.venv-tools").mkdir()
        (dp / "my.venv-tools" / "keep.py").write_text("def kept(): pass\n", encoding="utf-8")
        (dp / "__pycache__").mkdir()
        (dp / "__pycache__" / "skip.py").write_text("def dropped(): pass\n", encoding="utf-8")
        (dp / ".venv").mkdir()
        (dp / ".venv" / "v.py").write_text("def venvfn(): pass\n", encoding="utf-8")
        names = {n["name"] for n in S._rebuild_code_ast(dp)[0]}
        check("file under 'my.venv-tools' (mere '.venv' substring) is KEPT", "kept" in names, str(names))
        check("file under a real __pycache__ component is dropped", "dropped" not in names, str(names))
        check("file under a real .venv component is dropped", "venvfn" not in names, str(names))

    # #19 — two call-sites to the same callee == ONE edge (idempotent MERGE), counted once.
    nodes = [
        {"key": "a.py::caller:1", "label": "Function", "name": "caller", "source_file": "a.py", "start_line": 1, "body_summary": "c"},
        {"key": "a.py::foo:5", "label": "Function", "name": "foo", "source_file": "a.py", "start_line": 5, "body_summary": "f"},
    ]
    calls = [("a.py::caller:1", "foo"), ("a.py::caller:1", "foo")]  # two sites, same (a,b)

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **p):
            return []

        def begin_transaction(self):
            return self

        def commit(self):
            pass

    class _D:
        def session(self):
            return _S()

    _n, n_edges = S.push_to_neo4j(_D(), nodes, calls, imports=[])
    check("two call-sites to same callee -> ONE distinct edge counted (#19)", n_edges == 1, str(n_edges))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — confidence model gates (no Neo4j)")
    print("=" * 64)

    S = _load("dm_sync", "sync.py")

    print("\n-- edge confidence grading --")
    check("single candidate -> INFERRED", S._edge_confidence(1) == "INFERRED")
    check("two candidates -> AMBIGUOUS", S._edge_confidence(2) == "AMBIGUOUS")
    check("many candidates -> AMBIGUOUS", S._edge_confidence(7) == "AMBIGUOUS")
    for n in range(0, 6):
        check(f"_edge_confidence({n}) is a valid level", S._edge_confidence(n) in VALID)

    gate_ast_extractor(S)
    gate_push_resolution(S)
    gate_push_edge_cases(S)
    gate_parse_skip(S)
    gate_post_parse_isolation(S)
    gate_extraction_bughunt_fixes(S)

    print("\n-- storage + retrieval contract --")
    sync_src = (BUILD / "sync.py").read_text(encoding="utf-8")
    check("push sets node x.confidence", "x.confidence=row.conf" in sync_src)
    check("push sets edge r.confidence", "SET r.confidence=row.c" in sync_src)
    check("node confidence defaults to EXTRACTED", 'n.get("confidence", "EXTRACTED")' in sync_src)

    # BEHAVIORAL contract: drive the REAL retriever._hydrate with a fake driver and assert the FULL
    # documented MCP result shape {id,name,type,file,line,summary,confidence,score} — score injected
    # from the scores map, confidence preserved from the row. This REPLACES the old source-greps
    # ("AS confidence" in ret_src / "confidence" in srv_src) which passed on docstring prose and could
    # NOT go RED if a contract key were dropped/renamed or score-injection broke (bug-hunt #5).
    R = _load("dm_retrieval_conf", "retrieval.py")

    class _HydrateSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            if "unwind $ids" in cypher.lower():
                return iter([{"id": "x", "name": "charge", "type": "Function",
                              "file": "billing/pay.py", "line": 12, "summary": "charge the card",
                              "confidence": "INFERRED"}])
            return iter([])     # the _detect_search_clause probe and anything else -> empty

    class _HydrateDriver:
        def session(self):
            return _HydrateSession()

        def verify_connectivity(self):
            pass

    out = R.DeepMapRetriever(_HydrateDriver(), embed_fn=lambda q: [0.0] * 768)._hydrate(["x"], {"x": 1.23})
    contract = {"id", "name", "type", "file", "line", "summary", "confidence", "score"}
    check("retriever _hydrate returns the FULL documented result contract (behavioral, not a grep)",
          len(out) == 1 and set(out[0]) == contract, str(set(out[0]) if out else None))
    check("_hydrate preserves the node confidence from the row",
          bool(out) and out[0]["confidence"] == "INFERRED", str(out[0].get("confidence") if out else None))
    check("_hydrate injects the fused score from the scores map",
          bool(out) and out[0]["score"] == 1.23, str(out[0].get("score") if out else None))

    # the coalesce DEFAULTS are DB-side Cypher (a mock can't exercise them) — lock them by source:
    ret_src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("hydrate defaults a missing node confidence to EXTRACTED",
          "coalesce(n.confidence, 'EXTRACTED')" in ret_src)
    check("query_callers defaults a missing edge confidence to INFERRED",
          "coalesce(r.confidence, 'INFERRED')" in ret_src)
    skill_src = (Path(__file__).resolve().parent.parent / "skills" / "deep-map" / "SKILL.md").read_text(encoding="utf-8")
    check("skill teaches AMBIGUOUS caveat", "AMBIGUOUS" in skill_src)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
