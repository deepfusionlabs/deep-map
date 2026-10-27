#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the provisioner LIFECYCLE fixes from the 2026-09-24 live
validation (build/provision.py — E1..E8). Each gate reproduces a field-confirmed defect hermetically:

  E1  per-port MCP START LOCK (two concurrent heals spawned two servers; the pid file + owner sidecar
      named the dead loser, so mcp-stop orphaned the real listener) + the Neo4j start lock (the losing
      launcher overwrote run/neo4j.pid) + start_mcp_servers tries BOTH servers even if one fails.
  E2  heal writes <DEEPMAP_HOME>/run/heal-<project_key>.json on every run (the SessionStart notify
      hook's input); never routes to a 7687 it does not own (no registry entry + a foreign program on
      7687; or an instance-rm'd project while 7687 is another project's instance); re-adopts its own
      instance from its conf after a registry loss; never starts MCP servers against an unreachable
      Neo4j and never provisions a store from scratch.
  E3  `stop <unregistered dir>` never stops another project's instance; a recycled pid is never killed.
  E4  `provision.py status` on a down / wrong-password instance is a friendly message + exit code.
  E5  setup honesty: a malformed .mcp.json fails setup; a failed set-initial-password never starts
      Neo4j; a slow `java -version` is not "no Java on PATH"; the MCP start/verify budgets cover a cold
      start; a failed verify is a DEGRADED verdict (exit 0 only while the servers are listening).
  E6  mcp-stop never stops ANOTHER project's servers; instance-rm stops the project's own MCP servers,
      retries the delete, and keeps the registry entry when the dir could not be removed.
  E7  the shared Neo4j download/extract no longer holds the REGISTRY lock (it starved allocation).
  E8  honest isolation note (escape hatch / MCP-only rows), instance table, AuthError vs unreachable.

Hermetic: DEEPMAP_HOME -> a throwaway dir; the port probes, bolt probes, pid liveness / command line,
Popen and every kill are stubbed. NO sockets to real services, NO Neo4j, NO processes, NO network.
Exit 0 = GREEN.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402
import provision as P       # noqa: E402

_PASS = 0
_FAIL = 0
_ENV_KEYS = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI",
             "DEEPMAP_MCP_START_WAIT", "DEEPMAP_MCP_VERIFY_DEADLINE")
_MISSING = object()


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


def _quiet(fn, *a, **k):
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _captured(fn, *a, **k):
    """(result, stdout+stderr text) — the result is the exception object if fn raised."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            res = fn(*a, **k)
        except Exception as e:   # noqa: BLE001
            res = e
    return res, out.getvalue() + err.getvalue()


def _mk_project(parent: Path, name: str, *, marker: bool = False) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        try:
            subprocess.run([git, "init", "-q"], cwd=str(d), capture_output=True, timeout=30)
        except Exception:
            pass
    if marker:
        (d / ".deepmap").mkdir(exist_ok=True)
        (d / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 5}', encoding="utf-8")
    return d.resolve()


class _Patch:
    """Set module attributes for the duration of a with-block; restore (or delete) them after."""

    def __init__(self, obj, **attrs):
        self.obj, self.attrs = obj, attrs

    def __enter__(self):
        self._saved = {k: getattr(self.obj, k, _MISSING) for k in self.attrs}
        for k, v in self.attrs.items():
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is _MISSING:
                try:
                    delattr(self.obj, k)
                except AttributeError:
                    pass
            else:
                setattr(self.obj, k, v)


class _Sandbox:
    """Fresh DEEPMAP_HOME + work dir; the per-project env cleared; the port probes answer from
    `occupied`; the bolt probes answer `bolt` ("up" | "down" | "auth"); the escape hatch pinned.
    Restores EVERYTHING on exit so no gate leaks into the next."""

    def __init__(self, occupied=(), bolt="down", escape_hatch=False):
        self.occupied = set(occupied)
        self.bolt = bolt
        self.escape_hatch = escape_hatch

    def __enter__(self):
        self._home = tempfile.mkdtemp(prefix="dmw2h")
        self._work = tempfile.mkdtemp(prefix="dmw2w")
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = self._home
        probe = (lambda host, port: port in self.occupied)
        self._pr = _Patch(PR, _port_in_use=probe).__enter__()
        # D2 (round 2): every stubbed pid counts as created long ago (older than its pid file / record),
        # so these round-1 gates keep their meaning without a real OS lookup of a FAKE pid; pid-recycling
        # by creation time is gated in tests/provision_route_identity_gates.py.
        self._p = _Patch(P, _port_in_use=probe,
                         is_bolt_up=lambda *a, **k: self.bolt == "up",
                         _bolt_state=lambda *a, **k: self.bolt,
                         _pid_create_time=lambda pid: 1.0,
                         _USER_SET_NEO4J_URI=bool(self.escape_hatch)).__enter__()
        return Path(self._home), Path(self._work)

    def __exit__(self, *a):
        self._p.__exit__(*a)
        self._pr.__exit__(*a)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._home, ignore_errors=True)
        shutil.rmtree(self._work, ignore_errors=True)


def _clear_port_env() -> None:
    for k in ("DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI"):
        os.environ.pop(k, None)


def _own_cmd(name) -> str:
    return f"C:\\Python\\python.exe C:\\plugins\\deep-map\\build\\{P.MCP_SERVERS[name]['script']}"


def _write_pid(name, port, pid, owner=None) -> None:
    pf = P._mcp_pidfile(name, port)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    if owner is not None:
        P._write_mcp_owner(name, port, pid, owner)


_REAL_POPEN = subprocess.Popen


def _server_popen(factory):
    """A Popen that fakes ONLY the MCP server launches (argv names a deepmap_*_server.py) and passes
    everything else (e.g. the `git rev-parse` behind project_identity) to the real Popen."""
    def _popen(args, *a, **k):
        if any(str(x).endswith("_server.py") for x in (args if isinstance(args, (list, tuple)) else [args])):
            return factory()
        return _REAL_POPEN(args, *a, **k)
    return _popen


class _Spy:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.ret(*a, **k) if callable(self.ret) else self.ret


def _heal_status(home: Path, target) -> dict:
    try:
        return json.loads((home / "run" / f"heal-{PR.project_key(target)}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# =========================================================================== E1 — start locks

def gate_mcp_start_lock_one_spawn() -> None:
    print("\n-- E1: two concurrent start_mcp_server calls spawn ONE server; pid + owner name the listener --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18801"
        os.environ["DEEPMAP_EMBED_PORT"] = "18802"
        guard = threading.Lock()
        bound = {"pid": None}
        spawned = []

        class _Proc:
            def __init__(self, pid):
                self.pid, self.returncode = pid, None
                threading.Timer(0.4, self._bind).start()   # the real server binds after its imports

            def _bind(self):
                with guard:
                    if bound["pid"] is None:
                        bound["pid"] = self.pid
                    else:
                        self.returncode = 1                  # [Errno 10048] — the loser dies

            def poll(self):
                return self.returncode

        def _new_proc():
            with guard:
                pid = 50000 + 4 * len(spawned)
                spawned.append(pid)
            return _Proc(pid)

        results = []
        barrier = threading.Barrier(2)

        def _start():
            barrier.wait()
            results.append(P.start_mcp_server("deepmap-search", target=str(a), wait=15))

        with _Patch(subprocess, Popen=_server_popen(_new_proc)), \
                _Patch(P, is_http_up=lambda host, port, timeout=2.0: port == 18801 and bound["pid"] is not None), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            ts = [threading.Thread(target=_start) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
        check("two concurrent starts spawn exactly ONE server (per-port start lock)", len(spawned) == 1,
              f"spawned={spawned}")
        check("both callers report the port served", results == [True, True], str(results))
        pf = P._mcp_pidfile("deepmap-search", 18801)
        rec = {}
        try:
            rec = json.loads(P._mcp_ownerfile("deepmap-search", 18801).read_text(encoding="utf-8"))
        except Exception:
            pass
        pid_txt = pf.read_text().strip() if pf.exists() else None
        check("the pid file names the process that holds the port", pid_txt == str(bound["pid"]),
              f"pidfile={pid_txt} listener={bound['pid']}")
        check("the owner sidecar names that same pid", rec.get("pid") == bound["pid"], str(rec))


def gate_mcp_dead_child_never_recorded() -> None:
    print("\n-- E1: a spawned server that DIED (lost the bind) is never written to the pid/owner files --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18803"
        os.environ["DEEPMAP_EMBED_PORT"] = "18804"
        n = {"http": 0}

        def _http(host, port, timeout=2.0):
            n["http"] += 1
            return n["http"] > 1          # free at the pre-check, then held by SOMEONE ELSE

        class _Dead:
            pid = 60004
            returncode = 1

            def poll(self):
                return 1

        with _Patch(subprocess, Popen=_server_popen(_Dead)), _Patch(P, is_http_up=_http):
            _quiet(P.start_mcp_server, "deepmap-search", target=str(a), wait=5)
        check("dead child -> NO pid file naming it", not P._mcp_pidfile("deepmap-search", 18803).exists())
        check("dead child -> NO owner sidecar naming it", not P._mcp_ownerfile("deepmap-search", 18803).exists())


def gate_neo4j_start_lock_one_launch() -> None:
    print("\n-- E1: two concurrent ensure_neo4j for ONE instance launch ONE Neo4j (run/neo4j.pid race) --")
    with _Sandbox() as (home, work):
        state = {"up": False, "starts": 0}

        def _start(version, project_key=None):
            state["starts"] += 1
            threading.Timer(0.5, lambda: state.__setitem__("up", True)).start()
            return 4244

        results = []
        barrier = threading.Barrier(2)

        def _ensure():
            barrier.wait()
            results.append(P.ensure_neo4j(uri="bolt://localhost:17991", user="neo4j", password="x",
                                          project_key="k1lock000001", bolt=17991, http=17992, timeout=20))

        with _Patch(P, is_bolt_up=lambda *a, **k: state["up"],
                    _bolt_state=lambda *a, **k: "up" if state["up"] else "down",
                    preflight=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                    configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True,
                    _is_initialized=lambda *a, **k: False, start_neo4j=_start), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            ts = [threading.Thread(target=_ensure) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(90)
        check("ONE launcher started for two concurrent provisions of the same instance", state["starts"] == 1,
              f"starts={state['starts']}")
        check("both callers see Neo4j up", results == [True, True], str(results))


def gate_start_both_servers() -> None:
    print("\n-- E1/#109: start_mcp_servers attempts BOTH servers even when the first fails --")
    seen = []
    fake = lambda name, password=None, target=None, **k: (seen.append(name) or name != "deepmap-search")  # noqa: E731
    with _Patch(P, start_mcp_server=fake):
        ok = _quiet(P.start_mcp_servers, target=None)
    check("aggregate is False when one server failed", ok is False, str(ok))
    check("embed is still started after search failed (no all() short-circuit)",
          seen == ["deepmap-search", "deepmap-embed"], str(seen))


# =========================================================================== E2 — heal routing + status

def gate_heal_status_file() -> None:
    print("\n-- E2: heal writes <home>/run/heal-<key>.json on EVERY run (the notify hook's input) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)   # its own instance (7689)
        mcp = _Spy(True)
        with _Patch(P, ensure_neo4j=lambda **k: True, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        rec = _heal_status(home, a)
        check("heal OK -> rc 0", rc == 0, str(rc))
        check("status file written with the contract keys",
              all(k in rec for k in ("ts", "neo4j_ok", "mcp_ok", "reason")), str(rec))
        check("status: neo4j_ok True, mcp_ok True, reason None",
              rec.get("neo4j_ok") is True and rec.get("mcp_ok") is True and rec.get("reason") is None, str(rec))
        check("heal_status_path() names the same file",
              callable(getattr(P, "heal_status_path", None))
              and P.heal_status_path(str(a)) == home / "run" / f"heal-{PR.project_key(a)}.json")
        _clear_port_env()
        mcp2 = _Spy(True)
        with _Patch(P, ensure_neo4j=lambda **k: False, start_mcp_servers=mcp2, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        rec = _heal_status(home, a)
        check("heal with Neo4j unreachable -> rc 1", rc == 1, str(rc))
        check("status: neo4j_ok False + an explicit reason naming the fix",
              rec.get("neo4j_ok") is False and "setup" in str(rec.get("reason") or ""), str(rec))
        check("heal does NOT start MCP servers against an unreachable Neo4j", mcp2.calls == [], str(mcp2.calls))


def gate_heal_status_carries_started() -> None:
    print("\n-- E2/H4: the 'servers were just (re)started' signal survives the concurrent heals that follow --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)

        def _starting(**k):
            P._MCP_STARTED.extend(["deepmap-search", "deepmap-embed"])
            return True
        with _Patch(P, ensure_neo4j=lambda **k: True, start_mcp_servers=_starting, _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(a)])
        first = _heal_status(home, a)
        check("the starting heal records mcp_started + its epoch",
              first.get("mcp_started") == ["deepmap-search", "deepmap-embed"]
              and isinstance(first.get("mcp_started_epoch"), float), str(first))
        _clear_port_env()
        with _Patch(P, ensure_neo4j=lambda **k: True, start_mcp_servers=lambda **k: True,
                    _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(a)])          # a concurrent heal that found them "already listening"
        second = _heal_status(home, a)
        check("a later 'already listening' heal keeps mcp_started + the ORIGINAL start epoch",
              second.get("mcp_started") == first.get("mcp_started")
              and second.get("mcp_started_epoch") == first.get("mcp_started_epoch"), str(second))
        _clear_port_env()
        with _Patch(P, ensure_neo4j=lambda **k: False, start_mcp_servers=lambda **k: True,
                    _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(a)])
        third = _heal_status(home, a)
        check("servers no longer up (Neo4j down) -> the started signal is dropped",
              third.get("mcp_started") == [] and third.get("mcp_started_epoch") is None, str(third))


def gate_heal_no_registry_foreign_base() -> None:
    print("\n-- E2/#66: heal with NO registry entry never routes to a 7687 held by a foreign program --")
    with _Sandbox(occupied={7687, 7474}, bolt="auth") as (home, work):
        a = _mk_project(work, "a", marker=True)
        P.write_mcp_json(a, "127.0.0.1", 18811, 18812)     # an older setup's .mcp.json, no registry entry
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        uris = [k.get("uri") for _, k in ens.calls]
        check("ensure_neo4j is never pointed at the foreign 7687",   # J4: compared by endpoint (any loopback spelling)
              not any(PR.same_bolt_endpoint(u, "bolt://localhost:7687") for u in uris), str(uris))
        check("no MCP servers started (they would serve the foreign program)", mcp.calls == [], str(mcp.calls))
        check("heal rc != 0", rc != 0, str(rc))
        check("status reason names /deep-map:setup", "setup" in str(_heal_status(home, a).get("reason") or ""),
              str(_heal_status(home, a)))


def gate_removed_instance_not_routed_to_base() -> None:
    print("\n-- E2/#46: after instance-rm (bolt dropped), heal/status/mcp-start never route to another project's 7687 --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)    # A owns the base 7687 instance
        PR.allocate_ports(c)
        PR.allocate_neo4j_ports(c, base_adopt_ok=lambda: True)    # C on 7689 ...
        PR.remove_entry(c, drop_neo4j_only=True)                  # ... then instance-rm'd
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None,
                    verify_mcp=lambda *a_, **k_: True, _print_mcp_hint=lambda *a_, **k_: None):
            rc_h = _quiet(P.main, ["heal", str(c)])
            _clear_port_env()
            drivers = []

            def _drv(uri, user, pw):
                drivers.append(uri)
                raise RuntimeError("no driver in a hermetic gate")
            with _Patch(P, _make_driver=_drv):
                rc_s, out_s = _captured(P.main, ["status", str(c)])
            _clear_port_env()
            rc_m = _quiet(P.main, ["mcp-start", str(c)])
        uris = [k.get("uri") for _, k in ens.calls]
        check("heal: never ensure_neo4j on A's 7687",   # J4: compared by endpoint
              not any(PR.same_bolt_endpoint(u, "bolt://localhost:7687") for u in uris), str(uris))
        check("heal: no MCP servers started for C", mcp.calls == [], str(mcp.calls))
        check("heal rc != 0", rc_h != 0, str(rc_h))
        check("status: never connects to A's 7687",   # J4: compared by endpoint
              not any(PR.same_bolt_endpoint(u, "bolt://localhost:7687") for u in drivers), str(drivers))
        check("status: non-zero exit + 'run /deep-map:setup'", rc_s not in (0, None) and "setup" in out_s,
              f"rc={rc_s} out={out_s.strip()[-300:]}")
        check("mcp-start: refuses (rc != 0), no servers wired to A's graph", rc_m != 0 and mcp.calls == [],
              f"rc={rc_m} calls={mcp.calls}")


def gate_heal_registry_loss_recovery() -> None:
    print("\n-- E2: a lost registry entry is recovered from the project's OWN instance conf (not 7687) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        P.write_mcp_json(a, "127.0.0.1", 18813, 18814)
        key = PR.project_key(a)
        conf = P.paths(None, key)["conf"]
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("server.bolt.listen_address=127.0.0.1:7695\nserver.http.listen_address=127.0.0.1:7482\n",
                        encoding="utf-8")
        ens = _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=_Spy(True), _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(a)])
        k = ens.calls[0][1] if ens.calls else {}
        check("heal routes to the instance conf's bolt 7695", k.get("uri") == "bolt://127.0.0.1:7695", str(k))  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
        check("heal revives THAT per-project instance (project_key passed)", k.get("project_key") == key, str(k))


def gate_heal_never_provisions() -> None:
    print("\n-- E2: heal (create=False) never provisions a store from scratch --")
    with _Sandbox() as (home, work):
        pre, dl, st = _Spy(True), _Spy(None), _Spy(1)
        diag = {}
        with _Patch(P, preflight=pre, _download_extract_shared=dl, start_neo4j=st,
                    configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True):
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:17001", user="neo4j", password="x",
                                project_key="deadbeef0001", bolt=17001, http=17002, create=False, diag=diag)
        check("create=False + no store -> False", ok is False, repr(ok))
        check("... with no preflight / download / start", not (pre.calls or dl.calls or st.calls),
              f"{pre.calls} {dl.calls} {st.calls}")
        check("... and a reason naming /deep-map:setup", "setup" in str(diag.get("reason") or ""), str(diag))


def gate_heal_skips_failed_setup() -> None:
    print("\n-- E2/#19: heal never revives a project whose last setup/sync FAILED (0 code nodes) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        (a / ".deepmap" / "graph_build.json").write_text('{"verdict": "FAILED", "nodes": 0}', encoding="utf-8")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        ens, mcp, mk = _Spy(True), _Spy(True), _Spy(None)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=mk):
            rc = _quiet(P.main, ["heal", str(a)])
        check("FAILED build -> no Neo4j revive, no MCP servers, no .mcp.json",
              not (ens.calls or mcp.calls or mk.calls), f"{ens.calls} {mcp.calls} {mk.calls}")
        check("rc != 0 + the status reason names /deep-map:setup",
              rc != 0 and "setup" in str(_heal_status(home, a).get("reason") or ""), f"rc={rc}")
        (a / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 12}', encoding="utf-8")
        _clear_port_env()
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=mk):
            rc = _quiet(P.main, ["heal", str(a)])
        check("control: a READY build IS revived", rc == 0 and len(ens.calls) == 1 and len(mcp.calls) == 1,
              f"rc={rc} {ens.calls} {mcp.calls}")


# =========================================================================== E3 — stop

def gate_stop_unregistered_never_kills_owner() -> None:
    print("\n-- E3/#47: `stop <unregistered dir>` never stops the base instance another project owns --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        u = _mk_project(work, "unregistered")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)   # A owns base 7687 (legacy layout pidfile)
        pf = P.paths(None)["pidfile"]
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("4248", encoding="utf-8")
        kill = _Spy(True)
        launcher = f'cmd.exe /c {P.paths(None)["neo4j_bin"]} console'
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: launcher, _terminate_pid=kill):
            rc = _quiet(P.main, ["stop", str(u)])
        check("no kill issued for A's instance", kill.calls == [], str(kill.calls))
        check("A's pid file untouched", pf.exists())
        check("rc != 0 (nothing of this project's to stop)", rc != 0, str(rc))


def gate_stop_recycled_pid() -> None:
    print("\n-- E3: stop_neo4j identity-checks the pid (a recycled pid is never killed) --")
    with _Sandbox() as (home, work):
        key = "abcdef012345"
        pf = P.paths(None, key)["pidfile"]
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("4252", encoding="utf-8")
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: "C:\\Python\\python.exe sleeper.py",
                    _terminate_pid=kill):
            ok = _quiet(P.stop_neo4j, project_key=key)
        check("recycled pid (not a DeepMap Neo4j) -> NOT killed", kill.calls == [], str(kill.calls))
        check("... returns False and clears the stale pid file", ok is False and not pf.exists(), f"{ok} {pf.exists()}")
        pf.write_text("4252", encoding="utf-8")
        launcher = f'cmd.exe /c {P.paths(None)["neo4j_bin"]} console'
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: launcher, _terminate_pid=kill):
            ok = _quiet(P.stop_neo4j, project_key=key)
        check("control: DeepMap's own launcher IS stopped", ok is True and len(kill.calls) == 1, str(kill.calls))


# =========================================================================== E4 — status

def _neo4j_exc(name):
    try:
        import neo4j.exceptions as NE
        return getattr(NE, name)("simulated")
    except Exception:
        return type(name, (Exception,), {})("simulated")


class _FailDrv:
    def __init__(self, exc):
        self.exc = exc

    def verify_connectivity(self):
        raise self.exc

    def session(self, *a, **k):
        raise self.exc

    def close(self):
        pass


def gate_cmd_status_friendly() -> None:
    print("\n-- E4/#12/#22: provision status on a down / wrong-password instance -> friendly + exit code --")
    with _Sandbox() as (home, work):
        for exc_name, want in (("ServiceUnavailable", "not reachable"), ("AuthError", "NEO4J_PASSWORD")):
            exc = _neo4j_exc(exc_name)
            with _Patch(P, _make_driver=lambda *a, **k: _FailDrv(exc)):
                rc, out = _captured(P.cmd_status, "bolt://localhost:17993", "neo4j", "x")
            check(f"{exc_name}: no raw traceback (returns an exit code)", isinstance(rc, int), repr(rc))
            check(f"{exc_name}: non-zero exit", isinstance(rc, int) and rc != 0, repr(rc))
            check(f"{exc_name}: message says '{want}'", want in out, out.strip()[-300:])
            check(f"{exc_name}: still prints the isolation line", "isolation:" in out, out.strip()[-300:])


# =========================================================================== E5 — setup honesty

class _FakeSync:
    @staticmethod
    def sync(*a, **k):
        return True


def gate_setup_malformed_mcp_json() -> None:
    print("\n-- E5/#83: a malformed .mcp.json fails setup (no false 'reload now' banner) --")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        (a / ".mcp.json").write_text("{ \"mcpServers\": { ,,, broken", encoding="utf-8")
        check("write_mcp_json on garbage -> False (explicit refusal)",
              _quiet(P.write_mcp_json, a, "127.0.0.1", 18815, 18816) is False)
        with _Patch(P, ensure_neo4j=lambda **k: True, _load=lambda name: _FakeSync,
                    verify_search=lambda *a_, **k_: True, start_mcp_servers=lambda **k: True,
                    verify_mcp=lambda *a_, **k_: True):
            ok, out = _captured(P.setup, str(a), password="x")
        check("setup returns False", ok is False, repr(ok))
        check("no 'ACTION REQUIRED: reload' banner", "ACTION REQUIRED" not in out, out.strip()[-300:])
        check("the file is named with the fix", ".mcp.json" in out, out.strip()[-300:])


def gate_set_initial_password_aborts_start() -> None:
    print("\n-- E5/#54: a failed set-initial-password never starts Neo4j on the fresh store --")
    with _Sandbox() as (home, work):
        st = _Spy(4256)
        with _Patch(P, preflight=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                    configure_neo4j=lambda *a, **k: True, set_initial_password=lambda *a, **k: False,
                    _is_initialized=lambda *a, **k: False, start_neo4j=st):
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:17003", user="neo4j", password="x",
                                project_key="k2spw0000001", bolt=17003, http=17004, timeout=1)
        check("ensure_neo4j -> False", ok is False, repr(ok))
        check("Neo4j was NOT started (the store stays fresh, recoverable)", st.calls == [], str(st.calls))
        # set_initial_password now reports success/failure
        runs = {"rc": 1}
        fake_run = lambda *a, **k: types.SimpleNamespace(returncode=runs["rc"], stdout="",   # noqa: E731
                                                         stderr="Could not reserve enough space for object heap")
        with _Patch(P, _is_initialized=lambda *a, **k: False, is_windows=lambda: False), \
                _Patch(subprocess, run=fake_run):
            r_fail = _quiet(P.set_initial_password, "5.26.0", "deepmap", "k2spw0000001")
            runs["rc"] = 0
            r_ok = _quiet(P.set_initial_password, "5.26.0", "deepmap", "k2spw0000001")
        check("set_initial_password rc!=0 -> False", r_fail is False, repr(r_fail))
        check("set_initial_password rc==0 -> True", r_ok is True, repr(r_ok))


def gate_java_timeout_not_missing() -> None:
    print("\n-- E5/#81/#107: a slow or failing `java -version` is never 'no Java on PATH' --")

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="java -version", timeout=k.get("timeout", 15))

    def _fail(*a, **k):
        return types.SimpleNamespace(returncode=1, stdout="",
                                     stderr="Error: Could not reserve enough space for object heap")
    with _Sandbox(), _Patch(P, _port_in_use=lambda h, p: False, _disk_free_gb=lambda path: 100.0), \
            _Patch(shutil, which=lambda name: "C:/jdk/bin/java.exe"):
        with _Patch(P, subprocess=types.SimpleNamespace(run=_timeout, TimeoutExpired=subprocess.TimeoutExpired)):
            ok, out = _captured(P.preflight, "5.26.0")
        check("timeout: never says 'no Java on PATH'", "no Java on PATH" not in out, out.strip()[-300:])
        check("timeout: says it timed out", "timed out" in out, out.strip()[-300:])
        check("timeout: proceeds (Java IS present; the start itself will show a real error)", ok is True, repr(ok))
        with _Patch(P, subprocess=types.SimpleNamespace(run=_fail, TimeoutExpired=subprocess.TimeoutExpired)):
            ok, out = _captured(P.preflight, "5.26.0")
        check("failing java: never says 'no Java on PATH'", "no Java on PATH" not in out, out.strip()[-300:])
        check("failing java: surfaces the JVM's own error", "Could not reserve" in out, out.strip()[-300:])
        check("failing java: preflight fails", ok is False, repr(ok))


def gate_mcp_budgets_and_wording() -> None:
    print("\n-- E5/#109/#99: MCP start/verify budgets cover a cold start; a timeout is worded as one --")
    check("MCP listen wait >= 120s (was 40s)", getattr(P, "MCP_START_WAIT_S", 0) >= 120,
          str(getattr(P, "MCP_START_WAIT_S", None)))
    check("MCP verify deadline >= 240s (was 90s < a cold model load)", getattr(P, "MCP_VERIFY_DEADLINE_S", 0) >= 240,
          str(getattr(P, "MCP_VERIFY_DEADLINE_S", None)))
    sig = inspect.signature(P.start_mcp_server)
    check("start_mcp_server's default wait is the load-tolerant budget (not a hard 40)",
          sig.parameters["wait"].default in (None, getattr(P, "MCP_START_WAIT_S", -1)),
          str(sig.parameters["wait"].default))
    run = lambda *a, **k: types.SimpleNamespace(returncode=2, stdout="",   # noqa: E731
                                                stderr="FAIL: probe exceeded 240.0s deadline (server hung or unreachable)")
    with _Patch(P, subprocess=types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)):
        ok, out = _captured(P.verify_mcp, "deepmap-search")
    check("verify rc=2 -> False", ok is False, repr(ok))
    check("verify timeout is worded 'timed out' + 'cold' start (not 'tool call failed')",
          "timed out" in out and "cold" in out and "tool call failed" not in out, out.strip()[-300:])


def gate_setup_verify_failure_is_degraded() -> None:
    print("\n-- E5/#74/#85/#113: a failed MCP verify is a DEGRADED verdict; exit 0 only while listening --")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        base = dict(ensure_neo4j=lambda **k: True, _load=lambda name: _FakeSync,
                    verify_search=lambda *a_, **k_: True, start_mcp_servers=lambda **k: True,
                    verify_mcp=lambda *a_, **k_: False)
        with _Patch(P, is_http_up=lambda *a_, **k_: True, **base):
            ok, out = _captured(P.setup, str(a), password="x")
        check("verify failed, servers listening -> setup True (exit 0)", ok is True, repr(ok))
        check("... with a 'setup verdict: DEGRADED' line", "setup verdict: DEGRADED" in out, out.strip()[-400:])
        _clear_port_env()
        with _Patch(P, is_http_up=lambda *a_, **k_: False, **base):
            ok, out = _captured(P.setup, str(a), password="x")
        check("verify failed AND a server is no longer listening -> setup False", ok is False, repr(ok))


# =========================================================================== E6 — mcp-stop / instance-rm

def gate_mcp_stop_foreign_owner() -> None:
    print("\n-- E6/#44: mcp-stop from a worktree/copy never stops ANOTHER project's servers --")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        w = _mk_project(work, "a-copy")
        pa = PR.allocate_ports(a)
        P.write_mcp_json(w, "127.0.0.1", *pa)              # the committed .mcp.json names A's ports
        _write_pid("deepmap-search", pa[0], 5004, owner=a)
        _write_pid("deepmap-embed", pa[1], 5008, owner=a)
        kill = _Spy(True)
        procs = {5004: _own_cmd("deepmap-search"), 5008: _own_cmd("deepmap-embed")}
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _terminate_pid=kill, is_http_up=lambda *a_, **k_: True):
            _quiet(P.main, ["mcp-stop", str(w)])
            check("the copy's mcp-stop did NOT signal A's servers", kill.calls == [], str(kill.calls))
            check("A's pid files are intact", P._mcp_pidfile("deepmap-search", pa[0]).exists()
                  and P._mcp_pidfile("deepmap-embed", pa[1]).exists())
            _clear_port_env()
            _quiet(P.main, ["mcp-stop", str(a)])
            check("control: A's own mcp-stop DOES stop both", len(kill.calls) == 2, str(kill.calls))


def gate_instance_rm_stops_own_mcp() -> None:
    print("\n-- E6/#48: instance-rm also stops the project's OWN MCP servers --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        pb = PR.allocate_ports(b)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        _write_pid("deepmap-search", pb[0], 6004, owner=b)
        _write_pid("deepmap-embed", pb[1], 6008, owner=b)
        kill = _Spy(True)
        procs = {6004: _own_cmd("deepmap-search"), 6008: _own_cmd("deepmap-embed")}
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _terminate_pid=kill, stop_neo4j=lambda version=None, project_key=None: True):
            rc = _quiet(P.instance_rm, str(b), yes=True)
        pids = sorted(a_[0] for a_, _ in kill.calls)
        check("instance-rm rc 0", rc == 0, str(rc))
        check("both of B's MCP servers were stopped", pids == [6004, 6008], str(kill.calls))
        check("their pid files are gone", not P._mcp_pidfile("deepmap-search", pb[0]).exists())


def gate_instance_rm_delete_failure_keeps_registry() -> None:
    print("\n-- E6/#106: a dir that will not delete -> rc 1 and the registry keeps the allocation --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        keyB = PR.project_key(b)
        inst = P.deepmap_home() / "instances" / keyB
        (inst / "data").mkdir(parents=True, exist_ok=True)
        (inst / "data" / "locked").write_text("x", encoding="utf-8")
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: True, _RM_RETRY=(2, 0.01)), \
                _Patch(shutil, rmtree=lambda *a_, **k_: None):          # every delete attempt "fails"
            rc = _quiet(P.instance_rm, str(b), yes=True)
        check("undeletable dir -> rc 1 (not a false success)", rc == 1, str(rc))
        check("... registry still records B's instance (so a retry can remove it)",
              PR.lookup_neo4j_ports(b) is not None, str(PR.lookup_neo4j_ports(b)))
        # an ORPHAN dir (registry already forgot it) is still removable, and not called the base store
        PR.remove_entry(b, drop_neo4j_only=True)
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: True, _RM_RETRY=(2, 0.01)):
            rc, out = _captured(P.instance_rm, str(b), yes=True)
        check("orphan instances/<key>/ is cleaned up (rc 0)", rc == 0 and not inst.exists(), f"rc={rc} {inst.exists()}")
        check("... never mislabelled as 'the base/legacy store'", "base/legacy" not in out, out.strip()[-300:])
        rc, out = _captured(P.instance_rm, str(b), yes=True)
        check("nothing allocated + no dir -> 'no Neo4j instance' (rc 2), not 'base/legacy'",
              rc == 2 and "base/legacy" not in out and "no Neo4j instance" in out, f"rc={rc} {out.strip()[-200:]}")


# =========================================================================== E7 — download lock

def gate_download_off_registry_lock() -> None:
    print("\n-- E7: the shared download/extract never holds the REGISTRY lock --")
    with _Sandbox() as (home, work):
        reg_lock = _Spy(None)
        dl, ex = _Spy(None), _Spy(None)
        with _Patch(PR, _acquire_lock=reg_lock), _Patch(P, download_neo4j=dl, extract_neo4j=ex):
            _quiet(P._download_extract_shared, "5.26.0")
            check("registry lock NOT taken for the download", reg_lock.calls == [], str(reg_lock.calls))
            check("download + extract ran", len(dl.calls) == 1 and len(ex.calls) == 1)
            sentinel = P.paths("5.26.0")["neo4j_home"] / ".deepmap-extracted"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("5.26.0", encoding="utf-8")
            dl.calls.clear()
            _quiet(P._download_extract_shared, "5.26.0")
            check("already extracted -> no archive re-verification (download skipped)", dl.calls == [],
                  str(dl.calls))


# =========================================================================== E8 — honesty

def gate_isolation_note_escape_hatch() -> None:
    print("\n-- E8/#72: escape-hatch note never claims per-project instances share the operator endpoint --")
    rows = [{"root": f"/{c}", "neo4j_uri": f"bolt://localhost:{7689 + 2 * i}"} for i, c in enumerate("abc")]
    esc = P.isolation_note("community", {"n_projects": 3, "projects": rows}, escape_hatch=True)
    check("no '3 projects share the operator endpoint'", "3 projects share" not in esc, esc)
    check("still OPERATOR-MANAGED", "OPERATOR-MANAGED" in esc, esc)
    check("says it is THIS invocation's NEO4J_URI", "this invocation" in esc, esc)
    # an MCP-only row (no instance) does not converge with the project that owns 7687
    mixed = P.isolation_note("community", {"n_projects": 2, "projects": [
        {"root": "/a", "neo4j_uri": "bolt://localhost:7687"}, {"root": "/b", "neo4j_uri": None}]})
    check("MCP-only row + base owner -> no false 'share a graph' WARNING", "WARNING" not in mixed, mixed)


def gate_instance_table_mcp_only() -> None:
    print("\n-- E8/#77: an MCP-only project is not shown as '7687 hibernated' with another store's size --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)                                      # MCP pair only (mcp-start before setup)
        legacy = P.paths(None)["databases"] / "neo4j"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "store").write_text("x" * 4096, encoding="utf-8")
        rows = P.instance_table()
        key = PR.project_key(a)
        row = next((r for r in rows if r.get("key") == key), {})
        check("row exists for the MCP-only project", bool(row), str(rows))
        check("not 'hibernated'/'running'", row.get("state") not in ("hibernated", "running"), str(row))
        check("not attributed to bolt 7687", not str(row.get("neo4j_uri") or "").endswith(":7687"), str(row))
        check("not charged the legacy store's size", not row.get("data_bytes"), str(row))


def gate_auth_vs_unreachable() -> None:
    print("\n-- E8/#84: an AuthError is reported as a credentials problem, not 'port in use'/'unreachable' --")
    with _Sandbox(bolt="auth") as (home, work):
        pre = _Spy(False)
        with _Patch(P, preflight=pre):
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:17005", user="neo4j", password="wrong",
                                project_key="k3auth000001", bolt=17005, http=17006, timeout=1)
        check("auth failure -> False", ok is False, repr(ok))
        check("... preflight NOT run (it would blame 'port already in use')", pre.calls == [], str(pre.calls))
        check("... message names the password", "NEO4J_PASSWORD" in out and "already in use" not in out,
              out.strip()[-300:])
    # the real classifier
    fn = getattr(P, "_bolt_state", None)
    check("provision._bolt_state exists", callable(fn))
    if callable(fn):
        class _OkDrv:
            def verify_connectivity(self):
                return None

            def close(self):
                pass
        for exc_name, want in (("AuthError", "auth"), ("ServiceUnavailable", "down")):
            exc = _neo4j_exc(exc_name)
            with _Patch(P, _make_driver=lambda *a, **k: _FailDrv(exc)):
                got = fn("bolt://localhost:17007", "neo4j", "x")
            check(f"_bolt_state: {exc_name} -> {want!r}", got == want, repr(got))
        with _Patch(P, _make_driver=lambda *a, **k: _OkDrv()):
            check("_bolt_state: reachable -> 'up'", fn("bolt://localhost:17007", "neo4j", "x") == "up")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — provisioner lifecycle gates E1-E8 (hermetic: no Neo4j, no spawn, no kill)")
    print("=" * 64)
    for g in (gate_mcp_start_lock_one_spawn, gate_mcp_dead_child_never_recorded,
              gate_neo4j_start_lock_one_launch, gate_start_both_servers,
              gate_heal_status_file, gate_heal_status_carries_started, gate_heal_no_registry_foreign_base,
              gate_removed_instance_not_routed_to_base, gate_heal_registry_loss_recovery,
              gate_heal_never_provisions, gate_heal_skips_failed_setup,
              gate_stop_unregistered_never_kills_owner, gate_stop_recycled_pid,
              gate_cmd_status_friendly,
              gate_setup_malformed_mcp_json, gate_set_initial_password_aborts_start,
              gate_java_timeout_not_missing, gate_mcp_budgets_and_wording,
              gate_setup_verify_failure_is_degraded,
              gate_mcp_stop_foreign_owner, gate_instance_rm_stops_own_mcp,
              gate_instance_rm_delete_failure_keeps_registry,
              gate_download_off_registry_lock,
              gate_isolation_note_escape_hatch, gate_instance_table_mcp_only, gate_auth_vs_unreachable):
        _run_gate(g)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
