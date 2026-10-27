#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-socket, no-spend gates for the CLI's round-2 fixes (2026-09 live validation, owner W3).

Locks:
  I1  `deepmap impact` prints query_impact's honesty notes (ImpactRows.notes): "not in the graph" vs
      "in the graph, no resolved dependents — NOT proof", "more exist" on truncation, and the reduced-
      depth note — and never the old conflated "Nothing reaches X (symbol not in the graph, or nothing
      depends on it)" line.
  I2  retrieval.ImpactTooExpensive (a 1-hop walk that exhausts Neo4j's transaction memory, directly or
      on the automatic retry) is a friendly `[deepmap] impact: ...` on stderr + exit 3 through the REAL
      main() and the REAL query_impact — never a traceback.
  Q1  `deepmap search` applies mcp_bounds.cap_query (the real name — no getattr soft lookup): a query
      over QUERY_MAX_CHARS reaches the retriever truncated and the note is printed.
  T1  _driver() passes mcp_bounds.neo4j_driver_timeouts() (bounded connect/acquire on a dead Neo4j).
  D1  CLI reads route through port_registry.resolve_neo4j_route: no route (another project owns 7687 /
      unreadable registry) -> refuse BEFORE a driver exists; the LEGACY 7687 route is used only when the
      graph's :_DeepMapMeta owner is this project (or it is unowned + empty); a registry / env route is
      used as is. doctor reports the same verdicts and never shows a foreign graph's census as ours.
  A1  the AuthError remedy is runnable in the state it names: never `instance-rm --yes` for the base
      store or an operator NEO4J_URI; the legacy 7687 route says it is probably another program's.
  S1  D4 freshness: a round-1 manifest (built_at_base_sha only) still detects a moved HEAD; a dirty
      build is worded as "built from uncommitted changes", and is flagged STALE once a sampled dirty
      file changed after the build.
  R1  retrieval_log.project_root prefers the OUTERMOST synced ancestor up to the git toplevel (a stale
      subdir manifest never shadows the root) and a non-git dir is its own project (sync._sync_root);
      an explicit <root> argument maps through the same rule.
  H1  offline hints are runnable (the absolute deepmap_cli.py command, not a bare `deepmap tune`);
      insights on an empty log never says "enable logging" when logging is already on; `tune`
      rejects --min-queries / --min-queries-bucket below 1 (exit 2).
  M1  doctor never reports another project's MCP servers as this project's: with a registry present
      and no pair for this project (and no .mcp.json), it says so instead of probing the base pair.

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers (neo4j.GraphDatabase.driver patched), temp project
dirs; never touches ~/.deepmap or the committed .mcp.json. No sockets.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
HOOKS = REPO / "hooks"
CLI_PATH = BUILD / "deepmap_cli.py"

_PASS = 0
_FAIL = 0

_SCRUB = ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_LEARNING",
          "DEEPMAP_EXTRACTOR", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_APPLY_LEARNED",
          "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_MAX_HOPS", "DEEPMAP_ENRICH",
          "DEEPMAP_MCP_HOST", "DEEPMAP_PYTHON", "NEO4J_PASSWORD", "NEO4J_USER")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_cli():
    return _load_module(CLI_PATH, f"dm_cli_route_{time.time_ns()}")


def _git(*args, cwd: Path):
    git = shutil.which("git")
    return subprocess.run([git, *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
                          encoding="utf-8", errors="replace")


def _git_repo(d: Path) -> str:
    """Init a repo in `d` with one commit; return HEAD (lower-case 40-hex), '' when git is absent."""
    if not shutil.which("git"):
        return ""
    d.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=d)
    _git("config", "user.email", "t@example.invalid", cwd=d)
    _git("config", "user.name", "t", cwd=d)
    (d / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git("add", "-A", cwd=d)
    _git("commit", "-q", "-m", "one", cwd=d)
    return _git("rev-parse", "HEAD", cwd=d).stdout.strip().lower()


@contextlib.contextmanager
def _chdir(d: Path):
    prev = os.getcwd()
    os.chdir(str(d))
    try:
        yield
    finally:
        os.chdir(prev)


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _fake_ml_stack():
    saved = {m: sys.modules.get(m) for m in ("torch", "sentence_transformers")}
    for m in saved:
        sys.modules[m] = types.ModuleType(m)
    try:
        yield
    finally:
        for m, v in saved.items():
            if v is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = v


# ---- fake Neo4j ------------------------------------------------------------------------------------
class _Result(list):
    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **kw):
        text = str(getattr(q, "text", q))
        self.drv.queries.append(text)
        if self.drv.session_exc is not None:
            raise self.drv.session_exc
        return _Result(self.drv.answer(text, kw))


class _GraphDriver:
    """A fake driver over a tiny graph: `owner` (the :_DeepMapMeta owning_root or None) + `nodes`."""

    def __init__(self, owner=None, nodes=7, session_exc=None, answer=None):
        self.owner, self.nodes, self.session_exc = owner, nodes, session_exc
        self.queries: list = []
        self.closed = False
        self._answer = answer

    def answer(self, q, kw):
        if self._answer is not None:
            return self._answer(q, kw)
        if "owning_root" in q:
            return [{"owning_root": self.owner, "r": self.owner}] if self.owner else []
        if "SHOW INDEXES" in q:
            return [{"name": "deepmap_fn_embeddings", "type": "VECTOR", "state": "ONLINE"}]
        if "embedding IS NOT NULL" in q:
            return [{"c": self.nodes}]
        if "count(" in q:
            return [{"c": self.nodes}]
        return []

    def verify_connectivity(self):
        if self.session_exc is not None:
            raise self.session_exc

    def session(self, **kw):
        return _Session(self)

    def close(self):
        self.closed = True


class _Factory:
    """Stand-in for neo4j.GraphDatabase.driver: records every call, returns `make()`."""

    def __init__(self, make):
        self.make = make
        self.calls: list = []
        self.last = None

    def __call__(self, uri, *a, **kw):
        self.calls.append((uri, kw))
        self.last = self.make()
        return self.last


@contextlib.contextmanager
def _patched_graphdb(factory):
    import neo4j
    raw = neo4j.GraphDatabase.__dict__["driver"]
    neo4j.GraphDatabase.driver = factory
    try:
        yield
    finally:
        setattr(neo4j.GraphDatabase, "driver", raw)


def _stub_loader(C, overrides: dict):
    orig = C._load

    def _load(name):
        if name in overrides:
            return overrides[name]
        return orig(name)
    return orig, _load


def _search_stubs(captured: dict):
    """Light stand-ins for the heavy search modules (no model, no Neo4j)."""
    class _Ret:
        def __init__(self, drv, embed_fn=None, arm_weights=None):
            pass

        def search(self, q, top_k=15):
            captured["query"] = q
            return []
    return {
        "embeddings": types.SimpleNamespace(embed_query=lambda q: [0.0]),
        "retrieval": types.SimpleNamespace(DeepMapRetriever=_Ret),
        "learned_fusion": types.SimpleNamespace(resolve_arm_weights_for_live=lambda **k: None),
        "retrieval_log": types.SimpleNamespace(maybe_log_search=lambda *a, **k: None),
        "port_registry": types.SimpleNamespace(touch_last_used=lambda *a, **k: None),
    }


def _main(C, argv, *, overrides=None, driver=None, cwd=None):
    """(rc, raised, stdout, stderr) of C.main() with argv, optional _load overrides + _driver stub."""
    saved_argv, saved_load, saved_driver = sys.argv[:], C._load, C._driver
    out, err = io.StringIO(), io.StringIO()
    rc, raised = None, None
    try:
        if overrides:
            _orig, C._load = _stub_loader(C, overrides)
        if driver is not None:
            C._driver = lambda: driver
        sys.argv = ["deepmap", *argv]
        ctx = _chdir(cwd) if cwd is not None else contextlib.nullcontext()
        with ctx, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main()
    except BaseException as e:  # noqa: BLE001 — a traceback is the failure being gated
        raised = e
    finally:
        sys.argv, C._load, C._driver = saved_argv, saved_load, saved_driver
    return rc, raised, out.getvalue(), err.getvalue()


def _mem_error():
    import neo4j.exceptions as E
    return E.Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.General.MemoryPoolOutOfMemoryError",
        message="The allocation of an extra 2.0 MiB would use more than the limit 358.4 MiB. "
                "Currently using 357.0 MiB. dbms.memory.transaction.total.max threshold reached")


def _impact_driver(*, matched=1, rows=None, walk_exc=None, retry_exc=None, retry_rows=None):
    """A scripted driver for the REAL retrieval.query_impact: the symbol count, the rel-type probe,
    then the walk (1st call) and the 1-hop retry (2nd call)."""
    state = {"walks": 0}

    def answer(q, kw):
        if "AS matched" in q:
            return [{"matched": matched}]
        if "db.relationshipTypes" in q:
            return [{"types": ["CALLS"]}]
        if "MATCH path" in q:
            state["walks"] += 1
            if state["walks"] == 1:
                if walk_exc is not None:
                    raise walk_exc
                return list(rows or [])
            if retry_exc is not None:
                raise retry_exc
            return list(retry_rows or [])
        return []
    return _GraphDriver(answer=answer)


def _row(i, hop=1):
    return {"caller": f"caller{i}", "caller_type": "Function", "file": f"m{i}.py", "line": i,
            "confidence": "INFERRED", "callee": "target", "hop": hop, "via": "CALLS",
            "via_path": ["CALLS"]}


def _write_registry(PR, entries: dict) -> None:
    """entries: {root_path: {field: port}} -> a registry file in the current DEEPMAP_HOME."""
    projects = {}
    for root, fields in entries.items():
        ent = {"root": PR.project_identity(root)}
        ent.update(fields)
        projects[PR.project_key(root)] = ent
    p = PR.registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
                             "projects": projects}), encoding="utf-8")


def _clear_registry(PR) -> None:
    try:
        PR.registry_path().unlink()
    except FileNotFoundError:
        pass


def _write_manifest(root: Path, **fields) -> None:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    data = {"nodes": 7, "calls_edges": 3, "embedded": 7}
    data.update(fields)
    (d / "graph_build.json").write_text(json.dumps(data), encoding="utf-8")


def _line_with(out: str, needle: str) -> str:
    return next((ln for ln in out.splitlines() if needle in ln), "")


def _verdict_of(out: str) -> str:
    for ln in out.splitlines():
        if "[deepmap] verdict:" in ln:
            return ln.split("verdict:", 1)[1].strip()
    return ""


# ---- I1 / I2: impact honesty notes + ImpactTooExpensive through the real retriever ----------------
def gate_impact(C, work: Path) -> None:
    print("\n-- I1/I2: impact prints the retriever's notes; ImpactTooExpensive -> friendly exit 3 --")
    proj = work / "impact"
    proj.mkdir()
    with _env(NEO4J_URI="bolt://localhost:1"):
        # (a) truncated at --limit: 3 dependents exist, --limit 2
        rc, raised, out, err = _main(C, ["impact", "target", "--limit", "2"],
                                     driver=_impact_driver(matched=1, rows=[_row(i) for i in range(3)]),
                                     cwd=proj)
        check("truncated: exit 0, no exception", raised is None and rc == 0, f"{rc} {raised!r}")
        check("truncated: the 'more exist' note is printed", "more exist" in out + err, (out + err)[-300:])
        # (b) not in the graph (matched == 0)
        rc, raised, out, err = _main(C, ["impact", "nosuchsym"], driver=_impact_driver(matched=0), cwd=proj)
        both = out + err
        check("absent symbol: says it is NOT in the graph", "not in the graph" in both, both[-300:])
        check("absent symbol: the conflated 'nothing depends on it' line is gone",
              "nothing depends on it" not in both, both[-300:])
        # (c) in the graph, no resolved dependents
        rc, raised, out, err = _main(C, ["impact", "leaf"], driver=_impact_driver(matched=2, rows=[]),
                                     cwd=proj)
        both = out + err
        check("present, no dependents: 'NOT proof nothing uses it' note is printed", "NOT proof" in both,
              both[-300:])
        check("present, no dependents: never the conflated 'Nothing reaches ... or nothing depends' line",
              "nothing depends on it" not in both, both[-300:])
        # (d) deep walk hits the memory limit, the 1-hop retry answers -> reduced-depth note
        rc, raised, out, err = _main(C, ["impact", "hub"],
                                     driver=_impact_driver(matched=40, walk_exc=_mem_error(),
                                                           retry_rows=[_row(1)]), cwd=proj)
        both = out + err
        check("reduced depth: rows printed + the transaction-memory note", rc == 0 and "caller1" in both
              and "transaction memory" in both, both[-300:])
        # (e) --max-hops 1 walk exhausts memory -> ImpactTooExpensive
        rc, raised, out, err = _main(C, ["impact", "get", "--max-hops", "1"],
                                     driver=_impact_driver(matched=40, walk_exc=_mem_error()), cwd=proj)
        check("ImpactTooExpensive at 1 hop: no exception escapes main()", raised is None, repr(raised))
        check("ImpactTooExpensive at 1 hop: exit 3", rc == 3, repr(rc))
        check("ImpactTooExpensive at 1 hop: '[deepmap] impact:' + 'too expensive' on stderr",
              "[deepmap] impact:" in err and "too expensive" in err, err[-300:])
        check("ImpactTooExpensive: the remedy (Class.method) is shown", "Class.method" in err, err[-300:])
        # (f) the automatic 1-hop retry also exhausts memory
        rc, raised, out, err = _main(C, ["impact", "get"],
                                     driver=_impact_driver(matched=40, walk_exc=_mem_error(),
                                                           retry_exc=_mem_error()), cwd=proj)
        check("ImpactTooExpensive on the automatic retry: exit 3, no traceback",
              raised is None and rc == 3 and "too expensive" in err, f"{rc} {raised!r} {err[-200:]}")


# ---- Q1: the CLI query cap is live -----------------------------------------------------------------
def gate_query_cap(C, work: Path) -> None:
    print("\n-- Q1: `deepmap search` caps an over-long query with mcp_bounds.cap_query --")
    import mcp_bounds
    captured: dict = {}
    long_q = ("parse the configuration file and validate every field " * 120).strip()
    rc, raised, out, err = _main(C, ["search", long_q], overrides=_search_stubs(captured),
                                 driver=_GraphDriver(), cwd=work)
    q = captured.get("query") or ""
    check("a >QUERY_MAX_CHARS query reaches the retriever truncated",
          raised is None and 0 < len(q) <= mcp_bounds.QUERY_MAX_CHARS, f"{len(q)} {raised!r}")
    check("... and the truncation note is printed", "truncated" in err, err[-200:])
    captured.clear()
    rc, raised, out, err = _main(C, ["search", "short query"], overrides=_search_stubs(captured),
                                 driver=_GraphDriver(), cwd=work)
    check("a short query is untouched and silent", captured.get("query") == "short query"
          and "truncated" not in err, f"{captured.get('query')!r} {err[-120:]}")
    src = inspect.getsource(C.cmd_search)
    check("cmd_search calls mcp_bounds.cap_query directly (a rename must fail loudly, no getattr)",
          "cap_query(" in src and "getattr(mcp_bounds" not in src, "")


# ---- T1 + D1: routing + legacy verification for CLI reads -------------------------------------------
def gate_routing(C, PR, work: Path) -> None:
    print("\n-- T1/D1: CLI reads use the shared route; the legacy 7687 route is owner-verified --")
    import mcp_bounds
    a, b = work / "projA", work / "projB"
    a.mkdir()
    b.mkdir()
    ident_b = PR.project_identity(b)

    # registry route: b owns (7689, 7476) -> connect there with bounded timeouts, no owner check.
    _write_registry(PR, {a: {"bolt_port": 7687, "http_port": 7474},
                         b: {"bolt_port": 7689, "http_port": 7476}})
    captured: dict = {}
    fac = _Factory(lambda: _GraphDriver(owner=None, nodes=7))
    with _patched_graphdb(fac):
        rc, raised, out, err = _main(C, ["search", "x"], overrides=_search_stubs(captured), cwd=b)
    check("registry route: the driver targets b's own bolt 7689", bool(fac.calls)
          and fac.calls[0][0] == PR.bolt_uri(7689), str(fac.calls[:1]))   # ROUND-8 J4: bolt://127.0.0.1:7689
    kw = fac.calls[0][1] if fac.calls else {}
    exp = mcp_bounds.neo4j_driver_timeouts()
    check("T1: _driver passes mcp_bounds.neo4j_driver_timeouts() (connect + acquisition bounds)",
          all(kw.get(k) == v for k, v in exp.items()), str({k: kw.get(k) for k in exp}))
    check("registry route: no legacy owner query before the search",
          not any("_DeepMapMeta" in q for q in (fac.last.queries if fac.last else [])),
          str(fac.last.queries if fac.last else None))

    # no route: a owns the base 7687, b has no instance (instance-rm'd / never set up).
    _write_registry(PR, {a: {"bolt_port": 7687, "http_port": 7474}})
    for argv in (["search", "x"], ["impact", "foo"], ["status"]):
        fac = _Factory(lambda: _GraphDriver(owner=str(a)))
        with _patched_graphdb(fac):
            rc, raised, out, err = _main(C, argv, overrides=_search_stubs({}), cwd=b)
        check(f"no route ({argv[0]}): refused with exit 3 and /deep-map:setup, no traceback",
              raised is None and rc == 3 and "/deep-map:setup" in err, f"{rc} {raised!r} {err[-200:]}")
        check(f"no route ({argv[0]}): no driver was ever created (A's graph never queried)",
              not fac.calls, str(fac.calls))

    # unreadable/unrecognised registry -> refuse, no driver.
    PR.registry_path().write_text("{not json", encoding="utf-8")
    fac = _Factory(lambda: _GraphDriver())
    with _patched_graphdb(fac):
        rc, raised, out, err = _main(C, ["search", "x"], overrides=_search_stubs({}), cwd=b)
    check("unreadable registry: refused (exit 3), no driver created", raised is None and rc == 3
          and not fac.calls, f"{rc} {raised!r} {fac.calls}")
    _clear_registry(PR)

    # legacy route (no registry): verify the graph's owner.
    for label, owner, nodes, ok in (("another project's graph", str(a), 7, False),
                                    ("this project's own graph", ident_b, 7, True),
                                    ("an unowned EMPTY graph", None, 0, True),
                                    ("an unowned non-empty graph", None, 7, False)):
        captured = {}
        fac = _Factory(lambda o=owner, n=nodes: _GraphDriver(owner=o, nodes=n))
        with _patched_graphdb(fac):
            rc, raised, out, err = _main(C, ["search", "x"], overrides=_search_stubs(captured), cwd=b)
        if ok:
            check(f"legacy 7687 + {label}: search proceeds (exit 0)",
                  raised is None and rc == 0 and "query" in captured, f"{rc} {raised!r} {err[-200:]}")
        else:
            check(f"legacy 7687 + {label}: refused (exit 3) before searching",
                  raised is None and rc == 3 and "query" not in captured, f"{rc} {raised!r} {err[-200:]}")
            check(f"legacy 7687 + {label}: the refusal names /deep-map:setup",
                  "/deep-map:setup" in err, err[-300:])
            check(f"legacy 7687 + {label}: the driver it opened is closed",
                  fac.last is not None and fac.last.closed, "")
        if label == "another project's graph":
            check("legacy 7687 + another project's graph: the refusal names that owner",
                  str(a) in err or Path(str(a)).name in err, err[-300:])

    # an explicit NEO4J_URI (escape hatch) is used verbatim, no owner check.
    fac = _Factory(lambda: _GraphDriver(owner=str(a)))
    with _env(NEO4J_URI="bolt://example.invalid:7999"), _patched_graphdb(fac):
        captured = {}
        rc, raised, out, err = _main(C, ["search", "x"], overrides=_search_stubs(captured), cwd=b)
    check("env NEO4J_URI: used verbatim, search proceeds", rc == 0 and fac.calls
          and fac.calls[0][0] == "bolt://example.invalid:7999", f"{rc} {fac.calls[:1]} {err[-200:]}")

    # doctor: the same verdicts; never another project's census as ours.
    _write_registry(PR, {a: {"bolt_port": 7687, "http_port": 7474}})
    fac = _Factory(lambda: _GraphDriver(owner=str(a), nodes=99))
    with _patched_graphdb(fac):
        rc, out = _run_doctor(C, cwd=b)
    neo = _line_with(out, "Neo4j")
    check("doctor, no route: FAIL Neo4j line names the missing instance + /deep-map:setup",
          "[FAIL]" in neo and "/deep-map:setup" in neo and "unreachable" not in neo.lower(), neo)
    check("doctor, no route: no driver created, verdict FAILED, exit 1",
          not fac.calls and _verdict_of(out) == "FAILED" and rc == 1, f"{fac.calls} {_verdict_of(out)} {rc}")
    _clear_registry(PR)
    fac = _Factory(lambda: _GraphDriver(owner=str(a), nodes=99))
    with _patched_graphdb(fac):
        rc, out = _run_doctor(C, cwd=b)
    neo = _line_with(out, "Neo4j")
    check("doctor, legacy route + another project's graph: FAIL line names that owner",
          "[FAIL]" in neo and (str(a) in neo or Path(str(a)).name in neo), neo)
    check("doctor, legacy route + another project's graph: its census (99 nodes) is NOT shown as ours",
          "99 nodes" not in out, _line_with(out, "nodes"))
    fac = _Factory(lambda: _GraphDriver(owner=ident_b, nodes=12))
    with _patched_graphdb(fac):
        rc, out = _run_doctor(C, cwd=b)
    check("doctor, legacy route + this project's own graph: Neo4j OK + census shown",
          "[OK  ] Neo4j" in out and "12 nodes" in out, _line_with(out, "Neo4j") + " | " + _line_with(out, "nodes"))

    # escape hatch shared by two projects: the graph belongs to another project -> doctor WARNs.
    fac = _Factory(lambda: _GraphDriver(owner=str(a), nodes=12))
    with _env(NEO4J_URI="bolt://localhost:7999"), _patched_graphdb(fac):
        rc, out = _run_doctor(C, cwd=b)
    own = _line_with(out, "owner")
    check("doctor, env route + a graph owned by another project: owner line WARNs (not OK)",
          own.strip().startswith("[WARN") and "not this project" in own.lower(), own)
    check("doctor, env route + foreign owner: verdict is not READY", _verdict_of(out) != "READY",
          _verdict_of(out))


def _fake_provision(up_ports: set, probed: list):
    ns = types.SimpleNamespace()
    ns.MCP_SERVERS = {"deepmap-search": {"port_env": "DEEPMAP_SEARCH_PORT", "default_port": 8787},
                      "deepmap-embed": {"port_env": "DEEPMAP_EMBED_PORT", "default_port": 8788}}
    ns.mcp_host = lambda: "127.0.0.1"

    def mcp_port(name):
        cfg = ns.MCP_SERVERS[name]
        return int(os.environ.get(cfg["port_env"], cfg["default_port"]))

    ns.mcp_port = mcp_port

    def is_http_up(host, port, timeout=2.0):
        probed.append(int(port))
        return int(port) in up_ports

    ns.is_http_up = is_http_up
    ns._ports_from_mcp_json = lambda root: None
    ns.isolation_note = lambda *a, **k: "isolation: (test stub)"
    ns.print_instance_table = lambda *a, **k: None
    ns._USER_SET_NEO4J_URI = False
    ns.neo4j_edition = lambda drv: "community"
    ns.paths = lambda *a, **k: {"data": Path("/nonexistent/deepmap/neo4j/data")}
    return ns


def _run_doctor(C, *, cwd: Path, provision=None, probed=None):
    orig_load = C._load
    prov = provision or _fake_provision(set(), probed if probed is not None else [])

    def _load(name):
        if name == "provision":
            return prov
        return orig_load(name)

    C._load = _load
    out = io.StringIO()
    rc = None
    try:
        with _chdir(cwd), _fake_ml_stack(), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = C.cmd_doctor(argparse.Namespace())
    finally:
        C._load = orig_load
    return rc, out.getvalue()


# ---- M1: doctor never probes another project's MCP pair as ours -------------------------------------
def gate_doctor_mcp_claims(C, PR, work: Path) -> None:
    print("\n-- M1: doctor does not report ANOTHER project's MCP servers as this project's --")
    a, c = work / "mcpA", work / "mcpC"
    a.mkdir()
    c.mkdir()
    _write_registry(PR, {a: {"search_port": 8787, "embed_port": 8788}})
    probed: list = []
    with _env(NEO4J_URI="bolt://localhost:7999"):
        fac = _Factory(lambda: _GraphDriver(owner=PR.project_identity(c), nodes=5))
        with _patched_graphdb(fac):
            rc, out = _run_doctor(C, cwd=c, provision=_fake_provision({8787, 8788}, probed))
    ln = _line_with(out, "deepmap-search")
    check("unregistered project, base MCP pair claimed by another project: not reported UP as ours",
          "UP" not in ln and "/deep-map:setup" in ln, ln)
    check("... and the verdict is DEGRADED, not READY", _verdict_of(out).startswith("DEGRADED"),
          _verdict_of(out))
    check("... and those ports were never probed as ours", 8787 not in probed and 8788 not in probed,
          str(probed))
    # a registry exists but has NO pair for this project (nobody claims the base pair either): the
    # servers on the base pair are not provably ours -> the provision mcp_route_problem() verdict.
    _write_registry(PR, {a: {"search_port": 8789, "embed_port": 8790}})
    probed = []
    with _env(NEO4J_URI="bolt://localhost:7999"):
        fac = _Factory(lambda: _GraphDriver(owner=PR.project_identity(c), nodes=5))
        with _patched_graphdb(fac):
            rc, out = _run_doctor(C, cwd=c, provision=_fake_provision({8787, 8788}, probed))
    ln = _line_with(out, "deepmap-search")
    check("unregistered project (registry present, base pair unclaimed): no 'UP' claim, names setup",
          "UP" not in ln and "/deep-map:setup" in ln and "no MCP port pair is registered" in ln, ln)
    # no registry at all (a pre-registry single-project install): the base pair is the project's own.
    _clear_registry(PR)
    probed = []
    with _env(NEO4J_URI="bolt://localhost:7999"):
        fac = _Factory(lambda: _GraphDriver(owner=PR.project_identity(c), nodes=5))
        with _patched_graphdb(fac):
            rc, out = _run_doctor(C, cwd=c, provision=_fake_provision({8787, 8788}, probed))
    check("no registry at all: the base pair is probed (legacy single project) and reported UP",
          8787 in probed and "UP" in _line_with(out, "deepmap-search"), _line_with(out, "deepmap-search"))
    _clear_registry(PR)


# ---- A1: runnable auth remedy -------------------------------------------------------------------------
def gate_auth_hint(C, PR, work: Path) -> None:
    print("\n-- A1: the AuthError remedy is runnable for the route in use (D6) --")
    import neo4j.exceptions as E
    auth = E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.Unauthorized",
                                       message="The client is unauthorized due to authentication failure.")
    p = work / "authP"
    p.mkdir()

    def run(**env):
        with _env(**env):
            return _main(C, ["status"], driver=_GraphDriver(session_exc=auth), cwd=p)

    _write_registry(PR, {p: {"bolt_port": 7687, "http_port": 7474}})
    rc, raised, out, err = run()
    check("base store (registry pair 7687): auth failure exit 3, no traceback", raised is None and rc == 3,
          f"{rc} {raised!r}")
    check("base store: does NOT advise `instance-rm` (it refuses the base store)", "instance-rm" not in err,
          err[-400:])
    check("base store: names a remedy that works (move the data dir aside + /deep-map:setup)",
          "data" in err and "/deep-map:setup" in err, err[-400:])

    _write_registry(PR, {p: {"bolt_port": 7689, "http_port": 7476}})
    rc, raised, out, err = run()
    check("per-project instance: advises instance-rm ... --yes + /deep-map:setup",
          "instance-rm" in err and "--yes" in err and "/deep-map:setup" in err, err[-400:])

    rc, raised, out, err = run(NEO4J_URI="bolt://db.example.invalid:7687")
    check("operator NEO4J_URI: no instance-rm; names the NEO4J_URI endpoint's credentials",
          "instance-rm" not in err and "NEO4J_URI" in err, err[-400:])

    _clear_registry(PR)
    fac = _Factory(lambda: _GraphDriver(session_exc=auth))
    with _patched_graphdb(fac):
        rc, raised, out, err = _main(C, ["status"], cwd=p)
    check("legacy 7687 route + auth failure: exit 3, no traceback", raised is None and rc == 3,
          f"{rc} {raised!r} {err[-200:]}")
    check("legacy 7687 route: says it is probably ANOTHER program's Neo4j + /deep-map:setup, no instance-rm",
          "another program" in err and "/deep-map:setup" in err and "instance-rm" not in err, err[-400:])


# ---- S1: D4 freshness wording ------------------------------------------------------------------------
def gate_staleness(C, work: Path) -> None:
    print("\n-- S1: D4 freshness — base-sha fallback, dirty builds, edits after a dirty build --")
    if not shutil.which("git"):
        check("(git on PATH for the freshness cases)", True, "skipped — git absent")
        return
    g = work / "fresh"
    sha1 = _git_repo(g)
    # round-1 manifest: a dirty build recorded only built_at_base_sha; then HEAD moves.
    _write_manifest(g, built_at_base_sha=sha1, built_at_dirty=True)
    (g / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git("add", "-A", cwd=g)
    _git("commit", "-q", "-m", "two", cwd=g)
    head = _git("rev-parse", "HEAD", cwd=g).stdout.strip().lower()
    line = C._staleness_line(g) or ""
    check("built_at_base_sha only + HEAD moved -> STALE (not silent)", "STALE" in line and sha1[:7] in line,
          line)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    _write_manifest(g, built_at_sha=head, dirty=True, built_at_dirty=True, dirty_paths_sample=["a.py"],
                    dirty_paths_count=1, built_at_utc=now)
    line = C._staleness_line(g) or ""
    check("dirty build, HEAD unchanged -> worded as built from uncommitted changes",
          "uncommitted" in line and "fresh as of the last commit" not in line, line)
    _write_manifest(g, built_at_sha=head, dirty=True, built_at_dirty=True, dirty_paths_sample=["a.py"],
                    dirty_paths_count=1, built_at_utc="2000-01-01T00:00:00Z")
    line = C._staleness_line(g) or ""
    check("dirty build + a sampled dirty file edited AFTER the build -> STALE", "STALE" in line, line)
    _write_manifest(g, built_at_sha=head, dirty=False, built_at_dirty=False, dirty_paths_sample=[],
                    dirty_paths_count=0)
    line = C._staleness_line(g) or ""
    check("clean build at HEAD -> fresh", "fresh" in line and "STALE" not in line, line)
    _write_manifest(g, built_at_sha=head, dirty=None, built_at_dirty=None)
    line = C._staleness_line(g) or ""
    check("unknown dirty state at HEAD -> says the work-tree state was unknown", "unknown" in line, line)


# ---- R1: outermost project root ----------------------------------------------------------------------
def gate_project_root(C, work: Path) -> None:
    print("\n-- R1: project_root prefers the OUTERMOST synced ancestor (sync._sync_root parity) --")
    L = _load_module(BUILD / "retrieval_log.py", f"dm_rl_r1_{time.time_ns()}")
    HS = None
    try:
        HS = _load_module(HOOKS / "deepmap_hook_state.py", f"dm_hs_r1_{time.time_ns()}")
    except Exception:
        HS = None
    if shutil.which("git"):
        g = work / "outer"
        _git_repo(g)
        sub = g / "pkg" / "sub"
        sub.mkdir(parents=True)
        _write_manifest(g, verdict="READY")
        _write_manifest(g / "pkg", verdict="FAILED")     # a stale leftover from an old subdir sync
        got = L.project_root(sub)
        check("git repo: a stale subdir manifest does NOT shadow the root's (outermost wins)",
              got is not None and Path(got).resolve() == g.resolve(), str(got))
        if HS is not None and hasattr(HS, "project_root"):
            check("... same answer as the hooks' project_root", Path(HS.project_root(sub)).resolve()
                  == Path(got).resolve(), f"{HS.project_root(sub)} vs {got}")
        # explicit <root> argument maps through the same rule
        r, rc = C._root_arg(argparse.Namespace(root=str(g / "pkg")), "enrich")
        check("explicit <root> inside the repo maps to the synced project root",
              rc is None and r is not None and Path(r).resolve() == g.resolve(), f"{r} {rc}")
    n = work / "nongit"
    (n / "child").mkdir(parents=True)
    _write_manifest(n, verdict="READY")
    got = L.project_root(n / "child")
    check("non-git: a subdirectory is its own project (sync._sync_root: synced as given)",
          got is not None and Path(got).resolve() == (n / "child").resolve(), str(got))
    got = L.project_root(n)
    check("non-git: the synced dir itself resolves to itself", got is not None
          and Path(got).resolve() == n.resolve(), str(got))


# ---- H1: runnable offline hints ----------------------------------------------------------------------
def gate_hints(C, work: Path) -> None:
    print("\n-- H1: offline hints are runnable (absolute deepmap_cli.py command) --")
    proj = work / "hints"
    proj.mkdir()
    IS = _load_module(BUILD / "instance_state.py", f"dm_is_h1_{time.time_ns()}")
    IS.merge_learned_fusion(proj, {"learning_enabled": True})
    LL = _load_module(BUILD / "learning_loop.py", f"dm_ll_h1_{time.time_ns()}")
    st = LL.learning_status(proj, env={})
    reasons = " ".join(str(r) for r in st.get("inactive_reasons") or [])
    check("learning_status 'no tune yet' reason names a runnable command (deepmap_cli.py ... tune)",
          "deepmap_cli.py" in reasons and "`deepmap tune`" not in reasons, reasons)
    _state, detail = LL.format_status_line({"enabled": True, "source": "instance", "log_exists": True,
                                            "n_records": 1, "n_labeled": 0, "target_labeled": 150})
    check("format_status_line 'summarize:' hint is runnable", "summarize:" in detail
          and "deepmap_cli.py" in detail and "summarize: deepmap insights" not in detail, detail[-200:])
    I = _load_module(BUILD / "insights.py", f"dm_in_h1_{time.time_ns()}")
    txt = I.render_text({"counts": {"n_records": 1, "n_search": 1}, "thresholds": {}, "window": {},
                         "sufficient": False, "tuner": {"current": None, "would_be": None}})
    check("insights render: the G4-evidence pointer is runnable (still names `tune --report`)",
          "tune --report" in txt and "deepmap_cli.py" in txt, txt[-200:])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        C.cmd_tune(argparse.Namespace(root=str(proj), status=True))
    check("tune --status 'none yet' hint is runnable", "none yet" in out.getvalue()
          and "deepmap_cli.py" in out.getvalue(), out.getvalue()[-300:])
    # --min-queries / --min-queries-bucket below 1 are rejected (exit 2), not tuned on (#78 remainder)
    for flag in ("--min-queries", "--min-queries-bucket"):
        a = C.build_parser().parse_args(["tune", str(proj), flag, "-3", "--dry-run"])
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = a.fn(a)
        check(f"tune {flag} -3 -> exit 2 with a message (no tune run)", rc == 2 and flag in err.getvalue(),
              f"rc={rc} {err.getvalue().strip()[:160]}")
    # an empty log: "enable logging" only when it is NOT already on (else it sends the user in a circle)
    empty = {"counts": {"n_records": 0}, "thresholds": {}, "window": {}, "sufficient": False}
    txt = I.render_text(dict(empty, root=str(proj)))   # proj has learning ON (instance flag above)
    check("insights, empty log, logging already ON: says so (no circular 'enable logging')",
          "logging is ON" in txt and "enable logging" not in txt, txt[-200:])
    off = work / "hints_off"
    off.mkdir()
    txt = I.render_text(dict(empty, root=str(off)))
    check("insights, empty log, logging OFF: the enable hint is a runnable `learning --enable` command",
          "learning --enable" in txt and "deepmap_cli.py" in txt, txt[-250:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-2 gates (W3: impact notes, query cap, D1 routing, auth remedy, D4)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        home = base / "home"
        home.mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            C = _load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_route_{time.time_ns()}")
            for gate in (lambda: gate_impact(C, work),
                         lambda: gate_query_cap(C, work),
                         lambda: gate_routing(C, PR, work),
                         lambda: gate_doctor_mcp_claims(C, PR, work),
                         lambda: gate_auth_hint(C, PR, work),
                         lambda: gate_staleness(C, work),
                         lambda: gate_project_root(C, work),
                         lambda: gate_hints(C, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
                finally:
                    _clear_registry(PR)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
