#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-clone graph build for the benchmark — each pinned clone gets its OWN DeepMap instance (D1).

WHY. The benchmark used to build every clone's graph into ONE shared Neo4j: `provision.py setup` in the
dev repo, then `deepmap_cli.py sync <clone> --stale-cleanup` per repo, with the ON arm reading the dev
repo's MCP servers. Under the single routing rule (ROUND-2 D1) a clone that is not a registered DeepMap
project has no instance of its own: while another project owns bolt 7687 its sync REFUSES ("this project
has no DeepMap Neo4j instance of its own"), and `--switch-project` no longer overrides that — the drivers
aborted on the first repo. Even where it could still work, the ON arm's servers would be named after the
dev repo (their tool descriptions say which project they index), not the clone the agent is working in.

WHAT. The benchmark does exactly what a user does for each clone: `provision.py setup <clone>` — its own
Neo4j instance, its own graph built by the product's default sync at the clone's pinned SHA (the
.deepmap/graph_build.json SHA manifest assert_graph_at_pinned_sha checks), its own MCP (search, embed)
pair and servers. The ON arm is then pointed at THAT pair, per command (never exported: `provision.py
setup` honours an exported DEEPMAP_SEARCH_PORT as an override, so an export left over from the previous
repo would wire the next clone's servers onto the previous clone's ports). run_benchmark.do_run verifies
before spending that the ON-arm pair is the measured clone's own (assert_on_arm_serves_repo).

    python benchmark/clone_graph.py build <clone>   # setup: own instance + graph + MCP servers (idempotent)
    python benchmark/clone_graph.py ports <clone>   # prints "<search> <embed>" (exit 2: not built)
    python benchmark/clone_graph.py stop  <clone>   # stop its MCP servers + Neo4j (frees RAM; keeps the graph)
    python benchmark/clone_graph.py rm    <clone>   # stop + instance-rm --purge (full cleanup of that clone)

A clone NEVER gets the base Neo4j pair (7687/7474). On a box where DeepMap was never set up — the runbooks'
default, since no dev-repo setup is needed any more — `provision.py setup` gives the FIRST project the base
pair, and `instance-rm` refuses the base store unconditionally, so that clone could never be cleaned up
(ROUND-3 review #4). `build` therefore claims a NON-base Neo4j pair for the clone in the registry BEFORE
setup runs (provision adopts an existing registry pair as-is), and `rm` still releases a clone that holds
the base pair from an earlier build: it stops that clone's servers + Neo4j, removes its registry entry and
its graphify output dirs, and leaves the base store itself (reported, never deleted).

"Nothing to stop" is success (ROUND-5 W8). `provision.py stop` exits 1 whenever it signalled nothing (no pid
file, or a stale one after a reboot) — the very state the runbooks leave a clone in (build, measure, `stop`,
later `rm`) — so `stop` and the base-pair `rm` decide from the LIVE state after the stop commands
(still_running: no live verified DeepMap pid, none of the clone's ports listening), never from their exit
codes. Anything of the clone still running keeps its claim; a probe that fails counts as running. While
something still looks up, that state is re-checked for a bounded grace (settled_running,
DEEPMAP_BENCH_STOP_GRACE_S, default 10 s): a just-signalled process can hold its port for a moment (Windows
TerminateProcess is asynchronous), and that moment is not "still running". Nothing up = no wait.

Only the ROUTING env is dropped for the child commands (NEO4J_URI, DEEPMAP_SEARCH_PORT, DEEPMAP_EMBED_PORT);
every other DEEPMAP_* knob an operator sets for an experiment still applies to the graph build.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
BUILD = HERE.parent / "build"
ROUTING_ENV = ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")
VERIFY_ENV = "DEEPMAP_BENCH_VERIFY_ON_ARM"   # "1" (the drivers set it): an unregistered clone is fatal too
DEFAULT_PAIR = (8787, 8788)
MCP_NAMES = ("deepmap-search", "deepmap-embed")   # provision.MCP_SERVERS, in registry (search, embed) order
SETUP_GRAPH_KEPT_RC = 4   # provision.SETUP_GRAPH_KEPT_RC: setup's sync pushed nothing (the previous graph is served)
# How long `stop` / `rm` keep re-checking a clone that still looks up right after its stop commands (a
# just-signalled process can hold its port for a moment: Windows TerminateProcess is asynchronous). Only
# while something is still up; bounded; env-tunable (0 = judge once).
STOP_GRACE_ENV = "DEEPMAP_BENCH_STOP_GRACE_S"
DEFAULT_STOP_GRACE_S = 10.0
MAX_STOP_GRACE_S = 120.0
STOP_POLL_S = 0.5
_sleep = time.sleep          # module-level so the gates can drive the wait with a fake clock
_clock = time.monotonic


def _registry():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import port_registry as PR   # noqa: E402
    return PR


def child_env(base=None) -> Dict[str, str]:
    """The environment for a clone's provision / sync command: the routing env removed, so the clone is
    routed to ITS OWN registered instance and ports — never to an operator NEO4J_URI (one shared graph)
    or a DEEPMAP_*_PORT left exported for the previous clone — and with DeepMap's background refresh off
    (DEEPMAP_AUTO_SYNC=0)."""
    e = dict(os.environ if base is None else base)
    for k in ROUTING_ENV:
        e.pop(k, None)
    e.setdefault("PYTHONUTF8", "1")
    # Forced, not setdefault: a measured clone's graph must never change mid-run, so DeepMap's SessionStart background
    # refresh stays off for everything the clone builder starts — whatever the operator's environment says (M12).
    e["DEEPMAP_AUTO_SYNC"] = "0"
    return e


def build_argv(clone, py: str = sys.executable) -> List[str]:
    return [py, str(BUILD / "provision.py"), "setup", str(clone)]


def stop_argvs(clone, py: str = sys.executable) -> List[List[str]]:
    return [[py, str(BUILD / "provision.py"), "mcp-stop", str(clone)],
            [py, str(BUILD / "provision.py"), "stop", str(clone)]]


def rm_argvs(clone, key: str, py: str = sys.executable) -> List[List[str]]:
    return stop_argvs(clone, py) + [[py, str(BUILD / "provision.py"), "instance-rm", "--key", key, "--purge", "--yes"]]


def preallocate_instance(clone, *, PR=None) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """((bolt, http), None) — the clone's Neo4j pair, claimed in the registry as a NON-base pair when it has
    none yet — or (None, problem). Runs BEFORE `provision.py setup <clone>`, which adopts an existing
    registry pair as-is (port_registry._allocate_pair returns it before any base-adoption verdict), so the
    clone gets its own removable instance and never the base store that instance-rm refuses (review #4).
    A clone that already has a pair keeps it (re-running build is idempotent)."""
    PR = PR or _registry()
    base = (PR.BASE_BOLT, PR.BASE_HTTP)
    try:
        status = PR.registry_status()
        if status in ("unreadable", "unrecognized"):
            return None, (f"the DeepMap port registry {PR.registry_path()} is {status} — other projects' port "
                          "claims are invisible, so no pair can be claimed safely")
        have = PR.lookup_neo4j_ports(clone)
        if have:
            return (int(have[0]), int(have[1])), None
        pair = PR.allocate_neo4j_ports(clone, base_adopt_ok=lambda: False)
    except Exception as exc:  # noqa: BLE001
        return None, f"could not claim a Neo4j pair ({type(exc).__name__}: {exc})"
    if not pair or tuple(pair) == base:
        return None, "could not claim a free non-base Neo4j (bolt, http) pair (registry lock busy or ports exhausted)"
    return (int(pair[0]), int(pair[1])), None


def _gout_dirs(key: str) -> List[Path]:
    try:
        if str(BUILD) not in sys.path:
            sys.path.insert(0, str(BUILD))
        import graphify_extract as GX   # noqa: E402
        return [Path(d) for d in GX.gout_dirs_for_project(key)]
    except Exception:  # noqa: BLE001
        return []


def _provision():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import provision as PV   # noqa: E402
    return PV


def _base_store_dir() -> str:
    try:
        return str(_provision().paths()["data"])
    except Exception:  # noqa: BLE001
        return "<DEEPMAP_HOME>/neo4j/neo4j-community-<version>/data"


def _base_store_owner(clone, PV) -> Optional[str]:
    """The project the base store's owner record names when it is ANOTHER project than `clone` (the <= v0.27.4
    mis-adoption, F2 — provision.foreign_base_claim_problem, a file read), else None. Never raises."""
    try:
        if not PV.foreign_base_claim_problem(clone):
            return None
        owner_fn = getattr(PV, "_base_owner_root", None)
        return str((owner_fn() if callable(owner_fn) else None) or "another project")
    except Exception:  # noqa: BLE001
        return None


def _mcp_live_pid(PV, name: str, port: int) -> Optional[int]:
    """The pid in the server's per-port pid file when it is alive AND provably still runs that server (its
    readable command line names the server script); None otherwise — no pid file, a dead pid, or a pid the
    OS recycled for another program (the stop just before would have signalled a server it could verify)."""
    try:
        pid = int(Path(PV._mcp_pidfile(name, port)).read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return None
    if not PV._pid_alive(pid):
        return None
    cmd = PV._pid_cmdline(pid)
    return pid if (cmd is not None and PV.MCP_SERVERS[name]["script"] in cmd) else None


def still_running(clone, key: str, neo=None, *, PR=None, PV=None) -> Tuple[List[str], List[str]]:
    """(running, notes) for `clone` AFTER its stop commands ran: `running` names what of THIS clone is still
    up (empty = stopped: its registry claims may be released), `notes` what was left alone and why.

    "Nothing to stop" IS stopped. `provision.py stop` exits 1 whenever it signalled nothing — no pid file, or
    only a stale one after a reboot — which is exactly the state the runbooks leave a clone in (build,
    measure, `stop`, later `rm`), so the decision comes from the LIVE state, never from the stop commands'
    exit codes (ROUND-4 review / ROUND-5 W8):
      * Neo4j, base pair: DeepMap's own base instance running under a verified pid (any extracted version)
        is this clone's — unless the base store's owner record names ANOTHER project (F2: that instance is
        the other project's, `provision.py stop` refused it on purpose, and it is left running). A bolt
        listener with no live DeepMap Neo4j pid is not a DeepMap-managed instance (e.g. a Docker Neo4j): it
        is left alone and noted, never a reason to keep the claim forever.
      * Neo4j, its own instance: its pid file names a live DeepMap Neo4j, or its registered bolt port listens.
      * MCP: each registered (search, embed) port still listening, or its pid file naming a live server
        process — unless the port's owner sidecar names ANOTHER project's live server (not this clone's).
    A probe that fails, or an unreadable registry, counts as still running (the claim is kept). Never raises."""
    running: List[str] = []
    notes: List[str] = []
    try:
        PR = PR or _registry()
        PV = PV or _provision()
        status = PR.registry_status()
        if status in ("unreadable", "unrecognized"):
            return ([f"the DeepMap port registry {PR.registry_path()} is {status}, so what this clone still runs "
                     "can't be told"], notes)
        base = (int(PR.BASE_BOLT), int(PR.BASE_HTTP))
        pair = (int(neo[0]), int(neo[1])) if neo else None
        if pair == base:
            other = _base_store_owner(clone, PV)
            if other:
                notes.append(f"the base Neo4j store belongs to project {other}, not this clone (a claim an older "
                             "DeepMap wrote by mistake) — its Neo4j was not stopped and is left as it is")
            elif PV._legacy_base_instance_alive():
                running.append(f"its Neo4j (DeepMap's base instance, bolt {base[0]}) is still running")
            elif PV.is_http_up("127.0.0.1", base[0], timeout=1.0):
                notes.append(f"bolt {base[0]} is held by a process DeepMap does not manage (no live DeepMap Neo4j "
                             "pid file — e.g. another Neo4j); left alone")
        elif pair:
            pid = PV._live_instance_pid(key)
            if pid and PV._is_deepmap_neo4j_pid(pid):
                running.append(f"its Neo4j instance (pid {pid}) is still running")
            elif PV.is_http_up("127.0.0.1", pair[0], timeout=1.0):
                running.append(f"its Neo4j bolt port {pair[0]} is still listening")
        for name, port in zip(MCP_NAMES, PR.lookup_ports(clone) or ()):
            port = int(port)
            other = PV._mcp_foreign_owner(name, port, clone)
            if other:
                notes.append(f"{name} port {port} is held by project {other}'s server, not this clone's — left running")
            elif PV.is_http_up(PV.mcp_host(), port, timeout=1.0):
                running.append(f"{name} port {port} is still listening")
            else:
                pid = _mcp_live_pid(PV, name, port)
                if pid:
                    running.append(f"{name} (pid {pid}) is still running")
    except Exception as exc:  # noqa: BLE001
        running.append(f"could not verify that its servers / Neo4j are stopped ({type(exc).__name__}: {exc})")
    return running, notes


def stop_grace_s(env=None) -> float:
    """Seconds `stop` / `rm` keep re-checking a clone that still looks up after its stop commands:
    DEEPMAP_BENCH_STOP_GRACE_S when it is a number (negative -> 0, capped at MAX_STOP_GRACE_S), else
    DEFAULT_STOP_GRACE_S. Never raises."""
    e = os.environ if env is None else env
    raw = str(e.get(STOP_GRACE_ENV, "") or "").strip()
    try:
        v = float(raw) if raw else DEFAULT_STOP_GRACE_S
    except (TypeError, ValueError):
        v = DEFAULT_STOP_GRACE_S
    if math.isnan(v):
        v = DEFAULT_STOP_GRACE_S
    return max(0.0, min(v, MAX_STOP_GRACE_S))


def settled_running(clone, key: str, neo=None, *, PR=None, PV=None,
                    grace_s: Optional[float] = None) -> Tuple[List[str], List[str]]:
    """still_running, re-checked every STOP_POLL_S for up to `grace_s` (stop_grace_s()) while anything of the
    clone still looks up. The stop commands return once a process is SIGNALLED, but it can hold its port for
    a moment after that (Windows TerminateProcess is asynchronous), and judging that moment once reported a
    clone that was being stopped as "not fully stopped" (rm kept the claim, `stop` exited 1). Nothing up ->
    the first answer, with no wait; still up after the grace -> that answer (the claim is kept). Never raises."""
    grace = stop_grace_s() if grace_s is None else max(0.0, float(grace_s))
    try:
        deadline = _clock() + grace
    except Exception:  # noqa: BLE001
        deadline = None
    while True:
        running, notes = still_running(clone, key, neo, PR=PR, PV=PV)
        try:
            if not running or deadline is None or _clock() >= deadline:
                return running, notes
            _sleep(STOP_POLL_S)
        except Exception:  # noqa: BLE001
            return running, notes


def _listener_hint(running: List[str]) -> str:
    """For each port still listening: the command that names the process holding it (runnable as written)."""
    ports = re.findall(r"port (\d+) is still listening", "; ".join(running))
    if not ports:
        return ""
    cmds = [(f"netstat -ano | findstr :{p}" if os.name == "nt" else f"lsof -iTCP:{p} -sTCP:LISTEN -P") for p in ports]
    return (" If a port stays held by a process DeepMap does not manage, find its PID (" + " ; ".join(cmds)
            + ") and stop it by hand if it is a stale DeepMap server.")


def _report_state(clone, stop_rcs: List[int], running: List[str], notes: List[str], *, remedy: str) -> None:
    for n in notes:
        print(f"[clone-graph] note: {n}")
    if running:
        print(f"[clone-graph] {clone} is not fully stopped — {'; '.join(running)}. {remedy}"
              f"{_listener_hint(running)}", file=sys.stderr)
    elif any(rc != 0 for rc in stop_rcs):
        why = ("there was nothing of it left to stop" if not notes else
               "there was nothing of it left to stop, or what they left alone is not this clone's (see above)")
        print(f"[clone-graph] nothing of {clone} is left running (the stop commands' exit codes {stop_rcs}: {why})")


def release_base_pair_clone(clone, key: str, stop_rcs: List[int], *, PR=None, PV=None) -> int:
    """rm for a clone that holds the BASE Neo4j pair (built before `build` pre-claimed a non-base one).
    instance-rm refuses the base store, so do what it would do for this clone, minus deleting the store:
    once nothing of the clone is left running (settled_running — "nothing to stop" counts as stopped; the stop
    commands' exit codes do not decide), drop its whole registry entry (it no longer claims 7687/7474 or its
    MCP pair) and purge its graphify output dirs. The base store is left in place and reported — another
    project is not handed a graph stamped with this clone as its owner. Anything of the clone still running
    keeps the claim (it would otherwise run on unclaimed ports). Returns 0 / 1."""
    PR = PR or _registry()
    try:
        PV = PV or _provision()
    except Exception as exc:  # noqa: BLE001
        print(f"[clone-graph] could not load build/provision.py ({type(exc).__name__}: {exc}) — {clone}'s claim on "
              "the base pair is kept.", file=sys.stderr)
        return 1
    running, notes = settled_running(clone, key, (PR.BASE_BOLT, PR.BASE_HTTP), PR=PR, PV=PV)
    _report_state(clone, stop_rcs, running, notes,
                  remedy=(f"Its claim on the base pair is kept. Stop it (python benchmark/clone_graph.py stop {clone}) "
                          "and re-run rm."))
    if running:
        return 1
    other = _base_store_owner(clone, PV)
    removed = PR.remove_entry(clone, drop_neo4j_only=False)
    if not removed and (PR.lookup_neo4j_ports(clone) or PR.lookup_ports(clone)):
        print(f"[clone-graph] could not remove {clone}'s registry entry ({PR.registry_path()}) — retry rm.",
              file=sys.stderr)
        return 1
    purged = 0
    for d in _gout_dirs(key):
        try:
            shutil.rmtree(d)
            purged += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[clone-graph] could not delete graphify output dir {d}: {exc}", file=sys.stderr)
    store = (f"The base store belongs to project {other} and was left untouched: {_base_store_dir()}" if other else
             f"The base store was left in place and still holds that clone's graph: {_base_store_dir()}")
    print(f"[clone-graph] released {clone}: it held the base Neo4j pair {PR.BASE_BOLT}/{PR.BASE_HTTP}, which "
          f"instance-rm never removes — registry entry dropped, {purged} graphify output dir(s) deleted. {store}")
    return 0


def clone_ports(clone, *, PR=None) -> Optional[Tuple[int, int]]:
    """The clone's OWN registered MCP (search, embed) pair, or None when it has none."""
    PR = PR or _registry()
    pair = PR.lookup_ports(clone)
    return (int(pair[0]), int(pair[1])) if pair else None


def on_arm_ports(env=None) -> Tuple[int, int]:
    """The pair the ON arm's --mcp-config points at — run_benchmark.write_on_mcp_config's own rule."""
    e = os.environ if env is None else env
    return (int(e.get("DEEPMAP_SEARCH_PORT", DEFAULT_PAIR[0])), int(e.get("DEEPMAP_EMBED_PORT", DEFAULT_PAIR[1])))


def _pair_owner(pair, PR) -> Optional[str]:
    """The root of the registered project that owns this MCP pair, if any."""
    try:
        reg = PR.load_registry() or {}
        for ent in (reg.get("projects") or {}).values():
            if isinstance(ent, dict) and (ent.get("search_port"), ent.get("embed_port")) == tuple(pair):
                return str(ent.get("root") or "?")
    except Exception:
        pass
    return None


def on_arm_problem(clone, *, env=None, strict: Optional[bool] = None, PR=None) -> Tuple[Optional[str], Optional[str]]:
    """(problem, warning) for measuring `clone` with the ON arm's MCP pair.

    problem (FAIL CLOSED, no spend):
      * the registry is unreadable (D1 — whose pair is whose cannot be told);
      * the clone HAS its own registered pair and the ON arm points elsewhere — typically another
        project's servers (the dev repo's 8787/8788), i.e. a different project's graph;
      * strict (the drivers: DEEPMAP_BENCH_VERIFY_ON_ARM=1) and the clone has no pair of its own — it was
        never built as its own project, so no server can be serving ITS graph.
    warning (printed, run continues): not strict and the clone is unregistered — an operator-managed
    layout (e.g. one shared NEO4J_URI) this check cannot verify."""
    e = os.environ if env is None else env
    strict = (str(e.get(VERIFY_ENV, "")).strip() == "1") if strict is None else strict
    name = Path(str(clone)).name
    try:
        PR = PR or _registry()
        status = PR.registry_status()
        if status in ("unreadable", "unrecognized"):
            return (f"repo '{name}': the DeepMap port registry {PR.registry_path()} is {status} — cannot verify "
                    "which project's MCP servers the ON arm would query. FAIL CLOSED.", None)
        own = clone_ports(clone, PR=PR)
    except Exception as exc:  # noqa: BLE001
        msg = f"repo '{name}': could not look up the clone's own MCP ports ({type(exc).__name__}: {exc})"
        return (msg + ". FAIL CLOSED.", None) if strict else (None, msg)
    on = on_arm_ports(e)
    if own and tuple(own) == tuple(on):
        return None, None
    if own:
        other = _pair_owner(on, PR)
        whose = f"project {other}'s servers" if other else "servers that are not this clone's"
        return (f"repo '{name}': the ON arm would query MCP :{on[0]}/:{on[1]} — {whose} — but this clone's own "
                f"graph is served on :{own[0]}/:{own[1]}. Run the capture with DEEPMAP_SEARCH_PORT={own[0]} "
                f"DEEPMAP_EMBED_PORT={own[1]} (benchmark/clone_graph.py ports <clone>). FAIL CLOSED.", None)
    msg = (f"repo '{name}': this clone has no DeepMap instance / MCP servers of its own, so the ON arm's "
           f":{on[0]}/:{on[1]} cannot be serving ITS graph — build it first: python benchmark/clone_graph.py "
           f"build <clone>")
    return (msg + ". FAIL CLOSED.", None) if strict else (None, msg + " (unverified — continuing).")


def _run(argv: List[str], env: Dict[str, str], runner: Optional[Callable] = None) -> int:
    runner = runner or (lambda a, e: subprocess.run(a, env=e).returncode)
    print(f"[clone-graph] $ {' '.join(argv)}", flush=True)
    return int(runner(argv, env))


def main(argv=None, *, runner: Optional[Callable] = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("build", "ports", "stop", "rm"):
        print("usage: python benchmark/clone_graph.py build|ports|stop|rm <clone-dir>", file=sys.stderr)
        return 2
    verb, clone = args[0], str(Path(args[1]).resolve())
    if not Path(clone).is_dir():
        print(f"[clone-graph] no such clone directory: {clone}", file=sys.stderr)
        return 2
    env = child_env()
    dropped = [k for k in ROUTING_ENV if k in os.environ]
    if dropped and verb == "build":
        print(f"[clone-graph] note: ignoring {', '.join(dropped)} for the clone's own instance/ports",
              file=sys.stderr)
    for k in ROUTING_ENV:   # this process's own provision probes (stop/rm) route like the children do
        os.environ.pop(k, None)
    if verb == "build":
        neo, problem = preallocate_instance(clone)
        if problem:
            print(f"[clone-graph] {problem} — not building {clone}.", file=sys.stderr)
            return 1
        PR = _registry()
        if tuple(neo) == (PR.BASE_BOLT, PR.BASE_HTTP):
            print(f"[clone-graph] WARNING: {clone} holds the BASE Neo4j pair {neo[0]}/{neo[1]} from an earlier "
                  "build (the base store, which instance-rm never removes). Rebuilding on it; to give the clone an instance of "
                  f"its own: python benchmark/clone_graph.py rm {clone} && python benchmark/clone_graph.py build "
                  f"{clone}", file=sys.stderr)
        else:
            print(f"[clone-graph] {clone}: its own Neo4j pair bolt {neo[0]} / http {neo[1]} (never the base store)")
        rc = _run(build_argv(clone), env, runner)
        if rc == SETUP_GRAPH_KEPT_RC:
            # round 11 (M1): the servers came up, but over the PREVIOUS graph — this run's sync pushed nothing (e.g.
            # graphify ran out of time). A graph setup did not rebuild at this checkout is never measured.
            print(f"[clone-graph] setup for {clone} kept the PREVIOUS graph (rc={rc}: the graph was NOT rebuilt "
                  "— its sync pushed nothing; see the setup verdict above, e.g. raise DEEPMAP_GRAPHIFY_TIMEOUT) "
                  "— not measuring a graph this build did not produce. FAIL CLOSED.", file=sys.stderr)
            return rc
        if rc != 0:
            print(f"[clone-graph] setup FAILED for {clone} (rc={rc}) — its graph / servers are not ready; do "
                  "not measure it.", file=sys.stderr)
            return rc
        pair = clone_ports(clone)
        if not pair:
            print(f"[clone-graph] setup returned 0 but {clone} has no registered MCP pair — FAIL CLOSED.",
                  file=sys.stderr)
            return 1
        print(f"[clone-graph] built {clone}: its own graph + MCP servers on :{pair[0]}/:{pair[1]}")
        return 0
    if verb == "ports":
        try:
            pair = clone_ports(clone)
        except Exception as exc:  # noqa: BLE001
            print(f"[clone-graph] could not read the port registry: {exc}", file=sys.stderr)
            return 2
        if not pair:
            print(f"[clone-graph] {clone} has no DeepMap MCP ports of its own — run: python "
                  f"benchmark/clone_graph.py build {clone}", file=sys.stderr)
            return 2
        print(f"{pair[0]} {pair[1]}")
        return 0
    if verb == "stop":
        rcs = [_run(a, env, runner) for a in stop_argvs(clone)]
        PR = _registry()
        try:
            key, neo = PR.project_key(clone), PR.lookup_neo4j_ports(clone)
        except Exception as exc:  # noqa: BLE001
            print(f"[clone-graph] could not read the port registry ({type(exc).__name__}: {exc})", file=sys.stderr)
            return 1
        running, notes = settled_running(clone, key, neo, PR=PR)
        _report_state(clone, rcs, running, notes,
                      remedy="The provision messages above say why it was not stopped; re-run stop once that is fixed.")
        return 1 if running else 0
    PR = _registry()
    key = PR.project_key(clone)
    neo = PR.lookup_neo4j_ports(clone)
    if neo and (int(neo[0]), int(neo[1])) == (PR.BASE_BOLT, PR.BASE_HTTP):
        stop_rcs = [_run(a, env, runner) for a in stop_argvs(clone)]
        return release_base_pair_clone(clone, key, stop_rcs, PR=PR)
    rcs = [_run(a, env, runner) for a in rm_argvs(clone, key)]
    return 0 if rcs[-1] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
