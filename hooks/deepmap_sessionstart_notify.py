#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap SessionStart notify hook — the "installed but not set up" onboarding surface (WS-A1).

Field report (2026-07): a user installed the DeepMap plugin, but NOTHING ever said "it isn't
provisioned" — every marker-gated hook stays silent precisely in that state, deepmap_search never
appears as a tool (the `.mcp.json` HTTP servers aren't running), and the whole session runs on grep.
This hook closes that loop at the one surface an agent reliably consumes (SessionStart
`additionalContext`):

  * marker ABSENT + the workspace LOOKS LIKE a code project -> a ONE-TIME (ever, per project root)
    onboarding line: "DeepMap is installed but not set up — /deep-map:setup enables it".
  * marker PRESENT -> the COVERAGE-GATED availability announce (once per session): prefer
    deepmap_search/deepmap_impact + the delegation line (tell subagents the tools exist — the one
    lever that precedes the first Task spawn). Gated on the verdict sync PERSISTED into
    graph_build.json: only "READY" announces the preference — a DEGRADED or missing verdict stays
    QUIET, so the hook never steers an agent toward a degraded graph (the Rails field state: plenty
    of nodes overall, dead model layer). If HEAD moved since the graph was built (a short-timeout
    `git rev-parse` — never import build/sync from a hook, never hand-parse .git/HEAD), a staleness
    note suggesting /deep-map:refresh is appended.
  * HEALTH beats the verdict: when this project's LAST SessionStart heal recorded its database (or MCP
    servers) down — <DEEPMAP_HOME>/run/heal-<project_key>.json, newer than the manifest — the announce
    is replaced by ONE line saying DeepMap is unavailable and /deep-map:setup repairs it (a heal that
    failed used to be silent, and this hook kept announcing READY over dead tools). That file is
    usually the PREVIOUS session's (this session's heal runs concurrently), so the failure is first
    re-checked live (deepmap_hook_state.heal_problem_superseded: this project's MCP ports + its own
    bolt port listening -> the failure is over and the normal announce is shown), and when it stands
    the line says a new check is running (heal enabled) instead of sounding final. A POLICY refusal
    (the database is up but holds ANOTHER project's graph — ROUND-5 G2) is never re-checked by a probe
    (listening ports can't tell "up" from "not yours"): it gets its own line saying the tools must not be
    used for this project until /deep-map:setup re-checks the claim.
  * FRESHNESS: HEAD moved since the build (built_at_sha, else built_at_base_sha) -> a stale note carrying
    the commit distance; HEAD unchanged but the build ran on uncommitted changes (built_at_dirty / dirty)
    -> a softer note. A graph with NO usable verdict (a legacy manifest, or DEGRADED without a coverage
    reason) whose HEAD moved gets ONE stale caution of its own (S-4) — never a steer toward it.
  * The ECONNREFUSED-at-startup race: Claude Code connects to the project's MCP servers as the session
    starts, while the heal hook (same event, detached) is still reviving them — so after a reboot the
    tools come up "failed". When this project's MCP ports are not listening at session start, the
    announce tells the agent the servers are being revived and to reconnect via /mcp.
  * The SAME-SESSION heal window (ROUND-7 T9#1): with the ports down and a heal coming, notify waits a
    short, bounded time for THIS session's heal outcome (DEEPMAP_NOTIFY_HEAL_WAIT, default 3 s) and
    reports it; what it told the session goes to <root>/.deepmap/session_health.json, where the pre-search
    nudge picks up a later, contradicting outcome (once).
  * setup's MCP BLOCKER (ROUND-7 I4) and a .mcp.json whose MCP ports the registry gives to ANOTHER project
    each replace the announce with a "must NOT be used" line.
  * After a plugin UPDATE (ROUND-8 J5): this project's servers still listen but run DeepMap code the installed
    plugin supersedes — the heal restarts them on the installed code, so the announce says the tools may need a
    reconnect via /mcp meanwhile. ROUND-9 K3: that includes the real upgrade FROM v0.27.4, whose servers carry no
    owner record (judged by this project's registered port + the server process's own command line); K4: a
    window still on the PREVIOUS plugin never calls a newer version's servers "stale" (restarts only move forward).
    What the session was told is recorded (`restart_told`) so the pre-search nudge does not repeat it.
  * AUTO-SYNC: when DeepMap's background refresh WILL run for this session (the plugin's CLI takes it,
    build/auto_sync.precheck with announce=True passes — its cooldown and a recorded skip the next run would repeat
    count outside git too —, no git operation is in progress, and — with DEEPMAP_HEAL=0, when no
    heal brings the database up — this project's database answers) and the graph is stale, built dirty or edited
    since (the edit hook's .deepmap/last_source_edit.json), the freshness note says the graph is being refreshed in
    the background (no need to run /deep-map:refresh) and what the session gets meanwhile — answers from the
    previous build, briefly mixed while it writes; `session_health.auto` records it, for the follow-ups the nudge and
    the tool-result hook give (deepmap_hook_state.autosync_followup). A graph that still holds nodes the background
    refresh could not prove removed (manifest stale_kept) gets STALE_KEPT_NOTE — the verdict stays READY. Auto-sync
    off: today's texts.

Discipline (mirrors the sibling hooks — a hook must never harm a session):
  * NEVER writes under <root>/ of an UNPROVISIONED project — a non-DeepMap project's tree stays
    byte-untouched (creating <root>/.deepmap here would dirty `git status` of every repo the user opens).
    The shown-flag lives in the USER-level DeepMap home: $DEEPMAP_HOME or ~/.deepmap (state/onboarded.json),
    the same home the provisioner already uses for pidfiles/logs. A PROVISIONED project's announce state
    (announce_state.json, session_health.json) lives in its existing, gitignored .deepmap/.
  * BOUNDED looks-like-code probe — at most the first ~200 top-level entries, no rglob.
  * ONE line, ONCE EVER per project root; opt-out DEEPMAP_ONBOARD=0 (or master DEEPMAP_HOOKS=0).
  * additionalContext-only with NO permission decision; on ANY error prints nothing, exits 0.
Registered via hooks/hooks.json (SessionStart, alongside deepmap_sessionstart_heal.py).
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

ENV_OPTOUT = "DEEPMAP_ONBOARD"
ENV_ANNOUNCE_OPTOUT = "DEEPMAP_ANNOUNCE"
ANNOUNCE_STATE_FILENAME = "announce_state.json"   # under <root>/.deepmap (provisioned projects only)
# T9#1: how long notify waits for THIS session's heal outcome when the project's MCP ports are down at session
# start and a heal is coming (seconds; 0 disables; capped so the hook stays well inside its 10 s budget).
ENV_HEAL_WAIT = "DEEPMAP_NOTIFY_HEAL_WAIT"
_HEAL_WAIT_DEFAULT_S = 3.0
_HEAL_WAIT_MAX_S = 6.0
_HEAL_POLL_S = 0.1
_PROBE_LIMIT = 200   # inspect at most this many top-level entries (bounded; no recursive walk)
_CODE_EXTS = {".py", ".rb", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs", ".php",
              ".c", ".cpp", ".kt", ".swift", ".scala",
              # a pure Delphi/Pascal (or Elixir/Groovy/Dart/...) tree is a code project too — this
              # probe deciding otherwise is why such a project never even saw the onboarding line
              ".pas", ".dpr", ".dpk", ".lpr", ".ex", ".exs", ".groovy", ".dart", ".zig", ".jl"}
_CODE_MARKERS = {".git", "pyproject.toml", "package.json", "go.mod", "cargo.toml", "gemfile",
                 "pom.xml", "build.gradle", "requirements.txt", "setup.py", "composer.json",
                 "src", "app", "lib"}

ONBOARD_TEXT = (
    "The DeepMap plugin is installed but NOT set up for this project, so its code-graph tools "
    "(deepmap_search — search code by what it DOES; deepmap_impact — multi-hop 'what breaks if I "
    "change this' blast-radius) are unavailable. If code investigation comes up, suggest the user "
    "run /deep-map:setup once (builds a local Neo4j code graph; a new Claude Code session then connects "
    "the tools, after the user approves the two deepmap servers if Claude Code asks). Informational "
    "only: do not run setup unprompted. Opt out: DEEPMAP_ONBOARD=0."
)

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
# S-4 / T9#6: a graph with NO usable verdict (a legacy manifest from an older DeepMap, or DEGRADED without a
# coverage reason) never gets the READY announce — but when HEAD moved since it was built, the field state
# ("2.5 months stale, no signal") is exactly what an agent needs to hear. ONE caution, never a steer toward
# the graph: confirm by reading, refresh it.
STALE_CAUTION_TEXT = (
    "DeepMap note for this project: its code graph was built at {built}, but HEAD is now {head}{distance} — "
    "the graph may be stale, and {health}. If you use deepmap_search/deepmap_impact here, confirm what they "
    "return by reading the code (anything added or changed since that build may be missing, so an empty "
    "answer does not mean the code is absent), and suggest the user run /deep-map:refresh."
)
_HEALTH_LEGACY = "it predates DeepMap's per-sync health verdict, so its coverage is unknown"
_HEALTH_DEGRADED = "its last sync was DEGRADED ({why})"
# The last sync ran on a work tree with uncommitted changes (manifest built_at_dirty / dirty): the graph
# reflects that snapshot, so edits made after it may be missing even though HEAD has not moved.
DIRTY_SUFFIX = (
    " NOTE: the graph was built from uncommitted changes on top of {head}; files edited after that "
    "sync may not be reflected — suggest /deep-map:refresh before relying on impact/blast-radius "
    "answers for them."
)
# The last heal found this project's services down: one line that steers AWAY from the tools. The
# reason is heal's own one-liner (it names the cause: an unreachable / foreign-held database, a failed
# server start, ...); the fallback wording is used only when heal recorded none.
HEAL_DOWN_TEXT = (
    "DeepMap is set up for this project but its database/servers are NOT reachable — its last "
    "start-up check (the SessionStart heal) failed: {reason}. Until that is fixed "
    "deepmap_search/deepmap_impact results are unavailable — an error or empty answer from them means "
    "DeepMap is down, NOT that the code is absent. Use Grep/Glob/Read for code investigation, and "
    "suggest the user run /deep-map:setup to repair it (the heal's own output is in .deepmap/heal.log)."
)
# The failure above was recorded by an EARLIER heal and a live probe could not show it is fixed — but
# this session's heal is running right now, so say the verdict is provisional rather than final.
HEAL_RETRY_NOTE = (
    " (A new start-up check is running in the background now: if deepmap_search/deepmap_impact answer "
    "normally later in this session, DeepMap has recovered and you can use them.)"
)
_HEAL_DOWN_DEFAULT = {"neo4j": "this project's database was not reachable",
                      "mcp": "this project's MCP search servers did not start"}
# The last heal REFUSED this project's database on policy (ROUND-5 G2): it is up, but it holds ANOTHER
# project's graph (a base-store claim an older DeepMap wrote by mistake, or a legacy 7687 owned by another
# project). Unlike HEAL_DOWN_TEXT there is no "it may recover by itself" note: a server left running there
# would answer — about the other project — so the only fix is /deep-map:setup, which re-checks the claim.
HEAL_REFUSED_TEXT = (
    "DeepMap is set up for this project but must NOT be used for it right now — its last start-up check "
    "(the SessionStart heal) refused this project's database: {reason}. A deepmap_search/deepmap_impact "
    "answer in this session could come from ANOTHER project's graph, so do not use those tools for this "
    "project and never present their results as this project's code — use Grep/Glob/Read for code "
    "investigation, and suggest the user run /deep-map:setup (it re-checks this and gives the project its "
    "own instance)."
)
_HEAL_REFUSED_DEFAULT = "the Neo4j this project is registered on holds another project's graph"
# ROUND-7 I4: setup could not restart the DeepMap MCP server that holds this project's port (setup's durable
# blocker, or a heal status saying the same). That server may answer from ANOTHER graph (a store setup moved
# this project off), so — like a refusal — no "it may recover" note: only the user stopping that process and
# a successful /deep-map:setup clear it.
HEAL_BLOCKED_TEXT = (
    "DeepMap is set up for this project but its tools must NOT be used right now — /deep-map:setup could "
    "not restart the DeepMap MCP server that holds this project's port: {reason}. That server may answer "
    "deepmap_search/deepmap_impact from ANOTHER graph (e.g. a store this project was moved off), so do not "
    "use those tools for this project and never present their results as this project's code — use "
    "Grep/Glob/Read for code investigation, and have the user stop that process and re-run /deep-map:setup."
)
# ROUND-7 I4 (hook side of the doctor finding): this project's .mcp.json names MCP ports the DeepMap port
# registry gives to ANOTHER project (a copied checkout that inherited the original's file) — the tools this
# session connects to are that project's servers.
FOREIGN_MCP_TEXT = (
    "DeepMap: this project's .mcp.json points deepmap-search/deepmap-embed at MCP ports the DeepMap port "
    "registry gives to ANOTHER project ({other}), so the deepmap_search/deepmap_impact tools in this session "
    "answer from THAT project's graph, not this one's. They must NOT be used for this project and their "
    "results must never be presented as this project's code — use Grep/Glob/Read, and suggest the user run "
    "/deep-map:setup here (it gives this project its own servers and rewrites .mcp.json; a new Claude Code "
    "session then connects them, after the user approves the two deepmap servers if Claude Code asks)."
)
# This project's MCP servers were down as the session started (the client's connect attempt already
# happened): heal is reviving them in the background, but the client won't retry on its own. It no longer
# says "do not conclude DeepMap is broken" unconditionally: the heal may FAIL (T9#1) — then the pre-search
# nudge reports that outcome, and a reconnect that keeps failing means exactly that.
RECONNECT_NOTE = (
    " NOTE: this project's DeepMap MCP servers were not running when this session started; the "
    "SessionStart heal is starting them in the background (about a minute from a cold start). If "
    "deepmap_search/deepmap_impact are missing from your tools or fail with a connection error, ask "
    "the user to run /mcp and reconnect deepmap-search and deepmap-embed (or reload). If they still "
    "fail after that, DeepMap could not start — use Grep/Glob/Read and suggest /deep-map:setup."
)
# T9#1: this session's own heal finished within notify's short wait and STARTED the servers.
JUST_STARTED_NOTE = (
    " NOTE: this project's DeepMap MCP servers were not running when this session started; the "
    "SessionStart heal has just started them. Until the user runs /mcp and reconnects deepmap-search and "
    "deepmap-embed (or reloads), deepmap_search/deepmap_impact may be missing from your tools or fail with "
    "a connection error — ask them to."
)
NOT_RUNNING_NOTE = (
    " NOTE: this project's DeepMap MCP servers are not running and the SessionStart heal is disabled "
    "(DEEPMAP_HEAL=0), so deepmap_search/deepmap_impact are unavailable until the user runs "
    "/deep-map:setup (it starts them; /mcp or a reload then connects them)."
)
# ROUND-8 J5: this project's servers are UP but were started from a DIFFERENT DeepMap version (a plugin update):
# the SessionStart heal restarts them on the installed code, and the connection the client made to the old ones
# at session start breaks when they are replaced — the same "reconnect via /mcp" need as the ECONNREFUSED race,
# with the ports listening all along (so the down-at-start notes above never fire).
UPDATE_RESTART_NOTE = (
    " NOTE: this project's DeepMap MCP servers are still running the code of a different DeepMap version "
    "({old}) than the one installed now ({new}); the SessionStart heal restarts them on the installed version "
    "in the background. While it does, deepmap_search/deepmap_impact may fail with a connection or session "
    "error — ask the user to run /mcp and reconnect deepmap-search and deepmap-embed (or reload). If they keep "
    "failing, /deep-map:setup restarts them."
)
OLD_CODE_NOTE = (
    " NOTE: this project's DeepMap MCP servers are still running the code of a different DeepMap version "
    "({old}) than the one installed now ({new}), and the SessionStart heal is disabled (DEEPMAP_HEAL=0), so "
    "fixes in the update do not reach deepmap_search/deepmap_impact until the user runs /deep-map:setup (it "
    "restarts them; /mcp or a reload then reconnects the tools)."
)

# Auto-sync (build/auto_sync.py): when DeepMap's background refresh WILL run for this session (auto_sync.precheck with
# announce=True passes, no git operation is in progress, and the graph is stale / built dirty / edited since), the
# freshness notes say so instead of "suggest /deep-map:refresh" — and say honestly what the session gets meanwhile:
# answers from the previous build, which may briefly mix old and new code while the refresh writes. Otherwise today's
# texts.
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
AUTO_WHAT_DIRTY = "it was built from uncommitted changes on top of {head}"
AUTO_WHAT_EDITED = "{path} was edited after it was built"
AUTO_STALE_CAUTION_TEXT = (
    "DeepMap note for this project: its code graph was built at {built}, but HEAD is now {head}{distance} — the graph "
    "may be stale, and {health}. DeepMap is refreshing it in the background now (low CPU priority{took}). Until a "
    "later DeepMap note says it finished, if you use deepmap_search/deepmap_impact here, confirm what they return by "
    "reading the code (anything added or changed since that build may be missing, so an empty answer does not mean "
    "the code is absent; while the refresh writes the graph, results may briefly mix old and new code)."
)
AUTO_TOOK = "; its last sync took about {took}"

# The DEGRADED counterpart of ANNOUNCE_TEXT. Written as an INSTRUCTION to the agent rather than a
# status line, because the failure it prevents is a reasoning error, not a missing fact: without it an
# agent reads an empty deepmap_search result as "this code does not exist" and answers confidently
# from nothing. It deliberately steers AWAY from the graph for the named layers (the opposite of
# ANNOUNCE_TEXT) and names the one command that fixes it, so the user never has to know any of this.
COVERAGE_TEXT = (
    "DeepMap graph coverage WARNING for this project — part of the codebase is NOT in the graph:\n"
    "{gaps}\n"
    "For code in those areas, an EMPTY deepmap_search/deepmap_impact result means NOT INDEXED, not "
    "'does not exist' — do not conclude the code is absent, and do not cite a file:line you did not "
    "read. Use Grep/Glob/Read there instead. The rest of the graph is still trustworthy. To fix the "
    "gap, run /deep-map:refresh (or /deep-map:doctor for the full per-language coverage table)."
)


def _looks_like_code(root: Path) -> bool:
    """Bounded probe: does the workspace root look like a code project? Never raises."""
    try:
        n = 0
        for p in root.iterdir():
            if n >= _PROBE_LIMIT:
                break
            n += 1
            try:
                if p.name.lower() in _CODE_MARKERS:
                    return True
                if p.is_file() and p.suffix.lower() in _CODE_EXTS:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _state_path() -> Path:
    """User-level shown-flag store — NEVER under the project root (see module docstring).
    Mirrors the DEEPMAP_HOME -> ~/.deepmap resolution build/sync.py and provision.py already use."""
    home = os.environ.get("DEEPMAP_HOME")
    base = Path(home) if home else (Path.home() / ".deepmap")
    return base / "state" / "onboarded.json"


def _already_shown(root: Path) -> bool:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return str(root) in (data.get("roots") or {})
    except Exception:
        return False   # missing/corrupt store -> treat as not shown (worst case: one extra line)


def _mark_shown(root: Path) -> None:
    """Record the one-time flag (atomic write, bounded store). Never raises."""
    try:
        sp = _state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            roots = dict(data.get("roots") or {})
        except Exception:
            roots = {}
        roots.pop(str(root), None)
        roots[str(root)] = True
        if len(roots) > 200:   # bound the store across many projects (dicts keep insertion order)
            for old in list(roots.keys())[: len(roots) - 200]:
                roots.pop(old, None)
        tmp = sp.with_suffix(sp.suffix + ".tmp")
        tmp.write_text(json.dumps({"roots": roots}), encoding="utf-8")
        os.replace(tmp, sp)
    except Exception:
        pass


def _legacy_st(fs):
    """deepmap_hook_state.staleness's answer, from a freshness_state computed already (auto-sync on: one set of git
    calls serves both the background-refresh texts and these). Never raises."""
    try:
        if fs.get("kind") == "stale":
            return {"kind": "stale", "built": fs["built"], "head": fs["head"], "distance": fs.get("distance")}
        if fs.get("kind") == "dirty":
            return {"kind": "dirty", "head": fs["head"]}
    except Exception:
        pass
    return None


def _staleness_suffix(root, data, fs=None) -> str:
    """STALE_SUFFIX (with the commit distance when git can tell) when HEAD moved since the graph was built,
    DIRTY_SUFFIX when it has not but the build ran on uncommitted changes, else "". Reads built_at_sha,
    falling back to built_at_base_sha (a dirty-tree manifest may carry only that), and built_at_dirty /
    dirty (deepmap_hook_state.staleness; `fs` — a freshness_state computed already). No SHA or no git -> ""
    (no freshness claim either way). Never raises."""
    try:
        st = _hs.staleness(root, data) if fs is None else _legacy_st(fs)
        if not st:
            return ""
        if st["kind"] == "stale":
            return STALE_SUFFIX.format(built=st["built"], head=st["head"],
                                       distance=_hs.distance_text(st.get("distance")))
        return DIRTY_SUFFIX.format(head=st["head"])
    except Exception:
        return ""


def _stale_caution(root, data, verdict, fs=None, auto=None) -> str:
    """STALE_CAUTION_TEXT for a graph with no usable verdict — a legacy manifest (no verdict at all) or
    DEGRADED without a coverage reason — whose HEAD moved since the build; "" otherwise (FAILED, READY,
    coverage-DEGRADED and fresh graphs are handled elsewhere or need nothing). `fs`: a freshness_state computed
    already; `auto` (_auto_context): the background refresh runs for this session -> AUTO_STALE_CAUTION_TEXT, which
    says so. Never raises."""
    try:
        if not isinstance(data, dict) or verdict not in (None, "", "DEGRADED"):
            return ""
        st = _hs.staleness(root, data) if fs is None else _legacy_st(fs)
        if not st or st.get("kind") != "stale":
            return ""
        if verdict == "DEGRADED":
            reasons = [str(r) for r in (data.get("reasons") or []) if str(r).strip()]
            why = " ".join(reasons[0].split())[:160].rstrip(" .") if reasons else "no reason recorded"
            health = _HEALTH_DEGRADED.format(why=why)
        else:
            health = _HEALTH_LEGACY
        if auto is not None:
            return AUTO_STALE_CAUTION_TEXT.format(built=st["built"], head=st["head"],
                                                  distance=_hs.distance_text(st.get("distance")), health=health,
                                                  took=auto["took"])
        return STALE_CAUTION_TEXT.format(built=st["built"], head=st["head"],
                                         distance=_hs.distance_text(st.get("distance")), health=health)
    except Exception:
        return ""


def _announce_precheck(pre, root, source, head):
    """auto_sync.precheck's answer to the notify's question — will a session start refresh the graph NOW? — asked with
    announce=True (the runner's contract: the recorded skip the next run would repeat, and the cooldown, are checked
    with or without a HEAD — outside git, head None is the time-only cooldown). An auto_sync whose precheck does not
    take `announce` (an older build/) is asked the old way (with a HEAD only). Raises what precheck raises otherwise."""
    try:
        return pre(root, trigger="session-start", source=source, head=head, announce=True)
    except TypeError as exc:
        if "announce" not in str(exc):
            raise
        return pre(root, trigger="session-start", source=source, head=head)


def _auto_context(root, payload, fs):
    """{"took": <"; its last sync took about …" or "">, "kind": "stale"|"dirty"|"edited"} when DeepMap's background
    refresh WILL run for this session — auto_sync.precheck(root, trigger="session-start", source=<payload source>,
    head=<HEAD, or None outside git>, announce=True) passes (_announce_precheck: enabled, not compact, no remote
    database, not paused / backing off / too slow / recipe-conflicted, no recorded skip the next run would repeat, not
    in its cooldown), no git operation is in progress (deepmap_hook_state.git_busy), the refresh can reach the database
    (deepmap_hook_state.autosync_db_ready: with DEEPMAP_HEAL=0 only while this project's own bolt port answers), and the
    graph is stale, built dirty or edited since (`fs`, a freshness_state with edits) — else None (today's texts). Never
    raises."""
    try:
        if not isinstance(fs, dict) or fs.get("kind") not in ("stale", "dirty", "edited"):
            return None
        AS = _hs._auto_sync()
        pre = getattr(AS, "precheck", None) if AS is not None else None
        if pre is None:
            return None
        pc = _announce_precheck(pre, root, payload.get("source"), fs.get("head_full"))
        if isinstance(pc, dict) and pc.get("code") == "cooldown" and _own_runner_started(root, payload):
            # the "cooldown" is THIS session's runner starting (it records its start a moment after the heal hook
            # spawned it, while this hook runs): ask what the heal hook asked when it started it (no HEAD)
            pc = pre(root, trigger="session-start", source=payload.get("source"), head=None)
        if not isinstance(pc, dict) or pc.get("ok") is not True:
            return None
        if _hs.git_busy(root):
            return None
        if not _hs.autosync_db_ready(root):
            return None   # DEEPMAP_HEAL=0 and this project's database is down: the refresh ends "unreachable" at once
        took_s = pc.get("took_s")
        took = ""
        if isinstance(took_s, (int, float)) and not isinstance(took_s, bool) and took_s > 0:
            took = AUTO_TOOK.format(took=_hs.human_duration(took_s))
        return {"took": took, "kind": fs.get("kind")}
    except Exception:
        return None


def _own_runner_started(root, payload) -> bool:
    """Was autosync.json's last_session_start written by the background refresh the heal hook started for THIS session
    (autosync_spawn.json names this session, and that start is no older than the spawn)? The heal and notify hooks run
    side by side, so the runner can record its start before this hook asks for the cooldown — a cooldown that must
    then not count against the very run it would announce. Never raises (-> False)."""
    try:
        sid = payload.get("session_id")
        if sid in (None, ""):
            return False
        spawn = _hs._read_json_dict(Path(root) / ".deepmap" / _hs.AUTOSYNC_SPAWN) or {}
        at = _hs._epoch(spawn.get("spawned_epoch"))
        if spawn.get("session_id") != str(sid) or at is None:
            return False
        ls = _hs.read_autosync_state(root).get("last_session_start")
        started = _hs._epoch(ls.get("started_epoch")) if isinstance(ls, dict) else None
        return started is not None and started >= at - 1.0
    except Exception:
        return False


def _auto_suffix(fs, auto) -> str:
    """AUTO_STALE_SUFFIX / AUTO_EDITED_SUFFIX for the READY / coverage announce. Never raises."""
    try:
        if fs.get("kind") == "stale":
            return AUTO_STALE_SUFFIX.format(built=fs["built"], head=fs["head"],
                                            distance=_hs.distance_text(fs.get("distance")), took=auto["took"])
        if fs.get("kind") == "dirty":
            what = AUTO_WHAT_DIRTY.format(head=fs["head"])
        else:
            what = AUTO_WHAT_EDITED.format(path=fs.get("path") or "a source file")
        return AUTO_EDITED_SUFFIX.format(what=what, took=auto["took"])
    except Exception:
        return ""


def _coverage_gaps(data) -> list:
    """The persisted LOW-COVERAGE gaps (see deepmap_hook_state.coverage_gaps — shared with the nudge)."""
    return _hs.coverage_gaps(data)


def _servers_down(root) -> bool:
    """True iff this project's MCP ports are KNOWN and not all listening right now. Unknown ports (no
    .mcp.json / registry pair, or a pair the registry gives to another project) -> False: never a guessed
    8787. Never raises."""
    try:
        ports = _hs.mcp_ports(root)
        return bool(ports) and not all(_hs.port_listening(p) for p in ports)
    except Exception:
        return False


def _heal_coming(data) -> bool:
    """The SessionStart heal runs for this project this session — the same predicate the heal hook applies
    (enabled, and the manifest is not proven empty). Never raises."""
    try:
        return _hs.feature_enabled(_hs.HEAL_OPTOUT) and not _hs.proven_empty(data)
    except Exception:
        return False


def _server_note_kind(root, data, *, down=None, outcome=None):
    """(note, kind) for the READY / COVERAGE messages, given whether this project's MCP ports were down at session
    start (`down`; probed now when None):
      * JUST_STARTED_NOTE — "just-started": this session's heal finished within the wait and STARTED the servers;
      * RECONNECT_NOTE    — "reconnect": a heal is coming (still running);
      * NOT_RUNNING_NOTE  — "not-running": the heal is disabled;
      * UPDATE_RESTART_NOTE / OLD_CODE_NOTE — "update-restart" / "old-code": the ports were up, but this project's
                            servers run DeepMap code the installed plugin supersedes (ROUND-8 J5 / ROUND-9 K3-K4,
                            deepmap_hook_state.mcp_code_stale — incl. a v0.27.4 server with no owner record): the heal
                            restarts them (reconnect via /mcp), or — heal disabled — /deep-map:setup does;
      * ("", None)        — the ports were up (current or newer code, or unknown), or are unknown (never a guessed
                            8787).
    Never raises."""
    try:
        if down is None:
            down = _servers_down(root)
        if not down:
            stale = _hs.mcp_code_stale(root)
            if not stale:
                return "", None
            coming = _heal_coming(data)
            note = UPDATE_RESTART_NOTE if coming else OLD_CODE_NOTE
            return (note.format(old=stale.get("old") or "an older build", new=stale.get("new") or "this plugin"),
                    "update-restart" if coming else "old-code")
        if isinstance(outcome, dict) and outcome.get("mcp_ok") is True and outcome.get("mcp_started"):
            return JUST_STARTED_NOTE, "just-started"
        return (RECONNECT_NOTE, "reconnect") if _heal_coming(data) else (NOT_RUNNING_NOTE, "not-running")
    except Exception:
        return "", None


def _server_note(root, data, *, down=None, outcome=None) -> str:
    """The MCP-connection note alone (see _server_note_kind). Never raises."""
    try:
        return _server_note_kind(root, data, down=down, outcome=outcome)[0]
    except Exception:
        return ""


def _heal_wait_budget() -> float:
    """DEEPMAP_NOTIFY_HEAL_WAIT in seconds: default 3, 0 disables, capped at 6 (the hook's own budget is
    10 s). Garbage -> the default. Never raises."""
    try:
        raw = str(os.environ.get(ENV_HEAL_WAIT, "")).strip()
        if not raw:
            return _HEAL_WAIT_DEFAULT_S
        v = float(raw)
        if v != v or v < 0:   # NaN / negative
            return _HEAL_WAIT_DEFAULT_S
        return min(v, _HEAL_WAIT_MAX_S)
    except Exception:
        return _HEAL_WAIT_DEFAULT_S


def _wait_for_heal(path, seen, budget: float):
    """Poll this project's heal status file for up to `budget` seconds for THIS session's heal outcome — a
    rewrite (its signature differs from `seen`, the one at session start) that parses as a JSON object.
    Returns the parsed status, or None when nothing arrived in time. Stops as soon as it arrives. Never
    raises."""
    try:
        if path is None or budget <= 0:
            return None
        deadline = time.monotonic() + budget
        while True:
            sig = _hs.file_sig(path)
            if sig is not None and sig != seen:
                data = _hs._read_json_dict(path)
                if isinstance(data, dict):
                    return data
            if time.monotonic() >= deadline:
                return None
            time.sleep(_HEAL_POLL_S)
    except Exception:
        return None


def _announce(root, payload) -> None:
    """The marker-PRESENT branch: a once-per-session, health- then verdict-gated message.

    this project's .mcp.json names MCP ports the registry gives to ANOTHER project -> FOREIGN_MCP_TEXT only
      (the tools this session connects to answer from that project's graph — ROUND-7 I4).
    last heal recorded the database / MCP servers down (no sync since, and a live probe can't show it
      is fixed) -> HEAL_DOWN_TEXT only (+ HEAL_RETRY_NOTE while this session's heal is retrying):
      DeepMap is unavailable, use Grep/Read, /deep-map:setup repairs it. Never an announce over dead tools.
    last heal REFUSED the database on policy (another project's graph behind this project's route — never
      re-checked by a port probe) -> HEAL_REFUSED_TEXT only: do not use the tools for this project,
      /deep-map:setup re-checks it (no "it may recover" note — a live server there answers wrongly).
    setup could not restart the DeepMap server holding this project's MCP port (its durable blocker, or a heal
      status saying the same — ROUND-7 I4) -> HEAL_BLOCKED_TEXT only: the tools must NOT be used; FINAL
      until setup succeeds (a listening port proves nothing here).
    READY   -> the availability announce (prefer deepmap_search/deepmap_impact + delegation line).
    DEGRADED with a persisted LOW-COVERAGE reason -> a COVERAGE WARNING instead. This is deliberately
      NOT a steer toward the graph — it is the opposite, and it exists because silence was the harmful
      option: the 2026-08 Delphi field report is a user whose graph held none of their language while
      an agent kept treating empty deepmap_search results as 'this code does not exist'. Telling the
      agent WHICH layer is missing, that empty != absent, and which command rebuilds it is the only
      thing that reaches a user who will never read a doc.
    a legacy manifest (no verdict) or DEGRADED without a coverage reason whose HEAD MOVED since the build ->
      ONE stale caution (S-4 / T9#6): confirm by reading, /deep-map:refresh — never a steer toward it.
    anything else (FAILED, an unknown/unhealthy graph that is fresh) -> QUIET, exactly as before.

    T9#1 — the same-session window: this hook runs WHILE this session's heal runs, so the heal status it
    reads is usually the PREVIOUS session's. When this project's MCP ports are down at session start and a
    heal is coming, it first waits a short, bounded time (DEEPMAP_NOTIFY_HEAL_WAIT, default 3 s, max 6) for
    THIS session's heal outcome and reports that instead. What it told the session is recorded in
    <root>/.deepmap/session_health.json, so the pre-search nudge can report a later outcome that
    contradicts it (a failure after READY, a recovery after 'not reachable') — once.
    The READY / COVERAGE messages gain the reconnect-via-/mcp note when this project's MCP servers are
    down at session start (see _server_note), and end with deepmap_hook_state.parser_missing_note: the files the
    graph lacks because the engine's optional parser for them is not installed (.sql / .dm; they do not count for
    coverage, so they never DEGRADE the verdict, but the agent must still hear they are not indexed). Never raises."""
    if not _hs.feature_enabled(ENV_ANNOUNCE_OPTOUT):
        return
    sid = payload.get("session_id")
    if _hs.session_count(root, ANNOUNCE_STATE_FILENAME, sid) >= 1:
        return   # this session was already served (SessionStart can fire again on resume / compact)
    t_start = time.time()
    data = _hs.read_manifest(root)
    verdict = data.get("verdict") if data else None
    heal_path = _hs.heal_status_path(root)
    seen = _hs.file_sig(heal_path) if heal_path is not None else None
    down = _servers_down(root)
    outcome = None
    if down and _heal_coming(data):
        outcome = _wait_for_heal(heal_path, seen, _heal_wait_budget())
        if outcome is not None:
            seen = _hs.file_sig(heal_path)
    fresh = outcome is not None           # this session's own heal has reported
    foreign = _hs.mcp_json_foreign_owner(root)
    problem = None if foreign else _hs.heal_problem(root)
    gaps = _coverage_gaps(data) if verdict == "DEGRADED" else []
    # Auto-sync: with DeepMap's background refresh on for this project, ONE freshness_state (with the hook-only
    # "edited" kind) serves both its texts and today's; it runs only when this session's refresh will (_auto_context).
    # Off (or its module unloadable): exactly today's calls and texts.
    auto_on = not foreign and problem is None and _hs.autosync_on(root, payload)
    fs = _hs.freshness_state(root, data, edits=True) if auto_on else None
    auto = _auto_context(root, payload, fs) if auto_on else None
    auto_told = None   # which AUTO text this session was told (session_health "auto")
    caution = "" if (foreign or problem is not None or verdict == "READY" or gaps) \
        else _stale_caution(root, data, verdict, fs=fs, auto=auto)
    if not foreign and problem is None and verdict != "READY" and not gaps and not caution:
        return   # unknown / unhealthy graph and no actionable fact -> no steer, as before
    count = _hs.bump_session_count(root, ANNOUNCE_STATE_FILENAME, sid)
    if count != 1:
        return   # once per session
    note_kind = None   # which MCP-connection note the READY / COVERAGE message carried (_server_note_kind)
    if foreign:
        told, text = "foreign", FOREIGN_MCP_TEXT.format(other=foreign)
    elif problem is not None:
        kind, reason = problem
        if kind == "refused":   # a POLICY refusal (G2): final until setup re-checks it — no recovery promise
            told = "refused"
            text = HEAL_REFUSED_TEXT.format(reason=(reason or _HEAL_REFUSED_DEFAULT).rstrip("."))
        elif kind == "blocked":   # I4: setup's blocker — final until setup succeeds
            told = "blocked"
            text = HEAL_BLOCKED_TEXT.format(reason=(reason or "no detail recorded").rstrip("."))
        else:
            told = "down"
            text = HEAL_DOWN_TEXT.format(
                reason=(reason or _HEAL_DOWN_DEFAULT.get(kind, "no detail recorded")).rstrip("."))
            if not fresh and _heal_coming(data):
                text += HEAL_RETRY_NOTE   # this session's heal is retrying right now
    elif verdict == "READY":
        told = "ready"
        note, note_kind = _server_note_kind(root, data, down=down, outcome=outcome)
        if auto is not None:
            suffix, auto_told = _auto_suffix(fs, auto), ("stale" if fs.get("kind") == "stale" else "edited")
        else:
            suffix = _staleness_suffix(root, data, fs)
        text = ANNOUNCE_TEXT + suffix + note + _hs.stale_kept_note(data) + _hs.parser_missing_note(data)
    elif gaps:
        told = "coverage"
        note, note_kind = _server_note_kind(root, data, down=down, outcome=outcome)
        if auto is not None:
            suffix, auto_told = _auto_suffix(fs, auto), ("stale" if fs.get("kind") == "stale" else "edited")
        else:
            suffix = _staleness_suffix(root, data, fs)
        text = (COVERAGE_TEXT.format(gaps="\n".join(f"  - {g}" for g in gaps)) + suffix + note
                + _hs.stale_kept_note(data) + _hs.parser_missing_note(data))
    else:
        told, text = "stale", caution
        auto_told = "caution" if auto is not None else None
    _hs.emit("SessionStart", text)
    # T9#1: what this session was told, for the pre-search nudge's one follow-up (a best-effort write under
    # <root>/.deepmap/, which exists here — the marker lives in it). ROUND-9 K3: `restart_told` — the session was
    # already told its servers are being restarted onto the installed code (reconnect via /mcp), so the nudge's
    # "restarted after this session began" follow-up would only repeat it.
    entry = {"start": t_start, "told": told, "servers_down": bool(down),
             "heal_path": str(heal_path) if heal_path is not None else None, "seen": seen,
             "resolved": fresh, "restart_told": note_kind in ("update-restart", "old-code")}
    if auto_on:
        # auto-sync: which AUTO text the session got (the follow-ups' "told auto"), and HEAD / the commit distance as
        # seen now, so a later follow-up names them without running git (deepmap_hook_state.autosync_followup)
        entry.update({"auto": auto_told, "head": fs.get("head"), "distance": fs.get("distance")})
    _hs.write_session_health(root, sid, entry)


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
        if root is None:
            return 0
        if _hs.graph_present(root):
            _announce(root, payload)   # provisioned -> the verdict-gated announce (own opt-out)
            return 0
        # Unprovisioned -> the one-time onboarding branch (its own opt-out: DEEPMAP_ONBOARD).
        if not _hs.feature_enabled(ENV_OPTOUT):
            return 0
        root = Path(root).resolve()
        if not _looks_like_code(root) or _already_shown(root):
            return 0
        _mark_shown(root)   # mark BEFORE emitting so a double-fire can never repeat the line
        _hs.emit("SessionStart", ONBOARD_TEXT)
    except Exception:
        return 0   # an onboarding hook must never surface an error into session start
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
