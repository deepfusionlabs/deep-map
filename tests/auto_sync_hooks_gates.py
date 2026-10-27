#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto-sync (SessionStart background catch-up), the HOOKS slice: what DeepMap's hooks start, say and record.

DeepMap refreshes a project's graph by itself when an interactive Claude Code session starts (build/auto_sync.py, run as
`deepmap_cli.py sync <root> --unattended=session-start`). The hooks are its only trigger and its only voice:
  A  hooks.json registers the new PostToolUse hook (deepmap_toolresult_freshness) on the deepmap tools — matcher
     `mcp__.*deepmap.*__deepmap_(search|impact|cypher)`, the `python … ; python3 … ; exit 0` form, timeout 10; and
     importing deepmap_hook_state loads no socket / selectors / subprocess (every hook's start cost).
  B  the SessionStart heal hook starts the heal FIRST, then the runner — exact argv (incl. --session), offline env
     without optional git locks and without __PYVENV_LAUNCHER__, Windows CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
     BELOW_NORMAL (0x08004200, never the DETACHED 0x8 bit) / POSIX start_new_session, output appended to
     .deepmap/autosync.log with a header, .deepmap/autosync_spawn.json; NO runner on compact / DEEPMAP_AUTO_SYNC=0 /
     the instance flag / the machine switch / DEEPMAP_HOOKS=0 / an unattended session / no marker / a proven-empty
     graph / a paused auto-sync; DEEPMAP_HEAL=0 -> the runner only; a runner that cannot start leaves the heal and
     exit 0; the heal hook runs no git.
  B2 two overlapping session starts (REAL children, the hook's own opener + _spawn): autosync.log and heal.log keep
     both headers and every line of both children (on Windows the handle is append-only: Python's "ab" appends only
     through the C runtime, and an inherited handle wrote at its own position, over the other session's lines); with
     no append-only handle the log still opens, appending.
  C  the SessionStart notify: the exact AUTO_STALE / AUTO_EDITED (dirty and edited) / AUTO_STALE_CAUTION texts,
     STALE_KEPT_NOTE (auto-sync on AND off), session_health.auto; today's texts verbatim under a cooldown, a git
     operation in progress, DEEPMAP_AUTO_SYNC=0, an unattended session, a paused auto-sync, and DEEPMAP_HEAL=0 with
     this project's database down (with it up: the AUTO text).
  C2 outside git the notify asks auto_sync.precheck with announce=True (head None): inside the runner's cooldown and
     after a recorded skip it would repeat it announces nothing (THIS session's own runner start still announces);
     a precheck without the keyword (an older build/) is asked the old way.
  D  deepmap_hook_state.freshness_state / staleness honour the background check's "fresh" verdict only with the
     manifest's fingerprint, the current HEAD and a newer record (and only while auto-sync is on); the hook-only
     "edited" kind; git_busy for every in-progress marker; GIT_OPTIONAL_LOCKS=0 in every git the hooks run.
  E  the follow-ups, through BOTH the pre-search nudge and the tool-result hook (one told-state): running (only for a
     LIVE unattended holder), synced (+ dirty / DEGRADED / kept / deferred), fresh / preempted (only when told auto),
     not-pushed, failed, skipped / unreachable (only when told auto; never foreign-owner / setup-blocked), nothing for
     queued / covered or for an outcome older than the session, not-started after 120 s — each ONCE per session, and
     silent under a policy refusal, setup's blocker, a foreign told, DEEPMAP_HOOKS=0, DEEPMAP_FRESHNESS_NOTE=0 (on the
     nudge too: the rule itself honours it), or auto-sync off; the nudge's reminder carries AUTO_RUNNING_NOTE while a
     refresh runs.
  E6 outside git (no recorded commit) every follow-up says "answer from a previous build", never "the build at a
     previous build".
  F  the edit hook: a source file inside the project -> AUTO_SYNC_TEXT (+ the commit clause only with this repo's
     DeepMap git hook) and .deepmap/last_source_edit.json; .md / outside the project -> silent, nothing recorded;
     the next session start will NOT refresh (too slow, paused, backing off, DEEPMAP_HEAL=0 with the database down,
     a recorded skip it would repeat — needs-timing / recipe:route / no-own-instance, where the notify announces
     nothing either) -> today's SYNC_TEXT for a source edit, still recorded; the skip lifted (manifest rewritten,
     sync time recorded) -> AUTO_SYNC_TEXT; auto-sync off -> today's SYNC_TEXT on any edit, no new file.
  G  every hook (all six) exits 0 with no stdout on a garbage payload, and stays well-formed on unreadable records and
     on a plugin whose build/ is empty (then: exactly today's behaviour).
  H  auto-sync OFF -> every hook's stdout and every file it writes are exactly today's (literal texts + the legacy
     session_health key set + no auto-sync record).
  J  the quiet stamp (latency): a call with nothing new to hear is stamped (.deepmap/autosync_quiet.json: this session +
     cwd, this plugin's build/, the five records' signatures, a time limit for the not-started wait and for a live
     process), and the next tool-result call exits before importing json / pathlib / deepmap_hook_state / build/;
     every rewritten record, a passed time limit, another plugin, another cwd or a corrupt stamp falls back to the
     rule (each change still heard, once); auto-sync off writes no stamp; a session that started with auto-sync off
     loads no auto-sync module; the hook's pre-import copy of the check agrees with deepmap_hook_state.quiet_holds;
     the nudge after its reminder loads no auto-sync module either; a told call loads sync_status only when a sync
     holds the lock.
  I  the cross-slice CLI contract: the runner argv the heal hook really builds is RUN against this tree's real
     build/deepmap_cli.py (auto-sync off, temp home, a closed NEO4J_URI port): rc 4 exactly when
     deepmap_hook_state.cli_takes_unattended says the CLI takes --unattended, never 2 then; and with that real CLI in
     the plugin the hooks start / announce / follow up a background refresh only when it does — else exactly today.

Hermetic: a temp plugin tree (the tree's hooks/ and build/, with stub provision.py / deepmap_cli.py — the CLI stub carries
the --unattended contract text; section I alone runs the tree's real CLI, with auto-sync off; build/auto_sync.py
and build/sync_status.py are the REAL ones when the tree has them, else fakes implementing exactly the spec's §2.3/§2.4
signatures — re-run after the merge), a temp DEEPMAP_HOME, temp git repos, explicit CLAUDE_CODE_SESSION_ATTENDED per
case. The hooks run as hooks.json runs them (a fresh `python -X utf8 <hook>` with the payload on stdin) under a
sitecustomize recorder: subprocess.Popen starts NOTHING (it records argv + kwargs) and subprocess.run is logged, then
run. No Neo4j, no network, never ~/.deepmap, and no real runner beyond section I's one CLI run with auto-sync off (the
runner answers "disabled" before it does anything); section B2's children are `python -c` line printers it waits for.
Usage: python tests/auto_sync_hooks_gates.py [--root <tree>]   (--root: load hooks/ + build/ from another tree — the
       RED-first run against the pre-change snapshot)
"""
from __future__ import annotations

import itertools
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
_WIN = os.name == "nt"
PASSES: list = []
FAILS: list = []
_LOCK = threading.Lock()
_BUF = threading.local()   # each section runs in its own thread and prints its lines in order at the end

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
BELOW_NORMAL = 0x00004000
DETACHED_PROCESS = 0x00000008


def check(name: str, ok: bool, detail="") -> None:
    with _LOCK:
        (PASSES if ok else FAILS).append(name)
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}")
    say(line)


def say(line: str) -> None:
    buf = getattr(_BUF, "lines", None)
    if buf is None:
        print(line)
    else:
        buf.append(line)


def _root_arg(argv: list) -> Path:
    """The tree whose hooks/ + build/ are tested. A --root without a value, or naming a tree without both, is a usage
    error (exit 2) — never a silent fallback to this checkout (a RED-first run would then report GREEN)."""
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "hooks").is_dir() or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/auto_sync_hooks_gates.py [--root <tree holding hooks/ and build/>] (got {tree!r})",
              file=sys.stderr)
        raise SystemExit(2)
    return Path(tree).resolve()


# =========================================================================== the spec's texts (verbatim, §2.10)
# Today's texts (auto-sync off must keep them exactly).
ANNOUNCE_TEXT = (
    "DeepMap is ACTIVE for this project (code graph built; last sync verdict READY). For "
    "intent/behaviour questions (\"where is X handled\", \"what computes Y\") prefer the "
    "deepmap_search MCP tool over raw grep; for who-calls / blast-radius (\"what breaks if I "
    "change X\") use deepmap_impact — it traverses runtime/framework edges (routes, DI, Rails "
    "cascades) a grep can't see. When DELEGATING code investigation to subagents (Task tool), "
    "include in the subagent's prompt that the deepmap_search/deepmap_impact MCP tools are "
    "available and preferred — subagents see the tools but not this note. If the tools are not in "
    "your tool list, the user starts a new Claude Code session (a reload) and approves the two deepmap "
    "servers if Claude Code asks."
)
STALE_SUFFIX = (
    " NOTE: the graph was built at {built}, but HEAD is now {head}{distance} — the graph may be stale; "
    "suggest /deep-map:refresh before relying on impact/blast-radius answers."
)
STALE_CAUTION_TEXT = (
    "DeepMap note for this project: its code graph was built at {built}, but HEAD is now {head}{distance} — "
    "the graph may be stale, and {health}. If you use deepmap_search/deepmap_impact here, confirm what they "
    "return by reading the code (anything added or changed since that build may be missing, so an empty "
    "answer does not mean the code is absent), and suggest the user run /deep-map:refresh."
)
HEALTH_LEGACY = "it predates DeepMap's per-sync health verdict, so its coverage is unknown"
NUDGE_TEXT = (
    "DeepMap is set up for this project. For intent/behaviour questions "
    "(\"where is X handled\", \"what computes Y\") deepmap_search searches the code graph by meaning; "
    "for who-calls / blast-radius use deepmap_impact. Grep/Glob stays right for a known exact string "
    "or filename."
)
NUDGE_STALE_NOTE = (
    " NOTE: the graph was built at {built}, but HEAD is now {head}{distance} — it may be stale; suggest "
    "/deep-map:refresh before relying on impact/blast-radius answers."
)
SYNC_TEXT = (
    "Source files changed since DeepMap's last graph build — deepmap_search/deepmap_impact reflect "
    "the code as of the last sync and may be stale for what you just edited. Run /deep-map:refresh to "
    "rebuild."
)
LEGACY_HEALTH_KEYS = {"start", "told", "servers_down", "heal_path", "seen", "resolved", "restart_told"}

# §2.10.1
AUTO_STALE_SUFFIX = (
    " NOTE: the graph was built at {built}, but HEAD is now {head}{distance}. DeepMap is refreshing it in the "
    "background now (low CPU priority{took}), so there is no need to run /deep-map:refresh. Until a later DeepMap note "
    "says it finished, deepmap_search/deepmap_impact answer from the previous build — code added or changed since "
    "then may be missing, so an empty answer does not mean the code is absent — and while the refresh writes the "
    "graph, results may briefly mix old and new code. Confirm recently changed code by reading it."
)
AUTO_EDITED_SUFFIX = (
    " NOTE: source files may have changed since the graph was built ({what}). DeepMap is checking and, if they did, "
    "refreshing the graph in the background now (low CPU priority{took}), so there is no need to run "
    "/deep-map:refresh. Until a later DeepMap note says it finished, deepmap_search/deepmap_impact answer from the "
    "previous build, and while the refresh writes the graph, results may briefly mix old and new code. Confirm "
    "recently changed code by reading it."
)
AUTO_STALE_CAUTION_TEXT = (
    "DeepMap note for this project: its code graph was built at {built}, but HEAD is now {head}{distance} — the graph "
    "may be stale, and {health}. DeepMap is refreshing it in the background now (low CPU priority{took}). Until a "
    "later DeepMap note says it finished, if you use deepmap_search/deepmap_impact here, confirm what they return by "
    "reading the code (anything added or changed since that build may be missing, so an empty answer does not mean "
    "the code is absent; while the refresh writes the graph, results may briefly mix old and new code)."
)
STALE_KEPT_NOTE = (
    " NOTE: the graph still holds {n} node(s) for code that may have been removed since (DeepMap's background refresh "
    "deletes a node only when it re-read the node's file or the file is gone); if a result names a symbol you cannot "
    "find in the file, it is stale — /deep-map:refresh removes such nodes."
)
# §2.10.2
AUTO_RUNNING_NOTE = (
    " NOTE: DeepMap is refreshing this graph in the background right now; until a later DeepMap note says it finished, "
    "results come from the previous build (and may briefly mix old and new code while it writes) — confirm recently "
    "changed code by reading it."
)
# §2.10.3
FOLLOW_RUNNING = (
    "DeepMap note: a background refresh of this project's graph is running (started {ago} ago; now {phase}). Until a "
    "later DeepMap note says it finished, deepmap_search/deepmap_impact answer from the previous build{at} — code "
    "changed since then may be missing — and while the refresh writes the graph, results may briefly mix old and new "
    "code. Confirm recently changed code by reading it."
)
FOLLOW_SYNCED = (
    "DeepMap note: the background refresh finished {ago} ago — the graph now reflects {head}{dirty}. "
    "deepmap_search/deepmap_impact answers from now on include that code{degraded}. {kept}{deferred}"
)
SYNCED_DIRTY = " plus the uncommitted edits present when it started"
SYNCED_DEGRADED = " (its verdict is DEGRADED: {reason1})"
SYNCED_KEPT = (
    "It kept {n} node(s) for code that may since have been removed (a background refresh deletes a node only when it "
    "re-read the node's file or the file is gone); if a result names a symbol you cannot find in the file, it is "
    "stale — /deep-map:refresh removes such nodes."
)
SYNCED_DEFERRED = (
    " Meaning-based (vector) search does not cover the changed code yet — computing its embeddings was postponed "
    "because the machine was low on memory; keyword and graph results include it, and the next refresh adds the "
    "embeddings."
)
FOLLOW_FRESH = (
    "DeepMap note: the background check found no source change since the last build — the graph is current for "
    "{head}; deepmap_search/deepmap_impact answer from it."
)
FOLLOW_PREEMPTED = (
    "DeepMap note: the background refresh stopped to let a sync the user started (/deep-map:refresh or "
    "/deep-map:setup) run instead; that run reports its own result."
)
FOLLOW_NOT_PUSHED = (
    "DeepMap note: the background refresh did NOT update the graph — {reason}. deepmap_search/deepmap_impact still "
    "answer from {build}, which may be stale: confirm recently changed code by reading it, and suggest the user run "
    "/deep-map:refresh{advice}."
)
FOLLOW_FAILED = (
    "DeepMap note: the background refresh failed — {reason} (its output is in .deepmap/autosync.log). "
    "deepmap_search/deepmap_impact still answer from {build}; suggest the user run /deep-map:refresh, and "
    "/deep-map:doctor if that fails too."
)
FOLLOW_SKIPPED = (
    "DeepMap did not refresh this graph automatically: {why}. deepmap_search/deepmap_impact answer from "
    "{build}{distance}: confirm recently changed code by reading it{suggest}."
)
FOLLOW_NOT_STARTED = (
    "DeepMap note: the background refresh announced at the start of this session did not start. "
    "deepmap_search/deepmap_impact answer from {build}{distance}: confirm recently changed code by reading it, and "
    "suggest the user run /deep-map:refresh."
)
# {build} / {at}: the build the tools answer from — "the build at <sha7>" / " (at <sha7>)" when the manifest records
# its commit; "a previous build" / "" when it records none (a project outside git: "answer from a previous build")
NOGIT_BUILD = "a previous build"


def _b(sha) -> str:
    return f"the build at {sha[:7]}"


def _at(sha) -> str:
    return f" (at {sha[:7]})"
# §2.10.5
AUTO_SYNC_TEXT = (
    "Source files changed since DeepMap's last graph build ({file}) — deepmap_search/deepmap_impact do not include "
    "these edits yet. DeepMap refreshes the graph by itself in the background when a session starts (at most once "
    "every 15 minutes for the same commit){commit}; run "
    "/deep-map:refresh (in the background) only if you need search/impact to reflect these edits in this session."
)
AUTO_COMMIT = " and after each commit, through this repo's DeepMap git hook"
MATCHER = "mcp__.*deepmap.*__deepmap_(search|impact|cypher)"


def _synced_text(**kw) -> str:
    """FOLLOW_SYNCED with its optional parts; the one normalisation: runs of spaces collapse to one and the end is
    trimmed (the template's `{kept}{deferred}` tail leaves a double space when only `{deferred}` is present)."""
    return re.sub(r" {2,}", " ", FOLLOW_SYNCED.format(**kw)).rstrip()


# =========================================================================== fixtures
# §2.4 / §2.3-conformant fakes, used only while the tree under test has no build/auto_sync.py / build/sync_status.py.
FAKE_AUTO_SYNC = r'''"""auto_sync_hooks_gates FAKE of build/auto_sync.py — the file-only rules the hooks ask (auto-sync spec §2.4):
attended / enabled / precheck / human_duration / read_state. Never imports sync."""
import json
import os
import time
from pathlib import Path

COOLDOWN_S = 900
MAX_S_DEFAULT = 1200
BACKOFF_S = 6 * 3600
UNATTENDED_ENTRYPOINTS = ("sdk-cli", "sdk-ts", "sdk-py", "mcp", "claude-code-github-action", "remote_trigger",
                          "remote_cowork_trigger")
_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}
FAKE = True


def _e(env):
    return os.environ if env is None else env


def _read(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _home(env):
    h = _e(env).get("DEEPMAP_HOME")
    return Path(h) if h else Path.home() / ".deepmap"


def attended(env=None):
    e = _e(env)
    v = str(e.get("CLAUDE_CODE_SESSION_ATTENDED", "")).strip()
    if v == "1":
        return True
    if v == "0":
        return False
    return str(e.get("CLAUDE_CODE_ENTRYPOINT", "")).strip() not in UNATTENDED_ENTRYPOINTS


def enabled(root, trigger="session-start", env=None):
    try:
        e = _e(env)
        v = str(e.get("DEEPMAP_AUTO_SYNC", "")).strip().lower()
        if v in _ON:
            return (True, "env")
        if v in _OFF:
            return (False, "env")
        a = (_read(Path(root) / ".deepmap" / "instance.json") or {}).get("auto_sync")
        if isinstance(a, dict) and a.get("enabled") is False:
            return (False, "instance")
        if (_read(_home(e) / "auto-sync.json") or {}).get("enabled") is False:
            return (False, "machine")
        if trigger == "session-start" and not attended(e):
            return (False, "unattended-session")
        return (True, "default")
    except Exception:
        return (False, "error")


def human_duration(seconds):
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(round(s))} s"
    if s < 5400:
        return f"{int(round(s / 60.0))} min"
    h, m = int(s // 3600), int(round((s % 3600) / 60.0))
    return f"{h} h {m} min"


def read_state(root):
    return _read(Path(root) / ".deepmap" / "autosync.json") or {}


def precheck(root, *, trigger="session-start", source=None, head=None, env=None, now=None):
    try:
        e = _e(env)
        now = time.time() if now is None else now
        man = _read(Path(root) / ".deepmap" / "graph_build.json")
        took = man.get("sync_wall_s") if isinstance(man, dict) else None
        src = [None]

        def r(code, ok=False):
            return {"ok": ok, "code": code, "took_s": took, "enabled_source": src[0]}
        if trigger == "session-start" and str(e.get("DEEPMAP_HOOKS", "")).strip().lower() in _OFF:
            return r("hooks-off")
        if source == "compact":
            return r("compact")
        on, src[0] = enabled(root, trigger, e)
        if not on:
            return r("unattended-session" if src[0] == "unattended-session" else "disabled")
        if man is None:
            return r("not-set-up")
        if str(man.get("verdict") or "").upper() == "FAILED" or man.get("nodes") == 0:
            return r("empty")
        st = read_state(root)
        last = _read(Path(root) / ".deepmap" / "sync_last.json") or {}
        lift = last.get("finished_epoch") if last.get("outcome") == "synced" else None
        p = st.get("paused")
        if isinstance(p, dict) and not (lift and lift > float(p.get("since_epoch") or 0)):
            return r("paused")
        b = st.get("backoff_until_epoch")
        if isinstance(b, (int, float)) and b > now and not (lift and lift > b - BACKOFF_S):
            return r("backoff")
        try:
            mx = int(str(e.get("DEEPMAP_AUTO_SYNC_MAX_S", "")).strip() or MAX_S_DEFAULT)
        except ValueError:
            mx = MAX_S_DEFAULT
        if mx > 0 and isinstance(took, (int, float)) and took > mx:
            return r("too-slow")
        if head:
            ls = st.get("last_session_start") if isinstance(st.get("last_session_start"), dict) else {}
            if ls.get("head") == head and now - float(ls.get("started_epoch") or 0) < COOLDOWN_S:
                return r("cooldown")
        return r(None, True)
    except Exception:
        return {"ok": False, "code": "error", "took_s": None, "enabled_source": None}
'''

FAKE_SYNC_STATUS = r'''"""auto_sync_hooks_gates FAKE of build/sync_status.py — what the hooks call (auto-sync spec §2.3): read_running /
holder_live / read_last / read_json. Stdlib-only at import."""
import json
from pathlib import Path

SCHEMA = 1
RUNNING_FILE = "sync_running.json"
LAST_FILE = "sync_last.json"
FAKE = True


def read_json(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def holder_live(rec):
    try:
        import port_registry as PR
        got = PR.same_start(rec.get("pid_created"), PR.pid_create_time(int(rec.get("pid"))))
        return got if got in (True, False) else None
    except Exception:
        return None


def read_running(root):
    rec = read_json(Path(root) / ".deepmap" / RUNNING_FILE)
    if rec is None:
        return None
    rec = dict(rec)
    rec["live"] = holder_live(rec)
    return rec


def read_last(root):
    return read_json(Path(root) / ".deepmap" / LAST_FILE)
'''

STUB = "import sys\nraise SystemExit(0)   # auto_sync_hooks_gates stub: never runs (Popen is a recorder)\n"
# The stub CLI of the temp plugin: never runs either, but carries the sync CLI contract the hooks read before they start,
# announce or follow a background refresh (spec Appendix C3 + C4: `sync` takes --unattended / --session and hands such a
# run to auto_sync.run). Section I checks the tree's REAL CLI against the same text proof.
STUB_CLI = STUB + (
    "\n\ndef _contract(p, _load, args):   # what deepmap_hook_state.cli_takes_unattended looks for (C3 + C4)\n"
    "    p.add_argument(\"--unattended\", choices=(\"session-start\",), default=None)\n"
    "    p.add_argument(\"--session\", default=None)\n"
    "    return _load(\"auto_sync\").run(args.target, trigger=args.unattended, session=args.session)\n"
)

# The recorder every hook process loads (PYTHONPATH): Popen starts NOTHING; subprocess.run is logged, then run.
SITECUSTOMIZE = r'''import os
_REC = os.environ.get("DMAS_REC")
if _REC:
    import json
    import subprocess
    _RealPopen = subprocess.Popen
    _real_run = subprocess.run
    _KEYS = ("PYTHONUTF8", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "GIT_OPTIONAL_LOCKS", "__PYVENV_LAUNCHER__")

    def _log(obj):
        with open(_REC, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")

    class _Proc:
        def __init__(self, args, **kw):
            env = kw.get("env")
            out = kw.get("stdout")
            rec = {"kind": "popen", "argv": [str(a) for a in args], "creationflags": kw.get("creationflags"),
                   "start_new_session": kw.get("start_new_session"), "env_given": env is not None,
                   "env": None if env is None else {k: env.get(k) for k in _KEYS if k in env},
                   "stdout": getattr(out, "name", None) if hasattr(out, "write") else repr(out),
                   "stderr_stdout": kw.get("stderr") == subprocess.STDOUT,
                   "stdin_devnull": kw.get("stdin") == subprocess.DEVNULL}
            _log(rec)
            fail = os.environ.get("DMAS_POPEN_FAIL")
            if fail and fail in " ".join(rec["argv"]):
                raise OSError("DMAS: recorded start failure")
            self.args, self.pid, self.returncode = args, os.getpid(), None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def _run(args, *a, **kw):
        env = kw.get("env") if kw.get("env") is not None else os.environ
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        _log({"kind": "run", "argv": argv, "glk": env.get("GIT_OPTIONAL_LOCKS")})
        subprocess.Popen = _RealPopen
        try:
            return _real_run(args, *a, **kw)
        finally:
            subprocess.Popen = _Proc

    subprocess.Popen = _Proc
    subprocess.run = _run
'''

# Section J's recorder (PYTHONPATH, in place of the one above): which modules the hook process loaded, written at exit —
# nothing else (the recorder above imports json / subprocess itself, which would hide what the hook imported).
MODS_SITE = r'''import os
_OUT = os.environ.get("DMAS_MODS")
if _OUT:
    import atexit
    import sys

    def _dmas_dump():
        try:
            with open(_OUT, "a", encoding="utf-8") as f:
                f.write(" ".join(sorted(sys.modules)) + "\n")
        except Exception:
            pass
    atexit.register(_dmas_dump)
'''

# Section C2: what the notify asks precheck (appended to a plugin copy's build/auto_sync.py). SPY records the keywords
# and asks the real precheck; OLD_PRECHECK is a precheck without the announce keyword (an older build/).
SPY_PRECHECK = r'''

# --- auto_sync_hooks_gates C2: a recorder around precheck ---
import json as _dmas_json
_dmas_real_precheck = precheck


def precheck(root, **kw):
    log = os.environ.get("DMAS_PRECHECK_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(_dmas_json.dumps({k: kw.get(k) for k in ("trigger", "head", "announce") if k in kw}) + "\n")
    return _dmas_real_precheck(root, **kw)
'''
OLD_PRECHECK = r'''

# --- auto_sync_hooks_gates C2: a precheck WITHOUT the announce keyword (an older build/) ---
import json as _dmas_json
_dmas_real_precheck = precheck


def precheck(root, *, trigger="session-start", source=None, head=None, env=None, now=None):
    log = os.environ.get("DMAS_PRECHECK_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(_dmas_json.dumps({"trigger": trigger, "head": head}) + "\n")
    return _dmas_real_precheck(root, trigger=trigger, source=source, head=head, env=env, now=now)
'''

# Section J: the tool-result hook's pre-import check (_quiet_fast) and deepmap_hook_state.quiet_holds on the same state
# (argv: hooks dir, JSON spec {root, sid, cwd, stamp: text or null}); prints [fast, slow] + the shared constants.
QUIET_EQ = r'''import importlib.util, json, sys
from pathlib import Path
hooks, spec = sys.argv[1], json.loads(sys.argv[2])
if spec.get("stamp") is not None:
    Path(spec["root"], ".deepmap", "autosync_quiet.json").write_text(spec["stamp"], encoding="utf-8")
sys.path.insert(0, hooks)
import deepmap_hook_state as hs
sp = importlib.util.spec_from_file_location("dmas_toolresult", str(Path(hooks) / "deepmap_toolresult_freshness.py"))
T = importlib.util.module_from_spec(sp)
sp.loader.exec_module(T)
raw = json.dumps({"session_id": spec["sid"], "cwd": spec["cwd"], "hook_event_name": "PostToolUse",
                  "tool_name": "mcp__deepmap-search__deepmap_search", "tool_input": {"query": "x"}}).encode("utf-8")
print(json.dumps({"fast": T._quiet_fast(raw), "slow": hs.quiet_holds(spec["root"], spec["sid"], spec["cwd"]),
                  "consts": [T._QUIET_FILE == hs.AUTOSYNC_QUIET, T._QUIET_SCHEMA == hs.QUIET_SCHEMA,
                             set(T._OFF_WORDS) == set(hs._OFF), T._MAX_WALK == hs._MAX_WALK,
                             tuple(hs._QUIET_FILES) == ("session_health.json", "autosync.json", "sync_running.json",
                                                        "autosync_spawn.json", "graph_build.json")]}))
'''

# A driver: call one deepmap_hook_state function in a fresh process (argv: hooks dir, JSON spec) and print the result.
DRIVER = r'''import json, os, subprocess, sys
spec = json.loads(sys.argv[2])
if spec.get("pop_glk"):
    os.environ.pop("GIT_OPTIONAL_LOCKS", None)
seen = []
if spec.get("record_run"):
    _real = subprocess.run
    def _rec(args, *a, **kw):
        env = kw.get("env") if kw.get("env") is not None else os.environ
        seen.append({"argv": [str(x) for x in args], "glk": env.get("GIT_OPTIONAL_LOCKS")})
        return _real(args, *a, **kw)
    subprocess.run = _rec
sys.path.insert(0, sys.argv[1])
import deepmap_hook_state as hs
fn = getattr(hs, spec["fn"], None)
if fn is None:
    print(json.dumps({"missing": spec["fn"]}))
    raise SystemExit(0)
try:
    out = fn(*spec.get("args", []), **spec.get("kwargs", {}))
    print(json.dumps({"ok": out, "runs": seen}, default=str))
except Exception as e:
    print(json.dumps({"raised": repr(e), "runs": seen}))
'''

# Two overlapping session starts writing one log (argv: hooks dir, project root, "autosync" | "heal" | "fallback"): the
# heal hook's own opener and _spawn hand each of two REAL children (`python -c`, waited for here) its log handle;
# child A writes a line, B's header + child are added after it, then A goes on. Prints the log's text as JSON.
# "fallback": the Windows append-only handle cannot be made (a recorder in place of _winapi.CreateFile) -> open(..ab).
LOGRACE = r'''import importlib.util, json, os, subprocess, sys, time
from pathlib import Path
hooks, root, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("dmas_heal", hooks / "deepmap_sessionstart_heal.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
out = {"text": "", "procs": [], "create_calls": 0}
if mode == "fallback":
    log = root / ".deepmap" / "fallback.log"
    log.write_bytes(b"kept\n")
    calls = []
    if os.name == "nt":
        import _winapi
        real = _winapi.CreateFile
        def _rec(*a, **k):
            calls.append(a[0] if a else None)
            raise OSError("DMAS: recorded CreateFile failure")
        _winapi.CreateFile = _rec
    try:
        f = H._open_append(log)
        f.write(b"appended\n")
        f.close()
    finally:
        if os.name == "nt":
            _winapi.CreateFile = real
    out.update(text=log.read_text(encoding="utf-8"), create_calls=len(calls))
    print(json.dumps(out))
    raise SystemExit(0)
log = root / ".deepmap" / ("autosync.log" if mode == "autosync" else "heal.log")
go = root / ".deepmap" / "dmas-go"
child = ("import sys, time\nfrom pathlib import Path\ntag, go = sys.argv[1], Path(sys.argv[2])\n"
         "print(tag + ' line 0', flush=True)\nt = time.monotonic()\n"
         "while tag == 'A' and not go.exists() and time.monotonic() - t < 30:\n    time.sleep(0.02)\n"
         "for i in range(1, 20):\n    print(tag + ' line ' + str(i), flush=True)\n    time.sleep(0.01)\n"
         "sys.stderr.write(tag + ' stderr end\\n')\n")
# hidden console / own session, at NORMAL priority: under a loaded machine a below-normal child can starve past any
# timeout here, and the property under test is where the writes land, not the runner's priority (section B checks that)
kw = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
procs = []
try:
    for tag in ("A", "B"):
        f = (H._open_autosync_log(root, [sys.executable], "test", "sess-" + tag) if mode == "autosync"
             else H._open_heal_log(root, [sys.executable], "test-" + tag))
        try:
            procs.append(H._spawn([sys.executable], ["-c", child, tag, str(go)], f, kw))
        finally:
            f.close()
        if tag == "A":   # A is mid-run (its first line is in the log) when B's header and child are added
            t = time.monotonic()
            while "A line 0" not in log.read_text(encoding="utf-8", errors="replace") and time.monotonic() - t < 90:
                time.sleep(0.02)
    go.write_text("go", encoding="utf-8")
    for p in procs:
        p.wait(timeout=150)
except BaseException:
    import traceback
    out["error"] = traceback.format_exc()[-1500:]
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()   # our own child, by its handle (never a recorded pid)
            try:
                p.wait(timeout=30)
            except Exception:
                out["error"] = (out.get("error") or "") + " | a killed child had not exited after 30 s"
out["procs"] = [p.returncode for p in procs]
out["text"] = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
print(json.dumps(out))
'''

_SCRUB_PREFIXES = ("DEEPMAP_", "CLAUDE_", "NEO4J_", "PYTHON", "DMAS_")
_SCRUB_NAMES = {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "GIT_OPTIONAL_LOCKS", "__PYVENV_LAUNCHER__", "GIT_DIR",
                "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}
_SID_N = itertools.count(1)


def _sid(tag: str) -> str:
    return f"as-{tag}-{next(_SID_N)}"


def _rmtree(path: Path) -> None:
    """Remove the gate's OWN temp tree (exact path; git object files are read-only on Windows)."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerr)


class Lab:
    """One temp tree: a plugin (the tree's hooks/ + build/, stubs, fakes when needed), an empty-build plugin, the
    recorder's site dir, DEEPMAP_HOMEs, two template repositories and room for projects."""

    def __init__(self, tree: Path):
        self.tree = tree
        self.base = Path(tempfile.mkdtemp(prefix="dmash")).resolve()
        self._n = itertools.count(1)
        self.plugin = self._plugin(self.base / "plugin", full=True)
        self.bare = self._plugin(self.base / "bare-plugin", full=False)
        self.site = self.base / "site"
        self.site.mkdir()
        (self.site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        self.site_mods = self.base / "site-mods"
        self.site_mods.mkdir()
        (self.site_mods / "sitecustomize.py").write_text(MODS_SITE, encoding="utf-8")
        (self.base / "driver.py").write_text(DRIVER, encoding="utf-8")
        (self.base / "quiet_eq.py").write_text(QUIET_EQ, encoding="utf-8")
        self.gitcfg = self.base / "gitconfig"
        self.gitcfg.write_text("", encoding="utf-8")
        self.fake_auto = not (tree / "build" / "auto_sync.py").is_file()
        self.fake_status = not (tree / "build" / "sync_status.py").is_file()
        self.templates = self._templates()

    def _templates(self) -> dict:
        """Two repositories built once (copied per project): HEAD at c1, and HEAD one commit past it (c2)."""
        out = {}
        t = self.base / "tpl" / "git"
        (t / "src").mkdir(parents=True)
        (t / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        (t / "README.md").write_text("# fixture\n", encoding="utf-8")
        (t / ".gitignore").write_text(".deepmap/\n", encoding="utf-8")
        self.git(t, "init", "-q")
        self.git(t, "add", "-A")
        self.git(t, "commit", "-q", "-m", "c1")
        c1 = self.git(t, "rev-parse", "HEAD").lower()
        out[1] = (self.base / "tpl" / "c1", [c1])
        shutil.copytree(t, out[1][0])
        (t / "src" / "m2.py").write_text("def f2():\n    return 2\n", encoding="utf-8")
        self.git(t, "add", "-A")
        self.git(t, "commit", "-q", "-m", "c2")
        out[2] = (t, [c1, self.git(t, "rev-parse", "HEAD").lower()])
        return out

    def _plugin(self, pr: Path, *, full: bool) -> Path:
        (pr / "hooks").mkdir(parents=True)
        (pr / "build").mkdir()
        for f in (self.tree / "hooks").glob("*.py"):
            shutil.copyfile(f, pr / "hooks" / f.name)
        if (self.tree / "hooks" / "hooks.json").is_file():
            shutil.copyfile(self.tree / "hooks" / "hooks.json", pr / "hooks" / "hooks.json")
        if not full:
            return pr   # build/ stays EMPTY: no port_registry, no auto_sync — the hooks' degraded path
        for f in (self.tree / "build").glob("*.py"):
            shutil.copyfile(f, pr / "build" / f.name)
        pj = REPO / ".claude-plugin" / "plugin.json"
        if pj.is_file():
            (pr / ".claude-plugin").mkdir()
            shutil.copyfile(pj, pr / ".claude-plugin" / "plugin.json")
        (pr / "build" / "provision.py").write_text(STUB, encoding="utf-8")
        (pr / "build" / "deepmap_cli.py").write_text(STUB_CLI, encoding="utf-8")
        if not (pr / "build" / "auto_sync.py").is_file():
            (pr / "build" / "auto_sync.py").write_text(FAKE_AUTO_SYNC, encoding="utf-8")
        if not (pr / "build" / "sync_status.py").is_file():
            (pr / "build" / "sync_status.py").write_text(FAKE_SYNC_STATUS, encoding="utf-8")
        return pr

    def realcli_plugin(self) -> Path:
        """A full plugin whose build/deepmap_cli.py is the tree's REAL CLI (never run here: Popen is a recorder) — what
        the hooks see in a plugin whose CLI does or does not take --unattended yet."""
        pr = self._plugin(self.base / "realcli-plugin", full=True)
        shutil.copyfile(self.tree / "build" / "deepmap_cli.py", pr / "build" / "deepmap_cli.py")
        return pr

    def plugin_variant(self, name: str, auto_sync_tail: str) -> Path:
        """A full plugin whose build/auto_sync.py has `auto_sync_tail` appended (a precheck recorder / an older
        precheck)."""
        pr = self._plugin(self.base / name, full=True)
        f = pr / "build" / "auto_sync.py"
        f.write_text(f.read_text(encoding="utf-8") + auto_sync_tail, encoding="utf-8")
        return pr

    def close(self) -> None:
        _rmtree(self.base)

    # ---- projects
    def git(self, cwd: Path, *args) -> str:
        env = {k: v for k, v in os.environ.items() if k.upper() not in _SCRUB_NAMES}
        env.update({"GIT_AUTHOR_NAME": "dm", "GIT_AUTHOR_EMAIL": "dm@example.invalid", "GIT_COMMITTER_NAME": "dm",
                    "GIT_COMMITTER_EMAIL": "dm@example.invalid", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": str(self.gitcfg)})
        p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"git {args}: {p.stderr.strip()[:300]}")
        return p.stdout.strip()

    def project(self, tag: str, *, git: bool = True, commits: int = 1, manifest=None) -> dict:
        """A project root with a READY manifest built at its FIRST commit; `commits` 2 = HEAD one commit past it (a
        copy of a template repository). `git=False`: a plain directory."""
        root = self.base / "p" / f"{next(self._n):03d}-{tag}"
        shas: list = []
        if git:
            src, shas = self.templates[2 if commits >= 2 else 1]
            shutil.copytree(src, root)
            shas = list(shas)
        else:
            (root / "src").mkdir(parents=True)
            (root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (root / "README.md").write_text("# fixture\n", encoding="utf-8")
        (root / ".deepmap").mkdir()
        man = {"nodes": 12, "verdict": "READY", "extractor": "graphify", "built_at_utc": _utc(time.time() - 3600)}
        if shas:
            man["built_at_sha"] = shas[0]
        man.update(manifest or {})
        for k in [k for k, v in man.items() if v is None]:
            man.pop(k)
        (root / ".deepmap" / "graph_build.json").write_text(json.dumps(man), encoding="utf-8")
        return {"root": root.resolve(), "shas": shas, "manifest": man}

    def home(self, tag: str) -> Path:
        h = self.base / "homes" / tag
        (h / "run").mkdir(parents=True, exist_ok=True)
        return h.resolve()

    # ---- running hooks
    def env(self, home: Path, *, plugin=None, rec=None, **extra) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(_SCRUB_PREFIXES) and k.upper() not in _SCRUB_NAMES}
        env.update({"DEEPMAP_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(plugin or self.plugin),
                    "PYTHONPATH": str(self.site), "CLAUDE_CODE_SESSION_ATTENDED": "1",
                    "DEEPMAP_NOTIFY_HEAL_WAIT": "0", "GIT_CONFIG_GLOBAL": str(self.gitcfg),
                    "GIT_CONFIG_NOSYSTEM": "1"})
        if rec is not None:
            env["DMAS_REC"] = str(rec)
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = str(v)
        return env

    def run(self, hook: str, payload, home: Path, *, plugin=None, raw: bytes | None = None, **extra) -> dict:
        rec = self.base / "rec" / f"{next(self._n):04d}-{hook}.jsonl"
        rec.parent.mkdir(exist_ok=True)
        rec.write_text("", encoding="utf-8")
        data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hp = (plugin or self.plugin) / "hooks" / f"{hook}.py"
        cwd = payload.get("cwd") if isinstance(payload, dict) and payload.get("cwd") else str(self.base)
        p = subprocess.run([PY, "-X", "utf8", str(hp)], input=data, capture_output=True, timeout=120, cwd=cwd,
                           env=self.env(home, plugin=plugin, rec=rec, **extra))
        out = p.stdout.decode("utf-8", "replace").strip()
        docs = None
        if not out:
            docs = []
        else:
            try:
                docs = [json.loads(out)]
            except Exception:
                docs = None
        recs = [json.loads(ln) for ln in rec.read_text(encoding="utf-8").splitlines() if ln.strip()]
        text = None
        if docs:
            text = ((docs[0].get("hookSpecificOutput") or {}).get("additionalContext")
                    if isinstance(docs[0], dict) else None)
        return {"rc": p.returncode, "out": out, "err": p.stderr.decode("utf-8", "replace")[-600:], "docs": docs,
                "text": text, "popen": [r for r in recs if r["kind"] == "popen"],
                "runs": [r for r in recs if r["kind"] == "run"]}

    def run_mods(self, hook: str, payload, home: Path, *, plugin=None, **extra) -> dict:
        """lab.run's twin for section J: the hook under the module recorder only (no Popen recorder: nothing here
        starts a process) — {"rc", "out", "text", "mods": the set of modules the hook process loaded}."""
        mods = self.base / "rec" / f"{next(self._n):04d}-{hook}.mods"
        mods.parent.mkdir(exist_ok=True)
        hp = (plugin or self.plugin) / "hooks" / f"{hook}.py"
        cwd = payload.get("cwd") if isinstance(payload, dict) and payload.get("cwd") else str(self.base)
        env = self.env(home, plugin=plugin, **extra)
        env.update({"PYTHONPATH": str(self.site_mods), "DMAS_MODS": str(mods)})
        p = subprocess.run([PY, "-X", "utf8", str(hp)], input=json.dumps(payload).encode("utf-8"), capture_output=True,
                           timeout=120, cwd=cwd, env=env)
        out = p.stdout.decode("utf-8", "replace").strip()
        text = None
        try:
            text = (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext") if out else None
        except Exception:
            text = None
        got = set(mods.read_text(encoding="utf-8").split()) if mods.is_file() else {"<no record>"}
        return {"rc": p.returncode, "out": out, "text": text, "mods": got,
                "err": p.stderr.decode("utf-8", "replace")[-600:]}

    def quiet_eq(self, root: Path, sid: str, cwd: str, stamp_text=None) -> dict:
        """Section J: the hook's _quiet_fast and deepmap_hook_state.quiet_holds on one state (fresh process)."""
        spec = {"root": str(root), "sid": sid, "cwd": cwd, "stamp": stamp_text}
        env = self.env(self.home("j-eq"))
        env.pop("PYTHONPATH", None)
        p = subprocess.run([PY, "-X", "utf8", str(self.base / "quiet_eq.py"), str(self.plugin / "hooks"),
                            json.dumps(spec)], capture_output=True, timeout=120, env=env)
        try:
            return json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception:
            return {"raised": p.stderr.decode("utf-8", "replace")[-500:]}

    def call(self, fn: str, home: Path, *args, plugin=None, pop_glk=False, record_run=False, env_extra=None,
             **kwargs):
        """deepmap_hook_state.<fn>(*args, **kwargs) in a fresh process (JSON in / out; no recorder)."""
        spec = {"fn": fn, "args": list(args), "kwargs": kwargs, "pop_glk": pop_glk, "record_run": record_run}
        env = self.env(home, plugin=plugin, **(env_extra or {}))
        env.pop("PYTHONPATH", None)
        p = subprocess.run([PY, "-X", "utf8", str(self.base / "driver.py"), str((plugin or self.plugin) / "hooks"),
                            json.dumps(spec)], capture_output=True, timeout=120, env=env)
        try:
            return json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception:
            return {"raised": f"driver: rc={p.returncode} {p.stderr.decode('utf-8', 'replace')[-400:]}"}


def _utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _age_manifest(root: Path) -> None:
    """graph_build.json written an hour ago: a recorded run that finished since is not superseded by a newer manifest
    (auto_sync._repeating_skip)."""
    old = time.time() - 3600
    os.utime(root / ".deepmap" / "graph_build.json", (old, old))


def _closed_port() -> int:
    """A loopback port nothing listens on (bound, then released)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class _Listener:
    """A loopback port that accepts connections (the kernel completes the handshake from the backlog) while open: a
    stand-in for this project's running database when the operator's NEO4J_URI points at it."""

    def __enter__(self):
        import socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        return self

    def __exit__(self, *_exc):
        self.sock.close()


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def _ss(sid, root, **kw) -> dict:
    return {"session_id": sid, "cwd": str(root), "hook_event_name": "SessionStart", "source": "startup", **kw}


def _grep(sid, root) -> dict:
    return {"session_id": sid, "cwd": str(root), "hook_event_name": "PreToolUse", "tool_name": "Grep",
            "tool_input": {"pattern": "def main", "path": str(root)}}


def _tool(sid, root, name="mcp__deepmap-search__deepmap_search") -> dict:
    return {"session_id": sid, "cwd": str(root), "hook_event_name": "PostToolUse", "tool_name": name,
            "tool_input": {"query": "where is main"}, "tool_response": [{"type": "text", "text": "[]"}]}


def _edit(sid, root, path) -> dict:
    return {"session_id": sid, "cwd": str(root), "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(path), "old_string": "1", "new_string": "2"},
            "tool_response": {"filePath": str(path), "success": True}}


def _health(root: Path, sid: str, **entry) -> None:
    """A session_health.json record as the SessionStart notify writes it (§2.6 / T9#1 + the auto-sync keys)."""
    base = {"start": time.time() - 30, "told": "ready", "servers_down": False, "heal_path": None, "seen": None,
            "resolved": True, "restart_told": False, "auto": "stale", "head": None, "distance": None}
    base.update(entry)
    p = root / ".deepmap" / "session_health.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {"sessions": {}}
    data["sessions"][sid] = base
    _w(p, data)


def _stamp(tree: Path):
    """This process's start stamp, in port_registry.pid_create_time's format, + a stamp of the same kind that is NOT
    this process's (a recycled pid)."""
    sys.path.insert(0, str(tree / "build"))
    try:
        import port_registry as PR  # type: ignore
        s = PR.pid_create_time(os.getpid())
    finally:
        try:
            sys.path.remove(str(tree / "build"))
        except ValueError:
            pass
    dead = (s.rsplit(":", 1)[0] + ":1") if isinstance(s, str) else 1.0
    return s, dead


def _running(root: Path, *, origin="unattended", pid=None, created=None, started_ago=300.0, phase="extract",
             rid="r-1") -> None:
    now = time.time()
    _w(root / ".deepmap" / "sync_running.json", {
        "schema": 1, "id": rid, "pid": pid if pid is not None else os.getpid(), "pid_created": created,
        "origin": origin, "trigger": "session-start" if origin == "unattended" else None, "session": None,
        "started_utc": _utc(now - started_ago), "started_epoch": now - started_ago, "pass": 1, "phase": phase,
        "phase_utc": _utc(now), "head_at_start": None, "source_fingerprint": None, "deepmap_version": None})


def _last_run(root: Path, outcome: str, *, finished_ago=600.0, rid="o-1", **kw) -> None:
    now = time.time()
    lr = {"id": rid, "session_id": None, "trigger": "session-start", "started_utc": _utc(now - finished_ago - 60),
          "finished_utc": _utc(now - finished_ago), "finished_epoch": now - finished_ago, "wall_s": 60.0,
          "rc": 0, "outcome": outcome, "reason": None, "advice": None, "suggest": None, "head": None,
          "fresh_at_head": None, "fingerprint": None, "sync_id": None, "verdict": None, "reasons": [],
          "built_at_sha": None, "dirty": False, "stale_kept": 0, "embeddings_deferred": False, "took_s": 60.0}
    lr.update(kw)
    p = root / ".deepmap" / "autosync.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        st = {"schema": 1, "current": None, "last_session_start": None, "consecutive_failures": 0,
              "backoff_until_epoch": None, "paused": None}
    st["last_run"] = lr
    _w(p, st)


# =========================================================================== A: hooks.json
def gate_a_hooks_json(tree: Path) -> None:
    say("\n-- A: hooks.json registers the tool-result hook on the deepmap tools --")
    try:
        hj = json.loads((tree / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        check("hooks.json parses", False, repr(e))
        return
    post = (hj.get("hooks") or {}).get("PostToolUse") or []
    matchers = [g.get("matcher") for g in post]
    check("PostToolUse: the Edit entry first, then the deepmap-tools entry",
          len(post) >= 2 and matchers[0] == "Edit|Write|MultiEdit|NotebookEdit" and MATCHER in matchers
          and matchers.index(MATCHER) > 0, matchers)
    ent = next((g for g in post if g.get("matcher") == MATCHER), None)
    hooks = (ent or {}).get("hooks") or []
    cmd = hooks[0].get("command", "") if hooks else ""
    want = ('python -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/deepmap_toolresult_freshness.py" ; '
            'python3 -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/deepmap_toolresult_freshness.py" ; exit 0')
    check("the entry runs deepmap_toolresult_freshness.py in the `python … ; python3 … ; exit 0` form",
          len(hooks) == 1 and hooks[0].get("type") == "command" and cmd == want, cmd)
    check("its timeout is 10 s", bool(hooks) and hooks[0].get("timeout") == 10, hooks)
    allc = [h.get("command", "") for gs in (hj.get("hooks") or {}).values() for g in gs for h in g.get("hooks") or []]
    check("no command uses || or && (Windows PowerShell 5.1 cannot parse them)",
          not any("||" in c or "&&" in c for c in allc), allc)
    check("hooks/deepmap_toolresult_freshness.py exists", (tree / "hooks" / "deepmap_toolresult_freshness.py").is_file())
    for name, want_m in (("mcp__deepmap-search__deepmap_search", True),
                         ("mcp__plugin_deep-map_deepmap-search__deepmap_impact", True),
                         ("mcp__deepmap-search__deepmap_cypher", True), ("mcp__deepmap-embed__deepmap_embed", False),
                         ("Grep", False), ("Edit", False)):
        got = ent is not None and re.fullmatch(ent.get("matcher") or "", name) is not None
        check(f"matcher {'matches' if want_m else 'does not match'} {name}", got == want_m)
    # the start cost every hook pays on every call (the tool-result hook runs after each deepmap tool call): importing
    # deepmap_hook_state loads no socket / selectors / subprocess — port_listening imports socket when it probes
    probe = "\n".join((
        "import json, sys",
        "mods = ('socket', 'selectors', 'subprocess')",
        "before = {m for m in mods if m in sys.modules}",
        "sys.path.insert(0, sys.argv[1])",
        "import deepmap_hook_state",
        "print(json.dumps(sorted(m for m in mods if m in sys.modules and m not in before)))", ""))
    p = subprocess.run([PY, "-X", "utf8", "-c", probe, str(tree / "hooks")], capture_output=True, timeout=120)
    out = p.stdout.decode("utf-8", "replace").strip()
    check("importing deepmap_hook_state loads no socket / selectors / subprocess (each hook's start cost)",
          p.returncode == 0 and out == "[]", (p.returncode, out, p.stderr.decode("utf-8", "replace")[-300:]))


# =========================================================================== B: the heal hook starts the runner
def _runner_of(res) -> list:
    return [p for p in res["popen"] if "--unattended=session-start" in " ".join(p["argv"])]


def _heal_of(res) -> list:
    return [p for p in res["popen"] if len(p["argv"]) >= 3 and p["argv"][1].endswith("provision.py")
            and p["argv"][2] == "heal"]


def gate_b_heal(lab: Lab) -> None:
    say("\n-- B: the SessionStart heal hook — heal first, then the background refresh --")
    home = lab.home("b")
    pr = lab.project("b-main", git=False)
    root = pr["root"]
    sid = _sid("b")
    # __PYVENV_LAUNCHER__ (a venv redirector's marker; on Windows it also names the interpreter's own sys.executable,
    # so it is set to the gate's interpreter) must not reach the runner
    res = lab.run("deepmap_sessionstart_heal", _ss(sid, root), home, __PYVENV_LAUNCHER__=PY)
    check("exit 0, no stdout", res["rc"] == 0 and res["out"] == "", res)
    check("two processes started: the heal FIRST, then the background refresh",
          len(res["popen"]) == 2 and _heal_of(res) == res["popen"][:1] and _runner_of(res) == res["popen"][1:],
          [p["argv"][1:4] for p in res["popen"]])
    run = _runner_of(res)
    r = run[0] if run else {"argv": [], "env": {}, "creationflags": None}
    hpy = _heal_of(res)[0]["argv"][0] if _heal_of(res) else None
    cli = str(lab.plugin / "build" / "deepmap_cli.py")
    check("runner argv: <the heal's python> <plugin>/build/deepmap_cli.py sync <root> --unattended=session-start "
          "--session <sid>", bool(hpy) and r["argv"] == [hpy, cli, "sync", str(root), "--unattended=session-start",
                                                          "--session", sid], (hpy, r["argv"]))
    env = r.get("env") or {}
    check("runner env: PYTHONUTF8=1, HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, GIT_OPTIONAL_LOCKS=0",
          env.get("PYTHONUTF8") == "1" and env.get("HF_HUB_OFFLINE") == "1" and env.get("TRANSFORMERS_OFFLINE") == "1"
          and env.get("GIT_OPTIONAL_LOCKS") == "0", env)
    check("runner env: no __PYVENV_LAUNCHER__ (it belongs to another interpreter)",
          "__PYVENV_LAUNCHER__" not in env and r.get("env_given") is True, env)
    if _WIN:
        hf = _heal_of(res)[0]["creationflags"] if _heal_of(res) else None
        check("Windows: runner creationflags == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL "
              "(0x08004200)", r.get("creationflags") == 0x08004200, hex(r.get("creationflags") or 0))
        check("Windows: never the DETACHED_PROCESS bit (0x8) on either process",
              all(not ((p.get("creationflags") or 0) & DETACHED_PROCESS) for p in res["popen"]))
        check("Windows: the heal keeps its own flags (0x08000200, no BELOW_NORMAL)", hf == 0x08000200,
              hex(hf or 0))
    else:
        check("POSIX: the runner starts in a new session", r.get("start_new_session") is True, r)
    log = root / ".deepmap" / "autosync.log"
    check("runner output appended to .deepmap/autosync.log, stderr into it, stdin DEVNULL",
          r.get("stdout") and Path(str(r.get("stdout"))).resolve() == log.resolve() and r.get("stderr_stdout") is True
          and r.get("stdin_devnull") is True, r)
    head = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    check("autosync.log carries a header naming the start (--unattended=session-start, the session)",
          "--unattended=session-start" in head and sid in head and str(root) in head, head[-300:])
    sp = {}
    try:
        sp = json.loads((root / ".deepmap" / "autosync_spawn.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    check("autosync_spawn.json: schema, session_id, source, spawned_epoch, pid, interpreter + its source",
          sp.get("schema") == 1 and sp.get("session_id") == sid and sp.get("source") == "startup"
          and isinstance(sp.get("spawned_epoch"), (int, float)) and abs(sp["spawned_epoch"] - time.time()) < 120
          and isinstance(sp.get("pid"), int) and sp.get("interpreter") == [hpy]
          and sp.get("interpreter_source") == "hook" and sp.get("pid_created") is not None, sp)
    gits = [x for x in res["runs"] if Path(x["argv"][0]).name.lower().startswith("git")]
    check("the heal hook runs no git (precheck and the spawn are file reads + Popen)", not gits, gits)

    sid2 = _sid("b-nosid")
    pr2 = lab.project("b-nosid", git=False)
    res = lab.run("deepmap_sessionstart_heal", {"cwd": str(pr2["root"]), "hook_event_name": "SessionStart"}, home)
    run = _runner_of(res)
    check("no session_id in the payload -> no --session (and a missing source counts as startup)",
          len(run) == 1 and run[0]["argv"][-1] == "--unattended=session-start", [p["argv"] for p in res["popen"]])
    del sid2

    # no runner — and exactly which heal still happens
    cases = []
    p = lab.project("b-compact", git=False)
    cases.append(("payload source compact", p, _ss(_sid("b"), p["root"], source="compact"), {}, 1))
    p = lab.project("b-env0", git=False)
    cases.append(("DEEPMAP_AUTO_SYNC=0", p, _ss(_sid("b"), p["root"]), {"DEEPMAP_AUTO_SYNC": "0"}, 1))
    p = lab.project("b-inst", git=False)
    _w(p["root"] / ".deepmap" / "instance.json", {"auto_sync": {"enabled": False}})
    cases.append(("instance.json auto_sync.enabled=false", p, _ss(_sid("b"), p["root"]), {}, 1))
    p = lab.project("b-machine", git=False)
    mh = lab.home("b-machine")
    _w(mh / "auto-sync.json", {"enabled": False})
    cases.append(("machine switch <DEEPMAP_HOME>/auto-sync.json enabled=false", p, _ss(_sid("b"), p["root"]),
                  {"DEEPMAP_HOME": str(mh)}, 1))
    p = lab.project("b-hooks0", git=False)
    cases.append(("DEEPMAP_HOOKS=0 (no heal either)", p, _ss(_sid("b"), p["root"]), {"DEEPMAP_HOOKS": "0"}, 0))
    p = lab.project("b-unatt", git=False)
    cases.append(("CLAUDE_CODE_SESSION_ATTENDED=0 (headless / SDK)", p, _ss(_sid("b"), p["root"]),
                  {"CLAUDE_CODE_SESSION_ATTENDED": "0"}, 1))
    nm = lab.base / "p" / "b-nomarker"
    nm.mkdir(parents=True)
    cases.append(("no graph marker (nothing at all)", {"root": nm}, _ss(_sid("b"), nm), {}, 0))
    p = lab.project("b-empty", git=False, manifest={"verdict": "FAILED", "nodes": 0})
    cases.append(("proven-empty graph (nothing at all)", p, _ss(_sid("b"), p["root"]), {}, 0))
    p = lab.project("b-paused", git=False)
    _w(p["root"] / ".deepmap" / "autosync.json",
       {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 3, "backoff_until_epoch": None,
        "paused": {"reason": "failures", "since_epoch": time.time() - 60, "detail": "x"}})
    cases.append(("auto-sync paused (autosync.json)", p, _ss(_sid("b"), p["root"]), {}, 1))
    for label, pr_, payload, extra, heals in cases:
        h = Path(extra.pop("DEEPMAP_HOME")) if "DEEPMAP_HOME" in extra else home
        res = lab.run("deepmap_sessionstart_heal", payload, h, **extra)
        check(f"{label}: no background refresh; heal spawned {heals}x; exit 0, no stdout",
              not _runner_of(res) and len(_heal_of(res)) == heals and res["rc"] == 0 and res["out"] == ""
              and not (pr_["root"] / ".deepmap" / "autosync_spawn.json").exists(),
              [x["argv"][1:4] for x in res["popen"]])

    p = lab.project("b-heal0", git=False)
    res = lab.run("deepmap_sessionstart_heal", _ss(_sid("b"), p["root"]), home, DEEPMAP_HEAL="0")
    check("DEEPMAP_HEAL=0 -> no heal, the background refresh still starts",
          not _heal_of(res) and len(_runner_of(res)) == 1 and len(res["popen"]) == 1,
          [x["argv"][1:4] for x in res["popen"]])

    p = lab.project("b-fail", git=False)
    res = lab.run("deepmap_sessionstart_heal", _ss(_sid("b"), p["root"]), home, DMAS_POPEN_FAIL="--unattended")
    check("the runner cannot start (OSError) -> the heal was still spawned first, exit 0, no stdout, no spawn record",
          len(_heal_of(res)) == 1 and res["popen"][0] in _heal_of(res) and res["rc"] == 0 and res["out"] == ""
          and not (p["root"] / ".deepmap" / "autosync_spawn.json").exists(), [x["argv"][1:4] for x in res["popen"]])

    # log rotation
    p = lab.project("b-rot", git=False)
    big = p["root"] / ".deepmap" / "autosync.log"
    big.write_bytes(b"x" * (256 * 1024 + 10))
    lab.run("deepmap_sessionstart_heal", _ss(_sid("b"), p["root"]), home)
    rot = p["root"] / ".deepmap" / "autosync.log.1"
    check("an autosync.log past 256 KB is rotated to autosync.log.1 before the new header",
          rot.is_file() and rot.stat().st_size > 256 * 1024 and big.is_file() and big.stat().st_size < 4096,
          (rot.exists(), big.stat().st_size if big.exists() else None))


def gate_b2_log_append(lab: Lab) -> None:
    say("\n-- B2: two overlapping session starts keep both headers and both children's output in one log --")
    drv = lab.base / "lograce.py"
    if not drv.is_file():
        drv.write_text(LOGRACE, encoding="utf-8")
    hooks = lab.plugin / "hooks"
    for mode, log in (("autosync", "autosync.log"), ("heal", "heal.log")):
        pr = lab.project(f"b2-{mode}", git=False)
        p = subprocess.run([PY, "-X", "utf8", str(drv), str(hooks), str(pr["root"]), mode], capture_output=True,
                           timeout=400, env=lab.env(lab.home("b2"), PYTHONPATH=None))
        try:
            got = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception:
            check(f"{log}: the overlap driver ran", False, (p.returncode, p.stderr.decode("utf-8", "replace")[-600:]))
            continue
        text = got.get("text") or ""
        lines = text.splitlines()
        heads = [ln for ln in lines if ln.startswith("--- ")]
        want = ("sess-A", "sess-B") if mode == "autosync" else ("[test-A]", "[test-B]")
        check(f"{log}: both runs' headers survive, one each (a second session's header is never overwritten)",
              len(heads) == 2 and all(sum(w in h for h in heads) == 1 for w in want), heads or text[-400:])
        for tag in ("A", "B"):
            check(f"{log}: child {tag}'s 20 stdout lines and its stderr line are all there, each intact",
                  all(lines.count(f"{tag} line {i}") == 1 for i in range(20))
                  and lines.count(f"{tag} stderr end") == 1, text[-600:])
        check(f"{log}: both children exited 0 (the driver raised nothing)",
              got.get("procs") == [0, 0] and not got.get("error"), (got.get("procs"), got.get("error")))
        h_a = next((i for i, ln in enumerate(lines) if ln.startswith("--- ") and want[0] in ln), None)
        h_b = next((i for i, ln in enumerate(lines) if ln.startswith("--- ") and want[1] in ln), None)
        check(f"{log}: in arrival order — A's header, A's first line, then B's header",
              h_a is not None and h_b is not None and h_a < lines.index("A line 0") < h_b
              if "A line 0" in lines else False, (h_a, h_b))
    pr = lab.project("b2-fallback", git=False)
    p = subprocess.run([PY, "-X", "utf8", str(drv), str(hooks), str(pr["root"]), "fallback"], capture_output=True,
                       timeout=120, env=lab.env(lab.home("b2"), PYTHONPATH=None))
    try:
        got = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        got = {"raised": p.stderr.decode("utf-8", "replace")[-600:]}
    check("no append-only handle (Windows CreateFile fails) -> the log still opens, appending (nothing truncated)",
          got.get("text") == "kept\nappended\n" and (got.get("create_calls") == 1 if _WIN else True), got)


# =========================================================================== C: the SessionStart notify
def _notify(lab, pr, home, sid, **extra):
    return lab.run("deepmap_sessionstart_notify", _ss(sid, pr["root"]), home, **extra)


def _session(root: Path, sid: str) -> dict:
    try:
        return json.loads((root / ".deepmap" / "session_health.json").read_text(encoding="utf-8"))["sessions"][sid]
    except Exception:
        return {}


def gate_c_notify(lab: Lab) -> None:
    say("\n-- C: the SessionStart notify says the graph is being refreshed in the background (when it will be) --")
    home = lab.home("c")
    pr = lab.project("c-stale", commits=2)
    c1, c2 = pr["shas"]
    sid = _sid("c")
    res = _notify(lab, pr, home, sid)
    want = ANNOUNCE_TEXT + AUTO_STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)", took="")
    check("HEAD moved, auto-sync on -> ANNOUNCE_TEXT + AUTO_STALE_SUFFIX (exact)", res["text"] == want,
          res["text"] or res)
    ent = _session(pr["root"], sid)
    check("session_health records auto=\"stale\", HEAD and the commit distance (for later git-free follow-ups)",
          ent.get("auto") == "stale" and ent.get("head") == c2[:7] and ent.get("distance") == 1, ent)
    gl = [x for x in res["runs"] if Path(x["argv"][0]).name.lower().startswith("git")]
    check("every git the notify ran had GIT_OPTIONAL_LOCKS=0 (H5)", gl and all(x["glk"] == "0" for x in gl),
          [(x["argv"][-2:], x["glk"]) for x in gl])

    pr = lab.project("c-took", commits=2, manifest={"sync_wall_s": 272.0})
    c1, c2 = pr["shas"]
    res = _notify(lab, pr, home, _sid("c"))
    want = ANNOUNCE_TEXT + AUTO_STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)",
                                                    took="; its last sync took about 5 min")
    check("the manifest's sync_wall_s -> \"(low CPU priority; its last sync took about 5 min)\"",
          res["text"] == want,
          res["text"])

    pr = lab.project("c-dirty", manifest={"dirty": True, "built_at_dirty": True})
    c1 = pr["shas"][0]
    sid = _sid("c")
    res = _notify(lab, pr, home, sid)
    want = ANNOUNCE_TEXT + AUTO_EDITED_SUFFIX.format(
        what=f"it was built from uncommitted changes on top of {c1[:7]}", took="")
    check("built dirty, HEAD unchanged -> AUTO_EDITED_SUFFIX (the uncommitted-changes wording, exact)",
          res["text"] == want, res["text"])
    check("session_health auto=\"edited\"", _session(pr["root"], sid).get("auto") == "edited")

    seen = time.time() - 600
    pr = lab.project("c-edited", manifest={"edits_seen_through_epoch": seen, "edits_seen_through_utc": _utc(seen)})
    _w(pr["root"] / ".deepmap" / "last_source_edit.json",
       {"schema": 1, "ts_utc": _utc(seen + 60), "ts_epoch": seen + 60, "path": "src/app.py", "session_id": "old"})
    res = _notify(lab, pr, home, _sid("c"))
    want = ANNOUNCE_TEXT + AUTO_EDITED_SUFFIX.format(what="src/app.py was edited after it was built", took="")
    check("a source edit recorded after the build saw the tree -> AUTO_EDITED_SUFFIX naming the file (exact)",
          res["text"] == want, res["text"])

    pr = lab.project("c-caution", commits=2, manifest={"verdict": None})
    c1, c2 = pr["shas"]
    sid = _sid("c")
    res = _notify(lab, pr, home, sid)
    want = AUTO_STALE_CAUTION_TEXT.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)",
                                          health=HEALTH_LEGACY, took="")
    check("a legacy graph (no verdict) whose HEAD moved -> AUTO_STALE_CAUTION_TEXT (exact)", res["text"] == want,
          res["text"])
    check("session_health told=\"stale\", auto=\"caution\"",
          _session(pr["root"], sid).get("auto") == "caution" and _session(pr["root"], sid).get("told") == "stale")

    # the heal and notify hooks run side by side: THIS session's runner may record its start (last_session_start)
    # before notify asks for the cooldown — that start must not silence the announcement of that very run
    def race(tag, *, spawn_sid, ls_ago, spawn_ago=2.0):
        pr = lab.project(tag, commits=2)
        sid = _sid("c")
        now = time.time()
        _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
           {"schema": 1, "session_id": sid if spawn_sid == "this" else "an-older-session", "source": "startup",
            "spawned_utc": _utc(now - spawn_ago), "spawned_epoch": now - spawn_ago, "pid": os.getpid(),
            "pid_created": None, "interpreter": [PY], "interpreter_source": "hook"})
        _w(pr["root"] / ".deepmap" / "autosync.json",
           {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 0, "backoff_until_epoch": None,
            "paused": None, "last_session_start": {"started_epoch": now - ls_ago, "head": pr["shas"][1]}})
        res = _notify(lab, pr, home, sid)
        c1, c2 = pr["shas"]
        auto_text = ANNOUNCE_TEXT + AUTO_STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)",
                                                             took="")
        legacy = ANNOUNCE_TEXT + STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)")
        return res["text"], auto_text, legacy
    got, auto_text, _legacy = race("c-race-own", spawn_sid="this", ls_ago=1.0)
    check("THIS session's runner recorded its start before notify ran -> still the AUTO text (not a cooldown)",
          got == auto_text, got)
    got, _auto, legacy = race("c-race-prev", spawn_sid="this", ls_ago=600.0)
    check("this session's spawn, but the recorded start is an EARLIER session's (10 min ago) -> the cooldown stands, "
          "today's text", got == legacy, got)
    got, _auto, legacy = race("c-race-other", spawn_sid="other", ls_ago=1.0)
    check("a fresh start recorded for ANOTHER session's spawn -> the cooldown stands, today's text", got == legacy, got)

    for off_label, extra in (("auto-sync on", {}), ("auto-sync off", {"DEEPMAP_AUTO_SYNC": "0"})):
        pr = lab.project("c-kept", manifest={"stale_kept": 3})
        res = _notify(lab, pr, home, _sid("c"), **extra)
        check(f"stale_kept=3, fresh, {off_label} -> ANNOUNCE_TEXT + STALE_KEPT_NOTE (the verdict stays READY)",
              res["text"] == ANNOUNCE_TEXT + STALE_KEPT_NOTE.format(n=3), res["text"])

    # today's texts verbatim when this session's refresh will NOT run
    legacy = []
    pr = lab.project("c-cool", commits=2)
    _w(pr["root"] / ".deepmap" / "autosync.json",
       {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 0, "backoff_until_epoch": None,
        "paused": None, "last_session_start": {"started_epoch": time.time() - 60, "head": pr["shas"][1]}})
    legacy.append(("its cooldown (a run 60 s ago at this HEAD)", pr, {}))
    pr = lab.project("c-busy", commits=2)
    (pr["root"] / ".git" / "MERGE_HEAD").write_text(pr["shas"][0] + "\n", encoding="utf-8")
    legacy.append(("a git merge in progress (.git/MERGE_HEAD)", pr, {}))
    pr = lab.project("c-off", commits=2)
    legacy.append(("DEEPMAP_AUTO_SYNC=0", pr, {"DEEPMAP_AUTO_SYNC": "0"}))
    pr = lab.project("c-unatt", commits=2)
    legacy.append(("an unattended session (ATTENDED=0)", pr, {"CLAUDE_CODE_SESSION_ATTENDED": "0"}))
    pr = lab.project("c-paused", commits=2)
    _w(pr["root"] / ".deepmap" / "autosync.json",
       {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 3, "backoff_until_epoch": None,
        "paused": {"reason": "failures", "since_epoch": time.time() - 60, "detail": "x"}})
    legacy.append(("a paused auto-sync", pr, {}))
    for label, pr, extra in legacy:
        sid = _sid("c")
        res = _notify(lab, pr, home, sid, **extra)
        c1, c2 = pr["shas"]
        want = ANNOUNCE_TEXT + STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)")
        check(f"{label} -> today's READY + STALE_SUFFIX text, verbatim", res["text"] == want, res["text"])
        ent = _session(pr["root"], sid)
        if "DEEPMAP_AUTO_SYNC" in extra or "CLAUDE_CODE_SESSION_ATTENDED" in extra:
            check(f"{label} -> session_health has exactly today's keys (no auto-sync key)",
                  set(ent) == LEGACY_HEALTH_KEYS, sorted(ent))
        else:
            check(f"{label} -> session_health auto is null (no AUTO text was used)", ent.get("auto") is None, ent)

    # DEEPMAP_HEAL=0: the background refresh still starts (spec §2.7), but nothing brings the database up for it — it
    # ends "unreachable" at once when this project's database is down, so notify announces it only while that database
    # accepts connections (the operator's NEO4J_URI stands in for this project's own route here)
    for label, up in (("the database is down", False), ("the database is up", True)):
        pr = lab.project("c-heal0", commits=2)
        c1, c2 = pr["shas"]
        sid = _sid("c")
        with _Listener() as lst:
            port = lst.port if up else _closed_port()
            res = _notify(lab, pr, home, sid, DEEPMAP_HEAL="0", NEO4J_URI=f"bolt://127.0.0.1:{port}")
        if up:
            want = ANNOUNCE_TEXT + AUTO_STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)",
                                                            took="")
        else:
            want = ANNOUNCE_TEXT + STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)")
        check(f"DEEPMAP_HEAL=0 and {label} -> {'the AUTO text' if up else 'today’s READY + STALE_SUFFIX'} (exact)",
              res["text"] == want, res["text"])
        ent = _session(pr["root"], sid)
        check(f"DEEPMAP_HEAL=0 and {label} -> session_health auto={'stale' if up else 'null'}",
              ent.get("auto") == ("stale" if up else None), ent)


# =========================================================================== C2: outside git — announce=True
def gate_c2_nongit(lab: Lab) -> None:
    say("\n-- C2: outside git the notify asks precheck with announce=True — the cooldown and a repeated skip count --")
    home = lab.home("c2")

    def ng(tag: str) -> dict:
        """A project outside git (no recorded commit) whose source was edited after the build saw it: the notify's
        only reason to announce a refresh there."""
        seen = time.time() - 600
        pr = lab.project(tag, git=False, manifest={"edits_seen_through_epoch": seen,
                                                   "edits_seen_through_utc": _utc(seen)})
        _w(pr["root"] / ".deepmap" / "last_source_edit.json",
           {"schema": 1, "ts_utc": _utc(seen + 60), "ts_epoch": seen + 60, "path": "src/app.py", "session_id": "old"})
        return pr

    def cooled(pr: dict, ago: float) -> None:
        _w(pr["root"] / ".deepmap" / "autosync.json",
           {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 0, "backoff_until_epoch": None,
            "paused": None, "last_session_start": {"started_epoch": time.time() - ago, "head": None}})

    auto_text = ANNOUNCE_TEXT + AUTO_EDITED_SUFFIX.format(what="src/app.py was edited after it was built", took="")
    pr = ng("c2-ok")
    res = _notify(lab, pr, home, _sid("c2"))
    check("outside git, a source edit since the build, nothing in the way -> the AUTO_EDITED text (the control)",
          res["text"] == auto_text, res["text"])
    pr = ng("c2-cool")
    cooled(pr, 60)
    sid = _sid("c2")
    res = _notify(lab, pr, home, sid)
    check("outside git, inside the runner's cooldown (a session-start run 60 s ago, no HEAD recorded) -> NOT "
          "announced: today's READY text alone", res["text"] == ANNOUNCE_TEXT, res["text"])
    check("...session_health auto is null (no AUTO text was used)", _session(pr["root"], sid).get("auto") is None,
          _session(pr["root"], sid))
    pr = ng("c2-cool-old")
    cooled(pr, 1200)
    res = _notify(lab, pr, home, _sid("c2"))
    check("outside git, a session-start run 20 min ago (past the 15-min cooldown) -> announced again",
          res["text"] == auto_text, res["text"])
    for code in ("needs-timing", "no-own-instance"):
        pr = ng(f"c2-skip-{code}")
        _age_manifest(pr["root"])
        _last_run(pr["root"], f"skipped:{code}", finished_ago=30)
        sid = _sid("c2")
        res = _notify(lab, pr, home, sid)
        check(f"outside git, a recorded skipped:{code} the next run would repeat -> NOT announced: today's READY text",
              res["text"] == ANNOUNCE_TEXT, res["text"])
        check(f"skipped:{code}: ...session_health auto is null", _session(pr["root"], sid).get("auto") is None)
    # this session's own runner recorded its start (no HEAD) a moment after the heal hook spawned it: still announced
    pr = ng("c2-own")
    sid = _sid("c2")
    now = time.time()
    _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
       {"schema": 1, "session_id": sid, "source": "startup", "spawned_utc": _utc(now - 2), "spawned_epoch": now - 2,
        "pid": os.getpid(), "pid_created": None, "interpreter": [PY], "interpreter_source": "hook"})
    cooled(pr, 1)
    res = _notify(lab, pr, home, sid)
    check("outside git, the 'cooldown' is THIS session's own runner starting -> still announced (the AUTO text)",
          res["text"] == auto_text, res["text"])
    # what the notify asks precheck
    spy = lab.plugin_variant("spy-plugin", SPY_PRECHECK)
    for tag, git in (("c2-spy-ng", False), ("c2-spy-git", True)):
        pr = ng(tag) if not git else lab.project(tag, commits=2)
        log = lab.base / "rec" / f"{tag}-precheck.jsonl"
        log.parent.mkdir(exist_ok=True)
        _notify(lab, pr, home, _sid("c2"), plugin=spy, DMAS_PRECHECK_LOG=str(log))
        calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
        want_head = None if not git else pr["shas"][1]
        check(f"{'outside git' if not git else 'in git'}: the notify asks precheck(trigger='session-start', "
              f"head={'None' if not git else 'HEAD'}, announce=True)",
              len(calls) == 1 and calls[0].get("announce") is True and calls[0].get("trigger") == "session-start"
              and "head" in calls[0] and calls[0]["head"] == want_head, calls)
    # an older build/ whose precheck takes no `announce`: asked the old way (no crash, the old answer)
    old = lab.plugin_variant("old-precheck-plugin", OLD_PRECHECK)
    pr = ng("c2-old")
    cooled(pr, 60)
    log = lab.base / "rec" / "c2-old-precheck.jsonl"
    res = _notify(lab, pr, home, _sid("c2"), plugin=old, DMAS_PRECHECK_LOG=str(log))
    calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
    check("a precheck WITHOUT the announce keyword (an older build/) -> asked the old way, once (no head outside git), "
          "exit 0, and the old answer (it cannot see the cooldown outside git: the AUTO text)",
          res["rc"] == 0 and res["text"] == auto_text and calls == [{"trigger": "session-start", "head": None}],
          (calls, res["text"]))


# =========================================================================== D: freshness_state / staleness / git
def gate_d_freshness(lab: Lab) -> None:
    say("\n-- D: freshness_state / staleness honour the background check; the edited kind; git_busy; no locks --")
    home = lab.home("d")
    fp = "f" * 32
    pr = lab.project("d-proof", commits=2, manifest={"source_fingerprint": fp})
    root = pr["root"]
    c1, c2 = pr["shas"]
    man = json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    mt = (root / ".deepmap" / "graph_build.json").stat().st_mtime

    def proof(**kw):
        lr = {"outcome": "fresh", "fresh_at_head": c2, "fingerprint": fp, "finished_epoch": mt + 5,
              "started_utc": _utc(mt + 2), "id": "p"}
        lr.update(kw)
        _w(root / ".deepmap" / "autosync.json", {"schema": 1, "current": None, "last_run": lr})

    proof()
    got = lab.call("staleness", home, str(root), man)
    check("a \"fresh\" verdict at the current HEAD with the manifest's fingerprint, newer than the manifest -> "
          "staleness None (a docs-only commit no longer reads stale)", got == {"ok": None, "runs": []}, got)
    got = lab.call("freshness_state", home, str(root), man, edits=True)
    st = got.get("ok") or {}
    check("freshness_state: kind None, built/head/head_full reported", st.get("kind") is None
          and st.get("built") == c1[:7] and st.get("head") == c2[:7] and st.get("head_full") == c2, got)
    for label, kw in (("another fingerprint", {"fingerprint": "e" * 32}),
                      ("finished BEFORE the manifest was written", {"finished_epoch": mt - 100}),
                      ("decided at another HEAD", {"fresh_at_head": c1}), ("an outcome other than fresh",
                                                                           {"outcome": "synced"})):
        proof(**kw)
        got = lab.call("staleness", home, str(root), man)
        check(f"the verdict is NOT honoured with {label} -> stale",
              isinstance(got.get("ok"), dict) and got["ok"].get("kind") == "stale" and got["ok"].get("distance") == 1,
              got)
    proof()
    got = lab.call("staleness", home, str(root), man, env_extra={"DEEPMAP_AUTO_SYNC": "0"})
    check("with auto-sync OFF the same verdict is not honoured (the hooks keep today's \"stale\")",
          isinstance(got.get("ok"), dict) and got["ok"].get("kind") == "stale", got)
    # an edit after the proof's check still reads as edited
    _w(root / ".deepmap" / "last_source_edit.json",
       {"schema": 1, "ts_utc": _utc(mt + 50), "ts_epoch": mt + 50, "path": "src/new.py", "session_id": "s"})
    got = lab.call("freshness_state", home, str(root), man, edits=True)
    check("a source edit AFTER the background check -> kind \"edited\" (path named) even with the fresh verdict",
          (got.get("ok") or {}).get("kind") == "edited" and (got.get("ok") or {}).get("path") == "src/new.py", got)

    seen = time.time() - 600
    pr = lab.project("d-edit", manifest={"edits_seen_through_epoch": seen})
    root, man = pr["root"], pr["manifest"]
    _w(root / ".deepmap" / "last_source_edit.json",
       {"schema": 1, "ts_utc": _utc(seen + 30), "ts_epoch": seen + 30, "path": "src/app.py", "session_id": "s"})
    got = lab.call("freshness_state", home, str(root), man, edits=True)
    check("an edit after edits_seen_through_epoch -> kind \"edited\", path src/app.py",
          (got.get("ok") or {}).get("kind") == "edited" and (got.get("ok") or {}).get("path") == "src/app.py", got)
    got = lab.call("freshness_state", home, str(root), man, edits=False)
    check("...without edits=True the kind stays None (and staleness() never says \"edited\")",
          (got.get("ok") or {}).get("kind") is None and lab.call("staleness", home, str(root), man).get("ok") is None,
          got)
    _w(root / ".deepmap" / "last_source_edit.json",
       {"schema": 1, "ts_utc": _utc(seen - 30), "ts_epoch": seen - 30, "path": "src/app.py", "session_id": "s"})
    got = lab.call("freshness_state", home, str(root), man, edits=True)
    check("an edit BEFORE edits_seen_through_epoch -> not edited", (got.get("ok") or {}).get("kind") is None, got)
    pr = lab.project("d-legacy", manifest={"built_at_utc": _utc(seen)})
    _w(pr["root"] / ".deepmap" / "last_source_edit.json",
       {"schema": 1, "ts_utc": _utc(seen + 30), "ts_epoch": seen + 30, "path": "src/x.py", "session_id": "s"})
    got = lab.call("freshness_state", home, str(pr["root"]), pr["manifest"], edits=True)
    check("a legacy manifest (built_at_utc only) -> the edit is compared with built_at_utc",
          (got.get("ok") or {}).get("kind") == "edited", got)

    pr = lab.project("d-busy")
    root = pr["root"]
    got = lab.call("git_busy", home, str(root))
    check("git_busy: a quiet repo -> None", got.get("ok") is None and "missing" not in got, got)
    gd = root / ".git"
    for marker, op, is_dir in (("rebase-merge", "rebase", True), ("rebase-apply", "rebase", True),
                               ("MERGE_HEAD", "merge", False), ("CHERRY_PICK_HEAD", "cherry-pick", False),
                               ("REVERT_HEAD", "revert", False), ("BISECT_LOG", "bisect", False),
                               ("sequencer", "sequence", True)):
        path = gd / marker
        if is_dir:
            path.mkdir()
        else:
            path.write_text("x\n", encoding="utf-8")
        got = lab.call("git_busy", home, str(root))
        check(f"git_busy: .git/{marker} -> {op!r}", got.get("ok") == op, got)
        shutil.rmtree(path) if is_dir else path.unlink()
    sub = root / "src"
    (gd / "MERGE_HEAD").write_text("x\n", encoding="utf-8")
    got = lab.call("git_busy", home, str(sub))
    check("git_busy from a subdirectory (git answers relative to it) -> 'merge'", got.get("ok") == "merge", got)
    (gd / "MERGE_HEAD").unlink()
    got = lab.call("git_busy", home, str(lab.base))
    check("git_busy outside a repository -> None", got.get("ok") is None and "missing" not in got, got)

    got = lab.call("git_head", home, str(root), pop_glk=True, record_run=True)
    runs = got.get("runs") or []
    check("GIT_OPTIONAL_LOCKS=0 in the env of git_head's git (set at import, H5)",
          got.get("ok") == pr["shas"][0] and runs and all(r.get("glk") == "0" for r in runs), got)


# =========================================================================== E: the follow-ups
def _e_proj(lab, tag, **man):
    return lab.project(tag, commits=2, manifest=man or None)


def gate_e1_running(lab: Lab, stamp, dead) -> None:
    say("\n-- E1: the follow-ups — a running background refresh (tool-result hook + nudge, one told-state) --")
    home = lab.home("e1")
    fresh_proj = lambda tag, **man: _e_proj(lab, tag, **man)   # noqa: E731
    pr = fresh_proj("e-run")
    root, c1 = pr["root"], pr["shas"][0]
    sid = _sid("e")
    _health(root, sid, start=time.time() - 30, auto="stale", head=pr["shas"][1][:7], distance=1)
    _running(root, created=stamp, started_ago=300, phase="extract")
    r1 = lab.run("deepmap_toolresult_freshness", _tool(sid, root), home)
    want = FOLLOW_RUNNING.format(ago="5 min", phase="reading the code", at=_at(c1))
    check("a live unattended refresh -> FOLLOW_RUNNING (tool-result hook, exact)", r1["text"] == want, r1["text"] or r1)
    r2 = lab.run("deepmap_toolresult_freshness", _tool(sid, root, "mcp__deepmap-search__deepmap_impact"), home)
    check("...told once: the next deepmap tool call is silent", r2["out"] == "" and r2["rc"] == 0, r2)
    r3 = lab.run("deepmap_presearch_nudge", _grep(sid, root), home)
    check("...and the nudge (same told-state) gives its reminder with AUTO_RUNNING_NOTE instead of the stale note",
          r3["text"] == NUDGE_TEXT + AUTO_RUNNING_NOTE, r3["text"])
    gl = [x for x in r1["runs"] + r2["runs"] if Path(x["argv"][0]).name.lower().startswith("git")]
    check("the tool-result hook runs no git", not gl, gl)

    for label, kw in (("a DEAD holder (another process's start stamp)", {"created": dead}),
                      ("a holder whose origin is manual", {"created": stamp, "origin": "manual"})):
        pr = fresh_proj("e-run2")
        sid = _sid("e")
        _health(pr["root"], sid, start=time.time() - 30, auto="stale")
        _running(pr["root"], **kw)
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
        check(f"{label} -> no running note", res["out"] == "", res["text"])


def gate_e2_outcomes(lab: Lab, stamp, dead) -> None:
    say("\n-- E2: the follow-ups — how the background refresh ended (once per session) --")
    home = lab.home("e2")
    fresh_proj = lambda tag, **man: _e_proj(lab, tag, **man)   # noqa: E731

    def outcome(tag, oc, *, auto="stale", start_ago=900.0, distance=1, man=None, **lr):
        pr = fresh_proj(tag, **(man or {}))
        sid = _sid("e")
        _health(pr["root"], sid, start=time.time() - start_ago, auto=auto, distance=distance)
        _last_run(pr["root"], oc, **lr)
        return pr, sid

    pr, sid = outcome("e-synced", "synced", head=None, finished_ago=600)
    c2 = pr["shas"][1]
    _last_run(pr["root"], "synced", head=c2, finished_ago=600, verdict="READY")
    rn = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home)
    want = _synced_text(ago="10 min", head=c2[:7], dirty="", degraded="", kept="", deferred="")
    check("synced -> FOLLOW_SYNCED through the nudge (exact)", rn["text"] == want, rn["text"])
    rt = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("...said once per session: the tool-result hook is then silent", rt["out"] == "", rt["text"])
    rn2 = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home)
    check("...and the nudge's once-per-session reminder still comes on the next search",
          (rn2["text"] or "").startswith(NUDGE_TEXT), rn2["text"])
    sid2 = _sid("e")
    _health(pr["root"], sid2, start=time.time() - 900, auto=None)
    rt2 = lab.run("deepmap_toolresult_freshness", _tool(sid2, pr["root"]), home)
    check("another session is told the same outcome (told-state is per session; synced needs no auto told)",
          rt2["text"] == want, rt2["text"])

    pr, sid = outcome("e-synced2", "synced", finished_ago=600, dirty=True, verdict="DEGRADED",
                      reasons=["LOW COVERAGE for Ruby — 40 files on disk but only 2 extracted node(s); details"],
                      stale_kept=4, embeddings_deferred=True)
    c2 = pr["shas"][1]
    _last_run(pr["root"], "synced", finished_ago=600, dirty=True, verdict="DEGRADED", head=c2,
              reasons=["LOW COVERAGE for Ruby — 40 files on disk but only 2 extracted node(s); details."],
              stale_kept=4, embeddings_deferred=True)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = _synced_text(ago="10 min", head=c2[:7], dirty=SYNCED_DIRTY,
                        degraded=SYNCED_DEGRADED.format(
                            reason1="LOW COVERAGE for Ruby — 40 files on disk but only 2 extracted node(s); details"),
                        kept=SYNCED_KEPT.format(n=4), deferred=SYNCED_DEFERRED)
    check("synced + dirty + DEGRADED + kept + deferred -> every clause (exact)", res["text"] == want, res["text"])
    pr, sid = outcome("e-synced3", "synced", finished_ago=600, embeddings_deferred=True)
    _last_run(pr["root"], "synced", finished_ago=600, head=pr["shas"][1], embeddings_deferred=True)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = _synced_text(ago="10 min", head=pr["shas"][1][:7], dirty="", degraded="", kept="", deferred=SYNCED_DEFERRED)
    check("synced + deferred only -> one space before the deferred sentence (exact)", res["text"] == want,
          res["text"])

    for oc, auto, want_fn in (
            ("fresh", "stale", lambda p: FOLLOW_FRESH.format(head=p["shas"][1][:7])),
            ("fresh", None, lambda p: None),
            ("preempted", "edited", lambda p: FOLLOW_PREEMPTED),
            ("preempted", None, lambda p: None),
            ("queued", "stale", lambda p: None),
            ("covered", "stale", lambda p: None)):
        pr, sid = outcome(f"e-{oc}", oc, auto=auto)
        if oc == "fresh":
            _last_run(pr["root"], oc, fresh_at_head=pr["shas"][1])
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
        w = want_fn(pr)
        check(f"{oc} (told auto: {auto}) -> {'its follow-up (exact)' if w else 'silent'}",
              res["text"] == w if w else res["out"] == "", res["text"])

    pr, sid = outcome("e-np", "not-pushed:engine-timeout",
                      reason="the engine ran out of its time budget (1200 s) and the ast fallback was refused.",
                      advice=" (a refresh you start is not limited by the background time budget; if it also runs "
                             "out of time, add DEEPMAP_GRAPHIFY_TIMEOUT=2400)", suggest="refresh", auto=None)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = FOLLOW_NOT_PUSHED.format(
        reason="the engine ran out of its time budget (1200 s) and the ast fallback was refused",
        build=_b(pr["shas"][0]), advice=" (a refresh you start is not limited by the background time budget; if it "
                                        "also runs out of time, add DEEPMAP_GRAPHIFY_TIMEOUT=2400)")
    check("not-pushed:* -> FOLLOW_NOT_PUSHED with reason + advice (exact; even when notify told no AUTO text)",
          res["text"] == want, res["text"])
    pr, sid = outcome("e-failed", "failed:error", reason="Traceback: boom", auto=None)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("failed:* -> FOLLOW_FAILED (exact)",
          res["text"] == FOLLOW_FAILED.format(reason="Traceback: boom", build=_b(pr["shas"][0])), res["text"])
    why = ("its last sync took 25 min (the background limit is 20 min; DEEPMAP_AUTO_SYNC_MAX_S raises it, 0 removes "
           "it)")
    pr, sid = outcome("e-skip", "skipped:too-slow", reason=why, suggest="refresh")
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = FOLLOW_SKIPPED.format(why=why, build=_b(pr["shas"][0]), distance=" (1 commit later)",
                                 suggest=", and suggest the user run /deep-map:refresh")
    check("skipped:* (told auto) -> FOLLOW_SKIPPED with the distance notify recorded and the suggestion (exact)",
          res["text"] == want, res["text"])
    pr, sid = outcome("e-unr", "unreachable", reason="this project's database did not come up within 3 minutes of "
                      "the session start", suggest="doctor", distance=None)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = FOLLOW_SKIPPED.format(why="this project's database did not come up within 3 minutes of the session start",
                                 build=_b(pr["shas"][0]), distance="",
                                 suggest=", and suggest the user run /deep-map:doctor")
    check("unreachable (told auto) -> FOLLOW_SKIPPED suggesting /deep-map:doctor (exact)", res["text"] == want,
          res["text"])
    for oc, auto in (("skipped:too-slow", None), ("skipped:foreign-owner", "stale"),
                     ("skipped:setup-blocked", "stale")):
        pr, sid = outcome(f"e-{oc.split(':')[1]}", oc, reason="x", suggest="setup", auto=auto)
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
        check(f"{oc} (told auto: {auto}) -> silent", res["out"] == "", res["text"])
    pr, sid = outcome("e-old", "synced", finished_ago=1200, start_ago=600, auto=None)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("an outcome that finished before the session started -> silent", res["out"] == "", res["text"])
    pr, sid = outcome("e-old2", "failed:error", reason="boom", finished_ago=1200, start_ago=100)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("...also when notify announced a refresh (no 'failed' from an earlier run; not 120 s yet)",
          res["out"] == "", res["text"])
    del stamp, dead


def gate_e3_not_started(lab: Lab, stamp, dead) -> None:
    say("\n-- E3: the follow-ups — the refresh announced at session start did not start --")
    home = lab.home("e3")
    fresh_proj = lambda tag, **man: _e_proj(lab, tag, **man)   # noqa: E731

    def ns(tag, start_ago=200.0):
        pr = fresh_proj(tag)
        sid = _sid("e")
        _health(pr["root"], sid, start=time.time() - start_ago, auto="stale", distance=1)
        return pr, sid
    pr, sid = ns("e-ns")
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    want = FOLLOW_NOT_STARTED.format(build=_b(pr["shas"][0]), distance=" (1 commit later)")
    check("told auto, 120 s passed, nothing started / running / recorded -> FOLLOW_NOT_STARTED (exact)",
          res["text"] == want, res["text"])
    res = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home)
    check("...once: the nudge then gives its plain reminder (no repeat)",
          res["text"] is not None and FOLLOW_NOT_STARTED[:40] not in res["text"], res["text"])
    pr, sid = ns("e-ns-young", start_ago=60)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("only 60 s after the session start -> silent", res["out"] == "", res["text"])
    pr, sid = ns("e-ns-live")
    start = _session(pr["root"], sid)["start"]
    _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
       {"schema": 1, "session_id": sid, "source": "startup", "spawned_utc": _utc(start + 1), "spawned_epoch": start + 1,
        "pid": os.getpid(), "pid_created": stamp, "interpreter": [PY], "interpreter_source": "hook"})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("a runner this session started is still alive -> silent", res["out"] == "", res["text"])
    pr, sid = ns("e-ns-dead")
    start = _session(pr["root"], sid)["start"]
    _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
       {"schema": 1, "session_id": sid, "source": "startup", "spawned_utc": _utc(start + 1), "spawned_epoch": start + 1,
        "pid": os.getpid(), "pid_created": dead, "interpreter": [PY], "interpreter_source": "hook"})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("the runner this session started is gone without a record -> FOLLOW_NOT_STARTED",
          res["text"] == FOLLOW_NOT_STARTED.format(build=_b(pr["shas"][0]), distance=" (1 commit later)"),
          res["text"])
    pr, sid = ns("e-ns-cur")
    _w(pr["root"] / ".deepmap" / "autosync.json",
       {"schema": 1, "last_run": None, "current": {"id": "c", "pid": os.getpid(), "pid_created": stamp,
                                                   "session_id": None, "trigger": "session-start",
                                                   "started_utc": _utc(time.time() - 100),
                                                   "started_epoch": time.time() - 100, "state": "waiting-db"}})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("a live runner still waiting (autosync.json current) -> silent", res["out"] == "", res["text"])
    pr, sid = ns("e-ns-other")
    start = _session(pr["root"], sid)["start"]
    _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
       {"schema": 1, "session_id": "an-older-session", "source": "startup", "spawned_utc": _utc(start - 3600),
        "spawned_epoch": start - 3600, "pid": os.getpid(), "pid_created": stamp, "interpreter": [PY],
        "interpreter_source": "hook"})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("a spawn record from an EARLIER session does not count -> FOLLOW_NOT_STARTED",
          res["text"] == FOLLOW_NOT_STARTED.format(build=_b(pr["shas"][0]), distance=" (1 commit later)"),
          res["text"])


def gate_e4_silence(lab: Lab, stamp, dead) -> None:
    say("\n-- E4: the follow-ups stay silent under a refusal, a blocker, a foreign told, every opt-out --")
    home = lab.home("e4")
    fresh_proj = lambda tag, **man: _e_proj(lab, tag, **man)   # noqa: E731

    def synced_proj(tag, **health):
        pr = fresh_proj(tag)
        sid = _sid("e")
        _health(pr["root"], sid, start=time.time() - 900, **health)
        _last_run(pr["root"], "synced", head=pr["shas"][1])
        return pr, sid
    hh = lab.home("e-refused")
    pr, sid = synced_proj("e-refused", heal_path=str(hh / "run" / "heal-k1.json"))
    _w(hh / "run" / "heal-k1.json", {"ts": _utc(time.time()), "neo4j_ok": False, "mcp_ok": False,
                                     "refused": True, "refusal_kind": "foreign-base-claim",
                                     "reason": "the Neo4j on 7687 belongs to project X"})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), hh)
    check("a heal POLICY refusal for this project -> silent", res["out"] == "", res["text"])
    hb = lab.home("e-blocked")
    pr, sid = synced_proj("e-blocked", heal_path=str(hb / "run" / "heal-k2.json"))
    _w(hb / "run" / "setup-blocked-k2.json", {"kind": "mcp-blocked", "reason": "held", "servers": ["deepmap-search"],
                                              "ports": [8787], "pids": [], "ts_epoch": time.time()})
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), hb)
    check("setup's MCP blocker -> silent", res["out"] == "", res["text"])
    for label, health, extra in (("told foreign (.mcp.json is another project's)", {"told": "foreign"}, {}),
                                 ("DEEPMAP_HOOKS=0", {}, {"DEEPMAP_HOOKS": "0"}),
                                 ("DEEPMAP_FRESHNESS_NOTE=0", {}, {"DEEPMAP_FRESHNESS_NOTE": "0"}),
                                 ("auto-sync off (DEEPMAP_AUTO_SYNC=0)", {}, {"DEEPMAP_AUTO_SYNC": "0"}),
                                 ("auto-sync off (an unattended session)", {}, {"CLAUDE_CODE_SESSION_ATTENDED": "0"})):
        pr, sid = synced_proj("e-quiet", **health)
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home, **extra)
        check(f"{label} -> the tool-result hook is silent", res["out"] == "" and res["rc"] == 0, res["text"])
    pr = fresh_proj("e-nohealth")
    _last_run(pr["root"], "synced", head=pr["shas"][1])
    res = lab.run("deepmap_toolresult_freshness", _tool(_sid("e"), pr["root"]), home)
    check("no session_health for the session (notify said nothing) -> silent", res["out"] == "", res["text"])
    pr, sid = synced_proj("e-nudge-off")
    res = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home, DEEPMAP_AUTO_SYNC="0")
    check("auto-sync off -> the nudge says only today's reminder (no follow-up, no AUTO note)",
          (res["text"] or "").startswith(NUDGE_TEXT) and "background refresh" not in (res["text"] or ""), res["text"])
    # DEEPMAP_FRESHNESS_NOTE=0 silences the follow-ups on BOTH channels — the rule itself honours it, so the nudge (which
    # has its own opt-out for its reminder) says no follow-up either, and nothing is marked told
    pr, sid = synced_proj("e-nudge-fn0")
    res = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home, DEEPMAP_FRESHNESS_NOTE="0")
    check("DEEPMAP_FRESHNESS_NOTE=0 -> the nudge says only its reminder (no follow-up)",
          (res["text"] or "").startswith(NUDGE_TEXT) and "DeepMap note" not in (res["text"] or ""), res["text"])
    check("...and no follow-up was marked told", not (pr["root"] / ".deepmap" / "autosync_told.json").exists())
    got = lab.call("autosync_followup", home, str(pr["root"]), _tool(sid, pr["root"]), None,
                   env_extra={"DEEPMAP_FRESHNESS_NOTE": "0"})
    check("deepmap_hook_state.autosync_followup itself answers None under DEEPMAP_FRESHNESS_NOTE=0",
          "ok" in got and got["ok"] is None, got)
    got = lab.call("autosync_followup", home, str(pr["root"]), _tool(sid, pr["root"]), None)
    check("...and the same call without the opt-out gives the synced follow-up (the control)",
          isinstance(got.get("ok"), str) and got["ok"].startswith("DeepMap note: the background refresh finished"), got)
    del stamp, dead


def gate_e5_end_to_end(lab: Lab, stamp, dead) -> None:
    say("\n-- E5: end to end — notify announces, the tool-result hook and the nudge follow --")
    home = lab.home("e5")
    pr = lab.project("e-e2e", commits=2)
    sid = _sid("e")
    lab.run("deepmap_sessionstart_notify", _ss(sid, pr["root"]), home)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
    check("end to end: right after notify announced the refresh, nothing has happened yet -> the tool-result hook is "
          "silent", res["out"] == "", res["text"])
    res = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home)
    check("...and the nudge's reminder says a refresh is under way (AUTO_RUNNING_NOTE), not \"suggest refresh\"",
          res["text"] == NUDGE_TEXT + AUTO_RUNNING_NOTE, res["text"])
    _last_run(pr["root"], "synced", head=pr["shas"][1], finished_ago=5)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"], "mcp__deepmap-search__deepmap_cypher"), home)
    check("...then the runner records `synced` -> the next deepmap tool call (cypher too) says it finished",
          re.match(r"DeepMap note: the background refresh finished \d+ s ago — the graph now reflects "
                   + re.escape(pr["shas"][1][:7]) + r"\. ", res["text"] or "") is not None, res["text"])
    del stamp, dead


def gate_e6_nongit_words(lab: Lab, stamp, dead) -> None:
    say("\n-- E6: outside git (no recorded commit) the follow-ups say 'answer from a previous build' --")
    home = lab.home("e6")

    def proj(tag, start_ago=900.0):
        pr = lab.project(tag, git=False)
        sid = _sid("e6")
        _health(pr["root"], sid, start=time.time() - start_ago, auto="edited", distance=None)
        return pr, sid

    cases = []
    pr, sid = proj("e6-failed")
    _last_run(pr["root"], "failed:error", reason="boom")
    cases.append(("failed", pr, sid, FOLLOW_FAILED.format(reason="boom", build=NOGIT_BUILD)))
    pr, sid = proj("e6-np")
    _last_run(pr["root"], "not-pushed:veto", reason="a sync you started took over")
    cases.append(("not-pushed", pr, sid, FOLLOW_NOT_PUSHED.format(reason="a sync you started took over",
                                                                  build=NOGIT_BUILD, advice="")))
    pr, sid = proj("e6-skip")
    _last_run(pr["root"], "skipped:too-slow", reason="its last sync took 25 min", suggest="refresh")
    cases.append(("skipped", pr, sid, FOLLOW_SKIPPED.format(why="its last sync took 25 min", build=NOGIT_BUILD,
                                                            distance="",
                                                            suggest=", and suggest the user run /deep-map:refresh")))
    pr, sid = proj("e6-ns", start_ago=200.0)
    cases.append(("not-started", pr, sid, FOLLOW_NOT_STARTED.format(build=NOGIT_BUILD, distance="")))
    pr, sid = proj("e6-run")
    _running(pr["root"], created=stamp, started_ago=300, phase="extract")
    cases.append(("running", pr, sid, FOLLOW_RUNNING.format(ago="5 min", phase="reading the code", at="")))
    for label, pr, sid, want in cases:
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home)
        text = res["text"] or ""
        check(f"outside git, {label}: the follow-up names no commit (exact)", text == want, text or res)
        check(f"outside git, {label}: ...never 'the build at a previous build' / '(at )'",
              bool(text) and "the build at a previous build" not in text and "(at )" not in text, text)
    check("outside git: 'answer from a previous build' is what failed / not-pushed / skipped / not-started say",
          all("answer from a previous build" in w for lbl, _p, _s, w in cases if lbl != "running"))
    del dead


# =========================================================================== F: the edit hook
def gate_f_edit(lab: Lab) -> None:
    say("\n-- F: the edit hook — source edits inside the project only; the record; today's text when off --")
    home = lab.home("f")
    pr = lab.project("f-src")
    root = pr["root"]
    sid = _sid("f")
    res = lab.run("deepmap_postedit_syncflag", _edit(sid, root, root / "src" / "app.py"), home)
    check("a source file inside the project -> AUTO_SYNC_TEXT naming it (no git hook: no commit clause; exact)",
          res["text"] == AUTO_SYNC_TEXT.format(file="src/app.py", commit=""), res["text"])
    rec = {}
    try:
        rec = json.loads((root / ".deepmap" / "last_source_edit.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    check("...and .deepmap/last_source_edit.json records it (schema, path, session, ts)",
          rec.get("schema") == 1 and rec.get("path") == "src/app.py" and rec.get("session_id") == sid
          and isinstance(rec.get("ts_epoch"), (int, float)) and abs(rec["ts_epoch"] - time.time()) < 120, rec)
    res = lab.run("deepmap_postedit_syncflag", _edit(sid, root, root / "src" / "m.py"), home)
    rec2 = json.loads((root / ".deepmap" / "last_source_edit.json").read_text(encoding="utf-8")) \
        if (root / ".deepmap" / "last_source_edit.json").is_file() else {}
    check("a second source edit: no second note (once per session), the record names the newest file",
          res["out"] == "" and rec2.get("path") == "src/m.py", (res["text"], rec2))

    pr = lab.project("f-md")
    root = pr["root"]
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "README.md"), home)
    check("a .md edit -> silent; no record; the note counter untouched",
          res["out"] == "" and not (root / ".deepmap" / "last_source_edit.json").exists()
          and not (root / ".deepmap" / "postedit_sync_state.json").exists(), res)
    out = lab.base / "p" / "f-outside.py"
    out.write_text("x = 1\n", encoding="utf-8")
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, out), home)
    check("a source file OUTSIDE the project -> silent, nothing recorded",
          res["out"] == "" and not (root / ".deepmap" / "last_source_edit.json").exists(), res)
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / ".deepmap" / "x.py"), home)
    check("a file under .deepmap -> silent, nothing recorded",
          res["out"] == "" and not (root / ".deepmap" / "last_source_edit.json").exists(), res)

    # the "refreshes by itself at the next session start" promise only when that start will run it: auto_sync.precheck
    # (paused / backing off / too slow keep it from running) and, with DEEPMAP_HEAL=0, a database that is up
    now = time.time()
    blocked = (
        ("its last sync took longer than the background limit (too-slow)", {"sync_wall_s": 5000.0}, None, {}),
        ("auto-sync paused after failures", None,
         {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 3, "backoff_until_epoch": None,
          "paused": {"reason": "failures", "since_epoch": now - 60, "detail": "x"}}, {}),
        ("auto-sync backing off after a failure", None,
         {"schema": 1, "current": None, "last_run": None, "consecutive_failures": 1, "backoff_until_epoch": now + 3600,
          "last_failure": {"finished_epoch": now - 60, "reason": "x"}, "paused": None}, {}),
        ("DEEPMAP_HEAL=0 and the database is down", None, None,
         {"DEEPMAP_HEAL": "0", "NEO4J_URI": f"bolt://127.0.0.1:{_closed_port()}"}),
    )
    for label, man, state, extra in blocked:
        pr = lab.project("f-blocked", manifest=man)
        root = pr["root"]
        if state is not None:
            _w(root / ".deepmap" / "autosync.json", state)
        sid = _sid("f")
        res = lab.run("deepmap_postedit_syncflag", _edit(sid, root, root / "src" / "app.py"), home, **extra)
        check(f"{label}: a source edit -> today's SYNC_TEXT (no promise of a refresh at the next session start)",
              res["text"] == SYNC_TEXT, res["text"])
        check(f"{label}: ...the source edit is still recorded (.deepmap/last_source_edit.json) for the next session",
              (root / ".deepmap" / "last_source_edit.json").is_file())
        if label.startswith("its last sync"):
            pr2 = lab.project("f-blocked-md", manifest=man)
            res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), pr2["root"], pr2["root"] / "README.md"), home)
            check(f"{label}: a .md edit -> silent, nothing recorded (auto-sync on: only source files count)",
                  res["out"] == "" and not (pr2["root"] / ".deepmap" / "last_source_edit.json").exists(), res["text"])

    # a recorded skip the next session start would end with again (auto_sync._repeating_skip: the manifest unchanged
    # since that run) is no promise either — the edit hook asks the question the SessionStart notify asks before it
    # announces a refresh; the manifest rewritten after that run (a refresh / setup), or needs-timing once the manifest
    # carries its sync time, lifts it
    def _aged(root: Path) -> None:
        old = time.time() - 3600
        os.utime(root / ".deepmap" / "graph_build.json", (old, old))

    for code in ("needs-timing", "recipe:route", "no-own-instance"):
        pr = lab.project(f"f-repeat-{code.replace(':', '-')}", commits=2)
        root = pr["root"]
        _aged(root)
        _last_run(root, f"skipped:{code}", finished_ago=30)
        res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "src" / "app.py"), home)
        check(f"a recorded skipped:{code} the next start would repeat: a source edit -> exactly today's SYNC_TEXT "
              "(no promise of a refresh at the next session start)", res["text"] == SYNC_TEXT, res["text"])
        check(f"skipped:{code}: ...the source edit is still recorded for the next session",
              (root / ".deepmap" / "last_source_edit.json").is_file())
        rn = lab.run("deepmap_sessionstart_notify", _ss(_sid("f"), root), home)
        check(f"skipped:{code}: ...and the next session's notify agrees: it announces no background refresh",
              bool(rn["text"]) and "in the background now" not in rn["text"]
              and "/deep-map:refresh" in rn["text"], rn["text"])
    lifted = (("recipe:route, the manifest rewritten after that run", "recipe:route", None, False),
              ("needs-timing, the manifest now carries its sync time", "needs-timing", {"sync_wall_s": 60.0}, True))
    for label, code, man, age in lifted:
        pr = lab.project("f-repeat-lifted", manifest=man)
        root = pr["root"]
        if age:
            _aged(root)
        _last_run(root, f"skipped:{code}", finished_ago=600 if not age else 30)
        res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "src" / "app.py"), home)
        check(f"a recorded skipped:{label} -> AUTO_SYNC_TEXT (the next start will run it)",
              res["text"] == AUTO_SYNC_TEXT.format(file="src/app.py", commit=""), res["text"])

    pr = lab.project("f-heal0-up")
    root = pr["root"]
    with _Listener() as lst:
        res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "src" / "app.py"), home,
                      DEEPMAP_HEAL="0", NEO4J_URI=f"bolt://127.0.0.1:{lst.port}")
    check("DEEPMAP_HEAL=0 and the database is up -> AUTO_SYNC_TEXT (the next session start can refresh it)",
          res["text"] == AUTO_SYNC_TEXT.format(file="src/app.py", commit=""), res["text"])

    pr = lab.project("f-off")
    root = pr["root"]
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "README.md"), home, DEEPMAP_AUTO_SYNC="0")
    check("auto-sync off -> today's SYNC_TEXT on ANY edit (a .md too), verbatim; no last_source_edit.json",
          res["text"] == SYNC_TEXT and not (root / ".deepmap" / "last_source_edit.json").exists(), res["text"])

    pr = lab.project("f-hook")
    root = pr["root"]
    cli = (lab.plugin / "build" / "deepmap_cli.py").as_posix()
    hook = root / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\n( PYTHONUTF8=1 nohup '{Path(PY).as_posix()}' '{cli}' sync '{root.as_posix()}' "
                    f">/dev/null 2>&1 & ) || true   # deepmap\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("f"), root, root / "src" / "app.py"), home)
    check("this repo's DeepMap git hook syncs the project -> the commit clause (exact)",
          res["text"] == AUTO_SYNC_TEXT.format(file="src/app.py", commit=AUTO_COMMIT), res["text"])


# =========================================================================== G: robustness
_SIX = ("deepmap_sessionstart_heal", "deepmap_sessionstart_notify", "deepmap_presearch_nudge",
        "deepmap_postedit_syncflag", "deepmap_toolresult_freshness", "deepmap_access_hook")


def gate_g_robust(lab: Lab, stamp) -> None:
    say("\n-- G: every hook exits 0 quietly on garbage, unreadable records and an empty build/ --")
    home = lab.home("g")
    pr = lab.project("g")
    root = pr["root"]
    for hook in _SIX:
        if not (lab.plugin / "hooks" / f"{hook}.py").is_file():
            check(f"{hook}.py exists in the tree", False)
            continue
        for label, raw in (("not JSON", b"{not json"), ("a JSON list", b"[1, 2]"), ("JSON null", b"null"),
                           ("binary junk", b"\xff\xfe\x00garbage")):
            res = lab.run(hook, {"cwd": str(root)}, home, raw=raw)
            if hook == "deepmap_sessionstart_heal":
                # today's heal falls back to the working directory (a project here); the refresh never starts on an
                # event it cannot read
                check(f"sessionstart_heal: {label} payload -> exit 0, no stdout, no background refresh",
                      res["rc"] == 0 and res["out"] == "" and not _runner_of(res)
                      and len(res["popen"]) == len(_heal_of(res)), (res["rc"], res["popen"]))
                continue
            check(f"{hook.replace('deepmap_', '')}: {label} payload -> exit 0, no stdout, nothing started",
                  res["rc"] == 0 and res["out"] == "" and not res["popen"], (res["rc"], res["out"][:200], res["err"]))
    pr = lab.project("g-garbage", commits=2)
    root = pr["root"]
    for name in ("autosync.json", "session_health.json", "autosync_told.json", "last_source_edit.json",
                 "sync_running.json", "autosync_spawn.json", "sync_last.json", "instance.json"):
        (root / ".deepmap" / name).write_text("{ garbage", encoding="utf-8")
    sid = _sid("g")
    payloads = {"deepmap_sessionstart_heal": _ss(sid, root), "deepmap_sessionstart_notify": _ss(sid, root),
                "deepmap_presearch_nudge": _grep(sid, root),
                "deepmap_postedit_syncflag": _edit(sid, root, root / "src" / "app.py"),
                "deepmap_toolresult_freshness": _tool(sid, root), "deepmap_access_hook": _tool(sid, root)}
    for hook in _SIX:
        if not (lab.plugin / "hooks" / f"{hook}.py").is_file():
            continue
        res = lab.run(hook, payloads[hook], home)
        check(f"{hook.replace('deepmap_', '')}: every auto-sync record unreadable -> exit 0, at most ONE well-formed "
              "JSON document", res["rc"] == 0 and res["docs"] is not None and len(res["docs"]) <= 1,
              (res["rc"], res["out"][:300], res["err"]))
    pr = lab.project("g-bare", commits=2)
    root = pr["root"]
    sid = _sid("g")
    c1, c2 = pr["shas"]
    res = lab.run("deepmap_sessionstart_heal", _ss(sid, root), home, plugin=lab.bare)
    check("an EMPTY build/ (no provision, no auto_sync): heal hook starts nothing, exit 0, no stdout",
          res["rc"] == 0 and res["out"] == "" and not res["popen"], res)
    res = lab.run("deepmap_sessionstart_notify", _ss(sid, root), home, plugin=lab.bare)
    check("an EMPTY build/: notify says exactly today's READY + stale text",
          res["rc"] == 0 and res["text"] == ANNOUNCE_TEXT + STALE_SUFFIX.format(
              built=c1[:7], head=c2[:7], distance=" (1 commit later)"), res["text"])
    res = lab.run("deepmap_postedit_syncflag", _edit(sid, root, root / "README.md"), home, plugin=lab.bare)
    check("an EMPTY build/: the edit hook says today's SYNC_TEXT (any edit), records nothing",
          res["rc"] == 0 and res["text"] == SYNC_TEXT and not (root / ".deepmap" / "last_source_edit.json").exists(),
          res["text"])
    _health(root, sid, start=time.time() - 900)
    _last_run(root, "synced", head=c2)
    res = lab.run("deepmap_toolresult_freshness", _tool(sid, root), home, plugin=lab.bare)
    check("an EMPTY build/: the tool-result hook is silent", res["rc"] == 0 and res["out"] == "", res)
    del stamp


# =========================================================================== H: auto-sync off == today
def gate_h_off(lab: Lab) -> None:
    say("\n-- H: auto-sync OFF — every hook's output and every file it writes are today's --")
    home = lab.home("h")
    for label, extra, setup in (("DEEPMAP_AUTO_SYNC=0", {"DEEPMAP_AUTO_SYNC": "0"}, None),
                                ("instance.json auto_sync.enabled=false", {}, "instance"),
                                ("an unattended session", {"CLAUDE_CODE_SESSION_ATTENDED": "0"}, None)):
        pr = lab.project("h-off", commits=2)
        root = pr["root"]
        c1, c2 = pr["shas"]
        if setup == "instance":
            _w(root / ".deepmap" / "instance.json", {"auto_sync": {"enabled": False}})
        sid = _sid("h")
        rh = lab.run("deepmap_sessionstart_heal", _ss(sid, root), home, **extra)
        rn = lab.run("deepmap_sessionstart_notify", _ss(sid, root), home, **extra)
        rg = lab.run("deepmap_presearch_nudge", _grep(sid, root), home, **extra)
        re_ = lab.run("deepmap_postedit_syncflag", _edit(sid, root, root / "src" / "app.py"), home, **extra)
        rt = lab.run("deepmap_toolresult_freshness", _tool(sid, root), home, **extra)
        check(f"{label}: the heal hook starts only the heal", len(rh["popen"]) == 1 and len(_heal_of(rh)) == 1,
              [x["argv"][1:4] for x in rh["popen"]])
        check(f"{label}: notify = today's READY + STALE_SUFFIX",
              rn["text"] == ANNOUNCE_TEXT + STALE_SUFFIX.format(built=c1[:7], head=c2[:7],
                                                                distance=" (1 commit later)"), rn["text"])
        check(f"{label}: nudge = today's reminder + stale note",
              rg["text"] == NUDGE_TEXT + NUDGE_STALE_NOTE.format(built=c1[:7], head=c2[:7],
                                                                 distance=" (1 commit later)"), rg["text"])
        check(f"{label}: edit hook = today's SYNC_TEXT", re_["text"] == SYNC_TEXT, re_["text"])
        check(f"{label}: tool-result hook silent", rt["out"] == "" and rt["rc"] == 0, rt)
        files = sorted(p.name for p in (root / ".deepmap").iterdir())
        want = sorted({"graph_build.json", "announce_state.json", "session_health.json", "presearch_nudge_state.json",
                       "postedit_sync_state.json", "heal.log"} | ({"instance.json"} if setup else set()))
        check(f"{label}: .deepmap holds exactly today's files (no autosync record, no last_source_edit.json)",
              files == want, files)
        check(f"{label}: session_health carries exactly today's keys", set(_session(root, sid)) == LEGACY_HEALTH_KEYS,
              sorted(_session(root, sid)))


# =========================================================================== J: the quiet stamp (latency)
_HEAVY = ("json", "pathlib", "re", "deepmap_hook_state", "auto_sync", "port_registry", "sync_status", "instance_state")
QUIET_FILES = ("session_health.json", "autosync.json", "sync_running.json", "autosync_spawn.json", "graph_build.json")


def _stamp_entry(root: Path, sid: str, cwd) -> dict:
    try:
        data = json.loads((root / ".deepmap" / "autosync_quiet.json").read_text(encoding="utf-8"))
        ent = (data.get("entries") or {}).get(f"{sid}\t{cwd}")
        return ent if isinstance(ent, dict) else {}
    except Exception:
        return {}


def _edit_stamp(root: Path, sid: str, cwd, **fields) -> None:
    p = root / ".deepmap" / "autosync_quiet.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["entries"][f"{sid}\t{cwd}"].update(fields)
    p.write_text(json.dumps(data), encoding="utf-8")


def gate_j_quiet(lab: Lab, stamp, dead) -> None:
    say("\n-- J: nothing new to hear -> the tool-result hook exits before any import; every change is still heard --")
    home = lab.home("j")
    hook = "deepmap_toolresult_freshness"
    pr = lab.project("j-main", commits=2)
    root, c1, c2 = pr["root"], pr["shas"][0], pr["shas"][1]
    sid = _sid("j")
    cwd = str(root)
    _health(root, sid, start=time.time() - 900, auto="stale", distance=1)
    _last_run(root, "synced", head=c2, finished_ago=600, rid="j-o1", verdict="READY")

    def run(**extra):
        return lab.run_mods(hook, _tool(sid, root), home, **extra)

    def fast(res) -> bool:
        return (res["rc"] == 0 and res["out"] == "" and not (set(_HEAVY) & res["mods"])
                and "<no record>" not in res["mods"])

    def heavy(res) -> list:
        return sorted(set(_HEAVY) & res["mods"])

    r1 = run()
    check("J: the first call tells the outcome (FOLLOW_SYNCED) and stamps nothing (it told)",
          (r1["text"] or "").startswith("DeepMap note: the background refresh finished")
          and not _stamp_entry(root, sid, cwd), (r1["text"], _stamp_entry(root, sid, cwd)))
    check("J: a told call with no sync holding the lock does not load build/sync_status (no sync_running.json)",
          "sync_status" not in r1["mods"] and "auto_sync" in r1["mods"], heavy(r1))
    r2 = run()
    ent = _stamp_entry(root, sid, cwd)
    check("J: the next call has nothing to say -> silent, and stamped (this session + cwd, this plugin's build/, no "
          "time limit, the five records' signatures)",
          r2["out"] == "" and ent.get("session_id") == sid and ent.get("cwd") == cwd
          and ent.get("code") == str((lab.plugin / "build").resolve()) and ent.get("until") is None
          and sorted(ent.get("files") or {}) == sorted(QUIET_FILES)
          and (ent.get("files") or {}).get("sync_running.json", "x") is None, ent)
    check("J: ...that call asked the rule itself (the auto-sync modules loaded)", "auto_sync" in r2["mods"], heavy(r2))
    r3 = run()
    check("J: with the stamp holding, the next call is silent and imports none of json / pathlib / re / "
          "deepmap_hook_state / build/", fast(r3), (heavy(r3), r3["out"][:200], r3["err"]))
    r4 = run(DEEPMAP_FRESHNESS_NOTE="0")
    r5 = run(DEEPMAP_HOOKS="0")
    check("J: DEEPMAP_FRESHNESS_NOTE=0 / DEEPMAP_HOOKS=0 -> silent before any import", fast(r4) and fast(r5),
          (heavy(r4), heavy(r5)))

    # a new outcome (autosync.json rewritten): heard, once — then quiet again
    _last_run(root, "failed:error", reason="boom", finished_ago=5, rid="j-o2")
    r = run()
    check("J: a new outcome recorded -> the stamp lapses and the hook tells it (FOLLOW_FAILED, exact)",
          r["text"] == FOLLOW_FAILED.format(reason="boom", build=_b(c1)), r["text"] or r)
    run()
    r = run()
    check("J: ...then quiet again (silent, nothing imported)", fast(r), heavy(r))
    # another session starts (session_health.json rewritten): the rule is asked again, nothing to say
    _health(root, _sid("j-other"), start=time.time())
    r = run()
    check("J: session_health.json rewritten (another session started) -> the rule is asked again, silent",
          r["out"] == "" and "deepmap_hook_state" in r["mods"], (heavy(r), r["out"][:200]))
    r = run()
    check("J: ...and the stamp holds again", fast(r), heavy(r))
    # a live background refresh takes the lock: heard; while it runs, re-checked every QUIET_LIVE_RECHECK_S
    _running(root, created=stamp, started_ago=30, phase="extract", rid="j-r1")
    r = run()
    check("J: a live background refresh takes the lock (sync_running.json) -> FOLLOW_RUNNING, and sync_status loaded",
          (r["text"] or "").startswith("DeepMap note: a background refresh of this project's graph is running")
          and "sync_status" in r["mods"], (r["text"], heavy(r)))
    t0 = time.time()
    r = run()
    ent = _stamp_entry(root, sid, cwd)
    until = ent.get("until")
    check("J: ...told; the next call is silent and stamped with a time limit about 60 s ahead (the holder may end "
          "without a record)", r["out"] == "" and isinstance(until, (int, float)) and t0 < until <= time.time() + 61,
          (r["out"][:200], until, time.time()))
    r = run()
    check("J: ...within it, quiet (nothing imported)", fast(r), heavy(r))
    _edit_stamp(root, sid, cwd, until=time.time() - 1)
    r = run()
    check("J: the stamp's time limit passed -> the rule is asked again (still silent: the refresh still runs)",
          r["out"] == "" and "auto_sync" in r["mods"], (heavy(r), r["out"][:200]))
    (root / ".deepmap" / "sync_running.json").unlink()
    _last_run(root, "synced", head=c2, finished_ago=1, rid="j-o3", verdict="READY")
    r = run()
    check("J: the refresh ends (lock released, outcome recorded) -> FOLLOW_SYNCED",
          (r["text"] or "").startswith("DeepMap note: the background refresh finished"), r["text"] or r)
    run()
    # another plugin's stamp, a corrupt stamp: the rule decides (and rewrites a good stamp)
    _edit_stamp(root, sid, cwd, code=str(lab.base / "another-plugin" / "build"))
    r = run()
    check("J: a stamp written by another plugin's code -> not trusted: the rule is asked (silent)",
          r["out"] == "" and "auto_sync" in r["mods"], heavy(r))
    check("J: ...and the stamp is rewritten for this plugin",
          _stamp_entry(root, sid, cwd).get("code") == str((lab.plugin / "build").resolve()))
    (root / ".deepmap" / "autosync_quiet.json").write_text("{ garbage", encoding="utf-8")
    r = run()
    check("J: a corrupt stamp -> exit 0, silent, the rule asked", r["rc"] == 0 and r["out"] == ""
          and "auto_sync" in r["mods"], (r["rc"], heavy(r), r["err"]))
    r = run()
    check("J: ...and a good stamp is back (quiet)", fast(r), heavy(r))
    # the session in a subdirectory: its own entry, the root's stays
    sub = root / "src"
    rs1 = lab.run_mods(hook, _tool(sid, sub), home)
    rs2 = lab.run_mods(hook, _tool(sid, sub), home)
    r = run()
    check("J: the same session from a subdirectory -> first the rule (its own entry), then quiet; the root's entry "
          "still holds", rs1["out"] == "" and "deepmap_hook_state" in rs1["mods"] and fast(rs2) and fast(r)
          and _stamp_entry(root, sid, str(sub)).get("cwd") == str(sub), (heavy(rs1), heavy(rs2), heavy(r)))
    rt = lab.run_mods(hook, _tool(_sid("j-third"), root), home)
    check("J: another session (no entry of its own) -> the rule decides (no other session's stamp is used)",
          "deepmap_hook_state" in rt["mods"], heavy(rt))

    # the not-started wait: stamped until it ends
    pr = lab.project("j-ns", commits=2)
    sid2 = _sid("j")
    _health(pr["root"], sid2, start=time.time() - 30, auto="stale", distance=1)
    r = lab.run_mods(hook, _tool(sid2, pr["root"]), home)
    start = _session(pr["root"], sid2).get("start")
    ent = _stamp_entry(pr["root"], sid2, str(pr["root"]))
    check("J: told a refresh was coming, 30 s ago, nothing started yet -> silent, stamped until the 120 s wait ends",
          r["out"] == "" and isinstance(start, (int, float)) and ent.get("until") == start + 120.0, (ent, start))
    # waiting on a live process (a runner still on its way / one this session started): 60 s re-checks
    for tag, rec in (("j-cur", "current"), ("j-spawn", "spawn")):
        pr = lab.project(tag, commits=2)
        s3 = _sid("j")
        _health(pr["root"], s3, start=time.time() - 200, auto="stale", distance=1)
        st = _session(pr["root"], s3)["start"]
        if rec == "current":
            _w(pr["root"] / ".deepmap" / "autosync.json",
               {"schema": 1, "last_run": None, "current": {"id": "c", "pid": os.getpid(), "pid_created": stamp,
                                                           "session_id": None, "trigger": "session-start",
                                                           "started_utc": _utc(time.time() - 100),
                                                           "started_epoch": time.time() - 100, "state": "waiting-db"}})
        else:
            _w(pr["root"] / ".deepmap" / "autosync_spawn.json",
               {"schema": 1, "session_id": s3, "source": "startup", "spawned_utc": _utc(st + 1),
                "spawned_epoch": st + 1, "pid": os.getpid(), "pid_created": stamp, "interpreter": [PY],
                "interpreter_source": "hook"})
        t0 = time.time()
        r = lab.run_mods(hook, _tool(s3, pr["root"]), home)
        until = _stamp_entry(pr["root"], s3, str(pr["root"])).get("until")
        check(f"J: a live {'runner on its way (autosync.json current)' if rec == 'current' else 'spawned runner'} -> "
              "silent, stamped with a ~60 s re-check", r["out"] == "" and isinstance(until, (int, float))
              and t0 < until <= time.time() + 61, (r["out"][:200], until))

    # auto-sync off: no stamp; a session that started with auto-sync off loads no auto-sync module
    pr = lab.project("j-off", commits=2)
    s4 = _sid("j")
    _health(pr["root"], s4, start=time.time() - 900, auto="stale")
    _last_run(pr["root"], "synced", head=pr["shas"][1], finished_ago=600)
    r = lab.run_mods(hook, _tool(s4, pr["root"]), home, DEEPMAP_AUTO_SYNC="0")
    r2 = lab.run_mods(hook, _tool(s4, pr["root"]), home, DEEPMAP_AUTO_SYNC="0")
    check("J: auto-sync off (DEEPMAP_AUTO_SYNC=0) -> silent, and no autosync_quiet.json is ever written",
          r["out"] == "" and r2["out"] == "" and not (pr["root"] / ".deepmap" / "autosync_quiet.json").exists())
    pr = lab.project("j-legacy", commits=2)
    s5 = _sid("j")
    _w(pr["root"] / ".deepmap" / "session_health.json", {"sessions": {s5: {
        "start": time.time() - 900, "told": "ready", "servers_down": False, "heal_path": None, "seen": None,
        "resolved": True, "restart_told": False}}})
    _last_run(pr["root"], "synced", head=pr["shas"][1], finished_ago=600)
    r = lab.run_mods(hook, _tool(s5, pr["root"]), home)
    check("J: a session the notify told with auto-sync off (no `auto` in its record) -> silent, no auto-sync module "
          "loaded, no stamp", r["out"] == "" and "auto_sync" not in r["mods"]
          and not (pr["root"] / ".deepmap" / "autosync_quiet.json").exists(), heavy(r))

    # under a heal policy refusal nothing is said — and nothing stamped: the refusal can be lifted (a newer heal / sync)
    hh = lab.home("j-refused")
    pr = lab.project("j-refused", commits=2)
    s8 = _sid("j")
    _health(pr["root"], s8, start=time.time() - 900, auto="stale", heal_path=str(hh / "run" / "heal-k9.json"))
    _w(hh / "run" / "heal-k9.json", {"ts": _utc(time.time()), "neo4j_ok": False, "mcp_ok": False, "refused": True,
                                     "refusal_kind": "foreign-base-claim", "reason": "the Neo4j on 7687 belongs to X"})
    _last_run(pr["root"], "synced", head=pr["shas"][1], finished_ago=600)
    r = lab.run_mods(hook, _tool(s8, pr["root"]), hh)
    check("J: under a heal policy refusal -> silent and NO stamp (once the refusal is lifted, the outcome is heard)",
          r["out"] == "" and not (pr["root"] / ".deepmap" / "autosync_quiet.json").exists(), r["out"][:200])

    # the nudge: after its once-per-session reminder, a quiet search loads no auto-sync module
    pr = lab.project("j-nudge", commits=2)
    s6 = _sid("j")
    _health(pr["root"], s6, start=time.time() - 900, auto="stale", distance=1)
    _last_run(pr["root"], "synced", head=pr["shas"][1], finished_ago=600)
    g1 = lab.run_mods("deepmap_presearch_nudge", _grep(s6, pr["root"]), home)
    g2 = lab.run_mods("deepmap_presearch_nudge", _grep(s6, pr["root"]), home)
    g3 = lab.run_mods("deepmap_presearch_nudge", _grep(s6, pr["root"]), home)
    check("J: the nudge — the follow-up, then its reminder, then a quiet search that loads no build/ module",
          (g1["text"] or "").startswith("DeepMap note: the background refresh finished")
          and (g2["text"] or "").startswith(NUDGE_TEXT) and g3["out"] == ""
          and not ({"auto_sync", "port_registry", "sync_status", "instance_state"} & g3["mods"]),
          ((g1["text"] or "")[:60], (g2["text"] or "")[:60], heavy(g3)))

    # the hook's pre-import copy of the check == deepmap_hook_state.quiet_holds
    pr = lab.project("j-eq", commits=2)
    s7 = _sid("j")
    rq, cq = pr["root"], str(pr["root"])
    _health(rq, s7, start=time.time() - 900, auto="stale", distance=1)
    _last_run(rq, "synced", head=pr["shas"][1], finished_ago=600)
    for _ in range(2):
        lab.run_mods(hook, _tool(s7, rq), home)
    good = json.loads((rq / ".deepmap" / "autosync_quiet.json").read_text(encoding="utf-8"))
    key = f"{s7}\t{cq}"

    def variant(**fields):
        d = json.loads(json.dumps(good))
        d["entries"][key].update(fields)
        return json.dumps(d)

    files = good["entries"][key]["files"]
    cases = [("the stamp as written", None, s7, cq, True),
             ("a time limit ahead", variant(until=time.time() + 600), s7, cq, True),
             ("a time limit passed", variant(until=time.time() - 1), s7, cq, False),
             ("another plugin's code", variant(code=str(lab.base / "x" / "build")), s7, cq, False),
             ("another session", json.dumps(good), _sid("j-eq-other"), cq, False),
             ("another cwd (a subdirectory)", json.dumps(good), s7, str(rq / "src"), False),
             ("a corrupt stamp", "{ garbage", s7, cq, False),
             ("schema 2", json.dumps(dict(good, schema=2)), s7, cq, False),
             ("no files recorded", variant(files={}), s7, cq, False),
             ("a recorded name with a path in it", variant(files={"../graph_build.json": files["graph_build.json"]}),
              s7, cq, False),
             ("an entry under the right key naming another session",
              variant(session_id="someone-else"), s7, cq, False)]
    for name in QUIET_FILES:
        sig = files.get(name)
        changed = [sig[0], sig[1] + 1, sig[2]] if isinstance(sig, list) else [1, 1, 1]
        cases.append((f"{name} rewritten", variant(files=dict(files, **{name: changed})), s7, cq, False))
    agree = []
    for label, stamp_text, sid_, cwd_, want in cases:
        got = lab.quiet_eq(rq, sid_, cwd_, stamp_text if stamp_text is not None else json.dumps(good))
        ok = got.get("fast") is want and got.get("slow") is want
        agree.append(ok)
        check(f"J: _quiet_fast == quiet_holds == {want}: {label}", ok, got)
    got = lab.quiet_eq(rq, s7, cq, json.dumps(good))
    check("J: the hook's copy shares deepmap_hook_state's constants (file name, schema, OFF words, walk bound, the "
          "five records)", got.get("consts") == [True] * 5, got)
    (rq / ".deepmap" / "autosync.json").write_text((rq / ".deepmap" / "autosync.json").read_text(encoding="utf-8"),
                                                    encoding="utf-8")
    got = lab.quiet_eq(rq, s7, cq, json.dumps(good))
    check("J: a record really rewritten (same content, new signature) -> both checks fail", got.get("fast") is False
          and got.get("slow") is False, got)
    del dead


# =========================================================================== I: the CLI contract (cross-slice)
def gate_i_cli_contract(lab: Lab) -> None:
    say("\n-- I: the runner argv the heal hook builds, run for real against this tree's CLI; the hooks follow it --")
    home = lab.home("i")
    real_cli = lab.tree / "build" / "deepmap_cli.py"
    got = lab.call("cli_takes_unattended", home, str(real_cli))
    takes = got.get("ok") is True
    check("deepmap_hook_state.cli_takes_unattended answers for this tree's real build/deepmap_cli.py",
          isinstance(got.get("ok"), bool), got)
    # the argv the heal hook really builds (its Popen is a recorder) ...
    pr = lab.project("i-argv", git=False)
    sid = _sid("i")
    res = lab.run("deepmap_sessionstart_heal", _ss(sid, pr["root"]), home)
    run = _runner_of(res)
    argv = run[0]["argv"] if run else []
    check("the heal hook built the runner argv (with the stub CLI, which carries the contract)",
          len(argv) == 7 and argv[2:5] == ["sync", str(pr["root"]), "--unattended=session-start"], res["popen"])
    if len(argv) >= 5:
        # ... run FOR REAL with this tree's CLI in place of the stub: auto-sync off (the runner's own answer is then
        # "disabled", rc 4, nothing done), a temp DEEPMAP_HOME, no managed-venv re-exec, an operator NEO4J_URI on a
        # closed port (even a CLI that ran a plain sync could reach no database), offline
        real = [argv[0], str(real_cli), *argv[2:]]
        env = lab.env(home, DEEPMAP_AUTO_SYNC="0", DEEPMAP_MANAGED_VENV="0", DEEPMAP_EXTRACTOR="ast",
                      NEO4J_URI=f"bolt://127.0.0.1:{_closed_port()}", NEO4J_PASSWORD="dmas-unused",
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1")
        env.pop("PYTHONPATH", None)
        p = subprocess.run(real, capture_output=True, timeout=300, cwd=str(lab.base), env=env)
        rc = p.returncode
        tail = (p.stdout + p.stderr).decode("utf-8", "replace")[-500:]
        check("the hooks' text proof agrees with the real CLI: the heal hook's argv exits 4 (auto-sync off -> "
              "\"disabled\") exactly when cli_takes_unattended says the CLI takes it", (rc == 4) == takes,
              (rc, takes, tail))
        if takes:
            check("...this tree's CLI takes the heal hook's argv: rc 4, never 2 (an argparse refusal)", rc == 4,
                  (rc, tail))
        else:
            say(f"  [INFO] this tree's build/deepmap_cli.py does not take --unattended yet (rc {rc}; spec Appendix "
                "C3/C4 not applied) — the hooks must then start, announce and say nothing new:")
            check("...the CLI refuses the argv in argparse (rc 2: nothing ran)", rc == 2, (rc, tail))
    # the hooks in a plugin whose CLI is this tree's real one: a runner, an AUTO text, a follow-up exactly when it takes
    # --unattended — otherwise exactly today's behaviour
    plugin = lab.realcli_plugin()
    word = "takes" if takes else "does NOT take"
    pr = lab.project("i-heal", git=False)
    res = lab.run("deepmap_sessionstart_heal", _ss(_sid("i"), pr["root"]), home, plugin=plugin)
    check(f"real CLI ({word} --unattended): the heal hook starts the heal"
          + (", then the background refresh" if takes else " only (no background refresh, no spawn record)"),
          len(_heal_of(res)) == 1 and len(_runner_of(res)) == (1 if takes else 0)
          and (pr["root"] / ".deepmap" / "autosync_spawn.json").exists() == takes, [x["argv"][1:4] for x in res["popen"]])
    pr = lab.project("i-notify", commits=2)
    c1, c2 = pr["shas"]
    sid = _sid("i")
    res = lab.run("deepmap_sessionstart_notify", _ss(sid, pr["root"]), home, plugin=plugin)
    want = ANNOUNCE_TEXT + (AUTO_STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)", took="")
                            if takes else STALE_SUFFIX.format(built=c1[:7], head=c2[:7], distance=" (1 commit later)"))
    check(f"real CLI ({word} --unattended): notify = " + ("the AUTO text" if takes else "today's READY + STALE_SUFFIX"),
          res["text"] == want, res["text"])
    if not takes:
        check("...session_health carries exactly today's keys", set(_session(pr["root"], sid)) == LEGACY_HEALTH_KEYS,
              sorted(_session(pr["root"], sid)))
        _last_run(pr["root"], "synced", head=c2, finished_ago=5)
        res = lab.run("deepmap_toolresult_freshness", _tool(sid, pr["root"]), home, plugin=plugin)
        check("...a recorded background outcome is not followed up (the tool-result hook is silent)",
              res["out"] == "" and res["rc"] == 0, res["text"])
        res = lab.run("deepmap_presearch_nudge", _grep(sid, pr["root"]), home, plugin=plugin)
        check("...the nudge says today's reminder + stale note",
              res["text"] == NUDGE_TEXT + NUDGE_STALE_NOTE.format(built=c1[:7], head=c2[:7],
                                                                  distance=" (1 commit later)"), res["text"])
    pr = lab.project("i-edit")
    root = pr["root"]
    res = lab.run("deepmap_postedit_syncflag", _edit(_sid("i"), root, root / "src" / "app.py"), home, plugin=plugin)
    check(f"real CLI ({word} --unattended): the edit hook says "
          + ("AUTO_SYNC_TEXT" if takes else "today's SYNC_TEXT and records nothing"),
          res["text"] == (AUTO_SYNC_TEXT.format(file="src/app.py", commit="") if takes else SYNC_TEXT)
          and (root / ".deepmap" / "last_source_edit.json").exists() == takes, res["text"])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv)
    print("=" * 76)
    print(f"DeepMap — auto-sync, hooks slice (tree: {tree})")
    print("=" * 76)
    if not shutil.which("git"):
        print("git is required by this gate")
        return 1
    gate_a_hooks_json(tree)
    t0 = time.monotonic()
    lab = Lab(tree)
    print(f"(build/auto_sync.py: {'FAKE (spec §2.4)' if lab.fake_auto else 'REAL'}; "
          f"build/sync_status.py: {'FAKE (spec §2.3)' if lab.fake_status else 'REAL'})")
    try:
        stamp, dead = _stamp(tree)
        sections = ((gate_b_heal, (lab,)), (gate_b2_log_append, (lab,)), (gate_c_notify, (lab,)),
                    (gate_c2_nongit, (lab,)), (gate_d_freshness, (lab,)),
                    (gate_e1_running, (lab, stamp, dead)), (gate_e2_outcomes, (lab, stamp, dead)),
                    (gate_e3_not_started, (lab, stamp, dead)), (gate_e4_silence, (lab, stamp, dead)),
                    (gate_e5_end_to_end, (lab, stamp, dead)), (gate_e6_nongit_words, (lab, stamp, dead)),
                    (gate_j_quiet, (lab, stamp, dead)), (gate_f_edit, (lab,)), (gate_g_robust, (lab, stamp)),
                    (gate_h_off, (lab,)), (gate_i_cli_contract, (lab,)))

        def run_section(g, args) -> list:
            # each section: its own projects / homes / session ids, its lines printed together, in order
            _BUF.lines = []
            try:
                g(*args)
            except Exception as exc:  # noqa: BLE001 — a crashing section is a RED check, never a crash of the suite
                check(f"{g.__name__} ran without raising", False, "".join(traceback.format_exception(exc))[-1200:])
            return _BUF.lines

        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(run_section, g, a) for g, a in sections]
            for f in futs:
                for line in f.result():
                    print(line)
    finally:
        lab.close()
    print(f"\n(hook runs: {time.monotonic() - t0:.0f} s wall)")
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 76)
    print(f"RESULT: {'GREEN' if not FAILS else 'RED'} — {len(PASSES)}/{total} gates passed.")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
