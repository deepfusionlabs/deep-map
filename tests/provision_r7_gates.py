#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spawn, no-kill gates for the ROUND-7 provisioner fixes (build/provision.py, owner W2):

  A  I4 (HIGH, Linux): Neo4j 5.26's Unix bootloader reads run/neo4j.pid before `console` starts and refuses to
     run ("already running") when that file names a LIVE process — and start_neo4j pre-wrote the launcher's own
     pid there (the launcher `exec`s the bootloader, so the pid stays alive): no local Neo4j could ever start on
     POSIX. On POSIX start_neo4j no longer writes run/neo4j.pid (Neo4j writes it itself, naming its server JVM);
     DeepMap keeps the launcher's identity in its OWN file (run/neo4j.launcher.json), clears a pid file that
     would block the start (a dead / recycled pid, another instance's JVM), stops through the stamp-verified
     launcher (its process group holds the server JVM) or a server JVM verified by its --config-dir, records the
     server JVM's start stamp once it is up (the E2 stamp comparison applies on Linux again), and finds a server
     JVM whose pid file went stale by its conf dir. Windows is unchanged (its bootloader never reads the file).
  B  I4: a zombie is not alive on POSIX (/proc state Z; `ps` stat Z elsewhere), and _terminate_pid reaps its own
     child — a stop no longer reports FAILED over processes that are already dead. On a real Windows host the
     POSIX probes never call os.kill (which would TERMINATE the pid there).
  C  I4 / H2: DeepMap's base JVM running under a STALE pid file is recognised by its conf dir (the base conf of an
     extracted binary, while the base ports are held) — its pid file is re-recorded, so the owner record, stop and
     hibernate work again and the store's real owner is not locked out.
  D  I4 / H1: setup never reports success while this project's MCP port is held by a listener it cannot attribute
     to this project's current config (a stale pid file or none — an unmanaged / pre-upgrade server); verify_mcp
     checks the server's E4 project label; the blocker is PERSISTED (setup-blocked-<key>.json, W1's contract) and
     honoured by heal (refusal_kind "mcp-blocked") until the held listener is gone or setup succeeds.
  E  `provision.py stop` with nothing to stop exits 0 (like mcp-stop) unless the bolt port is still held.
  F  write_mcp_json: a BOM-prefixed file is parsed (Claude Code accepts it) and keeps its BOM; an empty file is {};
     a non-object mcpServers is refused, never silently replaced; the git-tracked NOTE.
  G  setup runs a source census BEFORE allocating ports / starting Neo4j (an empty or docs-only dir never pays a
     JVM start or a registry entry); a FAILED first sync stops the instance setup itself started and no longer
     claims "nothing was started".
  H  a long DEEPMAP_HOME on Windows (MAX_PATH): preflight refuses before the ~180 MB download; a real extraction
     failure is an actionable message, never a traceback.
  I  a plain restart says "starting the existing instance" (not "provisioning"); instance_table tells a
     hibernated instance (marker) from a merely stopped one.
  J  `provision.py git-hook --install|--uninstall|--status`: an OPT-IN, chained, sentinel-wrapped post-commit
     auto-sync (the installers' block); setup prints the hint and never installs it.
  K  setup's end-to-end search check (verify_search) derives its query from the project's OWN graph (T4d-I).

Reuses the round-4/round-6 hermetic harnesses (same owner): DEEPMAP_HOME -> a throwaway dir; port / bolt probes,
drivers, pid liveness / command line / creation time / environment, spawns and kills are stubbed. NO Neo4j, NO
processes started, NO network. Exit 0 = GREEN.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from provision_round4_gates import (P, PR, _Patch, _Spy, _Sandbox, _GraphDrv, _driver_factory,  # noqa: E402
                                    _mk_project, _claim_base, _legacy_store, _ident, _own_cmd, _captured,
                                    _quiet, _clear_port_env)
from provision_round6_gates import (_World, _mcp_env, _seed_pidfile_only, _seed_owned, _setup_stubs,  # noqa: E402
                                    _OkSync)

_PASS = 0
_FAIL = 0
BASE = (7687, 7474)
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
KEY = "ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12"


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
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e} "
              + traceback.format_exc().strip().splitlines()[-3].strip())


def _api(name: str):
    fn = getattr(P, name, None)
    check(f"provision exposes {name}", fn is not None)
    return fn


def _same(a, b) -> bool:
    try:
        return P._same_path(str(a), str(b))
    except Exception:
        return False


def _nhome() -> Path:
    return P.paths(None)["neo4j_home"]


def _binary(present: bool = True) -> None:
    """The shared Neo4j binary under the sandbox home (extracted: sentinel + bin + lib + conf)."""
    nh = _nhome()
    for d in ("bin", "lib", "conf"):
        (nh / d).mkdir(parents=True, exist_ok=True)
    (nh / "bin" / ("neo4j.bat" if os.name == "nt" else "neo4j")).write_text("x", encoding="utf-8")
    (nh / "bin" / "neo4j").write_text("x", encoding="utf-8")
    if present:
        (nh / ".deepmap-extracted").write_text("5.26.0", encoding="utf-8")


def _jvm_cmd(conf) -> str:
    nh = _nhome()
    return (f"/usr/lib/jvm/java-17/bin/java -XX:+UseG1GC -cp {nh}/lib/*:{nh}/plugins/* "
            f"org.neo4j.server.CommunityEntryPoint --home-dir={nh} --config-dir={conf} --console-mode")


def _boot_cmd() -> str:
    nh = _nhome()
    return f"/usr/lib/jvm/java-17/bin/java -cp {nh}/lib/* org.neo4j.server.startup.Neo4jBoot console"


class _NeoWorld:
    """pid -> {cmd, created, pgid, alive}. POSIX simulation: is_windows False, liveness / command line / start
    stamp answer from here; _terminate_pid records (pid, tree) and kills the pid (tree: its whole group);
    _list_processes lists the live processes. Nothing real is probed or signalled."""

    def __init__(self, procs: dict, *, kill_ok: bool = True):
        self.procs = {int(k): dict(v) for k, v in procs.items()}
        self.killed = []
        self.listed = 0
        self.kill_ok = kill_ok

    def alive(self, pid) -> bool:
        p = self.procs.get(int(pid))
        return bool(p) and p.get("alive", True)

    def _kill(self, pid, tree=False):
        self.killed.append((int(pid), bool(tree)))
        if not self.kill_ok:
            return False
        grp = self.procs.get(int(pid), {}).get("pgid", int(pid))
        for q, p in self.procs.items():
            if q == int(pid) or (tree and p.get("pgid", q) == grp):
                p["alive"] = False
        return True

    def _list(self):
        self.listed += 1
        return [(q, p.get("cmd")) for q, p in self.procs.items() if p.get("alive", True)]

    def stubs(self, **extra) -> dict:
        d = dict(is_windows=lambda: False,
                 _pid_alive=self.alive,
                 _pid_cmdline=lambda pid: self.procs[int(pid)].get("cmd") if self.alive(pid) else None,
                 _pid_create_time=lambda pid: self.procs[int(pid)].get("created") if self.alive(pid) else None,
                 _pid_image=lambda pid: None,
                 _proc_environ=lambda pid: None,
                 _terminate_pid=self._kill,
                 _list_processes=self._list)
        d.update(extra)
        return d


class _FakeProc:
    def __init__(self, pid, rc=None):
        self.pid, self._rc = pid, rc

    def poll(self):
        return self._rc


def _fake_subprocess(pid: int, calls: list):
    """A stand-in for provision's `subprocess` module: Popen records its argv/kwargs and returns a live fake."""
    def _popen(argv, **kw):
        calls.append((list(argv), kw))
        return _FakeProc(pid)
    return types.SimpleNamespace(Popen=_popen, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE,
                                 TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError,
                                 CalledProcessError=subprocess.CalledProcessError, list2cmdline=subprocess.list2cmdline,
                                 run=subprocess.run)


def _launcher_file(key=KEY) -> Path:
    fn = getattr(P, "_neo4j_launcher_file", None)
    pf = P.paths(None, key)["pidfile"]
    return fn(pf) if callable(fn) else pf.with_name("neo4j.launcher.json")


def _write_launcher(key, pid, created) -> None:
    lf = _launcher_file(key)
    lf.parent.mkdir(parents=True, exist_ok=True)
    lf.write_text(json.dumps({"launcher_pid": pid, "pgid": pid, "created": created, "project_key": key,
                              "conf_dir": str(P.paths(None, key)["conf_dir"])}), encoding="utf-8")


def _write_pid(key, pid) -> Path:
    pf = P.paths(None, key)["pidfile"]
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    return pf


# =========================================================================== A — POSIX Neo4j pid file (HIGH)

def gate_a_posix_start_never_prewrites_pidfile() -> None:
    print("\n-- A (I4 HIGH): POSIX start_neo4j leaves run/neo4j.pid to Neo4j; the launcher identity goes to our own file --")
    with _Sandbox() as (home, work):
        calls: list = []
        v = P.neo4j_version()
        p = P.paths(v, KEY)
        with _Patch(P, is_windows=lambda: False, subprocess=_fake_subprocess(7001, calls),
                    _pid_create_time=lambda pid: "linux:boot-1:5000", _instances_running=lambda env=None: 0):
            pid, out = _captured(P.start_neo4j, v, KEY)
        check("start_neo4j returns the launcher pid", pid == 7001, repr(pid))
        check("run/neo4j.pid is NOT written (Neo4j 5.26's Unix bootloader would see its own live pid there and "
              "refuse to start: 'Neo4j is already running')", not p["pidfile"].exists(),
              p["pidfile"].read_text() if p["pidfile"].exists() else "")
        lf = _launcher_file()
        rec = json.loads(lf.read_text(encoding="utf-8")) if lf.exists() else {}
        check("the launcher identity is kept in DeepMap's OWN file (run/neo4j.launcher.json)",
              lf.name == "neo4j.launcher.json" and lf.parent == p["run"], str(lf))
        check("... with the launcher pid, its start stamp and the instance it runs",
              rec.get("launcher_pid") == 7001 and rec.get("created") == "linux:boot-1:5000"
              and rec.get("project_key") == KEY and _same(rec.get("conf_dir"), p["conf_dir"]), str(rec))
        check("... launched in its own session (its process group holds the server JVM) with NEO4J_CONF",
              calls and calls[0][1].get("start_new_session") is True
              and _same((calls[0][1].get("env") or {}).get("NEO4J_CONF"), p["conf_dir"]), str(calls[:1]))


def gate_a_windows_start_unchanged() -> None:
    print("\n-- A control: Windows start_neo4j still records the launcher pid in run/neo4j.pid (+ identity) --")
    with _Sandbox() as (home, work):
        calls: list = []
        v = P.neo4j_version()
        p = P.paths(v, KEY)
        with _Patch(P, is_windows=lambda: True, subprocess=_fake_subprocess(7101, calls),
                    _pid_create_time=lambda pid: 1234.5, _instances_running=lambda env=None: 0):
            pid = _quiet(P.start_neo4j, v, KEY)
        idf = P._neo4j_identity_file(p["pidfile"])
        rec = json.loads(idf.read_text(encoding="utf-8")) if idf.exists() else {}
        check("Windows: run/neo4j.pid names the launcher (its bootloader never reads the file; stop needs it)",
              pid == 7101 and p["pidfile"].exists() and p["pidfile"].read_text().strip() == "7101")
        check("Windows: the identity record is kept as before", rec.get("pid") == 7101 and rec.get("created") == 1234.5,
              str(rec))


def gate_a_posix_start_clears_blocking_pidfile() -> None:
    print("\n-- A: POSIX start clears a pid file that would block Neo4j (dead / recycled / another instance's JVM) --")
    mine_conf = lambda: P.paths(None, KEY)["conf_dir"]            # noqa: E731
    other_conf = lambda: P.paths(None, "ff" * 20)["conf_dir"]     # noqa: E731
    cases = (
        ("a DEAD pid", dict(alive=False, cmd="x"), False),
        ("a live pid RECYCLED by another program", dict(cmd="/usr/sbin/sshd -D"), False),
        ("a live DeepMap JVM of ANOTHER instance (its --config-dir)", "other", False),
        ("a live JVM of THIS instance (--config-dir = ours) — left for Neo4j to judge", "mine", True),
    )
    for label, proc, kept in cases:
        with _Sandbox() as (home, work):
            if proc == "other":
                proc = dict(cmd=_jvm_cmd(other_conf()))
            elif proc == "mine":
                proc = dict(cmd=_jvm_cmd(mine_conf()))
            w = _NeoWorld({6001: proc})
            pf = _write_pid(KEY, 6001)
            calls: list = []
            with _Patch(P, **w.stubs(subprocess=_fake_subprocess(7001, calls), _instances_running=lambda env=None: 0)):
                _quiet(P.start_neo4j, P.neo4j_version(), KEY)
            if kept:
                check(f"{label}: the pid file is kept", pf.exists() and pf.read_text().strip() == "6001")
            else:
                check(f"{label}: the pid file is removed before the launch", not pf.exists(),
                      pf.read_text() if pf.exists() else "")
            check(f"{label}: nothing is signalled", w.killed == [], str(w.killed))


def gate_a_posix_stop_via_launcher() -> None:
    print("\n-- A: POSIX stop — the stamp-verified launcher's process group (launcher + server JVM) --")
    with _Sandbox() as (home, work):
        conf = P.paths(None, KEY)["conf_dir"]
        w = _NeoWorld({7001: dict(cmd=_boot_cmd(), created="linux:b:100", pgid=7001),
                       7002: dict(cmd=_jvm_cmd(conf), created="linux:b:105", pgid=7001)})
        _write_launcher(KEY, 7001, "linux:b:100")
        pf = _write_pid(KEY, 7002)                       # what Neo4j itself writes: its server JVM
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.stop_neo4j, project_key=KEY)
        check("stop_neo4j -> True", ok is True, f"{ok!r} {out.strip()[-300:]}")
        check("... signalled the LAUNCHER's group once (tree=True: killpg covers the server JVM)",
              w.killed == [(7001, True)], str(w.killed))
        check("... both processes are gone", not w.alive(7001) and not w.alive(7002))
        check("... pid file, identity and launcher record are cleared",
              not pf.exists() and not _launcher_file().exists()
              and not P._neo4j_identity_file(pf).exists())
    with _Sandbox() as (home, work):
        conf = P.paths(None, KEY)["conf_dir"]
        w = _NeoWorld({7002: dict(cmd=_jvm_cmd(conf), created=time.time() - 3600, pgid=7001)})
        _write_launcher(KEY, 7001, "linux:b:100")        # the launcher is gone (record stale)
        pf = _write_pid(KEY, 7002)
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.stop_neo4j, project_key=KEY)
        check("launcher gone, server JVM alive (its --config-dir is ours): stopped via Neo4j's pid file",
              ok is True and w.killed == [(7002, True)], f"{ok!r} {w.killed}")
        check("... the stale launcher record is cleared", not _launcher_file().exists())


def gate_a_posix_stop_never_signals_unproven() -> None:
    print("\n-- A controls: a recycled launcher pid / another instance's JVM / a dead record are never signalled --")
    with _Sandbox() as (home, work):
        w = _NeoWorld({7001: dict(cmd="/usr/bin/vim notes.txt", created="linux:b:999")})
        _write_launcher(KEY, 7001, "linux:b:100")        # recorded stamp 100, live stamp 999 -> recycled
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.stop_neo4j, project_key=KEY)
        check("recycled launcher pid (start stamp differs): NOT signalled", w.killed == [] and ok is False,
              f"{ok!r} {w.killed}")
        check("... its record is proven stale and cleared", not _launcher_file().exists())
    with _Sandbox() as (home, work):
        other = P.paths(None, "ff" * 20)["conf_dir"]
        w = _NeoWorld({7002: dict(cmd=_jvm_cmd(other), created=time.time() - 3600)})
        pf = _write_pid(KEY, 7002)
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.stop_neo4j, project_key=KEY)
        check("a pid file whose JVM runs ANOTHER instance's --config-dir: NOT signalled", w.killed == [] and ok is False,
              f"{ok!r} {w.killed}")
        check("... this instance's stale pid file is cleared", not pf.exists())
    with _Sandbox() as (home, work):
        w = _NeoWorld({})
        _write_launcher(KEY, 7001, "linux:b:100")
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.stop_neo4j, project_key=KEY)
        check("only a DEAD launcher record, no pid file: nothing to stop, nothing signalled",
              ok is False and w.killed == [], f"{ok!r} {w.killed}")
        check("... the dead record is cleared", not _launcher_file().exists())


def gate_a_record_server_identity() -> None:
    print("\n-- A: once up, the server JVM's start stamp is recorded (the E2 stamp comparison applies on Linux) --")
    fn = _api("_record_neo4j_server_identity")
    if not callable(fn):
        return
    with _Sandbox() as (home, work):
        v = P.neo4j_version()
        conf = P.paths(v, KEY)["conf_dir"]
        w = _NeoWorld({7002: dict(cmd=_jvm_cmd(conf), created="linux:b:105")})
        pf = _write_pid(KEY, 7002)
        with _Patch(P, **w.stubs()):
            fn(v, KEY)
        idf = P._neo4j_identity_file(pf)
        rec = json.loads(idf.read_text(encoding="utf-8")) if idf.exists() else {}
        check("the identity file records Neo4j's server pid + its stamp", rec.get("pid") == 7002
              and rec.get("created") == "linux:b:105", str(rec))
        with _Patch(P, **w.stubs()):
            got = P._pid_check(7002, recorded=P._recorded_create_time(idf, 7002), pidfile=pf)
        check("... so stop's comparison rests on two comparable stamps ('stamp')", got == (True, "stamp"), str(got))
    with _Sandbox() as (home, work):
        v = P.neo4j_version()
        other = P.paths(v, "ff" * 20)["conf_dir"]
        w = _NeoWorld({7002: dict(cmd=_jvm_cmd(other), created="linux:b:105")})
        pf = _write_pid(KEY, 7002)
        with _Patch(P, **w.stubs()):
            fn(v, KEY)
        check("control: a pid-file JVM of another conf dir is never recorded as this instance's",
              not P._neo4j_identity_file(pf).exists())
    # ensure_neo4j records it after a start it made
    with _Sandbox() as (home, work):
        v = P.neo4j_version()
        conf = P.paths(v, KEY)["conf_dir"]
        w = _NeoWorld({7002: dict(cmd=_jvm_cmd(conf), created="linux:b:105")})

        def _start(version, project_key=None):
            _write_pid(project_key, 7002)            # Neo4j writes its own pid file
            return 7001
        with _Patch(P, **w.stubs(preflight=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                                  configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True,
                                  start_neo4j=_start, wait_for_bolt=lambda *a, **k: True)):
            ok = _quiet(P.ensure_neo4j, uri="bolt://localhost:7689", project_key=KEY, bolt=7689, http=7476)
        idf = P._neo4j_identity_file(P.paths(v, KEY)["pidfile"])
        check("ensure_neo4j (POSIX) records the server JVM's identity after the start it made",
              ok is True and idf.exists(), f"ok={ok} exists={idf.exists()}")


def gate_a_conf_dir_parse() -> None:
    print("\n-- A: the serving JVM's conf dir is read from its --config-dir argument --")
    fn = _api("_neo4j_pid_conf_dir")
    if not callable(fn):
        return
    cases = (
        ("/usr/bin/java -cp /h/lib/* org.neo4j.server.CommunityEntryPoint --home-dir=/h --config-dir=/h/i/abc/conf "
         "--console-mode", "/h/i/abc/conf"),
        ("/usr/bin/java org.neo4j.server.CommunityEntryPoint --home-dir=/home/j d/n --config-dir=/home/j d/i/conf",
         "/home/j d/i/conf"),
        ('"C:\\Program Files\\Java\\bin\\java.exe" -cp "C:\\x\\lib\\*" org.neo4j.server.CommunityEntryPoint '
         '"--home-dir=C:\\Users\\John Doe\\.deepmap\\neo4j\\neo4j-community-5.26.0" '
         '"--config-dir=C:\\Users\\John Doe\\.deepmap\\instances\\abc\\conf" --console-mode',
         "C:\\Users\\John Doe\\.deepmap\\instances\\abc\\conf"),
        ("C:\\java\\bin\\java.exe -cp C:\\x org.neo4j.server.CommunityEntryPoint --home-dir=C:\\d\\n "
         "--config-dir=C:\\d\\n\\conf", "C:\\d\\n\\conf"),
        ("/usr/bin/java -cp /h/lib/* org.neo4j.server.startup.Neo4jBoot console", None),
    )
    for cmd, want in cases:
        with _Patch(P, _pid_cmdline=lambda pid, _c=cmd: _c, _pid_alive=lambda pid: True, _proc_environ=lambda pid: None):
            got = fn(4242)
        check(f"--config-dir parsed -> {want!r}", got == want, f"{got!r} from {cmd[:70]}")


def gate_a_stale_pidfile_jvm_found_by_conf() -> None:
    print("\n-- A: a server JVM whose pid file went stale is found by its conf dir (stop + instance-rm guard) --")
    fn_live = getattr(P, "_live_instance_pid", None)
    for label, binary, occupied, want_stop in (
            ("binary extracted + the instance's bolt port held", True, {17693}, True),
            ("control: no extracted binary -> no process scan", False, {17693}, False),
            ("control: the instance's bolt port is free -> no process scan", True, set(), False)):
        with _Sandbox(occupied=occupied) as (home, work):
            if binary:
                _binary()
            conf = P.paths(None, KEY)["conf_dir"]
            conf.mkdir(parents=True, exist_ok=True)
            (conf / "neo4j.conf").write_text("server.bolt.listen_address=:17693\nserver.http.listen_address=:17694\n",
                                             encoding="utf-8")
            w = _NeoWorld({9300: dict(cmd=_jvm_cmd(conf), created="linux:b:300", pgid=9290)})
            pf = _write_pid(KEY, 31337)                     # the dead loser of the v0.27.4 start race
            with _Patch(P, **w.stubs()):
                live = fn_live(KEY) if callable(fn_live) else None
                ok = _quiet(P.stop_neo4j, project_key=KEY)
            if want_stop:
                check(f"{label}: instance-rm's guard sees the live JVM (never rmtree under it)", live == 9300, str(live))
                check(f"{label}: stop finds and stops it (tree)", ok is True and w.killed == [(9300, True)],
                      f"{ok!r} {w.killed}")
            else:
                check(f"{label}", w.listed == 0 and w.killed == [] and ok is False,
                      f"listed={w.listed} killed={w.killed} ok={ok!r}")


# =========================================================================== B — zombies

class _FakeOS:
    """provision's `os`, with the POSIX process calls scripted (so the POSIX branch runs on any host without ever
    signalling a real pid). Every other attribute is the real os module's."""

    def __init__(self, name, **fns):
        self.name = name
        self.WNOHANG = 1
        self._fns = fns

    def __getattr__(self, a):
        if a in self._fns:
            return self._fns[a]
        return getattr(os, a)


def _proc_stat(root: Path, pid: int, state: str) -> None:
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text(f"{pid} (java) {state} 1 {pid} {pid} 0 -1 4194560 " + "0 " * 12 + "123456 0 0",
                            encoding="utf-8")


def gate_b_zombie_not_alive() -> None:
    print("\n-- B (I4): a zombie is NOT alive on POSIX; no os.kill probe ever runs on a real Windows host --")
    tmp = Path(tempfile.mkdtemp(prefix="dmr7z"))
    try:
        proc = tmp / "proc"
        _proc_stat(proc, 5101, "Z")
        _proc_stat(proc, 5102, "S")
        _proc_stat(proc, 5103, "X")
        kills: list = []
        fos = _FakeOS("posix", kill=lambda pid, sig: kills.append((pid, sig)))
        with _Patch(P, is_windows=lambda: False, _PROC=str(proc), os=fos):
            z, s, x = P._pid_alive(5101), P._pid_alive(5102), P._pid_alive(5103)
        check("state Z (zombie) -> not alive", z is False, repr(z))
        check("state S -> alive", s is True, repr(s))
        check("state X (dead) -> not alive", x is False, repr(x))
        check("... decided from /proc — no signal probe", kills == [], str(kills))
        # "Zl": the main thread is a zombie but another thread still runs (a JVM still shutting down — measured live
        # on Linux: its bolt port was still bound) -> NOT dead yet
        _proc_stat(proc, 5104, "Z")
        _proc_stat(proc / "5104" / "task", 5104, "Z")
        _proc_stat(proc / "5104" / "task", 5199, "S")
        _proc_stat(proc, 5105, "Z")
        _proc_stat(proc / "5105" / "task", 5105, "Z")
        with _Patch(P, is_windows=lambda: False, _PROC=str(proc), os=fos):
            zl, zz = P._pid_alive(5104), P._pid_alive(5105)
        check("state Z with a LIVE thread ('Zl', still shutting down) -> alive", zl is True, repr(zl))
        check("state Z with every thread gone -> not alive", zz is False, repr(zz))
        # macOS / BSD (no /proc): the existence probe, then `ps -o stat=`
        fos2 = _FakeOS("posix", kill=lambda pid, sig: None)
        ps_calls: list = []

        def _ps_run(argv, **kw):
            ps_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="Z+\n", stderr="")
        with _Patch(P, is_windows=lambda: False, _PROC=str(tmp / "noproc"), os=fos2,
                    subprocess=types.SimpleNamespace(run=_ps_run, TimeoutExpired=subprocess.TimeoutExpired)):
            got = P._pid_alive(5201)
        check("no /proc (macOS): `ps -o stat=` Z -> not alive", got is False and ps_calls
              and "stat=" in " ".join(ps_calls[0]), f"{got!r} {ps_calls}")
        # a real Windows host simulating POSIX (is_windows stubbed) must never reach os.kill (it TERMINATES there)
        if os.name == "nt":
            kills3: list = []
            fos3 = _FakeOS("nt", kill=lambda pid, sig: kills3.append((pid, sig)))
            with _Patch(P, is_windows=lambda: False, _PROC=str(tmp / "noproc"), os=fos3):
                P._pid_alive(4_000_000_001)
            check("real Windows host + is_windows stubbed False: os.kill is never called", kills3 == [], str(kills3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gate_b_terminate_reaps_own_child() -> None:
    print("\n-- B: _terminate_pid reaps its own child — a stop does not wait ~7 s and report FAILED over a zombie --")
    tmp = Path(tempfile.mkdtemp(prefix="dmr7t"))
    try:
        proc = tmp / "proc"
        _proc_stat(proc, 8001, "S")
        reaped: list = []
        signals: list = []

        def _kill(pid, sig):          # like Linux: ESRCH once the process (its /proc entry) is gone
            if not (proc / str(pid)).exists():
                raise ProcessLookupError(pid)

        def _killpg(pg, sig):
            signals.append((pg, sig))
            _proc_stat(proc, 8001, "Z")            # the child exits and becomes a zombie until reaped

        def _waitpid(pid, flags):
            reaped.append((pid, flags))
            if (proc / str(pid)).exists() and "Z" in (proc / str(pid) / "stat").read_text():
                shutil.rmtree(proc / str(pid), ignore_errors=True)
                return pid, 0
            return 0, 0
        fos = _FakeOS("posix", getpgid=lambda pid: pid, getpgrp=lambda: 1, killpg=_killpg,
                      kill=_kill, waitpid=_waitpid)
        t0 = time.monotonic()
        with _Patch(P, is_windows=lambda: False, _PROC=str(proc), os=fos):
            ok = P._terminate_pid(8001, tree=True)
        dt = time.monotonic() - t0
        check("tree terminate of a child that exits -> True", ok is True, repr(ok))
        check("... without the ~7 s zombie wait", dt < 2.0, f"{dt:.1f}s")
        check("... SIGTERM to its group (never SIGKILL)", signals == [(8001, 15)], str(signals))
        check("... and the zombie child was reaped (waitpid WNOHANG)", any(p == 8001 for p, _f in reaped), str(reaped))
        # not our child (ChildProcessError): the zombie still reads as dead
        _proc_stat(proc, 8002, "S")

        def _killpg2(pg, sig):
            _proc_stat(proc, 8002, "Z")

        def _waitpid2(pid, flags):
            raise ChildProcessError("not a child")
        fos2 = _FakeOS("posix", getpgid=lambda pid: pid, getpgrp=lambda: 1, killpg=_killpg2,
                       kill=_kill, waitpid=_waitpid2)
        t0 = time.monotonic()
        with _Patch(P, is_windows=lambda: False, _PROC=str(proc), os=fos2):
            ok2 = P._terminate_pid(8002, tree=True)
        check("a zombie that is NOT our child (orphaned under a non-reaping PID 1) -> True, fast",
              ok2 is True and time.monotonic() - t0 < 2.0, repr(ok2))
        # a tree stop of a group MEMBER (Neo4j's server JVM; its group leader is the launcher) waits for the leader too
        _proc_stat(proc, 9000, "S")
        _proc_stat(proc, 9001, "S")
        polls = {"leader": 0}

        def _killpg3(pg, sig):
            shutil.rmtree(proc / "9001", ignore_errors=True)       # the JVM exits at once

        def _waitpid3(pid, flags):
            if pid == 9000:
                polls["leader"] += 1
                if polls["leader"] >= 3:                             # the launcher exits a little later
                    shutil.rmtree(proc / "9000", ignore_errors=True)
            raise ChildProcessError("not a child")
        fos3 = _FakeOS("posix", getpgid=lambda pid: 9000, getpgrp=lambda: 1, killpg=_killpg3,
                       kill=_kill, waitpid=_waitpid3)
        with _Patch(P, is_windows=lambda: False, _PROC=str(proc), os=fos3):
            ok3 = P._terminate_pid(9001, tree=True)
        check("tree stop of a non-leader member: done only once its group LEADER (the launcher) exited too",
              ok3 is True and polls["leader"] >= 3 and not (proc / "9000").exists(), f"{ok3} {polls}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# =========================================================================== C — H2: base JVM under a stale pid file

def _base_pidfile(pid) -> Path:
    pf = P.paths(None)["pidfile"]
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    return pf


def gate_c_stale_base_pidfile_recognised() -> None:
    print("\n-- C (I4/H2): DeepMap's base JVM under a STALE pid file is recognised by its conf dir and re-recorded --")
    with _Sandbox(occupied=set(BASE)) as (home, work):
        _binary()
        base_conf = P.paths(None)["conf_dir"]
        w = _NeoWorld({9100: dict(cmd=_jvm_cmd(base_conf), created=500.0)})
        pf = _base_pidfile(31337)                       # the dead loser of the v0.27.4 start race
        with _Patch(P, **w.stubs(is_windows=P.is_windows)):
            alive = P._legacy_base_instance_alive()
        check("_legacy_base_instance_alive -> True (the base conf's server JVM holds the base ports)",
              alive is True, repr(alive))
        check("... its pid file is re-recorded (stop / hibernate / the owner record work again)",
              pf.exists() and pf.read_text().strip() == "9100", pf.read_text() if pf.exists() else "")
        idf = P._neo4j_identity_file(pf)
        rec = json.loads(idf.read_text(encoding="utf-8")) if idf.exists() else {}
        check("... with its start stamp", rec.get("pid") == 9100 and rec.get("created") == 500.0, str(rec))
    for label, conf_of, binary, occupied in (
            ("the only JVM runs a per-project INSTANCE's conf", "inst", True, set(BASE)),
            ("no extracted binary under this home", "base", False, set(BASE)),
            ("base ports free", "base", True, set())):
        with _Sandbox(occupied=occupied) as (home, work):
            if binary:
                _binary()
            conf = P.paths(None)["conf_dir"] if conf_of == "base" else P.paths(None, KEY)["conf_dir"]
            w = _NeoWorld({9100: dict(cmd=_jvm_cmd(conf), created=500.0)})
            pf = _base_pidfile(31337)
            with _Patch(P, **w.stubs(is_windows=P.is_windows)):
                alive = P._legacy_base_instance_alive()
            check(f"control ({label}): not DeepMap's base instance, pid file untouched",
                  alive is False and pf.read_text().strip() == "31337", f"{alive!r} {pf.read_text()}")
            if not binary or not occupied:
                check(f"control ({label}): ... and no process scan at all", w.listed == 0, f"listed={w.listed}")


def gate_c_real_owner_not_locked_out() -> None:
    print("\n-- C: the review's H2 repro — the real owner A is routed to its store, not moved to a new instance --")
    sb = _Sandbox(bolt="up")
    with sb as (home, work):
        a, b = _mk_project(work, "a", marker=True), _mk_project(work, "b")
        _claim_base(b)                                   # the <= v0.27.4 mis-adoption of A's store
        PR.allocate_ports(a)
        sb.occupied.update(BASE)                         # DeepMap's base JVM serves 7687/7474
        _legacy_store()
        _binary()
        base_conf = P.paths(None)["conf_dir"]
        w = _NeoWorld({9100: dict(cmd=_jvm_cmd(base_conf), created=500.0)})
        _base_pidfile(31337)
        with _Patch(P, **w.stubs(is_windows=P.is_windows,
                                  _make_driver=_driver_factory(_GraphDrv(owner=_ident(a), nodes=9)))):
            route = P._neo4j_lookup_route(str(a))
            _quiet(P._resolve_ports, str(a), allocate=True)
        check("lookup: A is routed to the base store (legacy-unverified), not 'no instance of its own'",
              route[2] == "legacy-unverified" and route[0] == BASE, str(route))
        check("setup: A keeps its store (the base pair), no new instance / full re-sync elsewhere",
              PR.lookup_neo4j_ports(a) == BASE, str(PR.lookup_neo4j_ports(a)))
        check("... the owner record names A", PR._same_project_path(str(P._base_owner_root() or ""), _ident(a)),
              str(P._base_owner_root()))


# =========================================================================== D — H1 listener attribution + blocker

def _blocker_path(target) -> Path:
    return Path(os.environ["DEEPMAP_HOME"]) / "run" / f"setup-blocked-{PR.project_key(target)}.json"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def gate_d_unattributed_listener_blocks_setup_restart() -> None:
    print("\n-- D (I4/H1): a listener setup can't attribute (stale pid file / none) on THIS project's port is a BLOCKER --")
    for label, seed in (("a STALE pid file (dead pid — e.g. the loser of the v0.27.4 start race)", "stale"),
                        ("NO pid file at all (an unmanaged listener)", "none")):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            w = _World({5601: (SEARCH, s)})
            if seed == "stale":
                _seed_pidfile_only(SEARCH, s, 5601)
            w.live.clear()                               # the recorded pid is gone; the port still answers
            with _Patch(P, **w.stubs()):
                ok, out = _captured(P.start_mcp_servers, restart_own=True, target=str(a))
            check(f"{label}: start_mcp_servers(restart_own) FAILS (never 'already listening')", ok is False, repr(ok))
            check(f"{label}: ... recorded in _MCP_BLOCKED", SEARCH in P._MCP_BLOCKED, str(P._MCP_BLOCKED))
            check(f"{label}: ... nothing signalled, the other server still starts",
                  w.killed == [] and (EMBED, e) in w.spawned and (SEARCH, s) not in w.spawned,
                  f"{w.killed} {w.spawned}")
            check(f"{label}: ... the ERROR names the port and a by-hand stop",
                  "ERROR" in out and str(s) in out and "by hand" in out.lower(), out.strip()[-400:])


def gate_d_setup_fails_and_persists_blocker() -> None:
    print("\n-- D: setup's verdict is FAILED over such a listener, and the blocker is PERSISTED for heal / the hooks --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _clear_port_env()
        _seed_pidfile_only(SEARCH, s, 5601)
        w = _World({5601: (SEARCH, s)})
        w.live.clear()
        hint, vmcp = _Spy(None), _Spy(True)
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs(_print_mcp_hint=hint, verify_mcp=vmcp)):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("setup -> False (the review's 'setup -> True, reload now' is gone)", ok is False, repr(ok))
        check("... verdict FAILED naming the held port, no reload hint, no verify against it",
              "setup verdict: FAILED" in out and str(s) in out and hint.calls == [] and vmcp.calls == [],
              f"{hint.calls} {vmcp.calls} {out.strip()[-300:]}")
        bp = _blocker_path(a)
        rec = _read_json(bp)
        check("... the blocker is persisted at <home>/run/setup-blocked-<key>.json (W1's contract)", bp.is_file(),
              str(bp))
        check("... kind mcp-blocked, the server, its port and a reason naming /deep-map:setup",
              rec.get("kind") == "mcp-blocked" and SEARCH in (rec.get("servers") or [])
              and s in (rec.get("ports") or []) and "/deep-map:setup" in str(rec.get("reason")), str(rec))
        check("... written as UTF-8 JSON with LF", bp.is_file() and b"\r" not in bp.read_bytes())
    # a later successful setup clears it
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _clear_port_env()
        bp = _blocker_path(a)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps({"kind": "mcp-blocked", "reason": "old", "servers": [SEARCH], "ports": [s]}),
                      encoding="utf-8")
        w = _World({})
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs()):
            ok = _quiet(P.setup, str(a), password="deepmap")
        check("a later setup that starts this project's own servers succeeds and CLEARS the blocker",
              ok is True and not bp.exists(), f"ok={ok} exists={bp.exists()}")


def gate_d_controls() -> None:
    print("\n-- D controls: a proven own current-config listener is fine; heal / mcp-start stay non-disruptive --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        os.environ["NEO4J_URI"] = "bolt://localhost:7689"
        _seed_owned(SEARCH, s, 5601, a, uri="bolt://localhost:7689", created=100.0)
        w = _World({5601: (SEARCH, s)}, created=100.0, kill_ok=False)   # it won't stop, but it is provably ours
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=str(a))
        check("own sidecar + CURRENT config + verified process (could not be restarted): not a blocker",
              ok is True and SEARCH not in P._MCP_BLOCKED, f"ok={ok} {P._MCP_BLOCKED}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _World({5601: (SEARCH, s)})
        w.live.clear()
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_servers, target=str(a))
        check("heal / mcp-start (no restart_own): an unattributed listener is still 'already listening'",
              ok is True and SEARCH not in P._MCP_BLOCKED, f"ok={ok} {P._MCP_BLOCKED}")


def gate_d_verify_label() -> None:
    print("\n-- D: verify_mcp checks the server's E4 project label (a pre-upgrade server has none) --")
    probe = getattr(P, "_probe_mcp_instructions", None)
    check("provision exposes _probe_mcp_instructions", callable(probe))
    ok_smoke = types.SimpleNamespace(
        run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="PASS: deepmap_search responded", stderr=""),
        TimeoutExpired=subprocess.TimeoutExpired)
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        root = P._server_project_root(str(a))
        mine = (f"This DeepMap server indexes project a ({root}). deepmap_search and deepmap_impact answer about "
                "that project's code only.")
        other = ("This DeepMap server indexes project zeta (C:/elsewhere/zeta). deepmap_search and deepmap_impact "
                 "answer about that project's code only.")
        for label, answer, want in (("names THIS project", (True, mine), True),
                                    ("no label (a pre-upgrade server)", (True, None), False),
                                    ("names ANOTHER project", (True, other), False),
                                    ("the label probe could not run (unknown) -> the smoke test decides",
                                     (False, None), True)):
            with _Patch(P, _probe_mcp_instructions=lambda url, timeout=None, _a=answer: _a, subprocess=ok_smoke):
                got, out = _captured(P.verify_mcp, SEARCH, target=str(a))
            check(f"verify_mcp({label}) -> {want}", got is want, f"{got!r} {out.strip()[-300:]}")
            if not want:
                check(f"... ({label}) recorded as BLOCKED (not a cold start)", SEARCH in getattr(P, "_VERIFY_BLOCKED", {}),
                      str(getattr(P, "_VERIFY_BLOCKED", None)))
        with _Patch(P, _probe_mcp_instructions=lambda url, timeout=None: (True, None), subprocess=ok_smoke):
            got = _quiet(P.verify_mcp, SEARCH)
        check("verify_mcp without a target (mcp-start / older callers) is unchanged", got is True, repr(got))
    # setup: a label mismatch is FAILED (not DEGRADED) and persisted
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        _clear_port_env()
        w = _World({})

        def _vm(name, *, deadline=None, target=None):
            if target is not None and name == SEARCH:
                P._VERIFY_BLOCKED[name] = "deepmap-search answers without a project label (an older build)"
                return False
            return True
        hint = _Spy(None)
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs(verify_mcp=_vm, _print_mcp_hint=hint)):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("setup: verify passes target -> a label mismatch is FAILED, no reload hint", ok is False
              and "setup verdict: FAILED" in out and hint.calls == [], f"ok={ok} {out.strip()[-300:]}")
        check("... and the blocker is persisted", _read_json(_blocker_path(a)).get("kind") == "mcp-blocked")


def gate_d_heal_honours_blocker() -> None:
    print("\n-- D: heal honours the persisted blocker (refusal_kind mcp-blocked) until the held listener is gone --")
    for label, listening, want_rc in (("the blocked port is still held", True, 1),
                                      ("the held listener is gone (heal starts a fresh server)", False, 0)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
            bp = _blocker_path(a)
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_text(json.dumps({"kind": "mcp-blocked", "reason": f"{SEARCH} on 127.0.0.1:{s} is held",
                                      "servers": [SEARCH], "ports": [s], "ts_epoch": time.time()}), encoding="utf-8")
            w = _World({5601: (SEARCH, s)} if listening else {})
            if listening:
                w.live.clear()
            with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None):
                rc, out = _captured(P.main, ["heal", str(a)])
            st = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
            if listening:
                check(f"{label}: heal rc 1, mcp_ok false", rc == 1 and st.get("mcp_ok") is False, f"rc={rc} {st}")
                check(f"{label}: ... refused + refusal_kind 'mcp-blocked' persisted (final for old AND new hooks)",
                      st.get("refused") is True and st.get("refusal_kind") == "mcp-blocked", str(st))
                check(f"{label}: ... the held port in mcp_unverified, a reason naming it and /deep-map:setup",
                      s in (st.get("mcp_unverified") or []) and str(s) in str(st.get("reason"))
                      and "/deep-map:setup" in str(st.get("reason")), str(st))
                check(f"{label}: ... the blocker stays", bp.exists())
                check(f"{label}: ... nothing signalled", w.killed == [], str(w.killed))
            else:
                check(f"{label}: heal rc 0, mcp_ok true", rc == want_rc and st.get("mcp_ok") is True, f"rc={rc} {st}")
                check(f"{label}: ... the blocker is lifted (cleared)", not bp.exists())
    check("provision._REFUSAL_KINDS unchanged (the hooks' drift lock): ('foreign-base-claim', 'legacy-owner')",
          tuple(P._REFUSAL_KINDS) == ("foreign-base-claim", "legacy-owner"), str(P._REFUSAL_KINDS))
    # mcp-start honours it too (and lifts it the same way)
    for label, listening, want_rc in (("mcp-start, the blocked port still held", True, 1),
                                      ("mcp-start, the held listener gone", False, 0)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
            bp = _blocker_path(a)
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_text(json.dumps({"kind": "mcp-blocked", "reason": f"{SEARCH} on 127.0.0.1:{s} is held",
                                      "servers": [SEARCH], "ports": [s], "ts_epoch": time.time()}), encoding="utf-8")
            w = _World({5601: (SEARCH, s)} if listening else {})
            if listening:
                w.live.clear()
            with _Patch(P, **w.stubs()), _Patch(P, verify_mcp=lambda *x, **k: True, _ensure_mcp_json=lambda t: None,
                                                _print_mcp_hint=lambda *x, **k: None):
                rc, out = _captured(P.main, ["mcp-start", str(a)])
            check(f"{label}: rc {want_rc}", rc == want_rc, f"rc={rc} {out.strip()[-300:]}")
            if listening:
                check(f"{label}: ... says the port is still held + names /deep-map:setup, the blocker stays",
                      str(s) in out and "/deep-map:setup" in out and bp.exists(), out.strip()[-300:])
            else:
                check(f"{label}: ... the blocker is lifted", not bp.exists())


# =========================================================================== E — stop rc

def gate_e_stop_nothing_to_stop_rc0() -> None:
    print("\n-- E (T8d-X4): `provision.py stop` with nothing to stop exits 0 unless the bolt port is still held --")
    for label, pid, occupied, alive_pid, want in (
            ("no pid file, bolt free", None, False, None, 0),
            ("a stale pid file (dead pid), bolt free", 31337, False, None, 0),
            ("no pid file, but its bolt port is still LISTENING (not ours to stop)", None, True, None, 1),
            ("a live pid that can't be verified (unreadable start time)", 4242, False, 4242, 1)):
        sb = _Sandbox()
        with sb as (home, work):
            a = _mk_project(work, "a")
            PR.allocate_ports(a)
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
            bolt = PR.lookup_neo4j_ports(a)[0]
            if occupied:
                sb.occupied.add(bolt)
            k = PR.project_key(a)
            if pid is not None:
                _write_pid(k, pid)
            stubs = {}
            if alive_pid is not None:
                stubs = dict(_pid_alive=lambda p, _x=alive_pid: int(p) == _x,
                             _pid_cmdline=lambda p: f"cmd.exe /c {P.paths(None)['neo4j_bin']} console",
                             _pid_create_time=lambda p: None)
            with _Patch(P, **stubs):
                rc, out = _captured(P.main, ["stop", str(a)])
            check(f"{label}: rc {want}", rc == want, f"rc={rc} {out.strip()[-300:]}")
            if want == 1 and occupied:
                check(f"{label}: ... says the port is still held", str(bolt) in out and "listen" in out.lower(),
                      out.strip()[-300:])


# =========================================================================== F — write_mcp_json

def gate_f_write_mcp_json_bom_empty_nondict() -> None:
    print("\n-- F (T10d-11): write_mcp_json — BOM kept + parsed, empty file = {}, non-object mcpServers refused --")
    bom = "\ufeff"
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        mj = a / ".mcp.json"
        mj.write_text(bom + '{\n  "mcpServers": {\n    "other": {"command": "x"}\n  }\n}\n', encoding="utf-8")
        got, out = _captured(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
        raw = mj.read_bytes()
        obj = json.loads(raw.decode("utf-8-sig")) if raw else {}
        check("BOM + valid JSON: written (not 'invalid JSON')", got not in (False, None), f"{got!r} {out.strip()[-200:]}")
        check("... the BOM is kept (Claude Code accepts it; the user's bytes stay theirs)", raw.startswith(b"\xef\xbb\xbf"))
        check("... the other entry is preserved + the deepmap entries added",
              obj.get("mcpServers", {}).get("other") == {"command": "x"}
              and obj["mcpServers"].get(SEARCH, {}).get("url") == "http://127.0.0.1:8801/mcp", str(obj))
        before = mj.read_bytes()
        got2 = _quiet(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
        check("BOM + already-correct entries: a semantic no-op (bytes unchanged)", got2 not in (False, None)
              and mj.read_bytes() == before)
        check("_ports_from_mcp_json reads a BOM-prefixed file", P._ports_from_mcp_json(str(a)) == (8801, 8802),
              str(P._ports_from_mcp_json(str(a))))
    for label, text in (("an EMPTY file", ""), ("a whitespace-only file", "  \n\n")):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            mj = a / ".mcp.json"
            mj.write_text(text, encoding="utf-8")
            got = _quiet(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
            obj = _read_json(mj)
            check(f"{label}: treated as {{}} — written", got not in (False, None)
                  and obj.get("mcpServers", {}).get(EMBED, {}).get("url") == "http://127.0.0.1:8802/mcp", str(obj))
    for label, val in (("a LIST", "[]"), ("a STRING", '"x"'), ("a NUMBER", "3")):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            mj = a / ".mcp.json"
            body = '{"mcpServers": ' + val + ', "keep": 1}\n'
            mj.write_text(body, encoding="utf-8")
            got, out = _captured(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
            check(f"mcpServers is {label}: refused (False), the file untouched, a WARNING naming mcpServers",
                  got is False and mj.read_text(encoding="utf-8") == body and "mcpServers" in out, f"{got!r} {out[-200:]}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        mj = a / ".mcp.json"
        mj.write_text('{"mcpServers": null}\n', encoding="utf-8")
        got = _quiet(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
        check("mcpServers null: treated as absent — written", got not in (False, None)
              and SEARCH in (_read_json(mj).get("mcpServers") or {}), str(_read_json(mj)))
    # the git-tracked NOTE (critic T5#13)
    git = shutil.which("git")
    if git:
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            mj = a / ".mcp.json"
            mj.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
            for args in (["add", ".mcp.json"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
                subprocess.run([git, *args], cwd=str(a), capture_output=True, timeout=30)
            got, out = _captured(P.write_mcp_json, str(a), "127.0.0.1", 8801, 8802)
            check("a git-TRACKED .mcp.json rewrite prints the NOTE (review before committing)",
                  got not in (False, None) and "git-tracked" in out and "git diff .mcp.json" in out, out.strip()[-300:])


# =========================================================================== G — census before provisioning

def gate_g_census_before_provisioning() -> None:
    print("\n-- G (T10d-W): setup runs a source census BEFORE ports / Neo4j — an empty dir pays nothing --")
    for label, files, want_census_fail in (
            ("an EMPTY project (only .git)", {}, True),
            ("a docs-only project", {"README.md": "# x", "notes.txt": "x", "data.json": "{}"}, True),
            ("source only under node_modules/ (never indexed)", {"node_modules/lib/x.js": "var a"}, True),
            ("one Python file", {"app.py": "def f():\n    pass\n"}, False),
            ("source only under build/ (an engine-skipped dir the rescue can re-include)",
             {"build/core.py": "def f():\n    pass\n"}, False),
            ("an UPPER-case extension (MAIN.PAS)", {"MAIN.PAS": "program x;"}, False)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            for rel, body in files.items():
                f = a / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(body, encoding="utf-8")
            resolve = _Spy((None, "stop-here (gate)", None))
            ens = _Spy(True)
            with _Patch(P, _resolve_ports=resolve, ensure_neo4j=ens):
                ok, out = _captured(P.setup, str(a), password="deepmap")
            if want_census_fail:
                check(f"{label}: setup FAILS at the census", ok is False and "setup verdict: FAILED" in out
                      and "source" in out.lower(), f"ok={ok} {out.strip()[-300:]}")
                check(f"{label}: ... before any port allocation / Neo4j start", resolve.calls == [] and ens.calls == [],
                      f"{resolve.calls} {ens.calls}")
                check(f"{label}: ... no registry entry was created", PR.lookup_ports(a) is None, str(PR.lookup_ports(a)))
            else:
                check(f"{label}: the census passes (setup goes on to allocate ports)", len(resolve.calls) == 1,
                      f"{resolve.calls} {out.strip()[-300:]}")
    with _Sandbox() as (home, work):
        resolve = _Spy((None, "stop-here (gate)", None))
        with _Patch(P, _resolve_ports=resolve):
            ok, out = _captured(P.setup, str(work / "no-such-dir"), password="deepmap")
        check("a target that does not exist: FAILED naming it, nothing allocated", ok is False
              and "does not exist" in out and resolve.calls == [], f"ok={ok} {out.strip()[-200:]}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        resolve = _Spy((None, "stop-here (gate)", None))
        with _Patch(P, _resolve_ports=resolve, _load=lambda name: _OkSync()):
            _quiet(P.setup, str(a), password="deepmap")
        check("a caller that stubs setup's module loader (_load) skips the census (the gates' fake sync)",
              len(resolve.calls) == 1, str(resolve.calls))


class _FailedSync:
    last_verdict = None

    def sync(self, target, **k):
        self.last_verdict = "FAILED"
        return True


def gate_g_failed_sync_leaves_instance_as_found() -> None:
    print("\n-- G: a FAILED sync stops the Neo4j instance THIS setup started (and no longer says 'nothing was started') --")
    for label, started in (("setup started the instance", True), ("the instance was already running", False)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a")
            PR.allocate_ports(a)
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)

            def _ens(*, diag=None, _s=started, **k):
                if diag is not None and _s:
                    diag["started"] = True
                return True
            stop = _Spy(True)
            with _Patch(P, ensure_neo4j=_ens, stop_neo4j=stop, _load=lambda name: _FailedSync(),
                        _resolve_ports=lambda t, **k: (None, None, None)):
                ok, out = _captured(P.setup, str(a), password="deepmap")
            check(f"{label}: setup -> False with a FAILED verdict", ok is False and "setup verdict: FAILED" in out,
                  f"ok={ok} {out.strip()[-200:]}")
            check(f"{label}: ... never claims 'nothing was started'", "nothing was started" not in out, out.strip()[-300:])
            if started:
                check(f"{label}: ... the instance it started is stopped again (no idle JVM for an empty graph)",
                      len(stop.calls) == 1 and stop.calls[0][1].get("project_key") == PR.project_key(a), str(stop.calls))
            else:
                check(f"{label}: ... a running instance setup did not start is left running", stop.calls == [],
                      str(stop.calls))


# =========================================================================== H — MAX_PATH

def _long_home(base: Path, total: int = 176) -> Path:
    d = base
    i = 0
    while len(str(d)) < total:
        d = d / f"seg{i:02d}xxxxxxxxxxxxxxxx"
        i += 1
    d.mkdir(parents=True, exist_ok=True)
    return d


def gate_h_maxpath_preflight_and_extract() -> None:
    print("\n-- H (T6#9): a long DEEPMAP_HOME on Windows — preflight refuses before the download; extraction never raises --")
    fn = getattr(P, "_win_long_paths_enabled", None)
    check("provision exposes _win_long_paths_enabled()", callable(fn))
    if callable(fn):
        check("... it answers a bool on this host", isinstance(fn(), bool), repr(fn()))
    with _Sandbox() as (home, work):
        lh = _long_home(Path(home))
        os.environ["DEEPMAP_HOME"] = str(lh)
        dl = _Spy(None)
        with _Patch(P, is_windows=lambda: True, _win_long_paths_enabled=lambda: False,
                    _java_version=lambda: (21, 'openjdk version "21"'), _download_extract_shared=dl):
            diag: dict = {}
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:7689", project_key=KEY, bolt=7689, http=7476,
                                diag=diag)
        check("Windows, long paths disabled, binary not yet extracted: ensure_neo4j -> False", ok is False, repr(ok))
        check("... a PREFLIGHT failure naming MAX_PATH + the remedy (a short DEEPMAP_HOME / long paths)",
              "PREFLIGHT FAILED" in out and "MAX_PATH" in out and "DEEPMAP_HOME" in out, out.strip()[-400:])
        check("... before any download / extraction", dl.calls == [], str(dl.calls))
    for label, stubs in (("long paths ENABLED", dict(is_windows=lambda: True, _win_long_paths_enabled=lambda: True)),
                         ("POSIX", dict(is_windows=lambda: False, _win_long_paths_enabled=lambda: False)),
                         ("the binary is already extracted", dict(is_windows=lambda: True,
                                                                  _win_long_paths_enabled=lambda: False,
                                                                  _shared_binary_present=lambda v=None: True))):
        with _Sandbox() as (home, work):
            lh = _long_home(Path(home))
            os.environ["DEEPMAP_HOME"] = str(lh)
            with _Patch(P, _java_version=lambda: (21, "x"), **stubs):
                ok, out = _captured(P.preflight, P.neo4j_version(), bolt=7689, http=7476)
            check(f"control ({label}): no MAX_PATH preflight failure", "MAX_PATH" not in out, out.strip()[-200:])
    check("_install_failed_text: a long home on Windows names MAX_PATH", _txt_maxpath(True, 180) is True)
    check("_install_failed_text: a short home does not", _txt_maxpath(True, 40) is False)
    # the REAL extraction on a Windows host with long paths disabled: an actionable message, never a traceback
    real_ok = os.name == "nt" and callable(fn) and fn() is False
    if not real_ok:
        print("  [SKIP] real MAX_PATH extraction (needs Windows with LongPathsEnabled=0)")
        return
    with _Sandbox() as (home, work):
        lh = _long_home(Path(home))
        os.environ["DEEPMAP_HOME"] = str(lh)
        v = P.neo4j_version()
        zp = P.paths(v)["zip"]
        zp.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr(f"neo4j-community-{v}/lib/listenablefuture-9999.0-empty-to-avoid-conflict-with-guava.jar", b"x")
        with _Patch(P, preflight=lambda *a, **k: True, download_neo4j=lambda *a, **k: zp):
            diag = {}
            ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:7689", project_key=KEY, bolt=7689, http=7476,
                                diag=diag)
        check("real extraction past MAX_PATH: ensure_neo4j -> False (no raw traceback)",
              ok is False and not isinstance(ok, BaseException), repr(ok))
        check("... the ERROR names MAX_PATH and the remedy", "MAX_PATH" in out and "DEEPMAP_HOME" in out,
              out.strip()[-300:])


def _txt_maxpath(win: bool, n: int) -> bool:
    saved = os.environ.get("DEEPMAP_HOME")
    tmp = tempfile.mkdtemp(prefix="dmr7m")
    try:
        os.environ["DEEPMAP_HOME"] = str(Path(tmp) / ("x" * max(1, n - len(tmp) - 1)))
        with _Patch(P, is_windows=lambda: win):
            return "MAX_PATH" in P._install_failed_text(FileNotFoundError(2, "No such file"))
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved
        shutil.rmtree(tmp, ignore_errors=True)


# =========================================================================== I — wording + hibernate marker

def gate_i_restart_wording_and_hibernate_marker() -> None:
    print("\n-- I (T10d-W): a plain restart says so; a hibernated instance is told from a merely stopped one --")
    for label, initialized, want, notwant in (("an existing store", True, "starting the existing", "provisioning"),
                                               ("a fresh store", False, "provisioning", "starting the existing")):
        with _Sandbox() as (home, work):
            v = P.neo4j_version()
            if initialized:
                _binary()
                (P.paths(v, KEY)["databases"] / "neo4j").mkdir(parents=True, exist_ok=True)
            with _Patch(P, preflight=lambda *a, **k: True, _download_extract_shared=lambda v: None,
                        configure_neo4j=lambda *a, **k: False, set_initial_password=lambda *a, **k: True,
                        start_neo4j=lambda *a, **k: 7001, wait_for_bolt=lambda *a, **k: True):
                ok, out = _captured(P.ensure_neo4j, uri="bolt://localhost:7689", project_key=KEY, bolt=7689, http=7476)
            check(f"{label}: says '{want}'", ok is True and want in out and notwant not in out, out.strip()[-300:])
    with _Sandbox() as (home, work):
        a, b = _mk_project(work, "a"), _mk_project(work, "b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: False)
        kb = PR.project_key(b)
        (P.paths(None, kb)["data"]).mkdir(parents=True, exist_ok=True)
        row = lambda: next((r for r in P.instance_table() if r.get("key") == kb), {})   # noqa: E731
        check("a stopped instance with data (never hibernated) reads 'stopped'", row().get("state") == "stopped",
              str(row()))
        # hibernate_idle marks what it stopped
        _write_pid(kb, 6100)
        ent = PR.load_registry()
        ent["projects"][kb]["last_used"] = "2000-01-01T00:00:00Z"
        PR._write_registry(ent)
        with _Patch(P, _pid_alive=lambda pid: int(pid) == 6100, stop_neo4j=lambda **k: True):
            n = P.hibernate_idle(str(a), idle_minutes=1)
        check("hibernate_idle stopped the idle instance", n == 1, str(n))
        check("... and it now reads 'hibernated'", row().get("state") == "hibernated", str(row()))
        # a (re)start clears the marker
        calls: list = []
        with _Patch(P, is_windows=lambda: True, subprocess=_fake_subprocess(7001, calls),
                    _pid_create_time=lambda pid: 1.0, _instances_running=lambda env=None: 0):
            _quiet(P.start_neo4j, P.neo4j_version(), kb)
        (P.paths(None, kb)["pidfile"]).unlink(missing_ok=True)
        check("a start clears the hibernation marker (stopped again later -> 'stopped')", row().get("state") == "stopped",
              str(row()))


# =========================================================================== J — opt-in git post-commit hook

def _hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


def gate_j_git_hook_opt_in() -> None:
    print("\n-- J (I3): `provision.py git-hook` — an OPT-IN, chained, sentinel-wrapped post-commit auto-sync --")
    fn = _api("git_hook")
    if not callable(fn) or not shutil.which("git"):
        return
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        hp = _hook_path(a)
        hp.unlink(missing_ok=True)
        rc, out = _captured(fn, str(a), "install")
        body = hp.read_text(encoding="utf-8") if hp.exists() else ""
        check("install -> rc 0 and a post-commit hook", rc == 0 and hp.exists(), f"rc={rc} {out.strip()[-200:]}")
        check("... sentinel-wrapped like the installers, with the '# deepmap' marker the syncflag hook checks",
              "# >>> deepmap" in body and "# <<< deepmap <<<" in body and "# deepmap" in body, body)
        check("... a detached, commit-safe sync of this repo (nohup + & + || true), no --stale-cleanup",
              "deepmap_cli.py" in body and " sync " in body and "nohup" in body and "|| true" in body
              and "--stale-cleanup" not in body and a.name in body, body)
        bash = shutil.which("bash") or shutil.which("sh")
        if bash:
            r = subprocess.run([bash, "-n", str(hp)], capture_output=True, text=True, timeout=30)
            check("... the hook parses as sh (bash -n)", r.returncode == 0, r.stderr.strip())
        rc2 = _quiet(fn, str(a), "install")
        body2 = hp.read_text(encoding="utf-8")
        check("install again: idempotent (one block)", rc2 == 0 and body2.count("# >>> deepmap") == 1, body2)
        rc3 = _quiet(fn, str(a), "uninstall")
        check("uninstall of a hook DeepMap created: the file is removed", rc3 == 0 and not hp.exists())
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        hp = _hook_path(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        foreign = "#!/bin/sh\necho 'my own hook'\n"
        hp.write_text(foreign, encoding="utf-8")
        os.chmod(hp, 0o755)   # ROUND-8 J1: an ACTIVE hook (install refuses a disabled, non-executable one on POSIX)
        _quiet(fn, str(a), "install")
        body = hp.read_text(encoding="utf-8")
        # ROUND-8 J1: the block goes right AFTER the shebang (a trailing exec / exit can't skip it); the rest verbatim
        check("an existing hook is CHAINED (kept verbatim; our block right after its shebang)",
              body.startswith("#!/bin/sh" + chr(10) + "# >>> deepmap") and body.endswith(foreign.split(chr(10), 1)[1]),
              body)
        _quiet(fn, str(a), "uninstall")
        check("uninstall excises only our block (the user's hook stays as it was)",
              hp.read_text(encoding="utf-8").rstrip("\n") == foreign.rstrip("\n"), hp.read_text(encoding="utf-8"))
        rc, out = _captured(fn, str(a), "status")
        check("status after uninstall: not installed, rc 0", rc == 0 and "not installed" in out.lower(), out.strip())
    with _Sandbox() as (home, work):
        d = work / "plain"
        d.mkdir()
        rc, out = _captured(fn, str(d), "install")
        check("not a git work tree: rc 2 with a message", rc == 2 and "git" in out.lower(), f"rc={rc} {out.strip()}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        rc = _quiet(P.main, ["git-hook", "--install", str(a)])
        check("main: `git-hook --install <dir>` -> rc 0 + the hook", rc == 0 and _hook_path(a).exists(), f"rc={rc}")
    # setup prints the hint and never installs the hook
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        _clear_port_env()
        _hook_path(a).unlink(missing_ok=True)
        w = _World({})
        with _Patch(P, **w.stubs()), _Patch(P, **_setup_stubs()):
            ok, out = _captured(P.setup, str(a), password="deepmap")
        check("setup never installs a git hook", ok is True and not _hook_path(a).exists(), f"ok={ok}")
        check("... but prints the opt-in hint (git-hook --install)", "git-hook --install" in out, out.strip()[-400:])


# =========================================================================== K — verify_search query (T4d-I)

class _NameSess:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **k):
        class _R:
            def __init__(s, rows):
                s.rows = rows

            def __iter__(s):   # ROUND-8 J6: the query reads several rows (the first FTS-visible name)
                return iter(s.rows)

            def single(s):
                return s.rows[0] if s.rows else None
        if "body_summary" in q:
            return _R([])
        return _R([{"name": self.name}] if self.name else [])


class _NameDrv:
    def __init__(self, name):
        self.name = name

    def verify_connectivity(self):
        return None

    def session(self, *a, **k):
        return _NameSess(self.name)

    def close(self):
        return None


def gate_k_verify_search_query_from_graph() -> None:
    print("\n-- K (T4d-I): setup's end-to-end search check asks the project's OWN graph (not DeepMap's vocabulary) --")
    seen: list = []

    class _Ret:
        def __init__(self, drv, embed_fn=None):
            pass

        def search(self, q, top_k=5):
            seen.append(q)
            return [{"name": "x", "type": "Function"}]
    fake_mods = {"embeddings": types.SimpleNamespace(embed_query=lambda t: [0.0]),
                 "retrieval": types.SimpleNamespace(DeepMapRetriever=_Ret)}
    # ROUND-8 J6: a dotted name is used WHOLE — the BM25 analyzer keeps `a.b.c` as one token, so its bare tail would
    # never match by keyword (an FTS-only setup reported FAILED over a working graph)
    for label, name, want in (("a graph with code nodes", "pkg.mod.parse_config", "pkg.mod.parse_config"),
                              ("no readable name", None, "fuse ranked search results")):
        seen.clear()
        with _Patch(P, _load=lambda n: fake_mods[n], _make_driver=lambda *a, **k: _NameDrv(name)):
            ok = _quiet(P.verify_search, "bolt://localhost:7689", "neo4j", "deepmap")
        check(f"{label}: the query is {want!r}", ok is True and seen == [want], f"{ok} {seen}")
    seen.clear()
    with _Patch(P, _load=lambda n: fake_mods[n], _make_driver=lambda *a, **k: _NameDrv("x")):
        _quiet(P.verify_search, "bolt://localhost:7689", "neo4j", "deepmap", "explicit query")
    check("an explicit query is used as given", seen == ["explicit query"], str(seen))


# =========================================================================== main

def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-7 gates (hermetic, W2): POSIX Neo4j pid file, zombies, stale base JVM,")
    print("          H1 listener attribution + persisted blocker, stop rc, .mcp.json, census, MAX_PATH, git hook")
    print("=" * 68)
    for g in (gate_a_posix_start_never_prewrites_pidfile, gate_a_windows_start_unchanged,
              gate_a_posix_start_clears_blocking_pidfile, gate_a_posix_stop_via_launcher,
              gate_a_posix_stop_never_signals_unproven, gate_a_record_server_identity, gate_a_conf_dir_parse,
              gate_a_stale_pidfile_jvm_found_by_conf,
              gate_b_zombie_not_alive, gate_b_terminate_reaps_own_child,
              gate_c_stale_base_pidfile_recognised, gate_c_real_owner_not_locked_out,
              gate_d_unattributed_listener_blocks_setup_restart, gate_d_setup_fails_and_persists_blocker,
              gate_d_controls, gate_d_verify_label, gate_d_heal_honours_blocker,
              gate_e_stop_nothing_to_stop_rc0,
              gate_f_write_mcp_json_bom_empty_nondict,
              gate_g_census_before_provisioning, gate_g_failed_sync_leaves_instance_as_found,
              gate_h_maxpath_preflight_and_extract,
              gate_i_restart_wording_and_hibernate_marker,
              gate_j_git_hook_opt_in,
              gate_k_verify_search_query_from_graph):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
