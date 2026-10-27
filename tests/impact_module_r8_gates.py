#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for ROUND-8 J6 (W4): deepmap_impact accepts MODULE-qualified names.

THE DEFECT (round-7 live validation, L1): `deepmap_cli.py impact provision.is_bolt_up` and MCP
deepmap_impact {"symbol": "retrieval._weighted_rrf"} both answered "'provision.is_bolt_up' is not in the graph
(no indexed symbol has that name)" — although is_bolt_up (build/provision.py) has 37 dependents and _weighted_rrf
21. A module-qualified name is how a Python/Go/Rust developer spells a function, and "not in the graph" is false
for the function they meant.

THE FIX (impact path only; the ranking path is byte-frozen): when a QUALIFIED name matches no symbol directly, it
is read as <module>.<symbol> — the module being the defining FILE's stem (`provision` -> provision.py; a dotted
package prefix names its directories: `build.provision.is_bolt_up`), else a package DIRECTORY holding the file
(Go `render.WriteJSON` -> render/json.go). The longest module prefix is tried first; the match is case-sensitive
(`Provision.x` is not provision.py). The walk and the one-hop fallback are then restricted to that file, and a
note says how the name was read. A name that still resolves to nothing keeps the "not in the graph" note and adds
"did you mean" candidates for its last segment. A name that matches DIRECTLY is answered exactly as before (the
same Cypher text, no file restriction, no note).

Hermetic: a fake Neo4j session evaluates the callee match (= / ENDS WITH / =~ name_rx / =~ file_rx) in Python.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import types
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


_ENV = dict(DEEPMAP_IMPACT_PROFILE=None, DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_MAX_HOPS=None)

# ---- a tiny graph ----------------------------------------------------------------------------------------
NODES = [
    # (name, label, source_file, line)
    ("is_bolt_up", "Function", "build/provision.py", 100),
    ("setup", "Function", "build/provision.py", 300),
    ("heal", "Function", "build/provision.py", 400),
    ("AuthBackoff.check", "Method", "build/provision.py", 500),
    ("guarded", "Function", "build/provision.py", 520),
    ("_weighted_rrf", "Function", "build/retrieval.py", 434),
    ("DeepMapRetriever.search", "Method", "build/retrieval.py", 1033),
    ("is_bolt_up", "Function", "tests/fake_provision.py", 10),          # same name, another file
    ("test_fake", "Function", "tests/fake_provision.py", 30),
    ("check", "Function", "tests/a_gates.py", 5),
    ("test_a", "Function", "tests/a_gates.py", 20),
    ("WriteJSON", "Function", "render/json.go", 12),                     # Go: package dir 'render', no render.* file
    ("Context.Render", "Method", "gin/context.go", 80),
    ("helper", "Function", "util.py", 3),                                # stem rule ...
    ("helper", "Function", "util/extra.py", 7),                          # ... wins over the dir rule
    ("use_util", "Function", "app.py", 1),
    ("use_extra", "Function", "app.py", 9),
]
EDGES = [
    # (caller name, caller file, callee name, callee file, confidence)
    ("setup", "build/provision.py", "is_bolt_up", "build/provision.py", "INFERRED"),
    ("heal", "build/provision.py", "is_bolt_up", "build/provision.py", "INFERRED"),
    ("test_fake", "tests/fake_provision.py", "is_bolt_up", "tests/fake_provision.py", "INFERRED"),
    ("guarded", "build/provision.py", "AuthBackoff.check", "build/provision.py", "INFERRED"),
    ("test_a", "tests/a_gates.py", "check", "tests/a_gates.py", "INFERRED"),
    ("DeepMapRetriever.search", "build/retrieval.py", "_weighted_rrf", "build/retrieval.py", "INFERRED"),
    ("Context.Render", "gin/context.go", "WriteJSON", "render/json.go", "INFERRED"),
    ("use_util", "app.py", "helper", "util.py", "INFERRED"),
    ("use_extra", "app.py", "helper", "util/extra.py", "INFERRED"),
]


def _node(name, file):
    for n in NODES:
        if n[0] == name and n[2] == file:
            return n
    raise KeyError((name, file))


class _Result:
    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


def _callee_ok(node, cypher: str, p: dict) -> bool:
    nm, file = node[0], node[2]
    name = p.get("name")
    ok = nm == name or nm.endswith("." + str(name)) or bool(p.get("name_rx") and re.fullmatch(p["name_rx"], nm))
    if "$file_rx" in cypher:
        ok = ok and bool(re.fullmatch(p["file_rx"], file))
    return ok


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        d = self.drv
        d.log.append((cypher, dict(p)))
        if "db.relationshipTypes" in cypher:
            return _Result([{"types": ["CALLS"]}])
        if "AS matched" in cypher:
            hits = [n for n in NODES if _callee_ok(n, cypher, p)]
            rec = {"matched": len(hits)}
            if "AS files" in cypher:
                rec["files"] = sorted({n[2] for n in hits})[:3]
            return _Result([rec])
        if "MATCH path = (caller)" in cypher or "MATCH (caller)-[r:CALLS]->(callee)" in cypher:
            if d.walk_error is not None and "MATCH path = (caller)" in cypher:
                raise d.walk_error
            callees = [n for n in NODES if _callee_ok(n, cypher, p)]
            rows = []
            for cn, cf, en, ef, conf in EDGES:
                callee = _node(en, ef)
                if callee in callees:
                    caller = _node(cn, cf)
                    row = {"caller": cn, "caller_type": caller[1], "file": cf, "line": caller[3],
                           "confidence": conf, "callee": en}
                    if "MATCH path = (caller)" in cypher:
                        row.update(hop=1, via="CALLS", via_path=["CALLS"])
                    rows.append(row)
            rows.sort(key=lambda r: (r["caller"], r["file"], r["line"]))
            n = int(p.get("limit", 30)) + (1 if "$limit + 1" in cypher else 0)
            return _Result(rows[:n])
        if "AS suggestion_name" in cypher or ("n.source_file" in cypher and "$last" in cypher):
            last = p.get("last")
            hits = sorted({(n[0], n[2]) for n in NODES if n[0] == last or n[0].endswith("." + str(last))},
                          key=lambda t: (t[1], t[0]))
            lim = int(p.get("cap", 4))
            return _Result([{"suggestion_name": a, "suggestion_file": b} for a, b in hits[:lim]])
        return _Result([])


class _Driver:
    def __init__(self, walk_error=None):
        self.log: list = []
        self.walk_error = walk_error

    def session(self, **kw):
        return _Session(self)


def _impact(R, symbol, limit=30, walk_error=None):
    drv = _Driver(walk_error)
    with _env(**_ENV):
        rows = R.query_impact(drv, symbol, limit)
    return rows, drv


def _callers(rows):
    return sorted((r["caller"], r["file"]) for r in rows)


def gate_module_stem(R) -> None:
    print("\n-- 'provision.is_bolt_up' is read as is_bolt_up in provision.py (was 'not in the graph') --")
    rows, drv = _impact(R, "provision.is_bolt_up")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("dependents are the callers of build/provision.py's is_bolt_up only",
          _callers(rows) == [("heal", "build/provision.py"), ("setup", "build/provision.py")], _callers(rows))
    check("the same-named is_bolt_up in tests/fake_provision.py is NOT mixed in",
          all(r["file"] != "tests/fake_provision.py" for r in rows), _callers(rows))
    check("no 'not in the graph' note", "not in the graph" not in notes, notes[:300])
    check("a note says how the name was read (module 'provision', build/provision.py)",
          "provision" in notes and "is_bolt_up" in notes and "build/provision.py" in notes, notes[:300])
    check("matched counts the resolved symbol (1)", getattr(rows, "matched", None) == 1, getattr(rows, "matched", None))
    check("a module-qualified FUNCTION gets no method caveat", "Callers of a method" not in notes, notes[:300])
    walks = [(c, p) for c, p in drv.log if "MATCH path = (caller)" in c]
    check("the walk is restricted to the resolved file ($file_rx)", walks and all("$file_rx" in c for c, _ in walks),
          [c[:120] for c, _ in walks])
    rows2, _d = _impact(R, "retrieval._weighted_rrf")
    check("'retrieval._weighted_rrf' -> DeepMapRetriever.search (build/retrieval.py)",
          _callers(rows2) == [("DeepMapRetriever.search", "build/retrieval.py")], _callers(rows2))


def gate_package_and_class(R) -> None:
    print("\n-- dotted package prefixes, a module-qualified METHOD, and '::' --")
    rows, _d = _impact(R, "build.provision.is_bolt_up")
    check("'build.provision.is_bolt_up' (package path) resolves to build/provision.py",
          _callers(rows) == [("heal", "build/provision.py"), ("setup", "build/provision.py")], _callers(rows))
    rows_m, _d = _impact(R, "provision.AuthBackoff.check")
    notes_m = " ".join(rows_m.notes)
    check("'provision.AuthBackoff.check' -> the method in provision.py, not tests' check()",
          _callers(rows_m) == [("guarded", "build/provision.py")], _callers(rows_m))
    check("…and a method answer keeps the method caveat", "Callers of a method" in notes_m, notes_m[:200])
    rows_c, _d = _impact(R, "provision::is_bolt_up")
    check("'provision::is_bolt_up' (Rust-style separator) resolves too",
          _callers(rows_c) == [("heal", "build/provision.py"), ("setup", "build/provision.py")], _callers(rows_c))
    rows_h, _d = _impact(R, " provision#is_bolt_up ")
    check("padding and '#' normalise as before (' provision#is_bolt_up ')",
          _callers(rows_h) == [("heal", "build/provision.py"), ("setup", "build/provision.py")], _callers(rows_h))


def gate_dir_rule(R) -> None:
    print("\n-- a package DIRECTORY (Go) when no file has that stem; the stem rule wins when both could --")
    rows, _d = _impact(R, "render.WriteJSON")
    notes = " ".join(rows.notes)
    check("'render.WriteJSON' -> WriteJSON in render/json.go (package directory)",
          _callers(rows) == [("Context.Render", "gin/context.go")], _callers(rows))
    check("…and the note says it was read as a package directory", "render/json.go" in notes and "director" in notes,
          notes[:300])
    rows_u, _d = _impact(R, "util.helper")
    check("'util.helper' -> util.py's helper only (stem rule), not util/extra.py's",
          _callers(rows_u) == [("use_util", "app.py")], _callers(rows_u))


def gate_unresolved(R) -> None:
    print("\n-- still unresolved: 'not in the graph' stays, with did-you-mean candidates --")
    rows, drv = _impact(R, "provison.is_bolt_up")          # typo in the module
    notes = " ".join(rows.notes)
    check("no dependents and matched == 0", list(rows) == [] and rows.matched == 0, (list(rows), rows.matched))
    check("the 'not in the graph' note is kept", "is not in the graph" in notes, notes[:300])
    check("…and it names the candidates for 'is_bolt_up' with their files",
          "build/provision.py" in notes and "tests/fake_provision.py" in notes and "is_bolt_up" in notes, notes[:400])
    check("no walk ran for an unresolved name", not [c for c, _ in drv.log if "MATCH path = (caller)" in c])
    rows_c, _d = _impact(R, "Provision.is_bolt_up")          # case-sensitive module
    check("'Provision.is_bolt_up' is not read as provision.py (case-sensitive)", list(rows_c) == [], _callers(rows_c))
    check("…but the candidates are offered", "build/provision.py" in " ".join(rows_c.notes), rows_c.notes)
    rows_b, drv_b = _impact(R, "no_such_function")
    notes_b = rows_b.notes
    check("a BARE unknown name keeps the exact old note (no candidates query)",
          notes_b == ["'no_such_function' is not in the graph (no indexed symbol has that name) — check the spelling "
                      "or try the bare method name; if its language or directory is not indexed, search the source "
                      "with Grep instead."], notes_b)
    check("…and issues only the one count query", len(drv_b.log) == 1, [c[:80] for c, _ in drv_b.log])
    rows_z, _d = _impact(R, "nothing.at_all")
    check("a qualified name whose last segment exists nowhere: the plain 'not in the graph' note",
          len(rows_z.notes) == 1 and "is not in the graph" in rows_z.notes[0] and "Did you mean" not in rows_z.notes[0],
          rows_z.notes)


def gate_direct_unchanged(R) -> None:
    print("\n-- a name that matches DIRECTLY is answered exactly as before --")
    for sym in ("AuthBackoff.check", "is_bolt_up", ".check", "Context#Render"):
        rows, drv = _impact(R, sym)
        check(f"{sym!r}: no query carries a file restriction", not any("file_rx" in c or "file_rx" in p
                                                                       for c, p in drv.log))
        check(f"{sym!r}: every callee match is the unchanged _CALLEE_MATCH text",
              all(c.startswith(R._CALLEE_MATCH) for c, _p in drv.log if "callee" in c and "MATCH (callee)" in c))
        check(f"{sym!r}: no module-reading note", not any("read as" in n for n in rows.notes), rows.notes)
    check("_CALLEE_MATCH is the frozen text",
          R._CALLEE_MATCH == ("MATCH (callee) WHERE callee.name = $name OR callee.name ENDS WITH '.' + $name "
                              "OR callee.name =~ $name_rx "), R._CALLEE_MATCH)
    rows, _d = _impact(R, "is_bolt_up")
    check("bare 'is_bolt_up' still spans every file (3 callers)", len(rows) == 3, _callers(rows))


def gate_fallback_one_hop(R) -> None:
    print("\n-- the one-hop fallback (unsupported multi-hop) keeps the module restriction --")
    from neo4j.exceptions import ClientError
    err = ClientError("Unsupported var-length feature (fake)")
    rows, drv = _impact(R, "provision.is_bolt_up", walk_error=err)
    notes = " ".join(rows.notes)
    check("fallback rows are provision.py's callers only",
          _callers(rows) == [("heal", "build/provision.py"), ("setup", "build/provision.py")], _callers(rows))
    check("…with the 1-hop note and the module-reading note", "1 hop" in notes and "build/provision.py" in notes,
          notes[:300])
    one_hop = [c for c, _ in drv.log if "MATCH (caller)-[r:CALLS]->(callee)" in c]
    check("the one-hop query carries the file restriction", one_hop and all("$file_rx" in c for c in one_hop), one_hop)
    direct = R.query_callers(_Driver(), "is_bolt_up", 30)
    check("query_callers without in_file is unchanged (all 3 callers)", len(direct) == 3, direct)


def gate_bounded(R) -> None:
    print("\n-- bounded work: a long dotted name tries a capped number of readings --")
    rows, drv = _impact(R, ".".join(f"p{i}" for i in range(30)) + ".is_bolt_up")
    counts = [c for c, _ in drv.log if "AS matched" in c]
    check("at most 1 direct + 2x6 module count queries", len(counts) <= 13, len(counts))
    check("…and the answer is the honest 'not in the graph' + candidates", "is not in the graph" in " ".join(rows.notes))


def gate_mcp(R) -> None:
    print("\n-- MCP deepmap_impact returns the dependents + the reading note --")
    import neo4j
    saved_gdb, saved_emb = neo4j.GraphDatabase, sys.modules.get("embeddings")

    class _GDB:
        @staticmethod
        def driver(*a, **kw):
            d = _Driver()
            d.verify_connectivity = lambda: None
            d.close = lambda: None
            return d

    emb = types.ModuleType("embeddings")
    emb.ModelUnavailable = type("ModelUnavailable", (RuntimeError,), {})
    emb.ensure_model = lambda max_wait_s=None, retry_after_s=None: object()
    emb.embed_query = lambda text: [0.01] * 768
    neo4j.GraphDatabase = _GDB
    sys.modules["embeddings"] = emb
    try:
        with _env(NEO4J_URI="bolt://127.0.0.1:17999", DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None,
                  DEEPMAP_MCP_WARMUP="0", **_ENV):
            spec = importlib.util.spec_from_file_location(f"dm_srv_mod_r8_{time.monotonic_ns()}",
                                                          BUILD / "deepmap_search_server.py")
            srv = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(srv)
            import anyio
            from mcp.shared.memory import create_connected_server_and_client_session
            out = {}

            async def go():
                async with create_connected_server_and_client_session(srv.mcp) as client:
                    res = await client.call_tool("deepmap_impact", {"symbol": "provision.is_bolt_up"})
                    out["err"] = res.isError
                    sc = res.structuredContent
                    out["payload"] = sc["result"] if isinstance(sc, dict) and "result" in sc else [
                        json.loads(c.text) for c in res.content if getattr(c, "text", None)]
            anyio.run(go)
    finally:
        neo4j.GraphDatabase = saved_gdb
        if saved_emb is None:
            sys.modules.pop("embeddings", None)
        else:
            sys.modules["embeddings"] = saved_emb
    payload = out.get("payload") or []
    rows = [p for p in payload if isinstance(p, dict) and "caller" in p]
    notes = [p["note"] for p in payload if isinstance(p, dict) and "note" in p]
    check("the MCP call succeeds", out.get("err") is False, out)
    check("…with provision.py's two callers", sorted(r["caller"] for r in rows) == ["heal", "setup"], rows)
    check("…and a trailing note naming the reading", any("build/provision.py" in n for n in notes), notes)


_FROZEN = ("_weighted_rrf", "_candidate_floor", "DEFAULT_ARM_WEIGHTS", "RRF_K", "VECTOR_INDEXES")
_FROZEN_METHODS = ("__init__", "search", "_hydrate", "_embed_or_none", "_arm_vector", "_arm_vector_search",
                   "_arm_vector_procedure", "_arm_fulltext", "_arm_graph_weighted", "_arm_graph_weighted_one")


def _frozen_dump(src: str) -> dict:
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _FROZEN:
            out[node.name] = ast.dump(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgt = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(tgt, ast.Name) and tgt.id in _FROZEN:
                out[tgt.id] = ast.dump(node)
        elif isinstance(node, ast.ClassDef) and node.name == "DeepMapRetriever":
            for m in node.body:
                if isinstance(m, ast.FunctionDef) and m.name in _FROZEN_METHODS:
                    out[f"DeepMapRetriever.{m.name}"] = ast.dump(m)
    return out


def gate_ranking_frozen() -> None:
    print("\n-- the ranking path is byte-frozen (AST-identical to HEAD) --")
    try:
        head = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:build/retrieval.py"], capture_output=True,
                              timeout=60).stdout.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"  [SKIP] git unavailable ({type(exc).__name__}) — frozen-path comparison skipped")
        return
    if not head.strip():
        print("  [SKIP] HEAD:build/retrieval.py unreadable — frozen-path comparison skipped")
        return
    now = _frozen_dump((BUILD / "retrieval.py").read_text(encoding="utf-8"))
    was = _frozen_dump(head)
    check("every frozen ranking symbol is present", set(was) == set(now) and len(now) == 15, sorted(set(was) ^ set(now)))
    diff = [k for k in was if was.get(k) != now.get(k)]
    check("…and AST-identical to HEAD", not diff, diff)


def main() -> int:
    print("impact_module_r8_gates — ROUND-8 J6: module-qualified impact names")
    R = _load("retrieval", "dm_retrieval_mod_r8")
    gate_module_stem(R)
    gate_package_and_class(R)
    gate_dir_rule(R)
    gate_unresolved(R)
    gate_direct_unchanged(R)
    gate_fallback_one_hop(R)
    gate_bounded(R)
    gate_mcp(R)
    gate_ranking_frozen()
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
