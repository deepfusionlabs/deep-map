#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for hooks/deepmap_postedit_syncflag.py — the PostToolUse graph-
staleness reminder. After a code edit (Edit/Write/MultiEdit/NotebookEdit) it notes that the graph
reflects the LAST sync, so mid-refactor queries may be stale, and suggests /deep-map:refresh. It
NEVER blocks a tool and NEVER disrupts the session.

Locks:
  - NO-OP when DeepMap isn't set up (.deepmap/graph_build.json absent): exit 0, empty stdout, no state,
  - FIRES when the graph exists: exit 0, stdout is ONE json object with
    hookSpecificOutput.hookEventName == "PostToolUse" and a non-empty additionalContext, NO
    permissionDecision key (adds context, never blocks),
  - DEBOUNCE: at most once per session by default (a 2nd edit same session is silent),
  - THRESHOLD: DEEPMAP_SYNC_FLAG_EVERY=2 re-reminds every 2 edits (fires on the 1st and 3rd),
  - OPT-OUT: DEEPMAP_SYNC_FLAG=0 (and the master DEEPMAP_HOOKS=0) -> silent even when the graph exists,
  - malformed / empty stdin -> exit 0, silent, no crash.

Hermetic: a temp dir is the project root (passed via the payload `cwd`); the hook is invoked as a
real subprocess feeding stdin. No Neo4j, no network, no spend.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
HOOK = HOOKS / "deepmap_postedit_syncflag.py"
HELPER = HOOKS / "deepmap_hook_state.py"
PY = sys.executable

EVENT = "PostToolUse"
STATE_FILENAME = "postedit_sync_state.json"
ENV_OPTOUT = "DEEPMAP_SYNC_FLAG"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _provision(root: Path) -> None:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph_build.json").write_text(json.dumps({"nodes": 1, "calls_edges": 0}), encoding="utf-8")


def _run(root: Path, payload=None, raw=None, extra_env=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for k in ("DEEPMAP_NUDGE", "DEEPMAP_SYNC_FLAG", "DEEPMAP_HOOKS", "DEEPMAP_SYNC_FLAG_EVERY"):
        env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    stdin = raw if raw is not None else json.dumps(payload if payload is not None else {})
    return subprocess.run([PY, str(HOOK)], input=stdin, text=True,
                          capture_output=True, cwd=str(root), env=env, timeout=60)


def _emit(stdout: str):
    s = (stdout or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _fires_ok(p) -> bool:
    if p.returncode != 0:
        return False
    d = _emit(p.stdout)
    if not isinstance(d, dict):
        return False
    hso = d.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return False
    return (hso.get("hookEventName") == EVENT
            and isinstance(hso.get("additionalContext"), str)
            and hso["additionalContext"].strip() != ""
            and "permissionDecision" not in hso)


def _edit(root, sid, fp="build/x.py", extra_env=None, tool="Edit"):
    return _run(root, {"cwd": str(root), "session_id": sid, "tool_name": tool,
                       "tool_input": {"file_path": fp}}, extra_env=extra_env)


def gate_noop_when_absent() -> None:
    print("\n-- no-op when DeepMap isn't set up --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)  # NO marker
        p = _edit(root, "s1")
        check("absent: exit 0", p.returncode == 0, p.stderr[-200:])
        check("absent: silent (no reminder toward a graph that isn't there)",
              p.stdout.strip() == "", repr(p.stdout[:160]))
        check("absent: no state file written", not (root / ".deepmap" / STATE_FILENAME).exists())


def gate_fires_and_debounces() -> None:
    print("\n-- fires once per session (debounced); correct PostToolUse envelope; never blocks --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        p1 = _edit(root, "s1")
        check("present: fires with a valid PostToolUse additionalContext envelope, exit 0",
              _fires_ok(p1), f"rc={p1.returncode} out={p1.stdout[:200]!r} err={p1.stderr[-160:]!r}")
        d = _emit(p1.stdout) or {}
        ac = (d.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("reminder mentions /deep-map:refresh", "/deep-map:refresh" in ac, ac[:200])
        check("reminder signals staleness", "stale" in ac.lower(), ac[:200])
        check("envelope carries NO permissionDecision (cannot block the tool)",
              "permissionDecision" not in (d.get("hookSpecificOutput") or {}), str(d))
        stray = [x for x in root.rglob("*") if x.is_file() and ".deepmap" not in x.parts]
        check("only writes under .deepmap (no stray files)", stray == [], str(stray))
        # 2nd edit, same session -> debounced (silent).
        p2 = _edit(root, "s1", fp="build/y.py", tool="Write")
        check("2nd edit same session: exit 0", p2.returncode == 0, p2.stderr[-200:])
        check("2nd edit same session: silent (debounced once per session)",
              p2.stdout.strip() == "", repr(p2.stdout[:160]))
        # New session -> fires again.
        p3 = _edit(root, "s2", tool="MultiEdit")
        check("new session: fires again", _fires_ok(p3), f"rc={p3.returncode} out={p3.stdout[:200]!r}")


def gate_threshold_every_n() -> None:
    print("\n-- DEEPMAP_SYNC_FLAG_EVERY=2 re-reminds every 2 edits (fires on 1st and 3rd) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        env = {"DEEPMAP_SYNC_FLAG_EVERY": "2"}
        p1 = _edit(root, "s1", extra_env=env)
        p2 = _edit(root, "s1", extra_env=env)
        p3 = _edit(root, "s1", extra_env=env)
        check("every=2: fires on edit #1", _fires_ok(p1), f"out={p1.stdout[:160]!r}")
        check("every=2: silent on edit #2", p2.stdout.strip() == "", repr(p2.stdout[:160]))
        check("every=2: fires again on edit #3", _fires_ok(p3), f"out={p3.stdout[:160]!r}")


def gate_optout() -> None:
    print("\n-- opt-out: DEEPMAP_SYNC_FLAG=0 and the master DEEPMAP_HOOKS=0 silence it --")
    for env_name in (ENV_OPTOUT, "DEEPMAP_HOOKS"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _provision(root)
            p = _edit(root, "s1", extra_env={env_name: "0"})
            check(f"{env_name}=0: exit 0", p.returncode == 0, p.stderr[-200:])
            check(f"{env_name}=0: silent (opt-out honored)", p.stdout.strip() == "",
                  repr(p.stdout[:160]))


def gate_malformed_stdin() -> None:
    print("\n-- malformed / empty stdin never disrupts the tool --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        p = _run(root, raw="}{ not json")
        check("malformed stdin: exit 0", p.returncode == 0, p.stderr[-200:])
        check("malformed stdin: silent, no crash", p.stdout.strip() == "", repr(p.stdout[:160]))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        p = _run(root, raw="")
        check("empty stdin: exit 0", p.returncode == 0, p.stderr[-200:])
        check("empty stdin: silent", p.stdout.strip() == "", repr(p.stdout[:160]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — post-edit sync-flag hook gates (PostToolUse; no Neo4j, no spend)")
    print("=" * 68)
    if not HOOK.exists():
        print(f"  [FAIL] hooks/deepmap_postedit_syncflag.py missing at {HOOK}")
        print("RESULT: RED — 0/1 gates passed.")
        return 1
    if not HELPER.exists():
        print(f"  [FAIL] hooks/deepmap_hook_state.py missing at {HELPER}")
        print("RESULT: RED — 0/1 gates passed.")
        return 1

    gate_noop_when_absent()
    gate_fires_and_debounces()
    gate_threshold_every_n()
    gate_optout()
    gate_malformed_stdin()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
