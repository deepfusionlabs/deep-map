#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-4 provisioner fixes (build/provision.py), on top of the
round-2/3 gates (tests/provision_route_identity_gates.py, tests/provision_round3_gates.py):

  F1  ONE owner predicate: _base_adopt_ok's live-base branch and the base-store owner record check
      (_base_owner_is) decide "is this graph this project's?" with port_registry.owner_is_this_project —
      so a store stamped with the project's pre-UTF-8-fix (garbled, E1) identity is still its own, and a
      NESTED but distinct project (a repo inside another project's folder) is not.
  F2  a base-store claim persisted by the <= v0.27.4 mis-adoption is re-verified on the SETUP path: when this
      project's registered pair is the base and the live base's owner (or the base owner record) names
      ANOTHER project, the claim is released and the project gets its own instance — also when setup itself
      had to start that store (it is stopped again). heal never revives / serves such a claim. The LIVE
      owner is read whatever process serves 7687 (only the RECORD needs DeepMap's own instance), and a
      claim with NO record yet (pre-upgrade) is checked live by heal (right after the store is up — stopped
      again if heal started it), status, stop, mcp-start and hibernate.
  W8  DEEPMAP_NO_BASE_ADOPT=1 never gives a project the base pair, so a benchmark clone stays removable by
      instance-rm (which never touches the base store).
  E2  a pre-upgrade (no-record) LINUX pid file whose btime-derived start is later than the pid file (a wall-
      clock step can do that) is UNKNOWN, not "recycled": DeepMap's own command line decides and the
      process IS stopped — unless its environment / another instance's verified record proves it is
      another instance's. Two COMPARABLE start stamps that differ prove another process: the stale pid file
      is CLEARED (never signalled), so instance-rm is not wedged and never advises killing another
      project's Neo4j.
  E1  the MCP owner checks are ALIAS-aware: a sidecar still naming the project's migrated (old) key is the
      project's own server.
  W8  instance-rm removes the per-key Neo4j start lock, the per-port MCP start locks (and, with --purge, the
      heal status file + sync's per-instance write lock) — never a lock that is held.
  W4  verify_mcp classifies the server's Neo4j AUTH answer as a credentials problem (setup banner), not a
      generic tool error / cold start.

Hermetic: DEEPMAP_HOME -> a throwaway dir; port/bolt probes, drivers, pid liveness / command line /
creation time, a FAKE /proc tree, Popen and every kill are stubbed. NO Neo4j, NO processes, NO network.
Exit 0 = GREEN.
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

_REAL_CT = P._pid_create_time          # the real seams, captured before any gate stubs them

_PASS = 0
_FAIL = 0
_ENV_KEYS = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI", "NEO4J_USER", "DEEPMAP_NO_BASE_ADOPT",
             "NEO4J_PASSWORD", "DEEPMAP_MCP_START_WAIT", "DEEPMAP_MCP_VERIFY_DEADLINE",
             "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY")
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
        (d / ".deepmap").mkdir(parents=True, exist_ok=True)
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


class _Spy:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.ret(*a, **k) if callable(self.ret) else self.ret


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
        if "count(n)" in q:
            return _Res([{"c": self.drv.nodes}])
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


class _Sandbox:
    """Fresh DEEPMAP_HOME + work dir; per-project env cleared; port probes answer from `occupied`; bolt
    probes answer `self.bolt` ("up" | "down" | "auth", mutable mid-gate); the escape hatch pinned; every
    process probe answers "no such process" unless a gate stubs it; the driver never connects anywhere
    real. Restores everything on exit."""

    def __init__(self, occupied=(), bolt="down"):
        self.occupied = set(occupied)
        self.bolt = bolt

    def __enter__(self):
        self._home = tempfile.mkdtemp(prefix="dmr4h")
        self._work = tempfile.mkdtemp(prefix="dmr4w")
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
                         _pid_alive=lambda pid: False,
                         _pid_cmdline=lambda pid: None,
                         _pid_image=lambda pid: None,
                         _make_driver=_driver_factory(_GraphDrv(exc=_neo4j_exc("ServiceUnavailable"))),
                         _NEO4J_ROUTE_SOURCE=None,
                         _USER_SET_NEO4J_URI=False).__enter__()
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


class _LegacyEnc:
    """Pin the pre-UTF-8-fix code page (E1) to cp1252 — the same simulation on every platform."""

    def __enter__(self):
        self._had = hasattr(PR, "_legacy_encodings")
        self._saved = getattr(PR, "_legacy_encodings", None)
        PR._legacy_encodings = lambda: ["cp1252"]
        return self

    def __exit__(self, *a):
        if self._had:
            PR._legacy_encodings = self._saved
        else:
            try:
                delattr(PR, "_legacy_encodings")
            except AttributeError:
                pass


def _clear_port_env() -> None:
    for k in ("DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI"):
        os.environ.pop(k, None)


def _legacy_store() -> None:
    (P.paths(None)["databases"] / "neo4j").mkdir(parents=True, exist_ok=True)


def _base_owner_file() -> Path:
    return P.paths(None)["data"] / "deepmap_base_owner.json"


def _ident(p) -> str:
    return PR.project_identity(str(p))


def _launcher_cmd(key=None) -> str:
    return f'/bin/sh {P.paths(None, key)["neo4j_bin"]} console'


def _own_cmd(name) -> str:
    return f"/usr/bin/python3 /plugins/deep-map/build/{P.MCP_SERVERS[name]['script']}"


# ---- a fake Linux /proc ------------------------------------------------------------------------------
def _fake_proc(root: Path, pid: int, *, ticks: int, btime: int, boot_id: str, cmdline: str = "",
               comm: str = "java", environ: dict | None = None) -> None:
    """/proc/<pid>/{stat,cmdline,comm[,environ]} + /proc/stat (btime) + /proc/sys/kernel/random/boot_id."""
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10          # fields[19] = field 22 (starttime)
    (d / "stat").write_text(f"{pid} ({comm} x) " + " ".join(fields) + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(("\0".join(cmdline.split(" ")) + "\0").encode("utf-8") if cmdline else b"")
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    if environ is not None:
        (d / "environ").write_bytes("".join(f"{k}={v}\0" for k, v in environ.items()).encode("utf-8"))
    elif (d / "environ").exists():
        (d / "environ").unlink()
    (root / "stat").write_text(f"cpu  10 20 30 40\nbtime {btime}\nprocesses 42\n", encoding="utf-8")
    (root / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(boot_id + "\n", encoding="utf-8")


def _no_signal(*a, **k):
    raise AssertionError(f"a gate tried to signal a real process: {a}")


class _Linux:
    """provision behaves as on Linux, reading the FAKE /proc under `root` (every pid is 'alive'; os.kill /
    killpg disarmed — on WINDOWS os.kill(pid, 0) TERMINATES a process)."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        self._p = _Patch(P, is_windows=lambda: False, _PROC=str(self.root), _pid_alive=lambda pid: True,
                         _pid_create_time=_REAL_CT, _pid_cmdline=_REAL_CMDLINE).__enter__()
        self._os = _Patch(os, kill=_no_signal, killpg=_no_signal).__enter__()
        return self

    def __exit__(self, *a):
        self._os.__exit__(*a)
        self._p.__exit__(*a)


_REAL_CMDLINE = P._pid_cmdline


def _set_mtime(path: Path, t: float) -> None:
    os.utime(str(path), (t, t))


# =========================================================================== F1 — one owner predicate

def gate_f1_base_adopt_uses_owner_predicate() -> None:
    print("\n-- F1: _base_adopt_ok (live base) + the owner-record check use port_registry.owner_is_this_project --")
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a")
        elsewhere = str(work / "elsewhere")
        spy = _Spy(True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=elsewhere, nodes=3)),
                    _legacy_base_instance_alive=lambda: False), _Patch(PR, owner_is_this_project=spy):
            v = _quiet(P._base_adopt_ok, a)
        check("live base: the verdict IS owner_is_this_project's (True for a path-unrelated owner it accepts)",
              v is True and any(c[0][1] == elsewhere for c in spy.calls), f"v={v} calls={spy.calls}")
        spy = _Spy(False)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=str(a), nodes=3)),
                    _legacy_base_instance_alive=lambda: False), _Patch(PR, owner_is_this_project=spy):
            v = _quiet(P._base_adopt_ok, a)
        check("... and False when it says 'not this project' (even for the same path)", v is False and spy.calls,
              f"v={v} calls={spy.calls}")
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        P._write_base_owner_record(elsewhere)
        spy = _Spy(True)
        with _Patch(PR, owner_is_this_project=spy):
            v = P._base_owner_is(a)
        check("_base_owner_is: the record is judged by owner_is_this_project",
              v is True and any(str(c[0][1]).replace("\\", "/") == elsewhere.replace("\\", "/") for c in spy.calls),
              f"v={v} calls={spy.calls}")


def gate_f1_legacy_garbled_owner_is_own() -> None:
    print("\n-- F1/E1: a base store stamped with the project's pre-fix (garbled) identity is its OWN --")
    with _Sandbox(bolt="up") as (home, work), _LegacyEnc():
        repo = _mk_project(work / "José", "proj")
        garbled = next(iter(PR.legacy_identities(repo)), None)
        check("precondition: a cp1252-garbled legacy identity exists for a non-ASCII repo",
              bool(garbled) and garbled != _ident(repo), repr(garbled))
        if not garbled:
            return
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=garbled, nodes=4)),
                    _legacy_base_instance_alive=lambda: False):
            v = _quiet(P._base_adopt_ok, repo)
        check("live base stamped with the garbled identity -> adopt (it is this project's graph)", v is True, repr(v))
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        P._write_base_owner_record(garbled)
        check("owner record naming the garbled identity -> _base_owner_is True", P._base_owner_is(repo) is True)


def gate_f1_nested_distinct_project_is_not_owner() -> None:
    print("\n-- F1: a NESTED but distinct project (a repo inside another project's folder) is not the owner --")
    with _Sandbox(bolt="up") as (home, work):
        ws = work / "ws"
        ws.mkdir(parents=True, exist_ok=True)                    # a pre-v0.26 project: a plain (non-git) folder
        api = _mk_project(ws, "api")                             # its own git repo, never set up
        check("precondition: two different registry identities",
              PR.project_key(api) != PR.project_key(ws), f"{PR.project_key(api)} {PR.project_key(ws)}")
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=str(ws.resolve()), nodes=9)),
                    _legacy_base_instance_alive=lambda: False):
            v = _quiet(P._base_adopt_ok, api)
        check("live base owned by the enclosing project 'ws' -> api does NOT adopt it", v is False, repr(v))
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        P._write_base_owner_record(str(ws.resolve()))
        check("owner record naming 'ws' -> not api's (_base_owner_is False)", P._base_owner_is(api) is False)
        check("control: the record names api itself -> True",
              (P._write_base_owner_record(_ident(api)) or True) and P._base_owner_is(api) is True)


# =========================================================================== F2 — mis-adopted base claims

def _claim_base(p: Path) -> None:
    """A <= v0.27.4 mis-adoption: this project's registry entry records the base 7687/7474."""
    PR.allocate_ports(p)
    PR.allocate_neo4j_ports(p, base_adopt_ok=lambda: True)


def gate_f2_release_claim_live_foreign_owner() -> None:
    print("\n-- F2: setup re-verifies a persisted base claim: a LIVE base owned by another project -> released --")
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        _claim_base(a)
        mcp = PR.lookup_ports(a)
        check("precondition: A is registered on the base pair",
              PR.lookup_neo4j_ports(a) == (PR.BASE_BOLT, PR.BASE_HTTP), str(PR.lookup_neo4j_ports(a)))
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _, out = _captured(P._release_foreign_base_claim, str(a))
        check("the base claim is RELEASED (B's graph is not A's)", PR.lookup_neo4j_ports(a) is None,
              str(PR.lookup_neo4j_ports(a)))
        check("... the MCP pair is kept", PR.lookup_ports(a) == mcp, f"{PR.lookup_ports(a)} vs {mcp}")
        check("... and it says whose store it is", "b" in out and "7687" in out, out.strip()[-300:])
        check("... the proof is recorded (B owns the base store)",
              PR._same_project_path(P._base_owner_root() or "", _ident(b)), str(P._base_owner_root()))
        _clear_port_env()
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            _quiet(P._resolve_ports, str(a), allocate=True)
        got = PR.lookup_neo4j_ports(a)
        check("setup's allocation then gives A its OWN instance (never 7687 again)",
              got is not None and got != (PR.BASE_BOLT, PR.BASE_HTTP), str(got))


def gate_f2_release_claim_down_record() -> None:
    print("\n-- F2: a DOWN base whose owner record names another project -> the claim is released --")
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        _, out = _captured(P._release_foreign_base_claim, str(a))
        check("record names B -> A's base claim released", PR.lookup_neo4j_ports(a) is None,
              f"{PR.lookup_neo4j_ports(a)} {out.strip()[-200:]}")


def gate_f2_keeps_legit_claims() -> None:
    print("\n-- F2 controls: a claim is kept unless the store PROVABLY belongs to another project --")
    cases = (
        ("live base owned by THIS project", "up", "self", None),
        ("live, unowned (pre-owner-stamping) base", "up", None, None),
        ("live base whose owner can't be read", "up", "error", None),
        ("down base, no owner record", "down", None, None),
        ("down base, record names THIS project", "down", None, "self"),
    )
    for label, bolt, owner, record in cases:
        with _Sandbox(bolt=bolt) as (home, work):
            a = _mk_project(work, "a")
            _claim_base(a)
            _legacy_store()
            if record == "self":
                P._write_base_owner_record(_ident(a))
            drv = (_GraphDrv(exc=_neo4j_exc("ServiceUnavailable")) if owner == "error"
                   else _GraphDrv(owner=(_ident(a) if owner == "self" else None), nodes=5))
            with _Patch(P, _make_driver=_driver_factory(drv), _legacy_base_instance_alive=lambda: True):
                _quiet(P._release_foreign_base_claim, str(a))
            check(f"{label}: claim KEPT", PR.lookup_neo4j_ports(a) == (PR.BASE_BOLT, PR.BASE_HTTP),
                  str(PR.lookup_neo4j_ports(a)))


class _OkSync:
    last_verdict = None

    def sync(self, target, **k):
        self.last_verdict = "READY"
        return True


def gate_f2_setup_started_foreign_base() -> None:
    print("\n-- F2: setup that had to START the base finds another project's graph -> own instance, base stopped --")
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()                                          # B's store on disk, no owner record yet
        calls = []

        def _ensure(**k):
            calls.append(k)
            if k.get("project_key") is None:
                sb.bolt = "up"                                   # the base (B's store) came up
            P.paths(None, k.get("project_key"))["data"].mkdir(parents=True, exist_ok=True)
            return True
        stop = _Spy(True)
        stubs = dict(ensure_neo4j=_ensure, _load=lambda name: _OkSync(), verify_search=lambda *a_, **k_: True,
                     start_mcp_servers=lambda **k: True, verify_mcp=lambda *a_, **k_: True,
                     _print_mcp_hint=lambda *a_, **k_: None, stop_neo4j=stop,
                     _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=7)),
                     _legacy_base_instance_alive=lambda: True)
        with _Patch(P, **stubs):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        got = PR.lookup_neo4j_ports(a)
        check("setup succeeds", ok is True, f"{ok!r} {out.strip()[-400:]}")
        check("A ends on its OWN instance, not the base", got is not None and got != (PR.BASE_BOLT, PR.BASE_HTTP),
              str(got))
        keys = [c.get("project_key") for c in calls]
        check("ensure_neo4j: the base first, then A's own instance",
              len(keys) == 2 and keys[0] is None and keys[1] == PR.project_key(a), str(keys))
        check("the base store setup started for nothing is STOPPED again (it was down)",
              any(k.get("project_key", "x") is None for _, k in stop.calls), str(stop.calls))
        check("... and setup says it is project b's store", "b" in out and "own instance" in out.lower(),
              out.strip()[-400:])
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        a = _mk_project(work, "a")
        _claim_base(a)
        calls = []

        def _ensure2(**k):
            calls.append(k)
            sb.bolt = "up"
            P.paths(None, k.get("project_key"))["data"].mkdir(parents=True, exist_ok=True)
            return True
        stop = _Spy(True)
        with _Patch(P, ensure_neo4j=_ensure2, _load=lambda name: _OkSync(), verify_search=lambda *a_, **k_: True,
                    start_mcp_servers=lambda **k: True, verify_mcp=lambda *a_, **k_: True,
                    _print_mcp_hint=lambda *a_, **k_: None, stop_neo4j=stop,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(a), nodes=7)),
                    _legacy_base_instance_alive=lambda: True):
            ok = _quiet(P.setup, str(a), password="deepmap")
        check("control: the base holds A's own graph -> kept, one ensure, nothing stopped",
              ok is True and PR.lookup_neo4j_ports(a) == (PR.BASE_BOLT, PR.BASE_HTTP) and len(calls) == 1
              and stop.calls == [], f"ok={ok} {PR.lookup_neo4j_ports(a)} {len(calls)} {stop.calls}")


def gate_f2_heal_never_serves_foreign_claim() -> None:
    print("\n-- F2: heal never revives / serves a base claim whose owner record names another project --")
    with _Sandbox(bolt="down") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        rec = {}
        try:
            rec = json.loads((home / "run" / f"heal-{PR.project_key(a)}.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        check("nothing started (no ensure_neo4j, no MCP servers)", ens.calls == [] and mcp.calls == [],
              f"{ens.calls} {mcp.calls}")
        check("rc != 0; the reason names /deep-map:setup", rc != 0 and "/deep-map:setup" in str(rec.get("reason")),
              f"rc={rc} {rec}")
    with _Sandbox(bolt="down") as (home, work):
        a = _mk_project(work, "a", marker=True)
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(a))
        ens, mcp = _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        check("control: the record names A -> revived as before", rc == 0 and len(ens.calls) == 1
              and len(mcp.calls) == 1, f"rc={rc} {ens.calls} {mcp.calls}")


def gate_f2_lookup_verbs_refuse_foreign_claim() -> None:
    print("\n-- F2: status / stop / mcp-start / hibernate never act on a mis-adopted base claim --")
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        P._write_base_owner_record(_ident(b))
        drivers = []

        def _drv(uri, user, pw):
            drivers.append(uri)
            raise RuntimeError("no driver in a hermetic gate")
        stop, mcp = _Spy(True), _Spy(True)
        with _Patch(P, _make_driver=_drv, stop_neo4j=stop, start_mcp_servers=mcp,
                    verify_mcp=lambda *a_, **k_: True, _print_mcp_hint=lambda *a_, **k_: None,
                    _ensure_mcp_json=lambda t: None):
            rc_s, out_s = _captured(P.main, ["status", str(a)])
            _clear_port_env()
            rc_t, out_t = _captured(P.main, ["stop", str(a)])
            _clear_port_env()
            rc_m, out_m = _captured(P.main, ["mcp-start", str(a)])
        check("status: rc 3, never reads the store, names /deep-map:setup",
              rc_s == P.STATUS_UNAVAILABLE_RC and not drivers and "/deep-map:setup" in out_s,
              f"rc={rc_s} drivers={drivers} {out_s.strip()[-300:]}")
        check("stop: refuses (rc 1), stops nothing", rc_t == 1 and stop.calls == [], f"rc={rc_t} {stop.calls}")
        check("mcp-start: refuses (rc 1), starts nothing", rc_m == 1 and mcp.calls == [], f"rc={rc_m} {mcp.calls}")
        pub = getattr(P, "foreign_base_claim_problem", None)
        txt = pub(str(a)) if callable(pub) else None
        check("public foreign_base_claim_problem (for the CLI / sync): names the owner + /deep-map:setup",
              bool(txt) and "b" in txt and "/deep-map:setup" in txt, repr(txt))
        check("... and None for a project with no base claim", callable(pub) and pub(str(b)) is None)
        # hibernate: A's claim is idle, the base store runs (it is B's) -> never stopped on A's idleness
        reg_path = PR.registry_path()
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["projects"][PR.project_key(a)]["last_used"] = "2020-01-01T00:00:00Z"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        lp = P.paths(None)["pidfile"]
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("4999", encoding="utf-8")
        stop = _Spy(True)
        with _Patch(P, stop_neo4j=stop, _pid_alive=lambda pid: True):
            n = _quiet(P.hibernate_idle, str(work), idle_minutes=1, scope="all")
        check("hibernate: the base store is not stopped on the mis-adopted claim's idleness",
              n == 0 and stop.calls == [], f"n={n} {stop.calls}")
        P._write_base_owner_record(_ident(a))
        stop = _Spy(True)
        with _Patch(P, stop_neo4j=stop, _pid_alive=lambda pid: True):
            n = _quiet(P.hibernate_idle, str(work), idle_minutes=1, scope="all")
        check("control: the record names A -> its idle base store IS hibernated", len(stop.calls) == 1,
              f"n={n} {stop.calls}")


def gate_f2_release_live_foreign_any_listener() -> None:
    print("\n-- F2: a LIVE base whose graph is another project's releases the claim even when its process can't be "
          "shown to be DeepMap's legacy instance (and records nothing) --")
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        _claim_base(a)
        P.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: False):
            _, out = _captured(P._release_foreign_base_claim, str(a))
        check("the claim is RELEASED (the graph the claim routes to is project b's — the same live read "
              "_base_adopt_ok trusts)", PR.lookup_neo4j_ports(a) is None,
              f"{PR.lookup_neo4j_ports(a)} {out.strip()[-200:]}")
        check("... and NO owner record is written (a Neo4j not provably DeepMap's own says nothing about the "
              "store on disk)", not _base_owner_file().exists())


def gate_f2_heal_live_owner_without_record() -> None:
    print("\n-- F2: heal on a base claim with NO owner record yet reads the live owner — another project's graph "
          "is never served --")
    # (a) the base is down: heal starts it, finds project b's graph -> MCP NOT started, the store stopped again,
    #     the owner recorded (so the next heal refuses before starting anything)
    sb = _Sandbox(bolt="down")
    with sb as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()

        def _ensure(**k):
            sb.bolt = "up"
            return True
        ens, mcp, stop = _Spy(_ensure), _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, stop_neo4j=stop, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: True):
            rc = _quiet(P.main, ["heal", str(a)])
        rec = {}
        try:
            rec = json.loads((home / "run" / f"heal-{PR.project_key(a)}.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        check("base down, no record, live owner = b: the MCP servers are NOT started", mcp.calls == [],
              str(mcp.calls))
        check("... rc != 0; neo4j_ok false; the reason names project b + /deep-map:setup",
              rc != 0 and rec.get("neo4j_ok") is False and "/deep-map:setup" in str(rec.get("reason"))
              and "b" in str(rec.get("reason")), f"rc={rc} {rec}")
        check("... heal started the store only to read its owner -> it is stopped again",
              any(k.get("project_key", "x") is None for _, k in stop.calls), str(stop.calls))
        check("... the owner is recorded (the next heal refuses from the file, before starting anything)",
              PR._same_project_path(P._base_owner_root() or "", _ident(b)), str(P._base_owner_root()))
    # (b) the base was already running: refused the same way, but never stopped (heal did not start it)
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        ens, mcp, stop = _Spy(True), _Spy(True), _Spy(True)
        with _Patch(P, ensure_neo4j=ens, start_mcp_servers=mcp, stop_neo4j=stop, _ensure_mcp_json=lambda t: None,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: False):
            rc = _quiet(P.main, ["heal", str(a)])
        check("base already up, live owner = b: refused (rc != 0, no MCP start), and NOT stopped",
              rc != 0 and mcp.calls == [] and stop.calls == [], f"rc={rc} {mcp.calls} {stop.calls}")
    # controls: the live graph is this project's / unowned -> served as before, nothing stopped
    for label, owner_of in (("live owner = this project", "self"), ("live graph unowned (no proof)", None)):
        sb = _Sandbox(bolt="down")
        with sb as (home, work):
            a = _mk_project(work, "a", marker=True)
            _claim_base(a)
            _legacy_store()

            def _ensure2(**k):
                sb.bolt = "up"
                return True
            mcp, stop = _Spy(True), _Spy(True)
            with _Patch(P, ensure_neo4j=_Spy(_ensure2), start_mcp_servers=mcp, stop_neo4j=stop,
                        _ensure_mcp_json=lambda t: None,
                        _make_driver=_driver_factory(_GraphDrv(owner=(_ident(a) if owner_of else None), nodes=5)),
                        _legacy_base_instance_alive=lambda: True):
                rc = _quiet(P.main, ["heal", str(a)])
            check(f"control ({label}): revived + served (rc 0, MCP started, nothing stopped)",
                  rc == 0 and len(mcp.calls) == 1 and stop.calls == [], f"rc={rc} {mcp.calls} {stop.calls}")


def gate_f2_lookup_verbs_live_owner_without_record() -> None:
    print("\n-- F2: status / stop / mcp-start / hibernate read the LIVE owner of a base claim with no record --")
    with _Sandbox(bolt="up") as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(a)
        _legacy_store()
        cs, stop, mcp = _Spy(0), _Spy(True), _Spy(True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)), cmd_status=cs,
                    stop_neo4j=stop, start_mcp_servers=mcp, verify_mcp=lambda *a_, **k_: True,
                    _print_mcp_hint=lambda *a_, **k_: None, _ensure_mcp_json=lambda t: None,
                    _legacy_base_instance_alive=lambda: False):
            rc_s, out_s = _captured(P.main, ["status", str(a)])
            _clear_port_env()
            rc_t, out_t = _captured(P.main, ["stop", str(a)])
            _clear_port_env()
            rc_m, out_m = _captured(P.main, ["mcp-start", str(a)])
        check("status: rc 3, no census of b's graph, names b + /deep-map:setup",
              rc_s == P.STATUS_UNAVAILABLE_RC and cs.calls == [] and "/deep-map:setup" in out_s and "b" in out_s,
              f"rc={rc_s} census={cs.calls} {out_s.strip()[-300:]}")
        check("stop: refuses (rc 1), stops nothing", rc_t == 1 and stop.calls == [], f"rc={rc_t} {stop.calls}")
        check("mcp-start: refuses (rc 1), starts nothing", rc_m == 1 and mcp.calls == [], f"rc={rc_m} {mcp.calls}")
        # hibernate: the claim's idleness never stops a store whose live graph is another project's
        reg_path = PR.registry_path()
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["projects"][PR.project_key(a)]["last_used"] = "2020-01-01T00:00:00Z"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        lp = P.paths(None)["pidfile"]
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("4999", encoding="utf-8")
        stop = _Spy(True)
        with _Patch(P, stop_neo4j=stop, _pid_alive=lambda pid: True,
                    _make_driver=_driver_factory(_GraphDrv(owner=_ident(b), nodes=9)),
                    _legacy_base_instance_alive=lambda: False):
            n = _quiet(P.hibernate_idle, str(work), idle_minutes=1, scope="all")
        check("hibernate: not stopped (the live graph is project b's)", n == 0 and stop.calls == [],
              f"n={n} {stop.calls}")
    # control: the live graph IS this project's -> every verb proceeds
    with _Sandbox(bolt="up") as (home, work):
        a = _mk_project(work, "a", marker=True)
        _claim_base(a)
        _legacy_store()
        cs, stop, mcp = _Spy(0), _Spy(True), _Spy(True)
        with _Patch(P, _make_driver=_driver_factory(_GraphDrv(owner=_ident(a), nodes=9)), cmd_status=cs,
                    stop_neo4j=stop, start_mcp_servers=mcp, verify_mcp=lambda *a_, **k_: True,
                    _print_mcp_hint=lambda *a_, **k_: None, _ensure_mcp_json=lambda t: None,
                    _legacy_base_instance_alive=lambda: False):
            rc_s = _quiet(P.main, ["status", str(a)])
            _clear_port_env()
            rc_t = _quiet(P.main, ["stop", str(a)])
            _clear_port_env()
            rc_m = _quiet(P.main, ["mcp-start", str(a)])
        check("control (live owner = this project): status censuses, stop stops, mcp-start starts",
              rc_s == 0 and len(cs.calls) == 1 and rc_t == 0 and len(stop.calls) == 1 and rc_m == 0
              and len(mcp.calls) == 1, f"{rc_s} {cs.calls} {rc_t} {stop.calls} {rc_m} {mcp.calls}")


def gate_no_base_adopt_switch() -> None:
    print("\n-- W8: DEEPMAP_NO_BASE_ADOPT=1 never gives a project the base pair (a benchmark clone must stay "
          "removable by instance-rm) --")
    with _Sandbox(bolt="down") as (home, work):
        c = _mk_project(work, "clone")
        check("control: a clean machine (no legacy store, base down) adopts the base",
              _quiet(P._base_adopt_ok, c) is True)
        os.environ["DEEPMAP_NO_BASE_ADOPT"] = "1"
        try:
            v = _quiet(P._base_adopt_ok, c)
            _quiet(P._resolve_ports, str(c), allocate=True)
            got = PR.lookup_neo4j_ports(c)
            rc = _quiet(P.instance_rm, str(c), yes=True, purge=True) if got else None
        finally:
            os.environ.pop("DEEPMAP_NO_BASE_ADOPT", None)
        check("with the switch: _base_adopt_ok is False", v is False, repr(v))
        check("... setup's allocation gives the clone its OWN pair (never 7687/7474)",
              got is not None and got != (PR.BASE_BOLT, PR.BASE_HTTP), str(got))
        check("... so `instance-rm --purge --yes` can remove it (rc 0, no registry entry left)",
              rc == 0 and PR.lookup_neo4j_ports(c) is None and PR.lookup_ports(c) is None, f"rc={rc}")


# =========================================================================== E2 — Linux no-record + clear on proof

def gate_e2_linux_no_record_clock_step_is_unknown() -> None:
    print("\n-- E2: a pre-upgrade Linux pid file across a clock step is UNKNOWN -> the command line decides --")
    with _Sandbox() as (home, work):
        proc = work / "proc"
        key = "abcdef012345"
        p = P.paths(None, key)
        p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time() - 5000
        # the process started 100 s after boot; since then the wall clock was stepped forward, so today's
        # btime puts that start 50 s AFTER the pid file was written (a v0.27.4 pid file: no identity record)
        _fake_proc(proc, 4901, ticks=100 * 100, btime=int(t0 - 50), boot_id="b-1", cmdline=_launcher_cmd(key))
        p["pidfile"].write_text("4901", encoding="utf-8")
        _set_mtime(p["pidfile"], t0)
        with _Linux(proc):
            verdict = P._pid_identity(4901, pidfile=p["pidfile"])
            check("_pid_identity: UNKNOWN (None), not 'recycled' (False)", verdict is None, repr(verdict))
            kill = _Spy(True)
            with _Patch(P, _terminate_pid=kill):
                ok, out = _captured(P.stop_neo4j, project_key=key)
        check("stop_neo4j: DeepMap's Neo4j command line decides -> the instance IS stopped",
              ok is True and len(kill.calls) == 1, f"ok={ok} {kill.calls} {out.strip()[-300:]}")
        # the same, but the process's environment names ANOTHER instance's conf dir -> provably not ours
        other = P.paths(None, "fedcba987654")["conf_dir"]
        _fake_proc(proc, 4901, ticks=100 * 100, btime=int(t0 - 50), boot_id="b-1", cmdline=_launcher_cmd(key),
                   environ={"NEO4J_CONF": str(other), "PATH": "/usr/bin"})
        p["pidfile"].write_text("4901", encoding="utf-8")
        _set_mtime(p["pidfile"], t0)
        with _Linux(proc):
            kill = _Spy(True)
            with _Patch(P, _terminate_pid=kill):
                ok, out = _captured(P.stop_neo4j, project_key=key)
        check("... its environment names ANOTHER instance's conf: NOT stopped", kill.calls == [] and not ok,
              f"ok={ok} {kill.calls}")
        check("... and the stale pid file is cleared (it is provably not this instance's)",
              not p["pidfile"].exists(), out.strip()[-300:])
        # ... or another instance's pid file claims that pid with a VERIFIED start stamp
        _fake_proc(proc, 4901, ticks=100 * 100, btime=int(t0 - 50), boot_id="b-1", cmdline=_launcher_cmd(key))
        p["pidfile"].write_text("4901", encoding="utf-8")
        _set_mtime(p["pidfile"], t0)
        q = P.paths(None, "fedcba987654")
        q["pidfile"].parent.mkdir(parents=True, exist_ok=True)
        q["pidfile"].write_text("4901", encoding="utf-8")
        with _Linux(proc):
            q["pidfile"].with_name("neo4j.pid.identity").write_text(json.dumps(
                {"pid": 4901, "created": P._pid_create_time(4901), "project_key": "fedcba987654"}), encoding="utf-8")
            kill = _Spy(True)
            with _Patch(P, _terminate_pid=kill):
                ok, out = _captured(P.stop_neo4j, project_key=key)
        check("... another instance's pid file + matching start record claims the pid: NOT stopped, ours cleared",
              kill.calls == [] and not ok and not p["pidfile"].exists() and q["pidfile"].exists(),
              f"ok={ok} {kill.calls} ours={p['pidfile'].exists()} theirs={q['pidfile'].exists()}")


def gate_e2_linux_no_record_mcp() -> None:
    print("\n-- E2: a pre-upgrade Linux MCP server (no owner record) across a clock step is still stopped --")
    with _Sandbox() as (home, work):
        proc = work / "proc"
        a = _mk_project(work, "a")
        pa = PR.allocate_ports(a)
        os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = str(pa[0]), str(pa[1])
        pf = P._mcp_pidfile("deepmap-search", pa[0])
        pf.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time() - 5000
        _fake_proc(proc, 4902, ticks=100 * 100, btime=int(t0 - 50), boot_id="b-1",
                   cmdline=_own_cmd("deepmap-search"), comm="python3")
        pf.write_text("4902", encoding="utf-8")
        _set_mtime(pf, t0)
        with _Linux(proc):
            kill = _Spy(True)
            with _Patch(P, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
                ok, out = _captured(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("its own registered port + the server script: stopped", ok is True and len(kill.calls) == 1,
              f"ok={ok} {kill.calls} {out.strip()[-300:]}")
        _fake_proc(proc, 4902, ticks=100 * 100, btime=int(t0 - 50), boot_id="b-1",
                   cmdline=_own_cmd("deepmap-search"), comm="python3",
                   environ={"DEEPMAP_SEARCH_PORT": str(pa[0] + 40)})
        pf.write_text("4902", encoding="utf-8")
        _set_mtime(pf, t0)
        with _Linux(proc):
            kill = _Spy(True)
            with _Patch(P, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
                ok, out = _captured(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("... its environment says it serves ANOTHER port: NOT stopped, the stale pid file cleared",
              kill.calls == [] and not pf.exists(), f"ok={ok} {kill.calls} {out.strip()[-300:]}")


def gate_e2_comparable_mismatch_clears() -> None:
    print("\n-- E2: two COMPARABLE start stamps that differ prove another process -> stale pid file CLEARED --")
    with _Sandbox() as (home, work):
        key = "abcdef012345"
        p = P.paths(None, key)
        ident = p["pidfile"].with_name("neo4j.pid.identity")
        for label, rec, now in (("Windows CreationDate 100 vs 900", 100.0, 900.0),
                                ("Linux token, another boot_id", "linux:boot-OLD:5000", "linux:boot-NEW:5000"),
                                ("Linux token, another tick count", "linux:b-1:5000", "linux:b-1:9000")):
            p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
            p["pidfile"].write_text("4501", encoding="utf-8")
            ident.write_text(json.dumps({"pid": 4501, "created": rec}), encoding="utf-8")
            kill = _Spy(True)
            with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(key),
                        _pid_create_time=lambda pid, _n=now: _n, _terminate_pid=kill):
                ok, out = _captured(P.stop_neo4j, project_key=key)
            check(f"{label}: NOT signalled", kill.calls == [] and not ok, f"ok={ok} {kill.calls}")
            check(f"... pid file + identity record CLEARED", not p["pidfile"].exists() and not ident.exists(),
                  f"pf={p['pidfile'].exists()} id={ident.exists()} {out.strip()[-200:]}")
        # no record: the pid-file-mtime fallback is suggestive, not proof -> kept (reported)
        ident.unlink(missing_ok=True)
        p["pidfile"].write_text("4501", encoding="utf-8")
        mt = p["pidfile"].stat().st_mtime
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(key),
                    _pid_create_time=lambda pid: mt + 3600.0, _terminate_pid=kill):
            ok, out = _captured(P.stop_neo4j, project_key=key)
        check("no record, started after the pid file (Windows/macOS epoch): not signalled, pid file KEPT",
              kill.calls == [] and p["pidfile"].exists(), f"{kill.calls} {p['pidfile'].exists()}")
        # MCP: a comparable mismatch clears the pid file + owner record, returns "nothing to stop"
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = "18963", "18964"
        with _Patch(P, _pid_create_time=lambda pid: 250.0):
            _quiet(P._record_mcp_server, "deepmap-search", 18963, 4601, str(a))
        pf, of = P._mcp_pidfile("deepmap-search", 18963), P._mcp_ownerfile("deepmap-search", 18963)
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _own_cmd("deepmap-search"),
                    _pid_create_time=lambda pid: 999.0, _terminate_pid=kill, is_http_up=lambda *a_, **k_: False):
            ok, out = _captured(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("MCP comparable mismatch: NOT signalled; pid file + owner sidecar CLEARED; 'nothing to stop' (True)",
              kill.calls == [] and not pf.exists() and not of.exists() and ok is True,
              f"ok={ok} {kill.calls} pf={pf.exists()} of={of.exists()}")


def gate_e2_instance_rm_not_wedged() -> None:
    print("\n-- E2: instance-rm is not wedged by a pid another instance inherited, and never advises killing it --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)       # its own instance (7689)
        k = PR.project_key(a)
        p = P.paths(None, k)
        for d in ("data", "run"):
            p[d].mkdir(parents=True, exist_ok=True)
        p["pidfile"].write_text("4801", encoding="utf-8")
        p["pidfile"].with_name("neo4j.pid.identity").write_text(
            json.dumps({"pid": 4801, "created": "linux:boot-OLD:777"}), encoding="utf-8")
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(k),
                    _pid_create_time=lambda pid: "linux:boot-NEW:777", _terminate_pid=kill, _RM_RETRY=(1, 0.0)):
            rc, out = _captured(P.instance_rm, str(a), yes=True)
        check("another boot's stamp (the pid now runs another instance): rc 0, nothing killed",
              rc == 0 and kill.calls == [], f"rc={rc} {kill.calls} {out.strip()[-300:]}")
        check("... the instance dir is removed", not p["data"].exists())
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        k = PR.project_key(a)
        p = P.paths(None, k)
        for d in ("data", "run"):
            p[d].mkdir(parents=True, exist_ok=True)
        p["pidfile"].write_text("4802", encoding="utf-8")            # pre-upgrade: no identity record
        mt = p["pidfile"].stat().st_mtime
        kill = _Spy(True)
        with _Patch(P, _pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _launcher_cmd(k),
                    _pid_create_time=lambda pid: mt + 3600.0, _terminate_pid=kill, _RM_RETRY=(1, 0.0)):
            rc, out = _captured(P.instance_rm, str(a), yes=True)
        check("unprovable (no record): rc 1, the dir is kept", rc == 1 and p["data"].exists(), f"rc={rc}")
        check("... the advice covers another project's process: delete the pid file, never kill it",
              "another project" in out and "neo4j.pid" in out and "delete" in out.lower(), out.strip()[-500:])


# =========================================================================== E1 — alias-aware MCP owner checks

def gate_mcp_owner_alias_aware() -> None:
    print("\n-- E1: the MCP owner checks accept the project's migrated (alias) key --")
    with _Sandbox() as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        pa = PR.allocate_ports(a)
        PR.allocate_ports(b)
        key = PR.project_key(a)
        old = "0a1b2c3d4e5f"
        reg_path = PR.registry_path()
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["projects"][key]["aliases"] = [old]
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = str(pa[0]), str(pa[1])
        with _Patch(P, _pid_create_time=lambda pid: 250.0):
            _quiet(P._record_mcp_server, "deepmap-search", pa[0], 5501, str(a))
        of = P._mcp_ownerfile("deepmap-search", pa[0])
        rec = json.loads(of.read_text(encoding="utf-8"))
        rec["project_key"] = old                               # the migration's side-file re-key failed
        of.write_text(json.dumps(rec), encoding="utf-8")
        procs = dict(_pid_alive=lambda pid: True, _pid_cmdline=lambda pid: _own_cmd("deepmap-search"),
                     _pid_create_time=lambda pid: 250.0)
        with _Patch(P, is_http_up=lambda *a_, **k_: True, **procs):
            foreign = P._mcp_foreign_owner("deepmap-search", pa[0], str(a))
            own = P._own_mcp_server_pid("deepmap-search", pa[0], str(a))
            ok, out = _captured(P.start_mcp_server, "deepmap-search", target=str(a), wait=1)
        check("a sidecar naming the project's ALIAS key is not 'another project's'", foreign is None, repr(foreign))
        check("... _own_mcp_server_pid recognises it", own == 5501, repr(own))
        check("... start_mcp_server keeps it as this project's server (True, no 'held by')",
              ok is True and "held by" not in out, f"{ok} {out.strip()[-200:]}")
        kill = _Spy(True)
        with _Patch(P, is_http_up=lambda *a_, **k_: False, _terminate_pid=kill, **procs):
            ok = _quiet(P.stop_mcp_server, "deepmap-search", target=str(a))
        check("... and mcp-stop stops it", ok is True and len(kill.calls) == 1, f"{ok} {kill.calls}")
        # migrated_from alone (no aliases list) is honoured too
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["projects"][key].pop("aliases", None)
        reg["projects"][key]["migrated_from"] = old
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        with _Patch(P, _pid_create_time=lambda pid: 250.0):
            _quiet(P._record_mcp_server, "deepmap-search", pa[0], 5501, str(a))
        rec = json.loads(of.read_text(encoding="utf-8"))
        rec["project_key"] = old
        of.write_text(json.dumps(rec), encoding="utf-8")
        with _Patch(P, is_http_up=lambda *a_, **k_: True, **procs):
            foreign = P._mcp_foreign_owner("deepmap-search", pa[0], str(a))
        check("... `migrated_from` is honoured the same way", foreign is None, repr(foreign))
        # control: a sidecar naming ANOTHER registered project stays foreign
        rec["project_key"], rec["root"] = PR.project_key(b), str(b)
        of.write_text(json.dumps(rec), encoding="utf-8")
        with _Patch(P, is_http_up=lambda *a_, **k_: True, **procs):
            foreign = P._mcp_foreign_owner("deepmap-search", pa[0], str(a))
        check("control: a sidecar naming ANOTHER project is still foreign", bool(foreign), repr(foreign))


# =========================================================================== instance-rm lock files

def gate_instance_rm_removes_locks() -> None:
    print("\n-- instance-rm removes the project's start locks (never a held one, never another project's) --")
    import sync as S
    with _Sandbox() as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        pa = PR.allocate_ports(a)
        pair = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        pb = PR.allocate_ports(b)
        k = PR.project_key(a)
        run = home / "run"
        run.mkdir(parents=True, exist_ok=True)
        start_lock = run / f"neo4j-start-{k}.lock"
        mcp_locks = [P._mcp_pidfile(n, port).with_suffix(".lock") for n, port in zip(P.MCP_SERVERS, pa)]
        theirs = P._mcp_pidfile("deepmap-search", pb[0]).with_suffix(".lock")
        heal_json, heal_lock = run / f"heal-{k}.json", run / f"heal-{k}.lock"
        bolt = (pair or (None,))[0] or PR.lookup_neo4j_ports(a)[0]
        sync_lock = S._instance_lock_path(f"bolt://localhost:{bolt}")
        for f in [start_lock, *mcp_locks, theirs, heal_json, heal_lock, sync_lock]:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("", encoding="utf-8")
        P.paths(None, k)["data"].mkdir(parents=True, exist_ok=True)
        with _Patch(P, stop_neo4j=lambda version=None, project_key=None: False,
                    _stop_mcp_servers_for_key=lambda *a_, **k_: None):
            rc, out = _captured(P.instance_rm, str(a), yes=True, purge=True)
        check("rc 0", rc == 0, f"rc={rc} {out.strip()[-300:]}")
        check("the per-key Neo4j start lock is removed", not start_lock.exists())
        check("the project's per-port MCP start locks are removed", not any(f.exists() for f in mcp_locks),
              str([f.name for f in mcp_locks if f.exists()]))
        check("--purge: the heal status file (+ its lock) is removed", not heal_json.exists() and not heal_lock.exists())
        check("--purge: sync's per-instance write lock for its bolt URI is removed", not sync_lock.exists(),
              str(sync_lock))
        check("another project's MCP lock is kept", theirs.exists())
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        k = PR.project_key(a)
        start_lock = home / "run" / f"neo4j-start-{k}.lock"
        held, how = P._file_lock(start_lock, 0)
        P.paths(None, k)["data"].mkdir(parents=True, exist_ok=True)
        try:
            with _Patch(P, stop_neo4j=lambda version=None, project_key=None: False,
                        _stop_mcp_servers_for_key=lambda *a_, **k_: None):
                rc = _quiet(P.instance_rm, str(a), yes=True)
            check("a lock HELD by a running start is never deleted (instance-rm still succeeds)",
                  rc == 0 and start_lock.exists() and how == "ok", f"rc={rc} how={how} {start_lock.exists()}")
        finally:
            P._file_unlock(held)


# =========================================================================== W4 — verify_mcp auth answers

_AUTH_ERR = ("FAIL: RuntimeError: tool call returned isError: Neo4j at bolt://localhost:7689 rejected this DeepMap "
             "server's credentials (Neo.ClientError.Security.Unauthorized). To avoid tripping Neo4j's login lock — "
             "which would also lock out sync and the CLI — the server will not try again for 30 s. Run "
             "/deep-map:doctor to see which Neo4j this project uses and how to fix its credentials; the server "
             "retries on its own after that.")
_RATE_ERR = ("FAIL: RuntimeError: tool call returned isError: Neo4j at bolt://localhost:7689 is temporarily refusing "
             "DeepMap's logins after repeated failed login attempts (Neo.ClientError.Security.AuthenticationRateLimit)."
             " To let that login lock expire — every new attempt prolongs it, and it locks out sync and the CLI too — "
             "the server will not try again for 30 s. Run /deep-map:doctor ...")


def gate_verify_auth_is_credentials_problem() -> None:
    print("\n-- W4: verify_mcp words the server's Neo4j AUTH answer as a credentials problem --")
    for label, err in (("rejected credentials", _AUTH_ERR), ("login rate-limit lock", _RATE_ERR)):
        def _run(cmd, _e=err, **k):
            return types.SimpleNamespace(returncode=1, stdout="", stderr=_e)
        with _Sandbox():
            with _Patch(P, subprocess=types.SimpleNamespace(run=_run, TimeoutExpired=subprocess.TimeoutExpired)):
                ok, out = _captured(P.verify_mcp, "deepmap-search")
            detail = str(P._VERIFY_DETAIL.get("deepmap-search") or "")
            check(f"{label}: verify fails, worded as a CREDENTIALS problem (not 'tool call failed' / cold start)",
                  ok is False and "credential" in detail.lower() and "tool call failed" not in detail
                  and "cold start" not in detail and "/deep-map:doctor" in detail, detail)
            with _Patch(P, is_http_up=lambda *a_, **k_: True):
                res, banner = _captured(P._report_verify_failure, ["deepmap-search"])
            if label == "rejected credentials":
                check("... setup verdict FAILED (a listening server that Neo4j rejects never works)",
                      res is False and "setup verdict: FAILED" in banner and "credential" in banner.lower(),
                      banner.strip()[-300:])
            else:
                check("... setup verdict DEGRADED naming the login lock (it clears on its own)",
                      res is True and "setup verdict: DEGRADED" in banner and "login" in banner.lower(),
                      banner.strip()[-300:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-4 gates (hermetic)")
    print("=" * 68)
    for g in (gate_f1_base_adopt_uses_owner_predicate, gate_f1_legacy_garbled_owner_is_own,
              gate_f1_nested_distinct_project_is_not_owner,
              gate_f2_release_claim_live_foreign_owner, gate_f2_release_claim_down_record,
              gate_f2_keeps_legit_claims, gate_f2_setup_started_foreign_base,
              gate_f2_heal_never_serves_foreign_claim, gate_f2_lookup_verbs_refuse_foreign_claim,
              gate_f2_release_live_foreign_any_listener, gate_f2_heal_live_owner_without_record,
              gate_f2_lookup_verbs_live_owner_without_record, gate_no_base_adopt_switch,
              gate_e2_linux_no_record_clock_step_is_unknown, gate_e2_linux_no_record_mcp,
              gate_e2_comparable_mismatch_clears, gate_e2_instance_rm_not_wedged,
              gate_mcp_owner_alias_aware, gate_instance_rm_removes_locks,
              gate_verify_auth_is_credentials_problem):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
