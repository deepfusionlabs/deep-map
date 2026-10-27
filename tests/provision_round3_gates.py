#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-3 provisioner fixes (build/provision.py), on top of the
round-2 gates (tests/provision_route_identity_gates.py):

  E2  process identity on LINUX = raw start ticks (/proc/<pid>/stat field 22) + boot_id — never an epoch
      derived from /proc/stat btime, which moves whenever the wall clock is stepped (NTP, a WSL2 / VM
      resume): a btime shift keeps the identity, a different boot_id or tick count breaks it; stop_neo4j /
      stop_mcp_server still stop their own process after a shift. A MISMATCH never deletes a pid file whose
      process still runs the expected command (reported, left alone); macOS reads `ps -o lstart=` in UTC.
      instance-rm never deletes an instance dir under a live Neo4j it could not stop.
  E5  the MCP servers get the SYNC root (the outermost synced dir up to the git toplevel — where the
      project's .deepmap/ state lives) as DEEPMAP_PROJECT_ROOT, the identity key as DEEPMAP_PROJECT_KEY.
  rev _base_adopt_ok never counts the adopter's OWN registry entry as proof it owns a DOWN legacy store:
      it defers unless the base store's owner record (written when setup syncs the base / a live owner read)
      names this project; heal on the LEGACY route never starts the base Neo4j (only an already-running,
      owner-verified one is served).
  rev mcp-stop / heal via the .mcp.json FALLBACK pair: a pair the registry gives another project is no
      route; a server with no owner record on a pair not registered to this project is never stopped.
  #105 write_mcp_json is a minimal TEXT edit: only the two deepmap entries change, every other byte stays.
  D6  auth texts are route-aware like the CLI's (_auth_hint), incl. Neo4j's AuthenticationRateLimit lock.
  W4  verify_mcp: a keyword-only answer ("vector search is unavailable" note) is a DEGRADED verify (a
      WARNING + "setup verdict: DEGRADED"), not a silent pass; the smoke test prints such notes.
  W5  instance-rm --purge deletes every graphify output dir of the project (graphify_extract's own layout),
      and the dirs / instances of the project's registry ALIASES (a migrated key, E1).

Hermetic: DEEPMAP_HOME -> a throwaway dir; port/bolt probes, drivers, pid liveness / command line /
creation time, a FAKE /proc tree, Popen and every kill are stubbed. NO Neo4j, NO processes, NO network.
Exit 0 = GREEN.
"""
from __future__ import annotations

import asyncio
import calendar
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402
import provision as P       # noqa: E402

_REAL_CT = P._pid_create_time          # the real seams, captured before any gate stubs them
_REAL_BOLT_STATE = P._bolt_state

_PASS = 0
_FAIL = 0
_ENV_KEYS = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI", "NEO4J_USER",
             "NEO4J_PASSWORD", "DEEPMAP_MCP_START_WAIT", "DEEPMAP_MCP_VERIFY_DEADLINE",
             "DEEPMAP_MCP_WARMUP_TIMEOUT_S", "DEEPMAP_MCP_TOOL_TIMEOUT_S", "DEEPMAP_PROJECT_ROOT",
             "DEEPMAP_PROJECT_KEY")
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


def _mk_project(parent: Path, name: str, *, marker: bool = False, git: bool = True) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    if git:
        _git("init", "-q", cwd=d)
        (d / ".git").mkdir(exist_ok=True)   # never let a missing git make the work-tree walk escape
    if marker:
        _manifest(d)
    return d.resolve()


def _manifest(d: Path) -> None:
    (d / ".deepmap").mkdir(parents=True, exist_ok=True)
    (d / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 5}', encoding="utf-8")


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
    gate stubs it; the driver never connects anywhere real. Restores everything on exit."""

    def __init__(self, occupied=(), bolt="down", escape_hatch=False):
        self.occupied = set(occupied)
        self.bolt = bolt
        self.escape_hatch = escape_hatch

    def __enter__(self):
        self._home = tempfile.mkdtemp(prefix="dmr3h")
        self._work = tempfile.mkdtemp(prefix="dmr3w")
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
                         _make_driver=_driver_factory(_GraphDrv(exc=_neo4j_exc("ServiceUnavailable"))),
                         _NEO4J_ROUTE_SOURCE=None,
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


class _Proc:
    def __init__(self, pid=71001):
        self.pid, self.returncode = pid, None

    def poll(self):
        return None


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


# ---- a scripted fake graph -------------------------------------------------------------------------
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
        if "_DeepMapMeta" in q and "owning_root" in q:
            return _Res([{"owning_root": self.drv.owner}] if self.drv.owner else [])
        if "count(r)" in q:
            return _Res([{"c": 3}])
        if "count(n)" in q:
            return _Res([{"c": self.drv.nodes}])
        if "dbms.components" in q:
            return _Res([{"edition": "community"}])
        return _Res([])


class _GraphDrv:
    def __init__(self, owner=None, nodes=0, exc=None):
        self.owner, self.nodes, self.exc = owner, nodes, exc
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


class _RateLimited(Exception):
    """neo4j 5.x/6.x while an account is locked after repeated failed logins (a plain ClientError)."""
    code = "Neo.ClientError.Security.AuthenticationRateLimit"

    def __init__(self):
        super().__init__("{code: Neo.ClientError.Security.AuthenticationRateLimit} {message: The client has "
                         "provided incorrect authentication details too many times in a row.}")


def _launcher_cmd(key=None) -> str:
    return f'/bin/sh {P.paths(None, key)["neo4j_bin"]} console'


def _own_cmd(name) -> str:
    return f"/usr/bin/python3 /plugins/deep-map/build/{P.MCP_SERVERS[name]['script']}"


# ---- a fake Linux /proc ------------------------------------------------------------------------------
def _fake_proc(root: Path, pid: int, *, ticks: int, btime: int, boot_id: str, cmdline: str = "",
               comm: str = "java") -> None:
    """/proc/<pid>/{stat,cmdline,comm} + /proc/stat (btime) + /proc/sys/kernel/random/boot_id."""
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10          # fields[19] = field 22 (starttime)
    (d / "stat").write_text(f"{pid} ({comm} x) " + " ".join(fields) + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(("\0".join(cmdline.split(" ")) + "\0").encode("utf-8") if cmdline else b"")
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    _set_btime(root, btime)
    _set_boot_id(root, boot_id)


def _set_btime(root: Path, btime: int) -> None:
    (root / "stat").write_text(f"cpu  10 20 30 40\nbtime {btime}\nprocesses 42\n", encoding="utf-8")


def _set_boot_id(root: Path, boot_id: str) -> None:
    (root / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(boot_id + "\n", encoding="utf-8")


class _Linux:
    """provision behaves as on Linux, reading the FAKE /proc under `root`."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        # _pid_alive's POSIX probe is os.kill(pid, 0) — on WINDOWS that TERMINATES the process — so under
        # the Linux personality every liveness probe is stubbed and os.kill/killpg are disarmed.
        self._p = _Patch(P, is_windows=lambda: False, _PROC=str(self.root), _pid_alive=lambda pid: True).__enter__()
        self._os = _Patch(os, kill=_no_signal, killpg=_no_signal).__enter__()
        return self

    def __exit__(self, *a):
        self._os.__exit__(*a)
        self._p.__exit__(*a)


def _no_signal(*a, **k):
    raise AssertionError(f"a gate tried to signal a real process: {a}")


# =========================================================================== E2 — Linux identity

def gate_linux_identity_ticks_and_boot_id() -> None:
    print("\n-- E2: Linux identity = start ticks + boot_id; a btime shift keeps it, a reboot breaks it --")
    tmp = Path(tempfile.mkdtemp(prefix="dmr3p"))
    try:
        proc = tmp / "proc"
        _fake_proc(proc, 4301, ticks=123456, btime=1_700_000_000, boot_id="0b1d-aaaa")
        with _Linux(proc):
            rec = P._pid_create_time(4301)
            check("the recorded identity is NOT an epoch derived from btime (not a float)",
                  not isinstance(rec, float), repr(rec))
            check("... it carries the boot_id and the raw start ticks",
                  "0b1d-aaaa" in str(rec) and "123456" in str(rec), repr(rec))
            check("json-serialisable (it is written into the pid identity file / owner sidecar)",
                  json.loads(json.dumps(rec)) == rec, repr(rec))
            _set_btime(proc, 1_700_003_600)                      # the wall clock was stepped by an hour
            check("btime moved by 1 h (NTP / WSL2 resume): SAME process", P._pid_identity(4301, recorded=rec) is True,
                  repr(P._pid_identity(4301, recorded=rec)))
            _set_btime(proc, 1_699_990_000)
            check("btime moved backwards: SAME process", P._pid_identity(4301, recorded=rec) is True)
            _set_boot_id(proc, "0b1d-bbbb")                      # rebooted: the pid now belongs to someone else
            check("a different boot_id: NOT the same process", P._pid_identity(4301, recorded=rec) is False,
                  repr(P._pid_identity(4301, recorded=rec)))
            _fake_proc(proc, 4301, ticks=654321, btime=1_700_000_000, boot_id="0b1d-aaaa")
            check("same boot, different start ticks (recycled pid): NOT the same process",
                  P._pid_identity(4301, recorded=rec) is False)
            check("a process that does not exist -> None (unknown)", P._pid_create_time(4302) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gate_linux_stop_after_btime_shift() -> None:
    print("\n-- E2: after a wall-clock step, stop_neo4j / stop_mcp_server still stop THEIR OWN process --")
    with _Sandbox() as (home, work):
        proc = work / "proc"
        key = "abcdef012345"
        a = _mk_project(work, "a")
        with _Linux(proc), _Patch(P, _pid_create_time=_REAL_CT):
            p = P.paths(None, key)
            _fake_proc(proc, 4401, ticks=5000, btime=1_700_000_000, boot_id="b-1", cmdline=_launcher_cmd(key))
            (p["neo4j_bin"]).parent.mkdir(parents=True, exist_ok=True)
            with _Patch(subprocess, Popen=lambda *a_, **k_: _Proc(4401)):
                _quiet(P.start_neo4j, "5.26.0", key)
            _set_btime(proc, 1_700_007_200)                     # a 2 h step since the start
            kill = _Spy(True)
            with _Patch(P, _pid_alive=lambda pid: True, _terminate_pid=kill):
                ok, out = _captured(P.stop_neo4j, project_key=key)
            check("stop_neo4j after a btime shift: the instance IS stopped", ok is True and len(kill.calls) == 1,
                  f"ok={ok} kills={kill.calls} {out.strip()[-200:]}")
            # MCP server
            os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = "18861", "18862"
            _fake_proc(proc, 4402, ticks=7000, btime=1_700_000_000, boot_id="b-1", cmdline=_own_cmd("deepmap-search"),
                       comm="python3")
            _quiet(P._record_mcp_server, "deepmap-search", 18861, 4402, str(a))
            _set_btime(proc, 1_700_086_400)                     # a day's step (a VM resumed)
            kill = _Spy(True)
            with _Patch(P, _pid_alive=lambda pid: True, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
                own = P._own_mcp_server_pid("deepmap-search", 18861, str(a))
                ok, out = _captured(P.stop_mcp_server, "deepmap-search", target=str(a))
            check("_own_mcp_server_pid still recognises its own server after the shift", own == 4402, repr(own))
            check("stop_mcp_server after the shift: stopped", ok is True and len(kill.calls) == 1,
                  f"ok={ok} kills={kill.calls} {out.strip()[-200:]}")


def gate_mismatch_keeps_pidfile_of_expected_command() -> None:
    # ROUND-4 refinement (binding F-plan): only a mismatch that is NOT proof keeps the pid file — the no-record
    # pid-file-mtime fallback (a clock step can fake "started after the pid file"). Two COMPARABLE start stamps
    # that differ ARE proof and now clear the stale pid file (tests/provision_round4_gates.py).
    print("\n-- E2: an UNPROVABLE (no-record) mismatch never deletes a pid file whose process runs the expected command --")
    with _Sandbox() as (home, work):
        key = "abcdef012345"
        p = P.paths(None, key)
        ident = p["pidfile"].with_name("neo4j.pid.identity")
        p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
        p["pidfile"].write_text("4501", encoding="utf-8")           # an older build's pid file: no identity record
        mt = p["pidfile"].stat().st_mtime
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(key),
                    _pid_create_time=lambda pid: mt + 900.0, _terminate_pid=kill):
            ok, out = _captured(P.stop_neo4j, project_key=key)
        check("no-record mismatch + DeepMap's Neo4j command: NOT killed", kill.calls == [] and not ok,
              f"ok={ok} {kill.calls}")
        check("... the pid file is KEPT", p["pidfile"].exists() and not ident.exists())
        check("... and it is reported (not silently)", "4501" in out and ("not" in out.lower()), out.strip()[-300:])
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: "/usr/sbin/sshd -D",
                    _pid_create_time=lambda pid: mt + 900.0, _terminate_pid=kill):
            _quiet(P.stop_neo4j, project_key=key)
        check("control: an UNRELATED command -> the stale pid file is cleared, nothing killed",
              not p["pidfile"].exists() and kill.calls == [], str(kill.calls))
        # MCP: a sidecar without a start stamp (unknown when recorded) -> the pid-file fallback decides
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = "18863", "18864"
        with _Patch(P, _pid_create_time=lambda pid: None):
            _quiet(P._record_mcp_server, "deepmap-search", 18863, 4601, str(a))
        pf, of = P._mcp_pidfile("deepmap-search", 18863), P._mcp_ownerfile("deepmap-search", 18863)
        mt = pf.stat().st_mtime
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _own_cmd("deepmap-search"),
                    _pid_create_time=lambda pid: mt + 900.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: True):
            ok, out = _captured(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("MCP: no-record mismatch + the server command: NOT killed, pid file + owner sidecar KEPT",
              kill.calls == [] and pf.exists() and of.exists(), f"{kill.calls} pf={pf.exists()} of={of.exists()}")
        check("... reported as not stopped", ok is False and "4601" in out, f"ok={ok} {out.strip()[-200:]}")


def gate_macos_lstart_in_utc() -> None:
    print("\n-- E2: macOS/BSD read `ps -o lstart=` in UTC (1-second resolution, no local-time/DST ambiguity) --")
    seen = {}
    tmp = Path(tempfile.mkdtemp(prefix="dmr3m"))

    def _run(cmd, **k):
        seen["cmd"], seen["env"] = list(cmd), dict(k.get("env") or {})
        return types.SimpleNamespace(returncode=0, stdout="Mon Sep 21 10:00:00 2026\n", stderr="")
    try:
        with _Linux(tmp), _Patch(P, subprocess=types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired)):
            got = P._pid_create_time(4701)
        want = float(calendar.timegm((2026, 9, 21, 10, 0, 0, 0, 0, 0)))
        check("ps is asked for lstart", "lstart=" in " ".join(seen.get("cmd") or []), str(seen.get("cmd")))
        check("... with TZ pinned to UTC (and the C locale)",
              str((seen.get("env") or {}).get("TZ", "")).upper().startswith("UTC")
              and (seen.get("env") or {}).get("LC_ALL") == "C", str({k: v for k, v in (seen.get("env") or {}).items()
                                                                      if k in ("TZ", "LC_ALL")}))
        check("... parsed as UTC (calendar.timegm), whatever the local zone", got == want, f"{got} vs {want}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gate_instance_rm_never_deletes_under_live_neo4j() -> None:
    print("\n-- E2: instance-rm never deletes an instance dir under a live Neo4j it could not stop --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)       # its own instance (7689)
        k = PR.project_key(a)
        p = P.paths(None, k)
        for d in ("data", "run"):
            p[d].mkdir(parents=True, exist_ok=True)
        (p["data"] / "databases").mkdir(exist_ok=True)
        p["pidfile"].write_text("4801", encoding="utf-8")
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(k),
                    _pid_create_time=lambda pid: None, _terminate_pid=kill, _RM_RETRY=(1, 0.0)):
            rc, out = _captured(P.instance_rm, str(a), yes=True)
        check("unverifiable live Neo4j: rc 1, nothing killed", rc == 1 and kill.calls == [], f"rc={rc} {kill.calls}")
        check("... the instance dir is NOT deleted under it", p["data"].exists(), str(p["data"]))
        check("... the registry still records the instance", PR.lookup_neo4j_ports(a) is not None)
        check("... and it says why (the pid + stop it by hand)", "4801" in out, out.strip()[-300:])


# =========================================================================== E5 — sync root

def gate_server_env_sync_root() -> None:
    print("\n-- E5: MCP servers get the SYNC root as DEEPMAP_PROJECT_ROOT (where .deepmap/ state lives) --")
    import retrieval_log as RL
    with _Sandbox() as (home, work):
        repo = _mk_project(work, "mono")
        svc = repo / "services" / "api"
        svc.mkdir(parents=True, exist_ok=True)
        _manifest(svc)                                     # a sub-project set up on its own
        os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = "18865", "18866"
        seen = []
        state = {"up": False}

        def _proc():
            state["up"] = True
            return _Proc(5401)
        with _Patch(subprocess, Popen=_server_popen(_proc, seen)), \
                _Patch(P, is_http_up=lambda host, port, timeout=2.0: state["up"], _pid_create_time=lambda pid: 1.0):
            ok = _quiet(P.start_mcp_server, "deepmap-search", target=str(svc), wait=5)
        env = (seen[0].get("env") if seen else None) or {}
        got = env.get("DEEPMAP_PROJECT_ROOT")
        check("server started", ok is True, repr(ok))
        check("sub-project synced on its own: ROOT = that sync root (not the git toplevel)",
              got is not None and os.path.normcase(str(Path(got).resolve())) == os.path.normcase(str(svc.resolve())),
              f"{got} vs {svc}")
        rl = RL.project_root(svc)
        check("... == retrieval_log.project_root (the CLI / access hook / tuner root)",
              got is not None and rl is not None and os.path.normcase(str(Path(got).resolve()))
              == os.path.normcase(str(Path(rl).resolve())), f"{got} vs {rl}")
        check("DEEPMAP_PROJECT_KEY = the identity key", env.get("DEEPMAP_PROJECT_KEY") == PR.project_key(str(svc)),
              str(env.get("DEEPMAP_PROJECT_KEY")))
        # the repo root synced: a deeper target resolves to the root that holds the manifest
        _manifest(repo)
        deeper = repo / "lib" / "x"
        deeper.mkdir(parents=True, exist_ok=True)
        seen.clear()
        state["up"] = False
        os.environ["DEEPMAP_SEARCH_PORT"] = "18867"
        with _Patch(subprocess, Popen=_server_popen(_proc, seen)), \
                _Patch(P, is_http_up=lambda host, port, timeout=2.0: state["up"], _pid_create_time=lambda pid: 1.0):
            _quiet(P.start_mcp_server, "deepmap-search", target=str(deeper), wait=5)
        got = ((seen[0].get("env") if seen else None) or {}).get("DEEPMAP_PROJECT_ROOT")
        check("repo root synced, target a subdir: ROOT = the repo root",
              got is not None and os.path.normcase(str(Path(got).resolve())) == os.path.normcase(str(repo)),
              f"{got} vs {repo}")


# =========================================================================== review — base adoption

def _legacy_store() -> None:
    (P.paths(None)["databases"] / "neo4j").mkdir(parents=True, exist_ok=True)


def _base_owner_file() -> Path:
    return P.paths(None)["data"] / "deepmap_base_owner.json"


def gate_base_adopt_down_store_needs_proof() -> None:
    print("\n-- review: a DOWN legacy store is adopted only on PROOF of ownership (never 'only my own entry') --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        _legacy_store()
        PR.allocate_ports(a)                      # the adopter's OWN, new, MCP-only entry
        v, out = _captured(P._base_adopt_ok, a)
        check("base down + legacy store + only the adopter's own entry -> DEFER (False)", v is False, repr(v))
        check("... and says so", "7687" in out, out.strip()[-300:])
        rec = getattr(P, "_write_base_owner_record", None)
        check("provision._record_base_owner / _write_base_owner_record exist",
              callable(rec) and callable(getattr(P, "_record_base_owner", None)))
        if callable(rec):
            rec(PR.project_identity(str(a)))
            check("... the owner record lives next to the base store", _base_owner_file().is_file())
            check("owner record names this project -> adopt (True)", _quiet(P._base_adopt_ok, a) is True)
            b = _mk_project(work, "b")
            rec(PR.project_identity(str(b)))
            check("owner record names ANOTHER project -> defer (False)", _quiet(P._base_adopt_ok, a) is False)
    with _Sandbox() as (home, work):
        # the finding end-to-end: B owns the (down) legacy store with no registry entry; A runs setup
        a = _mk_project(work, "a")
        _legacy_store()
        _quiet(P._resolve_ports, str(a), allocate=True)
        check("setup of A (B's legacy store down): A gets its OWN instance, never 7687",
              PR.lookup_neo4j_ports(a) not in (None, (PR.BASE_BOLT, PR.BASE_HTTP)), str(PR.lookup_neo4j_ports(a)))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        check("control: clean machine (no legacy store) -> adopt (zero drift)", _quiet(P._base_adopt_ok, a) is True)


def gate_setup_records_base_owner() -> None:
    print("\n-- review: setup records the base store's owner once its sync on 7687 succeeded --")

    class _OkSync:
        last_verdict = None

        def sync(self, target, **k):
            self.last_verdict = "READY"
            return True
    def _ensure(**k):     # the real ensure_neo4j leaves the store's data dir behind
        P.paths(None, k.get("project_key"))["data"].mkdir(parents=True, exist_ok=True)
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)   # (a base store may exist on the machine)
        return True
    stubs = dict(ensure_neo4j=_ensure, _load=lambda name: _OkSync(), verify_search=lambda *a_, **k_: True,
                 start_mcp_servers=lambda **k: True, verify_mcp=lambda *a_, **k_: True,
                 _print_mcp_hint=lambda *a_, **k_: None,
                 _legacy_base_instance_alive=lambda: True)      # the base setup started is DeepMap's own
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        with _Patch(P, **stubs):
            ok = _quiet(P.setup, str(a), password="deepmap")
        check("setup (first project, base 7687) ok", ok is True, repr(ok))
        rec = {}
        try:
            rec = json.loads(_base_owner_file().read_text(encoding="utf-8"))
        except Exception:
            pass
        check("the base-store owner record names this project",
              PR._same_project_path(rec.get("root") or "", PR.project_identity(str(a))), str(rec))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)     # its own instance (7689)
        with _Patch(P, **stubs):
            _quiet(P.setup, str(a), password="deepmap")
        check("a project on its OWN instance never writes the base-store record", not _base_owner_file().exists())
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a")
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)      # DeepMap's own base store on disk
        drv = _driver_factory(_GraphDrv(owner=PR.project_identity(str(a)), nodes=4))
        with _Patch(P, _make_driver=drv, _legacy_base_instance_alive=lambda: False):
            v = _quiet(P._base_adopt_ok, a)
        check("a LIVE base that is NOT DeepMap's own instance (a foreign Neo4j accepting our credentials): "
              "adopt, but nothing recorded about DeepMap's store", v is True and not _base_owner_file().exists(),
              f"{v} {_base_owner_file().exists()}")
        with _Patch(P, _make_driver=drv, _legacy_base_instance_alive=lambda: True):
            v = _quiet(P._base_adopt_ok, a)
        check("DeepMap's own LIVE base owned by this project -> adopt ...", v is True, repr(v))
        check("... and the proof is recorded for a later down-store adoption", _base_owner_file().is_file())


def gate_heal_legacy_never_starts_base() -> None:
    print("\n-- review: heal on the LEGACY route never starts the base Neo4j (owner unknown while it is down) --")
    with _Sandbox(bolt="down") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)                        # MCP pair only -> the legacy 7687 route
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(c)])
        rec = json.loads((home / "run" / f"heal-{PR.project_key(c)}.json").read_text(encoding="utf-8"))
        check("base down: ensure_neo4j NOT called (nothing started)", ens.calls == [], str(ens.calls))
        check("... no MCP servers", mcp.calls == [], str(mcp.calls))
        reason = str(rec.get("reason") or "")
        check("... neo4j_ok False; the reason says no instance of its own + /deep-map:setup",
              rec.get("neo4j_ok") is False and "own" in reason and "/deep-map:setup" in reason, str(rec))
    with _Sandbox(bolt="auth") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            _quiet(P.main, ["heal", str(c)])
        rec = json.loads((home / "run" / f"heal-{PR.project_key(c)}.json").read_text(encoding="utf-8"))
        check("base answering but rejecting credentials: nothing started; reason says 'credentials'",
              ens.calls == [] and mcp.calls == [] and "credential" in str(rec.get("reason") or "").lower(), str(rec))
    with _Sandbox(bolt="up") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=str(c), nodes=5))):
            rc = _quiet(P.main, ["heal", str(c)])
        check("control: base already up + owner verified -> MCP started, rc 0", rc == 0 and len(mcp.calls) == 1,
              f"rc={rc} {mcp.calls}")
    with _Sandbox(bolt="down") as (home, work):
        c = _mk_project(work, "c", marker=True)
        PR.allocate_ports(c)
        rc, out = _captured(P.main, ["status", str(c)])
        check("status on the legacy route, base down: rc 3, says 'no instance of its own' + /deep-map:setup",
              rc == 3 and "instance of its own" in out and "/deep-map:setup" in out, f"rc={rc} {out.strip()[-300:]}")
        check("... and never suggests `heal` (which does not start the legacy store)", " heal" not in out,
              out.strip()[-300:])


# =========================================================================== review — .mcp.json fallback

def _mcp_json(d: Path, s: int, e: int) -> None:
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": f"http://127.0.0.1:{s}/mcp"},
        "deepmap-embed": {"type": "http", "url": f"http://127.0.0.1:{e}/mcp"}}}), encoding="utf-8")


def gate_mcp_stop_via_mcp_json_fallback() -> None:
    print("\n-- review: mcp-stop via the .mcp.json fallback pair never stops another project's server --")
    procs = {5104: _own_cmd("deepmap-search"), 5108: _own_cmd("deepmap-embed")}

    def _pidfiles(pair):
        for name, port, pid in (("deepmap-search", pair[0], 5104), ("deepmap-embed", pair[1], 5108)):
            pf = P._mcp_pidfile(name, port)
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(pid), encoding="utf-8")       # pre-upgrade servers: NO owner sidecar
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")                           # a copy / worktree of A: A's .mcp.json, unregistered
        pa = PR.allocate_ports(a)
        _mcp_json(b, *pa)
        _pidfiles(pa)
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _pid_create_time=lambda pid: 1.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: True):
            rc, out = _captured(P.main, ["mcp-stop", str(b)])
        check("pair registered to ANOTHER project: no kill", kill.calls == [], str(kill.calls))
        check("... rc 0 + says whose ports they are / nothing to stop", rc == 0 and "nothing to stop" in out,
              f"rc={rc} {out.strip()[-300:]}")
        ens, mcp = _Spy(True), _Spy(True)
        _clear_port_env()
        _manifest(b)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=str(b), nodes=3))):
            _quiet(P.main, ["heal", str(b)])
        check("heal B on A's registered pair: no servers started", mcp.calls == [], str(mcp.calls))
    with _Sandbox() as (home, work):
        b = _mk_project(work, "b")                           # nobody registered (a lost registry)
        _mcp_json(b, 8787, 8788)
        _pidfiles((8787, 8788))
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _pid_create_time=lambda pid: 1.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: True):
            rc, out = _captured(P.main, ["mcp-stop", str(b)])
        check("pair not registered to this project + no owner record: no kill", kill.calls == [], str(kill.calls))
        check("... rc 0, reported as not provably this project's", rc == 0 and "not" in out.lower(),
              f"rc={rc} {out.strip()[-300:]}")
        # control: the SAME pair, with owner sidecars naming B -> stopped
        with _Patch(P, _pid_create_time=lambda pid: 1.0):
            _quiet(P._record_mcp_server, "deepmap-search", 8787, 5104, str(b))
            _quiet(P._record_mcp_server, "deepmap-embed", 8788, 5108, str(b))
        _clear_port_env()
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: pid in procs, _pid_cmdline=lambda pid: procs.get(pid),
                    _pid_create_time=lambda pid: 1.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
            rc = _quiet(P.main, ["mcp-stop", str(b)])
        check("control: owner records name THIS project -> both stopped", rc == 0 and len(kill.calls) == 2,
              f"rc={rc} {kill.calls}")


# =========================================================================== #105 — minimal diff

def gate_write_mcp_json_minimal_diff() -> None:
    print("\n-- #105: write_mcp_json changes ONLY the deepmap entries (every other byte kept) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        path = a / ".mcp.json"
        orig = ('{\n  "mcpServers": {\n    "chrome": {"command": "npx", "args": ["-y", "x"]},\n'
                '    "deepmap-search": {"type": "http", "url": "http://127.0.0.1:8787/mcp"},\n'
                '    "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:8788/mcp"}\n  },\n'
                '  "x": [1,2,3], "note": "café"\n}\n')
        path.write_bytes(orig.encode("utf-8"))
        _quiet(P.write_mcp_json, a, "127.0.0.1", 18851, 18852)
        got = path.read_bytes().decode("utf-8")
        want = orig.replace(":8787/", ":18851/").replace(":8788/", ":18852/")
        check("replace: ONLY the two URLs changed (compact entries, arrays, non-ASCII all kept byte-for-byte)",
              got == want, repr(got))
        path.write_bytes(orig.replace("\n", "\r\n").encode("utf-8"))
        _quiet(P.write_mcp_json, a, "127.0.0.1", 18853, 18854)
        got = path.read_bytes().decode("utf-8")
        check("CRLF file: only the URLs changed, CRLF kept",
              got == orig.replace("\n", "\r\n").replace(":8787/", ":18853/").replace(":8788/", ":18854/"), repr(got))
        ins = '{\n  "mcpServers": {\n    "chrome": {"command": "npx", "args": ["-y", "x"]}\n  }\n}\n'
        path.write_bytes(ins.encode("utf-8"))
        _quiet(P.write_mcp_json, a, "127.0.0.1", 18855, 18856)
        got = path.read_bytes().decode("utf-8")
        obj = json.loads(got)
        check("insert: the other server's compact entry is kept verbatim",
              '"chrome": {"command": "npx", "args": ["-y", "x"]}' in got, got)
        check("... and both deepmap entries are added with the new ports",
              obj["mcpServers"].get("deepmap-search", {}).get("url") == "http://127.0.0.1:18855/mcp"
              and obj["mcpServers"].get("deepmap-embed", {}).get("url") == "http://127.0.0.1:18856/mcp", got)
        check("... the original lines are still there (a pure addition, plus the separating comma)",
              got.startswith('{\n  "mcpServers": {\n    "chrome": {"command": "npx", "args": ["-y", "x"]},\n')
              and got.endswith("  }\n}\n"), got)
        for label, text in (("no mcpServers key", '{\n  "other": {"k": 1}\n}\n'), ("an empty object", "{}\n"),
                            ("empty mcpServers", '{"mcpServers": {}, "z": 1}')):
            path.write_text(text, encoding="utf-8")
            _quiet(P.write_mcp_json, a, "127.0.0.1", 18857, 18858)
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:   # noqa: BLE001
                obj = {"error": str(e)}
            base = json.loads(text)
            srv = obj.get("mcpServers") or {}
            check(f"{label}: valid JSON with both entries, every other key kept",
                  srv.get("deepmap-search", {}).get("url") == "http://127.0.0.1:18857/mcp"
                  and srv.get("deepmap-embed", {}).get("url") == "http://127.0.0.1:18858/mcp"
                  and all(obj.get(k) == v for k, v in base.items() if k != "mcpServers"), str(obj))
        check("... 'other' kept verbatim in the no-mcpServers case", True)


# =========================================================================== D6 — auth texts

def gate_auth_texts_route_aware() -> None:
    print("\n-- D6: auth texts are route-aware (CLI-consistent) and name Neo4j's login rate-limit lock --")
    with _Sandbox() as (home, work):
        try:
            rl = P._auth_failed_text("bolt://localhost:7689", project_key="k1a2b3c4d5e6", rate_limited=True)
        except TypeError as e:
            rl = f"TypeError: {e}"
        check("rate-limited: names the lock (AuthenticationRateLimit / too many failed logins)",
              "AuthenticationRateLimit" in rl or "too many" in rl.lower(), rl)
        check("... and still gives the instance remedy", "instance-rm --key k1a2b3c4d5e6 --yes" in rl, rl)
        base = P._auth_failed_text("bolt://localhost:7687")
        check("base store: move its data directory ASIDE (like the CLI), never instance-rm",
              "aside" in base and "instance-rm" not in base and str(P.paths(None)["data"]) in base, base)
        with _Patch(P, _NEO4J_ROUTE_SOURCE="legacy-unverified"):
            leg = P._auth_failed_text("bolt://localhost:7687")
        check("legacy route: 'no DeepMap instance of its own' + /deep-map:setup, no store deletion",
              "instance of its own" in leg and "/deep-map:setup" in leg and "aside" not in leg, leg)
        with _Patch(P, _USER_SET_NEO4J_URI=True):
            env = P._auth_failed_text("bolt://localhost:7687")
        check("operator NEO4J_URI: check that endpoint's credentials; never a store remedy",
              "NEO4J_URI" in env and "aside" not in env and "instance-rm" not in env, env)
    with _Sandbox() as (home, work):
        with _Patch(P, _bolt_state=_REAL_BOLT_STATE,
                    _make_driver=lambda *a, **k: _RLDrv(), _AUTH_LOCK_WAIT_S=0.0):
            diag = {}
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:17993", user="neo4j", password="x",
                                project_key="k1a2b3c4d5e6", bolt=17993, http=17994, diag=diag)
        check("ensure_neo4j on a login-locked Neo4j: fails, never provisions", ok is False, repr(ok))
        check("... the message names the lock", "AuthenticationRateLimit" in out or "too many" in out.lower(),
              out.strip()[-300:])
        check("... the short heal reason says 'credentials' and names the lock",
              "credential" in str(diag.get("reason") or "").lower()
              and ("lock" in str(diag.get("reason") or "").lower() or "too many" in str(diag.get("reason") or "").lower()),
              str(diag))
        with _Patch(P, _make_driver=lambda *a, **k: _RLDrv()):
            rc, out = _captured(P.cmd_status, "bolt://localhost:17993", "neo4j", "x", project_key="k1a2b3c4d5e6")
        check("status on a login-locked Neo4j: rc 3 + names the lock", rc == P.STATUS_UNAVAILABLE_RC
              and ("AuthenticationRateLimit" in out or "too many" in out.lower()), f"rc={rc} {out.strip()[-300:]}")


class _RLDrv:
    def verify_connectivity(self):
        raise _RateLimited()

    def session(self, *a, **k):
        raise _RateLimited()

    def close(self):
        pass


# =========================================================================== W4 — keyword-only verify

_NOTE = ("Keyword-only results: vector search is unavailable (the embedding model could not be loaded), so these "
         "are BM25 matches on names and summaries.")


def gate_verify_keyword_only_is_degraded() -> None:
    print("\n-- W4: a keyword-only verify answer is DEGRADED (a WARNING + setup verdict), not a silent pass --")

    def _run(cmd, **k):
        tool = cmd[cmd.index("--tool") + 1] if "--tool" in cmd else ""
        if tool == "deepmap_search":
            out = ("PASS: deepmap_search responded over HTTP; tools advertised = ['deepmap_search']\n"
                   "  2 result(s):\n    - fuse (Function)  a.py:3\n"
                   f"  NOTE: {_NOTE}\n")
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="PASS: deepmap_embed responded\n  embedding dim = 768\n",
                                     stderr="")
    fake_sp = types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired)
    with _Sandbox() as (home, work):
        with _Patch(P, subprocess=fake_sp):
            ok, out = _captured(P.verify_mcp, "deepmap-search")
        check("verify passes (the server answers) ...", ok is True, repr(ok))
        check("... but WARNs that vector search is unavailable (keyword-only)",
              "WARNING" in out and "vector search" in out.lower() and "keyword" in out.lower(), out.strip()[-300:])

        class _OkSync:
            last_verdict = "READY"

            def sync(self, target, **k):
                return True
        a = _mk_project(work, "a")
        with _Patch(P, subprocess=fake_sp, ensure_neo4j=lambda **k: True, _load=lambda name: _OkSync(),
                    verify_search=lambda *a_, **k_: True, start_mcp_servers=lambda **k: True,
                    is_http_up=lambda *a_, **k_: True, _print_mcp_hint=lambda *a_, **k_: None):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("setup exits 0 (the servers work) ...", ok is True, repr(ok))
        check("... with 'setup verdict: DEGRADED' naming vector search + /deep-map:doctor",
              "setup verdict: DEGRADED" in out and "vector search" in out.lower() and "/deep-map:doctor" in out,
              out.strip()[-500:])

    def _embed_down(cmd, **k):
        return types.SimpleNamespace(returncode=1, stdout="", stderr=(
            "FAIL: RuntimeError: tool call returned isError: DeepMap's embedding model is unavailable: "
            "OSError: no model. Run /deep-map:doctor"))
    with _Sandbox():
        with _Patch(P, subprocess=types.SimpleNamespace(run=_embed_down, TimeoutExpired=subprocess.TimeoutExpired)):
            ok, out = _captured(P.verify_mcp, "deepmap-embed")
        check("embed verify with no model: fails, worded as 'the embedding model is unavailable'",
              ok is False and "embedding model is unavailable" in out and "tool call failed" not in out,
              out.strip()[-300:])


try:
    BaseExceptionGroupCompat = ExceptionGroup          # noqa: F821 — Python 3.11+
except NameError:                                       # pragma: no cover — older Pythons
    class BaseExceptionGroupCompat(Exception):
        def __init__(self, msg, excs):
            super().__init__(msg)
            self.exceptions = tuple(excs)


def gate_smoke_prints_notes() -> None:
    print("\n-- W4: the MCP smoke test prints a search answer's {note} (so verify can see keyword-only) --")
    spec = importlib.util.spec_from_file_location("dm_smoke_r3", str(ROOT / "tests" / "mcp_http_smoketest.py"))
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    rows = [{"name": f"f{i}", "type": "Function", "file": "a.py", "line": i} for i in range(7)] + [{"note": _NOTE}]

    async def _probe(url, tool, query, top_k):
        return {"tools": ["deepmap_search"], "payload": {"result": rows}}
    smoke._probe = _probe
    args = types.SimpleNamespace(url="http://x/mcp", tool="deepmap_search", query="q", top_k=5, deadline=5)
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = asyncio.run(smoke._run(args))
    check("rc 0 and the note is printed as 'NOTE: ...' (even past the first 5 rows)",
          rc == 0 and f"NOTE: {_NOTE}" in out.getvalue(), out.getvalue()[-400:])
    # live-found: the MCP client wraps a tool's isError in an anyio ExceptionGroup whose message is only
    # "unhandled errors in a TaskGroup" — the smoke test must report the LEAF error, or verify_mcp can never
    # recognise a cold start / a missing embedding model from its output
    leaf = RuntimeError("tool call returned isError: DeepMap's embedding model is unavailable: OSError: x")

    async def _probe_group(url, tool, query, top_k):
        raise BaseExceptionGroupCompat("unhandled errors in a TaskGroup", [leaf])
    smoke._probe = _probe_group
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = asyncio.run(smoke._run(types.SimpleNamespace(url="http://x/mcp", tool="deepmap_embed", query="q",
                                                           top_k=5, deadline=5)))
    check("a tool error wrapped in an ExceptionGroup is reported by its LEAF message",
          rc == 1 and "embedding model is unavailable" in err.getvalue(), err.getvalue()[-300:])


# =========================================================================== W5 — purge every gout dir

def gate_instance_rm_purge_real_gout_and_aliases() -> None:
    print("\n-- W5/E1: instance-rm --purge deletes every gout dir of the project (the engine's layout) + aliases --")
    import graphify_extract as GX
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        PR.allocate_ports(b)
        keyA = PR.project_key(a)
        (a / "sub").mkdir(exist_ok=True)
        dirs = {}
        for label, root in (("A root", a), ("A subdir sync", a / "sub"), ("B", b)):
            d = GX.graphify_out_dir(root)
            (d / "cache").mkdir(parents=True, exist_ok=True)
            GX._write_owner_marker(d, root)
            dirs[label] = d
        alias = "0a1b2c3d4e5f"
        reg_path = PR.registry_path()
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["projects"][keyA]["aliases"] = [alias]             # a migrated (E1) key
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        old_gout = home / "gout" / alias
        (old_gout / "cache").mkdir(parents=True, exist_ok=True)
        old_inst = home / "instances" / alias
        (old_inst / "data").mkdir(parents=True, exist_ok=True)
        stopped = []
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: (stopped.append(project_key) or False),
                    _stop_mcp_servers_for_key=lambda k, **kw: None):
            rc, out = _captured(P.instance_rm, str(a), yes=True, purge=True)
        check("rc 0", rc == 0, f"rc={rc} {out.strip()[-300:]}")
        check("A's root + subdir-sync gout dirs are gone",
              not dirs["A root"].exists() and not dirs["A subdir sync"].exists(),
              f"{dirs['A root'].exists()} {dirs['A subdir sync'].exists()}")
        check("B's gout dir is kept", dirs["B"].exists())
        check("the alias key's gout dir is gone too", not old_gout.exists())
        check("the alias key's instance dir is stopped + removed too", alias in stopped and not old_inst.exists(),
              f"stopped={stopped} exists={old_inst.exists()}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-3 gates (hermetic)")
    print("=" * 68)
    for g in (gate_linux_identity_ticks_and_boot_id, gate_linux_stop_after_btime_shift,
              gate_mismatch_keeps_pidfile_of_expected_command, gate_macos_lstart_in_utc,
              gate_instance_rm_never_deletes_under_live_neo4j, gate_server_env_sync_root,
              gate_base_adopt_down_store_needs_proof, gate_setup_records_base_owner,
              gate_heal_legacy_never_starts_base, gate_mcp_stop_via_mcp_json_fallback,
              gate_write_mcp_json_minimal_diff, gate_auth_texts_route_aware,
              gate_verify_keyword_only_is_degraded, gate_smoke_prints_notes,
              gate_instance_rm_purge_real_gout_and_aliases):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
