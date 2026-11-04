#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap auto-sync — the unattended background refresh of a project's graph (the SessionStart catch-up).

When an interactive Claude Code session starts, the SessionStart heal hook spawns (after heal, below-normal
priority, no window)

    deepmap_cli.py sync <root> --unattended=session-start [--session <id>]

and the CLI hands the run to `run()` here BEFORE it imports sync, so the offline environment is in place when sync
and the embedding stack load. The run is a short state machine (R1-R17 below). Most runs end in a second or two
("fresh": the graph's sources did not change); a stale graph is re-synced with an EVIDENCE-ONLY prune, and any sync
the user starts (/deep-map:refresh, /deep-map:setup, deepmap enrich) preempts it and runs in its own process, with
its own environment, flags and verdict (sync_status.UnattendedControl).

  R1  environment   HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE = 1, GIT_OPTIONAL_LOCKS = 0, PYTHONUTF8 = 1; own CPU priority
                    (and, on Linux, I/O priority) lowered once (_lower_priority); DEEPMAP_EMBED_THREADS never touched
                    (embedding determinism).
  R2  enabled       enabled(root, trigger); DEEPMAP_HOOKS off ends a session-start run.        silent, rc 4
  R3  marker        no .deepmap/graph_build.json -> rc 1; a proven-empty manifest -> rc 4.  silent
  R4  cooldown      a session-start run started < COOLDOWN_S ago at the same HEAD.           silent, rc 4
  R5  recipe env    the manifest's build_recipe.env is replayed into the environment (an explicit conflicting
                    value is remembered and ends the run at R8) — before sync is imported. A manifest that recorded
                    no settings (written before build_recipe existed) is refreshed with the graph-shaping settings
                    of the environment the runner was started from (the session's): never a conflict, never stripped.
  R6  single-flight a non-blocking OS lock on .deepmap/autosync.lock; a loser queues a re-run through sync's own
                    queue when the sources changed ("coalesced", rc 0), then tries the lock again and takes over
                    when the holder released it or already made its one look at the queue — no trigger is lost.
  R7  start record  autosync.json `current` + `last_session_start`; port_registry.touch_last_used.
  R8  eligibility   own route (an operator NEO4J_URI on another machine only with DEEPMAP_AUTO_SYNC=1: no egress),
                    no setup blocker, packages, pause / backoff, the build recipe (settings, DeepMap version, Python
                    X.Y, DEEPMAP_HOME, route — the recorded ENDPOINT, however it was named; an operator NEO4J_URI with
                    no route recorded is never followed —, embedding model, engine, graphify version).
  R9  freshness     freshness(): the source fingerprint sync recorded vs now (git-busy / unknown skip); unchanged
                    sources but a graph due for a graph revision (sync_status.graph_rev_due: a release changed what
                    DeepMap builds from the same sources, and its scope covers this graph) is stale "graph-rev": rebuilt
                    once, the rebuild records the current revision (last_run.graph_rev names it; autosync.json
                    graph_rev_rebuilt keeps it). ONCE per revision: a graph due again for a revision already rebuilt
                    for (an older DeepMap rewrote it since, e.g. an older install's git post-commit hook) is a recorded
                    skip, graph-rev-again, never a rebuild on every commit.
  R10 budget        the wall time of the last sync that did NOT run in the background (_limit_wall: a below-normal
                    pass on a loaded machine never raises it) vs DEEPMAP_AUTO_SYNC_MAX_S; without one, a project with
                    more than LEGACY_FILE_LIMIT engine source files skips ("needs-timing").
  R11 database      wait (<= HEAL_WAIT_S) for this session's heal verdict or a listening bolt port; never starts
                    Neo4j.
  R12 machine slot  one unattended sync per machine (<DEEPMAP_HOME>/run/autosync-slot.lock, <= SLOT_WAIT_S).
  R13 memory/model  available memory >= 25 % of RAM, at least 3 GiB, at most 6 GiB (+ 1.5 GiB when only embeddings
                    postponed for low memory are due); the embedding model complete in the local cache. Available
                    memory is read on Windows, Linux and macOS (_memory: vm_stat on a Mac); unreadable -> no floor.
  R14 covered       a live sync (manual or unattended) already extracting these exact sources -> "covered".
                    (Before R10, a stale run ends skipped:user-request when a user's queued re-run waits and no sync
                    holds the lock: sync would never run it, so the run does not wait for the database or the slot.)
  R15 sync          sync.sync(root, cleanup_stale=False, prune_policy="evidence"|"none", unattended=<control>)
                    with the engine budget capped (engine_budget); its last_outcome maps to the outcome (§2.9.1;
                    user-request-pending -> skipped:user-request, an "unowned" refusal -> skipped:unowned, no backoff).
  R16 no lost trigger  a re-run request still queued after synced / fresh / covered gets ONE more iteration — never
                    a user's request (origin manual or none): only a sync the user starts runs it.
  R17 record        autosync.json `last_run` (+ backoff / pause), `current` cleared, both locks released.

Records (all JSON objects, UTF-8, "schema": 1, written through sync_status.atomic_write_json):
  <root>/.deepmap/autosync.json — single writer: the runner holding <root>/.deepmap/autosync.lock. Read by
  precheck, the hooks and status_line. Besides `current` / `last_run` / the backoff fields it keeps
  `background_manifest` (the identity of the manifest the last background pass wrote) and `attended_wall` (the
  sync_wall_s of the manifest that pass replaced) — what _limit_wall reads. <DEEPMAP_HOME>/auto-sync.json
  {"enabled": false} switches it off for the machine; <root>/.deepmap/instance.json {"auto_sync": {"enabled": false}}
  for the project (files only switch OFF, and fail closed: 0, an OFF word, a bare false, or a value / machine file
  DeepMap does not understand also mean off).

Exit codes (meanings unchanged): 0 = the graph is or will be current (fresh, covered, coalesced, synced, queued,
preempted); 1 = refused or failed; 3 = the database did not come up / a database error; 4 = not refreshed (the
previous graph is kept and may be stale: every skip, not-pushed).

Import rule: stdlib only at module level. sync_status, sync (only after R1/R5), port_registry, graphify_extract,
instance_state and hooks/deepmap_hook_state.py are loaded lazily, so the hooks can import this module cheaply and
importing it never imports sync / neo4j / torch / graphify_extract. Never calls deps_bootstrap (the CLI's __main__
already re-ran under DeepMap's own environment).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
_HOOKS = _BUILD.parent / "hooks"

TRIGGERS = ("session-start",)
ENV_AUTO_SYNC = "DEEPMAP_AUTO_SYNC"
ENV_MAX_S = "DEEPMAP_AUTO_SYNC_MAX_S"
ENV_PRUNE = "DEEPMAP_AUTO_SYNC_PRUNE"
ENV_HOOKS = "DEEPMAP_HOOKS"
ENV_HEAL = "DEEPMAP_HEAL"
ENV_HOME = "DEEPMAP_HOME"
MAX_S_DEFAULT = 1200
COOLDOWN_S = 900
HEAL_WAIT_S = 180
SLOT_WAIT_S = 1800
LEGACY_FILE_LIMIT = 1500
BACKOFF_S = 6 * 3600
PAUSE_AFTER_FAILURES = 3
MEM_FLOOR_MIN = 3 * 1024 ** 3
MEM_FLOOR_FRACTION = 0.25
# The floor is capped: it leaves room for the user's other work, and a refresh itself needs about 1.5 GB — 25 % of a
# 64 GB machine (16 GB free) would make a large or busy workstation skip almost every refresh.
MEM_FLOOR_MAX = 6 * 1024 ** 3
# A run whose ONLY reason is embeddings an earlier refresh postponed for low memory (sources unchanged) needs this much
# above the floor, so it does not re-sync the whole graph only to postpone them again under the same pressure.
EMBED_HEADROOM = 1536 * 1024 ** 2
STATE_FILE = "autosync.json"
GRAPH_REV_REBUILT = "graph_rev_rebuilt"   # autosync.json: {"rev", "finished_epoch"} of the last one-time rebuild
LOCK_FILE = "autosync.lock"
RERUN_FILE = "sync.rerun"                 # == sync._RERUN_REQUEST (the re-run queue, under <root>/.deepmap/)
SLOT_FILE = "autosync-slot.lock"          # under <DEEPMAP_HOME>/run/
MACHINE_SWITCH = "auto-sync.json"         # directly under <DEEPMAP_HOME>
# CLAUDE_CODE_ENTRYPOINT values of a session nobody is watching (the fallback when Claude Code does not set
# CLAUDE_CODE_SESSION_ATTENDED — older versions; spec Appendix B)
UNATTENDED_ENTRYPOINTS = ("sdk-cli", "sdk-ts", "sdk-py", "mcp", "claude-code-github-action",
                          "remote_trigger", "remote_cowork_trigger")
SCHEMA = 1

HEAL_POLL_S = 2.0            # R11: how often the heal verdict / the bolt port is looked at
BOLT_CONFIRM_S = 5.0         # R11: two listening probes at least this far apart mean "up"
SLOT_POLL_S = 5.0            # R12
RECHECK_AFTER_S = 30.0       # R14: re-run the freshness check when it is older than this
EMBED_MEM_POLL_S = 10.0      # before_embed: the memory floor is looked at this often ...
EMBED_MEM_WAIT_S = 120.0     # ... for at most this long before the embeddings are postponed
VM_STAT = "/usr/bin/vm_stat"  # macOS: where _memory reads the available memory (an absolute path, never one on PATH)
VM_STAT_TIMEOUT_S = 5.0      # ... and how long it may take (a snapshot takes milliseconds); the child is then ended
TAKEOVER_WAIT_S = 30.0       # R6c: how long a coalescing runner waits for a holder that already made its last look
TAKEOVER_POLL_S = 0.5
REASON_MAX = 300
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
POSIX_NICE = 10
# Linux I/O priority (ioprio_set): best-effort class, its lowest level (`ionice -c2 -n7`) — never the idle class, which
# can starve a refresh for as long as anything else reads the disk. Only the architectures whose syscall number is
# certain; anywhere else the I/O priority stays the one Linux derives from the nice value.
_IOPRIO_SET_NR = {"x86_64": 251, "amd64": 251, "aarch64": 30, "arm64": 30, "riscv64": 30}
IOPRIO_WHO_PROCESS = 1
IOPRIO_CLASS_BE = 2
IOPRIO_LOWEST = 7

_ON = frozenset({"1", "true", "yes", "on"})
_OFF = frozenset({"0", "false", "no", "off"})
_MANAGED_ROUTES = ("registry", "instance-conf")
_OWN_ROUTES = _MANAGED_ROUTES + ("env",)
_PACKAGES = ("neo4j", "sentence_transformers")   # what a refresh imports: the driver and the embedding stack
_HOLDER_ORIGINS = ("manual", "unattended")
_FOREIGN_KINDS = ("foreign-owner", "legacy-owner", "base-claim")

# The words a runner state / sync phase is said with (== sync_status.PHASE_WORDS, used when it cannot be loaded).
_PHASE_WORDS = {
    "start": "checking what changed", "checking": "checking what changed",
    "waiting-db": "waiting for the database", "waiting-slot": "waiting for another project's refresh to finish",
    "extract": "reading the code", "write": "writing the graph", "embed": "computing embeddings",
    "manifest": "finishing", "recording": "finishing", "syncing": "syncing",
}

# §2.10.4 — what the agent is told ({why}) and what it suggests, per runner outcome code.
_WHY = {
    "recipe:older-code": ("it was built by DeepMap {a}, newer than the {b} this session runs (a background refresh "
                          "never rebuilds a graph with older DeepMap code)", "refresh"),
    "recipe:settings": ("the graph-shaping settings differ from the ones it was built with ({name}={a} at build "
                        "time, {b} now)", "refresh"),
    "recipe:python": ("it was built under Python {a}, and the background refresh would run under Python {b} (the "
                      "Python version can change how Python code is read)", "refresh"),
    "recipe:home": ("it was built with DeepMap's home at {a}, and the background refresh sees {b}", "refresh"),
    "recipe:route": ("it was built in the database at {a}, and the background refresh would write to {b}",
                     "refresh"),
    "recipe:route-unrecorded": ("its build did not record which database it lives in, and the background refresh "
                                "would follow NEO4J_URI to {b} (it never writes to a database the build did not "
                                "record; one /deep-map:refresh records it)", "refresh"),
    "recipe:model": ("it was embedded with model files {a}, and the cached model files are now {b}", "refresh"),
    "recipe:engine": ("its extractor would change ({a} → {b})", "refresh"),
    "recipe:engine-version": ("the installed graphify ({b}) is older than the {a} that built it, or outside "
                              "DeepMap's supported range", "setup"),
    "too-slow": ("its last sync took {took} (background refreshes not counted; the background limit is {max}; "
                 "DEEPMAP_AUTO_SYNC_MAX_S raises it, 0 removes it)", "refresh"),
    "needs-timing": ("this project has more than 1,500 source files and its sync time is not recorded yet — "
                     "DeepMap refreshes such a project in the background only after one /deep-map:refresh has "
                     "recorded how long a sync takes", "refresh"),
    "paused:failures": ("the last {n} background refreshes did not complete (latest: {reason}) — automatic "
                        "refresh is paused until a refresh succeeds", "refresh"),
    "paused:engine-timeout": ("the background refresh ran out of its time budget ({t}) — automatic refresh is "
                              "paused until a refresh succeeds", "refresh"),
    "backoff": ("the last background refresh failed {ago} ago ({reason}); DeepMap retries it automatically after "
                "{until} UTC", "refresh"),
    "git-busy": ("a git {op} is in progress in this repository (DeepMap never refreshes in the middle of one)", None),
    "unknown": ("git did not answer in time, so DeepMap could not tell whether files changed", "refresh"),
    "unknown:truncated": ("too many source files changed to check (more than 20,000 would have to be read), so "
                          "DeepMap could not tell whether the graph is current", "refresh"),
    "machine-busy": (f"another project's background refresh kept this machine's refresh slot for "
                     f"{SLOT_WAIT_S // 60} minutes", "refresh"),
    "low-memory": ("only {avail} of memory was available, and a background refresh starts only when at least {floor} "
                   "is free (it needs about 1.5 GB itself and leaves the rest for your other work)", "refresh"),
    "low-memory:embed": ("the graph only lacks the embeddings an earlier background refresh postponed for low memory, "
                         "and only {avail} of memory was available — DeepMap computes them once at least {need} is "
                         "free, so they are not postponed again", "refresh"),
    "remote-database": ("this project's database is not on this machine ({where}), and a background refresh never "
                        "sends code off this machine on its own (DEEPMAP_AUTO_SYNC=1 allows it)", "refresh"),
    "model-missing": ("the embedding model is not in the local cache (a background refresh never downloads)",
                      "setup"),
    "packages-missing": ("DeepMap's Python packages are missing for the interpreter it would run under", "setup"),
    "no-own-instance": ("this project has no DeepMap database of its own on this machine (a background refresh never "
                        "writes to a database it cannot prove is this project's)", "setup"),
    "setup-blocked": ("this project's DeepMap setup is blocked ({detail})", "setup"),
    "busy-enrich": ("a deepmap enrich run holds this project's sync lock", "refresh"),
    "graph-rev-again": ("{writer} rewrote this graph after the background refresh's one-time rebuild for graph "
                        "revision {rev}, so that refresh does not rebuild it again (often an older install's git "
                        "post-commit hook still runs that version: /deep-map:setup names a stale one)", "setup"),
    "user-request": ("a re-run that a sync the user started queued is waiting, and a background refresh never runs one "
                     "(it carries that sync's own settings and prune)", "refresh"),
    "unowned": ("this project's database holds a graph with no recorded owner, and a background refresh never adopts a "
                "database it cannot prove is this project's (if it is this project's graph, /deep-map:refresh adopts "
                "it; if NEO4J_URI points at another database, unset it)", "refresh"),
    "unreachable": (f"this project's database did not come up within {HEAL_WAIT_S // 60} minutes of the session "
                    f"start", "doctor"),
    "unreachable:heal": ("this project's database did not come up at the session start (DeepMap's heal reported: "
                         "{detail})", "doctor"),
    "unreachable:no-heal": ("this project's database was not running at the session start, and DeepMap's heal is "
                            "switched off (DEEPMAP_HEAL), so nothing started it", "doctor"),
    "unreachable:during": ("the database failed while the background refresh ran ({detail}); the graph may hold part "
                           "of that refresh", "doctor"),
}
_GIT_OPS = {"rebase": "rebase", "merge": "merge", "cherry-pick": "cherry-pick", "revert": "revert",
            "bisect": "bisect", "sequence": "sequencer operation (a multi-commit cherry-pick or revert)",
            "unmerged": "merge-conflict resolution (the index has unmerged files)"}
_WARN_PREFIXES = ("failed", "not-pushed", "unreachable")
# Recorded skips the next session-start run would end with again under the same inputs (until a sync rewrites the
# manifest: a /deep-map:refresh or /deep-map:setup — the remedy each one suggests). While one stands, precheck with a
# HEAD or announce=True (the announcement) says so, so the session is told today's "suggest /deep-map:refresh", not
# "refreshing now". user-request also ends once its request left the queue (.deepmap/sync.rerun gone: a sync the user
# started claimed it). Transient ones (low-memory, machine-busy, git-busy, unknown, busy-enrich) are not here;
# settings, older code, too slow, pause, backoff and a remote database are checked by precheck directly.
# graph-rev-again is not here either: it holds only while the sources are unchanged, which precheck cannot see.
_REPEATING_SKIPS = ("needs-timing", "no-own-instance", "packages-missing", "model-missing", "setup-blocked",
                    "foreign-owner", "unowned", "user-request", "recipe:python", "recipe:home", "recipe:route",
                    "recipe:model", "recipe:engine", "recipe:engine-version")
# precheck codes status_line does not report as a block: not a refusal of the next session start's refresh (a
# cooldown only spaces two starts), reported by status_line itself (paused, the switches), or unknown (error)
_STATUS_NOT_BLOCKS = (None, "cooldown", "error", "paused", "hooks-off", "compact", "disabled", "unattended-session")
# what tells one written manifest from another (_manifest_identity)
_IDENTITY_KEYS = ("built_at_utc", "edits_seen_through_epoch", "sync_wall_s", "source_fingerprint")
CAPTURE_SLACK_S = 1.0        # _note_written: clock slack around "captured inside this run's sync call"


# ---- small helpers ---------------------------------------------------------------------------------------------------
def _utc(epoch=None) -> str:
    t = time.time() if epoch is None else float(epoch)
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_fmt(epoch, fmt: str) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime(fmt)
    except Exception:  # noqa: BLE001
        return "?"


def _num(value):
    """A finite float from `value`, or None (bools, garbage and NaN are not numbers here). Never raises."""
    try:
        if value is None or isinstance(value, bool):
            return None
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _word(env, name) -> str:
    try:
        return str(env.get(name, "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _env_on(env, name) -> bool:
    return _word(env, name) in _ON


def _env_off(env, name) -> bool:
    return _word(env, name) in _OFF


def _clip(text, n: int = REASON_MAX):
    if text is None:
        return None
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _python_xy() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _deepmap_dir(root) -> Path:
    return Path(root) / ".deepmap"


def _read_json(path):
    """The JSON object at `path`, or None (absent, unreadable, not an object). Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _read_manifest(root):
    return _read_json(_deepmap_dir(root) / "graph_build.json")


def _recipe(manifest) -> dict:
    r = manifest.get("build_recipe") if isinstance(manifest, dict) else None
    return r if isinstance(r, dict) else {}


def _recipe_env(manifest) -> dict:
    e = _recipe(manifest).get("env")
    return e if isinstance(e, dict) else {}


def _prior_engines(manifest) -> list:
    """The engines whose nodes the graph holds per `manifest` (sync._read_prior_engines's rule: `graph_engines`, else
    the legacy `extractor`). Never raises."""
    try:
        engines = manifest.get("graph_engines")
        if isinstance(engines, list):
            return sorted({str(x) for x in engines if x})
        ex = manifest.get("extractor")
        return [str(ex)] if ex else []
    except Exception:  # noqa: BLE001
        return []


def _log(msg: str) -> None:
    """One line on stderr (the heal hook sends it to .deepmap/autosync.log). Never raises."""
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        try:
            sys.stderr.write(msg.encode("ascii", "replace").decode("ascii") + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass


def human_duration(seconds) -> str:
    """'N s' under 90 s, 'N min' (rounded) under 90 min, else 'H h M min' (== sync_status.duration_words). Never
    raises."""
    s = _num(seconds)
    if s is None:
        return "an unknown time"
    s = max(0.0, s)
    if s < 90:
        return f"{int(round(s))} s"
    if s < 5400:
        return f"{int(round(s / 60))} min"
    h, m = divmod(int(round(s / 60)), 60)
    return f"{h} h {m} min"


def _gb(n) -> str:
    try:
        return f"{float(n) / 1024 ** 3:.1f} GB"
    except Exception:  # noqa: BLE001
        return "?"


def _why(code: str, **kw):
    """(reason, suggest) for an outcome code (the §2.10.4 table); (None, None) for a code without a text."""
    tpl, suggest = _WHY.get(code, (None, None))
    if tpl is None:
        return None, None
    try:
        return _clip(tpl.format(**kw)), suggest
    except Exception:  # noqa: BLE001
        return _clip(tpl), suggest


def _phase_text(phase) -> str:
    try:
        st = _load_status()
        fn = getattr(st, "phase_words", None)
        if fn is not None:
            words = fn(phase)
            if words and words != str(phase):
                return str(words)
    except Exception:  # noqa: BLE001
        pass
    return _PHASE_WORDS.get(str(phase or ""), str(phase or "running"))


def _strip_userinfo(uri):
    if uri is None:
        return None
    s = str(uri).strip()
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^@/?#]*@(.*)$", s)
    return (m.group(1) + m.group(2)) if m else s


def _accepts(fn, *names) -> bool:
    """Does callable `fn` take every keyword in `names` (or **kwargs)? Never raises (False when unknown)."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        if any(p.kind is p.VAR_KEYWORD for p in params.values()):
            return True
        return all(n in params for n in names)
    except Exception:  # noqa: BLE001
        return False


def _is_db_error(exc) -> bool:
    """The CLI's "database unreachable / errored" class (deepmap_cli._db_error_types): a neo4j driver / server error,
    or an OSError. Never raises."""
    if isinstance(exc, OSError):
        return True
    try:
        from neo4j.exceptions import DriverError, Neo4jError
        return isinstance(exc, (Neo4jError, DriverError))
    except Exception:  # noqa: BLE001
        return False


# ---- seams (module-level functions the gates replace with recorders) -------------------------------------------------
def _path_build() -> None:
    if str(_BUILD) not in sys.path:
        sys.path.insert(0, str(_BUILD))


def _load_status():
    """build/sync_status (stdlib only at import). Raises ImportError when it is not there."""
    _path_build()
    return importlib.import_module("sync_status")


def _load_sync():
    """build/sync — imported only after R1 / R5 put the offline environment and the recorded recipe in place."""
    _path_build()
    return importlib.import_module("sync")


def _load_registry():
    _path_build()
    return importlib.import_module("port_registry")


def _load_graphify():
    _path_build()
    return importlib.import_module("graphify_extract")


_HOOK_STATE: list = []


def _load_hook_state():
    """hooks/deepmap_hook_state.py of THIS plugin tree — the module a hook process already imported, else loaded by
    path (cached). Raises when it cannot be loaded."""
    if _HOOK_STATE:
        return _HOOK_STATE[0]
    want = (_HOOKS / "deepmap_hook_state.py").resolve()
    mod = sys.modules.get("deepmap_hook_state")
    try:
        if mod is not None and Path(getattr(mod, "__file__", "") or "").resolve() == want:
            _HOOK_STATE.append(mod)
            return mod
    except Exception:  # noqa: BLE001
        pass
    spec = importlib.util.spec_from_file_location("_deepmap_hook_state_auto_sync", str(want))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {want}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _HOOK_STATE.append(mod)
    return mod


def _now() -> float:
    return time.time()


def _sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def _memory():
    """(available physical bytes, total physical bytes), each None when unknown. Windows GlobalMemoryStatusEx, macOS
    vm_stat (_darwin_memory), Linux /proc/meminfo, else the total from sysconf. An unknown available means no memory
    floor (R13, _before_embed). Never raises."""
    try:
        if os.name == "nt":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            ms = _MemStatus()
            ms.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return int(ms.ullAvailPhys), int(ms.ullTotalPhys)
            return None, None
        if sys.platform == "darwin":        # no /proc on macOS
            return _darwin_memory()
        avail = total = None
        try:
            with open("/proc/meminfo", encoding="ascii", errors="replace") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    parts = rest.split()
                    if key in ("MemAvailable", "MemTotal") and parts and parts[0].isdigit():
                        val = int(parts[0]) * 1024
                        if key == "MemAvailable":
                            avail = val
                        else:
                            total = val
        except OSError:
            pass
        if total is None:
            try:
                total = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
            except (ValueError, OSError, AttributeError):
                total = None
        return avail, total
    except Exception:  # noqa: BLE001
        return None, None


# macOS vm_stat (one snapshot: no arguments) prints a header naming the page size its counts are in, then one row per
# counter: "Mach Virtual Memory Statistics: (page size of 16384 bytes)", "Pages free:    13105.", ... (16 KB pages on
# Apple silicon, 4 KB on Intel). The page size is the header's (the unit vm_stat's own counts are in), never
# sysconf's, which can differ: a process Rosetta translates is told 4 KB pages on a 16 KB Mac.
_VM_STAT_HEADER = re.compile(r"Mach Virtual Memory Statistics:.*?\bpage size of (\d+) bytes\b")
_VM_STAT_COUNT = re.compile(r"\s*(\d+)\.?\s*")
_VM_STAT_PAGE_MIN = 4096
_VM_STAT_PAGE_MAX = 65536
# Available = the pages on the free, speculative and inactive queues: what the kernel hands out or reclaims first, and
# what psutil (like Zabbix) reports as a Mac's available memory. vm_stat's "Pages free" already leaves the speculative
# pages out, so the three rows never overlap. "Pages purgeable" is left out on purpose: a purgeable page also sits on
# the active or inactive queue, so adding it would count some pages twice. Reading too little at worst skips a refresh;
# reading too much would start one under memory pressure, which is what the floor exists to prevent.
_VM_STAT_AVAILABLE = ("Pages free", "Pages inactive", "Pages speculative")


def _vm_stat_available(text, total=None):
    """The available memory in bytes in one vm_stat snapshot `text`: (free + inactive + speculative pages) x the page
    size its header names. None, never a guess, when `text` is not text, its first line is not vm_stat's header, the
    page size is not a power of two from 4 KB to 64 KB, one of the three rows is missing, repeated or not a whole
    number, or the sum exceeds `total` (the machine's physical memory, when known). Other rows are ignored, in any
    order (a later macOS may print more). Never raises."""
    try:
        if not isinstance(text, str):
            return None
        lines = [ln for ln in text.splitlines() if ln.strip()]
        m = _VM_STAT_HEADER.match(lines[0].strip()) if lines else None
        if not m:
            return None
        page = int(m.group(1))
        if not _VM_STAT_PAGE_MIN <= page <= _VM_STAT_PAGE_MAX or page & (page - 1):
            return None
        counts: dict = {}
        for line in lines[1:]:
            label, sep, rest = line.partition(":")
            label = label.strip()
            if label not in _VM_STAT_AVAILABLE:
                continue
            c = _VM_STAT_COUNT.fullmatch(rest)
            if not sep or c is None or label in counts:
                return None
            counts[label] = int(c.group(1))
        if len(counts) != len(_VM_STAT_AVAILABLE):
            return None
        avail = sum(counts.values()) * page
        t = _num(total)
        if t and t > 0 and avail > t:
            return None
        return avail
    except Exception:  # noqa: BLE001
        return None


def _vm_stat_text(argv=None, timeout=None):
    """One snapshot from macOS's vm_stat (VM_STAT; `argv` / `timeout` are test seams): its standard output, or None
    when the program is missing, exits non-zero or does not answer within VM_STAT_TIMEOUT_S (the child is then ended).
    Never raises."""
    try:
        import subprocess
        p = subprocess.run(list(argv) if argv else [VM_STAT], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                           timeout=VM_STAT_TIMEOUT_S if timeout is None else float(timeout))
        return p.stdout if p.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _sysctl_memsize():
    """macOS: sysctl hw.memsize (the bytes of physical memory, a 64-bit value) through libc's sysctlbyname; None when
    it cannot be read, and anywhere but macOS. Never raises."""
    try:
        import ctypes
        fn = ctypes.CDLL(None).sysctlbyname
        fn.argtypes = (ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                       ctypes.c_size_t)
        fn.restype = ctypes.c_int
        value = ctypes.c_uint64(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if (fn(b"hw.memsize", ctypes.byref(value), ctypes.byref(size), None, 0) == 0
                and size.value == ctypes.sizeof(value) and value.value > 0):
            return int(value.value)
    except Exception:  # noqa: BLE001
        pass
    return None


def _darwin_total():
    """macOS physical memory: sysconf (SC_PHYS_PAGES x SC_PAGE_SIZE, as on every POSIX system), else sysctl
    hw.memsize (_sysctl_memsize); None when neither answers. Never raises."""
    try:
        total = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        if total > 0:
            return total
    except Exception:  # noqa: BLE001
        pass
    try:
        total = _sysctl_memsize()
        return int(total) if isinstance(total, int) and not isinstance(total, bool) and total > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _darwin_memory():
    """macOS (no /proc): (available, total) — the total from _darwin_total, the available memory from one vm_stat
    snapshot (_vm_stat_available, checked against that total; None when vm_stat cannot be read). Never raises."""
    total = _darwin_total()
    try:
        text = _vm_stat_text()
    except Exception:  # noqa: BLE001
        text = None
    return _vm_stat_available(text, total), total


def _route(root):
    """THIS project's Neo4j route (port_registry.resolve_neo4j_route — the one routing rule)."""
    return _load_registry().resolve_neo4j_route(root)


def _heal_status(root):
    """This project's heal status record (<DEEPMAP_HOME>/run/heal-<key>.json), or None. Never raises."""
    try:
        p = _load_hook_state().heal_status_path(root)
        return _read_json(p) if p else None
    except Exception:  # noqa: BLE001
        return None


def _bolt_up(root) -> bool:
    """Does this project's OWN bolt port (deepmap_hook_state.bolt_target's rule) accept a connection? Never raises."""
    try:
        hs = _load_hook_state()
        t = hs.bolt_target(root)
        return bool(t) and bool(hs.port_listening(t[1], t[0], timeout=1.0))
    except Exception:  # noqa: BLE001
        return False


def _heal_problem(root):
    """(kind, reason) of the last heal / setup blocker (deepmap_hook_state.heal_problem, no live re-probe), or None."""
    try:
        return _load_hook_state().heal_problem(root, recheck=False)
    except Exception:  # noqa: BLE001
        return None


def _model_state():
    return _load_status().model_cache_state()


def _engine_file_count(root, limit) -> int:
    return int(_load_graphify().engine_source_file_count(root, limit=limit))


def _graphify_version():
    return _load_graphify().graphify_version()


def _graphify_budget(root) -> dict:
    """graphify_extract.graphify_timeout(root): the engine budget a sync would get (the user's
    DEEPMAP_GRAPHIFY_TIMEOUT from this process's environment, else the size-aware default)."""
    return _load_graphify().graphify_timeout(root)


def _lower_priority() -> None:
    """Lower THIS process's priority once, before sync loads: its CPU priority — Windows BELOW_NORMAL_PRIORITY_CLASS
    (children — the engine, its pool workers — inherit it), POSIX nice +10 — and, on Linux, its I/O priority too
    (_lower_io_priority; inherited by every thread and child started later). What it cannot lower: the I/O of the
    runner on Windows and macOS, and the Neo4j server's own work (writes, index builds), which runs in the database's
    process at its normal priority. Errors ignored."""
    try:
        if os.name == "nt":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            k32.SetPriorityClass.restype = ctypes.c_int
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(POSIX_NICE)
    except Exception:  # noqa: BLE001
        pass
    _lower_io_priority()


def _libc_syscall(nr: int, *args: int) -> int:
    """libc's syscall(2) (Linux). Raises when it cannot be called."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.syscall
    fn.restype = ctypes.c_long
    return int(fn(ctypes.c_long(nr), *(ctypes.c_long(a) for a in args)))


def _lower_io_priority(platform_name=None, machine=None, syscall=None) -> bool:
    """Linux: ioprio_set(IOPRIO_WHO_PROCESS, 0 = this process, best-effort class level 7) — `ionice -c2 -n7`, the
    lowest I/O priority that cannot starve. Elsewhere, or on an architecture without a known syscall number, nothing
    (never a guessed number). True when the call returned 0. Never raises."""
    try:
        plat = sys.platform if platform_name is None else str(platform_name)
        if not plat.startswith("linux"):
            return False
        if machine is None:
            import platform
            machine = platform.machine()
        nr = _IOPRIO_SET_NR.get(str(machine or "").strip().lower())
        if nr is None:
            return False
        call = _libc_syscall if syscall is None else syscall
        return call(nr, IOPRIO_WHO_PROCESS, 0, (IOPRIO_CLASS_BE << 13) | IOPRIO_LOWEST) == 0
    except Exception:  # noqa: BLE001
        return False


def _git_head(root):
    """The 40-hex HEAD of `root`'s repository (`git rev-parse HEAD`, GIT_OPTIONAL_LOCKS=0, 30 s), or None. Never
    raises."""
    try:
        import shutil
        import subprocess
        git = shutil.which("git")
        if not git:
            return None
        pre = ["-c", "core.longpaths=true"] if os.name == "nt" else []
        p = subprocess.run([git, *pre, "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        sha = (p.stdout or "").strip().lower()
        if p.returncode == 0 and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            return sha
    except Exception:  # noqa: BLE001
        return None
    return None


def _find_spec(name):
    try:
        return importlib.util.find_spec(name)
    except Exception:  # noqa: BLE001
        return None


def _running_version():
    """The DeepMap version of the code running now (port_registry.code_identity — the rule sync stamps)."""
    try:
        pr = _load_registry()
        return pr.code_identity(_BUILD).get(pr.CODE_VERSION_FIELD)
    except Exception:  # noqa: BLE001
        return None


def _touch_last_used(root) -> None:
    try:
        _load_registry().touch_last_used(root)
    except Exception:  # noqa: BLE001
        pass


def _project_root(target) -> Path:
    """The project root a sync of `target` keeps its .deepmap/ in (deepmap_hook_state.project_root — sync._sync_root's
    rule without a subprocess), else `target` resolved."""
    try:
        p = Path(target).resolve()
    except Exception:  # noqa: BLE001
        p = Path(target)
    try:
        r = _load_hook_state().project_root(p)
        return Path(r) if r else p
    except Exception:  # noqa: BLE001
        return p


# ---- policy -----------------------------------------------------------------------------------------------------------
def _home(env=None) -> Path:
    e = os.environ if env is None else env
    try:
        return Path(_load_registry().home_dir(e))
    except Exception:  # noqa: BLE001
        return Path(e.get(ENV_HOME) or str(Path.home() / ".deepmap"))


def _instance_raw(root):
    """<root>/.deepmap/instance.json's "auto_sync" value as written (None when absent). Never raises."""
    try:
        try:
            _path_build()
            data = importlib.import_module("instance_state").read_instance(root)
        except Exception:  # noqa: BLE001
            data = _read_json(_deepmap_dir(root) / "instance.json") or {}
        return data.get("auto_sync") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _instance_block(root) -> dict:
    """<root>/.deepmap/instance.json's "auto_sync" object ({} when absent). A bare value is the switch itself:
    {"auto_sync": false} reads as {"enabled": false}. Never raises."""
    raw = _instance_raw(root)
    if isinstance(raw, dict):
        return raw
    return {} if raw is None else {"enabled": raw}


def _switch_word(value):
    """How a file switch's value reads: "off" (false, 0, an OFF word), "on" (true, a non-zero number, an ON word),
    None (absent, null, an empty string) or "bad" (anything else: not understood). Never raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            return "bad"
        return "off" if value == 0 else "on"
    if isinstance(value, str):
        w = value.strip().lower()
        if not w:
            return None
        return "off" if w in _OFF else ("on" if w in _ON else "bad")
    return "bad"


def _switched_off(value) -> bool:
    """A file switch is OFF for false / 0 / an OFF word and — failing closed — for a value it does not understand;
    true, an ON word or no value never turn it on (files only switch off)."""
    return _switch_word(value) in ("off", "bad")


def _machine_switch(env=None):
    """(state, path) of the machine switch <DEEPMAP_HOME>/auto-sync.json: state None (no file), "on" / "off" (its
    "enabled"), or "bad" — the file exists but is not understood (unreadable, not JSON, no "enabled" true/false, or a
    value that is neither): read as OFF, so a user who meant to switch the background writer off is never left with
    it on. A bare JSON value is the switch itself (`false` is off). Never raises."""
    path = _home(env) / MACHINE_SWITCH
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, path
    except Exception:  # noqa: BLE001
        try:
            return ("bad" if path.exists() else None), path
        except Exception:  # noqa: BLE001
            return None, path
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return "bad", path
    word = _switch_word(data.get("enabled") if isinstance(data, dict) else data)
    return (word if word in ("on", "off") else "bad"), path


def attended(env=None) -> bool:
    """Is someone watching this Claude Code session? CLAUDE_CODE_SESSION_ATTENDED (Claude Code sets "1" / "0" in a
    hook's environment) decides; without it, a CLAUDE_CODE_ENTRYPOINT in UNATTENDED_ENTRYPOINTS (claude -p, the SDKs,
    mcp serve, the GitHub Action, remote triggers) is unattended; anything else counts as attended. Never raises."""
    e = os.environ if env is None else env
    v = _word(e, "CLAUDE_CODE_SESSION_ATTENDED")
    if v in _ON:
        return True
    if v in _OFF:
        return False
    try:
        return str(e.get("CLAUDE_CODE_ENTRYPOINT", "") or "").strip() not in UNATTENDED_ENTRYPOINTS
    except Exception:  # noqa: BLE001
        return True


def enabled(root, trigger: str = "session-start", env=None) -> tuple:
    """(on, source): is auto-sync on for `trigger` in project `root`?
      1. DEEPMAP_AUTO_SYNC an explicit ON word -> (True, "env"); an OFF word -> (False, "env")   [explicit env wins]
      2. <root>/.deepmap/instance.json {"auto_sync": {"enabled": false}}                         -> (False, "instance")
      3. <DEEPMAP_HOME>/auto-sync.json {"enabled": false}                                       -> (False, "machine")
      4. trigger "session-start" in a session nobody watches (attended)             -> (False, "unattended-session")
      5. (True, "default").
    A file only switches OFF: `true` in a file never forces auto-sync on. The off-switches fail CLOSED: false, 0, an
    OFF word, a bare {"auto_sync": false}, and a value or machine file DeepMap does not understand all read as off
    (status_line says so). Never raises."""
    e = os.environ if env is None else env
    try:
        if _env_on(e, ENV_AUTO_SYNC):
            return True, "env"
        if _env_off(e, ENV_AUTO_SYNC):
            return False, "env"
        if _switched_off(_instance_block(root).get("enabled")):
            return False, "instance"
        if _machine_switch(e)[0] in ("off", "bad"):
            return False, "machine"
        if trigger == "session-start" and not attended(e):
            return False, "unattended-session"
        return True, "default"
    except Exception:  # noqa: BLE001
        return True, "default"


def _max_s(env) -> int:
    """DEEPMAP_AUTO_SYNC_MAX_S in seconds: unset / garbage / negative -> MAX_S_DEFAULT; 0 = no limit."""
    raw = _word(env, ENV_MAX_S)
    if not raw:
        return MAX_S_DEFAULT
    f = _num(raw)
    if f is None or f < 0:
        return MAX_S_DEFAULT
    return int(f)


def _prune_policy(root, env) -> str:
    """"none" when DEEPMAP_AUTO_SYNC_PRUNE is an OFF word or instance.json auto_sync.prune is false, else
    "evidence" (an unattended sync never prunes "full")."""
    if _env_off(env, ENV_PRUNE) or _switched_off(_instance_block(root).get("prune")):
        return "none"
    return "evidence"


def _mem_floor(total) -> int:
    """The free memory a background refresh leaves the machine: 25 % of RAM, at least MEM_FLOOR_MIN (3 GiB) and at most
    MEM_FLOOR_MAX (6 GiB); MEM_FLOOR_MIN when the total is unknown."""
    t = _num(total)
    return int(min(MEM_FLOOR_MAX, max(MEM_FLOOR_MIN, MEM_FLOOR_FRACTION * t))) if t else MEM_FLOOR_MIN


def _off_machine(uri) -> bool:
    """Is the database at `uri` on another machine — its host neither "localhost" nor a loopback address? An
    unreadable URI or one without a host counts as off-machine (fail closed). Never raises."""
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(str(uri).strip()).hostname or "").strip().lower().rstrip(".")
        if not host:
            return True
        if host == "localhost":
            return False
        import ipaddress
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            return True
    except Exception:  # noqa: BLE001
        return True


def _proven_empty(manifest) -> bool:
    try:
        return bool(_load_hook_state().proven_empty(manifest))
    except Exception:  # noqa: BLE001
        return False


def read_state(root) -> dict:
    """<root>/.deepmap/autosync.json parsed; {} on any failure. Never raises."""
    return _read_json(_deepmap_dir(root) / STATE_FILE) or {}


def _cooling(state, head, now) -> bool:
    """R4: a session-start run started less than COOLDOWN_S ago at the same HEAD (None == None: a non-git project
    cools down on time alone)."""
    lss = state.get("last_session_start") if isinstance(state, dict) else None
    if not isinstance(lss, dict):
        return False
    t0 = _num(lss.get("started_epoch"))
    if t0 is None or abs(now - t0) >= COOLDOWN_S:
        return False
    return (lss.get("head") or None) == (head or None)


def _synced_after(last, t) -> bool:
    """Did a sync (any origin) finish with outcome "synced" (verdict not FAILED) after epoch `t`?"""
    if not isinstance(last, dict) or last.get("outcome") != "synced":
        return False
    if str(last.get("verdict") or "").upper() == "FAILED":
        return False
    fe, t = _num(last.get("finished_epoch")), _num(t)
    return fe is not None and t is not None and fe > t


def _failure_block(state, last, now):
    """(kind, lifted): kind "paused" / "backoff" when one is in force and not LIFTED — a sync of any origin finished
    "synced" after the pause / the failure (a manual /deep-map:refresh resumes it); `lifted` True when such a sync
    cleared an earlier failure state. Never raises."""
    try:
        state = state if isinstance(state, dict) else {}
        lf = state.get("last_failure") if isinstance(state.get("last_failure"), dict) else {}
        paused = state.get("paused") if isinstance(state.get("paused"), dict) else None
        if paused:
            if _synced_after(last, paused.get("since_epoch")):
                return None, True
            return "paused", False
        bu = _num(state.get("backoff_until_epoch"))
        since = _num(lf.get("finished_epoch"))
        if since is None and bu is not None:
            since = bu - BACKOFF_S
        if bu is not None and now < bu:
            if _synced_after(last, since):
                return None, True
            return "backoff", False
        cf = _num(state.get("consecutive_failures")) or 0
        if cf > 0 and since is not None and _synced_after(last, since):
            return None, True
        return None, False
    except Exception:  # noqa: BLE001
        return None, False


def _block_reason(kind, state, now):
    """(reason, suggest) for a "paused" / "backoff" state (§2.10.4)."""
    state = state if isinstance(state, dict) else {}
    lf = state.get("last_failure") if isinstance(state.get("last_failure"), dict) else {}
    if kind == "paused":
        p = state.get("paused") if isinstance(state.get("paused"), dict) else {}
        if p.get("reason") == "engine-timeout":
            return _why("paused:engine-timeout", t=p.get("detail") or "its time budget")
        n = int(_num(state.get("consecutive_failures")) or PAUSE_AFTER_FAILURES)
        return _why("paused:failures", n=n, reason=_clip(p.get("detail") or lf.get("reason") or "unknown", 160))
    bu = _num(state.get("backoff_until_epoch")) or now
    since = _num(lf.get("finished_epoch"))
    if since is None:
        since = bu - BACKOFF_S
    return _why("backoff", ago=human_duration(now - since), reason=_clip(lf.get("reason") or "unknown", 160),
                until=_utc_fmt(bu, "%Y-%m-%d %H:%M"))


def _older_code(manifest):
    """(recorded, running) when the graph was built by NEWER DeepMap code than this one, else None."""
    rec = _recipe(manifest).get("deepmap_version") or (manifest or {}).get("deepmap_version")
    run = _running_version()
    if not rec or not run:
        return None
    try:
        if _load_registry().compare_versions(rec, run) == 1:
            return str(rec), str(run)
    except Exception:  # noqa: BLE001
        return None
    return None


def _recipe_recorded(manifest) -> bool:
    """Did the sync that built this graph record its graph-shaping settings (build_recipe.env)? A manifest written
    before DeepMap recorded them (no build_recipe) did not."""
    return isinstance(_recipe(manifest).get("env"), dict)


def _settings_conflicts(st, manifest, env) -> list:
    """[(name, recorded, current)]: sync_status.recipe_conflicts (THE rule for what conflicts) against the RECORDED
    graph-shaping settings. A manifest that recorded none (_recipe_recorded False: built before DeepMap recorded them)
    has no conflicts: its build's settings are unknown, so the refresh runs with the settings of the environment the
    runner was started from (the session's: what a /deep-map:refresh in that session would use) and never strips one
    that is set there — the sync then records them. Raises what recipe_conflicts raises."""
    if not _recipe_recorded(manifest):
        return []
    return list(st.recipe_conflicts(_recipe_env(manifest), env) or [])


def _settings_why(conflict):
    """(reason, suggest) of a recipe:settings conflict (name, recorded, current)."""
    name, a, b = (list(conflict) + [None, None, None])[:3]
    return _why("recipe:settings", name=name, a=a if a not in (None, "") else "(default)",
                b=b if b not in (None, "") else "(default)")


def _session_settings(st, env) -> str:
    """The non-default graph-shaping settings `env` sets ("NAME=value, ..."; sync_status.recipe_env, what the sync
    records), for the log. Never raises."""
    try:
        fn = getattr(st, "recipe_env", None)
        vals = fn(env) if callable(fn) else None
        if isinstance(vals, dict):
            return ", ".join(f"{k}={v}" for k, v in sorted(vals.items())) or "all at their defaults"
    except Exception:  # noqa: BLE001
        pass
    return "as the session sets them"


def _manifest_identity(manifest):
    """What tells one written manifest from another (_IDENTITY_KEYS: its write time, the capture time, its
    sync_wall_s and source fingerprint) — None when it records no sync time (nothing for the time limit to read).
    Never raises."""
    try:
        if not isinstance(manifest, dict) or _num(manifest.get("sync_wall_s")) is None:
            return None
        return {k: manifest.get(k) for k in _IDENTITY_KEYS}
    except Exception:  # noqa: BLE001
        return None


def _background_wrote(manifest, state, also=None) -> bool:
    """Did a background pass write `manifest`: is its identity autosync.json's background_manifest (or `also`, one this
    run's pass wrote)? Never raises."""
    ident = _manifest_identity(manifest)
    if ident is None:
        return False
    recorded = state.get("background_manifest") if isinstance(state, dict) else None
    for rec in (recorded, also):
        if isinstance(rec, dict) and all(rec.get(k) == ident.get(k) for k in _IDENTITY_KEYS):
            return True
    return False


def _limit_wall(manifest, state):
    """The wall time DEEPMAP_AUTO_SYNC_MAX_S is compared with (precheck's too-slow, R10, the needs-timing repeat): the
    sync_wall_s of the last sync that did NOT run in the background — the manifest's own, unless a background pass
    wrote the manifest (autosync.json background_manifest); then the one that pass's runner saw before it synced
    (autosync.json attended_wall), None when that manifest recorded none. A background pass runs at below-normal
    priority and is slow on a loaded machine by design, so its own wall never raises the value the limit reads, nor
    stands in for a missing one. Never raises."""
    try:
        if _background_wrote(manifest, state):
            aw = state.get("attended_wall")
            return _num(aw.get("wall_s")) if isinstance(aw, dict) else None
        return _num((manifest or {}).get("sync_wall_s"))
    except Exception:  # noqa: BLE001
        return None


def _repeat_why(code, state):
    """(reason, suggest) of a repeating skip: the recorded run's own words."""
    lr = state.get("last_run") if isinstance(state, dict) else None
    lr = lr if isinstance(lr, dict) else {}
    reason = _clip(lr.get("reason")) or f"the last background refresh ended {lr.get('outcome') or code}"
    return reason, (lr.get("suggest") or _WHY.get(code, (None, None))[1])


def _repeating_skip(root, state, manifest, env):
    """The code of a recorded skip the next session-start run would end with again (_REPEATING_SKIPS), or "heal-off"
    for a run that found the database down with DEEPMAP_HEAL still off — while the manifest has not been rewritten
    since that run (graph_build.json's mtime <= its finished_epoch: a refresh or setup lifts it). needs-timing also
    needs the time limit to still be on and no sync time it reads (_limit_wall); user-request also needs the user's
    request still queued (<root>/.deepmap/sync.rerun: a sync the user started claims it). None otherwise. File-only;
    never raises."""
    try:
        lr = state.get("last_run") if isinstance(state, dict) else None
        if not isinstance(lr, dict):
            return None
        fin = _num(lr.get("finished_epoch"))
        if fin is None:
            return None
        try:
            if (_deepmap_dir(root) / "graph_build.json").stat().st_mtime > fin:
                return None            # a sync rewrote the manifest after that run: its inputs may have changed
        except OSError:
            return None
        outcome = str(lr.get("outcome") or "")
        if outcome == "unreachable":
            return "heal-off" if lr.get("why_code") == "unreachable:no-heal" and _env_off(env, ENV_HEAL) else None
        code = outcome[len("skipped:"):] if outcome.startswith("skipped:") else None
        if code not in _REPEATING_SKIPS:
            return None
        if code == "needs-timing" and (_max_s(env) <= 0 or _limit_wall(manifest, state) is not None):
            return None
        if code == "user-request" and not (_deepmap_dir(root) / RERUN_FILE).is_file():
            return None
        return code
    except Exception:  # noqa: BLE001
        return None


def precheck(root, *, trigger: str = "session-start", source=None, head=None, env=None, now=None,
             announce: bool = False) -> dict:
    """Should a hook spawn (or announce) the background refresh for `root`? FILE-ONLY: no git, no network, no Neo4j,
    no subprocess. Returns {"ok", "code", "reason", "suggest" (the §2.10.4 words of a refusing code from
    remote-database on; None when ok), "took_s" (the manifest's sync_wall_s: the last sync's wall time, whatever ran
    it — what the notify quotes), "limit_took_s" (the wall the time limit reads: _limit_wall, the last sync that did
    not run in the background), "enabled_source"}. Codes in order: hooks-off, compact, disabled / unattended-session,
    not-set-up, empty, remote-database (an operator NEO4J_URI off this machine — the first step of the routing rule —
    unless DEEPMAP_AUTO_SYNC is explicitly on), paused, backoff, too-slow, recipe:settings (only against RECORDED
    settings: a legacy manifest is refreshed with the session's), recipe:older-code, and then:
      - with `head` given or `announce` True: a recorded skip the next run would repeat (_repeating_skip:
        needs-timing, no-own-instance, user-request while its request stays queued, recipe:python, ..., heal-off),
        then cooldown — a session-start run started < COOLDOWN_S ago at the same HEAD, where head None is the non-git
        rule (time alone, against a start recorded without a HEAD; a git project's recorded HEAD never matches None),
        and a head no session start records (deepmap_hook_state._NEXT_START_HEAD: the edit hook's "will the NEXT
        start refresh it?") never cools down;
      - with neither (the heal hook's spawn): neither check — the runner re-checks, cheaply, whether a recorded
        cause went away, and judges its own cooldown with the HEAD it reads.
    The hooks' contract: the notify's "will a session start refresh now?" is precheck(root, trigger="session-start",
    source=<payload source>, head=<HEAD, or None outside git>, announce=True), so a non-git project gets the repeat
    and cooldown answers too. Any exception -> {"ok": False, "code": "error"}."""
    e = os.environ if env is None else env
    out = {"ok": False, "code": None, "reason": None, "suggest": None, "took_s": None, "limit_took_s": None,
           "enabled_source": None}
    try:
        t = _now() if now is None else float(now)
        on, src = enabled(root, trigger, e)
        out["enabled_source"] = src

        def _no(code, why=(None, None)):
            out["code"] = code
            out["reason"], out["suggest"] = why
            return out

        if trigger == "session-start" and _env_off(e, ENV_HOOKS):
            return _no("hooks-off")
        if str(source or "").strip().lower() == "compact":
            return _no("compact")
        if not on:
            return _no("unattended-session" if src == "unattended-session" else "disabled")
        manifest = _read_manifest(root)
        if manifest is None:
            return _no("not-set-up")
        out["took_s"] = _num(manifest.get("sync_wall_s"))
        if _proven_empty(manifest):
            return _no("empty")
        uri = str(e.get("NEO4J_URI") or "").strip()
        if uri and not _env_on(e, ENV_AUTO_SYNC) and _off_machine(uri):
            return _no("remote-database", _why("remote-database", where=_strip_userinfo(uri)))
        st = _load_status()
        state = read_state(root)
        out["limit_took_s"] = limit = _limit_wall(manifest, state)
        kind, _lifted = _failure_block(state, st.read_last(root), t)
        if kind:
            return _no(kind, _block_reason(kind, state, t))
        mx = _max_s(e)
        if mx > 0 and limit is not None and limit > mx:
            return _no("too-slow", _why("too-slow", took=human_duration(limit), max=human_duration(mx)))
        conflicts = _settings_conflicts(st, manifest, e)
        if conflicts:
            return _no("recipe:settings", _settings_why(conflicts[0]))
        older = _older_code(manifest)
        if older:
            return _no("recipe:older-code", _why("recipe:older-code", a=older[0], b=older[1]))
        if head is not None or announce:
            repeat = _repeating_skip(root, state, manifest, e)
            if repeat:
                return _no(repeat, _repeat_why(repeat, state))
            if _cooling(state, head, t):
                return _no("cooldown")
        out["ok"] = True
        return out
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "error", "reason": None, "suggest": None, "took_s": out.get("took_s"),
                "limit_took_s": out.get("limit_took_s"), "enabled_source": out.get("enabled_source")}


def _graph_rev_due(manifest):
    """sync_status.graph_rev_due(manifest), THE rule: the graph revision a rebuild of this graph is due for (a release
    changed what DeepMap builds from the same sources, and the revision's scope covers this graph), or None: none is,
    or the rule cannot be loaded or fails (never a rebuild on a guess). Never raises."""
    try:
        fn = getattr(_load_status(), "graph_rev_due", None)
        rev = fn(manifest) if callable(fn) else None
        return rev if isinstance(rev, int) and not isinstance(rev, bool) and rev > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _graph_rev_note(manifest):
    """sync_status.graph_rev_note(manifest): "this graph was built by an older DeepMap (graph revision R, this code G:
    <what changed>)" for a due graph, else None. Never raises."""
    try:
        fn = getattr(_load_status(), "graph_rev_note", None)
        note = fn(manifest) if callable(fn) else None
        return str(note) if note else None
    except Exception:  # noqa: BLE001
        return None


def _rebuilt_already(state, manifest):
    """ONCE per revision: the graph revision `manifest` is due for (_graph_rev_due) when this project's background
    refresh already made its one-time rebuild for it, or for a newer one (autosync.json graph_rev_rebuilt.rev), else
    None. Such a manifest was rewritten since by a sync that records an older revision, i.e. an OLDER DeepMap: an older
    install's git post-commit hook names that install's plugin folder, so it keeps running its version after a plugin
    update, and rebuilding after it would repeat on every commit. Never raises."""
    try:
        due = _graph_rev_due(manifest)
        rec = state.get(GRAPH_REV_REBUILT) if isinstance(state, dict) else None
        done = rec.get("rev") if isinstance(rec, dict) else None
        if due is None or isinstance(done, bool) or not isinstance(done, int) or done < due:
            return None
        return due
    except Exception:  # noqa: BLE001
        return None


def _again_kw(manifest, rev) -> dict:
    """The words of the graph-rev-again skip for `manifest`: who rewrote it (the DeepMap version it records) and the
    revision. Never raises."""
    try:
        v = _recipe(manifest).get("deepmap_version") or (manifest or {}).get("deepmap_version")
    except Exception:  # noqa: BLE001
        v = None
    return {"writer": f"an older DeepMap ({_clip(v, 20)})" if v else "an older DeepMap", "rev": rev}


def freshness(root, manifest=None) -> dict:
    """Is the graph at `root` current with its sources? {"state": "fresh"|"stale"|"git-busy"|"unknown", "why",
    "head", "fingerprint", "kind"} — sync.source_fingerprint (now) against the fingerprint sync recorded when it
    captured the sources; a legacy manifest without one falls back to HEAD / dirty / the non-git content hash. Where
    the sources say fresh, a graph older than a graph revision that covers it (sync_status.graph_rev_due: a release
    changed what DeepMap builds from the same sources) is "stale" with why "graph-rev", so the refresh rebuilds it
    once (the rebuild records the current revision). Imports sync. Never raises (a failure is "unknown")."""
    root = Path(root)
    if manifest is None:
        manifest = _read_manifest(root) or {}
    head = fp_value = kind = None

    def out(state, why):
        return {"state": state, "why": why, "head": head, "fingerprint": fp_value, "kind": kind}

    def fresh():
        return out("stale", "graph-rev") if _graph_rev_due(manifest) is not None else out("fresh", None)

    try:
        sync = _load_sync()
        fp = sync.source_fingerprint(root)
        fp = fp if isinstance(fp, dict) else {}
        head, fp_value, kind = fp.get("head"), fp.get("value"), fp.get("kind")
        if fp.get("git_busy"):
            return out("git-busy", f"git-busy:{fp.get('git_busy')}")
        if fp_value is None:
            return out("unknown", "truncated" if fp.get("truncated") else "git-error")
        if manifest.get("embeddings_pending"):
            return out("stale", "embeddings-pending")
        recorded = manifest.get("source_fingerprint")
        if recorded:
            if str(recorded) == str(fp_value):
                return fresh()
            built = str(manifest.get("built_at_sha") or "").lower() or None
            return out("stale", "head-moved" if (head or None) != built else "sources-changed")
        # a legacy manifest (written before the fingerprint existed)
        if kind == "git" or head:
            built = str(manifest.get("built_at_sha") or manifest.get("built_at_base_sha") or "").lower()
            if not built or (head or "") != built:
                return out("stale", "head-moved")
            dirty = manifest.get("dirty", manifest.get("built_at_dirty"))
            if dirty is not False:
                return out("stale", "legacy-dirty")
            now_dirty = sync._git_worktree_dirty(root)
            if now_dirty is True:
                return out("stale", "sources-changed")
            if now_dirty is None:
                return out("unknown", "git-error")
            return fresh()
        content = sync._fs_content_hash(root)
        if content is None or content != manifest.get("built_at_content_hash"):
            return out("stale", "sources-changed")
        return fresh()
    except Exception as exc:  # noqa: BLE001
        _log(f"[deepmap auto-sync] freshness check failed: {type(exc).__name__}: {exc}")
        return out("unknown", "error")


def engine_budget(root, env=None) -> int:
    """The engine time budget of an unattended sync, in seconds: graphify_extract.graphify_timeout(root) — the user's
    DEEPMAP_GRAPHIFY_TIMEOUT as given, else TWICE the size-aware default (a below-normal-priority run is slower) —
    capped at DEEPMAP_AUTO_SYNC_MAX_S (`env`; 0 = no cap), never under 60 s. run() puts it in
    DEEPMAP_GRAPHIFY_TIMEOUT for the sync it starts. The user's value is read where the engine reads it (this
    process's environment). Never raises."""
    e = os.environ if env is None else env
    try:
        b = _graphify_budget(root)
        t = int(_num(b.get("timeout_s")) or 600)
        if b.get("source") != "env":
            t *= 2
    except Exception:  # noqa: BLE001
        t = 1200
    mx = _max_s(e)
    if mx > 0:
        t = min(t, mx)
    return max(60, int(t))


# ---- the runner -----------------------------------------------------------------------------------------------------
class _End(Exception):
    """How a run ends: a runner outcome code, its exit code, the effect on the backoff state ("reset" | "+1" |
    "pause-timeout"), and the last_run fields (reason, suggest, advice, ...)."""

    def __init__(self, outcome: str, rc: int, *, effects=(), **fields):
        super().__init__(outcome)
        self.outcome = outcome
        self.rc = int(rc)
        self.effects = list(effects)
        self.fields = fields


def _skip(code: str, *, outcome=None, **kw) -> _End:
    """A recorded skip (rc 4, no backoff effect): outcome "skipped:<code>" (or `outcome`), the §2.10.4 reason text,
    and the reason's own code in last_run.why_code (precheck's repeat rule reads it)."""
    reason, suggest = _why(code, **kw)
    return _End(outcome or f"skipped:{code}", 4, reason=reason, suggest=suggest, why_code=code)


def _unreachable(code: str, **kw) -> _End:
    """The "unreachable" outcome (rc 3, no backoff effect) with the reason variant `code` (last_run.why_code)."""
    reason, suggest = _why(code, **kw)
    return _End("unreachable", 3, reason=reason, suggest=suggest, why_code=code)


class _Runner:
    def __init__(self, root: Path, trigger: str, session, env, started: float):
        self.root = root
        self.trigger = trigger
        self.session = session or None
        self.env = env
        self.started = started
        self.id = f"{_utc(started)}-{os.getpid()}"
        self.st = None
        self.sync = None
        self.lock = None
        self.slot = None
        self.state: dict = {}
        self.manifest: dict = {}
        self.head = None
        self.conflict = None
        self.lifted = False
        self.F = None
        self.F_at = None
        self.db_ok = False
        self.budget = None
        self.policy = "evidence"
        self.ctl = None
        self.veto = None
        self.attended = None          # R15: the wall of the manifest a sync that did NOT run in the background wrote
        self.bg_ident = None          # R15: the identity of the manifest this run's background pass wrote
        self.rev_done = None          # R15: the graph revision a pass of this run rebuilt for (it ended synced)
        self._wlock = threading.Lock()
        self._done = False

    # -- the environment the sync runs in (sync reads os.environ)
    def _setenv(self, name: str, value: str) -> None:
        os.environ[name] = value
        if self.env is not os.environ:
            try:
                self.env[name] = value
            except Exception:  # noqa: BLE001
                pass

    # -- records
    def _save(self) -> None:
        try:
            if self.st is not None and self.lock is not None:
                self.st.atomic_write_json(_deepmap_dir(self.root) / STATE_FILE, self.state)
        except Exception:  # noqa: BLE001
            pass

    def _set_current(self, phase: str) -> None:
        cur = self.state.get("current")
        if isinstance(cur, dict):
            cur["state"] = phase
            self._save()

    def _release(self) -> None:
        for name in ("slot", "lock"):
            h = getattr(self, name)
            setattr(self, name, None)
            if h is not None:
                try:
                    self.st.release_lock(h)
                except Exception:  # noqa: BLE001
                    pass

    def _silent(self, outcome: str, rc: int, why=None) -> int:
        _log(f"[deepmap auto-sync] {_utc()} outcome={outcome} rc={rc} wall={max(0.0, _now() - self.started):.1f}s"
             + (f" — {why}" if why else ""))
        return rc

    def _finish(self, end: _End, *, from_watcher: bool = False) -> int:
        """R17: last_run, the backoff / pause effects, current cleared — the one write of the outcome. Then both locks
        are released (not from the hard-exit watcher: the process exits and the OS releases them)."""
        with self._wlock:
            first = not self._done
            self._done = True
            if first:
                try:
                    self._record(end)
                except Exception:  # noqa: BLE001
                    traceback.print_exc(file=sys.stderr)
        if not from_watcher:
            self._release()
        if first:
            f = end.fields
            self._silent(end.outcome, end.rc, f.get("reason"))
        return end.rc

    def _record(self, end: _End) -> None:
        now = _now()
        f = end.fields
        state = self.state if isinstance(self.state, dict) else {}
        effects = (["reset"] if self.lifted else []) + list(end.effects)
        for eff in effects:
            if eff == "reset":
                state["consecutive_failures"] = 0
                state["backoff_until_epoch"] = None
                state["paused"] = None
            elif eff == "+1":
                cf = int(_num(state.get("consecutive_failures")) or 0) + 1
                state["consecutive_failures"] = cf
                state["backoff_until_epoch"] = now + BACKOFF_S
                state["last_failure"] = {"outcome": end.outcome, "reason": f.get("reason"), "finished_epoch": now}
                if cf >= PAUSE_AFTER_FAILURES:
                    state["paused"] = {"reason": "failures", "since_epoch": now,
                                       "detail": _clip(f.get("reason") or end.outcome, 200)}
            elif eff == "pause-timeout":
                state["paused"] = {"reason": "engine-timeout", "since_epoch": now,
                                   "detail": human_duration(self.budget) if self.budget else "its time budget"}
                state["last_failure"] = {"outcome": end.outcome, "reason": f.get("reason"), "finished_epoch": now}
        stale_kept = f.get("stale_kept")
        if self.attended is not None:
            state["attended_wall"] = self.attended
        if self.bg_ident is not None:
            state["background_manifest"] = self.bg_ident
        if self.rev_done is not None:              # the one-time rebuild, kept for _rebuilt_already (once per revision)
            prev = state.get(GRAPH_REV_REBUILT) if isinstance(state.get(GRAPH_REV_REBUILT), dict) else {}
            done = prev.get("rev")
            done = done if isinstance(done, int) and not isinstance(done, bool) else 0
            state[GRAPH_REV_REBUILT] = {"rev": max(done, self.rev_done), "finished_epoch": now}
        state["schema"] = SCHEMA
        state["current"] = None
        state["last_run"] = {
            "id": self.id, "session_id": self.session, "trigger": self.trigger,
            "started_utc": _utc(self.started), "finished_utc": _utc(now), "finished_epoch": now,
            "wall_s": round(max(0.0, now - self.started), 1), "rc": end.rc, "outcome": end.outcome,
            "reason": _clip(f.get("reason")), "advice": f.get("advice"), "suggest": f.get("suggest"),
            "head": f.get("head") or (self.F or {}).get("head") or self.head,
            "fresh_at_head": f.get("fresh_at_head"), "fingerprint": f.get("fingerprint"),
            "sync_id": f.get("sync_id"), "verdict": f.get("verdict"), "reasons": list(f.get("reasons") or [])[:3],
            "built_at_sha": f.get("built_at_sha"), "dirty": f.get("dirty"),
            "stale_kept": int(_num(stale_kept) or 0), "embeddings_deferred": bool(f.get("embeddings_deferred")),
            "took_s": f.get("took_s") if f.get("took_s") is not None else _num(self.manifest.get("sync_wall_s")),
            "why_code": f.get("why_code"),
        }
        if f.get("graph_rev") is not None:          # only a pass that rebuilt for a graph revision carries it
            state["last_run"]["graph_rev"] = f.get("graph_rev")
        self.state = state
        self._save()

    # -- the steps
    def go(self) -> int:
        e = self.env
        on, src = enabled(self.root, self.trigger, e)                                          # R2
        if not on:
            return self._silent("disabled", 4, f"auto-sync is off ({src})")
        if self.trigger == "session-start" and _env_off(e, ENV_HOOKS):
            return self._silent("disabled", 4, "DEEPMAP_HOOKS is off")
        manifest = _read_manifest(self.root)                                                    # R3
        if manifest is None:
            return self._silent("not-set-up", 1, "no .deepmap/graph_build.json")
        if _proven_empty(manifest):
            return self._silent("empty", 4, "the last sync built nothing usable")
        self.manifest = manifest
        self.head = _git_head(self.root)                                                        # R4
        if self.trigger == "session-start" and _cooling(read_state(self.root), self.head, _now()):
            return self._silent("cooldown", 4)
        try:                                                                                    # R5
            st = _load_status()
        except Exception as exc:  # noqa: BLE001
            return self._silent("skipped:packages-missing", 4, f"build/sync_status cannot be loaded ({exc})")
        self.st = st
        recorded = _recipe_env(manifest)
        try:
            conflicts = _settings_conflicts(st, manifest, e)
        except Exception:  # noqa: BLE001
            conflicts = []
        if conflicts:
            self.conflict = conflicts[0]
        elif not _recipe_recorded(manifest):
            # built before DeepMap recorded its settings: nothing to replay or compare — the sync runs with the
            # graph-shaping settings of the environment this runner was started from (the session's) and records them
            _log(f"[deepmap auto-sync] this graph's manifest records no build settings (it was built before DeepMap "
                 f"recorded them), so this refresh uses the graph-shaping settings of the session that started it "
                 f"({_session_settings(st, e)}) and records them")
        else:
            try:
                st.replay_env(recorded, os.environ)
                if e is not os.environ:
                    st.replay_env(recorded, e)
            except Exception:  # noqa: BLE001
                pass
        self.policy = _prune_policy(self.root, e)
        lock_path = _deepmap_dir(self.root) / LOCK_FILE
        handle = st.try_lock(lock_path)                                                         # R6
        if handle is None:
            handle, rc = self._coalesce(lock_path)
            if handle is None:
                return rc
        self.lock = handle
        try:
            self._start_record()                                                                # R7
            self._eligible()                                                                    # R8
            self._cycle()                                                                       # R9-R16
            raise _End("failed:error", 1, reason="the background refresh ended without an outcome", suggest="refresh",
                       effects=["+1"])
        except _End as end:
            return self._finish(end)                                                            # R17
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return self._finish(_End("failed:error", 1, effects=["+1"], suggest="refresh",
                                     reason=f"the background refresh crashed ({type(exc).__name__}: {exc})"))
        finally:
            self._release()

    def _coalesce(self, lock_path):
        """R6c: another runner holds this project's autosync.lock. When the sources changed since the graph was built,
        queue a re-run through sync's own queue (its pass loop, or the holder's R16, performs it), then try the lock
        once more (_take_over — sync's own skipper does the same, Y3): the holder may have released it meanwhile, or
        made its one look at sync.rerun already. (handle, None) when this run took the lock over — it continues as
        the holder from R7, and its sync claims the request — else (None, 0): "coalesced", nothing recorded
        (autosync.json has one writer, the holder). A graph that is stale only for a graph revision (sources
        unchanged) queues nothing: the holder reads the same manifest with the same rule and rebuilds it (a queued
        re-run would make it sync a second time), and a holder that ends without that leaves the graph due for the
        next session start, which finds it again."""
        why = "another background refresh of this project is running"
        try:
            sync = _load_sync()
            F = freshness(self.root, _read_manifest(self.root) or self.manifest)
            if F.get("state") == "stale" and F.get("why") == "graph-rev":
                why += (" (sources unchanged; the graph revision this graph is due for is that refresh's to rebuild: "
                        "nothing queued)")
            elif F.get("state") == "stale":
                ok = _queue_rerun(sync, self.root, self.policy, self.trigger)
                handle = self._take_over(lock_path)
                if handle is not None:
                    _log("[deepmap auto-sync] the other background refresh of this project ended meanwhile — this "
                         "run continues as its refresh" + ("" if ok else " (its re-run request could not be queued)"))
                    return handle, None
                why += " — queued a re-run for it" if ok else " — a re-run could not be queued"
            else:
                why += f" (sources: {F.get('state')})"
        except Exception as exc:  # noqa: BLE001
            why += f" (freshness not checked: {type(exc).__name__})"
        return None, self._silent("coalesced", 0, why)

    def _take_over(self, lock_path):
        """After queueing a request: the lock once more — the holder may have released it in between. Still held: a
        holder that has yet to make its one look at sync.rerun (R16) will see the request; one that already made it
        (autosync.json current.rerun_look "done", or `current` cleared: it is recording its outcome) will not, so wait
        — at most TAKEOVER_WAIT_S — for it to release, and continue as the holder. The handle, or None. Never
        raises."""
        try:
            handle = self.st.try_lock(lock_path)
            if handle is not None:
                return handle
            deadline = _now() + TAKEOVER_WAIT_S
            while True:
                cur = read_state(self.root).get("current")
                if isinstance(cur, dict) and cur.get("rerun_look") != "done":
                    return None                    # the holder has yet to look: it sees the request
                if _now() >= deadline:
                    return None
                _sleep(TAKEOVER_POLL_S)
                handle = self.st.try_lock(lock_path)
                if handle is not None:
                    return handle
        except Exception:  # noqa: BLE001
            return None

    def _mark_look(self) -> None:
        """Published right before this run's one look at sync.rerun (R16): from here on a runner that coalesces into
        this one cannot count on it to see a request it queues, so that runner waits for the lock instead
        (_take_over)."""
        cur = self.state.get("current") if isinstance(self.state, dict) else None
        if isinstance(cur, dict) and cur.get("rerun_look") != "done":
            cur["rerun_look"] = "done"
            self._save()

    def _start_record(self) -> None:
        ident = {}
        try:
            ident = self.st.process_identity() or {}
        except Exception:  # noqa: BLE001
            ident = {}
        self.state = read_state(self.root)
        self.state["schema"] = SCHEMA
        self.state["current"] = {"id": self.id, "pid": os.getpid(), "pid_created": ident.get("pid_created"),
                                 "session_id": self.session, "trigger": self.trigger,
                                 "started_utc": _utc(self.started), "started_epoch": self.started,
                                 "state": "checking"}
        if self.trigger == "session-start":
            self.state["last_session_start"] = {"started_epoch": self.started, "head": self.head}
        self._save()
        _touch_last_used(self.root)

    def _eligible(self) -> None:
        """R8 — every check that needs no git, in the spec's order; each failing one ends the run (recorded, rc 4)."""
        root, m, rec = self.root, self.manifest, _recipe(self.manifest)
        route = _route(root)
        if route is None or getattr(route, "problem", None) or getattr(route, "source", None) not in _OWN_ROUTES \
                or not getattr(route, "uri", None):
            raise _skip("no-own-instance")
        if route.source == "env" and _off_machine(route.uri) and not _env_on(self.env, ENV_AUTO_SYNC):
            # an operator NEO4J_URI on another machine (a hosted or team database): pushing the code graph there is
            # egress, which a background refresh never starts on its own — only an explicit DEEPMAP_AUTO_SYNC=1
            raise _skip("remote-database", where=_strip_userinfo(route.uri))
        hp = _heal_problem(root)
        if isinstance(hp, (tuple, list)) and hp and hp[0] in ("refused", "blocked"):
            raise _skip("setup-blocked", detail=_clip(hp[1] if len(hp) > 1 and hp[1] else hp[0], 200))
        missing = [p for p in _PACKAGES if _find_spec(p) is None]
        if missing:
            raise _skip("packages-missing")
        try:
            last = self.st.read_last(root)
        except Exception:  # noqa: BLE001
            last = None
        kind, self.lifted = _failure_block(self.state, last, _now())
        if kind:
            reason, suggest = _block_reason(kind, self.state, _now())
            raise _End(f"skipped:{kind}", 4, reason=reason, suggest=suggest)
        if self.conflict:
            reason, suggest = _settings_why(self.conflict)         # precheck's recipe:settings words too
            raise _End("skipped:recipe:settings", 4, reason=reason, suggest=suggest, why_code="recipe:settings")
        older = _older_code(m)
        if older:
            raise _skip("recipe:older-code", a=older[0], b=older[1])
        py = rec.get("python")
        if py and str(py) != _python_xy():
            raise _skip("recipe:python", a=py, b=_python_xy())
        home = rec.get("deepmap_home")
        if home:
            now_home = os.path.realpath(str(_home(self.env)))
            if os.path.normcase(os.path.realpath(str(home))) != os.path.normcase(now_home):
                raise _skip("recipe:home", a=home, b=now_home)
        try:
            self.sync = _load_sync()
        except Exception as exc:  # noqa: BLE001
            _log(f"[deepmap auto-sync] build/sync cannot be loaded: {type(exc).__name__}: {exc}")
            raise _skip("packages-missing") from None
        rroute = rec.get("route") if isinstance(rec.get("route"), dict) else {}
        if rroute.get("uri"):
            # compared by ENDPOINT only (sync._instance_lock_key: one key per endpoint whatever its loopback spelling;
            # userinfo never compared): the same endpoint is the same database however it was named — sync records
            # an operator NEO4J_URI naming this project's own instance as that managed source, and an older build
            # recorded it as "env" — so only a different endpoint is a different route
            a_uri, b_uri = _strip_userinfo(rroute.get("uri")), _strip_userinfo(route.uri)
            if self._route_key(a_uri) != self._route_key(b_uri):
                raise _skip("recipe:route", a=a_uri, b=b_uri)
        elif route.source == "env":
            # no recorded route (a manifest from before build_recipe recorded one): never follow an operator
            # NEO4J_URI to a database this graph's build did not record (a managed route is this project's own)
            raise _skip("recipe:route-unrecorded", outcome="skipped:recipe:route", b=_strip_userinfo(route.uri))
        try:
            ms = _model_state() or {}
        except Exception:  # noqa: BLE001
            ms = {}
        rev_a, rev_b = rec.get("embed_model_revision"), ms.get("revision")
        if rev_a and rev_b and str(rev_a) != str(rev_b):
            raise _skip("recipe:model", a=rev_a, b=rev_b)
        model_a, model_b = rec.get("embed_model"), self._model_name()
        if model_a and model_b and str(model_a) != str(model_b):
            raise _skip("recipe:model", a=model_a, b=model_b)
        prior = _prior_engines(m)
        want = self._requested_extractor()
        gv = _graphify_version()
        if prior == ["ast"] and want in ("auto", "graphify") and gv:
            raise _skip("recipe:engine", a="ast", b="graphify")
        if "graphify" in prior and want == "auto" and not gv:
            raise _skip("recipe:engine", a="graphify", b="ast")
        if gv and want in ("auto", "graphify") and "graphify" in prior:
            built_v = rec.get("graphify_version") or m.get("graphify_version")
            if not _engine_supported(gv) or (built_v and _version_older(gv, built_v)):
                raise _skip("recipe:engine-version", a=built_v or "version", b=gv)

    def _route_key(self, uri):
        try:
            return self.sync._instance_lock_key(uri)
        except Exception:  # noqa: BLE001
            return uri

    @staticmethod
    def _model_name():
        try:
            _path_build()
            return importlib.import_module("embeddings").MODEL_NAME
        except Exception:  # noqa: BLE001
            return None

    def _requested_extractor(self) -> str:
        """The extractor mode a sync would run with now (after the replay): build_recipe's own reading of the
        environment (sync_status.make_build_recipe), "auto" when unknown."""
        try:
            r = self.st.make_build_recipe(engine=None, extraction=None, route_source=None, neo4j_uri=None,
                                          code_version=None)
            v = str((r or {}).get("extractor_requested") or "").strip().lower()
            return v or "auto"
        except Exception:  # noqa: BLE001
            return "auto"

    def _rerun_pending(self) -> bool:
        """R16: a re-run request is still queued, it is not a user's (sync._is_user_request: only a sync the user
        starts runs one — another background pass would only record user-request-pending over this run's outcome),
        this run did not yield, and no user's sync waits to preempt. Called at most once per run (its one look), after
        _mark_look published it."""
        self._mark_look()
        try:
            if self.ctl is not None and self.ctl.yielded():
                return False
            if self.st.live_preempts(self.root):
                return False
            try:
                path = Path(self.sync._rerun_request_path(self.root))
            except Exception:  # noqa: BLE001
                path = _deepmap_dir(self.root) / RERUN_FILE
            if not path.is_file():
                return False
            peek = getattr(self.sync, "_peek_rerun_request", None)
            is_user = getattr(self.sync, "_is_user_request", None)
            return not (callable(peek) and callable(is_user) and is_user(peek(self.root)))
        except Exception:  # noqa: BLE001
            return False

    def _user_request_waiting(self) -> bool:
        """A re-run a user's sync queued is waiting (sync._peek_rerun_request / sync._is_user_request — THE rule) and no
        sync holds this project's lock to run it: the sync would end this run user-request-pending (it never runs, nor
        claims, such a request), so the run ends now — before it waits for the database or the machine slot. A live
        holder (a user's sync runs it; covered / queued) is left to R14 / R15. False when it cannot tell. Never
        raises."""
        try:
            peek = getattr(self.sync, "_peek_rerun_request", None)
            is_user = getattr(self.sync, "_is_user_request", None)
            if not (callable(peek) and callable(is_user)) or not is_user(peek(self.root)):
                return False
            h = self.st.read_running(self.root)
            return not (isinstance(h, dict) and h.get("live") is True)
        except Exception:  # noqa: BLE001
            return False

    def _cycle(self) -> None:
        """R9-R16. Raises the run's _End."""
        extra = False
        F = self._r9()
        while True:
            if F["state"] == "fresh":
                if not extra and self._rerun_pending():
                    extra = True
                    F = self._r9()
                    continue
                raise _End("fresh", 0, effects=["reset"], fresh_at_head=F.get("head"), head=F.get("head"),
                           fingerprint=F.get("fingerprint"))
            if self._user_request_waiting():
                raise _skip("user-request")
            self._r10()
            self._r11()
            self._r12()
            self._r13()
            F, covered = self._r14(F)
            if F["state"] == "fresh":
                continue
            if covered:
                if not extra and self._rerun_pending():
                    extra = True
                    F = self._r9()
                    continue
                raise _End("covered", 0, effects=["reset"], head=F.get("head"), fingerprint=F.get("fingerprint"))
            end = self._r15()
            if end.outcome == "synced" and not extra and self._rerun_pending():
                extra = True
                _log("[deepmap auto-sync] a re-run was queued while it synced — syncing once more")
                rev = end.fields.get("graph_rev")
                end = self._r15()
                if rev is not None and end.outcome == "synced" and end.fields.get("graph_rev") is None:
                    end.fields["graph_rev"] = rev           # the run's first pass made the one-time rebuild
            raise end

    def _r9(self) -> dict:
        """R9: freshness; git busy / unknown are recorded skips, and so is a graph due again for a graph revision this
        project's background refresh already rebuilt for (_rebuilt_already: once per revision)."""
        m = _read_manifest(self.root) or self.manifest
        self.F = F = freshness(self.root, m)
        self.F_at = _now()
        if F["state"] == "git-busy":
            op = str(F.get("why") or "").split(":", 1)[-1]
            raise _skip("git-busy", op=_GIT_OPS.get(op, op or "operation"))
        if F["state"] == "unknown":
            raise _skip("unknown:truncated" if F.get("why") == "truncated" else "unknown", outcome="skipped:unknown")
        if F["state"] == "stale" and F.get("why") == "graph-rev":
            rev = _rebuilt_already(self.state, m)
            if rev is not None:
                raise _skip("graph-rev-again", **_again_kw(m, rev))
        return F

    def _r10(self) -> None:
        mx = _max_s(self.env)
        if mx <= 0:
            return                                  # DEEPMAP_AUTO_SYNC_MAX_S=0: no time limit at all
        m = _read_manifest(self.root) or self.manifest
        if _background_wrote(m, None, self.bg_ident):           # this run's own pass wrote it (R16's extra pass)
            wall = _num((self.attended or {}).get("wall_s"))
        else:
            wall = _limit_wall(m, self.state)                    # the last sync that did NOT run in the background
        if wall is not None:
            if wall > mx:
                raise _skip("too-slow", took=human_duration(wall), max=human_duration(mx))
            return
        try:
            n = _engine_file_count(self.root, LEGACY_FILE_LIMIT + 1)
        except Exception:  # noqa: BLE001
            n = 0
        if n > LEGACY_FILE_LIMIT:
            raise _skip("needs-timing")

    def _r11(self) -> None:
        """Wait for the database: this session's heal verdict (written after this run started), or this project's
        own bolt port listening on two probes BOLT_CONFIRM_S apart. Never starts Neo4j."""
        if self.db_ok:
            return
        self._set_current("waiting-db")
        if _env_off(self.env, ENV_HEAL):
            if _bolt_up(self.root):
                self.db_ok = True
                return
            raise _unreachable("unreachable:no-heal")
        entered = _now()
        deadline = max(self.started + HEAL_WAIT_S, entered + 2 * BOLT_CONFIRM_S + HEAL_POLL_S)
        first_up = None
        while True:
            hs = _heal_status(self.root)
            ts = _num(hs.get("ts_epoch")) if isinstance(hs, dict) else None
            if ts is not None and ts >= self.started - 2:
                if hs.get("refused") is True or hs.get("refusal_kind"):
                    raise _skip("setup-blocked", detail=_clip(hs.get("reason") or hs.get("refusal_kind"), 200))
                if hs.get("neo4j_ok") is False:
                    raise _unreachable("unreachable:heal", detail=_clip(hs.get("reason") or "not reachable", 200))
                if hs.get("neo4j_ok") is True:
                    self.db_ok = True
                    return
            now = _now()
            if _bolt_up(self.root):
                if first_up is None:
                    first_up = now
                elif now - first_up >= BOLT_CONFIRM_S:
                    self.db_ok = True
                    return
            else:
                first_up = None
            if now >= deadline:
                raise _unreachable("unreachable")
            _sleep(HEAL_POLL_S)

    def _r12(self) -> None:
        """One unattended sync per machine: the slot lock, held until the run ends."""
        if self.slot is not None:
            return
        self._set_current("waiting-slot")
        path = _home(self.env) / "run" / SLOT_FILE
        deadline = _now() + SLOT_WAIT_S
        while True:
            h = self.st.try_lock(path)
            if h is not None:
                self.slot = h
                return
            if _now() >= deadline:
                raise _skip("machine-busy")
            _sleep(SLOT_POLL_S)

    def _embeddings_only(self) -> bool:
        """Is the only reason to refresh the embeddings an earlier refresh postponed (freshness "embeddings-pending"
        with the source fingerprint unchanged)?"""
        F = self.F or {}
        m = _read_manifest(self.root) or self.manifest or {}
        fp, rec = F.get("fingerprint"), m.get("source_fingerprint")
        return F.get("why") == "embeddings-pending" and bool(fp) and bool(rec) and str(fp) == str(rec)

    def _r13(self) -> None:
        """Memory: at least the floor (_mem_floor) available — plus EMBED_HEADROOM when the run would only compute
        embeddings postponed earlier for low memory (else it re-syncs the whole graph only to postpone them again,
        session after session). Then the embedding model complete in the local cache."""
        avail, total = _memory()
        floor = _mem_floor(total)
        if self._embeddings_only():
            need = floor + EMBED_HEADROOM
            if avail is not None and avail < need:
                raise _skip("low-memory:embed", outcome="skipped:low-memory", avail=_gb(avail), need=_gb(need))
        elif avail is not None and avail < floor:
            raise _skip("low-memory", avail=_gb(avail), floor=_gb(floor))
        try:
            ms = _model_state() or {}
        except Exception:  # noqa: BLE001
            ms = {}
        if not ms.get("complete"):
            raise _skip("model-missing")

    def _r14(self, F):
        """(F, covered): a live sync already reading exactly these sources covers this run; a live enrich holds the
        lock (busy-enrich). A freshness result older than RECHECK_AFTER_S is refreshed first."""
        if self.F_at is None or _now() - self.F_at >= RECHECK_AFTER_S:
            F = self._r9()
            if F["state"] == "fresh":
                return F, False
        try:
            h = self.st.read_running(self.root)
        except Exception:  # noqa: BLE001
            h = None
        if isinstance(h, dict) and h.get("live") is True:
            origin = h.get("origin")
            if origin in _HOLDER_ORIGINS:
                hf = h.get("source_fingerprint")
                if (hf and F.get("fingerprint") and str(hf) == str(F.get("fingerprint"))) or \
                        (not hf and h.get("phase") == "start"):
                    return F, True
            elif origin == "enrich":
                raise _skip("busy-enrich")
        return F, False

    def _r15(self) -> _End:
        """Run the sync under an UnattendedControl and map its outcome (§2.9.1)."""
        sync, st = self.sync, self.st
        if self.budget is None:
            self.budget = engine_budget(self.root, self.env)
            try:
                name = _load_graphify().ENV_GRAPHIFY_TIMEOUT
            except Exception:  # noqa: BLE001
                name = None
            if name:
                self._setenv(name, str(self.budget))
        if not _accepts(sync.sync, "prune_policy", "unattended"):
            return _End("failed:error", 1, effects=["+1"], suggest="refresh",
                        reason="this DeepMap build's sync cannot run a background refresh (it predates auto-sync)")
        rev = self._graph_rev_pass()
        self._set_current("syncing")
        self.veto = None
        before = _read_manifest(self.root)
        if not _background_wrote(before, self.state, self.bg_ident):
            # written by a sync that did NOT run in the background (or none yet): its wall is what the time limit
            # reads — kept (autosync.json attended_wall) before this pass rewrites the manifest with its own
            self.attended = {"wall_s": _num((before or {}).get("sync_wall_s")),
                             "built_at_utc": (before or {}).get("built_at_utc")}
        ctl = st.UnattendedControl(self.root, trigger=self.trigger, session=self.session, prune_policy=self.policy,
                                   pre_write=self._pre_write, before_embed=self._before_embed,
                                   on_hard_exit=self._on_hard_exit)
        self.ctl = ctl
        t0 = _now()
        try:
            ok = sync.sync(self.root, cleanup_stale=False, prune_policy=self.policy, unattended=ctl)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            if _is_db_error(exc):
                return _unreachable("unreachable:during", detail=_clip(f"{type(exc).__name__}: {exc}", 200))
            return _End("failed:error", 1, effects=["+1"], suggest="refresh",
                        reason=_clip(f"the sync raised {type(exc).__name__}: {exc}"))
        finally:
            try:
                ctl.end()
            except Exception:  # noqa: BLE001
                pass
            self._note_written(before, t0)
        end = self._map(ok)
        if rev is not None:
            end.fields["graph_rev"] = rev           # last_run.graph_rev: what this pass rebuilt for
            if end.outcome == "synced":
                self.rev_done = max(self.rev_done or 0, rev)
        return end

    def _graph_rev_pass(self):
        """The graph revision this pass rebuilds for, said once in the log: the sources did not change (freshness
        "graph-rev") and the manifest is still due for it (sync_status.graph_rev_due). None for any other pass, and
        for R16's extra pass once the first one recorded the current revision. Never raises."""
        try:
            if (self.F or {}).get("why") != "graph-rev":
                return None
            m = _read_manifest(self.root) or self.manifest
            rev = _graph_rev_due(m)
            if rev is not None:
                note = _graph_rev_note(m) or f"this graph was built before graph revision {rev}"
                _log(f"[deepmap auto-sync] the sources did not change, but {note}; rebuilding it once")
            return rev
        except Exception:  # noqa: BLE001
            return None

    def _note_written(self, before, t0) -> None:
        """After this run's sync returned: when it rewrote the manifest — another identity, captured inside this call
        (edits_seen_through_epoch within [t0, now], CAPTURE_SLACK_S either side; a sync the user started that this
        one only queued behind captured before t0) — remember that manifest as a background pass's (autosync.json
        background_manifest), so its below-normal wall is never the one the time limit reads. Never raises."""
        try:
            after = _read_manifest(self.root)
            ident = _manifest_identity(after)
            if ident is None or ident == _manifest_identity(before):
                return
            cap = _num(after.get("edits_seen_through_epoch"))
            if cap is not None and t0 - CAPTURE_SLACK_S <= cap <= _now() + CAPTURE_SLACK_S:
                self.bg_ident = ident
        except Exception:  # noqa: BLE001
            pass

    def _last_fields(self, lo: dict) -> dict:
        return {"sync_id": lo.get("id"), "verdict": lo.get("verdict"), "reasons": list(lo.get("reasons") or [])[:3],
                "built_at_sha": lo.get("built_at_sha"), "dirty": lo.get("dirty"), "stale_kept": lo.get("stale_kept"),
                "embeddings_deferred": bool(lo.get("embeddings_deferred")),
                "fingerprint": lo.get("source_fingerprint"), "took_s": _num(lo.get("wall_s")),
                "head": lo.get("built_at_sha") or (self.F or {}).get("head") or self.head}

    def _map(self, ok) -> _End:
        """sync.last_outcome -> the runner outcome, exit code and backoff effect (spec §2.9.1)."""
        lo = getattr(self.sync, "last_outcome", None)
        lo = lo if isinstance(lo, dict) else {}
        o = str(lo.get("outcome") or "")
        f = self._last_fields(lo)
        reason = _clip(lo.get("reason"))
        verdict = str(lo.get("verdict") or "").upper()
        rk = lo.get("refusal_kind")
        if not o:                                   # an older / partial sync module: read its other globals
            if not ok:
                return _End("failed:refused", 1, effects=["+1"], suggest="refresh", **{**f, "reason": reason or
                            "the sync refused or failed (its output is in .deepmap/autosync.log)"})
            nps = getattr(self.sync, "last_not_pushed", None)
            if isinstance(nps, str) and nps.strip():
                return _End("not-pushed:other", 4, suggest="refresh", advice="", **{**f, "reason": _clip(nps)})
            if str(getattr(self.sync, "last_verdict", "") or "").upper() == "FAILED":
                return _End("failed:verdict", 1, effects=["+1"], suggest="refresh",
                            **{**f, "reason": "the sync's verdict is FAILED"})
            return _End("synced", 0, effects=["reset"], **f)
        if o == "synced":
            if verdict == "FAILED":
                return _End("failed:verdict", 1, effects=["+1"], suggest="refresh", **{**f, "reason": reason or
                            "the refresh produced no usable graph (verdict FAILED)"})
            effects = ["reset"] + (["pause-timeout"] if lo.get("engine_timed_out") else [])
            return _End("synced", 0, effects=effects, **f)
        if o == "failed" or (o == "refused" and rk == "index-failure"):
            if rk == "index-failure":
                return _End("failed:index", 1, effects=["+1"], suggest="refresh",
                            **{**f, "reason": reason or "the graph's indexes could not be created"})
            return _End("failed:verdict", 1, effects=["+1"], suggest="refresh",
                        **{**f, "reason": reason or "the refresh produced no usable graph"})
        if o == "queued":
            return _End("queued", 0, effects=["reset"], **f)
        if o == "preempted":
            return _End("preempted", 0, effects=["reset"], **f)
        if o == "not-pushed":
            kind = lo.get("not_pushed_kind")
            if kind == "engine-downgrade":
                if lo.get("engine_timed_out"):
                    more = self._more_time()
                    return _End("not-pushed:engine-timeout", 4, effects=["pause-timeout"], suggest="refresh",
                                advice=(f" (a refresh you start is not limited by the background time budget; if it "
                                        f"also runs out of time, add {more})"), **{**f, "reason": reason})
                return _End("not-pushed:engine-downgrade", 4, effects=["+1"], suggest="refresh", advice="",
                            **{**f, "reason": reason})
            if kind == "busy-instance":
                return _End("not-pushed:busy-instance", 4, suggest="refresh",
                            advice=" once the other project's sync finishes", **{**f, "reason": reason})
            if kind == "veto":
                v = str(lo.get("veto") or "")
                detail = self.veto if isinstance(self.veto, dict) else {}
                if v == "git-busy":
                    op = detail.get("op")
                    r, _s = _why("git-busy", op=_GIT_OPS.get(op, op or "operation"))
                    return _End("not-pushed:git-busy", 4, suggest=None, advice="", **{**f, "reason": r})
                if v.startswith("recipe:"):
                    r, s = _why(v, a=detail.get("a", "?"), b=detail.get("b", "?"))
                    return _End(f"not-pushed:{v}", 4, effects=["+1"], suggest=s or "refresh", advice="",
                                **{**f, "reason": r or reason})
                return _End("not-pushed:veto", 4, suggest="refresh", advice="", **{**f, "reason": reason or v})
            return _End(f"not-pushed:{kind or 'other'}", 4, suggest="refresh", advice="", **{**f, "reason": reason})
        if o == "busy-unqueued":
            return _End("not-pushed:busy-lock", 4, suggest="refresh", advice="", **{**f, "reason": reason})
        if o == "busy-enrich":
            r, s = _why("busy-enrich")
            return _End("skipped:busy-enrich", 4, suggest=s, **{**f, "reason": r})
        if o == "user-request-pending":             # sync never runs (nor claims) a re-run a user's sync queued
            r, s = _why("user-request")
            return _End("skipped:user-request", 4, suggest=s, why_code="user-request", **{**f, "reason": r})
        if o == "refused":
            if rk == "unowned":                     # sync never adopts a graph it cannot prove is this project's
                r, s = _why("unowned")
                return _End("skipped:unowned", 4, suggest=s, why_code="unowned", **{**f, "reason": r})
            if rk in _FOREIGN_KINDS:
                return _End("skipped:foreign-owner", 4, suggest="setup", **{**f, "reason": reason})
            return _End("failed:refused", 1, effects=["+1"], suggest="refresh",
                        **{**f, "reason": reason or "the sync refused to run"})
        if o == "route-problem":
            return _End("failed:refused", 1, effects=["+1"], suggest="setup",
                        **{**f, "reason": reason or "this project's database route could not be resolved"})
        if o == "error":
            if _num(lo.get("rc")) == 3:
                r, s = _why("unreachable:during", detail=_clip(reason or "a database error", 200))
                return _End("unreachable", 3, suggest=s, why_code="unreachable:during", **{**f, "reason": r})
            return _End("failed:error", 1, effects=["+1"], suggest="refresh",
                        **{**f, "reason": reason or "the sync failed"})
        if o == "preempt-timeout":                  # a user's sync's own wait — never an unattended one's
            return _End("not-pushed:busy-lock", 4, suggest="refresh", advice="", **{**f, "reason": reason})
        if not ok:
            return _End("failed:refused", 1, effects=["+1"], suggest="refresh",
                        **{**f, "reason": reason or f"the sync ended with outcome {o}"})
        return _End(f"not-pushed:{o}", 4, suggest="refresh", advice="", **{**f, "reason": reason})

    def _more_time(self) -> str:
        try:
            ge = _load_graphify()
            name, cap = ge.ENV_GRAPHIFY_TIMEOUT, int(ge.GRAPHIFY_TIMEOUT_MAX_S)
        except Exception:  # noqa: BLE001
            return "a larger graphify time budget"
        used = int(self.budget or 0)
        return f"{name}={min(cap, 2 * used) if used else 3600}"

    # -- the callbacks sync calls on the control
    def _pre_write(self, engine, extraction):
        """Before anything is written to Neo4j: a git operation started meanwhile, or the engine that actually ran
        would change the graph's recipe -> veto the push (NOT PUSHED; graph and manifest kept). Never raises."""
        try:
            op = self.sync.git_busy(self.root)
            if op:
                self.veto = {"op": op}
                return "git-busy"
            m = _read_manifest(self.root) or {}
            if _prior_engines(m) == ["ast"] and engine == "graphify":
                self.veto = {"a": "ast", "b": "graphify"}
                return "recipe:engine"
            info = extraction if isinstance(extraction, dict) else {}
            gv = info.get("graphify_version")
            if engine == "graphify" and gv:
                built_v = _recipe(m).get("graphify_version") or m.get("graphify_version")
                if not _engine_supported(gv) or (built_v and _version_older(gv, built_v)):
                    self.veto = {"a": built_v or "version", "b": gv}
                    return "recipe:engine-version"
        except Exception as exc:  # noqa: BLE001
            _log(f"[deepmap auto-sync] pre-write check failed: {type(exc).__name__}: {exc}")
        return None

    def _before_embed(self) -> bool:
        """Right before the embedding model loads: memory at or above the floor -> True; else wait (<= 120 s) for
        it; still low -> False (sync postpones the embeddings). Never raises."""
        try:
            avail, total = _memory()
            if avail is None or avail >= _mem_floor(total):
                return True
            deadline = _now() + EMBED_MEM_WAIT_S
            while _now() < deadline:
                _sleep(EMBED_MEM_POLL_S)
                avail, total = _memory()
                if avail is None or avail >= _mem_floor(total):
                    return True
            _log(f"[deepmap auto-sync] memory stayed below {_gb(_mem_floor(total))} — embeddings postponed")
            return False
        except Exception:  # noqa: BLE001
            return True

    def _on_hard_exit(self, record) -> None:
        """The control is about to end the process (a sync the user started is waiting, nothing written yet): record
        `preempted` now — or, when only deepmap enrich waited (its record says busy-enrich: enrich never refreshes the
        graph), `skipped:busy-enrich` rc 4, as _map records the same yield. Must not raise; no waits."""
        try:
            rec = record if isinstance(record, dict) else {}
            if rec.get("outcome") == "busy-enrich":
                r, s = _why("busy-enrich")
                end = _End("skipped:busy-enrich", 4, suggest=s, **{**self._last_fields(rec), "reason": r})
            else:
                end = _End("preempted", 0, effects=["reset"], **self._last_fields(rec))
            self._finish(end, from_watcher=True)
        except Exception:  # noqa: BLE001
            pass


def _engine_supported(version) -> bool:
    """Is graphify `version` inside DeepMap's supported range? True when the range cannot be read (never a guess
    that blocks)."""
    try:
        ge = _load_graphify()
        t = ge._version_tuple(version)
        return t is not None and ge.SUPPORTED_GRAPHIFY_MIN <= t < ge.SUPPORTED_GRAPHIFY_BELOW
    except Exception:  # noqa: BLE001
        return True


def _version_older(a, b) -> bool:
    """Is version `a` older than `b` (numeric release parts)? False when either is unreadable."""
    try:
        ge = _load_graphify()
        ta, tb = ge._version_tuple(a), ge._version_tuple(b)
        return ta is not None and tb is not None and ta < tb
    except Exception:  # noqa: BLE001
        return False


def _queue_rerun(sync, root, policy, trigger) -> bool:
    """sync's own re-run queue, as an UNATTENDED request (cleanup_stale stays false; the policy rides in its own
    field — H4). Never raises."""
    try:
        if _accepts(sync._request_rerun, "prune_policy", "origin", "trigger"):
            return bool(sync._request_rerun(root, cleanup_stale=False, force_prune=False, prune_policy=policy,
                                            origin="unattended", trigger=trigger))
        return bool(sync._request_rerun(root, cleanup_stale=False, force_prune=False))
    except Exception:  # noqa: BLE001
        return False


def run(target, *, trigger: str = "session-start", session=None, env=None) -> int:
    """The unattended background refresh of the project at `target` (R1-R17 in the module docstring). Returns the
    exit code (0 / 1 / 3 / 4; 2 for an unknown trigger). `env` is the mapping the policy reads (default os.environ);
    the offline variables, the replayed recipe and the engine budget always go into os.environ too — sync reads it.
    Never raises (an unexpected error is recorded as failed:error)."""
    e = os.environ if env is None else env
    started = _now()
    for name, value in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("GIT_OPTIONAL_LOCKS", "0"),
                        ("PYTHONUTF8", "1")):
        os.environ[name] = value
        if e is not os.environ:
            try:
                e[name] = value
            except Exception:  # noqa: BLE001
                pass
    _lower_priority()
    root = _project_root(target)
    _log(f"[deepmap auto-sync] {_utc(started)} start trigger={trigger} root={root} pid={os.getpid()} "
         f"python={_python_xy()} HF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE')} "
         f"TRANSFORMERS_OFFLINE={os.environ.get('TRANSFORMERS_OFFLINE')} "
         f"GIT_OPTIONAL_LOCKS={os.environ.get('GIT_OPTIONAL_LOCKS')}")
    r = _Runner(root, trigger, session, e, started)
    if trigger not in TRIGGERS:
        return r._silent("usage", 2, f"unknown trigger {trigger!r} (known: {', '.join(TRIGGERS)})")
    try:
        return r.go()
    except Exception as exc:  # noqa: BLE001 — the runner never raises into the CLI
        traceback.print_exc(file=sys.stderr)
        return r._silent("failed:error", 1, f"{type(exc).__name__}: {exc}")


# ---- status / switches -----------------------------------------------------------------------------------------------
def _live(st, rec):
    try:
        return st.holder_live(rec) if st is not None else None
    except Exception:  # noqa: BLE001
        return None


def _off_text(root, e):
    """status_line's (level, text) when auto-sync is off for `root` (a switch, or DEEPMAP_HOOKS off: the session-start
    refresh never starts), else None."""
    on, src = enabled(root, "status", e)
    if not on:
        if src == "env":
            return "OK", f"off ({ENV_AUTO_SYNC}={str(e.get(ENV_AUTO_SYNC, '')).strip()})"
        if src == "instance":
            raw = _instance_raw(root)
            value = raw.get("enabled") if isinstance(raw, dict) else raw
            shown = "auto_sync.enabled" if isinstance(raw, dict) else "auto_sync"
            if _switch_word(value) == "bad":
                return "WARN", (f"off (.deepmap/instance.json {shown}={_clip(json.dumps(value), 60)} is not "
                                f"understood, so DeepMap reads it as off — `python build/auto_sync.py enable` "
                                f"removes it)")
            return "OK", (f"off (.deepmap/instance.json {shown}=false)" if shown == "auto_sync"
                          else "off (.deepmap/instance.json auto_sync.enabled=false)")
        if src == "machine":
            state, path = _machine_switch(e)
            if state == "bad":
                return "WARN", (f"off (machine switch {path} is not understood, so DeepMap reads it as off; write "
                                f'{{"enabled": false}} there to keep it off, or delete the file)')
            return "OK", f"off (machine switch {path})"
        return "OK", f"off ({src})"
    if _env_off(e, ENV_HOOKS):
        return "OK", (f"off ({ENV_HOOKS}={str(e.get(ENV_HOOKS, '')).strip()} — every DeepMap hook is off, so "
                      f"the session-start refresh never starts)")
    return None


def status_line(root, env=None) -> tuple:
    """(level "OK" | "WARN", text) — the auto-sync line of `deepmap status` / `doctor` (spec §2.10.6). Informational;
    never raises. Off -> why; running -> since when and what it does; paused -> WARN. Otherwise it asks
    precheck(root, trigger="status", announce=True) what the next session start would do: a refusal (too-slow,
    recipe:settings, recipe:older-code, remote-database, backoff, a recorded skip that repeats, ...) is a WARN "on —
    but the next session start will not refresh: <reason>"; a cooldown is not one. Else the last run (WARN when it
    failed, was not pushed or found the database down; a pass that rebuilt for a graph revision says so). A graph
    due for a graph revision (sync_status.graph_rev_note) gets one more sentence after any of these, the off texts
    too: what changed, and what rebuilds it: the next background refresh, once (this one, while it runs); else
    /deep-map:refresh (auto-sync off, a next session start that will not refresh, or the one-time rebuild already
    made: _rebuilt_already, the runner's once rule); the level is never changed by it."""
    e = os.environ if env is None else env
    try:
        manifest = _read_manifest(root)
        rev_note = _graph_rev_note(manifest) if manifest else None     # None unless due for a graph revision
        by_refresh = f"; {rev_note}, and /deep-map:refresh rebuilds it" if rev_note else ""
        off = _off_text(root, e)
        if off is not None:
            return off[0], off[1] + by_refresh
        try:
            st = _load_status()
        except Exception:  # noqa: BLE001
            st = None
        state = read_state(root)
        now = _now()
        again = _rebuilt_already(state, manifest) if rev_note else None
        cur = state.get("current") if isinstance(state.get("current"), dict) else None
        if cur and _live(st, cur) is True:
            phase = cur.get("state") or "checking"
            if phase == "syncing" and st is not None:
                try:
                    h = st.read_running(root)
                    if isinstance(h, dict) and h.get("live") is True and h.get("origin") == "unattended":
                        phase = h.get("phase") or phase
                except Exception:  # noqa: BLE001
                    pass
            running = f"on — running since {_utc_fmt(cur.get('started_epoch'), '%H:%M')} UTC ({_phase_text(phase)})"
            if rev_note and again is None:
                return "OK", running + f"; {rev_note}, which this refresh rebuilds"
            return "OK", running + by_refresh
        try:
            last_sync = st.read_last(root) if st is not None else None
        except Exception:  # noqa: BLE001
            last_sync = None
        kind, _lifted = _failure_block(state, last_sync, now)
        if kind == "paused":
            p = state.get("paused") if isinstance(state.get("paused"), dict) else {}
            if p.get("reason") == "engine-timeout":
                why = f"the background refresh ran out of its time budget ({p.get('detail') or 'its time budget'})"
            else:
                n = int(_num(state.get("consecutive_failures")) or PAUSE_AFTER_FAILURES)
                why = (f"the last {n} background refreshes did not complete (latest: "
                       f"{_clip(p.get('detail') or 'unknown', 160)})")
            return "WARN", f"paused — {why}; a successful /deep-map:refresh resumes it" + by_refresh
        lr = state.get("last_run") if isinstance(state.get("last_run"), dict) else None
        # what the next session start would do (precheck with announce: the repeat rule too); a refusal is a WARN
        pc = precheck(root, trigger="status", env=e, now=now, announce=True)
        code = pc.get("code")
        if code == "not-set-up":
            if not lr:
                return "OK", ("on — no background refresh has run yet (it runs at session start once /deep-map:setup "
                              "has built this project's graph)")
            return "OK", ("on — but nothing runs at session start until /deep-map:setup builds this project's graph "
                          "again (.deepmap/graph_build.json is missing)")
        if code == "empty":
            return "OK", ("on — but a session start does not refresh this graph: its last build produced nothing "
                          "usable (/deep-map:setup rebuilds it)")
        if pc.get("ok") is not True and code not in _STATUS_NOT_BLOCKS:
            text = f"on — but the next session start will not refresh: {pc.get('reason') or code}"
            if pc.get("suggest"):
                text += f"; suggested: /deep-map:{pc.get('suggest')}"
            return "WARN", text + by_refresh
        once = f"; {rev_note}, so DeepMap's next background refresh rebuilds it once" if rev_note else ""
        if again is not None:                      # the one-time rebuild was made: never promise it again
            once = by_refresh
            if not lr or lr.get("outcome") != "skipped:graph-rev-again":     # that skip's reason says why already
                why = _why("graph-rev-again", **_again_kw(manifest, again))[0]
                once = f"; {rev_note}; {why}; /deep-map:refresh rebuilds it"
        if not lr:
            return "OK", "on — no background refresh has run yet (it runs when a Claude Code session starts)" + once
        outcome = str(lr.get("outcome") or "?")
        parts = [f"{lr.get('trigger') or 'session-start'}, {outcome}"]
        if _num(lr.get("wall_s")) is not None:
            parts.append(human_duration(lr.get("wall_s")))
        if lr.get("verdict"):
            parts.append(f"verdict {lr.get('verdict')}")
        grev = lr.get("graph_rev")
        if outcome == "synced" and isinstance(grev, int) and not isinstance(grev, bool) and grev > 0:
            parts.append(f"a one-time rebuild for graph revision {grev}")
        text = f"on — last run {_utc_fmt(lr.get('finished_epoch'), '%Y-%m-%d %H:%M')} UTC: " + ", ".join(parts)
        if lr.get("reason") and outcome not in ("synced", "fresh", "covered", "queued"):
            text += f"; {lr.get('reason')}"
        level = "WARN" if outcome.startswith(_WARN_PREFIXES) else "OK"
        return level, text + once
    except Exception as exc:  # noqa: BLE001
        return "WARN", f"could not read the auto-sync state ({type(exc).__name__})"


def set_enabled(root, enabled: bool, *, machine: bool = False) -> tuple:
    """Switch auto-sync off (or back to its default) for project `root` — instance.json auto_sync.enabled through
    instance_state.update_instance (the one locked writer) — or, with `machine`, for every project on this machine
    (<DEEPMAP_HOME>/auto-sync.json, written atomically; enabling removes it). Files only switch OFF, so "enabled"
    means "remove the switch". Returns (ok, message). Never raises."""
    try:
        note = ""
        if _env_off(os.environ, ENV_AUTO_SYNC) and enabled:
            note = f" (note: {ENV_AUTO_SYNC}={os.environ.get(ENV_AUTO_SYNC)} in this environment still keeps it off)"
        elif _env_on(os.environ, ENV_AUTO_SYNC) and not enabled:
            note = f" (note: {ENV_AUTO_SYNC}={os.environ.get(ENV_AUTO_SYNC)} in this environment still turns it on)"
        if machine:
            path = _home() / MACHINE_SWITCH
            if enabled:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return True, f"auto-sync: the machine switch {path} is removed — each project's own setting applies{note}"
            ok = _load_status().atomic_write_json(path, {"schema": SCHEMA, "enabled": False})
            return (ok, f"auto-sync turned off for every project on this machine ({path}){note}" if ok
                    else f"auto-sync: could not write {path}")

        def mutate(data):
            d = dict(data or {})
            raw = d.get("auto_sync")
            # a bare value is the switch itself ({"auto_sync": false} reads as off): edit it as auto_sync.enabled
            block = dict(raw) if isinstance(raw, dict) else ({} if raw is None else {"enabled": raw})
            if enabled:
                if "enabled" not in block:
                    return None                       # nothing to change
                block.pop("enabled", None)
            else:
                if block.get("enabled") is False:
                    return None
                block["enabled"] = False
            if block:
                d["auto_sync"] = block
            else:
                d.pop("auto_sync", None)
            return d

        _path_build()
        path, status = importlib.import_module("instance_state").update_instance(Path(root), mutate)
        word = "on (DeepMap's default)" if enabled else "off for this project (.deepmap/instance.json)"
        if path is None and status != "skipped":
            return False, f"auto-sync: could not update .deepmap/instance.json ({status})"
        return True, f"auto-sync {word}{note}"
    except Exception as exc:  # noqa: BLE001
        return False, f"auto-sync: could not change the setting ({type(exc).__name__}: {exc})"


def main(argv=None) -> int:
    """`python build/auto_sync.py run <target> [--trigger session-start] [--session SID]` (the runner, under THIS
    interpreter — production starts it through `deepmap_cli.py sync <root> --unattended=session-start`), `status
    [root]`, `precheck [root] [--source S] [--head SHA] [--announce]` (JSON), `enable|disable [root] [--machine]`."""
    import argparse
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="auto_sync", description="DeepMap's background refresh (auto-sync).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run the unattended background refresh of a project")
    p.add_argument("target")
    p.add_argument("--trigger", default="session-start", choices=TRIGGERS)
    p.add_argument("--session", default=None)
    p = sub.add_parser("status", help="print the auto-sync status line")
    p.add_argument("root", nargs="?", default=".")
    p = sub.add_parser("precheck", help="print precheck()'s answer as JSON")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--source", default=None)
    p.add_argument("--head", default=None)
    p.add_argument("--announce", action="store_true",
                   help="the hooks' question (repeating skips and the cooldown even without --head)")
    for name in ("enable", "disable"):
        p = sub.add_parser(name, help=f"{name} auto-sync for a project (or, with --machine, this machine)")
        p.add_argument("root", nargs="?", default=".")
        p.add_argument("--machine", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return run(args.target, trigger=args.trigger, session=args.session)
    if args.cmd == "status":
        level, text = status_line(_project_root(args.root))
        print(f"[{level}] auto-sync: {text}")
        return 0
    if args.cmd == "precheck":
        print(json.dumps(precheck(_project_root(args.root), source=args.source, head=args.head,
                                  announce=args.announce)))
        return 0
    ok, msg = set_enabled(_project_root(args.root), args.cmd == "enable", machine=args.machine)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
