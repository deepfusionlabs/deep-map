#!/usr/bin/env python3
"""DeepMap SessionStart heal hook (P2) — best-effort auto-recovery of an ALREADY-PROVISIONED DeepMap.

Why it exists: the managed Neo4j and the two MCP servers are DETACHED processes — they survive the
provisioner exiting, but NOT a reboot. And the url-based MCP servers a project's `.mcp.json` names are only
CONNECTED to by Claude Code, never launched. So after a reboot every DeepMap project is dead until the
user re-runs /deep-map:setup. This hook auto-heals that.

Discipline (mirrors hooks/deepmap_access_hook.py — never disrupt a session):
  * MARKER-GATED — does NOTHING unless the workspace has a built graph (`.deepmap/graph_build.json`),
    so a non-DeepMap project or a fresh machine pays ~one stat() and exits. It never provisions from
    scratch; it only revives a project the user already set up — so a manifest whose last sync PROVED
    the graph empty (verdict FAILED / 0 nodes) is not revived either: setup deliberately stopped before
    starting servers or writing .mcp.json there, and heal redid that wiring every session (~1 GB of
    processes serving an empty graph).
  * NON-BLOCKING — spawns `provision.py heal` DETACHED and returns immediately, so a slow Neo4j
    cold-start never stalls session start. The child runs in Python UTF-8 mode (PYTHONUTF8=1), like
    every other DeepMap entry point.
  * THE PYTHON SETUP USED (ISS-build:013) — hooks.json starts this hook as `python … ; python3 …`, and the
    first of those need not be the interpreter DeepMap's dependencies are in. heal (and the MCP servers it
    starts) runs under DEEPMAP_PYTHON when that names a startable interpreter, else the interpreter this
    project's setup ran under (recorded in DeepMap's machine state, <DEEPMAP_HOME>/run/setup-python-<root
    id>.json — never read from the project's own files — while it exists), else the Python running this hook
    (deepmap_hook_state.heal_interpreter — no subprocess); the heal.log header names it.
  * ALWAYS exits 0 and prints nothing — it can never break a session, even on error. heal records its
    outcome in <DEEPMAP_HOME>/run/heal-<project_key>.json, which the notify + nudge hooks read, and its
    full output (stdout + stderr) in <root>/.deepmap/heal.log (ROUND-7 T9#1; bounded — rotated to
    heal.log.1 past 256 KB). Before, the output went to DEVNULL and a failed heal left no trace but its
    one-line status.
OPT-IN idle-hibernation (DEEPMAP_HIBERNATE=1, default OFF): when set, ALSO spawns a detached
`provision.py hibernate <root> --others` to spin down OTHER projects' idle Neo4j instances (RAM). It
is off by default because there is no auto-revive on the search path (see provision.hibernate_idle).
AUTO-SYNC (default ON for an interactive session): AFTER the heal, it starts DeepMap's background refresh of this
project's graph — `deepmap_cli.py sync <root> --unattended=session-start [--session <id>]` (build/auto_sync.py) — when
the plugin's CLI takes that argv (deepmap_hook_state.cli_takes_unattended, a text check) and auto_sync.precheck passes
(file reads only: enabled, not a compact, set up, not paused / backing off / too slow / recipe-conflicted). Same
interpreter rule as the heal; offline (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE), no optional git locks, BELOW_NORMAL
priority (Windows creation flag; the runner lowers its own on POSIX); its output goes to <root>/.deepmap/autosync.log
and what was started to .deepmap/autosync_spawn.json. Only for a payload that parses.
Registered via hooks/hooks.json (SessionStart). Opt-out: DEEPMAP_HEAL=0 skips the heal (and the hibernate sweep) only —
the background refresh still starts (nothing then brings the database up: with it down the refresh ends at once, and the
notify hook does not announce it — deepmap_hook_state.autosync_db_ready); DEEPMAP_AUTO_SYNC=0,
.deepmap/instance.json {"auto_sync": {"enabled": false}} or <DEEPMAP_HOME>/auto-sync.json {"enabled": false} skip the
background refresh only; the master DEEPMAP_HOOKS=0 skips everything; or disable/uninstall the plugin, or delete
`.deepmap/graph_build.json`.
"""
from __future__ import annotations

import sys

# ROUND-11 R3 — the FIRST thing this hook does when hooks.json runs it. The command is
#   python -X utf8 "<this file>" ; python3 -X utf8 "<this file>" ; exit 0
# (the one form that parses under sh, bash, dash, Windows PowerShell 5.1 and PowerShell 7, and still starts on a
# python3-only host), so where both names exist a second interpreter starts after the first. They share the shell's
# stdin: the first to get here takes the WHOLE payload; the second finds it drained and leaves at once — before any
# import or side effect — so the work is done exactly once. (A `python` that can't run this file never gets here and
# leaves the payload to python3.) Claude Code always sends a payload, so an empty stdin is never a real event. The
# payload is handed back as sys.stdin, where the hook reads it.
if __name__ == "__main__":
    try:
        _PAYLOAD = sys.stdin.buffer.read() if sys.stdin is not None else b""
    except Exception:
        _PAYLOAD = b""
    if not _PAYLOAD.strip():
        raise SystemExit(0)
    import io as _io
    sys.stdin = _io.TextIOWrapper(_io.BytesIO(_PAYLOAD), encoding="utf-8")

import json
import os
import subprocess
import sys
from pathlib import Path

# The shared hook helpers (sibling module in hooks/): UTF-8 stdin + the opt-out switches.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import deepmap_hook_state as _hs  # type: ignore
except Exception:  # pragma: no cover - degrade to the local fallbacks below
    _hs = None

_OFF = {"0", "false", "no", "off"}
# ROUND-7 T9#1: the detached heal's stdout+stderr used to go to DEVNULL, so a heal that failed left only its
# one-line status. They now go to <root>/.deepmap/heal.log (the gitignored per-project state dir the marker
# lives in), rotated to heal.log.1 past this size so it stays bounded.
HEAL_LOG = "heal.log"
_HEAL_LOG_MAX = 256 * 1024
# Auto-sync: DeepMap's background refresh of this project's graph (build/auto_sync.py, started through
# `deepmap_cli.py sync <root> --unattended=session-start`) — AFTER the heal, only when auto_sync.precheck passes (a few
# file reads: enabled, not compact, set up, not paused / backing off / too slow / recipe-conflicted). Offline
# (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE), git without optional locks, BELOW_NORMAL priority on Windows (the runner
# lowers its own on POSIX); its output goes to <root>/.deepmap/autosync.log (rotated like heal.log) and what was
# started to <root>/.deepmap/autosync_spawn.json. DEEPMAP_HEAL=0 skips only the heal; DEEPMAP_AUTO_SYNC=0 (the opt-out
# deepmap_hook_state.AUTO_SYNC_OPTOUT names) skips only this.
AUTOSYNC_LOG = "autosync.log"
AUTOSYNC_SPAWN = "autosync_spawn.json"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_RUNNER_ENV = {"PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "GIT_OPTIONAL_LOCKS": "0"}
# Windows: the access a log handle handed to a child is opened with — FILE_GENERIC_WRITE without FILE_WRITE_DATA
# (FILE_APPEND_DATA | FILE_WRITE_ATTRIBUTES | FILE_WRITE_EA | READ_CONTROL | SYNCHRONIZE) plus FILE_READ_ATTRIBUTES.
# Python's open(..., "ab") appends only through the C runtime's own seek before each write: the child inherits the bare
# HANDLE, which writes at its own file position — so a second session's header and runner, appended after this one
# started, were overwritten by it. A handle without FILE_WRITE_DATA makes EVERY write, from any process holding it, land
# at the end of the file.
_WIN_APPEND_ONLY = 0x0004 | 0x0100 | 0x0010 | 0x00020000 | 0x00100000 | 0x0080


def _open_append(path: Path):
    """`path` opened for appending (binary), in a form whose every write — this process's and those of a child that
    inherits it as stdout / stderr — lands at the END of the file even while other handles append to it: on POSIX
    open(..., "ab") (O_APPEND lives in the shared open file description); on Windows an append-only handle
    (_WIN_APPEND_ONLY, shared for read + write like open()'s, created when missing), named `path` like open()'s. Falls
    back to open(..., "ab") when that handle can't be made. Raises what open() raises."""
    if os.name == "nt":
        try:
            import io
            import msvcrt
            import _winapi
            # share read + write (open()'s), OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL; not inheritable (Popen duplicates it)
            h = _winapi.CreateFile(str(path), _WIN_APPEND_ONLY, 0x1 | 0x2, 0, 4, 0x80, 0)
            try:
                fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
            except Exception:
                _winapi.CloseHandle(h)
                raise
            try:
                raw = io.FileIO(fd, "w", closefd=True)   # an fd: never truncates, never seeks
            except Exception:
                os.close(fd)   # closes the handle too
                raise
            raw.name = str(path)
            return io.BufferedWriter(raw)
        except Exception:
            pass   # not made (an access / sharing error, no _winapi): open()'s handle — an overlapping run may overwrite
    return open(path, "ab")


def _open_heal_log(root: Path, python=None, source=None):
    """Open <root>/.deepmap/heal.log for APPEND (binary, append-only — _open_append; the child writes UTF-8 —
    PYTHONUTF8=1), rotating an oversized log to heal.log.1 first and writing a one-line header naming this run — and
    the interpreter heal runs under, with where it came from (ISS-build:013: DEEPMAP_PYTHON / setup / hook). None on any
    failure (the caller falls back to DEVNULL). Never raises."""
    try:
        d = root / ".deepmap"
        if not d.is_dir():
            return None
        log = d / HEAL_LOG
        try:
            if log.stat().st_size > _HEAL_LOG_MAX:
                os.replace(str(log), str(d / (HEAL_LOG + ".1")))
        except OSError:
            pass   # missing (first run) or a concurrent rotation: append to whatever is there
        f = _open_append(log)   # append-only: an overlapping session's header and output are never overwritten
        try:
            import time
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            who = f"; python {' '.join(str(w) for w in python)} [{source}]" if python else ""
            f.write(f"\n--- {stamp} DeepMap SessionStart heal: provision.py heal {root} "
                    f"(hook pid {os.getpid()}{who}) ---\n".encode("utf-8", "replace"))
            f.flush()
        except Exception:
            pass
        return f
    except Exception:
        return None


def _open_autosync_log(root: Path, python=None, source=None, session=None):
    """Open <root>/.deepmap/autosync.log for APPEND (binary, append-only — _open_append: two sessions starting together
    keep both headers and both runners' lines) — the background refresh's stdout + stderr — rotating an oversized log
    to autosync.log.1 first and writing a one-line header naming this start, the session and the interpreter (with
    where it came from). None on any failure (the caller falls back to DEVNULL). Never raises."""
    try:
        d = root / ".deepmap"
        if not d.is_dir():
            return None
        log = d / AUTOSYNC_LOG
        try:
            if log.stat().st_size > _HEAL_LOG_MAX:
                os.replace(str(log), str(d / (AUTOSYNC_LOG + ".1")))
        except OSError:
            pass   # missing (first run) or a concurrent rotation: append to whatever is there
        f = _open_append(log)   # append-only: an overlapping session's header and output are never overwritten
        try:
            import time
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            who = f"; python {' '.join(str(w) for w in python)} [{source}]" if python else ""
            f.write(f"\n--- {stamp} DeepMap SessionStart background refresh: deepmap_cli.py sync {root} "
                    f"--unattended=session-start (hook pid {os.getpid()}; session {session or '-'}{who}) ---\n"
                    .encode("utf-8", "replace"))
            f.flush()
        except Exception:
            pass
        return f
    except Exception:
        return None


def _spawn(python: list, args: list, log, kw: dict):
    """Popen `python + args`, detached per `kw`, output to `log` (else DEVNULL). An interpreter that can't be STARTED
    at all (OSError: the recorded one vanished since the check, a DEEPMAP_PYTHON command no longer on PATH) falls back
    to this hook's own Python — nothing was started, so the retry can never double a heal (ISS-build:013). Raises
    what the fallback raises (main swallows it)."""
    out = log if log is not None else subprocess.DEVNULL
    err = subprocess.STDOUT if log is not None else subprocess.DEVNULL
    try:
        return subprocess.Popen([*python, *args], stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kw)
    except OSError as exc:
        if list(python) == [sys.executable]:
            raise
        if log is not None:
            try:
                log.write(f"(could not start {' '.join(map(str, python))}: {exc} — using {sys.executable})\n"
                          .encode("utf-8", "replace"))
                log.flush()
            except Exception:
                pass
    return subprocess.Popen([sys.executable, *args], stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kw)


def _enabled() -> bool:
    """Default ON; OFF on DEEPMAP_HEAL=0 or the master DEEPMAP_HOOKS=0. Never raises."""
    if _hs is not None:
        return _hs.feature_enabled(_hs.HEAL_OPTOUT)
    return not any(str(os.environ.get(n, "")).strip().lower() in _OFF
                   for n in ("DEEPMAP_HOOKS", "DEEPMAP_HEAL"))


def _hooks_off() -> bool:
    """The master DEEPMAP_HOOKS=0: no heal, no hibernate sweep, no background refresh. Never raises."""
    try:
        return str(os.environ.get("DEEPMAP_HOOKS", "")).strip().lower() in _OFF
    except Exception:
        return False


def _payload():
    """The SessionStart payload as a dict (decoded as UTF-8, not the ANSI code page — a non-ASCII project path
    survives), or None when it is missing or not a JSON object. Never raises."""
    try:
        raw = _hs.read_stdin_text() if _hs is not None else sys.stdin.read()
        data = json.loads(raw) if raw else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _root(data=None) -> Path:
    """Resolve the project root: SessionStart payload cwd -> CLAUDE_PROJECT_DIR -> os.getcwd(), then
    deepmap_hook_state.project_root — the outermost directory (up to the git work tree's top) holding a
    graph manifest, so a session opened in a subdirectory revives the project synced at its root (it
    used to find no manifest there and do nothing). `data`: the payload read already (else it is read
    here, as UTF-8 — not the ANSI code page — so a non-ASCII project path survives)."""
    if data is None:
        data = _payload()
    cand = (data.get("cwd") if isinstance(data, dict) else None) \
        or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        if _hs is not None:
            found = _hs.project_root(cand)
            if found is not None:
                return Path(found)
        return Path(cand).resolve()
    except Exception:
        return Path.cwd()


def _write_spawn_record(root: Path, payload: dict, session, proc, python: list, source, args: list) -> None:
    """<root>/.deepmap/autosync_spawn.json — what this hook started for the session: {"schema", "session_id", "source",
    "spawned_utc", "spawned_epoch", "pid", "pid_created" (the process's start stamp — port_registry.pid_create_time —
    so a later "did it start?" check knows THIS process, never just a pid), "interpreter" (the argv words it really ran
    under: _spawn may have fallen back to this hook's own Python), "interpreter_source"}. Atomic; best effort; never
    raises."""
    try:
        import time
        used = [str(a) for a in (getattr(proc, "args", None) or [])]
        interp = used[:len(used) - len(args)] if len(used) > len(args) else [str(w) for w in python]
        src = source if interp == [str(w) for w in python] else "hook"
        pid = getattr(proc, "pid", None)
        created = None
        PR = _hs._registry() if _hs is not None else None
        if PR is not None and isinstance(pid, int) and not isinstance(pid, bool):
            created = PR.pid_create_time(pid)
        now = time.time()
        _hs._write_json_atomic(root / ".deepmap" / AUTOSYNC_SPAWN, {
            "schema": 1, "session_id": session, "source": payload.get("source"),
            "spawned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "spawned_epoch": now,
            "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None, "pid_created": created,
            "interpreter": interp, "interpreter_source": src})
    except Exception:
        pass


def _start_auto_sync(root: Path, payload: dict, cli: Path, python: list, source, kw: dict) -> None:
    """Start DeepMap's background refresh of this project — `<python> <plugin>/build/deepmap_cli.py sync <root>
    --unattended=session-start [--session <id>]` — when auto_sync.precheck(root, trigger="session-start",
    source=<payload source>) passes (file reads only: no git, no network; HEAD is the runner's to read, so its cooldown
    is decided there). Same interpreter rule as the heal (a project file never picks it); the heal's environment plus
    offline + no-optional-locks; on Windows the heal's hidden-console flags plus BELOW_NORMAL priority, in a dict of its
    own (`kw` keeps the heal's). Output appended to .deepmap/autosync.log; the start recorded in autosync_spawn.json.
    Raises what the spawn raises (main keeps it away from the heal)."""
    if _hs is None or not cli.is_file() or not _hs.cli_takes_unattended(cli):
        return   # a CLI without the --unattended contract would refuse the argv (exit 2): start nothing, as before
    AS = _hs._auto_sync()
    pre = getattr(AS, "precheck", None) if AS is not None else None
    if pre is None:
        return   # an older or broken build/: no background refresh (exactly the old hook)
    pc = pre(root, trigger="session-start", source=payload.get("source"))
    if not isinstance(pc, dict) or pc.get("ok") is not True:
        return
    sid = payload.get("session_id")
    sid = str(sid) if sid not in (None, "") else None
    args = [str(cli), "sync", str(root), "--unattended=session-start"] + (["--session", sid] if sid else [])
    env = dict(kw.get("env") or os.environ)
    env.update(_RUNNER_ENV)
    env.pop("__PYVENV_LAUNCHER__", None)
    rkw = dict(kw)
    rkw["env"] = env
    if os.name == "nt":
        rkw["creationflags"] = kw["creationflags"] | BELOW_NORMAL_PRIORITY_CLASS
    log = _open_autosync_log(root, python, source, sid)
    try:
        proc = _spawn(python, args, log, rkw)
    finally:
        if log is not None:
            try:
                log.close()
            except Exception:
                pass
    _write_spawn_record(root, payload, sid, proc, python, source, args)


def main() -> int:
    try:
        if _hooks_off():
            return 0  # the master DEEPMAP_HOOKS=0 — no heal, no hibernate sweep, no background refresh
        payload = _payload()
        root = _root(payload if payload is not None else {})
        if not (root / ".deepmap" / "graph_build.json").exists():
            return 0  # not a provisioned DeepMap project — no-op (near-zero cost)
        if _hs is not None and _hs.proven_empty(_hs.read_manifest(root)):
            return 0  # setup never wired this project (FAILED / 0 nodes) — nothing to revive
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        build = (Path(plugin_root) / "build") if plugin_root else (Path(__file__).resolve().parents[1] / "build")
        provision = build / "provision.py"
        # ISS-build:013: the Python setup ran under (it has DeepMap's dependencies), not whichever of python / python3
        # read the payload — DEEPMAP_PYTHON > the interpreter setup recorded (while it exists) > this hook's own
        py, source = _hs.heal_interpreter(root) if _hs is not None else ([sys.executable], "hook")
        env = dict(os.environ, PYTHONUTF8="1")
        # a venv redirector's marker belongs to the interpreter it was handed to: inherited by ANOTHER interpreter it
        # could re-point that one at this hook's venv
        env.pop("__PYVENV_LAUNCHER__", None)
        kw: dict = {"env": env}
        if os.name == "nt":
            # A hidden console of its own, not DETACHED_PROCESS: a detached heal has no console, so each
            # git / tasklist / powershell probe it runs would open a visible console window. Its children
            # inherit this hidden console instead; it still outlives the hook and ignores its Ctrl+C.
            kw["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        else:
            kw["start_new_session"] = True
        if _enabled() and provision.exists():   # DEEPMAP_HEAL=0 skips the heal (and the sweep) only
            try:
                # Detached + no wait: NEVER block session start (Neo4j cold-start can take tens of seconds).
                # Pass the project root so heal revives on THIS project's allocated MCP ports (looked up from
                # the registry / its .mcp.json), not the global 8787/8788 default. Its output goes to
                # <root>/.deepmap/heal.log (T9#1) — the child keeps its own copy of the handle; ours is closed.
                log = _open_heal_log(root, py, source)
                try:
                    _spawn(py, [str(provision), "heal", str(root)], log, kw)
                finally:
                    if log is not None:
                        try:
                            log.close()
                        except Exception:
                            pass
                # OPT-IN (DEEPMAP_HIBERNATE=1, default OFF): also spin down OTHER projects' IDLE Neo4j
                # instances to reclaim RAM (each is a ~0.5-1 GB JVM). Default-off because there is no
                # auto-revive on the search path — a returning project's own SessionStart heal revives it, but
                # sweeping a project that has an already-open, momentarily-idle session would break its next
                # search. Same detached/exit-0 contract; a separate verb so heal stays revive-only. The sweep
                # itself never touches the CURRENT project and only stops genuinely-idle (last_used-old)
                # instances.
                if os.environ.get("DEEPMAP_HIBERNATE") == "1":
                    _spawn(py, [str(provision), "hibernate", str(root), "--others"], None, kw)
            except Exception:
                pass   # the background refresh below still gets its chance
        # Auto-sync: DeepMap's background refresh — AFTER the heal (it waits for the database the heal revives), in
        # its own try: nothing it does can affect the heal spawn above. Only for a real SessionStart event (a payload
        # that parses as an object): an unreadable one keeps the heal's working-directory fallback and nothing more.
        if payload is not None:
            try:
                _start_auto_sync(root, payload, build / "deepmap_cli.py", py, source, kw)
            except Exception:
                pass
    except Exception:
        pass  # a heal hook must NEVER disrupt a session
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
