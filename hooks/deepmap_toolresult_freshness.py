#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap tool-result freshness hook (auto-sync) — a PostToolUse note after a deepmap_search / deepmap_impact /
deepmap_cypher call while DeepMap's background refresh of this project's graph is running, and once it has ended.

DeepMap refreshes the graph by itself in the background when an interactive Claude Code session starts
(build/auto_sync.py, started by the SessionStart heal hook). Until it finishes, the deepmap tools answer from the
PREVIOUS build — and while it writes, results may briefly mix old and new code. The SessionStart notify says so once;
this hook (with the pre-search nudge, through the SAME rule — deepmap_hook_state.autosync_followup) tells the session
the facts that arrive later, each ONCE per session: that the refresh is running, and how it ended (finished / did not
update the graph / failed / skipped, with the reason and what to do).

Discipline (mirrors the sibling hooks — a hook must never harm the session):
  * MARKER-GATED — nothing unless the project has a built graph (.deepmap/graph_build.json).
  * QUIET by default — it speaks only when DeepMap's background refresh is on for this project, the SessionStart
    notify told this session the graph is usable, and there is a fact the session has not been told; never under a
    heal policy refusal, setup's MCP blocker or a foreign .mcp.json.
  * CHEAP — a few small JSON reads under <root>/.deepmap and DEEPMAP_HOME: no git, no network, no port probe. It runs
    after EVERY deepmap tool call, and most of a session's calls have nothing to say: once this session was found to
    have nothing to be told, deepmap_hook_state.autosync_followup stamps the records that answer was read from
    (.deepmap/autosync_quiet.json), and while none of them is rewritten (and the stamp's time limit has not passed)
    this hook exits BEFORE it imports anything beyond the interpreter's own modules (_quiet_fast: os, time and the C
    JSON scanner — not json / re / pathlib / deepmap_hook_state / build/), so such a call costs little more than
    starting the interpreter.
  * OPT-OUT — DEEPMAP_FRESHNESS_NOTE=0 (or the master DEEPMAP_HOOKS=0; DEEPMAP_AUTO_SYNC=0 switches the refresh
    itself off, and with it every note).
  * ADDS CONTEXT, NEVER BLOCKS — a PostToolUse `additionalContext` envelope with no decision; on ANY error it prints
    nothing and exits 0.
Registered via hooks/hooks.json (PostToolUse, matcher "mcp__.*deepmap.*__deepmap_(search|impact|cypher)").
"""
# (no `from __future__ import annotations`: it imports the __future__ module before the stdin drain and _quiet_fast)
import sys

# == deepmap_hook_state.AUTOSYNC_QUIET / QUIET_SCHEMA / _OFF / _MAX_WALK (tests/auto_sync_hooks_gates.py section J)
_QUIET_FILE = "autosync_quiet.json"
_QUIET_SCHEMA = 1
_OFF_WORDS = ("0", "false", "no", "off")
_MAX_WALK = 64


def _json_reader():
    """A JSON text -> object reader built on the interpreter's C scanner (_json.make_scanner — what json.loads runs),
    without importing json (and with it re / enum / functools / collections: most of this hook's start cost on a busy
    machine). Raises when the scanner is not there (another Python): the caller then takes the normal path."""
    import _json

    class _Ctx:   # the attributes the scanner reads — json.JSONDecoder's defaults
        strict = True
        object_hook = None
        object_pairs_hook = None
        parse_float = float
        parse_int = int
        parse_constant = float   # NaN / Infinity / -Infinity, as json.loads

    scan = _json.make_scanner(_Ctx())

    def loads(text):
        text = text.strip(" \t\n\r")
        obj, end = scan(text, 0)
        if end != len(text):
            raise ValueError("extra data")
        return obj
    return loads


def _quiet_fast(raw: bytes) -> bool:
    """True iff this call provably has nothing to say, decided before anything is imported: DEEPMAP_HOOKS=0 or
    DEEPMAP_FRESHNESS_NOTE=0 (main says nothing then either), or this session's quiet stamp still holds — the copy of
    deepmap_hook_state._quiet_entry_holds for <dir>/.deepmap/autosync_quiet.json, found from the payload's cwd upward
    (the entry names that exact cwd, so it is the project root the stamp's writer resolved for it): the entry for
    this session_id and cwd, written by THIS plugin's code, not past its `until`, every recorded file signature
    unchanged. Anything else — no stamp, a doubt, an error, another Python — is False: main decides as before. Never
    raises."""
    try:
        import os
        import time
        env = os.environ
        if any(str(env.get(n, "")).strip().lower() in _OFF_WORDS for n in ("DEEPMAP_HOOKS", "DEEPMAP_FRESHNESS_NOTE")):
            return True
        loads = _json_reader()
        data = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        payload = loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            return False
        cwd, sid = payload.get("cwd"), payload.get("session_id")
        if not isinstance(cwd, str) or not cwd:
            return False
        sid = str(sid) if sid not in (None, "") else "_nosession"
        d = os.path.abspath(cwd)
        for _ in range(_MAX_WALK):
            base = os.path.join(d, ".deepmap")
            if os.path.isfile(os.path.join(base, _QUIET_FILE)):
                break
            up = os.path.dirname(d)
            if up == d:
                return False
            d = up
        else:
            return False
        with open(os.path.join(base, _QUIET_FILE), "rb") as f:
            stamp = loads(f.read().decode("utf-8"))
        if not isinstance(stamp, dict) or stamp.get("schema") != _QUIET_SCHEMA:
            return False
        entries = stamp.get("entries")
        ent = entries.get(f"{sid}\t{cwd}") if isinstance(entries, dict) else None
        code = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "build")
        if (not isinstance(ent, dict) or ent.get("session_id") != sid or ent.get("cwd") != cwd
                or ent.get("code") != code):
            return False
        until = ent.get("until")
        if until is not None:
            if isinstance(until, bool) or not isinstance(until, (int, float)) or time.time() >= float(until):
                return False
        files = ent.get("files")
        if not isinstance(files, dict) or not files:
            return False
        for name, want in files.items():
            if not isinstance(name, str) or not name or name in (".", "..") or os.path.basename(name) != name:
                return False
            try:
                st = os.stat(os.path.join(base, name))
                got = [int(st.st_mtime_ns), int(st.st_size), int(st.st_ino)]
            except FileNotFoundError:
                got = None
            if got != want:
                return False
        return True
    except Exception:
        return False


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
    if _quiet_fast(_PAYLOAD):
        raise SystemExit(0)   # nothing to say, decided before any import (the usual call)
    import io as _io
    sys.stdin = _io.TextIOWrapper(_io.BytesIO(_PAYLOAD), encoding="utf-8")

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import deepmap_hook_state as _hs  # type: ignore
except Exception:  # pragma: no cover - if the helper can't import, degrade to a clean no-op
    _hs = None

ENV_OPTOUT = "DEEPMAP_FRESHNESS_NOTE"


def main() -> int:
    # Read + parse stdin defensively (as UTF-8, whatever the locale); ANY failure is a clean no-op.
    if _hs is None:
        return 0
    raw = _hs.read_stdin_text()
    try:
        payload = json.loads(raw) if raw and raw.strip() else None
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        root = _hs.resolve_root(payload)
        if root is None or not _hs.graph_present(root):
            return 0   # not a provisioned DeepMap project
        if not _hs.feature_enabled(ENV_OPTOUT):
            return 0   # opted out
        text = _hs.autosync_followup(root, payload, None)   # the manifest is read only when there is something to say
        if text:
            _hs.emit("PostToolUse", text)
    except Exception:
        return 0   # a note hook must never surface an error to the tool flow
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
