#!/usr/bin/env python3
"""auto-sync S2 (the runner) — build/auto_sync.py: DeepMap's unattended background refresh at session start.

The SessionStart heal hook spawns `deepmap_cli.py sync <root> --unattended=session-start`, and the CLI hands the run to
auto_sync.run() before it imports sync. These gates lock the runner's contract (spec auto-sync-spec §1.2, §2.4,
§2.6.4, §2.9, §2.10.4, §2.10.6, §3.2) and the benchmark's opt-outs (M12):

  A. import hygiene and the public surface: stdlib-only imports at module level, importing it imports none of sync /
     neo4j / torch / graphify_extract / sync_status, only the six allowed DEEPMAP_* literals, the spec's constants and
     signatures; human_duration's table.
  B. attended() / enabled(): the full env x instance x machine x ATTENDED x entrypoint matrix; a `true` in a file never
     forces auto-sync on; the `status` trigger ignores who watches; the off-switches fail CLOSED (a bare
     {"auto_sync": false}, 0, an OFF word, a value or machine file DeepMap does not understand all mean off).
  C. precheck(): every code in order (hooks-off ... cooldown only with a head or announce), ok with took_s, error —
     and it starts no subprocess; remote-database (an operator NEO4J_URI off this machine); with a head, a recorded
     skip the next run would repeat (needs-timing, no-own-instance, user-request, recipe:*, heal-off, ...) until a sync
     rewrites the manifest.
  D. run(): the decision table — every R2-R17 ending and every §2.9.1 mapping: rc, the recorded last_run outcome /
     reason / suggest / advice, whether sync ran; silent ends leave no record. Also: no egress to a remote
     NEO4J_URI without an explicit DEEPMAP_AUTO_SYNC=1; the capped memory floor (25 %, 3-6 GiB) and its reason text;
     the 1.5 GiB headroom when only postponed embeddings are due; last_run.why_code. The recorded route is compared
     by ENDPOINT only (registry vs an operator NEO4J_URI naming the same instance, localhost vs 127.0.0.1, userinfo:
     never a skip); an operator NEO4J_URI with no recorded route skips (recipe:route-unrecorded); sync's
     "user-request-pending" is skipped:user-request and an "unowned" refusal skipped:unowned (neither backs off).
  E. the sync call: kwargs exactly cleanup_stale=False + prune_policy (evidence; none by DEEPMAP_AUTO_SYNC_PRUNE=0 or
     instance auto_sync.prune=false) + an UnattendedControl, never switch/force; the environment sync is first loaded
     under (offline, GIT_OPTIONAL_LOCKS=0, the replayed recipe, DEEPMAP_EMBED_THREADS untouched, the engine budget);
     engine_budget's table; pre_write vetoes; before_embed.
  F. single-flight (two runners, one sync, the loser queues one unattended request) and R16 (one more pass for a
     request left behind; none while a user's sync waits, and none for a USER's request — origin manual or none —
     which only a sync the user starts runs). R6c never loses a trigger: after queueing, the loser tries
     the lock again and takes over when the holder released it meanwhile or already made its one look at the queue
     (autosync.json current.rerun_look, published before that look); it waits for such a holder at most
     TAKEOVER_WAIT_S, and not at all for one that has yet to look.
  G. backoff / pause transitions and their lifting by a newer synced sync_last; the record schema; the hard-exit
     callback records `preempted` — or `skipped:busy-enrich` rc 4 when only deepmap enrich waited.
  H. _lower_priority: called once per run; really sets BELOW_NORMAL (Windows) / nice +10 (POSIX) — checked in a child —
     and lowers the I/O priority on Linux (ioprio_set best-effort level 7, only for a known syscall number; checked
     with ioprio_get in a child on Linux).
  I. status_line texts; set_enabled through instance_state.update_instance / the atomic machine switch.
  J. freshness(): the fingerprint / legacy table; the real _git_head on a temp repo passes GIT_OPTIONAL_LOCKS=0.
  K. main(argv): `python build/auto_sync.py run|status` in a child (ends before any sync).
  L. the benchmark: claude_child_env() carries DEEPMAP_AUTO_SYNC=0 + DEEPMAP_FRESHNESS_NOTE=0, clone_graph.child_env()
     forces DEEPMAP_AUTO_SYNC=0 even over an operator's 1 (M12).
  M. post-merge (automatic once the tree's sync.sync takes `unattended` and build/sync_status.py exists, or with
     --post-merge): the real sync_status / sync surfaces the runner calls, and sections C, D, F, G, O-R re-run
     against the REAL sync_status.
  N. the REAL SessionStart notify hook of the tree (a child, temp git repo + temp DEEPMAP_HOME): after a recorded
     skip that repeats (needs-timing, no-own-instance) it gives today's "suggest /deep-map:refresh", never "DeepMap is
     refreshing it in the background now"; a transient skip, or a manifest rewritten since, still gets the AUTO text.
  O. the background time limit reads the wall time of the last sync that did NOT run in the background: a slow
     below-normal pass never raises it (precheck, R10, two passes in one run), a sync the user started counts slow or
     fast (also one that wrote the manifest while this run only queued), and a background wall never stands in for a
     missing one (needs-timing stays).
  P. precheck(..., announce=True) — the hooks' contract: the repeat rule always, the cooldown with head None = the
     non-git time-only rule, a head no session start records never cools; skipped:user-request repeats while the
     user's request stays queued; status_line names every precheck block (too-slow, recipe:*, remote-database,
     backoff, a repeating skip) as WARN with its reason, never a cooldown.
  Q. a legacy manifest (no build_recipe): the refresh runs with the graph-shaping settings of the environment it was
     started from (DEEPMAP_PRECISE_CALLS=1 there -> sync runs with it), never stripped, never a recipe:settings skip;
     a recorded recipe still conflicts / replays as before.
  R. a user's queued re-run ends the run skipped:user-request before it waits for the database or the slot (unless a
     live sync of this project will run it; a fresh graph stays "fresh").

Hermetic: temp project dirs, a temp DEEPMAP_HOME and HF_HUB_CACHE, a fake `sync` (records every call and its kwargs),
a §2.3-conformant fake `sync_status` (the real one in M), every runner seam replaced by a recorder, a fake clock for
every wait. Never a real sync, never Neo4j, never the network; the only subprocesses are `git` in a temp repo and short
`python build/auto_sync.py` / priority children that end before any sync.
Usage: python tests/auto_sync_runner_gates.py [--root <tree>] [--post-merge]
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
SKIPS: list = []
GIB = 1024 ** 3
H1, H2, H3 = "a" * 40, "b" * 40, "c" * 40
FP1, FP2 = "f" * 32, "e" * 32
PY_XY = f"{sys.version_info[0]}.{sys.version_info[1]}"
ALLOWED_LITERALS = {"DEEPMAP_AUTO_SYNC", "DEEPMAP_AUTO_SYNC_MAX_S", "DEEPMAP_AUTO_SYNC_PRUNE", "DEEPMAP_HOOKS",
                    "DEEPMAP_HEAL", "DEEPMAP_HOME"}
WATCH_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "GIT_OPTIONAL_LOCKS", "PYTHONUTF8", "DEEPMAP_EMBED_THREADS",
             "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_GRAPHIFY_TIMEOUT", "DEEPMAP_EXTRACTOR", "DEEPMAP_PRECISE_CALLS")
NEXT_START_HEAD = "deepmap-hooks:next-session-start"     # == hooks/deepmap_hook_state._NEXT_START_HEAD
SEAMS = ("_load_sync", "_load_status", "_now", "_sleep", "_memory", "_route", "_heal_status", "_bolt_up",
         "_heal_problem", "_model_state", "_engine_file_count", "_graphify_version", "_graphify_budget",
         "_lower_priority", "_git_head", "_find_spec", "_running_version", "_touch_last_used")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}"))


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"  [SKIP] {name} — {why}")


def _root_arg(argv: list) -> Path:
    """The tree whose build/, hooks/ and benchmark/ are loaded. A --root without a value, or naming a tree without
    build/, is a usage error (exit 2) — never a silent fallback to this checkout."""
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/auto_sync_runner_gates.py [--root <tree holding build/>] [--post-merge] "
              f"(got {tree!r})", file=sys.stderr)
        raise SystemExit(2)
    return Path(tree).resolve()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _env(**kv):
    """The whole os.environ snapshotted and restored (run() writes the offline variables and the recipe into it)."""
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


def _rmtree(path: Path) -> None:
    """Remove the gate's OWN temp tree by its exact path — git's object files are read-only on Windows, which a bare
    rmtree(ignore_errors=True) leaves behind (with their directories)."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            pass
    shutil.rmtree(str(path), **({"onexc": onerr} if sys.version_info >= (3, 12) else {"onerror": onerr}))


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
    Path(path).write_text(json.dumps(obj), encoding="utf-8")


# =====================================================================================================================
# test doubles
# =====================================================================================================================
class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = float(t)
        self.mu = threading.Lock()
        self.slept = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s) -> None:
        with self.mu:
            self.t += float(s)
            self.slept += float(s)


class FakeControl:
    """sync_status.UnattendedControl (§2.3) — records what the runner gave it."""
    made: list = []

    def __init__(self, root, *, trigger, session=None, prune_policy="evidence", pre_write=None, before_embed=None,
                 on_hard_exit=None, poll_s=1.0, hard_exit=None):
        if prune_policy not in ("none", "evidence"):
            raise ValueError(prune_policy)
        self.root, self.trigger, self.session, self.prune_policy = Path(root), trigger, session, prune_policy
        self.pre_write, self.before_embed, self.on_hard_exit = pre_write, before_embed, on_hard_exit
        self.phase, self._yielded, self.ended = "start", None, 0
        FakeControl.made.append(self)

    def begin(self):
        pass

    def end(self):
        self.ended += 1

    def enter(self, phase):
        self.phase = phase

    def checkpoint(self, *, to_phase):
        self.phase = to_phase
        return self._yielded

    def yielded(self):
        return self._yielded


class FakeStatus:
    """A §2.3-conformant sync_status: real files under <root>/.deepmap, an in-process lock table, holders live iff
    they name this process with this process's stamp."""
    STAMP = 4242.5
    GRAPH_SHAPING_ENV = {"DEEPMAP_EXTRACTOR": "auto", "DEEPMAP_EXCLUDE_TESTS": "off", "DEEPMAP_PRECISE_CALLS": "off"}
    UnattendedControl = FakeControl
    _ON, _OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}

    def __init__(self):
        self.held: set = set()
        self.mu = threading.Lock()

    def canonical_env_value(self, name, raw):
        text = "" if raw is None else str(raw).strip()
        default = self.GRAPH_SHAPING_ENV.get(name)
        if default is None:
            return text
        if not text:
            return default
        if name == "DEEPMAP_EXTRACTOR":
            return text.lower()
        return "on" if text.lower() in self._ON else "off"

    def recipe_conflicts(self, recorded, env=None):
        e = os.environ if env is None else env
        rec = recorded if isinstance(recorded, dict) else {}
        out = []
        for name, default in self.GRAPH_SHAPING_ENV.items():
            raw = e.get(name)
            if raw is None or not str(raw).strip():
                continue
            cur = self.canonical_env_value(name, raw)
            built = self.canonical_env_value(name, rec.get(name)) if name in rec else default
            if cur != built:
                out.append((name, built, cur))
        return out

    def recipe_env(self, env=None):
        e = os.environ if env is None else env
        out = {}
        for name, default in self.GRAPH_SHAPING_ENV.items():
            value = self.canonical_env_value(name, e.get(name))
            if value != default:
                out[name] = value
        return out

    def replay_env(self, recorded, env=None):
        e = os.environ if env is None else env
        done = []
        for name, value in (recorded or {}).items():
            if name in self.GRAPH_SHAPING_ENV and value is not None and not str(e.get(name) or "").strip():
                e[name] = str(value)
                done.append(name)
        return done

    def make_build_recipe(self, *, engine, extraction, route_source, neo4j_uri, code_version, env=None):
        e = os.environ if env is None else env
        return {"extractor_requested": self.canonical_env_value("DEEPMAP_EXTRACTOR", e.get("DEEPMAP_EXTRACTOR"))}

    def try_lock(self, path):
        key = os.path.normcase(os.path.abspath(str(path)))
        with self.mu:
            if key in self.held:
                return None
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.held.add(key)
            return SimpleNamespace(key=key)

    def release_lock(self, handle):
        if handle is not None:
            with self.mu:
                self.held.discard(handle.key)

    def atomic_write_json(self, path, obj):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        return True

    def read_json(self, path):
        d = _read(path)
        return d if isinstance(d, dict) else None

    def process_identity(self):
        return {"pid": os.getpid(), "pid_created": self.STAMP}

    def holder_live(self, rec):
        if not isinstance(rec, dict):
            return None
        return rec.get("pid") == os.getpid() and rec.get("pid_created") == self.STAMP

    def read_running(self, root):
        rec = self.read_json(Path(root) / ".deepmap" / "sync_running.json")
        if rec is None:
            return None
        rec["live"] = self.holder_live(rec)
        return rec

    def read_last(self, root):
        return self.read_json(Path(root) / ".deepmap" / "sync_last.json")

    def live_preempts(self, root, *, now=None):
        out = []
        for p in sorted((Path(root) / ".deepmap").glob("sync.preempt.*.json")):
            rec = self.read_json(p)
            if rec and self.holder_live(rec):
                out.append(rec)
        return out

    def model_cache_state(self, env=None):
        return {"complete": True, "revision": "rev1", "path": None, "missing": []}

    def phase_words(self, phase):
        return {"waiting-db": "waiting for the database", "extract": "reading the code"}.get(str(phase), str(phase))


def _ok_synced(verdict="READY", **extra):
    def step(fs, root, ctl):
        lo = {"schema": 1, "id": f"S-{len(fs.calls)}", "origin": "unattended", "outcome": "synced", "rc": 0,
              "verdict": verdict, "reasons": [], "built_at_sha": fs.fp.get("head"), "dirty": False,
              "source_fingerprint": fs.fp.get("value"), "wall_s": 42.0, "stale_kept": 0,
              "embeddings_deferred": False}
        lo.update(extra)
        return True, lo
    return step


def _lo(outcome, ok=True, **fields):
    def step(fs, root, ctl):
        return ok, dict({"schema": 1, "id": f"S-{len(fs.calls)}", "outcome": outcome}, **fields)
    return step


class FakeSync:
    """The `sync` module the runner loads: records every sync() call (its kwargs and the environment it ran under),
    and answers source_fingerprint / git_busy / _request_rerun from settable fields."""
    SYNC_NOT_PUSHED_RC = 4
    _CODE_VERSION = "0.29.0"

    def __init__(self):
        self.calls: list = []
        self.fp = {"value": FP2, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
        self.busy = None
        self.requests: list = []
        self.plan: list = []
        self.last_outcome = None
        self.last_verdict = None
        self.last_not_pushed = None
        self.dirty = False
        self.content_hash = None
        self.fp_calls = 0

    def sync(self, workspace_root, **kw):
        self.calls.append((Path(workspace_root), dict(kw), {k: os.environ.get(k) for k in WATCH_ENV}))
        step = self.plan.pop(0) if self.plan else _ok_synced()
        ok, lo = step(self, Path(workspace_root), kw.get("unattended"))
        self.last_outcome = lo
        return ok

    def source_fingerprint(self, root):
        self.fp_calls += 1
        return dict(self.fp)

    def git_busy(self, root):
        return self.busy

    def _rerun_request_path(self, root):
        return Path(root) / ".deepmap" / "sync.rerun"

    def _request_rerun(self, root, *, cleanup_stale, force_prune, prune_policy=None, origin="manual", trigger=None):
        req = {"cleanup_stale": cleanup_stale, "force_prune": force_prune, "prune_policy": prune_policy,
               "origin": origin, "trigger": trigger}
        self.requests.append(req)
        _write(self._rerun_request_path(root), req)
        return True

    def _peek_rerun_request(self, root):
        # == sync._peek_rerun_request: the queued request without claiming it, None when absent / unreadable
        d = _read(self._rerun_request_path(root))
        return d if isinstance(d, dict) else None

    @staticmethod
    def _is_user_request(request):
        # == sync._is_user_request: origin "manual", or none at all (an older DeepMap's) — never run unattended
        return isinstance(request, dict) and bool(request) and request.get("origin") != "unattended"

    def _instance_lock_key(self, uri):
        from urllib.parse import urlsplit
        s = str(uri)
        u = urlsplit(s.strip())
        if (u.hostname or "").lower() not in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
            return s
        path = u.path if u.path not in ("", "/") else ""
        return f"{(u.scheme or 'bolt').lower()}://localhost:{u.port or 7687}{path}"

    def _git_worktree_dirty(self, root):
        return self.dirty

    def _fs_content_hash(self, root):
        return self.content_hash


# =====================================================================================================================
# the per-case harness
# =====================================================================================================================
class World:
    """Everything one gate run shares: the tree, the loaded auto_sync (A), the sync_status in use (ST), a temp base."""

    def __init__(self, tree: Path, A, base: Path, ST, label: str):
        self.tree, self.A, self.base, self.ST, self.label = tree, A, base, ST, label
        self.orig = _orig_seams(A)          # the real seams (built before any case installs its recorders)
        self.n = 0
        self.model_name = None
        try:
            src = (tree / "build" / "embeddings.py").read_text(encoding="utf-8")
            m = re.search(r'^MODEL_NAME\s*=\s*"([^"]+)"', src, re.M)
            self.model_name = m.group(1) if m else None
        except OSError:
            pass

    def case(self, **opts) -> "Case":
        self.n += 1
        return Case(self, self.base / f"{self.label}{self.n:03d}", **opts)


class Case:
    def __init__(self, world: World, d: Path, *, manifest=True, git=True):
        self.w, self.A, self.ST = world, world.A, world.ST
        self.dir = d
        self.root = d / "proj"
        self.home = d / "home"
        (self.root / ".deepmap").mkdir(parents=True, exist_ok=True)
        if git:
            (self.root / ".git").mkdir(exist_ok=True)     # the work tree's top for project_root's walk
        self.home.mkdir(parents=True, exist_ok=True)
        self.clock = Clock()
        self.sync = FakeSync()
        self.loaded_env = None
        self.lower = 0
        self.touched = 0
        self.heads = [H2]
        self.memory = [(8 * GIB, 16 * GIB)]
        self.mem_calls = 0
        self.heal = "ok"
        self.heal_seq = None
        self.bolt = [False]
        self.bolt_calls = 0
        self.route = SimpleNamespace(uri="bolt://127.0.0.1:17689", source="registry", problem=None)
        self.heal_prob = None
        self.model = {"complete": True, "revision": "rev1", "path": "x", "missing": []}
        self.files = 10
        self.file_limit = None
        self.gversion = "0.8.39"
        self.budget = {"timeout_s": 600, "source": "size-aware"}
        self.specs = {}
        self.version = "0.29.0"
        self.status_fail = False
        self.sleep_hook = None           # called once, after the next _sleep (e.g. another holder releasing)
        if manifest:
            self.write_manifest()

    # -- inputs
    def manifest_dict(self, **over) -> dict:
        m = {"nodes": 50, "verdict": "READY", "built_at_sha": H1, "dirty": False, "source_fingerprint": FP1,
             "sync_wall_s": 30.0, "graph_engines": ["graphify"], "extractor": "graphify",
             "graphify_version": "0.8.39", "deepmap_version": "0.29.0",
             "build_recipe": {"schema": 1, "deepmap_version": "0.29.0", "python": PY_XY,
                              "extractor_requested": "auto", "engine": "graphify", "graphify_version": "0.8.39",
                              "embed_model": self.w.model_name, "embed_model_revision": "rev1", "env": {},
                              "route": {"source": "registry", "uri": "bolt://127.0.0.1:17689"},
                              "deepmap_home": os.path.realpath(str(self.home)),
                              # what make_build_recipe records (the graph revision of the code in use: the REAL
                              # sync_status's in M, none for the fake): a graph this code built is never due for one
                              # (tests/graph_rev_gates.py locks the due cases)
                              "graph_rev": getattr(self.ST, "GRAPH_REV", 0)}}
        for k, v in over.items():
            if k.startswith("recipe_"):
                m["build_recipe"][k[len("recipe_"):]] = v
            elif v is None:
                m.pop(k, None)
            else:
                m[k] = v
        return m

    def write_manifest(self, **over) -> None:
        _write(self.root / ".deepmap" / "graph_build.json", self.manifest_dict(**over))

    def write_state(self, obj) -> None:
        _write(self.root / ".deepmap" / "autosync.json", obj)

    def write_last(self, **fields) -> None:
        rec = {"schema": 1, "id": "L1", "origin": "manual", "outcome": "synced", "verdict": "READY",
               "finished_epoch": self.clock.t}
        rec.update(fields)
        _write(self.root / ".deepmap" / "sync_last.json", rec)

    def write_running(self, *, origin="manual", phase="extract", fp=FP2, live=True) -> None:
        ident = self.ST.process_identity()
        _write(self.root / ".deepmap" / "sync_running.json",
               {"schema": 1, "id": "R1", "pid": os.getpid(),
                "pid_created": ident["pid_created"] if live else 1.0, "origin": origin, "phase": phase,
                "source_fingerprint": fp, "started_epoch": time.time(), "started_utc": "2026-10-22T00:00:00Z"})

    def write_preempt(self) -> Path:
        ident = self.ST.process_identity()
        p = self.root / ".deepmap" / f"sync.preempt.{os.getpid()}.json"
        _write(p, {"schema": 1, "pid": os.getpid(), "pid_created": ident["pid_created"], "by": "sync",
                   "ts_epoch": time.time(), "ts_utc": "x"})
        return p

    # -- the seams
    def install(self) -> None:
        A, c = self.A, self

        def load_sync():
            if c.loaded_env is None:
                c.loaded_env = {k: os.environ.get(k) for k in WATCH_ENV}
            return c.sync

        def load_status():
            if c.status_fail:
                raise ImportError("no sync_status")
            return c.ST

        def memory():
            c.mem_calls += 1
            return c.memory[min(c.mem_calls - 1, len(c.memory) - 1)]

        def heal_status(root):
            if c.heal_seq is not None:
                return c.heal_seq.pop(0) if len(c.heal_seq) > 1 else c.heal_seq[0]
            if c.heal == "ok":
                return {"ts_epoch": c.clock.t, "neo4j_ok": True, "refused": False, "refusal_kind": None}
            return None

        def bolt_up(root):
            c.bolt_calls += 1
            return c.bolt[min(c.bolt_calls - 1, len(c.bolt) - 1)]

        def git_head(root):
            return c.heads.pop(0) if len(c.heads) > 1 else c.heads[0]

        def engine_count(root, limit):
            c.file_limit = limit
            return c.files

        def lower():
            c.lower += 1

        def touch(root):
            c.touched += 1

        def sleep(s):
            c.clock.sleep(s)
            if c.sleep_hook is not None:
                hook, c.sleep_hook = c.sleep_hook, None
                hook()

        A._load_sync, A._load_status = load_sync, load_status
        A._now, A._sleep = c.clock.now, sleep
        A._memory, A._route = memory, (lambda root: c.route)
        A._heal_status, A._bolt_up = heal_status, bolt_up
        A._heal_problem = lambda root: c.heal_prob
        A._model_state = lambda: dict(c.model)
        A._engine_file_count, A._graphify_version = engine_count, (lambda: c.gversion)
        A._graphify_budget = lambda root: dict(c.budget)
        A._lower_priority, A._git_head = lower, git_head
        A._find_spec = lambda name: c.specs.get(name, object())
        A._running_version = lambda: c.version
        A._touch_last_used = touch

    def env(self, **kv):
        base = dict(DEEPMAP_HOME=str(self.home), HF_HUB_CACHE=str(self.dir / "hf"), CLAUDE_CODE_SESSION_ATTENDED="1",
                    CLAUDE_CODE_ENTRYPOINT=None)
        base.update(kv)
        return _env(**base)

    def run(self, **envkv) -> int:
        self.install()
        with self.env(**envkv):
            rc, self.log = _quiet(self.A.run, str(self.root), trigger="session-start", session="SID-1")
        return rc

    def precheck(self, *, head=None, source=None, announce=None, **envkv) -> dict:
        self.install()
        kw = {} if announce is None else {"announce": announce}
        with self.env(**envkv):
            return self.A.precheck(self.root, trigger="session-start", source=source, head=head, **kw)

    def status(self, **envkv) -> tuple:
        self.install()
        with self.env(**envkv):
            return tuple(self.A.status_line(self.root))

    # -- outputs
    def state(self) -> dict:
        return _read(self.root / ".deepmap" / "autosync.json") or {}

    def last(self) -> dict:
        lr = self.state().get("last_run")
        return lr if isinstance(lr, dict) else {}


def _orig_seams(A) -> dict:
    return {name: getattr(A, name) for name in SEAMS}


def _restore_seams(A, saved: dict) -> None:
    for name, fn in saved.items():
        setattr(A, name, fn)


# =====================================================================================================================
# A — import hygiene and the public surface
# =====================================================================================================================
def gate_import_and_surface(tree: Path, A) -> None:
    print("\n-- A. import hygiene, the DEEPMAP_* literals, constants and signatures --")
    src = (tree / "build" / "auto_sync.py").read_text(encoding="utf-8")
    mod = ast.parse(src)
    top = set()
    for node in mod.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top.add(node.module.split(".")[0])
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    check("auto_sync imports only the standard library at module level", bool(std) and top <= std,
          sorted(top - std))
    lits = {n.value for n in ast.walk(mod) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.fullmatch(r"DEEPMAP_[A-Z0-9_]*[A-Z0-9]", n.value)}
    check("auto_sync's only DEEPMAP_* literals are the six the spec allows (no graph-shaping knob read directly)",
          lits <= ALLOWED_LITERALS, sorted(lits - ALLOWED_LITERALS))
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import auto_sync; "
            "print(json.dumps(sorted(m for m in ('sync', 'neo4j', 'torch', 'graphify_extract', 'sync_status', "
            "'embeddings', 'sentence_transformers', 'deps_bootstrap') if m in sys.modules)))")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tree / "build")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, env=dict(os.environ))
    loaded = None
    try:
        loaded = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        pass
    check("importing auto_sync imports none of sync / neo4j / torch / graphify_extract / sync_status / embeddings / "
          "deps_bootstrap (a hook can import it cheaply)", p.returncode == 0 and loaded == [],
          (p.returncode, loaded, (p.stderr or "")[-400:]))
    want = {"TRIGGERS": ("session-start",), "ENV_AUTO_SYNC": "DEEPMAP_AUTO_SYNC", "ENV_MAX_S": "DEEPMAP_AUTO_SYNC_MAX_S",
            "ENV_PRUNE": "DEEPMAP_AUTO_SYNC_PRUNE", "MAX_S_DEFAULT": 1200, "COOLDOWN_S": 900, "HEAL_WAIT_S": 180,
            "SLOT_WAIT_S": 1800, "LEGACY_FILE_LIMIT": 1500, "BACKOFF_S": 21600, "PAUSE_AFTER_FAILURES": 3,
            "MEM_FLOOR_MIN": 3 * GIB, "MEM_FLOOR_FRACTION": 0.25, "STATE_FILE": "autosync.json",
            "LOCK_FILE": "autosync.lock", "SLOT_FILE": "autosync-slot.lock", "MACHINE_SWITCH": "auto-sync.json",
            "UNATTENDED_ENTRYPOINTS": ("sdk-cli", "sdk-ts", "sdk-py", "mcp", "claude-code-github-action",
                                       "remote_trigger", "remote_cowork_trigger")}
    bad = {k: getattr(A, k, "<missing>") for k, v in want.items() if getattr(A, k, "<missing>") != v}
    check("the spec's constants (§2.4) have the spec's values", not bad, bad)
    extra = {"MEM_FLOOR_MAX": 6 * GIB, "EMBED_HEADROOM": int(1.5 * GIB), "TAKEOVER_WAIT_S": 30.0}
    bad = {k: getattr(A, k, "<missing>") for k, v in extra.items() if getattr(A, k, "<missing>") != v}
    check("the review-round constants: the memory floor is capped at 6 GiB, an embeddings-only run needs 1.5 GiB more, "
          "a coalescing runner waits at most 30 s for a holder past its look", not bad, bad)
    sigs = {"attended": ["env"], "enabled": ["root", "trigger", "env"],
            "precheck": ["root", "trigger", "source", "head", "env", "now", "announce"],
            "freshness": ["root", "manifest"],
            "engine_budget": ["root", "env"], "run": ["target", "trigger", "session", "env"], "read_state": ["root"],
            "human_duration": ["seconds"], "status_line": ["root", "env"],
            "set_enabled": ["root", "enabled", "machine"], "main": ["argv"]}
    wrong = {}
    for fn, params in sigs.items():
        f = getattr(A, fn, None)
        try:
            got = list(inspect.signature(f).parameters) if f else None
        except (TypeError, ValueError):
            got = None
        if got != params:
            wrong[fn] = got
    check("the public functions have the spec's signatures (§2.4; main(argv) for the CLI to delegate to)",
          not wrong, wrong)
    kwonly = [n for n, p in inspect.signature(A.precheck).parameters.items() if p.kind is p.KEYWORD_ONLY]
    check("precheck / run take everything after the root keyword-only (precheck's `announce` too: the hooks' "
          "contract is precheck(root, head=<head or None>, announce=True), default False)",
          kwonly == ["trigger", "source", "head", "env", "now", "announce"]
          and getattr(inspect.signature(A.precheck).parameters.get("announce"), "default", None) is False
          and [n for n, p in inspect.signature(A.run).parameters.items() if p.kind is p.KEYWORD_ONLY]
          == ["trigger", "session", "env"], kwonly)
    missing = [s for s in SEAMS if not callable(getattr(A, s, None))]
    check("every seam the spec names is a module-level function", not missing, missing)
    table = {0: "0 s", 45: "45 s", 89: "89 s", 90: "2 min", 600: "10 min", 5399: "90 min", 5400: "1 h 30 min",
             7260: "2 h 1 min"}
    got = {k: A.human_duration(k) for k in table}
    check("human_duration: < 90 s -> 'N s', < 90 min -> 'N min', else 'H h M min'", got == table, got)


# =====================================================================================================================
# B — attended / enabled
# =====================================================================================================================
def gate_enabled_matrix(W: World) -> None:
    print("\n-- B. attended() / enabled(): env > instance > machine > unattended session > default --")
    A = W.A
    c = W.case()
    inst, mach = c.root / ".deepmap" / "instance.json", c.home / "auto-sync.json"
    unatt = set(A.UNATTENDED_ENTRYPOINTS)
    bad = []
    n = 0
    for env_v in ("1", "0", None):
        for inst_v in (False, True, None):
            for mach_v in (False, True, None):
                for att in ("1", "0", None):
                    for entry in ("sdk-cli", "cli", "claude-vscode", None):
                        n += 1
                        for path, v in ((inst, inst_v), (mach, mach_v)):
                            if v is None:
                                path.unlink(missing_ok=True)
                            elif path is inst:
                                _write(path, {"auto_sync": {"enabled": v}, "keep": 1})
                            else:
                                _write(path, {"enabled": v})
                        if env_v == "1":
                            want = (True, "env")
                        elif env_v == "0":
                            want = (False, "env")
                        elif inst_v is False:
                            want = (False, "instance")
                        elif mach_v is False:
                            want = (False, "machine")
                        else:
                            attended = (att == "1") if att is not None else (entry not in unatt)
                            want = (True, "default") if attended else (False, "unattended-session")
                        with _env(DEEPMAP_HOME=str(c.home), DEEPMAP_AUTO_SYNC=env_v,
                                  CLAUDE_CODE_SESSION_ATTENDED=att, CLAUDE_CODE_ENTRYPOINT=entry):
                            got = tuple(A.enabled(c.root, "session-start"))
                        if got != want:
                            bad.append(((env_v, inst_v, mach_v, att, entry), got, want))
    check(f"enabled(): all {n} combinations of env x instance x machine x ATTENDED x entrypoint follow the rule",
          not bad, bad[:5])
    true_bad = [b for b in bad if b[0][1] is True or b[0][2] is True]
    check("a `true` in instance.json or the machine switch never forces auto-sync on", not true_bad, true_bad[:3])
    with _env(DEEPMAP_HOME=str(c.home), DEEPMAP_AUTO_SYNC=None, CLAUDE_CODE_SESSION_ATTENDED="0",
              CLAUDE_CODE_ENTRYPOINT="sdk-cli"):
        inst.unlink(missing_ok=True)
        mach.unlink(missing_ok=True)
        got_status = A.enabled(c.root, "status")
        got_ss = A.enabled(c.root, "session-start")
    check("the `status` trigger ignores who watches (step 4 is session-start only)",
          tuple(got_status) == (True, "default") and tuple(got_ss) == (False, "unattended-session"),
          (got_status, got_ss))
    att = {}
    for v, entry in (("1", "sdk-cli"), ("0", "claude-vscode"), (None, "sdk-py"), (None, "mcp"), (None, "cli"),
                     (None, None), ("true", "sdk-cli"), ("garbage", "sdk-ts"), ("garbage", "claude-vscode")):
        with _env(CLAUDE_CODE_SESSION_ATTENDED=v, CLAUDE_CODE_ENTRYPOINT=entry):
            att[(v, entry)] = A.attended()
    want_att = {("1", "sdk-cli"): True, ("0", "claude-vscode"): False, (None, "sdk-py"): False, (None, "mcp"): False,
                (None, "cli"): True, (None, None): True, ("true", "sdk-cli"): True, ("garbage", "sdk-ts"): False,
                ("garbage", "claude-vscode"): True}
    check("attended(): CLAUDE_CODE_SESSION_ATTENDED decides; without it an UNATTENDED_ENTRYPOINTS entrypoint is "
          "unattended (Appendix B)", att == want_att, {k: v for k, v in att.items() if want_att[k] != v})

    # the off-switches fail CLOSED: a user who believes they switched the background writer off never keeps it
    inst_rows = (({"auto_sync": False}, (False, "instance")), ({"auto_sync": {"enabled": 0}}, (False, "instance")),
                 ({"auto_sync": {"enabled": "0"}}, (False, "instance")), ({"auto_sync": "off"}, (False, "instance")),
                 ({"auto_sync": 0}, (False, "instance")), ({"auto_sync": {"enabled": "maybe"}}, (False, "instance")),
                 ({"auto_sync": [False]}, (False, "instance")),
                 ({"auto_sync": True}, (True, "default")), ({"auto_sync": {"enabled": 1}}, (True, "default")),
                 ({"auto_sync": {"enabled": "on"}}, (True, "default")), ({"auto_sync": None}, (True, "default")),
                 ({"auto_sync": {}}, (True, "default")), ({"auto_sync": {"prune": False}}, (True, "default")))
    mach_rows = (('{"enabled": false}', (False, "machine")), ('{"enabled": 0}', (False, "machine")),
                 ('{"enabled": "off"}', (False, "machine")), ("false", (False, "machine")),
                 ('"off"', (False, "machine")), ("0", (False, "machine")), ("{not json", (False, "machine")),
                 ("", (False, "machine")), ("{}", (False, "machine")), ('{"enabled": null}', (False, "machine")),
                 ("[1]", (False, "machine")), ('﻿{"enabled": false}', (False, "machine")),
                 ('{"enabled": true}', (True, "default")), ("true", (True, "default")),
                 ('{"enabled": 1}', (True, "default")))
    got_bad = []
    with _env(DEEPMAP_HOME=str(c.home), DEEPMAP_AUTO_SYNC=None, CLAUDE_CODE_SESSION_ATTENDED="1",
              CLAUDE_CODE_ENTRYPOINT=None):
        mach.unlink(missing_ok=True)
        for shape, want in inst_rows:
            _write(inst, shape)
            got = tuple(A.enabled(c.root, "session-start"))
            if got != want:
                got_bad.append(("instance", shape, got, want))
        inst.unlink(missing_ok=True)
        for text, want in mach_rows:
            mach.write_text(text, encoding="utf-8")
            got = tuple(A.enabled(c.root, "session-start"))
            if got != want:
                got_bad.append(("machine", text, got, want))
        mach.write_text("{not json", encoding="utf-8")
        with _env(DEEPMAP_AUTO_SYNC="1"):
            env_wins = tuple(A.enabled(c.root, "session-start"))
        mach.unlink(missing_ok=True)
    check(f"the off-switches fail closed: all {len(inst_rows)} instance.json and {len(mach_rows)} machine-switch shapes "
          "(bare false, 0, '0', 'off', a value or file not understood -> off; true / 1 / absent -> on)",
          not got_bad, got_bad[:6])
    check("...an explicit DEEPMAP_AUTO_SYNC=1 still wins over a machine switch it does not understand",
          env_wins == (True, "env"), env_wins)


# =====================================================================================================================
# C — precheck
# =====================================================================================================================
def gate_precheck(W: World) -> None:
    print(f"\n-- C. precheck(): the code table, in order, file-only ({W.label}) --")
    A = W.A
    res = {}
    c = W.case()
    res["ok"] = c.precheck()
    c2 = W.case()
    res["hooks-off"] = c2.precheck(DEEPMAP_HOOKS="0")
    res["compact"] = c2.precheck(source="compact")
    res["startup"] = c2.precheck(source="startup")
    res["disabled"] = c2.precheck(DEEPMAP_AUTO_SYNC="0")
    res["unattended"] = c2.precheck(CLAUDE_CODE_SESSION_ATTENDED="0")
    c3 = W.case(manifest=False)
    res["not-set-up"] = c3.precheck()
    c4 = W.case()
    c4.write_manifest(verdict="FAILED")
    res["empty"] = c4.precheck()
    c5 = W.case()
    c5.write_state({"schema": 1, "paused": {"reason": "failures", "since_epoch": c5.clock.t - 100, "detail": "x"},
                    "consecutive_failures": 3})
    res["paused"] = c5.precheck()
    c5.write_last(outcome="synced", verdict="READY", finished_epoch=c5.clock.t - 10)
    res["paused-lifted"] = c5.precheck()
    c6 = W.case()
    c6.write_state({"schema": 1, "backoff_until_epoch": c6.clock.t + 3600, "consecutive_failures": 1,
                    "last_failure": {"outcome": "failed:refused", "reason": "r", "finished_epoch": c6.clock.t - 60}})
    res["backoff"] = c6.precheck()
    c6.write_last(outcome="synced", verdict="FAILED", finished_epoch=c6.clock.t - 1)
    res["backoff-not-lifted-by-FAILED"] = c6.precheck()
    c7 = W.case()
    c7.write_manifest(sync_wall_s=5000.0)
    res["too-slow"] = c7.precheck()
    res["too-slow-unlimited"] = c7.precheck(DEEPMAP_AUTO_SYNC_MAX_S="0")
    c8 = W.case()
    c8.write_manifest(recipe_env={"DEEPMAP_EXCLUDE_TESTS": "on"})
    res["recipe:settings"] = c8.precheck(DEEPMAP_EXCLUDE_TESTS="0")
    res["recipe-unset-is-no-conflict"] = c8.precheck()
    c9 = W.case()
    c9.write_manifest(recipe_deepmap_version="9.9.9")
    res["recipe:older-code"] = c9.precheck()
    c9.version = "10.0.0"
    res["newer-code-ok"] = c9.precheck()
    c10 = W.case()
    c10.write_state({"schema": 1, "last_session_start": {"started_epoch": c10.clock.t - 60, "head": H2}})
    res["cooldown"] = c10.precheck(head=H2)
    res["cooldown-no-head"] = c10.precheck()
    res["cooldown-head-moved"] = c10.precheck(head=H3)
    c11 = W.case()
    c11.write_state({"schema": 1, "paused": {"reason": "failures", "since_epoch": 1.0}})
    c11.status_fail = True
    res["error"] = c11.precheck()
    # an operator NEO4J_URI on another machine: a background refresh would push the code graph off this machine
    c12 = W.case()
    res["remote-database"] = c12.precheck(NEO4J_URI="neo4j+s://abc123.databases.neo4j.io")
    res["remote-database-userinfo"] = c12.precheck(NEO4J_URI="bolt://neo4j:s3cret@db.example:7687")
    res["remote-database-allowed-by-env"] = c12.precheck(NEO4J_URI="bolt://db.example:7687", DEEPMAP_AUTO_SYNC="1")
    res["local-env-uri"] = c12.precheck(NEO4J_URI="bolt://127.0.0.1:7690")
    res["local-env-uri-localhost"] = c12.precheck(NEO4J_URI="neo4j://localhost:7690")
    res["local-env-uri-ipv6"] = c12.precheck(NEO4J_URI="bolt://[::1]:7690")
    # a recorded skip the next run would repeat: with a head (the notify's question) precheck says so
    c13 = W.case()
    c13.write_manifest(sync_wall_s=None)
    c13.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "skipped:needs-timing", "rc": 4,
                                               "finished_epoch": c13.clock.t - 30, "why_code": "needs-timing"}})
    res["repeat:needs-timing"] = c13.precheck(head=H2)
    res["repeat:needs-timing-no-head"] = c13.precheck()
    res["repeat:needs-timing-unlimited"] = c13.precheck(head=H2, DEEPMAP_AUTO_SYNC_MAX_S="0")
    c13.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "skipped:needs-timing", "rc": 4,
                                               "finished_epoch": c13.clock.t - 30},
                     "last_session_start": {"started_epoch": c13.clock.t - 60, "head": H2}})
    res["repeat-before-cooldown"] = c13.precheck(head=H2)
    os.utime(c13.root / ".deepmap" / "graph_build.json", (c13.clock.t - 10, c13.clock.t - 10))
    res["repeat-lifted-by-manifest-rewrite"] = c13.precheck(head=H3)
    c14 = W.case()
    for code in ("no-own-instance", "recipe:python", "recipe:engine-version", "model-missing", "foreign-owner"):
        c14.write_state({"schema": 1, "last_run": {"id": "x", "outcome": f"skipped:{code}", "rc": 4,
                                                   "finished_epoch": c14.clock.t - 30, "why_code": code}})
        res[f"repeat:{code}"] = c14.precheck(head=H2)
    c14.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "skipped:needs-timing", "rc": 4,
                                               "finished_epoch": c14.clock.t - 30}})
    res["needs-timing-but-timed-now"] = c14.precheck(head=H2)     # c14's manifest records sync_wall_s
    for code in ("low-memory", "machine-busy", "git-busy", "unknown", "busy-enrich"):
        c14.write_state({"schema": 1, "last_run": {"id": "x", "outcome": f"skipped:{code}", "rc": 4,
                                                   "finished_epoch": c14.clock.t - 30, "why_code": code}})
        res[f"transient:{code}"] = c14.precheck(head=H2)
    c14.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "unreachable", "rc": 3,
                                               "finished_epoch": c14.clock.t - 30, "why_code": "unreachable:no-heal"}})
    res["heal-off"] = c14.precheck(head=H2, DEEPMAP_HEAL="0")
    res["heal-off-but-heal-on-now"] = c14.precheck(head=H2)
    c14.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "unreachable", "rc": 3,
                                               "finished_epoch": c14.clock.t - 30, "why_code": "unreachable"}})
    res["unreachable-heal-wait-is-transient"] = c14.precheck(head=H2, DEEPMAP_HEAL="0")
    codes = {k: v.get("code") for k, v in res.items()}
    want = {"ok": None, "hooks-off": "hooks-off", "compact": "compact", "startup": None, "disabled": "disabled",
            "unattended": "unattended-session", "not-set-up": "not-set-up", "empty": "empty", "paused": "paused",
            "paused-lifted": None, "backoff": "backoff", "backoff-not-lifted-by-FAILED": "backoff",
            "too-slow": "too-slow", "too-slow-unlimited": None, "recipe:settings": "recipe:settings",
            "recipe-unset-is-no-conflict": None, "recipe:older-code": "recipe:older-code", "newer-code-ok": None,
            "cooldown": "cooldown", "cooldown-no-head": None, "cooldown-head-moved": None, "error": "error",
            "remote-database": "remote-database", "remote-database-userinfo": "remote-database",
            "remote-database-allowed-by-env": None, "local-env-uri": None, "local-env-uri-localhost": None,
            "local-env-uri-ipv6": None,
            "repeat:needs-timing": "needs-timing", "repeat:needs-timing-no-head": None,
            "repeat:needs-timing-unlimited": None, "repeat-before-cooldown": "needs-timing",
            "repeat-lifted-by-manifest-rewrite": None, "repeat:no-own-instance": "no-own-instance",
            "repeat:recipe:python": "recipe:python", "repeat:recipe:engine-version": "recipe:engine-version",
            "repeat:model-missing": "model-missing", "repeat:foreign-owner": "foreign-owner",
            "needs-timing-but-timed-now": None, "transient:low-memory": None, "transient:machine-busy": None,
            "transient:git-busy": None, "transient:unknown": None, "transient:busy-enrich": None,
            "heal-off": "heal-off", "heal-off-but-heal-on-now": None, "unreachable-heal-wait-is-transient": None}
    for k, w in want.items():
        okv = res[k].get("ok") is (w is None)
        check(f"precheck [{k}] -> {w or 'ok'}", codes[k] == w and okv, res[k])
    check("precheck: took_s is the manifest's sync_wall_s; enabled_source names who decided",
          res["ok"].get("took_s") == 30.0 and res["ok"].get("enabled_source") == "default"
          and res["disabled"].get("enabled_source") == "env", (res["ok"], res["disabled"]))

    # file-only: no subprocess for any code (the REAL _running_version: a plugin.json read)
    calls = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def rec_run(*a, **k):
        calls.append(("run", a[:1]))
        return real_run(*a, **k)

    class RecPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            calls.append(("Popen", a[:1]))
            super().__init__(*a, **k)

    real_version = W.orig["_running_version"]
    try:
        subprocess.run, subprocess.Popen = rec_run, RecPopen
        for cc, kw in ((c, {}), (c2, {"source": "compact"}), (c5, {}), (c8, {"DEEPMAP_EXCLUDE_TESTS": "0"}),
                       (c9, {}), (c10, {"head": H2}), (c12, {"NEO4J_URI": "bolt://db.example:7687"}), (c14, {"head": H2})):
            cc.install()
            A._running_version = real_version
            with cc.env(**{k: v for k, v in kw.items() if k.startswith("DEEPMAP") or k == "NEO4J_URI"}):
                A.precheck(cc.root, source=kw.get("source"), head=kw.get("head"))
    finally:
        subprocess.run, subprocess.Popen = real_run, real_popen
    check("precheck starts no subprocess (no git, no python) on any path", not calls, calls[:3])


# =====================================================================================================================
# D — the run() decision table
# =====================================================================================================================
def _expect(c: Case, label: str, rc, outcome, *, synced: bool, reason_has=None, suggest="__any__",
            recorded=True) -> None:
    lr = c.last()
    problems = []
    if c.rc != rc:
        problems.append(f"rc={c.rc} want {rc}")
    if recorded:
        if lr.get("outcome") != outcome:
            problems.append(f"outcome={lr.get('outcome')!r} want {outcome!r}")
        if reason_has and reason_has not in str(lr.get("reason") or ""):
            problems.append(f"reason={lr.get('reason')!r} lacks {reason_has!r}")
        if suggest != "__any__" and lr.get("suggest") != suggest:
            problems.append(f"suggest={lr.get('suggest')!r} want {suggest!r}")
        if c.state().get("current") is not None:
            problems.append("current not cleared")
    else:
        if c.state().get("last_run") is not None:
            problems.append(f"a record was written: {c.state().get('last_run')}")
    if bool(c.sync.calls) != synced:
        problems.append(f"sync called {len(c.sync.calls)}x, want {'>=1' if synced else 0}")
    check(f"run [{label}] -> rc {rc}, {outcome or 'no record'}" + (", sync ran" if synced else ""), not problems,
          "; ".join(problems) + f" | log: {getattr(c, 'log', '')[-300:]}")


def _go(c: Case, **envkv) -> Case:
    c.rc = c.run(**envkv)
    return c


def gate_decisions(W: World) -> None:
    print(f"\n-- D. run(): every ending of R2-R17 and every §2.9.1 mapping ({W.label}) --")
    # silent ends (no record)
    _expect(_go(W.case(), DEEPMAP_AUTO_SYNC="0"), "disabled (env)", 4, None, synced=False, recorded=False)
    _expect(_go(W.case(), DEEPMAP_HOOKS="0"), "DEEPMAP_HOOKS=0", 4, None, synced=False, recorded=False)
    _expect(_go(W.case(), CLAUDE_CODE_SESSION_ATTENDED="0"), "unattended session (ATTENDED=0)", 4, None,
            synced=False, recorded=False)
    _expect(_go(W.case(), CLAUDE_CODE_SESSION_ATTENDED=None, CLAUDE_CODE_ENTRYPOINT="sdk-cli"),
            "unattended session (sdk-cli, no ATTENDED)", 4, None, synced=False, recorded=False)
    c = W.case()
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"enabled": False}})
    _expect(_go(c), "disabled (instance.json)", 4, None, synced=False, recorded=False)
    c = W.case()
    _write(c.home / "auto-sync.json", {"enabled": False})
    _expect(_go(c), "disabled (machine switch)", 4, None, synced=False, recorded=False)
    _expect(_go(W.case(manifest=False)), "no manifest", 1, None, synced=False, recorded=False)
    c = W.case()
    c.write_manifest(nodes=0)
    _expect(_go(c), "proven empty (nodes 0)", 4, None, synced=False, recorded=False)
    c = W.case()
    prev = {"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 120, "head": H2},
            "last_run": {"id": "old", "outcome": "fresh"}}
    c.write_state(prev)
    _go(c)
    check("run [cooldown, same HEAD within 15 min] -> rc 4, nothing recorded, no sync",
          c.rc == 4 and c.state() == prev and not c.sync.calls, (c.rc, c.state()))
    c = W.case()
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 120, "head": H1}})
    _expect(_go(c), "cooldown skipped: HEAD moved", 0, "synced", synced=True)
    c = W.case(git=False)
    c.heads = [None]
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 120, "head": None}})
    _go(c)
    check("run [cooldown, non-git: time alone] -> rc 4, no sync", c.rc == 4 and not c.sync.calls, c.rc)
    c = W.case()
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 901, "head": H2}})
    _expect(_go(c), "cooldown over after 900 s", 0, "synced", synced=True)

    # R8 eligibility
    c = W.case()
    c.route = SimpleNamespace(uri=None, source="none", problem="no route")
    _expect(_go(c), "route problem", 4, "skipped:no-own-instance", synced=False, reason_has="no DeepMap database",
            suggest="setup")
    c = W.case()
    c.route = SimpleNamespace(uri="bolt://127.0.0.1:7687", source="legacy-unverified", problem=None)
    _expect(_go(c), "legacy-unverified route", 4, "skipped:no-own-instance", synced=False)
    c = W.case()
    c.heal_prob = ("refused", "the store belongs to project X")
    _expect(_go(c), "heal refusal", 4, "skipped:setup-blocked", synced=False, suggest="setup")
    c = W.case()
    c.heal_prob = ("blocked", "port held")
    _expect(_go(c), "setup blocker", 4, "skipped:setup-blocked", synced=False)
    c = W.case()
    c.heal_prob = ("neo4j", "down")
    _expect(_go(c), "a heal outage is not a blocker", 0, "synced", synced=True)
    c = W.case()
    c.specs = {"neo4j": None}
    _expect(_go(c), "neo4j driver missing", 4, "skipped:packages-missing", synced=False, suggest="setup",
            reason_has="Python packages are missing")
    c = W.case()
    c.status_fail = True
    _go(c)
    check("run [sync_status unloadable] -> rc 4, outcome skipped:packages-missing (logged; no record possible)",
          c.rc == 4 and "skipped:packages-missing" in c.log and not c.sync.calls, (c.rc, c.log[-200:]))
    c = W.case()
    c.write_state({"schema": 1, "paused": {"reason": "failures", "since_epoch": c.clock.t - 50, "detail": "boom"},
                   "consecutive_failures": 3})
    _expect(_go(c), "paused (failures)", 4, "skipped:paused", synced=False, reason_has="the last 3 background")
    c = W.case()
    c.write_state({"schema": 1, "paused": {"reason": "engine-timeout", "since_epoch": c.clock.t - 50,
                                           "detail": "20 min"}})
    _expect(_go(c), "paused (engine-timeout)", 4, "skipped:paused", synced=False, reason_has="time budget (20 min)")
    c = W.case()
    c.write_state({"schema": 1, "backoff_until_epoch": c.clock.t + 3600, "consecutive_failures": 1,
                   "last_failure": {"outcome": "failed:index", "reason": "index boom",
                                    "finished_epoch": c.clock.t - 600}})
    _expect(_go(c), "backoff", 4, "skipped:backoff", synced=False, reason_has="retries it automatically after")
    c = W.case()
    c.write_manifest(recipe_env={"DEEPMAP_EXCLUDE_TESTS": "on"})
    _expect(_go(c, DEEPMAP_EXCLUDE_TESTS="0"), "recipe:settings", 4, "skipped:recipe:settings", synced=False,
            reason_has="DEEPMAP_EXCLUDE_TESTS=on at build time", suggest="refresh")
    c = W.case()
    c.write_manifest(recipe_deepmap_version="9.9.9")
    _expect(_go(c), "recipe:older-code", 4, "skipped:recipe:older-code", synced=False, reason_has="DeepMap 9.9.9")
    c = W.case()
    c.version = "0.30.0"
    _expect(_go(c), "newer running code (a plugin upgrade) runs", 0, "synced", synced=True)
    c = W.case()
    c.write_manifest(recipe_python="2.7")
    _expect(_go(c), "recipe:python", 4, "skipped:recipe:python", synced=False, reason_has="Python 2.7")
    c = W.case()
    c.write_manifest(recipe_deepmap_home=str(c.dir / "elsewhere"))
    _expect(_go(c), "recipe:home", 4, "skipped:recipe:home", synced=False, reason_has="elsewhere")
    c = W.case()
    c.write_manifest(recipe_route={"source": "registry", "uri": "bolt://127.0.0.1:17999"})
    _expect(_go(c), "recipe:route (another port)", 4, "skipped:recipe:route", synced=False, reason_has="17999")
    c = W.case()
    c.write_manifest(recipe_route={"source": "registry", "uri": "bolt://localhost:17689"})
    _expect(_go(c), "route: localhost vs 127.0.0.1 is one endpoint", 0, "synced", synced=True)
    c = W.case()
    c.write_manifest(recipe_route={"source": "instance-conf", "uri": "bolt://127.0.0.1:17689"})
    _expect(_go(c), "route: registry and instance-conf are both 'managed'", 0, "synced", synced=True)
    c = W.case()
    c.route = SimpleNamespace(uri="bolt://neo4j:s3cret@db.example:7687", source="env", problem=None)
    c.write_manifest(recipe_route={"source": "env", "uri": "bolt://db.example:7687"})
    _expect(_go(c, DEEPMAP_AUTO_SYNC="1"), "route: userinfo is never compared (remote, allowed by DEEPMAP_AUTO_SYNC=1)",
            0, "synced", synced=True)
    c = W.case()
    c.route = SimpleNamespace(uri="bolt://neo4j:s3cret@db.example:7687", source="env", problem=None)
    _go(c, DEEPMAP_AUTO_SYNC="1")
    lr = c.last()
    check("run [route: managed -> env] -> skipped:recipe:route, and the reason never carries the password",
          lr.get("outcome") == "skipped:recipe:route" and "s3cret" not in json.dumps(c.state()), lr)
    # the route is compared by ENDPOINT only: the same endpoint is the same database however it was named (sync records
    # an operator NEO4J_URI that names this project's own instance as its managed source, and an older build recorded
    # it as "env") — a category difference alone never skips
    for label, rec_route, cur in (
            ("recorded registry, NEO4J_URI = that endpoint (localhost spelling, with userinfo)",
             {"source": "registry", "uri": "bolt://127.0.0.1:17689"},
             SimpleNamespace(uri="bolt://neo4j:s3cret@localhost:17689", source="env", problem=None)),
            ("recorded env (an older build), the registry now names that endpoint",
             {"source": "env", "uri": "bolt://localhost:17689"},
             SimpleNamespace(uri="bolt://127.0.0.1:17689", source="registry", problem=None)),
            ("the first project on 7687, NEO4J_URI=bolt://localhost:7687 exported",
             {"source": "registry", "uri": "bolt://127.0.0.1:7687"},
             SimpleNamespace(uri="bolt://localhost:7687", source="env", problem=None))):
        c = W.case()
        c.route = cur
        c.write_manifest(recipe_route=rec_route)
        _expect(_go(c), f"route: {label} — not a recipe:route skip", 0, "synced", synced=True)
    c = W.case()
    c.route = SimpleNamespace(uri="bolt://localhost:17690", source="env", problem=None)
    _expect(_go(c), "route: recorded :17689, NEO4J_URI on this machine at :17690 -> recipe:route", 4,
            "skipped:recipe:route", synced=False, reason_has="bolt://localhost:17690", suggest="refresh")
    # no recorded route (a manifest from before build_recipe recorded one): never follow an operator NEO4J_URI to a
    # database the graph's build did not record; this project's own managed route still runs
    for label, over in (("build_recipe.route missing", {"recipe_route": None}),
                        ("a legacy manifest without build_recipe", {"build_recipe": None})):
        c = W.case()
        c.route = SimpleNamespace(uri="bolt://neo4j:s3cret@127.0.0.1:7690", source="env", problem=None)
        c.write_manifest(**over)
        _expect(_go(c), f"route unrecorded ({label}) + an operator NEO4J_URI -> recipe:route", 4,
                "skipped:recipe:route", synced=False, reason_has="bolt://127.0.0.1:7690", suggest="refresh")
        check(f"...why_code recipe:route-unrecorded, the reason says the build did not record its database, never the "
              f"password; precheck with a HEAD repeats it until a sync rewrites the manifest ({label})",
              c.last().get("why_code") == "recipe:route-unrecorded"
              and "did not record which database" in str(c.last().get("reason"))
              and "s3cret" not in json.dumps(c.state()) and c.precheck(head=H3).get("code") == "recipe:route",
              (c.last(), c.precheck(head=H3)))
        c = W.case()
        c.write_manifest(**over)
        _expect(_go(c), f"route unrecorded ({label}) + this project's managed route runs", 0, "synced", synced=True)
    # no egress on its own: an operator NEO4J_URI on another machine is skipped unless DEEPMAP_AUTO_SYNC=1
    for uri in ("bolt://neo4j:s3cret@db.example:7687", "neo4j+s://abc123.databases.neo4j.io",
                "bolt://192.168.1.20:7687"):
        c = W.case()
        c.route = SimpleNamespace(uri=uri, source="env", problem=None)
        c.write_manifest(recipe_route={"source": "env", "uri": uri.replace("neo4j:s3cret@", "")})
        _expect(_go(c), f"remote database {uri.split('@')[-1]} (by default)", 4, "skipped:remote-database",
                synced=False, reason_has="never sends code off this machine", suggest="refresh")
        check(f"...the remote-database reason names the host, never the password ({uri.split('@')[-1]})",
              uri.split("@")[-1].split("//")[-1] in str(c.last().get("reason"))
              and "s3cret" not in json.dumps(c.state()), c.last().get("reason"))
    for uri, label in (("bolt://127.0.0.1:7690", "127.0.0.1"), ("bolt://localhost:7690", "localhost"),
                       ("bolt://[::1]:7690", "::1"), ("bolt://127.0.0.2:7690", "127.0.0.2")):
        c = W.case()
        c.route = SimpleNamespace(uri=uri, source="env", problem=None)
        c.write_manifest(recipe_route={"source": "env", "uri": uri})
        _expect(_go(c), f"an operator NEO4J_URI on this machine ({label}) runs", 0, "synced", synced=True)
    c = W.case()
    c.route = SimpleNamespace(uri="bolt://db.example:7687", source="env", problem=None)
    c.write_manifest(recipe_route={"source": "env", "uri": "bolt://db.example:7687"})
    _expect(_go(c, DEEPMAP_AUTO_SYNC="0"), "remote database + DEEPMAP_AUTO_SYNC=0 -> disabled", 4, None,
            synced=False, recorded=False)
    off_machine = getattr(W.A, "_off_machine", lambda u: None)
    rows = [(u, off_machine(u)) for u in ("bolt://127.0.0.1", "neo4j://localhost:7687", "bolt://LOCALHOST.:1",
                                               "bolt://[::1]:7687", "bolt://[0:0:0:0:0:0:0:1]:7687",
                                               "bolt://127.9.9.9", "bolt://db.example", "bolt://10.0.0.1",
                                               "neo4j+s://x.databases.neo4j.io", "bolt://:7687", "not a uri", "")]
    want_rows = [False, False, False, False, False, False, True, True, True, True, True, True]
    check("_off_machine: localhost and loopback addresses are this machine; any other host, or none, is not",
          [r[1] for r in rows] == want_rows, [r for r, w in zip(rows, want_rows) if r[1] != w])
    c = W.case()
    c.write_manifest(recipe_embed_model_revision="rev0")
    _expect(_go(c), "recipe:model (revision)", 4, "skipped:recipe:model", synced=False, reason_has="rev0")
    c = W.case()
    c.write_manifest(recipe_embed_model="some/other-model")
    _expect(_go(c), "recipe:model (another model)", 4, "skipped:recipe:model", synced=False,
            reason_has="some/other-model")
    c = W.case()
    c.write_manifest(recipe_embed_model_revision=None)
    _expect(_go(c), "model revision unknown never blocks", 0, "synced", synced=True)
    c = W.case()
    c.write_manifest(graph_engines=["ast"], extractor="ast")
    _expect(_go(c), "recipe:engine ast -> graphify", 4, "skipped:recipe:engine", synced=False,
            reason_has="ast \u2192 graphify")
    c = W.case()
    c.write_manifest(graph_engines=["ast"], extractor="ast", recipe_env={"DEEPMAP_EXTRACTOR": "ast"})
    _expect(_go(c), "an ast graph built with DEEPMAP_EXTRACTOR=ast: the replayed recipe keeps it ast, it runs", 0,
            "synced", synced=True)
    check("...the replayed DEEPMAP_EXTRACTOR=ast is in the environment sync loads under",
          (c.loaded_env or {}).get("DEEPMAP_EXTRACTOR") == "ast", c.loaded_env)
    c = W.case()
    c.gversion = None
    _expect(_go(c), "recipe:engine graphify -> ast (graphify gone)", 4, "skipped:recipe:engine", synced=False,
            reason_has="graphify \u2192 ast")
    c = W.case()
    c.gversion = "0.8.38"
    _expect(_go(c), "recipe:engine-version (outside the supported range)", 4, "skipped:recipe:engine-version",
            synced=False, suggest="setup")
    c = W.case()
    c.gversion = "0.8.40"
    c.write_manifest(recipe_graphify_version="0.8.45")
    _expect(_go(c), "recipe:engine-version (older than the build's)", 4, "skipped:recipe:engine-version",
            synced=False, reason_has="0.8.40")

    # R9 freshness
    c = W.case()
    c.sync.fp = {"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    _expect(_go(c), "fresh", 0, "fresh", synced=False)
    lr = c.last()
    check("run [fresh] records fresh_at_head = HEAD and the fingerprint",
          lr.get("fresh_at_head") == H2 and lr.get("fingerprint") == FP1 and lr.get("rc") == 0, lr)
    c = W.case()
    c.sync.fp = {"value": None, "kind": "git", "head": H2, "git_busy": "rebase", "truncated": False}
    _expect(_go(c), "git-busy (rebase)", 4, "skipped:git-busy", synced=False, reason_has="git rebase", suggest=None)
    c = W.case()
    c.sync.fp = {"value": None, "kind": "git", "head": H2, "git_busy": "unmerged", "truncated": False}
    _expect(_go(c), "git-busy (unmerged entries)", 4, "skipped:git-busy", synced=False, reason_has="unmerged")
    c = W.case()
    c.sync.fp = {"value": None, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    _expect(_go(c), "unknown (git failed)", 4, "skipped:unknown", synced=False, reason_has="git did not answer")
    c = W.case()
    c.sync.fp = {"value": None, "kind": "git", "head": H2, "git_busy": None, "truncated": True}
    _expect(_go(c), "unknown (truncated)", 4, "skipped:unknown", synced=False, reason_has="20,000")

    # R10 budget
    c = W.case()
    c.write_manifest(sync_wall_s=1500.0)
    _expect(_go(c), "too-slow", 4, "skipped:too-slow", synced=False, reason_has="25 min")
    c = W.case()
    c.write_manifest(sync_wall_s=1500.0)
    _expect(_go(c, DEEPMAP_AUTO_SYNC_MAX_S="0"), "MAX_S=0 removes the limit", 0, "synced", synced=True)
    c = W.case()
    c.write_manifest(sync_wall_s=1500.0)
    _expect(_go(c, DEEPMAP_AUTO_SYNC_MAX_S="garbage"), "a garbage MAX_S is the default", 4, "skipped:too-slow",
            synced=False)
    c = W.case()
    c.write_manifest(sync_wall_s=None)
    c.files = 1501
    _expect(_go(c), "needs-timing (legacy manifest, > 1,500 files)", 4, "skipped:needs-timing", synced=False,
            reason_has="1,500 source files")
    check("needs-timing counts with limit LEGACY_FILE_LIMIT + 1 (a bounded walk)", c.file_limit == 1501,
          c.file_limit)
    check("last_run.why_code names the reason's own code (needs-timing)", c.last().get("why_code") == "needs-timing",
          c.last())
    pc_head, pc_spawn = c.precheck(head=H3), c.precheck()
    check("after that skip, precheck with a HEAD (the notify) says needs-timing — it never announces 'refreshing "
          "now' for a run that will skip again; without a HEAD (the heal hook's spawn) it lets the runner re-check",
          pc_head.get("code") == "needs-timing" and pc_head.get("ok") is False and pc_spawn.get("ok") is True,
          (pc_head, pc_spawn))
    later = c.clock.t + 60
    os.utime(c.root / ".deepmap" / "graph_build.json", (later, later))
    check("...a sync that rewrites the manifest afterwards lifts it (precheck with a HEAD is ok again)",
          c.precheck(head=H3).get("ok") is True, c.precheck(head=H3))
    c = W.case()
    c.write_manifest(sync_wall_s=None)
    c.files = 1500
    _expect(_go(c), "legacy manifest with <= 1,500 files runs", 0, "synced", synced=True)

    # R11 database
    c = W.case()
    c.heal_seq = [{"ts_epoch": c.clock.t, "neo4j_ok": False, "reason": "neo4j did not start"}]
    _expect(_go(c), "heal reports neo4j down", 3, "unreachable", synced=False, reason_has="neo4j did not start",
            suggest="doctor")
    c = W.case()
    c.heal = None
    c.bolt = [False]
    _expect(_go(c), "no heal verdict, bolt down for 3 min", 3, "unreachable", synced=False,
            reason_has="within 3 minutes")
    check("...the wait lasted the heal budget (fake clock >= 180 s)", c.clock.slept >= 180, c.clock.slept)
    c = W.case()
    c.bolt = [False]
    _expect(_go(c, DEEPMAP_HEAL="0"), "heal off + bolt down", 3, "unreachable", synced=False,
            reason_has="DEEPMAP_HEAL")
    check("...recorded with why_code unreachable:no-heal; precheck with a HEAD then says heal-off while DEEPMAP_HEAL "
          "stays off", c.last().get("why_code") == "unreachable:no-heal"
          and c.precheck(head=H3, DEEPMAP_HEAL="0").get("code") == "heal-off"
          and c.precheck(head=H3).get("ok") is True, (c.last().get("why_code"), c.precheck(head=H3, DEEPMAP_HEAL="0")))
    c = W.case()
    c.bolt = [True]
    _expect(_go(c, DEEPMAP_HEAL="0"), "heal off + bolt up", 0, "synced", synced=True)
    c = W.case()
    c.heal_seq = [{"ts_epoch": c.clock.t - 100, "neo4j_ok": True},
                  {"ts_epoch": c.clock.t - 100, "neo4j_ok": True},
                  {"ts_epoch": c.clock.t + 10, "neo4j_ok": True, "refused": True, "refusal_kind": "legacy-owner",
                   "reason": "the store belongs to project Y"}]
    _expect(_go(c), "heal status rewritten mid-wait with a refusal", 4, "skipped:setup-blocked", synced=False,
            reason_has="project Y")
    c = W.case()
    c.heal = None
    c.bolt = [True]
    _go(c)
    check("run [no heal verdict, bolt listening on two probes >= 5 s apart] -> synced",
          c.rc == 0 and c.last().get("outcome") == "synced" and c.bolt_calls >= 2 and 5 <= c.clock.slept < 30,
          (c.rc, c.last().get("outcome"), c.bolt_calls, c.clock.slept))
    c = W.case()
    c.heal = None
    c.bolt = [True, False, True, True, True, True]
    _go(c)
    check("...a probe that fails in between restarts the 5 s confirmation", c.rc == 0 and c.bolt_calls >= 5,
          (c.rc, c.bolt_calls))

    # R12 machine slot
    c = W.case()
    slot = W.ST.try_lock(c.home / "run" / "autosync-slot.lock")
    try:
        _expect(_go(c), "machine slot held for 30 min", 4, "skipped:machine-busy", synced=False,
                reason_has="30 minutes")
    finally:
        W.ST.release_lock(slot)
    check("...it waited SLOT_WAIT_S on the fake clock", c.clock.slept >= 1800, c.clock.slept)

    # R13 memory and model: floor = 25 % of RAM, at least 3 GiB, at most 6 GiB
    for avail, total, label, want in ((2, 16, "2 of 16 GiB (floor 4)", "skipped:low-memory"),
                                      (5, 16, "5 of 16 GiB (floor 4)", "synced"),
                                      (5, 32, "5 of 32 GiB (floor 6: 25 % capped)", "skipped:low-memory"),
                                      (7, 32, "7 of 32 GiB (floor 6, not 8)", "synced"),
                                      (7, 64, "7 of 64 GiB (floor 6, not 16)", "synced"),
                                      (5.5, 64, "5.5 of 64 GiB (floor 6)", "skipped:low-memory"),
                                      (6.5, 128, "6.5 of 128 GiB (floor 6, not 32)", "synced"),
                                      (3.5, 8, "3.5 of 8 GiB (floor 3 GiB)", "synced"),
                                      (2.5, 8, "2.5 of 8 GiB (floor 3 GiB)", "skipped:low-memory")):
        c = W.case()
        c.memory = [(int(avail * GIB), int(total * GIB))]
        _expect(_go(c), f"memory {label}", 0 if want == "synced" else 4, want, synced=want == "synced")
    floors = {t: W.A._mem_floor(t * GIB) / GIB for t in (4, 8, 12, 16, 24, 32, 64, 128)}
    check("_mem_floor: 25 % of RAM, at least 3 GiB, at most 6 GiB",
          floors == {4: 3.0, 8: 3.0, 12: 3.0, 16: 4.0, 24: 6.0, 32: 6.0, 64: 6.0, 128: 6.0}, floors)
    c = W.case()
    c.memory = [(None, None)]
    _expect(_go(c), "memory unknown -> proceed", 0, "synced", synced=True)
    c = W.case()
    c.memory = [(2 * GIB, 16 * GIB)]
    _go(c)
    check("run [low-memory] reason: what was available and the floor it leaves free, without contradicting itself",
          c.last().get("reason") == "only 2.0 GB of memory was available, and a background refresh starts only when "
                                    "at least 4.0 GB is free (it needs about 1.5 GB itself and leaves the rest for "
                                    "your other work)", c.last().get("reason"))
    # only postponed embeddings due (sources unchanged): floor + 1.5 GiB, so it never re-syncs only to postpone again
    for avail, fp, label, want in ((5, FP1, "5 GiB, floor 4 + 1.5", "skipped:low-memory"),
                                   (5.6, FP1, "5.6 GiB, floor 4 + 1.5", "synced"),
                                   (4.5, FP2, "4.5 GiB, sources changed too: the plain floor 4", "synced")):
        c = W.case()
        c.write_manifest(embeddings_pending=True)
        c.sync.fp = {"value": fp, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
        c.memory = [(int(avail * GIB), 16 * GIB)]
        _expect(_go(c), f"embeddings pending, {label}", 0 if want == "synced" else 4, want, synced=want == "synced")
        if want != "synced":
            check("...its reason says only the postponed embeddings are due and the memory it waits for",
                  "only lacks the embeddings" in str(c.last().get("reason")) and "5.5 GB" in str(c.last().get("reason"))
                  and c.last().get("why_code") == "low-memory:embed", c.last())
    c = W.case()
    c.model = {"complete": False, "revision": "rev1", "path": None, "missing": ["model.safetensors"]}
    _expect(_go(c), "model files incomplete", 4, "skipped:model-missing", synced=False, suggest="setup")

    # R14 covered / busy-enrich
    c = W.case()
    c.write_running(origin="manual", phase="extract", fp=FP2)
    _expect(_go(c), "covered by a live manual sync of the same sources", 0, "covered", synced=False)
    c = W.case()
    c.write_running(origin="unattended", phase="start", fp=None)
    _expect(_go(c), "covered: a live sync at phase start without a fingerprint", 0, "covered", synced=False)
    c = W.case()
    c.write_running(origin="manual", phase="extract", fp=FP1)
    _expect(_go(c), "a live sync of OTHER sources does not cover", 0, "synced", synced=True)
    c = W.case()
    c.write_running(origin="manual", phase="extract", fp=FP2, live=False)
    _expect(_go(c), "a dead holder never covers", 0, "synced", synced=True)
    c = W.case()
    c.write_running(origin="enrich", phase="enrich-extract", fp=None)
    _expect(_go(c), "a live enrich holds the lock", 4, "skipped:busy-enrich", synced=False, reason_has="enrich")

    # R15 -> §2.9.1
    c = W.case()
    _go(c)
    lr = c.last()
    check("run [synced READY] -> rc 0; last_run carries sync_id, verdict, built_at_sha, the fingerprint, took_s",
          c.rc == 0 and lr.get("outcome") == "synced" and lr.get("verdict") == "READY" and lr.get("sync_id")
          and lr.get("built_at_sha") == H2 and lr.get("fingerprint") == FP2 and lr.get("took_s") == 42.0
          and lr.get("head") == H2, lr)
    table = (
        ("synced DEGRADED", [_ok_synced("DEGRADED", reasons=["LOW COVERAGE for Go", "b", "c", "d"])], 0, "synced",
         None),
        ("synced verdict FAILED", [_ok_synced("FAILED")], 1, "failed:verdict", "+1"),
        ("outcome failed (zero nodes)", [_lo("failed", ok=False, reason="0 nodes")], 1, "failed:verdict", "+1"),
        ("queued", [_lo("queued")], 0, "queued", "reset"),
        ("preempted", [_lo("preempted")], 0, "preempted", "reset"),
        ("not-pushed engine-downgrade, timed out",
         [_lo("not-pushed", not_pushed_kind="engine-downgrade", engine_timed_out=True, reason="graphify timed out")],
         4, "not-pushed:engine-timeout", "pause"),
        ("not-pushed engine-downgrade", [_lo("not-pushed", not_pushed_kind="engine-downgrade", reason="fell back")],
         4, "not-pushed:engine-downgrade", "+1"),
        ("not-pushed busy-instance", [_lo("not-pushed", not_pushed_kind="busy-instance", reason="busy")], 4,
         "not-pushed:busy-instance", None),
        ("busy-unqueued", [_lo("busy-unqueued", reason="could not queue")], 4, "not-pushed:busy-lock", None),
        ("busy-enrich (sync's lock-miss)", [_lo("busy-enrich", reason="enrich")], 4, "skipped:busy-enrich", None),
        ("refused foreign-owner", [_lo("refused", ok=False, refusal_kind="foreign-owner", reason="owned by X")], 4,
         "skipped:foreign-owner", None),
        ("refused legacy-owner", [_lo("refused", ok=False, refusal_kind="legacy-owner", reason="legacy")], 4,
         "skipped:foreign-owner", None),
        ("refused base-claim", [_lo("refused", ok=False, refusal_kind="base-claim", reason="claim")], 4,
         "skipped:foreign-owner", None),
        ("refused unowned (a graph with no recorded owner)",
         [_lo("refused", ok=False, refusal_kind="unowned", reason="REFUSING: no recorded owner")], 4,
         "skipped:unowned", None),
        ("user-request-pending (a user's queued re-run waits)",
         [_lo("user-request-pending", rc=4, reason="a re-run that a sync the user started queued is still waiting")],
         4, "skipped:user-request", None),
        ("refused (other)", [_lo("refused", ok=False, reason="no")], 1, "failed:refused", "+1"),
        ("route-problem", [_lo("route-problem", ok=False, reason="no route")], 1, "failed:refused", "+1"),
        ("failed index-failure", [_lo("failed", ok=False, refusal_kind="index-failure", reason="idx")], 1,
         "failed:index", "+1"),
        ("error rc 3 (recorded by sync)", [_lo("error", ok=False, rc=3, reason="ServiceUnavailable")], 3,
         "unreachable", None),
        ("error rc 1", [_lo("error", ok=False, rc=1, reason="boom")], 1, "failed:error", "+1"),
    )
    for label, plan, rc, outcome, effect in table:
        c = W.case()
        c.sync.plan = list(plan)
        _go(c)
        st = c.state()
        eff_ok = True
        if effect == "+1":
            eff_ok = st.get("consecutive_failures") == 1 and (st.get("backoff_until_epoch") or 0) > c.clock.t
        elif effect == "pause":
            eff_ok = (st.get("paused") or {}).get("reason") == "engine-timeout"
        elif effect == "reset":
            eff_ok = st.get("consecutive_failures") == 0 and st.get("paused") is None
        elif effect is None:
            eff_ok = not st.get("consecutive_failures") and not st.get("paused") and not st.get("backoff_until_epoch")
        check(f"run [{label}] -> rc {rc}, {outcome}, backoff effect {effect or 'none'}",
              c.rc == rc and c.last().get("outcome") == outcome and eff_ok,
              (c.rc, c.last().get("outcome"), {k: st.get(k) for k in ("consecutive_failures", "paused",
                                                                        "backoff_until_epoch")}))
    c = W.case()
    c.sync.plan = [_ok_synced("DEGRADED", reasons=["a", "b", "c", "d"])]
    _go(c)
    check("last_run.reasons keeps the first three", c.last().get("reasons") == ["a", "b", "c"], c.last())
    c = W.case()
    c.sync.plan = [_lo("not-pushed", not_pushed_kind="engine-downgrade", engine_timed_out=True, reason="t/o")]
    _go(c)
    lr = c.last()
    check("not-pushed:engine-timeout advice: not limited by the background budget; DEEPMAP_GRAPHIFY_TIMEOUT=<2x>",
          "not limited by the background time budget" in str(lr.get("advice"))
          and "DEEPMAP_GRAPHIFY_TIMEOUT=2400" in str(lr.get("advice")) and lr.get("reason") == "t/o", lr)
    c = W.case()
    c.sync.plan = [_lo("not-pushed", not_pushed_kind="busy-instance", reason="busy")]
    _go(c)
    check("not-pushed:busy-instance advice: once the other project's sync finishes",
          c.last().get("advice") == " once the other project's sync finishes", c.last())
    c = W.case()
    c.sync.plan = [_lo("refused", ok=False, refusal_kind="foreign-owner", reason="owned by project X")]
    _go(c)
    check("skipped:foreign-owner keeps sync's refusal line and suggests /deep-map:setup (no backoff)",
          c.last().get("reason") == "owned by project X" and c.last().get("suggest") == "setup", c.last())
    c = W.case()
    c.sync.plan = [_lo("refused", ok=False, refusal_kind="unowned", reason="REFUSING: no recorded owner")]
    _go(c)
    lr = c.last()
    check("skipped:unowned (never failed:refused: a deliberate refusal, not a failure) says the background refresh "
          "never adopts a graph it cannot prove is this project's, suggests /deep-map:refresh, why_code unowned; "
          "precheck with a HEAD repeats it until a sync rewrites the manifest",
          "never adopts a database it cannot prove" in str(lr.get("reason")) and lr.get("suggest") == "refresh"
          and lr.get("why_code") == "unowned" and lr.get("rc") == 4 and c.precheck(head=H3).get("code") == "unowned",
          (lr, c.precheck(head=H3)))
    c = W.case()
    c.write_state({"schema": 1, "consecutive_failures": 2, "backoff_until_epoch": None})
    c.sync.plan = [_lo("refused", ok=False, refusal_kind="unowned", reason="REFUSING")]
    _go(c)
    check("...an unowned refusal never counts toward the backoff / pause (2 earlier failures stay 2, no backoff)",
          c.state().get("consecutive_failures") == 2 and not c.state().get("backoff_until_epoch")
          and not c.state().get("paused"), c.state())
    c = W.case()

    def user_request_left(fs, root, ctl):
        # sync found a user's request queued (it arrived after the runner's own look) and left it exactly as it was
        _write(root / ".deepmap" / "sync.rerun", {"origin": "manual", "cleanup_stale": True, "prune_policy": "full"})
        return _lo("user-request-pending", rc=4, reason="sync's own words")(fs, root, ctl)

    c.sync.plan = [user_request_left]
    _go(c)
    lr = c.last()
    pc_head, pc_spawn = c.precheck(head=H3), c.precheck()
    check("skipped:user-request says a sync the user started queued a re-run that only such a sync runs, suggests "
          "/deep-map:refresh, why_code user-request, no backoff; it REPEATS: precheck with a HEAD says user-request "
          "while that request stays queued (never 'refreshing now' for a run that would skip again), without a HEAD "
          "the heal hook's spawn still lets the runner re-check",
          "a background refresh never runs one" in str(lr.get("reason")) and lr.get("suggest") == "refresh"
          and lr.get("why_code") == "user-request" and not c.state().get("consecutive_failures")
          and pc_head.get("code") == "user-request" and pc_head.get("ok") is False and pc_spawn.get("ok") is True,
          (lr, pc_head, pc_spawn))
    (c.root / ".deepmap" / "sync.rerun").unlink()
    check("...a sync the user started claimed the request (sync.rerun gone) -> precheck with a HEAD is ok again",
          c.precheck(head=H3).get("ok") is True, c.precheck(head=H3))
    c = W.case()
    c.sync.plan = [_ok_synced(engine_timed_out=True)]
    _go(c)
    check("run [synced, but the engine timed out] -> synced AND paused (engine-timeout)",
          c.last().get("outcome") == "synced" and (c.state().get("paused") or {}).get("reason") == "engine-timeout",
          c.state())

    def raises(exc):
        def step(fs, root, ctl):
            raise exc
        return step

    c = W.case()
    c.sync.plan = [raises(OSError("connection refused"))]
    _go(c)
    check("run [sync raises a database-class error] -> rc 3 unreachable (no backoff), says so",
          c.rc == 3 and c.last().get("outcome") == "unreachable" and "failed while the background refresh ran"
          in str(c.last().get("reason")) and not c.state().get("consecutive_failures"), c.last())
    c = W.case()
    c.sync.plan = [raises(ValueError("bad"))]
    _go(c)
    check("run [sync raises anything else] -> rc 1 failed:error, traceback in the log, +1",
          c.rc == 1 and c.last().get("outcome") == "failed:error" and "ValueError" in c.log
          and c.state().get("consecutive_failures") == 1, (c.rc, c.last()))

    # every recorded skip carries a reason; the suggest values of the §2.10.4 table
    c = W.case()
    c.write_manifest(sync_wall_s=1500.0)
    _go(c)
    check("too-slow reason: took (a background refresh's own wall time is not counted) / the limit / how to raise it",
          c.last().get("reason") == "its last sync took 25 min (background refreshes not counted; the background "
                                    "limit is 20 min; DEEPMAP_AUTO_SYNC_MAX_S raises it, 0 removes it)",
          c.last().get("reason"))


# =====================================================================================================================
# E — the sync call, its environment, engine_budget, pre_write, before_embed
# =====================================================================================================================
def gate_sync_call(W: World) -> None:
    print(f"\n-- E. the sync call: kwargs, environment, engine budget, pre_write, before_embed ({W.label}) --")
    A = W.A
    c = W.case()
    c.write_manifest(recipe_env={"DEEPMAP_EXCLUDE_TESTS": "on"})
    _go(c, DEEPMAP_EMBED_THREADS="3", HF_HUB_OFFLINE="0", GIT_OPTIONAL_LOCKS=None)
    root, kw, env_at = c.sync.calls[0] if c.sync.calls else (None, {}, {})
    check("sync(root, cleanup_stale=False, prune_policy='evidence', unattended=<control>) — and nothing else",
          set(kw) == {"cleanup_stale", "prune_policy", "unattended"} and kw.get("cleanup_stale") is False
          and kw.get("prune_policy") == "evidence", kw)
    check("...the control is a sync_status.UnattendedControl with the trigger, session, policy and all three "
          "callbacks", isinstance(kw.get("unattended"), W.ST.UnattendedControl)
          and kw["unattended"].trigger == "session-start" and kw["unattended"].session == "SID-1"
          and kw["unattended"].prune_policy == "evidence" and all(callable(getattr(kw["unattended"], n, None))
                                                                   for n in ("pre_write", "before_embed",
                                                                             "on_hard_exit")), vars(kw.get("unattended", SimpleNamespace())))
    check("...the runner ends the control after sync returns (ctl.end())",
          getattr(kw.get("unattended"), "ended", 1) >= 1 or W.label != "fake", getattr(kw.get("unattended"), "ended", None))
    check("...it ran in the project root", root == c.root.resolve(), root)
    check("sync is first loaded under HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, GIT_OPTIONAL_LOCKS=0, PYTHONUTF8=1 "
          "(an operator's HF_HUB_OFFLINE=0 is overridden)",
          (c.loaded_env or {}).get("HF_HUB_OFFLINE") == "1" and c.loaded_env.get("TRANSFORMERS_OFFLINE") == "1"
          and c.loaded_env.get("GIT_OPTIONAL_LOCKS") == "0" and c.loaded_env.get("PYTHONUTF8") == "1",
          c.loaded_env)
    check("...with the recorded recipe replayed (DEEPMAP_EXCLUDE_TESTS=on) before sync is imported",
          (c.loaded_env or {}).get("DEEPMAP_EXCLUDE_TESTS") == "on", c.loaded_env)
    check("...and DEEPMAP_EMBED_THREADS untouched (embedding determinism)",
          (c.loaded_env or {}).get("DEEPMAP_EMBED_THREADS") == "3" and env_at.get("DEEPMAP_EMBED_THREADS") == "3",
          (c.loaded_env, env_at))
    check("the sync runs with the engine budget in DEEPMAP_GRAPHIFY_TIMEOUT (2 x 600 capped at 1200)",
          env_at.get("DEEPMAP_GRAPHIFY_TIMEOUT") == "1200", env_at)
    c = W.case()
    _go(c)
    check("DEEPMAP_EMBED_THREADS stays unset when the user never set it",
          c.sync.calls and c.sync.calls[0][2].get("DEEPMAP_EMBED_THREADS") is None, c.sync.calls[:1])
    c = W.case()
    _go(c, DEEPMAP_AUTO_SYNC_PRUNE="0")
    check("DEEPMAP_AUTO_SYNC_PRUNE=0 -> prune_policy 'none' (sync and control)",
          c.sync.calls and c.sync.calls[0][1].get("prune_policy") == "none"
          and c.sync.calls[0][1]["unattended"].prune_policy == "none", c.sync.calls[:1])
    c = W.case()
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"prune": False}})
    _go(c)
    check("instance.json auto_sync.prune=false -> prune_policy 'none'",
          c.sync.calls and c.sync.calls[0][1].get("prune_policy") == "none", c.sync.calls[:1])
    c = W.case()
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"prune": True, "enabled": True}})
    _go(c)
    check("instance.json auto_sync.prune=true never turns a 'full' prune on (still 'evidence')",
          c.sync.calls and c.sync.calls[0][1].get("prune_policy") == "evidence", c.sync.calls[:1])

    # engine_budget table
    rows = []
    for budget, envkv, want in (({"timeout_s": 600, "source": "size-aware"}, {}, 1200),
                                ({"timeout_s": 900, "source": "size-aware"}, {}, 1200),
                                ({"timeout_s": 900, "source": "size-aware"}, {"DEEPMAP_AUTO_SYNC_MAX_S": "0"}, 1800),
                                ({"timeout_s": 5000, "source": "env"}, {}, 1200),
                                ({"timeout_s": 100, "source": "env"}, {}, 100),
                                ({"timeout_s": 5000, "source": "env"}, {"DEEPMAP_AUTO_SYNC_MAX_S": "0"}, 5000),
                                ({"timeout_s": 20, "source": "size-aware"}, {}, 60),
                                ({"timeout_s": 600, "source": "size-aware"}, {"DEEPMAP_AUTO_SYNC_MAX_S": "30"}, 60),
                                ({"timeout_s": 600, "source": "size-aware"}, {"DEEPMAP_AUTO_SYNC_MAX_S": "x"}, 1200)):
        cc = W.case()
        cc.budget = budget
        cc.install()
        got = A.engine_budget(cc.root, env=dict(envkv))
        rows.append((budget, envkv, got, want))
    bad = [r for r in rows if r[2] != r[3]]
    check("engine_budget: user value as given / 2 x size-aware, capped at MAX_S (0 = uncapped), never under 60 s",
          not bad, bad)

    # pre_write vetoes (sync calls it before writing anything)
    def call_pre_write(engine, extraction, *, then):
        def step(fs, root, ctl):
            fs.veto_seen = ctl.pre_write(engine, extraction)
            return then(fs, root, ctl)
        return step

    c = W.case()
    c.sync.busy = "rebase"
    c.sync.plan = [call_pre_write("graphify", {"graphify_version": "0.8.39"},
                                  then=_lo("not-pushed", not_pushed_kind="veto", veto="git-busy",
                                           reason="vetoed: git-busy"))]
    _go(c)
    check("pre_write: a git operation started meanwhile -> 'git-busy'; recorded not-pushed:git-busy naming it, no "
          "backoff", getattr(c.sync, "veto_seen", None) == "git-busy"
          and c.last().get("outcome") == "not-pushed:git-busy" and "git rebase" in str(c.last().get("reason"))
          and c.last().get("suggest") is None and not c.state().get("consecutive_failures"),
          (getattr(c.sync, "veto_seen", None), c.last()))
    c = W.case()
    c.write_manifest(graph_engines=["ast"], extractor="ast")
    c.gversion = None          # R8 could not foresee it; the engine that actually ran was graphify
    c.sync.plan = [call_pre_write("graphify", {"graphify_version": "0.8.39"},
                                  then=_lo("not-pushed", not_pushed_kind="veto", veto="recipe:engine",
                                           reason="vetoed: recipe:engine"))]
    _go(c, DEEPMAP_EXTRACTOR="auto")
    check("pre_write: an ast graph about to get graphify nodes -> 'recipe:engine'; recorded with +1",
          getattr(c.sync, "veto_seen", None) == "recipe:engine"
          and c.last().get("outcome") == "not-pushed:recipe:engine" and c.state().get("consecutive_failures") == 1
          and "ast \u2192 graphify" in str(c.last().get("reason")), (getattr(c.sync, "veto_seen", None), c.last()))
    for gv, label, recorded in (("0.8.38", "outside the supported range", "0.8.39"),
                                ("0.8.40", "older than the build's 0.8.45", "0.8.45")):
        c = W.case()
        c.gversion = recorded         # R8 saw an acceptable engine; the one that actually ran differs
        c.write_manifest(recipe_graphify_version=recorded)
        c.sync.plan = [call_pre_write("graphify", {"graphify_version": gv},
                                      then=_lo("not-pushed", not_pushed_kind="veto", veto="recipe:engine-version"))]
        _go(c)
        check(f"pre_write: the engine that ran is {gv} ({label}) -> 'recipe:engine-version', suggests setup",
              getattr(c.sync, "veto_seen", None) == "recipe:engine-version"
              and c.last().get("outcome") == "not-pushed:recipe:engine-version"
              and c.last().get("suggest") == "setup", (getattr(c.sync, "veto_seen", None), c.last()))
    c = W.case()
    c.sync.plan = [call_pre_write("graphify", {"graphify_version": "0.8.39"}, then=_ok_synced())]
    _go(c)
    check("pre_write: nothing to object to -> None (the push goes ahead)",
          getattr(c.sync, "veto_seen", "x") is None and c.last().get("outcome") == "synced", c.last())
    c = W.case()
    c.sync.plan = [call_pre_write("ast", {}, then=_ok_synced())]
    c.write_manifest(graph_engines=["ast"], extractor="ast", recipe_env={"DEEPMAP_EXTRACTOR": "ast"})
    _go(c)
    check("pre_write: ast over an ast graph -> None", getattr(c.sync, "veto_seen", "x") is None, c.last())

    # before_embed
    def call_before_embed(fs, root, ctl):
        fs.embed_ok = ctl.before_embed()
        return _ok_synced()(fs, root, ctl)

    for mem, label, want, min_wait in (
            ([(8 * GIB, 16 * GIB)], "enough memory", True, 0),
            ([(8 * GIB, 16 * GIB), (1 * GIB, 16 * GIB), (1 * GIB, 16 * GIB), (8 * GIB, 16 * GIB)],
             "low, recovers within 120 s", True, 10),
            ([(8 * GIB, 16 * GIB), (1 * GIB, 16 * GIB)], "low for 120 s", False, 120),
            ([(8 * GIB, 16 * GIB), (None, None)], "unknown", True, 0)):
        c = W.case()
        c.memory = mem
        c.sync.plan = [call_before_embed]
        _go(c)
        check(f"before_embed [{label}] -> {want} (memory re-checked right before the model loads)",
              getattr(c.sync, "embed_ok", None) is want and c.clock.slept >= min_wait
              and (want or c.clock.slept <= 140), (getattr(c.sync, "embed_ok", None), c.clock.slept))


# =====================================================================================================================
# F — single-flight and R16
# =====================================================================================================================
def gate_single_flight(W: World) -> None:
    print(f"\n-- F. single-flight (one sync, the loser queues an unattended request) and R16 ({W.label}) --")
    A = W.A
    c = W.case()
    c.heads = [H2, H3]           # the second session sees a new HEAD: no cooldown, it reaches the lock
    ev_in, ev_go = threading.Event(), threading.Event()
    seen = {}

    def blocking(fs, root, ctl):
        ev_in.set()
        ev_go.wait(20)
        seen["ctl1"] = ctl
        return _ok_synced()(fs, root, ctl)

    def claims(fs, root, ctl):
        seen["ctl2"] = ctl
        try:
            (root / ".deepmap" / "sync.rerun").unlink()
        except OSError:
            pass
        return _ok_synced()(fs, root, ctl)

    c.sync.plan = [blocking, claims]
    c.install()
    out = {}
    with c.env():
        def first():
            out["rc1"], out["log1"] = _quiet(A.run, str(c.root), trigger="session-start", session="S1")
        t = threading.Thread(target=first, daemon=True)
        t.start()
        entered = ev_in.wait(20)
        try:
            calls_during = len(c.sync.calls)
            out["rc2"], out["log2"] = _quiet(A.run, str(c.root), trigger="session-start", session="S2")
            reqs = list(c.sync.requests)
        finally:
            ev_go.set()
            t.join(30)
    check("two runners on one project: the second ends rc 0 'coalesced' while the first syncs",
          entered and out.get("rc2") == 0 and "outcome=coalesced" in out.get("log2", "") and calls_during == 1,
          (entered, out.get("rc2"), out.get("log2", "")[-300:], calls_during))
    check("...the loser queued exactly ONE request, as an unattended one (cleanup_stale false, prune_policy "
          "evidence, origin unattended, trigger session-start)",
          reqs == [{"cleanup_stale": False, "force_prune": False, "prune_policy": "evidence", "origin": "unattended",
                    "trigger": "session-start"}], reqs)
    check("R16: the request left behind gets exactly ONE more sync() call, with a NEW control",
          out.get("rc1") == 0 and len(c.sync.calls) == 2 and seen.get("ctl1") is not None
          and seen.get("ctl2") is not None and seen["ctl1"] is not seen["ctl2"], (out.get("rc1"), len(c.sync.calls)))
    check("...and only the holder wrote autosync.json (its last_run is session S1's)",
          c.last().get("session_id") == "S1" and c.last().get("outcome") == "synced", c.last())

    c = W.case()
    c.sync.fp = {"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    lock = W.ST.try_lock(c.root / ".deepmap" / "autosync.lock")
    try:
        _go(c)
    finally:
        W.ST.release_lock(lock)
    check("a coalescing runner queues NOTHING when the sources did not change", c.rc == 0 and not c.sync.requests
          and "coalesced" in c.log and not c.state().get("last_run"), (c.rc, c.sync.requests))

    # R6c never loses a trigger (sync's own Y3 handshake): queue, then try the lock again
    lock_path_of = (lambda cc: cc.root / ".deepmap" / "autosync.lock")

    def claims_request(fs, root, ctl):
        try:
            (root / ".deepmap" / "sync.rerun").unlink()
        except OSError:
            pass
        return _ok_synced()(fs, root, ctl)

    def holder_state(cc, current):
        cc.write_state({"schema": 1, "current": current,
                        "last_run": {"id": "A-run", "session_id": "A", "outcome": "synced", "rc": 0,
                                     "finished_epoch": cc.clock.t - 5}})

    def a_current(cc, **extra):
        return dict({"id": "A", "pid": os.getpid(), "pid_created": W.ST.process_identity().get("pid_created"),
                     "session_id": "A", "trigger": "session-start", "started_epoch": cc.clock.t - 100,
                     "state": "syncing"}, **extra)

    # (a) the holder released the lock while this runner computed freshness and queued
    c = W.case()
    hold = [W.ST.try_lock(lock_path_of(c))]
    holder_state(c, a_current(c))
    real_request = c.sync._request_rerun

    def request_then_holder_releases(root, **kw):
        out = real_request(root, **kw)
        W.ST.release_lock(hold.pop()) if hold else None
        return out

    c.sync._request_rerun = request_then_holder_releases
    c.sync.plan = [claims_request]
    try:
        _go(c)
    finally:
        for h in hold:
            W.ST.release_lock(h)
    check("R6c: the holder released the lock while the loser queued -> the loser's retry takes it over and syncs "
          "(once), recording its own run — the request is never left behind",
          c.rc == 0 and len(c.sync.calls) == 1 and len(c.sync.requests) == 1
          and c.last().get("session_id") == "SID-1" and c.last().get("outcome") == "synced"
          and "continues as its refresh" in c.log, (c.rc, len(c.sync.calls), c.last(), c.log[-300:]))

    # (b) the holder still holds it but already made its one look at sync.rerun: wait for it, then take over
    for label, current in (("current.rerun_look 'done'", "done"), ("current cleared (it is recording)", None)):
        c = W.case()
        hold = [W.ST.try_lock(lock_path_of(c))]
        holder_state(c, a_current(c, rerun_look="done") if current == "done" else None)
        c.sleep_hook = (lambda hold=hold: W.ST.release_lock(hold.pop()) if hold else None)
        c.sync.plan = [claims_request]
        try:
            _go(c)
        finally:
            for h in hold:
                W.ST.release_lock(h)
        check(f"R6c: a holder past its look ({label}) -> the loser waits for the lock, takes over and syncs",
              c.rc == 0 and len(c.sync.calls) == 1 and c.last().get("session_id") == "SID-1"
              and c.last().get("outcome") == "synced" and 0 < c.clock.slept <= 2 * getattr(W.A, "TAKEOVER_POLL_S", 0.5),
              (c.rc, len(c.sync.calls), c.last().get("session_id"), c.clock.slept, c.log[-300:]))

    # (c) a holder that has yet to look will see the request: the loser does not wait at all
    c = W.case()
    hold = W.ST.try_lock(lock_path_of(c))
    holder_state(c, a_current(c))
    try:
        _go(c)
    finally:
        W.ST.release_lock(hold)
    check("R6c: a holder that has yet to look -> the loser queues one request and ends 'coalesced' at once (no wait, "
          "no sync)", c.rc == 0 and not c.sync.calls and len(c.sync.requests) == 1 and c.clock.slept == 0
          and "outcome=coalesced" in c.log and "queued a re-run" in c.log, (c.rc, c.clock.slept, c.log[-300:]))

    # (d) a holder past its look that never releases: a bounded wait, then 'coalesced' (the request stays queued)
    c = W.case()
    hold = W.ST.try_lock(lock_path_of(c))
    holder_state(c, a_current(c, rerun_look="done"))
    try:
        _go(c)
    finally:
        W.ST.release_lock(hold)
    check("R6c: a holder past its look that keeps the lock -> the loser waits at most TAKEOVER_WAIT_S, then ends "
          "'coalesced' with its request queued", c.rc == 0 and not c.sync.calls and len(c.sync.requests) == 1
          and 30.0 <= c.clock.slept <= 31.0 and "outcome=coalesced" in c.log,
          (c.rc, c.clock.slept, c.log[-300:]))

    # (e) the holder publishes its look BEFORE it looks, and not before
    c = W.case()
    looks = {}

    def snap_during(fs, root, ctl):
        looks["during"] = ((_read(root / ".deepmap" / "autosync.json") or {}).get("current") or {}).get("rerun_look")
        return _ok_synced()(fs, root, ctl)

    def path_at_look(root):
        looks["at_look"] = ((_read(Path(root) / ".deepmap" / "autosync.json") or {}).get("current")
                            or {}).get("rerun_look")
        return Path(root) / ".deepmap" / "sync.rerun"

    c.sync.plan = [snap_during]
    c.sync._rerun_request_path = path_at_look
    _go(c)
    check("the holder writes current.rerun_look 'done' right before its one look at sync.rerun (R16), never while it "
          "syncs", looks.get("during") is None and looks.get("at_look") == "done" and c.last().get("outcome") == "synced",
          looks)

    c = W.case()

    def leaves_request(fs, root, ctl):
        _write(root / ".deepmap" / "sync.rerun", {"origin": "unattended"})
        return _ok_synced()(fs, root, ctl)

    c.sync.plan = [leaves_request, _ok_synced()]
    pf = c.write_preempt()
    try:
        _go(c)
    finally:
        pf.unlink(missing_ok=True)
    check("R16: no extra pass while a user's sync is waiting to preempt (a live preempt record)",
          len(c.sync.calls) == 1 and c.last().get("outcome") == "synced", len(c.sync.calls))

    c = W.case()
    c.sync.plan = [leaves_request, leaves_request, leaves_request]
    _go(c)
    check("R16 is bounded: at most one extra sync() call per run", len(c.sync.calls) == 2, len(c.sync.calls))

    # R16 never runs a USER's request (origin "manual", or no origin: an older DeepMap's): only a sync the user starts
    # runs it — another background pass would only record user-request-pending over this run's outcome
    for label, req in (("origin manual", {"origin": "manual", "cleanup_stale": True, "prune_policy": "full"}),
                       ("no origin (an older DeepMap's)", {"cleanup_stale": False, "force_prune": False})):
        c = W.case()

        def leaves_user_request(fs, root, ctl, req=req):
            _write(root / ".deepmap" / "sync.rerun", req)
            return _ok_synced()(fs, root, ctl)

        c.sync.plan = [leaves_user_request, _lo("user-request-pending", rc=4, reason="pending")]
        _go(c)
        check(f"R16 after synced: a USER's request left queued ({label}) gets no extra pass — the run stays 'synced' "
              f"and the request stays queued as the user's sync left it",
              len(c.sync.calls) == 1 and c.last().get("outcome") == "synced" and c.rc == 0
              and _read(c.root / ".deepmap" / "sync.rerun") == req, (len(c.sync.calls), c.last().get("outcome")))
    c = W.case()
    _write(c.root / ".deepmap" / "sync.rerun", {"origin": "manual"})
    ufps = [{"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False},
            {"value": FP2, "kind": "git", "head": H3, "git_busy": None, "truncated": False}]

    def ufp_seq(root):
        c.sync.fp_calls += 1
        return dict(ufps.pop(0) if len(ufps) > 1 else ufps[0])

    c.sync.source_fingerprint = ufp_seq
    _go(c)
    check("R16 after 'fresh': a USER's queued request re-runs neither the freshness check nor a sync — 'fresh' stands",
          not c.sync.calls and c.last().get("outcome") == "fresh" and c.sync.fp_calls == 1,
          (len(c.sync.calls), c.last().get("outcome"), c.sync.fp_calls))

    c = W.case()
    c.sync.fp = {"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    _write(c.root / ".deepmap" / "sync.rerun", {"origin": "unattended"})
    fps = [{"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False},
           {"value": FP2, "kind": "git", "head": H3, "git_busy": None, "truncated": False}]

    def fp_seq(root):
        c.sync.fp_calls += 1
        return dict(fps.pop(0) if len(fps) > 1 else fps[0])

    c.sync.source_fingerprint = fp_seq
    _go(c)
    check("R16 after 'fresh': a queued request re-runs the freshness check once; stale now -> it syncs",
          len(c.sync.calls) == 1 and c.last().get("outcome") == "synced", (len(c.sync.calls), c.last()))


# =====================================================================================================================
# G — backoff / pause, the record schema, the hard-exit callback
# =====================================================================================================================
def gate_backoff(W: World) -> None:
    print(f"\n-- G. backoff, pause, lifting, the record schema, the hard-exit callback ({W.label}) --")
    A = W.A
    c = W.case()
    heads = iter([H2, H3, "1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40, "6" * 40])

    def nxt():
        c.heads = [next(heads)]

    fail = _lo("refused", ok=False, reason="refused for a test")
    nxt()
    c.sync.plan = [fail]
    _go(c)
    st = c.state()
    t0 = c.clock.t
    check("1st failure: consecutive_failures 1, backoff 6 h, last_failure recorded, not paused",
          st.get("consecutive_failures") == 1 and abs((st.get("backoff_until_epoch") or 0) - (t0 + 21600)) < 5
          and (st.get("last_failure") or {}).get("reason") == "refused for a test" and not st.get("paused"), st)
    check("precheck -> backoff while it lasts", c.precheck().get("code") == "backoff", c.precheck())
    c.clock.t += 3600
    nxt()
    _go(c)
    check("run during the backoff -> skipped:backoff rc 4, no sync, the count unchanged",
          c.rc == 4 and c.last().get("outcome") == "skipped:backoff" and len(c.sync.calls) == 1
          and c.state().get("consecutive_failures") == 1, (c.rc, c.last()))
    for i in (2, 3):
        c.clock.t += 21600 + 10
        nxt()
        c.sync.plan = [fail]
        _go(c)
    st = c.state()
    check("3rd consecutive failure -> paused (failures), detail = the latest reason",
          st.get("consecutive_failures") == 3 and (st.get("paused") or {}).get("reason") == "failures"
          and (st.get("paused") or {}).get("detail") == "refused for a test", st)
    check("precheck -> paused", c.precheck().get("code") == "paused", c.precheck())
    c.install()
    with c.env():
        level, text = A.status_line(c.root)
    check("status_line while paused: WARN 'paused — the last 3 background refreshes did not complete (...); a "
          "successful /deep-map:refresh resumes it'",
          level == "WARN" and text.startswith("paused \u2014 the last 3 background refreshes did not complete")
          and text.endswith("a successful /deep-map:refresh resumes it"), (level, text))
    c.clock.t += 60
    c.write_last(outcome="synced", verdict="READY", finished_epoch=c.clock.t)
    c.clock.t += 60
    check("a newer synced sync_last (a manual refresh) lifts the pause in precheck",
          c.precheck().get("ok") is True, c.precheck())
    c.clock.t += 1000
    nxt()
    c.sync.fp = {"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    _go(c)
    st = c.state()
    check("...and the next run resets the state (failures 0, no backoff, not paused)",
          c.rc == 0 and st.get("consecutive_failures") == 0 and st.get("paused") is None
          and st.get("backoff_until_epoch") is None, st)

    c = W.case()
    c.sync.plan = [_lo("not-pushed", not_pushed_kind="engine-downgrade", engine_timed_out=True, reason="t/o")]
    _go(c)
    st = c.state()
    check("an engine timeout pauses at once (engine-timeout, detail = the budget that ran out)",
          (st.get("paused") or {}).get("reason") == "engine-timeout" and (st.get("paused") or {}).get("detail")
          == "20 min", st)
    c.clock.t += 1000
    c.heads = [H3]
    _go(c)
    check("...the next run: skipped:paused naming the time budget",
          c.last().get("outcome") == "skipped:paused" and "time budget (20 min)" in str(c.last().get("reason")),
          c.last())

    c = W.case()
    c.write_state({"schema": 1, "consecutive_failures": 2, "backoff_until_epoch": c.clock.t - 10,
                   "last_failure": {"outcome": "failed:index", "reason": "x", "finished_epoch": c.clock.t - 21610}})
    c.write_last(outcome="synced", verdict="READY", finished_epoch=c.clock.t - 100)
    c.sync.plan = [fail]
    _go(c)
    check("a failure after a manual success counts from 1 again (the success lifted the earlier ones)",
          c.state().get("consecutive_failures") == 1 and not c.state().get("paused"), c.state())

    # the record schema
    c = W.case()
    _go(c)
    st = c.state()
    lr = st.get("last_run") or {}
    keys = {"id", "session_id", "trigger", "started_utc", "finished_utc", "finished_epoch", "wall_s", "rc",
            "outcome", "reason", "advice", "suggest", "head", "fresh_at_head", "fingerprint", "sync_id", "verdict",
            "reasons", "built_at_sha", "dirty", "stale_kept", "embeddings_deferred", "took_s"}
    check("autosync.json: schema 1, current null, last_session_start {started_epoch, head}, last_run with every "
          "§2.6.4 field", st.get("schema") == 1 and st.get("current") is None and keys <= set(lr)
          and st.get("last_session_start") == {"started_epoch": lr.get("finished_epoch") - lr.get("wall_s"),
                                               "head": H2}
          and lr.get("session_id") == "SID-1" and lr.get("trigger") == "session-start"
          and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ-\d+", str(lr.get("id"))), (st, sorted(keys - set(lr))))
    check("port_registry.touch_last_used is called once the run holds its lock", c.touched == 1, c.touched)

    seen = {}

    def snap_current(fs, root, ctl):
        seen["current"] = (_read(root / ".deepmap" / "autosync.json") or {}).get("current")
        return _ok_synced()(fs, root, ctl)

    c = W.case()
    c.sync.plan = [snap_current]
    _go(c)
    cur = seen.get("current") or {}
    check("while it syncs, autosync.json `current` names this run (pid + stamp, session, state 'syncing')",
          cur.get("pid") == os.getpid() and cur.get("state") == "syncing" and cur.get("session_id") == "SID-1"
          and cur.get("pid_created") == W.ST.process_identity().get("pid_created")
          and cur.get("trigger") == "session-start", cur)

    def hard_exit(fs, root, ctl):
        ctl.on_hard_exit({"id": "HX-1", "outcome": "preempted", "built_at_sha": H2, "source_fingerprint": FP2})
        seen["after_hard_exit"] = _read(root / ".deepmap" / "autosync.json") or {}
        return True, {"outcome": "preempted", "id": "HX-1"}

    c = W.case()
    c.write_state({"schema": 1, "consecutive_failures": 2, "backoff_until_epoch": None})
    c.sync.plan = [hard_exit]
    _go(c)
    snap = seen.get("after_hard_exit") or {}
    check("the hard-exit callback (the process is about to end) records last_run 'preempted' rc 0, clears current, "
          "resets the failure count", (snap.get("last_run") or {}).get("outcome") == "preempted"
          and (snap.get("last_run") or {}).get("rc") == 0 and snap.get("current") is None
          and (snap.get("last_run") or {}).get("sync_id") == "HX-1" and snap.get("consecutive_failures") == 0, snap)
    check("...and the main thread (had the process survived) does not record a second outcome over it",
          c.last().get("outcome") == "preempted" and c.log.count("outcome=preempted") == 1, (c.last(), c.log[-300:]))

    def hard_exit_enrich(fs, root, ctl):
        # the record sync_status writes when ONLY deepmap enrich waited (yield_fields: busy-enrich, rc 4)
        ctl.on_hard_exit({"id": "HX-2", "outcome": "busy-enrich", "rc": 4, "preempted_by": ["enrich"],
                          "reason": "a deepmap enrich run needed this project's sync lock; stopped while reading the "
                                    "code (enrich does not refresh the graph)",
                          "built_at_sha": H2, "source_fingerprint": FP2})
        seen["after_enrich_exit"] = _read(root / ".deepmap" / "autosync.json") or {}
        return True, {"outcome": "busy-enrich", "id": "HX-2"}

    c = W.case()
    c.write_state({"schema": 1, "consecutive_failures": 2, "backoff_until_epoch": None})
    c.sync.plan = [hard_exit_enrich]
    _go(c)
    snap = seen.get("after_enrich_exit") or {}
    lr = snap.get("last_run") or {}
    check("the hard-exit callback for a yield to deepmap enrich ONLY (its record says busy-enrich) records "
          "skipped:busy-enrich rc 4 — never 'preempted' (no user's sync runs next) — clears current, suggests "
          "/deep-map:refresh and leaves the failure count alone", lr.get("outcome") == "skipped:busy-enrich"
          and lr.get("rc") == 4 and snap.get("current") is None and lr.get("sync_id") == "HX-2"
          and "enrich" in str(lr.get("reason")) and lr.get("suggest") == "refresh"
          and snap.get("consecutive_failures") == 2, snap)
    check("...recorded once (the main thread does not write over it)",
          c.last().get("outcome") == "skipped:busy-enrich" and c.log.count("outcome=skipped:busy-enrich") == 1
          and "outcome=preempted" not in c.log, (c.last(), c.log[-300:]))


# =====================================================================================================================
# O — the background time limit reads the last sync that did NOT run in the background
# =====================================================================================================================
def _bg_pass(c: Case, wall, *, fp=FP2, head=H2, captured=None, utc="2026-10-23T09:00:00Z", then=None):
    """A fake sync step doing to the manifest what a background pass does: sync rewrites it with this pass's own
    sync_wall_s (a below-normal pass under load: slow) and its capture time (inside the sync call, unless `captured`
    says otherwise), then reports `then` (default synced)."""
    def step(fs, root, ctl):
        m = c.manifest_dict(sync_wall_s=wall, source_fingerprint=fp, built_at_sha=head, built_at_utc=utc,
                            edits_seen_through_epoch=c.clock.t if captured is None else captured)
        _write(root / ".deepmap" / "graph_build.json", m)
        return (then or _ok_synced(wall_s=wall))(fs, root, ctl)
    return step


def _restale(c: Case, head: str, fp: str) -> None:
    """The next session start, later, with new commits: a new HEAD (no cooldown) and new sources (stale)."""
    c.clock.t += 1000
    c.heads = [head]
    c.sync.fp = {"value": fp, "kind": "git", "head": head, "git_busy": None, "truncated": False}


def gate_budget_walls(W: World) -> None:
    print(f"\n-- O. the time limit reads the last sync that did not run in the background ({W.label}) --")
    H4, FP3, FP4 = "9" * 40, "d" * 32, "c" * 32
    c = W.case()
    c.write_manifest(sync_wall_s=300.0, built_at_utc="2026-10-20T08:00:00Z", edits_seen_through_epoch=c.clock.t - 86400)
    c.sync.plan = [_bg_pass(c, 1818.0)]
    _go(c)
    pc = c.precheck(head=H3)
    check("a background pass that took 1818 s (below-normal priority on a loaded machine) -> the next precheck is NOT "
          "too-slow: the limit reads the last sync that did not run in the background (300 s)",
          c.last().get("outcome") == "synced" and pc.get("ok") is True and pc.get("code") is None,
          (c.last().get("outcome"), pc))
    check("...precheck's took_s stays the last sync's wall (1818 s: what the notify quotes as 'its last sync took'); "
          "limit_took_s is the 300 s the limit reads", pc.get("took_s") == 1818.0 and pc.get("limit_took_s") == 300.0,
          pc)
    _restale(c, H3, FP3)
    c.sync.plan = [_bg_pass(c, 1900.0, fp=FP3, head=H3, utc="2026-10-23T10:00:00Z")]
    _go(c)
    pc = c.precheck(head=H4)
    lv, tx = c.status()
    check("...the next session start is not skipped:too-slow either (R10 reads the same 300 s), and after a second "
          "slow pass (1900 s) precheck is still ok, status_line OK 'on — last run ...'",
          c.rc == 0 and c.last().get("outcome") == "synced" and len(c.sync.calls) == 2 and pc.get("ok") is True
          and pc.get("limit_took_s") == 300.0 and lv == "OK" and tx.startswith("on — last run"),
          (c.rc, c.last().get("outcome"), pc, lv, tx))
    # a sync the user started rewrites the manifest: ITS wall counts, slow or fast
    c.write_manifest(sync_wall_s=1500.0, built_at_utc="2026-10-23T11:00:00Z", edits_seen_through_epoch=c.clock.t + 10,
                     source_fingerprint=FP4)
    pc = c.precheck(head=H4)
    lv, tx = c.status()
    check("a sync the user started that took 25 min -> precheck too-slow (limit_took_s 1500), and its reason says so",
          pc.get("code") == "too-slow" and pc.get("ok") is False and pc.get("limit_took_s") == 1500.0
          and "25 min" in str(pc.get("reason")) and "20 min" in str(pc.get("reason")), pc)
    check("...status_line names that block: WARN 'on — but the next session start will not refresh: its last sync "
          "took 25 min (... the background limit is 20 min; DEEPMAP_AUTO_SYNC_MAX_S raises it ...)'",
          lv == "WARN" and tx.startswith("on — but the next session start will not refresh: its last sync")
          and "25 min" in tx and "20 min" in tx and "DEEPMAP_AUTO_SYNC_MAX_S raises it" in tx, (lv, tx))
    c.write_manifest(sync_wall_s=200.0, built_at_utc="2026-10-23T12:00:00Z", edits_seen_through_epoch=c.clock.t + 20,
                     source_fingerprint=FP4)
    check("...a later fast sync the user started (200 s) lifts it", c.precheck(head=H4).get("ok") is True,
          c.precheck(head=H4))

    # R16's extra pass: pass 1 and pass 2 both run in the background; the limit still reads the user's 300 s
    c = W.case()
    c.write_manifest(sync_wall_s=300.0, built_at_utc="2026-10-20T08:00:00Z", edits_seen_through_epoch=c.clock.t - 86400)

    def pass1(fs, root, ctl):
        _write(root / ".deepmap" / "sync.rerun", {"origin": "unattended"})
        return _bg_pass(c, 1700.0)(fs, root, ctl)

    def pass2(fs, root, ctl):
        (root / ".deepmap" / "sync.rerun").unlink(missing_ok=True)
        return _bg_pass(c, 1750.0, utc="2026-10-23T09:30:00Z")(fs, root, ctl)

    c.sync.plan = [pass1, pass2]
    _go(c)
    pc = c.precheck(head=H3)
    check("two background passes in one run (R16) -> precheck still reads the user's 300 s (ok)",
          len(c.sync.calls) == 2 and pc.get("ok") is True and pc.get("limit_took_s") == 300.0, (len(c.sync.calls), pc))

    # a manifest a sync the USER started wrote while this run's sync only queued behind it (captured before the call)
    c = W.case()
    c.write_manifest(sync_wall_s=300.0, built_at_utc="2026-10-20T08:00:00Z", edits_seen_through_epoch=c.clock.t - 86400)
    c.sync.plan = [_bg_pass(c, 1500.0, captured=c.clock.t - 50, utc="2026-10-23T08:59:00Z",
                            then=_lo("queued", reason="queued behind a sync the user started"))]
    _go(c)
    pc = c.precheck(head=H3)
    check("a manifest written meanwhile by a sync the user started (its capture predates this run's sync call; this "
          "run only queued) is that user's: its 1500 s counts -> precheck too-slow",
          c.last().get("outcome") == "queued" and pc.get("code") == "too-slow" and pc.get("limit_took_s") == 1500.0,
          (c.last().get("outcome"), pc))

    # no sync time recorded by a sync the user started (a legacy manifest): a background wall never stands in for it
    c = W.case()
    c.write_manifest(sync_wall_s=None)
    c.sync.plan = [_bg_pass(c, 1818.0)]
    _go(c)
    pc = c.precheck(head=H3)
    check("a legacy manifest (no sync time) + a 1818 s background pass -> precheck ok, limit_took_s None (the "
          "background wall is not taken as the sync time)", c.last().get("outcome") == "synced"
          and pc.get("ok") is True and pc.get("limit_took_s") is None, pc)
    _restale(c, H3, FP3)
    c.files = 1501
    _go(c)
    check("...so with more than 1,500 source files the next run is still skipped:needs-timing (only a sync the user "
          "starts records the timing), and precheck with a HEAD repeats it",
          c.rc == 4 and c.last().get("outcome") == "skipped:needs-timing" and len(c.sync.calls) == 1
          and c.precheck(head=H4).get("code") == "needs-timing", (c.rc, c.last().get("outcome"), c.precheck(head=H4)))


# =====================================================================================================================
# P — precheck(..., announce=True): the hooks' "will a session start refresh now?", non-git too; user-request repeats
# =====================================================================================================================
def gate_precheck_announce(W: World) -> None:
    print(f"\n-- P. precheck(announce=True): repeats always, the cooldown with head None = non-git time rule; "
          f"user-request repeats; status_line names a block ({W.label}) --")
    res = {}
    c = W.case(git=False)
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 60, "head": None}})
    res["non-git, 60 s ago, announce"] = (c.precheck(announce=True), "cooldown")
    res["non-git, 60 s ago, no announce (the heal hook's spawn)"] = (c.precheck(), None)
    res["non-git, 60 s ago, the next-start head"] = (c.precheck(head=NEXT_START_HEAD, announce=True), None)
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 901, "head": None}})
    res["non-git, 901 s ago, announce"] = (c.precheck(announce=True), None)
    c = W.case()
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 60, "head": H2}})
    res["git, recorded HEAD, head None + announce (None is the non-git rule)"] = (c.precheck(announce=True), None)
    res["git, same HEAD + announce"] = (c.precheck(head=H2, announce=True), "cooldown")
    res["git, same HEAD, no announce (unchanged)"] = (c.precheck(head=H2), "cooldown")
    res["git, HEAD moved + announce"] = (c.precheck(head=H3, announce=True), None)
    c = W.case(git=False)
    c.write_manifest(sync_wall_s=None)
    c.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "skipped:needs-timing", "rc": 4,
                                             "finished_epoch": c.clock.t - 30, "why_code": "needs-timing",
                                             "reason": "needs timing", "suggest": "refresh"}})
    res["non-git, recorded needs-timing, announce"] = (c.precheck(announce=True), "needs-timing")
    res["non-git, recorded needs-timing, no announce"] = (c.precheck(), None)
    res["non-git, recorded needs-timing, the next-start head"] = (c.precheck(head=NEXT_START_HEAD, announce=True),
                                                                  "needs-timing")
    # skipped:user-request repeats while the user's request stays queued
    c = W.case()
    c.write_state({"schema": 1, "last_run": {"id": "x", "outcome": "skipped:user-request", "rc": 4,
                                             "finished_epoch": c.clock.t - 30, "why_code": "user-request",
                                             "reason": "a re-run that a sync the user started queued is waiting",
                                             "suggest": "refresh"}})
    rerun = c.root / ".deepmap" / "sync.rerun"
    _write(rerun, {"origin": "manual"})
    res["user-request queued, head"] = (c.precheck(head=H3), "user-request")
    res["user-request queued, announce, no head"] = (c.precheck(announce=True), "user-request")
    res["user-request queued, no head (the heal hook's spawn)"] = (c.precheck(), None)
    lv, tx = c.status()
    rerun.unlink()
    res["user-request claimed since (sync.rerun gone)"] = (c.precheck(head=H3), None)
    _write(rerun, {"origin": "manual"})
    later = c.clock.t + 60
    os.utime(c.root / ".deepmap" / "graph_build.json", (later, later))
    res["user-request queued, manifest rewritten since"] = (c.precheck(head=H3), None)
    for label, (got, want) in res.items():
        check(f"precheck [{label}] -> {want or 'ok'}", got.get("code") == want and got.get("ok") is (want is None),
              got)
    check("status_line names a repeating skip as a block: WARN 'on — but the next session start will not refresh: "
          "<the recorded reason>'", lv == "WARN"
          and tx.startswith("on — but the next session start will not refresh: a re-run that a sync the user")
          and "/deep-map:refresh" in tx, (lv, tx))

    # status_line names the other precheck blocks; a cooldown or a transient skip is not one
    c = W.case()
    c.write_manifest(recipe_env={"DEEPMAP_EXCLUDE_TESTS": "on"})
    lv, tx = c.status(DEEPMAP_EXCLUDE_TESTS="0")
    check("status_line, recipe:settings -> WARN naming the setting and both values",
          lv == "WARN" and tx.startswith("on — but the next session start will not refresh: the graph-shaping")
          and "DEEPMAP_EXCLUDE_TESTS=on at build time, off now" in tx, (lv, tx))
    c = W.case()
    c.write_manifest(recipe_deepmap_version="9.9.9")
    lv, tx = c.status()
    check("status_line, recipe:older-code -> WARN naming both DeepMap versions",
          lv == "WARN" and "will not refresh: it was built by DeepMap 9.9.9, newer than the 0.29.0" in tx, (lv, tx))
    c = W.case()
    lv, tx = c.status(NEO4J_URI="bolt://neo4j:s3cret@db.example:7687")
    check("status_line, remote-database -> WARN naming the host, never the password",
          lv == "WARN" and "will not refresh: this project's database is not on this machine" in tx
          and "db.example" in tx and "s3cret" not in tx, (lv, tx))
    c = W.case()
    c.write_state({"schema": 1, "backoff_until_epoch": c.clock.t + 3600, "consecutive_failures": 1,
                   "last_failure": {"outcome": "failed:index", "reason": "index boom",
                                    "finished_epoch": c.clock.t - 600},
                   "last_run": {"outcome": "failed:index", "trigger": "session-start",
                                "finished_epoch": c.clock.t - 600, "wall_s": 30.0, "reason": "index boom"}})
    lv, tx = c.status()
    check("status_line, backing off -> WARN with the failure and the retry time",
          lv == "WARN" and "will not refresh: the last background refresh failed 10 min ago (index boom)" in tx
          and "retries it automatically after" in tx, (lv, tx))
    c = W.case(git=False)
    c.write_state({"schema": 1, "last_session_start": {"started_epoch": c.clock.t - 60, "head": None},
                   "last_run": {"outcome": "synced", "trigger": "session-start", "finished_epoch": c.clock.t - 30,
                                "wall_s": 40.0, "verdict": "READY", "reason": None}})
    lv, tx = c.status()
    check("status_line, in the 15-minute cooldown (non-git: time alone) -> not a block (OK 'on — last run ...')",
          lv == "OK" and tx.startswith("on — last run"), (lv, tx))
    c = W.case(manifest=False)
    lv, tx = c.status()
    check("status_line, a project never set up -> OK 'on — no background refresh has run yet (... once "
          "/deep-map:setup has built this project's graph)'",
          lv == "OK" and tx.startswith("on — no background refresh has run yet") and "/deep-map:setup" in tx,
          (lv, tx))


# =====================================================================================================================
# Q — a manifest from before build_recipe: the refresh uses the session's graph-shaping settings
# =====================================================================================================================
def gate_legacy_recipe(W: World) -> None:
    print(f"\n-- Q. a legacy manifest (no build_recipe): the session's graph-shaping settings, never stripped "
          f"({W.label}) --")
    c = W.case()
    c.write_manifest(build_recipe=None)
    pc1, pc0 = c.precheck(head=H3, DEEPMAP_PRECISE_CALLS="1"), c.precheck(head=H3)
    check("precheck, legacy manifest + DEEPMAP_PRECISE_CALLS=1 in the session -> ok (the build's settings were never "
          "recorded, so the session's are used: no recipe:settings)", pc1.get("ok") is True and pc0.get("ok") is True,
          (pc1, pc0))
    c = W.case()
    c.write_manifest(recipe_env={})
    pc = c.precheck(head=H3, DEEPMAP_PRECISE_CALLS="1")
    check("...a manifest that RECORDED its settings (build_recipe.env {}: all defaults) + DEEPMAP_PRECISE_CALLS=1 -> "
          "recipe:settings, as before", pc.get("code") == "recipe:settings", pc)
    c = W.case()
    c.write_manifest(build_recipe=None)
    _go(c, DEEPMAP_PRECISE_CALLS="1")
    env_at = c.sync.calls[0][2] if c.sync.calls else {}
    check("run, legacy manifest, DEEPMAP_PRECISE_CALLS=1 in the environment it was started from -> it syncs; sync is "
          "loaded and runs with DEEPMAP_PRECISE_CALLS=1 (never stripped, never replaced)",
          c.rc == 0 and c.last().get("outcome") == "synced" and (c.loaded_env or {}).get("DEEPMAP_PRECISE_CALLS") == "1"
          and env_at.get("DEEPMAP_PRECISE_CALLS") == "1", (c.rc, c.last().get("outcome"), c.loaded_env, env_at))
    check("...its log says the manifest recorded no build settings and names the session's (DEEPMAP_PRECISE_CALLS=on)",
          "records no build settings" in c.log and "DEEPMAP_PRECISE_CALLS=on" in c.log, c.log[-500:])
    c = W.case()
    c.write_manifest(build_recipe=None)
    _go(c)
    env_at = c.sync.calls[0][2] if c.sync.calls else {}
    check("run, legacy manifest, the flag unset in the session -> it syncs with it unset (nothing invented)",
          c.rc == 0 and c.last().get("outcome") == "synced" and "DEEPMAP_PRECISE_CALLS" in env_at
          and env_at.get("DEEPMAP_PRECISE_CALLS") is None, (c.rc, env_at))
    c = W.case()
    c.write_manifest(recipe_env={})
    _expect(_go(c, DEEPMAP_PRECISE_CALLS="1"),
            "a recorded recipe (all defaults) + DEEPMAP_PRECISE_CALLS=1 in the session", 4, "skipped:recipe:settings",
            synced=False, reason_has="DEEPMAP_PRECISE_CALLS=", suggest="refresh")
    c = W.case()
    c.write_manifest(recipe_env={"DEEPMAP_PRECISE_CALLS": "on"})
    _go(c)
    env_at = c.sync.calls[0][2] if c.sync.calls else {}
    check("a recorded DEEPMAP_PRECISE_CALLS=on, the session leaves it unset -> replayed (the sync runs with it on)",
          c.rc == 0 and env_at.get("DEEPMAP_PRECISE_CALLS") == "on", (c.rc, env_at))


# =====================================================================================================================
# R — a user's queued request ends the run before it waits for the database or the machine slot
# =====================================================================================================================
def gate_user_request_early(W: World) -> None:
    print(f"\n-- R. a user's queued re-run: the run ends skipped:user-request before any wait ({W.label}) --")
    c = W.case()
    req = {"origin": "manual", "cleanup_stale": True, "prune_policy": "full"}
    _write(c.root / ".deepmap" / "sync.rerun", req)
    c.heal, c.bolt = None, [False]          # a run that waited for the database would end unreachable after 3 min
    _expect(_go(c), "a user's request queued, sources changed", 4, "skipped:user-request", synced=False,
            reason_has="a background refresh never runs one", suggest="refresh")
    check("...it ended before waiting for the database or the slot (no time slept), the request untouched, why_code "
          "user-request, no backoff", c.clock.slept == 0 and _read(c.root / ".deepmap" / "sync.rerun") == req
          and c.last().get("why_code") == "user-request" and not c.state().get("consecutive_failures"),
          (c.clock.slept, c.last()))
    c = W.case()
    _write(c.root / ".deepmap" / "sync.rerun", {"origin": "unattended", "prune_policy": "evidence"})
    _expect(_go(c), "an unattended request queued (the runner's own kind) -> the sync runs (and claims it)", 0,
            "synced", synced=True)
    c = W.case()
    _write(c.root / ".deepmap" / "sync.rerun", {"origin": "manual"})
    c.write_running(origin="manual", phase="extract", fp=FP2)
    _expect(_go(c), "a user's request queued while a user's sync of the same sources runs -> covered (that sync runs "
            "it)", 0, "covered", synced=False)
    c = W.case()
    _write(c.root / ".deepmap" / "sync.rerun", {"origin": "manual"})
    c.sync.fp = {"value": FP1, "kind": "git", "head": H2, "git_busy": None, "truncated": False}
    _expect(_go(c), "a user's request queued, sources unchanged -> fresh stands", 0, "fresh", synced=False)


# =====================================================================================================================
# H — priority
# =====================================================================================================================
def gate_priority(tree: Path, W: World) -> None:
    print("\n-- H. priority: lowered once per run; really BELOW_NORMAL / nice +10 (a child process) --")
    for label, envkv in (("synced run", {}), ("disabled run", {"DEEPMAP_AUTO_SYNC": "0"})):
        c = W.case()
        _go(c, **envkv)
        check(f"_lower_priority called exactly once ({label})", c.lower == 1, c.lower)
    # the child also counts the calls _lower_priority makes to the I/O step (a recorder in place of it)
    rec = "io = []; A._lower_io_priority = lambda *a, **k: io.append(1) or True; "
    if os.name == "nt":
        code = ("import sys, ctypes; sys.path.insert(0, sys.argv[1]); import auto_sync as A; " + rec +
                "k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
                "k.GetPriorityClass.argtypes = (ctypes.c_void_p,); "
                "before = k.GetPriorityClass(k.GetCurrentProcess()); A._lower_priority(); "
                "print(before, k.GetPriorityClass(k.GetCurrentProcess()), len(io))")
    else:
        code = ("import os, sys; sys.path.insert(0, sys.argv[1]); import auto_sync as A; " + rec +
                "before = os.nice(0); A._lower_priority(); print(before, os.nice(0), len(io))")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tree / "build")], capture_output=True,
                       text=True, timeout=60)
    try:
        before, after, io_calls = (int(x) for x in (p.stdout or "").split()[-3:])
    except Exception:
        before = after = io_calls = None
    if os.name == "nt":
        check("Windows: the real _lower_priority sets BELOW_NORMAL_PRIORITY_CLASS (0x4000) on the runner itself",
              after == 0x4000, (before, after, (p.stderr or "")[-300:]))
    else:
        check("POSIX: the real _lower_priority adds nice 10 (capped at 19)",
              before is not None and after == min(19, before + 10), (before, after, (p.stderr or "")[-300:]))
    check("_lower_priority also takes the I/O step (_lower_io_priority) — once", io_calls == 1,
          (io_calls, (p.stdout or "")[-200:], (p.stderr or "")[-300:]))

    # the I/O step: ioprio_set(IOPRIO_WHO_PROCESS, 0, best-effort level 7) on Linux, only for a known syscall number
    A = W.A
    if not callable(getattr(A, "_lower_io_priority", None)):
        check("auto_sync has the Linux I/O-priority step (_lower_io_priority)", False, "missing")
        return
    rows = {}
    for plat, machine in (("linux", "x86_64"), ("linux", "aarch64"), ("linux", "riscv64"), ("linux", "sparc64"),
                          ("linux", "armv7l"), ("win32", "AMD64"), ("darwin", "arm64")):
        calls = []
        ok = A._lower_io_priority(plat, machine, syscall=lambda *a: calls.append(a) or 0)
        rows[(plat, machine)] = (ok, calls)
    want = {("linux", "x86_64"): (True, [(251, 1, 0, (2 << 13) | 7)]),
            ("linux", "aarch64"): (True, [(30, 1, 0, (2 << 13) | 7)]),
            ("linux", "riscv64"): (True, [(30, 1, 0, (2 << 13) | 7)]),
            ("linux", "sparc64"): (False, []), ("linux", "armv7l"): (False, []),
            ("win32", "AMD64"): (False, []), ("darwin", "arm64"): (False, [])}
    check("_lower_io_priority: Linux ioprio_set(WHO_PROCESS, self, best-effort level 7 — `ionice -c2 -n7`, never the "
          "idle class) on x86_64 / aarch64 / riscv64; nothing on an unlisted architecture, Windows or macOS",
          rows == want, {k: v for k, v in rows.items() if want.get(k) != v})

    def raising(*a):
        raise OSError("no syscall")

    check("_lower_io_priority never raises (a failing or refused syscall -> False)",
          A._lower_io_priority("linux", "x86_64", syscall=raising) is False
          and A._lower_io_priority("linux", "x86_64", syscall=lambda *a: -1) is False)
    get_nr = {"x86_64": 252, "amd64": 252, "aarch64": 31, "arm64": 31, "riscv64": 31}
    import platform as _platform
    nr = get_nr.get(_platform.machine().lower())
    if not sys.platform.startswith("linux") or nr is None:
        skip("Linux: the real _lower_priority leaves the runner at I/O best-effort level 7 (ioprio_get in a child)",
             f"runs on Linux x86_64 / aarch64 / riscv64 only (this is {sys.platform} {_platform.machine()})")
    else:
        code = ("import sys, ctypes; sys.path.insert(0, sys.argv[1]); import auto_sync as A; A._lower_priority(); "
                "libc = ctypes.CDLL(None, use_errno=True); libc.syscall.restype = ctypes.c_long; "
                f"print(libc.syscall(ctypes.c_long({nr}), ctypes.c_long(1), ctypes.c_long(0)))")
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tree / "build")], capture_output=True,
                           text=True, timeout=60)
        got = (p.stdout or "").strip().splitlines()[-1:] or ["?"]
        check("Linux: the real _lower_priority leaves the runner at I/O best-effort level 7 (ioprio_get in a child)",
              got[0] == str((2 << 13) | 7), (got, (p.stderr or "")[-300:]))


# =====================================================================================================================
# I — status_line and set_enabled
# =====================================================================================================================
def gate_status_and_switches(W: World) -> None:
    print(f"\n-- I. status_line texts; set_enabled (instance through instance_state, machine atomic) ({W.label}) --")
    A = W.A
    c = W.case()

    def line(**envkv):
        c.install()
        with c.env(**envkv):
            return A.status_line(c.root)

    check("status_line: off by env", line(DEEPMAP_AUTO_SYNC="0") == ("OK", "off (DEEPMAP_AUTO_SYNC=0)"),
          line(DEEPMAP_AUTO_SYNC="0"))
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"enabled": False}})
    check("status_line: off by the project", line() == ("OK", "off (.deepmap/instance.json auto_sync.enabled=false)"),
          line())
    (c.root / ".deepmap" / "instance.json").unlink()
    _write(c.home / "auto-sync.json", {"enabled": False})
    lv, tx = line()
    check("status_line: off by the machine switch (names its path)",
          lv == "OK" and tx.startswith("off (machine switch ") and tx.rstrip(")").endswith("auto-sync.json"), tx)
    (c.home / "auto-sync.json").unlink()
    (c.home / "auto-sync.json").write_text("{not json", encoding="utf-8")
    lv, tx = line()
    check("status_line: a machine switch it does not understand reads as off — WARN, naming the file and the fix",
          lv == "WARN" and tx.startswith("off (machine switch ") and "not understood" in tx
          and '{"enabled": false}' in tx, (lv, tx))
    (c.home / "auto-sync.json").unlink()
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": {"enabled": "maybe"}})
    lv, tx = line()
    check("status_line: an instance.json auto_sync.enabled it does not understand reads as off — WARN",
          lv == "WARN" and '.deepmap/instance.json auto_sync.enabled="maybe" is not understood' in tx, (lv, tx))
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": False})
    check("status_line: a bare instance.json auto_sync=false is off (OK)",
          line() == ("OK", "off (.deepmap/instance.json auto_sync=false)"), line())
    (c.root / ".deepmap" / "instance.json").unlink()
    check("status_line: on, never run",
          line() == ("OK", "on \u2014 no background refresh has run yet (it runs when a Claude Code session starts)"),
          line())
    c.write_state({"schema": 1, "current": {"id": "x", "pid": os.getpid(),
                                            "pid_created": W.ST.process_identity()["pid_created"],
                                            "state": "waiting-db", "started_epoch": 1_800_000_000.0,
                                            "trigger": "session-start"}})
    lv, tx = line()
    check("status_line: running since HH:MM UTC (phase words)",
          lv == "OK" and re.fullmatch(r"on \u2014 running since \d\d:\d\d UTC \(waiting for the database\)", tx),
          (lv, tx))
    c.write_state({"schema": 1, "current": {"id": "x", "pid": os.getpid(), "pid_created": 1.0, "state": "syncing",
                                            "started_epoch": 1.0},
                   "last_run": {"outcome": "synced", "trigger": "session-start", "finished_epoch": 1_800_000_000.0,
                                "wall_s": 240.0, "verdict": "READY", "reason": None}})
    lv, tx = line()
    check("status_line: a dead `current` is not 'running'; the last run: 'on — last run YYYY-MM-DD HH:MM UTC: "
          "session-start, synced, 4 min, verdict READY'",
          lv == "OK" and re.fullmatch(r"on \u2014 last run \d{4}-\d\d-\d\d \d\d:\d\d UTC: session-start, synced, "
                                      r"4 min, verdict READY", tx), (lv, tx))
    c.write_state({"schema": 1, "last_run": {"outcome": "failed:index", "trigger": "session-start",
                                             "finished_epoch": 1_800_000_000.0, "wall_s": 30.0,
                                             "reason": "index boom"}})
    lv, tx = line()
    check("status_line: WARN for a failed / not-pushed / unreachable last run, with its reason",
          lv == "WARN" and tx.endswith("; index boom") and "failed:index" in tx, (lv, tx))
    c.write_state({"schema": 1, "last_run": {"outcome": "skipped:git-busy", "trigger": "session-start",
                                             "finished_epoch": 1_800_000_000.0, "wall_s": 1.0, "reason": "a git x"}})
    check("status_line: a skip is OK (informational), with its reason", line()[0] == "OK" and
          line()[1].endswith("; a git x"), line())

    c = W.case()
    _write(c.root / ".deepmap" / "instance.json", {"learned_fusion": {"arm_weights": [1, 1, 1]}})
    c.install()
    with c.env():
        ok1, msg1 = A.set_enabled(c.root, False)
        after_off = _read(c.root / ".deepmap" / "instance.json")
        en_off = A.enabled(c.root)
        ok2, msg2 = A.set_enabled(c.root, True)
        after_on = _read(c.root / ".deepmap" / "instance.json")
        en_on = A.enabled(c.root)
        ok3, msg3 = A.set_enabled(c.root, False, machine=True)
        mach = _read(c.home / "auto-sync.json")
        en_m = A.enabled(c.root)
        ok4, msg4 = A.set_enabled(c.root, True, machine=True)
        en_m2 = A.enabled(c.root)
    check("set_enabled(False): instance.json auto_sync.enabled=false, every other key kept, enabled() -> instance",
          ok1 and after_off == {"learned_fusion": {"arm_weights": [1, 1, 1]}, "auto_sync": {"enabled": False}}
          and tuple(en_off) == (False, "instance"), (ok1, msg1, after_off, en_off))
    check("set_enabled(True): the switch is removed (files only switch off), enabled() -> default",
          ok2 and after_on == {"learned_fusion": {"arm_weights": [1, 1, 1]}} and tuple(en_on) == (True, "default"),
          (ok2, msg2, after_on))
    check("set_enabled(False, machine=True): <home>/auto-sync.json {enabled: false} written atomically; "
          "True removes it", ok3 and isinstance(mach, dict) and mach.get("enabled") is False
          and tuple(en_m) == (False, "machine") and ok4 and not (c.home / "auto-sync.json").exists()
          and tuple(en_m2) == (True, "default"), (ok3, msg3, mach, en_m, ok4, msg4))
    tmp_left = [p.name for p in list(c.home.glob(".*.tmp")) + list((c.root / ".deepmap").glob("*.tmp"))]
    check("...no temp file left behind", not tmp_left, tmp_left)
    c = W.case()
    _write(c.root / ".deepmap" / "instance.json", {"auto_sync": False, "keep": 1})
    c.install()
    with c.env():
        en_before = A.enabled(c.root)
        ok5, msg5 = A.set_enabled(c.root, True)
        after5 = _read(c.root / ".deepmap" / "instance.json")
        en_after = A.enabled(c.root)
    check("set_enabled(True) clears a bare instance.json auto_sync=false (it is the switch), keeping other keys",
          tuple(en_before) == (False, "instance") and ok5 and after5 == {"keep": 1}
          and tuple(en_after) == (True, "default"), (en_before, ok5, msg5, after5, en_after))


# =====================================================================================================================
# J — freshness and the real _git_head
# =====================================================================================================================
def gate_freshness(W: World, tree: Path) -> None:
    print("\n-- J. freshness(): the fingerprint and legacy table; the real _git_head passes GIT_OPTIONAL_LOCKS=0 --")
    A = W.A
    c = W.case()
    c.install()
    base = c.manifest_dict()
    legacy = {k: v for k, v in base.items() if k not in ("source_fingerprint", "build_recipe")}

    def fr(fp, manifest, dirty=False, content=None):
        c.sync.fp = dict({"kind": "git", "head": H1, "git_busy": None, "truncated": False}, **fp)
        c.sync.dirty, c.sync.content_hash = dirty, content
        r = A.freshness(c.root, manifest)
        return r["state"], r["why"]

    rows = [
        ("git busy", fr({"value": None, "git_busy": "merge"}, base), ("git-busy", "git-busy:merge")),
        ("truncated", fr({"value": None, "truncated": True}, base), ("unknown", "truncated")),
        ("git error", fr({"value": None}, base), ("unknown", "git-error")),
        ("embeddings pending", fr({"value": FP1}, dict(base, embeddings_pending=True)), ("stale", "embeddings-pending")),
        ("equal fingerprint", fr({"value": FP1}, base), ("fresh", None)),
        ("other fingerprint, HEAD moved", fr({"value": FP2, "head": H2}, base), ("stale", "head-moved")),
        ("other fingerprint, same HEAD", fr({"value": FP2, "head": H1}, base), ("stale", "sources-changed")),
        ("legacy: HEAD moved", fr({"value": FP2, "head": H2}, legacy), ("stale", "head-moved")),
        ("legacy: built_at_base_sha only", fr({"value": FP2, "head": H1},
                                              dict({k: v for k, v in legacy.items() if k != "built_at_sha"},
                                                   built_at_base_sha=H1)), ("fresh", None)),
        ("legacy: built dirty", fr({"value": FP2}, dict(legacy, dirty=True)), ("stale", "legacy-dirty")),
        ("legacy: dirty unknown", fr({"value": FP2}, {k: v for k, v in legacy.items() if k != "dirty"}),
         ("stale", "legacy-dirty")),
        ("legacy: dirty now", fr({"value": FP2}, legacy, dirty=True), ("stale", "sources-changed")),
        ("legacy: git status unknown now", fr({"value": FP2}, legacy, dirty=None), ("unknown", "git-error")),
        ("legacy: clean", fr({"value": FP2}, legacy), ("fresh", None)),
        ("legacy non-git: same content", fr({"value": FP2, "kind": "fs", "head": None},
                                            {"built_at_content_hash": "abc", "nodes": 3}, content="abc"),
         ("fresh", None)),
        ("legacy non-git: changed", fr({"value": FP2, "kind": "fs", "head": None},
                                       {"built_at_content_hash": "abc", "nodes": 3}, content="abd"),
         ("stale", "sources-changed")),
    ]
    bad = [(label, got, want) for label, got, want in rows if got != want]
    check(f"freshness(): all {len(rows)} rows (fingerprint first, legacy HEAD/dirty/content-hash fallback)",
          not bad, bad)

    git = shutil.which("git")
    if not git:
        skip("the real _git_head on a temp repository", "git is not on PATH")
        return
    repo = c.dir / "gitrepo"
    repo.mkdir()
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@e", GIT_CONFIG_NOSYSTEM="1")
    for args in (["init", "-q"], ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run([git, "-C", str(repo), *args], capture_output=True, env=genv, timeout=60)
    want = subprocess.run([git, "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          env=genv, timeout=60).stdout.strip()
    seen = []
    real_run = subprocess.run

    def rec(*a, **k):
        seen.append((list(a[0]) if a else None, (k.get("env") or {}).get("GIT_OPTIONAL_LOCKS")))
        return real_run(*a, **k)

    fresh_mod = _load(tree / "build" / "auto_sync.py", "auto_sync_githead")
    try:
        subprocess.run = rec
        with _env(GIT_OPTIONAL_LOCKS=None):
            got = fresh_mod._git_head(repo)
    finally:
        subprocess.run = real_run
    check("the real _git_head reads HEAD (40 hex) with GIT_OPTIONAL_LOCKS=0 in git's environment (H5)",
          got == want and len(want) == 40 and seen and all(v == "0" for _a, v in seen), (got, want, seen))


# =====================================================================================================================
# K — main(argv) in a child
# =====================================================================================================================
def gate_main(tree: Path, base: Path) -> None:
    print("\n-- K. main(argv): python build/auto_sync.py run|status in a child (ends before any sync) --")
    d = base / "main"
    root, home = d / "proj", d / "home"
    (root / ".git").mkdir(parents=True)
    home.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEEPMAP_") and k not in ("NEO4J_URI",)}
    env.update(DEEPMAP_HOME=str(home), CLAUDE_CODE_SESSION_ATTENDED="1", PYTHONIOENCODING="utf-8",
               HF_HUB_CACHE=str(d / "hf"))
    script = str(tree / "build" / "auto_sync.py")

    def child(args, **extra):
        e = dict(env, **extra)
        return subprocess.run([sys.executable, "-X", "utf8", script, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, env=e)

    p = child(["run", str(root)], DEEPMAP_AUTO_SYNC="0")
    check("`auto_sync.py run <root>` with DEEPMAP_AUTO_SYNC=0 -> exit 4, outcome=disabled, nothing recorded",
          p.returncode == 4 and "outcome=disabled" in p.stderr and not (root / ".deepmap" / "autosync.json").exists(),
          (p.returncode, p.stderr[-400:]))
    check("...its start line names the offline environment: HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "
          "GIT_OPTIONAL_LOCKS=0", re.search(r"\[deepmap auto-sync\] \S+ start trigger=session-start root=.+ pid=\d+ "
                                            r"python=\d+\.\d+ HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "
                                            r"GIT_OPTIONAL_LOCKS=0", p.stderr) is not None, p.stderr[-400:])
    p = child(["run", str(root)])
    check("`auto_sync.py run <root>` on a project never set up -> exit 1 (outcome=not-set-up)",
          p.returncode == 1 and "outcome=not-set-up" in p.stderr, (p.returncode, p.stderr[-400:]))
    p = child(["status", str(root)])
    check("`auto_sync.py status <root>` prints the status line, exit 0",
          p.returncode == 0 and "auto-sync: on \u2014 no background refresh has run yet" in p.stdout,
          (p.returncode, p.stdout[-300:], p.stderr[-300:]))
    p = child(["run", str(root), "--trigger", "commit"])
    check("an unknown trigger is a usage error (exit 2)", p.returncode == 2, (p.returncode, p.stderr[-200:]))
    p = child(["precheck", str(root), "--announce"])
    try:
        got = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        got = None
    check("`auto_sync.py precheck <root> --announce` prints precheck(announce=True) as JSON (not-set-up here), exit 0",
          p.returncode == 0 and isinstance(got, dict) and got.get("code") == "not-set-up" and got.get("ok") is False,
          (p.returncode, p.stdout[-300:], p.stderr[-300:]))
    # a non-git project whose session-start refresh started a minute ago: only --announce applies the cooldown
    root2 = d / "proj2"
    _write(root2 / ".deepmap" / "graph_build.json", {"nodes": 5, "verdict": "READY", "sync_wall_s": 30.0,
                                                    "built_at_content_hash": "0" * 16})
    _write(root2 / ".deepmap" / "autosync.json", {"schema": 1, "current": None,
                                                  "last_session_start": {"started_epoch": time.time() - 60,
                                                                         "head": None}})
    got = {}
    for flag in ((), ("--announce",)):
        p = child(["precheck", str(root2), *flag])
        try:
            got[flag] = json.loads((p.stdout or "").strip().splitlines()[-1])
        except Exception:
            got[flag] = {"rc": p.returncode, "out": p.stdout[-200:], "err": p.stderr[-300:]}
    check("...--announce reaches precheck: a non-git project's refresh started 60 s ago -> 'cooldown' with it, ok "
          "without it (the heal hook's spawn question)", got[("--announce",)].get("code") == "cooldown"
          and got[()].get("ok") is True, got)


# =====================================================================================================================
# N — the real SessionStart notify hook honours a skip that repeats
# =====================================================================================================================
AUTO_MARK = "DeepMap is refreshing it in the background now"
STALE_MARK = "the graph may be stale; suggest /deep-map:refresh before relying on impact/blast-radius answers"


def gate_notify_integration(tree: Path, base: Path) -> None:
    print("\n-- N. the real SessionStart notify hook: a skip that repeats is never announced as 'refreshing now' --")
    git = shutil.which("git")
    hook = tree / "hooks" / "deepmap_sessionstart_notify.py"
    if not git or not hook.is_file():
        skip("N. the notify hook after a repeating skip", "git is not on PATH" if not git else f"no {hook}")
        return
    d = base / "notify"
    root, home = d / "proj", d / "home"
    (root / "pkg").mkdir(parents=True)
    home.mkdir(parents=True)
    # A plugin tree of the tree's own hooks/ and build/ — except deepmap_cli.py: the hooks turn auto-sync on only when
    # the plugin's CLI carries the `sync --unattended` contract (deepmap_hook_state.cli_takes_unattended), which is
    # the coordinator's wiring; the stub carries exactly that contract (the notify never runs the CLI).
    plugin = d / "plugin"
    for sub in ("build", "hooks"):      # the modules only (a checkout's build/ also holds sync artifacts)
        (plugin / sub).mkdir(parents=True)
        for src in sorted((tree / sub).glob("*.py")):
            if src.name != "deepmap_cli.py":
                shutil.copy2(str(src), str(plugin / sub / src.name))
    (plugin / "build" / "deepmap_cli.py").write_text(
        "# test stub: the CLI contract the hooks check (Appendix C3/C4)\n"
        "def cmd_sync(args):\n"
        "    return _load(\"auto_sync\").run(args.target, trigger=args.unattended, session=args.session)\n"
        "def _args(p):\n"
        "    p.add_argument(\"--unattended\", choices=(\"session-start\",), default=None)\n"
        "    p.add_argument(\"--session\", default=None)\n", encoding="utf-8")
    hook = plugin / "hooks" / "deepmap_sessionstart_notify.py"
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@e", GIT_CONFIG_NOSYSTEM="1")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"]):
        subprocess.run([git, "-C", str(root), *args], capture_output=True, env=genv, timeout=60)
    dm = root / ".deepmap"
    dm.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DEEPMAP_", "CLAUDE_")) and k not in ("NEO4J_URI", "HF_HUB_OFFLINE")}
    env.update(DEEPMAP_HOME=str(home), CLAUDE_CODE_SESSION_ATTENDED="1", PYTHONIOENCODING="utf-8",
               HF_HUB_CACHE=str(d / "hf"))
    now = time.time()

    def announce(sid, outcome, *, rewritten=False):
        # an upgraded project: a legacy manifest (no sync_wall_s) built at a commit that is not HEAD
        _write(dm / "graph_build.json", {"verdict": "READY", "nodes": 50, "built_at_sha": "0" * 40,
                                         "extractor": "graphify", "graph_engines": ["graphify"],
                                         "built_at_utc": "2026-10-01T00:00:00Z"})
        t_manifest = now + 120 if rewritten else now - 600
        os.utime(dm / "graph_build.json", (t_manifest, t_manifest))
        _write(dm / "autosync.json", {"schema": 1, "current": None, "consecutive_failures": 0,
                                      "last_run": {"id": f"run-{sid}", "outcome": outcome, "rc": 4,
                                                   "finished_epoch": now - 60, "reason": "x", "suggest": "refresh",
                                                   "why_code": outcome.split(":", 1)[-1]}})
        payload = {"session_id": sid, "source": "startup", "cwd": str(root), "hook_event_name": "SessionStart"}
        try:
            p = subprocess.run([sys.executable, "-X", "utf8", str(hook)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                               env=env, cwd=str(root))
        except subprocess.TimeoutExpired:
            return "<the notify hook did not finish within 180 s>"
        try:
            return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        except Exception:
            return f"<no envelope: rc {p.returncode} {p.stdout[-200:]!r} {p.stderr[-300:]!r}>"

    for sid, outcome in (("N-1", "skipped:needs-timing"), ("N-2", "skipped:no-own-instance"),
                         ("N-3", "skipped:recipe:python")):
        text = announce(sid, outcome)
        check(f"notify after a recorded {outcome}: today's 'suggest /deep-map:refresh', never 'refreshing it in the "
              "background now'", STALE_MARK in text and AUTO_MARK not in text, text[-400:])
    text = announce("N-4", "skipped:low-memory")
    check("notify after a transient skip (low-memory): the background refresh is announced (AUTO text)",
          AUTO_MARK in text and STALE_MARK not in text, text[-400:])
    text = announce("N-5", "skipped:needs-timing", rewritten=True)
    check("notify after a needs-timing skip whose manifest was rewritten since: announced again (AUTO text)",
          AUTO_MARK in text and STALE_MARK not in text, text[-400:])


# =====================================================================================================================
# L — the benchmark opt-outs (M12)
# =====================================================================================================================
def gate_benchmark(tree: Path) -> None:
    print("\n-- L. the benchmark: both arms and the clone builder run with DeepMap's background refresh off (M12) --")
    try:
        B = _load(tree / "benchmark" / "run_benchmark.py", "run_benchmark_autosync")
        child = B.claude_child_env({"DEEPMAP_AUTO_SYNC": "1", "DEEPMAP_FRESHNESS_NOTE": "1", "KEEP": "x"})
        check("run_benchmark.CLAUDE_CHILD_HOOK_OPTOUTS sets DEEPMAP_AUTO_SYNC=0 and DEEPMAP_FRESHNESS_NOTE=0",
              B.CLAUDE_CHILD_HOOK_OPTOUTS.get("DEEPMAP_AUTO_SYNC") == "0"
              and B.CLAUDE_CHILD_HOOK_OPTOUTS.get("DEEPMAP_FRESHNESS_NOTE") == "0", B.CLAUDE_CHILD_HOOK_OPTOUTS)
        check("claude_child_env() (both arms, the probe, the judge) overrides an operator's 1 with 0, keeps the rest",
              child.get("DEEPMAP_AUTO_SYNC") == "0" and child.get("DEEPMAP_FRESHNESS_NOTE") == "0"
              and child.get("KEEP") == "x" and child.get("DEEPMAP_HOOKS") == "0", child)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        check("benchmark/run_benchmark.py loads", False, f"{type(exc).__name__}: {exc}")
    try:
        CG = _load(tree / "benchmark" / "clone_graph.py", "clone_graph_autosync")
        e1 = CG.child_env({"DEEPMAP_AUTO_SYNC": "1", "NEO4J_URI": "bolt://x", "KEEP": "y"})
        e2 = CG.child_env({})
        check("clone_graph.child_env() FORCES DEEPMAP_AUTO_SYNC=0 (an operator's 1 too), still strips the routing "
              "env and keeps the rest", e1.get("DEEPMAP_AUTO_SYNC") == "0" and e2.get("DEEPMAP_AUTO_SYNC") == "0"
              and "NEO4J_URI" not in e1 and e1.get("KEEP") == "y", (e1, e2))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        check("benchmark/clone_graph.py loads", False, f"{type(exc).__name__}: {exc}")


# =====================================================================================================================
# M — post-merge: the real sync_status / sync surfaces
# =====================================================================================================================
def _sync_ast_surface(tree: Path) -> dict:
    out = {}
    try:
        mod = ast.parse((tree / "build" / "sync.py").read_text(encoding="utf-8"))
    except Exception:
        return out
    for node in mod.body:
        if isinstance(node, ast.FunctionDef):
            args = node.args
            out[node.name] = [a.arg for a in args.args + args.kwonlyargs]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, "<global>")
    return out


def post_merge_ready(tree: Path) -> bool:
    surf = _sync_ast_surface(tree)
    return "unattended" in (surf.get("sync") or []) and (tree / "build" / "sync_status.py").is_file()


def gate_real_surfaces(tree: Path, ST, A=None) -> None:
    print("\n-- M. post-merge: the real sync_status / sync expose exactly what the runner calls --")
    need = {"try_lock": ["path"], "release_lock": ["handle"], "atomic_write_json": ["path", "obj"],
            "read_running": ["root"], "read_last": ["root"], "live_preempts": ["root", "now"],
            "model_cache_state": ["env"], "recipe_conflicts": ["recorded", "env"], "replay_env": ["recorded", "env"],
            "process_identity": [], "holder_live": ["rec"],
            "make_build_recipe": ["engine", "extraction", "route_source", "neo4j_uri", "code_version", "env"]}
    bad = {}
    for name, params in need.items():
        f = getattr(ST, name, None)
        try:
            got = list(inspect.signature(f).parameters) if f else None
        except (TypeError, ValueError):
            got = None
        if got is None or got[:len(params)] != params:
            bad[name] = got
    check("sync_status exposes the §2.3 functions the runner calls, with their parameters", not bad, bad)
    try:
        ctl_params = list(inspect.signature(ST.UnattendedControl.__init__).parameters)
    except Exception:
        ctl_params = []
    want_ctl = ["self", "root", "trigger", "session", "prune_policy", "pre_write", "before_embed", "on_hard_exit"]
    check("sync_status.UnattendedControl(root, *, trigger, session, prune_policy, pre_write, before_embed, "
          "on_hard_exit, ...) with end() / yielded()", ctl_params[:len(want_ctl)] == want_ctl
          and callable(getattr(ST.UnattendedControl, "end", None))
          and callable(getattr(ST.UnattendedControl, "yielded", None)), ctl_params)
    surf = _sync_ast_surface(tree)
    want_sync = {"sync": ["prune_policy", "unattended"], "_request_rerun": ["prune_policy", "origin", "trigger"],
                 "source_fingerprint": ["root"], "git_busy": ["root"], "_rerun_request_path": ["root"],
                 "_peek_rerun_request": ["root"], "_is_user_request": ["request"],
                 "_instance_lock_key": ["uri"], "_git_worktree_dirty": ["root"], "_fs_content_hash": ["root"]}
    miss = {n: surf.get(n) for n, ps in want_sync.items() if not set(ps) <= set(surf.get(n) or [])}
    check("sync.py defines what the runner calls (sync(..., prune_policy, unattended), _request_rerun(..., "
          "prune_policy, origin, trigger), source_fingerprint, git_busy, ...)", not miss, miss)
    check("sync.py has the module global last_outcome", surf.get("last_outcome") == "<global>",
          surf.get("last_outcome"))
    # the fake's _is_user_request mirrors sync's rule: the real function, evaluated from its AST (no sync import)
    real = None
    try:
        mod = ast.parse((tree / "build" / "sync.py").read_text(encoding="utf-8"))
        node = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "_is_user_request")
        ns: dict = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "sync._is_user_request", "exec"), ns)  # noqa: S102
        real = ns["_is_user_request"]
    except Exception as exc:  # noqa: BLE001
        real = exc
    probes = (None, {}, {"origin": "manual"}, {"origin": "unattended"}, {"cleanup_stale": True}, {"origin": None},
              "x", ["origin"])
    diff = ([p for p in probes if bool(real(p)) != bool(FakeSync._is_user_request(p))] if callable(real)
            else [repr(real)])
    check("the gates' FakeSync._is_user_request agrees with sync._is_user_request (a user's request: origin manual or "
          "none; never an unattended one)", not diff, diff)
    rerun_name = None
    try:
        for node in ast.parse((tree / "build" / "sync.py").read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and any(
                    isinstance(t, ast.Name) and t.id == "_RERUN_REQUEST" for t in node.targets):
                rerun_name = node.value.value
    except Exception:  # noqa: BLE001
        rerun_name = None
    check("auto_sync.RERUN_FILE (the file precheck looks for a still-queued user's request) == sync._RERUN_REQUEST",
          A is not None and rerun_name is not None and getattr(A, "RERUN_FILE", None) == rerun_name,
          (getattr(A, "RERUN_FILE", None), rerun_name))


# =====================================================================================================================
def _sections(W: World, *, full: bool) -> None:
    funcs = [gate_precheck, gate_decisions, gate_single_flight, gate_backoff, gate_budget_walls, gate_precheck_announce,
             gate_legacy_recipe, gate_user_request_early]
    if full:
        funcs = [gate_enabled_matrix] + funcs[:2] + [gate_sync_call] + funcs[2:] + [gate_status_and_switches]
    for fn in funcs:
        try:
            fn(W)
        except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
            traceback.print_exc()
            check(f"{fn.__name__} [{W.label}] ran without raising", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    print("=" * 76)
    print(f"DeepMap auto-sync S2 — the unattended runner (build/auto_sync.py) (root: {tree})")
    print("=" * 76)
    sys.path.insert(0, str(tree / "build"))
    base = Path(tempfile.mkdtemp(prefix="dm_autosync_runner_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_") or k in (
        "NEO4J_URI", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_ENTRYPOINT", "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE", "GIT_OPTIONAL_LOCKS")}
    scrub.update(DEEPMAP_HOME=str(base / "home0"), HF_HUB_CACHE=str(base / "hf0"))
    try:
        with _env(**scrub):
            gate_benchmark(tree)
            path = tree / "build" / "auto_sync.py"
            A = None
            try:
                A = _load(path, "auto_sync")
            except Exception as exc:  # noqa: BLE001
                for section in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"):
                    check(f"section {section}: build/auto_sync.py exists and imports", False,
                          f"{type(exc).__name__}: {exc}")
            if A is not None:
                saved = _orig_seams(A)
                try:
                    gate_import_and_surface(tree, A)
                    W = World(tree, A, base, FakeStatus(), "fake")
                    _sections(W, full=True)
                    _restore_seams(A, saved)
                    gate_priority(tree, W)
                    _restore_seams(A, saved)
                    gate_freshness(W, tree)
                    _restore_seams(A, saved)
                    gate_main(tree, base)
                    gate_notify_integration(tree, base)
                    post = "--post-merge" in sys.argv[1:] or post_merge_ready(tree)
                    if post:
                        try:
                            ST = _load(tree / "build" / "sync_status.py", "sync_status")
                            sys.modules["sync_status"] = ST
                        except Exception as exc:  # noqa: BLE001
                            ST = None
                            check("post-merge: build/sync_status.py imports", False, f"{type(exc).__name__}: {exc}")
                        if ST is not None:
                            gate_real_surfaces(tree, ST, A)
                            if ST.process_identity().get("pid_created") is None:
                                skip("post-merge sections C/D/F/G/O/P/Q/R against the real sync_status",
                                     "this platform gives no process start stamp (holders read as unknown)")
                            else:
                                _restore_seams(A, saved)
                                print("\n== re-running C, D, F, G, O, P, Q, R against the REAL sync_status ==")
                                _sections(World(tree, A, base, ST, "real"), full=False)
                    else:
                        skip("M. post-merge: the real sync_status / sync surfaces, and C/D/F/G against the real "
                             "sync_status", "the tree's sync.sync does not take `unattended` yet (S1 not merged) — "
                             "re-run after the merge (automatic then, or pass --post-merge)")
                finally:
                    _restore_seams(A, saved)
            home = base / "home0"
            check("hermetic: no registry was written under the temp DEEPMAP_HOME",
                  not (home / "registry.json").exists(), sorted(p.name for p in home.iterdir()) if home.is_dir() else [])
    finally:
        _rmtree(base)                                   # exactly the temp dir this gate created
    check("hermetic: the gate's own temp tree is gone (git's read-only object files included)", not base.exists(),
          str(base))
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 76)
    if SKIPS:
        print(f"({len(SKIPS)} skipped: {', '.join(SKIPS)[:300]})")
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
