#!/usr/bin/env python3
"""Gates for P2 SessionStart heal: provision.heal() (revive Neo4j + MCP with NO re-sync) and the
marker-gated, non-blocking, always-exit-0 SessionStart hook. Hermetic: no Neo4j, no real spawn."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types
from contextlib import redirect_stderr
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(ROOT / "hooks"))
import provision as P  # noqa: E402
import deepmap_sessionstart_heal as HK  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def gate_heal() -> None:
    print("\n-- provision.heal(): ensure Neo4j + start MCP, NEVER re-sync --")
    saved = (P.ensure_neo4j, P.start_mcp_servers, P._load, P.apply_ports_for, P._failed_build_reason,
             P._ensure_mcp_json)
    saved_home = os.environ.get("DEEPMAP_HOME")
    checkout_mcp = ROOT / ".mcp.json"
    had_checkout_mcp = checkout_mcp.exists()
    calls = {"ensure": 0, "mcp": 0, "sync": 0}
    mcp_json_targets: list = []
    try:
        with tempfile.TemporaryDirectory(prefix="dm_ssheal_") as td:
            # Hermetic: no real port/Neo4j routing (it read this machine's registry and whatever holds 7687 —
            # an unregistered repo is a LEGACY route whose owner heal now verifies against a live graph), no
            # read of this checkout's .deepmap manifest, a temp DEEPMAP_HOME (registry, run/ records) and a temp
            # project dir as heal's target. Routing is gated in the provision_* suites. _ensure_mcp_json is a
            # recorder: the real one CREATES <toplevel>/.mcp.json when it is missing, and with heal's default
            # target (the cwd) it wrote 127.0.0.1:8787/8788 wiring into a checkout that ships none.
            os.environ["DEEPMAP_HOME"] = str(Path(td) / "home")
            target = Path(td) / "project"
            target.mkdir()
            P.apply_ports_for = lambda *a, **k: None
            P._failed_build_reason = lambda target: None
            P._ensure_mcp_json = lambda t: mcp_json_targets.append(str(t))
            P.ensure_neo4j = lambda **k: (calls.__setitem__("ensure", calls["ensure"] + 1) or True)
            P.start_mcp_servers = lambda **k: (calls.__setitem__("mcp", calls["mcp"] + 1) or True)

            def spy_load(name):
                if name == "sync":
                    calls["sync"] += 1
                return types.SimpleNamespace(sync=lambda *a, **k: True)
            P._load = spy_load

            with redirect_stderr(io.StringIO()):
                r = P.heal(str(target), password="x")
            check("heal True when Neo4j + MCP both up", r is True, str(r))
            check("heal calls ensure_neo4j once", calls["ensure"] == 1)
            check("heal starts MCP servers once", calls["mcp"] == 1)
            check("heal does NOT re-sync (never _load('sync'))", calls["sync"] == 0)
            check("heal with MCP up creates a missing .mcp.json only through _ensure_mcp_json, once, for its target",
                  mcp_json_targets == [str(target)], str(mcp_json_targets))

            mcp_json_targets.clear()
            P.ensure_neo4j = lambda **k: False
            with redirect_stderr(io.StringIO()):
                check("heal False when Neo4j down", P.heal(str(target), password="x") is False)
            P.ensure_neo4j = lambda **k: True
            P.start_mcp_servers = lambda **k: False
            with redirect_stderr(io.StringIO()):
                check("heal False when an MCP server fails", P.heal(str(target), password="x") is False)
            check("heal never touches .mcp.json when Neo4j or an MCP server is down", mcp_json_targets == [],
                  str(mcp_json_targets))
    finally:
        (P.ensure_neo4j, P.start_mcp_servers, P._load, P.apply_ports_for, P._failed_build_reason,
         P._ensure_mcp_json) = saved
        if saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved_home
    check("the gate wrote no .mcp.json into the checkout (a public clone ships none)",
          checkout_mcp.exists() == had_checkout_mcp,
          f"{checkout_mcp} existed before: {had_checkout_mcp}, now: {checkout_mcp.exists()}")


def gate_hook() -> None:
    print("\n-- SessionStart hook: marker-gated, non-blocking, always exit 0 --")
    saved_popen, saved_stdin = HK.subprocess.Popen, HK.sys.stdin
    spawned: list = []

    class FakePopen:
        def __init__(self, argv, **k):
            spawned.append((argv, k))
    try:
        HK.subprocess.Popen = FakePopen

        with tempfile.TemporaryDirectory() as td:   # (1) no marker -> no-op
            HK.sys.stdin = io.StringIO(json.dumps({"cwd": td}))
            rc = HK.main()
            check("no marker -> exit 0", rc == 0)
            check("no marker -> no spawn (near-zero cost for non-DeepMap projects)", spawned == [])

        spawned.clear()
        with tempfile.TemporaryDirectory() as td:   # (2) marker present -> spawn detached
            dm = Path(td) / ".deepmap"
            dm.mkdir()
            (dm / "graph_build.json").write_text("{}", encoding="utf-8")
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            HK.sys.stdin = io.StringIO(json.dumps({"cwd": td}))
            rc = HK.main()
            check("marker present -> exit 0", rc == 0)
            check("marker present -> spawned exactly one process", len(spawned) == 1, str(spawned))
            argv = spawned[0][0] if spawned else []
            check("spawns `<python> provision.py heal`",
                  len(argv) >= 3 and str(argv[1]).endswith("provision.py") and argv[2] == "heal",
                  str(argv))
            check("spawn is detached (no session block)",
                  ("creationflags" in spawned[0][1]) or ("start_new_session" in spawned[0][1]),
                  str(spawned[0][1]))
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)

        spawned.clear()   # (3) a spawn error must never propagate
        def boom(*a, **k):
            raise RuntimeError("spawn failed")
        HK.subprocess.Popen = boom
        with tempfile.TemporaryDirectory() as td:
            dm = Path(td) / ".deepmap"
            dm.mkdir()
            (dm / "graph_build.json").write_text("{}", encoding="utf-8")
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            HK.sys.stdin = io.StringIO(json.dumps({"cwd": td}))
            check("Popen raising -> still exit 0 (never disrupts a session)", HK.main() == 0)
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
    finally:
        HK.subprocess.Popen, HK.sys.stdin = saved_popen, saved_stdin


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — SessionStart heal gates (P2, no Neo4j/spawn)")
    print("=" * 64)
    gate_heal()
    gate_hook()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
