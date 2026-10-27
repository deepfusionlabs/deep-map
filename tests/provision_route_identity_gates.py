#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-2 provisioner fixes (build/provision.py), on top of the
round-1 lifecycle gates (tests/provision_lifecycle_gates.py):

  D1  ONE routing rule. provision's lookup path is port_registry.resolve_neo4j_route (not a private copy):
      an unreadable / unrecognised registry is NO route (fail loud — never 7687), so `stop` can't kill the
      base instance another project owns, `status` never censuses it, heal never revives it (review #11);
      a LEGACY 7687 route is used only after the graph's :_DeepMapMeta owner proves it is this project's
      (status / heal / stop / mcp-start all verify — sync.verify_legacy_owner).
  #13 `mcp-stop <unregistered dir>` honours mcp_route_problem(): it never stops the base-port servers.
  D2  process identity = pid + CREATION TIME: a recycled pid (created after the record / the pid file) is
      never signalled, an unknown creation time is never trusted (review #14).
  D3  the MCP servers are started with DEEPMAP_PROJECT_ROOT / DEEPMAP_PROJECT_KEY (the project guard).
  W5  instance-rm --purge also removes the project's graphify output dirs (<home>/gout/...); setup's
      FAILED hint quotes the manifest's recorded cause instead of guessing "graphify isn't installed".
  D6  an auth failure's remedy is runnable in that state (never `instance-rm --yes` on the base store);
      heal's persisted reason stays short and says "credentials".
  W4  verify_mcp words the server's "still starting up" answer as a cold start; its default deadline
      covers the servers' warm-up budget + one tool call.
  #69 heal restarts THIS project's own server whose recorded Neo4j config (URI / credentials) is stale.
  #105 write_mcp_json keeps the file's line endings (no CRLF rewrite on Windows) and says when it
      changed a git-TRACKED .mcp.json.
  +   an MCP server is stopped with its process TREE (a Windows venv python.exe is a launcher whose child
      is the real server); an escape-hatch `stop` stops the instance NEO4J_URI names, not the base store.

Hermetic: DEEPMAP_HOME -> a throwaway dir; port/bolt probes, drivers (a scripted fake graph), pid
liveness / command line / creation time, Popen and every kill are stubbed. NO Neo4j, NO processes,
NO network (the one real OS read is this test process's own creation time). Exit 0 = GREEN.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
_ENV_KEYS = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI", "NEO4J_USER",
             "NEO4J_PASSWORD", "DEEPMAP_MCP_START_WAIT", "DEEPMAP_MCP_VERIFY_DEADLINE",
             "DEEPMAP_MCP_WARMUP_TIMEOUT_S", "DEEPMAP_MCP_TOOL_TIMEOUT_S")
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


def _git(*args, cwd) -> None:
    git = shutil.which("git")
    if git:
        try:
            subprocess.run([git, *args], cwd=str(cwd), capture_output=True, timeout=30)
        except Exception:
            pass


def _mk_project(parent: Path, name: str, *, marker: bool = False) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=d)
    if marker:
        (d / ".deepmap").mkdir(exist_ok=True)
        (d / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 5}', encoding="utf-8")
    return d.resolve()


class _Patch:
    """Set attributes for the duration of a with-block; restore (or delete) them after."""

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
    """Fresh DEEPMAP_HOME + work dir; per-project env cleared; port probes answer from `occupied`; bolt
    probes answer `bolt` ("up" | "down" | "auth"); the escape hatch pinned; creation time UNKNOWN unless a
    gate stubs it. Restores everything on exit."""

    def __init__(self, occupied=(), bolt="down", escape_hatch=False):
        self.occupied = set(occupied)
        self.bolt = bolt
        self.escape_hatch = escape_hatch

    def __enter__(self):
        self._home = tempfile.mkdtemp(prefix="dmr2h")
        self._work = tempfile.mkdtemp(prefix="dmr2w")
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = self._home
        probe = (lambda host, port: port in self.occupied)
        self._pr = _Patch(PR, _port_in_use=probe).__enter__()
        self._p = _Patch(P, _port_in_use=probe,
                         is_bolt_up=lambda *a, **k: self.bolt == "up",
                         _bolt_state=lambda *a, **k: self.bolt,
                         _pid_create_time=lambda pid: None,
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


def _own_cmd(name) -> str:
    return f"C:\\Python\\python.exe C:\\plugins\\deep-map\\build\\{P.MCP_SERVERS[name]['script']}"


_REAL_POPEN = subprocess.Popen


def _server_popen(factory, seen=None):
    """A Popen that fakes ONLY the MCP server launches and passes everything else (git) through."""
    def _popen(args, *a, **k):
        if any(str(x).endswith("_server.py") for x in (args if isinstance(args, (list, tuple)) else [args])):
            if seen is not None:
                seen.append(k)
            return factory()
        return _REAL_POPEN(args, *a, **k)
    return _popen


class _Proc:
    def __init__(self, pid=71001):
        self.pid, self.returncode = pid, None

    def poll(self):
        return None


# ---- a scripted fake graph (the legacy-route owner check reads it) ---------------------------------
class _Res:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        return self.rows[0] if self.rows else None


class _Sess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **k):
        self.drv.queries.append(q)
        if "_DeepMapMeta" in q and "owning_root" in q:
            return _Res([{"owning_root": self.drv.owner}] if self.drv.owner else [])
        if "count(r)" in q:
            return _Res([{"c": 3}])
        if "embedding IS NOT NULL" in q:
            return _Res([{"c": self.drv.nodes}])
        if "count(n)" in q:
            return _Res([{"c": self.drv.nodes}])
        if "dbms.components" in q:
            return _Res([{"edition": "community"}])
        return _Res([])


class _GraphDrv:
    """A connected driver over a graph owned by `owner` (None = no :_DeepMapMeta) holding `nodes` nodes;
    `exc` makes verify_connectivity raise (down / auth)."""

    def __init__(self, owner=None, nodes=0, exc=None):
        self.owner, self.nodes, self.exc = owner, nodes, exc
        self.queries = []
        self.uris = []

    def verify_connectivity(self):
        if self.exc is not None:
            raise self.exc

    def session(self, *a, **k):
        if self.exc is not None:
            raise self.exc
        return _Sess(self)

    def close(self):
        pass


def _driver_factory(drv):
    def _mk(uri, user, password):
        drv.uris.append(uri)
        return drv
    return _mk


def _neo4j_exc(name):
    try:
        import neo4j.exceptions as NE
        return getattr(NE, name)("simulated")
    except Exception:
        return type(name, (Exception,), {})("simulated")


def _bump_registry_version(home: Path) -> None:
    reg = home / "registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    data["version"] = 99
    reg.write_text(json.dumps(data), encoding="utf-8")


def _launcher_cmd() -> str:
    return f'cmd.exe /c {P.paths(None)["neo4j_bin"]} console'


# =========================================================================== D1 — one routing rule

def gate_unusable_registry_is_no_route() -> None:
    print("\n-- D1/#11: an unrecognised or unreadable registry is NO route on the lookup path (never 7687) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        b = _mk_project(work, "b", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)      # A owns the base 7687 instance
        PR.allocate_ports(b)                                         # B: MCP-only (no Neo4j pair)
        _bump_registry_version(home)                                 # written by a "newer DeepMap"
        _, neo_problem, _ = _quiet(P._resolve_ports, str(b), allocate=False)
        check("unrecognised registry -> a Neo4j route problem", bool(neo_problem), repr(neo_problem))
        check("... naming the registry", "registry" in str(neo_problem or "").lower(), repr(neo_problem))
        check("... and NEO4J_URI fails closed (bolt://127.0.0.1:0), never 7687",  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
              os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:0", os.environ.get("NEO4J_URI"))
        # stop B must not kill A's base instance (its pid file names a live DeepMap launcher)
        pf = P.paths(None)["pidfile"]
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("4248", encoding="utf-8")
        kill = _Spy(True)
        _clear_port_env()
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(),
                    _pid_create_time=lambda pid: 1.0, _terminate_pid=kill):
            rc = _quiet(P.main, ["stop", str(b)])
        check("`stop B` issues NO kill for A's base instance", kill.calls == [], str(kill.calls))
        check("`stop B` rc != 0", rc != 0, str(rc))
        # status B never connects anywhere, rc 3
        drv = _GraphDrv(owner=str(a), nodes=9)
        _clear_port_env()
        with _Patch(P, _make_driver=_driver_factory(drv)):
            rc, out = _captured(P.main, ["status", str(b)])
        check("`status B` never connects to 7687",   # J4: compared by endpoint (any loopback spelling)
              not any(PR.same_bolt_endpoint(u, "bolt://localhost:7687") for u in drv.uris), str(drv.uris))
        check("`status B` rc 3 + names the registry", rc == 3 and "registry" in out.lower(),
              f"rc={rc} {out.strip()[-200:]}")
        # heal B revives nothing
        ens, mcp = _Spy(True), _Spy(True)
        _clear_port_env()
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(b)])
        check("`heal B`: no ensure_neo4j, no MCP servers", ens.calls == [] and mcp.calls == [],
              f"{ens.calls} {mcp.calls}")
        check("`heal B`: status reason names the registry",
              "registry" in str(_heal_status(home, b).get("reason") or "").lower(), str(_heal_status(home, b)))
    with _Sandbox() as (home, work):
        c = _mk_project(work, "c", marker=True)
        with _Patch(PR, _read_registry=lambda env=None: ("unreadable", None)):
            _, neo_problem, _ = _quiet(P._resolve_ports, str(c), allocate=False)
        check("unreadable registry -> a route problem + bolt://127.0.0.1:0",  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
              bool(neo_problem) and os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:0",
              f"{neo_problem!r} {os.environ.get('NEO4J_URI')}")


def gate_lookup_route_is_the_shared_resolver() -> None:
    print("\n-- D1: provision's lookup route == port_registry.resolve_neo4j_route (one rule) --")
    src = getattr(P, "neo4j_route_source", None)
    check("provision.neo4j_route_source() exists", callable(src))
    if not callable(src):
        return
    scenarios = []
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)       # its own instance on 7689
        _quiet(P._resolve_ports, str(a), allocate=False)
        scenarios.append(("registered", a, os.environ.get("NEO4J_URI"), src()))
        r = PR.resolve_neo4j_route(str(a), honor_env=False)
        check("registered: provision routes to the resolver's URI", os.environ.get("NEO4J_URI") == r.uri,
              f"{os.environ.get('NEO4J_URI')} vs {r.uri}")
        check("registered: source 'registry'", src() == "registry", str(src()))
        # lost registry entry -> its own instance conf
        b = _mk_project(work, "b")
        key = PR.project_key(b)
        conf = P.paths(None, key)["conf"]
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("server.bolt.listen_address=127.0.0.1:7695\nserver.http.listen_address=127.0.0.1:7482\n",
                        encoding="utf-8")
        _clear_port_env()
        _quiet(P._resolve_ports, str(b), allocate=False)
        check("lost registry: routes to its instance conf (7695)", os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:7695",
              os.environ.get("NEO4J_URI"))
        check("lost registry: source 'instance-conf'", src() == "instance-conf", str(src()))
        check("lost registry: resolved_instance is that instance", P.resolved_instance(str(b)) == (key, 7695, 7482),
              str(P.resolved_instance(str(b))))
        # a lone unregistered project on a free 7687 -> the LEGACY route (to be verified)
        c = _mk_project(work, "c")
        _clear_port_env()
        _, prob, _ = _quiet(P._resolve_ports, str(c), allocate=False)
        check("lone unregistered: legacy 7687 route, source 'legacy-unverified'",
              prob is None and os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:7687" and src() == "legacy-unverified",
              f"{prob!r} {os.environ.get('NEO4J_URI')} {src()}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        c = _mk_project(work, "c")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)        # A owns base
        _quiet(P._resolve_ports, str(c), allocate=False)
        r = PR.resolve_neo4j_route(str(c), honor_env=False)
        check("another project owns 7687: no route (source 'none'), like the resolver",
              src() == "none" and r.source == "none" and os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:0",
              f"{src()} {r.source} {os.environ.get('NEO4J_URI')}")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        os.environ["NEO4J_URI"] = "bolt://db.example.com:7687"
        _quiet(P._resolve_ports, str(a), allocate=False)
        check("escape hatch: source 'env', the operator URI untouched",
              src() == "env" and os.environ.get("NEO4J_URI") == "bolt://db.example.com:7687",
              f"{src()} {os.environ.get('NEO4J_URI')}")


def gate_legacy_route_status_verifies_owner() -> None:
    print("\n-- D1: `status` on the LEGACY 7687 route censuses only a graph provably this project's --")
    with _Sandbox() as (home, work):
        c = _mk_project(work, "c")
        other = str(work / "someone-else")
        drv = _GraphDrv(owner=other, nodes=40)
        with _Patch(P, _make_driver=_driver_factory(drv)):
            rc, out = _captured(P.main, ["status", str(c)])
        check("foreign-owned legacy graph: rc 3", rc == 3, f"rc={rc}")
        check("... no census of the other project's graph", "nodes=" not in out, out.strip()[-300:])
        check("... says whose graph it is and names /deep-map:setup",
              "setup" in out and "someone-else" in out, out.strip()[-300:])
        drv = _GraphDrv(owner=None, nodes=40)
        _clear_port_env()
        with _Patch(P, _make_driver=_driver_factory(drv)):
            rc, out = _captured(P.main, ["status", str(c)])
        check("unowned NON-empty legacy graph: rc 3, no census", rc == 3 and "nodes=" not in out,
              f"rc={rc} {out.strip()[-200:]}")
        drv = _GraphDrv(owner=str(c), nodes=12)
        _clear_port_env()
        with _Patch(P, _make_driver=_driver_factory(drv)):
            rc, out = _captured(P.main, ["status", str(c)])
        check("control: this project's own legacy graph -> census, rc 0", rc == 0 and "nodes=12" in out,
              f"rc={rc} {out.strip()[-200:]}")


def gate_legacy_route_heal_verifies_owner() -> None:
    print("\n-- D1: heal on the LEGACY route never starts MCP servers for another project's graph --")
    # (round 3: heal never STARTS the legacy base, so the owner check runs on an already-running one)
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)                       # an MCP pair of its own; NO Neo4j pair -> legacy 7687
        drv = _GraphDrv(owner=str(work / "other-proj"), nodes=40)
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(drv)):
            rc = _quiet(P.main, ["heal", str(c)])
        rec = _heal_status(home, c)
        check("foreign-owned legacy graph: no MCP servers started", mcp.calls == [], str(mcp.calls))
        check("... heal rc != 0, neo4j_ok False", rc != 0 and rec.get("neo4j_ok") is False, f"rc={rc} {rec}")
        check("... the reason is the owner refusal (names the other project + /deep-map:setup)",
              "other-proj" in str(rec.get("reason") or "") and "/deep-map:setup" in str(rec.get("reason") or ""),
              str(rec))
        drv = _GraphDrv(owner=str(c), nodes=12)
        mcp = _Spy(True)
        _clear_port_env()
        with _Patch(P, ensure_neo4j=_Spy(True), start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(drv)):
            rc = _quiet(P.main, ["heal", str(c)])
        check("control: its own legacy graph -> MCP started, rc 0", rc == 0 and len(mcp.calls) == 1,
              f"rc={rc} {mcp.calls}")


def gate_legacy_route_stop_and_mcp_start() -> None:
    print("\n-- D1: `stop` / `mcp-start` on the LEGACY route act only for this project's own graph --")
    with _Sandbox() as (home, work):
        c = _mk_project(work, "c")
        pf = P.paths(None)["pidfile"]
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("4250", encoding="utf-8")
        kill = _Spy(True)
        procs = dict(_pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(),
                     _pid_create_time=lambda pid: 1.0, _terminate_pid=kill)
        for label, drv in (("foreign-owned", _GraphDrv(owner=str(work / "zz"), nodes=5)),
                           ("unreachable", _GraphDrv(exc=_neo4j_exc("ServiceUnavailable"))),
                           ("wrong password", _GraphDrv(exc=_neo4j_exc("AuthError")))):
            _clear_port_env()
            with _Patch(P, _make_driver=_driver_factory(drv), **procs):
                rc = _quiet(P.main, ["stop", str(c)])
            check(f"stop, {label} legacy graph: NO kill of the base instance", kill.calls == [], str(kill.calls))
            check(f"stop, {label}: rc != 0", rc != 0, str(rc))
        _clear_port_env()
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=str(c), nodes=5)), **procs):
            rc = _quiet(P.main, ["stop", str(c)])
        check("control: its own legacy graph -> the base instance IS stopped", rc == 0 and len(kill.calls) == 1,
              f"rc={rc} {kill.calls}")
        # mcp-start
        mcp = _Spy(True)
        _clear_port_env()
        with _Patch(P, start_mcp_servers=mcp, verify_mcp=lambda *a_, **k_: True,
                    _print_mcp_hint=lambda *a_, **k_: None, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=str(work / "zz"), nodes=5))):
            rc = _quiet(P.main, ["mcp-start", str(c)])
        check("mcp-start, foreign-owned legacy graph: refused, no servers", rc != 0 and mcp.calls == [],
              f"rc={rc} {mcp.calls}")
        _clear_port_env()
        with _Patch(P, start_mcp_servers=mcp, verify_mcp=lambda *a_, **k_: True,
                    _print_mcp_hint=lambda *a_, **k_: None, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=str(c), nodes=5))):
            rc = _quiet(P.main, ["mcp-start", str(c)])
        check("control: mcp-start on its own legacy graph starts the servers", rc == 0 and len(mcp.calls) == 1,
              f"rc={rc} {mcp.calls}")


def gate_escape_hatch_stop_targets_named_instance() -> None:
    print("\n-- escape hatch: `stop` stops the instance NEO4J_URI names — never the base store by default --")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)       # A's own instance on 7689
        keyA = PR.project_key(a)
        stops = []
        fake_stop = lambda version=None, project_key=None: (stops.append(project_key) or True)  # noqa: E731
        with _Patch(P, stop_neo4j=fake_stop):
            os.environ["NEO4J_URI"] = "bolt://localhost:7689"
            rc1 = _quiet(P.main, ["stop", str(a)])
            after1 = list(stops)
            os.environ["NEO4J_URI"] = "bolt://db.example.com:7687"
            rc2 = _quiet(P.main, ["stop", str(a)])
            after2 = list(stops)
            os.environ["NEO4J_URI"] = "bolt://localhost:7687"
            rc3 = _quiet(P.main, ["stop", str(a)])
            after3 = list(stops)
        check("NEO4J_URI=localhost:7689 (A's instance) -> stops A's instance, not the base",
              after1 == [keyA] and rc1 == 0, f"{after1} rc={rc1}")
        check("NEO4J_URI=a remote endpoint -> nothing stopped, rc != 0", after2 == after1 and rc2 != 0,
              f"{after2} rc={rc2}")
        check("NEO4J_URI=localhost:7687 -> the base (legacy) instance", after3 == after2 + [None] and rc3 == 0,
              f"{after3} rc={rc3}")


def gate_mcp_stop_honours_route_problem() -> None:
    print("\n-- #13: `mcp-stop <unregistered dir>` never stops the base-port servers (pre-upgrade pid files) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        u = _mk_project(work, "unregistered")
        pa = PR.allocate_ports(a)                                    # A on 8787/8788
        for name, port, pid in (("deepmap-search", pa[0], 5104), ("deepmap-embed", pa[1], 5108)):
            pf = P._mcp_pidfile(name, port)
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(pid), encoding="utf-8")                # a pre-upgrade server: NO owner sidecar
        kill = _Spy(True)
        procs = {5104: _own_cmd("deepmap-search"), 5108: _own_cmd("deepmap-embed")}
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _pid_create_time=lambda pid: 1.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: True):
            rc, out = _captured(P.main, ["mcp-stop", str(u)])
        check("no kill of A's servers from an unregistered dir", kill.calls == [], str(kill.calls))
        check("says nothing to stop + names /deep-map:setup", "nothing to stop" in out and "setup" in out,
              out.strip()[-300:])
        check("A's pid files intact", P._mcp_pidfile("deepmap-search", pa[0]).exists())


# =========================================================================== D2 — creation-time identity

def gate_pid_create_time_real() -> None:
    print("\n-- D2: _pid_create_time reads a REAL process's creation time (this test process) --")
    fn = getattr(P, "_pid_create_time", None)
    check("provision._pid_create_time exists", callable(fn))
    if not callable(fn):
        return
    t0 = time.time()
    got = fn(os.getpid())
    if Path("/proc/self/stat").exists():
        # Linux (round 3, E2): "linux:<boot_id>:<start ticks>" — never an epoch derived from a moving btime
        check("this process (Linux): a boot_id + start-ticks token", isinstance(got, str) and got.startswith("linux:"),
              repr(got))
        check("... stable across two reads", fn(os.getpid()) == got, repr(got))
    else:
        check("this process: a float epoch time", isinstance(got, float), repr(got))
        if isinstance(got, float):
            check("... not in the future, and within the last day", t0 - 86400 < got <= t0 + 5, f"{got} vs {t0}")
    check("a dead pid -> None (unknown)", fn(2_000_000_000) is None, repr(fn(2_000_000_000)))
    check("pid 0 / garbage -> None", fn(0) is None and fn("x") is None)


def gate_stop_neo4j_creation_time() -> None:
    print("\n-- D2/#14: stop_neo4j compares the pid's creation time before any signal --")
    with _Sandbox() as (home, work):
        key = "abcdef012345"
        p = P.paths(None, key)
        launcher = _launcher_cmd()
        # start_neo4j records the identity next to the pid file
        (p["neo4j_bin"]).parent.mkdir(parents=True, exist_ok=True)
        with _Patch(subprocess, Popen=lambda *a, **k: _Proc(4301)), _Patch(P, _pid_create_time=lambda pid: 100.0):
            _quiet(P.start_neo4j, "5.26.0", key)
        ident = p["pidfile"].with_name("neo4j.pid.identity")
        rec = {}
        if P.is_windows():
            try:
                rec = json.loads(ident.read_text(encoding="utf-8"))
            except Exception:
                pass
            check("start_neo4j records {pid, created} next to run/neo4j.pid",
                  rec.get("pid") == 4301 and rec.get("created") == 100.0, str(rec))
        else:
            # ROUND-7 I4: on POSIX run/neo4j.pid is Neo4j's own (its bootloader refuses to start while it names a live
            # pid) — the launcher's identity goes into DeepMap's own run/neo4j.launcher.json
            try:
                rec = json.loads(p["pidfile"].with_name("neo4j.launcher.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            check("start_neo4j (POSIX) records {launcher_pid, created} in run/neo4j.launcher.json, never run/neo4j.pid",
                  rec.get("launcher_pid") == 4301 and rec.get("created") == 100.0 and not p["pidfile"].exists(),
                  str(rec))
        for label, now, want_kill in (("same process (created 100.0)", 100.0, True),
                                      ("recycled pid (created 900.0)", 900.0, False),
                                      ("creation time unknown", None, False)):
            p["pidfile"].write_text("4301", encoding="utf-8")
            ident.write_text(json.dumps({"pid": 4301, "created": 100.0}), encoding="utf-8")
            kill = _Spy(True)
            with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: launcher,
                        _pid_create_time=lambda pid, _n=now: _n, _terminate_pid=kill):
                ok = _quiet(P.stop_neo4j, project_key=key)
            check(f"{label}: {'killed' if want_kill else 'NOT killed'}",
                  (len(kill.calls) == 1) == want_kill and bool(ok) == want_kill, f"ok={ok} {kill.calls}")
            if now is None:
                check("... unknown: the pid file is KEPT (it may still be ours)", p["pidfile"].exists())
            elif not want_kill:
                # round 4 (binding F-plan): two COMPARABLE start stamps that differ PROVE another process — the
                # stale pid file + identity are cleared (never signalled); only an unprovable (no-record)
                # mismatch keeps them (see below + tests/provision_round3_gates.py)
                check("... a PROVEN mismatch (comparable stamps) on DeepMap's Neo4j: pid file + identity CLEARED",
                      not p["pidfile"].exists() and not ident.exists())
                p["pidfile"].write_text("4301", encoding="utf-8")
                ident.write_text(json.dumps({"pid": 4301, "created": 100.0}), encoding="utf-8")
                with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: "/usr/sbin/cron -f",
                            _pid_create_time=lambda pid, _n=now: _n, _terminate_pid=kill):
                    _quiet(P.stop_neo4j, project_key=key)
                check("... recycled by an UNRELATED program: the stale pid file + identity are cleared",
                      not p["pidfile"].exists() and not ident.exists() and kill.calls == [])
        # a pre-upgrade pid file (no identity record): created AFTER the pid file was written = recycled
        ident.unlink(missing_ok=True)
        p["pidfile"].write_text("4301", encoding="utf-8")
        mtime = p["pidfile"].stat().st_mtime
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: launcher,
                    _pid_create_time=lambda pid: mtime + 3600.0, _terminate_pid=kill):
            _quiet(P.stop_neo4j, project_key=key)
        check("no record + process younger than the pid file -> NOT killed", kill.calls == [], str(kill.calls))
        p["pidfile"].write_text("4301", encoding="utf-8")
        mtime = p["pidfile"].stat().st_mtime
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: launcher,
                    _pid_create_time=lambda pid: mtime - 60.0, _terminate_pid=kill):
            _quiet(P.stop_neo4j, project_key=key)
        check("no record + process older than the pid file -> killed", len(kill.calls) == 1, str(kill.calls))


def gate_mcp_creation_time() -> None:
    print("\n-- D2/#14: MCP owner sidecar records the creation time; stop/adopt compare it --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18831"
        os.environ["DEEPMAP_EMBED_PORT"] = "18832"
        with _Patch(P, _pid_create_time=lambda pid: 250.0):
            _quiet(P._record_mcp_server, "deepmap-search", 18831, 5201, str(a))
        rec = {}
        try:
            rec = json.loads(P._mcp_ownerfile("deepmap-search", 18831).read_text(encoding="utf-8"))
        except Exception:
            pass
        check("the owner sidecar records 'created'", rec.get("created") == 250.0, str(rec))
        cmd = _own_cmd("deepmap-search")
        for label, now, want in (("same process", 250.0, True), ("recycled", 999.0, False), ("unknown", None, False)):
            with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: cmd,
                        _pid_create_time=lambda pid, _n=now: _n):
                own = P._own_mcp_server_pid("deepmap-search", 18831, str(a))
            check(f"_own_mcp_server_pid, {label}: {'ours' if want else 'NOT provably ours'}",
                  (own == 5201) == want, repr(own))
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: cmd,
                    _pid_create_time=lambda pid: 999.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
            _quiet(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("stop_mcp_server: a recycled pid (newer creation time) is never signalled", kill.calls == [],
              str(kill.calls))
        _quiet(P._record_mcp_server, "deepmap-search", 18831, 5201, str(a))
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: cmd,
                    _pid_create_time=lambda pid: None, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
            ok = _quiet(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("stop_mcp_server: unknown creation time -> not signalled, reported as not stopped",
              kill.calls == [] and ok is False, f"ok={ok} {kill.calls}")


def gate_mcp_stop_kills_tree() -> None:
    print("\n-- MCP servers are stopped WITH their process tree (a venv python.exe is a launcher) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18833"
        os.environ["DEEPMAP_EMBED_PORT"] = "18834"
        with _Patch(P, _pid_create_time=lambda pid: 300.0):
            _quiet(P._record_mcp_server, "deepmap-search", 18833, 5301, str(a))
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _own_cmd("deepmap-search"),
                    _pid_create_time=lambda pid: 300.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
            _quiet(P.stop_mcp_server, "deepmap-search", target=str(a))
        trees = [k.get("tree") for _, k in kill.calls]
        check("stop_mcp_server signals the tree (tree=True)", trees == [True], str(kill.calls))


def gate_posix_tree_kill_never_own_group() -> None:
    print("\n-- POSIX tree kill never signals the CALLER's own process group --")
    sent = []
    with _Patch(P, is_windows=lambda: False, _pid_alive=lambda pid: False), \
            _Patch(os, getpgid=lambda pid: 4242, getpgrp=lambda: 4242,
                   killpg=lambda pg, s: sent.append(("killpg", pg, s)),
                   kill=lambda pid, s: sent.append(("kill", pid, s))):
        ok = P._terminate_pid(5555, tree=True)
    check("same group as ours -> a single-pid kill, no killpg", sent and all(x[0] == "kill" for x in sent),
          str(sent))
    check("... and it reports the process gone", ok is True, repr(ok))
    sent.clear()
    with _Patch(P, is_windows=lambda: False, _pid_alive=lambda pid: False), \
            _Patch(os, getpgid=lambda pid: 4242, getpgrp=lambda: 999,
                   killpg=lambda pg, s: sent.append(("killpg", pg, s)),
                   kill=lambda pid, s: sent.append(("kill", pid, s))):
        P._terminate_pid(5555, tree=True)
    check("control: another group -> killpg on it", sent[:1] == [("killpg", 4242, 15)], str(sent))


# =========================================================================== D3 — server env

def gate_server_env_project_guard() -> None:
    print("\n-- D3: MCP servers start with DEEPMAP_PROJECT_ROOT / DEEPMAP_PROJECT_KEY of their project --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18835"
        os.environ["DEEPMAP_EMBED_PORT"] = "18836"
        seen = []
        state = {"up": False}

        def _proc():
            state["up"] = True
            return _Proc(5401)
        with _Patch(subprocess, Popen=_server_popen(_proc, seen)), \
                _Patch(P, is_http_up=lambda host, port, timeout=2.0: state["up"], _pid_create_time=lambda pid: 1.0):
            ok = _quiet(P.start_mcp_server, "deepmap-search", target=str(a), wait=5)
        env = (seen[0].get("env") if seen else None) or {}
        check("server started", ok is True, repr(ok))
        check("DEEPMAP_PROJECT_ROOT = the project's sync root (the identity, for a repo-root project)",
              env.get("DEEPMAP_PROJECT_ROOT") == PR.project_identity(str(a)), str(env.get("DEEPMAP_PROJECT_ROOT")))
        check("DEEPMAP_PROJECT_KEY = the project's registry key",
              env.get("DEEPMAP_PROJECT_KEY") == PR.project_key(str(a)), str(env.get("DEEPMAP_PROJECT_KEY")))


# =========================================================================== #69 — stale config restart

def gate_heal_restarts_stale_own_server() -> None:
    print("\n-- #69: heal restarts THIS project's own server whose Neo4j config is stale (e.g. old password) --")
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "18837"
        os.environ["DEEPMAP_EMBED_PORT"] = "18838"
        os.environ["NEO4J_URI"] = "bolt://localhost:7689"
        cmd = _own_cmd("deepmap-search")

        def _run(start_pw, heal_pw, owned=True, bolt="up"):
            os.environ["NEO4J_PASSWORD"] = start_pw
            with _Patch(P, _pid_create_time=lambda pid: 400.0):
                P._record_mcp_server("deepmap-search", 18837, 5501, str(a) if owned else None)
            if not owned:
                P._mcp_pidfile("deepmap-search", 18837).write_text("5501", encoding="utf-8")
            os.environ["NEO4J_PASSWORD"] = heal_pw
            state = {"up": True, "spawned": 0}
            kill = _Spy(True)

            def _term(pid, tree=False):
                state["up"] = False
                return True

            def _proc():
                state["spawned"] += 1
                state["up"] = True
                return _Proc(5502)
            with _Patch(subprocess, Popen=_server_popen(_proc)), \
                    _Patch(P, is_http_up=lambda host, port, timeout=2.0: state["up"],
                           _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: cmd,
                           _bolt_state=lambda *a_, **k_: bolt,
                           _pid_create_time=lambda pid: 400.0, _terminate_pid=_Spy(_term)):
                ok = _quiet(P.start_mcp_server, "deepmap-search", password=heal_pw, target=str(a), wait=5)
            return ok, state["spawned"]
        ok, spawned = _run("wrong-pw", "deepmap")
        check("stale credentials in our own running server -> restarted (one fresh spawn)", ok is True and spawned == 1,
              f"ok={ok} spawned={spawned}")
        ok, spawned = _run("deepmap", "deepmap")
        check("control: same config -> left alone (no spawn)", ok is True and spawned == 0, f"ok={ok} spawned={spawned}")
        ok, spawned = _run("wrong-pw", "deepmap", owned=False)
        check("a server NOT provably ours is never restarted", spawned == 0, f"spawned={spawned}")
        ok, spawned = _run("deepmap", "wrong-pw", bolt="auth")
        check("a NEW config that can't connect (mcp-start with a wrong password) never replaces a serving one",
              ok is True and spawned == 0, f"ok={ok} spawned={spawned}")
        os.environ["NEO4J_PASSWORD"] = "s3cret-Pw"
        with _Patch(P, _pid_create_time=lambda pid: 400.0):
            P._record_mcp_server("deepmap-search", 18837, 5501, str(a))
        raw = P._mcp_ownerfile("deepmap-search", 18837).read_text(encoding="utf-8")
        check("the owner sidecar never stores the password in clear", "s3cret-Pw" not in raw, raw[:200])


class _RateLimited(Exception):
    """What neo4j 5.x/6.x raises while an account is locked after repeated failed logins (a plain
    ClientError — the driver does NOT map it to AuthError)."""
    code = "Neo.ClientError.Security.AuthenticationRateLimit"

    def __init__(self):
        super().__init__("{code: Neo.ClientError.Security.AuthenticationRateLimit} {message: The client has "
                         "provided incorrect authentication details too many times in a row.}")


def gate_rate_limited_neo4j_is_up() -> None:
    print("\n-- live-found: a Neo4j locking logins after failed attempts is RUNNING, not down --")
    fn = getattr(P, "_bolt_state", None)
    calls = {"n": 0}

    class _Drv:
        def __init__(self, fail_times):
            self.fail_times = fail_times

        def verify_connectivity(self):
            calls["n"] += 1
            if calls["n"] <= self.fail_times:
                raise _RateLimited()

        def close(self):
            pass
    with _Patch(P, _AUTH_LOCK_WAIT_S=0.01):
        calls["n"] = 0
        with _Patch(P, _make_driver=lambda *a, **k: _Drv(1)):
            got = fn("bolt://localhost:17991", "neo4j", "deepmap")
        check("rate-limited once, then accepted -> 'up' (waits out the lock)", got == "up", repr(got))
        calls["n"] = 0
        with _Patch(P, _make_driver=lambda *a, **k: _Drv(99)):
            got = fn("bolt://localhost:17991", "neo4j", "deepmap")
        check("still rate-limited -> 'auth' (running; a credentials problem) — never 'down'", got == "auth", repr(got))
    check("_is_auth_error recognises the rate-limit error", P._is_auth_error(_RateLimited()) is True)


# =========================================================================== W5 needs

def gate_instance_rm_purges_gout() -> None:
    print("\n-- W5: instance-rm --purge also removes the project's graphify output dirs (<home>/gout/...) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: False)
        keyA, keyB = PR.project_key(a), PR.project_key(b)
        gout = home / "gout"
        mine1 = gout / keyA
        mine2 = gout / "0123456789ab"                  # a subdirectory sync's dir, marked as A's
        theirs = gout / "fedcba987654"
        for d in (mine1, mine2, theirs):
            (d / "cache").mkdir(parents=True, exist_ok=True)
        (mine2 / "deepmap_owner.json").write_text(json.dumps({"project_key": keyA, "root": str(a / "sub")}),
                                                  encoding="utf-8")
        (theirs / "deepmap_owner.json").write_text(json.dumps({"project_key": keyB, "root": str(b)}),
                                                   encoding="utf-8")
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: True,
                    _stop_mcp_servers_for_key=lambda k: None):
            rc = _quiet(P.instance_rm, str(a), yes=True, purge=True)
        check("instance-rm --purge rc 0", rc == 0, str(rc))
        check("A's gout dirs (root + marked subdir) removed", not mine1.exists() and not mine2.exists(),
              f"{mine1.exists()} {mine2.exists()}")
        check("another project's gout dir is kept", theirs.exists())
        # a purge after a plain instance-rm (no instance left) still purges the entry + gout
        PR.allocate_ports(b)
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: True,
                    _stop_mcp_servers_for_key=lambda k: None):
            _quiet(P.instance_rm, str(b), yes=True)                    # drops B's Neo4j fields only
            rc, out = _captured(P.instance_rm, str(b), yes=True, purge=True)
        reg = PR.load_registry() or {}
        check("purge with no instance left: rc 0, the registry entry is gone",
              rc == 0 and keyB not in (reg.get("projects") or {}), f"rc={rc} {out.strip()[-200:]}")
        check("... and its gout dir too", not theirs.exists())


def gate_instance_rm_unusable_registry() -> None:
    print("\n-- D1: instance-rm refuses (does not guess) when the registry is unusable --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        _bump_registry_version(home)
        stops = []
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: (stops.append(project_key) or True),
                    _stop_mcp_servers_for_key=lambda k: None):
            rc, out = _captured(P.instance_rm, str(a), yes=True)
        check("rc 2 + names the registry", rc == 2 and "registry" in out.lower(), f"rc={rc} {out.strip()[-200:]}")
        check("... nothing stopped or deleted", stops == [], str(stops))


class _FailedSync:
    """A sync that extracted 0 nodes: it writes the FAILED manifest + records last_verdict."""
    last_verdict = None

    def __init__(self, reason):
        self.reason = reason

    def sync(self, target, **k):
        d = Path(target) / ".deepmap"
        d.mkdir(parents=True, exist_ok=True)
        (d / "graph_build.json").write_text(json.dumps({
            "verdict": "FAILED", "nodes": 0, "extractor": "ast", "extractor_requested": "auto",
            "extractor_fallback_reason": self.reason,
            "reasons": ["0 code nodes extracted — auto fell back to the Python-only ast path"]}), encoding="utf-8")
        self.last_verdict = "FAILED"
        return True


def gate_setup_failed_hint_quotes_manifest() -> None:
    print("\n-- W5: setup's FAILED hint quotes the recorded cause (never guesses 'graphify isn't installed') --")
    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        fake = _FailedSync("graphify exited 1: ModuleNotFoundError: No module named 'tree_sitter_pascal'")
        mcp = _Spy(True)
        with _Patch(P, ensure_neo4j=lambda **k: True, _load=lambda name: fake,
                    verify_search=lambda *a_, **k_: False, start_mcp_servers=mcp,
                    verify_mcp=lambda *a_, **k_: True):
            ok, out = _captured(P.setup, str(a), password="x")
        check("setup returns False", ok is False, repr(ok))
        check("the recorded cause is quoted", "tree_sitter_pascal" in out, out.strip()[-500:])
        check("no guess that graphify isn't installed", "usually means graphify isn't installed" not in out,
              out.strip()[-500:])
        check("says 'setup verdict: FAILED'", "setup verdict: FAILED" in out, out.strip()[-300:])
        check("no MCP servers started for a FAILED graph", mcp.calls == [], str(mcp.calls))


# =========================================================================== D6 — auth remedy

def gate_auth_remedy_runnable() -> None:
    print("\n-- D6: an auth failure's remedy is runnable in that state --")
    with _Sandbox() as (home, work):
        base = P._auth_failed_text("bolt://localhost:7687")
        check("base store: never suggests instance-rm (it refuses the base store)", "instance-rm" not in base, base)
        check("base store: names the store's data folder + /deep-map:setup",
              str(P.paths(None)["data"]) in base and "/deep-map:setup" in base, base)
        inst = P._auth_failed_text("bolt://localhost:7689", project_key="abc123def456")
        check("per-project instance: `instance-rm --key <key> --yes`", "instance-rm --key abc123def456 --yes" in inst,
              inst)
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)        # base 7687, as the #54 field case
    with _Sandbox(bolt="auth") as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        with _Patch(P, start_mcp_servers=_Spy(True), _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(a)])
        reason = str(_heal_status(home, a).get("reason") or "")
        check("heal's persisted auth reason says 'credentials'", "credential" in reason.lower(), reason)
        check("... is short (<= 300 chars) and never suggests instance-rm on the base store",
              len(reason) <= 300 and "instance-rm" not in reason, f"{len(reason)}: {reason}")


# =========================================================================== W4 — verify wording

def gate_verify_warmup_wording_and_deadline() -> None:
    print("\n-- W4: verify_mcp words 'still starting up' as a cold start; deadline covers warm-up + a call --")
    captured = []
    starting = ("FAIL: RuntimeError: tool call returned isError: [TextContent(type='text', text='DeepMap is "
                "still starting up after 300s (loading the embedding model / connecting to Neo4j) — retry')]")

    def _run(cmd, **k):
        captured.append(list(cmd))
        return types.SimpleNamespace(returncode=1, stdout="", stderr=starting)
    with _Sandbox():
        with _Patch(P, subprocess=types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired)):
            ok, out = _captured(P.verify_mcp, "deepmap-search")
        check("verify -> False", ok is False, repr(ok))
        check("worded as 'still starting up' (a cold start), not 'tool call failed'",
              "still starting up" in out and "tool call failed" not in out, out.strip()[-300:])
        deadline = None
        if captured and "--deadline" in captured[0]:
            try:
                deadline = float(captured[0][captured[0].index("--deadline") + 1])
            except Exception:
                deadline = None
        import mcp_bounds as MB
        need = MB.warmup_timeout_s() + MB.tool_timeout_s()
        check(f"default deadline >= warm-up + one call ({need:g}s)", deadline is not None and deadline >= need,
              repr(deadline))


# =========================================================================== #105 — .mcp.json writes

def gate_mcp_json_line_endings_and_tracked_notice() -> None:
    print("\n-- #105: write_mcp_json keeps the file's line endings and flags a git-TRACKED .mcp.json --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        path = a / ".mcp.json"
        lf = '{\n  "mcpServers": {\n    "chrome": {"command": "npx", "args": ["-y", "x"]}\n  }\n}\n'
        path.write_bytes(lf.encode("utf-8"))
        _, out = _captured(P.write_mcp_json, a, "127.0.0.1", 18841, 18842)
        raw = path.read_bytes()
        check("an LF file stays LF (no CRLF rewrite)", b"\r\n" not in raw, repr(raw[:80]))
        check("untracked file -> no 'tracked' notice", "tracked" not in out.lower(), out.strip()[-200:])
        crlf = lf.replace("\n", "\r\n")
        path.write_bytes(crlf.encode("utf-8"))
        P.write_mcp_json(a, "127.0.0.1", 18843, 18844)
        raw = path.read_bytes()
        check("a CRLF file stays CRLF (every line)", raw.count(b"\n") == raw.count(b"\r\n") and b"\r\n" in raw,
              repr(raw[:80]))
        obj = json.loads(raw.decode("utf-8"))
        check("... and still carries the other server + both deepmap entries",
              {"chrome", "deepmap-search", "deepmap-embed"} <= set(obj.get("mcpServers", {})), str(obj))
        _git("add", ".mcp.json", cwd=a)
        _, out = _captured(P.write_mcp_json, a, "127.0.0.1", 18845, 18846)
        if shutil.which("git"):
            check("a git-TRACKED .mcp.json change is announced (per-machine ports)", "tracked" in out.lower(),
                  out.strip()[-300:])
        _, out = _captured(P.write_mcp_json, a, "127.0.0.1", 18845, 18846)
        check("a semantic no-op writes nothing and announces nothing", "tracked" not in out.lower(),
              out.strip()[-200:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-2 gates: one route, verified legacy, pid identity (hermetic)")
    print("=" * 68)
    for g in (gate_unusable_registry_is_no_route, gate_lookup_route_is_the_shared_resolver,
              gate_legacy_route_status_verifies_owner, gate_legacy_route_heal_verifies_owner,
              gate_legacy_route_stop_and_mcp_start, gate_escape_hatch_stop_targets_named_instance,
              gate_mcp_stop_honours_route_problem,
              gate_pid_create_time_real, gate_stop_neo4j_creation_time, gate_mcp_creation_time,
              gate_mcp_stop_kills_tree, gate_posix_tree_kill_never_own_group,
              gate_server_env_project_guard, gate_heal_restarts_stale_own_server, gate_rate_limited_neo4j_is_up,
              gate_instance_rm_purges_gout, gate_instance_rm_unusable_registry,
              gate_setup_failed_hint_quotes_manifest, gate_auth_remedy_runnable,
              gate_verify_warmup_wording_and_deadline, gate_mcp_json_line_endings_and_tracked_notice):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
