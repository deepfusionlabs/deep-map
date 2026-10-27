#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for hooks/deepmap_access_hook.py — the RM-1 PostToolUse
access-capture hook (opt-in, zero-egress). It records which returned results the agent
subsequently accessed (Read/Edit/Write a returned file, or deepmap_impact a returned
symbol) as implicit relevance labels. It NEVER changes ranking and NEVER disrupts the
tool flow.

Locks:
  - extract_access: a pure classifier — Read/Edit/Write -> that file path; deepmap_impact
    -> that symbol; unrelated tools (Grep/Bash) -> nothing; malformed payload -> nothing
    (no raise),
  - the hook is a NO-OP when logging is disabled (opt-in), and appends exactly ONE access
    event when enabled,
  - the hook ALWAYS exits 0 and writes NOTHING to stdout (never disrupts the tool),
  - zero-egress: it only ever appends to <root>/.deepmap/retrieval_log.jsonl,
  - end-to-end: a search record + a Read-of-a-returned-file, folded, credits that id.

Hermetic: a temp dir is the project root (passed via the payload `cwd`); the hook is
invoked as a real subprocess feeding stdin. No Neo4j, no network, no spend.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
HOOKS = REPO / "hooks"
HOOK = HOOKS / "deepmap_access_hook.py"
PY = sys.executable

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


def gate_extract_access(H) -> None:
    print("\n-- extract_access: pure classifier (files from Read/Edit/Write; symbol from impact) --")
    fn = getattr(H, "extract_access", None)
    check("extract_access exists", callable(fn))
    if not callable(fn):
        return
    r = fn({"tool_name": "Read", "tool_input": {"file_path": "build/auth.py"}})
    check("Read -> that file path", r.get("paths") == ["build/auth.py"] and r.get("symbols") == [], str(r))
    r = fn({"tool_name": "Edit", "tool_input": {"file_path": "build/x.py"}})
    check("Edit -> that file path", r.get("paths") == ["build/x.py"], str(r))
    r = fn({"tool_name": "Write", "tool_input": {"file_path": "build/y.py"}})
    check("Write -> that file path", r.get("paths") == ["build/y.py"], str(r))
    r = fn({"tool_name": "mcp__deepmap-search__deepmap_impact", "tool_input": {"symbol": "check_auth"}})
    check("deepmap_impact -> that symbol", r.get("symbols") == ["check_auth"] and r.get("paths") == [], str(r))
    # P8: the PLUGIN-scoped tool name (installed-as-plugin) must also be credited, so the
    # `.*deepmap_impact` matcher in the docs actually captures the strong-positive traversal signal.
    r = fn({"tool_name": "mcp__plugin_deepmap_deepmap-search__deepmap_impact",
            "tool_input": {"symbol": "check_auth"}})
    check("plugin-scoped deepmap_impact -> that symbol (installed-as-plugin name is credited)",
          r.get("symbols") == ["check_auth"] and r.get("paths") == [], str(r))
    r = fn({"tool_name": "Grep", "tool_input": {"pattern": "auth"}})
    check("Grep -> nothing (not an access signal)", r == {"paths": [], "symbols": []}, str(r))
    r = fn({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    check("Bash -> nothing", r == {"paths": [], "symbols": []}, str(r))
    # Malformed payloads never raise.
    for bad in ({}, {"tool_name": "Read"}, {"tool_input": {"file_path": "z"}}, {"tool_name": "Read", "tool_input": None}):
        try:
            out = fn(bad)
            ok = out == {"paths": [], "symbols": []}
        except Exception as exc:
            ok = False
            out = f"raised {exc}"
        check(f"malformed payload -> empty, no raise ({bad})", ok, str(out))


def gate_build_event(H) -> None:
    print("\n-- build_access_event schema --")
    fn = getattr(H, "build_access_event", None)
    check("build_access_event exists", callable(fn))
    if not callable(fn):
        return
    ev = fn(["build/a.py"], ["foo"], ts="2026-07-01T00:00:00Z")
    check("type == 'access'", ev.get("type") == "access")
    check("carries paths + symbols + ts",
          ev.get("paths") == ["build/a.py"] and ev.get("symbols") == ["foo"] and ev.get("ts") == "2026-07-01T00:00:00Z",
          str(ev))


def _run_hook(payload: dict, root: Path, enabled: bool):
    env = dict(os.environ)
    env["DEEPMAP_RETRIEVAL_LOG"] = "1" if enabled else "0"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([PY, str(HOOK)], input=json.dumps(payload), text=True,
                       capture_output=True, cwd=str(root), env=env, timeout=60)
    return p


def gate_hook_subprocess(H, L) -> None:
    print("\n-- the hook end-to-end (subprocess): opt-in, exits 0, appends one event, silent --")
    # Disabled -> no-op (opt-in).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _run_hook({"tool_name": "Read", "tool_input": {"file_path": "build/a.py"}, "cwd": str(root)}, root, enabled=False)
        check("disabled: exit 0", p.returncode == 0, p.stderr[-200:])
        check("disabled: nothing on stdout", p.stdout.strip() == "", repr(p.stdout[:120]))
        check("disabled: no log written", not L.log_path(root).exists())
    # Enabled -> appends exactly one access event; exits 0; silent.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _run_hook({"tool_name": "Read", "tool_input": {"file_path": "build/auth.py"}, "cwd": str(root)}, root, enabled=True)
        check("enabled: exit 0", p.returncode == 0, p.stderr[-200:])
        check("enabled: nothing on stdout (never disrupts the tool)", p.stdout.strip() == "", repr(p.stdout[:120]))
        recs = L.read_records(root)
        access = [r for r in recs if r.get("type") == "access"]
        check("enabled: exactly one access event appended", len(access) == 1, str(recs))
        check("access event carries the read path",
              access and access[0].get("paths") == ["build/auth.py"], str(access))
        # Zero-egress: the only artifact is the log under .deepmap.
        stray = [x for x in root.rglob("*") if x.is_file() and x != L.log_path(root)]
        check("enabled: the ONLY file written is the .deepmap log (zero-egress)", stray == [], str(stray))
    # Enabled but an UNRELATED tool -> no event.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(root)}, root, enabled=True)
        check("enabled + unrelated tool: exit 0, no event",
              p.returncode == 0 and not any(r.get("type") == "access" for r in L.read_records(root)))
    # Totally malformed stdin -> exit 0, no crash, no write.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = dict(os.environ); env["DEEPMAP_RETRIEVAL_LOG"] = "1"
        p = subprocess.run([PY, str(HOOK)], input="}{ not json", text=True,
                           capture_output=True, cwd=str(root), env=env, timeout=60)
        check("malformed stdin: exit 0 (never disrupts the tool)", p.returncode == 0, p.stderr[-200:])
        check("malformed stdin: no log written", not L.log_path(root).exists())


def gate_end_to_end_fold(H, L) -> None:
    print("\n-- end-to-end: search record + Read-of-a-returned-file, folded, credits the id --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # A search record surfaced build/auth.py as id 'A'.
        rec = L.build_record("auth check", {"vector": ["A"]},
                             [{"id": "A", "file": "build/auth.py", "name": "check_auth", "line": 5}], ts="1")
        os.environ["DEEPMAP_RETRIEVAL_LOG"] = "1"
        try:
            L.append_record(root, rec)
            _run_hook({"tool_name": "Read", "tool_input": {"file_path": str(root / "build" / "auth.py")}, "cwd": str(root)}, root, enabled=True)
            folded = L.fold_access_events(L.read_records(root))
            credited = folded and "A" in folded[0]["accessed_ids"]
            check("the read of the returned file credits its id to the search", credited, str(folded))
        finally:
            os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — access-hook gates (RM-1 PostToolUse capture; no Neo4j, no spend)")
    print("=" * 68)
    if not HOOK.exists():
        print(f"  [FAIL] hooks/deepmap_access_hook.py missing at {HOOK}")
        print("RESULT: RED — 0/1 gates passed.")
        return 1
    H = _load("deepmap_access_hook", HOOKS)
    L = _load("retrieval_log", BUILD)

    gate_extract_access(H)
    gate_build_event(H)
    gate_hook_subprocess(H, L)
    gate_end_to_end_fold(H, L)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
