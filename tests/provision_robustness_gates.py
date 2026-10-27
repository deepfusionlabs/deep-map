#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn gates for provisioner process-management robustness (bug-hunt #4).

Two cross-platform/reliability defects, locked behaviorally (mocks; nothing is actually spawned or
signalled):
  * _terminate_pid(tree=True) on POSIX must signal the process GROUP (os.killpg), not just the one
    pid — start_neo4j launches with start_new_session=True, so the launcher is a group leader and the
    JVM children share its group; signalling only the launcher pid would orphan them (Windows already
    honours tree via taskkill /T).
  * start_neo4j must NOTICE an instant launcher exit (bad binary / bound port) instead of silently
    leaving wait_for_bolt to block for the full timeout.

The setup() gate runs in a throwaway DEEPMAP_HOME + temp project (_SetupSandbox): setup() clears the
target's setup blocker, and with the real home and the target "." it deleted THIS repo's real
<home>/run/setup-blocked-<key>.json — the record heal and the SessionStart hooks honour (r283 F5; proven
hermetic by tests/f5_test_hygiene_r283_gates.py).
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0
_MISSING = object()


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load():
    spec = importlib.util.spec_from_file_location("dm_provision_rob", BUILD / "provision.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _SetupSandbox:
    """A throwaway machine for a gate that drives provision.setup() (r283 F5). Even with its ports, Neo4j, sync
    and MCP start stubbed, setup() clears the target's setup blocker (<DEEPMAP_HOME>/run/setup-blocked-<key>.json),
    runs git on the target and, for a local base URI, records the base store's owner (_record_base_owner: a port
    probe, a JVM listing, <home>/neo4j/neo4j-community-*/data/deepmap_base_owner.json). Here: a temp DEEPMAP_HOME,
    a temp project (`target`, one source file), the per-project env and git's repo-discovery env cleared (git never
    searches above the temp dir), the escape hatch pinned off, and the Bolt / HTTP / port / process probes answering
    "down / free / gone" — no socket, no process listing, no real Neo4j driver. Restores everything on exit."""

    _ENV = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_PROJECT_ROOT",
            "DEEPMAP_PROJECT_KEY", "DEEPMAP_NO_BASE_ADOPT", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES")

    def __init__(self, P):
        self.P = P

    def __enter__(self):
        import port_registry as PR   # the module setup() imports (provision put BUILD on sys.path)

        def no_driver(*a, **k):
            raise RuntimeError("sandbox: no real Neo4j driver")

        self._td = tempfile.TemporaryDirectory(prefix="dm_setup_sandbox_")
        base = Path(self._td.name)
        self.home, self.target = base / "deepmap_home", base / "proj"
        self.target.mkdir()
        (self.target / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        self._env = {k: os.environ.get(k) for k in self._ENV}
        for k in self._ENV:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ["GIT_CEILING_DIRECTORIES"] = str(base)
        stubs = ((self.P, {"_USER_SET_NEO4J_URI": False, "_bolt_state": lambda *a, **k: "down",
                           "is_bolt_up": lambda *a, **k: False, "is_http_up": lambda *a, **k: False,
                           "_port_in_use": lambda *a, **k: False, "_make_driver": no_driver,
                           "_list_processes": lambda: [], "_pid_alive": lambda pid: False,
                           "_pid_cmdline": lambda pid: None, "_pid_image": lambda pid: None,
                           "_pid_create_time": lambda pid: None}),
                 (PR, {"_port_in_use": lambda *a, **k: False}))
        self._saved = [(obj, k, getattr(obj, k, _MISSING)) for obj, attrs in stubs for k in attrs]
        for obj, attrs in stubs:
            for k, v in attrs.items():
                setattr(obj, k, v)
        return self

    def __exit__(self, *exc):
        for obj, k, v in reversed(self._saved):
            if v is _MISSING:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()
        return False


def gate_terminate_pid_posix_tree() -> None:
    print("\n-- _terminate_pid: POSIX tree=True signals the process GROUP (killpg), not just the pid --")
    P = _load()
    saved = {"is_windows": P.is_windows, "_pid_alive": P._pid_alive,
             "kill": getattr(os, "kill", None), "killpg": getattr(os, "killpg", None),
             "getpgid": getattr(os, "getpgid", None), "had_killpg": hasattr(os, "killpg"),
             "had_getpgid": hasattr(os, "getpgid")}
    rec = {"kill": [], "killpg": [], "getpgid": []}
    try:
        P.is_windows = lambda: False          # force the POSIX branch on any host
        P._pid_alive = lambda pid: False      # "process is gone" right after the first signal
        os.kill = lambda pid, sig: rec["kill"].append((pid, sig))
        os.killpg = lambda pgid, sig: rec["killpg"].append((pgid, sig))
        os.getpgid = lambda pid: 4242         # a distinct fake group id

        rec["kill"].clear(); rec["killpg"].clear()
        ok = P._terminate_pid(1234, tree=True)
        check("tree=True -> killpg on the group (not a bare kill)",
              ok and rec["killpg"] and rec["killpg"][0] == (4242, 15) and not rec["kill"],
              f"kill={rec['kill']} killpg={rec['killpg']}")

        rec["kill"].clear(); rec["killpg"].clear()
        ok = P._terminate_pid(1234, tree=False)
        check("tree=False -> single os.kill on the pid (not killpg)",
              ok and rec["kill"] and rec["kill"][0] == (1234, 15) and not rec["killpg"],
              f"kill={rec['kill']} killpg={rec['killpg']}")
    finally:
        P.is_windows = saved["is_windows"]
        P._pid_alive = saved["_pid_alive"]
        if saved["kill"] is not None:
            os.kill = saved["kill"]
        if saved["had_killpg"]:
            os.killpg = saved["killpg"]
        elif hasattr(os, "killpg"):
            del os.killpg
        if saved["had_getpgid"]:
            os.getpgid = saved["getpgid"]
        elif hasattr(os, "getpgid"):
            del os.getpgid


class _FakePopenExited:
    pid = 4321

    def poll(self):
        return 1   # already exited (instant failure)


def gate_start_neo4j_early_exit() -> None:
    print("\n-- start_neo4j: warns on an instant launcher exit (doesn't silently block) --")
    P = _load()
    import tempfile
    saved = {"paths": P.paths, "Popen": P.subprocess.Popen, "is_windows": P.is_windows}
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            P.is_windows = lambda: False
            P.paths = lambda *a, **k: {"neo4j_bin": d / "neo4j", "log": d / "console.log",
                                       "conf_dir": d / "conf", "pidfile": d / "run" / "neo4j.pid"}
            P.subprocess.Popen = lambda *a, **k: _FakePopenExited()
            buf = io.StringIO()
            with redirect_stderr(buf):
                pid = P.start_neo4j("5.26.0")
            err = buf.getvalue().lower()
            check("returns the launcher pid", pid == 4321, str(pid))
            check("warns that the launcher exited immediately (fail-fast hint)",
                  "exit" in err or "early" in err or "immediately" in err, repr(buf.getvalue()))
    finally:
        P.paths = saved["paths"]
        P.subprocess.Popen = saved["Popen"]
        P.is_windows = saved["is_windows"]


def gate_setup_fails_loud_on_mcp_failure() -> None:
    """setup() must return False (exit 1) when the MCP servers GENUINELY fail to start — the deepmap
    tools are the product, so 'Neo4j+sync OK but no MCP' is not a success. start_mcp_server is idempotent
    (an already-listening port returns True), so a False is a real failure, not a benign 'already up'.
    It must also VERIFY BOTH servers (search AND embed), not just search. All deps are mocked — nothing
    is spawned or connected. (bug-hunt: setup returned True + verified only search.) Runs in _SetupSandbox (a
    temp DEEPMAP_HOME + temp project): never the real ~/.deepmap, never this repo as the target."""
    print("\n-- setup() fails loud on a genuine MCP startup failure + verifies BOTH servers --")
    P = _load()

    class _FakeSync:
        @staticmethod
        def sync(target, cleanup_stale=True):
            return True

    names = ("ensure_neo4j", "verify_search", "start_mcp_servers", "verify_mcp", "_print_mcp_hint",
             "_load", "apply_ports_for", "write_mcp_json")
    saved = {n: getattr(P, n, None) for n in names}
    try:
        P.ensure_neo4j = lambda **k: True
        P._load = lambda name: _FakeSync          # setup() does _load("sync")
        P.verify_search = lambda *a, **k: True
        P._print_mcp_hint = lambda *a, **k: None
        P.verify_mcp = lambda name: True
        # Neutralise the per-project port side effects — this gate tests setup()'s Neo4j/verify logic,
        # NOT the port registry (which has its own hermetic suite). Without this, setup() would allocate
        # in a port registry and write a .mcp.json into its target.
        P.apply_ports_for = lambda *a, **k: None
        P.write_mcp_json = lambda *a, **k: None

        with redirect_stderr(io.StringIO()), _SetupSandbox(P) as sb:
            target = str(sb.target)
            P.start_mcp_servers = lambda **k: False   # a GENUINE failure
            r_fail = P.setup(target, uri="x", user="y", password="z", verify=True)
            P.start_mcp_servers = lambda **k: True    # servers came up
            r_ok = P.setup(target, uri="x", user="y", password="z", verify=True)
            # verify must hit BOTH servers
            seen = []
            P.verify_mcp = lambda name: (seen.append(name) or True)
            P.setup(target, uri="x", user="y", password="z", verify=True)

        check("setup() returns False when MCP servers genuinely fail to start", r_fail is False, str(r_fail))
        check("setup() returns True when the MCP servers start", r_ok is True, str(r_ok))
        check("setup() verifies BOTH deepmap-search AND deepmap-embed (not just search)",
              set(seen) == {"deepmap-search", "deepmap-embed"}, str(seen))
    finally:
        for n, v in saved.items():
            if v is not None:
                setattr(P, n, v)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — provisioner process-management robustness gates (no spawn)")
    print("=" * 64)

    gate_terminate_pid_posix_tree()
    gate_start_neo4j_early_exit()
    gate_setup_fails_loud_on_mcp_failure()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
