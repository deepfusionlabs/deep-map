#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap hook helpers — shared, stdlib-only primitives for the activation/freshness hooks
(deepmap_presearch_nudge.py, deepmap_postedit_syncflag.py, deepmap_sessionstart_notify.py,
deepmap_sessionstart_heal.py, deepmap_access_hook.py).

Discipline (mirrors hooks/deepmap_access_hook.py + deepmap_sessionstart_heal.py — a hook must never
harm the session): every function is defensive and NEVER raises. On any I/O or parse failure the
throttle helper returns a value that makes the caller stay QUIET, so an unwritable state file
degrades to "no reminder", never to a crash or a blocked tool.

Writes happen only under <root>/.deepmap/ (which is gitignored): the per-hook session counters, and
(ROUND-7 T9#1) session_health.json — what the SessionStart notify told each session, which the pre-search
nudge reads to report a later, contradicting heal outcome once — and, while DeepMap's background refresh (auto-sync)
is on for the project, autosync_told.json (which follow-up each session was told) and last_source_edit.json (the
newest source edit). Importing this module sets GIT_OPTIONAL_LOCKS=0 for every git the hooks (and the processes they
start) run. The auto-sync RULES live in build/auto_sync.py and build/sync_status.py, loaded lazily (_auto_sync /
_sync_status); without them — or with a build/deepmap_cli.py that cannot start the refresh (cli_takes_unattended) —
the hooks behave exactly as before. The health helpers additionally READ the
user-level DeepMap home (the last heal status, setup's durable MCP blocker — ROUND-7 I4 — and the port
registry) and may connect-probe this project's two MCP ports and its own Neo4j bolt port; nothing leaves
the machine. heal_interpreter (ISS-build:013) reads <DEEPMAP_HOME>/run/setup-python-<root id>.json — the
interpreter setup ran under; machine state, never a file in the project — with no subprocess.

<root> is the PROJECT root (project_root): the outermost directory, up to the git work tree's top, whose
.deepmap/graph_build.json describes the graph — so a session opened in a subdirectory behaves exactly
like one opened at the root it was synced from.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Auto-sync H5: every git command a DeepMap hook runs — and every process it starts (the SessionStart heal, the
# background refresh) — skips git's OPTIONAL locks. `git status` (and the rev-parse / rev-list probes here) may
# otherwise refresh the index under .git/index.lock, and a hook or a background refresh running next to the user's
# own `git commit` / `git add` / rebase step would make it fail with "Unable to create index.lock".
os.environ["GIT_OPTIONAL_LOCKS"] = "0"

# The canonical "a graph was built here" marker (build/sync.py writes it after every rebuild). The
# hooks no-op unless it exists, so a non-DeepMap project pays ~one stat() and stays silent.
GRAPH_MARKER = "graph_build.json"
# Truthiness sets match the repo convention (build/retrieval_log.py). Only an explicit off-value
# disables a default-ON feature; everything else (incl. unset) stays ON.
_OFF = {"0", "false", "no", "off"}
# The SessionStart heal's own opt-out (notify reads it too, to know whether a revive is coming).
HEAL_OPTOUT = "DEEPMAP_HEAL"
# DeepMap's background refresh at session start (build/auto_sync.py — default ON for interactive sessions). Its
# rule (env > .deepmap/instance.json > the machine switch <DEEPMAP_HOME>/auto-sync.json > attended session) lives in
# auto_sync.enabled; the hooks only ask it (autosync_on). Declared here so every benchmark child switches it off with
# the other hook opt-outs (tests/o11_arm_isolation_r283_gates.py discovers the `*OPTOUT` names).
AUTO_SYNC_OPTOUT = "DEEPMAP_AUTO_SYNC"
# The follow-ups about that background refresh (autosync_followup: what it is doing, how it ended) — silenced on every
# channel that asks the rule (the pre-search nudge and deepmap_toolresult_freshness.py) by this one switch.
FRESHNESS_NOTE_OPTOUT = "DEEPMAP_FRESHNESS_NOTE"
# A sentinel the throttle returns on failure — below 1, so every caller (which checks `count == 1` or
# `count < 1` first) stays quiet when state can't be read/written.
_SKIP = -1
# Keep the per-hook state file from growing unbounded across many sessions.
_MAX_SESSIONS = 20
# build/ holds port_registry (stdlib-only) — the ONE source of the project key + DeepMap home.
_BUILD = Path(__file__).resolve().parent.parent / "build"
# The DeepMap code running this hook (its code_identity: plugin root + version) — what heal restarts servers onto.
_CODE_BUILD = _BUILD
_GIT_TIMEOUT_S = 5       # hooks run under a 10 s budget: never let a hung git eat it
_PROBE_TIMEOUT_S = 0.3   # per MCP port; a closed localhost port costs the full timeout on Windows
_MAX_WALK = 64           # ancestor levels project_root inspects (bounded; a stat or two per level)
_REASON_MAX = 600        # heal's reason is shown whole up to this (it names the fix; never cut mid-command)
# A heal failure the port probe can NOT disprove: the database was up but refused us.
_AUTH_WORDS = ("credential", "autherror", "authentication", "unauthorized")
# ROUND-5 G2: a heal POLICY refusal — the database is up, but it serves ANOTHER project's graph (a base claim
# an older DeepMap wrote by mistake, or a legacy 7687 owned by another project). Listening ports cannot tell
# "up" from "not yours", so a probe never supersedes one; only policy re-checks it (a newer heal, or a sync
# that passed its own owner check). heal marks it in the status file ("refused": true + "refusal_kind"); the
# reason phrases below are the same refusals' own wording (provision._foreign_claim_text /
# port_registry.legacy_owner_verdict), consulted ONLY for a status written before those fields existed
# (ROUND-6 H4): once a status carries `refused` or `refusal_kind`, those fields decide — heal's own
# NON-policy failures (a connect / credentials failure on the legacy route) are written refused:false, and
# their wording must never turn them into a final refusal.
_REFUSAL_FIELDS = ("refused", "refusal_kind")
_REFUSAL_KINDS = ("foreign-base-claim", "legacy-owner")
_REFUSAL_WORDS = ("belongs to project", "holds project", "can't be shown to be this project's")
_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1")
# ROUND-8 J4: a DeepMap-managed Neo4j instance binds 127.0.0.1 ONLY (provision's managed conf block), so its
# probe dials exactly that — never "localhost", which resolves to ::1 first on Windows (a refused IPv6 connect
# costs the whole probe timeout) and could reach a DIFFERENT listener on [::1]:<port> (Docker publishes
# [::1]:7687). == port_registry.LOOPBACK_HOST.
_INSTANCE_HOST = "127.0.0.1"
_MANAGED_ROUTES = ("registry", "instance-conf")   # routes to a DeepMap-managed instance (not an operator's)
# ROUND-7 I4: this project's MCP port is held by a DeepMap server setup could NOT restart (it may answer from
# ANOTHER graph). Recorded durably by setup (port_registry's setup blocker) and/or in a heal status
# (refusal_kind "mcp-blocked", or mcp_ok:false with `mcp_unverified` ports). FINAL like a policy refusal: a
# port probe can't disprove it (the held port listens by definition) and a refresh doesn't free the port.
BLOCKED_KIND = "mcp-blocked"
_BLOCKED_DEFAULT = "this project's MCP port is still held by a DeepMap server setup could not restart"
# ROUND-7 T9#1: what the SessionStart notify told THIS session (<root>/.deepmap/session_health.json), so the
# pre-search nudge can surface a later heal outcome that contradicts it — once.
SESSION_HEALTH_FILENAME = "session_health.json"
FOLLOWUP_WINDOW_S = 1800.0   # a heal outcome later than this after session start is not this session's news


def _deepmap_dir(root) -> Path:
    return Path(root) / ".deepmap"


def _home_state_dir():
    """DEEPMAP_HOME resolved (machine state, never a project's .deepmap), or None. Never raises."""
    try:
        return Path(os.environ.get("DEEPMAP_HOME", str(Path.home() / ".deepmap"))).resolve()
    except Exception:
        return None


def project_root(start) -> Path | None:
    """The directory whose .deepmap/graph_build.json describes the graph serving a session started in
    `start` — the SAME rule sync uses to pick the root it syncs (sync._sync_root):

      * inside a git work tree: the OUTERMOST ancestor of `start` (inclusive), up to the work tree's
        top (the nearest ancestor holding a `.git` dir or file), that holds a manifest. A session opened
        in a subdirectory of a repo synced at its root therefore finds that root's manifest (it used to
        stay silent), and a stale manifest an old subdirectory sync left behind never shadows it.
      * outside a git work tree only `start` itself counts (a non-git directory is its own project).
      * no manifest found -> `start` (resolved), exactly the pre-walk behaviour.
    DEEPMAP_HOME's own .deepmap (machine state) is never taken as a project. No subprocess: the git top
    is found by stat()ing for `.git`, so this stays cheap on the per-Grep / per-Edit hooks. Never raises;
    None only when `start` itself is unusable."""
    try:
        s = Path(start)
    except Exception:
        return None
    try:
        s = s.resolve()
    except Exception:
        return s
    try:
        home_state = _home_state_dir()
        best = None
        d = s
        for _ in range(_MAX_WALK):
            marker = d / ".deepmap"
            try:
                if (marker / GRAPH_MARKER).is_file() and (home_state is None or marker.resolve() != home_state):
                    best = d   # keep walking: the OUTERMOST manifest wins
                if (d / ".git").exists():
                    return best or s   # the work tree's top bounds the walk
            except OSError:
                pass
            parent = d.parent
            if parent == d:
                break
            d = parent
    except Exception:
        pass
    return s   # not in a git work tree (or the walk failed): the directory is its own project


def read_stdin_text() -> str:
    """The hook payload as text, decoded as UTF-8 whatever the locale. Claude Code writes the payload
    as raw UTF-8 JSON, but a plain sys.stdin.read() decodes it in the Windows ANSI code page unless
    Python runs in UTF-8 mode — a project under a non-ASCII path then resolved to a garbled `cwd` and
    every hook went silent (the access hook even logged into a mojibake SIBLING directory). Reads the
    binary buffer of a real pipe; a text-only stream (tests swap in io.StringIO) is read as-is.
    "" on any failure."""
    try:
        stream = sys.stdin
        if stream is None:
            return ""
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            return buf.read().decode("utf-8-sig", "replace")
        return stream.read()
    except Exception:
        return ""


def read_manifest(root):
    """The parsed <root>/.deepmap/graph_build.json (what the last sync persisted: verdict, nodes,
    reasons, built_at_sha), or None when missing/unreadable/not an object. Never raises."""
    try:
        data = json.loads((_deepmap_dir(root) / GRAPH_MARKER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def proven_empty(data) -> bool:
    """True iff a parsed manifest PROVES the last sync built nothing usable: verdict FAILED, or an
    explicit `nodes: 0`. setup stops before starting servers / writing .mcp.json in that state, so the
    SessionStart heal does not revive it. None / legacy / partial manifests -> False (unknown health is
    not a proven failure). Never raises."""
    try:
        if not isinstance(data, dict):
            return False
        if str(data.get("verdict") or "").strip().upper() == "FAILED":
            return True
        nodes = data.get("nodes")
        return isinstance(nodes, int) and not isinstance(nodes, bool) and nodes == 0
    except Exception:
        return False


def coverage_gaps(data) -> list:
    """The SHORT head of each persisted LOW-COVERAGE reason ('LOW COVERAGE for Pascal/Delphi — 412
    files on disk but only 0 extracted node(s)'), at most 3. [] when the manifest has none.

    Reads only what sync already stamped into the manifest — a hook never imports the extraction
    stack and never recomputes thresholds, so it cannot drift from verdict.py's own rule. Never raises."""
    out: list = []
    try:
        for reason in (data.get("reasons") or []):
            s = str(reason).strip()
            if s.upper().startswith("LOW COVERAGE"):
                out.append(s.split(";")[0].strip().rstrip("."))
    except Exception:
        return []
    return out[:3]


def _registry():
    """build/port_registry, or None. Stdlib-only (its one lazy import is never reached from here)."""
    try:
        if str(_BUILD) not in sys.path:
            sys.path.insert(0, str(_BUILD))
        import port_registry  # type: ignore
        return port_registry
    except Exception:
        return None


def _short_reason(text) -> str:
    """heal's one-line reason, whitespace-collapsed; cut only past _REASON_MAX, at a word boundary with
    an ellipsis (the old hard 240-char cut ended the line mid-command). Never raises."""
    try:
        s = " ".join(str(text or "").split())
        if len(s) <= _REASON_MAX:
            return s
        cut = s[:_REASON_MAX].rsplit(" ", 1)[0]
        return cut + " …"
    except Exception:
        return ""


def _reason_is_refusal(reason) -> bool:
    """True iff a heal reason is a POLICY refusal (another project's graph behind this project's route) by
    its own wording — see _REFUSAL_WORDS. Never raises."""
    try:
        low = " ".join(str(reason or "").split()).lower()
        return any(w in low for w in _REFUSAL_WORDS)
    except Exception:
        return False


def _has_refusal_fields(data) -> bool:
    """True iff a parsed heal status carries the refusal fields (`refused` or `refusal_kind` — every heal
    since round 5 writes both), which then decide on their own (ROUND-6 H4). Never raises."""
    try:
        return isinstance(data, dict) and any(k in data for k in _REFUSAL_FIELDS)
    except Exception:
        return False


def _status_is_refusal(data) -> bool:
    """True iff a parsed heal status records a POLICY refusal (ROUND-5 G2): "refused": true or a known
    "refusal_kind" (a contradictory refused:false + a known kind stays a refusal — the side that never steers
    toward another project's graph). The fields are AUTHORITATIVE (ROUND-6 H4): a status carrying either key
    is a refusal only by them, so an explicit refused:false is never overruled by its reason's wording (heal's
    own legacy connect / credentials failures used refusal phrasing). Only a status with NEITHER key (written
    before round 5) falls back to a failed database check whose reason is a refusal's own wording. Never
    raises."""
    try:
        if not isinstance(data, dict):
            return False
        if data.get("refused") is True or data.get("refusal_kind") in _REFUSAL_KINDS:
            return True
        if _has_refusal_fields(data):
            return False   # H4: the fields said "not a refusal" — the wording never re-decides it
        return data.get("neo4j_ok") is False and _reason_is_refusal(data.get("reason"))
    except Exception:
        return False


def _read_json_dict(path):
    """The parsed JSON object at `path`, or None (missing / unreadable / corrupt / not an object). Never
    raises."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _epoch(value):
    """`value` as a float epoch, or None (bools and non-numbers are not epochs). Never raises."""
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
    except Exception:
        return None


def _ports_note(reason: str, ports) -> str:
    """`reason` with the held port(s) named when it does not already name them. Never raises."""
    try:
        ps = [str(int(p)) for p in (ports or []) if str(p).strip().isdigit()]
        missing = [p for p in ps if p not in reason]
        if not missing:
            return reason
        tail = f"port(s) {'/'.join(missing)}"
        return f"{reason} ({tail})" if reason else f"{_BLOCKED_DEFAULT} ({tail})"
    except Exception:
        return reason


def _status_blocked(data) -> bool:
    """True iff a parsed heal status records the MCP BLOCKER (ROUND-7 I4): refusal_kind "mcp-blocked", or a
    failed MCP check naming `mcp_unverified` ports while NOT a policy refusal (a refusal's own text already
    names those ports). Never raises."""
    try:
        if not isinstance(data, dict):
            return False
        if data.get("refusal_kind") == BLOCKED_KIND:
            return True
        unverified = data.get("mcp_unverified")
        return (data.get("mcp_ok") is False and isinstance(unverified, list) and bool(unverified)
                and not _status_is_refusal(data))
    except Exception:
        return False


def _blocker_lifted(blocker, status) -> bool:
    """True iff a heal status proves setup's blocker is over: that heal STARTED the blocked servers itself
    (mcp_ok, `mcp_started` covering the blocker's servers, `mcp_started_epoch` AFTER the blocker) — the port
    was free again, so what listens there now is a current server. A start carried forward from before the
    blocker, or an "already listening" heal, proves nothing. Never raises."""
    try:
        if not isinstance(blocker, dict) or not isinstance(status, dict) or status.get("mcp_ok") is not True:
            return False
        started = status.get("mcp_started")
        if not isinstance(started, list) or not started:
            return False
        at, since = _epoch(status.get("mcp_started_epoch")), _epoch(blocker.get("ts_epoch"))
        if at is None or since is None or at <= since:
            return False
        need = {str(s) for s in (blocker.get("servers") or [])}
        return need <= {str(s) for s in started}
    except Exception:
        return False


def setup_blocker(root, key=None):
    """setup's durable MCP blocker for `root`'s project (port_registry.read_setup_blocker), or None. `key`
    skips a second git call. Never raises."""
    try:
        PR = _registry()
        read = getattr(PR, "read_setup_blocker", None) if PR is not None else None
        if read is None:
            return None
        return read(root, timeout=_GIT_TIMEOUT_S, key=key)
    except Exception:
        return None


# == port_registry.SETUP_PYTHON_PREFIX — <DEEPMAP_HOME>/run/setup-python-<root id>.json, the interpreter setup ran
# under (ISS-build:013): DeepMap's machine state, NEVER a file in the project — this hook STARTS the program a record
# names, so a repository must never be able to supply one. Read HERE without importing port_registry: this is on the
# SessionStart path, where that import costs more than the rest of the heal hook. _setup_python_norm /
# setup_python_root_id / _setup_python_tree / _setup_python_inside / _named_like_python / setup_python mirror
# port_registry's functions of the same names (read_setup_python for the last); tests/o1_heal_interpreter_r283_gates.py
# locks them together — change both or neither.
SETUP_PYTHON_PREFIX = "setup-python-"


def _setup_python_norm(path) -> str:
    """port_registry._setup_python_norm: `path` absolute, normalised, case-folded where the OS folds case."""
    return os.path.normcase(os.path.abspath(str(path)))


def setup_python_root_id(root) -> str:
    """port_registry.setup_python_root_id: the first 16 hex digits of the SHA-256 of `root`'s normalised path."""
    import hashlib   # only on the heal hook's path (the per-Grep / per-Edit hooks never need it)
    return hashlib.sha256(_setup_python_norm(root).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _setup_python_tree(root) -> Path:
    """port_registry._setup_python_tree: the nearest directory from `root` upward that holds `.git`, else `root`.
    Never raises."""
    top = Path(os.path.abspath(str(root)))
    d = top
    try:
        for _ in range(_MAX_WALK):
            try:
                if (d / ".git").exists():
                    return d
            except OSError:
                pass
            if d.parent == d:
                break
            d = d.parent
    except Exception:
        pass
    return top


def _setup_python_inside(path, top) -> bool:
    """port_registry._setup_python_inside: `path` is `top` or under it (a different drive is outside; a doubt is
    inside). Never raises."""
    try:
        p, t = _setup_python_norm(path), _setup_python_norm(top)
        try:
            return os.path.commonpath([p, t]) == t
        except ValueError:
            return False
    except Exception:
        return True


def _named_like_python(path) -> bool:
    """port_registry._named_like_python: the file name is a Python interpreter's (python…, pypy…). Never raises."""
    try:
        name = os.path.basename(str(path)).lower()
        name = name[:-4] if name.endswith(".exe") else name
        return name.startswith(("python", "pypy"))
    except Exception:
        return False


def setup_python(root):
    """The interpreter the project at `root` (project_root — the sync root) was set up under — port_registry.
    read_setup_python's answer, read without importing it: the "python" of
    <DEEPMAP_HOME>/run/setup-python-<root id>.json when the record names THIS root and an absolute path named like a
    Python interpreter, and the record lies OUTSIDE the project's own tree (a DEEPMAP_HOME inside the project is never
    read); else None (no record, an unreadable / corrupt / foreign one). Nothing under `root` is consulted. Whether the
    interpreter still exists is heal_interpreter's check. Never raises."""
    try:
        home = _home_state_dir()
        if home is None:
            return None
        path = home / "run" / f"{SETUP_PYTHON_PREFIX}{setup_python_root_id(root)}.json"
        if _setup_python_inside(path.resolve(), _setup_python_tree(root)):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        rec_root = data.get("root")
        if (not isinstance(rec_root, str) or not rec_root.strip()
                or _setup_python_norm(rec_root) != _setup_python_norm(root)):
            return None
        py = data.get("python")
        ok = isinstance(py, str) and py.strip() and os.path.isabs(py) and _named_like_python(py)
        return py if ok else None
    except Exception:
        return None


def _startable_file(path) -> bool:
    """True iff `path` is an existing file this process could start (POSIX: executable). Never raises."""
    try:
        p = str(path or "")
        return bool(p) and os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK))
    except Exception:
        return False


def _path_command(name):
    """The absolute path of the command `name` in an ABSOLUTE directory on PATH (Windows: with a PATHEXT extension
    unless `name` has one), else None. Never the working directory: this hook runs in the project's directory, whose
    files a repository controls, and shutil.which searches it FIRST on Windows (unless the environment sets
    NoDefaultCurrentDirectoryInExePath; always on Python 3.10/3.11), as a relative PATH entry ('.', '') does on POSIX.
    A name with a directory part is not a command name -> None. Never raises."""
    try:
        name = str(name or "")
        if not name or os.path.dirname(name):
            return None
        exts = [""]
        if os.name == "nt":
            pathext = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e.strip()]
            if os.path.splitext(name)[1].lower() not in {e.strip().lower() for e in pathext}:
                exts = [e.strip() for e in pathext]
        for d in os.environ.get("PATH", "").split(os.pathsep):
            d = d.strip().strip('"')
            if not d or not os.path.isabs(d):
                continue
            for ext in exts:
                cand = os.path.join(d, name + ext)
                if _startable_file(cand):
                    return cand
    except Exception:
        pass
    return None


def _program_word(word):
    """The program a DEEPMAP_PYTHON word names, as an absolute path — an absolute startable file, or a bare command
    name found by _path_command — else None: a RELATIVE path would be resolved against the working directory (the
    project), so it names nothing here. Never raises."""
    try:
        w = str(word or "")
        if os.path.isabs(w):
            return w if _startable_file(w) else None
        return _path_command(w)
    except Exception:
        return None


def _deepmap_python_argv(val) -> list:
    """DEEPMAP_PYTHON as the argv words that start it — provision._hook_python's reading of the variable (F4, ROUND-10
    L5): an absolute interpreter file -> [that file]; a command line (whitespace: 'py -3', '"C:\\Program Files\\Py\\
    python.exe" -X utf8') -> its words (port_registry.split_command_line — provision's rule too) when its first word
    names a program; one word -> the command it names on PATH. A program is an absolute startable file or a command in
    an absolute PATH directory (_program_word) — NEVER something resolved against the working directory, which for
    this hook is the project: a relative DEEPMAP_PYTHON, or a name only the project's own files provide, names nothing.
    [] when it names nothing startable (a directory, a command not on PATH, a relative path, unbalanced quotes) — the
    caller falls back. No subprocess (a hook runs on a 10 s budget): provision resolves a command line to the
    interpreter it runs, and its words start that same one. Never raises."""
    try:
        raw = str(val or "")
        s = raw.strip()
        if not s:
            return []
        if os.path.isabs(raw) and os.path.isfile(raw):   # an absolute interpreter path (spaces and all)
            return [raw] if _startable_file(raw) else []
        if os.path.isabs(s) and os.path.isdir(s):
            return []
        if re.search(r"\s", s):
            PR = _registry()   # only for a command line: the split rule lives there (one rule with provision)
            split = getattr(PR, "split_command_line", None) if PR is not None else None
            words = split(s) if split is not None else []
            first = _program_word(words[0]) if words else None
            return [first, *words[1:]] if first else []
        first = _program_word(s)
        return [first] if first else []
    except Exception:
        return []


def _managed_env_unused(py) -> bool:
    """True when `py` is DeepMap's own environment's interpreter (<DEEPMAP_HOME>/venv, which /deep-map:setup builds)
    and no command would be handed to it — build/deps_bootstrap.managed_python's rule, the one every bare-python
    command follows: setup has not finished it, or its interpreter cannot start (its pyvenv.cfg or its base Python
    gone), when the venv launcher would exit at once and heal would record nothing. deps_bootstrap is imported only
    for that interpreter. False otherwise, and on any doubt. Never raises."""
    try:
        home = os.environ.get("DEEPMAP_HOME", str(Path.home() / ".deepmap"))   # deps_bootstrap.home_dir's rule
        if not str(home).strip():
            return False   # an empty DEEPMAP_HOME names no DeepMap environment (setup refuses it)
        win = os.name == "nt"
        own = Path(home) / "venv" / ("Scripts" if win else "bin") / ("python.exe" if win else "python")
        if os.path.normcase(os.path.abspath(str(py))) != os.path.normcase(os.path.abspath(str(own))):
            return False
        if str(_BUILD) not in sys.path:
            sys.path.insert(0, str(_BUILD))
        import deps_bootstrap  # type: ignore  # stdlib-only
        return deps_bootstrap.managed_python() is None
    except Exception:
        return False


def heal_interpreter(root):
    """(argv words, source) — the Python the SessionStart heal hook starts `provision.py heal` (and the opt-in
    hibernate sweep) with, for the project whose manifest is at `root` (project_root) — ISS-build:013:
      1. DEEPMAP_PYTHON, when it names a startable interpreter — the operator's explicit choice, read by the rule
         provision._hook_python applies to the post-commit block (_deepmap_python_argv);        source "DEEPMAP_PYTHON"
      2. the interpreter THIS project's setup ran under (<DEEPMAP_HOME>/run/setup-python-<root id>.json —
         setup_python; setup records it once its MCP servers are up), while it still exists;   source "setup"
      3. the Python running this hook — the old behaviour, and the answer when nothing else is known.  source "hook"
    DeepMap's own environment (1 or 2) counts only while the commands would use it (_managed_env_unused); else heal
    runs under this hook's Python, whose heal records the remedy (/deep-map:setup) the notify hook then shows.
    Nothing in the project's own files can pick the program started here: the record is DeepMap's machine state (a
    record inside the project's tree is ignored), and DEEPMAP_PYTHON is the operator's environment, resolved without
    ever consulting the working directory (the project) — _deepmap_python_argv.
    Before, heal always ran under whichever of python / python3 read the payload; on a host where that one lacked
    DeepMap's dependencies (the installers use `py -3` / python3) every revive after a reboot failed. A few stat()s
    and one small file read — no git, no subprocess (nothing may start before the hook's one heal spawn), and no
    port_registry import unless DEEPMAP_PYTHON is a command line. Never raises."""
    try:
        words = _deepmap_python_argv(os.environ.get("DEEPMAP_PYTHON"))
        if words and not _managed_env_unused(words[0]):
            return words, "DEEPMAP_PYTHON"
        py = setup_python(root)
        if py and _startable_file(py) and not _managed_env_unused(py):
            return [py], "setup"
    except Exception:
        pass
    return [sys.executable], "hook"


def heal_status_path(root, key=None):
    """<DEEPMAP_HOME>/run/heal-<project_key>.json — the file `provision.py heal` writes — or None. Never
    raises."""
    try:
        PR = _registry()
        if PR is None:
            return None
        k = key or PR.project_key(root, timeout=_GIT_TIMEOUT_S)
        return PR.home_dir() / "run" / f"heal-{k}.json"
    except Exception:
        return None


def file_sig(path):
    """(mtime_ns, size) of `path`, or None — tells whether a file was rewritten since. Never raises."""
    try:
        st = Path(path).stat()
        return [int(st.st_mtime_ns), int(st.st_size)]
    except Exception:
        return None


def heal_problem(root, *, recheck: bool = True):
    """What the LAST SessionStart heal of this project recorded as broken, or None if nothing (or
    unknown, or no longer true). Reads <DEEPMAP_HOME>/run/heal-<project_key>.json —
    {"ts","neo4j_ok","mcp_ok","reason","refused","refusal_kind",...}, written by `provision.py heal` on
    every run — and setup's durable MCP blocker (<DEEPMAP_HOME>/run/setup-blocked-<project_key>.json).
    Returns ("refused" | "blocked" | "neo4j" | "mcp", reason).

    "blocked" (ROUND-7 I4): this project's MCP port is held by a DeepMap server setup could NOT restart, so
    it may answer from ANOTHER graph. From setup's blocker file it is FINAL until a later setup succeeds
    (setup clears the file) or a newer heal STARTED the blocked servers itself (_blocker_lifted) — a newer
    manifest (a refresh) does not free the port, and no port probe can disprove it. A heal status can carry
    the same fact (refusal_kind "mcp-blocked", or mcp_ok:false with `mcp_unverified` ports); that form ages
    like any heal status (below), but is never probe-superseded either.

    A status file OLDER than graph_build.json is ignored: a sync (setup / refresh) since then reached
    the database, so that failure is superseded. A file that exists but can't be parsed, or has no
    explicit `false` (or refusal), is treated as unknown -> None (never silences a hook on a guess).

    "refused" (ROUND-5 G2) is a POLICY refusal: the database is up but this project must not use it (it
    holds another project's graph — _status_is_refusal). It is FINAL until policy re-checks it (a newer
    heal, or a sync since, above): a port probe never supersedes it, because listening ports look exactly
    the same whether the graph behind them is this project's or not. A status that carries the refusal
    fields is classified by them alone (ROUND-6 H4): with refused:false its reason's wording neither makes it
    a refusal nor vetoes the probe below — heal's legacy connect / credentials failures are plain outages.

    `recheck` (default on): the SessionStart hooks read this file while THIS session's heal is still
    running, so what they see is usually the PREVIOUS session's verdict — and one transient failure
    (a JVM slower than heal's wait, a server that came up a moment late) used to tell a whole healthy
    session that DeepMap was down. So a recorded "neo4j"/"mcp" failure is dropped when a live probe shows
    it is no longer true (heal_problem_superseded). Never raises."""
    try:
        PR = _registry()
        if PR is None:
            return None
        key = PR.project_key(root, timeout=_GIT_TIMEOUT_S)
        path = PR.home_dir() / "run" / f"heal-{key}.json"
        return _heal_problem_at(root, key, path, recheck=recheck)
    except Exception:
        return None


def _heal_problem_at(root, key, path, *, recheck: bool):
    """heal_problem's rule for a KNOWN project key and heal status path (heal_problem computes both with one git call;
    the auto-sync follow-up reads them from what the SessionStart notify recorded, so it needs no git). Never
    raises."""
    try:
        path = Path(path)
        raw = _read_json_dict(path) if path.is_file() else None
        data = raw
        if raw is not None:
            try:
                if path.stat().st_mtime < (_deepmap_dir(root) / GRAPH_MARKER).stat().st_mtime:
                    data = None   # a sync since reached the database: this status is superseded
            except OSError:
                pass
        reason = _short_reason(data.get("reason")) if data is not None else ""
        if data is not None and data.get("refusal_kind") == BLOCKED_KIND:
            return ("blocked", _ports_note(reason or _BLOCKED_DEFAULT, data.get("mcp_unverified")))
        if data is not None and _status_is_refusal(data):
            return ("refused", reason)   # G2: final until policy re-checks it — never probe-superseded
        # I4: setup's durable blocker — FINAL whatever the manifest's age; only a newer heal that started the
        # blocked servers itself (read from the raw status, even one a refresh has since aged out) lifts it
        blocker = setup_blocker(root, key=key)
        if blocker is not None and not _blocker_lifted(blocker, raw):
            why = _short_reason(blocker.get("reason")) or _BLOCKED_DEFAULT
            return ("blocked", _ports_note(why, blocker.get("ports")))
        if data is None:
            return None
        if _status_blocked(data):
            return ("blocked", _ports_note(reason or _BLOCKED_DEFAULT, data.get("mcp_unverified")))
        if data.get("neo4j_ok") is False:
            problem = ("neo4j", reason)
        elif data.get("mcp_ok") is False:
            problem = ("mcp", reason)
        else:
            return None
        if recheck and heal_problem_superseded(root, problem, refusal_fields=_has_refusal_fields(data)):
            return None
        return problem
    except Exception:
        return None


def bolt_target(root):
    """(host, port) of this project's Neo4j for a liveness probe — only when it is the project's OWN
    instance or the operator's endpoint (port_registry.resolve_neo4j_route source registry /
    instance-conf / env). None for a legacy-unverified 7687 (a listener there may be another project's or
    another program's database, so a probe proves nothing), for no route, for a host that is not
    this machine (an operator's remote / Aura endpoint: a hook never connects off the box), and — ROUND-5
    G2 — for a registry claim on the base pair (7687) whose store's owner record names ANOTHER project
    (the <= v0.27.4 mis-adoption: that 7687 listening says nothing about whose graph it serves).

    ROUND-8 J4: for the project's OWN instance (source registry / instance-conf) the host is always
    127.0.0.1 — the only address a DeepMap instance binds — whatever loopback spelling the URI carries (an
    older build wrote bolt://localhost:<port>). An operator's loopback endpoint (source env) keeps its host;
    port_listening then tries 127.0.0.1 before ::1 for "localhost". Never raises."""
    try:
        PR = _registry()
        resolve = getattr(PR, "resolve_neo4j_route", None) if PR is not None else None
        if resolve is None:
            return None
        route = resolve(root, timeout=_GIT_TIMEOUT_S)
        if route.problem or not route.uri or route.source not in ("registry", "instance-conf", "env"):
            return None
        if route.source == "registry" and (getattr(route, "bolt", None), getattr(route, "http", None)) \
                == (getattr(PR, "BASE_BOLT", 7687), getattr(PR, "BASE_HTTP", 7474)):
            foreign = getattr(PR, "base_record_foreign_owner", None)
            if foreign is None or foreign(root, timeout=_GIT_TIMEOUT_S):
                return None   # another project's store (or no way to tell): a probe can't vouch for it
        m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?(\[[^\]]+\]|[^/:?#@]+)(?::(\d+))?",
                     str(route.uri))
        if not m:
            return None
        host = m.group(1).strip("[]")
        if host.lower() not in _LOOPBACK:
            return None   # a hook never connects off the machine (an operator's remote/Aura endpoint)
        if route.source in _MANAGED_ROUTES:
            host = _INSTANCE_HOST   # J4: the instance binds 127.0.0.1 only — never probe ::1 for it
        port = int(m.group(2)) if m.group(2) else 7687
        return (host, port) if 0 < port <= 65535 else None
    except Exception:
        return None


def heal_problem_superseded(root, problem, *, refusal_fields: bool = False) -> bool:
    """True iff a live probe shows a recorded heal failure is no longer true, so it must not steer the
    session away from DeepMap:
      * "mcp"   -> both of this project's MCP ports (its .mcp.json / registry pair) accept connections;
      * "neo4j" -> both MCP ports AND this project's OWN bolt port (bolt_target) accept connections, and
                   the recorded reason is not a credentials rejection (a database that is up but refused
                   us looks exactly like a healthy one to a port probe).
    NEVER for a POLICY refusal (ROUND-5 G2): kind "refused", or a reason that is one (another project's
    graph behind this project's route) — a probe cannot tell "up" from "not yours"; only policy re-checks it.
    NEVER for kind "blocked" (ROUND-7 I4) either: the held MCP port listens by definition.
    `refusal_fields` (ROUND-6 H4): the problem was read from a status that carries `refused` /
    `refusal_kind`, which already said "not a refusal" — its reason's wording is then NOT re-read as one
    (heal's legacy connect / credentials failures reuse refusal phrasing). Default False: a caller passing a
    bare (kind, reason) keeps the wording check (a status written before those fields).
    Anything unknown (no ports, no own route, a probe error) -> False: the recorded failure stands.
    Costs at most three short localhost connects, only when a failure is on record. Never raises."""
    try:
        kind, reason = problem
        if kind in ("refused", "blocked") or (not refusal_fields and _reason_is_refusal(reason)):
            return False   # policy refusal / MCP blocker: a listening port proves nothing (ROUND-5 G2, ROUND-7 I4)
        ports = mcp_ports(root)
        if not ports or not all(port_listening(p) for p in ports):
            return False
        if kind == "mcp":
            return True
        if kind != "neo4j":
            return False
        if any(w in str(reason or "").lower() for w in _AUTH_WORDS):
            return False
        target = bolt_target(root)
        return bool(target) and port_listening(target[1], host=target[0])
    except Exception:
        return False


def _mcp_json_pair(root, PR):
    """(declared, pair) from the project's .mcp.json at the git toplevel: declared=True when it names the
    deepmap servers at all; pair = their (search, embed) LOCAL ports, or None when not both are local.
    Never raises (-> (False, None))."""
    try:
        top = Path(PR.project_identity(root, timeout=_GIT_TIMEOUT_S)) if PR else Path(root)
        servers = {}
        try:
            obj = json.loads((top / ".mcp.json").read_text(encoding="utf-8-sig"))
            servers = obj.get("mcpServers", {}) if isinstance(obj, dict) else {}
            servers = servers if isinstance(servers, dict) else {}
        except Exception:
            servers = {}
        urls = [str((servers.get(n) or {}).get("url", "")) if isinstance(servers.get(n), dict) else ""
                for n in ("deepmap-search", "deepmap-embed")]
        if not any(urls):
            return (False, None)
        got = [re.search(r"//(?:127\.0\.0\.1|localhost):(\d+)/", u) for u in urls]
        return (True, (int(got[0].group(1)), int(got[1].group(1))) if all(got) else None)
    except Exception:
        return (False, None)


def mcp_json_foreign_owner(root):
    """The root of ANOTHER project that the DeepMap port registry gives the MCP pair this project's .mcp.json
    names (e.g. a copied checkout that inherited the original's .mcp.json) — the tools the session connects to
    are then THAT project's servers and answer from ITS graph. None when the pair is this project's own,
    unregistered, or unknown. Never raises (ROUND-7 I4, the hook side of the doctor finding)."""
    try:
        PR = _registry()
        claimed = getattr(PR, "mcp_pair_claimed_by_other", None) if PR is not None else None
        if claimed is None:
            return None
        declared, pair = _mcp_json_pair(root, PR)
        if not declared or not pair:
            return None
        return claimed(root, pair, timeout=_GIT_TIMEOUT_S)
    except Exception:
        return None


def mcp_ports(root):
    """The (search, embed) ports Claude Code connects to for this project: its .mcp.json at the git
    toplevel (the file provision writes and the client reads), else the registry pair. None when
    unknown — the caller then says nothing rather than guess the global 8787/8788. A .mcp.json pair the
    registry gives to ANOTHER project (mcp_json_foreign_owner) is not this project's either -> None: those
    listening servers must never "supersede" this project's own failure or pass for its servers being up.
    Never raises."""
    try:
        PR = _registry()
        declared, pair = _mcp_json_pair(root, PR)
        if declared:
            # .mcp.json decides; only a LOCAL server can be probed from here
            if not pair:
                return None
            claimed = getattr(PR, "mcp_pair_claimed_by_other", None) if PR is not None else None
            if claimed is not None and claimed(root, pair, timeout=_GIT_TIMEOUT_S):
                return None   # another project's servers (ROUND-7 I4): unknown for THIS project
            return pair
        pair = PR.lookup_ports(root) if PR else None
        return (int(pair[0]), int(pair[1])) if pair else None
    except Exception:
        return None


def _probe_hosts(host) -> tuple:
    """The addresses a probe of `host` dials, in order: "localhost" -> 127.0.0.1 THEN ::1 (IPv4 first — a
    refused IPv6 connect costs a whole timeout on Windows, and every DeepMap listener binds 127.0.0.1);
    a bracketed IPv6 literal unbracketed; anything else as given. Never raises."""
    try:
        h = str(host if host is not None else _INSTANCE_HOST).strip()
        if h.lower() == "localhost":
            return (_INSTANCE_HOST, "::1")
        return (h[1:-1] if h.startswith("[") and h.endswith("]") else h,)
    except Exception:
        return (_INSTANCE_HOST,)


_MCP_SERVER_NAMES = ("deepmap-search", "deepmap-embed")


def _code_label(rec_root, rec_ver, cur_ver) -> str:
    """How the notify names the code a server runs: its version when that differs from the installed one, else the
    version + directory (the same version from another copy), else the directory. Never raises."""
    try:
        if rec_ver and str(rec_ver) != str(cur_ver or ""):
            return str(rec_ver)
        if rec_root:
            return f"{rec_ver} from {rec_root}" if rec_ver else str(rec_root)
        return str(rec_ver or "an older build")
    except Exception:
        return "an older build"


def _heal_mcp_pair(root, PR):
    """The (search, embed) pair the SessionStart heal revives and judges for this project — provision.apply_ports_for
    (allocate=False): DEEPMAP_SEARCH_PORT / DEEPMAP_EMBED_PORT when either is set (provision.mcp_port's defaults for
    the other), else the registry pair, else the pair .mcp.json declares unless the registry gives it to ANOTHER
    project. None when unknown. Never raises."""
    try:
        es, ee = os.environ.get("DEEPMAP_SEARCH_PORT"), os.environ.get("DEEPMAP_EMBED_PORT")
        if es is not None or ee is not None:
            return (int(es or 8787), int(ee or 8788))
        pair = PR.lookup_ports(root)
        if pair:
            return (int(pair[0]), int(pair[1]))
        declared, pair = _mcp_json_pair(root, PR)
        if not declared or not pair:
            return None
        claimed = getattr(PR, "mcp_pair_claimed_by_other", None)
        if claimed is not None and claimed(root, pair, timeout=_GIT_TIMEOUT_S):
            return None
        return (int(pair[0]), int(pair[1]))
    except Exception:
        return None


def mcp_code_stale(root):
    """ROUND-8 J5 (hook side): {"old": <version or code root>, "new": <installed version or None>} when the SessionStart
    heal WILL restart a server on this project's MCP ports onto the code running this hook — the state right after a
    plugin update, when the client's connection to the old servers breaks as heal replaces them.

    ROUND-10 L1: the note promises exactly the restart heal performs, so both sides apply ONE rule —
    port_registry.own_server_code_stale, the mirror of provision._own_server_code_stale over the same files and OS
    facts (the owner sidecar, the pid file, the process's argv, its start stamp, the registry). In short: an owner
    record naming the pid-file pid decides by its project key + recorded code (restarts only move FORWARD —
    port_registry.code_restart_verdict: never onto an older version, never between two directories holding the SAME
    version; its own plugin dir only when updated in place after it started) + the process being the recorded one;
    with no record — none, one naming ANOTHER pid (the record of an EARLIER process on the port), or one that can't be
    read — the port must be REGISTERED to this project and the pid-file process must run this server's script from a
    DeepMap root (a v0.27.4 server: pid file only), be older than its pid file and be nobody else's. The ports judged
    are the pair heal revives, which must be the pair the client connects to (mcp_ports). None otherwise (unknown
    ports, a dead pid, an unreadable command line, current or newer code). Reads <DEEPMAP_HOME>/run and the registry;
    no port probe. Never raises."""
    try:
        PR = _registry()
        stale_fn = getattr(PR, "own_server_code_stale", None) if PR is not None else None
        ident_fn = getattr(PR, "code_identity", None) if PR is not None else None
        if stale_fn is None or ident_fn is None:
            return None
        ports = mcp_ports(root)
        if not ports:
            return None
        heal_pair = _heal_mcp_pair(root, PR)
        if not heal_pair or tuple(int(p) for p in ports) != tuple(heal_pair):
            return None   # heal revives another pair than the one the client connects to: it promises nothing here
        current = ident_fn(_CODE_BUILD)
        cur_ver = current.get(getattr(PR, "CODE_VERSION_FIELD", "code_version"))
        keys_fn = getattr(PR, "own_keys", None)
        keys = keys_fn(root, timeout=_GIT_TIMEOUT_S) if keys_fn is not None else None
        same_root = getattr(PR, "_same_code_root", None)
        for name, port in zip(_MCP_SERVER_NAMES, ports):
            got = stale_fn(root, name, int(port), current, keys=keys, timeout=_GIT_TIMEOUT_S)
            if got:
                rec_root, rec_ver = got
                if rec_root and not rec_ver and same_root is not None \
                        and same_root(rec_root, current.get(getattr(PR, "CODE_ROOT_FIELD", "code_root"))):
                    # ROUND-10 L3: this very plugin dir, updated in place after the server started (heal's wording)
                    return {"old": f"an older build from {rec_root}, updated in place after the server started",
                            "new": cur_ver}
                if rec_root and not rec_ver:
                    rec_ver = getattr(PR, "_plugin_version", lambda r: None)(rec_root)
                return {"old": _code_label(rec_root, rec_ver, cur_ver), "new": cur_ver}
        return None
    except Exception:
        return None


def port_listening(port, host: str = "127.0.0.1", timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """True iff something accepts a TCP connection on host:port (for "localhost": on 127.0.0.1, else ::1 —
    _probe_hosts). Never raises."""
    try:
        import socket   # here, not at the top: most hook calls never probe a port (socket + selectors at every start)
        p = int(port)
        for h in _probe_hosts(host):
            try:
                with socket.create_connection((h, p), timeout=timeout):
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def resolve_root(payload) -> Path | None:
    """The project root for this hook call: project_root() of the payload's cwd (so a session opened in
    a subdirectory finds the manifest of the root it was synced at), else DEEPMAP_PROJECT_ROOT verbatim
    (an explicit override), else project_root() of the process cwd. Never raises."""
    try:
        if isinstance(payload, dict) and payload.get("cwd"):
            return project_root(payload["cwd"])
    except Exception:
        pass
    env = os.environ.get("DEEPMAP_PROJECT_ROOT")
    if env:
        try:
            return Path(env)
        except Exception:
            return None
    try:
        return project_root(Path.cwd())
    except Exception:
        return None


def graph_present(root) -> bool:
    """True iff <root>/.deepmap/graph_build.json exists (DeepMap is provisioned here). Never raises."""
    try:
        return (_deepmap_dir(root) / GRAPH_MARKER).exists()
    except Exception:
        return False


def _env_off(name: str) -> bool:
    try:
        return str(os.environ.get(name, "")).strip().lower() in _OFF
    except Exception:
        return False


def feature_enabled(env_name: str) -> bool:
    """Default ON. OFF iff `env_name` is an explicit off-value, or the master DEEPMAP_HOOKS is off
    (which covers every hook, the SessionStart heal included). Never raises."""
    if _env_off("DEEPMAP_HOOKS"):
        return False
    return not _env_off(env_name)


def bump_session_count(root, filename: str, session_id) -> int:
    """Increment and return this session's counter in <root>/.deepmap/<filename>. A first call for a
    session returns 1, the next 2, etc.; distinct sessions are independent; a missing/empty
    session_id shares one "_nosession" bucket. The store is bounded to the most-recent ~20 sessions.
    Written atomically (temp + os.replace). On ANY failure returns a sentinel (< 1) so the caller
    stays quiet — a broken state file must never turn into a crash or a repeated reminder."""
    try:
        sid = str(session_id) if session_id not in (None, "") else "_nosession"
        d = _deepmap_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        path = d / filename
        counts: dict = {}
        try:
            raw = path.read_text(encoding="utf-8")
            loaded = json.loads(raw) if raw.strip() else {}
            if isinstance(loaded, dict) and isinstance(loaded.get("counts"), dict):
                counts = dict(loaded["counts"])
        except Exception:
            counts = {}  # missing / corrupt -> start fresh
        n = int(counts.get(sid, 0) or 0) + 1
        # Re-insert last so it counts as most-recent, then bound the store (dicts keep insertion order).
        counts.pop(sid, None)
        counts[sid] = n
        if len(counts) > _MAX_SESSIONS:
            for old in list(counts.keys())[: len(counts) - _MAX_SESSIONS]:
                counts.pop(old, None)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"counts": counts}), encoding="utf-8")
        os.replace(tmp, path)
        return n
    except Exception:
        return _SKIP


def session_count(root, filename: str, session_id) -> int:
    """This session's CURRENT counter in <root>/.deepmap/<filename> (bump_session_count's store) WITHOUT
    bumping it — 0 when unknown / unreadable. Lets a hook skip work (e.g. notify's bounded wait) for a
    session it already served. Never raises."""
    try:
        sid = str(session_id) if session_id not in (None, "") else "_nosession"
        data = _read_json_dict(_deepmap_dir(root) / filename) or {}
        counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
        n = counts.get(sid, 0)
        return int(n) if isinstance(n, int) and not isinstance(n, bool) else 0
    except Exception:
        return 0


# ---- freshness (shared by the SessionStart notify and the pre-search nudge) ----------------------
def git_argv(git, root, *args) -> list:
    """`git [-c core.longpaths=true] -C <root> <args>` as an argv list — every git probe the hooks run.
    ROUND-8 J6: on Windows git refuses any path past MAX_PATH (260) unless core.longpaths is set, so in a
    repository whose root is ~200+ chars it could not read its own loose objects (.git/objects/xx/<38 hex>)
    and `rev-list` failed — the freshness notes then went silent or wrong. The flag is harmless where paths
    are short; elsewhere than Windows it is not passed (== port_registry.git_longpaths_args)."""
    pre = ["-c", "core.longpaths=true"] if os.name == "nt" else []
    return [git, *pre, "-C", str(root), *args]


def git_head(root):
    """The 40-hex HEAD SHA via a short-timeout `git rev-parse` subprocess, or None (no git, not a repo,
    timeout). NOT an import of build/sync (its import chain pulls the ML stack) and NOT a hand-parse of
    .git/HEAD (breaks on packed-refs and .git-file worktrees). Never raises."""
    try:
        import shutil
        import subprocess
        git = shutil.which("git")
        if not git:
            return None
        p = subprocess.run(git_argv(git, root, "rev-parse", "HEAD"), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_S)
        sha = (p.stdout or "").strip().lower()
        if p.returncode == 0 and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            return sha
    except Exception:
        return None
    return None


def sha_matches(a, b) -> bool:
    """Two commit SHAs name the same commit (either may be abbreviated, >= 7 hex). Never raises."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if len(a) < 7 or len(b) < 7:
        return False
    return a.startswith(b) or b.startswith(a)


def commit_distance(root, built):
    """How many commits HEAD is ahead of `built` (`git rev-list --count <built>..HEAD`), or None when git
    can't tell (the commit is gone after a rebase, a shallow clone, no git). Never raises."""
    try:
        import shutil
        import subprocess
        b = str(built or "").strip()
        git = shutil.which("git")
        if not git or len(b) < 7 or not all(c in "0123456789abcdefABCDEF" for c in b):
            return None
        p = subprocess.run(git_argv(git, root, "rev-list", "--count", f"{b}..HEAD"), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_S)
        out = (p.stdout or "").strip()
        return int(out) if p.returncode == 0 and out.isdigit() else None
    except Exception:
        return None


def distance_text(n) -> str:
    """' (3 commits later)' / ' (1 commit later)' — '' when the distance is unknown. Never raises."""
    try:
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return f" ({n} commit{'s' if n != 1 else ''} later)"
    except Exception:
        pass
    return ""


def freshness_state(root, data, *, edits: bool = False) -> dict:
    """The graph's freshness against the work tree, from what sync persisted (built_at_sha, else built_at_base_sha;
    built_at_dirty / dirty), as {"kind", "built": <7>|None, "head": <7>|None, "head_full": <40>|None,
    "distance": int|None, "path": str|None}. `kind`, first match wins:
      "stale"  HEAD moved since the build (`distance` = commits since, when git can tell);
      "dirty"  HEAD unchanged, but the build ran on uncommitted changes;
      "edited" (only with `edits=True`) the edit hook recorded a source edit (.deepmap/last_source_edit.json, its
               `path`) after the build saw the work tree (manifest edits_seen_through_epoch, else
               edits_seen_through_utc, else built_at_utc) — the hook-only kind the SessionStart notify uses;
      None     fresh, or unknown (no SHA / no git).
    Auto-sync H3: the hooks never compute a source fingerprint — they honour the background refresh's own verdict.
    When .deepmap/autosync.json's last_run decided "fresh" for the CURRENT HEAD (fresh_at_head, the full SHA) with the
    manifest's own source_fingerprint, after the manifest was written, the graph is current even though HEAD moved
    (a docs-only commit) or it was built dirty (_fresh_proof) — honoured only while auto-sync is on for this project
    (autosync_on), so with it off every hook says exactly what it said before. Costs one short git call (two when
    stale) and a few small file reads. Never raises."""
    out = {"kind": None, "built": None, "head": None, "head_full": None, "distance": None, "path": None}
    try:
        if not isinstance(data, dict):
            return out
        built = str(data.get("built_at_sha") or data.get("built_at_base_sha") or "")
        head = None
        if built:
            out["built"] = built[:7]
            head = git_head(root)
            if head:
                out["head"], out["head_full"] = head[:7], head
        proof = _fresh_proof(root, data, head) if head else None
        if head and proof is None:
            if not sha_matches(built, head):
                out.update(kind="stale", distance=commit_distance(root, built))
                return out
            if data.get("built_at_dirty") is True or data.get("dirty") is True:
                out["kind"] = "dirty"
                return out
        if edits:
            path = _edited_since(root, data, proof)
            if path:
                out.update(kind="edited", path=path)
    except Exception:
        pass
    return out


def staleness(root, data):
    """The graph's freshness against the work tree (freshness_state without the hook-only "edited" kind):
      {"kind": "stale", "built": <7>, "head": <7>, "distance": <int|None>}  HEAD moved since the build;
      {"kind": "dirty", "head": <7>}                                          HEAD unchanged, but the build
                                                                              ran on uncommitted changes;
      None                                                                    fresh (or proven fresh by the
                                                                              background refresh), or no SHA / no git.
    Costs one short git call (two when stale). Never raises."""
    try:
        st = freshness_state(root, data)
        if st.get("kind") == "stale":
            return {"kind": "stale", "built": st["built"], "head": st["head"], "distance": st["distance"]}
        if st.get("kind") == "dirty":
            return {"kind": "dirty", "head": st["head"]}
    except Exception:
        return None
    return None


# ---- what notify told THIS session (ROUND-7 T9#1: the same-session heal window) ------------------
def read_session_health(root, session_id):
    """This session's record in <root>/.deepmap/session_health.json — {"start", "told", "servers_down",
    "heal_path", "seen", "resolved"} as the SessionStart notify wrote it — or None. Never raises."""
    try:
        sid = str(session_id) if session_id not in (None, "") else "_nosession"
        data = _read_json_dict(_deepmap_dir(root) / SESSION_HEALTH_FILENAME) or {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
        ent = sessions.get(sid)
        return dict(ent) if isinstance(ent, dict) else None
    except Exception:
        return None


def write_session_health(root, session_id, entry: dict) -> bool:
    """Replace this session's record (bounded to the most-recent ~20 sessions; atomic temp + os.replace;
    only under <root>/.deepmap/, which exists wherever the notify runs). True iff written. Never raises."""
    try:
        sid = str(session_id) if session_id not in (None, "") else "_nosession"
        d = _deepmap_dir(root)
        if not d.is_dir():
            return False
        path = d / SESSION_HEALTH_FILENAME
        data = _read_json_dict(path) or {}
        sessions = dict(data.get("sessions")) if isinstance(data.get("sessions"), dict) else {}
        sessions.pop(sid, None)
        sessions[sid] = dict(entry)
        if len(sessions) > _MAX_SESSIONS:
            for old in list(sessions.keys())[: len(sessions) - _MAX_SESSIONS]:
                sessions.pop(old, None)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---- auto-sync: DeepMap's background refresh at session start (build/auto_sync.py) ---------------------------------
# The heal hook starts it (deepmap_cli.py sync <root> --unattended=session-start) after the heal; the notify announces
# it; the pre-search nudge and the tool-result hook (deepmap_toolresult_freshness.py) tell the session — once per fact —
# that it is running and how it ended. The RULES live in build/auto_sync.py (enabled / precheck) and
# build/sync_status.py (who holds the sync lock and whether that process is alive); the hooks load them lazily from
# the plugin's own build/ and behave exactly as before when they can't (an older plugin, a broken install).
# Records the hooks read or write, all under <root>/.deepmap/ (which exists wherever the marker does):
AUTOSYNC_STATE = "autosync.json"            # the runner's record (current run, last_run, pause / backoff)
AUTOSYNC_SPAWN = "autosync_spawn.json"      # written by the heal hook right after it starts the runner
AUTOSYNC_TOLD = "autosync_told.json"        # which follow-ups each session was already told
AUTOSYNC_QUIET = "autosync_quiet.json"      # per session: the records' signatures when it last had nothing to be told
LAST_SOURCE_EDIT = "last_source_edit.json"  # the edit hook's newest recorded source edit (one atomic record)
SYNC_RUNNING = "sync_running.json"          # == sync_status.RUNNING_FILE (the holder of .deepmap/sync.lock)
QUIET_SCHEMA = 1
_QUIET_MAX = 40                             # autosync_quiet.json entries kept (session x directory)
# The records autosync_followup's answer is read from (all under <root>/.deepmap/). A "nothing to say" answer stays
# true while none of them is rewritten (and, when it rests on time or on a live process, until its stamp's `until`) —
# quiet_holds / the tool-result hook's own pre-import check (deepmap_toolresult_freshness._quiet_fast, which mirrors
# quiet_holds: tests/auto_sync_hooks_gates section J locks the two together). Not in the list on purpose:
# autosync_told.json only ever grows for a session (more told facts = less to say), and every other input —
# DEEPMAP_AUTO_SYNC and the other env switches, instance.json, the machine switch, the plugin's CLI, a heal refusal or
# setup's blocker — can only turn an answer INTO "nothing" while a session runs (a stamp is written only while
# auto-sync is on and no refusal / blocker stands).
_QUIET_FILES = (SESSION_HEALTH_FILENAME, AUTOSYNC_STATE, SYNC_RUNNING, AUTOSYNC_SPAWN, GRAPH_MARKER)
NOT_STARTED_AFTER_S = 120.0                 # "the refresh announced at session start did not start" after this long
QUIET_LIVE_RECHECK_S = 60.0                 # a quiet stamp that rests on a live process is re-checked this often
_OUTCOME_SLACK_S = 5.0                      # an outcome this close before the session start still belongs to it
_TOLD_MAX_KEYS = 50                         # per session (the store keeps the ~20 most-recent sessions)
_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
# What the refresh is doing, in the words the agent is told (== the runner's / sync's phase names).
_PHASE_WORDS = {"start": "checking what changed", "checking": "checking what changed",
                "waiting-db": "waiting for the database",
                "waiting-slot": "waiting for another project's refresh to finish", "extract": "reading the code",
                "write": "writing the graph", "embed": "computing embeddings", "manifest": "finishing",
                "recording": "finishing"}
_GIT_BUSY_MARKERS = (("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
                     ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("BISECT_LOG", "bisect"),
                     ("sequencer", "sequence"))

# Appended to the READY / coverage announce and to the nudge's reminder whenever the manifest records stale_kept > 0
# (auto-sync on or off): the background refresh prunes only what it can prove removed, so such nodes stay until a
# /deep-map:refresh, and the verdict stays READY (H7) — the caveat travels with it.
STALE_KEPT_NOTE = (
    " NOTE: the graph still holds {n} node(s) for code that may have been removed since (DeepMap's background refresh "
    "deletes a node only when it re-read the node's file or the file is gone); if a result names a symbol you cannot "
    "find in the file, it is stale — /deep-map:refresh removes such nodes."
)
# The follow-ups (autosync_followup — said by the pre-search nudge or the tool-result hook, each once per session).
# {build} names the build the tools answer from — "the build at <sha7>", or "a previous build" when the manifest records
# no commit (a project outside git); {at} is " (at <sha7>)" or "" (_build_words).
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
FOLLOW_SYNCED_DIRTY = " plus the uncommitted edits present when it started"
FOLLOW_SYNCED_DEGRADED = " (its verdict is DEGRADED: {reason})"
FOLLOW_SYNCED_KEPT = (
    "It kept {n} node(s) for code that may since have been removed (a background refresh deletes a node only when it "
    "re-read the node's file or the file is gone); if a result names a symbol you cannot find in the file, it is "
    "stale — /deep-map:refresh removes such nodes."
)
FOLLOW_SYNCED_DEFERRED = (
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
FOLLOW_BUILD_AT = "the build at {sha}"   # {build} when the manifest records the commit it was built at
FOLLOW_BUILD_ANY = "a previous build"     # ... and when it records none (a project outside git)
_SUGGEST = {"refresh": ", and suggest the user run /deep-map:refresh",
            "setup": ", and suggest the user run /deep-map:setup",
            "doctor": ", and suggest the user run /deep-map:doctor"}
_SKIP_SILENT = ("skipped:foreign-owner", "skipped:setup-blocked")   # other channels already steer away


def _build_module(name: str):
    """build/<name>.py of the plugin running this hook (the sibling of hooks/), loaded once per process, or None (no
    such file, or it fails to import). Loaded by PATH, so a same-named module elsewhere on sys.path is never taken for
    it; build/ goes on sys.path for its own lazy imports (port_registry, sync_status). Never raises."""
    try:
        path = _BUILD / f"{name}.py"
        mod = sys.modules.get(name)
        if mod is not None:
            try:
                if Path(getattr(mod, "__file__", "") or "").resolve() == path.resolve():
                    return mod
            except Exception:
                pass
        if not path.is_file():
            return None
        if str(_BUILD) not in sys.path:
            sys.path.insert(0, str(_BUILD))
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(name, None)
            return None
        return mod
    except Exception:
        return None


def _auto_sync():
    """build/auto_sync (stdlib-only at import: enabled / precheck / human_duration), or None. Never raises."""
    return _build_module("auto_sync")


def _sync_status():
    """build/sync_status (stdlib-only at import: read_running / holder_live), or None. Never raises."""
    return _build_module("sync_status")


def _sid(session_id) -> str:
    return str(session_id) if session_id not in (None, "") else "_nosession"


def _utc(epoch: float) -> str:
    return time.strftime(_UTC_FMT, time.gmtime(epoch))


def _utc_epoch(value):
    """A "YYYY-MM-DDTHH:MM:SSZ" stamp as epoch seconds, or None. Never raises."""
    try:
        import calendar
        return float(calendar.timegm(time.strptime(str(value).strip(), _UTC_FMT)))
    except Exception:
        return None


def _write_json_atomic(path, obj) -> bool:
    """Write `obj` as JSON to `path` through a temp file in the same directory + os.replace (a Windows sharing
    violation on the replace is retried briefly); the directory must exist (a hook never creates .deepmap). True iff
    written. Never raises, never leaves a partial target."""
    tmp = None
    try:
        path = Path(path)
        if not path.parent.is_dir():
            return False
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(str(tmp), str(path))
                return True
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        pass
    try:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    except Exception:
        pass
    return False


def human_duration(seconds) -> str:
    """auto_sync.human_duration (< 90 s -> "N s"; < 90 min -> "N min"; else "H h M min"); its mirror when that module
    can't be loaded. Never raises."""
    try:
        AS = _auto_sync()
        fn = getattr(AS, "human_duration", None) if AS is not None else None
        if fn is not None:
            return str(fn(seconds))
    except Exception:
        pass
    try:
        s = max(0.0, float(seconds))
        if s < 90:
            return f"{int(round(s))} s"
        if s < 5400:
            return f"{int(round(s / 60.0))} min"
        h, m = int(s // 3600), int(round((s % 3600) / 60.0))
        if m == 60:
            h, m = h + 1, 0
        return f"{h} h {m} min"
    except Exception:
        return "a while"


def _phase_words(phase) -> str:
    """What a refresh phase means, in the words the agent is told — sync_status.phase_words (the preempting sync's
    texts use the same words), else its mirror here. Never raises."""
    try:
        SS = _sync_status()
        fn = getattr(SS, "phase_words", None) if SS is not None else None
        if fn is not None:
            words = fn(phase)
            if isinstance(words, str) and words and words != str(phase):
                return words
    except Exception:
        pass
    return _PHASE_WORDS.get(str(phase or ""), "refreshing the graph")


def git_busy(root):
    """The git operation in progress in `root`'s repository — "rebase" | "merge" | "cherry-pick" | "revert" |
    "bisect" | "sequence" — or None (none, no git, unknown). The hook-cheap subset of sync.git_busy: ONE
    `git rev-parse --git-path ...` call and a stat per marker (no ls-files: unmerged entries are the runner's
    fingerprint's business). Never raises."""
    try:
        import shutil
        import subprocess
        git = shutil.which("git")
        if not git:
            return None
        args = ["rev-parse"]
        for marker, _op in _GIT_BUSY_MARKERS:
            args += ["--git-path", marker]
        p = subprocess.run(git_argv(git, root, *args), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=_GIT_TIMEOUT_S)
        lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
        if p.returncode != 0 or len(lines) != len(_GIT_BUSY_MARKERS):
            return None
        for (_marker, op), rel in zip(_GIT_BUSY_MARKERS, lines):
            path = Path(rel) if os.path.isabs(rel) else Path(root) / rel   # relative to -C <root>
            if path.exists():
                return op
    except Exception:
        return None
    return None


# The sync CLI contract DeepMap's background refresh needs (auto-sync spec Appendix C3 + C4): `deepmap_cli.py sync` takes
# --unattended / --session and hands such a run to build/auto_sync.run. The heal hook starts the refresh as
# `deepmap_cli.py sync <root> --unattended=session-start [--session <id>]`; a CLI without the contract refuses that argv
# in argparse (exit 2) — or, with only the flags, would run a plain sync — so with such a CLI the hooks start, announce
# and follow up nothing (autosync_on False: exactly the old hooks). A TEXT proof (the hooks never import the CLI);
# tests/auto_sync_hooks_gates.py section I runs the heal hook's real argv against the real CLI and requires this proof
# to agree with what the CLI does.
_CLI_CONTRACT = (re.compile(r"""add_argument\(\s*["']--unattended["']"""),
                 re.compile(r"""add_argument\(\s*["']--session["']"""),
                 re.compile(r"""_load\(\s*["']auto_sync["']\s*\)\s*\.\s*run\("""))
_CLI_SEEN: dict = {}   # path -> ((size, mtime_ns), answer): one read per process and CLI version


def cli_takes_unattended(cli=None) -> bool:
    """Does the plugin's deepmap_cli.py (`cli`; default build/deepmap_cli.py beside these hooks) start DeepMap's
    background refresh — `sync <root> --unattended=session-start [--session <id>]` handed to auto_sync.run? True iff
    its text carries every _CLI_CONTRACT pattern. Never raises (-> False: no background refresh)."""
    try:
        path = Path(cli) if cli is not None else _BUILD / "deepmap_cli.py"
        st = path.stat()
        sig = (st.st_size, st.st_mtime_ns)
        seen = _CLI_SEEN.get(str(path))
        if seen is not None and seen[0] == sig:
            return seen[1]
        text = path.read_text(encoding="utf-8", errors="replace")
        ok = all(p.search(text) for p in _CLI_CONTRACT)
        _CLI_SEEN[str(path)] = (sig, ok)
        return ok
    except Exception:
        return False


def autosync_on(root, payload=None) -> bool:
    """Is DeepMap's background refresh on for this project and session? auto_sync.enabled(root, "session-start") —
    THE rule (explicit DEEPMAP_AUTO_SYNC > .deepmap/instance.json auto_sync.enabled > the machine switch > an
    attended session) — and False when that module can't be loaded or the plugin's CLI cannot start the refresh
    (cli_takes_unattended): the hooks then behave exactly as before. Never raises."""
    try:
        AS = _auto_sync()
        fn = getattr(AS, "enabled", None) if AS is not None else None
        if fn is None:
            return False
        got = fn(root, "session-start")
        on = bool(got[0]) if isinstance(got, (tuple, list)) else bool(got)
        # the CLI is read only when auto-sync is on (one ~200 KB read per process, cached)
        return on and cli_takes_unattended()
    except Exception:
        return False


def autosync_db_ready(root) -> bool:
    """Can the background refresh a session start runs reach this project's database? With the SessionStart heal on,
    the heal brings it up (True). With it off (DEEPMAP_HEAL=0 — the refresh still starts: auto-sync spec §2.7) nothing
    does: the runner probes this project's own bolt port once and, when it is down, ends "unreachable" at once
    (auto_sync R11) — so True only when that port accepts a connection now (bolt_target + port_listening, the runner's
    probe). Runs git (the route's project key) only with the heal off. Never raises (-> False)."""
    try:
        if not _env_off(HEAL_OPTOUT):
            return True
        t = bolt_target(root)
        return bool(t) and bool(port_listening(t[1], t[0]))
    except Exception:
        return False


# The head autosync_at_next_start hands auto_sync.precheck: no recorded session start carries it (a real one is a
# 40-hex SHA or None), so precheck's per-HEAD cooldown never applies — when the next session starts is unknown, the
# runner judges its cooldown then — while the check that only runs with a head does: a recorded skip that next start
# would end with again (auto_sync._repeating_skip: needs-timing, no-own-instance, packages-missing, model-missing,
# setup-blocked, foreign-owner, recipe:python / home / route / model / engine / engine-version, heal-off).
_NEXT_START_HEAD = "deepmap-hooks:next-session-start"


def autosync_at_next_start(root, payload=None) -> bool:
    """Will the next session start refresh this project's graph by itself? autosync_on, auto_sync.precheck(root,
    trigger="session-start", head=_NEXT_START_HEAD) passes (file reads: not paused, backing off, too slow or
    recipe-conflicted, and no recorded skip the next run would repeat — the question the SessionStart notify asks
    before it announces a refresh; the cooldown is the runner's to judge at that start) and autosync_db_ready. What
    the edit hook's "DeepMap refreshes the graph by itself … at the next session start" rests on. Never raises
    (-> False)."""
    try:
        if not autosync_on(root, payload):
            return False
        AS = _auto_sync()
        pre = getattr(AS, "precheck", None) if AS is not None else None
        pc = pre(root, trigger="session-start", head=_NEXT_START_HEAD) if pre is not None else None
        return isinstance(pc, dict) and pc.get("ok") is True and autosync_db_ready(root)
    except Exception:
        return False


def _record_live(rec):
    """Is the process a record names ({"pid", "pid_created"}) still THAT process? sync_status.holder_live's rule
    (pid + creation time; port_registry.same_start when that module can't be loaded) — and when that can't tell (a
    process that has exited has no creation time to compare), port_registry._pid_alive: a pid that is gone is False.
    True / False / None (unknown). Reads OS facts only — never a signal. Never raises."""
    try:
        if not isinstance(rec, dict):
            return None
        SS = _sync_status()
        fn = getattr(SS, "holder_live", None) if SS is not None else None
        if fn is not None:
            got = fn(rec)
            if got in (True, False):
                return got
        PR = _registry()
        pid = rec.get("pid")
        if PR is None or pid in (None, "") or isinstance(pid, bool):
            return None
        pid = int(pid)
        if not PR._pid_alive(pid):
            return False
        if fn is None and rec.get("pid_created") not in (None, ""):
            got = PR.same_start(rec.get("pid_created"), PR.pid_create_time(pid))
            return got if got in (True, False) else None
        return None
    except Exception:
        return None


def _read_running(root):
    """sync_status.read_running(root) — the holder record of this project's sync lock with "live" — or None. No
    sync_running.json (no sync holds the lock: the usual state) answers None before build/sync_status is loaded, as
    read_running itself would. Never raises."""
    try:
        if not (_deepmap_dir(root) / SYNC_RUNNING).is_file():
            return None
        SS = _sync_status()
        fn = getattr(SS, "read_running", None) if SS is not None else None
        rec = fn(root) if fn is not None else None
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _unattended_live(rec) -> bool:
    return isinstance(rec, dict) and rec.get("origin") == "unattended" and rec.get("live") is True


def autosync_running(root):
    """The LIVE background refresh holding this project's sync lock — sync_status.read_running(root) when its origin
    is "unattended" and its process is provably alive (pid + creation time) — else None. Never raises."""
    try:
        rec = _read_running(root)
        return rec if _unattended_live(rec) else None
    except Exception:
        return None


def read_autosync_state(root) -> dict:
    """<root>/.deepmap/autosync.json as a dict ({} when missing / unreadable). Never raises."""
    return _read_json_dict(_deepmap_dir(root) / AUTOSYNC_STATE) or {}


def _fresh_proof(root, data, head):
    """The background refresh's last_run when it PROVES the graph current at `head` (the full SHA): outcome "fresh",
    fresh_at_head == head, its fingerprint == the manifest's source_fingerprint, finished after the manifest was
    written — and auto-sync is on for this project (with it off the hooks keep their old texts). Else None. Never
    raises."""
    try:
        if not head:
            return None
        last = read_autosync_state(root).get("last_run")
        if not isinstance(last, dict) or last.get("outcome") != "fresh":
            return None
        at = str(last.get("fresh_at_head") or "").strip().lower()
        if len(at) != 40 or at != str(head).strip().lower():
            return None
        fp = last.get("fingerprint")
        if not isinstance(fp, str) or not fp or fp != data.get("source_fingerprint"):
            return None
        fin = _epoch(last.get("finished_epoch"))
        if fin is None or fin < (_deepmap_dir(root) / GRAPH_MARKER).stat().st_mtime:
            return None
        return last if autosync_on(root) else None
    except Exception:
        return None


def _edited_since(root, data, proof=None):
    """The path of a source edit the edit hook recorded (.deepmap/last_source_edit.json) AFTER the build saw the work
    tree — the manifest's edits_seen_through_epoch (else edits_seen_through_utc, else built_at_utc), or the start of
    a background check that proved the graph fresh — else None (none, or no reference time). Never raises."""
    try:
        rec = _read_json_dict(_deepmap_dir(root) / LAST_SOURCE_EDIT)
        ts = _epoch(rec.get("ts_epoch")) if rec else None
        if ts is None:
            return None
        seen = _epoch(data.get("edits_seen_through_epoch"))
        if seen is None:
            seen = _utc_epoch(data.get("edits_seen_through_utc"))
        if seen is None:
            seen = _utc_epoch(data.get("built_at_utc"))
        if seen is None:
            return None
        if isinstance(proof, dict):
            checked = _utc_epoch(proof.get("started_utc"))
            if checked is None:
                checked = _epoch(proof.get("finished_epoch"))
            if checked is not None:
                seen = max(seen, checked)
        if ts <= seen:
            return None
        return str(rec.get("path") or "").strip() or "a source file"
    except Exception:
        return None


def was_told(root, session_id, key) -> bool:
    """Was this session already told follow-up `key` (.deepmap/autosync_told.json)? Never raises (-> False)."""
    try:
        data = _read_json_dict(_deepmap_dir(root) / AUTOSYNC_TOLD) or {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
        keys = sessions.get(_sid(session_id))
        return isinstance(keys, list) and str(key) in keys
    except Exception:
        return False


def mark_told(root, session_id, key) -> bool:
    """Record that this session was told follow-up `key` (bounded: ~20 sessions x 50 keys; atomic; concurrent hook
    writes are whole-file last-writer-wins). True iff recorded. Never raises."""
    try:
        path = _deepmap_dir(root) / AUTOSYNC_TOLD
        data = _read_json_dict(path) or {}
        sessions = dict(data.get("sessions")) if isinstance(data.get("sessions"), dict) else {}
        sid = _sid(session_id)
        keys = [str(k) for k in (sessions.get(sid) or []) if isinstance(k, str)] \
            if isinstance(sessions.get(sid), list) else []
        if str(key) not in keys:
            keys.append(str(key))
        sessions.pop(sid, None)
        sessions[sid] = keys[-_TOLD_MAX_KEYS:]
        if len(sessions) > _MAX_SESSIONS:
            for old in list(sessions.keys())[: len(sessions) - _MAX_SESSIONS]:
                sessions.pop(old, None)
        return _write_json_atomic(path, {"sessions": sessions})
    except Exception:
        return False


def record_source_edit(root, rel: str, session_id) -> bool:
    """Record the newest source edit (.deepmap/last_source_edit.json — ONE atomic record, overwritten per edit, never
    a growing log): {"schema", "ts_utc", "ts_epoch", "path" (relative, '/'), "session_id"}. True iff written. Never
    raises."""
    try:
        now = time.time()
        rec = {"schema": 1, "ts_utc": _utc(now), "ts_epoch": now, "path": str(rel).replace("\\", "/"),
               "session_id": str(session_id) if session_id not in (None, "") else None}
        return _write_json_atomic(_deepmap_dir(root) / LAST_SOURCE_EDIT, rec)
    except Exception:
        return False


def source_exts():
    """The lowercased source extensions DeepMap's census counts (build/verdict._SOURCE_EXTS — the languages a graph
    can hold), or None when that module can't be loaded. Never raises."""
    try:
        V = _build_module("verdict")
        exts = getattr(V, "_SOURCE_EXTS", None) if V is not None else None
        return {str(e).lower() for e in exts} if exts else None
    except Exception:
        return None


def stale_kept_note(data) -> str:
    """STALE_KEPT_NOTE when the manifest records stale_kept > 0, else "". Never raises."""
    try:
        n = data.get("stale_kept") if isinstance(data, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return STALE_KEPT_NOTE.format(n=n)
    except Exception:
        pass
    return ""


def _session_final_problem(root, rec) -> bool:
    """heal_problem(root)'s refused / blocked verdict for the session `rec` (session_health), read from the heal status
    path the SessionStart notify recorded — no git (the tool-result hook runs on every deepmap tool call). A record
    without that path has no status to read (heal_problem would have found no registry either). Never raises."""
    try:
        hp = rec.get("heal_path") if isinstance(rec, dict) else None
        if not hp:
            return False
        m = re.match(r"^heal-(.+)\.json$", Path(str(hp)).name)
        if not m:
            return False
        got = _heal_problem_at(root, m.group(1), Path(str(hp)), recheck=False)
        return bool(got) and got[0] in ("refused", "blocked")
    except Exception:
        return False


def _clip(text, limit: int = 300) -> str:
    try:
        s = " ".join(str(text or "").split()).rstrip(" .")
        return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + " …"
    except Exception:
        return ""


def _build_words(data):
    """({build}, {at}) for the follow-ups: ("the build at <sha7>", " (at <sha7>)") when the manifest records the commit
    the graph was built at (built_at_sha, else built_at_base_sha), else ("a previous build", "") — a project outside
    git, whose notes then read "answer from a previous build". Never raises."""
    try:
        sha = str((data or {}).get("built_at_sha") or (data or {}).get("built_at_base_sha") or "").strip()[:7]
    except Exception:
        sha = ""
    return (FOLLOW_BUILD_AT.format(sha=sha), f" (at {sha})") if sha else (FOLLOW_BUILD_ANY, "")


def _outcome_text(last, data, *, told_auto: bool, build: str, distance: str, now: float):
    """The follow-up for a finished background run (autosync.json last_run), or None (nothing to say now). Never
    raises."""
    try:
        outcome = str(last.get("outcome") or "")
        fin = _epoch(last.get("finished_epoch"))
        ago = human_duration(max(0.0, now - fin)) if fin is not None else "a moment"
        if outcome == "synced":
            head = str(last.get("head") or last.get("built_at_sha") or "")[:7] or "the code as of its start"
            reasons = last.get("reasons") if isinstance(last.get("reasons"), list) else []
            if not reasons and isinstance(data, dict) and isinstance(data.get("reasons"), list):
                reasons = data.get("reasons")
            degraded = ""
            if str(last.get("verdict") or "").upper() == "DEGRADED":
                degraded = FOLLOW_SYNCED_DEGRADED.format(
                    reason=_clip(reasons[0], 200) if reasons else "no reason recorded")
            n = last.get("stale_kept")
            kept = FOLLOW_SYNCED_KEPT.format(n=n) if isinstance(n, int) and not isinstance(n, bool) and n > 0 else ""
            deferred = FOLLOW_SYNCED_DEFERRED if last.get("embeddings_deferred") is True else ""
            text = FOLLOW_SYNCED.format(ago=ago, head=head,
                                        dirty=FOLLOW_SYNCED_DIRTY if last.get("dirty") is True else "",
                                        degraded=degraded, kept=kept, deferred=deferred)
            return re.sub(r" {2,}", " ", text).rstrip()
        if outcome == "fresh":
            head = str(last.get("fresh_at_head") or last.get("head") or "")[:7] or "the current code"
            return FOLLOW_FRESH.format(head=head) if told_auto else None
        if outcome in ("queued", "covered", "coalesced"):
            return None   # another sync is doing it: the next record decides
        if outcome == "preempted":
            return FOLLOW_PREEMPTED if told_auto else None
        if outcome.startswith("not-pushed"):
            advice = last.get("advice") if isinstance(last.get("advice"), str) else ""
            return FOLLOW_NOT_PUSHED.format(reason=_clip(last.get("reason")) or "it could not finish",
                                            build=build, advice=advice.rstrip(" .") if advice else "")
        if outcome.startswith("failed"):
            return FOLLOW_FAILED.format(reason=_clip(last.get("reason")) or "no reason was recorded", build=build)
        if outcome.startswith("skipped") or outcome == "unreachable":
            if not told_auto or outcome in _SKIP_SILENT:
                return None
            return FOLLOW_SKIPPED.format(why=_clip(last.get("reason")) or "no reason was recorded", build=build,
                                         distance=distance, suggest=_SUGGEST.get(str(last.get("suggest")), ""))
    except Exception:
        return None
    return None


def _spawn_live(root, start: float) -> bool:
    """Did the heal hook start a background refresh for THIS session (autosync_spawn.json spawned at or after its
    start) whose process may still be running (alive, or not provably gone)? Never raises (-> False)."""
    try:
        rec = _read_json_dict(_deepmap_dir(root) / AUTOSYNC_SPAWN)
        at = _epoch(rec.get("spawned_epoch")) if rec else None
        if at is None or at < start - _OUTCOME_SLACK_S:
            return False
        return _record_live(rec) is not False
    except Exception:
        return False


def _tell(root, session_id, key, text):
    """`text`, once: recorded as told first (an unwritable store keeps the hooks quiet, never repeating). Never
    raises."""
    try:
        return text if text and mark_told(root, session_id, key) else None
    except Exception:
        return None


# ---- the quiet stamp: "nothing changed since this session last had nothing to be told" -------------------------------
def _quiet_sig(path):
    """[mtime_ns, size, file id] of `path` (a rewrite through temp + os.replace always gets a new file id), None when
    it does not exist; raises OSError when it can't be read (the caller then never trusts a stamp)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return [int(st.st_mtime_ns), int(st.st_size), int(st.st_ino)]


def _quiet_sigs(root):
    """{name: _quiet_sig} for _QUIET_FILES under <root>/.deepmap, or None when one can't be read. Taken BEFORE the
    records are read, so a record rewritten while autosync_followup reads it fails the next check. Never raises."""
    try:
        d = _deepmap_dir(root)
        return {name: _quiet_sig(d / name) for name in _QUIET_FILES}
    except Exception:
        return None


def _quiet_key(session_id, cwd) -> str:
    """The stamp's entry key: the session and the payload's cwd (a session that changes directory keeps one entry per
    directory)."""
    return f"{_sid(session_id)}\t{cwd}"


def _quiet_entry_holds(data, session_id, cwd, base, now) -> bool:
    """Does the parsed autosync_quiet.json `data` hold an entry for `session_id` and this payload `cwd` written by THIS
    plugin's code (str(_BUILD)), not past its `until`, whose files (under `base`, the .deepmap directory it lives in)
    all still carry the recorded signatures? The ONE check — deepmap_toolresult_freshness._quiet_fast is its copy for
    the moment before this module is imported (section J of tests/auto_sync_hooks_gates.py keeps them equal). Never
    raises (-> False)."""
    try:
        if not isinstance(data, dict) or data.get("schema") != QUIET_SCHEMA or not isinstance(cwd, str) or not cwd:
            return False
        entries = data.get("entries")
        ent = entries.get(_quiet_key(session_id, cwd)) if isinstance(entries, dict) else None
        if (not isinstance(ent, dict) or ent.get("session_id") != _sid(session_id) or ent.get("cwd") != cwd
                or ent.get("code") != str(_BUILD)):
            return False
        until = ent.get("until")
        if until is not None:
            u = _epoch(until)
            if u is None or (time.time() if now is None else float(now)) >= u:
                return False
        files = ent.get("files")
        if not isinstance(files, dict) or not files:
            return False
        for name, want in files.items():
            if not isinstance(name, str) or not name or name in (".", "..") or os.path.basename(name) != name:
                return False
            if _quiet_sig(os.path.join(str(base), name)) != want:
                return False
        return True
    except Exception:
        return False


def quiet_holds(root, session_id, cwd, *, now=None) -> bool:
    """True iff this session was found to have nothing to be told and no record that answer was read from has been
    rewritten since (<root>/.deepmap/autosync_quiet.json, written by autosync_followup — _quiet_entry_holds). Lets every
    later tool call skip the auto-sync modules entirely. Never raises (-> False)."""
    try:
        d = _deepmap_dir(root)
        return _quiet_entry_holds(_read_json_dict(d / AUTOSYNC_QUIET), session_id, cwd, d, now)
    except Exception:
        return False


def _write_quiet(root, session_id, cwd, sigs, until) -> bool:
    """Record that `session_id` (its payload `cwd`) has nothing to be told while `sigs` (_quiet_sigs, taken before the
    answer was read) hold, until `until` (an epoch, or None: no time limit). Only a changed entry is written (atomic;
    the _QUIET_MAX most-recent entries kept). True iff the entry is in place. Never raises."""
    try:
        if not isinstance(cwd, str) or not cwd or not isinstance(sigs, dict) or not sigs:
            return False
        path = _deepmap_dir(root) / AUTOSYNC_QUIET
        data = _read_json_dict(path) or {}
        entries = data.get("entries") if data.get("schema") == QUIET_SCHEMA else None
        entries = dict(entries) if isinstance(entries, dict) else {}
        key = _quiet_key(session_id, cwd)
        ent = {"session_id": _sid(session_id), "cwd": cwd, "code": str(_BUILD), "until": until, "files": sigs}
        if entries.get(key) == ent:
            return True
        entries.pop(key, None)
        entries[key] = ent
        if len(entries) > _QUIET_MAX:
            for old in list(entries.keys())[: len(entries) - _QUIET_MAX]:
                entries.pop(old, None)
        return _write_json_atomic(path, {"schema": QUIET_SCHEMA, "entries": entries})
    except Exception:
        return False


def autosync_followup(root, payload, data, *, now=None):
    """THE follow-up rule for DeepMap's background refresh (the pre-search nudge and the tool-result hook both ask it;
    each fact is said once per session — .deepmap/autosync_told.json). None unless: auto-sync is on for this project
    (autosync_on) and was when this session started (session_health carries `auto` — the notify writes it only then),
    neither DEEPMAP_FRESHNESS_NOTE (FRESHNESS_NOTE_OPTOUT: it silences the nudge's follow-ups as well as the tool-result
    hook's) nor the master DEEPMAP_HOOKS is off, the SessionStart notify told this session the graph is usable
    (session_health `told` ready / coverage / stale), and the last heal did not refuse this project's database or
    report setup's MCP blocker (read from the status path notify recorded — no git). In order, the first fact not told
    yet:
      1. running:<id>      a live background refresh holds the sync lock (sync_status.read_running) -> FOLLOW_RUNNING;
      2. outcome:<id>      the runner's last_run finished since the session started -> its outcome (synced / not
                           updated / failed always; fresh, preempted and skipped only when notify said a background
                           refresh was coming; queued / covered say nothing yet);
      3. not-started:<sid> notify said a refresh was coming, 120 s have passed, and no run is going, recorded or was
                           started for this session -> FOLLOW_NOT_STARTED.
    A "nothing to say" is stamped (_write_quiet), and while the stamp holds (quiet_holds) the answer is None at once —
    no build/ module loaded: most calls of a session have nothing to say. The stamp lapses when a record it was read
    from is rewritten, when the not-started wait ends, and — when the answer rests on a live process (a running
    refresh, a runner still on its way), whose end no record may announce (a killed process) — after
    QUIET_LIVE_RECHECK_S. Reads a few small files; no git, no
    network. Never raises."""
    try:
        if not feature_enabled(FRESHNESS_NOTE_OPTOUT) or not isinstance(payload, dict):
            return None
        sid, cwd = payload.get("session_id"), payload.get("cwd")
        if quiet_holds(root, sid, cwd, now=now):
            return None   # nothing it is read from changed since this session last had nothing to be told
        sigs = _quiet_sigs(root)   # BEFORE the records are read
        text, stamp, until = _followup_decide(root, payload, data, now)
        if text is None and stamp and sigs is not None:
            _write_quiet(root, sid, cwd, sigs, until)
        return text
    except Exception:
        return None


def _followup_decide(root, payload, data, now):
    """autosync_followup's answer, as (text, stamp, until): `stamp` — a None that may be stamped (no stamp before
    auto-sync is known to be on for the session: with it off a project gains no new file), valid until `until` (an
    epoch: the not-started wait ending, or QUIET_LIVE_RECHECK_S past now when the answer rests on a live process), or
    for as long as the records hold (None). Never raises (-> (None, False, None))."""
    try:
        sid = payload.get("session_id")
        rec = read_session_health(root, sid)
        if not rec or rec.get("told") not in ("ready", "coverage", "stale"):
            return None, False, None
        start = _epoch(rec.get("start"))
        if start is None or "auto" not in rec:
            return None, False, None   # no `auto`: auto-sync was off when this session started (or an older notify)
        if not autosync_on(root, payload):
            return None, False, None
        if _session_final_problem(root, rec):
            return None, False, None
        now = time.time() if now is None else float(now)
        data = data if isinstance(data, dict) else (read_manifest(root) or {})
        told_auto = bool(rec.get("auto"))
        build, at = _build_words(data)
        distance = distance_text(rec.get("distance"))
        recheck = now + QUIET_LIVE_RECHECK_S
        held = _read_running(root)
        running = held if _unattended_live(held) else None
        # no holder, a user's sync, or a background one provably gone: no process to watch; a live (or unknown)
        # background holder is one — its end is news no record may announce (a killed process)
        watch = not (held is None or held.get("origin") != "unattended" or held.get("live") is False)
        if running is not None:
            key = f"running:{running.get('id') or running.get('started_utc') or running.get('pid')}"
            if not was_told(root, sid, key):
                started = _epoch(running.get("started_epoch"))
                ago = human_duration(max(0.0, now - started)) if started is not None else "a moment"
                phase = _phase_words(running.get("phase"))
                return _tell(root, sid, key, FOLLOW_RUNNING.format(ago=ago, phase=phase, at=at)), False, None
        state = read_autosync_state(root)
        last = state.get("last_run") if isinstance(state.get("last_run"), dict) else None
        fin = _epoch(last.get("finished_epoch")) if last else None
        if last is not None and fin is not None and fin >= start - _OUTCOME_SLACK_S:
            key = f"outcome:{last.get('id') or last.get('finished_utc') or fin}"
            if was_told(root, sid, key):
                return None, True, recheck if watch else None
            text = _outcome_text(last, data, told_auto=told_auto, build=build, distance=distance, now=now)
            return (_tell(root, sid, key, text), False, None) if text else (None, True, recheck if watch else None)
        if not told_auto or running is not None:
            return None, True, recheck if watch else None
        if now - start <= NOT_STARTED_AFTER_S:
            due = start + NOT_STARTED_AFTER_S   # "did not start" is not due yet
            return None, True, min(due, recheck) if watch else due
        key = f"not-started:{_sid(sid)}"
        if was_told(root, sid, key):
            return None, True, recheck if watch else None
        current = state.get("current") if isinstance(state.get("current"), dict) else None
        if current is not None and _record_live(current) is not False:
            return None, True, recheck   # the runner is still on its way (waiting for the database / the slot)
        if _spawn_live(root, start):
            return None, True, recheck
        return _tell(root, sid, key, FOLLOW_NOT_STARTED.format(build=build, distance=distance)), False, None
    except Exception:
        return None, False, None


def emit(event_name: str, text: str) -> None:
    """Print the Claude Code hook envelope that injects `text` as additional context WITHOUT a
    permission decision (so the tool is never blocked/denied). Exit 0 is the caller's job."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name,
        "additionalContext": text,
    }}))
