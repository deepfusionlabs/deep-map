#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the 2026-09 FOREIGN-BASE-NEO4J field report.

The report (reproduced on the dev box): another project's Neo4j container (`neo4j:2026.05.0`)
held the base Neo4j ports 7687/7474. `/deep-map:setup` then died in PREFLIGHT —
"port 7687 (Bolt) is already in use" — instead of giving the project its own instance, which is the
whole point of per-project isolation. Three defects, each locked here RED-before-GREEN:

  1. `_base_adopt_ok` read "cannot authenticate to the base" as "the base is DOWN" and adopted
     7687/7474 anyway (clean machine -> adopt; legacy store + <=1 project -> adopt). A base HELD by
     a listener DeepMap cannot use (and that is not DeepMap's own legacy instance) must be DEFERRED
     so the allocator scans on to the next free pair (7689/7476).
  2. The bad (7687, 7474) claim is then PERSISTED, and the allocator returns an existing entry
     without re-probing — so even a fixed allocator stays wedged. setup (the allocate path, never the
     lookup-only heal) must drop a base claim a foreign listener now holds and re-allocate.
  3. The SessionStart heal had already revived this project's MCP servers from `.mcp.json`
     (8787/8788, pointing at the foreign 7687) before the project had a registry entry. setup treated
     those ports as foreign, allocated 8789/8790 (rewriting the committed `.mcp.json`, stranding the
     old servers), and — because start_mcp_server only checks "is the port listening" — would have
     left any server on the project's own pair running with a STALE NEO4J_URI. setup now re-adopts
     the project's `.mcp.json` pair when those ports are free or held by DeepMap's OWN servers, and
     restarts its own (pid-file + command-line-verified) servers so they pick up the current config.

Hermetic: DEEPMAP_HOME -> a throwaway dir; the free-port probe, is_bolt_up, pid liveness and the
process command line are stubbed (NO sockets to real services, NO Neo4j, NO network, NO kills).
Exit 0 = green.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402
import provision as P       # noqa: E402

_PASS = 0
_FAIL = 0

_ENV_KEYS = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _run_gate(fn) -> None:
    """Report an unexpected raise (e.g. a not-yet-built helper) as ONE failed check, not a crash."""
    try:
        fn()
    except Exception as e:   # noqa: BLE001
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e}")


def _mk_project(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        try:
            subprocess.run([git, "init", "-q"], cwd=str(d), capture_output=True, timeout=30)
        except Exception:
            pass
    return d.resolve()


class _Sandbox:
    """Redirect DEEPMAP_HOME to a fresh tmp dir, stub the port probe to `occupied` (mutable via
    .occupy()), stub is_bolt_up to `bolt_up`, clear the per-project env, and restore EVERYTHING on
    exit (module globals included) so a gate can never leak into the next one."""

    def __init__(self, occupied=(), bolt_up=False, escape_hatch=False):
        self.occupied = set(occupied)
        self.bolt_up = bolt_up
        self.escape_hatch = escape_hatch

    def occupy(self, ports):
        self.occupied = set(ports)

    def __enter__(self):
        self._home = tempfile.mkdtemp()
        self._work = tempfile.mkdtemp()
        self._saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = self._home
        self._saved = {"probe": PR._port_in_use, "bolt": P.is_bolt_up,
                       "hatch": P._USER_SET_NEO4J_URI}
        PR._port_in_use = lambda host, port: port in self.occupied
        P.is_bolt_up = lambda *a, **k: bool(self.bolt_up)
        P._USER_SET_NEO4J_URI = bool(self.escape_hatch)
        return Path(self._home), Path(self._work)

    def __exit__(self, *a):
        PR._port_in_use = self._saved["probe"]
        P.is_bolt_up = self._saved["bolt"]
        P._USER_SET_NEO4J_URI = self._saved["hatch"]
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._home, ignore_errors=True)
        shutil.rmtree(self._work, ignore_errors=True)


def _make_legacy_store() -> None:
    db = P.paths(None)["databases"]
    (db / "neo4j").mkdir(parents=True, exist_ok=True)   # a non-empty legacy base store


def _make_live_legacy_pidfile(version=None) -> None:
    pf = P.paths(version)["pidfile"]
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()), encoding="utf-8")    # THIS process: guaranteed alive


def _legacy_launcher_cmd(version=None) -> str:
    """The command line DeepMap's own legacy launcher really has (cmd.exe /c <home>\\neo4j\\...\\neo4j.bat)."""
    return f'cmd.exe /c {P.paths(version)["neo4j_bin"]} console'


class _Cmdline:
    """Stub provision._pid_cmdline to a fixed answer for EVERY pid (restored on exit)."""

    def __init__(self, cmd):
        self.cmd = cmd

    def __enter__(self):
        self._saved = P._pid_cmdline
        P._pid_cmdline = lambda pid: self.cmd
        return self

    def __exit__(self, *a):
        P._pid_cmdline = self._saved


class _Image:
    """Stub provision._pid_image to a fixed answer for EVERY pid (restored on exit)."""

    def __init__(self, image):
        self.image = image

    def __enter__(self):
        self._saved = P._pid_image
        P._pid_image = lambda pid: self.image
        return self

    def __exit__(self, *a):
        P._pid_image = self._saved


class _RemoveSpy:
    """Record every PR.remove_entry call (the claim-release side effect) while still performing it."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        self._saved = PR.remove_entry

        def _spy(*a, **k):
            self.calls.append((a, k))
            return self._saved(*a, **k)
        PR.remove_entry = _spy
        return self

    def __exit__(self, *a):
        PR.remove_entry = self._saved


def _quiet(fn, *a, **k):
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# --------------------------------------------------------------------------- 1. _base_adopt_ok

def gate_foreign_base_is_deferred() -> None:
    print("\n-- _base_adopt_ok: a base HELD by a foreign listener is DEFERRED, never adopted --")
    # (a) the dev-box case on a CLEAN machine: nothing of ours on disk, a Docker Neo4j on 7687/7474
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        check("clean machine + foreign base (7687+7474 held) -> False (defer; next free pair)",
              _quiet(P._base_adopt_ok, a) is False)
    # (b) the EXACT field case: a legacy DeepMap base store on disk + <=1 registered project
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _make_legacy_store()
        PR.allocate_ports(a)   # the one registered project
        check("legacy store + 1 project + foreign base -> False (the reported wedge)",
              _quiet(P._base_adopt_ok, a) is False)
    # (c) EITHER port held is enough (preflight fails on either)
    for held in ({7687}, {7474}):
        with _Sandbox(occupied=held) as (home, work):
            a = _mk_project(work, "a")
            check(f"only {sorted(held)} held -> False (defer)", _quiet(P._base_adopt_ok, a) is False)
    # (d) the defer is ANNOUNCED (the user must learn why they got 7689, not a silent drift)
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        buf = io.StringIO()
        with redirect_stderr(buf), redirect_stdout(io.StringIO()):
            P._base_adopt_ok(a)
        msg = buf.getvalue()
        check("defer prints a NOTE naming the held ports + the NEO4J_URI way to use that instance",
              "7687" in msg and "NEO4J_URI" in msg, msg.strip()[:300])


def gate_base_adopt_non_regression() -> None:
    print("\n-- _base_adopt_ok: unchanged verdicts when the base is NOT foreign-held --")
    with _Sandbox(occupied=set()) as (home, work):
        a = _mk_project(work, "a")
        check("base FREE + clean machine -> True (first project keeps 7687: zero drift)",
              _quiet(P._base_adopt_ok, a) is True)
    with _Sandbox(occupied=set()) as (home, work):
        a = _mk_project(work, "a")
        _make_legacy_store()
        PR.allocate_ports(a)
        # round 3 (review): the adopter's OWN registry entry is not proof it owns a DOWN legacy store — only
        # the store's owner record is (written when setup synced the base / a live owner read)
        check("base FREE + legacy store + only the adopter's own entry -> False (no proof of ownership)",
              _quiet(P._base_adopt_ok, a) is False)
        P._write_base_owner_record(PR.project_identity(str(a)))
        check("base FREE + legacy store whose owner record names it -> True (the owner reclaims its base)",
              _quiet(P._base_adopt_ok, a) is True)
    # our OWN legacy instance holds the port but rejects our creds (a password mismatch): NOT
    # foreign — keep the old verdict, so preflight's "set NEO4J_PASSWORD" hint still reaches the user
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _make_live_legacy_pidfile()
        with _Cmdline(_legacy_launcher_cmd()):
            check("base held by DeepMap's OWN live legacy instance -> not treated as foreign (True)",
                  _quiet(P._base_adopt_ok, a) is True)


def gate_legacy_pid_identity() -> None:
    print("\n-- the legacy base pid file is IDENTITY-checked (a reused PID never poses as our Neo4j) --")
    # (a) the field shape: a stale legacy pid file whose PID was reused by an unrelated process, a
    #     foreign Docker Neo4j on 7687/7474 -> it is NOT our instance -> defer (the fix still runs)
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _make_legacy_store()
        PR.allocate_ports(a)
        _make_live_legacy_pidfile()
        with _Cmdline("C:\\Windows\\System32\\svchost.exe -k netsvcs"):
            check("recycled legacy PID (svchost) + foreign base -> _legacy_base_instance_alive False",
                  P._legacy_base_instance_alive() is False)
            check("recycled legacy PID + foreign base -> defer (False), not the preflight wedge",
                  _quiet(P._base_adopt_ok, a) is False)
    # (a2) the REALISTIC Windows shape (round-2 review): the recycled PID is a SYSTEM/service process,
    #      so a non-elevated user CAN'T read its command line (None) — the always-visible image decides
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _make_legacy_store()
        PR.allocate_ports(a)
        _make_live_legacy_pidfile()
        with _Cmdline(None), _Image("svchost.exe"):
            check("unreadable cmdline + image svchost.exe -> NOT our instance (False)",
                  P._legacy_base_instance_alive() is False)
            check("unreadable cmdline + svchost + foreign base -> defer (the wedge stays fixed)",
                  _quiet(P._base_adopt_ok, a) is False)
    # (a3) Linux kernel thread / zombie: command line readable but EMPTY -> not ours
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        _make_live_legacy_pidfile()
        with _Cmdline(""), _Image("kworker/0:1"):
            check("readable-but-empty cmdline (kernel thread) -> NOT our instance (False)",
                  P._legacy_base_instance_alive() is False)
    # (a4) the marker is the instance tree, not merely a path under DeepMap's home
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        _make_live_legacy_pidfile()
        log = P.deepmap_home() / "neo4j-console.log"
        with _Cmdline(f"powershell Get-Content -Wait {log}"):
            check("a process that merely mentions <home>/neo4j-console.log -> NOT our instance",
                  P._legacy_base_instance_alive() is False)
    # (b) an UNREADABLE command line on a LAUNCHER-shaped image keeps the conservative answer (ours)
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        _make_live_legacy_pidfile()
        with _Cmdline(None), _Image("cmd.exe"):
            check("unreadable cmdline + image cmd.exe (the launcher) -> conservatively ours (True)",
                  P._legacy_base_instance_alive() is True)
        with _Cmdline(None), _Image(None):
            check("unreadable cmdline AND unknown image -> conservatively ours (True)",
                  P._legacy_base_instance_alive() is True)
    # (c) a legacy instance started under ANOTHER extracted version is still seen
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        _make_live_legacy_pidfile("5.20.0")
        with _Cmdline(_legacy_launcher_cmd("5.20.0")):
            check("own legacy instance under a different NEO4J_VERSION dir -> seen (True)",
                  P._legacy_base_instance_alive() is True)
    # (d) no pid file / dead PID -> not alive
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        check("no legacy pid file -> False", P._legacy_base_instance_alive() is False)
        pf = P.paths(None)["pidfile"]
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("999999991", encoding="utf-8")
        saved = P._pid_alive
        P._pid_alive = lambda pid: False
        try:
            check("dead legacy PID -> False", P._legacy_base_instance_alive() is False)
        finally:
            P._pid_alive = saved


# --------------------------------------------------------------------------- 1b. end-to-end allocation

def gate_foreign_base_allocation_end_to_end() -> None:
    print("\n-- setup's allocate path: foreign base -> the project's OWN instance on 7689/7476 --")
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _make_legacy_store()
        _quiet(P.apply_ports_for, str(a), allocate=True)
        check("registry records the next free pair (7689, 7476), NOT the held base",
              PR.lookup_neo4j_ports(a) == (7689, 7476), str(PR.lookup_neo4j_ports(a)))
        check("NEO4J_URI injected at the project's own bolt 7689",
              os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:7689", str(os.environ.get("NEO4J_URI")))  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
        inst = P.resolved_instance(str(a))
        check("resolved_instance -> a per-project instance (key, 7689, 7476), not the legacy layout",
              inst is not None and tuple(inst[1:]) == (7689, 7476), str(inst))
        check("preflight on the ALLOCATED pair would pass the port check (neither port held)",
              not PR._port_in_use("127.0.0.1", 7689) and not PR._port_in_use("127.0.0.1", 7476))


# --------------------------------------------------------------------------- 2. poisoned registry self-heal

def _seed_base_claim(a) -> None:
    """Reproduce the persisted field state: the project's registry entry claims the base pair."""
    PR.allocate_ports(a)
    got = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
    assert got == (7687, 7474), got


def gate_poisoned_base_claim_self_heals() -> None:
    print("\n-- a PERSISTED base claim now held by a foreign listener is released on setup --")
    with _Sandbox(occupied=set()) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        mcp_before = PR.lookup_ports(a)
        sb_ports = {7687, 7474} | set(mcp_before)
        PR._port_in_use = lambda host, port: port in sb_ports
        # heal is LOOKUP-ONLY: it must never re-allocate (the .mcp.json/registry drift contract)
        _quiet(P.apply_ports_for, str(a), allocate=False)
        check("heal (allocate=False) leaves the persisted claim untouched",
              PR.lookup_neo4j_ports(a) == (7687, 7474), str(PR.lookup_neo4j_ports(a)))
        # setup (allocate=True) releases the foreign-held base claim and re-allocates
        os.environ.pop("DEEPMAP_SEARCH_PORT", None)
        os.environ.pop("DEEPMAP_EMBED_PORT", None)
        _quiet(P.apply_ports_for, str(a), allocate=True)
        check("setup re-allocates the wedged project to (7689, 7476)",
              PR.lookup_neo4j_ports(a) == (7689, 7476), str(PR.lookup_neo4j_ports(a)))
        check("the MCP pool survives the Neo4j re-allocation (only the Neo4j claim was dropped)",
              PR.lookup_ports(a) == mcp_before, f"{PR.lookup_ports(a)} vs {mcp_before}")
        check("NEO4J_URI follows the re-allocation", os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:7689",  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
              str(os.environ.get("NEO4J_URI")))


def _kept(label, a, spy) -> None:
    """A kept claim means NO release happened at all — not merely "the end pair is (7687, 7474)",
    which a release followed by a re-adoption would also produce (a vacuous check)."""
    check(f"{label}: claim NOT released (remove_entry never called)", spy.calls == [], str(spy.calls))
    check(f"{label}: registry still (7687, 7474)", PR.lookup_neo4j_ports(a) == (7687, 7474),
          str(PR.lookup_neo4j_ports(a)))


def gate_legit_base_claim_is_kept() -> None:
    print("\n-- a LEGITIMATE base claim is never released --")
    # Each case adds a legacy store + a SECOND registered project, so a wrongful release would be
    # VISIBLE: with the base down, base_adopt_ok then defers (>1 project) and the project would move.
    def _crowd(work, a):
        _make_legacy_store()
        PR.allocate_ports(_mk_project(work, "other"))
    # (a) DeepMap's own base instance merely DOWN (ports free) -> keep (revive it, keep its graph)
    with _Sandbox(occupied=set()) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        _crowd(work, a)
        with _RemoveSpy() as spy:
            _quiet(P.apply_ports_for, str(a), allocate=True)
        _kept("own base DOWN (ports free)", a, spy)
    # (b) the base is reachable WITH our credentials -> it is ours to use -> keep
    with _Sandbox(occupied={7687, 7474}, bolt_up=True) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        _crowd(work, a)
        with _RemoveSpy() as spy:
            _quiet(P.apply_ports_for, str(a), allocate=True)
        _kept("base reachable with our creds", a, spy)
    # (c) our OWN live legacy instance holds it (a password mismatch, not a foreign program) -> keep
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        _crowd(work, a)
        _make_live_legacy_pidfile()
        with _Cmdline(_legacy_launcher_cmd()), _RemoveSpy() as spy:
            _quiet(P.apply_ports_for, str(a), allocate=True)
        _kept("DeepMap's own live legacy instance holds the base", a, spy)
    # (d) the operator escape hatch never touches the registry
    with _Sandbox(occupied={7687, 7474}, escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        os.environ["NEO4J_URI"] = "bolt://operator:7000"
        with _RemoveSpy() as spy:
            _quiet(P.apply_ports_for, str(a), allocate=True)
        _kept("escape hatch (operator NEO4J_URI)", a, spy)
    # (e) CONTROL — the spy can see a release: the foreign-held case DOES call remove_entry
    with _Sandbox(occupied={7687, 7474}) as (home, work):
        a = _mk_project(work, "a")
        _seed_base_claim(a)
        with _RemoveSpy() as spy:
            _quiet(P.apply_ports_for, str(a), allocate=True)
        check("control: a foreign-held base claim IS released (the spy is not blind)", len(spy.calls) == 1,
              str(spy.calls))


# --------------------------------------------------------------------------- 3a. .mcp.json pair re-adoption

_SCRIPTS = {"deepmap-search": "deepmap_search_server.py", "deepmap-embed": "deepmap_embed_server.py"}


class _FakeProcs:
    """Stub pid liveness + command line: pid -> cmdline (None = alive but unreadable)."""

    def __init__(self, procs):
        self.procs = dict(procs)

    def __enter__(self):
        self._saved = (P._pid_alive, getattr(P, "_pid_cmdline", None), getattr(P, "_pid_create_time", None))
        P._pid_alive = lambda pid: pid in self.procs
        P._pid_cmdline = lambda pid: self.procs.get(pid)
        # D2: a fake live pid was created long ago (older than its pid file) — never a real OS lookup
        P._pid_create_time = lambda pid: 1.0 if pid in self.procs else None
        return self

    def __exit__(self, *a):
        P._pid_alive = self._saved[0]
        for name, saved in (("_pid_cmdline", self._saved[1]), ("_pid_create_time", self._saved[2])):
            if saved is None:
                try:
                    delattr(P, name)
                except AttributeError:
                    pass
            else:
                setattr(P, name, saved)


def _write_pid(name, port, pid, owner=None) -> None:
    """A per-port pid file, plus (when `owner` is a project) the owner sidecar a fixed build writes."""
    pf = P._mcp_pidfile(name, port)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    if owner is not None:
        P._write_mcp_owner(name, port, pid, owner)


def _own_cmd(name) -> str:
    return f"C:\\Python\\python.exe C:\\plugins\\deep-map\\build\\{_SCRIPTS[name]}"


def _mcp_json_bytes(a):
    p = Path(PR.project_identity(a)) / ".mcp.json"
    return p.read_bytes() if p.exists() else None


def gate_mcp_json_pair_readopted() -> None:
    print("\n-- setup re-adopts the project's .mcp.json pair (no drift, no stranded servers) --")
    own = {4242: _own_cmd("deepmap-search"), 4343: _own_cmd("deepmap-embed")}
    # (a) heal revived THIS project's servers on the .mcp.json pair before any registry entry
    with _Sandbox(occupied={8787, 8788}, escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        before = _mcp_json_bytes(a)
        _write_pid("deepmap-search", 8787, 4242, owner=a)
        _write_pid("deepmap-embed", 8788, 4343, owner=a)
        with _FakeProcs(own):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("THIS project's own servers on the .mcp.json pair -> re-adopted (8787, 8788)",
              pair == (8787, 8788) and PR.lookup_ports(a) == (8787, 8788), f"{pair} / {PR.lookup_ports(a)}")
        P.write_mcp_json(a, "127.0.0.1", *pair)
        check("the committed .mcp.json is byte-UNCHANGED (no spurious git diff)", _mcp_json_bytes(a) == before)
    # (a2) a DeepMap server with NO owner sidecar (an older build) is not provably ours -> not adopted
    with _Sandbox(occupied={8787, 8788}, escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        _write_pid("deepmap-search", 8787, 4242)
        _write_pid("deepmap-embed", 8788, 4343)
        with _FakeProcs(own):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("DeepMap server without an owner sidecar -> NOT adopted (8789, 8790)", pair == (8789, 8790), str(pair))
    # (b) a FOREIGN listener (no pid file of ours) on the .mcp.json pair -> scan on, as before
    with _Sandbox(occupied={8787, 8788}, escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        with _FakeProcs({}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("foreign listener on the .mcp.json pair -> NOT adopted (8789, 8790)", pair == (8789, 8790), str(pair))
    # (c) a pid file (even with THIS project's owner record) whose PID was RECYCLED by an unrelated
    #     process -> not ours -> not adopted
    with _Sandbox(occupied={8787, 8788}, escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        _write_pid("deepmap-search", 8787, 4242, owner=a)
        _write_pid("deepmap-embed", 8788, 4343, owner=a)
        with _FakeProcs({4242: "C:\\Windows\\notepad.exe", 4343: "/usr/bin/vim"}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("recycled pid (command line is not a DeepMap server) -> NOT adopted", pair == (8789, 8790), str(pair))
    # (d) the .mcp.json pair is FREE (a fresh clone of a repo whose .mcp.json says 8791/8792) -> keep it
    with _Sandbox(occupied=set(), escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8791, 8792)
        with _FakeProcs({}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("free .mcp.json pair -> adopted as declared (8791, 8792)", pair == (8791, 8792), str(pair))
    # (e) the .mcp.json pair is CLAIMED by another registered project -> never adopted
    with _Sandbox(occupied=set(), escape_hatch=True) as (home, work):
        b = _mk_project(work, "b")
        PR.allocate_ports(b)                       # b owns 8787/8788
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        with _FakeProcs({}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("another project's claimed pair -> NOT adopted (a gets 8789, 8790)", pair == (8789, 8790), str(pair))
    # (f) a degenerate .mcp.json (both servers on ONE port) -> ignored
    with _Sandbox(occupied=set(), escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        P.write_mcp_json(a, "127.0.0.1", 8795, 8795)
        with _FakeProcs({}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("degenerate .mcp.json (search port == embed port) -> ignored (base 8787, 8788)",
              pair == (8787, 8788), str(pair))
    # (g) an existing registry entry still WINS (the prefer is only a lost-registry recovery source)
    with _Sandbox(occupied=set(), escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)                       # registered at 8787/8788
        P.write_mcp_json(a, "127.0.0.1", 8791, 8792)
        with _FakeProcs({}):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("registered pair wins over .mcp.json (idempotent allocation unchanged)", pair == (8787, 8788), str(pair))
    # (h) allocate_ports(prefer=None) is the unchanged scan
    with _Sandbox(occupied=set()) as (home, work):
        a = _mk_project(work, "a")
        check("allocate_ports(prefer=None) -> base (8787, 8788) as before",
              PR.allocate_ports(a, prefer=None) == (8787, 8788))


def gate_no_cross_project_takeover() -> None:
    print("\n-- setup in project A NEVER adopts or restarts project Z's live servers (shared .mcp.json pair) --")
    own_cmds = {5001: _own_cmd("deepmap-search"), 5002: _own_cmd("deepmap-embed")}
    # Z: unregistered (heal never registers), its heal-revived servers live on 8787/8788, owned by Z.
    # A: a clone/worktree whose committed .mcp.json ALSO says 8787/8788. (Adversarial-review repro.)
    with _Sandbox(occupied={8787, 8788}, escape_hatch=True) as (home, work):
        z = _mk_project(work, "z")
        a = _mk_project(work, "a")
        P.write_mcp_json(z, "127.0.0.1", 8787, 8788)
        P.write_mcp_json(a, "127.0.0.1", 8787, 8788)
        _write_pid("deepmap-search", 8787, 5001, owner=z)
        _write_pid("deepmap-embed", 8788, 5002, owner=z)
        with _FakeProcs(own_cmds):
            pair = _quiet(P.apply_ports_for, str(a), allocate=True)
        check("A does NOT adopt Z's live pair -> scans to (8789, 8790)", pair == (8789, 8790), str(pair))
        check("A is registered on its own pair, Z's ports untouched", PR.lookup_ports(a) == (8789, 8790),
              str(PR.lookup_ports(a)))
        # Even if A's env ends up on Z's ports (a lock-timeout fallback to the base pair, or a partial
        # DEEPMAP_*_PORT override), A's setup restart must not signal Z's servers.
        os.environ["DEEPMAP_SEARCH_PORT"] = "8787"
        os.environ["DEEPMAP_EMBED_PORT"] = "8788"
        with _McpHarness({8787, 8788}, own_cmds) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=str(a))
        check("A's restart_own on Z's ports -> Z's servers NOT stopped", h.stopped == [], str(h.stopped))
        with _McpHarness({8787, 8788}, own_cmds) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=str(z))
        check("control: Z's own setup DOES restart Z's servers", sorted(h.stopped) == ["deepmap-embed", "deepmap-search"],
              str(h.stopped))


def gate_owner_sidecar_lifecycle() -> None:
    print("\n-- the owner sidecar is written on start (project-scoped) and cleared on stop --")

    class _FakeProc:
        pid = 777
        returncode = None

        def poll(self):
            return None

    with _Sandbox(escape_hatch=True) as (home, work):
        a = _mk_project(work, "a")
        os.environ["DEEPMAP_SEARCH_PORT"] = "8787"
        os.environ["DEEPMAP_EMBED_PORT"] = "8788"
        saved = (subprocess.Popen, P.is_http_up, P._pid_alive, P._terminate_pid)
        state = {"n": 0}

        def _http(host, port, timeout=2.0):
            state["n"] += 1
            return state["n"] > 1          # not listening for the pre-check, listening after spawn
        try:
            P.is_http_up = _http
            subprocess.Popen = lambda *x, **k: _FakeProc()
            ok = _quiet(P.start_mcp_server, "deepmap-search", target=str(a))
        finally:
            subprocess.Popen = saved[0]
        of = P._mcp_ownerfile("deepmap-search", 8787)
        rec = {}
        try:
            import json as _json
            rec = _json.loads(of.read_text(encoding="utf-8"))
        except Exception:
            pass
        check("start_mcp_server(target=A) -> started", ok is True)
        check("owner sidecar records the spawned pid + A's project key",
              rec.get("pid") == 777 and rec.get("project_key") == PR.project_key(a), str(rec))
        # a start with NO known project clears any stale owner record for that port
        state["n"] = 0
        try:
            P.is_http_up = _http
            subprocess.Popen = lambda *x, **k: _FakeProc()
            _quiet(P.start_mcp_server, "deepmap-search")
        finally:
            subprocess.Popen = saved[0]
        check("start with no target -> stale owner record cleared", not of.exists())
        # stop clears pid file AND sidecar together
        _write_pid("deepmap-search", 8787, 777, owner=a)
        saved_ident = (P._pid_cmdline, P._pid_create_time)
        try:
            P._pid_alive = lambda pid: True
            P._terminate_pid = lambda pid, tree: True
            P._pid_cmdline = lambda pid: _own_cmd("deepmap-search")   # hermetic: never a real OS lookup
            P._pid_create_time = lambda pid: 1.0                       # D2: created before its pid file
            _quiet(P.stop_mcp_server, "deepmap-search")
        finally:
            P.is_http_up, P._pid_alive, P._terminate_pid = saved[1], saved[2], saved[3]
            P._pid_cmdline, P._pid_create_time = saved_ident
        check("stop_mcp_server removes the pid file", not P._mcp_pidfile("deepmap-search", 8787).exists())
        check("stop_mcp_server removes the owner sidecar with it", not of.exists())


# --------------------------------------------------------------------------- 3b. restart own stale servers

class _McpHarness:
    """Stub the MCP lifecycle so start_mcp_servers' restart path is observable without sockets/kills."""

    def __init__(self, up, procs):
        self.up = set(up)
        self.procs = _FakeProcs(procs)
        self.stopped = []
        self.started = []

    def __enter__(self):
        self._saved = (P.is_http_up, P.stop_mcp_server, P.start_mcp_server)
        P.is_http_up = lambda host, port, timeout=2.0: port in self.up

        def _stop(name):
            self.stopped.append(name)
            self.up.discard(P.mcp_port(name))
            return True

        def _start(name, password=None, wait=40, target=None):
            self.started.append(name)
            return True
        P.stop_mcp_server = _stop
        P.start_mcp_server = _start
        self.procs.__enter__()
        return self

    def __exit__(self, *a):
        self.procs.__exit__(*a)
        P.is_http_up, P.stop_mcp_server, P.start_mcp_server = self._saved


def gate_setup_restarts_own_stale_servers() -> None:
    print("\n-- setup restarts OUR OWN already-running MCP servers (stale NEO4J_URI / old build) --")
    own = {4242: _own_cmd("deepmap-search"), 4343: _own_cmd("deepmap-embed")}
    with _Sandbox(escape_hatch=True) as (home, work):
        a = str(_mk_project(work, "a"))
        os.environ["DEEPMAP_SEARCH_PORT"] = "8787"
        os.environ["DEEPMAP_EMBED_PORT"] = "8788"
        _write_pid("deepmap-search", 8787, 4242, owner=a)
        _write_pid("deepmap-embed", 8788, 4343, owner=a)
        # (a) restart_own=True + both provably THIS project's -> both stopped, then both started fresh
        with _McpHarness({8787, 8788}, own) as h:
            ok = _quiet(P.start_mcp_servers, restart_own=True, target=a)
        check("restart_own: both own servers stopped", sorted(h.stopped) == ["deepmap-embed", "deepmap-search"],
              str(h.stopped))
        check("restart_own: both servers started after the stop", ok and len(h.started) == 2, str(h.started))
        # (b) default (heal / mcp-start): NEVER disruptive
        with _McpHarness({8787, 8788}, own) as h:
            _quiet(P.start_mcp_servers, target=a)
        check("default start_mcp_servers (heal/mcp-start) never stops a running server", h.stopped == [],
              str(h.stopped))
        # (b2) restart_own without a known project -> nothing can be proven ours -> no stop
        with _McpHarness({8787, 8788}, own) as h:
            _quiet(P.start_mcp_servers, restart_own=True)
        check("restart_own with no target -> NOT stopped", h.stopped == [], str(h.stopped))
        # (c) recycled pid -> never signalled
        with _McpHarness({8787, 8788}, {4242: "C:\\Windows\\notepad.exe", 4343: "/usr/bin/vim"}) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=a)
        check("recycled pid (not a DeepMap server) -> NOT stopped", h.stopped == [], str(h.stopped))
        # (d) command line UNREADABLE -> not stopped, and the user is told how to restart by hand
        with _McpHarness({8787, 8788}, {4242: None, 4343: None}) as h:
            buf = io.StringIO()
            with redirect_stderr(buf), redirect_stdout(io.StringIO()):
                P.start_mcp_servers(restart_own=True, target=a)
        check("unverifiable pid -> NOT stopped", h.stopped == [], str(h.stopped))
        check("unverifiable pid -> a WARNING with the manual mcp-stop hint",
              "mcp-stop" in buf.getvalue(), buf.getvalue().strip()[:300])
        # (e) nothing listening -> nothing to restart
        with _McpHarness(set(), own) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=a)
        check("port not listening -> no stop (just a start)", h.stopped == [] and len(h.started) == 2,
              f"{h.stopped} / {h.started}")
        # (g) owner sidecar names a DIFFERENT pid (the file outlived its server) -> not ours
        _write_pid("deepmap-search", 8787, 4242)          # pid file rewritten, sidecar untouched...
        P._write_mcp_owner("deepmap-search", 8787, 9999, a)   # ...and pointing at an older pid
        with _McpHarness({8787}, own) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=a)
        check("owner sidecar for a different pid -> NOT stopped", "deepmap-search" not in h.stopped, str(h.stopped))
    # (f) no pid file at all (an unmanaged listener) -> left alone
    with _Sandbox(escape_hatch=True) as (home, work):
        a = str(_mk_project(work, "a"))
        os.environ["DEEPMAP_SEARCH_PORT"] = "8787"
        os.environ["DEEPMAP_EMBED_PORT"] = "8788"
        with _McpHarness({8787, 8788}, own) as h:
            _quiet(P.start_mcp_servers, restart_own=True, target=a)
        check("no pid file of ours -> NOT stopped (unmanaged / foreign listener)", h.stopped == [], str(h.stopped))


def gate_setup_passes_restart_own() -> None:
    print("\n-- setup() (the deliberate configure step) asks for the restart; heal() does not --")
    names = ("ensure_neo4j", "verify_search", "start_mcp_servers", "verify_mcp", "_print_mcp_hint",
             "_load", "apply_ports_for", "write_mcp_json", "resolved_instance", "_ensure_mcp_json")
    saved = {n: getattr(P, n) for n in names}
    seen = {}

    class _FakeSync:
        @staticmethod
        def sync(*a, **k):
            return True
    try:
        P.ensure_neo4j = lambda **k: True
        P.verify_search = lambda *a, **k: True
        P.verify_mcp = lambda *a, **k: True
        P._print_mcp_hint = lambda *a, **k: None
        P._load = lambda name: _FakeSync
        P.apply_ports_for = lambda *a, **k: None
        P.write_mcp_json = lambda *a, **k: None
        P.resolved_instance = lambda *a, **k: None
        P._ensure_mcp_json = lambda *a, **k: None   # heal must never create a stray .mcp.json here

        def _rec(tag):
            def _f(**kw):
                seen[tag] = kw
                return True
            return _f
        P.start_mcp_servers = _rec("setup")
        _quiet(P.setup, ".", uri="x", user="y", password="z", verify=True)
        check("setup() -> start_mcp_servers(restart_own=True)", seen.get("setup", {}).get("restart_own") is True,
              str(seen.get("setup")))
        check("setup() passes its project as target (owner sidecar + restart identity)",
              seen.get("setup", {}).get("target") == ".", str(seen.get("setup")))
        P.start_mcp_servers = _rec("heal")
        _quiet(P.heal, ".", password="z")
        check("heal() -> start_mcp_servers WITHOUT restart_own (never disrupts a live session)",
              not seen.get("heal", {}).get("restart_own"), str(seen.get("heal")))
        check("heal() passes its project as target (revived servers get an owner sidecar)",
              seen.get("heal", {}).get("target") == ".", str(seen.get("heal")))
    finally:
        for n, v in saved.items():
            setattr(P, n, v)


# --------------------------------------------------------------------------- helpers + honesty

def gate_pid_cmdline_real() -> None:
    print("\n-- _pid_cmdline: reads a REAL process's command line (the pid-recycling guard) --")
    fn = getattr(P, "_pid_cmdline", None)
    check("provision._pid_cmdline exists", callable(fn))
    if not callable(fn):
        return
    me = fn(os.getpid())
    script = Path(sys.argv[0]).name or "python"   # whatever launched this process (the gate, or a wrapper)
    if sys.platform.startswith(("win", "linux")):
        check("own process -> a command line naming the running script",
              isinstance(me, str) and script in me, f"{script!r} not in {me!r}"[:240])
    else:
        check("own process -> str or None (best-effort platform)", me is None or isinstance(me, str))
    check("pid 0 -> None", fn(0) is None)
    check("pid -1 -> None", fn(-1) is None)
    check("garbage pid -> None (never raises)", fn("nope") is None)
    # a NON-ASCII command line must round-trip intact (round-2 review: the Windows console code page
    # turned 'José-א' into 'Jos?-?', so an accented/Hebrew profile path never matched the marker)
    if sys.platform.startswith(("win", "linux")):
        tag = "dm-José-א-marker"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", tag],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            got = None
            for _ in range(20):                      # the child may need a moment to exist
                got = fn(child.pid)
                if got:
                    break
                import time as _t
                _t.sleep(0.25)
            check("non-ASCII command line round-trips intact (UTF-8 forced on the pipe)",
                  isinstance(got, str) and tag in got, ascii(got)[:240])
        finally:
            child.terminate()                        # our OWN child — never a foreign process
            try:
                child.wait(timeout=10)
            except Exception:
                pass
    img = getattr(P, "_pid_image", None)
    check("provision._pid_image exists", callable(img))
    if callable(img):
        mine = img(os.getpid())
        check("own process image looks like python", isinstance(mine, str) and "python" in mine.lower(),
              repr(mine))
        check("_pid_image(0) / garbage -> None", img(0) is None and img("x") is None)


def gate_mcp_status_honest_wording() -> None:
    print("\n-- mcp_status: no stale 'the Neo4j graph is shared' claim (false since v0.26 isolation) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a")
        PR.allocate_ports(a)
        saved = P.is_http_up
        P.is_http_up = lambda *a_, **k_: False
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                P.mcp_status()
        finally:
            P.is_http_up = saved
        out = buf.getvalue()
        check("mcp_status does not claim the graph is shared", "graph is shared" not in out, out.strip()[-200:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — foreign-base Neo4j + setup port/server reconciliation gates (hermetic)")
    print("=" * 64)
    for g in (gate_foreign_base_is_deferred, gate_base_adopt_non_regression, gate_legacy_pid_identity,
              gate_foreign_base_allocation_end_to_end, gate_poisoned_base_claim_self_heals,
              gate_legit_base_claim_is_kept, gate_mcp_json_pair_readopted,
              gate_no_cross_project_takeover, gate_owner_sidecar_lifecycle,
              gate_setup_restarts_own_stale_servers, gate_setup_passes_restart_own,
              gate_pid_cmdline_real, gate_mcp_status_honest_wording):
        _run_gate(g)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
