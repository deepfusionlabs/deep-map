#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for hooks/deepmap_presearch_nudge.py — the PreToolUse activation
nudge. Before a text search (Grep/Glob) it injects a SHORT reminder that DeepMap is available
(deepmap_search for intent/behaviour, deepmap_impact for who-calls). It NEVER blocks a tool and
NEVER disrupts the session.

Locks:
  - NO-OP when DeepMap isn't set up (.deepmap/graph_build.json absent): exit 0, empty stdout, no
    state file written (never nudge toward a tool that isn't there),
  - FIRES when the graph exists (with a READY verdict): exit 0, stdout is ONE json object with
    hookSpecificOutput.hookEventName == "PreToolUse" and a non-empty additionalContext, and NO
    permissionDecision key (it adds context, it never denies/asks -> can't block a tool),
  - THROTTLE: at most once per session (a 2nd call with the same session_id is silent; a new
    session_id fires again),
  - OPT-OUT: DEEPMAP_NUDGE=0 (and the master DEEPMAP_HOOKS=0) -> silent even when the graph exists,
  - malformed / empty stdin -> exit 0, silent, no crash,
  - the shared helper deepmap_hook_state: graph_present / feature_enabled / bump_session_count units.

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
HOOK = HOOKS / "deepmap_presearch_nudge.py"
HELPER = HOOKS / "deepmap_hook_state.py"
PY = sys.executable

EVENT = "PreToolUse"
STATE_FILENAME = "presearch_nudge_state.json"
ENV_OPTOUT = "DEEPMAP_NUDGE"

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


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _provision(root: Path) -> None:
    """Write the canonical 'a graph was built here' marker so the hook treats root as a DeepMap project.
    It carries a READY verdict (what every sync persists today): since the 2026-09 fix the nudge is
    verdict-gated like the notify hook — a FAILED / legacy / DEGRADED graph gets no steer (locked in
    tests/hook_reliability_gates.py)."""
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph_build.json").write_text(json.dumps({"nodes": 1, "calls_edges": 0, "verdict": "READY"}),
                                        encoding="utf-8")


def _run(root: Path, payload=None, raw=None, extra_env=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # Never inherit the runner's opt-out state — each case sets exactly what it needs.
    for k in ("DEEPMAP_NUDGE", "DEEPMAP_SYNC_FLAG", "DEEPMAP_HOOKS", "DEEPMAP_SYNC_FLAG_EVERY"):
        env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    stdin = raw if raw is not None else json.dumps(payload if payload is not None else {})
    return subprocess.run([PY, str(HOOK)], input=stdin, text=True,
                          capture_output=True, cwd=str(root), env=env, timeout=60)


def _emit(stdout: str):
    """Parse the hook's stdout as the CC hook JSON envelope; return the dict or None."""
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
            and "permissionDecision" not in hso)  # adds context, never denies/asks


def gate_noop_when_absent() -> None:
    print("\n-- no-op when DeepMap isn't set up (never nudge toward a tool that isn't there) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)  # NO .deepmap/graph_build.json
        p = _run(root, {"cwd": str(root), "session_id": "s1", "tool_name": "Grep",
                        "tool_input": {"pattern": "x"}})
        check("absent: exit 0", p.returncode == 0, p.stderr[-200:])
        check("absent: nothing on stdout (no nudge)", p.stdout.strip() == "", repr(p.stdout[:160]))
        check("absent: no state file written", not (root / ".deepmap" / STATE_FILENAME).exists())


def gate_fires_and_throttles() -> None:
    print("\n-- fires once per session; correct PreToolUse envelope; throttles; never blocks --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        p1 = _run(root, {"cwd": str(root), "session_id": "s1", "tool_name": "Grep",
                         "tool_input": {"pattern": "x"}})
        check("present: fires with a valid PreToolUse additionalContext envelope, exit 0",
              _fires_ok(p1), f"rc={p1.returncode} out={p1.stdout[:200]!r} err={p1.stderr[-160:]!r}")
        d = _emit(p1.stdout) or {}
        ac = (d.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("nudge mentions deepmap_search", "deepmap_search" in ac, ac[:160])
        check("nudge mentions deepmap_impact", "deepmap_impact" in ac, ac[:160])
        check("envelope carries NO permissionDecision (cannot block/deny the tool)",
              "permissionDecision" not in (d.get("hookSpecificOutput") or {}), str(d))
        check("state file written under .deepmap", (root / ".deepmap" / STATE_FILENAME).exists())
        # Only files touched live under .deepmap (no egress elsewhere).
        stray = [x for x in root.rglob("*") if x.is_file() and ".deepmap" not in x.parts]
        check("only writes under .deepmap (no stray files)", stray == [], str(stray))
        # Second call, SAME session -> throttled (silent).
        p2 = _run(root, {"cwd": str(root), "session_id": "s1", "tool_name": "Grep",
                         "tool_input": {"pattern": "y"}})
        check("2nd call same session: exit 0", p2.returncode == 0, p2.stderr[-200:])
        check("2nd call same session: silent (throttled once per session)",
              p2.stdout.strip() == "", repr(p2.stdout[:160]))
        # New session -> fires again.
        p3 = _run(root, {"cwd": str(root), "session_id": "s2", "tool_name": "Glob",
                         "tool_input": {"pattern": "**/*.py"}})
        check("new session (via Glob): fires again", _fires_ok(p3),
              f"rc={p3.returncode} out={p3.stdout[:200]!r}")


def gate_optout() -> None:
    print("\n-- opt-out: DEEPMAP_NUDGE=0 and the master DEEPMAP_HOOKS=0 silence it --")
    for env_name in (ENV_OPTOUT, "DEEPMAP_HOOKS"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _provision(root)
            p = _run(root, {"cwd": str(root), "session_id": "s1", "tool_name": "Grep",
                            "tool_input": {"pattern": "x"}}, extra_env={env_name: "0"})
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


def gate_helper_units(S) -> None:
    print("\n-- shared helper deepmap_hook_state: graph_present / feature_enabled / bump_session_count --")
    # graph_present
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("graph_present False when marker absent", S.graph_present(root) is False)
        _provision(root)
        check("graph_present True when marker present", S.graph_present(root) is True)
    # feature_enabled (default ON; off-set disables; master kill switch)
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_NUDGE", "DEEPMAP_HOOKS")}
    try:
        for k in ("DEEPMAP_NUDGE", "DEEPMAP_HOOKS"):
            os.environ.pop(k, None)
        check("feature_enabled default ON (unset)", S.feature_enabled("DEEPMAP_NUDGE") is True)
        os.environ["DEEPMAP_NUDGE"] = "0"
        check("feature_enabled OFF when value in off-set", S.feature_enabled("DEEPMAP_NUDGE") is False)
        os.environ["DEEPMAP_NUDGE"] = "1"
        check("feature_enabled ON when truthy", S.feature_enabled("DEEPMAP_NUDGE") is True)
        os.environ["DEEPMAP_HOOKS"] = "0"
        check("master DEEPMAP_HOOKS=0 disables everything", S.feature_enabled("DEEPMAP_NUDGE") is False)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    # bump_session_count: per-session increment, distinct sessions independent, never raises
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _provision(root)
        c1 = S.bump_session_count(root, STATE_FILENAME, "s1")
        c2 = S.bump_session_count(root, STATE_FILENAME, "s1")
        c3 = S.bump_session_count(root, STATE_FILENAME, "s1")
        check("bump: 1,2,3 for repeated same session", (c1, c2, c3) == (1, 2, 3), str((c1, c2, c3)))
        d1 = S.bump_session_count(root, STATE_FILENAME, "other")
        check("bump: a distinct session starts at 1", d1 == 1, str(d1))
        n1 = S.bump_session_count(root, STATE_FILENAME, None)
        n2 = S.bump_session_count(root, STATE_FILENAME, None)
        check("bump: missing session_id buckets together (1,2)", (n1, n2) == (1, 2), str((n1, n2)))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — pre-search nudge hook gates (PreToolUse; no Neo4j, no spend)")
    print("=" * 68)
    if not HOOK.exists():
        print(f"  [FAIL] hooks/deepmap_presearch_nudge.py missing at {HOOK}")
        print("RESULT: RED — 0/1 gates passed.")
        return 1
    if not HELPER.exists():
        print(f"  [FAIL] hooks/deepmap_hook_state.py missing at {HELPER}")
        print("RESULT: RED — 0/1 gates passed.")
        return 1
    S = _load("deepmap_hook_state", HOOKS)

    gate_noop_when_absent()
    gate_fires_and_throttles()
    gate_optout()
    gate_malformed_stdin()
    gate_helper_units(S)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
