#!/usr/bin/env python3
"""Graph revision: sync_status.GRAPH_REV / graph_rev_due and auto_sync.freshness()'s "graph-rev" (the 2026-11 SQL
design, section 5.5, and its section 13 "Graph-revision gates").

A graph revision numbers a change to what DeepMap builds from UNCHANGED sources (a release that changes the default
graph or the coverage census), which the source fingerprint cannot see: the 0.6.3 SQL census fix never reached a tree
whose sources did not change, so its old DEGRADED verdict stayed. make_build_recipe records the code's GRAPH_REV in
every manifest (build_recipe.graph_rev); graph_rev_due is THE rule (the highest revision in (recorded, GRAPH_REV]
whose scope covers the graph); auto_sync.freshness() reports such a graph stale ("graph-rev"), so the background
refresh rebuilds it once.

  A. the surface: GRAPH_REV (an int >= 0), GRAPH_REV_SCOPE (consecutive revisions from 1, each a callable),
     GRAPH_REV_CHANGES (plain words for each, no em / en dash), every revision up to GRAPH_REV has a scope (a raise
     without one fails here), recorded_graph_rev / graph_rev_due / graph_rev_note take the manifest and never raise
     on junk; sync_status still imports only the standard library; sync.py records make_build_recipe's result as the
     manifest's build_recipe (the one writer).
  B. the shipped GRAPH_REV is 1: revision 1 (the census of languages this install cannot parse, its relay to the
     agent, and the SQL reader behind its switch) ships with its scope and words. A manifest without the field (every
     graph built before revisions existed, 0.6.3's included) reads as revision 0: the 0.6.3 SQL graph, a 0.6.2
     DEGRADED one, one without coverage or recipe and the ones blaming SQL / Perl / Python are due for 1; a clean one,
     one blaming Ruby, one this code wrote (not_parsed or coverage.sql, graph_rev 1) and a newer one are not;
     make_build_recipe records graph_rev == GRAPH_REV (1).
  C. recorded_graph_rev: absent / malformed (bool, negative, text, fraction, not a dict) -> 0; a whole number -> it.
  D. revision 1's scope, exactly as designed: no coverage block -> covered (unknown: rebuilt once); the census
     counted SQL / DM / Perl / R files; parser_missing / not_parsed; a persisted "LOW COVERAGE for " SQL, DM, Perl, R,
     Python or JavaScript (whole names: Ruby, TypeScript, C# and the dir / long-path reasons are not); a SQL-free,
     warning-free graph is never covered. Drift lock: the reasons the REAL verdict writes for a sparse SQL / R / Perl /
     DM / Python / JavaScript layer are covered, a sparse C# one is not.
  E. graph_rev_due / graph_rev_note with patched revisions: revision 1 against the 0.6.2 / 0.6.3 / up-to-date / newer
     manifests; synthetic revisions 1-3 (the highest covering one wins, none at or below the recorded one, a missing
     scope covers nothing, a raising scope -> None); the note names both revisions and what changed.
  F. freshness(): fingerprint fresh + graph_rev absent + SQL -> ("stale", "graph-rev"); graph_rev == GRAPH_REV ->
     fresh; SQL-free and reason-free without a revision -> fresh (no mass rebuild); no coverage -> stale; a persisted
     LOW COVERAGE for R -> stale; the legacy HEAD / dirty and non-git content-hash paths too; changed sources keep
     their own reason; graph_rev_due raising, missing, or sync_status not loadable -> fresh; the shipped GRAPH_REV ->
     stale "graph-rev" for the 0.6.3 / 0.6.2 / no-coverage manifests, fresh for a clean one and one this code wrote;
     the result keeps its five keys.
  G. the runner (run(), real sync_status, a fake sync whose rebuild writes the manifest through the REAL
     make_build_recipe): stale once -> one sync, outcome synced, last_run.graph_rev, the log names the revision; the
     next start (after the cooldown) -> fresh, no sync, fresh_at_head; a rebuild that is not pushed leaves the graph
     due and the next start tries again; an up-to-date graph and a SQL-free graph without a revision -> fresh, no
     sync; at the shipped GRAPH_REV the 0.6.3 SQL graph is rebuilt once and a clean one is not; auto-sync off
     (DEEPMAP_AUTO_SYNC=0, or the project switch) -> no sync, no record; a
     second runner that finds the lock held queues nothing for a graph revision (the holder rebuilds it), and
     exactly one re-run when the sources changed too. ONCE per revision: the one-time rebuild is recorded
     (autosync.json graph_rev_rebuilt, also when R16's extra pass ends the run); an older DeepMap that rewrites the
     manifest after it (an older install's git post-commit hook, which keeps running its version after a plugin
     update) makes no further rebuild, commit after commit: a recorded skip graph-rev-again naming that version and
     the likely cause; changed sources still sync, and a NEWER covering revision is rebuilt once again.
  H. status_line: a due graph is named with both revisions and "rebuilds it once" (level kept); a refused next start
     and a paused refresh point at /deep-map:refresh; after the rebuild the last run names the one-time rebuild and the
     due text is gone; an up-to-date graph says what it said before; auto-sync off (every switch) says what it said
     before plus, for a due graph, the due sentence and /deep-map:refresh; a running refresh of a due graph says so;
     once the one-time rebuild was made, "rebuilds it once" is never said again; at the shipped GRAPH_REV a due graph
     gets the due sentence after the text without the rule, and a clean graph's text equals it.
  I. the hooks' proof is untouched: after the graph-revision rebuild the real deepmap_hook_state._fresh_proof gives
     no proof (outcome synced), after the next start's "fresh" it gives the proof (fresh_at_head, the manifest's
     fingerprint), as before.

Hermetic: temp project dirs, a temp DEEPMAP_HOME and HF_HUB_CACHE, every DEEPMAP_* variable scrubbed, a fake `sync`,
every runner seam that waits or probes replaced (a fake clock), the real sync_status / auto_sync / hooks state. Never
Neo4j, never the network, never a real sync; no subprocess but one short import check.
Usage: python tests/graph_rev_gates.py
"""
from __future__ import annotations

import contextlib
import importlib
import inspect
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
PASSES: list = []
FAILS: list = []
H1, H2, H3, H4 = "a" * 40, "b" * 40, "c" * 40, "d" * 40
FP1, FP2 = "f" * 32, "e" * 32
PY_XY = f"{sys.version_info[0]}.{sys.version_info[1]}"
VERSION = "0.6.3"
ROUTE = "bolt://127.0.0.1:17689"
DASHES = ("\u2014", "\u2013")
GIB = 1024 ** 3
SEAMS = ("_load_sync", "_now", "_sleep", "_memory", "_route", "_heal_status", "_bolt_up", "_heal_problem",
         "_model_state", "_engine_file_count", "_graphify_version", "_graphify_budget", "_lower_priority", "_git_head",
         "_find_spec", "_running_version", "_touch_last_used", "_load_graphify", "_load_status")
GX = SimpleNamespace(ENV_GRAPHIFY_TIMEOUT="DEEPMAP_GRAPHIFY_TIMEOUT", GRAPHIFY_TIMEOUT_MAX_S=7200,
                     SUPPORTED_GRAPHIFY_MIN=(0, 8, 39), SUPPORTED_GRAPHIFY_BELOW=(0, 8, 52),
                     _version_tuple=lambda v: tuple(int(x) for x in str(v).split(".")))
SQL_REASON = ("LOW COVERAGE for SQL \u2014 12 files on disk but only 0 extracted node(s); SQL code is effectively "
              "invisible to search/impact (check the extractor lines in the sync output \u2014 /deep-map:doctor "
              "shows the engine the last sync used).")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" -- {str(detail)[:900]}"))


@contextlib.contextmanager
def _env(**kv):
    """The whole os.environ snapshotted and restored (run() writes the offline variables into it)."""
    saved = dict(os.environ)
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@contextlib.contextmanager
def _patched(mod, **attrs):
    """Set module attributes for the block, then put back exactly what was there (absent stays absent)."""
    missing = object()
    saved = {k: getattr(mod, k, missing) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(mod, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(mod, k)
                except AttributeError:
                    pass
            else:
                setattr(mod, k, v)


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def _rmtree(path: Path) -> None:
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            pass
    shutil.rmtree(str(path), **({"onexc": onerr} if sys.version_info >= (3, 12) else {"onerror": onerr}))


def _section(label: str, fn, *a) -> None:
    """Run one section; an exception is ONE failed check naming it (the RED run before the code exists)."""
    try:
        fn(*a)
    except Exception as exc:  # noqa: BLE001
        check(f"section {label} ran to the end", False, f"{type(exc).__name__}: {exc} | "
              + " / ".join(traceback.format_exc().strip().splitlines()[-4:]))


# ---- manifests -------------------------------------------------------------------------------------------------------
def _recipe(home, **over) -> dict:
    r = {"schema": 1, "deepmap_version": VERSION, "python": PY_XY, "extractor_requested": "auto", "engine": "graphify",
         "graphify_version": "0.8.39", "embed_model": None, "embed_model_revision": None, "env": {},
         "route": {"source": "registry", "uri": ROUTE}, "deepmap_home": os.path.realpath(str(home))}
    r.update(over)
    return r


def m063(home=".", **over) -> dict:
    """What 0.6.3 wrote for the field case (20 .cs + 12 .sql, no tree_sitter_sql): SQL uncounted, named in
    parser_missing, READY, no graph_rev."""
    m = {"nodes": 5644, "verdict": "READY", "reasons": [], "built_at_sha": H1, "dirty": False,
         "source_fingerprint": FP1, "sync_wall_s": 30.0, "graph_engines": ["graphify"], "extractor": "graphify",
         "graphify_version": "0.8.39", "deepmap_version": VERSION, "built_at_utc": "2026-10-31T10:00:00Z",
         "edits_seen_through_epoch": 1_790_000_000.0,
         "coverage": {"languages": {"C#": {"files": 20, "nodes": 5644}}, "dirs": {},
                      "parser_missing": {".sql": {"files": 12, "package": "tree-sitter-sql"}}},
         "build_recipe": _recipe(home)}
    for k, v in over.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    return m


def m062(home=".", **over) -> dict:
    """What 0.6.2 wrote for the same tree: SQL counted with 0 nodes -> DEGRADED, LOW COVERAGE for SQL."""
    m = m063(home, verdict="DEGRADED", reasons=[SQL_REASON], deepmap_version="0.6.2",
             coverage={"languages": {"C#": {"files": 20, "nodes": 5644}, "SQL": {"files": 12, "nodes": 0}},
                       "dirs": {}})
    m["build_recipe"] = _recipe(home, deepmap_version="0.6.2")
    for k, v in over.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    return m


def m_clean(home=".", **over) -> dict:
    """A SQL-free, warning-free graph (C# only, READY, no graph_rev): never rebuilt by revision 1."""
    return m063(home, coverage={"languages": {"C#": {"files": 20, "nodes": 5644}}, "dirs": {}}, **over)


def with_rev(m: dict, rev) -> dict:
    out = json.loads(json.dumps(m))
    out.setdefault("build_recipe", {})["graph_rev"] = rev
    return out


# ---- the fakes the runner calls --------------------------------------------------------------------------------------
class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = float(t)

    def now(self) -> float:
        return self.t

    def sleep(self, s) -> None:
        self.t += max(0.0, float(s))


class FakeSync:
    """The `sync` module the runner loads: a rebuild rewrites the manifest the way sync does (build_recipe from the REAL
    sync_status.make_build_recipe, so it records the code's GRAPH_REV); "not-pushed" leaves it as it was; a pass claims
    a queued re-run (.deepmap/sync.rerun) as sync's pass loop does, and "rebuild+rerun" queues one while it syncs (a
    second runner coalescing into this one), so the runner makes R16's extra pass."""

    def __init__(self, case):
        self.case = case
        self.calls: list = []
        self.plan: list = []
        self.requests: list = []
        self.fp = {"value": FP1, "kind": "git", "head": H1, "git_busy": None, "truncated": False}
        self.last_outcome = None
        self.dirty = False
        self.content_hash = None

    def sync(self, workspace_root, **kw):
        self.calls.append(dict(kw))
        step = self.plan.pop(0) if self.plan else "rebuild"
        rerun = Path(workspace_root) / ".deepmap" / "sync.rerun"
        if rerun.is_file():
            rerun.unlink()
        if step == "rebuild+rerun":
            _write(rerun, {"cleanup_stale": False, "force_prune": False, "prune_policy": "evidence",
                           "origin": "unattended", "trigger": "session-start"})
        if step in ("rebuild", "rebuild+rerun"):
            self.case.rebuild()
            self.last_outcome = {"schema": 1, "id": f"S-{len(self.calls)}", "origin": "unattended",
                                 "outcome": "synced", "rc": 0, "verdict": "READY", "reasons": [], "built_at_sha": H1,
                                 "dirty": False, "source_fingerprint": FP1, "wall_s": 40.0, "stale_kept": 0,
                                 "embeddings_deferred": False}
        else:
            self.last_outcome = {"schema": 1, "id": f"S-{len(self.calls)}", "origin": "unattended",
                                 "outcome": "not-pushed", "not_pushed_kind": "busy-instance", "rc": 4,
                                 "reason": "another project's sync held the database"}
        return True

    def source_fingerprint(self, root):
        return dict(self.fp)

    def git_busy(self, root):
        return None

    def _request_rerun(self, root, *, cleanup_stale, force_prune, prune_policy=None, origin="manual", trigger=None):
        req = {"cleanup_stale": cleanup_stale, "force_prune": force_prune, "prune_policy": prune_policy,
               "origin": origin, "trigger": trigger}
        self.requests.append(req)
        _write(Path(root) / ".deepmap" / "sync.rerun", req)
        return True

    def _git_worktree_dirty(self, root):
        return self.dirty

    def _fs_content_hash(self, root):
        return self.content_hash


class Case:
    def __init__(self, W, label: str, manifest=None):
        self.W, self.S, self.A = W, W.S, W.A
        W.n += 1
        self.dir = W.base / f"{label}{W.n:03d}"
        self.root = self.dir / "proj"
        self.home = self.dir / "home"
        (self.root / ".deepmap").mkdir(parents=True, exist_ok=True)
        (self.root / ".git").mkdir(exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)
        self.clock = Clock()
        self.sync = FakeSync(self)
        self.head = H1
        self.log = ""
        self.write_manifest(manifest if manifest is not None else m063(self.home))

    def write_manifest(self, m: dict) -> None:
        _write(self.root / ".deepmap" / "graph_build.json", m)

    def manifest(self) -> dict:
        return _read(self.root / ".deepmap" / "graph_build.json") or {}

    def rebuild(self) -> None:
        """What a successful sync pass writes: the manifest with a new build_recipe from the REAL make_build_recipe."""
        m = self.manifest()
        m["build_recipe"] = self.S.make_build_recipe(engine="graphify", extraction={"graphify_version": "0.8.39"},
                                                     route_source="registry", neo4j_uri=ROUTE, code_version=VERSION)
        m.update(built_at_utc="2026-11-01T12:00:00Z", edits_seen_through_epoch=self.clock.t, sync_wall_s=40.0,
                 source_fingerprint=FP1, verdict="READY", reasons=[])
        self.write_manifest(m)

    def old_writer(self, head: str, version: str = "0.6.2") -> None:
        """A commit at `head`, then the sync an OLDER DeepMap runs for it: an older install's git post-commit hook names
        that install's plugin folder, so it keeps running its version after a plugin update. It rewrites the manifest
        at the new HEAD with the sources current (fingerprint unchanged), as a sync that is not a background pass, and
        its make_build_recipe records no graph_rev."""
        self.clock.t += 60
        m = self.manifest()
        r = dict(m.get("build_recipe") or {})
        r.pop("graph_rev", None)
        r["deepmap_version"] = version
        m.update(build_recipe=r, deepmap_version=version, built_at_sha=head, sync_wall_s=35.0,
                 built_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock.t)),
                 edits_seen_through_epoch=self.clock.t)
        self.write_manifest(m)
        self.head = head
        self.sync.fp["head"] = head

    def install(self) -> None:
        A, c = self.A, self
        A._load_sync = lambda: c.sync
        A._load_status = lambda: c.S
        A._now, A._sleep = c.clock.now, c.clock.sleep
        A._memory = lambda: (8 * GIB, 16 * GIB)
        A._route = lambda root: SimpleNamespace(uri=ROUTE, source="registry", problem=None)
        A._heal_status = lambda root: {"ts_epoch": c.clock.t, "neo4j_ok": True, "refused": False,
                                       "refusal_kind": None}
        A._bolt_up = lambda root: True
        A._heal_problem = lambda root: None
        A._model_state = lambda: {"complete": True, "revision": None, "path": "x", "missing": []}
        A._engine_file_count = lambda root, limit: 10
        A._graphify_version = lambda: "0.8.39"
        A._graphify_budget = lambda root: {"timeout_s": 600, "source": "size-aware"}
        A._lower_priority = lambda: None
        A._git_head = lambda root: c.head
        A._find_spec = lambda name: object()
        A._running_version = lambda: VERSION
        A._touch_last_used = lambda root: None
        A._load_graphify = lambda: GX

    def env(self, **kv):
        base = {k: None for k in list(os.environ) if k.startswith("DEEPMAP_")}
        base.update(DEEPMAP_HOME=str(self.home), HF_HUB_CACHE=str(self.dir / "hf"), CLAUDE_CODE_SESSION_ATTENDED="1",
                    CLAUDE_CODE_ENTRYPOINT=None, NEO4J_URI=None)
        base.update(kv)
        return _env(**base)

    def run(self, **envkv) -> int:
        self.install()
        with self.env(**envkv):
            rc, self.log = _quiet(self.A.run, str(self.root), trigger="session-start", session="SID-1")
        return rc

    def status(self, **envkv) -> tuple:
        self.install()
        with self.env(**envkv):
            return tuple(self.A.status_line(self.root))

    def state(self) -> dict:
        return _read(self.root / ".deepmap" / "autosync.json") or {}

    def last(self) -> dict:
        lr = self.state().get("last_run")
        return lr if isinstance(lr, dict) else {}


class World:
    def __init__(self, S, A, base: Path):
        self.S, self.A, self.base, self.n = S, A, base, 0
        self.seams = {name: getattr(A, name) for name in SEAMS}

    def restore(self) -> None:
        for name, fn in self.seams.items():
            setattr(self.A, name, fn)

    def case(self, label: str, manifest=None) -> Case:
        self.restore()
        return Case(self, label, manifest)


# =====================================================================================================================
# A - the surface
# =====================================================================================================================
def gate_surface(W: World) -> None:
    print("\n-- A. the surface: GRAPH_REV, the scope and words tables, the three functions, the one writer --")
    S = W.S
    rev = getattr(S, "GRAPH_REV", None)
    check("A: sync_status.GRAPH_REV is a whole number >= 0", isinstance(rev, int) and not isinstance(rev, bool)
          and rev >= 0, rev)
    scope = getattr(S, "GRAPH_REV_SCOPE", None)
    words = getattr(S, "GRAPH_REV_CHANGES", None)
    keys = sorted(scope) if isinstance(scope, dict) else None
    check("A: GRAPH_REV_SCOPE maps consecutive revisions from 1 to callables",
          bool(keys) and keys == list(range(1, len(keys) + 1)) and all(callable(scope[k]) for k in keys), scope)
    check("A: every revision up to GRAPH_REV has a scope (a raise without one fails here)",
          isinstance(scope, dict) and isinstance(rev, int) and all(callable(scope.get(r)) for r in range(1, rev + 1)),
          (rev, keys))
    bad_words = {k: (words or {}).get(k) for k in (keys or [])
                 if not isinstance((words or {}).get(k), str) or not (words or {}).get(k).strip()
                 or any(d in (words or {}).get(k) for d in DASHES) or len((words or {}).get(k)) > 200}
    check("A: GRAPH_REV_CHANGES says what each revision changed, in plain words (<= 200 chars, no em / en dash)",
          isinstance(words, dict) and not bad_words and set(words) == set(keys or []), bad_words or words)
    sigs = {"recorded_graph_rev": ["manifest"], "graph_rev_due": ["manifest"], "graph_rev_note": ["manifest"]}
    wrong = {}
    for name, params in sigs.items():
        fn = getattr(S, name, None)
        try:
            got = list(inspect.signature(fn).parameters) if callable(fn) else None
        except (TypeError, ValueError):
            got = None
        if got != params:
            wrong[name] = got
    check("A: recorded_graph_rev(manifest) / graph_rev_due(manifest) / graph_rev_note(manifest)", not wrong, wrong)

    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

        def __getitem__(self, k):
            raise RuntimeError("boom")

    junk = (None, [], "x", 7, {"build_recipe": 5}, {"coverage": 7, "reasons": 3}, {"build_recipe": {"graph_rev": []}},
            Boom(), {"coverage": Boom()}, {"build_recipe": Boom()})
    raised = []
    with _patched(S, GRAPH_REV=max(1, rev if isinstance(rev, int) else 1)):
        for j in junk:
            for name in sigs:
                try:
                    out = getattr(S, name)(j)
                    if name == "recorded_graph_rev" and out != 0:
                        raised.append((name, repr(j)[:40], out))
                except Exception as exc:  # noqa: BLE001
                    raised.append((name, repr(j)[:40], type(exc).__name__))
    check("A: the three never raise on junk (recorded_graph_rev of junk is 0)", not raised, raised)
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    check("A: sync.py writes make_build_recipe's result as the manifest's build_recipe (the one writer of graph_rev)",
          "recipe = sync_status.make_build_recipe(" in src and "build_recipe=recipe" in src
          and src.count("write_graph_build_manifest(") == 2)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import sync_status; "
            "print(json.dumps(sorted(m for m in ('sync', 'neo4j', 'torch', 'graphify_extract', 'verdict') "
            "if m in sys.modules)))")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(BUILD)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    check("A: importing sync_status still imports none of sync / neo4j / torch / graphify_extract / verdict",
          p.returncode == 0 and (p.stdout or "").strip().splitlines()[-1:] == ["[]"],
          (p.returncode, p.stdout[-200:], p.stderr[-300:]))


# =====================================================================================================================
# B - the shipped baseline
# =====================================================================================================================
def gate_baseline(W: World) -> None:
    print("\n-- B. the shipped GRAPH_REV 1: the graphs revision 1 covers are due once, every other graph is not --")
    S = W.S
    check("B: the shipped GRAPH_REV is 1 (revision 1 ships with the census of languages this install cannot parse, "
          "its relay to the agent and the SQL reader behind DEEPMAP_SQL_DECLARATIONS; a raise needs this check updated "
          "with that revision's cases)", getattr(S, "GRAPH_REV", None) == 1, getattr(S, "GRAPH_REV", None))
    # what this code writes for the field case (reader off by default, no tree_sitter_sql): .sql not counted, named
    # in coverage "not_parsed" with its cause, READY, graph_rev 1
    new_cov = {"languages": {"C#": {"files": 20, "nodes": 5644}}, "dirs": {},
               "not_parsed": {".sql": {"files": 12, "cause": "off", "package": "tree-sitter-sql", "detail": "default"}}}
    read_cov = {"languages": {"C#": {"files": 20, "nodes": 5644}, "SQL": {"files": 12, "nodes": 52}}, "dirs": {},
                "sql": {"read": 12, "declarations": 40}}
    rows = {"0.6.3 SQL (parser_missing)": (m063(), 1), "0.6.2 SQL (LOW COVERAGE for SQL)": (m062(), 1),
            "clean C#": (m_clean(), None), "no coverage block": (m063(coverage=None), 1),
            "a recorded 0": (with_rev(m063(), 0), 1), "no build_recipe": (m063(build_recipe=None), 1),
            "a recorded 0, clean": (with_rev(m_clean(), 0), None),
            "LOW COVERAGE for Perl": (m_clean(reasons=["LOW COVERAGE for Perl \u2014 6 files"]), 1),
            "LOW COVERAGE for Python (.pyi)": (m_clean(reasons=["LOW COVERAGE for Python \u2014 10 files"]), 1),
            "LOW COVERAGE for Ruby (not revision 1's)": (m_clean(reasons=["LOW COVERAGE for Ruby \u2014 6 files"]),
                                                         None),
            "what this code writes (not_parsed, graph_rev 1)": (with_rev(m063(coverage=new_cov), 1), None),
            "the same, reader on (coverage.sql, SQL counted)": (with_rev(m063(coverage=read_cov), 1), None),
            "a newer DeepMap's graph (graph_rev 2)": (with_rev(m063(), 2), None)}
    due = {k: S.graph_rev_due(m) for k, (m, _w) in rows.items()}
    bad = {k: (due[k], w) for k, (_m, w) in rows.items() if due[k] != w}
    check(f"B: at the shipped GRAPH_REV, all {len(rows)} rows: the 0.6.3 SQL graph, a 0.6.2 DEGRADED one, one with "
          "no coverage or no recipe and the ones blaming SQL / Perl / Python are due for 1; a clean one, one blaming "
          "Ruby, one this code wrote and a newer one are not", not bad, bad)
    notes = {k: S.graph_rev_note(m) for k, (m, _w) in rows.items()}
    check("B: graph_rev_note names exactly the due graphs (graph revision 0, this code 1, revision 1's words)",
          all((notes[k] is None) == (w is None) for k, (_m, w) in rows.items())
          and all("graph revision 0, this code 1" in notes[k] and S.GRAPH_REV_CHANGES[1] in notes[k]
                  for k, (_m, w) in rows.items() if w is not None), notes)
    with _env(**{k: None for k in list(os.environ) if k.startswith("DEEPMAP_")}):
        r = S.make_build_recipe(engine="graphify", extraction={"graphify_version": "0.8.39"}, route_source="registry",
                                neo4j_uri=ROUTE, code_version=VERSION)
    check("B: make_build_recipe records graph_rev == GRAPH_REV (1)", isinstance(r, dict) and "graph_rev" in r
          and r.get("graph_rev") == S.GRAPH_REV == 1 and type(r.get("graph_rev")) is int, r)
    with _patched(S, GRAPH_REV=7):
        r7 = S.make_build_recipe(engine=None, extraction=None, route_source=None, neo4j_uri=None, code_version=None)
    check("B: make_build_recipe reads GRAPH_REV when it is called (a raised revision is what the next sync records)",
          r7.get("graph_rev") == 7, r7.get("graph_rev"))
    check("B: revision 1 is pre-registered: GRAPH_REV_SCOPE[1] is _rev1_applies, with its words",
          S.GRAPH_REV_SCOPE.get(1) is getattr(S, "_rev1_applies", None) and bool(S.GRAPH_REV_CHANGES.get(1)),
          (S.GRAPH_REV_SCOPE, S.GRAPH_REV_CHANGES))


# =====================================================================================================================
# C - recorded_graph_rev
# =====================================================================================================================
def gate_recorded(W: World) -> None:
    print("\n-- C. recorded_graph_rev: absent or malformed is 0, a whole number is itself --")
    S = W.S
    rows = [("no build_recipe", m063(build_recipe=None), 0), ("recipe without the field", m063(), 0),
            ("0", with_rev(m063(), 0), 0), ("1", with_rev(m063(), 1), 1), ("3", with_rev(m063(), 3), 3),
            ("2.0 (JSON float)", with_rev(m063(), 2.0), 2), ("true", with_rev(m063(), True), 0),
            ("-1", with_rev(m063(), -1), 0), ('"2"', with_rev(m063(), "2"), 0), ("2.5", with_rev(m063(), 2.5), 0),
            ("null", with_rev(m063(), None), 0), ("a list", with_rev(m063(), [1]), 0),
            ("build_recipe not a dict", m063(build_recipe=[1, 2]), 0), ("top-level graph_rev only", m063(graph_rev=5),
                                                                        0)]
    bad = [(label, S.recorded_graph_rev(m), want) for label, m, want in rows if S.recorded_graph_rev(m) != want]
    check(f"C: all {len(rows)} rows (only build_recipe.graph_rev counts)", not bad, bad)


# =====================================================================================================================
# D - revision 1's scope
# =====================================================================================================================
def _verdict_reason(lang: str, files: int = 12, nodes: int = 0):
    """The LOW COVERAGE reason the REAL verdict writes for a sparse `lang` layer (None when it writes none)."""
    V = importlib.import_module("verdict")
    with _env(DEEPMAP_EXCLUDE_TESTS=None):
        out = V._coverage_reasons({"languages": {lang: {"files": files, "nodes": nodes}}, "dirs": {}})
    return [r for r in out if r.startswith("LOW COVERAGE for ")]


def gate_scope(W: World) -> None:
    print("\n-- D. revision 1's scope (the census of languages this install cannot parse; the SQL reader) --")
    S = W.S
    f = getattr(S, "_rev1_applies")

    def cov(**langs):
        return {"languages": {k.replace("_", "#"): {"files": v, "nodes": 0 if k != "C_" else 300}
                              for k, v in langs.items()}, "dirs": {}}

    def rs(*reasons):
        return m_clean(reasons=list(reasons), verdict="DEGRADED")

    rows = [
        ("no coverage key", m063(coverage=None), True),
        ("coverage null", m063(coverage=None) | {"coverage": None}, True),
        ("coverage a list", m063(coverage=[1]), True),
        ("SQL-free, warning-free (C# only)", m_clean(), False),
        ("an empty census (a failed census: {languages: {}, dirs: {}})", m063(coverage={"languages": {}, "dirs": {}}),
         False),
        ("census SQL 12", m063(coverage=cov(C_=20, SQL=12)), True),
        ("census SQL 0", m063(coverage=cov(C_=20, SQL=0)), False),
        ("census DM 1", m063(coverage=cov(DM=1)), True),
        ("census Perl 2", m063(coverage=cov(Perl=2)), True),
        ("census R 3", m063(coverage=cov(R=3)), True),
        ("census Ruby 5, TypeScript 9 (R / Perl are whole names)", m063(coverage=cov(Ruby=5, TypeScript=9)), False),
        ("census SQL files as text '12'", m063(coverage={"languages": {"SQL": {"files": "12"}}, "dirs": {}}), True),
        ("census SQL entry malformed", m063(coverage={"languages": {"SQL": {"files": "many"}}, "dirs": {}}), False),
        ("census SQL entry not a dict", m063(coverage={"languages": {"SQL": 12}, "dirs": {}}), False),
        ("census languages not a dict", m063(coverage={"languages": [["SQL", 12]], "dirs": {}}), False),
        ("parser_missing .sql (0.6.3)", m063(), True),
        ("parser_missing empty", m063(coverage={"languages": {}, "dirs": {}, "parser_missing": {}}), False),
        ("not_parsed .pl", m063(coverage={"languages": {}, "dirs": {}, "not_parsed": {".pl": {"files": 6}}}), True),
        ("reason LOW COVERAGE for SQL (0.6.2)", m062(), True),
        ("reason LOW COVERAGE for R", rs("LOW COVERAGE for R \u2014 6 file(s) on disk but 0 extracted"), True),
        ("reason LOW COVERAGE for Perl", rs("LOW COVERAGE for Perl \u2014 6 files"), True),
        ("reason LOW COVERAGE for DM", rs("LOW COVERAGE for DM \u2014 6 files"), True),
        ("reason LOW COVERAGE for Python (.pyi)", rs("LOW COVERAGE for Python \u2014 10 files"), True),
        ("reason LOW COVERAGE for JavaScript (.cjs)", rs("LOW COVERAGE for JavaScript \u2014 6 files"), True),
        ("reason LOW COVERAGE for Ruby (not R)", rs("LOW COVERAGE for Ruby \u2014 12 files"), False),
        ("reason LOW COVERAGE for Rust (not R)", rs("LOW COVERAGE for Rust \u2014 12 files"), False),
        ("reason LOW COVERAGE for TypeScript", rs("LOW COVERAGE for TypeScript \u2014 12 files"), False),
        ("reason LOW COVERAGE for C#", rs("LOW COVERAGE for C# \u2014 12 files"), False),
        ("reason LOW COVERAGE for SQLite-like name 'SQLX'", rs("LOW COVERAGE for SQLX \u2014 12 files"), False),
        ("reason LOW COVERAGE for CUDA, Metal (upgradeable)", rs("LOW COVERAGE for CUDA, Metal \u2014 3 .cu"), False),
        ("reason LOW COVERAGE for CUDA, R (a list naming R)", rs("LOW COVERAGE for CUDA, R \u2014 3 .cu"), True),
        ("reason LOW COVERAGE in app/models/", rs("LOW COVERAGE in app/models/ \u2014 12 source files"), False),
        ("reason LOW COVERAGE (long paths)", rs("LOW COVERAGE \u2014 3 source file(s) have a full path"), False),
        ("reason lower-case 'low coverage for SQL'", rs("low coverage for SQL \u2014 x"), False),
        ("reason ' LOW COVERAGE for SQL' (not at the start)", rs("note: LOW COVERAGE for SQL \u2014 x"), False),
        ("reasons not a list", m_clean(reasons="LOW COVERAGE for SQL \u2014 x"), False),
        ("reasons with a non-text entry", m_clean(reasons=[None, 7, "LOW COVERAGE for R \u2014 x"]), True),
    ]
    bad = []
    for label, m, want in rows:
        try:
            got = bool(f(m))
        except Exception as exc:  # noqa: BLE001
            got = f"raised {type(exc).__name__}"
        if got != want:
            bad.append((label, got, want))
    check(f"D: _rev1_applies, all {len(rows)} rows exactly as designed", not bad, bad)
    with _patched(S, GRAPH_REV=1):
        bad = [(label, S.graph_rev_due(m), want) for label, m, want in rows
               if (S.graph_rev_due(m) == 1) != want]
    check("D: graph_rev_due at GRAPH_REV 1 follows the scope on every row (1 when covered, None otherwise)", not bad,
          bad)
    # drift lock: the reason heads the REAL verdict writes
    try:
        got = {lang: _verdict_reason(lang) for lang in ("SQL", "R", "Perl", "DM", "Python", "JavaScript", "C#", "Ruby")}
    except Exception as exc:  # noqa: BLE001
        got = {"error": f"{type(exc).__name__}: {exc}"}
    covered = {lang: [bool(f(rs(r))) for r in got.get(lang) or []] for lang in got if lang != "error"}
    check("D: drift lock: the real verdict writes a LOW COVERAGE reason for a sparse layer of each language",
          "error" not in got and all(got.get(lang) for lang in covered), got)
    check("D: drift lock: those reasons are covered for SQL, R, Perl, DM, Python, JavaScript and not for C# / Ruby",
          all(all(covered[lang]) and covered[lang] for lang in ("SQL", "R", "Perl", "DM", "Python", "JavaScript"))
          and not any(covered.get("C#") or []) and not any(covered.get("Ruby") or []), covered)


# =====================================================================================================================
# E - graph_rev_due / graph_rev_note with patched revisions
# =====================================================================================================================
def gate_due(W: World) -> None:
    print("\n-- E. graph_rev_due / graph_rev_note: the highest covering revision above the recorded one --")
    S = W.S
    with _patched(S, GRAPH_REV=1):
        rows = [("0.6.3 SQL, no field", m063(), 1), ("0.6.2 SQL DEGRADED, no field", m062(), 1),
                ("0.6.3 SQL, recorded 0", with_rev(m063(), 0), 1), ("up to date: recorded 1", with_rev(m063(), 1),
                                                                    None),
                ("built by newer code: recorded 2", with_rev(m063(), 2), None), ("clean C#, no field", m_clean(), None),
                ("no coverage, no field", m063(coverage=None), 1), ("not a dict", None, None)]
        bad = [(label, S.graph_rev_due(m), want) for label, m, want in rows if S.graph_rev_due(m) != want]
        check(f"E: revision 1: all {len(rows)} manifests", not bad, bad)
        note = S.graph_rev_note(m063())
        check("E: graph_rev_note names the recorded and the current revision and what changed, without em / en dash",
              isinstance(note, str) and "graph revision 0" in note and "this code 1" in note
              and S.GRAPH_REV_CHANGES[1] in note and not any(d in note for d in DASHES), note)
        check("E: graph_rev_note is None for an up-to-date graph", S.graph_rev_note(with_rev(m063(), 1)) is None)

    t, f_ = (lambda m: True), (lambda m: False)

    def boom(m):
        raise RuntimeError("scope failed")

    cases = [
        ("only rev 2 covers, recorded 0 -> 2", {1: f_, 2: t, 3: f_}, 3, m063(), 2),
        ("all cover, recorded 1 -> 3 (the highest)", {1: t, 2: t, 3: t}, 3, with_rev(m063(), 1), 3),
        ("rev 1 covers, recorded 1 -> None (never at or below the recorded one)", {1: t, 2: f_, 3: f_}, 3,
         with_rev(m063(), 1), None),
        ("rev 1 covers, 2 and 3 do not, recorded 0 -> 1", {1: t, 2: f_, 3: f_}, 3, m063(), 1),
        ("recorded 3 == GRAPH_REV -> None", {1: t, 2: t, 3: t}, 3, with_rev(m063(), 3), None),
        ("a pre-registered rev above GRAPH_REV is inert", {1: f_, 2: t}, 1, m063(), None),
        ("a revision without a scope covers nothing", {1: f_, 3: f_}, 3, m063(), None),
        ("a raising scope -> None (never a rebuild on a guess)", {1: t, 2: boom}, 2, m063(), None),
        ("a truthy non-bool scope result counts", {1: (lambda m: "yes")}, 1, m063(), 1),
    ]
    bad = []
    for label, scope, cur, m, want in cases:
        with _patched(S, GRAPH_REV=cur, GRAPH_REV_SCOPE=scope,
                      GRAPH_REV_CHANGES={k: f"change {k}" for k in scope}):
            try:
                got = S.graph_rev_due(m)
            except Exception as exc:  # noqa: BLE001
                got = f"raised {type(exc).__name__}"
        if got != want:
            bad.append((label, got, want))
    check(f"E: synthetic revisions: all {len(cases)} cases", not bad, bad)
    with _patched(S, GRAPH_REV=3, GRAPH_REV_SCOPE={1: t, 2: f_, 3: t},
                  GRAPH_REV_CHANGES={1: "change one", 2: "change two", 3: "change three"}):
        note = S.graph_rev_note(m063())
    check("E: the note lists what every covering revision above the recorded one changed (not the others)",
          isinstance(note, str) and "change one" in note and "change three" in note and "change two" not in note
          and "graph revision 0" in note and "this code 3" in note, note)


# =====================================================================================================================
# F - freshness()
# =====================================================================================================================
def gate_freshness(W: World) -> None:
    print("\n-- F. freshness(): unchanged sources but a due graph revision -> stale 'graph-rev' --")
    S, A = W.S, W.A
    c = W.case("fresh")
    c.install()

    def fr(manifest, fp=None, dirty=False, content=None):
        c.sync.fp = dict({"value": FP1, "kind": "git", "head": H1, "git_busy": None, "truncated": False}, **(fp or {}))
        c.sync.dirty, c.sync.content_hash = dirty, content
        r = A.freshness(c.root, manifest)
        return (r["state"], r["why"]), r

    legacy = {k: v for k, v in m063().items() if k not in ("source_fingerprint", "build_recipe")}
    legacy_clean = {k: v for k, v in m_clean().items() if k not in ("source_fingerprint", "build_recipe")}
    nongit = {"built_at_content_hash": "abc", "nodes": 3, "coverage": m063()["coverage"]}
    with _patched(S, GRAPH_REV=1):
        rows = [
            ("fingerprint fresh + no graph_rev + parser_missing .sql", fr(m063())[0], ("stale", "graph-rev")),
            ("fingerprint fresh + no graph_rev + census SQL (0.6.2)", fr(m062())[0], ("stale", "graph-rev")),
            ("graph_rev == GRAPH_REV", fr(with_rev(m063(), 1))[0], ("fresh", None)),
            ("SQL-free, reason-free, no graph_rev (no mass rebuild)", fr(m_clean())[0], ("fresh", None)),
            ("no coverage block", fr(m063(coverage=None))[0], ("stale", "graph-rev")),
            ("a persisted LOW COVERAGE for R", fr(m_clean(reasons=["LOW COVERAGE for R \u2014 6 files"]))[0],
             ("stale", "graph-rev")),
            ("sources changed keep their own reason", fr(m063(), {"value": FP2})[0], ("stale", "sources-changed")),
            ("HEAD moved keeps its own reason", fr(m063(), {"value": FP2, "head": H2})[0], ("stale", "head-moved")),
            ("embeddings pending keeps its own reason", fr(m063(embeddings_pending=True))[0],
             ("stale", "embeddings-pending")),
            ("git busy is not a graph revision", fr(m063(), {"value": None, "git_busy": "merge"})[0],
             ("git-busy", "git-busy:merge")),
            ("legacy git manifest, clean, HEAD == built, due", fr(legacy, {"value": FP2})[0], ("stale", "graph-rev")),
            ("legacy git manifest, clean, not covered", fr(legacy_clean, {"value": FP2})[0], ("fresh", None)),
            ("legacy non-git, same content, due", fr(nongit, {"value": FP2, "kind": "fs", "head": None},
                                                     content="abc")[0], ("stale", "graph-rev")),
        ]
        bad = [(label, got, want) for label, got, want in rows if got != want]
        check(f"F: all {len(rows)} rows (the graph revision is read only where the sources say fresh)", not bad, bad)
        _st, r = fr(m063())
        check("F: the result keeps exactly its five keys", set(r) == {"state", "why", "head", "fingerprint", "kind"},
              sorted(r))

        def boom(m):
            raise RuntimeError("rule failed")

        with _patched(S, graph_rev_due=boom):
            got1 = fr(m063())[0]
        stub = SimpleNamespace()                       # a sync_status without the rule (an older build)
        A._load_status = lambda: stub
        got2 = fr(m063())[0]

        def no_status():
            raise ImportError("no sync_status")

        A._load_status = no_status
        got3 = fr(m063())[0]
        A._load_status = lambda: S
        check("F: graph_rev_due raising, a sync_status without it, or none loadable -> fresh (never a rebuild on a "
              "guess)", got1 == got2 == got3 == ("fresh", None), (got1, got2, got3))
    got = fr(m063())[0], fr(m062())[0], fr(m063(coverage=None))[0]
    check("F: at the shipped GRAPH_REV the 0.6.3 / 0.6.2 / no-coverage manifests are stale 'graph-rev'",
          all(g == ("stale", "graph-rev") for g in got), got)
    got = fr(m_clean())[0], fr(with_rev(m063(), 1))[0]
    check("F: ...and a clean graph and one this code wrote are fresh (no mass rebuild)",
          all(g == ("fresh", None) for g in got), got)
    W.restore()


# =====================================================================================================================
# G - the runner
# =====================================================================================================================
def gate_runner(W: World) -> None:
    print("\n-- G. the runner: rebuild once, then fresh; nothing for an up-to-date graph or with auto-sync off --")
    S, A = W.S, W.A
    with _patched(S, GRAPH_REV=1):
        c = W.case("run")
        rc1 = c.run()
        lr1 = c.last()
        m1 = c.manifest()
        check("G: a due graph with unchanged sources is rebuilt: one sync, rc 0, outcome synced",
              rc1 == 0 and len(c.sync.calls) == 1 and lr1.get("outcome") == "synced", (rc1, lr1, c.log[-600:]))
        check("G: the sync is the usual background one (cleanup_stale False, prune_policy evidence)",
              c.sync.calls[:1] and c.sync.calls[0].get("cleanup_stale") is False
              and c.sync.calls[0].get("prune_policy") == "evidence", c.sync.calls)
        check("G: the rebuilt manifest records graph_rev 1 (through the real make_build_recipe)",
              (m1.get("build_recipe") or {}).get("graph_rev") == 1, m1.get("build_recipe"))
        check("G: last_run.graph_rev names the revision this pass rebuilt for", lr1.get("graph_rev") == 1, lr1)
        check("G: the log names the graph revision (recorded 0, this code 1) and the one-time rebuild",
              "graph revision 0" in c.log and "this code 1" in c.log and "once" in c.log, c.log[-900:])
        c.clock.t += A.COOLDOWN_S + 1
        rc2 = c.run()
        lr2 = c.last()
        check("G: the next session start finds it fresh: rc 0, outcome fresh, no second sync, fresh_at_head",
              rc2 == 0 and len(c.sync.calls) == 1 and lr2.get("outcome") == "fresh"
              and lr2.get("fresh_at_head") == H1, (rc2, lr2, c.log[-600:]))
        check("G: a fresh run records no graph_rev", "graph_rev" not in lr2, lr2)
        hs = A._load_hook_state()
        with c.env():
            proof2 = hs._fresh_proof(c.root, c.manifest(), H1)
        c.clock.t += A.COOLDOWN_S + 1
        rc3 = c.run()
        check("G: and stays fresh (stale ONCE)", rc3 == 0 and len(c.sync.calls) == 1
              and c.last().get("outcome") == "fresh", (rc3, c.last()))

        # I - the hooks' proof (kept here: it reads the runs above)
        print("\n-- I. the hooks' proof (deepmap_hook_state._fresh_proof) reads the runs as before --")
        c0 = W.case("proof")
        c0.run()
        with c0.env():
            proof1 = hs._fresh_proof(c0.root, c0.manifest(), H1)
        check("I: after the graph-revision rebuild (outcome synced) the hooks have no 'fresh' proof", proof1 is None,
              proof1)
        check("I: after the next start's 'fresh' the hooks' proof is there (fresh_at_head, the manifest's "
              "fingerprint)", isinstance(proof2, dict) and proof2.get("fresh_at_head") == H1
              and proof2.get("fingerprint") == FP1, proof2)

        # a rebuild that is not pushed leaves the graph due
        c = W.case("notpushed")
        c.sync.plan = ["not-pushed"]
        rc1 = c.run()
        c.clock.t += A.COOLDOWN_S + 1
        rc2 = c.run()
        check("G: a rebuild that is not pushed keeps the graph due: the next start syncs again, then it is current",
              rc1 == 4 and c.last().get("outcome") == "synced" and len(c.sync.calls) == 2 and rc2 == 0
              and (c.manifest().get("build_recipe") or {}).get("graph_rev") == 1, (rc1, rc2, c.last()))

        for label, m in (("an up-to-date graph (graph_rev 1)", None), ("a SQL-free graph without graph_rev", "clean")):
            c = W.case("noop")
            if m is None:
                c.write_manifest(with_rev(m063(c.home), 1))
            else:
                c.write_manifest(m_clean(c.home))
            rc = c.run()
            check(f"G: {label}: fresh, no sync", rc == 0 and not c.sync.calls and c.last().get("outcome") == "fresh",
                  (rc, c.last(), c.log[-400:]))

        c = W.case("off")
        rc = c.run(DEEPMAP_AUTO_SYNC="0")
        check("G: DEEPMAP_AUTO_SYNC=0: no sync, rc 4, no record", rc == 4 and not c.sync.calls and not c.state(),
              (rc, c.state()))
        c = W.case("offinst")
        _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"enabled": False}})
        rc = c.run()
        check("G: the project switch off: no sync, rc 4, no record", rc == 4 and not c.sync.calls and not c.state(),
              (rc, c.state()))

        # a second runner that finds the lock held queues nothing for a graph revision
        c = W.case("coalesce")
        c.install()
        handle = S.try_lock(c.root / ".deepmap" / A.LOCK_FILE)
        try:
            rc = c.run()
        finally:
            S.release_lock(handle)
        check("G: a runner that finds the lock held (graph revision due, sources unchanged) ends coalesced, queues "
              "no re-run and never syncs", rc == 0 and not c.sync.requests and not c.sync.calls
              and "coalesced" in c.log and not (c.root / ".deepmap" / "sync.rerun").exists(),
              (rc, c.sync.requests, c.log[-500:]))
        # ... and the control: a due graph whose sources changed too still queues its one re-run
        c = W.case("coalsrc")
        c.sync.fp["value"] = FP2
        c.install()
        handle = S.try_lock(c.root / ".deepmap" / A.LOCK_FILE)
        try:
            rc = c.run()
        finally:
            S.release_lock(handle)
        reqs = c.sync.requests
        check("G: a runner that finds the lock held for a due graph whose SOURCES changed queues exactly one "
              "unattended re-run (edits made meanwhile are never dropped) and never syncs itself",
              rc == 0 and len(reqs) == 1 and reqs[0].get("origin") == "unattended" and not c.sync.calls
              and "queued a re-run" in c.log, (rc, reqs, c.log[-500:]))
        gate_once(W, S, A)
    c = W.case("shipped")
    rc = c.run()
    lr = c.last()
    check("G: at the shipped GRAPH_REV the 0.6.3 SQL graph is rebuilt once: one sync, outcome synced, "
          "last_run.graph_rev 1, the manifest records graph_rev 1", rc == 0 and len(c.sync.calls) == 1
          and lr.get("outcome") == "synced" and lr.get("graph_rev") == 1
          and (c.manifest().get("build_recipe") or {}).get("graph_rev") == 1, (rc, lr, c.log[-600:]))
    c.clock.t += A.COOLDOWN_S + 1
    rc = c.run()
    check("G: ...and the next start finds it fresh", rc == 0 and len(c.sync.calls) == 1
          and c.last().get("outcome") == "fresh", (rc, c.last()))
    c = W.case("shipclean")
    c.write_manifest(m_clean(c.home))
    rc = c.run()
    check("G: at the shipped GRAPH_REV a clean graph without a revision is fresh: no sync (no mass rebuild)",
          rc == 0 and not c.sync.calls and c.last().get("outcome") == "fresh", (rc, c.last()))
    W.restore()


def gate_once(W: World, S, A) -> None:
    """G, once per revision (GRAPH_REV is patched to 1 by the caller)."""
    print("\n-- G. once per revision: an older DeepMap rewriting the manifest never makes the refresh rebuild again --")
    c = W.case("again")
    rc0 = c.run()
    rec = c.state().get("graph_rev_rebuilt")
    check("G: the one-time rebuild is recorded (autosync.json graph_rev_rebuilt.rev 1)",
          rc0 == 0 and len(c.sync.calls) == 1 and isinstance(rec, dict) and rec.get("rev") == 1, (rc0, rec))
    c.old_writer(H2)
    lvl, text = c.status()
    check("H: an older DeepMap rewrote the manifest after the one-time rebuild: the status never promises 'rebuilds it "
          "once' again; it names the due graph and /deep-map:refresh", "rebuilds it once" not in text
          and "graph revision 0" in text and "/deep-map:refresh rebuilds it" in text.split("graph revision 0", 1)[-1],
          (lvl, text))
    runs = []
    for head in (H2, H3, H4):
        if head != H2:
            c.old_writer(head)
        c.clock.t += 600                   # the next session start, at the new HEAD: no cooldown
        rc = c.run()
        lr = c.last()
        runs.append((rc, len(c.sync.calls), lr.get("outcome"), lr.get("why_code")))
    check("G: three commits through an older DeepMap after the one-time rebuild, each followed by a session start at "
          "the new HEAD: no further rebuild (one sync in all), each start a recorded skip graph-rev-again, rc 4",
          runs == [(4, 1, "skipped:graph-rev-again", "graph-rev-again")] * 3, (runs, c.log[-700:]))
    lr = c.last()
    reason = str(lr.get("reason") or "")
    check("G: the skip's reason names the older DeepMap (0.6.2), the revision, the likely cause (an older install's "
          "git post-commit hook) and suggests /deep-map:setup (it names a stale hook); no em / en dash, never clipped",
          "0.6.2" in reason and "graph revision 1" in reason and "post-commit hook" in reason
          and lr.get("suggest") == "setup" and not any(d in reason for d in DASHES) and not reason.endswith("…"),
          lr)
    st = c.state()
    check("G: the skip carries no last_run.graph_rev and never backs off or pauses", "graph_rev" not in lr
          and not st.get("consecutive_failures") and not st.get("paused") and not st.get("backoff_until_epoch"), st)
    long_reason = A._why("graph-rev-again", **A._again_kw({"build_recipe": {"deepmap_version": "0." * 60}}, 123))[0]
    check("G: the reason fits the record unclipped even for a 120-character version (the version itself is clipped)",
          isinstance(long_reason, str) and len(long_reason) <= A.REASON_MAX and not long_reason.endswith("…")
          and "post-commit hook" in long_reason, long_reason)
    lvl2, text2 = c.status()
    check("H: after the skip: OK, the skip and its reason, no 'rebuilds it once', /deep-map:refresh rebuilds it",
          lvl2 == "OK" and "skipped:graph-rev-again" in text2 and "0.6.2" in text2 and "rebuilds it once" not in text2
          and "/deep-map:refresh rebuilds it" in text2, (lvl2, text2))
    ident = S.process_identity()
    st["current"] = {"id": "r", "pid": ident["pid"], "pid_created": ident["pid_created"], "state": "checking",
                     "started_epoch": c.clock.t}
    _write(c.root / ".deepmap" / "autosync.json", st)
    lvl3, text3 = c.status()
    st["current"] = None
    _write(c.root / ".deepmap" / "autosync.json", st)
    check("H: a refresh running after the one-time rebuild never claims it rebuilds the due graph; /deep-map:refresh "
          "does", "running since" in text3 and "which this refresh rebuilds" not in text3
          and "/deep-map:refresh rebuilds it" in text3.split("graph revision 0", 1)[-1], (lvl3, text3))
    c.sync.fp["value"] = FP2                # changed sources: a normal refresh, never held back by the once rule
    c.clock.t += A.COOLDOWN_S + 1
    rc = c.run()
    check("G: after the skip, changed sources still sync (a normal refresh; it records the current revision)",
          rc == 0 and len(c.sync.calls) == 2 and c.last().get("outcome") == "synced"
          and (c.manifest().get("build_recipe") or {}).get("graph_rev") == 1, (rc, c.last(), c.log[-500:]))

    c = W.case("again2")                    # a NEWER covering revision is rebuilt once again
    c.run()
    c.old_writer(H2)
    with _patched(S, GRAPH_REV=2, GRAPH_REV_SCOPE={1: S._rev1_applies, 2: lambda m: True},
                  GRAPH_REV_CHANGES={1: S.GRAPH_REV_CHANGES[1], 2: "a test change"}):
        c.clock.t += 600
        rc = c.run()
        lr = c.last()
        rec = c.state().get("graph_rev_rebuilt")
        c.old_writer(H3)
        c.clock.t += 600
        rc2 = c.run()
    check("G: a newer revision covering the graph is rebuilt once (graph_rev 2 recorded), then the same older writer "
          "makes no further rebuild", rc == 0 and lr.get("outcome") == "synced" and lr.get("graph_rev") == 2
          and isinstance(rec, dict) and rec.get("rev") == 2 and rc2 == 4 and len(c.sync.calls) == 2
          and c.last().get("outcome") == "skipped:graph-rev-again", (rc, lr, rec, rc2, c.last()))

    c = W.case("againx")                    # R16's extra pass ends the run: the one-time rebuild is still recorded
    c.sync.plan = ["rebuild+rerun", "rebuild"]
    rc = c.run()
    lr = c.last()
    rec = c.state().get("graph_rev_rebuilt")
    check("G: a graph-revision rebuild followed by R16's extra pass: two syncs, outcome synced, last_run.graph_rev 1, "
          "the one-time rebuild recorded", rc == 0 and len(c.sync.calls) == 2 and lr.get("outcome") == "synced"
          and lr.get("graph_rev") == 1 and isinstance(rec, dict) and rec.get("rev") == 1, (rc, lr, rec, c.log[-600:]))
    c.old_writer(H2)
    c.clock.t += 600
    rc = c.run()
    check("G: ... so an older writer after it makes no third sync", rc == 4 and len(c.sync.calls) == 2
          and c.last().get("outcome") == "skipped:graph-rev-again", (rc, c.last()))

    c = W.case("againnp")                   # a pass that was not pushed is not the one-time rebuild
    c.sync.plan = ["not-pushed"]
    c.run()
    check("G: a graph-revision pass that is not pushed records no one-time rebuild",
          "graph_rev_rebuilt" not in c.state(), c.state())


# =====================================================================================================================
# H - status_line
# =====================================================================================================================
def gate_status(W: World) -> None:
    print("\n-- H. status_line names a due graph revision, the rebuild, and nothing otherwise --")
    S, A = W.S, W.A
    with _patched(S, GRAPH_REV=1):
        c = W.case("st")
        lvl, text = c.status()
        check("H: a due graph, no run yet: OK, names both revisions and the one-time rebuild",
              lvl == "OK" and "graph revision 0" in text and "this code 1" in text and "rebuilds it once" in text,
              (lvl, text))
        tail = text.split("starts)", 1)[-1]
        check("H: the added words carry no em / en dash", not any(d in tail for d in DASHES), tail)
        c.run()
        lvl2, text2 = c.status()
        check("H: after the rebuild: the last run names the one-time rebuild for graph revision 1; no due text",
              "graph revision 1" in text2 and "rebuilds it once" not in text2 and "this code 1" not in text2,
              (lvl2, text2))
        c = W.case("stfail")
        c.write_manifest(m063(c.home))
        _write(c.root / ".deepmap" / "autosync.json",
               {"schema": 1, "current": None, "last_run": {"id": "x", "outcome": "failed:error", "rc": 1,
                                                            "reason": "boom", "finished_epoch": c.clock.t - 60,
                                                            "wall_s": 3.0, "trigger": "session-start"}})
        lvl3, text3 = c.status()
        check("H: a failed last run stays a WARN, and the due graph revision is named after it",
              lvl3 == "WARN" and "boom" in text3 and "graph revision 0" in text3, (lvl3, text3))
        c = W.case("stslow")
        c.write_manifest(m063(c.home, sync_wall_s=3000.0))
        lvl4, text4 = c.status()
        check("H: a refused next start (too slow): WARN with its reason, and /deep-map:refresh rebuilds the due graph",
              lvl4 == "WARN" and "will not refresh" in text4 and "graph revision 0" in text4
              and "/deep-map:refresh" in text4.split("graph revision 0", 1)[-1], (lvl4, text4))
        c = W.case("stcur")
        c.write_manifest(with_rev(m063(c.home), 1))
        lvl5, text5 = c.status()
        check("H: an up-to-date graph: no graph-revision words", "graph revision" not in text5, (lvl5, text5))
        c = W.case("stoff")
        c.write_manifest(with_rev(m063(c.home), 1))
        lvl6, text6 = c.status(DEEPMAP_AUTO_SYNC="0")
        check("H: auto-sync off, an up-to-date graph: exactly the off text",
              (lvl6, text6) == ("OK", "off (DEEPMAP_AUTO_SYNC=0)"), (lvl6, text6))
        # auto-sync off and a due graph: nothing rebuilds it on its own, so every off text says so
        c = W.case("stoffdue")
        switch = c.home / "auto-sync.json"
        inst = c.root / ".deepmap" / "instance.json"
        offs = {}
        for label, envkv, files in (("DEEPMAP_AUTO_SYNC=0", {"DEEPMAP_AUTO_SYNC": "0"}, ()),
                                    ("DEEPMAP_HOOKS=0", {"DEEPMAP_HOOKS": "0"}, ()),
                                    ("the machine switch", {}, (switch,)),
                                    ("the project switch", {}, (inst,))):
            for f in files:
                _write(f, {"enabled": False} if f == switch else {"auto_sync": {"enabled": False}})
            got = c.status(**envkv)
            with _patched(S, graph_rev_due=lambda manifest: None, graph_rev_note=lambda manifest: None):
                before = c.status(**envkv)
            for f in files:
                f.unlink()
            offs[label] = (got, before)
        bad = {}
        for label, (got, before) in offs.items():
            tail = got[1][len(before[1]):]
            if not (before[1].startswith("off (") and got[0] == before[0] and got[1].startswith(before[1] + "; ")
                    and "graph revision 0" in tail and "this code 1" in tail
                    and "/deep-map:refresh rebuilds it" in tail.split("graph revision 0", 1)[-1]
                    and "rebuilds it once" not in tail and not any(d in tail for d in DASHES)):
                bad[label] = (got, before)
        check("H: auto-sync off (DEEPMAP_AUTO_SYNC=0, DEEPMAP_HOOKS=0, the machine and the project switch) and a due "
              "graph: the off text and level as before, then the due graph and /deep-map:refresh rebuilds it",
              len(offs) == 4 and not bad, bad or offs)
        c = W.case("stpause")
        _write(c.root / ".deepmap" / "autosync.json",
               {"schema": 1, "current": None, "consecutive_failures": A.PAUSE_AFTER_FAILURES,
                "paused": {"reason": "failures", "since_epoch": c.clock.t - 100, "detail": "boom"},
                "last_failure": {"outcome": "failed:error", "reason": "boom", "finished_epoch": c.clock.t - 100}})
        lvl8, text8 = c.status()
        check("H: a paused refresh of a due graph: WARN, the paused text kept, then the due graph and "
              "/deep-map:refresh",
              lvl8 == "WARN" and text8.startswith("paused") and "graph revision 0" in text8
              and "/deep-map:refresh rebuilds it" in text8.split("graph revision 0", 1)[-1]
              and "rebuilds it once" not in text8, (lvl8, text8))
        c = W.case("strun")
        ident = S.process_identity()
        _write(c.root / ".deepmap" / "autosync.json",
               {"schema": 1, "current": {"id": "r", "pid": ident["pid"], "pid_created": ident["pid_created"],
                                         "state": "syncing", "started_epoch": c.clock.t}})
        lvl7, text7 = c.status()
        check("H: a running refresh of a due graph says it rebuilds it",
              "running since" in text7 and "graph revision 0" in text7, (lvl7, text7))
    for label, m in (("the 0.6.3 SQL graph", None), ("a 0.6.2 DEGRADED graph", "062")):
        c = W.case("stship")
        if m:
            c.write_manifest(m062(c.home))
        got = c.status()
        with _patched(S, graph_rev_due=lambda manifest: None, graph_rev_note=lambda manifest: None):
            want = c.status()
        check(f"H: at the shipped GRAPH_REV, {label}: the text without the rule, then the due sentence (graph revision "
              "0, this code 1) and 'rebuilds it once' (level kept)",
              got[0] == want[0] and got[1].startswith(want[1]) and "graph revision 0, this code 1" in got[1]
              and "rebuilds it once" in got[1], (got, want))
    c = W.case("stshipclean")
    c.write_manifest(m_clean(c.home))
    got = c.status()
    with _patched(S, graph_rev_due=lambda manifest: None, graph_rev_note=lambda manifest: None):
        want = c.status()
    check("H: at the shipped GRAPH_REV a clean graph: the text equals the one without the graph-revision rule",
          got == want and "graph revision" not in got[1], (got, want))
    W.restore()


# =====================================================================================================================
def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 76)
    print("DeepMap graph revision (sync_status.GRAPH_REV / graph_rev_due, auto_sync freshness 'graph-rev')")
    print("=" * 76)
    sys.path.insert(0, str(BUILD))
    base = Path(tempfile.mkdtemp(prefix="dm_graphrev_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_") or k in (
        "NEO4J_URI", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_ENTRYPOINT", "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE", "GIT_OPTIONAL_LOCKS")}
    scrub.update(DEEPMAP_HOME=str(base / "home0"), HF_HUB_CACHE=str(base / "hf0"))
    try:
        with _env(**scrub):
            S = importlib.import_module("sync_status")
            A = importlib.import_module("auto_sync")
            W = World(S, A, base)
            for label, fn in (("A", gate_surface), ("B", gate_baseline), ("C", gate_recorded), ("D", gate_scope),
                              ("E", gate_due), ("F", gate_freshness), ("G", gate_runner), ("H", gate_status)):
                _section(label, fn, W)
                W.restore()
    finally:
        _rmtree(base)
    print("\n" + "=" * 76)
    total = len(PASSES) + len(FAILS)
    if FAILS:
        print(f"RESULT: RED: {len(FAILS)} of {total} gates failed:")
        for name in FAILS:
            print(f"   - {name}")
        return 1
    print(f"RESULT: GREEN: {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
