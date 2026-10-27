#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-kill gates for the ROUND-10 provisioner fixes (build/provision.py, owner W2) — the heal / mcp-start side
of the code-restart rule and the git-hook interpreter:

  L1 an owner record that names ANOTHER pid than the pid file (the record of an EARLIER process on the port — a newer
     build's server died, an older window's heal respawned it and rewrote only the pid file) counts as NO owner record,
     exactly as the hook's reconnect note (hook_state.mcp_code_stale) and stop_mcp_server treat it: the K3 evidence
     decides. Before, heal left such a server on old code forever while every session start promised a restart. The
     paired check: for every owner-record state the heal decision and the hook note agree.
  L2 mcp-start's code restart of a server whose owner record can't vouch for the current Neo4j config (no record — every
     v0.27.4 server — or no fingerprint) first proves the current config connects (_bolt_state 'up'); otherwise the old
     server keeps serving (a NOTE) — never a working server swapped for one Neo4j rejects.
  L3 a plugin dir updated IN PLACE (--plugin-dir / git-clone + git pull): a server with no recorded version running from
     THIS code's own dir is restarted when it provably started before that dir's plugin.json or its own server script
     was last written — never on a touch of any other file, never for a server whose record names its version.
  L4 an EQUAL version from another plugin dir that still exists is never restarted (two installed copies — two
     CLAUDE_CONFIG_DIR accounts, two marketplaces — each "the installed newest" in its own window used to ping-pong).
  L5 `git-hook --install` with a command-line DEEPMAP_PYTHON ('py -3') writes the interpreter that command runs (one
     existing file) and the hook REALLY syncs on commit; one that can't run is refused (rc 2); --status verifies the
     interpreter a block names exists / runs (the old one-word 'py -3' block is STALE, not "installed").
  L6 the code root of a server whose (removed) plugin dir has a SPACE in its path is read from the process's EXACT argv
     (Linux /proc NUL-split, macOS KERN_PROCARGS2), so heal agrees with the hook; the space-joined fallback accepts a gone
     `<...>/build/<script>` only behind an existing interpreter file.
  doc heal()'s docstring states the K3 / L1 rule (no longer "heal never signals a server with no owner sidecar").

Reuses the round-4/6/8/9 hermetic harnesses (same owner): DEEPMAP_HOME -> a throwaway dir; port probes, pid liveness /
command line / creation time / environment, spawns and kills are stubbed; the Linux /proc is simulated through the
provision._PROC seam; CLAUDE_CONFIG_DIR -> throwaway dirs. The git-hook gates use REAL git in throwaway repos (isolated
global/system config) and a stub interpreter (a commit never runs DeepMap's real sync). No Neo4j, no network. Exit 0 =
GREEN.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from provision_round4_gates import (P, PR, _Patch, _Sandbox, _mk_project, _captured, _quiet,  # noqa: E402
                                    _clear_port_env)
from provision_round6_gates import _mcp_env, _seed_owned, _seed_pidfile_only  # noqa: E402
from provision_r8_gates import _GitBox, _CodeWorld, _rewrite_owner, _script_cmd, _read_json, _rmtree  # noqa: E402
from provision_r9_gates import _ClaudeDir, _fake_plugin, _ident, _preowner_world, OLD_VER  # noqa: E402

_PASS = 0
_FAIL = 0
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
_REAL_PID_CMDLINE = P._pid_cmdline   # the real reader (the round-4 _Sandbox stubs it to "unreadable")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _run_gate(fn) -> None:
    """An unexpected raise (e.g. a not-yet-built API) counts as ONE failed check, never a crash."""
    try:
        fn()
    except Exception as e:   # noqa: BLE001
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e} " + " | ".join(tb[-4:]))


def _hook_state():
    """hooks/deepmap_hook_state.py, loaded fresh (it shares provision's port_registry module object)."""
    spec = importlib.util.spec_from_file_location("dm_hook_state_r10w2", str(REPO / "hooks" / "deepmap_hook_state.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_owner(name, port, rec) -> None:
    of = P._mcp_ownerfile(name, int(port))
    of.parent.mkdir(parents=True, exist_ok=True)
    of.write_text(rec if isinstance(rec, str) else json.dumps(rec), encoding="utf-8")


def _reset_started() -> None:
    del P._MCP_STARTED[:]
    P._MCP_CODE_RESTARTED.clear()


# =========================================================================== L1 — a pid-mismatched owner record

def gate_l1_stale_owner_restart() -> None:
    print("\n-- L1: an owner record naming ANOTHER pid than the pid file = no owner record -> the K3 evidence decides --")
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        v274 = _fake_plugin(work / "plugins" / "deep-map", OLD_VER)
        w = _preowner_world(a, s, 7101, v274)
        cur = P._code_identity()
        # the leftover record of a NEWER build's server that died (its pid is gone); v0.27.4 rewrote only the pid file
        _write_owner(SEARCH, s, {"pid": 999991, "project_key": PR.project_key(a), "root": a.as_posix(), "created": 50.0,
                                 PR.CODE_ROOT_FIELD: cur.get(PR.CODE_ROOT_FIELD),
                                 PR.CODE_VERSION_FIELD: cur.get(PR.CODE_VERSION_FIELD)})
        _reset_started()
        with _Patch(P, **w.stubs()):
            why = P._own_server_code_stale(SEARCH, s, a)
            ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("heal's decision: an older build's server whose .owner names an EARLIER pid is this project's old code "
              "(restart)", isinstance(why, str) and OLD_VER in why, repr(why))
        check("start_mcp_server (heal / mcp-start) restarts it through stop_mcp_server (stop + spawn)",
              ok is True and (7101, True) in w.killed and (SEARCH, s) in w.spawned,
              f"ok={ok} killed={w.killed} spawned={w.spawned} {out.strip()[-300:]}")
        rec = _read_json(P._mcp_ownerfile(SEARCH, s))
        check("... the stale record is replaced by the respawned server's own (its pid, this project, current code)",
              rec.get("pid") not in (None, 999991, 7101) and rec.get("project_key") == PR.project_key(a)
              and rec.get(PR.CODE_ROOT_FIELD) == cur.get(PR.CODE_ROOT_FIELD), str(rec))
        check("... recorded as a code restart (heal status mcp_code_restarted evidence)",
              OLD_VER in str(P._MCP_CODE_RESTARTED.get(SEARCH, "")), str(P._MCP_CODE_RESTARTED))

    def world(a, s, pid, root, **kw):
        return _preowner_world(a, s, pid, root, **kw)

    cases = (
        # label, owner record (callable(a) -> rec | str), world kwargs, environ, registered, want restart
        ("a MALFORMED .owner (not JSON) = no owner record -> restarted", lambda a: "{not json", {}, None, True, True),
        ("an .owner naming an earlier pid of ANOTHER project (a record about another process) -> restarted",
         lambda a: {"pid": 999992, "project_key": "f" * 12, "root": "/elsewhere"}, {}, None, True, True),
        ("... but the port is NOT registered to this project -> left alone",
         lambda a: {"pid": 999993, "project_key": PR.project_key(a)}, {}, None, False, False),
        ("... but the process started AFTER its pid file was written -> left alone",
         lambda a: {"pid": 999994, "project_key": PR.project_key(a)}, {"created": time.time() + 10_000}, None, True,
         False),
        ("... but its environment names ANOTHER project -> left alone",
         lambda a: {"pid": 999995, "project_key": PR.project_key(a)}, {}, {"DEEPMAP_PROJECT_KEY": "e" * 12}, True,
         False),
        ("control: an .owner naming THIS pid for ANOTHER project -> never (not this project's server)",
         lambda a: {"pid": 7102, "project_key": "d" * 12, "root": "/elsewhere"}, {}, None, True, False),
    )
    for label, mk_owner, world_kw, environ, registered, want in cases:
        with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
            a = _mk_project(work, "a", marker=True)
            if registered:
                s, e = PR.allocate_ports(a)
            else:
                s, e = 8981, 8982
            _mcp_env((s, e))
            w = world(a, s, 7102, _fake_plugin(work / "plugins" / "deep-map", OLD_VER), **world_kw)
            if environ:
                w.environ[7102] = environ
            _write_owner(SEARCH, s, mk_owner(a))
            _reset_started()
            with _Patch(P, **w.stubs()):
                ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            restarted = bool(w.killed) and bool(w.spawned)
            check(f"L1: {label}", (ok is True or not want) and restarted is want and (want or not w.killed),
                  f"ok={ok} killed={w.killed} spawned={w.spawned} {out.strip()[-200:]}")
    # heal end to end: both servers, each with a stale owner record from an earlier (newer) process
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        v274 = _fake_plugin(work / "plugins" / "deep-map", OLD_VER)
        _seed_pidfile_only(SEARCH, s, 7111)
        _seed_pidfile_only(EMBED, e, 7112)
        for n, p, old in ((SEARCH, s, 999981), (EMBED, e, 999982)):
            _write_owner(n, p, {"pid": old, "project_key": PR.project_key(a), "created": 50.0})
        w = _CodeWorld({7111: (SEARCH, s), 7112: (EMBED, e)})
        w.cmd[7111] = _script_cmd(v274.as_posix(), SEARCH)
        w.cmd[7112] = _script_cmd(v274.as_posix(), EMBED)
        _clear_port_env()
        with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None):
            rc, out = _captured(P.main, ["heal", str(a)])
        st = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
        check("heal end to end (stale .owner files + an older build's pid files): rc 0, both servers restarted",
              rc == 0 and sorted(w.spawned) == sorted([(SEARCH, s), (EMBED, e)]), f"rc={rc} {w.spawned} {out[-300:]}")
        mcr = st.get("mcp_code_restarted") or {}
        check("... heal status: mcp_started + mcp_code_restarted name both (the reconnect note's evidence)",
              sorted(st.get("mcp_started") or []) == sorted([SEARCH, EMBED]) and sorted(mcr) == sorted([SEARCH, EMBED]),
              str(st))


def gate_l1_agrees_with_hook() -> None:
    print("\n-- L1 (paired): heal's restart decision == the hook's reconnect note, for every owner-record state --")
    try:
        HS = _hook_state()
    except Exception as e:   # noqa: BLE001
        check("hooks/deepmap_hook_state.py loads", False, repr(e))
        return
    fn = getattr(HS, "mcp_code_stale", None)
    check("hook_state.mcp_code_stale exists", callable(fn))
    if not callable(fn):
        return
    pid = 7201
    states = (
        ("no .owner file (an older build)", lambda a, cur, old: None, True),
        ("an unreadable .owner (not JSON)", lambda a, cur, old: "{not json", True),
        ("an .owner naming an earlier pid (this project's, current code)",
         lambda a, cur, old: {"pid": 999971, "project_key": PR.project_key(a), **cur}, True),
        ("an .owner naming an earlier pid (another project's)",
         lambda a, cur, old: {"pid": 999972, "project_key": "c" * 12}, True),
        ("an .owner naming THIS pid for ANOTHER project", lambda a, cur, old: {"pid": pid, "project_key": "b" * 12},
         False),
        ("an .owner naming THIS pid for this project, recording the OLD code",
         lambda a, cur, old: {"pid": pid, "project_key": PR.project_key(a), "created": 100.0, **old}, True),
        ("an .owner naming THIS pid for this project, recording the CURRENT code",
         lambda a, cur, old: {"pid": pid, "project_key": PR.project_key(a), "created": 100.0, **cur}, False),
        ("an .owner naming THIS pid for this project with no code fields (pre-J5)",
         lambda a, cur, old: {"pid": pid, "project_key": PR.project_key(a), "created": 100.0}, True),
    )
    for label, mk, want in states:
        with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            v274 = _fake_plugin(work / "plugins" / "deep-map", OLD_VER)
            w = _preowner_world(a, s, pid, v274)
            cur = {k: v for k, v in P._code_identity().items() if v}
            old = {PR.CODE_ROOT_FIELD: v274.as_posix(), PR.CODE_VERSION_FIELD: OLD_VER}
            rec = mk(a, cur, old)
            if rec is not None:
                _write_owner(SEARCH, s, rec)
            stubs = w.stubs()
            with _Patch(P, **stubs), _Patch(PR, _pid_alive=stubs["_pid_alive"], pid_cmdline=stubs["_pid_cmdline"],
                                            pid_create_time=stubs["_pid_create_time"],
                                            proc_environ=stubs["_proc_environ"]):
                heal_says = P._own_server_code_stale(SEARCH, s, a) is not None
                note = fn(a)
            hook_says = bool(note)
            check(f"agree — {label}: heal restart={heal_says}, hook note={hook_says} (want {want})",
                  heal_says is want and hook_says is want, f"note={note!r}")


# =========================================================================== L2 — only a working config replaces

def gate_l2_code_restart_needs_working_config() -> None:
    print("\n-- L2: a code restart the owner record can't vouch for runs only when the current Neo4j config connects --")
    for state in ("auth", "down"):
        with _Sandbox(bolt=state) as (home, work), _ClaudeDir():
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            w = _preowner_world(a, s, 7301, _fake_plugin(work / "plugins" / "deep-map", OLD_VER))
            calls = []

            def spy(*args, _st=state, **k):
                calls.append(args)
                return _st
            _reset_started()
            with _Patch(P, **w.stubs()), _Patch(P, _bolt_state=spy):
                ok, out = _captured(P.start_mcp_server, SEARCH, password="WRONG-PASSWORD", target=a)
            check(f"mcp-start, an older build's server (no record to vouch for the config), current config '{state}' -> the "
                  "working old server is left serving (no stop, no spawn), True + a NOTE",
                  ok is True and not w.killed and not w.spawned and "NOTE" in out and state in out,
                  f"ok={ok} killed={w.killed} spawned={w.spawned} {out.strip()[-300:]}")
            check(f"... the current config WAS probed (with the password mcp-start was given) [{state}]",
                  bool(calls) and any("WRONG-PASSWORD" in [str(x) for x in c] for c in calls), str(calls))
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _preowner_world(a, s, 7302, _fake_plugin(work / "plugins" / "deep-map", OLD_VER))
        _reset_started()
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("control: the current config connects ('up') -> the older build's server IS restarted",
              ok is True and (7302, True) in w.killed and (SEARCH, s) in w.spawned, f"{w.killed} {w.spawned}")
    # a sidecar whose fingerprint names exactly the current config vouches for it: no probe is needed
    with _Sandbox(bolt="auth") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _CodeWorld({7303: (SEARCH, s)})
        with _Patch(P, **w.stubs()):
            _seed_owned(SEARCH, s, 7303, a, uri=P._server_config()[0])
        _rewrite_owner(SEARCH, s, **{PR.CODE_ROOT_FIELD: "/plugins/cache/deep-map/0.27.9",
                                     PR.CODE_VERSION_FIELD: "0.27.9", "script": None})
        calls = []
        _reset_started()
        with _Patch(P, **w.stubs()), _Patch(P, _bolt_state=lambda *a_, **k: calls.append(a_) or "auth"):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("control: a J5 sidecar whose config fingerprint IS the current config vouches for it -> the old-code "
              "server is restarted without a probe (it already ran with exactly these credentials)",
              ok is True and (SEARCH, s) in w.spawned and not calls, f"spawned={w.spawned} calls={calls}")


# =========================================================================== L3 — updated in place

def _inplace_root(work: Path) -> Path:
    return _fake_plugin(work / "clone", "0.28.0", name="deep-map")


def _set_mtime(p: Path, t: float) -> None:
    os.utime(p, (t, t))


def gate_l3_inplace_update() -> None:
    print("\n-- L3: a plugin dir updated IN PLACE restarts the servers its previous code started (no recorded version) --")
    script = P.MCP_SERVERS[SEARCH]["script"]
    now = time.time()

    def run(label, *, pj_age, script_age, created_age, touch_other=False, want, owner=None):
        with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            root = _inplace_root(work)
            ident = {PR.CODE_ROOT_FIELD: root.as_posix(), PR.CODE_VERSION_FIELD: "0.28.0"}
            _set_mtime(root / ".claude-plugin" / "plugin.json", now - pj_age)
            _set_mtime(root / "build" / script, now - script_age)
            if touch_other:
                (root / "README.md").write_text("x\n", encoding="utf-8")
                (root / "build" / "sync.py").write_text("x = 1\n", encoding="utf-8")
                _set_mtime(root / "README.md", now)
                _set_mtime(root / "build" / "sync.py", now)
            created = now - created_age
            w = _preowner_world(a, s, 7401, root, created=created)
            if owner == "j5":
                with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(ident)):
                    _seed_owned(SEARCH, s, 7401, a, created=created)
            elif owner == "pre-j5":
                with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(ident)):
                    _seed_owned(SEARCH, s, 7401, a, created=created)
                _rewrite_owner(SEARCH, s, **{PR.CODE_ROOT_FIELD: None, PR.CODE_VERSION_FIELD: None, "script": None})
            _reset_started()
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(ident)):
                ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            restarted = (7401, True) in w.killed and (SEARCH, s) in w.spawned
            check(f"L3: {label}", ok is True and restarted is want and (want or not w.killed),
                  f"ok={ok} killed={w.killed} spawned={w.spawned} {out.strip()[-240:]}")
            if want:
                check("... it says the directory was updated in place",
                      "updated in place" in out and "updated in place" in str(P._MCP_CODE_RESTARTED.get(SEARCH, "")),
                      out.strip()[-300:])

    run("no owner record, started BEFORE plugin.json was rewritten (git pull of a new release) -> restarted",
        pj_age=0, script_age=7200, created_age=3600, want=True)
    run("no owner record, started before only its SERVER SCRIPT was rewritten -> restarted",
        pj_age=7200, script_age=0, created_age=3600, want=True)
    run("no owner record, started AFTER both were written (it runs the code on disk) -> left alone",
        pj_age=7200, script_age=7200, created_age=3600, want=False)
    run("no owner record, only OTHER files (README.md, build/sync.py) touched since it started -> left alone",
        pj_age=7200, script_age=7200, created_age=3600, touch_other=True, want=False)
    run("a pre-J5 sidecar (no code fields) started before plugin.json was rewritten -> restarted",
        pj_age=0, script_age=7200, created_age=3600, owner="pre-j5", want=True)
    run("a J5 sidecar recording this dir's SAME version, started before plugin.json was rewritten -> left alone "
        "(a dev tree's edits never restart its servers)", pj_age=0, script_age=0, created_age=3600, owner="j5",
        want=False)
    verdict = getattr(P, "_code_restart_verdict", None)
    with tempfile.TemporaryDirectory(prefix="dmr10v") as t:
        root = _inplace_root(Path(t))
        cur = _ident(root, "0.28.0")
        _set_mtime(root / ".claude-plugin" / "plugin.json", now)
        _set_mtime(root / "build" / script, now - 7200)
        for label, kw, want in (
                ("started before plugin.json -> True", dict(started=now - 3600, script=script), True),
                ("start unknown -> False (never restarted on a guess)", dict(started=None, script=script), False),
                ("started after -> False", dict(started=now + 5, script=script), False),
                ("a start within the 2 s tolerance -> False", dict(started=now - 1, script=script), False),
                ("no start argument (the pre-L3 call) -> False", {}, False)):
            got = verdict(root.as_posix(), None, cur, **kw) if callable(verdict) else "missing"
            check(f"verdict, same dir, unknown version: {label}", got is want, f"got {got!r}")
    # heal end to end: both servers of the previous in-place code restarted, the status says why
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        root = _inplace_root(work)
        ident = {PR.CODE_ROOT_FIELD: root.as_posix(), PR.CODE_VERSION_FIELD: "0.28.0"}
        _set_mtime(root / ".claude-plugin" / "plugin.json", now)
        _seed_pidfile_only(SEARCH, s, 7411)
        _seed_pidfile_only(EMBED, e, 7412)
        w = _CodeWorld({7411: (SEARCH, s), 7412: (EMBED, e)}, created=now - 3600)
        w.cmd[7411] = _script_cmd(root.as_posix(), SEARCH)
        w.cmd[7412] = _script_cmd(root.as_posix(), EMBED)
        _clear_port_env()
        with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None,
                                            _code_identity=lambda: dict(ident)):
            rc, out = _captured(P.main, ["heal", str(a)])
        st = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
        mcr = st.get("mcp_code_restarted") or {}
        check("heal end to end after an in-place update: rc 0, both servers restarted, the status says 'updated in "
              "place'", rc == 0 and sorted(w.spawned) == sorted([(SEARCH, s), (EMBED, e)])
              and sorted(mcr) == sorted([SEARCH, EMBED]) and all("updated in place" in str(v) for v in mcr.values()),
              f"rc={rc} {w.spawned} {mcr} {out[-300:]}")


# =========================================================================== L4 — equal versions never ping-pong

def gate_l4_equal_version_never_restarts() -> None:
    print("\n-- L4: an EQUAL version from another plugin dir that still exists is never restarted (no tie games) --")
    verdict = getattr(P, "_code_restart_verdict", None)
    check("provision._code_restart_verdict exists", callable(verdict))
    if not callable(verdict):
        return
    with tempfile.TemporaryDirectory(prefix="dmr10q") as t:
        t = Path(t)
        a280 = _fake_plugin(t / "acct-a" / "cache", "0.28.0")
        b280 = _fake_plugin(t / "acct-b" / "cache", "0.28.0")
        b281 = _fake_plugin(t / "acct-b" / "cache", "0.28.1")
        gone = (t / "acct-a" / "cache" / "0.27.9").as_posix()
        for label, rroot, rver, cur, installed, want in (
                ("equal version, the CURRENT dir is the installed newest -> NO restart (was: restart onto it)",
                 a280, "0.28.0", _ident(b280, "0.28.0"), [(b280, "0.28.0")], False),
                ("equal version, both dirs installed (two marketplaces) -> no restart", a280, "0.28.0",
                 _ident(b280, "0.28.0"), [(a280, "0.28.0"), (b280, "0.28.0")], False),
                ("equal version, no installed record -> no restart", a280, "0.28.0", _ident(b280, "0.28.0"), None,
                 False),
                ("a sidecar-less server (version from its dir's plugin.json) at the equal version -> no restart",
                 a280, None, _ident(b280, "0.28.0"), [(b280, "0.28.0")], False),
                ("equal version but the running dir is GONE -> restart (nothing else can serve it)", gone, "0.28.0",
                 _ident(b280, "0.28.0"), None, True),
                ("control: current NEWER (0.28.1 > 0.28.0) -> restart", a280, "0.28.0", _ident(b281, "0.28.1"), None,
                 True),
                ("control: current OLDER -> never (a downgrade)", b281, "0.28.1", _ident(a280, "0.28.0"), None, False),
                ("control: unordered versions from an existing dir -> unknown (None)", a280, "banana",
                 _ident(b280, "cherry"), [(b280, "cherry")], None)):
            with _ClaudeDir(installed):
                got = verdict(rroot if isinstance(rroot, str) else rroot.as_posix(), rver, cur)
            check(f"verdict: {label}", got is want, f"got {got!r}")
    # two Claude Code accounts (two CLAUDE_CONFIG_DIRs), one version each installed, both windows open on one project
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        root_a = _fake_plugin(work / "acct-a" / "plugins" / "cache", "0.28.0")
        root_b = _fake_plugin(work / "acct-b" / "plugins" / "cache", "0.28.0")
        id_a, id_b = _ident(root_a, "0.28.0"), _ident(root_b, "0.28.0")
        with _ClaudeDir([(root_a, "0.28.0")]) as cfg_a, _ClaudeDir([(root_b, "0.28.0")]) as cfg_b:
            w = _CodeWorld({7501: (SEARCH, s)})
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_a)):
                _seed_owned(SEARCH, s, 7501, a)          # window A (account A) started them
            for _ in range(3):
                for cfg, ident in ((cfg_b, id_b), (cfg_a, id_a)):
                    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg.base)
                    with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda i=ident: dict(i)):
                        _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            check("two accounts' copies of 0.28.0, alternating SessionStart heals x3 -> ZERO restarts (no ping-pong)",
                  not w.killed and not w.spawned, f"killed={w.killed} spawned={w.spawned}")
        # one config listing both copies (two marketplaces): the same, in one window
        with _ClaudeDir([(root_a, "0.28.0"), (root_b, "0.28.0")]):
            w = _CodeWorld({7502: (SEARCH, s)})
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_a)):
                _seed_owned(SEARCH, s, 7502, a)
            for _ in range(3):
                for ident in (id_b, id_a):
                    with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda i=ident: dict(i)):
                        _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            check("two marketplaces' copies of 0.28.0 in ONE config, both plugins' heals x3 -> ZERO restarts",
                  not w.killed and not w.spawned, f"killed={w.killed} spawned={w.spawned}")


# =========================================================================== L5 — git-hook with a command-line DEEPMAP_PYTHON

def _launcher(gb) -> str:
    """A command line (two words, like `py -3`) that answers `-c "...sys.executable"` with the gate's STUB interpreter —
    so the resolved hook runs the stub, never DeepMap's real sync."""
    launch = gb.base / "launch.py"
    launch.write_text("import sys\nif sys.argv[1:2] == ['-c']:\n    sys.stdout.write(%r)\n    sys.exit(0)\n"
                      "sys.exit(3)\n" % gb.stub.as_posix(), encoding="utf-8")
    return f'"{sys.executable}" "{launch}"'


def _clear_py_cache() -> None:
    cache = getattr(P, "_HOOK_PY_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()


def _old_block_text(root, top, interp_word: str, stub) -> str:
    """The block the pre-L5 provision wrote for DEEPMAP_PYTHON=`interp_word` (the whole value as ONE quoted word)."""
    saved = os.environ.get("DEEPMAP_PYTHON")
    os.environ["DEEPMAP_PYTHON"] = str(stub)
    try:
        _clear_py_cache()
        block = P._git_hook_block(root, top)
    finally:
        os.environ["DEEPMAP_PYTHON"] = saved if saved is not None else ""
        _clear_py_cache()
    fwd = Path(stub).as_posix() if os.name == "nt" else str(stub)
    return block.replace(f"nohup {P._sh_quote(fwd)}", f"nohup {P._sh_quote(interp_word)}")


def gate_l5_command_line_python() -> None:
    print("\n-- L5: git-hook with a command-line DEEPMAP_PYTHON ('py -3') -> the interpreter it runs; else refused --")
    if not shutil.which("git"):
        check("git is available for the git-hook gates", False)
        return
    split = getattr(P, "_split_command_line", None)
    check("provision._split_command_line exists", callable(split))
    if callable(split):
        check("split: 'py -3' -> ['py', '-3']", split("py -3") == ["py", "-3"], repr(split("py -3")))
        check("split: '/usr/bin/env python3' -> 2 words", split("/usr/bin/env python3") == ["/usr/bin/env", "python3"])
        if os.name == "nt":
            got = split(r'"C:\Program Files\Py\python.exe" -X utf8')
            check("split (Windows): a quoted path keeps its backslashes, quotes removed",
                  got == [r"C:\Program Files\Py\python.exe", "-X", "utf8"], repr(got))
        else:
            got = split("'/opt/my py/bin/python3' -X utf8")
            check("split (POSIX): a quoted path with a space stays one word", got == ["/opt/my py/bin/python3", "-X",
                                                                                   "utf8"], repr(got))
    with _GitBox() as gb:
        # (a) a command line that resolves: the block names ONE existing interpreter and a real commit syncs
        a = gb.repo("a")
        os.environ["DEEPMAP_PYTHON"] = _launcher(gb)
        _clear_py_cache()
        rc, out = _captured(P.git_hook, str(a), "install")
        text = gb.hook(a).read_text(encoding="utf-8") if gb.hook(a).exists() else ""
        stub = gb.stub.as_posix()
        check("a command-line DEEPMAP_PYTHON: --install rc 0 and the block names the interpreter it runs (one existing "
              "file), not the command line", rc == 0 and f"nohup {P._sh_quote(stub)}" in text and "launch.py" not in text,
              f"rc={rc} {text[-400:]} {out[-300:]}")
        rc_s, out_s = _captured(P.git_hook, str(a), "status")
        check("... --status: installed (current)", rc_s == 0 and "installed —" in out_s and "STALE" not in out_s,
              out_s.strip()[-300:])
        gb.reset_log()
        check("... a REAL commit runs the hook and it syncs this project", gb.commit(a) == 0
              and any(Path(x).name == "a" for x in gb.synced()), str(gb.synced(wait_s=1)))
        # (b) a command line that can't run: refused, nothing written; --status says why
        b = gb.repo("b")
        os.environ["DEEPMAP_PYTHON"] = "dm-no-such-python-r10 -3"
        _clear_py_cache()
        rc, out = _captured(P.git_hook, str(b), "install")
        check("a command-line DEEPMAP_PYTHON that does not run: --install REFUSED rc 2, no hook written, the message "
              "names DEEPMAP_PYTHON", rc == 2 and not gb.hook(b).exists() and "DEEPMAP_PYTHON" in out,
              f"rc={rc} {out[-300:]}")
        rc, out = _captured(P.git_hook, str(b), "status")
        check("... --status: rc 0 and --install is 'not available until DEEPMAP_PYTHON' works",
              rc == 0 and "DEEPMAP_PYTHON" in out and "not available" in out, out.strip()[-300:])
        # (c) the block the OLD code wrote for DEEPMAP_PYTHON='py -3' (the value as ONE word) is STALE, not installed
        c = gb.repo("c")
        top = gb.git(c, "rev-parse", "--show-toplevel")
        hook = gb.hook(c)
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\n" + _old_block_text(c, top, "py -3", gb.stub), encoding="utf-8", newline="\n")
        os.chmod(hook, 0o755)
        os.environ["DEEPMAP_PYTHON"] = "py -3"
        _clear_py_cache()
        rc, out = _captured(P.git_hook, str(c), "status")
        check("the old one-word 'py -3' block with DEEPMAP_PYTHON='py -3': --status says STALE and why (ONE word)",
              rc == 0 and "STALE" in out and "ONE word" in out, out.strip()[-400:])
        hint = _captured(P._print_git_hook_hint, str(c))[1]
        check("... setup's hint says the hook is out of date", "out of date" in hint, hint.strip()[-300:])
        os.environ["DEEPMAP_PYTHON"] = _launcher(gb)
        _clear_py_cache()
        rc, out = _captured(P.git_hook, str(c), "install")
        text = hook.read_text(encoding="utf-8")
        gb.reset_log()
        check("... --install replaces it in place with the resolved interpreter, and a commit syncs",
              rc == 0 and "'py -3'" not in text and f"nohup {P._sh_quote(stub)}" in text and gb.commit(c) == 0
              and any(Path(x).name == "c" for x in gb.synced()), f"rc={rc} {text[-300:]} {out[-200:]}")
        # (d)/(e) --status verifies the interpreter a block names exists / runs
        for label, word, needle, rname in (
                ("a removed venv's python", "/dm-r10-gone/venv/bin/python", "no longer exists", "d1"),
                ("a bare command that is not on PATH", "dm-no-such-python-r10", "not a command on PATH", "d2")):
            d = gb.repo(rname)
            top = gb.git(d, "rev-parse", "--show-toplevel")
            hk = gb.hook(d)
            hk.parent.mkdir(parents=True, exist_ok=True)
            hk.write_text("#!/bin/sh\n" + _old_block_text(d, top, word, gb.stub), encoding="utf-8", newline="\n")
            os.chmod(hk, 0o755)
            os.environ["DEEPMAP_PYTHON"] = word
            _clear_py_cache()
            rc, out = _captured(P.git_hook, str(d), "status")
            check(f"--status: a block naming {label} (DEEPMAP_PYTHON set to it too) is STALE ({needle})",
                  rc == 0 and "STALE" in out and needle in out, out.strip()[-300:])
            rc, out = _captured(P.git_hook, str(d), "install")
            check(f"... and --install refuses it (rc 2): DEEPMAP_PYTHON names {label}", rc == 2 and "DEEPMAP_PYTHON" in out,
                  f"rc={rc} {out[-200:]}")
        # (f) controls: F4 unchanged for an existing interpreter file / unset
        os.environ["DEEPMAP_PYTHON"] = stub
        _clear_py_cache()
        blk = P._git_hook_block("/r", "/r")
        check("control (F4): a single existing interpreter file is written verbatim", f"nohup {P._sh_quote(stub)}" in blk,
              blk)
        os.environ["DEEPMAP_PYTHON"] = "/opt/venv/bin/python"
        _clear_py_cache()
        check("control (F4): a single-word path is still NAMED verbatim by the block text",
              "nohup '/opt/venv/bin/python'" in P._git_hook_block("/r", "/r"))
        os.environ["DEEPMAP_PYTHON"] = str(gb.base)
        _clear_py_cache()
        got = P._hook_python() if hasattr(P, "_hook_python") else (None, None)
        check("a DEEPMAP_PYTHON naming a DIRECTORY is a problem (--install refuses it), not an interpreter",
              "directory" in str(got[1] or ""), repr(got))
        os.environ.pop("DEEPMAP_PYTHON", None)
        _clear_py_cache()
        fwd = Path(sys.executable).as_posix() if os.name == "nt" else sys.executable
        check("control (F4): unset -> the Python running this provisioner",
              f"nohup {P._sh_quote(fwd)}" in P._git_hook_block("/r", "/r"))
        os.environ["DEEPMAP_PYTHON"] = stub
        _clear_py_cache()


# =========================================================================== L6 — the exact argv

def _procargs2(argv, exe=None, env=("A=B",)) -> bytes:
    exe = exe or argv[0]
    body = exe.encode() + b"\0\0\0\0" + b"".join(a.encode() + b"\0" for a in argv) + b"".join(
        x.encode() + b"\0" for x in env)
    return len(argv).to_bytes(4, sys.byteorder) + body


class _FakeProc:
    """A simulated Linux /proc (provision._PROC and port_registry._PROC seams) with is_windows() False."""

    def __init__(self, base: Path, pid: int, argv):
        self.dir = base / "proc"
        (self.dir / str(pid)).mkdir(parents=True)
        (self.dir / str(pid) / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))

    def patches(self):
        return [_Patch(P, _PROC=str(self.dir), is_windows=lambda: False),
                _Patch(PR, _PROC=str(self.dir), _windows=lambda: False)]


def gate_l6_exact_argv() -> None:
    print("\n-- L6: a removed plugin root WITH A SPACE is read from the exact argv (heal agrees with the hook) --")
    parse = getattr(P, "_parse_procargs2", None)
    check("provision._parse_procargs2 exists", callable(parse))
    if callable(parse):
        argv = ["/usr/local/bin/python3", "/Users/u/plug ins/deep-map/0.27.4/build/deepmap_search_server.py"]
        check("macOS KERN_PROCARGS2: argc + exec path + padding + args (+ env) -> the exact argv",
              parse(_procargs2(argv)) == argv, repr(parse(_procargs2(argv))))
        check("... garbage / truncated buffers -> None", parse(b"\x05\x00") is None and parse(b"") is None
              and parse((9).to_bytes(4, sys.byteorder) + b"/x\0\0a\0") is None)
    gone_root = "/dm-r10-gone/plug ins/deep-map/0.27.4"
    script = f"{gone_root}/build/{P.MCP_SERVERS[SEARCH]['script']}"
    with tempfile.TemporaryDirectory(prefix="dmr10p") as t:
        fp = _FakeProc(Path(t), 424242, ["/usr/bin/python3", script])
        pa, pb = fp.patches()
        with pa, pb:
            argv = P._pid_argv(424242) if hasattr(P, "_pid_argv") else None
            got = P._running_code_root(424242, SEARCH)
            hook_root = PR.running_code_root(424242, SEARCH) if hasattr(PR, "running_code_root") else "n/a"
        check("Linux /proc: the argv keeps '<gone root with a space>/build/<script>' as ONE argument",
              argv == ["/usr/bin/python3", script], repr(argv))
        check("heal's code root of that server is the removed root (was None: the space split it)", got == gone_root,
              repr(got))
        check("... and it is the root the hook reads (port_registry.running_code_root) — one answer",
              hook_root == got, f"hook={hook_root!r} heal={got!r}")
    # heal's full decision on that server (registered port, pid file, older than its pid file): restart
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_pidfile_only(SEARCH, s, 424243)
        w = _CodeWorld({424243: (SEARCH, s)})
        stubs = w.stubs()
        stubs["_pid_cmdline"] = _REAL_PID_CMDLINE   # read the simulated /proc for real
        fp = _FakeProc(Path(home), 424243, ["/usr/bin/python3", script])
        pa, pb = fp.patches()
        with pa, pb, _Patch(P, **stubs):
            why = P._own_server_code_stale(SEARCH, s, a)
        check("heal / mcp-start: the v0.27.4 server whose removed root has a space -> restart (the code is gone)",
              isinstance(why, str) and "plug ins" in why, repr(why))
    # the space-joined fallback (macOS `ps` when KERN_PROCARGS2 is unavailable)
    joined = f"{sys.executable} {script}"
    got = P._script_from_cmdline(joined, P.MCP_SERVERS[SEARCH]["script"])
    check("space-joined command line: a gone '<...>/build/<script>' behind an EXISTING interpreter file is taken",
          got == script, repr(got))
    got = P._script_from_cmdline(f"/dm-r10-no/such/python {script}", P.MCP_SERVERS[SEARCH]["script"])
    check("... behind an interpreter that is not a file: not guessed (None)", got is None, repr(got))
    with tempfile.TemporaryDirectory(prefix="dmr10b") as t:
        flat = Path(t) / P.MCP_SERVERS[SEARCH]["script"]
        flat.write_text("x", encoding="utf-8")
        cmd = f'"{sys.executable}" "{flat}"'
        with _Patch(P, _pid_argv=lambda pid: None):
            got = P._running_code_root(1, SEARCH, cmd=cmd)
        check("a server script NOT inside a build/ directory -> no code root (port_registry's rule)", got is None,
              repr(got))


# =========================================================================== doc

def gate_doc_heal() -> None:
    print("\n-- doc: heal()'s docstring states the K3 / L1 rule --")
    doc = " ".join((P.heal.__doc__ or "").split())
    check("heal() no longer claims it never signals a server with no owner sidecar",
          "heal still never signals a server with no owner sidecar" not in doc, doc[-400:])
    check("heal() names the evidence rule (registered port, pid file, older than its pid file, another pid's record)",
          all(x in doc for x in ("REGISTERED", "older than its pid file", "another pid")), doc[-600:])


# =========================================================================== main

def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-10 gates (hermetic, W2): L1 stale owner record, L2 working-config guard,")
    print("          L3 in-place update, L4 equal versions, L5 command-line DEEPMAP_PYTHON, L6 exact argv")
    print("=" * 68)
    for g in (gate_l1_stale_owner_restart, gate_l1_agrees_with_hook, gate_l2_code_restart_needs_working_config,
              gate_l3_inplace_update, gate_l4_equal_version_never_restarts, gate_l5_command_line_python,
              gate_l6_exact_argv, gate_doc_heal):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
