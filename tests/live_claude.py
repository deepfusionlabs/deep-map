#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Claude Code CLI side of the install sanity harness (tests/live_e2e.py): run the CLI, read what it says.

  run()            runs a child with its output captured as BYTES and decoded as UTF-8 (a runner's or a user's ANSI
                   code page, cp1252 / cp1255, must never decide what the harness reads), a hard timeout that ends the
                   child's whole tree, and stdin closed unless given
  child_env()      the environment every claude child (and the commands the harness runs as a user would) gets: an
                   ALLOWLIST of what a user's shell has (PATH, HOME / USERPROFILE, the Windows folders, locale, a proxy,
                   JAVA_HOME), never an API key or token, never a CI marker (CI, GITHUB_*, RUNNER_*, JAVA_HOME_<v>_X64),
                   plus CLAUDE_CONFIG_DIR and DISABLE_AUTOUPDATER=1 (the CLI version cannot move mid-run)
  p_argv()         the $0 session: `claude -p <prompt> --output-format stream-json --verbose --include-hook-events`.
                   With no login it loads the plugins, runs the SessionStart hooks, connects the project's .mcp.json
                   servers, lists everything in system/init, then ends "Not logged in" at total_cost_usd 0
  parse_stream()   a tolerant stream-json reader: events found by type / subtype, unknown fields and events ignored,
                   blank, garbage and cut lines counted (bad_lines), never raised on; a session with no result event
                   has no cost (None), so it can never read as $0
  stderr_verdict() what a hook's stderr says beside its positive signal. hooks.json runs every hook as
                   `python ... ; python3 ... ; exit 0`, so a host with only one of those names prints "command not
                   found" / "is not recognized" for the other while the hook works: that is tolerated. Fatal: both
                   names missing, a traceback, an unopenable hook file, a PowerShell parse error, any other command
                   missing. Exit code 0 is never evidence (every command ends in `exit 0`).
  find_secrets()   an sk-ant- key or the value of a secret, by kind (never echoing the value)

Standard library only: the harness runs from a bare interpreter.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from typing import Iterable, Mapping, NamedTuple

WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------------------------------------------- env
KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
ENV_ALLOW = frozenset({
    # POSIX
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TZ", "LANG", "LANGUAGE", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    # Windows
    "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "ALLUSERSPROFILE", "PUBLIC", "USERNAME",
    "USERDOMAIN", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL", "PROCESSOR_REVISION", "OS", "PSMODULEPATH", "DRIVERDATA",
    # what a user's machine may hold that changes the product's path
    "JAVA_HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "CLAUDE_CODE_GIT_BASH_PATH",
})
ENV_ALLOW_PREFIXES = ("LC_",)


def key_env_names(env: Mapping) -> list:
    """The API-key / token variables `env` holds with a non-empty value (names only, sorted)."""
    upper = {str(k).upper(): v for k, v in env.items()}
    return sorted(k for k in KEY_ENV if str(upper.get(k) or "").strip())


def child_env(base: Mapping, *, config_dir: str | None, extra: Mapping | None = None) -> dict:
    """A user-like environment for a child: the allowlist above from `base`, then `extra`, then CLAUDE_CONFIG_DIR
    (when given) and DISABLE_AUTOUPDATER=1. An API key or token never gets in, from `base` or from `extra`."""
    out = {}
    for k, v in base.items():
        ku = str(k).upper()
        if ku in ENV_ALLOW or ku.startswith(ENV_ALLOW_PREFIXES):
            out[str(k)] = str(v)
    for k, v in (extra or {}).items():
        if v is None:
            out.pop(k, None)
        else:
            out[str(k)] = str(v)
    for k in list(out):
        if k.upper() in KEY_ENV:
            del out[k]
    if config_dir:
        out["CLAUDE_CONFIG_DIR"] = str(config_dir)
    out["DISABLE_AUTOUPDATER"] = "1"
    return out


# --------------------------------------------------------------------------------------------------------------- run
class CliResult(NamedTuple):
    rc: int | None          # None: timed out or could not start
    out: str
    err: str
    secs: float
    raw: bytes              # stdout as bytes (stream-json is parsed from these)


def decode(b) -> str:
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)


def _kill_tree(p: subprocess.Popen) -> None:
    """End a child WE started and its descendants, while it is still ours (its pid cannot have been reused: we have
    not reaped it). Never a pid read from a file."""
    try:
        if p.poll() is not None:
            return
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.kill()
    except Exception:  # noqa: BLE001
        pass


def run(argv, *, cwd=None, env=None, timeout: float = 600, stdin: bytes | None = None) -> CliResult:
    """Run argv; (rc, stdout, stderr, seconds, raw stdout). Never raises."""
    t0 = time.time()
    kw: dict = {}
    if WINDOWS:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen([str(a) for a in argv], cwd=None if cwd is None else str(cwd), env=env,
                             stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    except OSError as exc:
        return CliResult(None, "", f"[could not start: {exc}]", round(time.time() - t0, 1), b"")
    try:
        out, err = p.communicate(input=stdin, timeout=timeout)
        rc = p.returncode
        note = ""
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            out, err = p.communicate(timeout=30)
        except Exception:  # noqa: BLE001
            out, err = b"", b""
        rc, note = None, f"\n[TIMEOUT after {timeout} s]"
    return CliResult(rc, decode(out), decode(err) + note, round(time.time() - t0, 1), out or b"")


def p_argv(claude: str, prompt: str = "noop", extra: Iterable = ()) -> list:
    """The $0 headless session (unauthenticated: it ends 'Not logged in' at total_cost_usd 0)."""
    return [str(claude), "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events",
            *[str(x) for x in extra]]


_VERSION = re.compile(r"^\s*(\d+\.\d+\.\d+)\b")


def version_of(text: str) -> str | None:
    """'2.1.317 (Claude Code)' -> '2.1.317'."""
    m = _VERSION.match(text or "")
    return m.group(1) if m else None


# ------------------------------------------------------------------------------------------------------- stream-json
class Session:
    """One parsed `claude -p --output-format stream-json` session."""

    def __init__(self):
        self.init: dict | None = None
        self.result: dict | None = None
        self.events: list = []
        self.hooks: list = []            # one dict per hook (start and response merged by hook_id)
        self.assistant_texts: list = []
        self.bad_lines = 0

    # ---- init
    def _i(self, key, default):
        v = (self.init or {}).get(key)
        return default if v is None else v

    def version(self):
        return self._i("claude_code_version", None)

    def plugins(self) -> list:
        return [p for p in self._i("plugins", []) if isinstance(p, dict)]

    def plugin(self, plugin_id: str) -> dict | None:
        """The init.plugins entry whose `source` is plugin_id ('deep-map@deepfusionlabs-deep-map')."""
        return next((p for p in self.plugins() if p.get("source") == plugin_id), None)

    def plugin_errors(self) -> list:
        v = self._i("plugin_errors", [])
        return list(v) if isinstance(v, (list, tuple)) else [v]

    def mcp_server_errors(self) -> list:
        v = self._i("mcp_server_errors", [])
        return list(v) if isinstance(v, (list, tuple)) else [v]

    def mcp_servers(self) -> dict:
        return {str(s.get("name")): str(s.get("status")) for s in self._i("mcp_servers", []) if isinstance(s, dict)}

    def tools(self) -> list:
        return [str(t) for t in self._i("tools", [])]

    def slash_commands(self) -> list:
        return [str(t) for t in self._i("slash_commands", [])]

    def api_key_source(self):
        return self._i("apiKeySource", None)

    # ---- result
    def cost(self):
        """total_cost_usd of the result event, or None (no result: never read as free)."""
        if not isinstance(self.result, dict):
            return None
        v = self.result.get("total_cost_usd")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    # ---- hooks
    def hook_responses(self, event: str | None = None) -> list:
        return [h for h in self.hooks if h.get("responded") and (event is None or h.get("hook_event") == event)]

    def hook_contexts(self, event: str | None = None) -> list:
        out = []
        for h in self.hook_responses(event):
            out += additional_contexts(h.get("stdout") or h.get("output") or "")
        return out


def additional_contexts(stdout: str) -> list:
    """Every hookSpecificOutput.additionalContext in a hook's stdout: one JSON object, or several one per line."""
    text = (stdout or "").strip()
    if not text:
        return []
    chunks = [text] if text.startswith("{") and text.count("\n") == 0 else text.splitlines()
    out = []
    for c in chunks:
        c = c.strip()
        if not c.startswith("{"):
            continue
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        ctx = (obj.get("hookSpecificOutput") or {}).get("additionalContext") if isinstance(obj, dict) else None
        if isinstance(ctx, str):
            out.append(ctx)
    if not out and len(chunks) > 1:        # a pretty-printed single object
        try:
            obj = json.loads(text)
            ctx = (obj.get("hookSpecificOutput") or {}).get("additionalContext")
            if isinstance(ctx, str):
                out.append(ctx)
        except (ValueError, AttributeError):
            pass
    return out


BOM = "\ufeff"


def parse_stream(data) -> Session:
    """Parse stream-json (bytes or text; BOM, CRLF and junk tolerated). Never raises."""
    s = Session()
    text = decode(data) if isinstance(data, (bytes, bytearray)) else str(data or "")
    by_id: dict = {}
    for line in text.splitlines():
        line = line.strip().lstrip(BOM)      # a BOM may start the stream, or any chunk of concatenated output
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            s.bad_lines += 1
            continue
        if not isinstance(e, dict):
            s.bad_lines += 1
            continue
        s.events.append(e)
        t, st = e.get("type"), e.get("subtype")
        if t == "system" and st == "init" and s.init is None:
            s.init = e
        elif t == "system" and st in ("hook_started", "hook_response"):
            hid = e.get("hook_id") or f"_anon{len(s.hooks)}"
            h = by_id.get(hid)
            if h is None:
                h = {"hook_id": hid}
                by_id[hid] = h
                s.hooks.append(h)
            h["hook_event"] = e.get("hook_event") or h.get("hook_event")
            h["hook_name"] = e.get("hook_name") or h.get("hook_name")
            if st == "hook_started":
                h["started"] = True
            else:
                h["responded"] = True
                for k in ("exit_code", "stdout", "stderr", "output", "outcome"):
                    h[k] = e.get(k)
        elif t == "assistant":
            for block in ((e.get("message") or {}).get("content") or []):
                if isinstance(block, dict) and block.get("type") == "text":
                    s.assistant_texts.append(str(block.get("text") or ""))
        elif t == "result":
            s.result = e
    return s


# --------------------------------------------------------------------------------------------------- hook stderr
_INTERP = ("python", "python3")
_BASH_NF = re.compile(r"(?:^|[\s:/])([\w.+-]+): command not found", re.M)
_PS_NF = re.compile(r"The term '([^']+)' is not recognized", re.M)
_PS_NF_HEAD = re.compile(r"^\s*([\w.+-]+) : The term '", re.M)
_STUB = "Python was not found; run without arguments to install from the Microsoft Store"
_FATAL = (("Traceback (most recent call last)", "a Python traceback"), ("can't open file", "a hook file not found"),
          ("is not a valid statement separator", "a PowerShell parse error"),
          ("ParserError", "a PowerShell parse error"), ("Unexpected token", "a PowerShell parse error"),
          ("No such file or directory", "a path not found"))


def _interp_name(name: str) -> str | None:
    n = os.path.basename(name).lower()
    n = n[:-4] if n.endswith(".exe") else n
    return n if n in _INTERP else None


def stderr_verdict(stderr: str) -> dict:
    """{'fatal': [reasons], 'missing': {interpreter names reported missing}, 'tolerated': [lines]} for a hook's stderr.
    A missing python OR python3 is tolerated (the other ran the hook; the caller proves that by a positive signal);
    both missing, or anything in _FATAL, or another command missing, is fatal."""
    text = stderr or ""
    fatal, tolerated, missing = [], [], set()
    for rx in (_BASH_NF, _PS_NF):
        for m in rx.finditer(text):
            n = _interp_name(m.group(1))
            if n:
                missing.add(n)
                tolerated.append(m.group(0).strip())
            else:
                fatal.append(f"command missing: {m.group(1)}")
    if _STUB in text:
        tolerated.append("the Windows python alias stub")
        missing.add("stub")
    for needle, why in _FATAL:
        if needle in text and not (needle == "No such file or directory" and "can't open file" in text):
            fatal.append(why)
    if {"python", "python3"} <= missing or ("stub" in missing and missing & {"python", "python3"}):
        fatal.append("no Python started the hook (both interpreter names failed)")
    missing.discard("stub")
    return {"fatal": sorted(set(fatal)), "missing": missing, "tolerated": tolerated}


# ------------------------------------------------------------------------------------------------------- secrets
SECRET_PREFIX = "sk-" + "ant-"
_KEY_RX = re.compile(re.escape(SECRET_PREFIX) + r"[A-Za-z0-9_-]{8,}")
_MIN_SECRET = 12


def find_secrets(text: str, secrets: Iterable = ()) -> list:
    """The kinds of secret found in `text`: 'sk-ant- key' and / or "a secret's value" (any of `secrets` at least 12
    characters long; shorter values are never secrets worth a false hit). Never returns the value."""
    hits = []
    if _KEY_RX.search(text or ""):
        hits.append("sk-ant- key")
    for v in secrets or ():
        v = str(v or "")
        if len(v) >= _MIN_SECRET and v in (text or ""):
            hits.append("a secret's value")
            break
    return hits


def redact(text: str, secrets: Iterable = ()) -> str:
    out = _KEY_RX.sub("[REDACTED sk-ant- key]", text or "")
    for v in secrets or ():
        v = str(v or "")
        if len(v) >= _MIN_SECRET:
            out = out.replace(v, "[REDACTED secret]")
    return out


if __name__ == "__main__":   # a tiny manual aid: parse a recorded session file
    if len(sys.argv) == 2:
        with open(sys.argv[1], "rb") as fh:
            ses = parse_stream(fh.read())
        print(json.dumps({"version": ses.version(), "plugins": [p.get("source") for p in ses.plugins()],
                          "mcp": ses.mcp_servers(), "cost": ses.cost(), "bad_lines": ses.bad_lines,
                          "contexts": ses.hook_contexts()}, indent=1))
