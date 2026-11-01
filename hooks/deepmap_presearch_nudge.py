#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap pre-search nudge hook (Track D) — a PreToolUse reminder that the code graph exists.

Skill activation is non-deterministic: Claude may reach for a raw text search (Grep/Glob) when the
DeepMap graph would answer better. This hook fires deterministically BEFORE such a search and injects
a SHORT reminder — deepmap_search for intent/behaviour ("where is X handled", "what computes Y"),
deepmap_impact for who-calls / blast-radius — while making clear that grep/glob is still right for a
known exact string or filename.

Discipline (mirrors hooks/deepmap_access_hook.py — a hook must never harm the session):
  * MARKER-GATED — does NOTHING unless the workspace has a built graph (.deepmap/graph_build.json),
    so a non-DeepMap project pays ~one stat() and stays silent. Never nudge toward a tool that isn't there.
  * HEALTH-GATED, like the SessionStart notify hook — only a READY verdict gets the nudge; DEGRADED with
    a persisted LOW-COVERAGE reason gets a coverage-aware variant (the uncovered layers stay Grep's);
    FAILED / other DEGRADED / legacy / unreadable manifests stay silent, and so does a project whose
    LAST heal recorded its database or MCP servers down (<DEEPMAP_HOME>/run/heal-<key>.json) — unless a
    live probe shows they are back (deepmap_hook_state.heal_problem re-checks this project's MCP ports and
    its own bolt port, so one earlier transient failure can't silence a healthy session). A heal POLICY
    refusal (its database holds ANOTHER project's graph), setup's MCP BLOCKER (a DeepMap server setup could
    not restart holds this project's port — ROUND-7 I4) and a .mcp.json whose ports the registry gives to
    ANOTHER project are never probe-re-checked: silent until setup.
  * FRESHNESS — the nudge carries the same stale / uncommitted-build note as the SessionStart announce when
    HEAD moved since the graph was built (S-4), so an agent that skipped the announce still hears it.
  * SAME-SESSION HEAL OUTCOME (ROUND-7 T9#1) — the SessionStart notify runs while this session's heal is
    still running and records what it told the session (<root>/.deepmap/session_health.json). When that
    heal's outcome lands later and CONTRADICTS it — a failure after the READY announce ("DeepMap could not
    start ... /deep-map:setup"), a recovery after "not reachable", servers started after a reconnect note —
    the next Grep/Glob says so, ONCE (a stat of the heal status file per search, no git, only while the
    session's record is unresolved and within FOLLOWUP_WINDOW_S of session start). ROUND-9 K3: also when the
    servers were UP at session start and this session's heal then RESTARTED them (mcp_started with an epoch after
    the session began — e.g. the first session after a plugin update, whose old servers the notify could not
    identify): the client's MCP session to the old processes is gone, so it says to reconnect via /mcp — unless
    the notify already said so (`restart_told`).
  * AUTO-SYNC — with DeepMap's background refresh on for this project, a search also carries its follow-up (it is
    running; how it ended) — deepmap_hook_state.autosync_followup, each fact once per session, shared with the
    tool-result hook, and silenced with it by DEEPMAP_FRESHNESS_NOTE=0 — and, while a refresh runs (or one announced
    to this session has not ended), the reminder's freshness note becomes AUTO_RUNNING_NOTE. One text per call: the
    heal follow-up first, then the auto-sync follow-up, then the reminder (which the auto-sync follow-up does not use
    up). STALE_KEPT_NOTE rides on the reminder whenever the manifest records stale_kept > 0, and so does
    deepmap_hook_state.parser_missing_note (also on the heal-recovered follow-up) whenever the manifest's
    coverage.parser_missing names files the graph lacks because the engine's optional parser is not installed.
  * ROOT-AWARE — a Grep from a subdirectory uses the manifest of the root the project was synced at
    (deepmap_hook_state.project_root: the outermost one up to the git work tree's top).
  * THROTTLED — the reminder fires AT MOST ONCE PER SESSION (keyed on the payload's session_id), so it
    can't nag. The heal status is read only for that first search (it costs one short git call).
  * OPT-OUT — DEEPMAP_NUDGE=0 (or the master DEEPMAP_HOOKS=0) silences it.
  * ADDS CONTEXT, NEVER BLOCKS — emits a PreToolUse `additionalContext` envelope with NO permission
    decision, so the search proceeds normally. On ANY error it prints nothing and exits 0.

Registered via hooks/hooks.json (PreToolUse, matcher "Grep|Glob"). Composes with the opt-in RM-1
access hook: Claude Code runs all matching hooks and merges their additionalContext.
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
import sys
import time
from pathlib import Path

# Import the shared hook helpers (sibling module in hooks/).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import deepmap_hook_state as _hs  # type: ignore
except Exception:  # pragma: no cover - if the helper can't import, degrade to a clean no-op
    _hs = None

STATE_FILENAME = "presearch_nudge_state.json"
ENV_OPTOUT = "DEEPMAP_NUDGE"
NUDGE_TEXT = (
    "DeepMap is set up for this project. For intent/behaviour questions "
    "(\"where is X handled\", \"what computes Y\") deepmap_search searches the code graph by meaning; "
    "for who-calls / blast-radius use deepmap_impact. Grep/Glob stays right for a known exact string "
    "or filename."
)
# DEGRADED with a coverage gap: still useful for the indexed part, but never a steer for the gap.
COVERAGE_NUDGE_TEXT = (
    "DeepMap is set up for this project, but its graph does NOT cover: {gaps}. For code in those "
    "areas keep using Grep/Glob/Read — an empty deepmap_search/deepmap_impact result there means not "
    "indexed, not absent. Elsewhere, deepmap_search answers intent questions and deepmap_impact "
    "who-calls / blast-radius."
)
# S-4: the freshness notes (the SessionStart announce carries the same facts).
STALE_NOTE = (
    " NOTE: the graph was built at {built}, but HEAD is now {head}{distance} — it may be stale; suggest "
    "/deep-map:refresh before relying on impact/blast-radius answers."
)
DIRTY_NOTE = (
    " NOTE: the graph was built from uncommitted changes on top of {head}; files edited after that sync may "
    "not be reflected — suggest /deep-map:refresh before relying on impact/blast-radius answers for them."
)
# Auto-sync: replaces STALE_NOTE / DIRTY_NOTE while DeepMap's background refresh is running, or was announced to this
# session and has not finished yet (deepmap_hook_state.autosync_followup then says how it ended).
AUTO_RUNNING_NOTE = (
    " NOTE: DeepMap is refreshing this graph in the background right now; until a later DeepMap note says it finished, "
    "results come from the previous build (and may briefly mix old and new code while it writes) — confirm recently "
    "changed code by reading it."
)

# ROUND-7 T9#1 — this session's heal outcome arrived AFTER the SessionStart notify and contradicts it.
FOLLOW_FAIL_TEXT = (
    "DeepMap could not start for this project: its start-up check (the SessionStart heal) finished after "
    "this session began and FAILED — {reason}. deepmap_search/deepmap_impact are unavailable, so an error or "
    "empty answer from them means DeepMap is down, NOT that the code is absent: use Grep/Glob/Read for code "
    "investigation, and suggest the user run /deep-map:setup to repair it (the heal's own output is in "
    ".deepmap/heal.log)."
)
FOLLOW_REFUSED_TEXT = (
    "DeepMap must NOT be used for this project: its start-up check (the SessionStart heal), which finished "
    "after this session began, refused this project's database — {reason}. A deepmap_search/deepmap_impact "
    "answer could come from ANOTHER project's graph: do not use those tools for this project and never "
    "present their results as this project's code — use Grep/Glob/Read, and suggest the user run "
    "/deep-map:setup."
)
FOLLOW_BLOCKED_TEXT = (
    "DeepMap must NOT be used for this project: its MCP port is held by a DeepMap server setup could not "
    "restart — {reason}. That server may answer from ANOTHER graph: do not use deepmap_search/deepmap_impact "
    "for this project, use Grep/Glob/Read, and have the user stop that process and re-run /deep-map:setup."
)
FOLLOW_RECOVERED_TEXT = (
    "DeepMap is available again for this project: its start-up check (the SessionStart heal) has now "
    "succeeded, so deepmap_search (intent/behaviour questions) and deepmap_impact (who-calls / "
    "blast-radius) work again. If they are missing from your tools or fail with a connection error, ask "
    "the user to run /mcp and reconnect deepmap-search and deepmap-embed (or reload)."
)
FOLLOW_UP_NOW_TEXT = (
    "DeepMap's MCP servers for this project are up now — the SessionStart heal has started them. "
    "deepmap_search/deepmap_impact connect once the user runs /mcp and reconnects deepmap-search and "
    "deepmap-embed (or reloads); if they are missing from your tools or fail with a connection error, ask "
    "them to."
)
# ROUND-9 K3: the servers were UP when this session started (the client connected to them), and this session's heal
# then RESTARTED them — onto the installed DeepMap code after a plugin update (incl. the v0.27.4 upgrade, whose
# servers carry no owner record), or with a corrected config. The client's MCP session to the old processes is gone.
# Conditional wording: a client that connected after the restart needs nothing.
FOLLOW_RESTARTED_TEXT = (
    "DeepMap's MCP servers for this project were restarted after this session began (the SessionStart heal moved "
    "them onto the installed DeepMap code or a corrected configuration). If deepmap_search/deepmap_impact fail with a "
    "connection or session error, or are missing from your tools, ask the user to run /mcp and reconnect "
    "deepmap-search and deepmap-embed (or reload) — the tools then work normally."
)
_FOLLOW_DEFAULT = {"neo4j": "this project's database was not reachable",
                   "mcp": "this project's MCP search servers did not start",
                   "refused": "the Neo4j this project is registered on holds another project's graph",
                   "blocked": "this project's MCP port is still held by a DeepMap server setup could not restart"}
_TOLD_OK = ("ready", "coverage", "stale")          # notify let the session use / look at the graph
_TOLD_DOWN = ("down", "refused", "blocked")        # notify steered the session away from it
_MTIME_SLACK_S = 5.0


def _freshness_note(root, data) -> str:
    """STALE_NOTE / DIRTY_NOTE for this graph (deepmap_hook_state.staleness), else "". Never raises."""
    try:
        st = _hs.staleness(root, data)
        if not st:
            return ""
        if st["kind"] == "stale":
            return STALE_NOTE.format(built=st["built"], head=st["head"],
                                     distance=_hs.distance_text(st.get("distance")))
        return DIRTY_NOTE.format(head=st["head"])
    except Exception:
        return ""


def _auto_running(root, payload) -> bool:
    """Auto-sync: is DeepMap's background refresh running for this project (a live unattended sync holds its lock), or
    was one announced to this session (session_health "auto") with no outcome recorded since the session began?
    Never raises."""
    try:
        if _hs.autosync_running(root) is not None:
            return True
        rec = _hs.read_session_health(root, payload.get("session_id"))
        if not rec or not rec.get("auto"):
            return False
        start = _hs._epoch(rec.get("start"))
        last = _hs.read_autosync_state(root).get("last_run")
        fin = _hs._epoch(last.get("finished_epoch")) if isinstance(last, dict) else None
        return start is not None and (fin is None or fin < start - _hs._OUTCOME_SLACK_S)
    except Exception:
        return False


def _ports_up(root) -> bool:
    """This project's MCP ports are known and all listening now. Never raises."""
    try:
        ports = _hs.mcp_ports(root)
        return bool(ports) and all(_hs.port_listening(p) for p in ports)
    except Exception:
        return False


def _restarted_since(status, start) -> bool:
    """ROUND-9 K3: does heal status `status` record that the heal STARTED this project's MCP servers (mcp_ok and a
    non-empty mcp_started) strictly AFTER `start` (the session's start epoch)? write_heal_status carries an earlier
    start forward while the servers stay up, so only the start's own epoch (mcp_started_epoch) counts. Never
    raises."""
    try:
        if not isinstance(status, dict) or status.get("mcp_ok") is not True:
            return False
        started = status.get("mcp_started")
        at = _hs._epoch(status.get("mcp_started_epoch"))
        return isinstance(started, list) and bool(started) and at is not None and at > float(start)
    except Exception:
        return False


def _followup(root, payload, data):
    """ROUND-7 T9#1: the ONE follow-up for this session, or None. Reads what the SessionStart notify told
    this session (session_health.json); when this session's heal status has since been rewritten (its
    signature differs from the one notify saw, and it is not older than the session) and the outcome
    CONTRADICTS what the session was told, returns the text to say — and marks the record resolved so it is
    said once. Cheap while nothing changed: a small JSON read + one stat, no git. Never raises."""
    try:
        sid = payload.get("session_id")
        rec = _hs.read_session_health(root, sid)
        if not rec or rec.get("resolved") is True:
            return None
        start = _hs._epoch(rec.get("start"))
        path = rec.get("heal_path")
        if start is None or not path:
            return None
        if time.time() - start > _hs.FOLLOWUP_WINDOW_S:
            rec["resolved"] = True
            _hs.write_session_health(root, sid, rec)
            return None
        sig = _hs.file_sig(path)
        if sig is None or sig == rec.get("seen"):
            return None                          # this session's heal has not reported yet
        if sig[0] / 1e9 < start - _MTIME_SLACK_S:
            return None                          # a status older than this session is not its outcome
        status = _hs._read_json_dict(path)
        if status is None:
            return None                          # mid-write / unreadable: look again at the next search
        problem = _hs.heal_problem(root)
        told = rec.get("told")
        text = None
        if told in _TOLD_OK and problem is not None:
            kind, reason = problem
            reason = (reason or _FOLLOW_DEFAULT.get(kind, "no detail recorded")).rstrip(".")
            if kind == "refused":
                text = FOLLOW_REFUSED_TEXT.format(reason=reason)
            elif kind == "blocked":
                text = FOLLOW_BLOCKED_TEXT.format(reason=reason)
            else:
                text = FOLLOW_FAIL_TEXT.format(reason=reason)
        elif told in _TOLD_OK and rec.get("servers_down") is True and _ports_up(root):
            text = FOLLOW_UP_NOW_TEXT
        elif told in _TOLD_OK and rec.get("servers_down") is False and rec.get("restart_told") is not True \
                and _restarted_since(status, start):
            text = FOLLOW_RESTARTED_TEXT         # K3: the servers the session connected to were replaced
        elif told in _TOLD_DOWN and problem is None:
            text = FOLLOW_RECOVERED_TEXT
            gaps = _hs.coverage_gaps(data) if (data or {}).get("verdict") == "DEGRADED" else []
            if gaps:
                text += (f" Its graph still does NOT cover: {'; '.join(gaps)} — keep using Grep/Glob/Read "
                         "there (an empty result means not indexed, not absent).")
            text += _hs.parser_missing_note(data)   # said instead of the reminder, so it carries that sentence too
        rec["resolved"], rec["seen"] = True, sig
        _hs.write_session_health(root, sid, rec)
        return text
    except Exception:
        return None


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
        return 0  # no payload -> we can't resolve the session/root reliably; stay quiet
    try:
        root = _hs.resolve_root(payload)
        if root is None or not _hs.graph_present(root):
            return 0  # not a provisioned DeepMap project
        if not _hs.feature_enabled(ENV_OPTOUT):
            return 0  # opted out
        data = _hs.read_manifest(root) or {}
        follow = _followup(root, payload, data)
        if follow:
            # said instead of (and counting as) this session's reminder, so the next search stays quiet
            _hs.bump_session_count(root, STATE_FILENAME, payload.get("session_id"))
            _hs.emit("PreToolUse", follow)
            return 0
        # Auto-sync: DeepMap's background refresh (on for this project) — running / how it ended, each fact once per
        # session (the tool-result hook shares the record). One text per call: the heal follow-up above wins, then
        # this, then the once-per-session reminder below (which this does not use up). The rule itself asks whether
        # auto-sync is on, and answers a search that has nothing new to hear from its quiet stamp without loading the
        # auto-sync modules; autosync_on is asked below only for the once-per-session reminder.
        auto_follow = _hs.autosync_followup(root, payload, data)
        if auto_follow:
            _hs.emit("PreToolUse", auto_follow)
            return 0
        verdict = data.get("verdict")
        if verdict == "READY":
            text = NUDGE_TEXT
        elif verdict == "DEGRADED" and _hs.coverage_gaps(data):
            text = COVERAGE_NUDGE_TEXT.format(gaps="; ".join(_hs.coverage_gaps(data)))
        else:
            return 0  # FAILED / other DEGRADED / legacy / unreadable -> never steer toward it
        count = _hs.bump_session_count(root, STATE_FILENAME, payload.get("session_id"))
        if count != 1:
            return 0  # once per session
        if _hs.heal_problem(root) is not None:
            return 0  # the last heal found the database / servers down (or setup's MCP blocker stands)
        if _hs.mcp_json_foreign_owner(root):
            return 0  # the session's deepmap tools are ANOTHER project's servers (ROUND-7 I4)
        auto_on = _hs.autosync_on(root, payload)
        note = AUTO_RUNNING_NOTE if auto_on and _auto_running(root, payload) else _freshness_note(root, data)
        _hs.emit("PreToolUse", text + note + _hs.stale_kept_note(data) + _hs.parser_missing_note(data))
    except Exception:
        # A reminder hook must never surface an error to the tool flow.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
