#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-5 provisioner fixes (build/provision.py) — the HEAL side of
binding decision G2 ("policy refusals are FINAL until re-checked by policy, never superseded by a port probe"):

  G2a heal's status file (<DEEPMAP_HOME>/run/heal-<key>.json) marks a POLICY refusal: `"refused": true` +
      `"refusal_kind": "foreign-base-claim"` (a <= v0.27.4 mis-adopted base claim whose store is provably
      another project's — by its owner record, or by the live :_DeepMapMeta owner, whether or not heal had to
      start the store to read it) or `"legacy-owner"` (the legacy 7687 route whose live graph is not this
      project's). Every other outcome — healthy, a down / unreachable store, a credentials rejection, a
      failed build, no route at all — writes `"refused": false, "refusal_kind": null` (a port probe CAN
      disprove those, so the hooks may re-check them). A later heal never carries a refusal forward.
  G2b when heal refuses (either kind) it also STOPS this project's own MCP servers whose owner sidecar records
      the refused Neo4j URI (a v0.27.4 heal left them serving the other project's graph, and the hooks'
      port probe then read "up" as "healthy"). Owner-verified ONLY — the sidecar names this project (or one of
      its migrated keys) and the very live process it recorded (server script + creation time). Never
      stopped: a server of ANOTHER project, one whose sidecar points at a different Neo4j, one with NO
      sidecar (an older build's — it is reported instead: the reason warns that deepmap_search / impact may
      still answer from that graph), a recycled / re-started pid; and nothing is stopped on a non-policy
      failure or on a route-less project (e.g. a project another project's claim shadows — it may be the real
      owner whose servers are fine).
  W3  provision.foreign_base_claim_problem keeps its signature (target, uri=None) — the CLI (G1) calls it.

Reuses the round-4 gates' hermetic harness (tests/provision_round4_gates.py — same owner): DEEPMAP_HOME -> a
throwaway dir; port / bolt probes, drivers, pid liveness / command line / creation time, Popen and every kill
are stubbed. NO Neo4j, NO processes, NO network. Exit 0 = GREEN.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# the shared hermetic harness (importing it also puts build/ on sys.path)
from provision_round4_gates import (P, PR, _Patch, _Spy, _Sandbox, _GraphDrv, _driver_factory,  # noqa: E402
                                    _mk_project, _claim_base, _legacy_store, _ident, _own_cmd, _captured,
                                    _quiet)

_PASS = 0
_FAIL = 0

BASE_URI = "bolt://localhost:7687"
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
_PIDS = {SEARCH: 5601, EMBED: 5602}


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


def _seed_server(name: str, port: int, target, *, uri: str = BASE_URI, pid: int | None = None,
                 created: float = 250.0) -> int:
    """A running server's per-port pid file + owner sidecar exactly as _record_mcp_server writes them."""
    pid = pid or _PIDS[name]
    env = {"NEO4J_URI": uri, "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "deepmap"}
    with _Patch(P, _pid_create_time=lambda p: created):
        _quiet(P._record_mcp_server, name, int(port), pid, str(target), env=env)
    return pid


def _seed_pidfile_only(name: str, port: int, pid: int) -> None:
    """A server an OLDER build started: a per-port pid file, no owner sidecar."""
    pf = P._mcp_pidfile(name, int(port))
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    P._mcp_ownerfile(name, int(port)).unlink(missing_ok=True)


def _procs(live: dict, created: float = 250.0, listening=None):
    """Process + port probes: `live` = {pid: server name} are alive and run the server script; every other pid
    is gone. `listening` = ports whose TCP probe answers (default: none)."""
    ports = set(listening or ())
    return dict(_pid_alive=lambda pid: int(pid) in live,
                _pid_cmdline=lambda pid: (_own_cmd(live[int(pid)]) if int(pid) in live else None),
                _pid_create_time=lambda pid: (created if int(pid) in live else None),
                is_http_up=lambda host, port, timeout=2.0: int(port) in ports)


def _heal(target, **stubs):
    """`provision.py heal <target>` with MCP start / .mcp.json writes stubbed. -> (rc, output)."""
    base = dict(start_mcp_servers=_Spy(True), _ensure_mcp_json=lambda t: None)
    base.update(stubs)
    with _Patch(P, **base):
        return _captured(P.main, ["heal", str(target)])


def _gone(name: str, port: int) -> bool:
    return not P._mcp_pidfile(name, int(port)).exists() and not P._mcp_ownerfile(name, int(port)).exists()


def _killed(kill: _Spy) -> list:
    return sorted(int(a[0]) for a, _k in kill.calls)


# =========================================================================== G2a — the status file marks refusals

def gate_g2a_foreign_base_claim_record() -> None:
    print("\n-- G2a: a base claim whose OWNER RECORD names another project -> refused, kind foreign-base-claim --")
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        rc, out = _heal(a, ensure_neo4j=_Spy(True))
        rec = _status(home, a)
        check("heal refuses (rc != 0, neo4j_ok false, reason names /deep-map:setup)",
              rc != 0 and rec.get("neo4j_ok") is False and "/deep-map:setup" in str(rec.get("reason")),
              f"rc={rc} {rec}")
        check("status: refused = true", rec.get("refused") is True, str(rec))
        check("status: refusal_kind = 'foreign-base-claim'", rec.get("refusal_kind") == "foreign-base-claim",
              str(rec.get("refusal_kind")))


def gate_g2a_foreign_base_claim_live() -> None:
    print("\n-- G2a: a base claim with NO record whose LIVE owner is another project -> refused, foreign-base-claim --")
    # (a) the base was already running
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        rc, out = _heal(a, ensure_neo4j=_Spy(True), stop_neo4j=_Spy(True),
                        _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                        _legacy_base_instance_alive=lambda: False)
        rec = _status(home, a)
        check("base up, live owner b: refused = true, kind foreign-base-claim",
              rc != 0 and rec.get("refused") is True and rec.get("refusal_kind") == "foreign-base-claim",
              f"rc={rc} {rec}")
    # (b) heal had to start the base to read its owner (it is stopped again)
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()

        def _ensure(**k):
            sb.bolt = "up"
            return True
        stop = _Spy(True)
        rc, out = _heal(a, ensure_neo4j=_Spy(_ensure), stop_neo4j=stop,
                        _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                        _legacy_base_instance_alive=lambda: True)
        rec = _status(home, a)
        check("base started only to read the owner: refused = true, kind foreign-base-claim (store stopped again)",
              rc != 0 and rec.get("refused") is True and rec.get("refusal_kind") == "foreign-base-claim"
              and any(k.get("project_key", "x") is None for _, k in stop.calls), f"rc={rc} {rec} {stop.calls}")


def gate_g2a_legacy_owner() -> None:
    print("\n-- G2a: the LEGACY 7687 route whose live graph is another project's -> refused, kind legacy-owner --")
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)                       # an MCP pair of its own; NO Neo4j pair -> legacy 7687
        rc, out = _heal(c, ensure_neo4j=_Spy(True),
                        _make_driver=_driver_factory(_GraphDrv(owner=str(work / "other-proj"), nodes=40)))
        rec = _status(home, c)
        check("refused = true, kind legacy-owner; the reason names the other project + /deep-map:setup",
              rc != 0 and rec.get("refused") is True and rec.get("refusal_kind") == "legacy-owner"
              and "other-proj" in str(rec.get("reason")) and "/deep-map:setup" in str(rec.get("reason")),
              f"rc={rc} {rec}")
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        rc, out = _heal(c, ensure_neo4j=_Spy(True), _make_driver=_driver_factory(_GraphDrv(owner=None, nodes=40)))
        rec = _status(home, c)
        check("an unowned but POPULATED legacy graph is a policy refusal too (kind legacy-owner)",
              rc != 0 and rec.get("refused") is True and rec.get("refusal_kind") == "legacy-owner", f"rc={rc} {rec}")


def gate_g2a_non_refusals_are_not_marked() -> None:
    print("\n-- G2a controls: healthy / down / credentials / failed-build / no-route outcomes are NOT refusals --")
    cases = []
    # healthy: own per-project instance up, servers started
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        rc, _ = _heal(a, ensure_neo4j=_Spy(True))
        cases.append(("healthy heal", rc == 0, _status(home, a)))
    # own instance unreachable
    with _Sandbox(bolt="down") as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        rc, _ = _heal(a, ensure_neo4j=_Spy(False))
        cases.append(("own instance unreachable", rc != 0, _status(home, a)))
    # legacy route: base down (its owner can't be read — a port probe CAN disprove "down")
    with _Sandbox(bolt="down") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        rc, _ = _heal(c, ensure_neo4j=_Spy(True))
        cases.append(("legacy route, base down", rc != 0, _status(home, c)))
    # legacy route: the bolt port answers but the owner READ fails (verdict "down" — an outage, not a policy)
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        rc, _ = _heal(c, ensure_neo4j=_Spy(True))          # the sandbox driver raises ServiceUnavailable
        cases.append(("legacy route, owner read fails (outage)", rc != 0, _status(home, c)))
    # legacy route: credentials rejected
    with _Sandbox(bolt="auth") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        rc, _ = _heal(c, ensure_neo4j=_Spy(True))
        cases.append(("legacy route, credentials rejected", rc != 0, _status(home, c)))
    # last build FAILED
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a")
        (a / ".deepmap").mkdir(parents=True, exist_ok=True)
        (a / ".deepmap" / "graph_build.json").write_text('{"verdict": "FAILED", "nodes": 0}', encoding="utf-8")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        rc, _ = _heal(a, ensure_neo4j=_Spy(True))
        cases.append(("last build FAILED", rc != 0, _status(home, a)))
    for label, rc_ok, rec in cases:
        check(f"{label}: refused = false, refusal_kind = null (keys present)",
              rc_ok and rec.get("refused") is False and "refusal_kind" in rec and rec.get("refusal_kind") is None,
              str(rec))


def gate_g2a_writer_contract() -> None:
    print("\n-- G2a: write_heal_status persists the refusal fields, validates the kind, never carries a refusal --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        P.write_heal_status(str(a), {"neo4j_ok": False, "mcp_ok": False, "reason": "x — run /deep-map:setup",
                                     "refused": True, "refusal_kind": "legacy-owner"})
        rec = _status(home, a)
        check("refused + refusal_kind persisted", rec.get("refused") is True and rec.get("refusal_kind") == "legacy-owner",
              str(rec))
        P.write_heal_status(str(a), {"neo4j_ok": False, "mcp_ok": False, "reason": "y", "refused": True,
                                     "refusal_kind": "made-up"})
        rec = _status(home, a)
        check("an unknown kind is never persisted as a kind (refused kept, kind null)",
              rec.get("refused") is True and rec.get("refusal_kind") is None, str(rec))
        P.write_heal_status(str(a), {"neo4j_ok": False, "mcp_ok": False, "reason": "z", "refused": False,
                                     "refusal_kind": "legacy-owner"})
        rec = _status(home, a)
        check("a kind without refused=true is dropped (refused false, kind null)",
              rec.get("refused") is False and rec.get("refusal_kind") is None, str(rec))
        P.write_heal_status(str(a), {"neo4j_ok": False, "mcp_ok": False, "reason": "r", "refused": True,
                                     "refusal_kind": "foreign-base-claim"})
        P.write_heal_status(str(a), {"neo4j_ok": True, "mcp_ok": True, "reason": None, "mcp_started": []})
        rec = _status(home, a)
        check("a later healthy heal is NOT a refusal (nothing carried forward)",
              rec.get("refused") is False and rec.get("refusal_kind") is None and rec.get("neo4j_ok") is True,
              str(rec))


# =========================================================================== G2b — owner-verified stop on refusal

def gate_g2b_stops_own_servers_on_foreign_claim() -> None:
    print("\n-- G2b: heal refusing a foreign base claim stops THIS project's servers that point at that store --")
    # (1) the record path (nothing is started; the base stays down)
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        s, e = PR.lookup_ports(a)
        _seed_server(SEARCH, s, a)
        _seed_server(EMBED, e, a)
        kill, mcp = _Spy(True), _Spy(True)
        rc, out = _heal(a, ensure_neo4j=_Spy(True), start_mcp_servers=mcp, _terminate_pid=kill,
                        **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        rec = _status(home, a)
        check("record path: both of THIS project's servers are stopped (owner-verified)",
              _killed(kill) == sorted(_PIDS.values()), f"{kill.calls} {out.strip()[-300:]}")
        check("... with their process TREE (a venv launcher's child is the real server)",
              all(k.get("tree") is True for _a, k in kill.calls), str(kill.calls))
        check("... their pid files + owner sidecars are cleared", _gone(SEARCH, s) and _gone(EMBED, e))
        check("... nothing is started", mcp.calls == [], str(mcp.calls))
        check("... the status lists them (mcp_stopped) and stays a refusal",
              sorted(rec.get("mcp_stopped") or []) == sorted([SEARCH, EMBED]) and rec.get("refused") is True,
              str(rec))
        check("... the reason says heal stopped this project's MCP servers",
              "stopped" in str(rec.get("reason")).lower() and "/deep-map:setup" in str(rec.get("reason")),
              str(rec.get("reason")))
    # (2) the live path (no record; the base already runs)
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        s, e = PR.lookup_ports(a)
        _seed_server(SEARCH, s, a)
        _seed_server(EMBED, e, a, uri="bolt://127.0.0.1:7687")        # the same endpoint, spelled differently
        kill = _Spy(True)
        rc, out = _heal(a, ensure_neo4j=_Spy(True), stop_neo4j=_Spy(True), _terminate_pid=kill,
                        _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                        _legacy_base_instance_alive=lambda: False,
                        **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        check("live path: both servers stopped (127.0.0.1:7687 is the same store as localhost:7687)",
              _killed(kill) == sorted(_PIDS.values()), f"{kill.calls} {out.strip()[-300:]}")


def gate_g2b_stops_own_servers_on_legacy_owner() -> None:
    print("\n-- G2b: heal refusing the legacy route (another project's graph) stops this project's servers on it --")
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        s, e = PR.allocate_ports(c)
        _seed_server(SEARCH, s, c)
        _seed_server(EMBED, e, c)
        kill = _Spy(True)
        rc, out = _heal(c, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                        _make_driver=_driver_factory(_GraphDrv(owner=str(work / "other-proj"), nodes=40)),
                        **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        rec = _status(home, c)
        check("both servers stopped; refusal kind legacy-owner",
              _killed(kill) == sorted(_PIDS.values()) and rec.get("refusal_kind") == "legacy-owner",
              f"{kill.calls} {rec}")


def gate_g2b_never_stops_unverified() -> None:
    print("\n-- G2b controls: only an owner-verified server pointing at the refused store is ever stopped --")
    # (a) a different Neo4j in the sidecar / another project's sidecar / a recycled pid / a re-started pid
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        PR.allocate_ports(b)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        s, e = PR.lookup_ports(a)
        _seed_server(SEARCH, s, a, uri="bolt://localhost:7699")     # this project's, but NOT on the refused store
        _seed_server(EMBED, e, b)                                   # ANOTHER project's server on A's port
        kill = _Spy(True)
        rc, out = _heal(a, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                        **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        rec = _status(home, a)
        check("a server whose sidecar points at ANOTHER Neo4j is not stopped", _PIDS[SEARCH] not in _killed(kill),
              str(kill.calls))
        check("another project's server (sidecar names b) is not stopped", _PIDS[EMBED] not in _killed(kill),
              str(kill.calls))
        check("... their files are kept", not _gone(SEARCH, s) and not _gone(EMBED, e))
        check("... and neither is reported as an unverified server on the refused store",
              not rec.get("mcp_unverified") and "could not verify" not in str(rec.get("reason")), str(rec))
    for label, live, created in (("a recycled pid (not the server script)", {}, 250.0),
                                 ("a re-started pid (creation time differs from the record)", "same", 999.0)):
        with _Sandbox(bolt="down") as (home, work):
            a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
            _claim_base(a)
            _legacy_store()
            P._write_base_owner_record(_ident(b))
            s, e = PR.lookup_ports(a)
            _seed_server(SEARCH, s, a)
            kill = _Spy(True)
            procs = (_procs({_PIDS[SEARCH]: SEARCH}, created=created) if live == "same"
                     else dict(_pid_alive=lambda pid: True, _pid_cmdline=lambda pid: "/usr/sbin/sshd -D",
                               _pid_create_time=lambda pid: 250.0, is_http_up=lambda *a_, **k_: False))
            _heal(a, ensure_neo4j=_Spy(True), _terminate_pid=kill, **procs)
            check(f"{label}: never signalled", kill.calls == [], str(kill.calls))
    # (b) a server an OLDER build started (pid file, no owner sidecar) is left alone but REPORTED
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        s, e = PR.lookup_ports(a)
        _seed_pidfile_only(SEARCH, s, _PIDS[SEARCH])
        kill = _Spy(True)
        rc, out = _heal(a, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                        **_procs({_PIDS[SEARCH]: SEARCH}, listening=(s,)))
        rec = _status(home, a)
        check("no sidecar (an older build's server): never signalled (owner-verified stops only)", kill.calls == [],
              str(kill.calls))
        check("... its pid file is kept (mcp-stop can still stop it)", P._mcp_pidfile(SEARCH, s).exists())
        check("... reported: status mcp_unverified names its port, the reason warns deepmap_search may answer "
              "from that graph", s in (rec.get("mcp_unverified") or []) and "deepmap_search" in str(rec.get("reason"))
              and "/deep-map:setup" in str(rec.get("reason")), str(rec))
        check("... and it stays a refusal", rec.get("refused") is True, str(rec))


def gate_g2b_nothing_stopped_without_policy_refusal() -> None:
    print("\n-- G2b controls: no stop on a non-policy failure, a healthy heal, or a route-less project --")
    # own per-project instance unreachable: its own servers (pointing at it) are left as they are
    with _Sandbox(bolt="down") as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        bolt, _http = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        uri = f"bolt://localhost:{bolt}"
        _seed_server(SEARCH, s, a, uri=uri)
        _seed_server(EMBED, e, a, uri=uri)
        kill = _Spy(True)
        rc, _ = _heal(a, ensure_neo4j=_Spy(False), _terminate_pid=kill,
                      **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        check("own instance unreachable (not a refusal): nothing stopped", rc != 0 and kill.calls == [],
              f"rc={rc} {kill.calls}")
    # legacy route whose owner read FAILS (an outage): this project's servers on 7687 are left as they are
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        s, e = PR.allocate_ports(c)
        _seed_server(SEARCH, s, c)
        _seed_server(EMBED, e, c)
        kill = _Spy(True)
        rc, _ = _heal(c, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                      **_procs({_PIDS[SEARCH]: SEARCH, _PIDS[EMBED]: EMBED}, listening=(s, e)))
        check("legacy route, owner read fails (not a refusal): nothing stopped", rc != 0 and kill.calls == [],
              f"rc={rc} {kill.calls}")
    # healthy heal: nothing stopped
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        bolt, _http = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        _seed_server(SEARCH, s, a, uri=f"bolt://localhost:{bolt}")
        kill = _Spy(True)
        rc, _ = _heal(a, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                      **_procs({_PIDS[SEARCH]: SEARCH}, listening=(s,)))
        check("healthy heal: nothing stopped", rc == 0 and kill.calls == [], f"rc={rc} {kill.calls}")
    # a project with no route of its own because ANOTHER project claims the base: it may be the store's real
    # owner (its servers then serve its own graph) — heal refuses to start anything but stops nothing
    with _Sandbox(bolt="up") as (home, work):
        c, d = _mk_project(work, "c", marker=True), _mk_project(work, "d")
        s, e = PR.allocate_ports(c)
        _claim_base(d)
        _seed_server(SEARCH, s, c)
        kill = _Spy(True)
        rc, _ = _heal(c, ensure_neo4j=_Spy(True), _terminate_pid=kill,
                      _make_driver=_driver_factory(_GraphDrv(owner=_ident(c), nodes=5)),
                      **_procs({_PIDS[SEARCH]: SEARCH}, listening=(s,)))
        rec = _status(home, c)
        route = PR.resolve_neo4j_route(str(c), honor_env=False)
        if route.source == "none":
            check("route-less project (another project's base claim): nothing stopped, not a G2 refusal",
                  kill.calls == [] and rec.get("refused") is False, f"{kill.calls} {rec}")
        else:   # G4 (W1) may route the real owner as legacy-unverified: then its own graph is served, not refused
            check("the base's live owner is this project (G4 route): nothing stopped, not refused",
                  kill.calls == [] and rec.get("refused") is not True, f"{route.source} {kill.calls} {rec}")


# =========================================================================== W3 — a stable public signature

def gate_foreign_base_claim_problem_signature() -> None:
    print("\n-- W3: provision.foreign_base_claim_problem keeps its signature (the CLI calls it — G1) --")
    fn = getattr(P, "foreign_base_claim_problem", None)
    params = list(inspect.signature(fn).parameters.values()) if callable(fn) else []
    check("foreign_base_claim_problem(target, uri=None)",
          [p.name for p in params] == ["target", "uri"] and params[1].default is None,
          str([(p.name, p.default) for p in params]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-5 gates (hermetic): G2 heal refusals")
    print("=" * 68)
    for g in (gate_g2a_foreign_base_claim_record, gate_g2a_foreign_base_claim_live, gate_g2a_legacy_owner,
              gate_g2a_non_refusals_are_not_marked, gate_g2a_writer_contract,
              gate_g2b_stops_own_servers_on_foreign_claim, gate_g2b_stops_own_servers_on_legacy_owner,
              gate_g2b_never_stops_unverified, gate_g2b_nothing_stopped_without_policy_refusal,
              gate_foreign_base_claim_problem_signature):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
