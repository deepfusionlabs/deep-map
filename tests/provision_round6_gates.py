#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-6 provisioner fixes (build/provision.py):

  H1  a PRE-UPGRADE MCP server (a per-port pid file, NO owner sidecar — every server a <= v0.27.4 build started)
      on a port the registry gives THIS project is this project's server for setup's restart (restart_own) —
      the rule stop_mcp_server(name, target) already applies (_mcp_port_registered_to), with its pid-file
      start-time checks kept: it is stopped and respawned with the current config (the field case: after
      setup moved the project off a mis-adopted base claim, the old server kept answering from the OTHER
      project's graph while setup said "reload now"). setup never reports success while a DeepMap server it
      could not restart still holds the project's port (a sidecar-less server whose process can't be verified,
      one on a port NOT registered to the project, or this project's own server whose recorded Neo4j config is
      provably stale): start_mcp_servers fails for it and setup's verdict is FAILED with the manual remedy.
      stop_mcp_server gets the project (target), so a process whose environment names another project is never
      signalled. heal / mcp-start (no restart_own) stay non-disruptive.
  H2  the live owner is the proof: setup (_apply_neo4j_env(allocate=True)) reads the LIVE base's :_DeepMapMeta
      owner BEFORE the allocator relies on the base owner record when another project's registry entry claims
      the base pair and no record names this project yet — so the store's real owner, acting first after the
      upgrade, keeps (re-adopts) its own store instead of being moved to a new instance. Only DeepMap's own
      running base instance gets a record (a Neo4j that merely accepts our credentials proves nothing about the
      store on disk). The lookup verbs (heal / status / stop / mcp-start) do the same live read when their route
      is "none" only because of such a claim, so the real owner is served without a rebuild.
  H3  legacy single-project users are never stranded by the upgrade:
        (a) heal's legacy path (and every other _legacy_route_verdict "ok" with an owner stamp naming this
            project) RECORDS the proof next to the store (self-guarded to DeepMap's own running base instance) —
            never for an unowned+empty graph;
        (b) setup with a DOWN legacy store, no bolt pair, no owner record and FREE base ports STARTS the store
            (create=False — never provisions one; only when the shared binary is already extracted — never a
            download just to read an owner; never with DEEPMAP_NO_BASE_ADOPT=1) to read its live owner: it is
            adopted when it names this project (or is unowned+empty), otherwise it is stopped again (only if
            setup started it) and the project gets its own instance. Base ports held by another program, or an
            owner record already naming another project -> defer without starting anything (as before).
  H4  heal's legacy connect ("down") / credentials ("auth") failure texts never reuse refusal phrasing (the hooks'
      _REFUSAL_WORDS), still name /deep-map:setup, and the auth text says "credentials" (the hooks' _AUTH_WORDS).

Reuses the round-4 gates' hermetic harness (tests/provision_round4_gates.py — same owner): DEEPMAP_HOME -> a
throwaway dir; port / bolt probes, drivers, pid liveness / command line / creation time / environment, the
server spawn and every kill are stubbed. NO Neo4j, NO processes, NO network. Exit 0 = GREEN.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# the shared hermetic harness (importing it also puts build/ on sys.path)
from provision_round4_gates import (P, PR, _Patch, _Spy, _Sandbox, _GraphDrv, _driver_factory,  # noqa: E402
                                    _mk_project, _claim_base, _legacy_store, _ident, _own_cmd, _captured,
                                    _quiet, _neo4j_exc, _clear_port_env)

sys.path.insert(0, str(HERE.parent / "hooks"))
try:
    import deepmap_hook_state as HS   # noqa: E402 — read-only: the hooks' refusal / auth word lists
except Exception:                     # noqa: BLE001
    HS = None

_PASS = 0
_FAIL = 0

BASE = (7687, 7474)
BASE_URI = "bolt://localhost:7687"
SEARCH, EMBED = "deepmap-search", "deepmap-embed"


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
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e}")


def _status(home: Path, target) -> dict:
    try:
        return json.loads((home / "run" / f"heal-{PR.project_key(target)}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _same(a, b) -> bool:
    return bool(a) and bool(b) and PR._same_project_path(str(a), str(b))


def _record_names(target) -> bool:
    return _same(P._base_owner_root(), _ident(target))


def _record_source() -> str | None:
    try:
        return json.loads(P._base_owner_path().read_text(encoding="utf-8")).get("source")
    except Exception:
        return None


# ---- a scripted MCP process world ---------------------------------------------------------------------------
class _World:
    """pid -> (server name, port). A live pid runs that server's script (or `cmd` overrides it: None = an
    unreadable command line); `created` is its start stamp (None = unreadable); `listening` = ports whose TCP
    probe answers. _terminate_pid kills the pid (and frees its port); the spawn stub records the respawn and
    makes the port answer again. Nothing real is ever probed, spawned or signalled."""

    def __init__(self, procs: dict, *, created=100.0, cmd=None, environ=None, kill_ok=True):
        self.procs = dict(procs)
        self.live = set(self.procs)
        self.listening = {port for _n, port in self.procs.values()}
        self.created = created
        self.cmd = cmd if cmd is not None else {}
        self.environ = environ or {}
        self.kill_ok = kill_ok
        self.killed, self.spawned = [], []

    def _cmdline(self, pid):
        pid = int(pid)
        if pid not in self.live:
            return None
        if pid in self.cmd:
            return self.cmd[pid]
        return _own_cmd(self.procs[pid][0])

    def _kill(self, pid, tree=False):
        self.killed.append((int(pid), tree))
        if self.kill_ok and int(pid) in self.live:
            self.live.discard(int(pid))
            self.listening.discard(self.procs[int(pid)][1])
        return self.kill_ok

    def _spawn(self, name, host, port, *, password=None, wait=None, target=None):
        self.spawned.append((name, int(port)))
        self.listening.add(int(port))
        return True

    def stubs(self) -> dict:
        created = self.created
        return dict(_pid_alive=lambda pid: int(pid) in self.live,
                    _pid_cmdline=self._cmdline,
                    _pid_create_time=lambda pid: ((created(int(pid)) if callable(created) else created)
                                                  if int(pid) in self.live else None),
                    _pid_image=lambda pid: None,
                    _proc_environ=lambda pid: self.environ.get(int(pid)),
                    is_http_up=lambda host, port, timeout=2.0: int(port) in self.listening,
                    _terminate_pid=self._kill,
                    _spawn_mcp_server=self._spawn,
                    _RESTART_WAIT_S=0.3)


def _mcp_env(pair) -> None:
    os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = str(pair[0]), str(pair[1])


def _seed_pidfile_only(name: str, port: int, pid: int) -> Path:
    """A server an OLDER build started: a per-port pid file (written now), no owner sidecar."""
    pf = P._mcp_pidfile(name, int(port))
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    P._mcp_ownerfile(name, int(port)).unlink(missing_ok=True)
    return pf


def _seed_owned(name: str, port: int, pid: int, target, *, uri: str = BASE_URI, created=100.0) -> None:
    """A server a sidecar-writing build started for `target`, with the Neo4j config `uri` (default password)."""
    env = {"NEO4J_URI": uri, "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "deepmap"}
    with _Patch(P, _pid_create_time=lambda p: created):
        _quiet(P._record_mcp_server, name, int(port), pid, str(target), env=env)


class _OkSync:
    last_verdict = None

    def sync(self, target, **k):
        self.last_verdict = "READY"
        return True


def _setup_stubs(**extra) -> dict:
    base = dict(ensure_neo4j=_Spy(True), _load=lambda name: _OkSync(), verify_search=lambda *a, **k: True,
                verify_mcp=_Spy(True), write_mcp_json=lambda *a, **k: None, _print_mcp_hint=_Spy(None))
    base.update(extra)
    return base


# =========================================================================== H1 — setup restarts pre-upgrade servers

def gate_h1_preupgrade_server_restarted() -> None:
    print("\n-- H1: a pre-upgrade server (pid file, no sidecar) on THIS project's registered port is stopped + "
          "respawned by setup's restart --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_pidfile_only(SEARCH, s, 5601)
        _seed_pidfile_only(EMBED, e, 5602)
        w = _World({5601: (SEARCH, s), 5602: (EMBED, e)})
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a))
        check("both pre-upgrade servers are stopped", sorted(p for p, _t in w.killed) == [5601, 5602],
              f"{w.killed} {out.strip()[-400:]}")
        check("... with their process TREE", all(t is True for _p, t in w.killed), str(w.killed))
        check("... their pid files are cleared", not P._mcp_pidfile(SEARCH, s).exists()
              and not P._mcp_pidfile(EMBED, e).exists())
        check("... both are respawned with the current config (a fresh server per port)",
              sorted(w.spawned) == sorted([(SEARCH, s), (EMBED, e)]), str(w.spawned))
        check("... and start_mcp_servers succeeds", ok is True, repr(ok))
        check("... it says why it restarted them (an older DeepMap build started them)",
              "older" in out.lower() and str(s) in out, out.strip()[-400:])
    # a STALE pre-upgrade pid file (its pid is gone) on the registered port while the port still answers: the stale
    # file is cleared and nothing is signalled — and (ROUND-7 I4/H1, superseding round 6's "an unmanaged listener
    # is not a blocker") setup can't attribute that listener to this project's current servers, so it is a BLOCKER
    # (the review's repro: it kept answering from another project's graph while setup said "reload now")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_pidfile_only(SEARCH, s, 5601)
        w = _World({5601: (SEARCH, s)})
        w.live.clear()
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=str(a))
        check("control: a stale pid file (dead pid) on the registered port — cleared, never signalled, and a "
              "BLOCKER (round 7)", ok is False and SEARCH in P._MCP_BLOCKED and w.killed == []
              and not P._mcp_pidfile(SEARCH, s).exists(),
              f"ok={ok} {P._MCP_BLOCKED} {w.killed} {P._mcp_pidfile(SEARCH, s).exists()}")
    # the default (heal / mcp-start) path is never disruptive for a pre-upgrade server running the CURRENT code.
    # (ROUND-9 K3 supersedes the rest: one whose command line runs OLDER code from another plugin dir IS restarted by
    # heal / mcp-start — the real v0.27.4 upgrade; tests/provision_r9_gates.py gates that and its evidence rules.)
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_pidfile_only(SEARCH, s, 5601)
        # ROUND-10 L3: "running the current code" = started AFTER that code was written (one started before an in-place
        # update of its own plugin dir is restarted — tests/provision_r10_gates.py)
        w = _World({5601: (SEARCH, s)}, created=time.time(),
                   cmd={5601: f'"{sys.executable}" "{P.HERE / P.MCP_SERVERS[SEARCH]["script"]}"'})
        with _Patch(P, **w.stubs()):
            _quiet(P.start_mcp_servers, target=str(a))
        check("control: start_mcp_servers WITHOUT restart_own (heal / mcp-start) never stops a pre-upgrade server "
              "running the current code", w.killed == [], str(w.killed))


def gate_h1_setup_never_succeeds_over_unrestartable_server() -> None:
    print("\n-- H1: setup never reports success while a DeepMap server it could not restart holds its port --")
    cases = (
        ("pre-upgrade server whose start time can't be read", dict(created=None)),
        ("pre-upgrade server that started AFTER its pid file was written", dict(created=time.time() + 10_000)),
    )
    for label, kw in cases:
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            _seed_pidfile_only(SEARCH, s, 5601)
            w = _World({5601: (SEARCH, s)}, **kw)
            with _Patch(P, **w.stubs()):
                ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a))
            check(f"{label}: never signalled", w.killed == [], str(w.killed))
            check(f"{label}: start_mcp_servers FAILS (not 'already listening')", ok is False, repr(ok))
            check(f"{label}: ... its port is not respawned over, the other server still starts",
                  (SEARCH, s) not in w.spawned and (EMBED, e) in w.spawned, str(w.spawned))
            check(f"{label}: ... the ERROR names the port, the pid and a by-hand stop",
                  "ERROR" in out and str(s) in out and "5601" in out and "by hand" in out.lower(),
                  out.strip()[-500:])
    # end to end: setup's verdict
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _clear_port_env()
        _seed_pidfile_only(SEARCH, s, 5601)
        w = _World({5601: (SEARCH, s)}, created=None)
        hint, vmcp = _Spy(None), _Spy(True)
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs(_print_mcp_hint=hint, verify_mcp=vmcp)):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("setup returns False (never success over a server serving an unknown graph)", ok is False, repr(ok))
        check("... its verdict is FAILED and names the held port", "setup verdict: FAILED" in out and str(s) in out,
              out.strip()[-500:])
        check("... no 'reload now' hint, no end-to-end verify against that server", hint.calls == [] and
              vmcp.calls == [], f"{hint.calls} {vmcp.calls}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _clear_port_env()
        _seed_pidfile_only(SEARCH, s, 5601)
        _seed_pidfile_only(EMBED, e, 5602)
        w = _World({5601: (SEARCH, s), 5602: (EMBED, e)})
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs()):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("control: setup over stoppable pre-upgrade servers succeeds (both restarted)",
              ok is True and sorted(p for p, _t in w.killed) == [5601, 5602], f"ok={ok} {w.killed}")


def gate_h1_unregistered_or_foreign_never_signalled() -> None:
    print("\n-- H1 controls: a sidecar-less server on a port NOT registered to this project / another project's "
          "server / a pid whose environment names another project is never signalled --")
    # (a) env-override ports the registry does not give this project
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        _mcp_env((8801, 8802))
        _seed_pidfile_only(SEARCH, 8801, 5601)
        w = _World({5601: (SEARCH, 8801)})
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a))
        check("unregistered port: never signalled", w.killed == [], str(w.killed))
        check("... and setup can't call it this project's server: start FAILS (unknown config)", ok is False,
              repr(ok))
    # (a2) ... a DEAD pid there is only a stale file — never signalled — but the listener still answering on the port
    # can't be attributed to this project's current servers: a blocker too (ROUND-7 I4/H1; round 6 let it pass)
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        _mcp_env((8801, 8802))
        _seed_pidfile_only(SEARCH, 8801, 5601)
        w = _World({5601: (SEARCH, 8801)})
        w.live.clear()
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=str(a))
        check("control: a stale pid file (dead pid) on an unregistered port — never signalled, a BLOCKER (round 7)",
              ok is False and SEARCH in P._MCP_BLOCKED and w.killed == [], f"ok={ok} {P._MCP_BLOCKED} {w.killed}")
    # (b) another project's server (its sidecar names b) on this project's port
    with _Sandbox() as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_owned(SEARCH, s, 5601, b)
        w = _World({5601: (SEARCH, s)})
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a))
        check("another project's server: never signalled", w.killed == [], str(w.killed))
        check("... and never reported as this project's (start fails with the foreign-owner WARNING)",
              ok is False and "b" in out, f"ok={ok} {out.strip()[-300:]}")
    # (c) the stop goes through stop_mcp_server WITH the project: a process whose environment says another
    #     project's server is never signalled (the Linux /proc environ evidence)
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_pidfile_only(SEARCH, s, 5601)
        w = _World({5601: (SEARCH, s)}, environ={5601: {"DEEPMAP_PROJECT_KEY": "0therproj0k3y"}})
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=str(a))
        check("environment names another project: never signalled", w.killed == [], str(w.killed))
        check("... and setup does not succeed over it", ok is False, repr(ok))


def gate_h1_own_sidecar_unverifiable() -> None:
    print("\n-- H1: this project's OWN server (sidecar) that can't be verified: kept when its recorded config is "
          "current, a blocker when it is provably stale --")
    for label, uri, want_ok in (("recorded config == the current one", BASE_URI, True),
                                ("recorded config is a DIFFERENT Neo4j (stale)", "bolt://localhost:7699", False)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            os.environ["NEO4J_URI"] = BASE_URI
            _seed_owned(SEARCH, s, 5601, a, uri=uri)
            w = _World({5601: (SEARCH, s)}, cmd={5601: None})        # command line unreadable: not provable
            with _Patch(P, **w.stubs()):
                ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a), password="deepmap")
            check(f"{label}: never signalled", w.killed == [], str(w.killed))
            check(f"{label}: start_mcp_servers -> {want_ok}", ok is want_ok, f"ok={ok} {out.strip()[-300:]}")
    # ... when the recorded start stamp PROVES the pid is another process by now, the pid file is stale: nothing is
    # signalled — and the listener on the port, which setup can't attribute to this project's CURRENT config, is a
    # blocker (ROUND-7 I4/H1; round 6 called it an unmanaged listener and let setup succeed over it)
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        os.environ["NEO4J_URI"] = BASE_URI
        _seed_owned(SEARCH, s, 5601, a, uri="bolt://localhost:7699", created=100.0)
        w = _World({5601: (SEARCH, s)}, created=900.0)                 # the live pid 5601 started at another time
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=str(a), password="deepmap")
        check("stale sidecar (the recorded stamp proves the pid is another process): never signalled, a BLOCKER "
              "(round 7)", ok is False and SEARCH in P._MCP_BLOCKED and w.killed == [],
              f"ok={ok} {P._MCP_BLOCKED} {w.killed}")


def gate_h1_heal_text_names_setup_remedy() -> None:
    print("\n-- H1: heal's report of an unverified server says /deep-map:setup restarts it --")
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        s, e = PR.lookup_ports(a)
        _seed_pidfile_only(SEARCH, s, 5601)
        w = _World({5601: (SEARCH, s)})
        with _Patch(P, **w.stubs()), _Patch(P, start_mcp_servers=_Spy(True), _ensure_mcp_json=lambda t: None,
                                             ensure_neo4j=_Spy(True)):
            rc, out = _captured(P.main, ["heal", str(a)])
        rec = _status(home, a)
        reason = str(rec.get("reason"))
        check("heal still never signals a sidecar-less server (unattended: setup is the deliberate verb)",
              w.killed == [], str(w.killed))
        check("... the reason says /deep-map:setup restarts it", "/deep-map:setup" in reason
              and "restart" in reason.lower() and s in (rec.get("mcp_unverified") or []), reason[-400:])


# =========================================================================== H2 — the live owner before the record

def gate_h2_real_owner_acting_first_keeps_its_store() -> None:
    print("\n-- H2: the store's REAL owner, acting first after the upgrade, re-adopts its own base store --")
    with _Sandbox(bolt="up") as (home, work):
        x, y = _mk_project(work, "x"), _mk_project(work, "y")
        _claim_base(x)                   # the <= v0.27.4 mis-adoption of y's store
        PR.allocate_ports(y)             # y: an MCP pair only (a pre-v0.26 entry)
        _legacy_store()
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _, out = _captured(P._resolve_ports, str(y), allocate=True)
        got = PR.lookup_neo4j_ports(y)
        check("y keeps its own store: registered on the base pair (no new instance, no rebuild elsewhere)",
              got == BASE, f"{got} {out.strip()[-300:]}")
        check("... NEO4J_URI -> the base", os.environ.get("NEO4J_URI") == PR.bolt_uri(PR.BASE_BOLT),  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
              str(os.environ.get("NEO4J_URI")))
        check("... the live owner is recorded next to the store (the proof the route + the claimant's heal use)",
              _record_names(y), str(P._base_owner_root()))
        check("... so the claimant x's claim now reads as a foreign claim (heal / setup refuse / release it)",
              bool(P.foreign_base_claim_problem(str(x))), str(P.foreign_base_claim_problem(str(x))))
    # controls
    for label, owner_of, alive, want in (
            ("the live owner is the claimant x (a legitimate claim)", "x", True, "own"),
            ("the base is a Neo4j that is NOT DeepMap's own instance (accepts our credentials)", "y", False, "own")):
        with _Sandbox(bolt="up") as (home, work):
            x, y = _mk_project(work, "x"), _mk_project(work, "y")
            _claim_base(x)
            PR.allocate_ports(y)
            _legacy_store()
            owner = _ident(x if owner_of == "x" else y)
            with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=owner, nodes=9)),
                        _legacy_base_instance_alive=lambda _a=alive: _a):
                _quiet(P._resolve_ports, str(y), allocate=True)
            got = PR.lookup_neo4j_ports(y)
            check(f"control ({label}): y gets its OWN instance", got is not None and got != BASE, str(got))
            if alive:
                check(f"control ({label}): ... the live owner x is recorded", _record_names(x), str(P._base_owner_root()))
            else:
                check(f"control ({label}): ... nothing is recorded (it says nothing about the store on disk)",
                      P._base_owner_root() is None, str(P._base_owner_root()))


def gate_h2_lookup_route_flips_for_real_owner() -> None:
    print("\n-- H2 (lookup side): heal serves the real owner's store without a rebuild once its live owner is read --")
    with _Sandbox(bolt="up") as (home, work):
        x, y = _mk_project(work, "x"), _mk_project(work, "y", marker=True)
        _claim_base(x)
        PR.allocate_ports(y)
        _legacy_store()
        mcp = _Spy(True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                    _legacy_base_instance_alive=lambda: True, start_mcp_servers=mcp,
                    _ensure_mcp_json=lambda t: None, ensure_neo4j=_Spy(True)):
            rc, out = _captured(P.main, ["heal", str(y)])
        rec = _status(home, y)
        check("heal (y = the live owner): served — rc 0, MCP started, neo4j_ok", rc == 0 and len(mcp.calls) == 1
              and rec.get("neo4j_ok") is True, f"rc={rc} {mcp.calls} {rec} {out.strip()[-300:]}")
        check("... the owner is recorded (the CLI / hooks' route resolves to the base too)",
              _record_names(y) and PR.resolve_neo4j_route(str(y), honor_env=False).source == "legacy-unverified",
              f"{P._base_owner_root()} {PR.resolve_neo4j_route(str(y), honor_env=False)}")
    with _Sandbox(bolt="up") as (home, work):
        x, y = _mk_project(work, "x"), _mk_project(work, "y", marker=True)
        _claim_base(x)
        PR.allocate_ports(y)
        _legacy_store()
        mcp = _Spy(True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(x), nodes=9)),
                    _legacy_base_instance_alive=lambda: True, start_mcp_servers=mcp,
                    _ensure_mcp_json=lambda t: None, ensure_neo4j=_Spy(True)):
            rc, out = _captured(P.main, ["heal", str(y)])
        rec = _status(home, y)
        check("control (the live owner is x): y is still refused a route — nothing started, names /deep-map:setup",
              rc != 0 and mcp.calls == [] and "/deep-map:setup" in str(rec.get("reason")), f"rc={rc} {rec}")


# =========================================================================== H3 — legacy single-project users

def _binary_present() -> None:
    """The shared Neo4j binary is already extracted (a legacy store implies it was run from there)."""
    nh = P.paths(None)["neo4j_home"]
    nh.mkdir(parents=True, exist_ok=True)
    (nh / ".deepmap-extracted").write_text(P.neo4j_version(), encoding="utf-8")


def _ensure_world(sb, *, ok=True, started=True, up_after=True):
    """An ensure_neo4j stub: the legacy store comes up (sb.bolt -> 'up') and says it started it."""
    calls = []

    def _ensure(**k):
        calls.append(k)
        if started and isinstance(k.get("diag"), dict):
            k["diag"]["started"] = True
        if up_after:
            sb.bolt = "up"
        return ok
    return _ensure, calls


def _stop_world(sb):
    def _stop(version=None, project_key=None):
        sb.bolt = "down"
        return True
    return _Spy(_stop)


def gate_h3a_heal_records_verified_legacy_owner() -> None:
    print("\n-- H3(a): heal's verified legacy route RECORDS the owner (the proof a later down-store setup needs) --")
    with _Sandbox(bolt="up") as (home, work):
        y = _mk_project(work, "y", marker=True)
        PR.allocate_ports(y)                     # a <= v0.25 shape: MCP pair only, graph on the base store
        _legacy_store()
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                    _legacy_base_instance_alive=lambda: True, start_mcp_servers=_Spy(True),
                    _ensure_mcp_json=lambda t: None):
            rc, out = _captured(P.main, ["heal", str(y)])
        check("heal serves the legacy store (rc 0)", rc == 0, f"rc={rc} {out.strip()[-300:]}")
        check("... and records this project as the store's owner", _record_names(y), str(P._base_owner_root()))
        check("... from a live owner read", _record_source() == "owner-read", str(_record_source()))
    for label, owner_self, nodes, alive in (("an unowned EMPTY graph (no proof of ownership)", False, 0, True),
                                            ("not DeepMap's own running base instance", True, 9, False)):
        with _Sandbox(bolt="up") as (home, work):
            y = _mk_project(work, "y", marker=True)
            PR.allocate_ports(y)
            _legacy_store()
            with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=(_ident(y) if owner_self else None),
                                                                  nodes=nodes)),
                        _legacy_base_instance_alive=lambda _a=alive: _a, start_mcp_servers=_Spy(True),
                        _ensure_mcp_json=lambda t: None):
                rc = _quiet(P.main, ["heal", str(y)])
            check(f"control ({label}): served (rc 0) but NOTHING recorded", rc == 0 and P._base_owner_root() is None,
                  f"rc={rc} {P._base_owner_root()}")


def gate_h3b_setup_starts_down_legacy_store() -> None:
    print("\n-- H3(b): setup starts a DOWN legacy store (no pair, no record, ports free) to read its owner --")
    # (1) the store is this project's -> adopted, kept running, recorded
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        y = _mk_project(work, "y")
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb)
        stop = _stop_world(sb)
        with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _, out = _captured(P._resolve_ports, str(y), allocate=True)
        got = PR.lookup_neo4j_ports(y)
        check("the legacy store is STARTED (the legacy layout, revive-only: create=False)",
              len(calls) == 1 and calls[0].get("project_key") is None and calls[0].get("create") is False,
              f"{calls} {out.strip()[-300:]}")
        check("... its owner is this project -> ADOPTED (the base pair), never a new instance", got == BASE, str(got))
        check("... kept running (not stopped)", stop.calls == [], str(stop.calls))
        check("... the owner is recorded", _record_names(y), str(P._base_owner_root()))
        check("... and it says what it did", "7687" in out, out.strip()[-300:])
    # (2) another project's store / unowned+populated -> stopped again, own instance
    for label, owner_of, nodes, recorded in (("another project's store", "x", 9, "x"),
                                             ("an unowned POPULATED store (no proof)", None, 5, None)):
        sb = _Sandbox(bolt="down")
        with sb as (home, work):
            x, y = _mk_project(work, "x"), _mk_project(work, "y")
            PR.allocate_ports(y)
            _legacy_store()
            _binary_present()
            ens, calls = _ensure_world(sb)
            stop = _stop_world(sb)
            owner = _ident(x) if owner_of == "x" else None
            with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                        _make_driver=_driver_factory(_GraphDrv(owner=owner, nodes=nodes)),
                        _legacy_base_instance_alive=lambda: True):
                _quiet(P._resolve_ports, str(y), allocate=True)
            got = PR.lookup_neo4j_ports(y)
            check(f"{label}: started to read the owner", len(calls) == 1, str(calls))
            check(f"{label}: ... stopped again (left as found)",
                  any(k.get("project_key", "x") is None for _a, k in stop.calls), str(stop.calls))
            check(f"{label}: ... this project gets its OWN instance", got is not None and got != BASE, str(got))
            if recorded:
                check(f"{label}: ... the owner x is recorded (no second start next time)", _record_names(x),
                      str(P._base_owner_root()))
            else:
                check(f"{label}: ... nothing recorded", P._base_owner_root() is None, str(P._base_owner_root()))
    # (3) an unowned EMPTY store -> adopted (the live branch's rule)
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        y = _mk_project(work, "y")
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb)
        stop = _stop_world(sb)
        with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                    _make_driver=_driver_factory(_GraphDrv(owner=None, nodes=0)),
                    _legacy_base_instance_alive=lambda: True):
            _quiet(P._resolve_ports, str(y), allocate=True)
        check("an unowned EMPTY store: adopted, not stopped", PR.lookup_neo4j_ports(y) == BASE and stop.calls == [],
              f"{PR.lookup_neo4j_ports(y)} {stop.calls}")
    # (4) a mis-claim by x + y the real owner, the store DOWN, no record: y's setup reads it and re-adopts it
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        x, y = _mk_project(work, "x"), _mk_project(work, "y")
        _claim_base(x)
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb)
        stop = _stop_world(sb)
        with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _quiet(P._resolve_ports, str(y), allocate=True)
        check("x mis-claims y's DOWN store: y's setup starts it, reads y, re-adopts it (G4 + H2 + H3)",
              len(calls) == 1 and PR.lookup_neo4j_ports(y) == BASE and stop.calls == [] and _record_names(y),
              f"{calls} {PR.lookup_neo4j_ports(y)} {stop.calls} {P._base_owner_root()}")


def gate_h3b_never_starts_without_cause() -> None:
    print("\n-- H3(b) controls: nothing is started when the owner is known, the ports are held, the binary is "
          "absent, the project has a pair / its own instance, or DEEPMAP_NO_BASE_ADOPT=1 --")

    def _run(label, *, prep=None, occupied=(), binary=True, env=None, want_base=False):
        sb = _Sandbox(bolt="down", occupied=occupied)
        with sb as (home, work):
            x, y = _mk_project(work, "x"), _mk_project(work, "y")
            PR.allocate_ports(y)
            _legacy_store()
            if binary:
                _binary_present()
            if prep:
                prep(x, y)
            for k, v in (env or {}).items():
                os.environ[k] = v
            ens, calls = _ensure_world(sb)
            stop = _stop_world(sb)
            try:
                with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                            _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9))):
                    _quiet(P._resolve_ports, str(y), allocate=True)
            finally:
                for k in (env or {}):
                    os.environ.pop(k, None)
            got = PR.lookup_neo4j_ports(y)
            check(f"{label}: nothing started or stopped", calls == [] and stop.calls == [], f"{calls} {stop.calls}")
            if want_base:
                check(f"{label}: ... adopts the base as before", got == BASE, str(got))
            else:
                check(f"{label}: ... defers as before (own instance)", got is not None and got != BASE, str(got))
    _run("an owner record already names another project",
         prep=lambda x, y: P._write_base_owner_record(_ident(x)))
    _run("an owner record names THIS project (the down branch adopts on the file)",
         prep=lambda x, y: P._write_base_owner_record(_ident(y)), want_base=True)
    _run("the base ports are held by another program", occupied=(7687, 7474))
    _run("the shared binary is not extracted (never a download just to read an owner)", binary=False)
    _run("DEEPMAP_NO_BASE_ADOPT=1 (a benchmark clone)", env={"DEEPMAP_NO_BASE_ADOPT": "1"})

    def _own_conf(x, y):
        key = PR.project_key(y)
        conf = P.paths(None, key)["conf"]
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("server.bolt.listen_address=:7695\nserver.http.listen_address=:7482\n", encoding="utf-8")
    _run("the project's own instance still exists (a lost registry entry: it is re-adopted)", prep=_own_conf)


def gate_h3b_start_failures_leave_store_as_found() -> None:
    print("\n-- H3(b): a start that failed / was a concurrent one — the store is stopped only if setup started it --")
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        y = _mk_project(work, "y")
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb, ok=False, up_after=False)      # e.g. the store rejects our credentials
        stop = _stop_world(sb)
        with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop):
            _quiet(P._resolve_ports, str(y), allocate=True)
        check("start failed after launching: stopped again, own instance",
              len(calls) == 1 and any(k.get("project_key", "x") is None for _a, k in stop.calls)
              and PR.lookup_neo4j_ports(y) not in (None, BASE), f"{calls} {stop.calls} {PR.lookup_neo4j_ports(y)}")
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        x, y = _mk_project(work, "x"), _mk_project(work, "y")
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb, started=False)                  # a concurrent setup started it
        stop = _stop_world(sb)
        with _Patch(P, ensure_neo4j=ens, stop_neo4j=stop,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(x), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _quiet(P._resolve_ports, str(y), allocate=True)
        check("started by someone else (another project's store): NOT stopped by this setup, own instance",
              stop.calls == [] and PR.lookup_neo4j_ports(y) not in (None, BASE), f"{stop.calls} {PR.lookup_neo4j_ports(y)}")


def gate_h3b_setup_end_to_end_legacy_user() -> None:
    print("\n-- H3(b) end to end: the legacy user's /deep-map:setup after a reboot keeps its own store --")
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        y = _mk_project(work, "y")
        PR.allocate_ports(y)
        _legacy_store()
        _binary_present()
        ens, calls = _ensure_world(sb)
        stop = _stop_world(sb)
        with _Patch(P, **_setup_stubs(ensure_neo4j=ens, stop_neo4j=stop, start_mcp_servers=_Spy(True),
                                      _make_driver=_driver_factory(_GraphDrv(owner=_ident(y), nodes=9)),
                                      _legacy_base_instance_alive=lambda: True)):
            ok, out = _captured(P.setup, str(y), password="deepmap")
        check("setup succeeds on the project's OWN legacy store (base pair, never stopped)",
              ok is True and PR.lookup_neo4j_ports(y) == BASE and stop.calls == [],
              f"ok={ok} {PR.lookup_neo4j_ports(y)} {stop.calls} {out.strip()[-300:]}")
        check("... the first start is the owner read (create=False), setup's own ensure then finds it up",
              len(calls) >= 1 and calls[0].get("create") is False, str(calls))


# =========================================================================== H4 — non-policy legacy failure wording

class _RateLimit(Exception):
    code = "Neo.ClientError.Security.AuthenticationRateLimit"


def gate_h4_legacy_failure_texts_not_refusals() -> None:
    print("\n-- H4: heal's legacy connect / credentials failure texts are not refusal-worded --")
    check("precondition: the hooks' word lists are importable", HS is not None and hasattr(HS, "_reason_is_refusal"))
    if HS is None:
        return
    with _Sandbox() as (home, work):
        repo = _mk_project(work, "proj")
        for name, exc, state in (("unreachable", _neo4j_exc("ServiceUnavailable"), "down"),
                                 ("rejected credentials", _neo4j_exc("AuthError"), "auth"),
                                 ("login lock", _RateLimit("too many times in a row"), "auth")):
            with _Patch(P, _make_driver=_driver_factory(_GraphDrv(exc=exc))):
                st, text = _quiet(P._legacy_route_verdict, repo, BASE_URI, "neo4j", "deepmap")
            check(f"{name}: state {state!r}", st == state, f"{st} {text}")
            check(f"{name}: not refusal-worded (hooks' _REFUSAL_WORDS)", bool(text) and not HS._reason_is_refusal(text),
                  str(text)[:300])
            check(f"{name}: still names /deep-map:setup", "/deep-map:setup" in str(text), str(text)[:300])
            if state == "auth":
                check(f"{name}: says 'credentials' (the hooks' _AUTH_WORDS: never superseded by a port probe)",
                      any(w in str(text).lower() for w in HS._AUTH_WORDS), str(text)[:300])
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=str(work / "other"), nodes=4))):
            st, text = _quiet(P._legacy_route_verdict, repo, BASE_URI, "neo4j", "deepmap")
        check("control: the POLICY refusal text is still recognised by its wording",
              st == "refused" and HS._reason_is_refusal(text), f"{st} {text}")
    # end to end: heal's status for a legacy owner read that fails is a plain failure, not a refusal
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        with _Patch(P, start_mcp_servers=_Spy(True), _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(c)])
        rec = _status(home, c)
        check("heal status: refused false, kind null, reason not refusal-worded",
              rc != 0 and rec.get("refused") is False and rec.get("refusal_kind") is None
              and not HS._reason_is_refusal(rec.get("reason")), f"rc={rc} {rec}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-6 gates (hermetic): H1 pre-upgrade servers, H2 live owner, H3 legacy users,")
    print("          H4 wording")
    print("=" * 68)
    for g in (gate_h1_preupgrade_server_restarted, gate_h1_setup_never_succeeds_over_unrestartable_server,
              gate_h1_unregistered_or_foreign_never_signalled, gate_h1_own_sidecar_unverifiable,
              gate_h1_heal_text_names_setup_remedy,
              gate_h2_real_owner_acting_first_keeps_its_store, gate_h2_lookup_route_flips_for_real_owner,
              gate_h3a_heal_records_verified_legacy_owner, gate_h3b_setup_starts_down_legacy_store,
              gate_h3b_never_starts_without_cause, gate_h3b_start_failures_leave_store_as_found,
              gate_h3b_setup_end_to_end_legacy_user,
              gate_h4_legacy_failure_texts_not_refusals):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
