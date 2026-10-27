#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — PostToolUse access-capture hook (opt-in, local-only, zero-egress).

After `deepmap_search` returns ranked results, the agent acts on them: it Reads/Edits a
returned file, or `deepmap_impact`s a returned symbol. That access is an IMPLICIT relevance
label — the "click" of an agentic loop (design §2). This hook records those accesses to the
local retrieval log so the OFFLINE tuner can learn per-arm fusion weights from real usage.

Hard invariants (a hook must never harm the session):
  * OPT-IN. It does nothing unless retrieval logging is enabled for the project
    (env DEEPMAP_RETRIEVAL_LOG=1 or an instance.json flag). Off by default.
  * NEVER disrupts the tool flow. It ALWAYS exits 0 and writes NOTHING to stdout, no matter
    what the payload contains (malformed JSON, missing fields, an unwritable dir).
  * ZERO-EGRESS. Its only side effect is appending one access event to
    <root>/.deepmap/retrieval_log.jsonl. Nothing leaves the machine.

WIRING (opt-in — the user adds this; it is NOT auto-installed): register as a PostToolUse
hook in settings.json and enable logging:

  {"hooks": {"PostToolUse": [{"matcher": "Read|Edit|Write|.*deepmap_impact",
     "hooks": [{"type": "command",
                "command": "python -X utf8 <repo>/hooks/deepmap_access_hook.py"}]}]}}

  # `.*deepmap_impact` matches the project-scoped tool name (mcp__deepmap-search__deepmap_impact) that
  # /deep-map:setup's per-project .mcp.json gives, and also the plugin-scoped form
  # (mcp__plugin_deepmap_deepmap-search__deepmap_impact) of older installs whose plugin shipped a
  # .mcp.json. (_tool_base() below already normalizes either prefix to deepmap_impact.)

  # and turn logging on for the project:
  #   set DEEPMAP_RETRIEVAL_LOG=1   (or .deepmap/instance.json learned_fusion.logging_enabled=true)

Until wired AND enabled, this file is inert. Live ranking is untouched either way.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Import the logging primitives from build/ (sibling of hooks/).
_BUILD = Path(__file__).resolve().parent.parent / "build"
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))

try:
    import retrieval_log as _rl  # type: ignore
except Exception:  # pragma: no cover - if the import fails the hook must still be a clean no-op
    _rl = None

# The shared hook helpers (sibling module in hooks/) — only for the UTF-8 stdin read.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import deepmap_hook_state as _hs  # type: ignore
except Exception:  # pragma: no cover
    _hs = None


# Tools whose file_path is an access of a returned result.
_FILE_TOOLS = {"read", "edit", "write", "multiedit", "notebookedit"}
# Graded access kinds (design §2): a Read is a WEAK positive (the agent looked); an Edit/Write is a
# STRONG positive (the agent ACTED on it); a deepmap_impact traversal is STRONG. The grade weights
# live in retrieval_log.GRADE_WEIGHTS; here we only classify.
_READ_TOOLS = {"read"}
_EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit"}


def _tool_base(name: str) -> str:
    """Normalize a tool name: strip an mcp__server__ prefix, lowercase. 'Read' -> 'read';
    'mcp__deepmap-search__deepmap_impact' -> 'deepmap_impact'."""
    n = str(name or "")
    if "__" in n:
        n = n.rsplit("__", 1)[-1]
    return n.strip().lower()


def _action_kind(base: str) -> str:
    """The graded access kind for a normalized tool base: 'read' | 'edit' | 'impact' | ''."""
    if base in _READ_TOOLS:
        return "read"
    if base in _EDIT_TOOLS:
        return "edit"
    if base == "deepmap_impact":
        return "impact"
    return ""


def extract_access(payload) -> dict:
    """Pure classifier. Returns {"paths": [...], "symbols": [...]} of what this tool call
    accessed. Read/Edit/Write -> its file_path; deepmap_impact -> its symbol; anything else
    -> nothing. Never raises on a malformed payload."""
    paths: list[str] = []
    symbols: list[str] = []
    try:
        if not isinstance(payload, dict):
            return {"paths": paths, "symbols": symbols}
        base = _tool_base(payload.get("tool_name", ""))
        ti = payload.get("tool_input") or {}
        if not isinstance(ti, dict):
            return {"paths": paths, "symbols": symbols}
        if base in _FILE_TOOLS:
            fp = ti.get("file_path") or ti.get("notebook_path")
            if fp:
                paths.append(str(fp))
        elif base == "deepmap_impact":
            sym = ti.get("symbol")
            if sym:
                symbols.append(str(sym))
    except Exception:
        return {"paths": [], "symbols": []}
    return {"paths": paths, "symbols": symbols}


def build_access_event(paths, symbols, ts: str | None = None, *,
                       kind: str = "", session_id: str | None = None) -> dict:
    """One access event (schema v2). `kind` grades the access (read/edit/impact); `session_id`
    (from the PostToolUse payload) lets the offline fold reason about within-session order. Extra
    fields are additive — a reader that ignores them sees the same v1 access event."""
    ev = {"type": "access", "v": 2, "paths": list(paths or []), "symbols": list(symbols or []),
          "kind": kind or "", "session_id": session_id}
    ev["ts"] = ts or (_rl._now_iso() if _rl else "")
    return ev


def _project_root(start) -> Path:
    """retrieval_log.project_root(start) — the SAME root the search side logs to (retrieval_log
    .default_root), so an access from a subdirectory session lands in the project's one log, not in a
    stray <subdir>/.deepmap. Falls back to `start` itself if that helper is unavailable. Never raises."""
    try:
        fn = getattr(_rl, "project_root", None) if _rl is not None else None
        if fn is not None:
            got = fn(Path(start))
            if got is not None:
                return Path(got)
    except Exception:
        pass
    return Path(start)


def resolve_root(payload) -> Path | None:
    """The project root: retrieval_log.project_root of the payload's cwd, else DEEPMAP_PROJECT_ROOT
    (verbatim — an explicit override), else project_root of the process cwd."""
    try:
        if isinstance(payload, dict) and payload.get("cwd"):
            return _project_root(payload["cwd"])
    except Exception:
        pass
    env = os.environ.get("DEEPMAP_PROJECT_ROOT")
    if env:
        return Path(env)
    try:
        return _project_root(Path.cwd())
    except Exception:
        return None


def main() -> int:
    # Read + parse stdin defensively; ANY failure is a clean no-op (exit 0). Decoded as UTF-8, not the
    # ANSI code page: a locale-decoded non-ASCII cwd logged into a mojibake SIBLING directory.
    try:
        raw = _hs.read_stdin_text() if _hs is not None else sys.stdin.read()
    except Exception:
        return 0
    try:
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0

    if _rl is None:
        return 0
    try:
        root = resolve_root(payload)
        if root is None or not _rl.logging_enabled(root):
            return 0
        acc = extract_access(payload)
        if not acc["paths"] and not acc["symbols"]:
            return 0
        base = _tool_base(payload.get("tool_name", "")) if isinstance(payload, dict) else ""
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        _rl.append_record(root, build_access_event(
            acc["paths"], acc["symbols"], kind=_action_kind(base), session_id=session_id))
    except Exception:
        # A logging failure must never surface to the tool flow.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
