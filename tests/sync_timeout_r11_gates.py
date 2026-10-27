#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for round 11 / M1 (+ the optional M5): the graphify time budget and the sync exit contract.

The defect (Phase M, fastapi on a loaded box): `graphify update` ran under a HARD-CODED 600 s timeout
(sync._rebuild_code -> graphify_extract.extract_with_report, no override). fastapi's 1,129 .py files needed
522-622 s under load, so two re-syncs timed out, fell back to ast, and the engine-downgrade guard refused the
push ("SKIPPED ... Nothing was pushed", verdict DEGRADED) — and `deepmap_cli.py sync` STILL EXITED 0, so
/deep-map:refresh, the git hook and scripts read success over a stale graph.

  A  the engine's time budget: DEEPMAP_GRAPHIFY_TIMEOUT (seconds; validated, clamped to [60, 86400]) overrides a
     SIZE-AWARE default (max(600, 1.5 s x the source files the engine will parse), capped at 4 h); the count walks
     like the engine (its own skip rule), case-insensitively, and stops at the cap; extract_with_report hands the
     resolved budget to the engine; a timeout is reported as a timeout whose cause NAMES the variable (inside the
     200 characters sync quotes), not as a dumped command line.
  B  sync: the timeout flag reaches the extraction info; the engine-downgrade message says to raise the budget
     (not "fix the graphify install"); every run that pushes nothing over a kept graph (engine downgrade, instance
     lock held, per-root lock held with no re-run queued) sets sync.last_not_pushed; sync.py's own _main exits 4
     for it (FAILED keeps 1, DEGRADED-with-a-push keeps 0).
  C  `deepmap_cli.py sync` exits SYNC_NOT_PUSHED_RC (4) when the push was skipped, keeps 1 for FAILED / a refused
     sync, 0 for READY and DEGRADED-with-a-push; `sync --help` documents every exit code.
  D  provision setup: a sync that kept the previous graph still brings the tools up over it, but setup's banner says
     "setup verdict: DEGRADED — the graph was NOT rebuilt" and `provision.py setup` exits 4 (never a plain 0);
     heal still revives the kept graph (its manifest is the previous build's).
  E  the git post-commit block: a sync that exits 4 never blocks or fails a commit (it is detached and `|| true`).
  F  installers: provision exit 4 is a WARNING (keep going, the hook is still installed); any other non-zero stops.
     clone_graph build fails CLOSED on it (a stale graph is never measured).
  G  verdict: an auto->ast fallback caused by a timeout names DEEPMAP_GRAPHIFY_TIMEOUT as the remedy.
  H  docs: commands/refresh.md and commands/setup.md explain exit 4 and the timeout variable.
  M5 DEEPMAP_GRAPHIFY_CALLS_PLUS (default OFF): an alias imported inside a module-level try/except
     (`try: import deepmap_hook_state as _hs`) resolves like a plain module-level alias; a name the try and the
     except bind to DIFFERENT modules resolves to neither; with the flag off _calls_plus never runs (the default
     graph is untouched).

Hermetic: temp dirs, fake drivers, stubbed engine / index / embedding stages, no sockets, no Neo4j. The E and F
behavioral checks need git + a POSIX sh / PowerShell and are skipped (visibly) where those are missing.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_CREATED: list = []          # every temp dir THIS gate made — removed by exact path at the end


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")


def _mkdtemp(prefix: str) -> Path:
    p = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    _CREATED.append(p)
    return p


def _rmtree(path) -> None:
    """Delete a dir THIS gate created (exact path) — git's read-only object files included."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    if path and Path(path).exists():
        shutil.rmtree(str(path), onerror=onerr)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_r11m1_{name}_{time.time_ns()}", BUILD / f"{name}.py")
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
def _patch(obj, **attrs):
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


def _captured(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def _write(root: Path, rel: str, text: str = "def f():\n    pass\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# =====================================================================================================
# A — the engine's time budget
# =====================================================================================================
MEASURED_WORST_S = 622        # fastapi, 1,129 .py files, CPU-saturated box (the round-11 M1 evidence)
FASTAPI_FILES = 1129


def gate_a_budget() -> None:
    print("\n-- A: DEEPMAP_GRAPHIFY_TIMEOUT + a size-aware default (was a hard-coded 600 s) --")
    check("graphify_extract.ENV_GRAPHIFY_TIMEOUT == 'DEEPMAP_GRAPHIFY_TIMEOUT'",
          getattr(GX, "ENV_GRAPHIFY_TIMEOUT", None) == "DEEPMAP_GRAPHIFY_TIMEOUT")
    fn = getattr(GX, "graphify_timeout", None)
    check("graphify_extract.graphify_timeout() exists", callable(fn))
    if not callable(fn):
        return
    with _env(DEEPMAP_GRAPHIFY_TIMEOUT=None):
        small = fn(n_files=10)
        zero = fn(n_files=0)
        fa = fn(n_files=FASTAPI_FILES)
        big = fn(n_files=10_000_000)
        two_k = fn(n_files=2000)
    check("a small repo keeps the old 600 s budget (the floor; small repos unchanged)",
          small.get("timeout_s") == 600 and small.get("source") == "size-aware", small)
    check("an uncountable / empty tree keeps 600 s", zero.get("timeout_s") == 600, zero)
    check(f"fastapi-size ({FASTAPI_FILES} source files) gets >= 2.5x the {MEASURED_WORST_S} s measured under load",
          int(fa.get("timeout_s") or 0) >= 2.5 * MEASURED_WORST_S, fa)
    check("... exactly max(600, ceil(1.5 s x files)) = 1694 s", fa.get("timeout_s") == 1694, fa)
    check("the default grows with the file count (2000 files > 1129 files)",
          int(two_k.get("timeout_s") or 0) > int(fa.get("timeout_s") or 0), (two_k, fa))
    check("the size-aware default is capped (a hung engine on a huge repo still ends): 4 h",
          big.get("timeout_s") == 14400, big)
    check("the result records the file count it was sized from", fa.get("source_files") == FASTAPI_FILES, fa)

    cases = [("2400", 2400, False), ("90.5", 91, False), ("10", 60, True), ("999999", 86400, True)]
    for raw, want, warned in cases:
        with _env(DEEPMAP_GRAPHIFY_TIMEOUT=raw):
            r = fn(n_files=FASTAPI_FILES)
        check(f"DEEPMAP_GRAPHIFY_TIMEOUT={raw} -> {want} s (source env{', clamped with a warning' if warned else ''})",
              r.get("timeout_s") == want and r.get("source") == "env" and bool(r.get("problem")) == warned
              and (not warned or "DEEPMAP_GRAPHIFY_TIMEOUT" in str(r.get("problem"))), r)
    for raw in ("abc", "0", "-5", "nan", "inf", "1e999"):
        with _env(DEEPMAP_GRAPHIFY_TIMEOUT=raw):
            r = fn(n_files=FASTAPI_FILES)
        check(f"an invalid DEEPMAP_GRAPHIFY_TIMEOUT={raw!r} is ignored with a warning naming it -> the size-aware "
              f"default", r.get("timeout_s") == 1694 and r.get("source") == "size-aware"
              and "DEEPMAP_GRAPHIFY_TIMEOUT" in str(r.get("problem") or ""), r)
    with _env(DEEPMAP_GRAPHIFY_TIMEOUT="   "):
        r = fn(n_files=10)
    check("an empty / blank value is 'unset' (no warning)", r.get("timeout_s") == 600 and not r.get("problem"), r)

    # the count: files the engine will parse — DeepMap's source extensions, any case; the engine's skipped dirs out
    cnt = getattr(GX, "engine_source_file_count", None)
    check("graphify_extract.engine_source_file_count() exists", callable(cnt))
    if not callable(cnt):
        return
    root = _mkdtemp("dm_r11_cnt_")
    for rel in ("a.py", "pkg/b.PY", "web/c.ts", "docs/readme.md", "data.json", "node_modules/x/d.js",
                ".git/e.py", "pkg/__pycache__/f.py", ".venv/lib/g.py", "build/h.py", "notes.txt"):
        _write(root, rel)
    n = cnt(root)
    check("counts a.py + pkg/b.PY (case-insensitive) + web/c.ts; not .md/.json/.txt, not node_modules/.git/"
          "__pycache__/.venv/build (dirs the engine never walks)", n == 3, n)
    check("... and stops at `limit` (the walk is bounded by the default's cap)", cnt(root, limit=2) == 2,
          cnt(root, limit=2))
    check("a missing root counts 0 (never raises)", cnt(root / "nope") == 0)


def _engine_env(home: Path):
    return _env(DEEPMAP_HOME=str(home), DEEPMAP_RESCUE_SKIPPED_DIRS="0", DEEPMAP_CASE_INSENSITIVE_EXT="0",
                DEEPMAP_GRAPHIFY_TIMEOUT=None)


def _run_extract(root: Path, engine, **kw):
    """extract_with_report with the engine child faked by `engine(cmd, cwd, env, timeout)` (and graphify reported
    installed + supported, so the run gets as far as the engine on any box). Returns (result, report, stderr)."""
    real_find = importlib.util.find_spec

    def find_spec(name, *a, **k):
        if name == "graphify":
            return SimpleNamespace(name="graphify")
        return real_find(name, *a, **k)

    with _patch(importlib.util, find_spec=find_spec), \
            _patch(GX, _run_engine=lambda cmd, *, cwd, env, timeout: engine(cmd, cwd, env, timeout),
                   graphify_support=lambda: (True, "0.8.39")):
        (res, report), err = _captured(GX.extract_with_report, root, **kw)
    return res, report, err


def gate_a_wiring() -> None:
    print("\n-- A: extract_with_report hands the resolved budget to the engine; a timeout names the variable --")
    home = _mkdtemp("dm_r11_home_")
    root = _mkdtemp("dm_r11_eng_")
    for rel in ("a.py", "b.py", "pkg/c.py"):
        _write(root, rel)
    seen: dict = {}

    def engine_no_graph(cmd, cwd, env, timeout):
        seen["timeout"] = timeout
        return GX._EngineResult(cmd, 1, "", "")

    with _engine_env(home):
        _res, report, _err = _run_extract(root, engine_no_graph)
    check("no override, 3 source files -> the engine gets 600 s", seen.get("timeout") == 600, seen)
    check("... the report records the budget, its source and the count",
          report.get("timeout_s") == 600 and report.get("timeout_source") == "size-aware"
          and report.get("source_files") == 3, {k: report.get(k) for k in ("timeout_s", "timeout_source",
                                                                           "source_files")})
    with _engine_env(home), _env(DEEPMAP_GRAPHIFY_TIMEOUT="1234"):
        _res, report, _err = _run_extract(root, engine_no_graph)
    check("DEEPMAP_GRAPHIFY_TIMEOUT=1234 reaches the engine (it was always 600)", seen.get("timeout") == 1234, seen)
    check("... recorded as the env's", report.get("timeout_source") == "env", report.get("timeout_source"))
    with _engine_env(home), _patch(GX, engine_source_file_count=lambda r, limit=None: FASTAPI_FILES):
        _res, report, _err = _run_extract(root, engine_no_graph)
    check(f"a {FASTAPI_FILES}-file tree -> the engine gets the size-aware 1694 s", seen.get("timeout") == 1694, seen)
    with _engine_env(home), _env(DEEPMAP_GRAPHIFY_TIMEOUT="1234"):
        _res, report, _err = _run_extract(root, engine_no_graph, timeout=7)
    check("an explicit timeout= argument still wins (callers / tests)", seen.get("timeout") == 7, seen)
    with _engine_env(home), _env(DEEPMAP_GRAPHIFY_TIMEOUT="soon"):
        _res, report, err = _run_extract(root, engine_no_graph)
    check("an invalid override is ignored with a warning on stderr that names it",
          seen.get("timeout") == 600 and "DEEPMAP_GRAPHIFY_TIMEOUT" in err, err[-400:])

    def engine_timeout(cmd, cwd, env, timeout):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    with _engine_env(home):
        res, report, err = _run_extract(root, engine_timeout)
    cause = str(report.get("cause") or "")
    check("a timeout -> no result (sync falls back), report['timed_out'] is True",
          res is None and report.get("timed_out") is True, report)
    check("... the cause names DEEPMAP_GRAPHIFY_TIMEOUT inside the 200 characters sync quotes",
          "DEEPMAP_GRAPHIFY_TIMEOUT" in cause[:200], cause)
    check("... and says it timed out after the budget used (600 s)", "timed out" in cause and "600" in cause, cause)
    check("... without dumping the engine's command line", "--no-cluster" not in cause, cause)
    check("... the stderr line names the variable too", "DEEPMAP_GRAPHIFY_TIMEOUT" in err, err[-400:])

    def engine_ok(cmd, cwd, env, timeout):
        return GX._EngineResult(cmd, 0, "", "")

    with _engine_env(home):
        res, report, _err = _run_extract(root, engine_ok)
    check("a normal (non-timeout) failure is NOT flagged timed_out", not report.get("timed_out"), report)


# =====================================================================================================
# B — sync: the timeout reaches the info, the downgrade message, last_not_pushed, sync.py _main
# =====================================================================================================
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class _WSess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.stmts.append(cypher)
        return _Res()


class _WDrv:
    def __init__(self):
        self.stmts = []

    def session(self, *a, **k):
        return _WSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _node(key="a.py::f:1", name="f", sf="a.py"):
    return {"key": key, "label": "Function", "name": name, "source_file": sf, "start_line": 1,
            "body_summary": "f", "documented": False}


def _wire(S, nodes, engine, info, order):
    Ext = S._Extraction

    def _rebuild(root):
        order.append("extract")
        return Ext([dict(n) for n in nodes], [], [], engine, dict(info))

    def _push(driver, n, c, i, typed_edges=None):
        order.append("push")
        return (len(n), 0)

    S._rebuild_code = _rebuild
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = _push
    S._prune_blast_radius = lambda d, live: (0, len(nodes))
    S._cleanup_stale = lambda d, live: order.append("prune")
    S._graph_has_nodes = lambda d: True
    S._graph_node_count = lambda d: len(nodes)
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(nodes)
    S._maybe_auto_tune = lambda root: None
    return order


def _proj(base: Path, name: str, manifest=None) -> Path:
    root = base / name
    root.mkdir()
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    _write(root, "a.py")
    if manifest is not None:
        (root / ".deepmap").mkdir(parents=True, exist_ok=True)
        (root / ".deepmap" / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


_TIMEOUT_CAUSE = ("graphify timed out after 600 s (the size-aware default for 1129 source files) and was stopped — "
                  "set DEEPMAP_GRAPHIFY_TIMEOUT=<seconds> to allow it longer")
_PRIOR = {"extractor": "graphify", "graph_engines": ["graphify"], "verdict": "READY", "nodes": 50}


def gate_b_sync() -> None:
    print("\n-- B: sync — the timeout reaches the info + the downgrade message; last_not_pushed; sync.py _main --")
    base = _mkdtemp("dm_r11_sync_")

    # B1 — _rebuild_code carries the timeout flag into the extraction info (and the cause as the fallback reason)
    S = _load("sync")
    S.graphify_extract = SimpleNamespace(extract_with_report=lambda root: (
        None, {"cause": _TIMEOUT_CAUSE, "timed_out": True, "timeout_s": 600}))
    S._rebuild_code_ast = lambda root: ([_node()], [], [])
    root = _proj(base, "b1")
    with _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_INCREMENTAL_SYNC=None):
        ext, _out = _captured(S._rebuild_code, root)
    info = getattr(ext, "info", {}) or {}
    check("an auto run whose engine timed out falls back to ast with the cause as the fallback reason",
          ext[3] == "ast" and "DEEPMAP_GRAPHIFY_TIMEOUT" in str(info.get("fallback_reason")), info)
    check("... and the extraction info says it TIMED OUT (with the budget)",
          info.get("timed_out") is True and info.get("timeout_s") == 600, info)

    # B2 — the downgrade message: a timeout is not a broken install
    S = _load("sync")
    msg_t = S._downgrade_message({"requested": "auto", "resolved": "ast", "fallback_reason": _TIMEOUT_CAUSE,
                                  "timed_out": True, "timeout_s": 600})
    msg_c = S._downgrade_message({"requested": "auto", "resolved": "ast", "fallback_reason": "graphify crashed"})
    check("a timeout downgrade tells the user to raise DEEPMAP_GRAPHIFY_TIMEOUT (with a concrete larger value)",
          "DEEPMAP_GRAPHIFY_TIMEOUT=1200" in msg_t, msg_t)
    check("... and no longer says to 'Fix the graphify install' (it is not broken)",
          "Fix the graphify install" not in msg_t, msg_t)
    check("... still says nothing was pushed and how to switch engines deliberately",
          "Nothing was pushed" in msg_t and "--stale-cleanup --force-prune" in msg_t, msg_t)
    check("a crash downgrade keeps its install remedy", "Fix the graphify install" in msg_c, msg_c)

    # B3 — the full downgrade pass: nothing pushed, DEGRADED, last_not_pushed set, manifest kept
    S = _load("sync")
    order = _wire(S, [_node()], "ast", {"requested": "auto", "resolved": "ast", "fallback_reason": _TIMEOUT_CAUSE,
                                        "timed_out": True, "timeout_s": 600}, [])
    root = _proj(base, "b3", dict(_PRIOR))
    with _env(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_EXTRACTOR=None):
        ok, out = _captured(S.sync, root, driver=_WDrv(), cleanup_stale=True)
    man = json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    check("a timed-out re-sync over a graphify graph pushes NOTHING and keeps the manifest",
          "push" not in order and man == _PRIOR, (order, man))
    check("... sync returns True (the graph is intact) with verdict DEGRADED", ok is True
          and getattr(S, "last_verdict", None) == "DEGRADED", (ok, getattr(S, "last_verdict", None)))
    lnp = getattr(S, "last_not_pushed", "<missing>")
    check("... and sync.last_not_pushed says why nothing was pushed (the CLI's exit-4 signal)",
          isinstance(lnp, str) and "downgrade" in lnp.lower(), lnp)
    check("... the printed message names DEEPMAP_GRAPHIFY_TIMEOUT", "DEEPMAP_GRAPHIFY_TIMEOUT" in out, out[-600:])
    S = _load("sync")
    order = _wire(S, [_node()], "ast", {"requested": "ast", "resolved": "ast", "fallback_reason": None}, [])
    root = _proj(base, "b3f", dict(_PRIOR))
    with _env(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_EXTRACTOR="ast"):
        ok, out = _captured(S.sync, root, driver=_WDrv())
    lnp = getattr(S, "last_not_pushed", None)
    check("a FORCED DEEPMAP_EXTRACTOR=ast downgrade is not-pushed too, and is not called a fallback",
          ok is True and "push" not in order and isinstance(lnp, str) and "forced" in lnp
          and "fell back" not in lnp, (ok, order, lnp))

    # B4 — a normal pushing sync leaves it None (and resets a previous value)
    order = _wire(S, [_node()], "graphify", {"requested": "auto", "resolved": "graphify"}, [])
    root = _proj(base, "b4", dict(_PRIOR))
    with _env(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_EXTRACTOR=None):
        ok, _out = _captured(S.sync, root, driver=_WDrv())
    check("a sync that pushed resets last_not_pushed to None", ok is True and "push" in order
          and getattr(S, "last_not_pushed", "<missing>") is None, (ok, order, getattr(S, "last_not_pushed", None)))

    # B5 — the instance lock is held (a project sharing this Neo4j is writing it): nothing synced
    S = _load("sync")
    order = _wire(S, [_node()], "graphify", {"requested": "auto", "resolved": "graphify"}, [])
    S._acquire_instance_lock = lambda uri: None
    root = _proj(base, "b5")
    with _env(NEO4J_URI="bolt://127.0.0.1:9"):
        ok, out = _captured(S.sync, root, driver=_WDrv())
    check("instance lock held: nothing extracted or pushed, sync returns True (not an error)",
          ok is True and order == [], (ok, order))
    check("... but last_not_pushed says the run was skipped", bool(getattr(S, "last_not_pushed", None)),
          getattr(S, "last_not_pushed", "<missing>"))

    # B6 — the per-root lock is held: queued -> the running sync includes the changes (0); not queued -> skipped (4)
    for queued in (True, False):
        S = _load("sync")
        order = _wire(S, [_node()], "graphify", {"requested": "auto", "resolved": "graphify"}, [])
        S._acquire_sync_lock = lambda root: None
        S._request_rerun = lambda root, **k: queued
        root = _proj(base, f"b6{int(queued)}")
        with _env(NEO4J_URI="bolt://127.0.0.1:9"):
            ok, out = _captured(S.sync, root, driver=_WDrv())
        has = hasattr(S, "last_not_pushed")
        lnp = getattr(S, "last_not_pushed", None)
        if queued:
            check("per-root lock held + re-run QUEUED: not a skip (the running sync includes the changes)",
                  ok is True and has and lnp is None, (ok, has, lnp))
        else:
            check("per-root lock held + the re-run could NOT be queued: last_not_pushed says it was skipped",
                  ok is True and has and isinstance(lnp, str) and bool(lnp), (ok, has, lnp))

    # B7 — sync.py's own entry point applies the same exit contract
    S = _load("sync")
    check("sync.SYNC_NOT_PUSHED_RC == 4", getattr(S, "SYNC_NOT_PUSHED_RC", None) == 4)

    def fake(verdict, ok=True, not_pushed=None):
        def _s(target, **kw):
            S.last_verdict = verdict
            S.last_not_pushed = not_pushed
            return ok
        return _s

    rows = [("DEGRADED + not pushed", fake("DEGRADED", not_pushed="engine-downgrade: ..."), 4),
            ("DEGRADED with a push", fake("DEGRADED"), 0), ("READY", fake("READY"), 0),
            ("FAILED (even with a kept graph)", fake("FAILED", not_pushed="x"), 1), ("refused", fake(None, ok=False), 1)]
    for label, f, want in rows:
        S.sync = f
        rc, _o = _captured(S._main, ["sync.py", "x"])
        check(f"python build/sync.py: {label} -> exit {want}", rc == want, rc)


# =====================================================================================================
# C — deepmap_cli.py sync exit codes + help
# =====================================================================================================
def gate_c_cli() -> None:
    print("\n-- C: `deepmap_cli.py sync` exits 4 when nothing was pushed; the codes are documented --")
    C = _load("deepmap_cli")
    S = _load("sync")
    check("deepmap_cli.SYNC_NOT_PUSHED_RC == sync.SYNC_NOT_PUSHED_RC == 4",
          getattr(C, "SYNC_NOT_PUSHED_RC", None) == 4 and getattr(S, "SYNC_NOT_PUSHED_RC", None) == 4,
          (getattr(C, "SYNC_NOT_PUSHED_RC", None), getattr(S, "SYNC_NOT_PUSHED_RC", None)))
    work = _mkdtemp("dm_r11_cli_")

    def fake_mod(*, ok=True, verdict=None, not_pushed=None, has_attr=True):
        m = types.ModuleType("fake_sync")

        def sync(target, **kw):
            return ok
        m.sync = sync
        m.last_verdict = verdict
        if has_attr:
            m.last_not_pushed = not_pushed
        m._sync_root = lambda r: r
        return m

    p = C.build_parser()
    rows = [("verdict DEGRADED, nothing pushed (engine downgrade after a timeout)",
             dict(verdict="DEGRADED", not_pushed="engine-downgrade: graphify timed out"), 4),
            ("READY", dict(verdict="READY"), 0),
            ("DEGRADED with a push", dict(verdict="DEGRADED"), 0),
            ("an older sync module without last_not_pushed", dict(verdict="DEGRADED", has_attr=False), 0),
            ("FAILED (precedence over not-pushed)", dict(verdict="FAILED", not_pushed="x"), 1),
            ("a refused sync", dict(ok=False), 1)]
    orig = C._load
    try:
        for label, kw, want in rows:
            C._load = (lambda kw_: (lambda name: fake_mod(**kw_) if name == "sync" else orig(name)))(kw)
            a = p.parse_args(["sync", str(work)])
            rc, out = _captured(a.fn, a)
            check(f"{label} -> exit {want}", rc == want, (rc, out[-300:]))
            if want == 4:
                check("... and says NOT PUSHED + that the previous graph (maybe stale) still answers",
                      "NOT PUSHED" in out and "stale" in out.lower() and "engine-downgrade" in out, out[-500:])
    finally:
        C._load = orig
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            p.parse_args(["sync", "--help"])
        except SystemExit:
            pass
    h = " ".join(buf.getvalue().split())
    check("`sync --help` documents the exit codes 0 / 1 / 3 / 4",
          all(re.search(rf"\b{c}\b", h) for c in ("0", "1", "3", "4")) and "exit" in h.lower()
          and "NOT PUSHED" in h, h[-900:])
    check("`sync --help` names DEEPMAP_GRAPHIFY_TIMEOUT", "DEEPMAP_GRAPHIFY_TIMEOUT" in h, h[-900:])
    check("the CLI module docstring documents exit 4 for sync", "4" in (C.__doc__ or "")
          and "NOT PUSHED" in (C.__doc__ or ""), (C.__doc__ or "")[-600:])


# =====================================================================================================
# D — provision setup + heal
# =====================================================================================================
class _FakeSync:
    def __init__(self, verdict="READY", not_pushed=None):
        self.verdict, self.not_pushed = verdict, not_pushed
        self.last_verdict = None
        self.last_not_pushed = None

    def sync(self, target, **kw):
        self.last_verdict = self.verdict
        self.last_not_pushed = self.not_pushed
        return True


def _setup_world(P, fake, calls):
    load = P._load
    return dict(_source_census_problem=lambda t: None,
                _resolve_ports=lambda t, allocate=True: (None, None, None),
                resolved_instance=lambda t: ("k0000000000", 7999, 7998),
                ensure_neo4j=lambda **k: True,
                _load=lambda name: fake if name == "sync" else load(name),
                verify_search=lambda *a, **k: True,
                start_mcp_servers=lambda **k: calls.append("mcp") or True,
                _clear_setup_blocker=lambda t: None,
                verify_mcp=lambda name, target=None: True,
                write_mcp_json=lambda *a, **k: None,
                _print_mcp_hint=lambda *a, **k: None,
                _print_git_hook_hint=lambda *a, **k: None,
                _record_base_owner=lambda t: None)


def gate_d_setup() -> None:
    print("\n-- D: provision setup over a kept graph — tools up, DEGRADED banner, exit 4; heal revives it --")
    P = _load("provision")
    home = _mkdtemp("dm_r11_prov_")
    target = home / "proj"
    target.mkdir()
    _write(target, "a.py")
    check("provision.SETUP_GRAPH_KEPT_RC == 4", getattr(P, "SETUP_GRAPH_KEPT_RC", None) == 4)
    for label, fake, want_rc in (
            ("the sync pushed nothing (engine downgrade after a timeout)",
             _FakeSync("DEGRADED", "engine-downgrade: graphify timed out (DEEPMAP_GRAPHIFY_TIMEOUT)"), 4),
            ("a normal sync", _FakeSync("READY"), 0)):
        calls: list = []
        world = _setup_world(P, fake, calls)
        with _env(DEEPMAP_HOME=str(home / "dmhome"), NEO4J_URI=None), _patch(P, **world):
            rc, out = _captured(P.main, ["setup", str(target)])
        if want_rc == 4:
            check(f"{label}: the MCP servers still come up over the kept graph", calls == ["mcp"], calls)
            check("... `provision.py setup` exits 4 (never a plain 0 over a graph it did not rebuild)", rc == 4, rc)
            check("... the banner says 'setup verdict: DEGRADED' and that the graph was NOT rebuilt",
                  "setup verdict: DEGRADED" in out and "NOT rebuilt" in out, out[-700:])
            check("... and carries the sync's reason", "engine-downgrade" in out, out[-700:])
            check("... with the timeout remedy (a larger DEEPMAP_GRAPHIFY_TIMEOUT)",
                  "larger DEEPMAP_GRAPHIFY_TIMEOUT" in out, out[-700:])
        else:
            check(f"{label}: exit 0, no graph-kept banner", rc == 0 and "NOT rebuilt" not in out, (rc, out[-400:]))
    # a not-pushed run for another reason (a sync already running) gets exit 4 too, but no timeout advice
    fake = _FakeSync("READY", "busy: another sync of this project was running and a re-run could not be queued")
    calls = []
    world = _setup_world(P, fake, calls)
    with _env(DEEPMAP_HOME=str(home / "dmhome"), NEO4J_URI=None), _patch(P, **world):
        rc, out = _captured(P.main, ["setup", str(target)])
    check("a 'busy' not-pushed setup: exit 4, and the banner does not blame the time budget",
          rc == 4 and "NOT rebuilt" in out and "larger DEEPMAP_GRAPHIFY_TIMEOUT" not in out, (rc, out[-500:]))
    # a stale flag from an earlier in-process setup never leaks into the next one
    fake = _FakeSync("READY")
    calls = []
    world = _setup_world(P, fake, calls)
    kept = getattr(P, "_SETUP_GRAPH_KEPT", None)
    if isinstance(kept, dict):
        kept["why"] = "left over"
    with _env(DEEPMAP_HOME=str(home / "dmhome"), NEO4J_URI=None), _patch(P, **world):
        rc, out = _captured(P.main, ["setup", str(target)])
    check("a graph-kept flag left over from an earlier setup call is cleared (exit 0)", rc == 0, (rc, out[-300:]))
    # heal: the kept manifest is the previous build's, so heal revives it (no FAILED / 0-node reason)
    (target / ".deepmap").mkdir(exist_ok=True)
    (target / ".deepmap" / "graph_build.json").write_text(json.dumps(_PRIOR), encoding="utf-8")
    with _env(DEEPMAP_HOME=str(home / "dmhome")):
        why = P._failed_build_reason(str(target))
    check("heal: a graph kept by a not-pushed sync is NOT a failed build (heal revives the previous graph)",
          why is None, why)


# =====================================================================================================
# E — the git post-commit block never blocks / fails a commit on exit 4
# =====================================================================================================
def _bash():
    cand = shutil.which("bash")
    if os.name != "nt":
        return cand
    out = []
    if cand and "system32" not in cand.lower():
        out.append(cand)
    git = shutil.which("git")
    if git:
        for up in Path(git).resolve().parents:
            for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                if (up / rel).is_file():
                    out.append(str(up / rel))
    return out[0] if out else None


def gate_e_git_hook() -> None:
    print("\n-- E: the post-commit block — a sync exiting 4 never blocks or fails the commit --")
    P = _load("provision")
    git, bash = shutil.which("git"), _bash()
    if not (git and bash):
        print("  [skip] git and a POSIX sh are needed for the behavioral check")
        return
    base = _mkdtemp("dm_r11_hook_")
    home, work, gcfg = base / "home", base / "w", base / "gitconfig"
    home.mkdir()
    work.mkdir()
    gcfg.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    release, marker = base / "release", base / "ran.txt"
    stub = base / "stub-python.sh"
    fwd = lambda p: Path(p).as_posix()  # noqa: E731
    # the "sync": waits for the test's release file (bounded), records its argv, then exits 4 (NOT PUSHED)
    stub.write_text("#!/bin/sh\ni=0\nwhile [ ! -e '" + fwd(release) + "' ] && [ $i -lt 600 ]; do sleep 0.2; "
                    "i=$((i+1)); done\nprintf '%s rc=4\\n' \"$2\" > '" + fwd(marker) + "'\nexit 4\n",
                    encoding="utf-8", newline="\n")
    os.chmod(stub, 0o755)
    env = dict(os.environ, DEEPMAP_HOME=str(home), GIT_CONFIG_GLOBAL=str(gcfg), GIT_CONFIG_NOSYSTEM="1",
               DEEPMAP_PYTHON=fwd(stub), NEO4J_URI="bolt://127.0.0.1:9")
    for k in ("GIT_DIR", "GIT_WORK_TREE"):
        env.pop(k, None)

    def g(*args):
        return subprocess.run([git, "-C", str(work), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, env=env, stdin=subprocess.DEVNULL)

    g("init", "-q")
    _write(work, "a.py")
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_PYTHON=fwd(stub), GIT_CONFIG_GLOBAL=str(gcfg), GIT_CONFIG_NOSYSTEM="1"):
        block = P._git_hook_block(str(work), str(work))
    check("the block runs the sync detached and guarded (`& ) || true`)", "& ) || true" in block and "nohup" in block,
          block)
    hook = work / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n" + block, encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)
    g("add", "a.py")
    t0 = time.time()
    r = g("commit", "-q", "-m", "x")
    took = time.time() - t0
    check("the commit succeeds (rc 0) while the hook's sync is still running — it never waited for it",
          r.returncode == 0 and not release.exists() and not marker.exists(),
          f"rc={r.returncode} took={took:.1f}s {r.stderr[-300:]}")
    release.write_text("go", encoding="utf-8")
    end = time.time() + 60
    while time.time() < end and not marker.exists():
        time.sleep(0.2)
    got = marker.read_text(encoding="utf-8", errors="replace") if marker.exists() else ""
    check("... the detached sync did run for this project and exited 4 (visible only in its log, by design)",
          "rc=4" in got and "sync" in got, got)


# =====================================================================================================
# F — installers + the benchmark's clone builder
# =====================================================================================================
def _functions(text: str, start_re: str) -> str:
    out, cur = [], None
    for ln in text.splitlines():
        if cur is None and re.match(start_re, ln):
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
            if ln.rstrip() == "}":
                out.append("\n".join(cur))
                cur = None
    return "\n\n".join(out)


def _runs(cmd: list) -> bool:
    try:
        return subprocess.run([*cmd, "-c", "import sys; sys.exit(7)"], capture_output=True, timeout=60,
                              stdin=subprocess.DEVNULL).returncode == 7
    except Exception:
        return False


def _win_short_path(path: str):
    """The 8.3 short form of an existing `path` (Windows), or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
        return buf.value if 0 < n < len(buf) else None
    except Exception:
        return None


def _space_free_python():
    """A Python command in install.ps1's `$PyCmd` convention — `$exe, $pre = $PyCmd.Split(" ", 2)`, i.e. "python"
    or "py -3" — for Invoke-Setup's behavioral check (R11b/V1). sys.executable itself when its path has no space;
    else its 8.3 short path; else a python / python3 on PATH whose path has no space; else the `py -3` launcher.
    Each candidate must actually run. None when there is no such command (the check is then skipped, visibly)."""
    exe = sys.executable
    cands = [exe, _win_short_path(exe)]
    cands += [shutil.which(n) for n in ("python", "python3")]
    for c in cands:
        if c and " " not in c and _runs([c]):
            return c
    launcher = shutil.which("py")
    if launcher and _runs([launcher, "-3"]):
        return "py -3"
    return None


def gate_f_installers() -> None:
    print("\n-- F: installers treat provision exit 4 as a warning (keep going); clone_graph fails closed on it --")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    main = sh[sh.find("\nmain() {"):]
    i_setup, i_hook = main.find('provision.py" setup'), main.find("\n  install_git_hooks")
    between = main[i_setup:i_hook] if 0 <= i_setup < i_hook else ""
    check("install.sh: main hands the setup's exit code to setup_outcome before the hook step",
          "setup_outcome" in between and "|| rc=$?" in between.replace("||rc=$?", "|| rc=$?"), between)
    bash = _bash()
    scripts = _mkdtemp("dm_r11_inst_")
    if bash:
        funcs = _functions(sh, r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{\s*$")
        for rc_in, want_cont in ((0, True), (4, True), (1, False), (3, False)):
            f = scripts / f"outcome_{rc_in}.sh"
            f.write_text(f"set -euo pipefail\n{funcs}\nif setup_outcome {rc_in}; then echo CONT; else echo STOP; fi\n",
                         encoding="utf-8", newline="\n")
            r = subprocess.run([bash, f.as_posix()], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
            out = r.stdout + r.stderr
            check(f"install.sh setup_outcome {rc_in} -> {'continue' if want_cont else 'stop'}",
                  ("CONT" in r.stdout) == want_cont and ("STOP" in r.stdout) != want_cont, out[-300:])
            if rc_in == 4:
                check("... exit 4 prints a WARNING that the graph was NOT rebuilt", "WARNING" in out
                      and "NOT rebuilt" in out, out[-300:])
    else:
        print("  [skip] no bash here (install.sh behavioral check)")
    ps = (REPO / "install.ps1").read_text(encoding="utf-8")
    m = ps[ps.find("# main"):]
    check("install.ps1: main runs setup through Invoke-Setup (exit 4 = warning) before the hook step",
          0 <= m.find("Invoke-Setup") < m.find("Install-DeepMapGitHooks $RepoRoot") and '"setup"' in m, m[:600])
    pwsh = (shutil.which("powershell") if os.name == "nt" else None) or shutil.which("pwsh")
    # R11b/V1: Invoke-Setup splits $PyCmd at the first space ("python" / "py -3"), so an interpreter path with a
    # space (C:\Program Files\Python312\python.exe, a venv under "My Projects") is not a $PyCmd the installer
    # ever builds — handing it one gave a false RED. Use a space-free command for the same interpreter family.
    py_cmd = _space_free_python() if pwsh else None
    if pwsh and py_cmd is None:
        print(f"  [skip] no space-free Python command here (sys.executable = {sys.executable!r}; no 8.3 short path, "
              f"no space-free python/python3 on PATH, no `py -3`) — install.ps1's Invoke-Setup behavioral check "
              f"is not run")
    elif pwsh:
        funcs = _functions(ps, r"^function\s+[A-Za-z][\w-]*")
        py = py_cmd.replace("'", "''")
        for rc_in, want_ok in ((0, True), (4, True), (1, False)):
            f = scripts / f"invoke_setup_{rc_in}.ps1"
            f.write_text("$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n" + funcs + "\n"
                         f"try {{ Invoke-Setup '{py}' @('-c', 'import sys; sys.exit({rc_in})'); Write-Output 'CONT' }} "
                         "catch { Write-Output ('STOP ' + $_) }\n", encoding="utf-8-sig")
            r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(f)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            out = r.stdout + r.stderr
            # a throw must be Invoke-Setup's OWN (`command failed`), never PowerShell failing to start the command
            check(f"install.ps1 Invoke-Setup rc {rc_in} -> {'continue' if want_ok else 'throw'}",
                  ("CONT" in r.stdout) == want_ok and (want_ok or "command failed" in r.stdout), out[-300:])
            if rc_in == 4:
                check("... exit 4 warns that the graph was NOT rebuilt", "NOT rebuilt" in out, out[-300:])
    else:
        print("  [skip] no PowerShell here (install.ps1 behavioral check)")

    CG = _load_bench("clone_graph")
    clone = _mkdtemp("dm_r11_clone_")
    # clone_graph.main pops its routing env from THIS process — hand every one back afterwards
    with _env(**{k: os.environ.get(k) for k in getattr(CG, "ROUTING_ENV", ())}), \
            _patch(CG, preallocate_instance=lambda c: ((7999, 7998), None),
                _registry=lambda: SimpleNamespace(BASE_BOLT=7687, BASE_HTTP=7474),
                clone_ports=lambda c: (8999, 8998)):
        rc, out = _captured(CG.main, ["build", str(clone)], runner=lambda a, e: 4)
    check("clone_graph build: setup exit 4 fails CLOSED (a graph the setup did not rebuild is never measured)",
          rc == 4 and "FAIL CLOSED" in out and "NOT rebuilt" in out, (rc, out[-500:]))
    P = _load("provision")
    check("clone_graph.SETUP_GRAPH_KEPT_RC == provision.SETUP_GRAPH_KEPT_RC",
          getattr(CG, "SETUP_GRAPH_KEPT_RC", None) == getattr(P, "SETUP_GRAPH_KEPT_RC", "<missing>") == 4)


def _load_bench(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_r11b_{name}_{time.time_ns()}", REPO / "benchmark" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# =====================================================================================================
# G — verdict remedy; H — docs
# =====================================================================================================
def gate_g_verdict() -> None:
    print("\n-- G: an auto->ast fallback caused by a timeout names DEEPMAP_GRAPHIFY_TIMEOUT as the remedy --")
    V = _load("verdict")
    fr = getattr(V, "_fallback_remedy", None)
    check("verdict._fallback_remedy exists", callable(fr))
    if not callable(fr):
        return
    t = fr({"fallback_reason": _TIMEOUT_CAUSE, "timed_out": True})
    c = fr({"fallback_reason": "graphify crashed (exit 1)"})
    check("timeout -> 'raise DEEPMAP_GRAPHIFY_TIMEOUT', not 'fix the graphify error'",
          "DEEPMAP_GRAPHIFY_TIMEOUT" in t and "Fix the graphify error" not in t, t)
    check("a crash keeps 'Fix the graphify error'", "Fix the graphify error" in c, c)


def gate_h_docs() -> None:
    print("\n-- H: the command docs explain exit 4 and the timeout variable --")
    refresh = (REPO / "commands" / "refresh.md").read_text(encoding="utf-8")
    setup = (REPO / "commands" / "setup.md").read_text(encoding="utf-8")
    flat = " ".join(refresh.split())
    check("refresh.md: exit 4 = nothing was pushed (the previous graph, maybe stale) — not a success",
          re.search(r"exit (code )?\**4", flat, re.I) is not None and "NOT PUSHED" in flat, flat[-1500:])
    check("refresh.md: names DEEPMAP_GRAPHIFY_TIMEOUT for a timed-out engine", "DEEPMAP_GRAPHIFY_TIMEOUT" in refresh)
    check("refresh.md: the re-run form keeps NEO4J_PASSWORD first (the allowed-tools prefix still matches)",
          re.search(r"NEO4J_PASSWORD=deepmap DEEPMAP_GRAPHIFY_TIMEOUT=\d+", refresh) is not None)
    check("setup.md: explains exit 4 (tools up over the PREVIOUS graph; not rebuilt)",
          re.search(r"exit (code )?\**4", " ".join(setup.split()), re.I) is not None and "NOT rebuilt" in setup)


# =====================================================================================================
# M5 — CALLS_PLUS: a module alias imported inside a module-level try/except
# =====================================================================================================
_FIXTURE = REPO / "tests" / "fixtures" / "graphify_r7" / "extraction_r7.json"
_ORIG_HEAD = ("import helpers as _hs\nimport pkg.mod\nfrom pkg import mod as m2\nimport typing\n\n")


def _mapped(files_override: dict, *, plus: bool):
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))["py"]
    root = _mkdtemp("dm_r11_m5_")
    for rel, text in data["files"].items():
        text = files_override.get(rel, text)
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    flags = dict(DEEPMAP_GRAPHIFY_CALLS_PLUS="1" if plus else "0", DEEPMAP_PRECISE_CALLS=None,
                 DEEPMAP_MODULE_DOCSTRINGS=None, DEEPMAP_SUMMARY_NORMALIZE=None, DEEPMAP_SUPPLEMENTAL_TS_RUST=None,
                 DEEPMAP_RESOLVED_IMPORTS=None, DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None,
                 DEEPMAP_FALSE_CALL_FILTER=None, DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=None)
    with _env(**flags):
        return GX.map_graph(json.loads(json.dumps(data["graph"])), root=root)


def gate_m5_try_alias() -> None:
    print("\n-- M5: CALLS_PLUS resolves `try: import X as alias` at module level (flag OFF: untouched) --")
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))["py"]
    run = data["files"]["scripts/run.py"]
    check("fixture precondition: scripts/run.py starts with the expected import block (5 lines incl. the blank)",
          _ORIG_HEAD in run, run[:200])
    # same line count, so every node line the engine recorded stays valid
    wrapped = run.replace(_ORIG_HEAD, "try: import helpers as _hs\nexcept ImportError: _hs = None\n"
                                      "import pkg.mod; from pkg import mod as m2\nimport typing\n\n")
    conflict = run.replace(_ORIG_HEAD, "try: import helpers as _hs\nexcept ImportError: import helper as _hs\n"
                                       "import pkg.mod; from pkg import mod as m2\nimport typing\n\n")
    check("fixture edits keep the line count", wrapped.count("\n") == run.count("\n") == conflict.count("\n"))
    _n, calls, _i = _mapped({"scripts/run.py": wrapped}, plus=True)
    cs = set(calls)
    check("CALLS_PLUS: `_hs.feature_enabled(1)` inside main() resolves through the try/except alias",
          ("scripts_run_main", "scripts_helpers_feature_enabled") in cs, sorted(cs))
    check("... and the module-level `_hs.feature_enabled(3)` is recorded on the Module node",
          ("scripts_run", "scripts_helpers_feature_enabled") in cs, sorted(cs))
    _n, calls_c, _i = _mapped({"scripts/run.py": conflict}, plus=True)
    check("a name the try and the except bind to DIFFERENT modules resolves to neither (no guess)",
          not ({("scripts_run_main", "scripts_helpers_feature_enabled"), ("scripts_run", "scripts_helpers_feature_enabled")}
               & set(calls_c)), sorted(set(calls_c)))
    _n, calls_o, _i = _mapped({}, plus=True)
    check("the unmodified fixture's CALLS_PLUS edges are unchanged (top-level aliases resolve as before)",
          ("scripts_run_main", "scripts_helpers_feature_enabled") in set(calls_o)
          and ("scripts_run_main", "pkg_mod_g") in set(calls_o), sorted(set(calls_o)))

    def boom(*a, **k):
        raise AssertionError("_calls_plus ran with DEEPMAP_GRAPHIFY_CALLS_PLUS off")

    with _patch(GX, _calls_plus=boom):
        try:
            _n, calls_off, _i = _mapped({"scripts/run.py": wrapped}, plus=False)
            ran = False
        except AssertionError:
            ran, calls_off = True, []
    check("flag OFF (the default): _calls_plus never runs, so the default graph cannot change", not ran)
    _n, calls_off2, _i = _mapped({"scripts/run.py": wrapped}, plus=False)
    check("... and the flag-off map adds no alias edge", ("scripts_run_main", "scripts_helpers_feature_enabled")
          not in set(calls_off2), sorted(set(calls_off2)))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-11 M1 (graphify time budget + sync exit contract) + M5 (try-alias) gates; no Neo4j")
    print("=" * 72)
    home = _mkdtemp("dm_r11_main_home_")
    with _env(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR=None, DEEPMAP_INCREMENTAL_SYNC=None,
              DEEPMAP_PRECISE_CALLS=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None, DEEPMAP_EXCLUDE_TESTS=None,
              DEEPMAP_INCLUDE_VENDOR=None, DEEPMAP_FTS_SHORT_NAME=None, DEEPMAP_AUTO_GITIGNORE="0",
              DEEPMAP_GRAPHIFY_TIMEOUT=None):
        for gate in (gate_a_budget, gate_a_wiring, gate_b_sync, gate_c_cli, gate_d_setup, gate_e_git_hook,
                     gate_f_installers, gate_g_verdict, gate_h_docs, gate_m5_try_alias):
            try:
                gate()
            except Exception as e:  # noqa: BLE001 — a crashing gate is a RED gate, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    for p in reversed(_CREATED):
        _rmtree(p)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
